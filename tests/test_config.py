from lab_auto.config import (
    clear_saved_workspace,
    get_saved_workspace,
    resolve_workspace,
    set_saved_workspace,
    user_config_path,
)


def test_workspace_set_and_resolve(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "my-labs"
    workspace.mkdir()

    set_saved_workspace(workspace)
    assert get_saved_workspace() == workspace.resolve()
    assert resolve_workspace() == workspace.resolve()
    assert resolve_workspace(tmp_path / "override") == (tmp_path / "override").resolve()

    clear_saved_workspace()
    assert get_saved_workspace() is None


def test_get_saved_workspace_raises_when_path_missing(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    missing = tmp_path / "missing-workspace"
    config_home.mkdir(parents=True)
    (config_home / "lab-auto").mkdir()
    (config_home / "lab-auto" / "config.yaml").write_text(
        f"workspace: {missing}\n",
        encoding="utf-8",
    )

    try:
        get_saved_workspace()
    except FileNotFoundError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_user_config_path_uses_xdg_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert user_config_path() == tmp_path / "xdg" / "lab-auto" / "config.yaml"
