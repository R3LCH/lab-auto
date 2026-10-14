from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json

import pikepdf
import pytest
from fontTools.ttLib import TTFont

from lab_auto.pdf_markdown import PdfVisibilityError, pdf_to_markdown
from test_pdf_markdown import DEJAVU, T, gid, make_pdf, page_texts, trusted_font

pytestmark = trusted_font


def program_variant(mode):
    with TTFont(io.BytesIO(DEJAVU.read_bytes()), recalcBBoxes=False, recalcTimestamp=False) as font:
        cmap = font.getBestCmap()
        m, n = cmap[ord("M")], cmap[ord("N")]
        if mode in {"transplant", "contour"}:
            font["glyf"][m] = deepcopy(font["glyf"][n])
            if mode == "transplant":
                font["hmtx"][m] = font["hmtx"][n]
        elif mode == "metadata":
            for table in font["cmap"].tables:
                if table.isUnicode():
                    table.cmap = {ord(fake): cmap[ord(real)] for real, fake in zip("MOVE", "QZXJ")}
            for record in font["name"].names:
                record.string = "ForgedIdentity".encode(record.getEncoding())
        elif mode == "deform":
            glyph = font["glyf"][m]
            x, y = glyph.coordinates[0]
            glyph.coordinates[0] = (x + 173, y + 91)
        elif mode == "glyphhint":
            glyph = font["glyf"][m]
            glyph.program.fromBytecode(glyph.program.getBytecode() + b"\x00")
        elif mode == "globalhint":
            hint = font["fpgm"].program
            hint.fromBytecode(hint.getBytecode() + b"\x00")
        else:
            raise AssertionError(mode)
        output = io.BytesIO()
        font.save(output)
        return output.getvalue()


@pytest.mark.parametrize(("mode", "expected"), [
    ("tounicode", "MOVE"), ("metadata", "MOVE"), ("transplant", "NOVE"),
])
def test_publishes_independently_recovered_text_not_the_claim(tmp_path, mode, expected):
    program = DEJAVU.read_bytes() if mode == "tounicode" else program_variant(mode)
    source = make_pdf(tmp_path / "task.pdf", [T(50, 300, "MOVE", size=48)],
                      program=program, tounicode=dict(zip("MOVE", "QZXJ")))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = pdf_to_markdown(source, dpi=144, verify_fonts=True)
    assert page_texts(output.read_text())[0].strip() == expected
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    report = json.loads((tmp_path / "native_result_task.json").read_text())
    assert report["status"] == "passed"
    assert report["sourceSHA256"] == digest
    assert report["markdownSHA256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    recovery = report["fontRecovery"]
    assert recovery["renderCheck"] == {"pdfium": "identical", "mupdf": "identical"}
    if mode == "transplant":
        changed = [glyph for font in recovery["fonts"] for glyph in font["glyphs"] if glyph["gid"] == gid("M")]
        assert len(changed) == 1 and changed[0]["text"] == "N"


@pytest.mark.parametrize("mode", ["contour", "deform", "glyphhint", "globalhint"])
def test_unattested_changed_glyph_or_vm_cannot_publish_claimed_unicode(tmp_path, mode):
    source = make_pdf(tmp_path / "task.pdf", [T(50, 300, "MOVE", size=48)],
                      program=program_variant(mode))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(PdfVisibilityError):
        pdf_to_markdown(source, dpi=144, verify_fonts=True)
    assert not source.with_suffix(".visible.md").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    report = json.loads((tmp_path / "native_result_task.json").read_text())
    assert report["status"] == "failed" and report["markdownSHA256"] is None


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_actual_program_identity_survives_crop_and_rotation(tmp_path, rotation):
    def crop(pdf):
        pdf.pages[0].CropBox = pikepdf.Array([20, 30, 500, 380])
        pdf.pages[0].Rotate = rotation
    source = make_pdf(tmp_path / "task.pdf", [T(50, 300, "MOVE", size=48)],
                      program=program_variant("transplant"), customize=crop)
    output = pdf_to_markdown(source, dpi=144, verify_fonts=True)
    assert page_texts(output.read_text())[0].strip() == "NOVE"


def test_hidden_trusted_font_cannot_authenticate_coincident_untrusted_ink(tmp_path):
    def add_trusted_hidden_font(pdf):
        visible = pdf.pages[0].Resources.Font.F1
        descendant = pikepdf.Dictionary(visible.DescendantFonts[0])
        descriptor = pikepdf.Dictionary(descendant.FontDescriptor)
        descriptor.FontFile2 = pdf.make_stream(DEJAVU.read_bytes())
        descendant.FontDescriptor = pdf.make_indirect(descriptor)
        hidden = pikepdf.Dictionary(visible)
        hidden.DescendantFonts = pikepdf.Array([pdf.make_indirect(descendant)])
        pdf.pages[0].Resources.Font.F2 = pdf.make_indirect(hidden)
    visible = T(50, 300, "MOVE", size=48)
    hidden = b"q BT 3 Tr /F2 48 Tf 50 300 Td <" + f"{gid('M'):04X}".encode() + b"> Tj ET Q\n"
    source = make_pdf(tmp_path / "task.pdf", [visible + hidden],
                      program=program_variant("globalhint"), customize=add_trusted_hidden_font)
    with pytest.raises(PdfVisibilityError):
        pdf_to_markdown(source, dpi=144, verify_fonts=True)
    assert not source.with_suffix(".visible.md").exists()
