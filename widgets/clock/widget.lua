-- Clock: analog dial. small: the dial. medium: dial plus digital time and date. large: big dial.
local L = require 'lib'
local C, F, PAD = L.C, L.F, L.PAD

local W = { scale = { large = 'small' } }
local SECONDS = L.opt_bool('seconds', true)
local NUMBERS = L.opt_bool('numbers', true)

local function hand(cr, cx, cy, angle, back, len, lw, col)
    local sx, sy = math.sin(angle), -math.cos(angle)
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND)
    cairo_set_line_width(cr, lw)
    cairo_move_to(cr, cx - sx * back, cy - sy * back)
    cairo_line_to(cr, cx + sx * len, cy + sy * len)
    L.set(cr, col)
    cairo_stroke(cr)
end

local function dial(cr, cx, cy, rf)
    if not L.T.card then   -- no card: a hairline circle only
        cairo_arc(cr, cx, cy, rf, 0, 2 * math.pi)
        L.set(cr, L.ink(0.25)); cairo_set_line_width(cr, 1.5); cairo_stroke(cr)
        return
    end
    local inner, outer
    if not L.T.dark then inner, outer = { 1, 1, 1, 0.95 }, { 0.90, 0.90, 0.93, 0.95 }
    elseif L.STYLE == 'color' then inner, outer = { 0, 0, 0, 0.10 }, { 0, 0, 0, 0.22 }
    else inner, outer = { 0.17, 0.17, 0.20, 0.95 }, { 0.06, 0.06, 0.08, 0.97 } end
    local g = cairo_pattern_create_radial(cx, cy - rf * 0.35, rf * 0.15, cx, cy, rf)
    cairo_pattern_add_color_stop_rgba(g, 0, inner[1], inner[2], inner[3], inner[4])
    cairo_pattern_add_color_stop_rgba(g, 1, outer[1], outer[2], outer[3], outer[4])
    cairo_arc(cr, cx, cy, rf, 0, 2 * math.pi)
    cairo_set_source(cr, g)
    cairo_fill(cr)
    cairo_pattern_destroy(g)
    cairo_arc(cr, cx, cy, rf - 0.5, 0, 2 * math.pi)
    if L.STYLE == 'manga' then
        cairo_set_source_rgba(cr, 0, 0, 0, 1); cairo_set_line_width(cr, 2)
    else
        L.set(cr, L.ink(0.08)); cairo_set_line_width(cr, 1)
    end
    cairo_stroke(cr)
end

local function face(cr, cx, cy, rf)
    dial(cr, cx, cy, rf)
    local accent = L.acc(L.SYS.orange)
    for i = 0, 59 do
        local a = i * math.pi / 30
        local hour = i % 5 == 0
        local r0 = rf - (hour and 7.5 or 4.5)
        cairo_set_line_width(cr, hour and 2 or 1)
        cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND)
        cairo_move_to(cr, cx + math.sin(a) * r0, cy - math.cos(a) * r0)
        cairo_line_to(cr, cx + math.sin(a) * (rf - 3.5), cy - math.cos(a) * (rf - 3.5))
        if hour and i % 15 == 0 and L.ACCENT then L.set(cr, accent) else
            L.set(cr, hour and C.text or C.text3, hour and 0.8 or 0.4) end
        cairo_stroke(cr)
    end
    if NUMBERS then
        L.font(cr, F.semibold, rf * 0.17)
        L.set(cr, C.text)
        for hr = 1, 12 do
            local a = hr * math.pi / 6
            L.text(cr, tostring(hr), cx + math.sin(a) * (rf * 0.73), cy - math.cos(a) * (rf * 0.73) + rf * 0.06, 'c')
        end
    end

    local t = os.date('*t')
    local min = t.min + (SECONDS and t.sec / 60 or 0)
    local hr = t.hour % 12 + min / 60
    for _, sh in ipairs({ 1, 0 }) do  -- shadow pass, then the hands
        local function col(c) return sh == 1 and { 0, 0, 0, L.T.dark and 0.4 or 0.18 } or c end
        cairo_save(cr)
        cairo_translate(cr, sh * 0.8, sh * 1.8)
        hand(cr, cx, cy, hr * math.pi / 6, 6, rf * 0.5, 5.5, col(C.text))
        hand(cr, cx, cy, min * math.pi / 30, 6, rf * 0.8, 3.5, col(C.text))
        if SECONDS then hand(cr, cx, cy, t.sec * math.pi / 30, 14, rf * 0.9, 1.5, col(accent)) end
        cairo_restore(cr)
    end
    cairo_arc(cr, cx, cy, 4.5, 0, 2 * math.pi); L.set(cr, SECONDS and accent or C.text); cairo_fill(cr)
    cairo_arc(cr, cx, cy, 1.6, 0, 2 * math.pi); cairo_set_source_rgba(cr, 0.08, 0.08, 0.1, 1); cairo_fill(cr)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    if L.SIZE ~= 'medium' then
        face(cr, x + w / 2, y + h / 2, math.min(w, h) / 2 - 10)
        return
    end
    face(cr, x + h / 2, y + h / 2, h / 2 - 10)
    local t = os.date('*t')
    local tx, rx = x + h + 6, x + w - PAD
    L.font(cr, F.bold, 12); L.set(cr, L.acc(L.SYS.orange))
    L.spaced(cr, L.WEEKDAY[t.wday], tx, y + 52, 0.8)
    L.font(cr, F.light, 46); L.set(cr, C.text)
    L.text(cr, string.format('%02d:%02d', t.hour, t.min), tx - 2, y + 104)
    L.font(cr, F.medium, 13); L.set(cr, C.text2)
    L.text(cr, L.fit(cr, L.day_month(t) .. ', ' .. t.year, rx - tx), tx, y + 128)
end

return W
