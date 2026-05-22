from pathlib import Path

from lab_auto.models import LocalStatus, status_from_website
from lab_auto.parsers import (
    diagnose_empty_task_list,
    find_task_website_status,
    is_known_website_status,
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


def test_is_known_website_status_treats_dash_as_expected():
    assert is_known_website_status("—") is True
    assert is_known_website_status("возвращено на исправление") is False


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


def test_parse_task_detail_extracts_assignment_pdf_and_submitted_reports():
    html = load_fixture_task_detail()
    detail = parse_task_detail(html, base_url=BASE_URL)

    assert detail.pdf_url is not None
    assert "/inside/student/tasks/" in detail.pdf_url
    assert "/inside/student/reports/" not in detail.pdf_url
    assert detail.pdf_url.endswith("/download")
    if "student/reports" in html:
        assert detail.report_download_urls
        assert detail.report_download_urls[0].endswith("/inside/student/reports/5283063/download")


def test_parse_task_detail_extracts_submit_state_from_synthetic_fixture():
    from conftest import SYNTHETIC_TASK_DETAIL_HTML

    detail = parse_task_detail(SYNTHETIC_TASK_DETAIL_HTML, base_url=BASE_URL)
    assert detail.has_upload_form is True
    assert detail.report_download_urls == []


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


def test_parse_task_list_uses_row_detail_link_when_name_cell_is_download_only():
    html = """
    <table class="table table-bordered">
      <thead>
        <tr>
          <th></th>
          <th title="Дисциплина">Дисциплина</th>
          <th title="Название">Название</th>
          <th title="Статус">Статус</th>
          <th title="Предельная дата">Предельная дата</th>
        </tr>
      </thead>
      <tbody><tr>
        <td>
          <a class="btn btn-outline-info btn-sm" href="/inside/student/tasks/d1b5d6c43001fe8ce6156986faf81c18">
            view
          </a>
        </td>
        <td>Math</td>
        <td>
          <a class="link-switch-blue" href="/inside/student/tasks/d1b5d6c43001fe8ce6156986faf81c18/download">
            Assignment PDF
          </a>
        </td>
        <td>не принят</td>
        <td>2099-01-01</td>
      </tr></tbody>
    </table>
    """
    task = parse_task_list(html, base_url=BASE_URL)[0]
    assert task.task_url == (
        f"{BASE_URL}/inside/student/tasks/d1b5d6c43001fe8ce6156986faf81c18"
    )


def test_parse_fixture_task_list_when_present():
    html = load_fixture_task_list()
    if "link-switch-blue" not in html:
        return
    tasks = parse_task_list(html, base_url=BASE_URL)
    assert len(tasks) >= 40
    assert all("/download" not in task.task_url for task in tasks)


def test_parse_task_list_reads_dash_status_from_span_cell():
    html = load_fixture_task_list()
    if "<span>—</span>" not in html:
        return
    from lab_auto.models import LocalStatus, status_from_website

    dash_tasks = [
        task
        for task in parse_task_list(html, base_url=BASE_URL)
        if status_from_website(task.website_status) == LocalStatus.UNDONE
    ]
    assert dash_tasks
    assert dash_tasks[0].website_status in {"—", "-"}


def test_parse_task_list_prefers_detail_link_over_download_in_name_cell():
    html = """
    <table class="table table-bordered">
      <thead>
        <tr>
          <th title="Дисциплина">Дисциплина</th>
          <th title="Название">Название</th>
          <th title="Статус">Статус</th>
          <th title="Предельная дата">Предельная дата</th>
        </tr>
      </thead>
      <tbody><tr>
        <td>Math</td>
        <td>
          <a href="/inside/student/tasks/site99/download">PDF</a>
          <a class="link-switch-blue" href="/inside/student/tasks/site99">Lab 9</a>
        </td>
        <td>не принят</td>
        <td>2099-01-01</td>
      </tr></tbody>
    </table>
    """
    task = parse_task_list(html, base_url=BASE_URL)[0]
    assert task.task_url == f"{BASE_URL}/inside/student/tasks/site99"
    assert task.task_site_id == "site99"


def test_parse_task_list_normalizes_download_only_link_to_detail_url():
    html = """
    <table class="table table-bordered">
      <thead>
        <tr>
          <th title="Дисциплина">Дисциплина</th>
          <th title="Название">Название</th>
          <th title="Статус">Статус</th>
          <th title="Предельная дата">Предельная дата</th>
        </tr>
      </thead>
      <tbody><tr>
        <td>Math</td>
        <td><a href="/inside/student/tasks/onlydl/download">Lab download only</a></td>
        <td>не принят</td>
        <td>2099-01-01</td>
      </tr></tbody>
    </table>
    """
    task = parse_task_list(html, base_url=BASE_URL)[0]
    assert task.task_url == f"{BASE_URL}/inside/student/tasks/onlydl"


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
