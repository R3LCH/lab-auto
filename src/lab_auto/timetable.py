"""Public GUAP timetable corrections; portal teachers remain the baseline."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from parsel import Selector

from lab_auto.models import Teacher

TIMETABLE_URL = "https://guap.ru/rasp"
_TIMEOUT = 20
_MAX_BYTES = 4 * 1024 * 1024
_KINDS = {
    "лабораторное занятие": "laboratory",
    "практическое занятие": "practice",
}


class TimetableError(ValueError):
    """Unknown group or unrecognized/contradictory public timetable markup."""


class TimetableNetworkError(TimetableError):
    """The public timetable could not be fetched."""


def normalize_subject(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold().replace("ё", "е")


def _text(selector: Selector) -> str:
    return " ".join((selector.xpath("string()").get() or "").split())


@dataclass(frozen=True, slots=True)
class TimetableGroup:
    number: str
    site_id: str


@dataclass(frozen=True, slots=True)
class TimetableLesson:
    subject: str
    kind: str
    teacher: Teacher


@dataclass(slots=True)
class TimetableResult:
    group: TimetableGroup
    source_url: str
    lessons: list[TimetableLesson]
    warnings: list[str] = field(default_factory=list)

    def teacher_for(self, subject: str) -> Teacher | None:
        key = normalize_subject(subject)
        matching = [lesson for lesson in self.lessons if normalize_subject(lesson.subject) == key]
        labs = [lesson for lesson in matching if lesson.kind == "laboratory"]
        selected = labs or [lesson for lesson in matching if lesson.kind == "practice"]
        if not selected:
            return None
        identities = {(lesson.teacher.profile_url, normalize_subject(lesson.teacher.name)) for lesson in selected}
        if len(identities) != 1:
            warning = f"{subject}: multiple {selected[0].kind} teachers for group {self.group.number}; keeping portal teacher"
            if warning not in self.warnings:
                self.warnings.append(warning)
            return None
        first = selected[0].teacher
        positions: list[str] = []
        seen: set[str] = set()
        for lesson in selected:
            for position in lesson.teacher.positions:
                normalized = normalize_subject(position)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    positions.append(position)
        return Teacher(first.profile_url, first.name, positions)


def parse_group(html: str, number: str) -> TimetableGroup:
    selector = Selector(text=html)
    selects = selector.css("main select#selGroup")
    if len(selects) != 1:
        raise TimetableError("Timetable group selector is missing or ambiguous")
    key = normalize_subject(number)
    if not key:
        raise TimetableError("Group number is empty")
    matches = [option for option in selects[0].css("option") if normalize_subject(_text(option)) == key]
    if len(matches) != 1:
        raise TimetableError(f"Group {number!r} is missing or ambiguous on guap.ru/rasp")
    option = matches[0]
    site_id = option.attrib.get("value", "")
    if not re.fullmatch(r"[1-9][0-9]*", site_id):
        raise TimetableError(f"Group {number!r} has an invalid timetable ID")
    return TimetableGroup(_text(option), site_id)


def _link_id(href: str, parameter: str) -> str | None:
    parsed = urlparse(urljoin(TIMETABLE_URL, href))
    if (
        parsed.scheme != "https"
        or parsed.netloc != "guap.ru"
        or parsed.path.rstrip("/") != "/rasp"
        or parsed.fragment
    ):
        return None
    values = parse_qs(parsed.query).get(parameter, [])
    if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]*", values[0]):
        return None
    return values[0]


def parse_timetable(html: str, group: TimetableGroup) -> TimetableResult:
    selector = Selector(text=html)
    actual = parse_group(html, group.number)
    selected = selector.css("main #selGroup option[selected]")
    if (
        actual != group
        or len(selected) != 1
        or selected[0].attrib.get("value") != group.site_id
    ):
        raise TimetableError("Timetable response does not select the requested group")
    result = TimetableResult(group, TIMETABLE_URL + "?" + urlencode({"gr": group.site_id}), [])
    subjects = selector.css("main div.lead.lh-sm")
    if not subjects:
        links = selector.css("main a[href]")
        has_teacher_links = any(_link_id(link.attrib["href"], "pr") for link in links)
        kinds = selector.css("main div.fs-6.lh-sm")
        filter_text = normalize_subject(_text(selector.css("main #result")))
        if has_teacher_links or kinds or f"фильтр: гр. {normalize_subject(group.number)}" != filter_text:
            raise TimetableError("Timetable lesson markup is missing or unrecognized")
        result.warnings.append(f"No timetable lessons for group {group.number}; keeping portal teachers")
        return result
    for subject_node in subjects:
        subject = _text(subject_node)
        block = subject_node.xpath("..")
        kind_nodes = block.css("div.fs-6.lh-sm")
        if not subject or len(kind_nodes) != 1:
            raise TimetableError("Timetable lesson subject/type is unrecognized")
        kind = _KINDS.get(normalize_subject(_text(kind_nodes[0])))
        if kind is None:
            continue
        links = block.css("a[href]")
        group_ids = {_link_id(link.attrib["href"], "gr") for link in links}
        if group.site_id not in group_ids:
            continue
        found = False
        for link in links:
            teacher_id = _link_id(link.attrib["href"], "pr")
            if teacher_id is None:
                continue
            fields = [_part.strip() for _part in _text(link).split(",")]
            if len(fields) < 2 or not fields[0] or not fields[1]:
                continue
            teacher = Teacher(
                TIMETABLE_URL + "?" + urlencode({"pr": teacher_id}),
                fields[0],
                [fields[1]],
            )
            result.lessons.append(TimetableLesson(subject, kind, teacher))
            found = True
        if not found:
            warning = f"{subject}: {kind} teacher/position missing for group {group.number}; keeping portal teacher"
            if warning not in result.warnings:
                result.warnings.append(warning)
    return result


def _fetch_html(url: str) -> str:
    try:
        request = Request(url, headers={"User-Agent": "guap-lab-auto"})
        with urlopen(request, timeout=_TIMEOUT) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.netloc != "guap.ru" or final.path.rstrip("/") != "/rasp":
                raise TimetableError("Timetable redirected outside guap.ru/rasp")
            body = response.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                raise TimetableError("Timetable HTML exceeds 4 MiB")
            charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset)
    except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
        raise TimetableNetworkError(f"Public timetable request failed: {type(exc).__name__}") from exc
    except (UnicodeError, LookupError) as exc:
        raise TimetableError("Timetable text encoding is invalid") from exc


class TimetableService:
    def __init__(self, fetch: Callable[[str], str] | None = None) -> None:
        self._fetch = fetch or _fetch_html

    def resolve_group(self, number: str) -> TimetableGroup:
        return parse_group(self._fetch(TIMETABLE_URL), number)

    def load(self, number: str) -> TimetableResult:
        group = self.resolve_group(number)
        url = TIMETABLE_URL + "?" + urlencode({"gr": group.site_id})
        return parse_timetable(self._fetch(url), group)
