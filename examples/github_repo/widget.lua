-- GitHub repository: stars, forks and open issues. Shows the usual pattern for online data:
-- a helper script downloads in the background (L.python + L.due), the widget reads its cache.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local REPO = L.opt('repo', 'brndnmtthws/conky')
local CACHE = L.RUN .. '/github-' .. L.key(REPO)
local data = {}

function W.tick()
    if L.DEMO then
        data = { name = REPO, stars = '7612', forks = '637', issues = '142', language = 'C++',
                 description = 'Light-weight system monitor for X, Wayland and other things, too' }
        return
    end
    if L.due('fetch', 1800) then L.python('fetch.py', CACHE, REPO) end   -- every 30 minutes
    if L.due('read', 30) then data = L.read_kv(CACHE) or {} end
end

local function stat(cr, x, y, icon, value, label)
    L.set(cr, L.acc(C.text2)); L.icon(cr, icon, 14, x, y)
    L.font(cr, F.semibold, 18); L.set(cr, C.text)
    L.text(cr, value, x + 20, y + 1)
    L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
    L.text(cr, label, x, y + 18)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, 0xF02A4, L.acc(C.text), 'GitHub')   -- 0xF02A4 = mdi-github
    if not data.name then
        L.font(cr, F.regular, 12); L.set(cr, C.text3)
        L.text(cr, L.tr('Завантаження…', 'Loading…'), x + PAD, y + 70)
        return
    end
    L.font(cr, F.semibold, 14); L.set(cr, C.text)
    L.text(cr, L.fit(cr, data.name, w - 2 * PAD), x + PAD, y + 58)
    if L.SIZE == 'medium' then
        L.font(cr, F.regular, 11.5); L.set(cr, C.text2)
        for i, line in ipairs(L.wrap(cr, data.description or '', w - 2 * PAD, 2)) do L.text(cr, line, x + PAD, y + 78 + (i - 1) * 15) end
    end
    local sy = y + h - 36
    local col = (w - 2 * PAD) / 3
    stat(cr, x + PAD, sy, ICON.star, data.stars or '?', L.tr('зірки', 'stars'))
    stat(cr, x + PAD + col, sy, 0xF02A5, data.forks or '?', L.tr('форки', 'forks'))       -- mdi-source-fork
    stat(cr, x + PAD + 2 * col, sy, 0xF0028, data.issues or '?', L.tr('задачі', 'issues')) -- mdi-alert-circle
end

return W
