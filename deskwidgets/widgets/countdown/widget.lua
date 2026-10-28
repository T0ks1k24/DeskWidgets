-- Countdown: days left until a date. medium adds details and progress. large is small scaled up.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = { scale = { large = 'small' } }
local TITLE = L.opt('title', L.tr('Новий рік', 'New Year'))
local now_t = os.date('*t')

local function parse(s)
    local y, m, d = (s or ''):match('^(%d%d%d%d)-(%d%d?)-(%d%d?)$')
    if not y then return nil end
    return os.time({ year = L.num(y), month = L.num(m), day = L.num(d), hour = 0 })
end
local TARGET = parse(L.opt('date', '')) or os.time({ year = now_t.year + 1, month = 1, day = 1, hour = 0 })
local SINCE = parse(L.opt('since', ''))

local function midnight(ts)
    local d = os.date('*t', ts)
    return os.time({ year = d.year, month = d.month, day = d.day, hour = 0 })
end

local function unit(n) return L.tr(L.plural(n, 'день', 'дні', 'днів'), n == 1 and 'day' or 'days') end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local accent = L.acc(L.SYS.orange)
    L.set(cr, accent); L.icon(cr, ICON.hourglass, 13, x + PAD, y + 31)
    L.font(cr, F.semibold, 13); L.set(cr, C.text2)
    L.text(cr, L.fit(cr, TITLE, w - 2 * PAD - 18), x + PAD + 18, y + 30)

    local days = math.floor((TARGET - midnight(os.time())) / 86400 + 0.5)
    local t = os.date('*t', TARGET)
    local date_line = L.day_month(t) .. ' ' .. t.year
    local n = math.abs(days)

    if days == 0 then
        L.font(cr, F.semibold, 30); L.set(cr, accent)
        L.text(cr, L.tr('Сьогодні!', 'Today!'), x + PAD, y + 100)
    else
        L.font(cr, F.light, 64); L.set(cr, C.text)
        local nw = L.width(cr, tostring(n))
        L.text(cr, tostring(n), x + PAD - 3, y + 106)
        L.font(cr, F.medium, 15); L.set(cr, C.text2)
        L.text(cr, unit(n), x + PAD + nw + 4, y + 106)
        if days < 0 then
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, L.tr('минуло', 'ago'), x + PAD, y + 125)
        end
    end

    if L.SIZE == 'medium' then
        -- right column: date, weeks/days and a progress bar from the start date
        local rx = x + w - PAD
        L.font(cr, F.medium, 13); L.set(cr, C.text)
        L.text(cr, date_line, rx, y + 62, 'r')
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, L.WEEKDAY_CAP[t.wday], rx, y + 80, 'r')
        if days > 0 then
            local weeks = days // 7
            L.text(cr, weeks > 0 and (weeks .. L.tr(' тиж. ', ' wk ') .. (days % 7) .. L.tr(' дн.', ' d')) or '', rx, y + 98, 'r')
        end
        if SINCE and TARGET > SINCE then
            local frac = (os.time() - SINCE) / (TARGET - SINCE)
            local pair = L.pair(L.ring_from(L.SYS.orange))
            L.bar(cr, x + PAD, y + 136, w - 2 * PAD, 6, frac, pair[1], pair[2])
            L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
            L.text(cr, string.format('%.0f%%', math.max(0, math.min(frac, 1)) * 100), rx, y + 156, 'r')
        else
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, (days >= 0 and L.tr('до ', 'until ') or '') .. date_line, x + PAD, y + 150)
        end
        return
    end
    L.font(cr, F.regular, 11); L.set(cr, C.text3)
    L.text(cr, L.fit(cr, (days >= 0 and L.tr('до ', 'until ') or '') .. date_line, w - 2 * PAD), x + PAD, y + 150)
end

return W
