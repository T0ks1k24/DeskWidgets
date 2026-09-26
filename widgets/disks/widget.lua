-- Disks. small: the system disk as a ring. medium/large: every real partition as a bar
local L = require 'lib'
local C, F, PAD, ICON, RING = L.C, L.F, L.PAD, L.ICON, L.RING

local W = {}
local disks = {}
local REAL = { ext4 = true, ext3 = true, btrfs = true, xfs = true, f2fs = true, ntfs = true, ntfs3 = true,
               fuseblk = true, exfat = true, zfs = true, vfat = false }

local function name_of(target)
    if target == '/' then return L.tr('Система', 'System') end
    if target == L.HOME then return L.tr('Домашня тека', 'Home') end
    return target:match('([^/]+)$') or target
end

local function read_disks()
    disks = {}
    local seen = {}
    for line in L.popen('df -B1 --output=source,target,fstype,size,used'):gmatch('[^\n]+') do
        local src, target, fstype, size, used = line:match('^(%S+)%s+(%S+)%s+(%S+)%s+(%d+)%s+(%d+)$')
        if src and REAL[fstype] and not seen[src] and not target:match('^/snap') and not target:match('^/boot') then
            seen[src] = true
            disks[#disks + 1] = { name = name_of(target), size = L.num(size), used = L.num(used) }
        end
    end
end

function W.tick()
    if L.DEMO then
        if #disks == 0 then
            disks = { { name = L.tr('Система', 'System'), size = 422e9, used = 318e9 }, { name = 'storage', size = 81e9, used = 36e9 },
                      { name = 'backup', size = 1000e9, used = 412e9 } }
        end
        return
    end
    if L.due('df', 60) then read_disks() end
end

local function gb(b)
    local g = b / 1e9
    if g >= 1000 then return string.format(L.tr('%.1f ТБ', '%.1f TB'), g / 1000) end
    return string.format(L.tr('%.0f ГБ', '%.0f GB'), g)
end

local function colours(frac)
    if frac >= 0.9 then return RING.hot end
    if frac >= 0.75 and not (L.ACCENT or L.T.hi) then return RING.warn end
    return L.pair(RING.disk)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.harddisk, L.acc(RING.disk[2]), L.tr('Диски', 'Disks'))
    if #disks == 0 then return end
    local of = L.tr(' з ', ' of ')

    if L.SIZE == 'small' then
        local d = disks[1]
        local frac = d.used / math.max(d.size, 1)
        local c = colours(frac)
        local cx, cy = x + w / 2, y + 82
        L.ring(cr, cx, cy, 32, 8, frac, c[1], c[2])
        L.set(cr, C.text)
        L.value_unit(cr, string.format('%.0f', frac * 100), '%', cx, cy + 6.5, F.semibold, 19, F.medium, 10.5)
        L.font(cr, F.medium, 11.5); L.set(cr, C.text2)
        L.text(cr, d.name, cx, y + 133, 'c')
        L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
        L.text(cr, gb(d.used) .. of .. gb(d.size), cx, y + 150, 'c')
        return
    end

    local rows = math.floor((h - 50) / 37)
    for i = 1, math.min(#disks, rows) do
        local d = disks[i]
        local frac = d.used / math.max(d.size, 1)
        local c = colours(frac)
        local ry = y + 60 + (i - 1) * 37
        L.font(cr, F.medium, 12.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, d.name, w * 0.42), x + PAD, ry)
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, gb(d.used) .. of .. gb(d.size) .. ' · ' .. string.format('%.0f%%', frac * 100), x + w - PAD, ry, 'r')
        L.bar(cr, x + PAD, ry + 8, w - 2 * PAD, 6, frac, c[1], c[2])
    end
end

return W
