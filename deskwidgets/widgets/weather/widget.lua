-- Weather (wttr.in). small: now. medium: now + hourly. large: adds a 3-day forecast.
local L = require 'lib'
local F, PAD, ICON = L.F, L.PAD, L.ICON

local W = {}
local LOC = L.opt('city', 'Kyiv')
local TITLE = L.opt('title', LOC)
local CACHE = L.RUN .. '/weather-' .. L.key(LOC .. '-' .. L.LANG)
local weather

-- the sky gradient replaces the card in glass/graphite/light unless the user picked an accent
-- colour (then the tinted card is used) or switched it off
local SKY_MODE = L.T.card and L.opt_bool('sky', true) and not L.ACCENT and not L.T.deco
    and (L.STYLE == 'glass' or L.STYLE == 'graphite' or L.STYLE == 'light')
local C = SKY_MODE and L.ON_DARK or L.C

local function load_weather()
    local s = L.read_file(CACHE)
    if not s then return end
    local w = { hourly = {}, daily = {} }
    for k, v in s:gmatch('([%w_]+)=([^\n]*)') do
        local slot = k:match('^h(%d+)$')
        local dslot = k:match('^d(%d+)$')
        if slot then
            local f = L.fields(v)
            w.hourly[L.num(slot)] = { time = f[1], code = L.num(f[2]), temp = f[3], day = f[4] == '1' }
        elseif dslot then
            local f = L.fields(v)
            w.daily[L.num(dslot)] = { date = f[1], code = L.num(f[2]), hi = L.num(f[3]), lo = L.num(f[4]), rain = L.num(f[5]) }
        else
            w[k] = v
        end
    end
    weather = w
end

local function demo()
    local today = os.time()
    local function d(n) return os.date('%Y-%m-%d', today + n * 86400) end
    weather = { city = TITLE, temp = '12', feels = '8', code = '116', desc = L.tr('Мінлива хмарність', 'Partly cloudy'),
                hum = '53', wind = '16', hi = '17', lo = '7', day = '1',
                hourly = { { time = '15:00', code = 116, temp = '15', day = true }, { time = '18:00', code = 113, temp = '14', day = true },
                           { time = '21:00', code = 113, temp = '11', day = false }, { time = '00:00', code = 119, temp = '9', day = false },
                           { time = '03:00', code = 176, temp = '8', day = false } },
                daily = { { date = d(0), code = 116, hi = 17, lo = 7, rain = 10 }, { date = d(1), code = 176, hi = 14, lo = 6, rain = 70 },
                          { date = d(2), code = 113, hi = 19, lo = 8, rain = 0 } } }
end

function W.tick()
    if L.DEMO then
        if not weather then demo() end
        return
    end
    if L.due('weather_fetch', 900) then L.python('fetch.py', LOC, TITLE, CACHE, L.LANG) end
    if L.due('weather_read', 20) then load_weather() end
end

-- weather mapping
local KIND = {}
local function kinds(kind, codes) for _, c in ipairs(codes) do KIND[c] = kind end end
kinds('clear', { 113 })
kinds('partly', { 116 })
kinds('cloudy', { 119, 122 })
kinds('fog', { 143, 248, 260 })
kinds('rain', { 176, 263, 266, 293, 296, 299, 302, 353 })
kinds('pouring', { 305, 308, 356, 359 })
kinds('sleet', { 182, 185, 281, 284, 311, 314, 317, 320, 350, 362, 365, 374, 377 })
kinds('snow', { 179, 227, 230, 323, 326, 329, 332, 335, 338, 368, 371 })
kinds('thunder', { 200, 386, 389, 392, 395 })

local function weather_icon(code, day)
    local k = KIND[code] or 'cloudy'
    if k == 'clear' then return day and ICON.sunny or ICON.night, day and L.SYS.orange or C.text end
    if k == 'partly' then return day and ICON.partly or ICON.partly_night, C.text end
    return ICON[k], C.text
end

local SKY = {
    clear_day = { '2B6CD4', '5AA7EF' }, clear_night = { '0A1532', '1D2E5C' },
    partly_day = { '33629F', '6893C4' }, partly_night = { '111B33', '26334F' },
    cloudy_day = { '43566E', '6A7E97' }, cloudy_night = { '192030', '2D3646' },
    rain_day = { '36455A', '54657B' }, rain_night = { '141A26', '273041' },
    snow_day = { '50627A', '8597AE' }, snow_night = { '1E2636', '3A4558' },
    thunder_day = { '262B3C', '3F4560' }, thunder_night = { '161924', '2C3044' },
}
local SKY_GROUP = { clear = 'clear', partly = 'partly', cloudy = 'cloudy', fog = 'cloudy',
                    rain = 'rain', pouring = 'rain', sleet = 'rain', snow = 'snow', thunder = 'thunder' }

local function background(cr, x, y, w, h, code, day)
    if SKY_MODE then
        local sky = SKY[(SKY_GROUP[KIND[code] or 'cloudy']) .. (day and '_day' or '_night')]
        L.card(cr, x, y, w, h, L.hex(sky[1], 0.95), L.hex(sky[2], 0.95))
    else
        L.card(cr, x, y, w, h)
    end
end

local function city(cr, x, y)
    L.font(cr, F.semibold, 14); L.set(cr, C.text)
    local name = weather.city or TITLE
    local cw = L.width(cr, name)
    L.text(cr, name, x, y)
    L.set(cr, SKY_MODE and C.text or L.acc(C.text)); L.icon(cr, ICON.nav, 10, x + cw + 4, y)
end

local function hilo(t) return L.tr('Макс. ', 'H:') .. t.hi .. L.tr('°  Мін. ', '°  L:') .. t.lo .. '°' end

local function now_and_hourly(cr, x, y, w, code, day)
    local wt = weather
    local l, r = x + PAD, x + w - PAD
    city(cr, l, y + 30)

    L.font(cr, F.light, 48); L.set(cr, C.text)
    local tw = L.width(cr, wt.temp .. '°')
    L.text(cr, wt.temp .. '°', l - 2, y + 80)
    L.font(cr, F.regular, 11.5); L.set(cr, C.text2)
    L.text(cr, L.tr('відчув. ', 'feels ') .. wt.feels .. '°', l + tw + 4, y + 80)

    L.font(cr, F.regular, 11); L.set(cr, C.text2)
    local rx = L.text(cr, wt.wind .. L.tr(' км/год', ' km/h'), r, y + 30, 'r')
    L.set(cr, C.text2, 0.55); rx = L.icon(cr, ICON.wind, 11, rx - 4, y + 31, 'r')
    L.font(cr, F.regular, 11); L.set(cr, C.text2)
    rx = L.text(cr, wt.hum .. '%', rx - 10, y + 30, 'r')
    L.set(cr, SKY_MODE and L.SYS.cyan or L.acc(L.SYS.cyan), 0.85); L.icon(cr, ICON.water, 11, rx - 3, y + 31, 'r')

    L.font(cr, F.semibold, 13); L.set(cr, C.text)
    local ico, icol = weather_icon(code, day)
    rx = L.text(cr, L.fit(cr, wt.desc or '', w * 0.38), r, y + 59, 'r')
    L.set(cr, icol); L.icon(cr, ico, 18, rx - 6, y + 61, 'r')
    L.font(cr, F.regular, 11.5); L.set(cr, C.text2)
    L.text(cr, hilo(wt), r, y + 79, 'r')

    L.set(cr, C.text, 0.16)
    cairo_rectangle(cr, l, y + 95, w - 2 * PAD, 1)
    cairo_fill(cr)

    local slots = { { time = L.tr('Зараз', 'Now'), code = code, temp = wt.temp, day = day } }
    for i = 1, 5 do if wt.hourly[i] then slots[#slots + 1] = wt.hourly[i] end end
    local colw = (w - 2 * PAD) / 6
    for i, s in ipairs(slots) do
        local cx = l + colw * (i - 0.5)
        L.font(cr, F.medium, 10.5); L.set(cr, i == 1 and C.text or C.text2)
        L.text(cr, s.time, cx, y + 116, 'c')
        local si, sc = weather_icon(s.code, s.day)
        L.set(cr, sc); L.icon(cr, si, 17, cx, y + 139, 'c')
        L.font(cr, F.semibold, 13); L.set(cr, C.text)
        L.text(cr, s.temp .. '°', cx + 2, y + 157, 'c')
    end
end

local function daily(cr, x, y, w, h)
    local l, r = x + PAD, x + w - PAD
    local days = weather.daily
    if #days == 0 then return end
    local lo, hi = 100, -100
    for _, d in ipairs(days) do lo, hi = math.min(lo, d.lo), math.max(hi, d.hi) end
    local span = math.max(hi - lo, 1)
    local rowh = (h - 8) / #days
    local bar = L.ACCENT and L.ring_from(L.ACCENT) or nil
    for i, d in ipairs(days) do
        local by = y + rowh * (i - 0.5) + 5
        local yy, mm, dd = d.date:match('(%d+)-(%d+)-(%d+)')
        local ts = os.time({ year = L.num(yy), month = L.num(mm), day = L.num(dd), hour = 12 })
        local name = i == 1 and L.tr('Сьогодні', 'Today') or L.WEEKDAY_CAP[os.date('*t', ts).wday]
        L.font(cr, F.medium, 13); L.set(cr, C.text)
        L.text(cr, L.fit(cr, name, 92), l, by)
        local ico, icol = weather_icon(d.code, true)
        L.set(cr, icol); L.icon(cr, ico, 17, l + 104, by + 1, 'c')
        if d.rain >= 30 then
            L.font(cr, F.semibold, 9.5); L.set(cr, L.SYS.cyan)
            L.text(cr, d.rain .. '%', l + 104, by + 13, 'c')
        end
        local bx, bw = l + 170, r - 34 - (l + 170)
        L.font(cr, F.medium, 12.5); L.set(cr, C.text2)
        L.text(cr, d.lo .. '°', bx - 8, by, 'r')
        L.set(cr, C.text, 0.15); L.rrect(cr, bx, by - 6, bw, 5, 2.5); cairo_fill(cr)
        local x0 = bx + bw * (d.lo - lo) / span
        local x1 = bx + bw * (d.hi - lo) / span
        L.rrect(cr, x0, by - 6, math.max(x1 - x0, 5), 5, 2.5)
        local g = cairo_pattern_create_linear(bx, 0, bx + bw, 0)
        if bar then
            cairo_pattern_add_color_stop_rgba(g, 0, bar[1][1], bar[1][2], bar[1][3], 1)
            cairo_pattern_add_color_stop_rgba(g, 1, bar[2][1], bar[2][2], bar[2][3], 1)
        else
            cairo_pattern_add_color_stop_rgba(g, 0, 0.39, 0.82, 1, 1)
            cairo_pattern_add_color_stop_rgba(g, 0.5, 1, 0.84, 0.04, 1)
            cairo_pattern_add_color_stop_rgba(g, 1, 1, 0.62, 0.04, 1)
        end
        cairo_set_source(cr, g); cairo_fill(cr); cairo_pattern_destroy(g)
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        L.text(cr, d.hi .. '°', r, by, 'r')
    end
end

function W.draw(cr, x, y, w, h)
    if not weather then
        L.card(cr, x, y, w, h)
        L.font(cr, F.medium, 13); L.set(cr, L.C.text3)
        L.text(cr, L.tr('Погода завантажується…', 'Loading weather…'), x + w / 2, y + h / 2 + 4, 'c')
        return
    end
    local code, day = L.num(weather.code), weather.day == '1'
    background(cr, x, y, w, h, code, day)

    if L.SIZE == 'small' then
        local l, r = x + PAD, x + w - PAD
        city(cr, l, y + 30)
        local ico, icol = weather_icon(code, day)
        L.font(cr, F.light, 46); L.set(cr, C.text)
        L.text(cr, weather.temp .. '°', l - 2, y + 86)
        L.set(cr, icol); L.icon(cr, ico, 24, r, y + 82, 'r')
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        for i, line in ipairs(L.wrap(cr, weather.desc or '', w - 2 * PAD, 2)) do L.text(cr, line, l, y + 112 + (i - 1) * 16) end
        L.font(cr, F.regular, 11); L.set(cr, C.text2)
        L.text(cr, hilo(weather), l, y + 152)
        return
    end

    now_and_hourly(cr, x, y, w, code, day)
    if L.SIZE == 'large' then
        L.set(cr, C.text, 0.16)
        cairo_rectangle(cr, x + PAD, y + 176, w - 2 * PAD, 1)
        cairo_fill(cr)
        daily(cr, x, y + 180, w, h - 180 - 8)
    end
end

return W
