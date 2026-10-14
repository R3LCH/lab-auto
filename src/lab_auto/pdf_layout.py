"""Editable PDF layout from audited characters and geometry only.

MuPDF supplies drawing/image geometry, never document text. Assets are crops of
the already-rendered primary PDFium image. All coordinates in published records
are displayed CropBox points; native character input is unrotated CropBox points.
"""
from __future__ import annotations

import hashlib
import html
import io
import math
import re
from pathlib import Path

import pymupdf
from PIL import Image

_TOL = 2.0
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


def _literal(text: str) -> str:
    return _MARKDOWN.sub(r"\\\1", html.escape(text, quote=False))


def _union(rects) -> pymupdf.Rect:
    result = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        result |= rect
    return result


def _expand(rect, amount=2.0) -> pymupdf.Rect:
    return pymupdf.Rect(rect.x0 - amount, rect.y0 - amount,
                        rect.x1 + amount, rect.y1 + amount)


def _touch(a, b, tolerance=0.0) -> bool:
    return not (_expand(a, tolerance) & b).is_empty


def _center(rect):
    return pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def _gap(previous, current):
    if "advance" in previous:
        return current["x"] - (previous["x"] + previous["advance"])
    return current["rect"].x0 - previous["rect"].x1


def _lines(chars: list[dict]) -> list[dict]:
    """Group baselines, then split disconnected columns before joining text."""
    rows: list[list[dict]] = []
    for char in sorted(chars, key=lambda c: (-c["size"], c["y"], c["x"])):
        row = next((r for r in rows
                    if abs(r[0]["y"] - char["y"]) <= max(1.5, max(r[0]["size"], char["size"]) * .5)), None)
        if row is None:
            rows.append([char])
        else:
            row.append(char)
    lines = []
    for row in rows:
        groups: list[list[dict]] = [[]]
        for char in sorted(row, key=lambda c: c["x"]):
            if groups[-1]:
                previous = groups[-1][-1]
                gap = _gap(previous, char)
                if gap > max(char["size"], previous["size"]) * 2.5:
                    groups.append([])
            groups[-1].append(char)
        for group in groups:
            text = ""
            previous = None
            for char in group:
                if (previous is not None and text and not text[-1].isspace()
                        and not char["text"].startswith((" ", "\t", "\n"))
                        and _gap(previous, char) > char["size"] * .18):
                    gap = _gap(previous, char)
                    width = previous.get("spaceAdvance")
                    amount = max(1, round(gap / width)) if width else 1
                    text += " " * amount
                text += char["text"]
                previous = char
            lines.append({"text": text.strip(), "chars": group,
                          "rect": _union([c["rect"] for c in group]),
                          "size": max(c["size"] for c in group)})
    return sorted(lines, key=lambda line: (line["rect"].y0, line["rect"].x0))


def _paragraphs(chars):
    paragraphs = []
    for line in _lines(chars):
        if not line["text"]:
            continue
        previous = paragraphs[-1] if paragraphs else None
        if (previous is not None
                and -line["size"] * .35 <= line["rect"].y0 - previous["last"].y1 <= line["size"] * .65
                and abs(line["rect"].x0 - previous["last"].x0) <= line["size"] * 1.5
                and abs(line["size"] - previous["size"]) <= 1):
            previous["text"] += "  \n" + _literal(line["text"])
            previous["rect"] |= line["rect"]
            previous["last"] = line["rect"]
        else:
            paragraphs.append({"rect": pymupdf.Rect(line["rect"]), "last": line["rect"],
                               "size": line["size"], "text": _literal(line["text"])})
    return paragraphs


def _drawing_parts(drawings):
    """Return axis-aligned rules and non-rule graphic components separately."""
    rules = []
    graphics = []
    for drawing in drawings:
        stroke = "s" in drawing["type"] and drawing.get("stroke_opacity", 1) > 0
        fill = "f" in drawing["type"] and drawing.get("fill_opacity", 1) > 0
        if not stroke and not fill:
            continue
        width = float(drawing.get("width") or 0)
        for item in drawing["items"]:
            tag = item[0]
            if tag == "l" and (stroke or fill):
                a, b = item[1:3]
                if abs(a.y - b.y) <= .5:
                    if stroke:
                        rules.append(("h", (a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x), width))
                elif abs(a.x - b.x) <= .5:
                    if stroke:
                        rules.append(("v", (a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y), width))
                else:
                    graphics.append(_expand(pymupdf.Rect(a, b).normalize(), max(1, width / 2)))
            elif tag == "re":
                rect = pymupdf.Rect(item[1])
                if fill and min(rect.width, rect.height) <= 2 and max(rect.width, rect.height) >= 6:
                    if rect.width > rect.height:
                        rules.append(("h", (rect.y0 + rect.y1) / 2, rect.x0, rect.x1, rect.height))
                    else:
                        rules.append(("v", (rect.x0 + rect.x1) / 2, rect.y0, rect.y1, rect.width))
                elif stroke:
                    rules.extend([("h", rect.y0, rect.x0, rect.x1, width),
                                  ("h", rect.y1, rect.x0, rect.x1, width),
                                  ("v", rect.x0, rect.y0, rect.y1, width),
                                  ("v", rect.x1, rect.y0, rect.y1, width)])
                # Solid rectangular fills alone are covers/backgrounds, not graphs.
            elif tag == "qu":
                quad = item[1]
                if quad.is_rectangular:
                    rect = quad.rect
                    if stroke:
                        rules.extend([("h", rect.y0, rect.x0, rect.x1, width),
                                      ("h", rect.y1, rect.x0, rect.x1, width),
                                      ("v", rect.x0, rect.y0, rect.y1, width),
                                      ("v", rect.x1, rect.y0, rect.y1, width)])
                else:
                    graphics.append(_expand(quad.rect, max(1, width / 2)))
            elif tag == "c":
                graphics.append(_expand(pymupdf.Rect(drawing["rect"]), max(1, width / 2)))
    return rules, graphics


def _snap(values):
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= _TOL:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _table_geometry(rules) -> list[list[pymupdf.Rect]]:
    """Closed minimal cells from snapped, joined borders, including merged cells."""
    xs = _snap([r[1] for r in rules if r[0] == "v"])
    ys = _snap([r[1] for r in rules if r[0] == "h"])
    if len(xs) < 2 or len(ys) < 2:
        return []
    borders = {"h": {}, "v": {}}
    for axis, coordinate, start, end, _ in rules:
        snapped = min(ys if axis == "h" else xs, key=lambda value: abs(value - coordinate))
        borders[axis].setdefault(snapped, []).append((start, end))

    def covers(axis, coordinate, start, end):
        reach = start
        for left, right in sorted(borders[axis].get(coordinate, [])):
            if left > reach + _TOL:
                break
            if right >= reach - _TOL:
                reach = max(reach, right)
            if reach >= end - _TOL:
                return True
        return False

    cells = []
    for yi, top in enumerate(ys[:-1]):
        for xi, left in enumerate(xs[:-1]):
            found = False
            for bottom in ys[yi + 1:]:
                for right in xs[xi + 1:]:
                    if (covers("h", top, left, right) and covers("h", bottom, left, right)
                            and covers("v", left, top, bottom) and covers("v", right, top, bottom)):
                        # An internal complete border means this is not a minimal cell.
                        if any(covers("v", x, top, bottom) for x in xs[xi + 1:] if x < right):
                            continue
                        if any(covers("h", y, left, right) for y in ys[yi + 1:] if y < bottom):
                            continue
                        cells.append(pymupdf.Rect(left, top, right, bottom))
                        found = True
                        break
                if found:
                    break
    groups = []
    for cell in cells:
        touching = [group for group in groups
                    if any(_touch(cell, other, .1) for other in group)]
        if not touching:
            groups.append([cell])
        else:
            group = touching[0]
            group.append(cell)
            for other in touching[1:]:
                group.extend(other)
                groups.remove(other)
    return [group for group in groups if len(group) >= 2]


def _table(cells, chars, rotation):
    xs = _snap([v for c in cells for v in (c.x0, c.x1)])
    ys = _snap([v for c in cells for v in (c.y0, c.y1)])
    rows = [["" for _ in xs[:-1]] for _ in ys[:-1]]
    consumed = set()
    records = []
    for cell in sorted(cells, key=lambda c: (c.y0, c.x0)):
        selected = [c for c in chars if _center(c["rect"]) in cell and c["id"] not in consumed]
        consumed.update(c["id"] for c in selected)
        text = "\n".join(line["text"] for line in _lines(selected))
        row = min(range(len(ys) - 1), key=lambda index: abs(ys[index] - cell.y0))
        column = min(range(len(xs) - 1), key=lambda index: abs(xs[index] - cell.x0))
        rows[row][column] = text
        records.append({"row": row, "column": column, "text": text,
                        "bbox": list(cell * rotation),
                        "rowspan": sum(cell.y0 - _TOL <= y < cell.y1 - _TOL for y in ys[:-1]),
                        "colspan": sum(cell.x0 - _TOL <= x < cell.x1 - _TOL for x in xs[:-1])})
    # The first real row is retained as the Markdown header. No guessed headings.
    formatted = ["| " + " | ".join(_literal(value).replace("\n", "<br>") for value in row) + " |"
                 for row in rows]
    formatted.insert(1, "| " + " | ".join("---" for _ in xs[:-1]) + " |")
    rect = _union(cells)
    return {"bbox": list(rect * rotation), "rows": len(rows), "columns": len(xs) - 1,
            "cells": records}, "\n".join(formatted), consumed, rect


def _merge_regions(regions):
    result = []
    for rect, kind in regions:
        touching = [entry for entry in result if _touch(rect, entry[0], 2)]
        for entry in touching:
            rect |= entry[0]
            if entry[1] == "graph":
                kind = "graph"
            result.remove(entry)
        result.append((rect, kind))
    # A union can bridge previously disjoint regions: repeat until stable.
    if len(result) < len(regions):
        return _merge_regions(result)
    return result


def _asset(image, rect, page_rect, kind, stage):
    rect = rect & page_rect
    if rect.is_empty:
        return None
    sx, sy = image.width / page_rect.width, image.height / page_rect.height
    pixels = (max(0, math.floor(rect.x0 * sx)), max(0, math.floor(rect.y0 * sy)),
              min(image.width, math.ceil(rect.x1 * sx)), min(image.height, math.ceil(rect.y1 * sy)))
    if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
        return None
    crop = image.crop(pixels).convert("RGB")
    if kind != "type3-page" and all(low == high for low, high in crop.getextrema()):
        return None
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{digest}.png"
    (stage / filename).write_bytes(data)
    return {"png": filename, "pngSHA256": digest, "kind": kind,
            "bbox": [pixels[0] / sx, pixels[1] / sy, pixels[2] / sx, pixels[3] / sy],
            "width": crop.width, "height": crop.height}


def _reading_order(events):
    """Recursive whitespace cuts preserve columns and full-width separators."""
    if len(events) < 2:
        return events
    for axis in (0, 1):
        ordered = sorted(events, key=lambda event: event[0][axis])
        reach = ordered[0][0][axis + 2]
        gaps = []
        for index, event in enumerate(ordered[1:], 1):
            gap = event[0][axis] - reach
            if gap > (18 if axis == 0 else 2):
                gaps.append((gap, index))
            reach = max(reach, event[0][axis + 2])
        if gaps:
            _, index = max(gaps)
            return _reading_order(ordered[:index]) + _reading_order(ordered[index:])
    return sorted(events, key=lambda event: (event[0].y0, event[0].x0))


def layout_pages(source: Path, native_pages: list[dict], stage: Path, dpi: int) -> list[dict]:
    """Lay out visibility-checked native characters; crop only images/graphs/Type3."""
    pages = []
    with pymupdf.open(source) as document:
        if len(native_pages) != len(document):
            raise ValueError("Layout/native page count mismatch")
        for index, native in enumerate(native_pages):
            page = document[index]
            rotation = page.rotation_matrix
            result = {"markdown": "", "assets": [], "tables": [], "warnings": []}
            with Image.open(stage / f"pdfium-{index + 1:04}.png") as image:
                if native.get("reviewRequired") == "type3":
                    asset = _asset(image, page.rect, page.rect, "type3-page", stage)
                    result["assets"].append(asset)
                    result["markdown"] = f"![Type3 page — visual review required](__ASSET_DIR__/{asset['png']})"
                    pages.append(result)
                    continue
                chars = [dict(c, id=i, rect=pymupdf.Rect(c["bbox"]))
                         for i, c in enumerate(native["characters"])]
                lines = _lines(chars)
                rules, graphic_parts = _drawing_parts(page.get_drawings())
                unrotated = page.rect * page.derotation_matrix
                graphic_parts = [rect & unrotated for rect in graphic_parts
                                 if _touch(rect, unrotated)]
                tables = []
                grids = _table_geometry(rules)
                grid_bounds = [_union(cells) for cells in grids]
                for cells in grids:
                    record, markdown, consumed, rect = _table(cells, chars, rotation)
                    # Empty borders can be chart grids. Require audited cell text.
                    if consumed:
                        tables.append((record, markdown, consumed, rect))
                table_ids = set().union(*(entry[2] for entry in tables)) if tables else set()
                regions = [(pymupdf.Rect(info["bbox"]) & unrotated, "image")
                           for info in page.get_image_info()
                           if _touch(pymupdf.Rect(info["bbox"]), unrotated)]
                for rect in graphic_parts:
                    regions.append((rect, "graph"))
                for axis, coordinate, start, end, width in rules:
                    rect = (pymupdf.Rect(start, coordinate, end, coordinate) if axis == "h"
                            else pymupdf.Rect(coordinate, start, coordinate, end))
                    rect = _expand(rect, max(.5, width / 2)) & unrotated
                    if rect.is_empty:
                        continue
                    if any(rect in _expand(bounds, _TOL) for bounds in grid_bounds):
                        continue
                    # Horizontal underlines and separators are rules, not graph assets.
                    if axis == "h":
                        crossing = any(_touch(rect, line["rect"])
                                       and coordinate < line["rect"].y1 - line["size"] * .15
                                       for line in lines)
                        connected = any(_touch(rect, part, 2) for part in graphic_parts)
                        if not crossing and not connected:
                            continue
                    regions.append((rect, "graph"))
                regions = _merge_regions(regions)
                # Native labels within a graph, or a line altered by vector ink, belong
                # to the crop. Expand to its whole line so no misleading fragments escape.
                expanded = []
                for rect, kind in regions:
                    for line in lines:
                        if _touch(rect, line["rect"]):
                            rect |= line["rect"]
                    expanded.append((_expand(rect), kind))
                regions = _merge_regions(expanded)
                events = []
                graphic_ids = set()
                for rect, kind in regions:
                    displayed = rect * rotation
                    asset = _asset(image, displayed, page.rect, kind, stage)
                    if asset is None:
                        continue
                    result["assets"].append(asset)
                    graphic_ids.update(c["id"] for c in chars if _touch(rect, c["rect"]))
                    events.append((displayed, f"![{kind.capitalize()}](__ASSET_DIR__/{asset['png']})"))
                # A graph crossing a table must not coexist with editable misleading cells.
                for record, markdown, consumed, rect in tables:
                    if graphic_ids & consumed:
                        result["warnings"].append("Table overlaps a graphic; overlapping characters are kept only in the crop.")
                        remaining = [c for c in chars if c["id"] in consumed - graphic_ids]
                        events.extend((line["rect"] * rotation, _literal(line["text"])) for line in _lines(remaining))
                    else:
                        result["tables"].append(record)
                        events.append((rect * rotation, markdown))
                remaining = [c for c in chars if c["id"] not in table_ids | graphic_ids]
                for paragraph in _paragraphs(remaining):
                    events.append((paragraph["rect"] * rotation, paragraph["text"]))
                result["markdown"] = "\n\n".join(text for _, text in _reading_order(events))
            pages.append(result)
    return pages
