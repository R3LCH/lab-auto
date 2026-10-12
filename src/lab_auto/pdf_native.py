"""Native glyph evidence, independent of PDF replacement text and font cmap claims."""
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
from PIL import Image, ImageChops
from lab_auto.pdf_font_recovery import recover_font_mappings

_TRUSTED_ROOTS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path.home() / ".local/share/fonts/msttcorefonts",
)


@lru_cache(maxsize=1)
def _references():
    fonts = []
    for root in _TRUSTED_ROOTS:
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if path.suffix.lower() == ".ttf":
                    fonts.append((hashlib.sha256(path.read_bytes()).digest(), TTFont(path, lazy=True)))
    return fonts




def _suppress_text(source, destination):
    """Change painting modes, including nested Forms; never modify the source snapshot."""
    with pikepdf.open(source) as pdf:
        seen = set()

        def rewrite(stream, resources, depth=0):
            if depth > 64:
                raise ValueError("text suppression: Form nesting exceeds limit")
            key = stream.obj.objgen if isinstance(stream, pikepdf.Page) else stream.objgen
            if key != (0, 0):
                if key in seen:
                    return
                seen.add(key)
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
                        raise ValueError("text suppression: text clipping modes are unsupported")
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

        for page in pdf.pages:
            rewrite(page, page.Resources)
        # Appearance text is not part of page texttrace. It remains in the baseline so
        # mixed-content verification cannot silently discard it.
        pdf.save(destination)


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


def analyze(source: Path, dpi: int, stage: Path) -> tuple[list[dict], dict]:
    working, bindings, recovery = recover_font_mappings(source, stage, _references())
    visible_texts, program_origins = _recovered_visible(working, source)
    suppressed = stage / "without-text.pdf"
    _suppress_text(working, suppressed)
    results = []
    trusted = {digest: font for digest, font in _references()}
    rewritten = {bytes.fromhex(font["programSHA256"]) for font in recovery["fonts"] if font["rewritten"]}
    with pymupdf.open(working) as document, pymupdf.open(suppressed) as baseline:
        for index, page in enumerate(document):
            number = index + 1
            pixmap = baseline[index].get_pixmap(dpi=dpi, alpha=False, annots=True, colorspace=pymupdf.csRGB)
            background = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            with Image.open(stage / f"mupdf-{number:04}.png") as rendered:
                original = rendered.convert("RGB")
            delta = ImageChops.difference(original, background).convert("L")
            # Visible non-text additions require supplementary verification. Uniform
            # backgrounds are harmless; opaque covering shapes are handled by delta.
            background_ink = any(low != high for low, high in background.getextrema())
            origins = program_origins[index]
            emitted, hidden, authenticated, recovered = 0, 0, 0, 0
            lines = []
            last_y = None
            traces = page.get_texttrace()
            positions = set()
            for span in traces:
                for _, _, origin, _ in span["chars"]:
                    position = tuple(round(value, 3) for value in origin)
                    if position in positions:
                        raise PdfVisibilityError(f"Page {number}: coincident glyph identity is ambiguous")
                    positions.add(position)
            for span in traces:
                direction = span["dir"]
                if abs(direction[0] - 1) > 0.001 or abs(direction[1]) > 0.001:
                    raise ValueError(f"Page {number}: native evidence: unsupported text direction")
                text = []
                for code, gid, origin, bbox in span["chars"]:
                    if not 0 <= code <= 0x10ffff or 0xD800 <= code <= 0xDFFF:
                        raise ValueError(f"Page {number}: native evidence: invalid Unicode")
                    char = chr(code)
                    rect = pymupdf.Rect(bbox) * page.rotation_matrix
                    scale = dpi / 72
                    box = (max(0, math.floor(rect.x0 * scale)), max(0, math.floor(rect.y0 * scale)),
                           min(delta.width, math.ceil(rect.x1 * scale)), min(delta.height, math.ceil(rect.y1 * scale)))
                    visible = (span["type"] != 3 and span.get("opacity", 1) > 0
                               and box[0] < box[2] and box[1] < box[3]
                               and delta.crop(box).getextrema()[1] >= 32)
                    digest = _program_at(origins, origin)
                    binding = bindings.get((digest, gid))
                    if binding is None and digest not in rewritten and digest in trusted:
                        reference = trusted[digest]
                        order = reference.getGlyphOrder()
                        if 0 <= gid < len(order) and reference.getBestCmap().get(code) == order[gid]:
                            binding = char
                    if char.isspace():
                        if binding != " ":
                            raise PdfVisibilityError(f"Page {number}: unauthenticated whitespace glyph")
                        if span["type"] != 3 and span.get("opacity", 1) > 0:
                            text.append(" ")
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
                        raise ValueError(f"Page {number}: native evidence: control/format character")
                    if binding != char:
                        raise PdfVisibilityError(f"Page {number}: no unique trusted binding for visible glyph {gid}")
                    authenticated += 1
                    recovered += int((digest, gid) in bindings)
                    text.append(char)
                    emitted += 1
                if text:
                    y = span["chars"][0][2][1]
                    if last_y is not None and abs(y - last_y) > span["size"] * 0.4:
                        lines.append("\n")
                    lines.append("".join(text))
                    last_y = y
            native = visible_texts[index]
            if "".join(native.split()) != "".join("".join(lines).split()):
                raise ValueError(f"Page {number}: native visibility evidence disagrees between renderers")
            # Recovered PDFium checks determine which painted glyphs may be used;
            # texttrace retains their native line/code spacing without PDFium's
            # generated separators between split single-glyph text objects.
            native = "".join(lines).strip()
            reason = "mixed raster/vector composition" if background_ink else None
            results.append({"text": native, "nativePresent": bool(page.get_texttrace()),
                            "verificationReason": reason,
                            "source": "native" if native else "blank",
                            "glyphs": {"emitted": emitted, "hidden": hidden,
                                       "authenticated": authenticated, "recovered": recovered}})
            original.close()
            background.close()
            delta.close()
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
    if subtype in {"/Type1", "/MMType1", "/TrueType", "/Type3"}:
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


def _normalize(pdf: pikepdf.Pdf, *, reference: bool) -> None:
    # Remove replacement/accessibility strings, including named property dictionaries.
    for obj in pdf.objects:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) and "/ActualText" in obj:
            del obj["/ActualText"]
    visited = set()
    processing = set()

    def rewrite(container, resources):
        identity = container.objgen
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
            if operator in {"Tj", "TJ", "'", '"'}:
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

    for page in pdf.pages:
        resources = page.obj
        while "/Resources" not in resources and "/Parent" in resources:
            resources = resources.Parent
        rewrite(page, resources.get("/Resources", {}))


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


def _page_text(original, page, reference, page_number: int) -> str:
    size = tuple(math.ceil(length * _SCALE) for length in page.get_size())
    whole = (0, 0, *size)
    baseline = _render(page, size, whole)
    source = _render(original, size, whole)
    # Splitting encoded strings must not change their painted geometry.
    difference = _intensity(ImageChops.difference(baseline, source))
    if difference.getextrema()[1] > 8:
        raise PdfVisibilityError(f"Page {page_number}: glyph splitting changed rendering")
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
        tokens = []
        glyphs = {}
        for index in range(textpage.count_chars()):
            code = pdfium.raw.FPDFText_GetUnicode(textpage.raw, index)
            char = chr(code) if code else "\ufffd"
            obj = pdfium.raw.FPDFText_GetTextObject(textpage.raw, index)
            if pdfium.raw.FPDFText_IsGenerated(textpage.raw, index) or not obj:
                if not char.isspace():
                    raise PdfVisibilityError(f"Page {page_number}: text not tied to a painted glyph")
                tokens.append(char)
                continue
            box = textpage.get_charbox(index)
            key = (char, tuple(round(value, 3) for value in box))
            tokens.append(key)
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
                    raise PdfVisibilityError(f"Page {page_number}: coincident glyphs have different text mappings")
            key = (char, bounds)
            members = glyphs.get(key)
            if members and all(ctypes.cast(other, ctypes.c_void_p).value !=
                               ctypes.cast(obj.raw, ctypes.c_void_p).value for index, other in members):
                members.append((members[0][0], obj.raw))
        seen_objects = set()
        visible = {}
        for key, members in glyphs.items():
            char, box = key
            if char.isspace():
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
            for index, obj in members:
                address = ctypes.cast(obj, ctypes.c_void_p).value
                if address in seen_objects:
                    raise PdfVisibilityError(f"Page {page_number}: multiple characters mapped to one glyph")
                seen_objects.add(address)
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
            contribution = _intensity(ImageChops.difference(baseline.crop(region), without))
            if contribution.getextrema()[1] == 0:
                visible[key] = ""
                continue
            if char == "\ufffd" or not char.isprintable() or any(
                pdfium.raw.FPDFText_HasUnicodeMapError(textpage.raw, index) for index, obj in members
            ):
                raise PdfVisibilityError(f"Page {page_number}: visible glyph has no reliable Unicode mapping")
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
            uncovered = ImageChops.multiply(expected, actual).histogram()[255]
            if not total or uncovered / total < 0.98:
                raise PdfVisibilityError(f"Page {page_number}: partially hidden or low-contrast glyph; inspect rendered page")
            visible[key] = char
        emitted = set()
        result = []
        for token in tokens:
            if isinstance(token, str):
                result.append(token)
            elif token not in emitted:
                result.append(visible[token])
                emitted.add(token)
        content = "".join(result).replace("\r\n", "\n").replace("\r", "\n")
        content = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in content.splitlines())
        return re.sub(r"\n{3,}", "\n\n", content).strip()



def _recovered_visible(source, original_source):
    with pikepdf.open(source) as pdf:
        _normalize(pdf, reference=False)
        normalized = io.BytesIO()
        pdf.save(normalized)
        _normalize(pdf, reference=True)
        reference = io.BytesIO()
        pdf.save(reference)
    texts, program_origins = [], []
    with pdfium.PdfDocument(original_source) as original, pdfium.PdfDocument(normalized.getvalue()) as document, pdfium.PdfDocument(reference.getvalue()) as ref_document:
        for index in range(len(document)):
            with closing(original[index]) as original_page, closing(document[index]) as page, closing(ref_document[index]) as ref_page:
                program_origins.append(_actual_programs(page))
                texts.append(_page_text(original_page, page, ref_page, index + 1))
    return texts, program_origins
