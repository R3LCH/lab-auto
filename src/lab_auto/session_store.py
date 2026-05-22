from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lab_auto.files import atomic_write_text, restrict_private_file
from lab_auto.session_crypto import SessionKeyError, decrypt_text, encrypt_text

_INNER_VERSION = 1
_OUTER_VERSION = 2


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _inner_wrapper(storage: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_json(storage).encode("utf-8")).hexdigest()
    return {"version": _INNER_VERSION, "sha256": digest, "storage": storage}


def _verify_inner_wrapper(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("version") != _INNER_VERSION:
        raise ValueError(f"Unsupported inner session version: {payload.get('version')!r}.")
    storage = payload.get("storage")
    if not isinstance(storage, dict):
        raise ValueError("Session wrapper field 'storage' must be an object.")
    digest = hashlib.sha256(_canonical_json(storage).encode("utf-8")).hexdigest()
    if digest != payload.get("sha256"):
        raise ValueError(
            "Session file integrity check failed (hash mismatch). "
            "Re-run `lab-auto auth login` or import cookies again."
        )
    return storage


def wrap_storage_state(data: dict[str, Any]) -> str:
    """Serialize Playwright storage with integrity check and Fernet encryption."""
    inner_text = json.dumps(_inner_wrapper(data), ensure_ascii=False, separators=(",", ":"))
    outer = {
        "version": _OUTER_VERSION,
        "encrypted": True,
        "ciphertext": encrypt_text(inner_text),
    }
    return json.dumps(outer, ensure_ascii=False, indent=2)


def unwrap_storage_state(text: str) -> dict[str, Any]:
    """Load storage state from encrypted, legacy wrapped, or plain Playwright JSON."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Session file must contain a JSON object.")

    if payload.get("version") == _OUTER_VERSION and payload.get("encrypted"):
        ciphertext = payload.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ValueError("Encrypted session file is missing ciphertext.")
        try:
            inner_payload = json.loads(decrypt_text(ciphertext))
        except SessionKeyError:
            raise
        except json.JSONDecodeError as exc:
            raise ValueError("Decrypted session payload is not valid JSON.") from exc
        if not isinstance(inner_payload, dict):
            raise ValueError("Decrypted session payload must be a JSON object.")
        return _verify_inner_wrapper(inner_payload)

    if "sha256" in payload and "storage" in payload:
        return _verify_inner_wrapper(payload)

    if "cookies" in payload:
        return payload

    raise ValueError("Session file must contain Playwright storage state or an encrypted wrapper.")


def session_file_needs_rewrap(text: str) -> bool:
    """True when the on-disk session is legacy plaintext or v1 hash-only."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        return False
    return not (
        payload.get("version") == _OUTER_VERSION and payload.get("encrypted") is True
    )


def rewrap_session_file(path: Path) -> bool:
    """Upgrade a legacy session file to the encrypted v2 envelope."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if not session_file_needs_rewrap(text):
        return False
    storage = unwrap_storage_state(text)
    atomic_write_text(path, wrap_storage_state(storage))
    restrict_private_file(path)
    return True
