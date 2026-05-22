from pathlib import Path
from unittest.mock import MagicMock

from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import load_state, save_state
from lab_auto.submit import PlaywrightUploader, SubmitService, validate_pdf


def record(root: Path) -> WorkRecord:
    folder = root / "labs" / "Math" / "[REFACTOR] Lab 1"
    folder.mkdir(parents=True)
    return WorkRecord(
        work_id="task-abc",
        subject="Math",
        name="Lab 1",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/abc",
        due_date=None,
        website_status="не принят",
        local_status=LocalStatus.REFACTOR,
        folder=folder,
        task_pdf=None,
        reports=[],
        last_sync=None,
        last_submit_attempt=None,
        last_submit_result=None,
    )


def test_validate_pdf_checks_suffix_and_magic(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    validate_pdf(pdf)


def test_validate_pdf_rejects_docx(tmp_path):
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"PK")

    try:
        validate_pdf(docx)
    except ValueError as exc:
        assert "PDF" in str(exc)
    else:
        raise AssertionError("expected ValueError")


class FakeUploader:
    def __init__(self, results: list[bool]) -> None:
        self.results = list(results)
        self.calls = 0

    def upload_report(self, session, task_url: str, pdf_path: Path) -> bool:
        del session, task_url, pdf_path
        self.calls += 1
        return self.results.pop(0)


class FakeBrowser:
    def open_session(self):
        class Session:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                pass

            def close(self) -> None:
                pass

        return Session()


def test_submit_marks_sent_on_success(tmp_path):
    work = record(tmp_path)
    save_state(tmp_path, [work])
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    service = SubmitService(
        tmp_path,
        browser=FakeBrowser(),
        uploader=FakeUploader([True]),
        sleep=lambda seconds: None,
    )

    updated = service.submit("task-abc", pdf)

    assert updated.local_status == LocalStatus.SENT
    assert load_state(tmp_path)[0].local_status == LocalStatus.SENT


def test_submit_marks_sentfailed_after_three_failures(tmp_path):
    work = record(tmp_path)
    save_state(tmp_path, [work])
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    uploader = FakeUploader([False, False, False])
    service = SubmitService(
        tmp_path,
        browser=FakeBrowser(),
        uploader=uploader,
        sleep=lambda seconds: None,
    )

    updated = service.submit("task-abc", pdf)

    assert updated.local_status == LocalStatus.SENTFAILED
    assert uploader.calls == 3


def test_playwright_uploader_retries_detail_page_until_awaiting_review(tmp_path):
    pending_html = """
    <html><body>
      <input type="file" id="file" />
      <table><thead><tr><th>Статус</th><th>Файл</th></tr></thead>
      <tbody><tr><td colspan="2">Отчетов не найдено</td></tr></tbody></table>
    </body></html>
    """
    accepted_html = """
    <html><body>
      <input type="file" id="file" />
      <table><thead><tr><th>Статус</th><th>Файл</th></tr></thead>
      <tbody><tr><td>ожидает проверки</td><td>report.pdf</td></tr></tbody></table>
    </body></html>
    """
    page = MagicMock()
    page.content.side_effect = [pending_html, pending_html, pending_html, accepted_html]
    session = MagicMock()
    session.new_page.return_value = page

    uploader = PlaywrightUploader(
        MagicMock(),
        detail_retry_attempts=3,
        detail_retry_delay_seconds=0,
        sleep=lambda seconds: None,
    )
    ok = uploader.upload_report(
        session,
        "https://pro.guap.ru/inside/student/tasks/abc",
        tmp_path / "report.pdf",
    )

    assert ok is True
    assert page.reload.call_count == 2
