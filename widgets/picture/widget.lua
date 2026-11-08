-- Picture: an image (any format, the service converts it to PNG) with an optional caption.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local IMAGE = L.opt('image_png', '')
local CAPTION = L.opt('caption', '')
local FIT = L.opt('fit', 'cover')

function W.draw(cr, x, y, w, h)
    local surf = L.image(IMAGE)
    if not surf then
        L.card(cr, x, y, w, h)
        L.set(cr, L.acc(L.SYS.purple)); L.icon_mid(cr, ICON.image, 34, x + w / 2, y + h / 2 - 14)
        L.font(cr, F.medium, 12); L.set(cr, C.text2)
        L.text(cr, L.tr('Оберіть картинку', 'Choose a picture'), x + w / 2, y + h / 2 + 24, 'c')
        L.font(cr, F.regular, 10.5); L.set(cr, C.text3)
        L.text(cr, L.tr('права кнопка — налаштування', 'right-click for settings'), x + w / 2, y + h / 2 + 41, 'c')
        return
    end
    L.card(cr, x, y, w, h)
    local inset = L.T.card and 0 or 0
    if FIT == 'contain' then
        local iw, ih = cairo_image_surface_get_width(surf), cairo_image_surface_get_height(surf)
        local k = math.min((w - 16) / iw, (h - 16) / ih)
        local dw, dh = iw * k, ih * k
        L.cover(cr, surf, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, 12)
    else
        L.cover(cr, surf, x + inset, y + inset, w - 2 * inset, h - 2 * inset, L.R)
    end
    if CAPTION ~= '' then
        -- dark scrim at the bottom so the caption reads on any picture
        L.rrect(cr, x, y, w, h, L.R)
        cairo_save(cr); cairo_clip(cr)
        local g = cairo_pattern_create_linear(0, y + h - 60, 0, y + h)
        cairo_pattern_add_color_stop_rgba(g, 0, 0, 0, 0, 0)
        cairo_pattern_add_color_stop_rgba(g, 1, 0, 0, 0, 0.6)
        cairo_set_source(cr, g); cairo_rectangle(cr, x, y + h - 60, w, 60); cairo_fill(cr)
        cairo_pattern_destroy(g)
        cairo_restore(cr)
        L.font(cr, F.semibold, 13); cairo_set_source_rgba(cr, 1, 1, 1, 1)
        cairo_move_to(cr, x + PAD, y + h - 16)
        cairo_show_text(cr, L.fit(cr, CAPTION, w - 2 * PAD))
    end
end

return W
