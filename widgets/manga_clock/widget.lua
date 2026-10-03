-- Manga clock: inked digits with a comic offset shadow, the weekday in kanji and a sound effect.
-- small, medium (time + date column), large (small scaled up). Best with the "Manga" style.
local L = require 'lib'
local C, F, PAD = L.C, L.F, L.PAD

local W = { scale = { large = 'small' } }
local SECONDS = L.opt_bool('seconds', false)
local SFX = L.opt('sfx', 'チクタク')
local HAS_CJK = L.popen('fc-list :lang=ja family'):match('%S') ~= nil
local KANJI = { '日曜日', '月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日' }

-- text with a thick ink outline (comic lettering)
local function inked(cr, face, size, s, x, y, fill, outline, lw)
    L.font(cr, face, size)
    cairo_move_to(cr, x, y)
    cairo_text_path(cr, s)
    L.set(cr, fill); cairo_fill_preserve(cr)
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND)
    L.set(cr, outline); cairo_set_line_width(cr, lw); cairo_stroke(cr)
end

local function speed_lines(cr, cx, cy, r0, r1, n, c)
    cairo_set_line_width(cr, 1.2)
    for i = 0, n - 1 do
        local a = i * 2 * math.pi / n + (i % 3) * 0.05
        local rr = r0 + (i % 4) * 6
        cairo_move_to(cr, cx + math.cos(a) * rr, cy + math.sin(a) * rr)
        cairo_line_to(cr, cx + math.cos(a) * r1, cy + math.sin(a) * r1)
    end
    L.set(cr, c); cairo_stroke(cr)
end

local function time_block(cr, cx, by, size)
    local t = os.date('*t')
    local s = string.format('%02d:%02d', t.hour, t.min)
    local paper = L.T.dark and { 0.98, 0.98, 1, 1 } or { 1, 1, 1, 1 }
    local ink = L.T.dark and L.acc(L.SYS.pink) or { 0.05, 0.05, 0.05, 1 }
    L.font(cr, F.heavy, size)
    local tw = L.width(cr, s)
    local x0 = cx - tw / 2
    -- offset comic shadow in the accent colour, then the inked digits
    inked(cr, F.heavy, size, s, x0 + 4, by + 4, L.acc(L.SYS.red), L.acc(L.SYS.red), size * 0.09)
    inked(cr, F.heavy, size, s, x0, by, paper, ink, size * 0.09)
    if SECONDS then
        L.font(cr, F.bold, size * 0.3); L.set(cr, C.text2)
        L.text(cr, string.format('%02d', t.sec), x0 + tw + 4, by)
    end
    return t
end

local function weekday_box(cr, x, y, t)
    local label = HAS_CJK and KANJI[t.wday] or L.WEEKDAY[t.wday]
    L.font(cr, HAS_CJK and F.cjk or F.bold, HAS_CJK and 12 or 10.5)
    local tw = L.width(cr, label)
    L.rrect(cr, x, y, tw + 14, 20, 4)
    if L.T.dark then L.set(cr, L.acc(L.SYS.pink)) else cairo_set_source_rgba(cr, 0.05, 0.05, 0.05, 1) end
    cairo_fill(cr)
    cairo_set_source_rgba(cr, 1, 1, 1, 1)
    cairo_move_to(cr, x + 7, y + 14.5)
    cairo_show_text(cr, label)
end

-- sound effect lettering: accent fill, black ink outline, white halo, tilted
local function sfx(cr, x, y, size)
    cairo_save(cr)
    cairo_translate(cr, x, y)
    cairo_rotate(cr, -0.2)
    local face = SFX:match('[\128-\255]') and HAS_CJK and F.cjk or F.heavy
    inked(cr, face, size, SFX, 0, 0, { 1, 1, 1, 1 }, { 1, 1, 1, 1 }, size * 0.32)
    inked(cr, face, size, SFX, 0, 0, L.acc(L.SYS.red), { 0, 0, 0, 1 }, size * 0.06)
    cairo_restore(cr)
end

-- speed lines stay inside the card
local function clipped_lines(cr, x, y, w, h, ...)
    cairo_save(cr)
    L.rrect(cr, x, y, w, h, L.R)
    cairo_clip(cr)
    speed_lines(cr, ...)
    cairo_restore(cr)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local lines = L.T.dark and { 1, 1, 1, 0.10 } or { 0, 0, 0, 0.10 }
    if L.SIZE == 'medium' then
        clipped_lines(cr, x, y, w, h, x + w * 0.33, y + h * 0.55, 46, 200, 36, lines)
        local t = time_block(cr, x + w * 0.36, y + h / 2 + 26, 66)
        weekday_box(cr, x + PAD, y + PAD, t)
        local rx = x + w - PAD
        L.font(cr, HAS_CJK and F.cjk or F.bold, 20); L.set(cr, C.text)
        L.text(cr, HAS_CJK and string.format('%d月%d日', t.month, t.day) or L.day_month(t), rx, y + 54, 'r')
        L.font(cr, F.medium, 11.5); L.set(cr, C.text2)
        L.text(cr, L.day_month(t) .. ' ' .. t.year, rx, y + 74, 'r')
        sfx(cr, x + w - 112, y + h - 22, 26)
        return
    end
    clipped_lines(cr, x, y, w, h, x + w / 2, y + h / 2 + 6, 34, 140, 30, lines)
    local t = time_block(cr, x + w / 2, y + h / 2 + 18, 50)
    weekday_box(cr, x + PAD, y + PAD - 2, t)
    sfx(cr, x + w - 90, y + h - 20, 21)
    L.font(cr, F.semibold, 11); L.set(cr, C.text2)
    L.text(cr, L.day_month(t), x + PAD, y + h - 16)
end

return W
