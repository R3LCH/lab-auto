import inspect

from lab_auto.browser import BrowserSession


def test_download_file_does_not_wait_for_domcontentloaded():
    source = inspect.getsource(BrowserSession.download_file)
    assert "wait_until" not in source
    assert "expect_download" in source
