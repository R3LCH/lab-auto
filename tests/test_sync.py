from pathlib import Path

from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.paths import build_work_id, sync_work_folder
from lab_auto.state import load_state, save_state
from lab_auto.browser import TaskPageTriggersDownloadError
from lab_auto.sync import SyncService
from conftest import first_fixture_task, fixture_task_with_status, load_fixture_task_list


class FakeBrowserSession:
    def __init__(self, browser: "FakeBrowser") -> None:
        self.browser = browser
        self.detail_fetches = 0

    def __enter__(self) -> FakeBrowserSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        pass

    def task_list_html(self) -> str:
        return self.browser.html

    def page_html(self, url: str) -> str:
        self.browser.detail_fetches += 1
        if "/download" in url:
            raise TaskPageTriggersDownloadError(url)
        from conftest import load_fixture_task_detail

        return load_fixture_task_detail()

    def download_file(self, url: str, destination: Path) -> None:
        self.browser.downloads.append((url, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4\n")


class FakeBrowser:
    def __init__(self, html: str) -> None:
        self.html = html
        self.downloads: list[tuple[str, Path]] = []
        self.detail_fetches = 0

    def open_session(self) -> FakeBrowserSession:
        return FakeBrowserSession(self)


def test_sync_preserves_review_status(tmp_path):
    html = load_fixture_task_list()
    sample = fixture_task_with_status("не принят")
    subject = sample.subject
    name = sample.name
    work_id = build_work_id(sample.task_url, subject, name)
    folder = sync_work_folder(
        tmp_path, subject, name, LocalStatus.REVIEW, task_site_id=sample.task_site_id
    )
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id=work_id,
                subject=subject,
                name=name,
                number=sample.number,
                task_url=sample.task_url,
                due_date=sample.due_date,
                website_status=sample.website_status,
                local_status=LocalStatus.REVIEW,
                folder=folder,
                task_pdf=folder / "task.pdf",
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )
    folder.joinpath("task.pdf").write_bytes(b"%PDF-1.4\n")

    records = SyncService(tmp_path, browser=FakeBrowser(html)).sync().records

    synced = next(record for record in records if record.work_id == work_id)
    loaded = next(record for record in load_state(tmp_path) if record.work_id == work_id)
    assert synced.local_status == LocalStatus.REVIEW
    assert loaded.local_status == LocalStatus.REVIEW


def test_sync_skips_updates_for_archived_work_still_on_portal(tmp_path):
    from lab_auto.models import LocalStatus, WorkRecord
    from lab_auto.paths import build_work_id
    from lab_auto.state import load_state, save_state

    html = load_fixture_task_list()
    sample = first_fixture_task()
    subject = sample.subject
    name = sample.name
    work_id = build_work_id(sample.task_url, subject, name)
    folder = tmp_path / "labs" / subject / "[REVIEW] Manual Archive"
    folder.mkdir(parents=True)
    original = WorkRecord(
        work_id=work_id,
        subject=subject,
        name=name,
        number="1",
        task_url=sample.task_url,
        task_site_id=sample.task_site_id,
        due_date=sample.due_date,
        website_status=sample.website_status,
        local_status=LocalStatus.REVIEW,
        folder=folder,
        task_pdf=None,
        reports=[],
        last_sync="2026-05-01T12:00:00+03:00",
        last_submit_attempt=None,
        last_submit_result=None,
        archived=True,
        archived_at="2026-05-10T12:00:00+03:00",
    )
    save_state(tmp_path, [original])

    records = SyncService(tmp_path, browser=FakeBrowser(html)).sync().records
    loaded = next(record for record in load_state(tmp_path) if record.work_id == work_id)

    assert loaded == original
    assert loaded.folder.name == "[REVIEW] Manual Archive"
    assert loaded.archived_at == "2026-05-10T12:00:00+03:00"
    assert any(record.work_id == work_id for record in records)


def test_sync_archives_removed_works_when_flag_set(tmp_path):
    from lab_auto.models import LocalStatus, WorkRecord
    from lab_auto.state import archived_works, load_state, save_state

    html = load_fixture_task_list()
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="old-work",
                subject="Physics",
                name="Old Lab",
                number=None,
                task_url="https://pro.guap.ru/inside/student/tasks/old",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.DONE,
                folder=tmp_path / "labs" / "Physics" / "[DONE] Old Lab",
                task_pdf=None,
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    records = SyncService(tmp_path, browser=FakeBrowser(html)).sync(archive_removed=True).records

    assert len(archived_works(records)) == 1
    assert archived_works(records)[0].work_id == "old-work"
    assert archived_works(load_state(tmp_path))[0].work_id == "old-work"


def test_sync_drops_removed_works_without_archive_flag(tmp_path):
    from lab_auto.models import LocalStatus, WorkRecord
    from lab_auto.state import load_state, save_state

    html = load_fixture_task_list()
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="old-work",
                subject="Physics",
                name="Old Lab",
                number=None,
                task_url="https://pro.guap.ru/inside/student/tasks/old",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.DONE,
                folder=tmp_path / "labs" / "Physics" / "[DONE] Old Lab",
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    result = SyncService(tmp_path, browser=FakeBrowser(html)).sync(archive_removed=False)

    assert [work.work_id for work in load_state(tmp_path)] != ["old-work"]
    assert result.dropped_from_site == ["old-work"]


def test_sync_skips_detail_page_when_pdf_and_status_unchanged(tmp_path):
    html = """
    <table class="table table-bordered">
      <thead><tr>
        <th>№</th><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th>
      </tr></thead>
      <tbody><tr>
        <td>1</td><td>не принят</td><td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/only">Cached Lab</a></td>
        <td>Math</td>
      </tr></tbody>
    </table>
    """
    subject = "Math"
    name = "Cached Lab"
    task_url = "https://pro.guap.ru/inside/student/tasks/only"
    work_id = build_work_id(task_url, subject, name)
    folder = sync_work_folder(
        tmp_path, subject, name, LocalStatus.DONE, task_site_id="only"
    )
    folder.joinpath("task.pdf").write_bytes(b"%PDF-1.4\n")
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id=work_id,
                subject=subject,
                name=name,
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/only",
                due_date="2026-06-02",
                website_status="не принят",
                local_status=LocalStatus.DONE,
                folder=folder,
                task_pdf=folder / "task.pdf",
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    browser = FakeBrowser(html)
    SyncService(tmp_path, browser=browser, base_url="https://pro.guap.ru").sync()

    assert browser.detail_fetches == 0


def test_sync_preserves_sent_when_list_still_shows_not_accepted(tmp_path):
    html = """
    <table class="table table-bordered">
      <thead><tr>
        <th>№</th><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th>
      </tr></thead>
      <tbody><tr>
        <td>1</td><td>не принят</td><td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/sent">Sent Lab</a></td>
        <td>Math</td>
      </tr></tbody>
    </table>
    """
    subject = "Math"
    name = "Sent Lab"
    task_url = "https://pro.guap.ru/inside/student/tasks/sent"
    work_id = build_work_id(task_url, subject, name)
    folder = sync_work_folder(
        tmp_path, subject, name, LocalStatus.SENT, task_site_id="sent"
    )
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id=work_id,
                subject=subject,
                name=name,
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/sent",
                due_date="2026-06-02",
                website_status="не принят",
                local_status=LocalStatus.SENT,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt="2026-05-20T13:00:00+03:00",
                last_submit_result="success",
            )
        ],
    )

    records = SyncService(tmp_path, browser=FakeBrowser(html), base_url="https://pro.guap.ru").sync().records
    synced = next(record for record in records if record.work_id == work_id)

    assert synced.local_status == LocalStatus.SENT
    assert synced.folder.name.startswith("[SENT]")


def test_sync_empty_task_list_preserves_state(tmp_path):
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="keep-me",
                subject="Math",
                name="Lab 1",
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/keep",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.REFACTOR,
                folder=tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1",
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    result = SyncService(tmp_path, browser=FakeBrowser("<html></html>")).sync()

    assert result.empty_task_list is True
    assert result.parse_warning
    assert [work.work_id for work in load_state(tmp_path)] == ["keep-me"]


def test_sync_refetches_pdf_when_deleted_locally(tmp_path):
    html = """
    <table class="table table-bordered">
      <thead><tr>
        <th>№</th><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th>
      </tr></thead>
      <tbody><tr>
        <td>1</td><td>не принят</td><td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/only">Cached Lab</a></td>
        <td>Math</td>
      </tr></tbody>
    </table>
    """
    subject = "Math"
    name = "Cached Lab"
    task_url = "https://pro.guap.ru/inside/student/tasks/only"
    work_id = build_work_id(task_url, subject, name)
    folder = sync_work_folder(
        tmp_path, subject, name, LocalStatus.REFACTOR, task_site_id="only"
    )
    task_pdf = folder / "task.pdf"
    task_pdf.write_bytes(b"%PDF-1.4\n")
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id=work_id,
                subject=subject,
                name=name,
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/only",
                due_date="2026-06-02",
                website_status="не принят",
                local_status=LocalStatus.REFACTOR,
                folder=folder,
                task_pdf=task_pdf,
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )
    task_pdf.unlink()

    browser = FakeBrowser(html)
    SyncService(tmp_path, browser=browser, base_url="https://pro.guap.ru").sync()

    assert browser.detail_fetches == 1
    assert task_pdf.exists()


def test_sync_done_moves_to_sent_when_list_shows_awaiting_review(tmp_path):
    html = """
    <table class="table table-bordered">
      <thead><tr>
        <th>№</th><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th>
      </tr></thead>
      <tbody><tr>
        <td>1</td><td>ожидает проверки</td><td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/done-resubmit">Done Lab</a></td>
        <td>Math</td>
      </tr></tbody>
    </table>
    """
    subject = "Math"
    name = "Done Lab"
    task_url = "https://pro.guap.ru/inside/student/tasks/done-resubmit"
    work_id = build_work_id(task_url, subject, name)
    folder = sync_work_folder(
        tmp_path, subject, name, LocalStatus.DONE, task_site_id="done-resubmit"
    )
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id=work_id,
                subject=subject,
                name=name,
                number="1",
                task_url=task_url,
                due_date="2026-06-02",
                website_status="принят",
                local_status=LocalStatus.DONE,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync="2026-05-20T12:00:00+03:00",
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    records = SyncService(tmp_path, browser=FakeBrowser(html), base_url="https://pro.guap.ru").sync().records
    synced = next(record for record in records if record.work_id == work_id)

    assert synced.local_status == LocalStatus.SENT
    assert synced.folder.name == "[SENT] Done Lab [done-resubmit]"
    assert synced.folder.exists()
    assert not folder.exists()


def test_sync_downloads_pdf_when_detail_page_triggers_download(tmp_path, monkeypatch):
    html = load_fixture_task_list()
    expected = first_fixture_task()
    browser = FakeBrowser(html)
    service = SyncService(tmp_path, browser=browser, base_url="https://pro.guap.ru")

    def failing_page_html(_self, url: str) -> str:
        raise TaskPageTriggersDownloadError(url)

    monkeypatch.setattr(FakeBrowserSession, "page_html", failing_page_html)

    records = service.sync().records
    synced = next(
        record
        for record in records
        if record.work_id == build_work_id(expected.task_url, expected.subject, expected.name)
    )

    assert synced.task_pdf.exists()
    assert browser.downloads[0][0].endswith(f"/inside/student/tasks/{expected.task_site_id}/download")


def test_sync_imports_submitted_reports_once(tmp_path, monkeypatch):
    html = load_fixture_task_list()
    expected = fixture_task_with_status("принят")
    browser = FakeBrowser(html)
    service = SyncService(tmp_path, browser=browser, base_url="https://pro.guap.ru")

    def page_html_with_reports(_self, url: str) -> str:
        from conftest import load_fixture_task_detail

        return load_fixture_task_detail()

    monkeypatch.setattr(FakeBrowserSession, "page_html", page_html_with_reports)

    records = service.sync().records
    work_id = build_work_id(expected.task_url, expected.subject, expected.name)
    synced = next(record for record in records if record.work_id == work_id)

    assert synced.reports
    assert synced.reports[0].name.startswith("site-report-")
    assert (synced.folder / "reports" / synced.reports[0].name).exists()

    download_count = len(browser.downloads)
    records_again = service.sync().records
    synced_again = next(record for record in records_again if record.work_id == work_id)
    assert len(synced_again.reports) == len(synced.reports)
    assert len(browser.downloads) == download_count


def test_sync_creates_state_folder_and_downloads_pdf(tmp_path):
    html = load_fixture_task_list()
    expected = first_fixture_task()
    browser = FakeBrowser(html)
    service = SyncService(tmp_path, browser=browser, base_url="https://pro.guap.ru")

    records = service.sync().records

    synced = next(
        (record for record in records if record.work_id == build_work_id(expected.task_url, expected.subject, expected.name)),
        records[0],
    )
    assert synced.subject == expected.subject
    assert synced.task_pdf.exists()
    assert (tmp_path / "state" / "works.yaml").exists()
    assert (tmp_path / "state" / "summary.md").exists()
    assert browser.downloads[0][0].endswith("/download")
