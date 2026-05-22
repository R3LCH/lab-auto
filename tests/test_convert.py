import pytest

from lab_auto import convert as convert_module
from lab_auto.convert import convert_docx_to_pdf, default_pdf_path


def test_default_pdf_path_changes_suffix(tmp_path):
    assert default_pdf_path(tmp_path / "report.docx") == tmp_path / "report.pdf"


def test_convert_rejects_non_docx(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("x", encoding="utf-8")

    try:
        convert_docx_to_pdf(source)
    except ValueError as exc:
        assert "DOCX" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_convert_rejects_missing_docx(tmp_path):
    try:
        convert_docx_to_pdf(tmp_path / "missing.docx")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_convert_uses_libreoffice_when_available(tmp_path, monkeypatch):
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"PK")
    pdf = tmp_path / "report.pdf"
    calls: list[tuple[Path, Path, str]] = []

    def fake_libreoffice(docx_path: Path, output: Path, soffice: str) -> None:
        calls.append((docx_path, output, soffice))
        output.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(convert_module, "find_libreoffice", lambda: r"C:\LibreOffice\soffice.exe")
    monkeypatch.setattr(convert_module, "convert_with_libreoffice", fake_libreoffice)
    monkeypatch.setattr(convert_module, "convert_with_word", lambda *_: (_ for _ in ()).throw(AssertionError("word should not run")))

    result = convert_docx_to_pdf(docx)
    assert result == pdf
    assert calls == [(docx, pdf, r"C:\LibreOffice\soffice.exe")]


def test_convert_falls_back_to_word_on_windows_without_libreoffice(tmp_path, monkeypatch):
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"PK")
    pdf = tmp_path / "report.pdf"
    calls: list[tuple[Path, Path]] = []

    def fake_word(docx_path: Path, output: Path) -> None:
        calls.append((docx_path, output))
        output.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(convert_module, "find_libreoffice", lambda: None)
    monkeypatch.setattr(convert_module.sys, "platform", "win32")
    monkeypatch.setattr(convert_module, "convert_with_word", fake_word)

    result = convert_docx_to_pdf(docx)
    assert result == pdf
    assert calls == [(docx, pdf)]


def test_convert_falls_back_to_word_when_libreoffice_fails(tmp_path, monkeypatch):
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"PK")
    pdf = tmp_path / "report.pdf"
    word_calls: list[tuple[Path, Path]] = []

    def failing_libreoffice(*_args, **_kwargs):
        raise RuntimeError("LibreOffice crashed")

    def fake_word(docx_path: Path, output: Path) -> None:
        word_calls.append((docx_path, output))
        output.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(convert_module, "find_libreoffice", lambda: r"C:\LibreOffice\soffice.exe")
    monkeypatch.setattr(convert_module, "convert_with_libreoffice", failing_libreoffice)
    monkeypatch.setattr(convert_module.sys, "platform", "win32")
    monkeypatch.setattr(convert_module, "convert_with_word", fake_word)

    result = convert_docx_to_pdf(docx)

    assert result == pdf
    assert word_calls == [(docx, pdf)]


def test_convert_without_libreoffice_or_windows_raises(tmp_path, monkeypatch):
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"PK")
    monkeypatch.setattr(convert_module, "find_libreoffice", lambda: None)
    monkeypatch.setattr(convert_module.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="LibreOffice"):
        convert_docx_to_pdf(docx)
