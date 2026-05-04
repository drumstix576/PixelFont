#!/usr/bin/env python3
"""Extract per-glyph tiles and metrics from a 1:1 tracker font atlas.

Input
-----
  font_atlas.png — 1:1 screenshot produced by the FontAtlas tracker extension.
                   Atlas is a 16-column grid of 14x14 cells containing ASCII
                   printable chars (32-126), rendered by BizHawk gui.drawText
                   at the tracker's native font (Franklin Gothic Medium 9pt).

Outputs
-------
  font_tiles/<name>.png   One annotated tile per glyph. Tile wraps the glyph
                          with a 1-px red bounding box (drawn 1 px beyond the
                          glyph on every side) and places a green marker pixel
                          on the left border at the font's baseline row.
  font_metrics.json       Structured metrics for every glyph: bbox within its
                          cell, width, height, ascent above baseline, descent
                          below baseline.

Approach
--------
  For each ASCII printable code, locate its 14x14 cell in the atlas, scan for
  non-black pixels to compute the tight bbox, and record the glyph bitmap.
  Derive a single baseline shared across the font from the most common bottom
  row of non-descender reference chars (A-Z, 0-9 excluding the few with
  descenders). The baseline row is the y immediately below the last glyph row
  of a non-descender.
"""

import json
from collections import Counter
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ATLAS_PATH = Path("font_atlas.png")
OUT_TILES = Path("font_tiles")
OUT_GROUPS = Path("font_groups")
OUT_JSON = Path("font_metrics.json")

# Per-char grid layout (matches FontAtlas.lua).
GRID_X0 = 0
GRID_Y0 = 0

# Pair-measurement section: each cell contains "cc" so we can measure the
# native advance by finding the distance between the two glyph clusters.
PAIR_CELL_W = 20
PAIR_CELL_H = 14
PAIR_Y0 = 86      # gap of 2 px below the 84-px grid
PAIR_COLS = 16

# Multi-char row tiles — share a common baseline across all glyphs in the row,
# so that editing the row's height adjusts every glyph's vertical extent at once.
GROUPS = {
    "digits":         "0123456789",
    "upper":          "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "lower":          "abcdefghijklmnopqrstuvwxyz",
    "punct_symbols":  "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
}

CELL_W = 14
CELL_H = 14
COLS = 16
FIRST_CHAR = 32
LAST_CHAR = 126

# Default advance width for glyphs with no drawn pixels (e.g. space).
# Franklin Gothic Medium 9pt renders a ~3 px space.
EMPTY_GLYPH_ADVANCE = 3

# Annotated-tile colors (RGBA).
BG_COLOR       = (0, 0, 0, 255)
GLYPH_COLOR    = (255, 255, 255, 255)
BBOX_COLOR     = (255, 80, 80, 255)      # 1-px bounding box, red
BASELINE_COLOR = (80, 255, 80, 255)      # baseline marker pixel, green


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

# Where the atlas's per-char drawText was called inside each cell. The
# native font's left bearing for a char is (bbox.min_x - DRAW_TEXT_X_OFFSET),
# i.e. how many pixels to the right of the caller's x the FG actually starts.
DRAW_TEXT_X_OFFSET = 2


def extract_cell(img, code):
    """Return (glyph_matrix, bbox) for the cell at ASCII `code`.

    glyph_matrix is the full 14x14 cell as 0/1 ints (1 = glyph pixel).
    bbox is (min_x, min_y, max_x, max_y) within the cell, or None for empty.
    """
    idx = code - FIRST_CHAR
    col = idx % COLS
    row = idx // COLS
    cx, cy = col * CELL_W, row * CELL_H

    matrix = [[0] * CELL_W for _ in range(CELL_H)]
    min_x, min_y = CELL_W, CELL_H
    max_x, max_y = -1, -1
    for y in range(CELL_H):
        for x in range(CELL_W):
            if img.getpixel((cx + x, cy + y)) > 0:
                matrix[y][x] = 1
                if x < min_x: min_x = x
                if y < min_y: min_y = y
                if x > max_x: max_x = x
                if y > max_y: max_y = y
    bbox = (min_x, min_y, max_x, max_y) if max_x >= 0 else None
    return matrix, bbox


def extract_pair_advance(img, code, atlas_fg_width=None):
    """Measure the native advance for `code` from the pair cell.

    Each cell contains `gui.drawText(cx+2, cy+1, "cc")`. Two copies of a
    char with FG width F laid out with advance A span a total of F + A
    columns (first copy at [0, F-1], second at [A, A+F-1]). So we measure
    the full ink span inside the cell and subtract the known single-char
    FG width — this sidesteps every run-merging edge case ('A' crossbars,
    '"' adjacent dots, '-' touching dashes, etc.).
    """
    if atlas_fg_width is None or atlas_fg_width <= 0:
        return None

    idx = code - FIRST_CHAR
    col = idx % PAIR_COLS
    row = idx // PAIR_COLS
    cx = col * PAIR_CELL_W
    cy = PAIR_Y0 + row * PAIR_CELL_H

    w, h = img.size
    if cy + PAIR_CELL_H > h or cx + PAIR_CELL_W > w:
        return None

    min_x, max_x = None, None
    for x in range(PAIR_CELL_W):
        for y in range(PAIR_CELL_H):
            if img.getpixel((cx + x, cy + y)) > 0:
                if min_x is None or x < min_x:
                    min_x = x
                if max_x is None or x > max_x:
                    max_x = x
                break
    if min_x is None:
        return None
    span = max_x - min_x + 1
    return span - atlas_fg_width


def derive_baseline_y(bboxes):
    """Find the baseline y (row) within the 14x14 cell.

    Non-descender uppercase/digit glyphs all rest on the baseline, so their
    bbox.max_y + 1 is the baseline row. Take the mode across the reference
    set to survive any stray outliers.
    """
    refs = "ABCDEFHIKLMNORSTUVWXZ0123456789"  # omit G, J, P, Q, Y (potential descenders or loops)
    bottoms = [bboxes[c][3] for c in refs if bboxes.get(c) is not None]
    if not bottoms:
        raise RuntimeError("No reference glyphs found to derive baseline")
    return Counter(bottoms).most_common(1)[0][0] + 1


# ---------------------------------------------------------------------------
# Tile rendering
# ---------------------------------------------------------------------------

def make_tile(matrix, bbox, baseline_y_in_cell):
    """Return an annotated RGBA tile for a glyph.

    Tile layout (rings from outside in):
      row 0 / last row, col 0 / last col : bbox border
      1-px buffer ring                   : reserved for shadow pixels
      glyph interior                     : the foreground pixels

    Total size: (gw + 4) x (gh + 4). Glyph pixels are placed at offset (2, 2).
    A single green pixel on the left border marks the baseline row.
    """
    bx, by, bmx, bmy = bbox
    gw = bmx - bx + 1
    gh = bmy - by + 1
    tw = gw + 4
    th = gh + 4

    tile = Image.new("RGBA", (tw, th), BG_COLOR)
    # Bbox perimeter (outermost ring).
    for i in range(tw):
        tile.putpixel((i, 0), BBOX_COLOR)
        tile.putpixel((i, th - 1), BBOX_COLOR)
    for j in range(th):
        tile.putpixel((0, j), BBOX_COLOR)
        tile.putpixel((tw - 1, j), BBOX_COLOR)
    # Glyph pixels (offset by +2,+2 to sit inside border + shadow buffer).
    for y in range(gh):
        for x in range(gw):
            if matrix[by + y][bx + x]:
                tile.putpixel((x + 2, y + 2), GLYPH_COLOR)
    # Baseline marker on the left border. Tile row 0 corresponds to cell row
    # (by - 2), so baseline tile row = baseline_y_in_cell - by + 2.
    bl_tile_row = baseline_y_in_cell - by + 2
    if 0 <= bl_tile_row < th:
        tile.putpixel((0, bl_tile_row), BASELINE_COLOR)

    return tile


# ---------------------------------------------------------------------------
# Group row rendering
# ---------------------------------------------------------------------------

def make_group_tile(chars, matrices, bboxes, baseline_y_in_cell):
    """Render a row of glyphs on a shared baseline.

    Layout, per glyph left-to-right: 1-px left buffer, glyph pixels, 1-px
    right buffer. Adjacent glyphs are separated by a 1-px vertical border
    in the bbox color so each glyph's advance width is visually explicit.

    Vertically the row is sized to fit the tallest ascent and deepest
    descent in the group, with 1-px shadow buffers top and bottom and the
    1-px bbox border on the outside. Every glyph's baseline-row coincides
    with the row's baseline. A green baseline marker is placed on both
    outer borders and on every inter-glyph separator.
    """
    # Derive shared vertical extents from present glyphs only.
    present = [c for c in chars if bboxes.get(c) is not None]
    if not present:
        raise RuntimeError("group has no renderable glyphs")
    max_ascent = max(baseline_y_in_cell - bboxes[c][1] for c in present)
    max_descent = max(
        max(0, bboxes[c][3] - (baseline_y_in_cell - 1)) for c in present
    )

    # Row layout, vertically:
    #   row 0                        : top border
    #   row 1                        : top shadow buffer
    #   rows 2 .. 1+max_ascent       : ascent area
    #   rows 2+max_ascent .. 1+max_ascent+max_descent : descent area
    #   row 2+max_ascent+max_descent : bottom shadow buffer
    #   row 3+max_ascent+max_descent : bottom border
    total_h = max_ascent + max_descent + 4
    baseline_row = 2 + max_ascent   # first row below the last ascent row

    sub_widths = []
    for c in chars:
        bbox = bboxes.get(c)
        if bbox is None:
            # Empty glyph (space): reserve its advance width + L/R buffers.
            sub_widths.append(EMPTY_GLYPH_ADVANCE + 2)
        else:
            gw = bbox[2] - bbox[0] + 1
            sub_widths.append(gw + 2)
    # +2 for outer L/R border, +(N-1) for inter-glyph separators.
    total_w = sum(sub_widths) + 2 + max(0, len(chars) - 1)

    tile = Image.new("RGBA", (total_w, total_h), BG_COLOR)
    # Outer bbox border.
    for i in range(total_w):
        tile.putpixel((i, 0), BBOX_COLOR)
        tile.putpixel((i, total_h - 1), BBOX_COLOR)
    for j in range(total_h):
        tile.putpixel((0, j), BBOX_COLOR)
        tile.putpixel((total_w - 1, j), BBOX_COLOR)
    # Baseline markers on left + right outer borders.
    if 0 <= baseline_row < total_h:
        tile.putpixel((0, baseline_row), BASELINE_COLOR)
        tile.putpixel((total_w - 1, baseline_row), BASELINE_COLOR)

    # Draw each glyph, followed by a 1-px separator column (except after the
    # last glyph, where the right outer border serves that purpose).
    x_cursor = 1  # skip left border
    for i, (c, sub_w) in enumerate(zip(chars, sub_widths)):
        bbox = bboxes.get(c)
        if bbox is not None:
            bx, by, bmx, bmy = bbox
            gw = bmx - bx + 1
            gh = bmy - by + 1
            ascent = baseline_y_in_cell - by
            glyph_top = baseline_row - ascent
            glyph_left = x_cursor + 1  # skip per-glyph left buffer
            for gy in range(gh):
                for gx in range(gw):
                    if matrices[c][by + gy][bx + gx]:
                        tile.putpixel(
                            (glyph_left + gx, glyph_top + gy), GLYPH_COLOR
                        )
        x_cursor += sub_w
        if i < len(chars) - 1:
            for j in range(total_h):
                tile.putpixel((x_cursor, j), BBOX_COLOR)
            if 0 <= baseline_row < total_h:
                tile.putpixel((x_cursor, baseline_row), BASELINE_COLOR)
            x_cursor += 1

    return tile


# ---------------------------------------------------------------------------
# Filesystem naming
# ---------------------------------------------------------------------------

_SAFE_NAMES = {
    ' ': 'space',   '!': 'excl',     '"': 'quot',       '#': 'hash',
    '$': 'dollar',  '%': 'percent',  '&': 'amp',        "'": 'apos',
    '(': 'lparen',  ')': 'rparen',   '*': 'asterisk',   '+': 'plus',
    ',': 'comma',   '-': 'minus',    '.': 'period',     '/': 'slash',
    ':': 'colon',   ';': 'semi',     '<': 'lt',         '=': 'eq',
    '>': 'gt',      '?': 'question', '@': 'at',         '[': 'lbracket',
    '\\': 'backslash', ']': 'rbracket', '^': 'caret',   '_': 'underscore',
    '`': 'backtick', '{': 'lbrace',   '|': 'pipe',      '}': 'rbrace',
    '~': 'tilde',
}


def tile_filename(c):
    if c in _SAFE_NAMES:
        return _SAFE_NAMES[c]
    if c.isalpha():
        return ("U_" if c.isupper() else "L_") + c.lower()
    if c.isdigit():
        return "digit_" + c
    return str(ord(c))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    img = Image.open(ATLAS_PATH).convert("L")
    matrices = {}
    bboxes = {}
    advances = {}
    for code in range(FIRST_CHAR, LAST_CHAR + 1):
        c = chr(code)
        matrix, bbox = extract_cell(img, code)
        matrices[c] = matrix
        bboxes[c] = bbox
        atlas_fg_w = (bbox[2] - bbox[0] + 1) if bbox is not None else 0
        adv = extract_pair_advance(img, code, atlas_fg_width=atlas_fg_w)
        if adv is not None:
            advances[c] = adv

    baseline_y = derive_baseline_y(bboxes)

    OUT_TILES.mkdir(exist_ok=True)
    metrics = {
        "atlas": str(ATLAS_PATH),
        "cell_size": [CELL_W, CELL_H],
        "baseline_y_in_cell": baseline_y,
        "first_char": FIRST_CHAR,
        "last_char": LAST_CHAR,
        "glyphs": {},
    }

    for code in range(FIRST_CHAR, LAST_CHAR + 1):
        c = chr(code)
        bbox = bboxes[c]
        entry = {}
        if bbox is None:
            entry["empty"] = True
            entry["width"] = EMPTY_GLYPH_ADVANCE
            entry["height"] = 0
            entry["ascent"] = 0
            entry["descent"] = 0
        else:
            bx, by, bmx, bmy = bbox
            gw = bmx - bx + 1
            gh = bmy - by + 1
            entry["bbox"] = [bx, by, bmx, bmy]
            entry["width"] = gw
            entry["height"] = gh
            entry["ascent"] = baseline_y - by
            entry["descent"] = max(0, bmy - (baseline_y - 1))
            # Native left bearing: offset from the caller's x to the FG's
            # leftmost column. This varies per char — '1', '<', '>' have
            # bearing 2; most letters have 1; 'j' has 0; '|' has 3.
            entry["left_bearing"] = bx - DRAW_TEXT_X_OFFSET
            tile = make_tile(matrices[c], bbox, baseline_y)
            tile.save(OUT_TILES / f"{tile_filename(c)}.png")
        metrics["glyphs"][c] = entry

    # Group row tiles — NEVER overwrite existing ones. The group PNGs are
    # hand-edited by the user (foreground + shadow pixels) and are the
    # source of truth for shapes. Re-running this extractor after a fresh
    # atlas capture must not clobber that work. Only emit groups for names
    # that don't already have a file on disk (first-run bootstrap only).
    OUT_GROUPS.mkdir(exist_ok=True)
    regenerated = []
    for name, chars in GROUPS.items():
        out_path = OUT_GROUPS / f"{name}.png"
        if out_path.exists():
            continue
        row = make_group_tile(list(chars), matrices, bboxes, baseline_y)
        row.save(out_path)
        regenerated.append(name)

    # Attach advances to metrics (from the pair-measurement atlas section).
    for c, adv in advances.items():
        if c in metrics["glyphs"]:
            metrics["glyphs"][c]["advance"] = adv

    OUT_JSON.write_text(json.dumps(metrics, indent=2))
    print(f"Baseline y (in cell) = {baseline_y}")
    print(f"Wrote {sum(1 for e in metrics['glyphs'].values() if not e.get('empty'))} "
          f"per-char tiles to {OUT_TILES}/")
    if regenerated:
        print(f"Bootstrapped group rows: {regenerated}")
    else:
        print(f"Preserved existing group rows in {OUT_GROUPS}/ (none overwritten)")
    print(f"Measured advances: {len(advances)} / {LAST_CHAR - FIRST_CHAR + 1} chars")
    print(f"Metrics: {OUT_JSON}")


if __name__ == "__main__":
    main()
