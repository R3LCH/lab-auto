"""Persistent, subject-owned materials from GUAP's general Materials page."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from lab_auto.files import atomic_write_text
from lab_auto.materials import MaterialDownloader
from lab_auto.models import MaterialFile, WorkRecord
from lab_auto.parsers import ParsedSubjectMaterial, parse_subject_materials
from lab_auto.paths import safe_name

STATE_VERSION = 1
STATE_FILE = "materials.yaml"


@dataclass(slots=True)
class SubjectMaterial:
    material_id: str
    subject: str
    title: str
    subject_site_id: str | None = None
    added_at: str | None = None
    source_url: str | None = None
    download_url: str | None = None
    local_path: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    remote_version: str | None = None
    present: bool = True
    missing_since: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SubjectMaterial:
        allowed = cls.__dataclass_fields__
        return cls(**{name: item for name, item in value.items() if name in allowed})


@dataclass(slots=True)
class SubjectMaterialsResult:
    materials: list[SubjectMaterial]
    warnings: list[str]
    page_loaded: bool


def normalize_subject(value: str) -> str:
    return " ".join(value.split()).casefold()


def material_local_path(root: Path, material: SubjectMaterial) -> Path | None:
    if not material.local_path:
        return None
    relative = PurePosixPath(material.local_path)
    if relative.is_absolute() or len(relative.parts) < 3 or relative.parts[0] != "materials" or "\\" in material.local_path:
        return None
    target = root.joinpath(*relative.parts)
    base = (root / "materials").resolve()
    if target.is_symlink() or base not in target.resolve().parents:
        return None
    return target if target.is_file() else None


def load_subject_materials(root: Path) -> list[SubjectMaterial]:
    path = root / "state" / STATE_FILE
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("materials", []), list):
        raise ValueError(f"Invalid subject materials state: {path}")
    return [SubjectMaterial.from_dict(item) for item in data.get("materials", []) if isinstance(item, dict)]


def save_subject_materials(root: Path, materials: list[SubjectMaterial]) -> None:
    payload = {"version": STATE_VERSION, "materials": [asdict(item) for item in sorted(materials, key=lambda x: x.material_id)]}
    atomic_write_text(root / "state" / STATE_FILE,
                      yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def materials_for_work(work: WorkRecord, materials: list[SubjectMaterial]) -> list[SubjectMaterial]:
    current = [item for item in materials if item.present]
    if work.subject_site_id:
        return [item for item in current if item.subject_site_id == work.subject_site_id]
    subject = normalize_subject(work.subject)
    return [item for item in current if normalize_subject(item.subject) == subject]


def _external_file(item: SubjectMaterial) -> MaterialFile | None:
    if not item.local_path or not item.source_url:
        return None
    return MaterialFile(item.source_url, item.local_path, item.etag, item.last_modified, item.remote_version)


def sync_subject_materials(
    root: Path,
    session: Any,
    previous: list[SubjectMaterial],
    downloader: MaterialDownloader,
    *,
    base_url: str,
    now: str,
) -> SubjectMaterialsResult:
    try:
        parsed = parse_subject_materials(session.materials_html(), base_url)
    except Exception as exc:
        return SubjectMaterialsResult(previous, [f"Materials page unavailable: {type(exc).__name__}"], False)
    previous_by_id = {item.material_id: item for item in previous}
    result: list[SubjectMaterial] = []
    warnings: list[str] = []
    for row in parsed:
        old = previous_by_id.get(row.material_id)
        item = SubjectMaterial(**asdict(row))
        if row.download_url:
            existing = material_local_path(root, old) if old else None
            unchanged = bool(old and existing and old.added_at == row.added_at and old.download_url == row.download_url)
            if unchanged:
                item.local_path = old.local_path
            else:
                try:
                    base_directory = root / "materials"
                    directory = base_directory / safe_name(row.subject)
                    if base_directory.is_symlink() or directory.is_symlink():
                        raise ValueError("unsafe subject materials directory")
                    base_directory.mkdir(parents=True, exist_ok=True)
                    if directory.resolve().parent != base_directory.resolve():
                        raise ValueError("unsafe subject materials directory")
                    downloaded = session.download_named_file(row.download_url, directory, row.title, existing=existing)
                    if downloaded.resolve().parent != directory.resolve():
                        raise ValueError("download escaped subject materials directory")
                    item.local_path = downloaded.relative_to(root).as_posix()
                except Exception as exc:
                    item.local_path = old.local_path if existing else None
                    warnings.append(f"{row.material_id}: download failed ({type(exc).__name__})")
        elif row.source_url:
            downloaded = downloader.sync(
                row.source_url,
                root,
                _external_file(old) if old else None,
                relative_directory=f"materials/{safe_name(row.subject)}",
            )
            if downloaded.file:
                item.local_path = downloaded.file.path
                item.etag = downloaded.file.etag
                item.last_modified = downloaded.file.last_modified
                item.remote_version = downloaded.file.remote_version
            if downloaded.warning:
                warnings.append(f"{row.material_id}: {downloaded.warning}")
        result.append(item)
    seen = {item.material_id for item in result}
    for old in previous:
        if old.material_id not in seen:
            result.append(replace(old, present=False, missing_since=old.missing_since or now))
    return SubjectMaterialsResult(result, warnings, True)


def task_material_markdown(root: Path, work: WorkRecord, materials: list[SubjectMaterial]) -> str:
    relevant = materials_for_work(work, materials)
    if not relevant:
        return "Нет"
    lines: list[str] = []
    for item in sorted(relevant, key=lambda x: (x.title.casefold(), x.material_id)):
        local = material_local_path(root, item)
        if local:
            relative = Path(os.path.relpath(local, work.folder)).as_posix()
            target = relative.replace("%", "%25").replace(" ", "%20").replace("(", "%28").replace(")", "%29")
        else:
            target = item.source_url or item.download_url
        if target:
            title = item.title.replace("[", "\\[").replace("]", "\\]")
            lines.append(f"- [{title}]({target})")
    return "\n".join(lines) if lines else "Нет"
