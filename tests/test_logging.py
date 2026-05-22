from lab_auto.logging import LogKind, append_log


def test_append_ai_log_uses_prefixed_daily_file(tmp_path):
    append_log(
        tmp_path,
        LogKind.AI,
        "Generated report draft",
        now="2026-05-20T15:30:00+03:00",
    )

    path = tmp_path / "logs" / "[AI] 2026-05-20.md"
    assert path.exists()
    assert "15:30:00" in path.read_text(encoding="utf-8")


def test_append_website_log_uses_website_prefix(tmp_path):
    message = "math-lab-1: не принят -> принят"
    append_log(
        tmp_path,
        LogKind.WEBSITE,
        message,
        now="2026-05-20T15:30:00+03:00",
    )

    path = tmp_path / "logs" / "[WEBSITE] 2026-05-20.md"
    assert message in path.read_text(encoding="utf-8")


def test_append_log_appends_one_bullet_per_call(tmp_path):
    first = append_log(
        tmp_path,
        LogKind.AI,
        "Generated report draft",
        now="2026-05-20T15:30:00+03:00",
    )
    second = append_log(
        tmp_path,
        LogKind.AI,
        "Checked rubric",
        now="2026-05-20T15:31:00+03:00",
    )

    assert second == first
    assert first.read_text(encoding="utf-8").splitlines() == [
        "- 15:30:00 - Generated report draft",
        "- 15:31:00 - Checked rubric",
    ]


def test_append_log_sanitizes_message_to_single_markdown_bullet(tmp_path):
    path = append_log(
        tmp_path,
        LogKind.WEBSITE,
        "math-lab-1\n- injected `status`",
        now="2026-05-20T15:30:00+03:00",
    )

    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 1
    assert text == "- 15:30:00 - math-lab-1 - injected 'status'\n"
