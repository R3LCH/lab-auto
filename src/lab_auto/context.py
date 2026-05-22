from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lab_auto.config import resolve_workspace


@dataclass(slots=True)
class AppContext:
    root: Path
    verbose: bool = False


_context = AppContext(root=resolve_workspace())


def get_context() -> AppContext:
    return _context


def set_context(root: Path | None = None, *, verbose: bool = False) -> None:
    global _context
    _context = AppContext(root=resolve_workspace(root), verbose=verbose)
