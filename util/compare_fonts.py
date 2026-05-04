#!/usr/bin/env python3
"""Build a side-by-side comparison atlas: standard font vs pixel font.

For each ASCII printable char, renders:
  * LEFT grid — the standard font as captured in font_atlas.png (what
    BizHawk's gui.drawText produces for Franklin Gothic Medium 9pt).
  * RIGHT grid — the pixel font reconstructed from font_groups/*.png with
    the same drawText (x, y) and the same X_LEADING/Y_LEADING offsets the
    runtime applies. FG in bright white, shadow in mid-gray.

Both grids use identical 14x14 cell layout, so any misalignment between
what the standard font produces and what the pixel font produces lines up
as an obvious offset between the two cells when viewed side-by-side.

A tight red 1-px bbox is drawn one pixel outside each glyph's FG pixels
(shadow ignored), so the exact FG extent per char is visible in both
grids. If the FG-top or FG-left of a given char differs between the two
grids, it jumps out immediately.
"""

from pathlib import Path

from PIL import Image

ATLAS_PATH = Path("font_atlas.png")
GROUPS_DIR = Path("font_groups")
OUTPUT = Path("font_comparison.png")

CELL_W = 14
CELL_H = 14
COLS = 16
FIRST_CHAR = 32
LAST_CHAR = 126

# Runtime constant: native font baseline sits at drawText_y + 9.
NATIVE_BASELINE_Y = 9
DEFAULT_LEFT_BEARING = 1

# Must match extract_tracker_font.py.
GROUPS = {
    "digits":         "0123456789",
    "upper":          "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "lower":          "abcdefghijklmnopqrstuvwxyz",
    "punct_symbols":  "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
}

# Group PNG palette.
GROUP_FG       = (255, 255, 255, 255)
GROUP_SHADOW   = (192, 192, 192, 255)
GROUP_BBOX     = (255, 80,  80,  255)
GROUP_BASELINE = (80,  255, 80,  255)
GROUP_BG       = (0,   0,   0,   255)

# Output colors.
BG_COLOR         = (0,   0,   0,   255)
OUTER_BG         = (30,  30,  30,  255)
FG_COLOR         = (255, 255, 255, 255)
SHADOW_COLOR     = (128, 128, 128, 255)   # mid-gray stand-in for 50% alpha
BBOX_COLOR       = (255, 80,  80,  255)
GAP_COLOR        = (60,  60,  60,  255)
LABEL_STD        = "STANDARD"
LABEL_PIX        = "PIXEL"


# ---------------------------------------------------------------------------
# Load pixel font from group PNGs (mirrors generate_tracker_font.py)
# ---------------------------------------------------------------------------

def parse_group(png_path, chars):
    img = Image.open(png_path).convert("RGBA")
    w, h = img.size

    def is_sep(x):
        for y in range(h):
            p = img.getpixel((x, y))
            if p != GROUP_BBOX and p != GROUP_BASELINE:
                return False
        return True

    seps = [x for x in range(w) if is_sep(x)]
    baseline_row = None
    for y in range(h):
        if img.getpixel((0, y)) == GROUP_BASELINE:
            baseline_row = y
            break
    if baseline_row is None:
        raise RuntimeError(f"{png_path}: no baseline marker")

    glyphs = {}
    for i, c in enumerate(chars):
        left = seps[i] + 1
        right = seps[i + 1] - 1
        matrix = []
        for y in range(1, h - 1):
            row = []
            for x in range(left, right + 1):
                p = img.getpixel((x, y))
                if p == GROUP_FG:
                    row.append(1)
                elif p == GROUP_SHADOW:
                    row.append(2)
                else:
                    row.append(0)
            matrix.append(row)
        glyphs[c] = {
            "matrix": matrix,
            "baseline_in_matrix": baseline_row - 1,
            "matrix_height": h - 2,
        }
    return glyphs


def strip_leading_empty_cols(matrix):
    if not matrix or not matrix[0]:
        return matrix
    n = len(matrix[0])
    first = 0
    while first < n and all(row[first] == 0 for row in matrix):
        first += 1
    if first == 0:
        return matrix
    return [row[first:] for row in matrix]


def load_pixel_font():
    """Return (padded_matrices, font_ascent) mimicking the generator.

    Baseline-aligned, padded to the max FG ascent/descent across all
    groups (NOT the raw PNG matrix height — we exclude buffer rows that
    the native font doesn't use, so each line occupies the same vertical
    extent as gui.drawText).
    """
    groups_data = {}
    for name, chars in GROUPS.items():
        per_char = parse_group(GROUPS_DIR / f"{name}.png", list(chars))
        # Normalize: one group dict with .glyphs, .baseline_in_matrix
        first = next(iter(per_char.values()))
        groups_data[name] = {
            "glyphs": {c: d["matrix"] for c, d in per_char.items()},
            "baseline_in_matrix": first["baseline_in_matrix"],
        }

    def max_fg_descent(g):
        bl = g["baseline_in_matrix"]
        md = 0
        for m in g["glyphs"].values():
            for y, row in enumerate(m):
                if y >= bl and any(v == 1 for v in row):
                    md = max(md, y - bl + 1)
        return md

    def max_fg_ascent(g):
        bl = g["baseline_in_matrix"]
        ma = 0
        for m in g["glyphs"].values():
            for y, row in enumerate(m):
                if y < bl and any(v == 1 for v in row):
                    ma = max(ma, bl - y)
        return ma

    global_above = max(max_fg_ascent(g) for g in groups_data.values())
    global_below = max(max_fg_descent(g) for g in groups_data.values())

    padded = {}
    for g in groups_data.values():
        bl = g["baseline_in_matrix"]
        for c, matrix in g["glyphs"].items():
            width = len(matrix[0]) if matrix else 0
            new = []
            for rel in range(-global_above, global_below):
                src = bl + rel
                if 0 <= src < len(matrix):
                    new.append(matrix[src])
                else:
                    new.append([0] * width)
            padded[c] = new

    trimmed = {c: strip_leading_empty_cols(m) for c, m in padded.items()}
    return trimmed, global_above


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_pixel_matrix(img, matrix, ox, oy):
    if not matrix:
        return
    w, h = img.size
    for y, row in enumerate(matrix):
        py = oy + y
        if py < 0 or py >= h:
            continue
        for x, v in enumerate(row):
            if v == 0:
                continue
            px = ox + x
            if px < 0 or px >= w:
                continue
            img.putpixel((px, py), FG_COLOR if v == 1 else SHADOW_COLOR)


def find_fg_bbox(img, cx, cy, cw, ch, fg_only=True):
    """Tight bbox of foreground pixels within a cell (relative to cx, cy)."""
    min_x, min_y, max_x, max_y = cw, ch, -1, -1
    for y in range(ch):
        py = cy + y
        if py >= img.size[1]:
            break
        for x in range(cw):
            px = cx + x
            if px >= img.size[0]:
                break
            p = img.getpixel((px, py))
            if fg_only:
                is_fg = p == FG_COLOR or p == GROUP_FG or (
                    isinstance(p, tuple)
                    and p[0] > 200 and p[1] > 200 and p[2] > 200
                )
            else:
                is_fg = any(ch_ > 30 for ch_ in p[:3]) if isinstance(p, tuple) else p > 30
            if is_fg:
                if x < min_x: min_x = x
                if y < min_y: min_y = y
                if x > max_x: max_x = x
                if y > max_y: max_y = y
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def draw_bbox(img, cx, cy, bbox, color):
    """1-px bbox drawn one pixel OUTSIDE the glyph's FG extent."""
    bx, by, bmx, bmy = bbox
    bx -= 1; by -= 1; bmx += 1; bmy += 1
    w, h = img.size
    for x in range(bx, bmx + 1):
        for yy in (by, bmy):
            px, py = cx + x, cy + yy
            if 0 <= px < w and 0 <= py < h:
                img.putpixel((px, py), color)
    for y in range(by, bmy + 1):
        for xx in (bx, bmx):
            px, py = cx + xx, cy + y
            if 0 <= px < w and 0 <= py < h:
                img.putpixel((px, py), color)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    atlas = Image.open(ATLAS_PATH).convert("RGBA")
    grid_rows = 6
    grid_w = COLS * CELL_W
    grid_h = grid_rows * CELL_H

    # Standard grid = crop the atlas's per-char grid. The atlas may have been
    # captured wider (for the pair section) — we only want the top-left
    # COLS*CELL_W x 6*CELL_H region.
    std_grid = Image.new("RGBA", (grid_w, grid_h), BG_COLOR)
    std_grid.paste(atlas.crop((0, 0, grid_w, grid_h)), (0, 0))

    # Pixel grid — render each char at (caller_x + left_bearing, caller_y + y_leading)
    # where caller_x = cx + 2 and caller_y = cy + 1 to match the atlas's drawText
    # call position. y_leading compensates for where the matrix baseline sits.
    pixel_grid = Image.new("RGBA", (grid_w, grid_h), BG_COLOR)
    glyphs, baseline_in_font = load_pixel_font()
    y_leading = NATIVE_BASELINE_Y - baseline_in_font

    # Read per-char left bearings from metrics.
    import json
    metrics = json.loads(Path("font_metrics.json").read_text())
    bearings = {c: e.get("left_bearing", DEFAULT_LEFT_BEARING)
                for c, e in metrics["glyphs"].items()}

    for code in range(FIRST_CHAR, LAST_CHAR + 1):
        c = chr(code)
        if c not in glyphs:
            continue
        matrix = glyphs[c]
        idx = code - FIRST_CHAR
        col = idx % COLS
        row = idx // COLS
        cx = col * CELL_W
        cy = row * CELL_H
        lb = bearings.get(c, DEFAULT_LEFT_BEARING)
        render_pixel_matrix(
            pixel_grid, matrix, cx + 2 + lb, cy + 1 + y_leading
        )

    # Tight bboxes around FG in both grids.
    for code in range(FIRST_CHAR, LAST_CHAR + 1):
        idx = code - FIRST_CHAR
        col = idx % COLS
        row = idx // COLS
        cx = col * CELL_W
        cy = row * CELL_H
        for grid in (std_grid, pixel_grid):
            bbox = find_fg_bbox(grid, cx, cy, CELL_W, CELL_H)
            if bbox is not None:
                draw_bbox(grid, cx, cy, bbox, BBOX_COLOR)

    # Compose side-by-side with a gap and outer margin.
    margin = 4
    gap = 8
    out_w = margin * 2 + grid_w * 2 + gap
    out_h = margin * 2 + grid_h
    out = Image.new("RGBA", (out_w, out_h), OUTER_BG)
    out.paste(std_grid, (margin, margin))
    out.paste(pixel_grid, (margin + grid_w + gap, margin))
    # Simple color bar in the gap to visually separate the two.
    for y in range(margin, margin + grid_h):
        for x in range(margin + grid_w, margin + grid_w + gap):
            out.putpixel((x, y), GAP_COLOR)

    out.save(OUTPUT)
    # Also emit an upscaled version for easier visual inspection.
    SCALE = 4
    up = out.resize((out_w * SCALE, out_h * SCALE), Image.NEAREST)
    up_path = OUTPUT.with_stem(OUTPUT.stem + f"_{SCALE}x")
    up.save(up_path)
    print(f"Wrote {OUTPUT} ({out_w}x{out_h})")
    print(f"Wrote {up_path} ({up.size[0]}x{up.size[1]})")
    print(f"Left grid:  standard font (from {ATLAS_PATH})")
    print(f"Right grid: pixel font (from {GROUPS_DIR}/)")


if __name__ == "__main__":
    main()
