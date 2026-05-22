from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from lab_auto.archive import archive_removed_works
from lab_auto.browser import BASE_URL, BrowserService, BrowserSession
from lab_auto.logging import LogKind, append_log
from lab_auto.models import WorkRecord, merge_state_after_sync, resolve_local_status
from lab_auto.parsers import (
    diagnose_empty_task_list,
    is_known_website_status,
    parse_task_detail,
    parse_task_list,
)
from lab_auto.paths import build_work_id, sync_work_folder
from lab_auto.state import (
    active_works,
    generate_markdown_views,
    load_state_unlocked,
    locked_workspace,
    lookup_previous_work,
    save_state_unlocked,
)


@dataclass(slots=True)
class SyncResult:
    records: list[WorkRecord]
    dropped_from_site: list[str]
    empty_task_list: bool = False
    parse_warning: str | None = None


class SyncService:
    def __init__(
        self,
        root: Path,
        browser: BrowserService | Any | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self.root = root
        self.browser = browser or BrowserService(root)
        self.base_url = base_url

    def sync(self, *, archive_removed: bool = False) -> SyncResult:
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        with locked_workspace(self.root):
            old_records = load_state_unlocked(self.root)
        old_by_id = {record.work_id: record for record in old_records}
        old_by_url = {record.task_url: record for record in old_records}

        with self._open_session() as session:
            html = session.task_list_html()
            parsed_tasks = parse_task_list(html, self.base_url)

            if not parsed_tasks:
                warning = diagnose_empty_task_list(html)
                append_log(
                    self.root,
                    LogKind.AI,
                    f"Sync parsed 0 tasks from website; state unchanged. {warning}",
                    now=now,
                )
                return SyncResult(
                    records=list(old_by_id.values()),
                    dropped_from_site=[],
                    empty_task_list=True,
                    parse_warning=warning,
                )

            records_by_id: dict[str, WorkRecord] = {}

            for task in parsed_tasks:
                work_id = build_work_id(task.task_url, task.subject, task.name)
                if work_id in records_by_id:
                    append_log(
                        self.root,
                        LogKind.AI,
                        f"{work_id}: duplicate task row on website; keeping first entry",
                        now=now,
                    )
                    continue

                previous = lookup_previous_work(
                    old_by_id,
                    old_by_url,
                    task_url=task.task_url,
                    subject=task.subject,
                    name=task.name,
                )

                if previous and previous.archived:
                    records_by_id[work_id] = previous
                    continue

                if not is_known_website_status(task.website_status):
                    append_log(
                        self.root,
                        LogKind.WEBSITE,
                        f"{work_id}: unknown website status {task.website_status!r}",
                        now=now,
                    )

                local_status = resolve_local_status(
                    task.website_status,
                    previous.local_status if previous else None,
                )
                folder = sync_work_folder(
                    self.root,
                    task.subject,
                    task.name,
                    local_status,
                    task_site_id=task.task_site_id,
                )
                task_pdf = folder / "task.pdf"
                need_detail_page = not task_pdf.exists()

                if need_detail_page:
                    detail = parse_task_detail(
                        session.page_html(task.task_url),
                        self.base_url,
                    )
                    if detail.pdf_url and not task_pdf.exists():
                        session.download_file(detail.pdf_url, task_pdf)

                if previous and previous.website_status != task.website_status:
                    append_log(
                        self.root,
                        LogKind.WEBSITE,
                        f"{work_id}: {previous.website_status} -> {task.website_status}",
                        now=now,
                    )

                records_by_id[work_id] = WorkRecord(
                    work_id=work_id,
                    subject=task.subject,
                    name=task.name,
                    number=task.number,
                    task_url=task.task_url,
                    task_site_id=task.task_site_id,
                    due_date=task.due_date,
                    website_status=task.website_status,
                    local_status=local_status,
                    folder=folder,
                    task_pdf=task_pdf if task_pdf.exists() else None,
                    reports=previous.reports if previous else [],
                    last_sync=now,
                    last_submit_attempt=previous.last_submit_attempt if previous else None,
                    last_submit_result=previous.last_submit_result if previous else None,
                    archived=False,
                    archived_at=None,
                )

            records = list(records_by_id.values())
            synced_ids = set(records_by_id)

        dropped_from_site = sorted(
            work_id
            for work_id, work in old_by_id.items()
            if not work.archived and work_id not in synced_ids
        )
        dropped_ids = set(dropped_from_site)

        if archive_removed:
            records = archive_removed_works(records, old_by_id, synced_ids, now)
        else:
            records.extend(
                work
                for work_id, work in old_by_id.items()
                if work.archived and work_id not in synced_ids
            )
            for work_id in dropped_from_site:
                append_log(
                    self.root,
                    LogKind.AI,
                    f"{work_id}: no longer on website; removed from state",
                    now=now,
                )

        with locked_workspace(self.root):
            current = load_state_unlocked(self.root)
            records = merge_state_after_sync(
                records,
                current,
                dropped_ids=dropped_ids,
            )
            save_state_unlocked(self.root, records)

        generate_markdown_views(self.root, records)
        active_count = len(active_works(records))
        archived_count = len(records) - active_count
        message = f"Sync completed: {active_count} active works"
        if archived_count:
            message += f", {archived_count} archived"
        if dropped_from_site and not archive_removed:
            message += f", {len(dropped_from_site)} dropped from site"
        append_log(self.root, LogKind.AI, message, now=now)
        return SyncResult(
            records=records,
            dropped_from_site=dropped_from_site,
            empty_task_list=False,
            parse_warning=None,
        )

    def _open_session(self) -> BrowserSession | Any:
        if hasattr(self.browser, "open_session"):
            return self.browser.open_session()
        if hasattr(self.browser, "begin_session"):
            return self.browser.begin_session()
        raise TypeError("Browser dependency must provide open_session() or begin_session().")
