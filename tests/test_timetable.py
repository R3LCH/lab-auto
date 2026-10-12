from pathlib import Path

import pytest

from lab_auto.models import Teacher
from lab_auto.timetable import (
    TimetableError,
    TimetableGroup,
    TimetableLesson,
    TimetableResult,
    parse_group,
    parse_timetable,
)

FIXTURES = Path(__file__).parent / "fixtures"
GROUP = TimetableGroup("1232", "6266")
SOURCE = "https://guap.ru/rasp?gr=6266"


def _lesson(subject="Предмет", kind="Лабораторное занятие", *,
            teacher="Иванов М.Е., ассистент", href="?pr=2987", group="6266"):
    return (
        f'<div><div class="fs-6 lh-sm opacity-50">{kind}</div>'
        f'<div class="lead lh-sm">{subject}</div>'
        f'<div class="opacity-75"><a href="{href}">{teacher}</a>'
        f'<a href="?gr={group}">Группа</a></div></div>'
    )


def _page(lessons="", *, selected="6266"):
    return (
        '<main><div id="result">Фильтр: гр. 1232</div>'
        + lessons
        + '<select id="selGroup">'
        + f'<option value="6266" {"selected" if selected == "6266" else ""}>1232</option>'
        + f'<option value="6273" {"selected" if selected == "6273" else ""}>1245</option>'
        + '</select><select id="selPrep">'
        '<option value="999">Петров П.П., профессор</option></select></main>'
    )


def test_real_lab_corrects_lecturer_and_excludes_academic_degree():
    html = (FIXTURES / "timetable_group_1232.html").read_text(encoding="utf-8")
    result = parse_timetable(html, GROUP)
    teacher = result.teacher_for("Обработка навигационной информации")
    assert teacher == Teacher("https://guap.ru/rasp?pr=109", "Козлов М.Е.", ["ассистент"])
    assert teacher.report_label == "ассистент, Козлов М.Е."
    assert result.teacher_for("Моделирование приборов и систем управления летательных аппаратов").positions == ["доцент"]
    assert result.teacher_for("Эксплуатация и испытания приборов и систем управления летательных аппаратов").name == "Смирнов С.С."


def test_real_practice_is_used_without_laboratory_and_not_coursework():
    html = (FIXTURES / "timetable_group_1232.html").read_text(encoding="utf-8")
    result = parse_timetable(html, GROUP)
    assert result.teacher_for("Системы управления летательными аппаратами").report_label == "доцент, Орлов О.О."
    assert result.teacher_for("Нет такого предмета") is None


def test_group_number_is_bound_to_public_internal_id():
    html = (FIXTURES / "timetable_index.html").read_text(encoding="utf-8")
    assert parse_group(html, " 1232 ") == GROUP
    assert parse_group(html, "1331") == TimetableGroup("1331", "6703")
    with pytest.raises(TimetableError):
        parse_group(html, "6266")


@pytest.mark.parametrize("options", [
    '<option value="6266">1232</option><option value="6273">1232</option>',
    '<option value="">1232</option>',
    '<option value="../1232">1232</option>',
])
def test_unknown_or_ambiguous_group_cannot_select_another_group(options):
    with pytest.raises(TimetableError):
        parse_group(f'<main><select id="selGroup">{options}</select></main>', "1232")


def test_subject_matching_normalizes_whitespace_case_and_yo_but_not_fuzzy_titles():
    result = parse_timetable(_page(_lesson(subject="Учёт данных")), GROUP)
    assert result.teacher_for("  УЧЕТ\n данных ").name == "Иванов М.Е."
    assert result.teacher_for("Учёт данных и знания") is None
    assert result.teacher_for("Учёт") is None


def test_lab_has_priority_over_different_practice_teacher():
    result = parse_timetable(_page(
        _lesson(kind="Практическое занятие", teacher="Петров П.П., доцент", href="?pr=999")
        + _lesson()
        + _lesson(kind="Лекция", teacher="Сидоров С.С., профессор", href="?pr=888")
    ), GROUP)
    assert result.teacher_for("Предмет").report_label == "ассистент, Иванов М.Е."


@pytest.mark.parametrize("kind", ["laboratory", "practice"])
def test_multiple_teachers_do_not_arbitrarily_replace_portal_teacher(kind):
    result = TimetableResult(GROUP, SOURCE, [
        TimetableLesson("Предмет", kind, Teacher("https://guap.ru/rasp?pr=1", "Иванов И.И.", ["ассистент"])),
        TimetableLesson("Предмет", kind, Teacher("https://guap.ru/rasp?pr=2", "Петров П.П.", ["доцент"])),
    ])
    assert result.teacher_for("Предмет") is None
    assert result.teacher_for("Предмет") is None
    assert len(result.warnings) == 1
    assert "Предмет" in result.warnings[0]


def test_same_teacher_positions_are_unique_across_meetings():
    result = TimetableResult(GROUP, SOURCE, [
        TimetableLesson("Предмет", "laboratory", Teacher("https://guap.ru/rasp?pr=1", "Иванов И.И.", ["ассистент"])),
        TimetableLesson("Предмет", "laboratory", Teacher("https://guap.ru/rasp?pr=1", "Иванов И.И.", ["Ассистент", "доцент"])),
    ])
    assert result.teacher_for("Предмет").positions == ["ассистент", "доцент"]


def test_filtered_page_does_not_apply_lesson_for_another_group():
    result = parse_timetable(_page(_lesson(group="6273")), GROUP)
    assert result.teacher_for("Предмет") is None


@pytest.mark.parametrize("href", [
    "https://example.com/rasp?pr=2987",
    "https://pro.guap.ru/inside/profile/2987",
    "http://guap.ru/rasp?pr=2987",
    "https://evil@guap.ru/rasp?pr=2987",
    "?pr=2987#another",
    "?pr=2987&pr=999",
    "?pr=not-a-number",
])
def test_untrusted_or_malformed_teacher_url_cannot_override_portal(href):
    result = parse_timetable(_page(_lesson(href=href)), GROUP)
    assert result.teacher_for("Предмет") is None


@pytest.mark.parametrize("teacher", ["", "Иванов М.Е.", "Иванов М.Е., "])
def test_missing_teacher_or_position_keeps_portal(teacher):
    result = parse_timetable(_page(_lesson(teacher=teacher)), GROUP)
    assert result.teacher_for("Предмет") is None
    assert "Предмет" in result.warnings[0]


def test_response_for_wrong_group_is_rejected():
    with pytest.raises(TimetableError):
        parse_timetable(_page(_lesson(), selected="6273"), GROUP)


def test_changed_subject_markup_is_not_reported_as_empty_schedule():
    changed = _page(_lesson()).replace('class="lead lh-sm"', 'class="renamed-subject"')
    with pytest.raises(TimetableError):
        parse_timetable(changed, GROUP)


def test_genuine_empty_filtered_schedule_keeps_portal_with_warning():
    result = parse_timetable(_page(), GROUP)
    assert result.teacher_for("Предмет") is None
    assert result.warnings
    with pytest.raises(TimetableError):
        parse_timetable(_page().replace("Фильтр: гр. 1232", "Выберите группу"), GROUP)


def test_lecture_and_teacher_dropdown_are_not_assignment_teachers():
    result = parse_timetable(_page(_lesson(kind="Лекция")), GROUP)
    assert result.teacher_for("Предмет") is None
    assert result.teacher_for("Петров П.П.") is None
