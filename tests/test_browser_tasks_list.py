from lab_auto.browser import TASKS_LIST_URL, TASK_LIST_PER_PAGE


def test_tasks_list_url_requests_max_page_size():
    assert f"perPage={TASK_LIST_PER_PAGE}" in TASKS_LIST_URL
    assert TASKS_LIST_URL.endswith(f"perPage={TASK_LIST_PER_PAGE}")
