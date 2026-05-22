from unittest.mock import MagicMock

from lab_auto.browser import TASKS_URL, _ensure_tasks_page, _wait_for_portal_after_sso


def test_ensure_tasks_page_skips_goto_when_already_on_tasks():
    page = MagicMock()
    page.url = "https://pro.guap.ru/inside/student/tasks/"

    _ensure_tasks_page(page)

    page.goto.assert_not_called()


def test_ensure_tasks_page_navigates_to_tasks_url():
    page = MagicMock()
    page.url = "https://pro.guap.ru/inside/dashboard"

    def after_goto(*_args, **_kwargs):
        page.url = "https://pro.guap.ru/inside/student/tasks/"

    page.goto.side_effect = after_goto

    _ensure_tasks_page(page)

    page.goto.assert_called_once_with(
        TASKS_URL,
        wait_until="domcontentloaded",
        timeout=60_000,
    )


def test_wait_for_portal_after_sso_waits_for_guap_domain():
    page = MagicMock()

    _wait_for_portal_after_sso(page)

    page.wait_for_url.assert_called_once_with("**://pro.guap.ru/**", timeout=120_000)
