from lab_auto.archive import archive_work, unarchive_work
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import archived_works, load_state, save_state


def _record(root, work_id: str = "math-lab-1") -> WorkRecord:
    folder = root / "labs" / "Math" / "[REFACTOR] Lab 1"
    folder.mkdir(parents=True)
    return WorkRecord(
        work_id=work_id,
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


def test_archive_removed_preserves_existing_archived_at(tmp_path):
    from lab_auto.archive import archive_removed_works
    from lab_auto.models import LocalStatus, WorkRecord

    previous = WorkRecord(
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
        last_sync="2026-05-01T12:00:00+03:00",
        last_submit_attempt=None,
        last_submit_result=None,
        archived=True,
        archived_at="2026-05-05T08:00:00+03:00",
    )
    result = archive_removed_works([], {"old-work": previous}, set(), "2026-05-20T12:00:00+03:00")

    assert result[0].archived_at == "2026-05-05T08:00:00+03:00"


def test_archive_and_unarchive_work(tmp_path):
    save_state(tmp_path, [_record(tmp_path)])

    archived = archive_work(tmp_path, "math-lab-1")
    assert archived.archived is True
    assert archived.archived_at is not None
    assert len(archived_works(load_state(tmp_path))) == 1

    restored = unarchive_work(tmp_path, "math-lab-1")
    assert restored.archived is False
    assert restored.archived_at is None
