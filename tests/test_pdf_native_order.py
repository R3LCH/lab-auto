from __future__ import annotations

from lab_auto.pdf_native import _same_glyphs
from lab_auto.pdf_markdown import pdf_to_markdown
from test_pdf_markdown import codes, make_pdf, page_texts, trusted_font


def _show(x: float, y: float, text: str, size: int) -> bytes:
    return b"BT /F1 %d Tf %g %g Td <%s> Tj ET\n" % (size, x, y, codes(text))


@trusted_font
def test_separately_drawn_scripts_are_emitted_in_painted_x_order(tmp_path):
    # Baseline text first, then a raised and a lowered glyph drawn later into the gap with
    # overlapping x ranges: PDFium and MuPDF order such late-drawn glyphs differently.
    content = (_show(50, 300, "S", 20) + _show(110, 300, "3=3", 20)
               + _show(64, 301.8, "k", 10) + _show(64.5, 298.2, "0", 10) + _show(80, 301.8, "7", 10))
    source = make_pdf(tmp_path / "task.pdf", [content])
    output = pdf_to_markdown(source, tmp_path / "out" / "task.visible.md", dpi=144)
    assert "".join(page_texts(output.read_text())[0].split()) == "Sk073=3"


def test_glyph_pairing_ignores_order_but_not_identity_or_position():
    pdfium = [("S", 50.0, 100.0), ("k", 64.0, 98.2), ("0", 64.5, 101.8)]
    mupdf = [("0", 64.5, 101.8), ("S", 50.0, 100.0), ("k", 64.0, 98.2)]
    assert _same_glyphs(pdfium, mupdf)
    # A different glyph at the same spot, a moved glyph, or a missing/extra glyph is refused.
    assert not _same_glyphs(pdfium, [("S", 50.0, 100.0), ("x", 64.0, 98.2), ("0", 64.5, 101.8)])
    assert not _same_glyphs(pdfium, [("S", 50.0, 100.0), ("k", 64.2, 98.2), ("0", 64.5, 101.8)])
    assert not _same_glyphs(pdfium, mupdf[:2])
    assert not _same_glyphs(pdfium[:2], mupdf)
    # Two equal glyphs cannot both pair with one painted origin.
    assert not _same_glyphs([("k", 64.0, 98.2), ("k", 64.0, 98.2)], [("k", 64.0, 98.2), ("k", 70.0, 98.2)])
