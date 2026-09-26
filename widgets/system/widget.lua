-- System. small: CPU + memory rings. medium: CPU, memory, disk, temperature. large: same four, bigger
local L = require 'lib'
local C, F, PAD, ICON, RING = L.C, L.F, L.PAD, L.ICON, L.RING

local W = {}

local UNIT = { B = 1, KiB = 2 ^ 10, MiB = 2 ^ 20, GiB = 2 ^ 30, TiB = 2 ^ 40 }
local function bytes(s)
    local n, u = s:match('([%d%.]+)%s*(%a*)')
    return L.num(n) * (UNIT[u] or 1)
end
local function gib(b)
    local g = b / 2 ^ 30
    return g >= 100 and string.format('%.0f', g) or string.format('%.1f', g)
end
local GB = L.tr(' ГБ', ' GB')

local function meminfo()
    local s = L.read_file('/proc/meminfo') or ''
    local total = L.num(s:match('MemTotal:%s+(%d+)'))
    local avail = L.num(s:match('MemAvailable:%s+(%d+)'))
    return (total - avail) * 1024, math.max(total, 1) * 1024
end

local function uptime_text()
    local up = L.num((L.read_file('/proc/uptime') or ''):match('^(%S+)'))
    local d, h, m = math.floor(up / 86400), math.floor(up % 86400 / 3600), math.floor(up % 3600 / 60)
    if d > 0 then return string.format(L.tr('%d д %d год', '%dd %dh'), d, h) end
    if h > 0 then return string.format(L.tr('%d год %d хв', '%dh %dm'), h, m) end
    return string.format(L.tr('%d хв', '%dm'), m)
end

local function items(n)
    local cp = L.cp
    local cpu = L.num(cp('${cpu cpu0}'))
    local used, total = meminfo()
    local out = {
        { cpu / 100, RING.cpu, tostring(cpu), '%', L.tr('Процесор', 'CPU'), string.format(L.tr('%.1f ГГц', '%.1f GHz'), L.num(cp('${freq_g}'))) },
        { used / total, RING.mem, string.format('%.0f', used / total * 100), '%', L.tr('Пам’ять', 'Memory'), gib(used) .. ' / ' .. gib(total) .. GB },
    }
    if n > 2 then
        local dperc = L.num(cp('${fs_used_perc /}'))
        local ctemp = L.num(cp('${hwmon k10temp temp 1}'))
        if ctemp == 0 then ctemp = L.num(cp('${hwmon coretemp temp 1}')) end
        local tring = ctemp >= 85 and RING.hot or ctemp >= 70 and RING.warm or RING.warn
        out[3] = { dperc / 100, RING.disk, tostring(dperc), '%', L.tr('Диск', 'Disk'),
                   gib(bytes(cp('${fs_used /}'))) .. ' / ' .. gib(bytes(cp('${fs_size /}'))) .. GB }
        local gpu = cp('${hwmon amdgpu temp 1}')
        out[4] = { ctemp / 100, tring, ctemp .. '°', '', L.tr('Температура', 'Temperature'),
                   (gpu ~= '' and gpu ~= '0') and ('GPU ' .. gpu .. '°') or '' }
    end
    -- a chosen accent (or an anime style) recolours every ring in its shades
    if L.ACCENT or L.T.hi or L.STYLE == 'color' then
        local base = L.pair(RING.cpu)
        for i, it in ipairs(out) do
            local k = (i - 1) * 0.12
            it[2] = { L.shade(base[1], k), L.shade(base[2], k) }
        end
    end
    return out
end

local function draw_ring(cr, it, cx, cy, r, lw, vs, ly, dy, colw)
    L.ring(cr, cx, cy, r, lw, it[1], it[2][1], it[2][2])
    L.set(cr, C.text)
    L.value_unit(cr, it[3], it[4], cx, cy + vs * 0.35, F.semibold, vs, F.medium, vs * 0.62)
    L.font(cr, F.medium, 11.5 * (vs / 16) ^ 0.4); L.set(cr, C.text)
    L.text(cr, it[5], cx, ly, 'c')
    L.font(cr, F.regular, 10.5 * (vs / 16) ^ 0.4); L.set(cr, C.text3)
    L.text(cr, L.fit(cr, it[6], colw - 4), cx, ly + dy, 'c')
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local size = L.SIZE
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.gauge, L.acc(RING.cpu[2]), L.tr('Система', 'System'),
             size ~= 'small' and uptime_text() or nil, ICON.timer)

    if size == 'large' then
        local list = items(4)
        local qw, qh = (w - 2 * PAD) / 2, (h - 48) / 2
        for i, it in ipairs(list) do
            local col, row = (i - 1) % 2, (i - 1) // 2
            local cx = x + PAD + qw * (col + 0.5)
            local qy = y + 44 + qh * row
            draw_ring(cr, it, cx, qy + 58, 44, 10, 24, qy + 128, 17, qw)
        end
        return
    end

    local list = items(size == 'small' and 2 or 4)
    local colw = (w - 2 * PAD) / #list
    for i, it in ipairs(list) do
        draw_ring(cr, it, x + PAD + colw * (i - 0.5), y + 80, 26, 7.5, 16, y + 131, 17, colw)
    end
end

return W
