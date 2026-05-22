from pathlib import Path

from lab_auto.models import LocalStatus, WorkRecord, resolve_local_status, status_from_website


def test_maps_known_website_statuses_to_local_prefixes():
    assert status_from_website("принят") == LocalStatus.DONE
    assert status_from_website("не принят") == LocalStatus.REFACTOR
    assert status_from_website("ожидает проверки") == LocalStatus.SENT


def test_unknown_website_status_maps_to_unknown_tag():
    assert status_from_website("возвращено на исправление") == LocalStatus.UNKNOWN


def test_resolve_local_status_preserves_review_until_website_advances():
    assert resolve_local_status("не принят", LocalStatus.REVIEW) == LocalStatus.REVIEW
    assert resolve_local_status("ожидает проверки", LocalStatus.REVIEW) == LocalStatus.SENT
    assert resolve_local_status("принят", LocalStatus.REVIEW) == LocalStatus.DONE


def test_resolve_local_status_preserves_sentfailed_until_website_advances():
    assert resolve_local_status("не принят", LocalStatus.SENTFAILED) == LocalStatus.SENTFAILED
    assert resolve_local_status("ожидает проверки", LocalStatus.SENTFAILED) == LocalStatus.SENT


def test_resolve_local_status_preserves_sent_until_website_advances():
    assert resolve_local_status("не принят", LocalStatus.SENT) == LocalStatus.SENT
    assert resolve_local_status("ожидает проверки", LocalStatus.SENT) == LocalStatus.SENT
    assert resolve_local_status("принят", LocalStatus.SENT) == LocalStatus.DONE


def test_resolve_local_status_preserves_done_when_list_lags_but_advances_on_resubmit():
    assert resolve_local_status("не принят", LocalStatus.DONE) == LocalStatus.DONE
    assert resolve_local_status("ожидает проверки", LocalStatus.DONE) == LocalStatus.SENT
    assert resolve_local_status("принят", LocalStatus.DONE) == LocalStatus.DONE


def test_status_mapping_collapses_scraped_whitespace():
    assert status_from_website(" ожидает\nпроверки ") == LocalStatus.SENT


def test_work_record_serializes_paths_as_strings(tmp_path):
    record = WorkRecord(
        work_id="math-lab-1",
        subject="Math",
        name="Lab 1",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/abc",
        due_date="2026-06-01",
        website_status="принят",
        local_status=LocalStatus.DONE,
        folder=tmp_path / "labs" / "Math" / "[DONE] Lab 1",
        task_pdf=tmp_path / "labs" / "Math" / "[DONE] Lab 1" / "task.pdf",
        reports=[],
        last_sync=None,
        last_submit_attempt=None,
        last_submit_result=None,
    )

    data = record.to_dict()

    assert data["folder"].endswith("[DONE] Lab 1")
    assert data["local_status"] == "DONE"


def test_work_record_persists_task_site_id(tmp_path):
    record = WorkRecord(
        work_id="task-178541",
        subject="Math",
        name="Lab 1",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/178541",
        task_site_id="178541",
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

    assert WorkRecord.from_dict(record.to_dict()).task_site_id == "178541"


def test_work_record_derives_task_site_id_from_url(tmp_path):
    restored = WorkRecord.from_dict({
        "work_id": "task-178541",
        "subject": "Math",
        "name": "Lab 1",
        "task_url": "https://pro.guap.ru/inside/student/tasks/178541",
        "website_status": "не принят",
        "local_status": "REFACTOR",
        "folder": str(tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1"),
    })

    assert restored.task_site_id == "178541"


def test_work_record_round_trips_serialized_paths_and_status(tmp_path):
    record = WorkRecord(
        work_id="math-lab-1",
        subject="Math",
        name="Lab 1",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/abc",
        task_site_id="abc",
        due_date="2026-06-01",
        website_status="принят",
        local_status=LocalStatus.DONE,
        folder=tmp_path / "labs" / "Math" / "[DONE] Lab 1",
        task_pdf=tmp_path / "labs" / "Math" / "[DONE] Lab 1" / "task.pdf",
        reports=[tmp_path / "labs" / "Math" / "[DONE] Lab 1" / "report.docx"],
        last_sync="2026-05-20T12:00:00+03:00",
        last_submit_attempt=None,
        last_submit_result="ok",
    )

    restored = WorkRecord.from_dict(record.to_dict())

    assert restored == record
    assert isinstance(restored.folder, Path)
    assert isinstance(restored.task_pdf, Path)
    assert all(isinstance(report, Path) for report in restored.reports)


def test_work_record_from_dict_rejects_unknown_fields(tmp_path):
    try:
        WorkRecord.from_dict({
            "work_id": "math-lab-1",
            "subject": "Math",
            "name": "Lab 1",
            "task_url": "https://pro.guap.ru/inside/student/tasks/abc",
            "website_status": "не принят",
            "local_status": "REFACTOR",
            "folder": str(tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1"),
            "unexpected": True,
        })
    except ValueError as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_work_record_from_dict_defaults_nullable_fields(tmp_path):
    restored = WorkRecord.from_dict({
        "work_id": "math-lab-1",
        "subject": "Math",
        "name": "Lab 1",
        "task_url": "https://pro.guap.ru/inside/student/tasks/abc",
        "website_status": "не принят",
        "local_status": "REFACTOR",
        "folder": str(tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1"),
    })

    assert restored.number is None
    assert restored.task_pdf is None
    assert restored.reports == []
