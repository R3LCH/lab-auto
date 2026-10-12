"""Profiles fetched once and shared by all tasks in a workspace.

``teachers`` holds only pro.guap.ru profiles. Each ``subjects`` entry keeps the
original portal association in ``portal_teacher`` and the effective teacher
(portal or a guap.ru/rasp lab/practice correction) in its top-level fields.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from lab_auto.files import atomic_write_text
from lab_auto.models import Teacher

_VERSION = 2
_READABLE_VERSIONS = (None, 1, 2)
_INITIALS = re.compile(r"([^\W\d_])\.(?:([^\W\d_])\.)?")


def _teachers_path(root: Path) -> Path:
    return root / "state" / "teachers.yaml"


def _load_raw(root: Path) -> dict[str, Any] | None:
    path = _teachers_path(root)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in _READABLE_VERSIONS:
        raise ValueError(f"Invalid teacher state: {path}")
    return data


def load_teachers(root: Path) -> dict[str, Teacher]:
    data = _load_raw(root)
    if data is None:
        return {}
    if not isinstance(data.get("teachers"), list):
        raise ValueError(f"Invalid teacher state: {_teachers_path(root)}")
    teachers = [Teacher(**item) for item in data["teachers"]]
    return {teacher.profile_url: teacher for teacher in teachers}


def subject_key(subject: str, subject_site_id: str | None) -> str:
    return f"id:{subject_site_id}" if subject_site_id else "name:" + " ".join(subject.split()).casefold()


def load_subject_teachers(root: Path) -> dict[str, dict]:
    data = _load_raw(root)
    if data is None:
        return {}
    if not isinstance(data.get("subjects", []), list):
        raise ValueError(f"Invalid teacher state: {_teachers_path(root)}")
    return {subject_key(item["subject"], item.get("subject_site_id")): item
            for item in data.get("subjects", [])}


def save_teachers(root: Path, teachers: dict[str, Teacher], subjects: dict[str, dict] | None = None) -> None:
    existing = _load_raw(root) or {}
    if subjects is None:
        subjects = load_subject_teachers(root)
    payload: dict[str, Any] = {
        "version": _VERSION,
        "teachers": [asdict(teachers[url]) for url in sorted(teachers)],
        "subjects": [subjects[key] for key in sorted(subjects)],
    }
    # Keep unrelated top-level fields written by other tools or future versions.
    payload.update((key, value) for key, value in existing.items() if key not in payload)
    atomic_write_text(_teachers_path(root),
                      yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _teacher_from(data: dict) -> Teacher:
    return Teacher(data.get("profile_url") or "", data.get("name") or "", list(data.get("positions") or []))


def portal_baseline(entry: dict, teachers: dict[str, Teacher]) -> Teacher:
    """Original pro.guap.ru teacher of a subject; legacy v1 entries are portal-only."""
    snapshot = entry.get("portal_teacher")
    source = snapshot if isinstance(snapshot, dict) else entry
    return teachers.get(source.get("profile_url")) or _teacher_from(source)


def effective_teacher(entry: dict) -> Teacher:
    return _teacher_from(entry)


def set_effective(entry: dict, teacher: Teacher, *, source: str, timetable: dict | None = None) -> None:
    entry["profile_url"] = teacher.profile_url
    entry["name"] = teacher.name
    entry["positions"] = list(teacher.positions)
    entry["report_label"] = teacher.report_label
    entry["source"] = source
    if timetable:
        entry["timetable"] = dict(timetable)
    else:
        entry.pop("timetable", None)


def new_subject_entry(subject: str, subject_site_id: str | None, teacher: Teacher) -> dict:
    entry: dict[str, Any] = {"subject": subject, "subject_site_id": subject_site_id}
    set_effective(entry, teacher, source="portal")
    entry["portal_teacher"] = asdict(teacher)
    return entry


def record_portal_baseline(entry: dict, teacher: Teacher) -> bool:
    """Store the portal snapshot; portal-sourced effective fields follow it. Returns True if changed."""
    before = copy.deepcopy(entry)
    entry["portal_teacher"] = asdict(teacher)
    if entry.get("source", "portal") == "portal":
        set_effective(entry, teacher, source="portal")
    return entry != before


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def expand_abbreviated_name(teacher: Teacher, known: Iterable[Teacher]) -> Teacher:
    """Reuse a unique full portal name for 'Surname I.O.'; URL and positions stay the timetable's."""
    parts = teacher.name.split()
    if len(parts) < 2:
        return teacher
    match = _INITIALS.fullmatch("".join(parts[1:]))
    if not match:
        return teacher
    surname = _fold(parts[0])
    initials = [_fold(letter) for letter in match.groups() if letter]
    names: set[str] = set()
    for candidate in known:
        tokens = candidate.name.split()
        if len(tokens) != len(initials) + 1 or any("." in token for token in tokens):
            continue
        if _fold(tokens[0]) == surname and all(
            _fold(token)[0] == initial for token, initial in zip(tokens[1:], initials)
        ):
            names.add(" ".join(tokens))
    if len(names) != 1:
        return teacher
    return Teacher(teacher.profile_url, names.pop(), list(teacher.positions))
