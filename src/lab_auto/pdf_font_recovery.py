"""Replace untrusted ToUnicode maps with bindings authenticated against trusted font programs.

The source PDF is never modified: rewritten maps go to a staged copy whose painting must
match the original byte-for-byte in both PDFium and MuPDF.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from contextlib import closing
from pathlib import Path

import pikepdf
import pymupdf
import pypdfium2 as pdfium

from fontTools.ttLib import TTFont

from lab_auto.pdf_glyph_evidence import GlyphEvidence, authenticate_program, homoglyph_scripts
from lab_auto.pdf_markdown import PdfVisibilityError

_MAX_FORM_DEPTH = 64
_SHOW_OPERATORS = {"Tj", "TJ", "'", '"'}
_BFCHAR_BLOCK = 100
_REPLACEMENT = "\ufffd"


def _homoglyphs(glyph) -> dict[str, str] | None:
    """Script -> letter for an ambiguous glyph whose candidates (all byte-identical trusted
    outlines) form one Latin/Cyrillic/Greek confusable class; None otherwise."""
    if glyph is None or glyph.status != "ambiguous":
        return None
    return homoglyph_scripts(glyph.candidates)


def _empty_space(glyph) -> bool:
    """An empty outline whose trusted twins (same metrics, also empty) are only whitespace.

    _decide keeps such renumbered-subset slots "unknown" because nothing proves which
    character the slot held. Publishing them as a plain space is still safe: the glyph paints
    no ink, so it can neither hide nor fake visible text, and pdf_native's whitespace check
    still refuses any space mapping whose glyph paints. Without it subsets glue words together.
    """
    # DejaVu Mono's empty slot is also the reference's U+FEFF/U+FFF9-FFFB (format controls)
    # and U+FFFC (object replacement); none of these paints anything either.
    return (glyph is not None and glyph.status == "unknown" and glyph.method == "signature"
            and " " in glyph.candidates
            and all(text.isspace() or unicodedata.category(text) == "Cf" or text == "\ufffc"
                    for text in glyph.candidates))


def _claims(font, width: int) -> dict[int, str]:
    """Single-scalar code -> text claims of the font's own (untrusted) ToUnicode CMap.

    Only consulted to choose among outline-proven homoglyph candidates of an all-ambiguous
    word (pdf_native._resolve_homoglyphs); unparsed or multi-scalar entries yield no claim,
    which makes that rule refuse.
    """
    stream = font.get("/ToUnicode")
    if not isinstance(stream, pikepdf.Stream):
        return {}
    try:
        data = stream.read_bytes().decode("latin-1")
    except pikepdf.PdfError:
        return {}
    claims: dict[int, str] = {}

    def text(hexa: str) -> str | None:
        try:
            value = bytes.fromhex(hexa).decode("utf-16-be")
        except ValueError:
            return None
        return value if len(value) == 1 else None

    for block in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            if len(source) == 2 * width and (value := text(target)) is not None:
                claims[int(source, 16)] = value
    for block in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for low, high, target in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            if len(low) != 2 * width or len(high) != 2 * width:
                continue
            first, last = int(low, 16), int(high, 16)
            if not 0 <= last - first < 0x10000:
                continue
            if target.startswith("["):
                for offset, item in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", target)[:last - first + 1]):
                    if (value := text(item)) is not None:
                        claims[first + offset] = value
            elif (value := text(target[1:-1])) is not None and len(value.encode("utf-16-be")) == 2:
                for offset in range(last - first + 1):
                    claims[first + offset] = chr(ord(value) + offset)
    return claims


def _program_identity(program: bytes) -> dict[str, str | None]:
    """Full name (name ID 4) and version (name ID 5) of an embedded program, for reporting only.

    These strings are untrusted and never authenticate anything; they only tell the user which
    licensed reference font to place into ~/.local/share/lab-auto/trusted-fonts.
    """
    try:
        parsed = TTFont(io.BytesIO(program), lazy=True, checkChecksums=0)
        try:
            table = parsed["name"] if "name" in parsed else None
            names = {key: table.getDebugName(key) if table else None for key in (4, 5)}
        finally:
            parsed.close()
    except Exception:  # hostile program data must never escape as an exception
        names = {4: None, 5: None}
    clean = lambda value: re.sub(r"[^\w .,;:()+-]", "?", value)[:80] if value else None  # noqa: E731
    return {"fontName": clean(names[4]), "fontVersion": clean(names[5])}



def _object_label(obj) -> str | None:
    number, generation = obj.objgen
    return f"{number} {generation}" if number else None


def _simple_truetype(font, descriptor):
    """Symbolic simple TrueType without /Encoding (LibreOffice subsets): code -> gid via the
    program's only cmap, a (1,0) table, which PDFium and MuPDF both use for such fonts.

    The derived map only selects which gids get authenticated and which ToUnicode codes are
    written; published glyphs are still keyed by the gid MuPDF actually painted, and any
    disagreement with PDFium's recovered text fails closed in pdf_native.
    """
    if "/Encoding" in font:
        return "simple TrueType font has an /Encoding"
    if not int(descriptor.get("/Flags", 0)) & 4:
        return "simple TrueType font is not symbolic"
    program = descriptor.get("/FontFile2")
    if not isinstance(program, pikepdf.Stream) or "/FontFile" in descriptor or "/FontFile3" in descriptor:
        return "font program is not an embedded FontFile2"
    try:
        data = program.read_bytes()
    except pikepdf.PdfError:
        return "font program cannot be decoded"
    try:
        parsed = TTFont(io.BytesIO(data), lazy=True, checkChecksums=0)
        try:
            tables = parsed["cmap"].tables
            if len(tables) != 1 or (tables[0].platformID, tables[0].platEncID) != (1, 0):
                return "simple TrueType cmap is not a single (1,0) table"
            mapping = {code: parsed.getGlyphID(name) for code, name in tables[0].cmap.items() if code < 256}
        finally:
            parsed.close()
    except Exception as exc:  # hostile program data must never escape as an exception
        return f"unparseable simple TrueType cmap: {type(exc).__name__}"
    return data, mapping, 1


def _font_program(font):
    """Return (program bytes, code->gid map, code width) for a supported font, else a reason string.

    The map is CIDToGIDMap bytes or None (identity) for Type0, a dict for simple TrueType.
    """
    if not font.is_indirect:
        return "font is a direct dictionary"
    if font.get("/Subtype") == pikepdf.Name.TrueType:
        descriptor = font.get("/FontDescriptor")
        if not isinstance(descriptor, pikepdf.Dictionary):
            return "font has no font descriptor"
        return _simple_truetype(font, descriptor)
    if font.get("/Subtype") != pikepdf.Name.Type0:
        return "font is neither Type0 nor TrueType"
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
    return data, table, 2


def _gid(table, cid: int) -> int:
    if table is None:
        return cid
    if isinstance(table, dict):
        return table.get(cid, 0)
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


def _tounicode(pdf: pikepdf.Pdf, entries: list[tuple[int, str]], width: int) -> pikepdf.Stream:
    blocks = []
    digits = 2 * width
    for start in range(0, len(entries), _BFCHAR_BLOCK):
        chunk = entries[start:start + _BFCHAR_BLOCK]
        lines = "".join(f"<{code:0{digits}X}> <{text.encode('utf-16-be').hex().upper()}>\n" for code, text in chunk)
        blocks.append(f"{len(chunk)} beginbfchar\n{lines}endbfchar\n")
    space = f"<{'00' * width}> <{'FF' * width}>".encode("ascii")
    return pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def\n"
        b"1 begincodespacerange " + space + b" endcodespacerange\n"
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
            program, table, width = supported
            if any(len(value) % width for value in strings):
                unsupported.append({"object": label, "reason": "odd-length Identity-H string"})
                continue
            codes = sorted({int.from_bytes(value[i:i + width], "big")
                            for value in strings for i in range(0, len(value), width)})
            if not codes:
                continue
            gids = {code: _gid(table, code) for code in codes}
            evidence = authenticate_program(program, sorted(set(gids.values())), references)
            rewritten = evidence.status != "rejected"
            # Untrusted ToUnicode claims per gid; a gid reached by codes claiming different text has none.
            claimed: dict[int, set[str | None]] = {}
            original_claims = _claims(font, width)
            for code in codes:
                claimed.setdefault(gids[code], set()).add(original_claims.get(code))

            def glyph_entry(glyph):
                entry = {"gid": glyph.gid, "status": glyph.status, "text": glyph.text, "method": glyph.method}
                classes = _homoglyphs(glyph)
                if classes:
                    claims = claimed.get(glyph.gid, set())
                    entry["homoglyphs"] = classes
                    entry["claim"] = next(iter(claims)) if len(claims) == 1 else None
                return entry

            fonts.append({
                "object": label,
                "programSHA256": hashlib.sha256(program).hexdigest(),
                **_program_identity(program),
                "evidence": evidence.status,
                "reason": evidence.reason,
                "references": list(evidence.references),
                "rewritten": rewritten,
                "glyphs": [glyph_entry(glyph) for _, glyph in sorted(evidence.glyphs.items())],
            })
            if not rewritten:
                continue
            entries = []
            for code in codes:
                glyph = evidence.glyphs.get(gids[code])
                bound = glyph is not None and glyph.status == "bound" and glyph.text is not None
                if _empty_space(glyph):
                    bound, glyph = True, GlyphEvidence(glyph.gid, "bound", " ", glyph.method, glyph.candidates)
                classes = _homoglyphs(glyph)
                # A confusable outline gets a fixed placeholder (its lowest candidate) so both
                # renderers expose the painted glyph; pdf_native replaces it per word or refuses.
                entries.append((code, glyph.text if bound else min(classes.values()) if classes else _REPLACEMENT))
                if bound:
                    bindings[(evidence.digest, glyph.gid)] = glyph.text
            font.ToUnicode = _tounicode(pdf, entries, width)
        diagnostics = {"status": "unchanged", "fonts": fonts, "unsupported": unsupported, "renderCheck": None}
        if not any(entry["rewritten"] for entry in fonts):
            return source, {}, diagnostics
        pdf.save(staged)
    diagnostics["renderCheck"] = _check_painting(source, staged)
    diagnostics["status"] = "recovered"
    return staged, bindings, diagnostics
