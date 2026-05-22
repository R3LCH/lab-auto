from lab_auto.convert import resolve_convert_paths
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import save_state


def test_resolve_convert_paths_uses_work_folder_by_default(tmp_path):
    folder = tmp_path / "labs" / "Math" / "[REVIEW] Lab 1"
    folder.mkdir(parents=True)
    docx = folder / "report.docx"
    docx.write_bytes(b"PK")
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="task-abc",
                subject="Math",
                name="Lab 1",
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/abc",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.REVIEW,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    docx_path, pdf_path = resolve_convert_paths(tmp_path, "task-abc", docx, None)

    assert docx_path == docx
    assert pdf_path == folder / "report.pdf"


def test_resolve_convert_paths_honors_custom_output(tmp_path):
    folder = tmp_path / "labs" / "Math" / "[REVIEW] Lab 1"
    folder.mkdir(parents=True)
    docx = folder / "report.docx"
    docx.write_bytes(b"PK")
    output = tmp_path / "out" / "final.pdf"
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="task-abc",
                subject="Math",
                name="Lab 1",
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/abc",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.REVIEW,
                folder=folder,
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )

    docx_path, pdf_path = resolve_convert_paths(tmp_path, "task-abc", docx, output)

    assert docx_path == docx
    assert pdf_path == output
