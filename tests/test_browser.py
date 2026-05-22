import json

import pytest

from lab_auto.browser import SESSION_DIR, BrowserService, session_path
from lab_auto.session_crypto import SessionKeyError, ensure_session_key
from lab_auto.session_store import unwrap_storage_state


@pytest.fixture(autouse=True)
def isolated_session_key(tmp_path, monkeypatch):
    key_dir = tmp_path / "config" / "lab-auto"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "lab_auto.session_crypto.session_key_path",
        lambda: key_dir / "session.key",
    )
    monkeypatch.delenv("LAB_AUTO_SESSION_KEY", raising=False)
    ensure_session_key()


def test_session_path_points_to_json_file(tmp_path):
    assert session_path(tmp_path) == tmp_path / SESSION_DIR / "storage_state.json"


def test_import_cookie_file_accepts_cookie_list(tmp_path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(json.dumps([{"name": "sid", "value": "1", "domain": "pro.guap.ru", "path": "/"}]), encoding="utf-8")

    target = BrowserService(tmp_path).import_cookie_file(cookie_file)
    data = unwrap_storage_state(target.read_text(encoding="utf-8"))

    assert data["cookies"][0]["name"] == "sid"
    assert data["origins"] == []
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["encrypted"] is True
    assert "sid" not in target.read_text(encoding="utf-8")


def test_logout_deletes_session_file(tmp_path):
    path = session_path(tmp_path)
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")

    assert BrowserService(tmp_path).logout() is True
    assert not path.exists()
