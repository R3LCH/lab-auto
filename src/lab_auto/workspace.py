from __future__ import annotations

from pathlib import Path

from lab_auto.models import WorkRecord


def _matches_work_ref(root: Path, work: WorkRecord, work_ref: str) -> bool:
    ref_path = Path(work_ref)
    if work.work_id == work_ref:
        return True
    if work.folder == ref_path or work.folder.name == work_ref:
        return True
    try:
        folder_rel = str(work.folder.relative_to(root.resolve()))
    except ValueError:
        folder_rel = str(work.folder)
    return folder_rel.replace("\\", "/") == work_ref.replace("\\", "/")


def _find_archived_match(root: Path, works: list[WorkRecord], work_ref: str) -> WorkRecord | None:
    for work in works:
        if work.archived and _matches_work_ref(root, work, work_ref):
            return work
    return None


def find_work_in_list(
    root: Path,
    works: list[WorkRecord],
    work_ref: str,
    *,
    include_archived: bool = False,
) -> tuple[list[WorkRecord], WorkRecord]:
    if not include_archived:
        archived = _find_archived_match(root, works, work_ref)
        if archived is not None:
            raise ValueError(
                f"Work {archived.work_id!r} is archived. Run `lab-auto unarchive {archived.work_id}` first."
            )

    for work in works:
        if work.archived and not include_archived:
            continue
        if _matches_work_ref(root, work, work_ref):
            return works, work
    raise ValueError(f"Unknown work: {work_ref}")


def find_work(
    root: Path,
    work_ref: str,
    *,
    include_archived: bool = False,
) -> tuple[list[WorkRecord], WorkRecord]:
    from lab_auto.state import load_state

    works = load_state(root)
    return find_work_in_list(
        root,
        works,
        work_ref,
        include_archived=include_archived,
    )
