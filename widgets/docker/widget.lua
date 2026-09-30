-- Docker: containers and their state (docker ps every 20 s).
-- small: running count and the first names. medium: 3 rows. large: up to 8 rows.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local ALL = L.opt_bool('all', true)
local containers, available = {}, true

local UNITS = {
    second = { 'с', 's' }, seconds = { 'с', 's' }, minute = { 'хв', 'm' }, minutes = { 'хв', 'm' },
    hour = { 'год', 'h' }, hours = { 'год', 'h' }, day = { 'дн', 'd' }, days = { 'дн', 'd' },
    week = { 'тиж', 'w' }, weeks = { 'тиж', 'w' }, month = { 'міс', 'mo' }, months = { 'міс', 'mo' },
    year = { 'р', 'y' }, years = { 'р', 'y' },
}

local function uptime(status)
    local n, unit = status:match('^Up (%d+) (%a+)')
    if n then
        local u = UNITS[unit]
        return n .. ' ' .. (u and L.tr(u[1], u[2]) or unit)
    end
    if status:match('^Up About an hour') then return L.tr('~1 год', '~1h') end
    if status:match('^Up About a minute') or status:match('^Up Less than') then return L.tr('~1 хв', '~1m') end
    return ''
end

local function read()
    local out = L.popen("docker ps " .. (ALL and '-a ' or '') .. "--format '{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}'")
    containers = {}
    available = out ~= ''
    for line in out:gmatch('[^\n]+') do
        local f = L.fields(line)
        containers[#containers + 1] = { name = f[1], image = f[2], state = f[3], status = f[4] or '' }
    end
    table.sort(containers, function(a, b)
        if (a.state == 'running') ~= (b.state == 'running') then return a.state == 'running' end
        return a.name < b.name
    end)
end

function W.tick()
    if L.DEMO then
        containers = { { name = 'postgres', image = 'postgres:16-alpine', state = 'running', status = 'Up 40 hours' },
                       { name = 'redis-cache', image = 'redis:7', state = 'running', status = 'Up 3 days' },
                       { name = 'nginx-dev', image = 'nginx:latest', state = 'exited', status = 'Exited (0) 2 days ago' } }
        return
    end
    if L.due('docker', 20) then read() end
end

local STATE = {
    running = { { 'працює', 'running' }, L.SYS.green }, exited = { { 'зупинено', 'stopped' }, nil },
    created = { { 'створено', 'created' }, nil }, paused = { { 'пауза', 'paused' }, L.SYS.yellow },
    restarting = { { 'перезапуск', 'restarting' }, L.SYS.orange }, dead = { { 'помилка', 'dead' }, L.SYS.red },
}

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local running = 0
    for _, c in ipairs(containers) do if c.state == 'running' then running = running + 1 end end
    local summary = running .. L.tr(' з ', ' of ') .. #containers .. L.tr(' працює', ' running')
    local small = L.SIZE == 'small'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.docker, L.acc(L.SYS.blue), 'Docker',
             (#containers > 0 and not small) and summary or nil)
    if not available or #containers == 0 then
        L.font(cr, F.regular, 12); L.set(cr, C.text3)
        L.text(cr, available and L.tr('Контейнерів немає', 'No containers') or L.tr('Docker недоступний', 'Docker is not available'),
               x + w / 2, y + h / 2 + 12, 'c')
        return
    end

    if small then
        L.font(cr, F.light, 44); L.set(cr, C.text)
        local nw = L.width(cr, tostring(running))
        L.text(cr, tostring(running), x + PAD - 2, y + 86)
        L.font(cr, F.medium, 13); L.set(cr, C.text2)
        L.text(cr, L.tr('з ', 'of ') .. #containers, x + PAD + nw + 6, y + 86)
        for i = 1, math.min(#containers, 3) do
            local c = containers[i]
            local st = STATE[c.state] or { { c.state, c.state }, nil }
            local ry = y + 110 + (i - 1) * 18
            cairo_arc(cr, x + PAD + 3, ry - 4, 3, 0, 2 * math.pi)
            L.set(cr, st[2] or C.text3); cairo_fill(cr)
            L.font(cr, F.medium, 11.5); L.set(cr, c.state == 'running' and C.text or C.text3)
            L.text(cr, L.fit(cr, c.name, w - 2 * PAD - 12), x + PAD + 12, ry)
        end
        return
    end

    local rows = math.floor((h - 50) / 37)
    for i = 1, math.min(#containers, rows) do
        local c = containers[i]
        local st = STATE[c.state] or { { c.state, c.state }, nil }
        local ry = y + 60 + (i - 1) * 37
        cairo_arc(cr, x + PAD + 4, ry - 4.5, 4, 0, 2 * math.pi)
        L.set(cr, st[2] or C.text3); cairo_fill(cr)
        L.font(cr, F.semibold, 13); L.set(cr, c.state == 'running' and C.text or C.text2)
        L.text(cr, L.fit(cr, c.name, w - 2 * PAD - 120), x + PAD + 16, ry)
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, c.image, w - 2 * PAD - 120), x + PAD + 16, ry + 15)
        local up = c.state == 'running' and uptime(c.status) or ''
        L.font(cr, F.medium, 11); L.set(cr, C.text2)
        L.text(cr, L.tr(st[1][1], st[1][2]), x + w - PAD, ry, 'r')
        if up ~= '' then
            L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
            L.text(cr, up, x + w - PAD, ry + 15, 'r')
        end
    end
end

return W
