-- World clock. small: 3 cities as a list. medium: up to 4 analog clocks.
-- large: 4 bigger clocks in a 2x2 grid. Light dial in daytime, dark at night.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}

local NAMES = {
    ['Europe/Kyiv'] = { 'Київ', 'Kyiv' }, ['Europe/Warsaw'] = { 'Варшава', 'Warsaw' },
    ['Europe/Berlin'] = { 'Берлін', 'Berlin' }, ['Europe/London'] = { 'Лондон', 'London' },
    ['Europe/Paris'] = { 'Париж', 'Paris' }, ['Europe/Istanbul'] = { 'Стамбул', 'Istanbul' },
    ['Asia/Dubai'] = { 'Дубай', 'Dubai' }, ['Asia/Kolkata'] = { 'Делі', 'Delhi' },
    ['Asia/Shanghai'] = { 'Пекін', 'Beijing' }, ['Asia/Seoul'] = { 'Сеул', 'Seoul' },
    ['Asia/Tokyo'] = { 'Токіо', 'Tokyo' }, ['Australia/Sydney'] = { 'Сідней', 'Sydney' },
    ['America/New_York'] = { 'Нью-Йорк', 'New York' }, ['America/Toronto'] = { 'Торонто', 'Toronto' },
    ['America/Chicago'] = { 'Чикаго', 'Chicago' }, ['America/Los_Angeles'] = { 'Лос-Анджелес', 'Los Angeles' },
    ['America/Sao_Paulo'] = { 'Сан-Паулу', 'São Paulo' },
}

local zones = {}
for _, tz in ipairs(L.opt_list('zones', 'Europe/Kyiv,Europe/London,America/New_York,Asia/Tokyo')) do
    local n = NAMES[tz]
    zones[#zones + 1] = { tz = tz, offset = 0,
                          name = n and L.tr(n[1], n[2]) or (tz:match('([^/]+)$') or tz):gsub('_', ' ') }
end

local function parse_offset(z)   -- "+0530" to seconds
    local sign, hh, mm = (z or ''):match('([+-])(%d%d)(%d%d)')
    if not sign then return 0 end
    local s = L.num(hh) * 3600 + L.num(mm) * 60
    return sign == '-' and -s or s
end

local local_offset = 0
local function refresh_offsets()
    local_offset = parse_offset(os.date('%z'))
    for _, z in ipairs(zones) do
        z.offset = parse_offset(L.popen('TZ=' .. L.quote(z.tz) .. ' date +%z'))
    end
end

function W.tick()
    if L.due('tz', 1800) then refresh_offsets() end
end

local function zone_time(z)
    local now = os.time()
    local t = os.date('!*t', now + z.offset)
    local here = os.date('!*t', now + local_offset)
    local dday = 0
    if t.year ~= here.year or t.yday ~= here.yday then
        dday = (os.time({ year = t.year, month = t.month, day = t.day, hour = 12 }) >
                os.time({ year = here.year, month = here.month, day = here.day, hour = 12 })) and 1 or -1
    end
    return t, dday
end

local function relative(z, dday)
    local diff = (z.offset - local_offset) / 3600
    local day = dday == 0 and L.tr('Сьогодні', 'Today') or dday > 0 and L.tr('Завтра', 'Tomorrow') or L.tr('Вчора', 'Yesterday')
    if diff == 0 then return day end
    local h = math.abs(diff) % 1 == 0 and string.format('%d', math.abs(diff)) or string.format('%.1f', math.abs(diff))
    return string.format('%s, %s%s%s', day, diff > 0 and '+' or '−', h, L.tr(' год', 'h'))
end

local function mini_clock(cr, cx, cy, r, t)
    local day = t.hour >= 6 and t.hour < 18
    cairo_arc(cr, cx, cy, r, 0, 2 * math.pi)
    if day then cairo_set_source_rgba(cr, 0.96, 0.96, 0.97, 1) else cairo_set_source_rgba(cr, 0.07, 0.07, 0.09, 1) end
    cairo_fill_preserve(cr)
    L.set(cr, L.ink(0.14)); cairo_set_line_width(cr, L.STYLE == 'manga' and 2 or 1); cairo_stroke(cr)
    local ink = day and { 0.1, 0.1, 0.12, 1 } or { 0.96, 0.96, 0.97, 1 }
    for i = 0, 11 do
        local a = i * math.pi / 6
        cairo_move_to(cr, cx + math.sin(a) * (r * 0.9), cy - math.cos(a) * (r * 0.9))
        cairo_line_to(cr, cx + math.sin(a) * (r * 0.8), cy - math.cos(a) * (r * 0.8))
        cairo_set_line_width(cr, i % 3 == 0 and 1.6 or 1)
        L.set(cr, ink, i % 3 == 0 and 0.8 or 0.4); cairo_stroke(cr)
    end
    local min = t.min + t.sec / 60
    local hr = t.hour % 12 + min / 60
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND)
    for _, hd in ipairs({ { hr * math.pi / 6, r * 0.5, r * 0.09 }, { min * math.pi / 30, r * 0.76, r * 0.06 } }) do
        cairo_move_to(cr, cx, cy)
        cairo_line_to(cr, cx + math.sin(hd[1]) * hd[2], cy - math.cos(hd[1]) * hd[2])
        cairo_set_line_width(cr, hd[3]); L.set(cr, ink); cairo_stroke(cr)
    end
    cairo_arc(cr, cx, cy, r * 0.08, 0, 2 * math.pi); L.set(cr, L.STYLE == 'color' and ink or L.acc(L.SYS.orange)); cairo_fill(cr)
end

local function caption(cr, cx, y, colw, z, t, dday, big)
    L.font(cr, F.semibold, big and 14 or 12.5); L.set(cr, C.text)
    L.text(cr, L.fit(cr, z.name, colw - 4), cx, y, 'c')
    L.font(cr, F.regular, big and 11 or 10); L.set(cr, C.text3)
    L.text(cr, L.fit(cr, relative(z, dday), colw - 2), cx, y + (big and 17 or 16), 'c')
    L.font(cr, F.medium, big and 12.5 or 11); L.set(cr, C.text2)
    L.text(cr, string.format('%02d:%02d', t.hour, t.min), cx, y + (big and 35 or 32), 'c')
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    if L.SIZE == 'large' then
        local qw, qh = (w - 2 * PAD) / 2, (h - 2 * PAD) / 2
        for i = 1, math.min(#zones, 4) do
            local z = zones[i]
            local t, dday = zone_time(z)
            local col, row = (i - 1) % 2, (i - 1) // 2
            local cx = x + PAD + qw * (col + 0.5)
            local qy = y + PAD + qh * row
            mini_clock(cr, cx, qy + 56, 46, t)
            caption(cr, cx, qy + 124, qw, z, t, dday, true)
        end
        return
    end
    if L.SIZE == 'medium' then
        local n = math.max(math.min(#zones, 4), 1)
        local colw = (w - 2 * PAD) / n
        for i = 1, math.min(#zones, 4) do
            local z = zones[i]
            local t, dday = zone_time(z)
            local cx = x + PAD + colw * (i - 0.5)
            mini_clock(cr, cx, y + 66, 32, t)
            caption(cr, cx, y + 123, colw, z, t, dday)
        end
        return
    end
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.globe, L.acc(L.SYS.cyan), L.tr('Світовий час', 'World clock'))
    for i = 1, math.min(#zones, 3) do
        local z = zones[i]
        local t, dday = zone_time(z)
        local ry = y + 60 + (i - 1) * 36
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, z.name, 80), x + PAD, ry)
        L.font(cr, F.regular, 10); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, relative(z, dday), 86), x + PAD, ry + 15)
        L.font(cr, F.light, 24); L.set(cr, C.text)
        L.text(cr, string.format('%02d:%02d', t.hour, t.min), x + w - PAD, ry + 9, 'r')
    end
end

return W
