-- Network: link speeds, traffic graph and (option `lan`) the LAN device watch (netwatch.sh, nmap).
-- small: speeds + mini graph. medium: adds the LAN line. large: bigger graph + totals.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local HIST = 90  -- graph samples (one per update)
local LAN = L.opt_bool('lan', false)
local NET_CACHE = L.RUN .. '/netwatch'

local W = {}
local lan, iface
local down_hist, up_hist = {}, {}

local function default_iface()
    local best, metric
    for line in (L.read_file('/proc/net/route') or ''):gmatch('[^\n]+') do
        local ifc, dest, met = line:match('^(%S+)%s+(%x+)%s+%x+%s+%x+%s+%d+%s+%d+%s+(%d+)')
        if dest == '00000000' and (not metric or L.num(met) < metric) then
            best, metric = ifc, L.num(met)
        end
    end
    return best
end

local function speed(kib)
    local mb, kb = L.tr('МБ/с', 'MB/s'), L.tr('КБ/с', 'KB/s')
    if kib >= 1024 then return string.format('%.1f', kib / 1024), mb end
    if kib >= 100 then return string.format('%.0f', kib), kb end
    return string.format('%.1f', kib), kb
end

local function load_lan()
    local s = L.read_file(NET_CACHE)
    if not s then return end
    local l = {}
    for line in (s .. '\n'):gmatch('([^\n]*)\n') do l[#l + 1] = line end
    lan = { state = l[1], count = L.num(l[2]), unknown = L.num(l[3]), ip = l[4] or '' }
end

local function push(hist, v)
    hist[#hist + 1] = v
    if #hist > HIST then table.remove(hist, 1) end
end

function W.tick()
    if LAN and not L.DEMO then
        if L.due('netwatch', 60) then
            L.spawn('/usr/bin/env bash ' .. L.quote(L.WIDGET_DIR .. '/netwatch.sh') .. ' ' .. L.quote(NET_CACHE) .. ' '
                    .. L.quote(L.CONFIG .. '/network_known.txt') .. ' ' .. L.quote(L.CONFIG .. '/network_seen.txt') .. ' '
                    .. L.quote(L.CONFIG .. '/network_log.txt') .. ' ' .. L.LANG)
        end
        if L.due('lan_read', 10) then load_lan() end
    end
    if L.due('iface', 10) then iface = default_iface() end
    if L.DEMO and #down_hist == 0 then
        for i = 1, HIST do
            push(down_hist, 40 + 30 * math.sin(i / 6) + (i % 7) * 9)
            push(up_hist, 10 + 8 * math.cos(i / 5))
        end
    end
    if iface and not L.DEMO then
        push(down_hist, L.num(L.cp('${downspeedf ' .. iface .. '}')))
        push(up_hist, L.num(L.cp('${upspeedf ' .. iface .. '}')))
    end
end

local function graph(cr, gx, gy, gw, gh)
    local peak = 24  -- KiB/s floor so an idle link stays a flat line
    for i = 1, #down_hist do peak = math.max(peak, down_hist[i], up_hist[i] or 0) end
    local step = gw / (HIST - 1)
    local function path(hist)
        local n = #hist
        for i = 1, n do
            local px = gx + gw - (n - i) * step
            local py = gy + gh - hist[i] / peak * (gh - 2) - 1
            if i == 1 then cairo_move_to(cr, px, py) else cairo_line_to(cr, px, py) end
        end
        return gx + gw - (n - 1) * step
    end
    L.set(cr, L.line)
    cairo_rectangle(cr, gx, gy + gh, gw, 1)
    cairo_fill(cr)
    if #down_hist < 2 then return end
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND)
    local down_c = L.acc(L.SYS.cyan)
    local up_c = L.ACCENT and L.shade(L.ACCENT, -0.35) or L.SYS.pink
    for _, s in ipairs({ { up_hist, up_c, 0.22 }, { down_hist, down_c, 0.38 } }) do
        local x0 = path(s[1])
        cairo_line_to(cr, gx + gw, gy + gh)
        cairo_line_to(cr, x0, gy + gh)
        cairo_close_path(cr)
        local g = L.vgrad(cr, gy, gy + gh, { s[2][1], s[2][2], s[2][3], s[3] }, { s[2][1], s[2][2], s[2][3], 0 })
        cairo_fill(cr)
        cairo_pattern_destroy(g)
        path(s[1])
        cairo_set_line_width(cr, 1.6)
        L.set(cr, s[2])
        cairo_stroke(cr)
    end
    local v, u = speed(peak)
    L.font(cr, F.regular, 9.5); L.set(cr, C.text3)
    L.text(cr, L.tr('пік ', 'peak ') .. v .. ' ' .. u, gx + gw, gy - 3, 'r')
end

local function stat(cr, sx, sy, ico, col, kib, size)
    local v, u = speed(kib)
    L.set(cr, col); L.icon(cr, ico, size * 0.68, sx - 2, sy)
    L.font(cr, F.semibold, size); L.set(cr, C.text)
    local vw = L.width(cr, v)
    L.text(cr, v, sx + size * 0.72, sy)
    L.font(cr, F.medium, 11); L.set(cr, C.text2)
    L.text(cr, u, sx + size * 0.72 + vw + 4, sy)
end

local function lan_line(cr, x, by, w)
    if not lan then
        L.font(cr, F.regular, 11.5); L.set(cr, C.text3)
        L.text(cr, L.tr('Сканування локальної мережі…', 'Scanning the local network…'), x + PAD, by)
    elseif lan.state == 'NONMAP' then
        L.font(cr, F.regular, 11.5); L.set(cr, C.text3)
        L.text(cr, L.tr('Для перевірки мережі встановіть nmap', 'Install nmap to watch the network'), x + PAD, by)
    elseif lan.state == 'ALERT' then
        L.set(cr, L.SYS.red); L.icon(cr, ICON.shield_alert, 14, x + PAD - 1, by + 1)
        L.font(cr, F.semibold, 12); L.set(cr, L.SYS.red)
        L.text(cr, lan.unknown > 1 and (L.tr('Невідомих пристроїв: ', 'Unknown devices: ') .. lan.unknown)
               or L.tr('Невідомий пристрій', 'Unknown device'), x + PAD + 19, by)
        L.font(cr, F.regular, 11); L.set(cr, C.text2)
        L.text(cr, lan.ip, x + w - PAD, by, 'r')
    else
        L.set(cr, L.SYS.green); L.icon(cr, ICON.shield_ok, 14, x + PAD - 1, by + 1)
        L.font(cr, F.medium, 12); L.set(cr, C.text2)
        L.text(cr, L.tr('Усі пристрої відомі', 'All devices known'), x + PAD + 19, by)
        L.font(cr, F.regular, 11); L.set(cr, C.text3)
        L.text(cr, L.tr('у мережі: ', 'online: ') .. lan.count, x + w - PAD, by, 'r')
    end
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local wifi = iface and iface:match('^wl')
    local where = L.tr('немає з’єднання', 'offline')
    if iface then
        local name = wifi and L.trim(L.cp('${wireless_essid ' .. iface .. '}')) or 'Ethernet'
        where = name .. ' · ' .. L.cp('${addr ' .. iface .. '}')
    end
    if L.DEMO then where = 'Wi-Fi · 192.168.1.26' end
    local small = L.SIZE == 'small'
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, wifi and ICON.wifi or ICON.ethernet, L.acc(L.SYS.cyan),
             L.tr('Мережа', 'Network'), not small and where or nil)
    local down_c, up_c = L.acc(L.SYS.cyan), L.ACCENT and L.shade(L.ACCENT, -0.35) or L.SYS.pink

    if small then
        stat(cr, x + PAD, y + 64, ICON.down, down_c, down_hist[#down_hist] or 0, 20)
        stat(cr, x + PAD, y + 92, ICON.up, up_c, up_hist[#up_hist] or 0, 16)
        graph(cr, x + PAD, y + 112, w - 2 * PAD, 40)
        return
    end

    stat(cr, x + PAD, y + 64, ICON.down, down_c, down_hist[#down_hist] or 0, 22)
    stat(cr, x + PAD + 150, y + 64, ICON.up, up_c, up_hist[#up_hist] or 0, 22)

    if L.SIZE == 'large' then
        local gh = h - 84 - (LAN and 90 or 60)
        graph(cr, x + PAD, y + 92, w - 2 * PAD, gh)
        local ty = y + 92 + gh + 30
        if iface and not L.DEMO then
            L.font(cr, F.regular, 11.5); L.set(cr, C.text2)
            L.text(cr, L.tr('Отримано: ', 'Received: ') .. L.cp('${totaldown ' .. iface .. '}'), x + PAD, ty)
            L.text(cr, L.tr('Надіслано: ', 'Sent: ') .. L.cp('${totalup ' .. iface .. '}'), x + w - PAD, ty, 'r')
        end
        if LAN then lan_line(cr, x, y + h - 22, w) end
        return
    end

    if not LAN then
        graph(cr, x + PAD, y + 88, w - 2 * PAD, 62)
        return
    end
    graph(cr, x + PAD, y + 84, w - 2 * PAD, 38)
    lan_line(cr, x, y + 154, w)
end

return W
