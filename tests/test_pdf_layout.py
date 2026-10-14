from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from lab_auto.pdf_layout import layout_pages
from lab_auto.pdf_markdown import _render


def _text(page, audited, x, y, text, size=12):
    page.insert_text((x, y), text, fontsize=size)
    font = pymupdf.Font("helv")
    for character in text:
        advance = font.glyph_advance(ord(character)) * size
        audited.append({"text": character, "x": x, "y": y, "size": float(size),
                        "advance": advance, "bbox": [x, y - size, x + advance, y + 3]})
        x += advance


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_table_content_comes_only_from_audited_characters(tmp_path: Path, rotation):
    source = tmp_path / "table.pdf"
    audited = []
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        _text(page, audited, 30, 40, "Paragraph <tag> *literal*")
        for y in [80, 120, 160]:
            page.draw_line((30, y), (290, y))
        for x in [30, 160, 290]:
            page.draw_line((x, 80), (x, 160))
        for x, y, text in [(40, 105, "CellA"), (170, 105, "CellB"),
                           (40, 145, "CellC"), (170, 145, "CellD")]:
            _text(page, audited, x, y, text)
        page.insert_text((50, 115), "HIDDEN_RAW", fontsize=9, render_mode=3)
        raster = Image.new("RGB", (90, 50), "white")
        ImageDraw.Draw(raster).ellipse((8, 8, 80, 40), fill="blue")
        buffer = io.BytesIO()
        raster.save(buffer, format="PNG")
        page.insert_image(pymupdf.Rect(30, 200, 210, 300), stream=buffer.getvalue())
        page.set_rotation(rotation)
        document.save(source)
    _render("pdfium", source, 144, tmp_path, 1)
    [result] = layout_pages(source, [{"characters": audited, "source": "native",
                                    "text": "HIDDEN_RAW: unaudited aggregate"}], tmp_path, 144)
    markdown = result["markdown"]
    assert "| CellA | CellB |" in markdown
    assert "| CellC | CellD |" in markdown
    assert "HIDDEN_RAW" not in markdown
    assert "Paragraph &lt;tag&gt; \\*literal\\*" in markdown
    assert all(markdown.count(value) == 1 for value in ("CellA", "CellB", "CellC", "CellD"))
    assert len(result["tables"]) == len(result["assets"]) == 1
    asset = result["assets"][0]
    assert asset["kind"] == "image"
    assert asset["width"] < 800 and asset["height"] < 800
    with pymupdf.open(source) as document:
        expected = pymupdf.Rect(30, 200, 210, 300) * document[0].rotation_matrix
    actual = pymupdf.Rect(asset["bbox"])
    assert expected in actual
    assert actual.width <= expected.width + 5
    assert actual.height <= expected.height + 5


def test_vector_crossing_statement_is_only_in_graph_crop(tmp_path):
    source = tmp_path / "crossed.pdf"
    audited = []
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        _text(page, audited, 40, 50, "Outside paragraph")
        _text(page, audited, 50, 130, "a = b", size=40)
        page.draw_line((85, 90), (110, 145), width=2)
        page.draw_line((40, 53), (135, 53), width=.5)  # ordinary underline
        page.draw_rect(pymupdf.Rect(10, 200, 390, 300), color=None, fill=(1, 1, 1))
        document.save(source)
    _render("pdfium", source, 144, tmp_path, 1)
    [result] = layout_pages(source, [{"characters": audited, "source": "native"}], tmp_path, 144)
    assert "Outside paragraph" in result["markdown"]
    assert "a = b" not in result["markdown"]
    assert len(result["assets"]) == 1
    assert result["assets"][0]["kind"] == "graph"
    assert result["assets"][0]["height"] < 200


def test_merged_and_blank_cells_keep_anchor_without_fabrication(tmp_path):
    source = tmp_path / "merged.pdf"
    audited = []
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=300)
        for y in [40, 100, 160]:
            page.draw_line((20, y), (280, y))
        for x in [20, 280]:
            page.draw_line((x, 40), (x, 160))
        page.draw_line((150, 100), (150, 160))
        _text(page, audited, 30, 65, "Merged")
        _text(page, audited, 30, 85, "Second line")
        _text(page, audited, 30, 125, "Left")
        document.save(source)
    _render("pdfium", source, 144, tmp_path, 1)
    [result] = layout_pages(source, [{"characters": audited, "source": "native"}], tmp_path, 144)
    assert "| Merged<br>Second line |  |" in result["markdown"]
    assert "| Left |  |" in result["markdown"]
    [table] = result["tables"]
    assert table["rows"] == table["columns"] == 2
    assert table["cells"][0]["colspan"] == 2
    assert not result["assets"]


def test_empty_grid_and_uniform_cover_are_not_graph_crops(tmp_path):
    source = tmp_path / "rules.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=300)
        for y in [50, 90, 130]:
            page.draw_line((30, y), (270, y), width=.5)
        for x in [30, 150, 270]:
            page.draw_line((x, 50), (x, 130), width=.5)
        page.draw_rect(pymupdf.Rect(10, 180, 290, 260), color=None, fill=(1, 1, 1))
        document.save(source)
    _render("pdfium", source, 144, tmp_path, 1)
    [result] = layout_pages(source, [{"characters": [], "text": "Not audited"}], tmp_path, 144)
    assert result == {"markdown": "", "assets": [], "tables": [], "warnings": []}


def test_audited_advances_not_ink_side_bearings_determine_spaces():
    from lab_auto.pdf_layout import _lines

    characters = []
    for text, x, right, advance in [
        ("V", 0, 9, 10), ("I", 10, 12, 5), ("S", 15, 24, 10),
        ("I", 25, 27, 6), ("B", 31, 40, 10), ("L", 41, 49, 9),
        ("E", 50, 58, 10), ("P", 65, 74, 10), ("A", 75, 84, 10),
    ]:
        characters.append({"text": text, "x": float(x), "y": 20., "size": 16.,
                           "advance": float(advance), "rect": pymupdf.Rect(x, 8, right, 20)})
    assert _lines(characters)[0]["text"] == "VISIBLE PA"
