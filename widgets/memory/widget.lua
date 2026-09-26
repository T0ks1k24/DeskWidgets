-- Memory. small: RAM ring + swap bar. medium: RAM and swap rings + details. large: small scaled up
local L = require 'lib'
local C, F, PAD, ICON, RING = L.C, L.F, L.PAD, L.ICON, L.RING

local W = { scale = { large = 'small' } }
local GB = L.tr(' ГБ', ' GB')

local function gib(kib)
    local g = kib / 2 ^ 20
    return g >= 100 and string.format('%.0f', g) or string.format('%.1f', g)
end

local function info()
    local s = L.read_file('/proc/meminfo') or ''
    local function kv(key) return L.num(s:match(key .. ':%s+(%d+)')) end
    local total, avail = math.max(kv('MemTotal'), 1), kv('MemAvailable')
    return { total = total, used = total - avail, cached = kv('Cached') + kv('Buffers'),
             stotal = kv('SwapTotal'), sused = kv('SwapTotal') - kv('SwapFree') }
end

function W.draw(cr, x, y, w, h)
    local m = info()
    local ring = L.pair(RING.mem)
    local swap = (L.ACCENT or L.T.hi or L.STYLE == 'color') and { L.shade(ring[1], 0.25), L.shade(ring[2], 0.25) } or RING.swap
    L.card(cr, x, y, w, h)
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.memory, ring[2], L.tr('Пам’ять', 'Memory'),
             L.SIZE == 'medium' and (L.tr('кеш ', 'cache ') .. gib(m.cached) .. GB) or nil)

    if L.SIZE == 'medium' then
        local items = { { m.used / m.total, ring, L.tr('Оперативна', 'RAM'), gib(m.used) .. ' / ' .. gib(m.total) .. GB } }
        if m.stotal > 0 then
            items[2] = { m.sused / m.stotal, swap, L.tr('Своп', 'Swap'), gib(m.sused) .. ' / ' .. gib(m.stotal) .. GB }
        end
        local colw = (w - 2 * PAD) / #items
        for i, it in ipairs(items) do
            local cx, cy = x + PAD + colw * (i - 0.5), y + 82
            L.ring(cr, cx, cy, 30, 8, it[1], it[2][1], it[2][2])
            L.set(cr, C.text)
            L.value_unit(cr, string.format('%.0f', it[1] * 100), '%', cx, cy + 6, F.semibold, 18, F.medium, 10)
            L.font(cr, F.medium, 11.5); L.set(cr, C.text)
            L.text(cr, it[3], cx, y + 134, 'c')
            L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
            L.text(cr, it[4], cx, y + 151, 'c')
        end
        return
    end

    local cx, cy = x + w / 2, y + 82
    L.ring(cr, cx, cy, 32, 8, m.used / m.total, ring[1], ring[2])
    L.set(cr, C.text)
    L.value_unit(cr, string.format('%.0f', m.used / m.total * 100), '%', cx, cy + 6.5, F.semibold, 19, F.medium, 10.5)
    L.font(cr, F.medium, 11.5); L.set(cr, C.text2)
    L.text(cr, gib(m.used) .. ' / ' .. gib(m.total) .. GB, cx, y + 133, 'c')
    if m.stotal > 0 then
        L.font(cr, F.regular, 10); L.set(cr, C.text3)
        L.text(cr, L.tr('своп', 'swap'), x + PAD, y + 151)
        L.text(cr, gib(m.sused) .. GB, x + w - PAD, y + 151, 'r')
        L.bar(cr, x + PAD + 30, y + 145, w - 2 * PAD - 30 - 44, 5, m.sused / m.stotal, swap[1], swap[2])
    end
end

return W
