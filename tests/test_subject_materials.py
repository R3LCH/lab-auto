from pathlib import Path
from urllib.error import URLError

from conftest import BASE_URL
from lab_auto.materials import MaterialDownloader, MaterialResult
from lab_auto.models import LocalStatus, MaterialFile
from lab_auto.parsers import parse_subject_materials, parse_task_list
from lab_auto.state import generate_task_markdown
from lab_auto.sync import SyncService
from lab_auto.subject_materials import (
    SubjectMaterial,
    load_subject_materials,
    material_local_path,
    materials_for_work,
    save_subject_materials,
    sync_subject_materials,
)
from test_state import make_record


FIXTURE = Path(__file__).parent / "fixtures" / "subject_materials.html"
HTML = FIXTURE.read_text(encoding="utf-8")


class Session:
    def __init__(self, html=HTML, failures=()):
        self.html = html
        self.failures = set(failures)
        self.page_fetches = 0
        self.downloads = []

    def materials_html(self):
        self.page_fetches += 1
        if isinstance(self.html, Exception):
            raise self.html
        return self.html

    def download_named_file(self, url, directory, title, *, existing=None):
        material_id = url.rstrip("/").split("/")[-2]
        if material_id in self.failures:
            raise RuntimeError("download failed")
        directory.mkdir(parents=True, exist_ok=True)
        destination = existing or directory / (title.replace(":", " ").replace("?", " ") + ".pdf")
        if destination.exists() and existing is None:
            destination = directory / (destination.stem + "-2" + destination.suffix)
        destination.write_bytes((material_id + "\n").encode())
        self.downloads.append(url)
        return destination


class ExternalDownloader:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def sync(self, url, root, previous=None, *, relative_directory="materials"):
        self.calls.append((url, relative_directory))
        if not url:
            return MaterialResult(previous)
        if self.fail:
            return MaterialResult(previous, "external unavailable")
        directory = root / relative_directory
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "external.pdf"
        path.write_bytes(b"external")
        return MaterialResult(MaterialFile(url, path.relative_to(root).as_posix()))


def test_parse_real_table_shape_multiple_subjects_and_rows():
    materials = parse_subject_materials(HTML, BASE_URL)
    assert len(materials) == 4
    first, second, external, _ = materials
    assert first.material_id == "db-manual"
    assert first.subject == "Базы данных"
    assert first.subject_site_id == "3403206"
    assert first.title == "Учебно-методическое пособие"
    assert first.added_at == "01.09.2026 10:01:58"
    assert first.download_url == BASE_URL + "/inside/student/materials/db-manual/download"
    assert first.source_url is None
    assert second.subject_site_id == first.subject_site_id
    assert external.material_id.startswith("external-")
    assert external.source_url == "https://example.com/book"
    assert external.download_url is None


def test_parse_requires_exact_headers_and_ignores_incomplete_rows():
    try:
        parse_subject_materials("<table></table>", BASE_URL)
    except ValueError as exc:
        assert "expected headers" in str(exc)
    else:
        raise AssertionError("expected parse failure")
    broken = HTML.replace("Учебно-методическое пособие", "")
    assert len(parse_subject_materials(broken, BASE_URL)) == 3


def test_task_list_parses_subject_id():
    html = '''<table><thead><tr><th>Дисциплина</th><th>Название</th><th>Статус</th><th>Предельная дата</th></tr></thead>
      <tbody><tr><td><a href="/inside/students/subjects/3403206">Базы данных</a></td>
      <td><a href="/inside/student/tasks/1">Лабораторная №1</a></td><td>—</td><td>01.01.2099</td></tr></tbody></table>'''
    task = parse_task_list(html, BASE_URL)[0]
    assert task.subject_site_id == "3403206"


def test_state_roundtrip_and_legacy_workspace(tmp_path):
    assert load_subject_materials(tmp_path) == []
    item = SubjectMaterial("id", "Базы данных", "Пособие", subject_site_id="1", local_path="materials/Базы данных/a.pdf")
    save_subject_materials(tmp_path, [item])
    assert load_subject_materials(tmp_path) == [item]
    text = (tmp_path / "state" / "materials.yaml").read_text(encoding="utf-8")
    assert "Базы данных" in text and "materials/Базы данных/a.pdf" in text


def test_sync_once_direct_external_sanitize_and_idempotency(tmp_path):
    session = Session()
    external = ExternalDownloader()
    first = sync_subject_materials(tmp_path, session, [], external, base_url=BASE_URL, now="t1")
    assert first.page_loaded and not first.warnings
    assert session.page_fetches == 1
    assert len(session.downloads) == 2
    db = [item for item in first.materials if item.subject_site_id == "3403206"]
    assert len(db) == 2 and all(material_local_path(tmp_path, item) for item in db)
    assert all(Path(item.local_path).parts[:2] == ("materials", "Базы данных") for item in db)
    book = next(item for item in first.materials if item.source_url == "https://example.com/book")
    assert book.local_path == "materials/Вычислительные системы, сети и телекоммуникации/external.pdf"
    second = sync_subject_materials(tmp_path, session, first.materials, external, base_url=BASE_URL, now="t2")
    assert session.page_fetches == 2
    assert len(session.downloads) == 2
    assert [item.local_path for item in second.materials] == [item.local_path for item in first.materials]


def test_filename_collision_is_kept_inside_subject(tmp_path):
    html = HTML.replace("Дополнительные примеры", "Учебно-методическое пособие")
    result = sync_subject_materials(tmp_path, Session(html), [], ExternalDownloader(), base_url=BASE_URL, now="t")
    paths = [item.local_path for item in result.materials if item.subject_site_id == "3403206"]
    assert len(set(paths)) == 2
    assert all(material_local_path(tmp_path, item) for item in result.materials if item.local_path)


def test_page_failure_preserves_previous_state_and_file(tmp_path):
    first = sync_subject_materials(tmp_path, Session(), [], ExternalDownloader(), base_url=BASE_URL, now="t1")
    failed = sync_subject_materials(tmp_path, Session(URLError("offline")), first.materials,
                                    ExternalDownloader(), base_url=BASE_URL, now="t2")
    assert not failed.page_loaded and failed.materials == first.materials and failed.warnings
    assert all(material_local_path(tmp_path, item) for item in failed.materials if item.local_path)


def test_one_download_failure_does_not_stop_others_and_preserves_old(tmp_path):
    first = sync_subject_materials(tmp_path, Session(), [], ExternalDownloader(), base_url=BASE_URL, now="t1")
    changed = HTML.replace("01.09.2026 10:01:58", "03.09.2026 10:01:58")
    second = sync_subject_materials(tmp_path, Session(changed, {"db-manual"}), first.materials,
                                    ExternalDownloader(), base_url=BASE_URL, now="t2")
    manual = next(item for item in second.materials if item.material_id == "db-manual")
    assert manual.local_path == next(item for item in first.materials if item.material_id == "db-manual").local_path
    assert material_local_path(tmp_path, manual)
    assert second.warnings and len(second.materials) == 4


def test_removed_material_is_marked_stale_without_deleting(tmp_path):
    first = sync_subject_materials(tmp_path, Session(), [], ExternalDownloader(), base_url=BASE_URL, now="t1")
    html = HTML.replace(next(row for row in HTML.split("<tr>") if "db-manual" in row).split("</tr>")[0] + "</tr>", "")
    second = sync_subject_materials(tmp_path, Session(html), first.materials, ExternalDownloader(), base_url=BASE_URL, now="t2")
    removed = next(item for item in second.materials if item.material_id == "db-manual")
    assert removed.present is False and removed.missing_since == "t2"
    assert material_local_path(tmp_path, removed)


def test_task_markdown_exact_subject_and_relative_links(tmp_path):
    result = sync_subject_materials(tmp_path, Session(), [], ExternalDownloader(), base_url=BASE_URL, now="t")
    work = make_record(tmp_path, LocalStatus.UNDONE, subject="Базы данных")
    work.subject_site_id = "3403206"
    work.folder.mkdir(parents=True, exist_ok=True)
    generate_task_markdown(work, result.materials, root=tmp_path)
    content = (work.folder / "task.md").read_text(encoding="utf-8")
    assert "## Материалы дисциплины" in content
    assert "Учебно-методическое пособие" in content
    assert "Дополнительные примеры" in content
    assert "../../../../materials/" not in content
    assert "../../../materials/Базы%20данных/" in content
    assert "Похожее название" not in content
    assert "Книга: архитектура" not in content


def test_exact_name_fallback_has_no_fuzzy_matching(tmp_path):
    materials = parse_subject_materials(HTML, BASE_URL)
    converted = [SubjectMaterial(
        item.material_id, item.subject, item.title, item.subject_site_id,
        item.added_at, item.source_url, item.download_url,
    ) for item in materials]
    exact = make_record(tmp_path, LocalStatus.UNDONE, subject="  БАЗЫ   ДАННЫХ ")
    similar = make_record(tmp_path, LocalStatus.UNDONE, subject="База данных")
    assert len(materials_for_work(exact, converted)) == 2
    assert materials_for_work(similar, converted) == []


def test_full_sync_writes_subject_state_and_task_links(tmp_path):
    task_list = '''<table><thead><tr><th>Дисциплина</th><th>Название</th><th>Статус</th><th>Предельная дата</th></tr></thead>
      <tbody><tr><td><a href="/inside/students/subjects/3403206">Базы данных</a></td>
      <td><a href="/inside/student/tasks/188297">Лабораторная работа №1</a></td>
      <td>—</td><td>20.09.2026</td></tr></tbody></table>'''
    detail = '''<p class="task-description-block">Описание</p>
      <a href="/inside/student/tasks/188297/download">PDF</a>'''

    class FullSession(Session):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def task_list_html(self):
            return task_list

        def page_html(self, url):
            return detail

        def download_file(self, url, destination):
            destination.write_bytes(b"%PDF")

    class Browser:
        def __init__(self):
            self.session = FullSession()

        def open_session(self):
            return self.session

    browser = Browser()
    result = SyncService(
        tmp_path,
        browser=browser,
        material_downloader=ExternalDownloader(),
    ).sync()
    work = result.records[0]
    assert browser.session.page_fetches == 1
    assert work.task_pdf.is_file() and work.description == "Описание"
    assert work.subject_site_id == "3403206"
    assert (tmp_path / "state" / "materials.yaml").is_file()
    content = (work.folder / "task.md").read_text(encoding="utf-8")
    assert "[task.pdf](task.pdf)" in content
    assert "Учебно-методическое пособие" in content
    assert "../../../materials/Базы%20данных/" in content
