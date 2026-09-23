-- Digital clock: big time + date. small, medium (time + date block), large (small scaled up)
local L = require 'lib'
local C, F, PAD = L.C, L.F, L.PAD

local W = { scale = { large = 'small' } }
local SECONDS = L.opt_bool('seconds', false)
local DATE = L.opt_bool('date', true)
local H12 = L.opt_bool('h12', false)

local function time_parts()
    local t = os.date('*t')
    local hour = t.hour
    local suffix = ''
    if H12 then
        suffix = hour < 12 and 'AM' or 'PM'
        hour = hour % 12
        if hour == 0 then hour = 12 end
    end
    return t, string.format(H12 and '%d:%02d' or '%02d:%02d', hour, t.min), string.format('%02d', t.sec), suffix
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local t, hm, ss, suffix = time_parts()
    local accent = L.acc(L.SYS.orange)
    local small_txt = SECONDS and ss or suffix

    if L.SIZE == 'medium' then
        L.font(cr, F.light, 70); L.set(cr, C.text)
        local tw = L.width(cr, hm)
        L.text(cr, hm, x + PAD - 2, y + h / 2 + 26)
        if small_txt ~= '' then
            L.font(cr, F.regular, 22); L.set(cr, C.text3)
            L.text(cr, small_txt, x + PAD + tw + 2, y + h / 2 + 26)
        end
        if DATE then
            local rx = x + w - PAD
            L.font(cr, F.bold, 12); L.set(cr, accent)
            L.spaced(cr, L.WEEKDAY[t.wday], rx, y + 54, 0.8, 'r')
            L.font(cr, F.light, 40); L.set(cr, C.text)
            L.text(cr, tostring(t.day), rx, y + 100, 'r')
            L.font(cr, F.medium, 13); L.set(cr, C.text2)
            L.text(cr, L.tr(L.MONTH_GEN[t.month], L.MONTH[t.month]) .. ' ' .. t.year, rx, y + 122, 'r')
        end
        return
    end

    local cx = x + w / 2
    if DATE then
        L.font(cr, F.bold, 11.5); L.set(cr, accent)
        L.spaced(cr, L.WEEKDAY[t.wday], cx, y + 36, 0.8, 'c')
    end
    local big = small_txt ~= '' and 44 or 52
    L.font(cr, F.light, big)
    local tw = L.width(cr, hm)
    local sw = 0
    if small_txt ~= '' then L.font(cr, F.regular, 18); sw = L.width(cr, small_txt) + 3 end
    local tx = cx - (tw + sw) / 2
    L.font(cr, F.light, big); L.set(cr, C.text)
    L.text(cr, hm, tx, y + (DATE and 100 or 104))
    if small_txt ~= '' then
        L.font(cr, F.regular, 18); L.set(cr, C.text3)
        L.text(cr, small_txt, tx + tw + 3, y + (DATE and 100 or 104))
    end
    if DATE then
        L.font(cr, F.medium, 13); L.set(cr, C.text2)
        L.text(cr, L.day_month(t), cx, y + 134, 'c')
    end
end

return W
