from __future__ import annotations

import os
import warnings
from pathlib import Path

import yaml

from lab_auto.files import atomic_write_text

_CONFIG_DIR_NAME = "lab-auto"
_CONFIG_FILE_NAME = "config.yaml"


def user_config_path() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME
    return Path.home() / ".config" / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME


def load_user_config() -> dict:
    path = user_config_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        warnings.warn(f"Invalid config file {path}: {exc}", stacklevel=2)
        return {}
    return data if isinstance(data, dict) else {}


def save_user_config(config: dict) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    )
    return path


def get_saved_workspace() -> Path | None:
    workspace = load_user_config().get("workspace")
    if not workspace:
        return None
    path = Path(str(workspace)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Saved workspace does not exist: {path}. Run `lab-auto workspace set <path>`."
        )
    return path


def set_saved_workspace(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    config = load_user_config()
    config["workspace"] = str(resolved)
    save_user_config(config)
    return Path(config["workspace"])


def clear_saved_workspace() -> None:
    config = load_user_config()
    config.pop("workspace", None)
    if config:
        save_user_config(config)
        return
    path = user_config_path()
    if path.exists():
        path.unlink()


def resolve_workspace(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    try:
        saved = get_saved_workspace()
    except FileNotFoundError as exc:
        warnings.warn(str(exc), stacklevel=2)
        return Path.cwd().resolve()
    if saved is not None:
        return saved
    return Path.cwd().resolve()
