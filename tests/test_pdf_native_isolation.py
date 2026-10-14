from __future__ import annotations

import pymupdf

from lab_auto.pdf_native import analyze
from test_pdf_markdown import T, _type3_triangle_claimed_bullet, make_pdf, trusted_font

pytestmark = trusted_font



def _analyze(source, stage, dpi=144):
    # analyze() reads the MuPDF worker's page renderings from the stage directory.
    with pymupdf.open(source) as document:
        for index, page in enumerate(document):
            page.get_pixmap(dpi=dpi, alpha=False, annots=True, colorspace=pymupdf.csRGB).save(
                str(stage / f"mupdf-{index + 1:04}.png"))
    return analyze(source, dpi, stage, verify_fonts=True)


def test_refused_page_does_not_invalidate_authenticated_neighbour(tmp_path):
    def customize(pdf):
        _type3_triangle_claimed_bullet(pdf, 0)  # declared but never shown on page 1
        _type3_triangle_claimed_bullet(pdf, 1)

    source = make_pdf(tmp_path / "task.pdf",
                      [T(50, 300, "MOVE", size=48), b"BT /F2 24 Tf 50 300 Td <95> Tj ET\n" + T(80, 300, "Cross")],
                      customize=customize)
    stage = tmp_path / "stage"
    stage.mkdir()

    rows, recovery = _analyze(source, stage)

    assert len(rows) == 2
    clean, refused = rows
    assert (clean["text"], clean["source"], clean["verificationReason"]) == ("MOVE", "native", None)
    assert clean["glyphs"]["authenticated"] == clean["glyphs"]["emitted"] == 4
    assert (refused["text"], refused["source"], refused["glyphs"]) == ("", None, None)
