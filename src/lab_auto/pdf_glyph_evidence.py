"""Bind embedded TrueType glyphs to Unicode only through trusted reference programs.

The authority is the rasterization input of a glyph. PDF ToUnicode, the embedded cmap,
name/post tables and font names are never consulted. A glyph is bound only when its program
is a trusted reference program, or when every global table that feeds the rasterizer is
byte-identical to a trusted reference and the glyph itself (outline, flags, instructions,
stored bbox and metrics) equals a trusted reference glyph.
"""
from __future__ import annotations

import hashlib
import io
import struct
import unicodedata
from dataclasses import dataclass

from fontTools.ttLib import TTFont

# Tables that do not feed rasterization of a given glyph (cmap/name/post/DSIG/FFTM) or that
# are compared per glyph through the signature instead (glyf/loca/hmtx).
_EXCLUDED = frozenset({"cmap", "name", "post", "DSIG", "FFTM", "glyf", "loca", "hmtx"})
_FORBIDDEN = frozenset({
    "CFF ", "CFF2", "fvar", "gvar", "avar", "HVAR", "VVAR", "MVAR", "cvar", "COLR", "CPAL",
    "CBDT", "CBLC", "sbix", "SVG ", "EBDT", "EBLC", "EBSC", "Silf", "Glat", "Gloc", "Feat", "Sill",
})
_REQUIRED = frozenset({"glyf", "loca", "head", "hhea", "hmtx", "maxp"})
# Embedded bitmap strikes in a *trusted reference* (msttcorefonts ship EBDT/EBLC). Anchoring
# compares only the reference's hinting tables and glyf outlines, never its bitmaps, and the
# untrusted program itself must still carry none of them (_FORBIDDEN / _KNOWN in _anchored),
# so the rasterized embedded glyph is always the compared outline.
_REFERENCE_BITMAPS = frozenset({"EBDT", "EBLC", "EBSC"})
# Global inputs of the hinting VM; any glyph's instructions can read them.
_RASTER = ("fpgm", "prep", "cvt ")
# Layout, device-metric and font-wide summary tables. PDF renderers never shape text with
# them and subsetters rewrite them; vmtx is compared per glyph through the signature.
_LAYOUT = frozenset({"OS/2", "hhea", "vhea", "vmtx", "hdmx", "VDMX", "LTSH", "kern", "GPOS", "GSUB",
                     "GDEF", "JSTF", "BASE", "MATH", "PCLT", "meta", "gasp"})
_KNOWN = _REQUIRED | _EXCLUDED | frozenset(_RASTER) | _LAYOUT
_TRUETYPE_VERSIONS = (b"\x00\x01\x00\x00", b"true")
_MAX_COMPONENT_DEPTH = 16
_SIMPLE_FLAG_MASK = 0x41  # ON_CURVE_POINT | OVERLAP_SIMPLE
_COMPONENT_FLAG_MASK = 0x1E06  # ARGS_ARE_XY, ROUND_XY, USE_MY_METRICS, OVERLAP, SCALED/UNSCALED
_INVALID_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


@dataclass(frozen=True)
class GlyphEvidence:
    gid: int
    status: str            # "bound" | "ambiguous" | "unknown" | "invalid"
    text: str | None       # single scalar when bound, else None
    method: str | None     # "program-digest" | "same-index-signature" | "signature" | None
    candidates: tuple[str, ...]   # sorted candidate scalars considered


@dataclass(frozen=True)
class ProgramEvidence:
    digest: bytes          # sha256 of program bytes
    status: str            # "fast-path" | "anchored" | "rejected"
    reason: str | None     # rejection reason when rejected
    references: tuple[str, ...]   # hex digests of matching/anchoring refs
    glyphs: dict[int, GlyphEvidence]  # exactly the requested gids (empty when rejected)


_ACTIVE = object()  # signature computation in progress (cycle marker)
_TOO_DEEP = object()  # component nesting exceeded the limit from the current call depth


def _global_key(reader, tags) -> dict[str, bytes | None] | None:
    """Rasterizer state shared by all glyphs: hinting programs, VM limits, head flags/unitsPerEm.

    Subsetters recompute maxp point/contour counts and head bbox/timestamps, so only the VM
    limits (maxZones..maxStackElements) and head flags/unitsPerEm are anchored there.
    """
    maxp, head = bytes(reader["maxp"]), bytes(reader["head"])
    if len(maxp) < 32 or maxp[:4] != b"\x00\x01\x00\x00" or len(head) < 54:
        return None
    key: dict[str, bytes | None] = {tag: bytes(reader[tag]) if tag in tags else None for tag in _RASTER}
    key["maxp"] = maxp[14:26]
    key["head"] = head[16:20]
    return key


def _bytecode(glyph) -> bytes:
    program = getattr(glyph, "program", None)
    return b"" if program is None else bytes(program.getBytecode())


class _Glyphs:
    """Recursive, deterministic glyph signatures of one font; None marks an invalid glyph."""

    def __init__(self, font: TTFont, names: list[str]):
        self.glyf = font["glyf"]
        self.hmtx = font["hmtx"].metrics
        # Vertical phantom points feed the hinting VM exactly like the horizontal ones.
        self.vmtx = font["vmtx"].metrics if "vmtx" in font else None
        self.names = names
        self.ids = {name: gid for gid, name in enumerate(names)}
        self.memo: dict[int, object] = {}
        self.too_deep: dict[int, int] = {}  # gid -> smallest call depth that exceeded the limit

    def signature(self, gid: int):
        value = self._signature(gid, 0)
        return None if value is _TOO_DEEP else value

    def _signature(self, gid: int, depth: int):
        if not 0 <= gid < len(self.names):
            return None
        if gid in self.memo:
            value = self.memo[gid]
            return None if value is _ACTIVE else value  # revisiting an active glyph is a cycle
        if depth > _MAX_COMPONENT_DEPTH or depth >= self.too_deep.get(gid, depth + 1):
            return _TOO_DEEP
        self.memo[gid] = _ACTIVE
        try:
            value = self._compute(gid, depth)
        except Exception:  # hostile glyph data
            value = None
        if value is _TOO_DEEP:
            # Depth failures depend on the caller's depth; remember the bound instead of the result.
            del self.memo[gid]
            self.too_deep[gid] = depth
            return _TOO_DEEP
        self.memo[gid] = value
        return value

    def _compute(self, gid: int, depth: int):
        name = self.names[gid]
        glyph = self.glyf.glyphs.get(name)
        if glyph is None:
            return None
        glyph.expand(self.glyf)
        metrics = (tuple(self.hmtx[name]), None if self.vmtx is None else tuple(self.vmtx[name]))
        count = glyph.numberOfContours
        if count == 0 and not hasattr(glyph, "xMin"):
            return ("empty", metrics)
        bbox = (glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax)
        if count >= 0:
            return ("simple", count,
                    tuple(getattr(glyph, "endPtsOfContours", ())),
                    tuple(tuple(point) for point in getattr(glyph, "coordinates", ())),
                    tuple(flag & _SIMPLE_FLAG_MASK for flag in getattr(glyph, "flags", ())),
                    _bytecode(glyph), bbox, metrics)
        if count != -1:
            return None
        parts = []
        for component in glyph.components:
            child = self.ids.get(component.glyphName)
            if child is None:
                return None
            child_signature = self._signature(child, depth + 1)
            if child_signature is None or child_signature is _TOO_DEEP:
                return child_signature
            if hasattr(component, "firstPt"):
                placement = ("points", component.firstPt, component.secondPt)
            else:
                placement = ("offset", component.x, component.y)
            transform = getattr(component, "transform", ((1, 0), (0, 1)))
            parts.append((child_signature, component.flags & _COMPONENT_FLAG_MASK, placement,
                          tuple(tuple(row) for row in transform)))
        if not parts:
            return None
        return ("composite", tuple(parts), _bytecode(glyph), bbox, metrics)


class _Reference:
    """Lazily derived, cached facts about one trusted reference program."""

    def __init__(self, digest: bytes, font: TTFont):
        self.digest = digest
        self.font = font
        self.num_glyphs = font["maxp"].numGlyphs
        scalars: dict[int, set[str]] = {}
        for code, name in (font.getBestCmap() or {}).items():
            try:
                scalars.setdefault(font.getGlyphID(name), set()).add(chr(code))
            except (KeyError, ValueError):
                continue
        self.scalars = {gid: frozenset(texts) for gid, texts in scalars.items()}
        self._anchor = None
        self._glyphs = None
        self._index = None

    def anchor(self) -> dict[str, bytes | None] | None:
        """Global rasterizer inputs (see _global_key) or None when unusable."""
        if self._anchor is None:
            try:
                reader = self.font.reader
                tags = frozenset(str(tag) for tag in reader.keys())
                usable = _REQUIRED <= tags and not tags & (_FORBIDDEN - _REFERENCE_BITMAPS)
                self._anchor = (_global_key(reader, tags) if usable else None) or False
            except Exception:
                self._anchor = False
        return self._anchor or None

    def glyphs(self) -> _Glyphs:
        if self._glyphs is None:
            self._glyphs = _Glyphs(self.font, self.font.getGlyphOrder())
        return self._glyphs

    def index(self) -> dict[object, tuple[int, ...]]:
        """Signature -> reference gids; built only for references that anchor a program."""
        if self._index is None:
            glyphs = self.glyphs()
            index: dict[object, list[int]] = {}
            for gid in range(self.num_glyphs):
                signature = glyphs.signature(gid)
                if signature is not None:
                    index.setdefault(signature, []).append(gid)
            self._index = {signature: tuple(gids) for signature, gids in index.items()}
        return self._index


_REFERENCE_CACHE: dict[bytes, _Reference] = {}


def _reference(digest: bytes, font: TTFont) -> _Reference:
    cached = _REFERENCE_CACHE.get(digest)
    if cached is None:
        cached = _REFERENCE_CACHE[digest] = _Reference(digest, font)
    return cached


# Scripts whose letters subsetters routinely collapse onto one outline (А/A/Α, О/O/Ο, Г/Γ).
_HOMOGLYPH_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")


def _fold(text: str) -> str:
    """Fold a letter to the character it is by Unicode definition: a <font> compatibility
    variant (U+1D670 MATHEMATICAL MONOSPACE A) to its base letter, a canonical singleton
    (U+212A KELVIN SIGN) to its NFC form. DejaVu Mono shares one outline for each pair; no
    other compatibility mapping folds."""
    if len(text) == 1 and unicodedata.decomposition(text).startswith("<font>"):
        return unicodedata.normalize("NFKC", text)
    return unicodedata.normalize("NFC", text)


def homoglyph_scripts(candidates) -> dict[str, str] | None:
    """{script: letter} when candidates are one letter per Latin/Cyrillic/Greek script, else None.

    The candidates come from GlyphEvidence of an "ambiguous" glyph, i.e. every one of them is
    the reference cmap's character for a byte-identical outline; this only names which scripts
    the painted shape could belong to. Anything else (digits, punctuation, two letters of one
    script, another script) is not a confusable class and stays refused.
    """
    scripts: dict[str, str] = {}
    for text in candidates:
        text = _fold(text)
        if len(text) != 1 or not text.isalpha():
            return None
        script = unicodedata.name(text, "").split(" ", 1)[0]
        if script not in _HOMOGLYPH_SCRIPTS or scripts.get(script, text) != text:
            return None
        scripts[script] = text
    return scripts if len(scripts) >= 2 else None


def letter_script(text: str) -> str | None:
    """Script name of a letter for homoglyph resolution; None for non-letters."""
    if not text.isalpha():
        return None
    return unicodedata.name(text, "").split(" ", 1)[0] or "UNKNOWN"


def _bindable(text: str) -> bool:
    return (len(text) == 1 and unicodedata.category(text) not in _INVALID_CATEGORIES
            and (text.isprintable() or text == " "))


def _decide(gid: int, candidates, method: str, empty: bool) -> GlyphEvidence:
    ordered = tuple(sorted(candidates))
    if not ordered:
        return GlyphEvidence(gid, "unknown", None, None, ())
    if empty and method == "signature":
        # An empty outline elsewhere in the font proves nothing about which character this
        # slot held; only the canonical same-index (or identical-program) slot does.
        return GlyphEvidence(gid, "unknown", None, method, ordered)
    if len(ordered) > 1:
        if empty and " " in ordered and all(text.isspace() for text in ordered):
            return GlyphEvidence(gid, "bound", " ", method, ordered)
        if not empty and "-" in ordered and set(ordered) <= {"-", "\u00ad", "\u2010"}:
            # A painted hyphen outline is a visible HYPHEN-MINUS (DejaVu Mono shares it with
            # U+00AD and U+2010); a soft hyphen is never emitted.
            return GlyphEvidence(gid, "bound", "-", method, ordered)
        canonical = {unicodedata.normalize("NFC", text) for text in ordered}
        if not empty and len(canonical) == 1 and len(text := canonical.pop()) == 1 and _bindable(text):
            # Canonical singletons (U+037E GREEK QUESTION MARK -> ';', U+2126 OHM -> U+03A9) are
            # the same character by Unicode definition; emitting the NFC form chooses nothing.
            return GlyphEvidence(gid, "bound", text, method, ordered)
        public = [text for text in ordered if unicodedata.category(text) != "Co"]
        if not empty and len(public) == 1 and _bindable(public[0]):
            # Symbol fonts (OpenSymbol) alias outlines into the Private Use Area; a PUA code has
            # no standard meaning, so the one public candidate is the only character the trusted
            # outline can denote. Two public candidates stay ambiguous.
            return GlyphEvidence(gid, "bound", public[0], method, ordered)
        return GlyphEvidence(gid, "ambiguous", None, method, ordered)
    text = ordered[0]
    if text.isspace() and not (text == " " and empty):
        return GlyphEvidence(gid, "invalid", None, method, ordered)
    if not _bindable(text):
        return GlyphEvidence(gid, "invalid", None, method, ordered)
    return GlyphEvidence(gid, "bound", text, method, ordered)


def _rejected(digest: bytes, reason: str) -> ProgramEvidence:
    return ProgramEvidence(digest, "rejected", reason, (), {})


def _invalid(gid: int) -> GlyphEvidence:
    return GlyphEvidence(gid, "invalid", None, None, ())


def authenticate_program(program: bytes, gids, references) -> ProgramEvidence:
    """Return per-gid Unicode evidence for an embedded TrueType program; never raises on it."""
    program = bytes(program)
    digest = hashlib.sha256(program).digest()
    requested = list(dict.fromkeys(gids))

    exact = [(ref_digest, font) for ref_digest, font in references if ref_digest == digest]
    if exact:
        reference = _reference(*exact[0])
        glyphs = {}
        for gid in requested:
            if type(gid) is not int or not 0 <= gid < reference.num_glyphs:
                glyphs[gid] = _invalid(gid)
                continue
            signature = reference.glyphs().signature(gid)
            empty = signature is not None and signature[0] == "empty"
            glyphs[gid] = _decide(gid, reference.scalars.get(gid, ()), "program-digest", empty)
        return ProgramEvidence(digest, "fast-path", None, (digest.hex(),), glyphs)

    if program[:4] not in _TRUETYPE_VERSIONS:
        return _rejected(digest, "not a TrueType sfnt program")
    try:
        font = TTFont(io.BytesIO(program), lazy=True, checkChecksums=0)
    except Exception as exc:
        return _rejected(digest, f"unparseable program: {type(exc).__name__}")
    try:
        return _anchored(font, program, digest, requested, references)
    except Exception as exc:  # hostile program data must never escape as an exception
        return _rejected(digest, f"unparseable program: {type(exc).__name__}")
    finally:
        # Only the per-call untrusted font is closed; cached trusted references stay open.
        font.close()


def _anchored(font: TTFont, program: bytes, digest: bytes, requested, references) -> ProgramEvidence:
    try:
        reader = font.reader
        tags = frozenset(str(tag) for tag in reader.keys())
        if len(tags) != struct.unpack(">H", program[4:6])[0]:
            return _rejected(digest, "duplicate table directory entries")
        unsupported = sorted((tags & _FORBIDDEN) | (tags - _KNOWN))
        if unsupported:
            return _rejected(digest, "unsupported tables: " + ", ".join(unsupported))
        missing = sorted(_REQUIRED - tags)
        if missing:
            return _rejected(digest, "missing required tables: " + ", ".join(missing))
        key = _global_key(reader, tags)
        if key is None:
            return _rejected(digest, "unsupported maxp/head table versions")
        num_glyphs = struct.unpack(">H", bytes(reader["maxp"])[4:6])[0]
    except Exception as exc:
        return _rejected(digest, f"unparseable program: {type(exc).__name__}")

    # Subsets may renumber glyphs and rewrite counts, bbox, hhea and OS/2, but the hinting VM's
    # global inputs must equal a reference byte for byte; per-glyph checks alone are never enough.
    anchored: list[_Reference] = []
    closest: frozenset[str] | None = None
    for ref_digest, ref_font in references:
        try:
            reference = _reference(ref_digest, ref_font)
            anchor = reference.anchor()
        except Exception:
            continue
        if anchor is None:
            continue
        changed = frozenset(name for name, data in key.items() if anchor[name] != data)
        if not changed:
            if reference not in anchored:
                anchored.append(reference)
        elif closest is None or len(changed) < len(closest):
            closest = changed
    if not anchored:
        if closest:
            return _rejected(digest, "tables differ from trusted reference: " + ", ".join(sorted(closest)))
        return _rejected(digest, "no trusted reference available")

    try:
        # Never let the untrusted post table name glyphs; names are positional placeholders.
        font.setGlyphOrder([f"gid{gid}" for gid in range(num_glyphs)])
        program_glyphs = _Glyphs(font, font.getGlyphOrder())
    except Exception as exc:
        return _rejected(digest, f"unparseable glyph tables: {type(exc).__name__}")

    glyphs = {}
    for gid in requested:
        try:
            glyphs[gid] = _anchored_glyph(gid, program_glyphs, num_glyphs, anchored)
        except Exception:  # malformed used glyph fails closed
            glyphs[gid] = _invalid(gid)
    references_hex = tuple(sorted({reference.digest.hex() for reference in anchored}))
    return ProgramEvidence(digest, "anchored", None, references_hex, glyphs)


def _anchored_glyph(gid, program_glyphs: _Glyphs, num_glyphs: int, anchored) -> GlyphEvidence:
    if type(gid) is not int or not 0 <= gid < num_glyphs:
        return _invalid(gid)
    signature = program_glyphs.signature(gid)
    if signature is None:
        return _invalid(gid)
    # Index coincidence only identifies a slot when the program keeps the reference numbering.
    same_index = [reference for reference in anchored
                  if reference.num_glyphs == num_glyphs and reference.glyphs().signature(gid) == signature]
    if same_index:
        method = "same-index-signature"
        candidates = set().union(*(reference.scalars.get(gid, ()) for reference in same_index))
    else:
        # A mismatch at the same index never falls back to the index's characters.
        method = "signature"
        candidates = set()
        for reference in anchored:
            for match in reference.index().get(signature, ()):
                candidates |= reference.scalars.get(match, frozenset())
    return _decide(gid, candidates, method, signature[0] == "empty")
