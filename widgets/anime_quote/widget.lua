-- Anime buddy: a character greets you in Japanese (by time of day) and says something
-- encouraging in a speech bubble. small: avatar and bubble. medium: picture left, bubble right.
-- large: big picture with the bubble over it.
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}
local NAME = L.opt('name', L.tr('Сенсей', 'Sensei'))
local IMAGE = L.opt('image_png', '')
local EVERY = math.max(1, L.opt_num('every', 30)) * 60

local BUILTIN = {
    { 'Сьогодні — чудовий день, щоб стати трохи сильнішим!', 'Today is a great day to get a little stronger!' },
    { 'Навіть найдовша арка починається з першої серії.', 'Even the longest arc starts with episode one.' },
    { 'Не здавайся. Головні герої так не роблять.', 'Don’t give up. Main characters never do.' },
    { 'Ще одна серія — і спати. Чесно-чесно.', 'One more episode, then bed. Pinky promise.' },
    { 'Втомився? Перерва — теж частина тренування.', 'Tired? Rest is part of the training too.' },
    { 'Рівень підвищено! +1 до наполегливості.', 'Level up! +1 to perseverance.' },
    { 'Мрії збуваються, якщо працювати над ними щодня.', 'Dreams come true if you work on them every day.' },
    { 'Попий води, сенпаю. Це наказ.', 'Drink some water, senpai. That’s an order.' },
    { 'Помилка — це просто флешбек перед перемогою.', 'A mistake is just the flashback before the win.' },
    { 'Цей код скомпілюється з першого разу. Віримо!', 'This code will compile on the first try. Believe!' },
    { 'Сила дружби + кава = непереможність.', 'Power of friendship + coffee = unstoppable.' },
    { 'Ти вже прокачався більше, ніж учора.', 'You already levelled up since yesterday.' },
}

local phrases = {}
for line in (L.opt('phrases', '') .. '\n'):gmatch('([^\n]*)\n') do
    line = L.trim(line)
    if line ~= '' then phrases[#phrases + 1] = line end
end
if #phrases == 0 then for _, p in ipairs(BUILTIN) do phrases[#phrases + 1] = L.tr(p[1], p[2]) end end

-- is a Japanese font installed? (otherwise the greeting is shown in the UI language only)
local HAS_CJK = L.popen('fc-list :lang=ja family'):match('%S') ~= nil

local function greeting()
    local hr = os.date('*t').hour
    if hr >= 5 and hr < 11 then return 'おはよう!', L.tr('Доброго ранку', 'Good morning') end
    if hr >= 11 and hr < 18 then return 'こんにちは!', L.tr('Добрий день', 'Good afternoon') end
    if hr >= 18 and hr < 23 then return 'こんばんは!', L.tr('Добрий вечір', 'Good evening') end
    return 'おやすみ…', L.tr('Пора спати', 'Time to sleep')
end

local function phrase()
    local slot = os.time() // EVERY
    return phrases[slot % #phrases + 1]
end

-- chibi face drawn with cairo: big shiny eyes, blush and a small smile
local function chibi(cr, cx, cy, r)
    local a = L.acc(L.SYS.pink)
    cairo_arc(cr, cx, cy, r, 0, 2 * math.pi)
    local g = L.vgrad(cr, cy - r, cy + r, L.shade(a, 0.55), L.shade(L.mix(a, L.SYS.purple, 0.4), 0.25))
    cairo_fill(cr); cairo_pattern_destroy(g)
    local ex, ey, ew, eh = r * 0.36, cy + r * 0.02, r * 0.16, r * 0.24
    for _, side in ipairs({ -1, 1 }) do
        local x0 = cx + side * ex
        cairo_save(cr)
        cairo_translate(cr, x0, ey)
        cairo_scale(cr, ew, eh)
        cairo_arc(cr, 0, 0, 1, 0, 2 * math.pi)
        cairo_restore(cr)
        cairo_set_source_rgba(cr, 0.16, 0.10, 0.22, 1); cairo_fill(cr)
        cairo_arc(cr, x0 - ew * 0.35, ey - eh * 0.4, ew * 0.38, 0, 2 * math.pi)
        cairo_set_source_rgba(cr, 1, 1, 1, 1); cairo_fill(cr)
        cairo_arc(cr, x0 + ew * 0.3, ey + eh * 0.35, ew * 0.18, 0, 2 * math.pi)
        cairo_fill(cr)
        cairo_save(cr)
        cairo_translate(cr, cx + side * r * 0.58, cy + r * 0.32)
        cairo_scale(cr, r * 0.17, r * 0.09)
        cairo_arc(cr, 0, 0, 1, 0, 2 * math.pi)
        cairo_restore(cr)
        L.set(cr, L.shade(a, -0.05), 0.55); cairo_fill(cr)
    end
    cairo_arc(cr, cx, cy + r * 0.28, r * 0.12, 0.15 * math.pi, 0.85 * math.pi)
    cairo_set_source_rgba(cr, 0.16, 0.10, 0.22, 1); cairo_set_line_width(cr, math.max(1.5, r * 0.05))
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND); cairo_stroke(cr)
end

local function avatar(cr, cx, cy, r)
    local surf = L.image(IMAGE)
    if surf then
        cairo_save(cr)
        cairo_arc(cr, cx, cy, r, 0, 2 * math.pi)
        cairo_clip(cr)
        L.cover(cr, surf, cx - r, cy - r, 2 * r, 2 * r, 0)
        cairo_restore(cr)
    else
        chibi(cr, cx, cy, r)
    end
    cairo_arc(cr, cx, cy, r, 0, 2 * math.pi)
    cairo_set_source_rgba(cr, 1, 1, 1, 0.9); cairo_set_line_width(cr, 2.5); cairo_stroke(cr)
end

-- speech bubble with the tail pointing to (tx, ty)
local function bubble(cr, x, y, w, h, tx, ty, text, fs)
    local light = not L.T.dark or L.STYLE == 'manga'
    local fill = light and { 1, 1, 1, 0.92 } or { 1, 1, 1, 0.12 }
    L.rrect(cr, x, y, w, h, 14)
    local bx = math.max(x + 18, math.min(tx, x + w - 18))
    if ty < y then   -- tail up
        cairo_move_to(cr, bx - 9, y); cairo_line_to(cr, tx, ty); cairo_line_to(cr, bx + 9, y)
    elseif tx < x then   -- tail left
        local by = math.max(y + 16, math.min(ty, y + h - 16))
        cairo_move_to(cr, x, by - 8); cairo_line_to(cr, tx, ty); cairo_line_to(cr, x, by + 8)
    end
    L.set(cr, fill); cairo_fill_preserve(cr)
    if L.STYLE == 'manga' then cairo_set_source_rgba(cr, 0, 0, 0, 1); cairo_set_line_width(cr, 2)
    else L.set(cr, L.acc(L.SYS.pink), 0.45); cairo_set_line_width(cr, 1.2) end
    cairo_stroke(cr)
    L.font(cr, F.medium, fs)
    L.set(cr, light and { 0.16, 0.10, 0.18, 1 } or C.text)
    local lh = fs * 1.3
    local lines = L.wrap(cr, text, w - 24, math.max(1, math.floor((h - 16) / lh)))
    local top = y + (h - #lines * lh) / 2 + fs * 0.95
    for i, line in ipairs(lines) do
        cairo_move_to(cr, x + 12, top + (i - 1) * lh)
        cairo_show_text(cr, line)
    end
end

local function heading(cr, x, y, maxw)
    local jp, local_greet = greeting()
    L.set(cr, L.acc(L.SYS.pink))
    if HAS_CJK then
        L.font(cr, F.cjk, 15)
        local jw = L.width(cr, jp)
        L.text(cr, jp, x, y)
        L.font(cr, F.medium, 11); L.set(cr, C.text3)
        L.text(cr, L.fit(cr, local_greet, maxw - jw - 6), x + jw + 6, y)
    else
        L.font(cr, F.semibold, 14)
        L.text(cr, L.fit(cr, local_greet .. '!', maxw), x, y)
    end
end

function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)
    local text = phrase()

    if L.SIZE == 'small' then
        avatar(cr, x + PAD + 22, y + PAD + 22, 22)
        L.font(cr, F.semibold, 12.5); L.set(cr, C.text)
        L.text(cr, L.fit(cr, NAME, w - 2 * PAD - 52), x + PAD + 52, y + 33)
        heading(cr, x + PAD + 52, y + 52, w - 2 * PAD - 52)
        bubble(cr, x + 12, y + 72, w - 24, h - 84, x + PAD + 22, y + 64, text, 11.5)
        return
    end

    if L.SIZE == 'large' then
        local ih = h * 0.56
        local surf = L.image(IMAGE)
        if surf then
            L.cover(cr, surf, x + 10, y + 10, w - 20, ih, L.R - 6)
        else
            avatar(cr, x + w / 2, y + 10 + ih / 2, ih * 0.38)
        end
        L.font(cr, F.semibold, 15); L.set(cr, C.text)
        L.text(cr, L.fit(cr, NAME, w * 0.45), x + PAD, y + ih + 38)
        heading(cr, x + w * 0.45 + 8, y + ih + 38, w * 0.55 - PAD - 8)
        bubble(cr, x + 12, y + ih + 52, w - 24, h - ih - 64, x + w * 0.3, y + ih + 42, text, 13.5)
        return
    end

    -- medium: picture on the left, bubble on the right
    local surf = L.image(IMAGE)
    local pw = h - 2 * 12
    if surf then
        L.cover(cr, surf, x + 12, y + 12, pw, pw, L.R - 8)
    else
        avatar(cr, x + 12 + pw / 2, y + 12 + pw / 2, pw * 0.4)
    end
    local tx = x + 12 + pw + 14
    L.font(cr, F.semibold, 13.5); L.set(cr, C.text)
    L.text(cr, L.fit(cr, NAME, x + w - PAD - tx), tx, y + 32)
    heading(cr, tx, y + 52, x + w - PAD - tx)
    bubble(cr, tx, y + 64, x + w - 12 - tx, h - 76, tx - 8, y + 100, text, 12)
end

return W
