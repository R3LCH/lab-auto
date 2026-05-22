from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from lab_auto.files import atomic_write_text


class LogKind(Enum):
    AI = "AI"
    WEBSITE = "WEBSITE"


def _markdown_message(message: str) -> str:
    return " ".join(message.split()).replace("`", "'")


def _timestamp(now: str | None) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    return datetime.fromisoformat(now)


def append_log(root: Path, kind: LogKind, message: str, now: str | None = None) -> Path:
    timestamp = _timestamp(now)
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    path = log_dir / f"[{kind.value}] {timestamp.date().isoformat()}.md"
    time_text = timestamp.time().replace(microsecond=0).isoformat()
    line = f"- {time_text} - {_markdown_message(message)}\n"
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = ""
    atomic_write_text(path, content + line)
    return path
