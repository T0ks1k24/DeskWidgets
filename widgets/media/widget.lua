-- Now playing: the active MPRIS player (Firefox, Spotify, Telegram...). The service follows the
-- players over D-Bus and writes RUN/media + cover art. small, medium, large (small scaled up).
-- Left click toggles play/pause.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = { scale = { large = 'small' } }
local m = {}

function W.tick()
    if L.DEMO then
        m = { status = 'Playing', player = 'Spotify', title = L.tr('Обійми', 'Midnight City'),
              artist = L.tr('Океан Ельзи', 'M83'), length = '241', pos = '83', ts = tostring(os.time()), rate = '1' }
        return
    end
    m = L.read_kv(L.RUN .. '/media') or {}
end

function W.click()
    if m.bus and m.bus ~= '' then
        L.spawn('gdbus call --session --dest ' .. L.quote(m.bus) .. ' --object-path /org/mpris/MediaPlayer2'
                .. ' --method org.mpris.MediaPlayer2.Player.PlayPause')
        return true
    end
    return false
end

local function clock(sec)
    sec = math.max(0, math.floor(sec + 0.5))
    if sec >= 3600 then return string.format('%d:%02d:%02d', sec // 3600, sec % 3600 // 60, sec % 60) end
    return string.format('%d:%02d', sec // 60, sec % 60)
end

local function cover(cr, ax, ay, as, surf, dim, radius)
    if surf then
        L.cover(cr, surf, ax, ay, as, as, radius)
    else
        L.rrect(cr, ax, ay, as, as, radius)
        cairo_save(cr)
        cairo_clip(cr)
        if L.STYLE == 'color' then
            cairo_set_source_rgba(cr, 0, 0, 0, dim and 0.10 or 0.18)
            cairo_paint(cr)
            L.set(cr, C.text, dim and 0.5 or 0.9)
        else
            local base = L.acc(L.SYS.pink)
            local c1, c2 = L.shade(base, 0.1), L.mix(base, L.SYS.purple, 0.7)
            local g = cairo_pattern_create_linear(ax, ay, ax + as, ay + as)
            cairo_pattern_add_color_stop_rgba(g, 0, c1[1], c1[2], c1[3], dim and 0.35 or 1)
            cairo_pattern_add_color_stop_rgba(g, 1, c2[1], c2[2], c2[3], dim and 0.35 or 1)
            cairo_set_source(cr, g)
            cairo_paint(cr)
            cairo_pattern_destroy(g)
            cairo_set_source_rgba(cr, 1, 1, 1, dim and 0.5 or 0.92)
        end
        L.icon_mid(cr, ICON.music, as * 0.33, ax + as / 2, ay + as / 2)
        cairo_restore(cr)
    end
    L.rrect(cr, ax + 0.5, ay + 0.5, as - 1, as - 1, radius - 0.5)
    cairo_set_source_rgba(cr, 1, 1, 1, 0.12)
    cairo_set_line_width(cr, 1)
    cairo_stroke(cr)
end

local function position()
    local len = L.num(m.length)
    local pos = L.num(m.pos)
    if m.status == 'Playing' then pos = pos + (os.time() - L.num(m.ts)) * (tonumber(m.rate) or 1) end
    return math.max(0, math.min(pos, len)), len
end

local function progress(cr, tx, by, tw, right)
    local pos, len = position()
    if len <= 0 then
        L.font(cr, F.medium, 10.5); L.set(cr, C.text3)
        L.text(cr, m.status == 'Playing' and L.tr('Відтворюється', 'Playing') or L.tr('Пауза', 'Paused'), tx, by + 19)
        return
    end
    L.rrect(cr, tx, by, tw, 4, 2)
    L.set(cr, L.ink(L.T.dark and 0.16 or 0.12)); cairo_fill(cr)
    if pos > 0 then
        L.rrect(cr, tx, by, math.max(4, tw * pos / len), 4, 2)
        L.set(cr, L.ACCENT and L.acc(L.fill) or L.fill); cairo_fill(cr)
    end
    if right then
        L.font(cr, F.medium, 10.5); L.set(cr, C.text3)
        L.text(cr, clock(pos), tx, by + 19)
        L.text(cr, '-' .. clock(len - pos), right, by + 19, 'r')
    end
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local active = (m.status == 'Playing' or m.status == 'Paused') and (m.title or '') ~= ''
    local art = active and L.image(m.art) or nil
    local idle_title = L.tr('Нічого не грає', 'Not playing')

    if L.SIZE == 'small' then
        local as = 70
        local ax, ay = x + PAD, y + PAD
        cover(cr, ax, ay, as, art, not active, 12)
        if not active then
            L.font(cr, F.semibold, 13); L.set(cr, C.text2)
            L.text(cr, idle_title, x + PAD, y + 118)
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, L.tr('Увімкніть музику', 'Play some music'), x + PAD, y + 136)
            return
        end
        L.set(cr, m.status == 'Playing' and C.text2 or C.text3)
        L.icon(cr, m.status == 'Playing' and ICON.play or ICON.pause, 13, x + w - PAD, y + 31, 'r')
        L.font(cr, F.semibold, 10.5); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, (m.player or ''):upper(), w - 2 * PAD - as - 30), ax + as + 10, y + 30)
        L.font(cr, F.semibold, 13); L.set(cr, C.text)
        L.text(cr, L.fit(cr, m.title, w - 2 * PAD), x + PAD, y + 112)
        L.font(cr, F.regular, 11.5); L.set(cr, C.text2)
        L.text(cr, L.fit(cr, m.artist or '', w - 2 * PAD), x + PAD, y + 129)
        progress(cr, x + PAD, y + 142, w - 2 * PAD, nil)
        return
    end

    local as = h - 2 * PAD
    local ax, ay = x + PAD, y + PAD
    local tx = ax + as + 16
    local tw = x + w - PAD - tx
    if not active then
        cover(cr, ax, ay, as, nil, true, 14)
        L.font(cr, F.semibold, 15); L.set(cr, C.text2)
        L.text(cr, idle_title, tx, y + 70)
        L.font(cr, F.regular, 11.5); L.set(cr, C.text3)
        for i, line in ipairs(L.wrap(cr, L.tr('Увімкніть музику чи відео — віджет підхопить плеєр',
                                             'Start some music or a video — the widget picks it up'), tw, 3)) do
            L.text(cr, line, tx, y + 92 + (i - 1) * 16)
        end
        return
    end

    cover(cr, ax, ay, as, art, false, 14)
    L.set(cr, L.acc(L.SYS.pink)); L.icon(cr, ICON.note, 11, tx, y + 31)
    L.font(cr, F.semibold, 11); L.set(cr, C.text3)
    L.text(cr, L.fit(cr, (m.player or ''):upper(), tw - 34), tx + 14, y + 30)
    L.set(cr, m.status == 'Playing' and C.text2 or C.text3)
    L.icon(cr, m.status == 'Playing' and ICON.play or ICON.pause, 12, x + w - PAD, y + 31, 'r')

    L.font(cr, F.semibold, 15); L.set(cr, C.text)
    local ty = y + 56
    for _, line in ipairs(L.wrap(cr, m.title, tw, 2)) do
        L.text(cr, line, tx, ty)
        ty = ty + 19
    end
    if (m.artist or '') ~= '' then
        L.font(cr, F.regular, 12.5); L.set(cr, C.text2)
        L.text(cr, L.fit(cr, m.artist, tw), tx, ty + 1)
    end
    progress(cr, tx, y + 128, tw, x + w - PAD)
end

return W
