from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from lab_auto.cli import app
from lab_auto.groups import load_group, save_group
from lab_auto.timetable import TimetableError, TimetableNetworkError

runner = CliRunner()

PROMPT = "GUAP group for lab/practice teacher corrections"


def _fake_timetable(monkeypatch, error: Exception | None = None) -> list[str]:
    calls: list[str] = []

    class FakeTimetableService:
        def resolve_group(self, number: str):
            calls.append(number)
            if error is not None:
                raise error
            return SimpleNamespace(number=number, site_id="site-" + number)

    monkeypatch.setattr("lab_auto.cli.TimetableService", FakeTimetableService)
    return calls


def _fake_sync(monkeypatch, warnings: list[str] | None = None) -> list[dict]:
    calls: list[dict] = []

    class FakeSyncService:
        def __init__(self, root) -> None:
            self.root = root

        def sync(self, archive_removed: bool = False):
            calls.append({"root": self.root, "archive_removed": archive_removed})
            return SimpleNamespace(
                records=[],
                dropped_from_site=[],
                empty_task_list=False,
                parse_warning=None,
                warnings=list(warnings or []),
            )

    monkeypatch.setattr("lab_auto.cli.SyncService", FakeSyncService)
    return calls


def _invoke(tmp_path, *args: str, input: str | None = None):
    return runner.invoke(app, ["--root", str(tmp_path), *args], input=input)


def test_group_set_saves_and_show_reads_it(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)

    result = _invoke(tmp_path, "group", "set", "  1232 ")

    assert result.exit_code == 0, result.output
    assert calls == ["1232"]
    assert load_group(tmp_path) == "1232"

    shown = _invoke(tmp_path, "group", "show")
    assert shown.exit_code == 0
    assert "1232" in shown.output


def test_group_set_invalid_exits_and_saves_nothing(tmp_path, monkeypatch):
    _fake_timetable(monkeypatch, TimetableError("unknown group 9999"))

    result = _invoke(tmp_path, "group", "set", "9999")

    assert result.exit_code == 1
    assert load_group(tmp_path) is None


def test_group_set_empty_number_is_invalid(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)

    result = _invoke(tmp_path, "group", "set", "   ")

    assert result.exit_code == 1
    assert calls == []
    assert load_group(tmp_path) is None


def test_group_set_network_error_keeps_previous_group(tmp_path, monkeypatch):
    save_group(tmp_path, "1232")
    _fake_timetable(monkeypatch, TimetableNetworkError("connection refused"))

    result = _invoke(tmp_path, "group", "set", "1245")

    assert result.exit_code == 1
    assert load_group(tmp_path) == "1232"


def test_group_unset_clears(tmp_path):
    save_group(tmp_path, "1232")

    result = _invoke(tmp_path, "group", "unset")

    assert result.exit_code == 0
    assert load_group(tmp_path) is None


def test_sync_with_group_option_replaces_saved_group_and_syncs(tmp_path, monkeypatch):
    _fake_timetable(monkeypatch)
    syncs = _fake_sync(monkeypatch)
    save_group(tmp_path, "1232")

    result = _invoke(tmp_path, "sync", "--group", "1245")

    assert result.exit_code == 0, result.output
    assert load_group(tmp_path) == "1245"
    assert len(syncs) == 1


def test_sync_with_invalid_group_option_fails_without_sync(tmp_path, monkeypatch):
    _fake_timetable(monkeypatch, TimetableError("unknown group"))
    syncs = _fake_sync(monkeypatch)

    result = _invoke(tmp_path, "sync", "--group", "9999")

    assert result.exit_code == 1
    assert syncs == []
    assert load_group(tmp_path) is None


def test_sync_interactive_prompts_once_and_saves(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)
    syncs = _fake_sync(monkeypatch)
    monkeypatch.setattr("lab_auto.cli._is_interactive", lambda: True)

    result = _invoke(tmp_path, "sync", input="1232\n")

    assert result.exit_code == 0, result.output
    assert result.output.count(PROMPT) == 1
    assert calls == ["1232"]
    assert load_group(tmp_path) == "1232"
    assert len(syncs) == 1


def test_sync_interactive_empty_answer_skips(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)
    syncs = _fake_sync(monkeypatch)
    monkeypatch.setattr("lab_auto.cli._is_interactive", lambda: True)

    result = _invoke(tmp_path, "sync", input="\n")

    assert result.exit_code == 0, result.output
    assert result.output.count(PROMPT) == 1
    assert calls == []
    assert load_group(tmp_path) is None
    assert len(syncs) == 1


def test_sync_interactive_invalid_answer_warns_and_continues(tmp_path, monkeypatch):
    _fake_timetable(monkeypatch, TimetableError("unknown group"))
    syncs = _fake_sync(monkeypatch)
    monkeypatch.setattr("lab_auto.cli._is_interactive", lambda: True)

    result = _invoke(tmp_path, "sync", input="9999\n")

    assert result.exit_code == 0, result.output
    assert load_group(tmp_path) is None
    assert len(syncs) == 1


def test_sync_noninteractive_without_group_warns_and_syncs(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)
    syncs = _fake_sync(monkeypatch)
    monkeypatch.setattr("lab_auto.cli._is_interactive", lambda: False)

    result = _invoke(tmp_path, "sync")

    assert result.exit_code == 0, result.output
    assert PROMPT not in result.output
    assert "group set" in result.output
    assert calls == []
    assert load_group(tmp_path) is None
    assert len(syncs) == 1


def test_sync_with_saved_group_does_not_prompt_or_warn(tmp_path, monkeypatch):
    calls = _fake_timetable(monkeypatch)
    syncs = _fake_sync(monkeypatch)
    monkeypatch.setattr("lab_auto.cli._is_interactive", lambda: True)
    save_group(tmp_path, "1232")

    result = _invoke(tmp_path, "sync")

    assert result.exit_code == 0, result.output
    assert PROMPT not in result.output
    assert calls == []
    assert len(syncs) == 1


