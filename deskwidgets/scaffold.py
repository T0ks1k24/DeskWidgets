"""`deskwidgets new <id>`: a ready-to-edit widget in ~/.config/deskwidgets/widgets/<id>."""
import json
import os
import re

from . import paths

LUA = '''-- {name}: your widget. Edit me, then run `deskwidgets reload` (or Reload in the gallery menu).
-- API: docs/creating-widgets.md. Examples: the built-in widgets in {builtin}
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {{}}
local GREETING = L.opt('greeting', 'Hello!')   -- an option from widget.json

-- tick() runs before every draw: read files, call commands (sparingly), keep state
local count = 0
function W.tick()
    count = count + 1
end

-- draw() paints the card. (x, y) is its top-left corner, w x h its size in base units:
-- small 170x170, medium 356x170, large 356x356. L.SIZE tells you which one.
function W.draw(cr, x, y, w, h)
    L.card(cr, x, y, w, h)                                  -- background in the chosen style
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.star, L.acc(L.SYS.purple), '{name}')

    L.font(cr, F.light, 40); L.set(cr, C.text)
    L.text(cr, GREETING, x + PAD, y + 90)

    L.font(cr, F.regular, 12); L.set(cr, C.text3)
    L.text(cr, L.tr('оновлень: ', 'updates: ') .. count, x + PAD, y + h - PAD)
end

-- optional: left click inside the card (coordinates relative to the card)
function W.click(cx, cy, w, h)
    L.spawn('notify-send ' .. L.quote('{name}') .. ' ' .. L.quote(GREETING))
    return true
end

return W
'''


def create(widget_id, name=None):
    widget_id = re.sub(r'[^a-z0-9_]+', '_', widget_id.lower()).strip('_') or 'my_widget'
    folder = os.path.join(paths.USER_WIDGETS, widget_id)
    if os.path.exists(folder):
        raise FileExistsError(folder)
    os.makedirs(folder)
    name = name or widget_id.replace('_', ' ').title()
    manifest = {
        'id': widget_id,
        'version': '1.0',
        'author': os.environ.get('USER', ''),
        'category': 'personal',
        'name': {'uk': name, 'en': name},
        'description': {'uk': 'Мій віджет', 'en': 'My widget'},
        'sizes': ['small', 'medium', 'large'],
        'interval': 5,
        'options': [
            {'key': 'greeting', 'type': 'text', 'default': 'Hello!', 'label': {'uk': 'Привітання', 'en': 'Greeting'}},
        ],
    }
    with open(os.path.join(folder, 'widget.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write('\n')
    with open(os.path.join(folder, 'widget.lua'), 'w', encoding='utf-8') as f:
        f.write(LUA.format(name=name, builtin=paths.BUILTIN_WIDGETS))
    return folder
