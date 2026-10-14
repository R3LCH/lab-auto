"""Always-on painted-glyph visibility, with opt-in independent Unicode authentication."""
from __future__ import annotations

import hashlib
import io
import ctypes
import re
from contextlib import closing
import pypdfium2 as pdfium
import math
from functools import lru_cache
from pathlib import Path

import pikepdf
import pymupdf
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw
from lab_auto.pdf_font_recovery import recover_font_mappings
from lab_auto.pdf_glyph_evidence import letter_script

_TYPE3_REASON = "Type3 glyphs require visual review; no verifiable character mapping"

# Trusted reference programs: distribution font packages plus a user-populated directory
# for fonts copied from a licensed Windows/Office install (never shipped with lab-auto).
_TRUSTED_ROOTS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/truetype/libreoffice"),
    Path("/usr/share/fonts/truetype/msttcorefonts"),
    Path.home() / ".local/share/fonts/msttcorefonts",
    Path.home() / ".local/share/lab-auto/trusted-fonts",
)


@lru_cache(maxsize=1)
def _references():
    fonts = []
    for root in _TRUSTED_ROOTS:
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if path.suffix.lower() == ".ttf" and path.is_file():
                    # In-memory copies: hundreds of references must not hold open file handles.
                    data = path.read_bytes()
                    fonts.append((hashlib.sha256(data).digest(), TTFont(io.BytesIO(data), lazy=True)))
    return fonts




def _suppress_text(source, destination) -> dict[int, str]:
    """Change painting modes, including nested Forms; never modify the source snapshot.

    Returns {page index: refusal}. A refused page, or one reusing a refused Form, is left
    partly unsuppressed and must not be analyzed; other pages are unaffected.
    """
    with pikepdf.open(source) as pdf:
        seen, failed, refused = set(), {}, {}

        def rewrite(stream, resources, depth=0):
            if depth > 64:
                raise PdfVisibilityError("text suppression: Form nesting exceeds limit")
            key = stream.obj.objgen if isinstance(stream, pikepdf.Page) else stream.objgen
            if key in failed:
                raise PdfVisibilityError(failed[key])
            if key != (0, 0):
                if key in seen:
                    return
                seen.add(key)
            try:
                instructions = []
                mode, stack = 0, []
                for operands, operator in pikepdf.parse_content_stream(stream):
                    op = str(operator)
                    if op == "q":
                        stack.append(mode)
                    elif op == "Q":
                        mode = stack.pop() if stack else 0
                    elif op == "Tr":
                        mode = int(operands[0])
                        if mode >= 4:
                            raise PdfVisibilityError("text suppression: text clipping modes are unsupported")
                    if op in {"Tj", "TJ", "'", '"'}:
                        instructions.append(([3], pikepdf.Operator("Tr")))
                        instructions.append((operands, operator))
                        instructions.append(([mode], pikepdf.Operator("Tr")))
                    else:
                        instructions.append((operands, operator))
                    if op == "Do":
                        objects = resources.get("/XObject", {})
                        child = objects.get(str(operands[0]))
                        if child is not None and str(child.get("/Subtype")) == "/Form":
                            rewrite(child, child.get("/Resources", resources), depth + 1)
                data = pikepdf.unparse_content_stream(instructions)
                if isinstance(stream, pikepdf.Page):
                    stream.Contents = pdf.make_stream(data)
                else:
                    stream.write(data)
            except PdfVisibilityError as exc:
                if key != (0, 0):
                    failed[key] = str(exc)
                raise

        for index, page in enumerate(pdf.pages):
            try:
                rewrite(page, page.Resources)
            except PdfVisibilityError as exc:
                refused[index] = str(exc)
        # Appearance text is not part of page texttrace. It remains in the baseline so
        # mixed-content verification cannot silently discard it.
        pdf.save(destination)
    return refused


def _actual_programs(page):
    """Index normalized single-glyph objects, including spaces omitted by text extraction."""
    origins, programs = {}, {}
    x0, _, _, y1 = page.get_cropbox()
    stack = [(pdfium.raw.FPDFPage_GetObject(page.raw, index), pymupdf.Matrix(1, 0, 0, 1, 0, 0), 0)
             for index in range(pdfium.raw.FPDFPage_CountObjects(page.raw))]
    while stack:
        obj, parent, depth = stack.pop()
        if depth > 64:
            raise PdfVisibilityError("PDF forms are nested too deeply")
        matrix = pdfium.raw.FS_MATRIX()
        if not pdfium.raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix)):
            raise PdfVisibilityError("Cannot locate native glyph object")
        transform = pymupdf.Matrix(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f) * parent
        kind = pdfium.raw.FPDFPageObj_GetType(obj)
        if kind == pdfium.raw.FPDF_PAGEOBJ_FORM:
            stack.extend((pdfium.raw.FPDFFormObj_GetObject(obj, index), transform, depth + 1)
                         for index in range(pdfium.raw.FPDFFormObj_CountObjects(obj)))
            continue
        if kind != pdfium.raw.FPDF_PAGEOBJ_TEXT:
            continue
        font = pdfium.raw.FPDFTextObj_GetFont(obj)
        identity = ctypes.cast(font, ctypes.c_void_p).value
        if identity not in programs:
            digest = None
            length = ctypes.c_ulong()
            if font and pdfium.raw.FPDFFont_GetIsEmbedded(font) and \
                    pdfium.raw.FPDFFont_GetFontData(font, None, 0, ctypes.byref(length)) and length.value:
                data = (ctypes.c_ubyte * length.value)()
                if pdfium.raw.FPDFFont_GetFontData(font, data, length.value, ctypes.byref(length)):
                    digest = hashlib.sha256(bytes(data)).digest()
            programs[identity] = digest
        px, py = transform.e - x0, y1 - transform.f
        key = (round(px * 10), round(py * 10))
        origins.setdefault(key, []).append((px, py, programs[identity]))
    return origins


def _program_at(origins, origin):
    x, y = origin
    key = (round(x * 10), round(y * 10))
    candidates = {
        digest for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        for px, py, digest in origins.get((key[0] + dx, key[1] + dy), ())
        if abs(px - x) <= 0.02 and abs(py - y) <= 0.02
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


_TRUSTED_FONTS_HINT = "~/.local/share/lab-auto/trusted-fonts"
# Horizontal gap (in em of the span's font size) between two painted glyph boxes on one
# baseline that counts as a visible word break; a normal space is ~0.25 em, letters touch.
_WORD_GAP = 0.2


def _ink_extents(working: Path) -> dict[bytes, tuple[int, dict[int, tuple[int, int]]]]:
    """sha256 of each embedded TrueType program -> (unitsPerEm, {gid: (yMin, yMax)}).

    MuPDF's texttrace boxes span only the font ascender..descender, but outlines such as Times
    New Roman's '_' paint below the descender. The bounds come from the program that is actually
    painted, so the widened box is exactly where that glyph's ink can be; nothing else is trusted.
    """
    extents = {}
    with pikepdf.open(working) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Dictionary) or obj.get("/Type") != pikepdf.Name.FontDescriptor:
                continue
            program = obj.get("/FontFile2")
            if not isinstance(program, pikepdf.Stream):
                continue
            try:
                data = program.read_bytes()
                font = TTFont(io.BytesIO(data), lazy=True, checkChecksums=0)
                try:
                    glyf = font["glyf"]
                    bounds = {}
                    for gid, name in enumerate(font.getGlyphOrder()):
                        glyph = glyf[name]
                        if getattr(glyph, "numberOfContours", 0) and hasattr(glyph, "yMin"):
                            bounds[gid] = (glyph.yMin, glyph.yMax)
                    extents[hashlib.sha256(data).digest()] = (font["head"].unitsPerEm, bounds)
                finally:
                    font.close()
            except Exception:  # unparseable program: keep MuPDF's own box
                continue
    return extents


def _font_hint(fonts: dict, digest) -> str:
    """Name the embedded font and why it has no trusted evidence (untrusted name strings,
    sanitized in pdf_font_recovery; they never authenticate anything)."""
    font = fonts.get(digest)
    if font is None:
        return ""
    label = " ".join(filter(None, (font.get("fontName"), font.get("fontVersion")))) or "embedded font"
    if font["evidence"] == "rejected":
        return (f" (font {label}: {font['reason']}; no matching trusted reference — put a licensed copy of"
                f" this exact font version into {_TRUSTED_FONTS_HINT})")
    return f" (font {label})"


class _Homoglyph:
    """A painted glyph whose trusted outline is shared by one letter per Latin/Cyrillic/Greek
    script; str() is the fixed placeholder the staged ToUnicode exposes to both renderers."""

    def __init__(self, entry: dict):
        self.classes = entry["homoglyphs"]
        self.claim = entry.get("claim")
        self.placeholder = min(self.classes.values())
        self.resolved: str | None = None  # the word's letter, set by _resolve_homoglyphs

    def __str__(self) -> str:
        return self.placeholder


def _resolve_homoglyphs(items: list, page_number: int) -> tuple[str, dict[str, int]]:
    """Replace each _Homoglyph by one script's letter, per word, or refuse the page.

    A word is a maximal run of letters (no whitespace/punctuation). Its unambiguous letters
    must all share one script, which every ambiguous glyph must offer ("word-script"). A word
    of only ambiguous glyphs may follow the original ToUnicode only when every claim lies in
    that glyph's outline-proven candidate set and all claims name one script
    ("tounicode-in-set"). So the document can at most pick one script for a whole word that
    is visually indistinguishable; it can never mix scripts in a word or leave the set.
    """
    counts = {"word-script": 0, "tounicode-in-set": 0}
    output: list[str] = []
    word: list = []

    def flush():
        pending = [item for item in word if isinstance(item, _Homoglyph)]
        if pending:
            scripts = {letter_script(item) for item in word if not isinstance(item, _Homoglyph)}
            if scripts:
                script = scripts.pop() if len(scripts) == 1 else None
                if script is None or any(script not in item.classes for item in pending):
                    raise PdfVisibilityError(f"Page {page_number}: confusable glyph in a mixed-script word")
                rule = "word-script"
            else:
                claims = [item.claim for item in pending]
                if any(claim is None or claim not in item.classes.values() for claim, item in zip(claims, pending)):
                    raise PdfVisibilityError(f"Page {page_number}: confusable glyph has no proven script")
                named = {letter_script(claim) for claim in claims}
                if len(named) != 1:
                    raise PdfVisibilityError(f"Page {page_number}: confusable glyph has no proven script")
                script, rule = named.pop(), "tounicode-in-set"
            counts[rule] += len(pending)
            for item in pending:
                item.resolved = item.classes[script]
        output.extend(item.resolved if isinstance(item, _Homoglyph) else item for item in word)
        word.clear()

    for item in items:
        if isinstance(item, _Homoglyph) or (len(item) == 1 and item.isalpha()):
            word.append(item)
        else:
            flush()
            output.append(item)
    flush()
    return "".join(output), counts


def _refused(number: int, reason: str) -> dict:
    """Row of a page without publishable native evidence; the consumer adds the page prefix.
    A page that shows Type3 text is tagged for visual review instead of a hard refusal."""
    prefix = f"Page {number}: "
    row = {"text": "", "source": None, "glyphs": None, "characters": [],
           "verificationReason": reason[len(prefix):] if reason.startswith(prefix) else reason}
    if row["verificationReason"] == _TYPE3_REASON:
        row["reviewRequired"] = "type3"
    return row


def analyze(source: Path, dpi: int, stage: Path, *, verify_fonts: bool = False) -> tuple[list[dict], dict]:
    """Always prove glyph visibility; authenticate Unicode only when explicitly requested."""
    if verify_fonts:
        return _analyze_authenticated(source, dpi, stage)
    with pikepdf.open(source) as pdf:
        _normalize(pdf, reference=False, split=False)
        geometry = io.BytesIO()
        pdf.save(geometry)
        refused = _normalize(pdf, reference=False)
        normalized = io.BytesIO()
        pdf.save(normalized)
        for index, reason in _normalize(pdf, reference=True).items():
            refused.setdefault(index, reason)
        reference = io.BytesIO()
        pdf.save(reference)
    results = []
    with pdfium.PdfDocument(source) as original, \
            pdfium.PdfDocument(geometry.getvalue()) as geometry_document, \
            pdfium.PdfDocument(normalized.getvalue()) as document, \
            pdfium.PdfDocument(reference.getvalue()) as ref_document:
        for index in range(len(document)):
            number = index + 1
            if index in refused:
                results.append(_refused(number, refused[index]))
                continue
            characters, issues, counts = [], set(), {}
            with closing(original[index]) as before, closing(document[index]) as page, \
                    closing(geometry_document[index]) as geometry_page, closing(ref_document[index]) as ref_page:
                try:
                    _page_text(before, page, ref_page, number, recover=True,
                               characters=characters, issues=issues, counts=counts,
                               origin_reference=geometry_page)
                except PdfVisibilityError as exc:
                    results.append(_refused(number, str(exc)))
                    continue
            results.append({
                "text": _visible_text(characters), "source": "visible-native" if characters else "blank",
                "characters": characters, "verificationReason": "; ".join(sorted(issues)) or None,
                "glyphs": {"emitted": len(characters), "hidden": counts.get("hidden", 0),
                           "authenticated": 0, "recovered": 0,
                           "unresolved": sum(char["text"] == "\ufffd" for char in characters)},
            })
    return results, {"status": "not-run", "reason": "Font authentication is opt-in (--verify-fonts)"}


def _visible_text(characters: list[dict]) -> str:
    lines = []
    for char in sorted(characters, key=lambda item: (-item["size"], item["y"], item["x"])):
        line = next((line for line in lines
                     if abs(char["y"] - line[0]["y"]) <= max(char["size"], line[0]["size"]) * 0.5), None)
        if line is None:
            lines.append([char])
        else:
            line.append(char)
    output = []
    for line in sorted(lines, key=lambda line: line[0]["y"]):
        text, previous = [], None
        for char in sorted(line, key=lambda item: (item["x"], item["y"])):
            if (previous is not None and not previous["text"].isspace() and not char["text"].isspace()
                    and char["x"] - (previous["x"] + previous.get("advance", previous["bbox"][2] - previous["bbox"][0]))
                    > _WORD_GAP * char["size"]):
                gap = char["x"] - (previous["x"] + previous.get("advance", previous["bbox"][2] - previous["bbox"][0]))
                width = previous.get("spaceAdvance")
                amount = max(1, round(gap / width)) if width and gap <= char["size"] * 2.5 else 1
                text.append(" " * amount)
            text.append(char["text"])
            previous = char
        output.append("".join(text))
    return "\n".join(output).strip()


def _analyze_authenticated(source: Path, dpi: int, stage: Path) -> tuple[list[dict], dict]:
    """One row per page. A refused page gets an empty row carrying its exact refusal while
    its neighbours keep their own evidence. Only font recovery, which stages and render-checks
    every font of the document at once, refuses all pages together."""
    try:
        working, bindings, recovery = recover_font_mappings(source, stage, _references())
    except PdfVisibilityError as exc:
        with pikepdf.open(source) as pdf:
            count = len(pdf.pages)
        reason = f"font recovery refused the document: {exc}"
        return ([_refused(number, reason) for number in range(1, count + 1)],
                {"status": "refused", "reason": str(exc), "fonts": [], "unsupported": [], "renderCheck": None})
    # The split check compares char codes before/after splitting, so its "original" must carry
    # the same staged ToUnicode; recovery already proved the staged painting byte-identical.
    page_evidence = _recovered_visible(working, working)
    suppressed = stage / "without-text.pdf"
    unsuppressed = _suppress_text(working, suppressed)
    results = []
    trusted = {digest: font for digest, font in _references()}
    rewritten = {bytes.fromhex(font["programSHA256"]) for font in recovery["fonts"] if font["rewritten"]}
    fonts_by_digest = {bytes.fromhex(font["programSHA256"]): font for font in recovery["fonts"]}
    homoglyphs = {(bytes.fromhex(font["programSHA256"]), glyph["gid"]): glyph
                  for font in recovery["fonts"] if font["rewritten"]
                  for glyph in font["glyphs"] if "homoglyphs" in glyph}
    ink_extents = _ink_extents(working)
    with pymupdf.open(working) as document, pymupdf.open(suppressed) as baseline:
        def page_row(index, page, images):
            """Evidence of one page whose PDFium visibility and text suppression succeeded;
            opened images go to `images` so the caller closes them on refusal too."""
            number = index + 1
            visible_glyphs, origins = page_evidence[index]
            pixmap = baseline[index].get_pixmap(dpi=dpi, alpha=False, annots=True, colorspace=pymupdf.csRGB)
            background = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            with Image.open(stage / f"mupdf-{number:04}.png") as rendered:
                original = rendered.convert("RGB")
            delta = ImageChops.difference(original, background).convert("L")
            images.extend((background, original, delta))
            # Visible non-text additions require supplementary verification. Uniform
            # backgrounds are harmless; opaque covering shapes are handled by delta.
            background_ink = any(low != high for low, high in background.getextrema())
            emitted, hidden, authenticated, recovered = 0, 0, 0, 0
            lines: list = []
            characters = []
            last_y = None
            traces = page.get_texttrace()
            positions = {}
            duplicates = set()
            for span in traces:
                for code, gid, origin, _ in span["chars"]:
                    position = tuple(round(value, 3) for value in origin)
                    identity = (code, gid, span["font"], span["size"], span["type"])
                    if position in positions:
                        # LibreOffice Writer repeats the same space glyph at one origin. An exact
                        # duplicate of a whitespace glyph paints nothing new and still has to pass
                        # the whitespace binding below; any differing or inked duplicate refuses.
                        if positions[position] != identity or not chr(code).isspace():
                            raise PdfVisibilityError(f"Page {number}: coincident glyph identity is ambiguous")
                        duplicates.add(position)
                    positions[position] = identity
            emitted_duplicates = set()
            painted_glyphs = []  # (text, origin x, origin y) of visible non-space glyphs
            line: list = []  # (origin x, origin y, item, box x0, box x1, size) of the current line

            def flush_line():
                # Canonical order: painted origin x within the line, never either engine's text
                # order (PDFium and MuPDF order separately drawn sub/superscripts differently).
                line.sort(key=lambda record: (record[0], record[1]))
                painted = None  # (right edge, baseline) of the last visible non-space glyph
                for x, y, item, left, right, size in line:
                    if isinstance(item, _Homoglyph) or not item.isspace():
                        # A visible horizontal gap (table cells, tab stops) ends the word for
                        # homoglyph resolution even without a space glyph; "" emits nothing.
                        # Contiguously painted letters stay one word, so a spoof like "pаypal"
                        # cannot be split invisibly.
                        if (painted is not None and abs(y - painted[1]) <= 0.01
                                and left - painted[0] > _WORD_GAP * size):
                            lines.append("")
                        painted = (right, y)
                    lines.append(item)
                line.clear()

            for span in traces:
                direction = span["dir"]
                if abs(direction[0] - 1) > 0.001 or abs(direction[1]) > 0.001:
                    raise PdfVisibilityError(f"Page {number}: native evidence: unsupported text direction")
                records = []
                for code, gid, origin, bbox in span["chars"]:
                    if not 0 <= code <= 0x10ffff or 0xD800 <= code <= 0xDFFF:
                        raise PdfVisibilityError(f"Page {number}: native evidence: invalid Unicode")
                    char = chr(code)
                    rect = pymupdf.Rect(bbox) * page.rotation_matrix
                    digest = _program_at(origins, origin)
                    extent = ink_extents.get(digest)
                    if extent is not None and gid in extent[1] and span["size"] > 0 and not page.rotation:
                        # MuPDF normalizes ascender/descender so its box spans one em (span size,
                        # already scaled by the text matrix); extend that box to the painted
                        # outline's own yMin..yMax only (e.g. '_' below the descender).
                        em = span["size"]
                        low, high = extent[1][gid]
                        rect = pymupdf.Rect(rect.x0, min(rect.y0, origin[1] - high / extent[0] * em),
                                            rect.x1, max(rect.y1, origin[1] - low / extent[0] * em))
                    scale = dpi / 72
                    box = (max(0, math.floor(rect.x0 * scale)), max(0, math.floor(rect.y0 * scale)),
                           min(delta.width, math.ceil(rect.x1 * scale)), min(delta.height, math.ceil(rect.y1 * scale)))
                    visible = (span["type"] != 3 and span.get("opacity", 1) > 0
                               and box[0] < box[2] and box[1] < box[3]
                               and delta.crop(box).getextrema()[1] >= 32)
                    binding = bindings.get((digest, gid))
                    if binding is None and digest not in rewritten and digest in trusted:
                        reference = trusted[digest]
                        order = reference.getGlyphOrder()
                        if 0 <= gid < len(order) and reference.getBestCmap().get(code) == order[gid]:
                            binding = char
                    if char.isspace():
                        if binding != " ":
                            raise PdfVisibilityError(
                                f"Page {number}: unauthenticated whitespace glyph{_font_hint(fonts_by_digest, digest)}")
                        position = tuple(round(value, 3) for value in origin)
                        if position in duplicates:
                            if position in emitted_duplicates:
                                hidden += 1
                                continue
                            emitted_duplicates.add(position)
                        if span["type"] != 3 and span.get("opacity", 1) > 0:
                            records.append((origin[0], origin[1], " ", rect.x0, rect.x1, span["size"]))
                            characters.append({"text": " ", "x": origin[0], "y": origin[1],
                                               "bbox": list(bbox), "size": span["size"],
                                               "advance": bbox[2] - bbox[0]})
                            emitted += 1
                            authenticated += 1
                            recovered += int((digest, gid) in bindings)
                        else:
                            hidden += 1
                        continue
                    if not visible:
                        hidden += 1
                        continue
                    if ord(char) < 32 or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069":
                        raise PdfVisibilityError(f"Page {number}: native evidence: control/format character")
                    if binding is None and char in _SHAPE_SYMBOLS:
                        # No trusted outline exists: the painted shape itself must prove the symbol.
                        shaped = _symbol_by_shape(char, delta.crop(box).point(lambda value: 255 if value >= 128 else 0))
                        if shaped:
                            char = binding = shaped
                    item = char
                    entry = homoglyphs.get((digest, gid)) if binding is None else None
                    if entry is not None and char == min(entry["homoglyphs"].values()):
                        # Outline proven to be one of these same-looking letters; the word decides.
                        item = binding = _Homoglyph(entry)
                    elif binding != char:
                        raise PdfVisibilityError(f"Page {number}: no unique trusted binding for visible glyph {gid}"
                                                 f"{_font_hint(fonts_by_digest, digest)}")
                    authenticated += 1
                    recovered += int((digest, gid) in bindings or isinstance(item, _Homoglyph))
                    records.append((origin[0], origin[1], item, rect.x0, rect.x1, span["size"]))
                    characters.append({"text": item, "x": origin[0], "y": origin[1],
                                       "bbox": list(bbox), "size": span["size"],
                                       "advance": bbox[2] - bbox[0]})
                    painted_glyphs.append((str(item), origin[0], origin[1]))
                    emitted += 1
                if records:
                    y = span["chars"][0][2][1]
                    if last_y is not None and abs(y - last_y) > span["size"] * 0.4:
                        flush_line()
                        lines.append("\n")
                    line.extend(records)
                    last_y = y
            flush_line()
            # Both renderers saw the staged placeholder letters; compare before resolution.
            # Every visible glyph must pair one-to-one by text and painted origin, in any order.
            if not _same_glyphs(visible_glyphs, painted_glyphs):
                raise PdfVisibilityError(f"Page {number}: native visibility evidence disagrees between renderers")
            # Recovered PDFium checks determine which painted glyphs may be used;
            # texttrace retains their native line/code spacing without PDFium's
            # generated separators between split single-glyph text objects.
            native, resolved = _resolve_homoglyphs(lines, number)
            native = native.strip()
            for char in characters:
                if isinstance(char["text"], _Homoglyph):
                    char["text"] = char["text"].resolved
            reason = "mixed raster/vector composition" if background_ink else None
            return {"text": native, "nativePresent": bool(page.get_texttrace()),
                    "verificationReason": reason, "characters": characters,
                    "source": "native" if native else "blank",
                    "glyphs": {"emitted": emitted, "hidden": hidden,
                               "authenticated": authenticated, "recovered": recovered,
                               "homoglyphResolved": resolved}}

        for index, page in enumerate(document):
            reason = page_evidence[index] if isinstance(page_evidence[index], str) else unsuppressed.get(index)
            images: list = []
            try:
                results.append(_refused(index + 1, reason) if reason else page_row(index, page, images))
            except PdfVisibilityError as exc:
                results.append(_refused(index + 1, str(exc)))
            finally:
                for image in images:
                    image.close()
    suppressed.unlink()
    return results, recovery


# Recovered native normalization and per-glyph counterfactual visibility.
_SCALE = 3

from lab_auto.pdf_markdown import PdfVisibilityError

def _instruction(operator: str, operands=()):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def _codes(font, value: bytes) -> list[bytes]:
    if font is None:
        raise PdfVisibilityError("Text has no identifiable font")
    subtype = str(font.get("/Subtype", ""))
    if subtype == "/Type3":
        # A glyph procedure has no font program a trusted reference could authenticate.
        raise PdfVisibilityError(_TYPE3_REASON)
    if subtype in {"/Type1", "/MMType1", "/TrueType"}:
        return [value[i:i + 1] for i in range(len(value))]
    if subtype != "/Type0":
        raise PdfVisibilityError("Unsupported glyph font; inspect rendered pages")
    encoding = font.get("/Encoding")
    if str(encoding) in {"/Identity-H", "/Identity-V"}:
        if len(value) % 2:
            raise PdfVisibilityError("Invalid two-byte glyph encoding")
        return [value[i:i + 2] for i in range(0, len(value), 2)]
    if not isinstance(encoding, pikepdf.Stream):
        raise PdfVisibilityError("Unsupported predefined composite-font encoding")
    cmap = encoding.read_bytes()
    if b"usecmap" in cmap:
        raise PdfVisibilityError("Inherited font CMaps require visual inspection")
    ranges = []
    for block in re.findall(rb"begincodespacerange(.*?)endcodespacerange", cmap, re.S):
        for first, last in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            low, high = bytes.fromhex(first.decode()), bytes.fromhex(last.decode())
            if len(low) != len(high) or not 1 <= len(low) <= 4:
                raise PdfVisibilityError("Invalid font codespace")
            ranges.append((low, high))
    result = []
    offset = 0
    while offset < len(value):
        matches = {value[offset:offset + len(low)] for low, high in ranges
                   if len(value[offset:offset + len(low)]) == len(low)
                   and low <= value[offset:offset + len(low)] <= high}
        if len(matches) != 1:
            raise PdfVisibilityError("Ambiguous font character boundaries")
        code = matches.pop()
        result.append(code)
        offset += len(code)
    return result


def _normalize(pdf: pikepdf.Pdf, *, reference: bool, split: bool = True) -> dict[int, str]:
    """Remove replacement claims and optionally split text showing per glyph.

    Return per-page refusals; failed/shared Forms must never be analyzed partly rewritten.
    """
    # Remove replacement/accessibility strings, including named property dictionaries.
    for obj in pdf.objects:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) and "/ActualText" in obj:
            del obj["/ActualText"]
    visited = set()
    processing = set()
    failed: dict = {}

    def rewrite(container, resources):
        identity = container.objgen
        if identity in failed:
            raise PdfVisibilityError(failed[identity])
        if identity in processing:
            raise PdfVisibilityError("Recursive PDF form")
        if identity in visited:
            return
        processing.add(identity)
        font = None
        stack = []
        result = []
        for instruction in pikepdf.parse_content_stream(container):
            if isinstance(instruction, pikepdf.ContentStreamInlineImage):
                result.append(instruction)
                continue
            operands, operator = instruction.operands, str(instruction.operator)
            if operator == "q":
                stack.append(font)
            elif operator == "Q":
                if not stack:
                    raise PdfVisibilityError("Unbalanced PDF graphics state")
                font = stack.pop()
            elif operator == "Tf":
                font = resources.get("/Font", {}).get(operands[0])
            elif operator == "Tr" and int(operands[0]) >= 4:
                raise PdfVisibilityError("Text used as a clipping mask requires visual inspection")
            elif operator == "gs":
                state = resources.get("/ExtGState", {}).get(operands[0], {})
                if "/Font" in state:
                    raise PdfVisibilityError("Graphics-state font changes require visual inspection")
            elif operator == "Do":
                form = resources.get("/XObject", {}).get(operands[0])
                if form is not None and str(form.get("/Subtype")) == "/Form":
                    rewrite(form, form.get("/Resources", resources))
            if operator == "BDC":
                properties = operands[1]
                if isinstance(properties, pikepdf.Name):
                    properties = resources.get("/Properties", {}).get(properties)
                if isinstance(properties, pikepdf.Dictionary) and "/ActualText" in properties:
                    properties = pikepdf.Dictionary(properties)
                    del properties["/ActualText"]
                    instruction = _instruction("BDC", [operands[0], properties])
            if reference and operator in {"gs", "W", "W*", "BDC", "BMC", "EMC"}:
                continue
            if split and operator in {"Tj", "TJ", "'", '"'}:
                if operator == "'":
                    result.append(_instruction("T*"))
                elif operator == '"':
                    result.extend([_instruction("Tw", [operands[0]]),
                                   _instruction("Tc", [operands[1]]), _instruction("T*" )])
                values = operands[0] if operator == "TJ" else [operands[-1]]
                for value in values:
                    if isinstance(value, pikepdf.String):
                        result.extend(_instruction("Tj", [pikepdf.String(code)])
                                      for code in _codes(font, bytes(value)))
                    else:
                        result.append(_instruction("TJ", [pikepdf.Array([value])]))
            else:
                result.append(instruction)
        if stack:
            raise PdfVisibilityError("Unbalanced PDF graphics state")
        content = pikepdf.unparse_content_stream(result)
        if isinstance(container, pikepdf.Page):
            container.Contents = pdf.make_stream(content)
            if reference and "/Annots" in container.obj:
                del container.obj["/Annots"]
        else:
            container.write(content)
        processing.remove(identity)
        visited.add(identity)

    refused = {}
    for index, page in enumerate(pdf.pages):
        resources = page.obj
        while "/Resources" not in resources and "/Parent" in resources:
            resources = resources.Parent
        try:
            rewrite(page, resources.get("/Resources", {}))
        except PdfVisibilityError as exc:
            # The containers still being rewritten are exactly the refused chain.
            failed.update(dict.fromkeys(processing, str(exc)))
            processing.clear()
            refused[index] = str(exc)
    return refused


def _render(page, size: tuple[int, int], region: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = region
    with closing(pdfium.PdfBitmap.new_native(right - left, bottom - top, format=pdfium.raw.FPDFBitmap_BGR)) as bitmap:
        bitmap.fill_rect((255, 255, 255, 255), 0, 0, right - left, bottom - top)
        pdfium.raw.FPDF_RenderPageBitmap(bitmap.raw, page.raw, -left, -top, *size, 0, pdfium.raw.FPDF_ANNOT)
        return bitmap.to_pil().convert("RGB")


def _intensity(image: Image.Image) -> Image.Image:
    red, green, blue = image.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _region(page, box, size):
    points = []
    for x in (box[0], box[2]):
        for y in (box[1], box[3]):
            px, py = ctypes.c_int(), ctypes.c_int()
            if not pdfium.raw.FPDF_PageToDevice(page.raw, 0, 0, *size, 0, x, y, px, py):
                raise PdfVisibilityError("Cannot locate glyph on rendered page")
            points.append((px.value, py.value))
    return (max(0, min(x for x, y in points) - 2), max(0, min(y for x, y in points) - 2),
            min(size[0], max(x for x, y in points) + 2), min(size[1], max(y for x, y in points) + 2))


def _set_active(obj, active):
    if not pdfium.raw.FPDFPageObj_SetIsActive(obj, active):
        raise PdfVisibilityError("Renderer cannot verify glyph visibility")


# Word symbol-font private-use codes with no trusted outline available: emitted only when the
# painted ink has the expected shape, so another font's glyph at the same code still refuses
# (Symbol 0xA7 is a club, Wingdings 0xB7 a clock face).
_SHAPE_SYMBOLS = {"\uf0b7": ("disc", "\u2022"), "\uf0a7": ("square", "\u25aa"), "\uf02d": ("bar", "\u2212")}
_MIN_SHAPE_IOU = 0.85


def _symbol_by_shape(char: str, mask) -> str | None:
    """Map a symbol PUA code to Unicode only if its binarized ink matches the filled shape."""
    entry, box = _SHAPE_SYMBOLS.get(char), mask.getbbox()
    if entry is None or not box:
        return None
    kind, text = entry
    glyph = mask.crop(box)
    width, height = glyph.size
    if kind == "bar":
        if width < 3 * height:
            return None
    elif min(width, height) < 4 or not 0.8 <= width / height <= 1.25:
        return None
    template = Image.new("L", glyph.size, 0 if kind == "disc" else 255)
    if kind == "disc":
        ImageDraw.Draw(template).ellipse((0, 0, width - 1, height - 1), fill=255)
    inter = ImageChops.multiply(glyph, template).histogram()[255]
    union = ImageChops.lighter(glyph, template).histogram()[255]
    return text if union and inter / union >= _MIN_SHAPE_IOU else None


# Horizontal bars carry no shape beyond the visible span; Word form-fill lines overlap each
# underscore with its neighbour by exactly half (measured 0.5 on real task and site PDFs).
_BAR_GLYPHS = frozenset("_‾")
_MIN_BAR_COVERAGE = 0.5


# Cell clips and adjacent opaque fills may shave the outermost device rows of a descender
# or ascender; real Word/site reports lose at most 2 rows (< 10% of the glyph's ink).
_EDGE_BAND_ROWS = 2
_MAX_EDGE_BAND_SHARE = 0.1


def _edge_band_only(expected, bare_mask, bare: int, total: int) -> bool:
    """True when every bare-background gap pixel sits in the top or bottom edge band of the
    glyph's expected ink and the gap is a small share of it; any interior gap refuses."""
    ink_box, gap_box = expected.getbbox(), bare_mask.getbbox()
    if not ink_box or not gap_box or bare / total > _MAX_EDGE_BAND_SHARE:
        return False
    top, bottom = ink_box[1] + _EDGE_BAND_ROWS, ink_box[3] - _EDGE_BAND_ROWS
    return gap_box[3] <= top or gap_box[1] >= bottom


# Splitting may move a glyph's float origin by ~1e-4 pt; 0.01 pt is far below PDFium's
# smallest paintable repositioning (1/3 device pixel = 0.111 pt at _SCALE 3).
_SPLIT_ORIGIN_TOLERANCE = 0.01
# Lowest share of a glyph's ink that must still change the page when other dark ink lies
# under the rest; real Word table/underline overlaps measured >= 0.652.
_MIN_INKED_COVERAGE = 0.6


def _char_origins(page):
    """Non-generated, non-space glyphs as (code, x, y, box); the original keeps standalone
    space characters that a split page folds into its glyph objects."""
    rows = []
    with closing(page.get_textpage()) as textpage:
        for index in range(textpage.count_chars()):
            if pdfium.raw.FPDFText_IsGenerated(textpage.raw, index):
                continue
            code = pdfium.raw.FPDFText_GetUnicode(textpage.raw, index)
            if code == 2 and pdfium.raw.FPDFText_IsHyphen(textpage.raw, index) == 1:
                code = ord("-")
            if code and chr(code).isspace():
                continue
            x, y = ctypes.c_double(), ctypes.c_double()
            if not pdfium.raw.FPDFText_GetCharOrigin(textpage.raw, index, ctypes.byref(x), ctypes.byref(y)):
                raise PdfVisibilityError("Cannot locate glyph origin")
            rows.append((code, x.value, y.value, textpage.get_charbox(index)))
    return rows


def _check_split_noise(original, page, size, difference, page_number):
    failure = PdfVisibilityError(f"Page {page_number}: glyph splitting changed rendering")
    if difference.getextrema()[1] >= 128:
        raise failure
    before, after = _char_origins(original), _char_origins(page)
    if len(before) != len(after):
        raise failure
    # One-to-one match by code and origin; buckets are 0.1 pt, neighbours cover the tolerance.
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for index, (code, x, y, _) in enumerate(before):
        buckets.setdefault((code, round(x * 10), round(y * 10)), []).append(index)
    used = set()
    mask = Image.new("L", difference.size, 0)
    for code, x, y, box in after:
        key = (round(x * 10), round(y * 10))
        match = next((index for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      for index in buckets.get((code, key[0] + dx, key[1] + dy), ())
                      if index not in used
                      and abs(before[index][1] - x) <= _SPLIT_ORIGIN_TOLERANCE
                      and abs(before[index][2] - y) <= _SPLIT_ORIGIN_TOLERANCE), None)
        if match is None:
            raise failure
        used.add(match)
        for glyph_box in (box, before[match][3]):
            region = _region(page, glyph_box, size)
            if region[0] < region[2] and region[1] < region[3]:
                mask.paste(255, region)
    changed = difference.point(lambda value: 255 if value > 8 else 0)
    if ImageChops.subtract(changed, mask).getextrema()[1]:
        raise failure


def _page_text(original, page, reference, page_number: int, *, recover: bool = False,
               characters: list | None = None, issues: set | None = None, counts: dict | None = None,
               origin_reference=None) -> list[tuple[str, float, float]]:
    """Prove painted glyphs; recover only uncertain visible ink.

    Pixel preservation uses the untouched PDF. Origin matching uses a claim-free, unsplit
    view because /ActualText can invent Unicode, coordinates and boxes without painting ink.
    """
    size = tuple(math.ceil(length * _SCALE) for length in page.get_size())
    whole = (0, 0, *size)
    baseline = _render(page, size, whole)
    source = _render(original, size, whole)
    # Splitting encoded strings must not change their painted geometry. PDFium re-derives
    # each split glyph's device origin in float, so glyphs may repaint with sub-pixel noise;
    # that is accepted only when every glyph origin is unchanged and the noise stays on glyphs.
    difference = _intensity(ImageChops.difference(baseline, source))
    if difference.getextrema()[1] > 8:
        _check_split_noise(origin_reference if origin_reference is not None else original,
                           page, size, difference, page_number)
    with closing(page.get_textpage()) as textpage, closing(reference.get_textpage()) as ref_text:
        if textpage.count_chars() != ref_text.count_chars():
            raise PdfVisibilityError(f"Page {page_number}: reference glyph mapping changed")
        for obj in reference.get_objects(max_depth=64):
            _set_active(obj.raw, obj.type == pdfium.raw.FPDF_PAGEOBJ_FORM)
            if obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
                pdfium.raw.FPDFPageObj_SetFillColor(obj.raw, 0, 0, 0, 255)
                pdfium.raw.FPDFPageObj_SetStrokeColor(obj.raw, 0, 0, 0, 255)
                pdfium.raw.FPDFPageObj_SetBlendMode(obj.raw, b"Normal")
                mode = pdfium.raw.FPDFTextObj_GetTextRenderMode(obj.raw)
                pdfium.raw.FPDFTextObj_SetTextRenderMode(obj.raw, 0 if mode == 3 else mode)
        glyphs = {}
        spacing = {}
        ambiguous_bounds = set()
        for index in range(textpage.count_chars()):
            code = pdfium.raw.FPDFText_GetUnicode(textpage.raw, index)
            # Only PDFium's explicitly flagged contextual marker is a visible hyphen.
            # A genuine U+0002 mapping remains an uncertain/control character.
            hyphen = code == 2 and pdfium.raw.FPDFText_IsHyphen(textpage.raw, index) == 1
            char = ("-" if hyphen else chr(code)
                    if 0 < code <= 0x10ffff and not 0xD800 <= code <= 0xDFFF else "\ufffd")
            obj = pdfium.raw.FPDFText_GetTextObject(textpage.raw, index)
            if pdfium.raw.FPDFText_IsGenerated(textpage.raw, index) or not obj:
                if not recover and not char.isspace():
                    raise PdfVisibilityError(f"Page {page_number}: text not tied to a painted glyph")
                continue
            box = textpage.get_charbox(index)
            key = (char, tuple(round(value, 3) for value in box))
            glyphs.setdefault(key, []).append((index, obj))
        # PDFium suppresses coincident duplicate characters in its text index.
        # Include all matching paint objects in the counterfactual, not only the indexed one.
        for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_TEXT], max_depth=64):
            bounds = tuple(round(value, 3) for value in obj.get_bounds())
            def object_text(text):
                length = pdfium.raw.FPDFTextObj_GetText(obj.raw, text.raw, None, 0)
                if length < 2:
                    return ""
                buffer = (ctypes.c_ushort * (length // 2))()
                pdfium.raw.FPDFTextObj_GetText(obj.raw, text.raw, buffer, length)
                return bytes(buffer).decode("utf-16-le").rstrip("\x00").strip()

            char = object_text(textpage)
            if not char:
                coincident = [member for key, group in glyphs.items() if key[1] == bounds for member in group]
                if not coincident:
                    continue
                try:
                    for index, other in coincident:
                        _set_active(other, False)
                    with closing(page.get_textpage()) as isolated_text:
                        char = object_text(isolated_text)
                finally:
                    for index, other in coincident:
                        _set_active(other, True)
                if char and (char, bounds) not in glyphs:
                    if not recover:
                        raise PdfVisibilityError(f"Page {page_number}: coincident glyphs have different text mappings")
                    ambiguous_bounds.add(bounds)
                    char = next(key[0] for key in glyphs if key[1] == bounds)
            key = (char, bounds)
            members = glyphs.get(key)
            if members and all(ctypes.cast(other, ctypes.c_void_p).value !=
                               ctypes.cast(obj.raw, ctypes.c_void_p).value for index, other in members):
                members.append((members[0][0], obj.raw))
        seen_objects = set()
        visible = {}
        for key, members in glyphs.items():
            char, box = key
            if char.isspace() and not recover:
                ref_obj = pdfium.raw.FPDFText_GetTextObject(ref_text.raw, members[0][0])
                if not ref_obj:
                    raise PdfVisibilityError(f"Page {page_number}: whitespace reference glyph missing")
                try:
                    _set_active(ref_obj, True)
                    whitespace_ink = _intensity(ImageChops.invert(_render(reference, size, whole)))
                finally:
                    _set_active(ref_obj, False)
                if whitespace_ink.getextrema()[1] > 8:
                    raise PdfVisibilityError(f"Page {page_number}: whitespace mapping hides painted glyph")
                visible[key] = char
                continue
            duplicate = False
            for index, obj in members:
                address = ctypes.cast(obj, ctypes.c_void_p).value
                if address in seen_objects:
                    if not recover:
                        raise PdfVisibilityError(f"Page {page_number}: multiple characters mapped to one glyph")
                    duplicate = True
                seen_objects.add(address)
            if duplicate:
                visible[key] = ""
                issues.add("multiple characters share one glyph")
                continue
            region = _region(page, box, size)
            if region[2] <= region[0] or region[3] <= region[1]:
                visible[key] = ""
                continue
            try:
                for index, obj in members:
                    _set_active(obj, False)
                without = _render(page, size, region)
            finally:
                for index, obj in members:
                    _set_active(obj, True)
            # Same device offset on both sides: PDFium's edge anti-aliasing depends on it.
            contribution = _intensity(ImageChops.difference(_render(page, size, region), without))
            if contribution.getextrema()[1] < (8 if recover else 1):
                visible[key] = ""
                continue
            uncertain = (char == "\ufffd" or char.isspace() or box in ambiguous_bounds
                         or not (char.isprintable() or char in _SHAPE_SYMBOLS) or any(
                             pdfium.raw.FPDFText_HasUnicodeMapError(textpage.raw, index) for index, obj in members))
            if uncertain and not recover:
                raise PdfVisibilityError(f"Page {page_number}: visible glyph has no reliable Unicode mapping")
            if uncertain:
                issues.add("visible glyph has an uncertain Unicode mapping")
            ref_obj = pdfium.raw.FPDFText_GetTextObject(ref_text.raw, members[0][0])
            if not ref_obj:
                raise PdfVisibilityError(f"Page {page_number}: reference glyph is missing")
            try:
                _set_active(ref_obj, True)
                ink = _intensity(ImageChops.invert(_render(reference, size, region)))
            finally:
                _set_active(ref_obj, False)
            expected = ink.point(lambda value: 255 if value >= 128 else 0)
            actual = contribution.point(lambda value: 255 if value >= 8 else 0)
            total = expected.histogram()[255]
            covered = ImageChops.multiply(expected, actual).histogram()[255]
            if not total:
                if not recover:
                    raise PdfVisibilityError(f"Page {page_number}: partially hidden or low-contrast glyph; inspect rendered page")
                uncertain = True
                issues.add("visible glyph has uncertain ink coverage")
            shown = "\ufffd" if uncertain else char
            if char in _SHAPE_SYMBOLS:
                shaped = _symbol_by_shape(char, expected)
                if shaped is None:
                    if not recover:
                        raise PdfVisibilityError(f"Page {page_number}: symbol glyph shape does not match its code")
                    shown = "\ufffd"
                    issues.add("visible symbol has an uncertain mapping")
                else:
                    shown = shaped
            if total and covered / total < 0.98:
                # Bare-background gaps mean covered/clipped ink, not a publishable full letter.
                missing = ImageChops.subtract(expected, actual)
                inked = _intensity(ImageChops.invert(without)).point(lambda value: 255 if value >= 128 else 0)
                bare_mask = ImageChops.subtract(missing, inked)
                bare = bare_mask.histogram()[255]
                if bare and not _edge_band_only(expected, bare_mask, bare, total):
                    if not recover:
                        raise PdfVisibilityError(f"Page {page_number}: partially hidden or low-contrast glyph; inspect rendered page")
                    shown = "\ufffd"
                    issues.add("partly covered or clipped glyph replaced")
                if covered == 0:
                    visible[key] = ""
                    continue
                floor = _MIN_BAR_COVERAGE if char in _BAR_GLYPHS else _MIN_INKED_COVERAGE
                if covered / total < floor:
                    if not recover:
                        raise PdfVisibilityError(f"Page {page_number}: glyph mostly covered by other ink; inspect rendered page")
                    shown = "\ufffd"
                    issues.add("mostly covered glyph replaced")
            visible[key] = shown
        # Visible glyphs with their painted origin in MuPDF's top-left page space (as in
        # _actual_programs); text order is deliberately dropped: the cross-check pairs glyphs.
        x0, y0, x1, y1 = page.get_cropbox()
        painted = []
        for key, members in glyphs.items():
            shown = visible[key]
            if not shown or shown.isspace():
                continue
            x, y = ctypes.c_double(), ctypes.c_double()
            if not pdfium.raw.FPDFText_GetCharOrigin(textpage.raw, members[0][0], ctypes.byref(x), ctypes.byref(y)):
                raise PdfVisibilityError(f"Page {page_number}: cannot locate painted glyph")
            painted.append((shown, x.value - x0, y1 - y.value))
            if characters is not None:
                left, bottom, right, top = key[1]
                if left < x0 - 0.01 or right > x1 + 0.01 or bottom < y0 - 0.01 or top > y1 + 0.01:
                    shown = "\ufffd"
                    issues.add("glyph crosses the page crop")
                loose = textpage.get_charbox(members[0][0], loose=True)
                font_size = pdfium.raw.FPDFText_GetFontSize(textpage.raw, members[0][0])
                font = pdfium.raw.FPDFTextObj_GetFont(members[0][1])
                identity = ctypes.cast(font, ctypes.c_void_p).value
                if identity not in spacing:
                    width = ctypes.c_float()
                    spacing[identity] = (width.value if
                        pdfium.raw.FPDFFont_GetGlyphWidth(font, 32, 1.0, ctypes.byref(width))
                        and math.isfinite(width.value) and width.value > 0 else None)
                matrix = pdfium.raw.FS_MATRIX()
                scale = (abs(matrix.a) if pdfium.raw.FPDFText_GetMatrix(
                    textpage.raw, members[0][0], ctypes.byref(matrix)) else 1.0)
                space_advance = spacing[identity]
                characters.append({"text": shown, "x": x.value - x0, "y": y1 - y.value,
                                   "bbox": [left - x0, y1 - top, right - x0, y1 - bottom],
                                   "size": font_size, "advance": loose[2] - loose[0],
                                   "spaceAdvance": space_advance * font_size * scale if space_advance else None})
        if counts is not None:
            counts["hidden"] = sum(not value for key, value in visible.items() if not key[0].isspace())
        return painted


# PDFium character origins and MuPDF texttrace origins of one painted glyph agree to ~1e-3 pt;
# 0.05 pt is far below any glyph advance, so a pairing can never jump to a neighbour.
_ORIGIN_TOLERANCE = 0.05


def _same_glyphs(pdfium_glyphs, mupdf_glyphs) -> bool:
    """True when both engines report the same visible glyphs one-to-one: equal text at the
    same painted origin. Order is ignored (engines order separately drawn sub/superscripts
    differently); any glyph missing, extra, moved or differently mapped returns False."""
    if len(pdfium_glyphs) != len(mupdf_glyphs):
        return False
    unused: dict[str, list[tuple[float, float]]] = {}
    for text, x, y in mupdf_glyphs:
        unused.setdefault(text, []).append((x, y))
    for text, x, y in pdfium_glyphs:
        candidates = unused.get(text, [])
        match = next((point for point in candidates
                      if abs(point[0] - x) <= _ORIGIN_TOLERANCE and abs(point[1] - y) <= _ORIGIN_TOLERANCE), None)
        if match is None:
            return False
        candidates.remove(match)
    return True



def _recovered_visible(source, original_source) -> list:
    """Per page: (visible PDFium glyphs, program origins), or that page's refusal string."""
    with pikepdf.open(source) as pdf:
        _normalize(pdf, reference=False, split=False)
        geometry = io.BytesIO()
        pdf.save(geometry)
        refused = _normalize(pdf, reference=False)
        normalized = io.BytesIO()
        pdf.save(normalized)
        for index, reason in _normalize(pdf, reference=True).items():
            refused.setdefault(index, reason)
        reference = io.BytesIO()
        pdf.save(reference)
    pages = []
    with pdfium.PdfDocument(original_source) as original, \
            pdfium.PdfDocument(geometry.getvalue()) as geometry_document, \
            pdfium.PdfDocument(normalized.getvalue()) as document, \
            pdfium.PdfDocument(reference.getvalue()) as ref_document:
        for index in range(len(document)):
            if index in refused:
                pages.append(refused[index])
                continue
            with closing(original[index]) as original_page, closing(document[index]) as page, \
                    closing(geometry_document[index]) as geometry_page, closing(ref_document[index]) as ref_page:
                try:
                    origins = _actual_programs(page)
                    pages.append((_page_text(original_page, page, ref_page, index + 1,
                                             origin_reference=geometry_page), origins))
                except PdfVisibilityError as exc:
                    pages.append(str(exc))
    return pages
