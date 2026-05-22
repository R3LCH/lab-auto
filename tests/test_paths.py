from pathlib import Path

import pytest

from lab_auto.models import LocalStatus
from lab_auto.paths import (
    build_work_id,
    legacy_work_id,
    safe_name,
    status_folder_name,
    sync_work_folder,
    work_folder_label,
)


def test_safe_name_removes_windows_invalid_characters():
    assert safe_name('Lab: 1 / "Intro"?') == "Lab 1 Intro"


def test_safe_name_guards_windows_reserved_device_names():
    assert safe_name("CON.txt") == "_CON.txt"


def test_build_work_id_uses_task_site_id():
    url = "https://pro.guap.ru/inside/student/tasks/178540"
    assert build_work_id(url, "Математика", "ЛР №1: Ряды") == "task-178540"


def test_legacy_work_id_matches_truncated_folder_names():
    long_name = "A" * 150
    assert legacy_work_id("Subject", long_name) == legacy_work_id("Subject", long_name[:100])


def test_work_folder_label_puts_task_name_before_site_id():
    assert work_folder_label("Технологии реализации электронной таблицы", task_site_id="178541") == (
        "Технологии реализации электронной таблицы [178541]"
    )


def test_status_folder_name_includes_task_title_and_site_id():
    assert status_folder_name(
        "Lab 1",
        LocalStatus.SENT,
        task_site_id="178541",
    ) == "[SENT] Lab 1 [178541]"


def test_sync_work_folder_renames_existing_prefix(tmp_path):
    old = tmp_path / "labs" / "Math" / "[REFACTOR] Lab 1"
    old.mkdir(parents=True)
    result = sync_work_folder(
        tmp_path,
        "Math",
        "Lab 1",
        LocalStatus.DONE,
        task_site_id="178541",
    )
    assert result == tmp_path / "labs" / "Math" / "[DONE] Lab 1 [178541]"
    assert result.exists()
    assert not old.exists()


def test_sync_work_folder_raises_when_target_is_file(tmp_path):
    target = tmp_path / "labs" / "Math" / "[DONE] Lab 1"
    target.parent.mkdir(parents=True)
    target.write_text("not a folder", encoding="utf-8")

    with pytest.raises(FileExistsError, match="folder target exists as a file"):
        sync_work_folder(tmp_path, "Math", "Lab 1", LocalStatus.DONE)


def test_sync_work_folder_raises_on_duplicate_matching_old_folders(tmp_path):
    subject_dir = tmp_path / "labs" / "Math"
    (subject_dir / "[DONE] Lab 1").mkdir(parents=True)
    (subject_dir / "[SENT] Lab 1").mkdir()

    with pytest.raises(ValueError, match="multiple existing folders"):
        sync_work_folder(tmp_path, "Math", "Lab 1", LocalStatus.REVIEW)


def test_sync_work_folder_raises_when_target_exists_with_duplicate_old_prefix(tmp_path):
    done = tmp_path / "labs" / "Math" / "[DONE] Lab 1"
    sent = tmp_path / "labs" / "Math" / "[SENT] Lab 1"
    done.mkdir(parents=True)
    sent.mkdir()

    with pytest.raises(ValueError):
        sync_work_folder(tmp_path, "Math", "Lab 1", LocalStatus.DONE)


def test_status_folder_name_has_single_prefix():
    assert status_folder_name("[DONE] Lab 1", LocalStatus.SENT) == "[SENT] Lab 1"


def test_status_folder_name_strips_repeated_known_prefixes():
    assert (
        status_folder_name("[DONE] [SENT] Lab 1", LocalStatus.REVIEW)
        == "[REVIEW] Lab 1"
    )


def test_sync_work_folder_sanitizes_subject_and_work_name(tmp_path):
    result = sync_work_folder(tmp_path, " Math: 1 ", " . ", LocalStatus.REVIEW)
    assert result == Path(tmp_path, "labs", "Math 1", "[REVIEW] unnamed")
    assert result.exists()
