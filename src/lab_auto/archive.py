from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lab_auto.logging import LogKind, append_log
from lab_auto.models import WorkRecord
from lab_auto.state import (
    generate_markdown_views,
    load_state_unlocked,
    locked_workspace,
    save_state_unlocked,
)
from lab_auto.workspace import find_work_in_list


def archive_work(root: Path, work_ref: str) -> WorkRecord:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with locked_workspace(root):
        works, work = find_work_in_list(
            root,
            load_state_unlocked(root),
            work_ref,
            include_archived=True,
        )
        work.archived = True
        work.archived_at = now
        save_state_unlocked(root, works)
    generate_markdown_views(root, works)
    append_log(root, LogKind.AI, f"{work.work_id}: archived", now=now)
    return work


def unarchive_work(root: Path, work_ref: str) -> WorkRecord:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with locked_workspace(root):
        works, work = find_work_in_list(
            root,
            load_state_unlocked(root),
            work_ref,
            include_archived=True,
        )
        work.archived = False
        work.archived_at = None
        save_state_unlocked(root, works)
    generate_markdown_views(root, works)
    append_log(root, LogKind.AI, f"{work.work_id}: unarchived", now=now)
    return work


def archive_removed_works(
    records: list[WorkRecord],
    old_by_id: dict[str, WorkRecord],
    synced_ids: set[str],
    now: str,
) -> list[WorkRecord]:
    archived_records = list(records)
    for work_id, previous in old_by_id.items():
        if work_id in synced_ids:
            continue
        archived_at = previous.archived_at if previous.archived and previous.archived_at else now
        archived_records.append(
            WorkRecord(
                work_id=previous.work_id,
                subject=previous.subject,
                name=previous.name,
                number=previous.number,
                task_url=previous.task_url,
                task_site_id=previous.task_site_id,
                due_date=previous.due_date,
                website_status=previous.website_status,
                local_status=previous.local_status,
                folder=previous.folder,
                task_pdf=previous.task_pdf,
                reports=list(previous.reports),
                last_sync=previous.last_sync,
                last_submit_attempt=previous.last_submit_attempt,
                last_submit_result=previous.last_submit_result,
                archived=True,
                archived_at=archived_at,
            )
        )
    return archived_records
