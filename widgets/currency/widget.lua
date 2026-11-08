-- Hryvnia rates (bank.gov.ua) with the change since the previous day.
-- small: 3 codes. medium: 3 with names. large: up to 7 with names.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local CODES = L.opt_list('codes', 'USD,EUR,PLN')
local API = 'https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json'
local rates = {}
local fetched

function W.tick()
    if L.DEMO then
        rates = { date = '12.10.2026', USD = '41.2894|41.1712|Долар США', EUR = '48.3138|48.4021|Євро',
                  PLN = '11.3813|11.3550|Злотий', GBP = '55.2|55.1|Фунт стерлінгів' }
        return
    end
    local body, _, _, st = L.http(API, { every = 3600 })
    if not body then return end
    local today = L.json(body)
    if type(today) ~= 'table' or not today[1] then return end
    -- the day before, for the change arrows
    local d, m, y = today[1].exchangedate:match('^(%d+)%.(%d+)%.(%d+)$')
    local prev_day = os.date('%Y%m%d', os.time({ year = tonumber(y), month = tonumber(m), day = tonumber(d) - 1, hour = 12 }))
    local before = L.http(API .. '&date=' .. prev_day, { every = 43200 })
    if not before then return end
    local key = tostring(st) .. before:len()
    if key == fetched then return end
    fetched = key
    local prev = {}
    for _, r in ipairs(L.json(before) or {}) do prev[r.cc] = r.rate end
    local list = { date = today[1].exchangedate }
    for _, r in ipairs(today) do
        local name = tostring(r.txt):gsub('%s+', ' '):gsub('|', '/')
        list[r.cc] = string.format('%s|%s|%s', r.rate, prev[r.cc] or r.rate, name)
    end
    rates = list
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local named = L.SIZE ~= 'small'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.cash, L.acc(L.SYS.green), L.tr('Курс НБУ', 'NBU rates'),
             named and rates.date and (L.tr('на ', 'for ') .. rates.date) or nil)
    if not rates.date then
        L.font(cr, F.regular, 11.5); L.set(cr, C.text3)
        L.text(cr, L.tr('Завантаження…', 'Loading…'), x + PAD, y + 70)
        return
    end
    local up, down = L.SYS.green, L.SYS.red
    if L.STYLE == 'color' then up, down = C.text, C.text end
    local rows = math.min(#CODES, math.floor((h - 50) / 35))
    for i = 1, rows do
        local code = CODES[i]
        local f = L.fields(rates[code] or '')
        local rate, prev = L.num(f[1]), L.num(f[2])
        local ry = y + 62 + (i - 1) * 35
        L.font(cr, F.semibold, 15); L.set(cr, C.text)
        L.text(cr, code, x + PAD, ry)
        if named and f[3] and L.LANG == 'uk' then
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, L.fit(cr, f[3], w - 2 * PAD - 160), x + PAD + 46, ry)
        end
        if rate > 0 then
            local diff = rate - prev
            L.font(cr, F.semibold, 16); L.set(cr, C.text)
            local rx = L.text(cr, string.format('%.2f', rate), x + w - PAD, ry, 'r')
            if math.abs(diff) >= 0.005 then
                L.font(cr, F.medium, 10); L.set(cr, diff > 0 and up or down)
                local dx = L.text(cr, string.format('%.2f', math.abs(diff)), x + w - PAD, ry + 14, 'r')
                L.icon(cr, diff > 0 and ICON.tri_up or ICON.tri_down, 12, dx - 1, ry + 16, 'r')
            end
            if named then
                L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
                L.text(cr, '₴', rx - 4, ry, 'r')
            end
        else
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, L.tr('немає даних', 'no data'), x + w - PAD, ry, 'r')
        end
    end
end

return W
