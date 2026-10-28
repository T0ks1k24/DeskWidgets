--[[ Entry point of a widget, loaded by the engine (engine/runtime.py).
     It loads lib.lua and the widget's widget.lua (from host.widget_dir), and gives the engine
     two functions: frame(cr) draws the widget, click(x, y) handles a left click.

     A widget module returns a table:
       draw(cr, x, y, w, h)   required: draw the card at (x, y), size w x h (base units)
       tick()                 optional: called before every draw (read data, spawn fetchers)
       click(x, y, w, h)      optional: left click inside the card, return true if handled
       scale = { large = 'small' }   optional: draw a size as another layout scaled up

     Right-click on a widget opens its settings (handled by the engine, not by the widget). ]]

local L = require 'lib'

local SCALE = host.scale
local WIN_W, WIN_H = host.width, host.height
local BASE_W, BASE_H = WIN_W / SCALE, WIN_H / SCALE   -- drawing happens in base units

local ok, widget = pcall(dofile, L.WIDGET_DIR .. L.SEP .. 'widget.lua')
if not ok or type(widget) ~= 'table' or type(widget.draw) ~= 'function' then
    local load_error = ok and 'widget.lua must return a table with a draw() function' or tostring(widget)
    host.log(L.TYPE .. ': ' .. load_error)
    widget = { draw = function(cr, x, y, w, h)
        L.card(cr, x, y, w, h)
        L.font(cr, L.F.medium, 12); L.set(cr, L.C.red)
        for i, line in ipairs(L.wrap(cr, L.tr('Помилка у віджеті: ', 'Widget error: ') .. load_error, w - 32, 6)) do
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

local M = {}

-- A widget error is logged once per message, not once per frame.
local reported = {}
local function report(what, err)
    local text = what .. ': ' .. tostring(err)
    if not reported[text] then
        reported[text] = true
        host.log(L.ID .. ': ' .. text)
    end
end

function M.frame(cr)
    L.prepare(cr)
    cairo_scale(cr, SCALE, SCALE)
    local ok_tick, err = pcall(function() if widget.tick then widget.tick() end end)
    if not ok_tick then report('tick', err) end
    local ok_draw, derr = pcall(draw_card, cr, L.M, L.M, BASE_W - 2 * L.M, BASE_H - 2 * L.M)
    if not ok_draw then report('draw', derr) end
end

-- (x, y) in window pixels; returns true when the widget handled the click
function M.click(px, py)
    if not widget.click then return false end
    local ok_click, handled = pcall(widget.click, px / SCALE - L.M, py / SCALE - L.M,
                                    BASE_W - 2 * L.M, BASE_H - 2 * L.M)
    if not ok_click then report('click', handled) return false end
    return handled and true or false
end

return M
