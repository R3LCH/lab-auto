from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from lab_auto.browser import BASE_URL, BrowserService, BrowserSession
from lab_auto.logging import LogKind, append_log
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.parsers import parse_task_detail
from lab_auto.paths import canonical_task_detail_url, extract_task_site_id, sync_work_folder
from lab_auto.state import (
    generate_markdown_views,
    load_state_unlocked,
    locked_workspace,
    save_state_unlocked,
)
from lab_auto.workspace import find_work_in_list

_DETAIL_RETRY_ATTEMPTS = 5
_DETAIL_RETRY_DELAY_SECONDS = 2


def validate_pdf(path: Path) -> None:
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files can be submitted.")
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("Selected file does not look like a PDF.")


class PlaywrightUploader:
    def __init__(
        self,
        browser: BrowserService,
        base_url: str = BASE_URL,
        *,
        detail_retry_attempts: int = _DETAIL_RETRY_ATTEMPTS,
        detail_retry_delay_seconds: int = _DETAIL_RETRY_DELAY_SECONDS,
        sleep: Callable[[int], None] = time.sleep,
    ) -> None:
        self.browser = browser
        self.base_url = base_url
        self.detail_retry_attempts = detail_retry_attempts
        self.detail_retry_delay_seconds = detail_retry_delay_seconds
        self.sleep = sleep

    def upload_report(self, session: BrowserSession, task_url: str, pdf_path: Path) -> bool:
        page = session.new_page()
        try:
            page.goto(task_url, wait_until="domcontentloaded", timeout=60_000)
            detail = parse_task_detail(page.content(), self.base_url)
            if not detail.has_upload_form:
                return False
            page.get_by_role("button", name="Добавить").click()
            page.set_input_files("input#file", str(pdf_path.resolve()))
            page.get_by_role("button", name="Сохранить").click()
            page.wait_for_load_state("domcontentloaded", timeout=60_000)
            return self._awaiting_review_on_detail(page)
        finally:
            page.close()

    def _awaiting_review_on_detail(self, page: Any) -> bool:
        for attempt in range(self.detail_retry_attempts):
            if attempt:
                self.sleep(self.detail_retry_delay_seconds)
                page.reload(wait_until="domcontentloaded", timeout=60_000)
            detail = parse_task_detail(page.content(), self.base_url)
            if detail.awaiting_review:
                return True
        return False


class SubmitService:
    def __init__(
        self,
        root: Path,
        browser: BrowserService | None = None,
        uploader: PlaywrightUploader | Any | None = None,
        sleep: Callable[[int], None] = time.sleep,
    ) -> None:
        self.root = root
        self.browser = browser or BrowserService(root)
        self.uploader = uploader or PlaywrightUploader(self.browser, sleep=sleep)
        self.sleep = sleep

    def submit(self, work_ref: str, pdf_path: Path) -> WorkRecord:
        validate_pdf(pdf_path)
        pdf_resolved = pdf_path.resolve()

        with locked_workspace(self.root):
            works, work = find_work_in_list(
                self.root,
                load_state_unlocked(self.root),
                work_ref,
            )
            work_id = work.work_id
            task_url = canonical_task_detail_url(work.task_url, BASE_URL)
            task_site_id = work.task_site_id or extract_task_site_id(work.task_url) or None

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        success = False

        with self.browser.open_session() as session:
            for attempt in range(1, 4):
                success = bool(self.uploader.upload_report(session, task_url, pdf_resolved))
                if success:
                    break
                if attempt < 3:
                    self.sleep(10)

        with locked_workspace(self.root):
            works = load_state_unlocked(self.root)
            _, work = find_work_in_list(self.root, works, work_id)
            work.local_status = LocalStatus.SENT if success else LocalStatus.SENTFAILED
            work.website_status = "ожидает проверки" if success else work.website_status
            work.last_submit_attempt = now
            work.last_submit_result = "success" if success else "failed"
            if pdf_resolved not in {report.resolve() for report in work.reports}:
                work.reports.append(pdf_resolved)
            work.folder = sync_work_folder(
                self.root,
                work.subject,
                work.name,
                work.local_status,
                task_site_id=task_site_id,
            )
            save_state_unlocked(self.root, works)

        generate_markdown_views(self.root, works)
        if success:
            append_log(
                self.root,
                LogKind.AI,
                f"{work_id}: submitted PDF and marked [SENT]",
                now=now,
            )
        else:
            append_log(
                self.root,
                LogKind.AI,
                f"{work_id}: submit failed after 3 attempts; marked [SENTFAILED]",
                now=now,
            )
        return work
