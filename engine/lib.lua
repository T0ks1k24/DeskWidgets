--[[ DeskWidgets drawing library, the API every widget uses. See docs/creating-widgets.md.

     The service starts one conky per widget instance and describes it in the environment:
       MAC_ID, MAC_TYPE, MAC_SIZE (small|medium|large), MAC_STYLE, MAC_ACCENT, MAC_LANG,
       MAC_OPTS (file with the widget's options), MAC_WIDGET_DIR, MAC_RUN, MAC_DEMO.
     Everything is drawn in "base" units: a small card is 170x170, a medium 2 cells wide,
     a large 2x2 cells. The global size setting scales the whole canvas. ]]

require 'cairo'

local L = {}
local env = os.getenv

L.HOME = env('HOME')
L.ENGINE = env('MAC_ENGINE') or (L.HOME .. '/.local/share/deskwidgets/engine')
L.WIDGET_DIR = env('MAC_WIDGET_DIR') or '.'
L.RUN = env('MAC_RUN') or ((env('XDG_RUNTIME_DIR') or '/tmp') .. '/deskwidgets')
L.CONFIG = env('MAC_CONFIG') or ((env('XDG_CONFIG_HOME') or (L.HOME .. '/.config')) .. '/deskwidgets')

L.ID = env('MAC_ID') or 'preview'
L.TYPE = env('MAC_TYPE') or 'widget'
L.SIZE = env('MAC_SIZE') or 'small'
L.STYLE = env('MAC_STYLE') or 'glass'
L.LANG = env('MAC_LANG') == 'en' and 'en' or 'uk'
L.DEMO = env('MAC_DEMO') == '1'   -- gallery thumbnails and previews show sample data
L.NO_CARD = false                 -- set while a layout is drawn scaled inside an already drawn card

-- geometry (base units)
L.M = 16            -- transparent window margin around the card (room for the shadow)
L.GAP = 16          -- space between cards
L.S = 170           -- one grid cell = small card
L.W = 2 * L.S + L.GAP
L.PAD = 16          -- inner padding
L.R = 22            -- corner radius

L.F = {
    light = 'DW Light', regular = 'DW Regular', medium = 'DW Medium',
    semibold = 'DW Semibold', bold = 'DW Bold', heavy = 'DW Heavy',
    icon = 'Material Design Icons', cjk = 'Noto Sans CJK JP',
}

-- language
-- L.tr('Погода', 'Weather') gives the string for the current language
function L.tr(uk, en) return (L.LANG == 'en' and en) or uk end

local function hex(h, a)
    h = h:gsub('^#', '')
    return { tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255,
             tonumber(h:sub(5, 6), 16) / 255, a or 1 }
end
L.hex = hex

local function shade(c, t)   -- t > 0 lightens, t < 0 darkens
    local function ch(v) return t > 0 and v + (1 - v) * t or v * (1 + t) end
    return { ch(c[1]), ch(c[2]), ch(c[3]), c[4] or 1 }
end
L.shade = shade

function L.mix(a, b, t)
    return { a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t,
             a[3] + (b[3] - a[3]) * t, (a[4] or 1) + ((b[4] or 1) - (a[4] or 1)) * t }
end
local function luminance(c) return 0.2126 * c[1] + 0.7152 * c[2] + 0.0722 * c[3] end

-- options of this instance
L.OPT = {}
do
    local path = env('MAC_OPTS')
    local f = path and io.open(path, 'r')
    if f then
        for line in f:lines() do
            local k, v = line:match('^([%w_]+)=(.*)$')
            if k then L.OPT[k] = v:gsub('\\n', '\n'):gsub('\\\\', '\\') end
        end
        f:close()
    end
end
function L.opt(key, default)
    local v = L.OPT[key]
    if v == nil or v == '' then return default end
    return v
end
function L.opt_bool(key, default)
    local v = L.OPT[key]
    if v == nil or v == '' then return default end
    return v == '1' or v == 'true'
end
function L.opt_num(key, default) return tonumber(L.OPT[key]) or default end
function L.opt_list(key, default)
    local out = {}
    for item in (L.opt(key, default) or ''):gmatch('[^,]+') do out[#out + 1] = item end
    return out
end

-- colours and styles
L.ACCENTS = {
    red = 'FF453A', orange = 'FF9F0A', yellow = 'FFD60A', green = '30D158', mint = '63E6E2',
    teal = '40C8E0', blue = '0A84FF', indigo = '5E5CE6', purple = 'BF5AF2', pink = 'FF375F',
    graphite = '8E8E93', sakura = 'FF7EB6',
}
local accent_name = env('MAC_ACCENT') or 'auto'
L.ACCENT = L.ACCENTS[accent_name] and hex(L.ACCENTS[accent_name]) or nil

-- Apple system colours, used for meaning (green = ok, red = alert)
L.SYS = {
    red = hex('FF453A'), pink = hex('FF375F'), cyan = hex('64D2FF'), purple = hex('BF5AF2'),
    green = hex('30D158'), orange = hex('FF9F0A'), yellow = hex('FFD60A'), indigo = hex('5E5CE6'),
    blue = hex('0A84FF'), teal = hex('40C8E0'),
}

local ON_DARK = { text = hex('F5F5F7'), text2 = hex('EBEBF5', 0.70), text3 = hex('EBEBF5', 0.42) }
local ON_LIGHT = { text = hex('1C1C1E'), text2 = hex('3C3C43', 0.74), text3 = hex('3C3C43', 0.48) }
L.ON_DARK, L.ON_LIGHT = ON_DARK, ON_LIGHT

--[[ style fields:
     top/bottom  card gradient          dark   light text on the card
     rim         highlight edge alpha   shadow soft shadow alpha per layer
     card=false  no card at all         text_shadow  shadow under text (no-card styles)
     hi          default highlight colour (anime styles)                                ]]
local STYLES = {
    glass = { top = hex('2C2A36', 0.88), bottom = hex('18171E', 0.92), dark = true, rim = 0.20, shadow = 0.032, tint = 0.30 },
    graphite = { top = hex('232326', 0.97), bottom = hex('18181A', 0.98), dark = true, rim = 0.10, shadow = 0.04, tint = 0.26 },
    light = { top = hex('FFFFFF', 0.94), bottom = hex('F0F0F5', 0.95), dark = false, rim = 0, shadow = 0.03, tint = 0.14 },
    color = { dark = true, rim = 0.28, shadow = 0.035 },
    clear_dark = { card = false, dark = false },
    clear_light = { card = false, dark = true, text_shadow = 0.55 },
    -- anime styles
    sakura = { top = hex('FFE3F0', 0.97), bottom = hex('E9DDFF', 0.97), dark = false, rim = 0, tint = 0.30,
               hi = hex('FF5FA2'), deco = 'sakura',
               text = { text = hex('3B2342'), text2 = hex('5A3A66', 0.80), text3 = hex('7B5A88', 0.62) } },
    manga = { top = hex('FFFFFF', 1), bottom = hex('F4F4F4', 1), dark = false, rim = 0, tint = 0,
              hi = hex('E60023'), deco = 'manga',
              text = { text = hex('111111'), text2 = hex('222222', 0.80), text3 = hex('333333', 0.60) } },
    neon = { top = hex('1A1030', 0.94), bottom = hex('0B0717', 0.96), dark = true, rim = 0, tint = 0.10,
             hi = hex('FF2E97'), deco = 'neon' },
}
local T = STYLES[L.STYLE] or STYLES.glass
if T.card == nil then T.card = true end
if L.STYLE == 'color' then
    local base = L.ACCENT or hex('5E5CE6')
    T.top, T.bottom = shade(base, 0.12), shade(base, -0.28)
    T.top[4], T.bottom[4] = 0.96, 0.97
    T.dark = luminance(base) < 0.62
elseif L.ACCENT and T.card and (T.tint or 0) > 0 then
    -- a chosen colour tints the whole card, so it shows on every widget
    local function tint(c, k) local m = L.mix(c, L.ACCENT, k); m[4] = c[4]; return m end
    T.top, T.bottom = tint(T.top, T.tint), tint(T.bottom, T.tint * 0.7)
end
L.T = T
L.C = {}
for k, v in pairs(T.text or (T.dark and ON_DARK or ON_LIGHT)) do L.C[k] = v end
for k, v in pairs(L.SYS) do L.C[k] = v end

-- neutral tokens that follow the text colour (lines, bar tracks, fills)
local ink = T.dark and { 1, 1, 1 } or { 0, 0, 0 }
function L.ink(a) return { ink[1], ink[2], ink[3], a } end
L.line = L.ink(T.dark and 0.12 or 0.10)
L.track = L.ink(T.dark and 0.10 or 0.08)
L.fill = L.ink(T.dark and 0.88 or 0.72)

-- highlight colour: the chosen accent, else the style's own, else the widget's default.
-- On a coloured card highlights turn into the text colour so they stay visible.
function L.acc(default)
    if L.STYLE == 'color' then return L.C.text end
    return L.ACCENT or T.hi or default
end

-- gradient pair for rings and bars from one colour
function L.ring_from(c) return { shade(c, -0.15), shade(c, 0.35) } end
-- rings/bars for a value: the accent (or the style's colour) when one is chosen, else `default`
function L.pair(default)
    if L.STYLE == 'color' then return { L.C.text, L.C.text } end
    local a = L.ACCENT or T.hi
    return a and L.ring_from(a) or default
end

L.RING = {
    cpu = { hex('FA114F'), hex('FF6A8E') },
    mem = { hex('00A3DE'), hex('5CF0FF') },
    swap = { hex('8E44FF'), hex('C59BFF') },
    disk = { hex('27B845'), hex('A8F07A') },
    battery = { hex('27B845'), hex('7CF08F') },
    warn = { hex('FF9F0A'), hex('FFD60A') },
    hot = { hex('E8132B'), hex('FF5E4D') },
    warm = { hex('FF6B1A'), hex('FFA51F') },
}

-- Material Design Icons code points (https://pictogrammers.com/library/mdi/)
L.ICON = {
    gauge = 0xF029A, pulse = 0xF0430, ethernet = 0xF0317, wifi = 0xF05A9,
    down = 0xF0045, up = 0xF005D, timer = 0xF13AB, nav = 0xF18F0,
    water = 0xF058C, wind = 0xF059D, shield_ok = 0xF0565, shield_alert = 0xF0ECC,
    sunny = 0xF0599, night = 0xF0594, partly = 0xF0595, partly_night = 0xF0F31,
    cloudy = 0xF0590, fog = 0xF0591, rain = 0xF0597, pouring = 0xF0596,
    snow = 0xF0598, sleet = 0xF067F, thunder = 0xF067E,
    laptop = 0xF0322, flash = 0xF0241, plug = 0xF06A5, battery = 0xF0079,
    headphones = 0xF02CB, headset = 0xF02CE, mouse = 0xF037D, keyboard = 0xF030C,
    phone = 0xF011C, tablet = 0xF04F7, gamepad = 0xF0296, bluetooth = 0xF00AF,
    calendar = 0xF00ED, calendar_blank = 0xF00EE, music = 0xF075A, note = 0xF0387,
    play = 0xF040A, pause = 0xF03E4, speaker = 0xF04C3, clock = 0xF0150, memory = 0xF035B,
    harddisk = 0xF02CA, docker = 0xF0868, cash = 0xF0114, globe = 0xF01E7, pin = 0xF0403,
    hourglass = 0xF051F, tri_up = 0xF0360, tri_down = 0xF035D, star = 0xF04CE, heart = 0xF02D1,
    sparkle = 0xF0AE2, flower = 0xF024A, tv = 0xF0502, chat = 0xF0B79, face = 0xF0C7B,
    account = 0xF0004, image = 0xF02E9,
}

local WD = {
    uk = { caps = { 'НЕДІЛЯ', 'ПОНЕДІЛОК', 'ВІВТОРОК', 'СЕРЕДА', 'ЧЕТВЕР', 'П’ЯТНИЦЯ', 'СУБОТА' },
           cap = { 'Неділя', 'Понеділок', 'Вівторок', 'Середа', 'Четвер', 'П’ятниця', 'Субота' },
           short = { 'нд', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб' },
           head = { 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд' },
           month_gen = { 'січня', 'лютого', 'березня', 'квітня', 'травня', 'червня', 'липня',
                         'серпня', 'вересня', 'жовтня', 'листопада', 'грудня' },
           month = { 'Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень', 'Липень',
                     'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень' } },
    en = { caps = { 'SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY' },
           cap = { 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday' },
           short = { 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat' },
           head = { 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su' },
           month_gen = { 'January', 'February', 'March', 'April', 'May', 'June', 'July',
                         'August', 'September', 'October', 'November', 'December' },
           month = { 'January', 'February', 'March', 'April', 'May', 'June', 'July',
                     'August', 'September', 'October', 'November', 'December' } },
}
local wd = WD[L.LANG]
L.WEEKDAY, L.WEEKDAY_CAP, L.WDAY_SHORT = wd.caps, wd.cap, wd.short   -- indexed by os.date('*t').wday
L.WEEK_SHORT = wd.head                                                -- Monday first
L.MONTH_GEN, L.MONTH = wd.month_gen, wd.month

-- "October 10" in English, "10 October" (day first) in Ukrainian
function L.day_month(t)
    if L.LANG == 'en' then return L.MONTH[t.month] .. ' ' .. t.day end
    return t.day .. ' ' .. L.MONTH_GEN[t.month]
end

-- drawing helpers
local ext = cairo_text_extents_t:create()
local UTF8_CHAR = '[%z\1-\127\194-\244][\128-\191]*'

-- grayscale antialiasing: subpixel (RGB) AA leaves colour fringes on a transparent window
function L.prepare(cr)
    local fo = cairo_font_options_create()
    cairo_font_options_set_antialias(fo, CAIRO_ANTIALIAS_GRAY)
    cairo_font_options_set_hint_style(fo, CAIRO_HINT_STYLE_SLIGHT)
    cairo_set_font_options(cr, fo)
    cairo_font_options_destroy(fo)
end

function L.set(cr, c, a) cairo_set_source_rgba(cr, c[1], c[2], c[3], a or c[4] or 1) end

function L.font(cr, face, size)
    cairo_select_font_face(cr, face, CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL)
    cairo_set_font_size(cr, size)
end

function L.width(cr, s)
    cairo_text_extents(cr, s, ext)
    return ext.x_advance
end

-- text without a card (clear_light) gets a soft shadow so it reads on any wallpaper
local function show(cr, s, x, y)
    if T.text_shadow then
        cairo_save(cr)
        cairo_set_source_rgba(cr, 0, 0, 0, T.text_shadow)
        cairo_move_to(cr, x + 0.6, y + 1.3)
        cairo_show_text(cr, s)
        cairo_restore(cr)
    end
    cairo_move_to(cr, x, y)
    cairo_show_text(cr, s)
    -- show_text leaves a current point behind; without this the next arc/path would start
    -- with a stray line from the end of the text
    cairo_new_path(cr)
end

-- align: nil/'l' left, 'c' centre, 'r' right; y is the baseline. Returns the left x.
function L.text(cr, s, x, y, align)
    if align == 'c' then x = x - L.width(cr, s) / 2
    elseif align == 'r' then x = x - L.width(cr, s) end
    show(cr, s, x, y)
    return x
end

-- shortens s with "…" so it fits into maxw with the current font
function L.fit(cr, s, maxw)
    if L.width(cr, s) <= maxw then return s end
    local chars = {}
    for ch in s:gmatch(UTF8_CHAR) do chars[#chars + 1] = ch end
    while #chars > 0 do
        chars[#chars] = nil
        local t = table.concat(chars):gsub('[%s%p]+$', '') .. '…'
        if L.width(cr, t) <= maxw then return t end
    end
    return '…'
end

-- word-wraps s into at most `lines` lines of width maxw (last one ellipsized);
-- explicit newlines start a new line
function L.wrap(cr, s, maxw, lines)
    local out = {}
    for para in (s .. '\n'):gmatch('([^\n]*)\n') do
        local cur = ''
        for wd_ in para:gmatch('%S+') do
            local try = cur == '' and wd_ or (cur .. ' ' .. wd_)
            if L.width(cr, try) <= maxw or cur == '' then
                cur = try
            else
                out[#out + 1] = cur
                cur = wd_
            end
        end
        out[#out + 1] = cur
    end
    if #out > lines then
        local rest = table.concat(out, ' ', lines)
        for i = #out, lines, -1 do out[i] = nil end
        out[lines] = rest
    end
    for k = 1, #out do out[k] = L.fit(cr, out[k], maxw) end
    while #out > 0 and out[#out] == '' do out[#out] = nil end
    return out
end

-- icons are aligned by their ink box; returns the left edge of the ink
function L.icon(cr, code, size, x, y, align)
    L.font(cr, L.F.icon, size)
    local s = utf8.char(code)
    cairo_text_extents(cr, s, ext)
    local left = x
    if align == 'c' then left = x - ext.width / 2
    elseif align == 'r' then left = x - ext.width end
    show(cr, s, left - ext.x_bearing, y)
    return left
end

-- icon centred on (cx, cy) by its ink box
function L.icon_mid(cr, code, size, cx, cy)
    L.font(cr, L.F.icon, size)
    local s = utf8.char(code)
    cairo_text_extents(cr, s, ext)
    show(cr, s, cx - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing)
end

-- letter-spaced caps labels; align like L.text
function L.spaced(cr, s, x, y, sp, align)
    if align then
        local total, n = 0, 0
        for ch in s:gmatch(UTF8_CHAR) do total = total + L.width(cr, ch); n = n + 1 end
        total = total + sp * math.max(n - 1, 0)
        if align == 'c' then x = x - total / 2 elseif align == 'r' then x = x - total end
    end
    for ch in s:gmatch(UTF8_CHAR) do
        show(cr, ch, x, y)
        x = x + L.width(cr, ch) + sp
    end
end

-- "63" big + "%" small, centred as one piece on cx
function L.value_unit(cr, val, unit, cx, y, vface, vsize, uface, usize)
    L.font(cr, vface, vsize); local w1 = L.width(cr, val)
    L.font(cr, uface, usize); local w2 = L.width(cr, unit)
    local x0 = cx - (w1 + w2 + 1) / 2
    L.font(cr, vface, vsize); L.text(cr, val, x0, y)
    L.font(cr, uface, usize); L.text(cr, unit, x0 + w1 + 1, y)
end

function L.rrect(cr, x, y, w, h, r)
    local p = math.pi
    r = math.max(0, math.min(r, w / 2, h / 2))
    cairo_new_sub_path(cr)
    cairo_arc(cr, x + w - r, y + r, r, -p / 2, 0)
    cairo_arc(cr, x + w - r, y + h - r, r, 0, p / 2)
    cairo_arc(cr, x + r, y + h - r, r, p / 2, p)
    cairo_arc(cr, x + r, y + r, r, p, 3 * p / 2)
    cairo_close_path(cr)
end

function L.vgrad(cr, y0, y1, c0, c1)
    local g = cairo_pattern_create_linear(0, y0, 0, y1)
    cairo_pattern_add_color_stop_rgba(g, 0, c0[1], c0[2], c0[3], c0[4] or 1)
    cairo_pattern_add_color_stop_rgba(g, 1, c1[1], c1[2], c1[3], c1[4] or 1)
    cairo_set_source(cr, g)
    return g
end

-- deterministic pseudo-random numbers per widget instance (decorations don't jump around)
local seed = 0
for i = 1, #L.ID do seed = (seed * 31 + L.ID:byte(i)) % 2147483647 end
local function rnd()
    seed = (seed * 1103515245 + 12345) % 2147483648
    return seed / 2147483648
end

local function sparkle(cr, cx, cy, r, c)
    cairo_new_path(cr)
    cairo_move_to(cr, cx, cy - r)
    cairo_curve_to(cr, cx + r * 0.12, cy - r * 0.12, cx + r * 0.12, cy - r * 0.12, cx + r, cy)
    cairo_curve_to(cr, cx + r * 0.12, cy + r * 0.12, cx + r * 0.12, cy + r * 0.12, cx, cy + r)
    cairo_curve_to(cr, cx - r * 0.12, cy + r * 0.12, cx - r * 0.12, cy + r * 0.12, cx - r, cy)
    cairo_curve_to(cr, cx - r * 0.12, cy - r * 0.12, cx - r * 0.12, cy - r * 0.12, cx, cy - r)
    L.set(cr, c); cairo_fill(cr)
end
L.sparkle = sparkle

local function petal(cr, cx, cy, size, angle, c)
    cairo_save(cr)
    cairo_translate(cr, cx, cy)
    cairo_rotate(cr, angle)
    cairo_new_path(cr)
    cairo_move_to(cr, 0, size * 0.5)
    cairo_curve_to(cr, -size * 0.55, size * 0.1, -size * 0.35, -size * 0.45, -size * 0.08, -size * 0.5)
    cairo_line_to(cr, 0, -size * 0.36)
    cairo_line_to(cr, size * 0.08, -size * 0.5)
    cairo_curve_to(cr, size * 0.35, -size * 0.45, size * 0.55, size * 0.1, 0, size * 0.5)
    L.set(cr, c); cairo_fill(cr)
    cairo_restore(cr)
end
L.petal = petal

-- style decorations drawn on top of the card body (clipped to it)
local function decorate(cr, x, y, w, h)
    if T.deco == 'sakura' then
        local pink = L.ACCENT or hex('FF8FBF')
        for i = 1, 5 do
            local px = (i % 2 == 1) and x + w - 18 - rnd() * w * 0.32 or x + 10 + rnd() * w * 0.25
            local py = (i % 2 == 1) and y + 8 + rnd() * h * 0.30 or y + h - 14 - rnd() * h * 0.25
            petal(cr, px, py, 9 + rnd() * 7, rnd() * 6.28, { pink[1], pink[2], pink[3], 0.22 + rnd() * 0.18 })
        end
        sparkle(cr, x + w - 20, y + 18, 6, { 1, 1, 1, 0.95 })
        sparkle(cr, x + w - 34, y + 30, 3.2, { 1, 1, 1, 0.8 })
        sparkle(cr, x + 14, y + h - 16, 4, { 1, 1, 1, 0.85 })
    elseif T.deco == 'manga' then
        local ink_c = L.ACCENT or { 0, 0, 0, 1 }
        -- halftone dots growing towards the bottom-right corner
        local step = 7
        for gx = x + w * 0.45, x + w, step do
            for gy = y + h * 0.45, y + h, step do
                local t = ((gx - x - w * 0.45) / (w * 0.55) + (gy - y - h * 0.45) / (h * 0.55)) / 2
                if t > 0.15 then
                    cairo_arc(cr, gx, gy, 0.4 + t * 2.1, 0, 2 * math.pi)
                    L.set(cr, ink_c, 0.10 + t * 0.06); cairo_fill(cr)
                end
            end
        end
        -- speed lines from the top-left corner
        cairo_set_line_width(cr, 1)
        for i = 1, 9 do
            local a = 0.15 + i * 0.13
            cairo_move_to(cr, x + math.cos(a) * 18, y + math.sin(a) * 18)
            cairo_line_to(cr, x + math.cos(a) * (40 + rnd() * 30), y + math.sin(a) * (40 + rnd() * 30))
            L.set(cr, ink_c, 0.12); cairo_stroke(cr)
        end
    end
end

-- the card: shadow, gradient body, style decorations, rim.
-- top/bottom override the body colours (weather sky). Styles without a card draw nothing.
function L.card(cr, x, y, w, h, top, bottom)
    if L.NO_CARD or not T.card then return end
    local R = L.R
    if T.deco == 'manga' then
        L.rrect(cr, x + 5, y + 6, w, h, R)                 -- hard comic shadow
        cairo_set_source_rgba(cr, 0, 0, 0, 0.85); cairo_fill(cr)
    elseif T.deco == 'sakura' then
        local s = L.ACCENT or hex('C084FC')
        L.rrect(cr, x + 4, y + 5, w, h, R)
        L.set(cr, s, 0.38); cairo_fill(cr)
    elseif T.deco == 'neon' then
        local glow = L.ACCENT or T.hi
        for i, a in ipairs({ 0.05, 0.08, 0.12, 0.18 }) do
            local g = 10 - i * 2
            L.rrect(cr, x - g / 2, y - g / 2, w + g, h + g, R + g / 2)
            L.set(cr, glow, a); cairo_set_line_width(cr, g); cairo_stroke(cr)
        end
    else
        for i = 1, 8 do
            L.rrect(cr, x - i + 1, y - i + 4, w + 2 * i - 2, h + 2 * i - 2, R + i - 1)
            cairo_set_source_rgba(cr, 0, 0, 0, T.shadow or 0.03)
            cairo_fill(cr)
        end
    end

    L.rrect(cr, x, y, w, h, R)
    local g = L.vgrad(cr, y, y + h, top or T.top, bottom or T.bottom)
    cairo_fill_preserve(cr)
    cairo_pattern_destroy(g)
    if T.deco and not top then
        cairo_save(cr)
        cairo_clip(cr)
        decorate(cr, x, y, w, h)
        cairo_restore(cr)
    end
    cairo_new_path(cr)

    if T.deco == 'manga' then
        L.rrect(cr, x + 1.25, y + 1.25, w - 2.5, h - 2.5, R - 1)
        cairo_set_source_rgba(cr, 0, 0, 0, 1); cairo_set_line_width(cr, 2.5); cairo_stroke(cr)
        return
    elseif T.deco == 'neon' then
        L.rrect(cr, x + 0.75, y + 0.75, w - 1.5, h - 1.5, R - 0.5)
        L.set(cr, L.ACCENT or T.hi, 0.95); cairo_set_line_width(cr, 1.5); cairo_stroke(cr)
        return
    elseif T.deco == 'sakura' then
        L.rrect(cr, x + 1, y + 1, w - 2, h - 2, R - 1)
        cairo_set_source_rgba(cr, 1, 1, 1, 0.9); cairo_set_line_width(cr, 2); cairo_stroke(cr)
        return
    end
    L.rrect(cr, x + 0.5, y + 0.5, w - 1, h - 1, R - 0.5)
    local b = cairo_pattern_create_linear(0, y, 0, y + h)
    if T.dark or top then
        local r = top and 0.20 or T.rim
        cairo_pattern_add_color_stop_rgba(b, 0, 1, 1, 1, r)
        cairo_pattern_add_color_stop_rgba(b, 0.3, 1, 1, 1, r * 0.3)
        cairo_pattern_add_color_stop_rgba(b, 1, 1, 1, 1, r * 0.45)
    else
        cairo_pattern_add_color_stop_rgba(b, 0, 0, 0, 0, 0.06)
        cairo_pattern_add_color_stop_rgba(b, 1, 0, 0, 0, 0.10)
    end
    cairo_set_source(cr, b)
    cairo_set_line_width(cr, 1)
    cairo_stroke(cr)
    cairo_pattern_destroy(b)
end

-- coloured icon + title on the left, optional icon + note on the right
function L.header(cr, x, y, w, ico, color, title, right, right_ico)
    L.set(cr, color); L.icon(cr, ico, 14, x, y + 1)
    L.font(cr, L.F.semibold, 13); L.set(cr, L.C.text2)
    local tx = x + 20
    L.text(cr, L.fit(cr, title, w - 20), tx, y)
    if right then
        local left_edge = tx + L.width(cr, title) + 12
        L.font(cr, L.F.regular, 11); L.set(cr, L.C.text3)
        local room = x + w - left_edge - (right_ico and 15 or 0)
        if room > 20 then
            local rx = L.text(cr, L.fit(cr, right, room), x + w, y, 'r')
            if right_ico then L.icon(cr, right_ico, 11, rx - 4, y + 1, 'r') end
        end
    end
end

-- Apple-Watch-style ring: angular gradient, round caps, tinted track
function L.ring(cr, cx, cy, r, lw, frac, c1, c2)
    local two_pi, start = 2 * math.pi, -math.pi / 2
    frac = math.max(0, math.min(frac, 1))
    cairo_set_line_width(cr, lw)
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_BUTT)
    cairo_new_path(cr)
    cairo_arc(cr, cx, cy, r, 0, two_pi)
    L.set(cr, c1, T.dark and 0.2 or 0.18)
    cairo_stroke(cr)

    L.set(cr, c1)
    cairo_arc(cr, cx + r * math.cos(start), cy + r * math.sin(start), lw / 2, 0, two_pi)
    cairo_fill(cr)
    local total = frac * two_pi
    if total <= 0 then return end
    local steps = math.max(1, math.ceil(total / 0.04))
    for i = 0, steps - 1 do
        local a0 = start + total * i / steps
        local a1 = math.min(start + total * (i + 1) / steps + 0.015, start + total)
        L.set(cr, L.mix(c1, c2, (i + 0.5) / steps))
        cairo_new_path(cr)
        cairo_arc(cr, cx, cy, r, a0, a1)
        cairo_stroke(cr)
    end
    local e = start + total
    L.set(cr, c2)
    cairo_arc(cr, cx + r * math.cos(e), cy + r * math.sin(e), lw / 2, 0, two_pi)
    cairo_fill(cr)
end

-- horizontal bar: rounded track + gradient fill
function L.bar(cr, x, y, w, h, frac, c1, c2)
    L.rrect(cr, x, y, w, h, h / 2)
    L.set(cr, L.track); cairo_fill(cr)
    frac = math.max(0, math.min(frac, 1))
    if frac <= 0 then return end
    c2 = c2 or c1
    L.rrect(cr, x, y, math.max(h, w * frac), h, h / 2)
    local g = cairo_pattern_create_linear(x, 0, x + w, 0)
    cairo_pattern_add_color_stop_rgba(g, 0, c1[1], c1[2], c1[3], 1)
    cairo_pattern_add_color_stop_rgba(g, 1, c2[1], c2[2], c2[3], 1)
    cairo_set_source(cr, g)
    cairo_fill(cr)
    cairo_pattern_destroy(g)
end

function L.divider(cr, x, y, w)
    L.set(cr, L.line)
    cairo_rectangle(cr, x, y, w, 1)
    cairo_fill(cr)
end

-- PNG to cached cairo surface. The service converts JPEG etc. to PNG (option `<key>_png`)
local images = {}
function L.image(path)
    if not path or path == '' then return nil end
    local cached = images[path]
    if cached ~= nil then return cached or nil end
    local surf
    if L.read_file(path) then
        local s = cairo_image_surface_create_from_png(path)
        if cairo_image_surface_get_width(s) > 0 then surf = s else cairo_surface_destroy(s) end
    end
    images[path] = surf or false
    return surf
end

-- draws `surf` covering the rounded rect (centre crop)
function L.cover(cr, surf, x, y, w, h, r)
    L.rrect(cr, x, y, w, h, r)
    cairo_save(cr)
    cairo_clip(cr)
    local iw, ih = cairo_image_surface_get_width(surf), cairo_image_surface_get_height(surf)
    local sc = math.max(w / iw, h / ih)
    cairo_translate(cr, x + (w - iw * sc) / 2, y + (h - ih * sc) / 2)
    cairo_scale(cr, sc, sc)
    cairo_set_source_surface(cr, surf, 0, 0)
    cairo_pattern_set_filter(cairo_get_source(cr), CAIRO_FILTER_BEST)
    cairo_paint(cr)
    cairo_restore(cr)
end

-- data helpers
function L.read_file(path)
    local f = io.open(path, 'r')
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s
end

-- key=value file to table (later keys win)
function L.read_kv(path)
    local s = L.read_file(path)
    if not s then return nil end
    local t = {}
    for k, v in s:gmatch('([%w_]+)=([^\n]*)') do t[k] = v end
    return t
end

function L.num(s) return tonumber(s) or 0 end
function L.cp(s) return conky_parse(s) end
function L.trim(s) return (s:gsub('^%s+', ''):gsub('%s+$', '')) end
function L.spawn(cmd) os.execute(cmd .. ' >/dev/null 2>&1 &') end
function L.quote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end
function L.python(script, ...)   -- runs a helper script of this widget in the background
    local args = {}
    for _, a in ipairs({ ... }) do args[#args + 1] = L.quote(a) end
    L.spawn('/usr/bin/python3 ' .. L.quote(L.WIDGET_DIR .. '/' .. script) .. ' ' .. table.concat(args, ' '))
end

function L.popen(cmd)
    local p = io.popen(cmd .. ' 2>/dev/null')
    if not p then return '' end
    local out = p:read('*a')
    p:close()
    return out or ''
end

-- splits "a|b|c" (empty fields kept)
function L.fields(s)
    local out = {}
    for f in (s .. '|'):gmatch('([^|]*)|') do out[#out + 1] = f end
    return out
end

-- stable short file-name-safe key for a string (cache file names)
function L.key(s)
    return (tostring(s):gsub('.', function(c) return string.format('%02x', c:byte()) end)):sub(1, 48)
end

local stamp = {}
function L.due(key, every)
    local now = os.time()
    if not stamp[key] or now - stamp[key] >= every then
        stamp[key] = now
        return true
    end
    return false
end

-- Ukrainian plural: 1 день, 2 дні, 5 днів. For both languages use
-- L.tr(L.plural(n, 'день', 'дні', 'днів'), n == 1 and 'day' or 'days')
function L.plural(n, one, few, many)
    local n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 ~= 11 then return one end
    if n10 >= 2 and n10 <= 4 and (n100 < 12 or n100 > 14) then return few end
    return many
end

return L
