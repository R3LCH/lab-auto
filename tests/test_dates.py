from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

from lab_auto.cli import app
from lab_auto.archive import archive_removed_works
from lab_auto.dates import normalize_deadline, refresh_dates, set_user_due_date
from lab_auto.models import LocalStatus, WorkRecord, merge_synced_work
from lab_auto.state import generate_markdown_views, load_state, save_state
from lab_auto.sync import SyncService


def record(root: Path, ident: str, subject: str, website: str | None, **kwargs) -> WorkRecord:
    return WorkRecord(
        work_id=ident, subject=subject, name=kwargs.pop('name', 'Лабораторная работа'),
        number=None, task_url=f'https://pro.guap.ru/inside/student/tasks/{ident[5:]}',
        due_date=website, website_status='—', local_status=LocalStatus.UNDONE,
        folder=root / 'labs' / subject / ident, task_pdf=None, reports=[],
        last_sync=None, last_submit_attempt=None, last_submit_result=None, **kwargs,
    )


def table_rows(root: Path) -> list[dict[str, str]]:
    rows = []
    headers = []
    for line in (root / 'dates.md').read_text(encoding='utf-8').splitlines():
        if not line.startswith('|'):
            continue
        cells = [cell.strip().strip('`') for cell in line.strip().strip('|').split('|')]
        if 'work_id' in cells and 'user' in cells and 'website' in cells:
            headers = cells
        elif headers and re.fullmatch(r'task-\d+', cells[headers.index('work_id')]):
            rows.append(dict(zip(headers, cells)))
    return rows


def edit_cell(root: Path, ident: str, column: str, value: str) -> None:
    path = root / 'dates.md'
    lines = path.read_text(encoding='utf-8').splitlines()
    headers = []
    changed = False
    for index, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if 'work_id' in cells and 'user' in cells:
            headers = cells
        elif headers and cells[headers.index('work_id')].strip('`') == ident:
            cells[headers.index(column)] = value
            lines[index] = '| ' + ' | '.join(cells) + ' |'
            changed = True
    assert changed, ident
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


@pytest.mark.parametrize(('value', 'expected'), [
    ('31.12.2026', '2026-12-31'), ('2027-01-02', '2027-01-02'),
    (' 01.02.2027 ', '2027-02-01'), ('29.02.2028', '2028-02-29'),
    (None, None), ('', None), ('Не указана', None), ('Не указан', None),
    ('Не указано', None), ('—', None), ('-', None), ('UNSPECIFIED', None),
])
def test_deadline_formats_and_missing_values(value, expected):
    assert normalize_deadline(value) == expected


@pytest.mark.parametrize('value', ['31.02.2027', '2027-13-01', 'tomorrow'])
def test_invalid_deadline_does_not_become_a_different_date(value):
    with pytest.raises(ValueError):
        normalize_deadline(value)


def test_dates_sort_by_user_chronologically_then_unspecified_subject(tmp_path):
    works = [
        record(tmp_path, 'task-1', 'Язык', '01.01.2027'),
        record(tmp_path, 'task-2', 'Алгебра', '31.12.2026'),
        record(tmp_path, 'task-3', 'Язык', 'Не указана'),
        record(tmp_path, 'task-4', 'Алгебра', None),
        record(tmp_path, 'task-5', 'Физика', None, user_due_date='2026-01-01'),
        record(tmp_path, 'task-6', 'Архив', '2020-01-01', archived=True),
    ]
    save_state(tmp_path, works)
    generate_markdown_views(tmp_path, works)
    rows = table_rows(tmp_path)
    assert [row['work_id'] for row in rows] == ['task-5', 'task-2', 'task-1', 'task-4', 'task-3']
    assert [(row['user'], row['website']) for row in rows] == [
        ('2026-01-01', 'UNSPECIFIED'), ('2026-12-31', '2026-12-31'),
        ('2027-01-01', '2027-01-01'), ('UNSPECIFIED', 'UNSPECIFIED'),
        ('UNSPECIFIED', 'UNSPECIFIED'),
    ]


def test_manual_user_date_is_read_and_persisted_without_trusting_website_edits(tmp_path):
    work = record(tmp_path, 'task-1', 'Физика | практика\n2', '10.12.2026')
    save_state(tmp_path, [work])
    generate_markdown_views(tmp_path, [work])
    edit_cell(tmp_path, 'task-1', 'user', '01.12.2026')
    edit_cell(tmp_path, 'task-1', 'website', '2020-01-01')
    loaded = load_state(tmp_path)[0]
    assert loaded.effective_due_date == '2026-12-01'
    assert loaded.due_date == '10.12.2026'
    refresh_dates(tmp_path)
    row = table_rows(tmp_path)[0]
    assert (row['user'], row['website']) == ('2026-12-01', '2026-12-10')
    assert load_state(tmp_path)[0].subject == work.subject
    assert '2026-12-01' in (tmp_path / 'state' / 'summary.md').read_text(encoding='utf-8')


def test_website_change_updates_default_but_not_explicit_equal_override(tmp_path):
    a = record(tmp_path, 'task-1', 'Физика', '2026-12-10')
    b = record(tmp_path, 'task-2', 'Математика', '2026-12-10')
    save_state(tmp_path, [a, b])
    refresh_dates(tmp_path)
    set_user_due_date(tmp_path, 'task-2', '2026-12-10')
    before = load_state(tmp_path)
    merged = [merge_synced_work(replace(work, due_date='2027-01-20'), work) for work in before]
    save_state(tmp_path, merged)
    generate_markdown_views(tmp_path, merged)
    after = {work.work_id: work for work in load_state(tmp_path)}
    assert after['task-1'].effective_due_date == '2027-01-20'
    assert after['task-2'].effective_due_date == '2026-12-10'
    assert after['task-2'].due_date == '2027-01-20'
    set_user_due_date(tmp_path, 'task-2', None)
    assert load_state(tmp_path)[1].effective_due_date == '2027-01-20'


def test_user_can_explicitly_clear_a_website_deadline(tmp_path):
    work = record(tmp_path, 'task-1', 'Физика', '2026-12-10')
    save_state(tmp_path, [work])
    refresh_dates(tmp_path)
    edit_cell(tmp_path, 'task-1', 'user', 'UNSPECIFIED')
    refresh_dates(tmp_path)
    loaded = load_state(tmp_path)[0]
    assert loaded.effective_due_date is None
    assert loaded.due_date == '2026-12-10'
    assert table_rows(tmp_path)[0]['user'] == 'UNSPECIFIED'
    set_user_due_date(tmp_path, 'task-1', None)
    assert load_state(tmp_path)[0].effective_due_date == '2026-12-10'


def test_invalid_manual_edit_is_not_silently_overwritten(tmp_path):
    save_state(tmp_path, [record(tmp_path, 'task-1', 'Физика', '2026-12-10')])
    refresh_dates(tmp_path)
    edit_cell(tmp_path, 'task-1', 'user', '31.02.2027')
    state_before = (tmp_path / 'state' / 'works.yaml').read_bytes()
    markdown_before = (tmp_path / 'dates.md').read_bytes()
    with pytest.raises(ValueError):
        refresh_dates(tmp_path)
    assert (tmp_path / 'state' / 'works.yaml').read_bytes() == state_before
    assert (tmp_path / 'dates.md').read_bytes() == markdown_before


def test_cli_set_and_reset_have_priority_and_preserve_website(tmp_path):
    save_state(tmp_path, [record(tmp_path, 'task-1', 'Физика', '2026-12-10')])
    runner = CliRunner()
    for args, expected in [
        (['dates'], '2026-12-10'),
        (['dates', 'set', 'task-1', '01.12.2026'], '2026-12-01'),
        (['dates', 'set', 'task-1', 'UNSPECIFIED'], None),
        (['dates', 'reset', 'task-1'], '2026-12-10'),
    ]:
        result = runner.invoke(app, ['--root', str(tmp_path), *args])
        assert result.exit_code == 0, result.output
        work = load_state(tmp_path)[0]
        assert work.effective_due_date == expected
        assert work.due_date == '2026-12-10'
    before = (tmp_path / 'state' / 'works.yaml').read_bytes()
    result = runner.invoke(app, ['--root', str(tmp_path), 'dates', 'set', 'task-1', '2027-02-31'])
    assert result.exit_code != 0
    assert (tmp_path / 'state' / 'works.yaml').read_bytes() == before


def test_archived_deadline_cannot_be_changed(tmp_path):
    save_state(tmp_path, [record(tmp_path, 'task-1', 'Физика', '2026-12-10', archived=True)])
    with pytest.raises(ValueError):
        set_user_due_date(tmp_path, 'task-1', '2027-01-01')
    assert load_state(tmp_path)[0].effective_due_date == '2026-12-10'


def test_duplicate_user_rows_fail_without_losing_dates(tmp_path):
    save_state(tmp_path, [record(tmp_path, 'task-1', 'Физика', '2026-12-10')])
    refresh_dates(tmp_path)
    path = tmp_path / 'dates.md'
    text = path.read_text(encoding='utf-8')
    row = next(line for line in text.splitlines() if re.match(r'^\|\s*`?task-1`?\s*\|', line))
    path.write_text(text + row + '\n', encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        refresh_dates(tmp_path)
    assert path.read_bytes() == before


def test_removal_archiving_preserves_explicit_user_date(tmp_path):
    work = record(tmp_path, 'task-1', 'Физика', '2026-12-10', user_due_date='2026-11-30')
    archived = archive_removed_works([], {work.work_id: work}, set(), '2026-12-01T12:00:00Z')
    assert archived[0].archived
    assert archived[0].effective_due_date == '2026-11-30'
    save_state(tmp_path, archived)
    assert load_state(tmp_path)[0].effective_due_date == '2026-11-30'


class DeadlineBrowser:
    def __init__(self):
        self.website = '10.12.2026'

    def open_session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def task_list_html(self):
        return f'''<table><thead><tr><th>Дисциплина</th><th>Название</th>
        <th>Статус</th><th>Предельная дата</th></tr></thead><tbody>
        <tr><td>Физика</td><td><a href="/inside/student/tasks/1">Лабораторная 1</a></td><td>—</td><td>{self.website}</td></tr>
        <tr><td>Алгебра</td><td><a href="/inside/student/tasks/2">Практическая 2</a></td><td>—</td><td>Не указана</td></tr>
        </tbody></table>'''

    def page_html(self, url):
        return '<html><body><p>Синтетическое задание</p></body></html>'


def test_sync_reads_portal_column_preserves_manual_user_and_updates_website(tmp_path):
    browser = DeadlineBrowser()
    service = SyncService(tmp_path, browser=browser)
    first = service.sync()
    assert {work.work_id for work in first.records} == {'task-1', 'task-2'}
    assert table_rows(tmp_path)[0]['website'] == '2026-12-10'
    edit_cell(tmp_path, 'task-1', 'user', '2026-11-30')
    browser.website = '20.12.2026'
    second = service.sync()
    work = next(work for work in second.records if work.work_id == 'task-1')
    assert work.effective_due_date == '2026-11-30'
    assert normalize_deadline(work.due_date) == '2026-12-20'
    row = next(row for row in table_rows(tmp_path) if row['work_id'] == 'task-1')
    assert (row['user'], row['website']) == ('2026-11-30', '2026-12-20')


def test_user_column_wins_even_when_displayed_website_cell_matches_it(tmp_path):
    save_state(tmp_path, [record(tmp_path, 'task-1', 'Физика', '2026-12-10')])
    refresh_dates(tmp_path)
    edit_cell(tmp_path, 'task-1', 'user', '2026-12-01')
    edit_cell(tmp_path, 'task-1', 'website', '2026-12-01')
    loaded = load_state(tmp_path)[0]
    assert loaded.effective_due_date == '2026-12-01'
    assert loaded.due_date == '2026-12-10'


def test_stale_generated_default_does_not_pin_a_new_website_deadline(tmp_path):
    work = record(tmp_path, 'task-1', 'Физика', '2026-12-10')
    save_state(tmp_path, [work])
    refresh_dates(tmp_path)
    work.due_date = '2027-01-20'
    save_state(tmp_path, [work])
    loaded = load_state(tmp_path)[0]
    assert loaded.user_due_date is None
    assert loaded.effective_due_date == '2027-01-20'
