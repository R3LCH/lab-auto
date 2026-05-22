from pathlib import Path

from lab_auto.models import LocalStatus, WorkRecord, merge_state_after_sync, merge_synced_work


def _record(
    root: Path,
    *,
    work_id: str = "task-178541",
    local_status: LocalStatus = LocalStatus.REFACTOR,
    website_status: str = "не принят",
    last_submit_attempt: str | None = None,
    last_submit_result: str | None = None,
) -> WorkRecord:
    folder = root / "labs" / "Math" / f"{local_status.prefix} Lab"
    folder.mkdir(parents=True, exist_ok=True)
    return WorkRecord(
        work_id=work_id,
        subject="Math",
        name="Lab",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/178541",
        task_site_id="178541",
        due_date=None,
        website_status=website_status,
        local_status=local_status,
        folder=folder,
        task_pdf=None,
        reports=[],
        last_sync="2026-05-20T13:00:00+03:00",
        last_submit_attempt=last_submit_attempt,
        last_submit_result=last_submit_result,
    )


def test_merge_synced_work_preserves_recent_submit(tmp_path):
    synced = _record(tmp_path, local_status=LocalStatus.REFACTOR, website_status="не принят")
    on_disk = _record(
        tmp_path,
        local_status=LocalStatus.SENT,
        website_status="ожидает проверки",
        last_submit_attempt="2026-05-20T14:00:00+03:00",
        last_submit_result="success",
    )

    merged = merge_synced_work(synced, on_disk)

    assert merged.local_status == LocalStatus.SENT
    assert merged.last_submit_attempt == "2026-05-20T14:00:00+03:00"
    assert merged.last_submit_result == "success"


def test_merge_synced_work_preserves_review_until_site_advances(tmp_path):
    synced = _record(tmp_path, local_status=LocalStatus.REFACTOR, website_status="не принят")
    on_disk = _record(tmp_path, local_status=LocalStatus.REVIEW, website_status="не принят")

    merged = merge_synced_work(synced, on_disk)

    assert merged.local_status == LocalStatus.REVIEW


def test_merge_state_after_sync_keeps_untouched_works(tmp_path):
    synced = _record(tmp_path, work_id="task-1")
    other = _record(tmp_path, work_id="task-2")

    merged = merge_state_after_sync([synced], [synced, other], dropped_ids=set())

    assert {work.work_id for work in merged} == {"task-1", "task-2"}


def test_merge_synced_work_uses_iso_timestamps_not_plain_strings(tmp_path):
    synced = _record(
        tmp_path,
        local_status=LocalStatus.REFACTOR,
        website_status="не принят",
        last_submit_attempt="2026-05-20T12:00:00+03:00",
    )
    on_disk = _record(
        tmp_path,
        local_status=LocalStatus.SENT,
        website_status="ожидает проверки",
        last_submit_attempt="2026-05-20T14:00:00+03:00",
        last_submit_result="success",
    )

    merged = merge_synced_work(synced, on_disk)

    assert merged.local_status == LocalStatus.SENT
    assert merged.last_submit_result == "success"


def test_merge_state_after_sync_preserves_concurrent_submit(tmp_path):
    """Simulate sync finishing while submit updated the same work on disk."""
    synced = _record(
        tmp_path,
        work_id="task-178541",
        local_status=LocalStatus.REFACTOR,
        website_status="не принят",
        last_submit_attempt=None,
    )
    on_disk = _record(
        tmp_path,
        work_id="task-178541",
        local_status=LocalStatus.SENT,
        website_status="ожидает проверки",
        last_submit_attempt="2026-05-20T14:00:00+03:00",
        last_submit_result="success",
    )

    merged_list = merge_state_after_sync([synced], [on_disk], dropped_ids=set())
    merged = next(work for work in merged_list if work.work_id == "task-178541")

    assert merged.local_status == LocalStatus.SENT
    assert merged.last_submit_result == "success"


def test_merge_state_after_sync_drops_removed_ids(tmp_path):
    synced = _record(tmp_path, work_id="task-1")
    dropped = _record(tmp_path, work_id="task-removed")

    merged = merge_state_after_sync([synced], [synced, dropped], dropped_ids={"task-removed"})

    assert [work.work_id for work in merged] == ["task-1"]
