#!/usr/bin/env python3
"""Generate PixelFont.lua: pixel-exact font + FRLG-style icon drop shadows.

Inputs:
  font_groups/{digits,upper,lower,punct_symbols,extras}.png    text glyph rows
  font_groups/icons.png + icons_manifest.json                  icon shadow markup
  icon_inventory.json                                          source icon matrices

Outputs:
  tracker/extensions/PixelFont.lua                             unified extension

Pixel palette in the group PNGs:
    (0, 0, 0)       background / transparent
    (255, 80, 80)   bbox border / inter-glyph separator
    (80, 255, 80)   baseline marker (on borders/separators only)
    (255, 255, 255) glyph foreground
    (192, 192, 192) glyph shadow

Glyph matrices encode 0=transparent, 1=foreground, 2=shadow. At render time
the shadow color is derived from the foreground color with 50% alpha, so
the shadow matches whatever text color the caller specified.

For icons, the matrix uses 0..N for original color values and N+1 for shadow.
The icon-rendering wrapper extends the colorList with shadowColor(colorList[1])
at index N+1 so the existing Drawing.drawImageAsPixels paints those pixels.

Extension lifecycle:
  startup()      Override Drawing.drawText immediately. Mutate Constants.PixelImages
                 with shadowed copies (gated by RogueMon Options toggle).
  afterRedraw()  On the first frame, install the Drawing.drawImageAsPixels
                 wrapper. Deferred so that any other extension's drawing
                 override (e.g. RoguemonExpansion's RLE batcher) has already
                 completed startup — Lua's pairs() iteration over the
                 ExtensionLibrary is non-deterministic, so we can't rely on
                 alphabetical or any other startup order.
"""

import json
from pathlib import Path

import jinja2
from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROUPS_DIR =     Path("font_groups")
METRICS_PATH =   Path("font_metrics.json")
ICONS_PNG =      Path("font_groups/icons.png")
ICONS_MANIFEST = Path("font_groups/icons_manifest.json")
ICON_INVENTORY = Path("icon_inventory.json")
OUTPUT =         Path("PixelFont.lua")
TEMPLATES_DIR =  Path(__file__).parent / "templates"
TEMPLATE_NAME =  "pixel_font.lua.j2"

# Source path constants for icon classification (Constants.PixelImages vs
# RogueMon-extension-local matrices that need opt-in by the consuming files).
CONSTANTS_LUA_PATH = "/root/roguemon/claude/tracker/ironmon_tracker/Constants.lua"

# Must match extract_tracker_font.py for groups bootstrapped from the atlas.
# `extras` is hand-authored (chars absent from the BizHawk atlas) so it lives
# only here; the parser treats it identically to the others.
GROUPS = {
    "digits":         "0123456789",
    "upper":          "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "lower":          "abcdefghijklmnopqrstuvwxyz",
    "punct_symbols":  "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
    "extras":         "é×’½⅔–",  # é × ’ ½ ⅔ –
}

# Left bearings for hand-authored chars (offset from caller's x to the glyph's
# leftmost FG column). Measured from the ExtrasAtlas screenshot, which renders
# each char through gui.drawText so the values match BizHawk's native font.
# Widths come from fg_only_width() against the PNG and need no override even
# when the user enlarges a glyph beyond its native extent (e.g. the × widened
# from 3 to 5 px FG): the renderer's `xoffset += FontWidths[c] + 1` formula
# advances by the authored width plus one shadow column automatically.
EXTRAS_LEFT_BEARINGS = {
    "é": 1,  # é  — match plain 'e' (bearing=1) so "Pokémon" doesn't get a gap
    "×": 1,  # ×
    "’": 1,  # ’ — sits close to the previous letter (e.g. "target’s")
    "½": 1,  # ½
    "⅔": 1,  # ⅔
    "–": 1,  # –
}

# Chars absent from any group (handled as width-only, no matrix). Matches
# the tracker's Constants.Char[" "].width so Utils.calcWordPixelLength and
# our rendering agree on the advance.
SPACE_WIDTH = 1

# PNG palette.
BG       = (0,   0,   0,   255)
BBOX     = (255, 80,  80,  255)
BASELINE = (80,  255, 80,  255)
FG       = (255, 255, 255, 255)
SHADOW   = (192, 192, 192, 255)


# ---------------------------------------------------------------------------
# PNG parsing
# ---------------------------------------------------------------------------

def is_separator_col(img, x, h):
    """Column is a separator if every pixel is red (bbox) or green (baseline)."""
    for y in range(h):
        p = img.getpixel((x, y))
        if p != BBOX and p != BASELINE:
            return False
    return True


def find_baseline_row(img, h):
    """The baseline is the row where the leftmost column has the green marker."""
    for y in range(h):
        if img.getpixel((0, y)) == BASELINE:
            return y
    raise RuntimeError("no baseline marker on left border")


def classify_pixel(p):
    if p == FG:
        return 1
    if p == SHADOW:
        return 2
    return 0


def parse_group(png_path, chars):
    img = Image.open(png_path).convert("RGBA")
    w, h = img.size

    baseline_row = find_baseline_row(img, h)
    sep_cols = [x for x in range(w) if is_separator_col(img, x, h)]
    expected = len(chars) + 1
    if len(sep_cols) != expected:
        raise RuntimeError(
            f"{png_path}: expected {expected} separator columns, found {len(sep_cols)}"
        )

    glyphs = {}
    for i, c in enumerate(chars):
        left = sep_cols[i] + 1
        right = sep_cols[i + 1] - 1
        matrix = []
        for y in range(1, h - 1):
            row = [classify_pixel(img.getpixel((x, y))) for x in range(left, right + 1)]
            matrix.append(row)
        glyphs[c] = matrix

    return {
        "glyphs": glyphs,
        # Matrix-row index of the baseline row (matrix row 0 == PNG row 1).
        "baseline_in_matrix": baseline_row - 1,
        "matrix_height": h - 2,
    }


# ---------------------------------------------------------------------------
# Matrix trimming and width measurement
# ---------------------------------------------------------------------------

def strip_leading_empty_cols(matrix):
    """Remove all-zero columns from the left edge of a glyph matrix.

    In the editor PNGs every glyph carries a 1-px left buffer so the user
    has room to add shadow pixels there if needed. In practice FRLG-style
    shadows only extend right and down, so the left buffer is always empty
    and we drop it before emitting so the glyph's leftmost FG pixel lines
    up with the caller's x coordinate.
    """
    if not matrix or not matrix[0]:
        return matrix
    n_cols = len(matrix[0])
    first = 0
    while first < n_cols and all(row[first] == 0 for row in matrix):
        first += 1
    if first == 0:
        return matrix
    return [row[first:] for row in matrix]


def fg_only_width(matrix):
    """Advance width of the glyph: extent of its foreground pixels only.

    Buffer columns and trailing shadow columns don't count toward the
    glyph's own dimension. The renderer adds a fixed inter-char gap
    separately, which is where the shadow naturally sits at render time.
    """
    fg_cols = []
    for row in matrix:
        for c, v in enumerate(row):
            if v == 1:
                fg_cols.append(c)
    if not fg_cols:
        return 0
    return max(fg_cols) - min(fg_cols) + 1


# ---------------------------------------------------------------------------
# Align baselines across groups
# ---------------------------------------------------------------------------

def _max_fg_descent(group):
    """Max rows-below-baseline that contain content (FG or shadow).

    The group PNG has all-zero buffer rows below descenders that we must NOT
    include in the padded matrix. By counting any non-zero pixel (FG or
    shadow), we still drop those empty buffer rows but preserve the shadow
    row that sits one below the deepest FG — without it, descender glyphs
    like j, g, p, ( and ) lose their bottom drop-shadow strip when the pad
    range clips before reaching it.
    """
    bl = group["baseline_in_matrix"]
    max_d = 0
    for matrix in group["glyphs"].values():
        for y, row in enumerate(matrix):
            if y < bl:
                continue
            if any(v != 0 for v in row):
                max_d = max(max_d, y - bl + 1)
    return max_d


def _max_fg_ascent(group):
    """Max rows-above-baseline that contain an FG pixel, across group chars."""
    bl = group["baseline_in_matrix"]
    max_a = 0
    for matrix in group["glyphs"].values():
        for y, row in enumerate(matrix):
            if y >= bl:
                continue
            if any(v == 1 for v in row):
                max_a = max(max_a, bl - y)
    return max_a


def pad_to_common_height(groups_data):
    """Return (padded_glyphs, font_height, baseline_in_font).

    Every glyph matrix is padded top/bottom so its baseline row lands on a
    common row index across the whole font. The total height is derived
    from the *FG extent* of every glyph — NOT the raw group-PNG matrix
    height, which includes editor buffer rows the native font doesn't use.
    Shadow pixels below the deepest FG descender are preserved only when
    they lie within global_below; anything below gets trimmed so each line
    takes the same vertical space as native gui.drawText would.
    """
    global_above = max(_max_fg_ascent(g) for g in groups_data.values())
    global_below = max(_max_fg_descent(g) for g in groups_data.values())
    font_height = global_above + global_below
    baseline_in_font = global_above

    padded = {}
    for group_name, data in groups_data.items():
        bl = data["baseline_in_matrix"]
        for c, matrix in data["glyphs"].items():
            width = len(matrix[0]) if matrix else 0
            new_matrix = []
            # Top padding rows (above anything in this char).
            for _ in range(global_above - bl):
                new_matrix.append([0] * width)
            # Clip any leading buffer rows from the group matrix — we're
            # padding from the baseline, not the group's matrix top.
            for row in matrix[:bl]:
                # Only include rows within global_above rows of baseline.
                pass
            # Actually, simplest: take the matrix rows in baseline-relative
            # range (-global_above .. global_below-1), padding with zeros
            # outside the matrix's own range.
            new_matrix = []
            for rel in range(-global_above, global_below):
                src_row = bl + rel
                if 0 <= src_row < len(matrix):
                    new_matrix.append(matrix[src_row])
                else:
                    new_matrix.append([0] * width)
            padded[c] = new_matrix

    return padded, font_height, baseline_in_font


# ---------------------------------------------------------------------------
# Lua emit
# ---------------------------------------------------------------------------



def lua_matrix(matrix, indent="      "):
    lines = []
    for row in matrix:
        lines.append(indent + "{" + ",".join(str(v) for v in row) + "},")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Icon shadow decoding
# ---------------------------------------------------------------------------

def decode_shadowed_icons():
    """Read icons.png + manifest, decode shadow positions per cell, combine
    with the source matrix from the icon inventory. Returns a list of dicts
    with keys: name, source, matrix, shadow_value.
    """
    if not ICONS_PNG.exists() or not ICONS_MANIFEST.exists():
        return []
    img = Image.open(ICONS_PNG).convert("RGBA")
    manifest = json.loads(ICONS_MANIFEST.read_text())
    inv = json.loads(ICON_INVENTORY.read_text())
    src_matrices = {i["name"]: i["matrix"] for i in inv}

    out = []
    for row in manifest["rows"]:
        for entry in row["icons"]:
            name = entry["name"]
            cx, cy = entry["cell_x"], entry["cell_y"]
            cw, ch = entry["cell_w"], entry["cell_h"]
            mw, mh = entry["matrix_w"], entry["matrix_h"]
            max_v = entry["max_value"]
            src = src_matrices[name]
            ix, iy = cx + 1, cy + 1

            shadows = []
            for py in range(cy, cy + ch):
                for px in range(cx, cx + cw):
                    if img.getpixel((px, py)) == SHADOW:
                        shadows.append((px - ix, py - iy))
            valid = [(rx, ry) for (rx, ry) in shadows if rx >= 0 and ry >= 0]
            if not valid:
                continue

            out_w, out_h = mw, mh
            for (rx, ry) in valid:
                if rx >= out_w: out_w = rx + 1
                if ry >= out_h: out_h = ry + 1

            shadow_value = max_v + 1
            m = [[0] * out_w for _ in range(out_h)]
            for ry in range(mh):
                for rx in range(mw):
                    m[ry][rx] = src[ry][rx]
            for (rx, ry) in valid:
                if 0 <= rx < out_w and 0 <= ry < out_h and m[ry][rx] == 0:
                    m[ry][rx] = shadow_value

            out.append({
                "name": name,
                "source": entry["source"],
                "matrix": m,
                "shadow_value": shadow_value,
            })
    return out


def render_icon_tables(icons):
    """Return Lua source for the two icon shadow data tables, attached to
    `self` so the lifecycle code can iterate them generically.
    """
    constants = sorted([i for i in icons if i["source"] == CONSTANTS_LUA_PATH],
                       key=lambda x: x["name"])
    external  = sorted([i for i in icons if i["source"] != CONSTANTS_LUA_PATH],
                       key=lambda x: x["name"])
    parts = []

    parts.append("  -- Shadowed matrices applied to Constants.PixelImages.<NAME>.")
    parts.append("  self.ShadowedIcons = {")
    for s in constants:
        parts.append(f"    {s['name']} = {{")
        parts.append(f"      shadow_value = {s['shadow_value']},")
        parts.append(f"      matrix = {{")
        parts.append(lua_matrix(s["matrix"], indent="        "))
        parts.append(f"      }},")
        parts.append(f"    }},")
    parts.append("  }")

    parts.append("")
    parts.append("  -- Shadowed matrices for RogueMon-extension-local pixel matrices.")
    parts.append("  -- Exposed via _G.Roguemon.ShadowedIcons for opt-in by consuming files.")
    parts.append("  self.ExternalShadowedIcons = {")
    for s in external:
        parts.append(f"    {s['name']} = {{")
        parts.append(f"      shadow_value = {s['shadow_value']},")
        parts.append(f"      matrix = {{")
        parts.append(lua_matrix(s["matrix"], indent="        "))
        parts.append(f"      }},")
        parts.append(f"    }},")
    parts.append("  }")

    return "\n".join(parts)


def render_font_table(glyphs, emission_order):
    parts = []
    for c in emission_order:
        if c not in glyphs:
            continue
        parts.append(f"    [{lua_char_literal(c)}] = {{")
        parts.append(lua_matrix(glyphs[c]))
        parts.append("    },")
    return "\n".join(parts)


def render_widths_table(widths, emission_order):
    parts = []
    for c in emission_order:
        if c not in widths:
            continue
        parts.append(f"    [{lua_char_literal(c)}] = {widths[c]},")
    return "\n".join(parts)


def render_left_bearing_table(left_bearings, emission_order):
    parts = []
    for c in emission_order:
        if c not in left_bearings:
            continue
        parts.append(f"    [{lua_char_literal(c)}] = {left_bearings[c]},")
    return "\n".join(parts)


def emit_lua(glyphs, widths, left_bearings, font_height, baseline_in_font,
             font_size, default_width, output_path, shadowed_icons=()):
    # Line spacing matches the tracker's Constants.SCREEN.LINESPACING (11)
    # regardless of our trimmed matrix height — multiple lines must land on
    # the tracker's expected y offsets so surrounding UI stays aligned.
    LINE_SPACING = 11
    # Target baseline row. The tracker's native Drawing.drawText normalizes
    # OS differences with a Linux y-1 compensation, so the effective
    # baseline is y+8 on every platform (not y+9 = Linux raw). Our override
    # replaces Drawing.drawText, so we render at the tracker-effective
    # position (y+8), not the OS-dependent raw position.
    NATIVE_BASELINE_Y = 8
    y_leading = NATIVE_BASELINE_Y - baseline_in_font

    emission_order = []
    for group_chars in GROUPS.values():
        emission_order.extend(group_chars)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        keep_trailing_newline=True,
        # Lua source has no analog to HTML escaping; rendered strings are
        # already Lua-ready.
        autoescape=False,
    )
    template = env.get_template(TEMPLATE_NAME)
    rendered = template.render(
        FONT_SIZE=font_size,
        LINE_SPACING=LINE_SPACING,
        Y_LEADING=y_leading,
        DEFAULT_WIDTH=default_width,
        FONT_HEIGHT=font_height,
        SPACE_WIDTH=SPACE_WIDTH,
        font_table=render_font_table(glyphs, emission_order),
        font_widths_table=render_widths_table(widths, emission_order),
        font_left_bearing_table=render_left_bearing_table(left_bearings, emission_order),
        icon_tables=render_icon_tables(shadowed_icons) if shadowed_icons else "",
    )
    output_path.write_text(rendered)


def lua_char_literal(c):
    if c == '"':
        return "'\"'"
    if c == "\\":
        return "'\\\\'"
    if c == "'":
        return '"\'"'
    return f'"{c}"'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    groups_data = {}
    for name, chars in GROUPS.items():
        groups_data[name] = parse_group(GROUPS_DIR / f"{name}.png", list(chars))

    padded, font_height, baseline_in_font = pad_to_common_height(groups_data)

    # Strip editor-only left buffer.
    trimmed = {c: strip_leading_empty_cols(m) for c, m in padded.items()}

    # Prefer measured advances from the pair-rendering atlas if available —
    # those match what gui.drawText actually produces for Franklin Gothic 9pt.
    # Fall back to FG_width + 1 for any char we don't have a measurement for.
    import json
    measured = {}
    left_bearings = {}
    if METRICS_PATH.exists():
        data = json.loads(METRICS_PATH.read_text())
        for c, entry in data.get("glyphs", {}).items():
            if "advance" in entry:
                measured[c] = entry["advance"]
            if "left_bearing" in entry:
                left_bearings[c] = entry["left_bearing"]

    widths = {}
    for c, m in trimmed.items():
        if c in measured:
            # Subtract 1 so the runtime's xoffset += width + 1 formula yields
            # the measured advance. Keeps the Lua unchanged.
            widths[c] = max(0, measured[c] - 1)
        else:
            widths[c] = fg_only_width(m)

    # Merge hand-authored bearings for chars not in the atlas-measured set.
    for c, lb in EXTRAS_LEFT_BEARINGS.items():
        left_bearings.setdefault(c, lb)

    shadowed_icons = decode_shadowed_icons()

    # Use the tracker's default font size constant (9) for the size gate.
    emit_lua(
        glyphs=trimmed,
        widths=widths,
        left_bearings=left_bearings,
        font_height=font_height,
        baseline_in_font=baseline_in_font,
        font_size=9,
        default_width=SPACE_WIDTH,
        output_path=OUTPUT,
        shadowed_icons=shadowed_icons,
    )
    n_constants = sum(1 for s in shadowed_icons if s["source"] == CONSTANTS_LUA_PATH)
    n_external = len(shadowed_icons) - n_constants
    print(f"Wrote {OUTPUT}")
    print(f"  text glyphs:    {len(padded)}")
    print(f"  font_height:    {font_height}")
    print(f"  baseline_row:   {baseline_in_font} (within the padded matrix)")
    print(f"  shadowed icons: {n_constants} (Constants.PixelImages) + {n_external} (RogueMon-extension)")


if __name__ == "__main__":
    main()
