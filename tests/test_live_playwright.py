"""Optional live checks against GUAP. Run: pytest -m live"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lab_auto.browser import BrowserService, session_path

pytestmark = pytest.mark.live


def _live_workspace_root() -> Path | None:
    raw = os.environ.get("LAB_AUTO_ROOT", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@pytest.mark.live
def test_live_session_reaches_task_list() -> None:
    root = _live_workspace_root()
    if root is None:
        pytest.skip("Set LAB_AUTO_ROOT to a workspace with a saved session.")
    storage = session_path(root)
    if not storage.exists():
        pytest.skip(f"No session file at {storage}. Run `lab-auto auth login` first.")

    ok, _migrated = BrowserService(root).validate_session()

    assert ok, "Session did not reach the GUAP task list. Run `lab-auto auth login`."
