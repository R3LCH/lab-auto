from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import save_state
from lab_auto.workspace import find_work


def test_find_work_reports_archived_instead_of_unknown(tmp_path):
    folder = tmp_path / "labs" / "Math" / "[DONE] Lab 1"
    folder.mkdir(parents=True)
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="math-lab-1",
                subject="Math",
                name="Lab 1",
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/abc",
                due_date=None,
                website_status="принят",
                local_status=LocalStatus.DONE,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
                archived=True,
                archived_at="2026-05-10T12:00:00+03:00",
            )
        ],
    )

    try:
        find_work(tmp_path, "math-lab-1")
    except ValueError as exc:
        assert "archived" in str(exc).lower()
        assert "unarchive" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
