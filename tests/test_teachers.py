from pathlib import Path

import pytest

from lab_auto.parsers import parse_task_detail, parse_teacher_profile
from lab_auto.state import load_state
from lab_auto.sync import SyncService
from lab_auto.teachers import load_teachers

BASE = "https://pro.guap.ru"
URL = BASE + "/inside/profile/30928"
DETAIL = '<a class="link-switch link-switch-blue" href="/inside/profile/30928">Боженко Виктория Вячеславовна</a>'
PROFILE = '''<h3 class="text-center" id="fio">Боженко Виктория Вячеславовна</h3>
<div class="list-group list-group-flush">
<div class="list-group-item fw-semibold underline__danger">Позиции</div>
<div class="list-group-item"><div>Кафедра 41</div><h5>старший преподаватель</h5><div>Институт 4</div></div>
<div class="list-group-item"><div>Центр</div><h5> старший   преподаватель </h5></div>
</div><div class="list-group"><div class="list-group-item"><h5>Не должность</h5></div></div>'''


def test_teacher_profile_deduplicates_positions():
    detail = parse_task_detail(DETAIL, BASE)
    assert detail.teacher_url == URL
    assert detail.teacher_name == "Боженко Виктория Вячеславовна"
    teacher = parse_teacher_profile(PROFILE, URL)
    assert teacher.positions == ["старший преподаватель"]
    assert teacher.report_label == "старший преподаватель, Боженко В.В."
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
        assert work.teacher.name == "Боженко Виктория Вячеславовна"
        assert work.teacher.positions == ["старший преподаватель"]
        assert "старший преподаватель, Боженко В.В." in (work.folder / "task.md").read_text()


def test_invalid_profile_is_not_cached_permanently(tmp_path: Path):
    failed = TeacherSession("<form>Login</form>")
    SyncService(tmp_path, browser=failed).sync()
    assert failed.profile_fetches == 1
    assert load_teachers(tmp_path) == {}
    assert all(work.teacher.name == "Боженко Виктория Вячеславовна" for work in load_state(tmp_path))
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
                return PROFILE.replace("Боженко Виктория Вячеславовна", "Иванов Иван Иванович")
            return DETAIL.replace("30928", "12345")

    changed = NewSubjectSession()
    records = SyncService(tmp_path, browser=changed).sync().records
    assert changed.profile_fetches == 1
    assert {work.subject: work.teacher.name for work in records} == {
        "Предмет": "Боженко Виктория Вячеславовна",
        "Новый предмет": "Иванов Иван Иванович",
    }
    data = yaml.safe_load((tmp_path / "state" / "teachers.yaml").read_text())
    assert {item["subject"]: item["name"] for item in data["subjects"]} == {
        "Предмет": "Боженко Виктория Вячеславовна",
        "Новый предмет": "Иванов Иван Иванович",
    }
    again = NewSubjectSession()
    SyncService(tmp_path, browser=again).sync()
    assert again.profile_fetches == 0
