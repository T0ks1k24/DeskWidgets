-- Batteries: a ring for the laptop battery. Bluetooth devices (mouse, headphones, etc.) show up next
-- to it when UPower reports their charge, like the macOS Batteries widget.
-- small: up to 3 rings. medium: up to 4 rings with names. large: small scaled up.
local L = require 'lib'
local C, F, PAD, ICON, RING = L.C, L.F, L.PAD, L.ICON, L.RING

local W = { scale = { large = 'small' } }
local laptop = { pct = 0, status = 'Unknown' }
local devices = {}

local KIND_ICON = {
    mouse = ICON.mouse, keyboard = ICON.keyboard, headset = ICON.headset, headphones = ICON.headphones,
    phone = ICON.phone, tablet = ICON.tablet, ['gaming-input'] = ICON.gamepad,
}

local function read_laptop()
    local b = L.sys.battery()
    if not b then laptop = nil return end
    local status = b.full and 'Full' or b.charging and 'Charging' or b.plugged and 'Not charging' or 'Discharging'
    laptop = { pct = b.percent, status = status, hours = b.secs_left and b.secs_left / 3600 or nil }
end

-- peripherals the host reports (Bluetooth mice and headphones, wireless gaming mice): see providers/batteries.py
local hid_denied = ''

local function read_peripherals()
    devices = {}
    local data = L.service('batteries')
    if not data then return end
    hid_denied = data.denied or ''
    for _, d in ipairs(data.devices or {}) do
        devices[#devices + 1] = {
            name = d.name, pct = d.percent, charging = d.charging, online = d.online,
            icon = KIND_ICON[d.kind] or ICON.bluetooth,
        }
    end
end

function W.tick()
    if L.DEMO then
        laptop = { pct = 76, status = 'Discharging', hours = 3.2 }
        devices = { { pct = 58, icon = ICON.headphones, name = L.tr('Навушники', 'Headphones'), online = true },
                    { pct = 87, icon = ICON.mouse, name = L.tr('Миша', 'Mouse'), online = true } }
        return
    end
    if L.due('laptop', 10) then read_laptop() end
    read_peripherals()
end

local function duration(hrs)
    local m = math.floor(hrs * 60 + 0.5)
    if m >= 60 then return string.format(L.tr('%d год %d хв', '%dh %dm'), m // 60, m % 60) end
    return string.format(L.tr('%d хв', '%dm'), m)
end

local function status_text(b)
    if b.status == 'Full' or (b.status == 'Not charging' and b.pct >= 95) then return L.tr('Заряджено', 'Charged'), true end
    if b.status == 'Charging' then
        return b.hours and (L.tr('До повного ', 'Full in ') .. duration(b.hours)) or L.tr('Заряджання', 'Charging'), true
    end
    if b.status == 'Not charging' then return L.tr('Від мережі', 'Plugged in'), true end
    if b.hours then return L.tr('Залишилось ', '') .. duration(b.hours) .. L.tr('', ' left'), false end
    return L.tr('Від батареї', 'On battery'), false
end

local function colours(pct, plugged)
    if pct <= 10 and not plugged then return RING.hot end
    if pct <= 20 and not plugged then return RING.warn end
    return L.pair(RING.battery)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local cx = x + w / 2
    if not laptop then
        L.set(cr, C.text3); L.icon_mid(cr, ICON.battery, 30, cx, y + 70)
        L.font(cr, F.medium, 12); L.set(cr, C.text2)
        L.text(cr, L.tr('Батарею не знайдено', 'No battery found'), cx, y + 118, 'c')
        return
    end
    local status, plugged = status_text(laptop)
    -- a mouse that needs the udev rule is named instead of being silently missing
    if hid_denied ~= '' and #devices == 0 then
        status = L.tr('Миша: потрібен доступ — deskwidgets doctor', 'Mouse: needs access — deskwidgets doctor')
    end
    local ring_c = colours(laptop.pct, plugged)
    local medium = L.SIZE == 'medium'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, plugged and ICON.flash or ICON.laptop,
             plugged and L.acc(L.SYS.yellow) or ring_c[2], L.tr('Батарея', 'Battery'), medium and status or nil)

    local items = { { pct = laptop.pct, icon = ICON.laptop, c = ring_c, name = L.tr('Ноутбук', 'Laptop'), online = true } }
    for i = 1, math.min(#devices, medium and 3 or 2) do
        local d = devices[i]
        local asleep = not d.online
        items[#items + 1] = {
            pct = d.pct, icon = d.icon, online = d.online,
            c = asleep and { L.ink(0.35), L.ink(0.55) } or colours(d.pct or 0, d.charging),
            name = d.name .. (asleep and L.tr(' · сон', ' · asleep') or ''),
        }
    end

    if #items == 1 and not medium then
        local cy = y + 90
        L.ring(cr, cx, cy, 35, 8, laptop.pct / 100, ring_c[1], ring_c[2])
        L.set(cr, C.text)
        L.value_unit(cr, tostring(laptop.pct), '%', cx, cy + 7, F.semibold, 21, F.medium, 11)
        L.font(cr, F.regular, 11); L.set(cr, C.text2)
        L.text(cr, L.fit(cr, status, w - 2 * PAD), cx, y + 151, 'c')
        return
    end

    local n = medium and math.max(#items, 2) or #items
    local r, lw = 27, 6.5
    if n == 3 and not medium then r, lw = 17.5, 5.5 end
    local colw = (w - 2 * PAD) / n
    for i, it in ipairs(items) do
        local icx, cy = x + PAD + colw * (i - 0.5), y + 84
        L.ring(cr, icx, cy, r, lw, (it.pct or 0) / 100, it.c[1], it.c[2])
        L.set(cr, it.online and C.text or C.text3); L.icon_mid(cr, it.icon, (n == 3 and not medium) and 13 or 18, icx, cy)
        L.set(cr, it.online and C.text or C.text3)
        if it.pct then
            L.value_unit(cr, tostring(math.floor(it.pct + 0.5)), '%', icx, y + 134, F.semibold, 13.5, F.medium, 9.5)
        else
            L.font(cr, F.semibold, 13.5); L.text(cr, '—', icx, y + 134, 'c')
        end
        if medium then
            L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
            L.text(cr, L.fit(cr, it.name, colw - 6), icx, y + 152, 'c')
        end
    end
    if not medium then
        L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, status, w - 2 * PAD), cx, y + 156, 'c')
    end
end

return W
