import pytest

from lab_auto.session_crypto import SessionKeyError, ensure_session_key, load_session_key, session_key_path


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


def test_ensure_session_key_creates_private_key_file(isolated_session_key):
    key = ensure_session_key()

    assert (isolated_session_key / "session.key").exists()
    assert load_session_key() == key


def test_load_session_key_requires_existing_file(isolated_session_key):
    with pytest.raises(SessionKeyError, match="not found"):
        load_session_key()
