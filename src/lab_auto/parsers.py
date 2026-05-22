from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urljoin

from parsel import Selector

from lab_auto.models import status_from_website
from lab_auto.paths import canonical_task_detail_url, extract_task_site_id, is_task_download_url
_KNOWN_WEBSITE_STATUSES = frozenset({"принят", "ожидает проверки", "не принят"})


@dataclass(slots=True)
class ParsedTask:
    subject: str
    name: str
    number: str | None
    task_url: str
    task_site_id: str
    due_date: str | None
    website_status: str


@dataclass(slots=True)
class ParsedTaskDetail:
    pdf_url: str | None
    report_download_urls: list[str]
    has_upload_form: bool
    awaiting_review: bool


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _list_row_status_text(status_cell: Selector) -> str:
    """Read the GUAP status column (badge label or plain <span>—</span>)."""
    badge_parts = status_cell.css("span.badge ::text").getall()
    if badge_parts:
        return clean_text(" ".join(badge_parts))
    span_text = status_cell.css("span::text").get()
    if span_text is not None:
        return clean_text(span_text)
    return clean_text(status_cell.xpath("string()").get())


def normalize_website_status(status_text: str) -> str:
    return " ".join(status_text.strip().lower().split())


def is_known_website_status(status_text: str) -> bool:
    from lab_auto.models import website_status_is_dash

    if website_status_is_dash(status_text):
        return True
    return normalize_website_status(status_text) in _KNOWN_WEBSITE_STATUSES


def parse_work_number(name: str) -> str | None:
    match = re.search(r"(?:№|N)\s*([0-9]+)", name, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _table_headers(table: Selector) -> list[str]:
    headers: list[str] = []
    for cell in table.css("thead th"):
        title = cell.css("a.sortable::attr(title)").get()
        if title:
            headers.append(clean_text(title))
            continue
        headers.append(clean_text(cell.xpath("string()").get()))
    return headers


def _column_indexes(headers: list[str]) -> dict[str, int] | None:
    required = ["Дисциплина", "Название", "Статус", "Предельная дата"]
    indexes = {header: index for index, header in enumerate(headers)}
    if not all(name in indexes for name in required):
        return None
    return {name: indexes[name] for name in required}


def _task_list_tables(selector: Selector) -> list[Selector]:
    tables = selector.css("table.table-bordered")
    if tables:
        return tables
    return selector.css("table")


def _task_link_href(name_cell: Selector, row: Selector | None = None) -> str | None:
    """Pick the task detail link, not the assignment PDF download link."""
    base = "https://pro.guap.ru"

    def pick(href: str | None) -> str | None:
        if href and not is_task_download_url(urljoin(base, href)):
            return href
        return None

    blue = name_cell.css("a.link-switch-blue::attr(href)").get()
    chosen = pick(blue)
    if chosen:
        return chosen
    for href in name_cell.css("a[href*='/inside/student/tasks/']::attr(href)").getall():
        chosen = pick(href)
        if chosen:
            return chosen
    if row is not None:
        for href in row.css("a[href*='/inside/student/tasks/']::attr(href)").getall():
            chosen = pick(href)
            if chosen:
                return chosen
    fallback = blue or name_cell.css("a[href*='/inside/student/tasks/']::attr(href)").get()
    return fallback


def _task_url_path(task_url: str) -> str:
    site_id = extract_task_site_id(task_url)
    if not site_id:
        return ""
    return f"/inside/student/tasks/{site_id}"


def _iter_list_rows(selector: Selector, base_url: str) -> Iterator[dict[str, str]]:
    for table in _task_list_tables(selector):
        headers = _table_headers(table)
        indexes = _column_indexes(headers)
        if not indexes:
            continue
        if not table.css("tbody a[href*='/inside/student/tasks/']"):
            continue
        for row in table.css("tbody tr"):
            cells = row.css("td")
            if len(cells) <= max(indexes.values()):
                continue
            name_cell = cells[indexes["Название"]]
            link = _task_link_href(name_cell, row)
            name = clean_text(name_cell.xpath("string()").get())
            if not name or not link:
                continue
            yield {
                "subject": clean_text(cells[indexes["Дисциплина"]].xpath("string()").get()),
                "name": name,
                "status": _list_row_status_text(cells[indexes["Статус"]]),
                "due_date": clean_text(cells[indexes["Предельная дата"]].xpath("string()").get()),
                "task_url": canonical_task_detail_url(urljoin(base_url, link), base_url),
            }


def validate_parsed_task(task: ParsedTask) -> None:
    if not task.subject:
        raise ValueError("Parsed task is missing subject.")
    if not task.name:
        raise ValueError("Parsed task is missing name.")
    if not task.task_site_id:
        raise ValueError(f"Parsed task URL is not a task detail link: {task.task_url!r}")
    if not task.website_status:
        raise ValueError(f"Parsed task {task.task_url!r} is missing website status.")


def find_task_website_status(html: str, task_url: str, base_url: str) -> str | None:
    """Return the list-page status text for a task URL, if present."""
    target_path = _task_url_path(task_url)
    if not target_path:
        return None
    selector = Selector(text=html)
    for row in _iter_list_rows(selector, base_url):
        if _task_url_path(row["task_url"]) != target_path:
            continue
        return row["status"]
    return None


def diagnose_empty_task_list(html: str) -> str:
    """Explain why parse_task_list returned no tasks."""
    stripped = html.strip()
    if len(stripped) < 200:
        return "Website returned very little HTML; session may have expired."
    lower = stripped.lower()
    if (
        "openid-connect" in lower
        or "name=\"username\"" in lower
        or ("login" in lower and "password" in lower)
    ):
        return "Session may have expired (login page detected)."
    if "inside/student/tasks" not in lower:
        return "Task list URL content not found; session may have expired."
    if "table-bordered" not in lower:
        return "Task list table markup not found; the website layout may have changed."
    return "No tasks parsed; check session or website markup."


def parse_task_list(html: str, base_url: str) -> list[ParsedTask]:
    selector = Selector(text=html)
    tasks: list[ParsedTask] = []
    for row in _iter_list_rows(selector, base_url):
        task_url = canonical_task_detail_url(row["task_url"], base_url)
        task = ParsedTask(
            subject=row["subject"],
            name=row["name"],
            number=parse_work_number(row["name"]),
            task_url=task_url,
            task_site_id=extract_task_site_id(task_url),
            due_date=row["due_date"] or None,
            website_status=row["status"],
        )
        validate_parsed_task(task)
        tasks.append(task)
    return tasks


def _reports_table_statuses(selector: Selector) -> list[str]:
    statuses: list[str] = []
    for table in selector.css("table"):
        headers = [clean_text(text) for text in table.css("th::text").getall()]
        if "Статус" not in headers:
            continue
        status_index = headers.index("Статус")
        for row in table.css("tbody tr"):
            cells = row.css("td")
            if len(cells) <= status_index or cells[0].attrib.get("colspan"):
                continue
            status = clean_text(cells[status_index].xpath("string()").get())
            if status:
                statuses.append(status)
    return statuses


def _detail_status_text(selector: Selector) -> str:
    statuses = _reports_table_statuses(selector)
    if statuses:
        return " ".join(statuses)
    return clean_text(" ".join(selector.css("table span::text").getall()))


def _task_assignment_pdf_href(selector: Selector) -> str | None:
    for href in selector.css("a[href*='/inside/student/tasks/'][href*='/download']::attr(href)").getall():
        if href and "/inside/student/reports/" not in href:
            return href
    return selector.css("a.btn-outline-secondary[href*='/inside/student/tasks/'][href*='/download']::attr(href)").get()


def _submitted_report_download_hrefs(selector: Selector) -> list[str]:
    seen: set[str] = set()
    hrefs: list[str] = []
    for href in selector.css(
        "a[href*='/inside/student/reports/'][href*='/download']::attr(href)"
    ).getall():
        if not href or href in seen:
            continue
        seen.add(href)
        hrefs.append(href)
    return hrefs


def parse_task_detail(html: str, base_url: str) -> ParsedTaskDetail:
    selector = Selector(text=html)
    download_href = _task_assignment_pdf_href(selector)
    report_hrefs = _submitted_report_download_hrefs(selector)
    status_text = _detail_status_text(selector)
    return ParsedTaskDetail(
        pdf_url=urljoin(base_url, download_href) if download_href else None,
        report_download_urls=[
            urljoin(base_url, href) for href in report_hrefs
        ],
        has_upload_form=bool(selector.css("input[type='file']#file").get()),
        awaiting_review="ожидает проверки" in status_text,
    )


def page_shows_awaiting_review(page_text: str) -> bool:
    selector = Selector(text=page_text)
    statuses = _reports_table_statuses(selector)
    if statuses:
        return any("ожидает проверки" in status for status in statuses)
    return "ожидает проверки" in clean_text(page_text)
