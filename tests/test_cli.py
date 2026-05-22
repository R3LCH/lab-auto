from typer.testing import CliRunner

from lab_auto.cli import app
from lab_auto.config import clear_saved_workspace, set_saved_workspace


runner = CliRunner()


def test_status_without_state_prints_empty_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No works synced yet" in result.output


def test_auth_migrate_session_upgrades_legacy_file(tmp_path, monkeypatch):
    import json

    from lab_auto.session_crypto import ensure_session_key

    key_dir = tmp_path / "config" / "lab-auto"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "lab_auto.session_crypto.session_key_path",
        lambda: key_dir / "session.key",
    )
    monkeypatch.delenv("LAB_AUTO_SESSION_KEY", raising=False)
    ensure_session_key()

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "storage_state.json").write_text(
        json.dumps({"cookies": [{"name": "sid", "value": "1"}], "origins": []}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--root", str(tmp_path), "auth", "migrate-session"])

    assert result.exit_code == 0
    assert "upgraded to encrypted format" in result.output


def test_auth_migrate_session_reports_already_encrypted(tmp_path, monkeypatch):
    from lab_auto.session_store import wrap_storage_state

    key_dir = tmp_path / "config" / "lab-auto"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "lab_auto.session_crypto.session_key_path",
        lambda: key_dir / "session.key",
    )
    monkeypatch.delenv("LAB_AUTO_SESSION_KEY", raising=False)

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    data = {"cookies": [{"name": "sid", "value": "1"}], "origins": []}
    (session_dir / "storage_state.json").write_text(
        wrap_storage_state(data),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--root", str(tmp_path), "auth", "migrate-session"])

    assert result.exit_code == 0
    assert "already encrypted" in result.output


def test_auth_logout_without_session_is_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["auth", "logout"])

    assert result.exit_code == 0
    assert "No local session" in result.output


def test_workspace_set_uses_saved_default(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    clear_saved_workspace()

    set_result = runner.invoke(app, ["workspace", "set", str(workspace)])
    assert set_result.exit_code == 0

    show_result = runner.invoke(app, ["workspace", "show"])
    assert show_result.exit_code == 0
    assert str(workspace.resolve()) in show_result.output

    clear_saved_workspace()


def test_sync_empty_task_list_warns_without_wiping_state(tmp_path, monkeypatch):
    from lab_auto.models import LocalStatus, WorkRecord
    from lab_auto.state import load_state, save_state
    from lab_auto.sync import SyncService

    class EmptyBrowserSession:
        def __init__(self, html: str) -> None:
            self.html = html

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            pass

        def task_list_html(self) -> str:
            return self.html

    class EmptyBrowser:
        def open_session(self):
            return EmptyBrowserSession("<html></html>")

    monkeypatch.chdir(tmp_path)
    save_state(
        tmp_path,
        [
            WorkRecord(
                work_id="keep-me",
                subject="Math",
                name="Lab 1",
                number="1",
                task_url="https://pro.guap.ru/inside/student/tasks/keep",
                due_date=None,
                website_status="не принят",
                local_status=LocalStatus.REFACTOR,
                folder=tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1",
                task_pdf=None,
                reports=[],
                last_sync=None,
                last_submit_attempt=None,
                last_submit_result=None,
            )
        ],
    )
    monkeypatch.setattr(
        "lab_auto.cli.SyncService",
        lambda root: SyncService(root, browser=EmptyBrowser()),
    )

    result = runner.invoke(app, ["--root", str(tmp_path), "sync"])

    assert result.exit_code == 0
    assert "parsed 0 tasks" in result.output.lower()
    assert "hint:" in result.output.lower()
    assert [work.work_id for work in load_state(tmp_path)] == ["keep-me"]
