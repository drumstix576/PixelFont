--- Extras Atlas — preview of font glyphs missing from TrackerPixelFont.
---
--- Drop in `tracker/extensions/`, enable, and the six characters render
--- on top of the tracker viewport in the same drawText path the real UI
--- uses. A screenshot is auto-captured to OUTPUT_PATH so you can pull
--- the pixel-exact shapes into font_groups/extras.png.

local ExtrasAtlas = {
    name = "Extras Atlas",
    author = "Claude",
    version = "1.0",
    description = "Renders missing glyphs (e-acute, x, curly apos, 1/2, 2/3, en-dash) for offline extraction.",
}

local OUTPUT_PATH = "z:\\root\\roguemon\\claude\\extras_atlas.png"

local CELL_W = 14
local CELL_H = 14
local COLS   = 8
local BG_COLOR = 0xFF000000
local FG_COLOR = 0xFFFFFFFF

-- Lua 5.4 string literals accept \u{XXXX}.
local CHARSET = {
    "\u{00E9}",  -- é
    "\u{00D7}",  -- ×
    "\u{2019}",  -- ' (right single quote)
    "\u{00BD}",  -- ½
    "\u{2154}",  -- ⅔
    "\u{2013}",  -- – (en-dash)
}

local PAIR_CELL_W = 20
local PAIR_CELL_H = 14
local PAIR_Y = CELL_H * math.ceil(#CHARSET / COLS) + 2

local CAPTURE_AFTER_FRAMES = 3
local RENDERING, CAPTURED = 1, 2
local state = RENDERING
local frameCount = 0

local function drawAtlas()
    local n = #CHARSET
    local rows = math.ceil(n / COLS)
    local totalW = COLS * CELL_W
    local totalH = rows * CELL_H

    gui.drawRectangle(0, 0, totalW, totalH, BG_COLOR, BG_COLOR)
    for i, c in ipairs(CHARSET) do
        local idx = i - 1
        local col = idx % COLS
        local row = math.floor(idx / COLS)
        gui.drawText(col * CELL_W + 2, row * CELL_H + 1, c, FG_COLOR, nil,
            Constants.Font.SIZE, Constants.Font.FAMILY, Constants.Font.STYLE)
    end

    local pairRows = math.ceil(n / COLS)
    local pairW = COLS * PAIR_CELL_W
    local pairH = pairRows * PAIR_CELL_H
    gui.drawRectangle(0, PAIR_Y, pairW, pairH, BG_COLOR, BG_COLOR)
    for i, c in ipairs(CHARSET) do
        local idx = i - 1
        local col = idx % COLS
        local row = math.floor(idx / COLS)
        gui.drawText(col * PAIR_CELL_W + 2, PAIR_Y + row * PAIR_CELL_H + 1,
            c .. c, FG_COLOR, nil,
            Constants.Font.SIZE, Constants.Font.FAMILY, Constants.Font.STYLE)
    end
end

function ExtrasAtlas.startup()
    state = RENDERING
    frameCount = 0
end

function ExtrasAtlas.unload() end

function ExtrasAtlas.afterRedraw()
    drawAtlas()
    if state == RENDERING then
        frameCount = frameCount + 1
        if frameCount >= CAPTURE_AFTER_FRAMES then
            client.screenshot(OUTPUT_PATH)
            state = CAPTURED
        end
    end
end

return ExtrasAtlas
