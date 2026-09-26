-- Processor. small: total load ring and the busiest process. medium: per-core bars and top 3.
-- large: taller bars and up to 9 processes
local L = require 'lib'
local C, F, PAD, ICON, RING = L.C, L.F, L.PAD, L.ICON, L.RING

local W = {}
local UNIT = { B = L.tr('Б', 'B'), KiB = L.tr('КБ', 'KB'), MiB = L.tr('МБ', 'MB'), GiB = L.tr('ГБ', 'GB'), TiB = L.tr('ТБ', 'TB') }

local ncores = 0
for _ in (L.read_file('/proc/stat') or ''):gmatch('\ncpu%d+') do ncores = ncores + 1 end

local function bars_pair() return L.pair(RING.cpu) end

local function processes(cr, x, y, w, rows, pair)
    local cp = L.cp
    for i = 1, rows do
        local by = y + (i - 1) * 22
        local name = L.trim(cp('${top name ' .. i .. '}'))
        local pcpu = L.num(cp('${top cpu ' .. i .. '}'))
        local mem = L.trim(cp('${top mem_res ' .. i .. '}'))
        cairo_arc(cr, x + 3, by - 4, 3, 0, 2 * math.pi)
        L.set(cr, L.mix(pair[1], pair[2], math.min((i - 1) / 2, 1)), math.max(1 - (i - 1) * 0.12, 0.35))
        cairo_fill(cr)
        L.font(cr, F.medium, 12.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, name, w - 150), x + 14, by)
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        L.text(cr, string.format('%.1f%%', pcpu), x + w, by, 'r')
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        local mn, mu = mem:match('([%d%.]+)(%a+)')
        L.text(cr, mn and (mn .. ' ' .. (UNIT[mu] or mu)) or mem, x + w - 62, by, 'r')
    end
end

local function bars(cr, x, top, gw, bh, pair)
    local n = math.max(ncores, 1)
    local cw = math.min(14, (gw - (n - 1) * 5) / n)
    local gap = n > 1 and (gw - n * cw) / (n - 1) or 0
    for i = 1, n do
        local bx = x + (i - 1) * (cw + gap)
        local frac = math.min(L.num(L.cp('${cpu cpu' .. i .. '}')) / 100, 1)
        L.rrect(cr, bx, top, cw, bh, cw / 2)
        L.set(cr, L.track)
        cairo_fill_preserve(cr)
        cairo_save(cr)
        cairo_clip(cr)
        local fh = math.max(frac * bh, frac > 0 and 3 or 0)
        cairo_rectangle(cr, bx, top + bh - fh, cw, fh)
        local g = L.vgrad(cr, top, top + bh, pair[2], pair[1])
        cairo_fill(cr)
        cairo_pattern_destroy(g)
        cairo_restore(cr)
    end
end

function W.draw(cr, x, y, w, h)
    local cp = L.cp
    local pair = bars_pair()
    local accent = L.acc(RING.cpu[2])
    L.card(cr, x, y, w, h)
    local small = L.SIZE == 'small'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.pulse, accent, L.tr('Процесор', 'Processor'),
             not small and (L.tr('навант. ', 'load ') .. cp('${loadavg 1}') .. ' · ' .. cp('${processes}') .. L.tr(' процесів', ' processes')) or nil)

    if small then
        local total = L.num(cp('${cpu cpu0}'))
        local cx, cy = x + w / 2, y + 80
        L.ring(cr, cx, cy, 30, 8, total / 100, pair[1], pair[2])
        L.set(cr, C.text)
        L.value_unit(cr, tostring(total), '%', cx, cy + 6, F.semibold, 18, F.medium, 10.5)
        L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
        L.text(cr, L.tr('навант. ', 'load ') .. cp('${loadavg 1}'), cx, y + 130, 'c')
        L.font(cr, F.medium, 11.5); L.set(cr, C.text2)
        L.text(cr, L.fit(cr, L.trim(cp('${top name 1}')), w - 2 * PAD), cx, y + 149, 'c')
        return
    end

    if L.SIZE == 'large' then
        bars(cr, x + PAD, y + 46, w - 2 * PAD, 70, pair)
        L.divider(cr, x + PAD, y + 132, w - 2 * PAD)
        processes(cr, x + PAD, y + 158, w - 2 * PAD, math.min(9, math.floor((h - 158 + 8) / 22)), pair)
        return
    end
    bars(cr, x + PAD, y + 44, w - 2 * PAD, 40, pair)
    processes(cr, x + PAD, y + 106, w - 2 * PAD, 3, pair)
end

return W
