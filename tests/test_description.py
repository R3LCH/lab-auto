from pathlib import Path

import pytest
import yaml

from conftest import BASE_URL, SYNTHETIC_TASK_LIST_HTML
from lab_auto.archive import archive_removed_works
from lab_auto.models import LocalStatus
from lab_auto.parsers import parse_task_detail
from lab_auto.state import load_state, save_state
from lab_auto.sync import SyncService
from test_state import make_record
from test_sync import FakeBrowser, FakeBrowserSession


FIXTURES = Path(__file__).parent / "fixtures"
TASK_URL = f"{BASE_URL}/inside/student/tasks/100001"


@pytest.mark.parametrize("fixture, has_pdf", [
    ("description_only.html", False),
    ("description_with_pdf.html", True),
])
def test_description_pipeline(tmp_path, monkeypatch, fixture, has_pdf):
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    monkeypatch.setattr(FakeBrowserSession, "page_html", lambda self, url: html)
    records = SyncService(tmp_path, browser=FakeBrowser(SYNTHETIC_TASK_LIST_HTML)).sync().records
    loaded = {work.work_id: work for work in load_state(tmp_path)}
    for work in records:
        assert work.description
        assert loaded[work.work_id].description == work.description
        content = (work.folder / "task.md").read_text(encoding="utf-8")
        assert work.description in content
        assert "Этот текст" not in content
        assert (work.folder / "task.pdf").exists() is has_pdf
        assert (work.task_pdf is not None) is has_pdf
        if has_pdf:
            assert "[task.pdf](task.pdf)" in content
            assert "[по ссылке](https://example.com/variants)" in content
        else:
            assert "## Прикреплённое задание\n\nНет" in content


def test_description_preserves_paragraphs_breaks_lists_and_inline_spacing():
    html = '''<div class="task-description-block">
      <p>Первый абзац.<br>Вторая строка.</p>
      <p>Следующий <a href="/variants">абзац</a>.</p>
      <ul><li>Один</li><li>Два<ul><li>Вложенный</li></ul></li></ul>
      <ol><li>Шаг один</li><li>Шаг два</li></ol>
    </div>'''
    description = parse_task_detail(html, BASE_URL, task_url=TASK_URL).description
    assert "Первый абзац.\\\nВторая строка.\n\n" in description
    assert f"Следующий [абзац]({BASE_URL}/variants)." in description
    assert "- Один\n- Два" in description
    assert "\n  - Вложенный" in description
    assert "1. Шаг один\n2. Шаг два" in description


@pytest.mark.parametrize("href, expected", [
    ("https://example.com/variants", "https://example.com/variants"),
    ("/variants", f"{BASE_URL}/variants"),
    ("variants", f"{BASE_URL}/inside/student/tasks/variants"),
    ("../variants", f"{BASE_URL}/inside/student/variants"),
    ("#variants", TASK_URL + "#variants"),
])
def test_description_resolves_links(href, expected):
    html = f'<p class="task-description-block">Варианты <a href="{href}">по ссылке</a>.</p>'
    assert parse_task_detail(html, BASE_URL, task_url=TASK_URL).description == (
        f"Варианты [по ссылке]({expected})."
    )


@pytest.mark.parametrize("html", ["<html></html>", '<p class="task-description-block"> \n<br></p>'])
def test_missing_description(tmp_path, monkeypatch, html):
    assert parse_task_detail(html, BASE_URL).description is None
    monkeypatch.setattr(FakeBrowserSession, "page_html", lambda self, url: html)
    records = SyncService(tmp_path, browser=FakeBrowser(SYNTHETIC_TASK_LIST_HTML)).sync().records
    assert all(work.description is None for work in records)
    assert "Описание задания на странице не указано." in (
        records[0].folder / "task.md"
    ).read_text(encoding="utf-8")


def test_legacy_state_and_description_roundtrip(tmp_path):
    record = make_record(tmp_path, LocalStatus.REVIEW)
    legacy = record.to_dict()
    legacy.pop("description")
    state_file = tmp_path / "state" / "works.yaml"
    state_file.parent.mkdir()
    state_file.write_text(yaml.safe_dump({"version": 1, "works": [legacy]}), encoding="utf-8")
    loaded = load_state(tmp_path)[0]
    assert loaded.description is None
    loaded.description = "Абзац.\n\n- [Ссылка](https://example.com)\n- Ещё пункт"
    save_state(tmp_path, [loaded])
    assert load_state(tmp_path)[0].description == loaded.description
    assert "description: |-" in state_file.read_text(encoding="utf-8")
    before = state_file.read_bytes()
    save_state(tmp_path, load_state(tmp_path))
    assert state_file.read_bytes() == before


def test_repeat_sync_refreshes_description_metadata_and_pdf(tmp_path, monkeypatch):
    browser = FakeBrowser(SYNTHETIC_TASK_LIST_HTML)
    html = (FIXTURES / "description_only.html").read_text(encoding="utf-8")
    fetches = []

    def page_html(self, url):
        fetches.append(url)
        return html

    monkeypatch.setattr(FakeBrowserSession, "page_html", page_html)
    service = SyncService(tmp_path, browser=browser)
    first = service.sync().records[0]
    html = (FIXTURES / "description_with_pdf.html").read_text(encoding="utf-8")
    browser.html = browser.html.replace("2099-01-15", "2099-04-01").replace("не принят", "принят")
    second = service.sync().records[0]
    assert second.folder != first.folder
    assert not first.folder.exists()
    content = (second.folder / "task.md").read_text(encoding="utf-8")
    assert "2099-04-01" in content and "[DONE]" in content
    assert "[task.pdf](task.pdf)" in content
    assert second.description in content
    downloads = len(browser.downloads)

    html = html.replace("Предварительный анализ данных.", "Изменённое описание.")
    third = service.sync().records[0]
    assert "Изменённое описание." in third.description
    assert load_state(tmp_path)[0].description == third.description
    assert third.description in (third.folder / "task.md").read_text(encoding="utf-8")
    assert len(browser.downloads) == downloads
    assert len(fetches) == 9  # Three tasks, once each per sync.

    artifact = third.folder / "task.md"
    before = artifact.stat().st_mtime_ns
    service.sync()
    assert artifact.stat().st_mtime_ns == before

    html = "<html></html>"
    cleared = service.sync().records[0]
    assert cleared.description is None
    assert load_state(tmp_path)[0].description is None
    assert "Описание задания на странице не указано." in artifact.read_text(encoding="utf-8")


def test_archived_description_and_artifact_are_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeBrowserSession, "page_html", lambda self, url: (
        '<p class="task-description-block">Описание</p>'
    ))
    service = SyncService(tmp_path, browser=FakeBrowser(SYNTHETIC_TASK_LIST_HTML))
    records = service.sync().records
    archived = records[0]
    archived.archived = True
    save_state(tmp_path, records)
    artifact = archived.folder / "task.md"
    artifact.write_text("Frozen", encoding="utf-8")
    service.sync()
    assert artifact.read_text(encoding="utf-8") == "Frozen"
    assert load_state(tmp_path)[0].description == "Описание"
    result = archive_removed_works([], {archived.work_id: archived}, set(), "2099-01-01")
    assert result[0].description == "Описание"


def test_single_detail_fetch_supplies_description_pdf_and_report(tmp_path, monkeypatch):
    html = (FIXTURES / "description_with_pdf.html").read_text(encoding="utf-8")
    html = html.replace("</body>", '<a href="/inside/student/reports/123/download">Отчёт</a></body>')
    fetches = []

    def page_html(self, url):
        fetches.append(url)
        return html

    monkeypatch.setattr(FakeBrowserSession, "page_html", page_html)
    browser = FakeBrowser(SYNTHETIC_TASK_LIST_HTML)
    service = SyncService(tmp_path, browser=browser)
    records = service.sync().records
    assert len(fetches) == 3
    assert len(browser.downloads) == 6
    assert all(work.description and work.task_pdf and work.reports for work in records)
    service.sync()
    assert len(fetches) == 6
    assert len(browser.downloads) == 6
