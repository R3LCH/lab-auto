from __future__ import annotations

import pytest

from lab_auto.pdf_glyph_evidence import _decide, homoglyph_scripts
from lab_auto.pdf_markdown import PdfVisibilityError
from lab_auto.pdf_native import _Homoglyph, _resolve_homoglyphs

# Byte-identical trusted outlines shared by one capital A per script.
A_CLASS = {"LATIN": "A", "CYRILLIC": "\u0410", "GREEK": "\u0391"}


def glyph(claim=None, classes=A_CLASS):
    return _Homoglyph({"homoglyphs": dict(classes), "claim": claim})


def test_ambiguous_letter_in_cyrillic_word_resolves_to_cyrillic():
    text, counts = _resolve_homoglyphs([glyph(), *"\u0411\u0412"], 1)  # "АБВ"
    assert text == "\u0410\u0411\u0412"
    assert counts == {"word-script": 1, "tounicode-in-set": 0}


def test_all_ambiguous_word_follows_tounicode_inside_the_set():
    text, counts = _resolve_homoglyphs([glyph("\u0410"), glyph("\u0410"), " ", *"ok"], 1)
    assert text == "\u0410\u0410 ok"
    assert counts == {"word-script": 0, "tounicode-in-set": 2}


@pytest.mark.parametrize("claims", [("Z",), (None,), ("A", "\u0410")])
def test_tounicode_outside_the_set_or_mixing_scripts_is_rejected(claims):
    with pytest.raises(PdfVisibilityError, match="no proven script"):
        _resolve_homoglyphs([glyph(claim) for claim in claims], 1)


def test_mixed_script_word_is_rejected():
    # "pаypal": Latin letters plus the confusable outline cannot pick a script.
    with pytest.raises(PdfVisibilityError, match="mixed-script"):
        _resolve_homoglyphs(["p", glyph("\u0410"), *"yp\u0431l"], 1)


def test_word_script_must_be_offered_by_every_ambiguous_glyph():
    latin_greek = {"LATIN": "A", "GREEK": "\u0391"}
    with pytest.raises(PdfVisibilityError, match="mixed-script"):
        _resolve_homoglyphs([glyph(classes=latin_greek), *"\u0411"], 1)


def test_punctuation_splits_words():
    text, counts = _resolve_homoglyphs(["\u0411", glyph(), "-", glyph(), "b"], 1)
    assert text == "\u0411\u0410-Ab"
    assert counts["word-script"] == 2


@pytest.mark.parametrize("candidates", [
    ("A", "B"),                 # same script twice: different letters
    ("A", "\u0410", "\ua4ee"),  # Lisu outside the confusable scripts
    ("0", "O"),                 # digit
    ("-", "\u2010"),            # punctuation
    ("A",),                     # nothing to choose between
])
def test_only_one_letter_per_latin_cyrillic_greek_is_a_confusable_class(candidates):
    assert homoglyph_scripts(candidates) is None


def test_painted_hyphen_outline_binds_hyphen_minus_never_soft_hyphen():
    evidence = _decide(5, {"-", "\u00ad"}, "signature", False)
    assert (evidence.status, evidence.text) == ("bound", "-")
    assert _decide(5, {"-", "\u00ad"}, "signature", True).status == "unknown"


def test_canonically_equivalent_candidates_bind_their_nfc_form():
    # U+037E GREEK QUESTION MARK is canonically ';' (shared outline in Times New Roman subsets).
    evidence = _decide(9, {";", "\u037e"}, "signature", False)
    assert (evidence.status, evidence.text) == ("bound", ";")
    assert _decide(9, {";", ":"}, "signature", False).status == "ambiguous"


def test_private_use_alias_binds_the_single_public_candidate_only():
    assert _decide(1, {"<", "\ue110"}, "signature", False).text == "<"
    assert _decide(1, {"<", ">", "\ue110"}, "signature", False).status == "ambiguous"


def test_hyphen_aliases_bind_hyphen_minus():
    assert _decide(17, {"-", "\u00ad", "\u2010"}, "signature", False).text == "-"
    assert _decide(17, {"\u00ad", "\u2010"}, "signature", False).status == "ambiguous"


def test_font_variant_letters_fold_into_their_script():
    # MATHEMATICAL MONOSPACE CAPITAL H shares DejaVu Mono's H outline with Greek Eta, Cyrillic En.
    assert homoglyph_scripts(("\u0397", "\u041d", "\U0001d677")) == {
        "GREEK": "\u0397", "CYRILLIC": "\u041d", "LATIN": "H"}
    # KELVIN SIGN is canonically K.
    assert homoglyph_scripts(("\u039a", "\u041a", "\u212a", "\U0001d67a")) == {
        "GREEK": "\u039a", "CYRILLIC": "\u041a", "LATIN": "K"}
    assert homoglyph_scripts(("A", "B", "\U0001d670")) is None
    # Letter vs punctuation (SALTILLO vs apostrophe) is not a confusable class.
    assert homoglyph_scripts(("'", "\ua78c")) is None


def test_shared_outline_is_a_class_but_a_deformed_outline_is_not():
    import copy

    from test_pdf_glyph_evidence import GID, _evidence, _variant  # A and Greek Alpha share one outline

    def transplant(font, deform=False):
        font["glyf"]["C"] = copy.deepcopy(font["glyf"]["A"])
        font["hmtx"]["C"] = font["hmtx"]["A"]
        if deform:
            font["glyf"]["C"].coordinates[1] = (300, 701)

    shared = _evidence(_variant(transplant), "C").glyphs[GID["C"]]
    assert homoglyph_scripts(shared.candidates) == {"LATIN": "A", "GREEK": "\u0391"}
    deformed = _evidence(_variant(lambda font: transplant(font, True)), "C").glyphs[GID["C"]]
    assert deformed.status == "unknown" and homoglyph_scripts(deformed.candidates) is None
