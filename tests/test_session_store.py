import json
from pathlib import Path

import pytest

from lab_auto.session_crypto import SessionKeyError, ensure_session_key
from lab_auto.session_store import (
    rewrap_session_file,
    session_file_needs_rewrap,
    unwrap_storage_state,
    wrap_storage_state,
)


@pytest.fixture
def isolated_session_key(tmp_path, monkeypatch):
    key_dir = tmp_path / "config" / "lab-auto"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "lab_auto.session_crypto.session_key_path",
        lambda: key_dir / "session.key",
    )
    monkeypatch.delenv("LAB_AUTO_SESSION_KEY", raising=False)
    return key_dir


def test_wrap_and_unwrap_storage_state_round_trip_encrypted(isolated_session_key):
    data = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
    text = wrap_storage_state(data)
    outer = json.loads(text)

    assert outer["version"] == 2
    assert outer["encrypted"] is True
    assert "sid" not in text

    restored = unwrap_storage_state(text)

    assert restored == data
    assert ensure_session_key()
    assert (isolated_session_key / "session.key").exists()


def test_unwrap_accepts_legacy_hash_wrapper(isolated_session_key):
    from lab_auto.session_store import _inner_wrapper

    data = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    text = json.dumps(_inner_wrapper(data), ensure_ascii=False)

    assert unwrap_storage_state(text) == data


def test_unwrap_rejects_hash_mismatch(isolated_session_key):
    from lab_auto.session_store import _inner_wrapper

    data = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    inner = _inner_wrapper(data)
    inner["sha256"] = "0" * 64
    text = json.dumps(inner)

    with pytest.raises(ValueError, match="integrity check failed"):
        unwrap_storage_state(text)


def test_session_file_needs_rewrap_detects_legacy_formats(isolated_session_key):
    data = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    assert session_file_needs_rewrap(json.dumps(data)) is True
    assert session_file_needs_rewrap(wrap_storage_state(data)) is False


def test_rewrap_session_file_upgrades_legacy_file(isolated_session_key, tmp_path):
    data = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert rewrap_session_file(path) is True
    assert session_file_needs_rewrap(path.read_text(encoding="utf-8")) is False
    assert unwrap_storage_state(path.read_text(encoding="utf-8")) == data


def test_offline_rewrap_without_browser(isolated_session_key, tmp_path):
    from lab_auto.browser import BrowserService

    data = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
    path = tmp_path / "session" / "storage_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")

    migrated = BrowserService(tmp_path).migrate_session_if_needed()

    assert migrated is True
    assert session_file_needs_rewrap(path.read_text(encoding="utf-8")) is False


def test_rewrap_session_file_skips_already_encrypted(isolated_session_key, tmp_path):
    data = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
    path = tmp_path / "storage_state.json"
    path.write_text(wrap_storage_state(data), encoding="utf-8")

    assert rewrap_session_file(path) is False


def test_unwrap_encrypted_requires_matching_key(isolated_session_key, monkeypatch):
    data = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    text = wrap_storage_state(data)

    other_dir = isolated_session_key.parent / "other"
    other_dir.mkdir()
    monkeypatch.setattr(
        "lab_auto.session_crypto.session_key_path",
        lambda: other_dir / "session.key",
    )
    ensure_session_key()

    with pytest.raises(SessionKeyError, match="Cannot decrypt"):
        unwrap_storage_state(text)
