--[[ conky entry point shared by every widget window.
     The service starts one conky per widget instance. This file loads lib.lua and the
     widget's widget.lua (from MAC_WIDGET_DIR) and draws the card.

     A widget module returns a table:
       draw(cr, x, y, w, h)   required: draw the card at (x, y), size w x h (base units)
       tick()                 optional: called before every draw (read data, spawn fetchers)
       click(x, y, w, h)      optional: left click inside the card, return true if handled
       scale = { large = 'small' }   optional: draw a size as another layout scaled up

     Right-click on a widget opens its settings. With MAC_PREVIEW=<file.png> one frame is
     rendered to a PNG and conky exits (gallery thumbnails, `deskwidgets preview`). ]]

local ENGINE = os.getenv('MAC_ENGINE')
package.path = ENGINE .. '/?.lua;' .. package.path
local L = require 'lib'

local PREVIEW = os.getenv('MAC_PREVIEW')
local SCALE = tonumber(os.getenv('MAC_SCALE')) or 1
local WIN_W = tonumber(os.getenv('MAC_W')) or math.floor((L.S + 2 * L.M) * SCALE)
local WIN_H = tonumber(os.getenv('MAC_H')) or math.floor((L.S + 2 * L.M) * SCALE)
local BASE_W, BASE_H = WIN_W / SCALE, WIN_H / SCALE   -- drawing happens in base units

local ok, widget = pcall(dofile, L.WIDGET_DIR .. '/widget.lua')
if not ok then
    print('deskwidgets: ' .. L.TYPE .. ': ' .. tostring(widget))
    widget = { draw = function(cr, x, y, w, h)
        L.card(cr, x, y, w, h)
        L.font(cr, L.F.medium, 12); L.set(cr, L.C.red)
        for i, line in ipairs(L.wrap(cr, L.tr('Помилка у віджеті: ', 'Widget error: ') .. tostring(widget), w - 32, 6)) do
            L.text(cr, line, x + 16, y + 30 + (i - 1) * 16)
        end
    end }
end

local CELLS = { small = { 1, 1 }, medium = { 2, 1 }, large = { 2, 2 } }
local function base_size(size)
    local c = CELLS[size] or CELLS.small
    return c[1] * L.S + (c[1] - 1) * L.GAP, c[2] * L.S + (c[2] - 1) * L.GAP
end

local function draw_card(cr, x, y, w, h)
    local alias = widget.scale and widget.scale[L.SIZE]
    if not alias then
        widget.draw(cr, x, y, w, h)
        return
    end
    -- the card at full size, then the other layout scaled to fit inside it
    L.card(cr, x, y, w, h)
    local bw, bh = base_size(alias)
    local k = math.min(w / bw, h / bh)
    local size = L.SIZE
    cairo_save(cr)
    cairo_translate(cr, x + (w - bw * k) / 2, y + (h - bh * k) / 2)
    cairo_scale(cr, k, k)
    L.SIZE, L.NO_CARD = alias, true
    widget.draw(cr, 0, 0, bw, bh)
    L.SIZE, L.NO_CARD = size, false
    cairo_restore(cr)
end

local frames = 0
function conky_main()
    frames = frames + 1
    local cs
    if PREVIEW then
        -- every frame is drawn (so conky starts sampling the values the widget asks for)
        -- but only the last one is saved
        cs = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, WIN_W, WIN_H)
    else
        if conky_window == nil then return end
        cs = cairo_xlib_surface_create(conky_window.display, conky_window.drawable,
                                       conky_window.visual, conky_window.width, conky_window.height)
    end
    local cr = cairo_create(cs)
    L.prepare(cr)
    cairo_scale(cr, SCALE, SCALE)
    local ok_tick, err = pcall(function() if widget.tick then widget.tick() end end)
    if not ok_tick then print('deskwidgets: ' .. L.ID .. ': ' .. tostring(err)) end
    local ok_draw, derr = pcall(draw_card, cr, L.M, L.M, BASE_W - 2 * L.M, BASE_H - 2 * L.M)
    if not ok_draw then print('deskwidgets: ' .. L.ID .. ': ' .. tostring(derr)) end
    cairo_destroy(cr)
    if PREVIEW and frames >= 4 then
        cairo_surface_write_to_png(cs, PREVIEW)
        cairo_surface_destroy(cs)
        os.exit(0)
    end
    cairo_surface_destroy(cs)
end

-- conky 1.19+ mouse events. `button` is a number in 1.19 and a name in later releases
local function button_is(ev, n, name) return ev.button == n or ev.button == name end

function conky_mouse(ev)
    if ev.type ~= 'button_down' then return false end
    if button_is(ev, 3, 'right') then
        local bin = os.getenv('MAC_BIN') or 'deskwidgets'
        L.spawn(L.quote(bin) .. ' configure ' .. L.quote(L.ID))
        return true
    end
    if button_is(ev, 1, 'left') and widget.click then
        local x = ev.x / SCALE - L.M
        local y = ev.y / SCALE - L.M
        return widget.click(x, y, BASE_W - 2 * L.M, BASE_H - 2 * L.M) and true or false
    end
    return false
end
