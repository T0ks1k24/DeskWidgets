"""`deskwidgets new <id>`: a ready-to-edit widget in ~/.config/deskwidgets/widgets/<id>."""
import getpass
import json
import re

from . import paths

LUA = '''-- {widget_id}: your widget. Edit me, then run `deskwidgets reload` (or Reload in the gallery menu).
-- API: docs/creating-widgets.md. Examples: the built-in widgets in {builtin}
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {{}}
local NAME = {lua_name}
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
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.star, L.acc(L.SYS.purple), NAME)

    L.font(cr, F.light, 40); L.set(cr, C.text)
    L.text(cr, GREETING, x + PAD, y + 90)

    L.font(cr, F.regular, 12); L.set(cr, C.text3)
    L.text(cr, L.tr('оновлень: ', 'updates: ') .. count, x + PAD, y + h - PAD)
end

-- optional: left click inside the card (coordinates relative to the card)
function W.click(cx, cy, w, h)
    L.notify(NAME, GREETING)
    return true
end

return W
'''


def create(widget_id, name=None):
    widget_id = re.sub(r'[^a-z0-9_]+', '_', widget_id.lower()).strip('_') or 'my_widget'
    folder = paths.USER_WIDGETS / widget_id
    if folder.exists():
        raise FileExistsError(str(folder))
    folder.mkdir(parents=True)
    name = name or widget_id.replace('_', ' ').title()
    manifest = {
        'id': widget_id,
        'version': '1.0',
        'author': _user(),
        'category': 'personal',
        'name': {'uk': name, 'en': name},
        'description': {'uk': 'Мій віджет', 'en': 'My widget'},
        'sizes': ['small', 'medium', 'large'],
        'interval': 5,
        'options': [
            {'key': 'greeting', 'type': 'text', 'default': 'Hello!', 'label': {'uk': 'Привітання', 'en': 'Greeting'}},
        ],
    }
    (folder / 'widget.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    # JSON string escaping is also valid Lua for ordinary Unicode text;
    # control characters need Lua's decimal escapes instead of JSON's \uXXXX.
    lua_name = '"' + ''.join('\\%03d' % ord(c) if ord(c) < 32 else
                             '\\' + c if c in ('"', '\\') else c for c in name) + '"'
    (folder / 'widget.lua').write_text(LUA.format(widget_id=widget_id, lua_name=lua_name, builtin=paths.BUILTIN_WIDGETS),
                                       encoding='utf-8')
    return folder


def _user():
    try:
        return getpass.getuser()
    except Exception:   # no passwd entry, no USERNAME: not worth failing over
        return ''
