from pathlib import Path

import pytest
import yaml

from lab_auto.archive import archive_work
from lab_auto.groups import save_group
from lab_auto.models import Teacher
from lab_auto.parsers import parse_task_detail, parse_teacher_profile
from lab_auto.state import load_state
from lab_auto.sync import SyncService
from lab_auto.teachers import load_teachers, save_teachers
from lab_auto.timetable import (
    TimetableError,
    TimetableGroup,
    TimetableLesson,
    TimetableNetworkError,
    TimetableResult,
)

BASE = "https://pro.guap.ru"
URL = BASE + "/inside/profile/10001"
DETAIL = '<a class="link-switch link-switch-blue" href="/inside/profile/10001">Петрова Анна Сергеевна</a>'
PROFILE = '''<h3 class="text-center" id="fio">Петрова Анна Сергеевна</h3>
<div class="list-group list-group-flush">
<div class="list-group-item fw-semibold underline__danger">Позиции</div>
<div class="list-group-item"><div>Кафедра 41</div><h5>старший преподаватель</h5><div>Институт 4</div></div>
<div class="list-group-item"><div>Центр</div><h5> старший   преподаватель </h5></div>
</div><div class="list-group"><div class="list-group-item"><h5>Не должность</h5></div></div>'''


def test_teacher_profile_deduplicates_positions():
    detail = parse_task_detail(DETAIL, BASE)
    assert detail.teacher_url == URL
    assert detail.teacher_name == "Петрова Анна Сергеевна"
    teacher = parse_teacher_profile(PROFILE, URL)
    assert teacher.positions == ["старший преподаватель"]
    assert teacher.report_label == "старший преподаватель, Петрова А.С."
    with pytest.raises(ValueError):
        parse_teacher_profile("<form>Login</form>", URL)


def test_teacher_profile_preserves_distinct_positions():
    teacher = parse_teacher_profile(PROFILE.replace(" старший   преподаватель ", "доцент"), URL)
    assert teacher.positions == ["старший преподаватель", "доцент"]


class TeacherSession:
    def __init__(self, profile=PROFILE):
        self.profile = profile
        self.profile_fetches = 0

    def open_session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def task_list_html(self):
        rows = "".join(f'<tr><td>Предмет</td><td><a href="/inside/student/tasks/{i}">Работа {i}</a></td><td>—</td><td>—</td></tr>' for i in (1, 2))
        return '<table><thead><tr><th>Дисциплина</th><th>Название</th><th>Статус</th><th>Предельная дата</th></tr></thead><tbody>' + rows + '</tbody></table>'

    def page_html(self, url):
        if url == URL:
            self.profile_fetches += 1
            return self.profile
        return DETAIL


def test_sync_reuses_profile_across_tasks_and_processes(tmp_path: Path):
    first = TeacherSession()
    assert len(SyncService(tmp_path, browser=first).sync().records) == 2
    assert first.profile_fetches == 1
    second = TeacherSession("<form>Login</form>")
    SyncService(tmp_path, browser=second).sync()
    assert second.profile_fetches == 0
    assert load_teachers(tmp_path)[URL].positions == ["старший преподаватель"]
    for work in load_state(tmp_path):
        assert work.teacher.name == "Петрова Анна Сергеевна"
        assert work.teacher.positions == ["старший преподаватель"]
        assert "старший преподаватель, Петрова А.С." in (work.folder / "task.md").read_text()


def test_invalid_profile_is_not_cached_permanently(tmp_path: Path):
    failed = TeacherSession("<form>Login</form>")
    SyncService(tmp_path, browser=failed).sync()
    assert failed.profile_fetches == 1
    assert load_teachers(tmp_path) == {}
    assert all(work.teacher.name == "Петрова Анна Сергеевна" for work in load_state(tmp_path))
    recovered = TeacherSession()
    SyncService(tmp_path, browser=recovered).sync()
    assert recovered.profile_fetches == 1
    assert load_teachers(tmp_path)[URL].positions == ["старший преподаватель"]


def test_subject_keeps_teacher_and_new_subject_gets_own_profile(tmp_path):
    import yaml

    SyncService(tmp_path, browser=TeacherSession()).sync()

    class NewSubjectSession(TeacherSession):
        def task_list_html(self):
            return super().task_list_html().replace(
                '<tr><td>Предмет</td><td><a href="/inside/student/tasks/2">',
                '<tr><td>Новый предмет</td><td><a href="/inside/student/tasks/2">',
            )

        def page_html(self, url):
            if "/inside/profile/" in url:
                self.profile_fetches += 1
                return PROFILE.replace("Петрова Анна Сергеевна", "Иванов Иван Иванович")
            return DETAIL.replace("10001", "10002")

    changed = NewSubjectSession()
    records = SyncService(tmp_path, browser=changed).sync().records
    assert changed.profile_fetches == 1
    assert {work.subject: work.teacher.name for work in records} == {
        "Предмет": "Петрова Анна Сергеевна",
        "Новый предмет": "Иванов Иван Иванович",
    }
    data = yaml.safe_load((tmp_path / "state" / "teachers.yaml").read_text())
    assert {item["subject"]: item["name"] for item in data["subjects"]} == {
        "Предмет": "Петрова Анна Сергеевна",
        "Новый предмет": "Иванов Иван Иванович",
    }
    again = NewSubjectSession()
    SyncService(tmp_path, browser=again).sync()
    assert again.profile_fetches == 0


RASP_URL = "https://guap.ru/rasp?pr=105"
LAB = Teacher(RASP_URL, "Смирнов С.С.", ["старший преподаватель"])


class EventSession(TeacherSession):
    def __init__(self, events, profile=PROFILE, detail=DETAIL):
        super().__init__(profile)
        self.events = events
        self.detail = detail

    def page_html(self, url):
        self.events.append(("page_html", url))
        if url == URL:
            self.profile_fetches += 1
            return self.profile
        return self.detail


class FakeTimetable:
    def __init__(self, events, outcome):
        self.events = events
        self.outcome = outcome

    def load(self, number):
        self.events.append(("timetable.load", number))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _result(*lessons, group="1232"):
    return TimetableResult(TimetableGroup(group, "6266"), "https://guap.ru/rasp?gr=6266", list(lessons))


def _lesson(kind="laboratory", teacher=LAB, subject="Предмет"):
    return TimetableLesson(subject, kind, teacher)


def _sync(root, outcome, events=None, **session_kwargs):
    events = [] if events is None else events
    session = EventSession(events, **session_kwargs)
    timetable = FakeTimetable(events, outcome)
    result = SyncService(root, browser=session, timetable=timetable).sync()
    return result, events, session


def _subject_entry(root):
    data = yaml.safe_load((root / "state" / "teachers.yaml").read_text(encoding="utf-8"))
    (entry,) = data["subjects"]
    return data, entry


def _loads(events):
    return [event for event in events if event[0] == "timetable.load"]


def test_portal_phase_finishes_before_timetable_load(tmp_path):
    save_group(tmp_path, "1232")
    _, events, _ = _sync(tmp_path, _result(_lesson()))
    load_index = events.index(("timetable.load", "1232"))
    assert load_index == len(events) - 1
    assert ("page_html", URL) in events[:load_index]


def test_laboratory_teacher_corrects_portal_lecturer_everywhere(tmp_path):
    save_group(tmp_path, "1232")
    result, _, _ = _sync(tmp_path, _result(_lesson()))
    assert result.group == "1232"
    for work in load_state(tmp_path):
        assert work.teacher == LAB
        assert "старший преподаватель, Смирнов С.С." in (work.folder / "task.md").read_text()
    data, entry = _subject_entry(tmp_path)
    assert [item["profile_url"] for item in data["teachers"]] == [URL]
    assert (entry["source"], entry["name"], entry["profile_url"]) == ("timetable", "Смирнов С.С.", RASP_URL)
    assert entry["report_label"] == "старший преподаватель, Смирнов С.С."
    assert entry["timetable"] == {"group": "1232", "site_id": "6266", "kind": "laboratory",
                                  "source_url": "https://guap.ru/rasp?gr=6266"}
    assert entry["portal_teacher"]["name"] == "Петрова Анна Сергеевна"


def test_practice_used_when_no_laboratory(tmp_path):
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson("practice")))
    _, entry = _subject_entry(tmp_path)
    assert entry["source"] == "timetable"
    assert entry["timetable"]["kind"] == "practice"


def test_correction_without_positions_still_applies(tmp_path):
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson(teacher=Teacher(RASP_URL, "Смирнов С.С.", []))))
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Смирнов С.С."}


@pytest.mark.parametrize("lessons", [
    [_lesson(subject="Другой предмет")],
    [_lesson(), _lesson(teacher=Teacher("https://guap.ru/rasp?pr=1", "Иванов М.Е.", ["ассистент"]))],
], ids=["missing", "ambiguous"])
def test_unmatched_or_ambiguous_subject_keeps_portal(tmp_path, lessons):
    save_group(tmp_path, "1232")
    result, _, _ = _sync(tmp_path, _result(*lessons))
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Петрова Анна Сергеевна"}
    _, entry = _subject_entry(tmp_path)
    assert entry["source"] == "portal" and "timetable" not in entry
    if len(lessons) == 2:
        assert any("Предмет" in warning for warning in result.warnings)


def test_network_failure_keeps_same_group_correction_with_warning(tmp_path):
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson()))
    result, _, session = _sync(tmp_path, TimetableNetworkError("offline"))
    assert session.profile_fetches == 0
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Смирнов С.С."}
    assert any("keeping last verified" in warning for warning in result.warnings)
    _, entry = _subject_entry(tmp_path)
    assert entry["source"] == "timetable"
    assert entry["portal_teacher"]["name"] == "Петрова Анна Сергеевна"


def test_non_network_timetable_error_resets_to_portal(tmp_path):
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson()))
    result, _, _ = _sync(tmp_path, TimetableError("markup changed"))
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Петрова Анна Сергеевна"}
    assert result.warnings


def test_changed_group_never_reuses_old_correction(tmp_path):
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson()))
    save_group(tmp_path, "1245")
    _sync(tmp_path, TimetableNetworkError("offline"))
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Петрова Анна Сергеевна"}
    _, entry = _subject_entry(tmp_path)
    assert entry["source"] == "portal" and "timetable" not in entry


def test_no_portal_teacher_skips_timetable(tmp_path):
    save_group(tmp_path, "1232")
    result, events, _ = _sync(tmp_path, _result(_lesson()), detail="<p>Без преподавателя</p>")
    assert _loads(events) == []
    assert all(work.teacher is None for work in result.records)


def test_no_group_skips_timetable_and_keeps_portal(tmp_path):
    result, events, _ = _sync(tmp_path, _result(_lesson()))
    assert _loads(events) == []
    assert result.group is None
    assert {work.teacher.name for work in load_state(tmp_path)} == {"Петрова Анна Сергеевна"}


def test_archived_work_teacher_is_frozen(tmp_path):
    _sync(tmp_path, _result())
    archived_id = sorted(work.work_id for work in load_state(tmp_path))[0]
    archive_work(tmp_path, archived_id)
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson()))
    teachers = {work.work_id: work.teacher.name for work in load_state(tmp_path)}
    assert teachers.pop(archived_id) == "Петрова Анна Сергеевна"
    assert set(teachers.values()) == {"Смирнов С.С."}


def test_legacy_v1_subject_entry_loads_and_corrects_without_refetch(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "teachers.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "teachers": [],
        "subjects": [{"subject": "Предмет", "subject_site_id": None, "profile_url": URL,
                      "name": "Петрова Анна Сергеевна", "positions": ["доцент"],
                      "report_label": "доцент, Петрова А.С.", "custom": "keep"}],
    }, allow_unicode=True), encoding="utf-8")
    save_group(tmp_path, "1232")
    _, _, session = _sync(tmp_path, _result(_lesson()))
    assert session.profile_fetches == 0
    data, entry = _subject_entry(tmp_path)
    assert data["version"] == 2
    assert entry["custom"] == "keep"
    assert entry["portal_teacher"] == {"profile_url": URL, "name": "Петрова Анна Сергеевна",
                                       "positions": ["доцент"]}
    _, _, again = _sync(tmp_path, _result())
    assert again.profile_fetches == 0
    assert {work.teacher.positions[0] for work in load_state(tmp_path)} == {"доцент"}


def test_unique_cached_full_name_expands_initials_only(tmp_path):
    save_group(tmp_path, "1232")
    short = Teacher(RASP_URL, "Петрова А.С.", ["доцент"])
    _sync(tmp_path, _result(_lesson(teacher=short)))
    _, entry = _subject_entry(tmp_path)
    assert (entry["name"], entry["profile_url"], entry["positions"]) == (
        "Петрова Анна Сергеевна", RASP_URL, ["доцент"])


def test_ambiguous_cached_full_names_keep_abbreviation(tmp_path):
    _sync(tmp_path, _result())
    teachers = load_teachers(tmp_path)
    other = BASE + "/inside/profile/1"
    teachers[other] = Teacher(other, "Петрова Алёна Светлановна", [])
    save_teachers(tmp_path, teachers)
    save_group(tmp_path, "1232")
    _sync(tmp_path, _result(_lesson(teacher=Teacher(RASP_URL, "Петрова А.С.", ["доцент"]))))
    _, entry = _subject_entry(tmp_path)
    assert entry["name"] == "Петрова А.С."
