-- Up next: events from the GNOME calendars. The service keeps RUN/events current, so new
-- events show up within seconds. small: the next 2. medium: 3. large: up to 8.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local events = {}

local function load_events()
    events = {}
    local kv = L.read_kv(L.RUN .. '/events')
    if not kv then return end
    for i = 1, L.num(kv.count) do
        local v = kv['e' .. i]
        if v then
            local f = L.fields(v)
            events[#events + 1] = {
                s = L.num(f[1]), e = L.num(f[2]), allday = f[3] == '1',
                color = (f[4] or ''):match('^#(%x%x%x%x%x%x)$'),
                title = table.concat(f, '|', 6),
            }
        end
    end
end

function W.tick()
    if L.DEMO then
        local t = os.date('*t'); local today = os.time({ year = t.year, month = t.month, day = t.day, hour = 0 })
        events = {
            { s = os.time() - 1800, e = os.time() + 1800, color = '62A0EA', title = L.tr('Стендап команди', 'Team stand-up') },
            { s = today + 86400 + 10 * 3600, e = today + 86400 + 11.5 * 3600, color = 'FA573C', title = L.tr('Зустріч щодо проєкту', 'Project meeting') },
            { s = today + 2 * 86400, e = today + 3 * 86400, allday = true, color = '16A765', title = L.tr('День народження Олі', 'Olivia’s birthday') },
            { s = today + 3 * 86400 + 18 * 3600, e = today + 3 * 86400 + 19 * 3600, color = '9FE1E7', title = L.tr('Англійська', 'Spanish class') },
        }
        return
    end
    if L.due('events', 5) then load_events() end
end

local function midnight(ts)
    local d = os.date('*t', ts)
    return os.time({ year = d.year, month = d.month, day = d.day, hour = 0 })
end

local function day_label(ts)
    local diff = math.floor((midnight(ts) - midnight(os.time())) / 86400 + 0.5)
    if diff == 0 then return L.tr('Сьогодні', 'Today') end
    if diff == 1 then return L.tr('Завтра', 'Tomorrow') end
    local d = os.date('*t', ts)
    return L.WDAY_SHORT[d.wday] .. ', ' .. L.day_month(d)
end

local function hm(ts) return os.date('%H:%M', ts) end

local function relative(sec)
    local m = math.floor(sec / 60 + 0.5)
    if m < 60 then return L.tr('за ', 'in ') .. math.max(m, 1) .. L.tr(' хв', ' min') end
    return string.format(L.tr('за %d год %d хв', 'in %dh %dm'), m // 60, m % 60)
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local t = os.date('*t')
    local small = L.SIZE == 'small'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.calendar, L.acc(L.SYS.red), L.tr('Події', 'Up next'),
             not small and (L.WDAY_SHORT[t.wday] .. ', ' .. L.day_month(t)) or nil)

    local now = os.time()
    local list = {}
    for _, ev in ipairs(events) do
        if ev.e > now then list[#list + 1] = ev end
    end

    if #list == 0 then
        local cy = y + h / 2
        L.set(cr, C.text3); L.icon_mid(cr, ICON.calendar_blank, 28, x + w / 2, cy - 7)
        L.font(cr, F.medium, small and 11.5 or 12.5); L.set(cr, C.text2)
        L.text(cr, L.fit(cr, L.tr('Найближчим тижнем подій немає', 'Nothing this week'), w - 20), x + w / 2, cy + 37, 'c')
        if not small then
            L.font(cr, F.regular, 11); L.set(cr, C.text3)
            L.text(cr, L.tr('Події з календарів GNOME (Google, локальні)', 'Events from GNOME calendars (Google, local)'),
                   x + w / 2, cy + 56, 'c')
        end
        return
    end

    local ROW = small and 52 or 37
    local inner = w - 2 * PAD - 12
    local rows = math.max(1, math.floor((h - 48 - 8) / ROW))
    for i = 1, math.min(#list, rows) do
        local ev = list[i]
        local ry = y + 48 + (i - 1) * ROW
        local col = ev.color and L.hex(ev.color) or L.SYS.indigo
        if L.STYLE == 'color' then col = C.text end
        local ongoing = ev.s <= now

        L.rrect(cr, x + PAD, ry + 1, 4, small and 44 or 30, 2)
        L.set(cr, col); cairo_fill(cr)

        local note, note_col
        if ongoing and not ev.allday then note, note_col = L.tr('зараз', 'now'), col
        elseif not ev.allday and ev.s - now < 3 * 3600 then note, note_col = relative(ev.s - now), C.text3 end
        local note_w = 0
        if note and not small then
            L.font(cr, F.medium, 10.5); note_w = L.width(cr, note) + 8
            L.set(cr, note_col); L.text(cr, note, x + w - PAD, ry + 13, 'r')
        end

        L.font(cr, F.semibold, 13); L.set(cr, C.text)
        local title = ev.title ~= '' and ev.title or L.tr('Без назви', 'Untitled')
        if small then
            local lines = L.wrap(cr, title, inner, 1)
            L.text(cr, lines[1] or '', x + PAD + 12, ry + 13)
        else
            L.text(cr, L.fit(cr, title, inner - note_w), x + PAD + 12, ry + 13)
        end

        local when
        if ev.allday then
            when = (ongoing and L.tr('Сьогодні', 'Today') or day_label(ev.s)) .. L.tr(' · весь день', ' · all day')
        elseif ongoing then
            when = L.tr('до ', 'until ') .. hm(ev.e)
        else
            when = day_label(ev.s) .. ' · ' .. hm(ev.s) .. (small and '' or (' – ' .. hm(ev.e)))
        end
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, when, inner), x + PAD + 12, ry + 29)
        if small and note then
            L.font(cr, F.medium, 10.5); L.set(cr, note_col)
            L.text(cr, note, x + PAD + 12, ry + 43)
        end
    end
    if #list > rows and not small then
        L.font(cr, F.medium, 10.5); L.set(cr, C.text3)
        L.text(cr, L.tr('ще ', '+') .. (#list - rows), x + w - PAD, y + 48 + rows * ROW - 7, 'r')
    end
end

return W
