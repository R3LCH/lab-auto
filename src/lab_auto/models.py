from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class LocalStatus(str, Enum):
    DONE = "DONE"
    REFACTOR = "REFACTOR"
    REVIEW = "REVIEW"
    SENT = "SENT"
    SENTFAILED = "SENTFAILED"
    UNDONE = "UNDONE"
    UNKNOWN = "UNKNOWN"

    @property
    def prefix(self) -> str:
        return f"[{self.value}]"


def _coerce_local_status(value: str) -> LocalStatus:
    return LocalStatus(value)


def _normalize_status_text(status_text: str) -> str:
    collapsed = " ".join(status_text.strip().split())
    for dash in ("—", "–", "−", "‑"):
        collapsed = collapsed.replace(dash, "-")
    return collapsed


def website_status_is_dash(status_text: str) -> bool:
    """True when GUAP shows no status yet (—, -, or empty)."""
    normalized = _normalize_status_text(status_text)
    return not normalized or normalized == "-"


def status_from_website(status_text: str) -> LocalStatus:
    if website_status_is_dash(status_text):
        return LocalStatus.UNDONE
    status = " ".join(status_text.strip().lower().split())
    if status == "принят":
        return LocalStatus.DONE
    if status == "ожидает проверки":
        return LocalStatus.SENT
    if status == "не принят":
        return LocalStatus.REFACTOR
    return LocalStatus.UNKNOWN


def resolve_local_status(
    website_status: str,
    previous: LocalStatus | None,
) -> LocalStatus:
    """Merge website status with workflow-only local statuses."""
    if website_status_is_dash(website_status):
        return LocalStatus.UNDONE
    website_mapped = status_from_website(website_status)
    if previous is None:
        return website_mapped
    if previous == LocalStatus.REVIEW:
        if website_mapped in (LocalStatus.DONE, LocalStatus.SENT):
            return website_mapped
        return LocalStatus.REVIEW
    if previous == LocalStatus.SENTFAILED:
        if website_mapped in (LocalStatus.DONE, LocalStatus.SENT):
            return website_mapped
        return LocalStatus.SENTFAILED
    if previous == LocalStatus.SENT:
        if website_mapped in (LocalStatus.DONE, LocalStatus.SENT):
            return website_mapped
        return LocalStatus.SENT
    if previous == LocalStatus.DONE:
        # Teachers do not "un-accept", but a resubmit moves the site to "ожидает проверки".
        if website_mapped == LocalStatus.REFACTOR:
            return LocalStatus.DONE
        return website_mapped
    if previous == LocalStatus.UNKNOWN:
        if website_mapped == LocalStatus.UNKNOWN:
            return LocalStatus.UNKNOWN
        return website_mapped
    if website_mapped == LocalStatus.UNKNOWN:
        return previous
    return website_mapped


_WORKFLOW_LOCAL_STATUSES = frozenset({
    LocalStatus.REVIEW,
    LocalStatus.SENT,
    LocalStatus.SENTFAILED,
})

_WORK_RECORD_FIELDS = frozenset({
    "work_id",
    "subject",
    "name",
    "number",
    "task_url",
    "task_site_id",
    "due_date",
    "website_status",
    "local_status",
    "folder",
    "task_pdf",
    "reports",
    "last_sync",
    "last_submit_attempt",
    "last_submit_result",
    "archived",
    "archived_at",
})


@dataclass(slots=True)
class WorkRecord:
    work_id: str
    subject: str
    name: str
    number: str | None
    task_url: str
    due_date: str | None
    website_status: str
    local_status: LocalStatus
    folder: Path
    task_pdf: Path | None
    reports: list[Path]
    last_sync: str | None
    last_submit_attempt: str | None
    last_submit_result: str | None
    archived: bool = False
    archived_at: str | None = None
    task_site_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "subject": self.subject,
            "name": self.name,
            "number": self.number,
            "task_url": self.task_url,
            "task_site_id": self.task_site_id,
            "due_date": self.due_date,
            "website_status": self.website_status,
            "local_status": self.local_status.value,
            "folder": str(self.folder),
            "task_pdf": str(self.task_pdf) if self.task_pdf else None,
            "reports": [str(report) for report in self.reports],
            "last_sync": self.last_sync,
            "last_submit_attempt": self.last_submit_attempt,
            "last_submit_result": self.last_submit_result,
            "archived": self.archived,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], root: Path | None = None) -> WorkRecord:
        unknown = set(data) - _WORK_RECORD_FIELDS
        if unknown:
            raise ValueError(f"Unknown work record fields: {', '.join(sorted(unknown))}")

        values = {key: data.get(key) for key in _WORK_RECORD_FIELDS}
        task_url = values.get("task_url") or ""
        if not values.get("task_site_id") and task_url:
            from lab_auto.paths import extract_task_site_id

            site_id = extract_task_site_id(task_url)
            values["task_site_id"] = site_id or None
        values["local_status"] = _coerce_local_status(str(values["local_status"]))
        folder = Path(values["folder"])
        values["folder"] = folder if folder.is_absolute() or root is None else root / folder
        task_pdf = values.get("task_pdf")
        if task_pdf:
            task_path = Path(task_pdf)
            values["task_pdf"] = task_path if task_path.is_absolute() or root is None else root / task_path
        else:
            values["task_pdf"] = None
        reports = []
        for report in values.get("reports") or []:
            report_path = Path(report)
            reports.append(
                report_path if report_path.is_absolute() or root is None else root / report_path
            )
        values["reports"] = reports
        values["archived"] = bool(values.get("archived", False))
        values.setdefault("archived_at", None)
        return cls(**values)


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_newer_timestamp(candidate: str | None, baseline: str | None) -> bool:
    parsed_candidate = _parse_iso_timestamp(candidate)
    parsed_baseline = _parse_iso_timestamp(baseline)
    if parsed_candidate is None:
        return False
    if parsed_baseline is None:
        return True
    return parsed_candidate > parsed_baseline


def merge_synced_work(synced: WorkRecord, on_disk: WorkRecord | None) -> WorkRecord:
    """Apply a sync snapshot without clobbering concurrent submit/review updates."""
    if on_disk is None:
        return synced
    if on_disk.archived:
        return on_disk

    local_status = synced.local_status
    if on_disk.local_status in _WORKFLOW_LOCAL_STATUSES:
        local_status = resolve_local_status(synced.website_status, on_disk.local_status)

    last_submit_attempt = synced.last_submit_attempt
    last_submit_result = synced.last_submit_result
    if _is_newer_timestamp(on_disk.last_submit_attempt, synced.last_submit_attempt):
        last_submit_attempt = on_disk.last_submit_attempt
        last_submit_result = on_disk.last_submit_result
        local_status = resolve_local_status(synced.website_status, on_disk.local_status)

    reports: list[Path] = []
    seen: set[Path] = set()
    for report in [*synced.reports, *on_disk.reports]:
        resolved = report.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        reports.append(resolved)

    task_site_id = synced.task_site_id or on_disk.task_site_id
    if not task_site_id:
        from lab_auto.paths import extract_task_site_id

        site_id = extract_task_site_id(synced.task_url)
        task_site_id = site_id or None

    return WorkRecord(
        work_id=synced.work_id,
        subject=synced.subject,
        name=synced.name,
        number=synced.number,
        task_url=synced.task_url,
        task_site_id=task_site_id,
        due_date=synced.due_date,
        website_status=synced.website_status,
        local_status=local_status,
        folder=synced.folder,
        task_pdf=synced.task_pdf,
        reports=reports,
        last_sync=synced.last_sync,
        last_submit_attempt=last_submit_attempt,
        last_submit_result=last_submit_result,
        archived=synced.archived,
        archived_at=synced.archived_at,
    )


def merge_state_after_sync(
    synced: list[WorkRecord],
    current: list[WorkRecord],
    *,
    dropped_ids: set[str],
) -> list[WorkRecord]:
    """Merge freshly synced works with on-disk state changed during the browser run."""
    current_by_id = {work.work_id: work for work in current}
    merged = [
        merge_synced_work(work, current_by_id.get(work.work_id))
        for work in synced
    ]
    seen = {work.work_id for work in merged}
    for work in current:
        if work.work_id in seen or work.work_id in dropped_ids:
            continue
        merged.append(work)
    return merged
