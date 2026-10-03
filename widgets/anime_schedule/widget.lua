-- Anime: next episodes from AniList. small: the next show with its cover. medium: 3 rows.
-- large: up to 7 rows. Left click opens the show on AniList.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local MODE = L.opt('mode', 'airing')
local USER = L.opt('username', '')
local LANG = L.opt('title_lang', 'romaji')
local CACHE = L.RUN .. '/anime-' .. L.key(MODE .. USER .. LANG)
local COVERS = L.RUN .. '/anime-covers'
local shows = {}
local rows_drawn = {}   -- y ranges of the rows on screen, for clicks

local function load()
    shows = {}
    local kv = L.read_kv(CACHE) or {}
    for i = 1, L.num(kv.count) do
        local f = L.fields(kv['a' .. i] or '')
        shows[#shows + 1] = { at = L.num(f[1]), ep = L.num(f[2]), total = L.num(f[3]), title = f[4] or '',
                              cover = f[5], url = f[6], progress = L.num(f[7]) }
    end
end

function W.tick()
    if L.DEMO then
        local now = os.time()
        shows = { { at = now + 2 * 3600 + 900, ep = 5, total = 12, title = 'Sousou no Frieren 2' },
                  { at = now + 26 * 3600, ep = 11, total = 24, title = 'Kusuriya no Hitorigoto 3' },
                  { at = now + 3 * 86400, ep = 1181, total = 0, title = 'ONE PIECE' },
                  { at = now + 4 * 86400, ep = 7, total = 13, title = 'Chainsaw Man' } }
        return
    end
    if L.due('fetch', 1800) then L.python('fetch.py', CACHE, COVERS, MODE, USER, LANG) end
    if L.due('read', 30) then load() end
end

function W.click(x, y)
    for _, r in ipairs(rows_drawn) do
        if y >= r[1] and y <= r[2] and r[3] and r[3] ~= '' then
            L.spawn('xdg-open ' .. L.quote(r[3]))
            return true
        end
    end
    return false
end

local function countdown(at)
    if at <= 0 then return L.tr('дата невідома', 'date unknown') end
    local s = at - os.time()
    if s <= 0 then return L.tr('вже вийшла', 'out now') end
    local d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d > 0 then return string.format(L.tr('за %d д %d год', 'in %dd %dh'), d, h) end
    if h > 0 then return string.format(L.tr('за %d год %d хв', 'in %dh %dm'), h, m) end
    return string.format(L.tr('за %d хв', 'in %dm'), math.max(m, 1))
end

local function episode(sh)
    if sh.ep <= 0 then return '' end
    local s = L.tr('Еп. ', 'Ep. ') .. sh.ep
    if sh.total > 0 then s = s .. '/' .. sh.total end
    return s
end

local function cover(cr, sh, x, y, w, h, r)
    local surf = L.image(sh.cover)
    if surf then
        L.cover(cr, surf, x, y, w, h, r)
    else
        L.rrect(cr, x, y, w, h, r)
        local g = L.vgrad(cr, y, y + h, L.shade(L.acc(L.SYS.pink), 0.2), L.mix(L.acc(L.SYS.pink), L.SYS.purple, 0.6))
        cairo_fill(cr); cairo_pattern_destroy(g)
        cairo_set_source_rgba(cr, 1, 1, 1, 0.9)
        L.icon_mid(cr, ICON.tv, math.min(w, h) * 0.45, x + w / 2, y + h / 2)
    end
end

local SUBTITLE = { airing = { 'онгоінги', 'airing' }, list = { 'мій список', 'my list' }, trending = { 'тренди', 'trending' } }

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    rows_drawn = {}
    local accent = L.acc(L.SYS.pink)
    local sub = SUBTITLE[MODE] or SUBTITLE.airing
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.tv, accent, L.tr('Аніме', 'Anime'), L.tr(sub[1], sub[2]))
    if #shows == 0 then
        L.font(cr, F.regular, 12); L.set(cr, C.text3)
        L.text(cr, L.tr('Завантаження з AniList…', 'Loading from AniList…'), x + w / 2, y + h / 2 + 10, 'c')
        return
    end

    if L.SIZE == 'small' then
        local sh = shows[1]
        cover(cr, sh, x + PAD, y + 44, 58, 82, 10)
        local tx, tw = x + PAD + 68, w - 2 * PAD - 68
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        for i, line in ipairs(L.wrap(cr, sh.title, tw, 3)) do L.text(cr, line, tx, y + 58 + (i - 1) * 15) end
        L.font(cr, F.medium, 11); L.set(cr, C.text2)
        L.text(cr, episode(sh), tx, y + 120)
        L.font(cr, F.semibold, 12); L.set(cr, accent)
        L.text(cr, L.fit(cr, countdown(sh.at), w - 2 * PAD), x + PAD, y + 148)
        rows_drawn[1] = { y + 40, y + h, sh.url }
        return
    end

    local ROW = 42
    local rows = math.min(#shows, math.floor((h - 48) / ROW))
    for i = 1, rows do
        local sh = shows[i]
        local ry = y + 44 + (i - 1) * ROW
        cover(cr, sh, x + PAD, ry, 26, 37, 6)
        local tx = x + PAD + 36
        local cd = countdown(sh.at)
        L.font(cr, F.semibold, 11.5)
        local cw = L.width(cr, cd)
        L.set(cr, i == 1 and accent or C.text2)
        L.text(cr, cd, x + w - PAD, ry + 15, 'r')
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, sh.title, w - 2 * PAD - 36 - cw - 10), tx, ry + 15)
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        local extra = (MODE == 'list' and sh.progress > 0) and (L.tr(' · переглянуто ', ' · seen ') .. sh.progress) or ''
        L.text(cr, episode(sh) .. extra, tx, ry + 31)
        rows_drawn[#rows_drawn + 1] = { ry - 2, ry + ROW - 2, sh.url }
    end
end

return W
