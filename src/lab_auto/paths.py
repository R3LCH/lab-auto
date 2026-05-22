from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from lab_auto.models import LocalStatus

_TASK_URL_SEGMENT_RE = re.compile(
    r"^/inside/student/tasks/([^/]+)(?:/download)?/?$",
    re.IGNORECASE,
)

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_STATUS_PREFIX = re.compile(
    r"^\[(?:" + "|".join(re.escape(status.value) for status in LocalStatus) + r")\]\s*"
)
_LEGACY_STATUS_PREFIXES = ("[UNKNOWN]",)
_WHITESPACE = re.compile(r"\s+")
_SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")
_MAX_NAME_LENGTH = 100
_MAX_WORK_FOLDER_TITLE_LENGTH = 72

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def rel_path(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _translit_map() -> dict[int, str]:
    pairs = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    mapping: dict[int, str] = {}
    for source, target in pairs.items():
        mapping[ord(source)] = target
        mapping[ord(source.upper())] = target
    return mapping


_RU_TRANSLIT = str.maketrans(_translit_map())


def _folder_has_status_prefix(folder_name: str) -> bool:
    if _STATUS_PREFIX.match(folder_name):
        return True
    return any(folder_name.startswith(f"{legacy} ") for legacy in _LEGACY_STATUS_PREFIXES)


def extract_report_site_id(report_url: str) -> str:
    path = urlparse(report_url).path.rstrip("/")
    match = re.match(r"^/inside/student/reports/([^/]+)/download$", path, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1)


def extract_task_site_id(task_url: str) -> str:
    path = urlparse(task_url).path.rstrip("/")
    match = _TASK_URL_SEGMENT_RE.match(path)
    if not match:
        return ""
    return match.group(1)


def is_task_download_url(task_url: str) -> bool:
    path = urlparse(task_url).path.rstrip("/").lower()
    return path.endswith("/download")


def canonical_task_detail_url(task_url: str, base_url: str) -> str:
    """Return the task detail page URL (never the PDF download endpoint)."""
    site_id = extract_task_site_id(task_url)
    if not site_id:
        return task_url
    return urljoin(base_url, f"/inside/student/tasks/{site_id}")


def task_pdf_download_url(site_id: str, base_url: str) -> str:
    return urljoin(base_url, f"/inside/student/tasks/{site_id}/download")


def safe_name(value: str) -> str:
    name = value.strip()
    while True:
        stripped = _STATUS_PREFIX.sub("", name, count=1)
        if stripped == name:
            break
        name = stripped
    name = _INVALID_FILENAME_CHARS.sub(" ", name)
    name = _WHITESPACE.sub(" ", name).strip().rstrip(" .")
    if not name:
        return "unnamed"
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > _MAX_NAME_LENGTH:
        name = name[:_MAX_NAME_LENGTH].rstrip(" .")
    return name or "unnamed"


def work_folder_label(work_name: str, *, task_site_id: str | None = None) -> str:
    """Human-readable folder title: task name first, GUAP id in brackets."""
    title = safe_name(work_name)
    if task_site_id:
        suffix = f" [{task_site_id}]"
        max_title = max(20, _MAX_WORK_FOLDER_TITLE_LENGTH - len(suffix))
        if len(title) > max_title:
            title = title[:max_title].rstrip(" .")
        return f"{title}{suffix}"
    if len(title) > _MAX_WORK_FOLDER_TITLE_LENGTH:
        return title[:_MAX_WORK_FOLDER_TITLE_LENGTH].rstrip(" .")
    return title


def status_folder_name(
    name: str,
    status: LocalStatus,
    *,
    task_site_id: str | None = None,
) -> str:
    return f"{status.prefix} {work_folder_label(name, task_site_id=task_site_id)}"


def normalized_work_names(subject: str, name: str) -> tuple[str, str]:
    return safe_name(subject), safe_name(name)


def legacy_work_id(subject: str, name: str) -> str:
    norm_subject, norm_name = normalized_work_names(subject, name)
    source = f"{norm_subject} {norm_name}".lower().translate(_RU_TRANSLIT)
    slug = _SLUG_SEPARATOR.sub("-", source).strip("-")
    return slug or "unnamed"


def build_work_id(task_url: str, subject: str = "", name: str = "") -> str:
    site_id = extract_task_site_id(task_url)
    if site_id:
        return f"task-{site_id}"
    return legacy_work_id(subject, name)


def _folder_label_matches(
    candidate_folder_name: str,
    work_name: str,
    task_site_id: str | None,
) -> bool:
    candidate_label = safe_name(candidate_folder_name)
    return candidate_label in {
        work_folder_label(work_name, task_site_id=task_site_id),
        safe_name(work_name),
    }


def sync_work_folder(
    root: Path,
    subject: str,
    work_name: str,
    status: LocalStatus,
    *,
    task_site_id: str | None = None,
) -> Path:
    subject_dir = root / "labs" / safe_name(subject)
    target = subject_dir / status_folder_name(
        work_name,
        status,
        task_site_id=task_site_id,
    )

    subject_dir.mkdir(parents=True, exist_ok=True)
    target_exists = target.exists()
    if target_exists and target.is_file():
        raise FileExistsError(f"Work folder target exists as a file: {target}")

    matches = []
    for candidate in subject_dir.iterdir():
        if (
            candidate.is_dir()
            and candidate != target
            and _folder_has_status_prefix(candidate.name)
            and _folder_label_matches(candidate.name, work_name, task_site_id)
        ):
            matches.append(candidate)

    if target_exists and matches:
        raise ValueError(
            f"Found existing target and duplicate folders for work {work_name!r}: "
            + ", ".join(str(match) for match in sorted(matches))
        )
    if target_exists:
        return target

    if len(matches) > 1:
        raise ValueError(
            f"Found multiple existing folders for work {work_name!r}: "
            + ", ".join(str(match) for match in sorted(matches))
        )
    if matches:
        matches[0].rename(target)
        return target

    target.mkdir(parents=True, exist_ok=True)
    return target
