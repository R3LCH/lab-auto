from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
        restrict_private_file(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _windows_acl_principals() -> list[str]:
    """Return candidate Windows principals, preferring DOMAIN\\user from whoami."""
    principals: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        principals.append(cleaned)

    try:
        completed = subprocess.run(
            ["whoami"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode == 0:
            add(completed.stdout)
    except OSError:
        pass

    username = os.environ.get("USERNAME", "").strip()
    domain = os.environ.get("USERDOMAIN", "").strip()
    if username:
        add(username)
    if domain and username:
        add(f"{domain}\\{username}")

    try:
        add(os.getlogin())
    except OSError:
        pass

    return principals


def _restrict_private_file_windows(path: Path) -> None:
    for principal in _windows_acl_principals():
        try:
            completed = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{principal}:F",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode == 0:
                return
        except OSError:
            continue


def restrict_private_file(path: Path) -> None:
    """Best-effort: keep session/state files readable only by the current user."""
    if os.name == "nt":
        _restrict_private_file_windows(path)
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


@contextmanager
def workspace_lock(lock_path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise TimeoutError(
            f"Could not acquire workspace lock at {lock_path} within {timeout}s"
        ) from exc
