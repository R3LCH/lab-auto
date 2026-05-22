from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from lab_auto.config import user_config_path
from lab_auto.files import atomic_write_text, restrict_private_file

_SESSION_KEY_NAME = "session.key"
_ENV_SESSION_KEY = "LAB_AUTO_SESSION_KEY"


class SessionKeyError(RuntimeError):
    """Raised when the session encryption key is missing or invalid."""


def session_key_path() -> Path:
    return user_config_path().parent / _SESSION_KEY_NAME


def _validate_fernet_key(key: bytes) -> bytes:
    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise SessionKeyError(
            "Session encryption key is invalid. "
            "Re-run `lab-auto auth login` or set a valid Fernet key in LAB_AUTO_SESSION_KEY."
        ) from exc
    return key


def load_session_key() -> bytes:
    """Return the Fernet key from LAB_AUTO_SESSION_KEY or the user config file."""
    env_key = os.environ.get(_ENV_SESSION_KEY, "").strip()
    if env_key:
        return _validate_fernet_key(env_key.encode("utf-8"))

    path = session_key_path()
    if not path.exists():
        raise SessionKeyError(
            f"Session encryption key not found at {path}. "
            "Run `lab-auto auth login` to create one."
        )
    return _validate_fernet_key(path.read_bytes().strip())


def ensure_session_key() -> bytes:
    """Load or create the Fernet key used to encrypt session/storage files."""
    env_key = os.environ.get(_ENV_SESSION_KEY, "").strip()
    if env_key:
        return _validate_fernet_key(env_key.encode("utf-8"))

    path = session_key_path()
    if path.exists():
        return _validate_fernet_key(path.read_bytes().strip())

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    atomic_write_text(path, key.decode("ascii") + "\n")
    restrict_private_file(path)
    return key


def encrypt_text(plaintext: str) -> str:
    token = Fernet(ensure_session_key()).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    try:
        payload = Fernet(load_session_key()).decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise SessionKeyError(
            "Cannot decrypt session file. The encryption key may have changed. "
            "Run `lab-auto auth logout` and `lab-auto auth login` again."
        ) from exc
    return payload.decode("utf-8")
