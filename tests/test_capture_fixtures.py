import importlib.util
import sys
from pathlib import Path

import pytest


def capture_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "capture_guap_fixtures.py"
    spec = importlib.util.spec_from_file_location("capture_guap_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_materials_uses_existing_session(tmp_path, monkeypatch):
    module = capture_script()
    requested = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def page_html(self, url):
            requested.append(url)
            return "<html>anonymous captured page</html>"

    class Browser:
        def __init__(self, root):
            assert root == tmp_path

        def open_session(self):
            return Session()

    monkeypatch.setattr(module, "BrowserService", Browser)
    monkeypatch.setattr(sys, "argv", ["capture", "--materials", "--root", str(tmp_path),
                                      "--output-dir", str(tmp_path / "capture")])
    module.main()
    assert requested == ["https://pro.guap.ru/inside/student/materials?perPage=100"]
    assert (tmp_path / "capture" / "materials.html").read_text(encoding="utf-8") == "<html>anonymous captured page</html>"
    assert not (tmp_path / "capture" / "task_detail.html").exists()


def test_capture_requires_a_target(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["capture"])
    with pytest.raises(SystemExit) as exc:
        capture_script().main()
    assert exc.value.code == 2
