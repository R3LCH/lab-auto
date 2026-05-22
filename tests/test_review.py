from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.review import mark_work_review
from lab_auto.state import load_state, save_state


def test_mark_work_review_updates_state_and_folder(tmp_path):
    folder = tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1"
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
                website_status="не принят",
                local_status=LocalStatus.REFACTOR,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    updated = mark_work_review(tmp_path, "math-lab-1")

    assert updated.local_status == LocalStatus.REVIEW
    assert updated.folder.name.startswith("[REVIEW]")
    assert load_state(tmp_path)[0].local_status == LocalStatus.REVIEW
