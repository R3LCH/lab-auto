from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lab_auto.logging import LogKind, append_log
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.paths import extract_task_site_id, sync_work_folder
from lab_auto.state import (
    generate_markdown_views,
    load_state_unlocked,
    locked_workspace,
    save_state_unlocked,
)
from lab_auto.workspace import find_work_in_list


def mark_work_review(root: Path, work_ref: str) -> WorkRecord:
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    with locked_workspace(root):
        works, work = find_work_in_list(
            root,
            load_state_unlocked(root),
            work_ref,
        )
        work_id = work.work_id
        subject = work.subject
        name = work.name
        task_site_id = work.task_site_id or extract_task_site_id(work.task_url) or None

    folder = sync_work_folder(
        root,
        subject,
        name,
        LocalStatus.REVIEW,
        task_site_id=task_site_id,
    )

    with locked_workspace(root):
        works = load_state_unlocked(root)
        _, work = find_work_in_list(root, works, work_id)
        work.local_status = LocalStatus.REVIEW
        work.folder = folder
        save_state_unlocked(root, works)

    generate_markdown_views(root, works)
    append_log(root, LogKind.AI, f"{work_id}: marked [REVIEW]", now=now)
    return work
