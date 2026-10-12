from __future__ import annotations

import copy
import hashlib
import io
from array import array

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.sfnt import SFNTReader, SFNTWriter
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent
from fontTools.ttLib.tables.ttProgram import Program

from lab_auto.pdf_glyph_evidence import authenticate_program

ORDER = [".notdef", "space", "A", "Aalt", "B", "C", "D", "emsp"]
GID = {name: gid for gid, name in enumerate(ORDER)}
ADVANCES = {".notdef": 500, "space": 500, "A": 600, "Aalt": 600, "B": 650, "C": 700, "D": 750, "emsp": 1000}
# U+0391 GREEK CAPITAL ALPHA shares the A outline, so an A shape outside its canonical slot is
# ambiguous. U+00A0 shares the canonical space slot; U+2003 has its own empty glyph.
CMAP = {0x20: "space", 0xA0: "space", 0x41: "A", 0x391: "Aalt", 0x42: "B", 0x43: "C", 0x44: "D",
        0x2003: "emsp"}


def _program(data: bytes) -> Program:
    program = Program()
    program.fromBytecode(data)
    return program


def _contour(points):
    pen = TTGlyphPen(None)
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()
    return pen.glyph()


def _reference_bytes() -> bytes:
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(ORDER)
    builder.setupCharacterMap(CMAP)
    a = _contour([(0, 0), (300, 700), (600, 0)])
    a.program = _program(bytes([0xB0, 0x01, 0x10]))  # PUSHB[0] 1; SRP0
    glyphs = {
        ".notdef": _contour([(50, 0), (50, 700), (450, 700), (450, 0)]),
        "space": TTGlyphPen(None).glyph(),
        "A": a,
        "Aalt": copy.deepcopy(a),
        "B": _contour([(0, 0), (0, 700), (650, 700), (650, 0)]),
        "C": _contour([(0, 0), (350, 700), (700, 350)]),
        "emsp": TTGlyphPen(None).glyph(),
    }
    pen = TTGlyphPen(glyphs)
    pen.addComponent("A", (1, 0, 0, 1, 0, 0))
    pen.addComponent("B", (1, 0, 0, 1, 100, 0))
    glyphs["D"] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (ADVANCES[name], 0) for name in ORDER})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Synthetic", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.font["fpgm"] = newTable("fpgm")
    builder.font["fpgm"].program = _program(bytes([0xB0, 0x00, 0x2C, 0x2D]))  # FDEF 0 / ENDF
    builder.font["prep"] = newTable("prep")
    builder.font["prep"].program = _program(bytes([0xB0, 0x00, 0x10]))
    builder.font["cvt "] = newTable("cvt ")
    builder.font["cvt "].values = array("h", [0, 20, 700])
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


REFERENCE = _reference_bytes()


def _references():
    return [(hashlib.sha256(REFERENCE).digest(), TTFont(io.BytesIO(REFERENCE), lazy=True))]


def _variant(mutate) -> bytes:
    font = TTFont(io.BytesIO(REFERENCE), recalcBBoxes=False, recalcTimestamp=False)
    mutate(font)
    output = io.BytesIO()
    font.save(output)
    return output.getvalue()


def _raw_variant(mutate) -> bytes:
    reader = SFNTReader(io.BytesIO(REFERENCE), checkChecksums=0)
    tables = {tag: reader[tag] for tag in reader.keys()}
    mutate(tables)
    output = io.BytesIO()
    writer = SFNTWriter(output, len(tables), flavor=None)
    for tag in sorted(tables):
        writer[tag] = tables[tag]
    writer.close()
    return output.getvalue()


def _lying_cmap(font):
    # The embedded cmap and names are never authority: claim every glyph is "Z".
    for table in font["cmap"].tables:
        table.cmap = {0x5A: name for name in ORDER[1:]}
    font["name"].setName("Liar", 1, 3, 1, 0x409)


def _evidence(program, *names):
    return authenticate_program(program, [GID[name] for name in names], _references())


def _status(evidence, name):
    glyph = evidence.glyphs[GID[name]]
    return glyph.status, glyph.text, glyph.method


def test_identical_program_binds_by_reference_cmap():
    evidence = _evidence(REFERENCE, "A", "B", "D", ".notdef", "emsp")
    assert evidence.status == "fast-path"
    assert evidence.references == (hashlib.sha256(REFERENCE).hexdigest(),)
    assert _status(evidence, "A") == ("bound", "A", "program-digest")
    assert _status(evidence, "D") == ("bound", "D", "program-digest")
    assert _status(evidence, ".notdef")[0] == "unknown"
    # A lone non-SPACE whitespace scalar is never bindable text.
    assert _status(evidence, "emsp")[0] == "invalid"


def test_canonical_space_slot_binds_space_despite_nbsp_alias():
    for program in (REFERENCE, _variant(_lying_cmap)):
        glyph = _evidence(program, "space").glyphs[GID["space"]]
        assert (glyph.status, glyph.text) == ("bound", " ")
        assert glyph.candidates == (" ", "\xa0")


def test_out_of_range_gid_is_invalid_and_requested_gids_are_exact():
    evidence = authenticate_program(REFERENCE, [GID["A"], len(ORDER), -1], _references())
    assert set(evidence.glyphs) == {GID["A"], len(ORDER), -1}
    assert evidence.glyphs[len(ORDER)].status == "invalid"
    assert evidence.glyphs[-1].status == "invalid"


def test_rewritten_cmap_and_names_anchor_on_glyph_programs():
    evidence = _evidence(_variant(_lying_cmap), "A", "B", "D")
    assert evidence.status == "anchored"
    assert _status(evidence, "A") == ("bound", "A", "same-index-signature")
    assert _status(evidence, "B") == ("bound", "B", "same-index-signature")
    # Composite recursion compares component signatures, not untrusted glyph names.
    assert _status(evidence, "D") == ("bound", "D", "same-index-signature")


def test_head_timestamps_and_checksum_are_not_anchored_but_flags_are():
    def retimestamp(font):
        _lying_cmap(font)
        font["head"].created += 12345
        font["head"].modified += 54321

    assert _evidence(_variant(retimestamp), "A").status == "anchored"

    def reflag(font):
        font["head"].flags ^= 0x0008

    rejected = _evidence(_variant(reflag), "A")
    assert rejected.status == "rejected" and "head" in rejected.reason
    assert rejected.glyphs == {}


@pytest.mark.parametrize("tag", ["fpgm", "prep", "cvt "])
def test_changed_global_hinting_rejects_whole_program(tag):
    def rehint(tables):
        tables[tag] = tables[tag][:-1] + bytes([tables[tag][-1] ^ 0x01])

    evidence = _evidence(_raw_variant(rehint), "A")
    assert evidence.status == "rejected"
    assert tag in evidence.reason


def test_complete_glyph_and_metrics_transplant_recovers_canonical_letter():
    def transplant(font):
        font["glyf"]["C"] = copy.deepcopy(font["glyf"]["B"])
        font["hmtx"]["C"] = font["hmtx"]["B"]

    evidence = _evidence(_variant(transplant), "C")
    assert evidence.status == "anchored"
    assert _status(evidence, "C") == ("bound", "B", "signature")


def test_contour_only_transplant_keeps_slot_metrics_and_is_refused():
    def transplant(font):
        font["glyf"]["C"] = copy.deepcopy(font["glyf"]["B"])  # hmtx stays C's

    glyph = _evidence(_variant(transplant), "C").glyphs[GID["C"]]
    assert (glyph.status, glyph.text, glyph.method) == ("unknown", None, None)


def test_changed_glyph_instructions_never_fall_back_to_index():
    def rehint(font):
        font["glyf"]["A"].program = _program(bytes([0xB0, 0x02, 0x10]))

    assert _status(_evidence(_variant(rehint), "A"), "A") == ("unknown", None, None)


@pytest.mark.parametrize("deform", ["coordinate", "bbox", "lsb"])
def test_unknown_deformation_is_refused(deform):
    def mutate(font):
        glyph = font["glyf"]["B"]
        if deform == "coordinate":
            glyph.coordinates[1] = (0, 701)
        elif deform == "bbox":
            glyph.yMax += 1
        else:
            font["hmtx"]["B"] = (ADVANCES["B"], 1)

    assert _status(_evidence(_variant(mutate), "B"), "B") == ("unknown", None, None)


def test_deformed_component_invalidates_composite():
    def mutate(font):
        font["glyf"]["A"].coordinates[1] = (300, 701)

    evidence = _evidence(_variant(mutate), "A", "D")
    assert _status(evidence, "A")[0] == "unknown"
    assert _status(evidence, "D")[0] == "unknown"


def test_shared_outline_outside_canonical_slot_is_ambiguous():
    def transplant(font):
        font["glyf"]["C"] = copy.deepcopy(font["glyf"]["A"])
        font["hmtx"]["C"] = font["hmtx"]["A"]

    glyph = _evidence(_variant(transplant), "C").glyphs[GID["C"]]
    assert (glyph.status, glyph.text, glyph.method) == ("ambiguous", None, "signature")
    assert glyph.candidates == ("A", "\u0391")


def test_arbitrary_empty_glyph_with_space_metrics_is_not_space():
    def blank(font):
        font["glyf"]["C"] = Glyph()
        font["hmtx"]["C"] = font["hmtx"]["space"]

    glyph = _evidence(_variant(blank), "C").glyphs[GID["C"]]
    assert glyph.status == "unknown" and glyph.text is None


def test_composite_cycle_is_invalid_without_raising():
    def cycle(font):
        glyph = Glyph()
        glyph.numberOfContours = -1
        component = GlyphComponent()
        component.glyphName, component.x, component.y, component.flags = "C", 0, 0, 0x0002
        glyph.components = [component]
        glyph.xMin = glyph.yMin = glyph.xMax = glyph.yMax = 0
        font["glyf"]["C"] = glyph

    evidence = _evidence(_variant(cycle), "C", "A")
    assert evidence.status == "anchored"
    assert _status(evidence, "C")[0] == "invalid"
    assert _status(evidence, "A") == ("bound", "A", "same-index-signature")


@pytest.mark.parametrize("tag", ["gvar", "ZZZZ"])
def test_forbidden_or_unanchored_tables_reject(tag):
    def add(tables):
        tables[tag] = b"\x00" * 8

    evidence = _evidence(_raw_variant(add), "A")
    assert evidence.status == "rejected" and evidence.reason


@pytest.mark.parametrize("program", [b"", b"\x00\x01\x00\x00", b"OTTO" + bytes(64), REFERENCE[:200],
                                     REFERENCE[:12] + bytes(len(REFERENCE) - 12)])
def test_malformed_programs_are_rejected_not_raised(program):
    evidence = authenticate_program(program, [GID["A"]], _references())
    assert evidence.status == "rejected" and evidence.reason and evidence.glyphs == {}


def test_malformed_used_glyph_fails_closed_while_siblings_bind():
    def corrupt(tables):
        # Overwrite B's numberOfContours so only that glyph's record is truncated garbage.
        font = TTFont(io.BytesIO(REFERENCE))
        offsets = font["loca"].locations
        start = offsets[GID["B"]]
        glyf = bytearray(tables["glyf"])
        glyf[start:start + 2] = b"\x7f\xff"  # absurd numberOfContours, contour data missing
        tables["glyf"] = bytes(glyf)
        tables["name"] = tables["name"][:-1] + b"!"

    evidence = _evidence(_raw_variant(corrupt), "A", "B")
    assert evidence.status == "anchored"
    assert _status(evidence, "B")[0] == "invalid"
    assert _status(evidence, "A") == ("bound", "A", "same-index-signature")
