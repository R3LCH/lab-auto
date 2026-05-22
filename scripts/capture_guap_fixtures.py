#!/usr/bin/env python3
"""Capture GUAP HTML fixtures for local tests (gitignored — may contain personal data)."""

from __future__ import annotations

import argparse
from pathlib import Path

from lab_auto.browser import BrowserService
from lab_auto.config import resolve_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Save GUAP task list/detail HTML fixtures.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace directory (default: saved workspace or current directory).",
    )
    parser.add_argument(
        "--task-url",
        required=True,
        help="Full task detail URL, e.g. https://pro.guap.ru/inside/student/tasks/12345",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures"),
        help="Directory for task_list.html and task_detail.html",
    )
    args = parser.parse_args()

    root = resolve_workspace(args.root)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with BrowserService(root).open_session() as session:
        list_page = session.goto_tasks()
        (output_dir / "task_list.html").write_text(list_page.content(), encoding="utf-8")
        list_page.close()

        detail_page = session.new_page()
        detail_page.goto(args.task_url, wait_until="domcontentloaded", timeout=60_000)
        (output_dir / "task_detail.html").write_text(detail_page.content(), encoding="utf-8")
        detail_page.close()

    print(f"Saved {output_dir / 'task_list.html'}")
    print(f"Saved {output_dir / 'task_detail.html'}")
    print("These files are gitignored. Do not commit them — they may contain personal data.")


if __name__ == "__main__":
    main()
