from __future__ import annotations

from pathlib import Path

from lab_auto.parsers import ParsedTask, parse_task_list

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://pro.guap.ru"

# Anonymous markup matching GUAP table shape — safe to commit (no real student data).
SYNTHETIC_TASK_LIST_HTML = """
<html><body>
  <table class="table table-bordered">
    <thead>
      <tr>
        <th title="Дисциплина">Дисциплина</th>
        <th title="Название">Название</th>
        <th title="Статус">Статус</th>
        <th title="Предельная дата">Предельная дата</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Тестовая дисциплина</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/100001">Лабораторная работа N1</a></td>
        <td>не принят</td>
        <td>2099-01-15</td>
      </tr>
      <tr>
        <td>Тестовая дисциплина</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/100002">Лабораторная работа N2</a></td>
        <td>ожидает проверки</td>
        <td>2099-02-20</td>
      </tr>
      <tr>
        <td>Другой предмет</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/100003">Практика N3</a></td>
        <td>принят</td>
        <td>2099-03-10</td>
      </tr>
    </tbody>
  </table>
</body></html>
"""

SYNTHETIC_TASK_DETAIL_HTML = """
<html><body>
  <a class="btn-outline-secondary" href="/inside/student/tasks/100001/download">Скачать</a>
  <input type="file" id="file" />
  <table class="table table-bordered">
    <thead><tr><th>Статус</th><th>Файл</th></tr></thead>
    <tbody><tr><td>не принят</td><td>—</td></tr></tbody>
  </table>
</body></html>
"""


def _read_fixture(name: str, *, synthetic: str) -> str:
    path = FIXTURES / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return synthetic


def load_fixture_task_list() -> str:
    return _read_fixture("task_list.html", synthetic=SYNTHETIC_TASK_LIST_HTML)


def load_fixture_task_detail() -> str:
    return _read_fixture("task_detail.html", synthetic=SYNTHETIC_TASK_DETAIL_HTML)


def first_fixture_task() -> ParsedTask:
    tasks = parse_task_list(load_fixture_task_list(), BASE_URL)
    if not tasks:
        raise RuntimeError("task list fixture produced no tasks")
    return tasks[0]


def fixture_task_with_status(website_status: str) -> ParsedTask:
    for task in parse_task_list(load_fixture_task_list(), BASE_URL):
        if task.website_status == website_status:
            return task
    raise RuntimeError(f"task list fixture has no task with status {website_status!r}")
