from pathlib import Path

import pytest
import yaml

from conftest import BASE_URL, SYNTHETIC_TASK_LIST_HTML
from lab_auto.archive import archive_removed_works, archive_work, unarchive_work
from lab_auto.models import LocalStatus
from lab_auto.parsers import parse_task_detail
from lab_auto.state import generate_task_markdown, load_state, save_state
from lab_auto.sync import SyncService
from test_state import make_record
from test_sync import FakeBrowser, FakeBrowserSession


HTML = (Path(__file__).parent / "fixtures" / "additional_material.html").read_text(encoding="utf-8")
OPEN_URL = "https://example.com/variants"
DOWNLOAD_URL = BASE_URL + "/inside/student/tasks/material-hash/download"


def test_representative_detail():
    detail = parse_task_detail(HTML, BASE_URL)
    assert detail.description == "Анализ данных.\\\nВарианты по ссылке."
    assert detail.additional_material_url == OPEN_URL
    assert detail.pdf_url == DOWNLOAD_URL


@pytest.mark.parametrize("href, expected", [
    ("/variants", BASE_URL + "/variants"),
    ("variants", BASE_URL + "/inside/student/tasks/variants"),
    ("", None),
])
def test_relative_and_empty_open_url(href, expected):
    detail = parse_task_detail(HTML.replace(OPEN_URL, href), BASE_URL,
                               task_url=BASE_URL + "/inside/student/tasks/100001")
    assert detail.additional_material_url == expected
    assert detail.pdf_url == DOWNLOAD_URL


def test_no_material_block_or_only_download(tmp_path):
    for html in ("<p>No materials</p>", HTML.replace("task-view-links\"", "other-link\"")):
        assert parse_task_detail(html, BASE_URL).additional_material_url is None
    record = make_record(tmp_path, LocalStatus.UNDONE)
    generate_task_markdown(record)
    content = (record.folder / "task.md").read_text(encoding="utf-8")
    assert "## Дополнительные материалы\n\nНет" in content
    assert "## Прикреплённое задание\n\nНет" in content
    unrelated = HTML.replace("Доп. материалы:", "Преподаватель:")
    assert parse_task_detail(unrelated, BASE_URL).additional_material_url is None


def test_legacy_and_archive_roundtrip(tmp_path):
    record = make_record(tmp_path, LocalStatus.REVIEW)
    payload = record.to_dict()
    payload.pop("additional_material_url")
    state = tmp_path / "state" / "works.yaml"
    state.parent.mkdir()
    state.write_text(yaml.safe_dump({"version": 1, "works": [payload]}), encoding="utf-8")
    loaded = load_state(tmp_path)[0]
    assert loaded.additional_material_url is None
    loaded.additional_material_url = OPEN_URL
    save_state(tmp_path, [loaded])
    assert archive_work(tmp_path, loaded.work_id).additional_material_url == OPEN_URL
    assert unarchive_work(tmp_path, loaded.work_id).additional_material_url == OPEN_URL
    assert load_state(tmp_path)[0].additional_material_url == OPEN_URL
    assert archive_removed_works([], {loaded.work_id: loaded}, set(), "2099-01-01")[0].additional_material_url == OPEN_URL


def test_sync_refreshes_url_without_extra_fetch_or_duplicate_pdf(tmp_path, monkeypatch):
    html = HTML
    fetches = []

    def page_html(self, url):
        fetches.append(url)
        return html

    monkeypatch.setattr(FakeBrowserSession, "page_html", page_html)
    browser = FakeBrowser(SYNTHETIC_TASK_LIST_HTML)
    service = SyncService(tmp_path, browser=browser)
    records = service.sync().records
    assert len(fetches) == len(records)
    assert len(browser.downloads) == len(records)
    for work in records:
        assert work.additional_material_url == OPEN_URL
        assert work.task_pdf.is_file()
        assert list(work.folder.glob("*.pdf")) == [work.task_pdf]
        content = (work.folder / "task.md").read_text(encoding="utf-8")
        assert f"[Открыть материал]({OPEN_URL})" in content
        assert "[task.pdf](task.pdf)" in content
    assert all(url == DOWNLOAD_URL for url, _ in browser.downloads)

    changed_url = "https://example.com/new-variants"
    html = HTML.replace(OPEN_URL, changed_url)
    service.sync()
    assert len(fetches) == 2 * len(records)
    assert len(browser.downloads) == len(records)
    for work in load_state(tmp_path):
        assert work.additional_material_url == changed_url
        assert changed_url in (work.folder / "task.md").read_text(encoding="utf-8")

    html = "<html></html>"
    service.sync()
    for work in load_state(tmp_path):
        assert work.additional_material_url is None
        assert "## Дополнительные материалы\n\nНет" in (work.folder / "task.md").read_text(encoding="utf-8")
