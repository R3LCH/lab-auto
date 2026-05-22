from pathlib import Path

from lab_auto.models import LocalStatus, status_from_website
from lab_auto.paths import build_work_id
from lab_auto.parsers import parse_task_detail, parse_task_list
from lab_auto.sync import SyncService
from conftest import BASE_URL, first_fixture_task, load_fixture_task_detail, load_fixture_task_list


def test_fixture_task_list_and_detail_parse_together():
    list_html = load_fixture_task_list()
    detail_html = load_fixture_task_detail()

    tasks = parse_task_list(list_html, BASE_URL)
    detail = parse_task_detail(detail_html, BASE_URL)

    assert len(tasks) >= 1
    task = tasks[0]
    assert task.subject
    assert status_from_website(task.website_status) in LocalStatus
    assert detail.pdf_url and detail.pdf_url.endswith("/download")
    assert detail.has_upload_form is True


def test_sync_pipeline_against_saved_fixtures(tmp_path):
    list_html = load_fixture_task_list()
    expected = first_fixture_task()

    records = SyncService(tmp_path, browser=FakeBrowserForIntegration(list_html)).sync().records

    work_id = build_work_id(expected.task_url, expected.subject, expected.name)
    synced = next((record for record in records if record.work_id == work_id), records[0])
    assert synced.work_id == work_id
    assert synced.task_pdf.exists()
    assert (tmp_path / "state" / "works.yaml").exists()


class FakeBrowserForIntegration:
    def __init__(self, html: str) -> None:
        self.html = html

    def open_session(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, browser: FakeBrowserForIntegration) -> None:
        self.browser = browser

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        pass

    def task_list_html(self) -> str:
        return self.browser.html

    def page_html(self, url: str) -> str:
        return load_fixture_task_detail()

    def download_file(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4\n")
