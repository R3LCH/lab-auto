"""Replace untrusted ToUnicode maps with bindings authenticated against trusted font programs.

The source PDF is never modified: rewritten maps go to a staged copy whose painting must
match the original byte-for-byte in both PDFium and MuPDF.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path

import pikepdf
import pymupdf
import pypdfium2 as pdfium

from lab_auto.pdf_glyph_evidence import authenticate_program
from lab_auto.pdf_markdown import PdfVisibilityError

_MAX_FORM_DEPTH = 64
_SHOW_OPERATORS = {"Tj", "TJ", "'", '"'}
_BFCHAR_BLOCK = 100
_REPLACEMENT = "\ufffd"


def _object_label(obj) -> str | None:
    number, generation = obj.objgen
    return f"{number} {generation}" if number else None


def _font_program(font):
    """Return (program bytes, cid->gid map bytes or None) for a supported font, else a reason string."""
    if not font.is_indirect:
        return "font is a direct dictionary"
    if font.get("/Subtype") != pikepdf.Name.Type0:
        return "font is not Type0"
    encoding = font.get("/Encoding")
    if not isinstance(encoding, pikepdf.Name) or encoding != pikepdf.Name("/Identity-H"):
        return "encoding is not Identity-H"
    descendants = font.get("/DescendantFonts")
    if not isinstance(descendants, pikepdf.Array) or len(descendants) != 1:
        return "font does not have exactly one descendant"
    descendant = descendants[0]
    if not isinstance(descendant, pikepdf.Dictionary) or descendant.get("/Subtype") != pikepdf.Name.CIDFontType2:
        return "descendant is not CIDFontType2"
    descriptor = descendant.get("/FontDescriptor")
    if not isinstance(descriptor, pikepdf.Dictionary):
        return "descendant has no font descriptor"
    program = descriptor.get("/FontFile2")
    if not isinstance(program, pikepdf.Stream) or "/FontFile" in descriptor or "/FontFile3" in descriptor:
        return "font program is not an embedded FontFile2"
    mapping = descendant.get("/CIDToGIDMap")
    if mapping is not None and not isinstance(mapping, pikepdf.Stream) and mapping != pikepdf.Name.Identity:
        return "unsupported CIDToGIDMap"
    try:
        data = program.read_bytes()
        table = mapping.read_bytes() if isinstance(mapping, pikepdf.Stream) else None
    except pikepdf.PdfError:
        return "font program or CIDToGIDMap cannot be decoded"
    return data, table


def _gid(table: bytes | None, cid: int) -> int:
    if table is None:
        return cid
    offset = 2 * cid
    return int.from_bytes(table[offset:offset + 2], "big") if offset + 2 <= len(table) else 0


def _page_resources(page: pikepdf.Page):
    holder = page.obj
    while "/Resources" not in holder and "/Parent" in holder:
        holder = holder.Parent
    resources = holder.get("/Resources")
    return (resources if isinstance(resources, pikepdf.Dictionary) else pikepdf.Dictionary()), holder.objgen


def _collect_used(pdf: pikepdf.Pdf):
    """Map font objgen -> (font, strings shown) across pages and nested Forms; list direct fonts."""
    used: dict[tuple[int, int], tuple[pikepdf.Object, list[bytes]]] = {}
    direct: list[pikepdf.Object] = []
    visited: set = set()

    def record(font, value: bytes) -> None:
        if not isinstance(font, pikepdf.Dictionary):
            return  # unidentifiable font: the native checks reject this text
        if not font.is_indirect:
            direct.append(font)
            return
        used.setdefault(font.objgen, (font, []))[1].append(value)

    def walk(container, resources, scope, font, depth, active):
        identity = (container.objgen, scope, font.objgen if isinstance(font, pikepdf.Dictionary) else None)
        if container.objgen in active:
            raise PdfVisibilityError("Recursive PDF form")
        if depth > _MAX_FORM_DEPTH:
            raise PdfVisibilityError("PDF forms are nested too deeply")
        if identity in visited:
            return
        visited.add(identity)
        active = active | {container.objgen}
        stack = []
        for instruction in pikepdf.parse_content_stream(container):
            if isinstance(instruction, pikepdf.ContentStreamInlineImage):
                continue
            operands, operator = instruction.operands, str(instruction.operator)
            if operator == "q":
                stack.append(font)
            elif operator == "Q":
                if stack:
                    font = stack.pop()
            elif operator == "Tf" and operands:
                font = resources.get("/Font", {}).get(operands[0])
            elif operator == "Do" and operands:
                form = resources.get("/XObject", {}).get(operands[0])
                if isinstance(form, pikepdf.Stream) and form.get("/Subtype") == pikepdf.Name.Form:
                    own = form.get("/Resources")
                    if isinstance(own, pikepdf.Dictionary):
                        walk(form, own, form.objgen, font, depth + 1, active)
                    else:
                        walk(form, resources, scope, font, depth + 1, active)
            elif operator in _SHOW_OPERATORS and operands:
                values = operands[0] if operator == "TJ" else [operands[-1]]
                for value in values:
                    if isinstance(value, pikepdf.String):
                        record(font, bytes(value))

    for page in pdf.pages:
        resources, scope = _page_resources(page)
        walk(page, resources, scope, None, 0, frozenset())
    return used, direct


def _tounicode(pdf: pikepdf.Pdf, entries: list[tuple[int, str]]) -> pikepdf.Stream:
    blocks = []
    for start in range(0, len(entries), _BFCHAR_BLOCK):
        chunk = entries[start:start + _BFCHAR_BLOCK]
        lines = "".join(f"<{code:04X}> <{text.encode('utf-16-be').hex().upper()}>\n" for code, text in chunk)
        blocks.append(f"{len(chunk)} beginbfchar\n{lines}endbfchar\n")
    return pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        + "".join(blocks).encode("ascii")
        + b"endcmap CMapName currentdict /CMap defineresource pop end end\n")


def _check_painting(original: Path, staged: Path) -> dict:
    with closing(pdfium.PdfDocument(str(original))) as left, closing(pdfium.PdfDocument(str(staged))) as right:
        if len(left) != len(right):
            raise PdfVisibilityError("Font recovery changed page painting")
        for index in range(len(left)):
            with closing(left[index]) as one, closing(right[index]) as two:
                with closing(one.render(scale=2, fill_color=(255, 255, 255, 255), draw_annots=True)) as a, \
                        closing(two.render(scale=2, fill_color=(255, 255, 255, 255), draw_annots=True)) as b:
                    if (a.width, a.height) != (b.width, b.height) or bytes(a.buffer) != bytes(b.buffer):
                        raise PdfVisibilityError("Font recovery changed page painting")
    with pymupdf.open(original) as left, pymupdf.open(staged) as right:
        if left.page_count != right.page_count:
            raise PdfVisibilityError("Font recovery changed page painting")
        for one, two in zip(left, right):
            a = one.get_pixmap(dpi=144, alpha=False, annots=True, colorspace=pymupdf.csRGB)
            b = two.get_pixmap(dpi=144, alpha=False, annots=True, colorspace=pymupdf.csRGB)
            if (a.width, a.height) != (b.width, b.height) or a.samples != b.samples:
                raise PdfVisibilityError("Font recovery changed page painting")
    return {"pdfium": "identical", "mupdf": "identical"}


def recover_font_mappings(source: Path, stage: Path, references) -> tuple[Path, dict[tuple[bytes, int], str], dict]:
    """Stage authenticated ToUnicode maps; return (working PDF, {(program sha256, gid): text}, diagnostics)."""
    fonts: list[dict] = []
    unsupported: list[dict] = []
    bindings: dict[tuple[bytes, int], str] = {}
    staged = stage / "font-recovered.pdf"
    with pikepdf.open(source) as pdf:
        used, direct = _collect_used(pdf)
        for font in direct:
            entry = {"object": None, "reason": "font is a direct dictionary"}
            if entry not in unsupported:
                unsupported.append(entry)
        for objgen in sorted(used):
            font, strings = used[objgen]
            label = _object_label(font)
            supported = _font_program(font)
            if isinstance(supported, str):
                unsupported.append({"object": label, "reason": supported})
                continue
            if any(len(value) % 2 for value in strings):
                unsupported.append({"object": label, "reason": "odd-length Identity-H string"})
                continue
            program, table = supported
            codes = sorted({int.from_bytes(value[i:i + 2], "big")
                            for value in strings for i in range(0, len(value), 2)})
            if not codes:
                continue
            gids = {code: _gid(table, code) for code in codes}
            evidence = authenticate_program(program, sorted(set(gids.values())), references)
            rewritten = evidence.status != "rejected"
            fonts.append({
                "object": label,
                "programSHA256": hashlib.sha256(program).hexdigest(),
                "evidence": evidence.status,
                "reason": evidence.reason,
                "references": list(evidence.references),
                "rewritten": rewritten,
                "glyphs": [{"gid": glyph.gid, "status": glyph.status, "text": glyph.text, "method": glyph.method}
                           for _, glyph in sorted(evidence.glyphs.items())],
            })
            if not rewritten:
                continue
            entries = []
            for code in codes:
                glyph = evidence.glyphs.get(gids[code])
                bound = glyph is not None and glyph.status == "bound" and glyph.text is not None
                entries.append((code, glyph.text if bound else _REPLACEMENT))
                if bound:
                    bindings[(evidence.digest, glyph.gid)] = glyph.text
            font.ToUnicode = _tounicode(pdf, entries)
        diagnostics = {"status": "unchanged", "fonts": fonts, "unsupported": unsupported, "renderCheck": None}
        if not any(entry["rewritten"] for entry in fonts):
            return source, {}, diagnostics
        pdf.save(staged)
    diagnostics["renderCheck"] = _check_painting(source, staged)
    diagnostics["status"] = "recovered"
    return staged, bindings, diagnostics
