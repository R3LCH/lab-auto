from pathlib import Path

from lab_auto.models import LocalStatus, status_from_website
from lab_auto.parsers import (
    diagnose_empty_task_list,
    find_task_website_status,
    page_shows_awaiting_review,
    parse_task_detail,
    parse_task_list,
)
from conftest import (
    BASE_URL,
    first_fixture_task,
    load_fixture_task_detail,
    load_fixture_task_list,
)


def test_parse_task_list_extracts_required_fields():
    html = load_fixture_task_list()
    tasks = parse_task_list(html, base_url=BASE_URL)

    assert len(tasks) >= 1
    task = tasks[0]
    assert task.subject
    assert task.name
    assert task.website_status
    assert task.task_url.startswith(f"{BASE_URL}/inside/student/tasks/")
    assert task.task_site_id
    assert status_from_website(task.website_status) in LocalStatus
    assert task.due_date


def test_page_shows_awaiting_review_reads_reports_table_status_column():
    html = """
    <table class="table table-bordered">
      <thead><tr><th>Статус</th><th>Файл</th></tr></thead>
      <tbody><tr>
        <td>ожидает проверки</td><td>report.pdf</td>
      </tr></tbody>
    </table>
    """
    assert page_shows_awaiting_review(html) is True


def test_page_shows_awaiting_review_ignores_unrelated_tables():
    html = """
    <table><tbody><tr><td>ожидает проверки</td></tr></tbody></table>
    <table class="table table-bordered">
      <thead><tr><th>Статус</th><th>Файл</th></tr></thead>
      <tbody><tr><td>не принят</td><td>report.pdf</td></tr></tbody>
    </table>
    """
    assert page_shows_awaiting_review(html) is False


def test_find_task_website_status_reads_fixture_list():
    html = load_fixture_task_list()
    sample = first_fixture_task()

    status = find_task_website_status(html, sample.task_url, BASE_URL)

    assert status == sample.website_status


def test_diagnose_empty_task_list_detects_login_page():
    message = diagnose_empty_task_list("<html><form name=\"username\">login password</form></html>")

    assert "session" in message.lower()


def test_parse_task_detail_extracts_download_and_submit_state():
    html = load_fixture_task_detail()
    detail = parse_task_detail(html, base_url=BASE_URL)

    assert detail.pdf_url is not None
    assert detail.pdf_url.endswith("/download")
    assert detail.has_upload_form is True


def test_parse_task_list_ignores_tables_without_task_links():
    html = """
    <table class="table table-bordered">
      <thead><tr><th>Статус</th><th>Название</th><th>Дисциплина</th></tr></thead>
      <tbody><tr><td>принят</td><td>Other</td><td>Misc</td></tr></tbody>
    </table>
    <table class="table table-bordered">
      <thead><tr><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th></tr></thead>
      <tbody><tr>
        <td>не принят</td><td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/abc">Lab A</a></td>
        <td>Math</td>
      </tr></tbody>
    </table>
    """
    tasks = parse_task_list(html, base_url=BASE_URL)
    assert len(tasks) == 1
    assert tasks[0].name == "Lab A"


def test_parse_task_list_uses_headers_not_fixed_column_order():
    html = """
    <table>
      <thead><tr><th>Статус</th><th>Предельная дата</th><th>Название</th><th>Дисциплина</th></tr></thead>
      <tbody><tr>
        <td>ожидает проверки</td>
        <td>2026-06-02</td>
        <td><a class="link-switch-blue" href="/inside/student/tasks/xyz">Практическая работа N2</a></td>
        <td>Физика</td>
      </tr></tbody>
    </table>
    """

    task = parse_task_list(html, base_url=BASE_URL)[0]

    assert task.subject == "Физика"
    assert task.name == "Практическая работа N2"
    assert task.number == "2"
    assert status_from_website(task.website_status) == LocalStatus.SENT
