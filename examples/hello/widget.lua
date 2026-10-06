-- The simplest widget: a greeting that follows the chosen style and colour.
-- Copy this folder to ~/.config/deskwidgets/widgets/hello and run `deskwidgets reload`.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local NAME = L.opt('name', L.tr('світе', 'world'))

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.heart, L.acc(L.SYS.pink), L.tr('Привіт', 'Hello'))
    L.font(cr, F.light, L.SIZE == 'small' and 30 or 40)
    L.set(cr, C.text)
    for i, line in ipairs(L.wrap(cr, L.tr('Привіт, ', 'Hello, ') .. NAME .. '!', w - 2 * PAD, 2)) do
        L.text(cr, line, x + PAD, y + 82 + (i - 1) * 40)
    end
end

return W
