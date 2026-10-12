"""User-selected GUAP group used to correct lab/practice teachers from guap.ru/rasp."""
from __future__ import annotations

from pathlib import Path

import yaml

from lab_auto.files import atomic_write_text

GROUP_FILE = Path("state") / "group.yaml"
_VERSION = 1


def normalize_group_number(number: str) -> str:
    normalized = " ".join(str(number).split())
    if not normalized:
        raise ValueError("Group number is empty")
    return normalized


def load_group(root: Path) -> str | None:
    path = root / GROUP_FILE
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid group file {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") not in (None, _VERSION):
        raise ValueError(f"Invalid group file: {path}")
    group = data.get("group")
    # Unquoted numbers are parsed by YAML as int; booleans are never group numbers.
    if isinstance(group, bool) or not isinstance(group, (str, int)):
        raise ValueError(f"Invalid group file: {path}")
    try:
        return normalize_group_number(str(group))
    except ValueError as exc:
        raise ValueError(f"Invalid group file {path}: {exc}") from exc


def save_group(root: Path, number: str) -> None:
    payload = {"version": _VERSION, "group": normalize_group_number(number)}
    atomic_write_text(root / GROUP_FILE, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def clear_group(root: Path) -> None:
    (root / GROUP_FILE).unlink(missing_ok=True)
