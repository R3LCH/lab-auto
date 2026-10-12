from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import io
from pathlib import Path

import pikepdf
import pymupdf
import pytest
import pypdfium2 as pdfium
from fontTools.ttLib import TTFont

from lab_auto.pdf_font_recovery import recover_font_mappings
from lab_auto.pdf_markdown import PdfVisibilityError
from lab_auto.pdf_native import _references
from test_pdf_markdown import DEJAVU, gid, trusted_font

pytestmark = trusted_font
PROGRAM_SHA = hashlib.sha256(DEJAVU.read_bytes()).digest() if DEJAVU.is_file() else b""


def hexcodes(values) -> bytes:
    return "".join(f"{value:04X}" for value in values).encode()


def dejavu_pdf(text: str = "Note") -> pikepdf.Pdf:
    """pymupdf-embedded DejaVuSans: indirect Type0, Identity-H, CIDFontType2 + FontFile2."""
    with pymupdf.open() as document:
        page = document.new_page(width=612, height=400)
        page.insert_font(fontname="F0", fontfile=str(DEJAVU))
        page.insert_text((50, 300), text, fontname="F0", fontsize=20)
        return pikepdf.open(io.BytesIO(document.tobytes()))


def font_of(pdf: pikepdf.Pdf) -> pikepdf.Dictionary:
    return pdf.pages[0].Resources.Font.F0

def forge(claimed: str, shown: str) -> bytes:
    """DejaVu whose glyph for `claimed` draws the `shown` outline; every other table stays byte-identical."""
    font = TTFont(DEJAVU, lazy=True, recalcBBoxes=False, recalcTimestamp=False)
    glyphs = font["glyf"]
    glyphs[font.getBestCmap()[ord(claimed)]] = copy.deepcopy(glyphs[font.getBestCmap()[ord(shown)]])
    data = io.BytesIO()
    font.save(data)
    return data.getvalue()


def lying_tounicode(pdf: pikepdf.Pdf) -> pikepdf.Stream:
    # Claims every low code is "X": a source ToUnicode is never authority.
    blocks = b"".join(
        b"100 beginbfchar\n" + b"".join(b"<%04X> <0058>\n" % code for code in range(start, start + 100))
        + b"endbfchar\n" for start in range(0, 1000, 100))
    return pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n" + blocks
        + b"endcmap CMapName currentdict /CMap defineresource pop end end\n")


def clone_font(pdf: pikepdf.Pdf, font, *, program: bytes | None = None, encoding: str | None = None,
               indirect: bool = True):
    descendant = font.DescendantFonts[0]
    descriptor = pikepdf.Dictionary(descendant.FontDescriptor)
    if program is not None:
        descriptor.FontFile2 = pdf.make_stream(program)
    clone = pikepdf.Dictionary(descendant)
    clone.FontDescriptor = pdf.make_indirect(descriptor)
    result = pikepdf.Dictionary(font)
    result.DescendantFonts = [pdf.make_indirect(clone)]
    result.ToUnicode = lying_tounicode(pdf)
    if encoding is not None:
        result.Encoding = pikepdf.Name(encoding)
    return pdf.make_indirect(result) if indirect else result


def form(pdf: pikepdf.Pdf, content: bytes, resources=None) -> pikepdf.Stream:
    stream = pdf.make_stream(content)
    stream.Type, stream.Subtype, stream.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 612, 400]
    if resources is not None:
        stream.Resources = resources
    return stream


def text_of(path: Path) -> str:
    """Decoded text as the native consumer (PDFium) reads it."""
    with closing(pdfium.PdfDocument(str(path))) as document:
        texts = []
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                texts.append(textpage.get_text_range())
    return "".join(texts).replace("\r", "").replace("\n", "").replace(" ", "")


def save(pdf: pikepdf.Pdf, path: Path) -> tuple[Path, bytes]:
    pdf.save(path)
    return path, hashlib.sha256(path.read_bytes()).digest()


def label(path: Path, find) -> str:
    """Object label after saving: pikepdf renumbers objects, so resolve through the saved file."""
    with pikepdf.open(path) as pdf:
        number, generation = find(pdf).objgen
    return f"{number} {generation}"


def glyph_gids(diagnostics: dict, name: str) -> list[int]:
    [entry] = [entry for entry in diagnostics["fonts"] if entry["object"] == name]
    return [glyph["gid"] for glyph in entry["glyphs"]]


@pytest.fixture
def stage(tmp_path):
    directory = tmp_path / "stage"
    directory.mkdir()
    return directory


def test_trusted_program_replaces_lying_tounicode_without_touching_source(tmp_path, stage):
    pdf = dejavu_pdf("Note")
    font_of(pdf).ToUnicode = lying_tounicode(pdf)
    source, digest = save(pdf, tmp_path / "lying.pdf")
    assert text_of(source) == "XXXX"

    working, bindings, diagnostics = recover_font_mappings(source, stage, _references())

    assert hashlib.sha256(source.read_bytes()).digest() == digest
    assert working == stage / "font-recovered.pdf"
    assert text_of(working) == "Note"
    assert bindings == {(PROGRAM_SHA, gid(char)): char for char in "Note"}
    assert diagnostics["status"] == "recovered"
    assert diagnostics["renderCheck"] == {"pdfium": "identical", "mupdf": "identical"}
    [entry] = diagnostics["fonts"]
    assert (entry["evidence"], entry["rewritten"], entry["programSHA256"]) == ("fast-path", True, PROGRAM_SHA.hex())


def test_forged_outline_is_not_bound_to_its_claimed_character(tmp_path, stage):
    pdf = dejavu_pdf("Note")
    forged = forge("e", "o")  # gid(e) now draws the "o" outline with e's metrics
    font_of(pdf).DescendantFonts[0].FontDescriptor.FontFile2.write(forged)
    source, digest = save(pdf, tmp_path / "forged.pdf")

    working, bindings, diagnostics = recover_font_mappings(source, stage, _references())

    assert hashlib.sha256(source.read_bytes()).digest() == digest
    forged_sha = hashlib.sha256(forged).digest()
    assert bindings == {(forged_sha, gid(char)): char for char in "Not"}
    assert text_of(working) == "Not\ufffd"
    [entry] = diagnostics["fonts"]
    assert entry["evidence"] == "anchored"
    assert {glyph["gid"]: glyph["status"] for glyph in entry["glyphs"]}[gid("e")] != "bound"


def test_codes_follow_tf_scopes_and_nested_form_resources(tmp_path, stage):
    pdf = dejavu_pdf("x")
    page_font = font_of(pdf)
    page_font.ToUnicode = lying_tounicode(pdf)
    scoped = clone_font(pdf, page_font)
    form_font = clone_font(pdf, page_font)
    annotation_font = clone_font(pdf, page_font)
    # The inner form has no /Resources: it inherits the outer form's, where F0 is form_font.
    inner = form(pdf, b"BT 50 100 Td <%s> Tj ET\n" % hexcodes([gid("c")]))
    outer = form(pdf, b"BT /F0 20 Tf 50 150 Td <%s> Tj ET q BT /F0 20 Tf ET /Inner Do Q\n" % hexcodes([gid("b")]),
                 pikepdf.Dictionary(Font=pikepdf.Dictionary(F0=form_font), XObject=pikepdf.Dictionary(Inner=inner)))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        b"BT /F0 20 Tf q /S 20 Tf 50 300 Td <%s> Tj Q 50 250 Td [<%s> -100 (%s)] TJ ET /Outer Do\n"
        % (hexcodes([gid("s")]), hexcodes([gid("a")]), bytes.fromhex(hexcodes([gid("d")]).decode())))
    # Page resources come from the page tree by inheritance.
    pdf.Root.Pages.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F0=page_font, S=scoped), XObject=pikepdf.Dictionary(Outer=outer))
    del page.obj["/Resources"]
    page.obj.Annots = [pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Stamp, Rect=[0, 0, 100, 100],
        AP=pikepdf.Dictionary(N=form(pdf, b"BT /F0 20 Tf <%s> Tj ET\n" % hexcodes([gid("z")]),
                                     pikepdf.Dictionary(Font=pikepdf.Dictionary(F0=annotation_font))))))]
    source, digest = save(pdf, tmp_path / "scoped.pdf")

    working, bindings, diagnostics = recover_font_mappings(source, stage, _references())

    assert hashlib.sha256(source.read_bytes()).digest() == digest
    fonts = lambda pdf: pdf.pages[0].Resources.Font  # noqa: E731
    outer_resources = lambda pdf: pdf.pages[0].Resources.XObject.Outer.Resources  # noqa: E731
    assert glyph_gids(diagnostics, label(source, lambda pdf: fonts(pdf).F0)) == sorted([gid("a"), gid("d")])
    assert glyph_gids(diagnostics, label(source, lambda pdf: fonts(pdf).S)) == [gid("s")]
    assert glyph_gids(diagnostics, label(source, lambda pdf: outer_resources(pdf).Font.F0)) == sorted(
        [gid("b"), gid("c")])
    annotation = label(source, lambda pdf: pdf.pages[0].obj.Annots[0].AP.N.Resources.Font.F0)
    assert annotation not in {entry["object"] for entry in diagnostics["fonts"]}
    assert set(bindings.values()) == set("abcds")
    assert sorted(text_of(working).replace("z", "").replace("X", "")) == sorted("abcds")
    assert diagnostics["renderCheck"] == {"pdfium": "identical", "mupdf": "identical"}


def mapped_pdf(tmp_path: Path, content: bytes) -> tuple[Path, bytes]:
    pdf = dejavu_pdf("x")
    font = font_of(pdf)
    font.ToUnicode = lying_tounicode(pdf)
    table = b"\0\0" + b"".join(gid(char).to_bytes(2, "big") for char in "Note")  # cid 0 -> 0, cid 1..4
    font.DescendantFonts[0].CIDToGIDMap = pdf.make_stream(table)
    pdf.pages[0].Contents = pdf.make_stream(b"BT /F0 20 Tf 50 300 Td <%s> Tj ET\n" % content)
    return save(pdf, tmp_path / "mapped.pdf")


def test_explicit_cid_to_gid_map_binds_codes_through_the_map(tmp_path, stage):
    source, digest = mapped_pdf(tmp_path, b"00010002000300040000")
    assert text_of(source) == "XXXXX"

    working, bindings, diagnostics = recover_font_mappings(source, stage, _references())

    assert hashlib.sha256(source.read_bytes()).digest() == digest
    # cid 0 maps to gid 0 (.notdef), which no trusted reference binds.
    assert text_of(working) == "Note\ufffd"
    assert bindings == {(PROGRAM_SHA, gid(char)): char for char in "Note"}
    assert glyph_gids(diagnostics, label(source, lambda pdf: pdf.pages[0].Resources.Font.F0)) == sorted(
        [0, *(gid(char) for char in "Note")])
    assert diagnostics["renderCheck"] == {"pdfium": "identical", "mupdf": "identical"}


def test_staged_copy_that_paints_differently_is_refused(tmp_path, stage):
    # cid 9 lies beyond the map stream: PDFium paints it with a fallback chosen from ToUnicode,
    # so replacing the map changes painting and recovery must refuse the staged copy.
    source, digest = mapped_pdf(tmp_path, b"00010009")

    with pytest.raises(PdfVisibilityError, match="changed page painting"):
        recover_font_mappings(source, stage, _references())

    assert hashlib.sha256(source.read_bytes()).digest() == digest


def test_rejected_and_unsupported_fonts_leave_source_as_working_pdf(tmp_path, stage):
    pdf = dejavu_pdf("x")
    base = font_of(pdf)
    rejected = clone_font(pdf, base, program=b"not a font program")
    vertical = clone_font(pdf, base, encoding="/Identity-V")
    direct = clone_font(pdf, base, indirect=False)
    odd = clone_font(pdf, base)
    pdf.pages[0].Resources.Font = pikepdf.Dictionary(R=rejected, V=vertical, D=direct, O=odd)
    code = hexcodes([gid("A")])
    pdf.pages[0].Contents = pdf.make_stream(
        b"BT /R 20 Tf 50 300 Td <%s> Tj /V 20 Tf <%s> Tj /D 20 Tf <%s> Tj /O 20 Tf <%s00> Tj ET\n"
        % (code, code, code, code))
    source, digest = save(pdf, tmp_path / "unsupported.pdf")

    working, bindings, diagnostics = recover_font_mappings(source, stage, _references())

    assert (working, bindings) == (source, {})
    assert hashlib.sha256(source.read_bytes()).digest() == digest
    assert not (stage / "font-recovered.pdf").exists()
    assert diagnostics["status"] == "unchanged" and diagnostics["renderCheck"] is None
    [entry] = diagnostics["fonts"]
    fonts = lambda pdf: pdf.pages[0].Resources.Font  # noqa: E731
    assert (entry["object"], entry["evidence"], entry["rewritten"]) == (
        label(source, lambda pdf: fonts(pdf).R), "rejected", False)
    reasons = {item["object"]: item["reason"] for item in diagnostics["unsupported"]}
    assert reasons == {label(source, lambda pdf: fonts(pdf).V): "encoding is not Identity-H",
                       label(source, lambda pdf: fonts(pdf).O): "odd-length Identity-H string",
                       None: "font is a direct dictionary"}
