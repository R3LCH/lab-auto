"""Profiles fetched once and shared by all tasks in a workspace."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import yaml

from lab_auto.files import atomic_write_text
from lab_auto.models import Teacher


def load_teachers(root: Path) -> dict[str, Teacher]:
    path = root / "state" / "teachers.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("teachers"), list):
        raise ValueError(f"Invalid teacher state: {path}")
    teachers = [Teacher(**item) for item in data["teachers"]]
    return {teacher.profile_url: teacher for teacher in teachers}


def subject_key(subject: str, subject_site_id: str | None) -> str:
    return f"id:{subject_site_id}" if subject_site_id else "name:" + " ".join(subject.split()).casefold()


def load_subject_teachers(root: Path) -> dict[str, dict]:
    path = root / "state" / "teachers.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("subjects", []), list):
        raise ValueError(f"Invalid teacher state: {path}")
    return {subject_key(item["subject"], item.get("subject_site_id")): item
            for item in data.get("subjects", [])}


def save_teachers(root: Path, teachers: dict[str, Teacher], subjects: dict[str, dict] | None = None) -> None:
    if subjects is None:
        subjects = load_subject_teachers(root)
    payload = {
        "version": 1,
        "teachers": [asdict(teachers[url]) for url in sorted(teachers)],
        "subjects": [subjects[key] for key in sorted(subjects)],
    }
    atomic_write_text(root / "state" / "teachers.yaml",
                      yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
