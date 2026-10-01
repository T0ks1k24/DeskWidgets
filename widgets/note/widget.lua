-- Note: your text on the desktop. Defaults to a sticky-note look: "colour" style, yellow accent.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local TITLE = L.opt('title', L.tr('Нотатка', 'Note'))
local TEXT = L.opt('text', L.tr('Клацніть правою кнопкою, щоб змінити текст цієї нотатки.',
                                'Right-click to change the text of this note.'))
-- bigger cards get bigger text unless a size was set
local AUTO = ({ small = 13.5, medium = 15, large = 18 })[L.SIZE] or 13.5
local SIZE = L.opt_num('font_size', 0)
if SIZE <= 0 then SIZE = AUTO end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local ty = y + 32
    if TITLE ~= '' then
        L.set(cr, L.acc(L.SYS.yellow)); L.icon(cr, ICON.pin, 13, x + PAD, y + 31)
        L.font(cr, F.semibold, 13.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, TITLE, w - 2 * PAD - 20), x + PAD + 19, y + 30)
        ty = y + 38 + SIZE * 1.3
    end
    local line = SIZE * 1.33
    L.font(cr, F.regular, SIZE); L.set(cr, C.text2)
    local lines = math.max(1, math.floor((y + h - 12 - ty) / line) + 1)
    for i, s in ipairs(L.wrap(cr, TEXT, w - 2 * PAD, lines)) do
        L.text(cr, s, x + PAD, ty + (i - 1) * line)
    end
end

return W
