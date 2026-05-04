--- Font Atlas Generator — standalone Ironmon Tracker extension
---
--- Renders each character the tracker uses through BizHawk's gui.drawText
--- pipeline (the exact rendering path the tracker uses for real UI), and
--- saves a screenshot for offline pixel extraction.
---
--- Usage
--- -----
---   1. Drop this file in `tracker/extensions/` and enable in the tracker.
---   2. Wait a second — the atlas renders on top of the tracker viewport
---      and is auto-captured to OUTPUT_PATH (relative to the BizHawk binary
---      unless you set an absolute path).
---   3. The atlas stays on-screen while the extension is active so you can
---      verify visually or grab a screenshot manually via F12.
---   4. Disable + re-enable to regenerate.

local FontAtlas = {
    name = "Font Atlas Generator",
    author = "Claude",
    version = "1.0",
    description = "Renders tracker glyphs to a grid and screenshots for offline extraction.",
}

-- ---------------------------------------------------------------------------
-- Config
-- ---------------------------------------------------------------------------

-- Output path. Relative paths are resolved against the BizHawk binary dir.
local OUTPUT_PATH = "z:\\root\\roguemon\\claude\\font_atlas.png"

-- Grid cell size. Must comfortably contain any glyph plus ~2 px of padding
-- so neighboring cells never touch. 14x14 is plenty for Franklin Gothic 9pt.
local CELL_W = 14
local CELL_H = 14
local COLS   = 16

-- Solid black background so the Python extractor can threshold trivially.
-- (Any non-black pixel is part of a glyph or a baseline marker.)
local BG_COLOR = 0xFF000000
local FG_COLOR = 0xFFFFFFFF

-- Characters to render. ASCII printable covers everything the English
-- tracker UI actually displays. Extend this list if you find missing glyphs
-- while previewing the tracker — pull them straight out of the string that
-- won't render correctly.
local CHARSET = {}
for i = 32, 126 do
    table.insert(CHARSET, string.char(i))
end

-- Pair-measurement atlas. Each cell renders the same char twice so the
-- Python extractor can derive the native advance width by measuring the
-- distance between the two glyph copies. Cells are wider here because
-- "WW" / "MM" need room for two full-width glyphs plus the advance.
local PAIR_CELL_W = 20
local PAIR_CELL_H = 14
local PAIR_Y = 84 + 2  -- place below the per-char grid with a small gap

-- How many frames to keep rendering before auto-capturing. Some frames of
-- render settle avoids races with the tracker's own redraw on the first
-- frame after enable.
local CAPTURE_AFTER_FRAMES = 3

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local RENDERING, CAPTURED = 1, 2
local state = RENDERING
local frameCount = 0

-- ---------------------------------------------------------------------------
-- Drawing
-- ---------------------------------------------------------------------------

local function drawAtlas()
    local n = #CHARSET
    local rows = math.ceil(n / COLS)
    local totalW = COLS * CELL_W
    local totalH = rows * CELL_H

    -- Full opaque background covering the grid footprint.
    gui.drawRectangle(0, 0, totalW, totalH, BG_COLOR, BG_COLOR)

    for i, c in ipairs(CHARSET) do
        local idx = i - 1
        local col = idx % COLS
        local row = math.floor(idx / COLS)
        local cx = col * CELL_W
        local cy = row * CELL_H
        -- Offset the glyph inside its cell: 2 px left pad, 1 px top pad.
        -- The Python extractor doesn't care about these — it scans for the
        -- tight bounding box — but consistent placement keeps cells
        -- visually readable and prevents accidental overlap at edges.
        gui.drawText(cx + 2, cy + 1, c, FG_COLOR, nil,
            Constants.Font.SIZE, Constants.Font.FAMILY, Constants.Font.STYLE)
    end

    -- Pair section: each cell renders "cc" via a single drawText so BizHawk
    -- applies its native advance between the two copies. The Python
    -- extractor finds the two glyph clusters per cell and derives the
    -- advance from the distance between their leftmost FG columns.
    local pairRows = math.ceil(n / COLS)
    local pairH = pairRows * PAIR_CELL_H
    local pairW = COLS * PAIR_CELL_W
    gui.drawRectangle(0, PAIR_Y, pairW, pairH, BG_COLOR, BG_COLOR)
    for i, c in ipairs(CHARSET) do
        local idx = i - 1
        local col = idx % COLS
        local row = math.floor(idx / COLS)
        local cx = col * PAIR_CELL_W
        local cy = PAIR_Y + row * PAIR_CELL_H
        gui.drawText(cx + 2, cy + 1, c .. c, FG_COLOR, nil,
            Constants.Font.SIZE, Constants.Font.FAMILY, Constants.Font.STYLE)
    end
end

-- ---------------------------------------------------------------------------
-- Extension hooks
-- ---------------------------------------------------------------------------

function FontAtlas.startup()
    state = RENDERING
    frameCount = 0
end

function FontAtlas.unload()
end

function FontAtlas.afterRedraw()
    drawAtlas()
    if state == RENDERING then
        frameCount = frameCount + 1
        if frameCount >= CAPTURE_AFTER_FRAMES then
            client.screenshot(OUTPUT_PATH)
            state = CAPTURED
        end
    end
end

return FontAtlas
