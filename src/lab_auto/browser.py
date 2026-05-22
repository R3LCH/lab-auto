from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from lab_auto.files import atomic_write_text, restrict_private_file
from lab_auto.paths import canonical_task_detail_url, is_task_download_url
from lab_auto.session_store import rewrap_session_file, unwrap_storage_state, wrap_storage_state

BASE_URL = "https://pro.guap.ru"
LOGIN_BASE = "https://sso.guap.ru/realms/master/protocol/openid-connect/auth"
LOGIN_QUERY = {
    "scope": "profile email",
    "response_type": "code",
    "approval_prompt": "auto",
    "redirect_uri": "https://pro.guap.ru/oauth/callback",
    "client_id": "prosuai",
}
TASKS_URL = f"{BASE_URL}/inside/student/tasks/"
TASK_LIST_PER_PAGE = 100
TASKS_LIST_URL = f"{TASKS_URL}?perPage={TASK_LIST_PER_PAGE}"
SESSION_DIR = "session"
_PAGE_LOAD_STATE = "domcontentloaded"
_DEFAULT_TIMEOUT_MS = 60_000
_LOGIN_TIMEOUT_MS = 120_000


def build_login_url() -> str:
    query = {**LOGIN_QUERY, "state": secrets.token_hex(16)}
    return f"{LOGIN_BASE}?{urlencode(query)}"


def session_path(root: Path) -> Path:
    return root / SESSION_DIR / "storage_state.json"


class TaskPageTriggersDownloadError(RuntimeError):
    """Raised when opening a task URL starts a file download instead of HTML."""


def _ensure_task_list_page_size(page: Any, *, per_page: int = TASK_LIST_PER_PAGE) -> None:
    """Set the list to show up to per_page tasks (GUAP defaults to 10)."""
    select = page.locator("#per-page-select-on-list, select[name='perPage']").first
    if select.count() == 0:
        return
    value = str(per_page)
    try:
        current = select.input_value()
    except Exception:
        current = None
    if current == value:
        return
    with page.expect_navigation(wait_until=_PAGE_LOAD_STATE, timeout=_DEFAULT_TIMEOUT_MS):
        select.select_option(value)


def _ensure_tasks_page(page: Any, *, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> None:
    """Navigate to the student task list and confirm the session is authenticated."""
    on_list = "/inside/student/tasks" in page.url and "perPage=" in page.url
    if not on_list:
        page.goto(TASKS_LIST_URL, wait_until=_PAGE_LOAD_STATE, timeout=timeout_ms)
    if "/inside/student/tasks" not in page.url:
        raise RuntimeError(
            "Could not open the GUAP task list. "
            "Check that your account has access to student tasks."
        )
    _ensure_task_list_page_size(page)


def _wait_for_portal_after_sso(page: Any) -> None:
    """Wait until SSO finishes and the browser reaches pro.guap.ru."""
    page.wait_for_url("**://pro.guap.ru/**", timeout=_LOGIN_TIMEOUT_MS)


@dataclass(slots=True)
class BrowserSession:
    manager: Any
    browser: Any
    context: Any

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self.manager.stop()

    def new_page(self) -> Any:
        return self.context.new_page()

    def goto_tasks(self) -> Any:
        page = self.new_page()
        _ensure_tasks_page(page)
        return page

    def task_list_html(self) -> str:
        page = self.goto_tasks()
        try:
            return page.content()
        finally:
            page.close()

    def page_html(self, url: str) -> str:
        if "/inside/student/tasks/" in url:
            url = canonical_task_detail_url(url, BASE_URL)
        page = self.new_page()
        try:
            try:
                page.goto(url, wait_until=_PAGE_LOAD_STATE, timeout=_DEFAULT_TIMEOUT_MS)
            except Exception as exc:
                if "Download is starting" in str(exc):
                    raise TaskPageTriggersDownloadError(url) from exc
                raise
            return page.content()
        finally:
            page.close()

    def download_file(self, url: str, destination: Path) -> None:
        """Save a GUAP file endpoint (usually .../tasks/<id>/download) to disk."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        page = self.new_page()
        try:
            with page.expect_download(timeout=_DEFAULT_TIMEOUT_MS) as download_info:
                # Do not wait for domcontentloaded — download URLs never load as HTML pages.
                page.goto(url, timeout=_DEFAULT_TIMEOUT_MS)
            download_info.value.save_as(str(destination))
        except Exception as exc:
            if "Download is starting" not in str(exc):
                raise
            response = page.context.request.get(url, timeout=_DEFAULT_TIMEOUT_MS)
            if not response.ok:
                raise RuntimeError(
                    f"Download failed ({response.status}): {url}"
                ) from exc
            destination.write_bytes(response.body())
        finally:
            page.close()


class BrowserService:
    def __init__(self, root: Path, headless: bool = True) -> None:
        self.root = root
        self.headless = headless

    def import_cookie_file(self, cookie_file: Path) -> Path:
        target = session_path(self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = cookie_file.read_text(encoding="utf-8")
        raw = json.loads(text)
        if isinstance(raw, list):
            if not raw:
                raise ValueError("Cookie list is empty.")
            data = {"cookies": raw, "origins": []}
        elif isinstance(raw, dict):
            data = unwrap_storage_state(text)
        else:
            raise ValueError("Cookie file must contain a Playwright storage state object or a cookie list.")
        if not isinstance(data, dict) or "cookies" not in data:
            raise ValueError("Cookie file must contain a Playwright storage state object or a cookie list.")
        cookies = data["cookies"]
        if not isinstance(cookies, list) or not cookies:
            raise ValueError("Cookie file must include at least one cookie.")
        atomic_write_text(target, wrap_storage_state(data))
        restrict_private_file(target)
        return target

    def logout(self) -> bool:
        path = session_path(self.root)
        if path.exists():
            path.unlink()
            return True
        return False

    def _sync_playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc
        return sync_playwright

    def open_session(self) -> BrowserSession:
        manager = self._sync_playwright()().start()
        browser = manager.chromium.launch(headless=self.headless)
        storage = session_path(self.root)
        if storage.exists():
            storage_state = unwrap_storage_state(storage.read_text(encoding="utf-8"))
            context = browser.new_context(storage_state=storage_state)
        else:
            context = browser.new_context()
        return BrowserSession(manager=manager, browser=browser, context=context)

    def migrate_session_if_needed(self) -> bool:
        """Re-save legacy plaintext or v1 session files using Fernet encryption."""
        return rewrap_session_file(session_path(self.root))

    def validate_session(self) -> tuple[bool, bool]:
        """Return (session_valid, session_rewrapped_to_encrypted_format)."""
        with self.open_session() as session:
            page = session.goto_tasks()
            try:
                ok = "/inside/student/tasks" in page.url
            finally:
                page.close()
        migrated = self.migrate_session_if_needed() if ok else False
        return ok, migrated

    def save_storage(self, context: Any) -> None:
        path = session_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            context.storage_state(path=str(tmp_path))
            restrict_private_file(tmp_path)
            storage = unwrap_storage_state(tmp_path.read_text(encoding="utf-8"))
            atomic_write_text(path, wrap_storage_state(storage))
        finally:
            tmp_path.unlink(missing_ok=True)
        restrict_private_file(path)

    def login_interactive(self, username: str, password: str) -> None:
        sync_playwright = self._sync_playwright()
        manager = sync_playwright().start()
        browser = manager.chromium.launch(headless=False)
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(build_login_url(), wait_until=_PAGE_LOAD_STATE, timeout=_DEFAULT_TIMEOUT_MS)
            page.fill("input[name='username']", username)
            page.fill("input[name='password']", password)
            page.keyboard.press("Enter")
            _wait_for_portal_after_sso(page)
            _ensure_tasks_page(page, timeout_ms=_LOGIN_TIMEOUT_MS)
            self.save_storage(context)
        finally:
            context.close()
            browser.close()
            manager.stop()
