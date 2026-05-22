from pathlib import Path

import yaml

from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import (
    StateFileError,
    generate_markdown_views,
    load_state,
    save_state,
    state_dir,
)


def make_record(
    root: Path,
    status: LocalStatus,
    *,
    work_id: str = "math-lab-1",
    subject: str = "Math",
    name: str = "Lab 1",
    due_date: str | None = "2026-06-01",
) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        subject=subject,
        name=name,
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/abc",
        due_date=due_date,
        website_status="ожидает проверки" if status == LocalStatus.SENT else "не принят",
        local_status=status,
        folder=root / "labs" / subject / f"{status.prefix} {name}",
        task_pdf=root / "labs" / subject / f"{status.prefix} {name}" / "task.pdf",
        reports=[],
        last_sync="2026-05-20T12:00:00+03:00",
        last_submit_attempt=None,
        last_submit_result=None,
    )


def test_state_dir_creates_state_folder(tmp_path):
    result = state_dir(tmp_path)

    assert result == tmp_path / "state"
    assert result.is_dir()


def test_load_state_returns_empty_list_when_state_file_missing(tmp_path):
    assert load_state(tmp_path) == []


def test_load_state_raises_on_corrupt_file(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    (path / "works.yaml").write_text("{broken: [", encoding="utf-8")

    try:
        load_state(tmp_path)
    except StateFileError as exc:
        assert "works.yaml" in str(exc)
    else:
        raise AssertionError("expected StateFileError")


def test_save_and_load_state_uses_relative_paths(tmp_path):
    record = make_record(tmp_path, LocalStatus.SENT)
    save_state(tmp_path, [record])

    text = (tmp_path / "state" / "works.yaml").read_text(encoding="utf-8")
    assert "labs/" in text or "labs\\" in text
    loaded = load_state(tmp_path)[0]
    assert loaded.folder.is_absolute()
    assert str(loaded.folder).startswith(str(tmp_path.resolve()))


def test_save_and_load_state_round_trip(tmp_path):
    record = make_record(tmp_path, LocalStatus.SENT)
    save_state(tmp_path, [record])
    loaded = load_state(tmp_path)
    assert loaded[0].work_id == "math-lab-1"
    assert loaded[0].local_status == LocalStatus.SENT


def test_save_state_sorts_by_work_id_and_keeps_unicode(tmp_path):
    second = make_record(
        tmp_path,
        LocalStatus.DONE,
        work_id="z-lab",
        subject="Физика",
        name="Лабораторная 2",
    )
    first = make_record(tmp_path, LocalStatus.REVIEW, work_id="a-lab")

    save_state(tmp_path, [second, first])

    state_text = (tmp_path / "state" / "works.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(state_text)
    assert state_text.index("a-lab") < state_text.index("z-lab")
    assert "Физика" in state_text
    assert [work["work_id"] for work in parsed["works"]] == ["a-lab", "z-lab"]
    assert [work.work_id for work in load_state(tmp_path)] == ["a-lab", "z-lab"]


def test_generate_markdown_views_groups_summary_and_review(tmp_path):
    review = make_record(tmp_path, LocalStatus.REVIEW)
    sent = make_record(tmp_path, LocalStatus.SENT)
    generate_markdown_views(tmp_path, [review, sent])
    summary = (tmp_path / "state" / "summary.md").read_text(encoding="utf-8")
    needs_review = (tmp_path / "state" / "needs_review.md").read_text(encoding="utf-8")
    assert "## Math" in summary
    assert "[SENT] Lab 1" in summary
    assert "math-lab-1" in needs_review
    assert "[SENT]" not in needs_review


def test_generate_markdown_views_marks_missing_due_dates_and_empty_review(tmp_path):
    record = make_record(tmp_path, LocalStatus.SENT, due_date=None)

    generate_markdown_views(tmp_path, [record])

    summary = (tmp_path / "state" / "summary.md").read_text(encoding="utf-8")
    needs_review = (tmp_path / "state" / "needs_review.md").read_text(encoding="utf-8")
    assert "no due date" in summary
    assert "No works need review." in needs_review


def test_generate_markdown_views_sanitizes_markdown_visible_fields(tmp_path):
    subject = "Math\n## Injected"
    name = "Lab `one`\n- injected"
    record = make_record(
        tmp_path,
        LocalStatus.REVIEW,
        subject=subject,
        name=name,
        work_id="math-injected",
    )

    generate_markdown_views(tmp_path, [record])

    summary = (tmp_path / "state" / "summary.md").read_text(encoding="utf-8")
    needs_review = (tmp_path / "state" / "needs_review.md").read_text(encoding="utf-8")
    assert summary.count("- `math-injected`") == 1
    assert needs_review.count("- `math-injected`") == 1
    assert "## Math ## Injected" in summary
    assert "\n## Injected" not in summary
    assert "\n- injected" not in summary
    assert "\n- injected" not in needs_review
    assert "`one`" not in summary
