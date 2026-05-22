from __future__ import annotations

import json
import warnings
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

from lab_auto.files import atomic_write_text, workspace_lock
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.paths import build_work_id, legacy_work_id, rel_path

_STATE_FILE = "works.yaml"
_LOCK_FILE = ".workspace.lock"
_STATE_VERSION = 1


class StateFileError(ValueError):
    """Raised when works.yaml cannot be read or parsed."""


def state_dir(root: Path) -> Path:
    path = root / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(root: Path) -> Path:
    return state_dir(root) / _STATE_FILE


def _lock_path(root: Path) -> Path:
    return state_dir(root) / _LOCK_FILE


def _load_raw_state(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as yaml_error:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as json_error:
            raise StateFileError(
                f"Cannot parse state file {path}: invalid YAML ({yaml_error}) "
                f"and invalid JSON ({json_error})."
            ) from json_error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise StateFileError(
            f"State file {path} must contain a mapping at the top level, "
            f"got {type(data).__name__}."
        )
    return data


def _records_from_data(root: Path, data: dict[str, Any]) -> list[WorkRecord]:
    resolved_root = root.resolve()
    file_version = data.get("version")
    path = _state_path(root)
    if file_version not in (None, _STATE_VERSION):
        warnings.warn(
            f"Unsupported state version {file_version!r} in {path}; expected {_STATE_VERSION}.",
            stacklevel=2,
        )
    by_id: dict[str, WorkRecord] = {}
    for work in data.get("works", []):
        if not isinstance(work, dict):
            continue
        record = WorkRecord.from_dict(work, root=resolved_root)
        if record.work_id in by_id:
            warnings.warn(
                f"Duplicate work_id {record.work_id!r} in {path}; keeping the last entry.",
                stacklevel=2,
            )
        by_id[record.work_id] = record
    return list(by_id.values())


def _load_records_unlocked(root: Path) -> list[WorkRecord]:
    path = _state_path(root)
    if not path.exists():
        return []
    data = _load_raw_state(path)
    return _records_from_data(root, data)


def _save_records_unlocked(root: Path, works: list[WorkRecord]) -> None:
    path = _state_path(root)
    resolved_root = root.resolve()
    sorted_works = sorted(works, key=lambda work: work.work_id)
    payload: dict[str, Any] = {
        "version": _STATE_VERSION,
        "works": [_serialize_work(resolved_root, work) for work in sorted_works],
    }
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    atomic_write_text(path, content)


def lookup_previous_work(
    old_by_id: dict[str, WorkRecord],
    old_by_url: dict[str, WorkRecord],
    *,
    task_url: str,
    subject: str,
    name: str,
) -> WorkRecord | None:
    return (
        old_by_url.get(task_url)
        or old_by_id.get(build_work_id(task_url, subject, name))
        or old_by_id.get(legacy_work_id(subject, name))
    )


@contextmanager
def locked_workspace(root: Path) -> Iterator[None]:
    with workspace_lock(_lock_path(root)):
        yield


def _serialize_work(root: Path, work: WorkRecord) -> dict[str, Any]:
    payload = work.to_dict()
    payload["folder"] = rel_path(root, work.folder)
    payload["task_pdf"] = rel_path(root, work.task_pdf)
    payload["reports"] = [rel_path(root, report) for report in work.reports]
    return payload


def active_works(works: list[WorkRecord]) -> list[WorkRecord]:
    return [work for work in works if not work.archived]


def archived_works(works: list[WorkRecord]) -> list[WorkRecord]:
    return [work for work in works if work.archived]


def load_state(root: Path) -> list[WorkRecord]:
    with workspace_lock(_lock_path(root)):
        return _load_records_unlocked(root)


def save_state(root: Path, works: list[WorkRecord]) -> None:
    with workspace_lock(_lock_path(root)):
        _save_records_unlocked(root, works)


def save_state_unlocked(root: Path, works: list[WorkRecord]) -> None:
    """Save while caller already holds the workspace lock."""
    _save_records_unlocked(root, works)


def load_state_unlocked(root: Path) -> list[WorkRecord]:
    """Load while caller already holds the workspace lock."""
    return _load_records_unlocked(root)


def markdown_text(value: object) -> str:
    return " ".join(str(value).split()).replace("`", "'")


def _work_markdown_line(work: WorkRecord) -> str:
    due_date = markdown_text(work.due_date or "no due date")
    work_id = markdown_text(work.work_id)
    name = markdown_text(work.name)
    folder = markdown_text(work.folder)
    site_id = markdown_text(work.task_site_id) if work.task_site_id else ""
    site_suffix = f" ({site_id})" if site_id else ""
    return f"- `{work_id}`{site_suffix} {work.local_status.prefix} {name} - {due_date} - `{folder}`"


def generate_markdown_views(root: Path, works: list[WorkRecord]) -> None:
    path = state_dir(root)
    sorted_works = sorted(active_works(works), key=lambda work: (work.subject, work.work_id))
    sorted_archived = sorted(archived_works(works), key=lambda work: (work.subject, work.work_id))

    summary_lines = ["# Works Summary", ""]
    by_subject: dict[str, list[WorkRecord]] = defaultdict(list)
    for work in sorted_works:
        by_subject[work.subject].append(work)

    if not by_subject:
        summary_lines.append("No works tracked.")
    for subject in sorted(by_subject):
        summary_lines.extend(["", f"## {markdown_text(subject)}", ""])
        for work in by_subject[subject]:
            summary_lines.append(_work_markdown_line(work))

    review_lines = ["# Needs Review", ""]
    review_works = [
        work for work in sorted_works if work.local_status == LocalStatus.REVIEW
    ]
    if not review_works:
        review_lines.append("No works need review.")
    else:
        for work in review_works:
            review_lines.append(_work_markdown_line(work))

    if sorted_archived:
        summary_lines.extend(["", "## Archived", ""])
        for work in sorted_archived:
            archived_at = markdown_text(work.archived_at or "unknown")
            summary_lines.append(
                f"- `{markdown_text(work.work_id)}` {work.local_status.prefix} "
                f"{markdown_text(work.name)} - archived {archived_at}"
            )

    atomic_write_text(path / "summary.md", "\n".join(summary_lines) + "\n")
    atomic_write_text(path / "needs_review.md", "\n".join(review_lines) + "\n")
