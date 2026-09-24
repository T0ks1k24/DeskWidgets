-- Calendar. small: today and the week. medium: today and the month. large: the month, big.
-- Days with events get a dot (from the events cache the service keeps).
local L = require 'lib'
local C, F, PAD = L.C, L.F, L.PAD

local W = {}
local WEEKNO = L.opt_bool('week_numbers', true)
local busy = {}  -- 'YYYY-MM-DD' -> true

local function load_busy()
    busy = {}
    local kv = L.read_kv(L.RUN .. '/events')
    if not kv then return end
    for k, v in pairs(kv) do
        if k:match('^e%d+$') then
            local f = L.fields(v)
            local s, e, allday = L.num(f[1]), L.num(f[2]), f[3] == '1'
            local last = allday and e - 1 or math.max(s, e - 1)
            local d = s
            while d <= last do
                busy[os.date('%Y-%m-%d', d)] = true
                d = d + 86400
            end
            busy[os.date('%Y-%m-%d', last)] = true
        end
    end
end

function W.tick()
    if L.due('busy', 60) then load_busy() end
end

local function today_block(cr, x, y, t, accent)
    L.font(cr, F.bold, 12); L.set(cr, accent)
    L.spaced(cr, L.WEEKDAY[t.wday], x + PAD, y + 30, 0.8)
    L.font(cr, F.light, 56); L.set(cr, C.text)
    L.text(cr, tostring(t.day), x + PAD - 3, y + 88)
    L.font(cr, F.medium, 14); L.set(cr, C.text2)
    L.text(cr, L.tr(L.MONTH_GEN[t.month], L.MONTH[t.month]), x + PAD, y + 109)
end

local function day_cell(cr, cx, by, day, is_today, weekend, dim, dot, accent, fs)
    fs = fs or 12
    if is_today then
        cairo_arc(cr, cx, by - fs * 0.37, fs * 0.84, 0, 2 * math.pi); L.set(cr, accent); cairo_fill(cr)
        L.font(cr, F.bold, fs)
        L.set(cr, L.STYLE == 'color' and (L.T.dark and { 0.1, 0.1, 0.12, 1 } or { 1, 1, 1, 1 }) or { 1, 1, 1, 1 })
    else
        L.font(cr, F.medium, fs)
        if dim then L.set(cr, C.text3) elseif weekend then L.set(cr, accent, 0.85) else L.set(cr, C.text2) end
    end
    L.text(cr, tostring(day), cx, by, 'c')
    if dot then
        cairo_arc(cr, cx, by + fs * 0.55, 1.6, 0, 2 * math.pi); L.set(cr, accent, 0.9); cairo_fill(cr)
    end
end

-- month grid in the box (gx, gy, gw, gh)
local function month(cr, gx, gy, gw, gh, t, accent, fs)
    local colw = gw / 7
    for i = 0, 6 do
        L.font(cr, F.medium, fs * 0.8); L.set(cr, i >= 5 and accent or C.text3, i >= 5 and 0.8 or nil)
        L.text(cr, L.WEEK_SHORT[i + 1], gx + colw * (i + 0.5), gy, 'c')
    end
    local first = os.date('*t', os.time({ year = t.year, month = t.month, day = 1, hour = 12 }))
    local offset = (first.wday + 5) % 7
    local days = os.date('*t', os.time({ year = t.year, month = t.month + 1, day = 0, hour = 12 })).day
    local rows = math.ceil((offset + days) / 7)
    local rh = (gh - fs * 1.2) / rows
    for d = 1, days do
        local cell = offset + d - 1
        local col, row = cell % 7, cell // 7
        local key = string.format('%04d-%02d-%02d', t.year, t.month, d)
        day_cell(cr, gx + colw * (col + 0.5), gy + fs * 1.5 + row * rh + rh * 0.35, d, d == t.day,
                 col >= 5, d < t.day, busy[key], accent, fs)
    end
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local t = os.date('*t')
    local accent = L.acc(L.SYS.red)
    local week = L.tr('тиждень ', 'week ') .. tonumber(os.date('%V'))

    if L.SIZE == 'large' then
        L.font(cr, F.bold, 13); L.set(cr, accent)
        L.spaced(cr, L.WEEKDAY[t.wday], x + PAD, y + 32, 0.8)
        L.font(cr, F.light, 40); L.set(cr, C.text)
        L.text(cr, L.day_month(t), x + PAD - 2, y + 76)
        L.font(cr, F.medium, 13); L.set(cr, C.text3)
        L.text(cr, L.MONTH[t.month] .. ' ' .. t.year .. (WEEKNO and (' · ' .. week) or ''), x + PAD, y + 98)
        L.divider(cr, x + PAD, y + 112, w - 2 * PAD)
        month(cr, x + PAD - 4, y + 140, w - 2 * PAD + 8, h - 150, t, accent, 15)
        return
    end

    if L.SIZE == 'medium' then
        today_block(cr, x, y, t, accent)
        if WEEKNO then
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, week, x + PAD, y + 150)
        end
        local gx = x + 150
        L.font(cr, F.semibold, 12); L.set(cr, C.text)
        L.text(cr, L.MONTH[t.month] .. ' ' .. t.year, gx + 4, y + 30)
        month(cr, gx, y + 50, w - 150 - PAD + 4, h - 58, t, accent, 12)
        return
    end

    today_block(cr, x, y, t, accent)
    if WEEKNO then
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, L.tr('тиж. ', 'wk ') .. tonumber(os.date('%V')), x + w - PAD, y + 30, 'r')
    end
    local colw = (w - 2 * PAD) / 7
    local idx = (t.wday + 5) % 7  -- Monday = 0
    for i = 0, 6 do
        local cx = x + PAD + colw * (i + 0.5)
        local ts = os.time({ year = t.year, month = t.month, day = t.day - idx + i, hour = 12 })
        local d = os.date('*t', ts)
        L.font(cr, F.medium, 9.5); L.set(cr, i >= 5 and accent or C.text3, i >= 5 and 0.8 or nil)
        L.text(cr, L.WEEK_SHORT[i + 1], cx, y + 131, 'c')
        day_cell(cr, cx, y + 152, d.day, i == idx, i >= 5, i < idx, busy[os.date('%Y-%m-%d', ts)], accent)
    end
end

return W
