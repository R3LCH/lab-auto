import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lab_auto.files import (
    _windows_acl_principals,
    atomic_write_text,
    restrict_private_file,
    workspace_lock,
)
from lab_auto.models import LocalStatus, WorkRecord
from lab_auto.state import load_state, save_state


def test_atomic_write_text_writes_content(tmp_path):
    path = tmp_path / "nested" / "file.txt"

    atomic_write_text(path, "hello")

    assert path.read_text(encoding="utf-8") == "hello"


def test_workspace_lock_serializes_access(tmp_path):
    lock_path = tmp_path / "state" / ".workspace.lock"
    order: list[str] = []

    with workspace_lock(lock_path):
        order.append("first")
    with workspace_lock(lock_path):
        order.append("second")

    assert order == ["first", "second"]


def test_save_state_and_load_state_round_trip(tmp_path):
    folder = tmp_path / "labs" / "Math" / "[REVIEW] Lab 1"
    folder.mkdir(parents=True)
    record = WorkRecord(
        work_id="task-abc",
        subject="Math",
        name="Lab 1",
        number="1",
        task_url="https://pro.guap.ru/inside/student/tasks/abc",
        due_date=None,
        website_status="не принят",
        local_status=LocalStatus.REVIEW,
        folder=folder,
        task_pdf=None,
        reports=[],
        last_sync=None,
        last_submit_attempt=None,
        last_submit_result=None,
    )

    save_state(tmp_path, [record])

    assert load_state(tmp_path)[0].work_id == "task-abc"


def test_restrict_private_file_chmods_on_unix(tmp_path):
    if os.name == "nt":
        pytest.skip("Unix chmod test")
    path = tmp_path / "secret.txt"
    path.write_text("data", encoding="utf-8")
    path.chmod(0o644)

    restrict_private_file(path)

    assert oct(path.stat().st_mode & 0o777) == oct(0o600)


def test_windows_acl_principals_prefers_whoami(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows principal test")
    monkeypatch.setenv("USERNAME", "localuser")
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setattr(
        "lab_auto.files.subprocess.run",
        MagicMock(
            return_value=MagicMock(returncode=0, stdout="CORP\\localuser\n", stderr="")
        ),
    )

    principals = _windows_acl_principals()

    assert principals[0] == "CORP\\localuser"
    assert "localuser" in principals


def test_restrict_private_file_uses_icacls_on_windows(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows icacls test")
    monkeypatch.setattr(
        "lab_auto.files._windows_acl_principals",
        lambda: ["CORP\\testuser"],
    )
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("lab_auto.files.subprocess.run", run)
    path = tmp_path / "secret.txt"
    path.write_text("data", encoding="utf-8")

    restrict_private_file(path)

    run.assert_called_once()
    args = run.call_args.args[0]
    assert args[0] == "icacls"
    assert args[-1] == "CORP\\testuser:F"
