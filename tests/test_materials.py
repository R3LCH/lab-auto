import io
import json
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from conftest import SYNTHETIC_TASK_LIST_HTML
from lab_auto.archive import archive_removed_works, archive_work, unarchive_work
from lab_auto.materials import MaterialDownloader, _HttpRedirects, local_material_path
from lab_auto.models import LocalStatus, MaterialFile
from lab_auto.state import generate_task_markdown, load_state, save_state
from lab_auto.sync import SyncService
from test_additional_material import HTML, OPEN_URL
from test_state import make_record
from test_sync import FakeBrowser, FakeBrowserSession


class Response(io.BytesIO):
    def __init__(self, body=b"data", url="https://example.com/file.csv", headers=None, status=200):
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = Message()
        for key, value in (headers or {"Content-Type": "text/csv"}).items():
            self.headers[key] = value

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 20
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_direct_download_and_no_validator_cache(tmp_path):
    opener = Opener(Response(b"a,b\n1,2\n"))
    downloader = MaterialDownloader(opener)
    result = downloader.sync(OPEN_URL, tmp_path)
    assert not result.warning
    assert (tmp_path / result.file.path).read_bytes() == b"a,b\n1,2\n"
    assert result.file.source_url == OPEN_URL
    again = downloader.sync(OPEN_URL, tmp_path, result.file)
    assert again.file == result.file
    assert len(opener.requests) == 1


def test_direct_download_supports_subject_subdirectory(tmp_path):
    downloader = MaterialDownloader(Opener(Response(b"manual")))
    result = downloader.sync(
        OPEN_URL,
        tmp_path,
        relative_directory="materials/Базы данных",
    )
    assert not result.warning
    assert result.file.path == "materials/Базы данных/file.csv"
    assert local_material_path(
        tmp_path,
        result.file,
        relative_directory="materials/Базы данных",
    ).read_bytes() == b"manual"


def test_redirect_and_filename_from_final_url(tmp_path):
    request = _HttpRedirects().redirect_request(Request(OPEN_URL), None, 302, "Found", {},
                                                "https://cdn.example.com/final.csv")
    assert request.full_url == "https://cdn.example.com/final.csv"
    result = MaterialDownloader(Opener(Response(url=request.full_url))).sync(OPEN_URL, tmp_path)
    assert result.file.path == "materials/final.csv"
    assert result.file.source_url == OPEN_URL
    with pytest.raises(ValueError):
        _HttpRedirects().redirect_request(Request(OPEN_URL), None, 302, "Found", {}, "file:///etc/passwd")


@pytest.mark.parametrize("response", [
    Response(b"<html>landing</html>", headers={"Content-Type": "text/html"}),
    Response(b"<!DOCTYPE html><html>captcha</html>", headers={"Content-Type": "application/octet-stream"}),
    Response("<html>login</html>".encode("utf-16"), headers={"Content-Type": "application/octet-stream"}),
    Response(b"partial", headers={"Content-Type": "text/csv", "Content-Length": "100"}),
    Response(status=403),
])
def test_bad_response_is_external_only(tmp_path, response):
    result = MaterialDownloader(Opener(response)).sync(OPEN_URL, tmp_path)
    assert result.file is None and result.warning
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.csv"))


@pytest.mark.parametrize("filename", ["../../outside.csv", "C:\\temp\\outside.csv", "/tmp/outside.csv", "CON", ".."])
def test_unsafe_filename_is_contained(tmp_path, filename):
    response = Response(headers={"Content-Type": "text/csv", "Content-Disposition": f'attachment; filename="{filename}"'})
    result = MaterialDownloader(Opener(response)).sync(OPEN_URL, tmp_path)
    assert not result.warning
    assert (tmp_path / result.file.path).resolve().parent == (tmp_path / "materials").resolve()
    assert local_material_path(tmp_path, result.file)


def test_rfc_filename_unknown_name_and_collision(tmp_path):
    opener = Opener(
        Response(headers={"Content-Type": "text/csv", "Content-Disposition": "attachment; filename*=UTF-8''data%20set.csv"}),
        Response(url="https://example.com/download", headers={"Content-Type": "application/octet-stream"}),
        Response(url="https://example.com/download", headers={"Content-Type": "application/octet-stream"}),
    )
    downloader = MaterialDownloader(opener)
    assert downloader.sync(OPEN_URL, tmp_path).file.path == "materials/data set.csv"
    first = downloader.sync(OPEN_URL + "1", tmp_path).file
    second = downloader.sync(OPEN_URL + "2", tmp_path).file
    assert first.path != second.path
    assert (tmp_path / first.path).is_file() and (tmp_path / second.path).is_file()


def test_conditional_get_updates_then_preserves_on_failure(tmp_path):
    headers = {"Content-Type": "text/csv", "ETag": '"v1"', "Last-Modified": "Mon, 07 Sep 2026 01:00:00 GMT"}
    opener = Opener(Response(b"old", headers=headers), HTTPError(OPEN_URL, 304, "Unchanged", {}, None),
                    Response(b"new", headers={**headers, "ETag": '"v2"'}), URLError("offline"))
    downloader = MaterialDownloader(opener)
    first = downloader.sync(OPEN_URL, tmp_path).file
    assert downloader.sync(OPEN_URL, tmp_path, first).file == first
    assert opener.requests[1].get_header("If-none-match") == '"v1"'
    assert opener.requests[1].get_header("If-modified-since") == headers["Last-Modified"]
    updated = downloader.sync(OPEN_URL, tmp_path, first).file
    assert updated.path == first.path
    assert (tmp_path / updated.path).read_bytes() == b"new"
    failed = downloader.sync(OPEN_URL, tmp_path, updated)
    assert failed.warning and failed.file == updated
    assert (tmp_path / updated.path).read_bytes() == b"new"


def test_deleted_file_is_downloaded_again(tmp_path):
    downloader = MaterialDownloader(Opener(Response(), Response()))
    first = downloader.sync(OPEN_URL, tmp_path).file
    (tmp_path / first.path).unlink()
    second = downloader.sync(OPEN_URL, tmp_path, first).file
    assert (tmp_path / second.path).is_file()


def api_response(data):
    return Response(json.dumps(data).encode(), headers={"Content-Type": "application/json"})


def test_yandex_public_file_and_metadata_cache(tmp_path):
    source = "https://disk.yandex.ru/d/example"
    info = {"type": "file", "name": "variants.xlsx", "sha256": "version1", "size": 4}
    opener = Opener(api_response(info), api_response({"href": "https://cdn.example.com/download"}),
                    Response(url="https://cdn.example.com/download", headers={"Content-Type": "application/octet-stream"}),
                    api_response(info), api_response({**info, "sha256": "version2"}),
                    api_response({"href": "https://cdn.example.com/download2"}), Response(b"updated"))
    downloader = MaterialDownloader(opener)
    first = downloader.sync(source, tmp_path).file
    assert first.path == "materials/variants.xlsx"
    assert first.remote_version == "version1"
    assert downloader.sync(source, tmp_path, first).file == first
    assert len(opener.requests) == 4
    updated = downloader.sync(source, tmp_path, first).file
    assert updated.remote_version == "version2"
    assert (tmp_path / updated.path).read_bytes() == b"updated"
    assert "public_key=https%3A%2F%2Fdisk.yandex.ru%2Fd%2Fexample" in opener.requests[0].full_url


@pytest.mark.parametrize("response", [api_response({"type": "dir"}), api_response({}),
    HTTPError(OPEN_URL, 403, "Forbidden", {}, None), Response(b"<html>captcha</html>")])
def test_yandex_folder_auth_or_invalid_response_stays_external(tmp_path, response):
    opener = Opener(response)
    result = MaterialDownloader(opener).sync("https://disk.yandex.ru/d/example", tmp_path)
    assert result.file is None and result.warning
    assert len(opener.requests) == 1


def test_size_limit_and_state_path_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("lab_auto.materials.MAX_BYTES", 2)
    assert MaterialDownloader(Opener(Response(b"large"))).sync(OPEN_URL, tmp_path).warning
    assert not list(tmp_path.rglob("*.tmp"))
    for path in ("../outside", "materials/../../outside", "C:/outside", "materials/..\\outside", "materials/file:stream"):
        assert local_material_path(tmp_path, MaterialFile(OPEN_URL, path)) is None


def test_materials_symlink_cannot_write_outside_work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (folder / "materials").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this host")
    result = MaterialDownloader(Opener(Response())).sync(OPEN_URL, folder)
    assert result.warning and result.file is None
    assert not list(outside.iterdir())


def test_changed_url_downloads_new_copy_without_overwriting_old(tmp_path):
    downloader = MaterialDownloader(Opener(Response(b"old"), Response(b"new")))
    first = downloader.sync(OPEN_URL, tmp_path).file
    second = downloader.sync(OPEN_URL + "changed", tmp_path, first).file
    assert first.path != second.path
    assert (tmp_path / first.path).read_bytes() == b"old"
    assert (tmp_path / second.path).read_bytes() == b"new"


def test_read_error_retains_previous_file(tmp_path):
    from http.client import IncompleteRead

    class BrokenResponse(Response):
        def read(self, size):
            raise IncompleteRead(b"partial")

    downloader = MaterialDownloader(Opener(Response(headers={"Content-Type": "text/csv", "ETag": "v1"}), BrokenResponse()))
    first = downloader.sync(OPEN_URL, tmp_path).file
    result = downloader.sync(OPEN_URL, tmp_path, first)
    assert result.file == first and result.warning
    assert (tmp_path / first.path).read_bytes() == b"data"
    assert not list(tmp_path.rglob("*.tmp"))


def test_sync_failure_is_nonfatal_and_pdf_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeBrowserSession, "page_html", lambda self, url: HTML)
    browser = FakeBrowser(SYNTHETIC_TASK_LIST_HTML)
    downloader = MaterialDownloader(Opener(*(URLError("offline") for _ in range(3))))
    records = SyncService(tmp_path, browser=browser, material_downloader=downloader).sync().records
    assert len(browser.downloads) == 3
    assert all(work.task_pdf.is_file() and work.description for work in records)
    assert all(work.additional_material_url == OPEN_URL and work.additional_material is None for work in records)
    assert "Открыть внешний материал" in (records[0].folder / "task.md").read_text(encoding="utf-8")
    assert "Additional material unavailable" in next((tmp_path / "logs").glob("*.md")).read_text(encoding="utf-8")


def test_local_material_state_rename_archive_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeBrowserSession, "page_html", lambda self, url: HTML)
    browser = FakeBrowser(SYNTHETIC_TASK_LIST_HTML)
    service = SyncService(tmp_path, browser=browser, material_downloader=MaterialDownloader(Opener(Response(), Response(), Response())))
    records = service.sync().records
    first = records[0]
    assert load_state(tmp_path)[0].additional_material == first.additional_material
    content = (first.folder / "task.md").read_text(encoding="utf-8")
    assert "[file.csv](materials/file.csv)" in content and OPEN_URL in content
    browser.html = browser.html.replace("не принят", "принят")
    renamed = service.sync().records[0]
    assert renamed.folder != first.folder
    assert local_material_path(renamed.folder, renamed.additional_material)
    assert archive_work(tmp_path, renamed.work_id).additional_material == renamed.additional_material
    assert unarchive_work(tmp_path, renamed.work_id).additional_material == renamed.additional_material
    assert archive_removed_works([], {renamed.work_id: renamed}, set(), "2099")[0].additional_material == renamed.additional_material


def test_source_change_failure_keeps_old_copy_with_correct_source(tmp_path):
    downloader = MaterialDownloader(Opener(Response(), URLError("offline")))
    first = downloader.sync(OPEN_URL, tmp_path).file
    record = make_record(tmp_path, LocalStatus.UNDONE)
    record.folder = tmp_path
    record.additional_material_url = "https://example.com/changed"
    record.additional_material = downloader.sync(record.additional_material_url, tmp_path, first).file
    generate_task_markdown(record)
    content = (tmp_path / "task.md").read_text(encoding="utf-8")
    assert OPEN_URL in content and record.additional_material_url in content
    assert "materials/file.csv" in content


def test_legacy_state_without_material(tmp_path):
    import yaml
    record = make_record(tmp_path, LocalStatus.UNDONE)
    payload = record.to_dict()
    payload.pop("additional_material")
    state = tmp_path / "state" / "works.yaml"
    state.parent.mkdir()
    state.write_text(yaml.safe_dump({"works": [payload]}), encoding="utf-8")
    assert load_state(tmp_path)[0].additional_material is None
