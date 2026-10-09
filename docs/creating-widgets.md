# Creating widgets

A DeskWidgets widget is **a folder with two files**:

```
my_widget/
├── widget.json   # name, sizes, options — the gallery builds the settings window from it
└── widget.lua    # draws the card with Cairo (via the helper library `lib`)
```

Optional extras: helper scripts (e.g. `fetch.py` that downloads data), images, a README.

Your own widgets live in **`~/.config/deskwidgets/widgets/<id>/`**. A folder there with the
same id as a built-in widget replaces it, so you can also fork a built-in one by copying
`/usr/share/deskwidgets/widgets/<id>` (or `widgets/<id>` in this repo) there.

## Quick start

```sh
deskwidgets new my_widget          # creates ~/.config/deskwidgets/widgets/my_widget from a template
$EDITOR ~/.config/deskwidgets/widgets/my_widget/widget.lua
deskwidgets preview my_widget --size medium -o /tmp/w.png   # render a PNG without touching the desktop
deskwidgets reload                 # re-read widget folders, restart widgets
deskwidgets add my_widget          # put it on the desktop
```

Or in the gallery: **Settings → Create a widget…**, then **Add**. Errors in `widget.lua` are shown
right on the card, so you see them immediately.

## widget.json

```json
{
  "id": "my_widget",
  "version": "1.0",
  "author": "you",
  "category": "personal",
  "name": {"uk": "Мій віджет", "en": "My widget"},
  "description": {"uk": "Що він показує", "en": "What it shows"},
  "sizes": ["small", "medium", "large"],
  "interval": 5,
  "fast_if": "seconds",
  "style": "glass",
  "accent": "auto",
  "color_accent": "indigo",
  "uses": [],
  "options": [
    {"key": "city", "type": "text", "default": "Kyiv", "label": {"uk": "Місто", "en": "City"}}
  ]
}
```

| field | meaning |
|---|---|
| `id` | folder name, latin letters, digits and `_` |
| `name`, `description` | string, or `{"uk": …, "en": …}` |
| `category` | `time`, `info`, `system`, `anime`, `personal` or `other` — the section in the gallery |
| `sizes` | any of `small` (1×1 cell), `medium` (2×1), `large` (2×2); **the first is the default** |
| `interval` | seconds between redraws — keep it as large as the widget allows |
| `fast_if` | a `bool` option that switches to 1-second updates when on (e.g. a seconds hand) |
| `style`, `accent` | defaults for new copies (see [styles](#styles-and-colours)) |
| `color_accent` | colour used by the “Colour” style when the accent is “Default” |
| `uses` | services the widget needs: `"media"` (MPRIS player info → `L.RUN/media`), `"events"` (GNOME calendars → `L.RUN/events`) |

### Option types

Every option becomes a row in the widget's settings window, and its value reaches Lua as
`L.opt(key)` (always a string; use the helpers below).

| type | settings row | Lua |
|---|---|---|
| `bool` | switch | `L.opt_bool('key', true)` |
| `text` | text field | `L.opt('key', 'default')` |
| `longtext` | multi-line editor | `L.opt('key', '')` (newlines kept) |
| `date` | text field checked as `YYYY-MM-DD` | `L.opt('key', '')` |
| `number` | spin button (`min`, `max`, `step`) | `L.opt_num('key', 10)` |
| `choice` | drop-down (`choices`) | `L.opt('key', 'a')` |
| `multi` | list of switches (`choices`, optional `limit`) | `L.opt_list('key', 'a,b')` → `{'a', 'b'}` |
| `file` | file chooser (images) | `L.opt('key_png')` — a PNG copy, ready for `L.image()` |

`choices` is a list of `{"value": "...", "label": {"uk": "...", "en": "..."}}`.
`default`, `label` and `hint` (shown as a tooltip/description) can be localised the same way.

## widget.lua

```lua
local L = require 'lib'
local C, F, PAD, ICON = L.C, L.F, L.PAD, L.ICON

local W = {}

function W.tick()            -- optional: before every draw; read data, start fetchers
end

function W.draw(cr, x, y, w, h)   -- required: paint the card
    L.card(cr, x, y, w, h)
    L.header(cr, x + PAD, y + 30, w - 2 * PAD, ICON.star, L.acc(L.SYS.purple), L.tr('Привіт', 'Hello'))
end

function W.click(cx, cy, w, h)    -- optional: left click inside the card; return true if handled
    return false
end

W.scale = { large = 'small' }     -- optional: draw "large" as the small layout scaled up
return W
```

* `cr` is a Cairo context; every Cairo call from [conky's Cairo bindings](https://github.com/brndnmtthws/conky/wiki/Using-Lua-scripts-in-conky)
  is available (`cairo_arc`, `cairo_fill`, …).
* Coordinates are **base units**: a small card is 170×170, medium 356×170, large 356×356 (with the
  default gap). The global “widget size” setting scales everything, you never have to.
* `L.SIZE` is `'small' | 'medium' | 'large'` — branch on it for different layouts. If a layout is
  just a bigger version of another, use `W.scale` instead of writing it twice.
* Right-click is reserved (it opens the settings window).

## The `lib` API

### Context

| value | |
|---|---|
| `L.ID`, `L.TYPE` | instance id (`note-2`), widget type (`note`) |
| `L.SIZE`, `L.STYLE`, `L.LANG` | `small/medium/large`, style key, `uk`/`en` |
| `L.DEMO` | `true` when rendering a gallery thumbnail/preview — show sample data then |
| `L.RUN` | runtime directory (tmpfs) for caches: `L.RUN .. '/my-cache'` |
| `L.CONFIG` | `~/.config/deskwidgets` (user files, e.g. known network devices) |
| `L.WIDGET_DIR` | this widget's folder (scripts, images) |
| `L.HOME` | home directory |

### Colours and fonts

| | |
|---|---|
| `L.C.text`, `L.C.text2`, `L.C.text3` | primary / secondary / tertiary text for the current style |
| `L.SYS.red`, `.green`, `.blue`, `.orange`, `.yellow`, `.pink`, `.purple`, `.cyan`, `.indigo`, `.teal` | Apple system colours |
| `L.acc(default)` | **highlight colour**: the user's accent, else the style's, else `default`. Use it for the things that should follow the chosen colour (header icons, rings, highlights) |
| `L.pair(default_pair)` | gradient pair for rings/bars that follows the accent: `L.pair(L.RING.cpu)` |
| `L.RING.cpu`, `.mem`, `.disk`, `.battery`, `.warn`, `.hot`, … | ready-made gradient pairs |
| `L.ring_from(colour)` | gradient pair from one colour |
| `L.ink(alpha)`, `L.line`, `L.track`, `L.fill` | neutral colours that work on light and dark cards |
| `L.hex('FF375F', alpha)`, `L.mix(a, b, t)`, `L.shade(c, ±t)` | colour helpers (colours are `{r, g, b, a}` 0…1) |
| `L.T.dark` | `true` if the card is dark (light text) |
| `L.F.light`, `.regular`, `.medium`, `.semibold`, `.bold`, `.heavy` | text weights (the user's font) |
| `L.F.icon` | [Material Design Icons](https://pictogrammers.com/library/mdi/) — `L.ICON.*` or any code point |
| `L.F.cjk` | Japanese font (Noto Sans CJK JP) |

### Drawing

| function | |
|---|---|
| `L.card(cr, x, y, w, h)` | the card background in the current style (shadow, gradient, decorations). Call it first |
| `L.header(cr, x, y, w, icon, colour, title, right_text?, right_icon?)` | icon + title row, optional note on the right |
| `L.set(cr, colour, alpha?)`, `L.font(cr, face, size)` | source colour, font |
| `L.text(cr, s, x, y, align?)` | text at baseline `y`; `align` = `'l'`, `'c'`, `'r'`; returns the left x |
| `L.width(cr, s)`, `L.fit(cr, s, maxw)`, `L.wrap(cr, s, maxw, lines)` | measure, ellipsize, word-wrap |
| `L.spaced(cr, s, x, y, spacing, align?)` | letter-spaced caps label |
| `L.value_unit(cr, '63', '%', cx, y, vface, vsize, uface, usize)` | big number + small unit, centred |
| `L.icon(cr, code, size, x, y, align?)`, `L.icon_mid(cr, code, size, cx, cy)` | icons |
| `L.ring(cr, cx, cy, r, width, fraction, c1, c2)` | Apple-Watch-style ring |
| `L.bar(cr, x, y, w, h, fraction, c1, c2?)` | rounded progress bar |
| `L.divider(cr, x, y, w)`, `L.rrect(cr, x, y, w, h, r)`, `L.vgrad(cr, y0, y1, c0, c1)` | shapes |
| `L.image(path)`, `L.cover(cr, surface, x, y, w, h, r)` | load a PNG (cached) and draw it cropped into a rounded rect |
| `L.sparkle(cr, cx, cy, r, colour)`, `L.petal(cr, cx, cy, size, angle, colour)` | anime decorations |

### Data

| function | |
|---|---|
| `L.opt`, `L.opt_bool`, `L.opt_num`, `L.opt_list` | options (see above) |
| `L.due(key, seconds)` | `true` once every `seconds` — throttle work in `tick()` |
| `L.read_file(path)`, `L.read_kv(path)`, `L.fields('a|b|c')` | read caches |
| `L.cp('${cpu cpu0}')` | any [conky variable](https://conky.cc/variables) |
| `L.popen(cmd)` | run a command and read its output (blocks drawing — use rarely, behind `L.due`) |
| `L.spawn(cmd)`, `L.python('fetch.py', args…)` | run something in the background |
| `L.quote(s)`, `L.key(s)` | shell quoting; file-name-safe key for cache names |
| `L.tr(uk, en)`, `L.day_month(t)`, `L.plural(n, one, few, many)` | language helpers |
| `L.WEEKDAY`, `L.WEEKDAY_CAP`, `L.WDAY_SHORT`, `L.WEEK_SHORT`, `L.MONTH`, `L.MONTH_GEN` | day and month names |

## Getting data from the internet

Never download inside `draw()` — it would freeze the widget. Start a helper in the background
from `tick()` and read what it wrote:

```lua
local CACHE = L.RUN .. '/my-widget-' .. L.key(L.opt('city', 'Kyiv'))
local data = {}

function W.tick()
    if L.DEMO then data = { temp = '21' } return end
    if L.due('fetch', 900) then L.python('fetch.py', CACHE, L.opt('city', 'Kyiv')) end  -- every 15 min
    if L.due('read', 30) then data = L.read_kv(CACHE) or {} end
end
```

`fetch.py` writes `key=value` lines to a temporary file and renames it over the cache, so the
widget never reads a half-written file. See `widgets/weather/fetch.py`,
`widgets/anime_schedule/fetch.py` and `examples/github_repo`.

## Styles and colours

The user picks a style (Glass, Graphite, Light, Colour, Sakura, Manga, Neon, no card) and an accent
colour for every copy of your widget. To look right in all of them:

* start with `L.card(...)`, draw text with `L.C.text/text2/text3`;
* use `L.acc(default)` and `L.pair(...)` for highlights, so the chosen colour shows up;
* use `L.line`, `L.track`, `L.fill` instead of hard-coded white/black;
* show sample data when `L.DEMO` is set (thumbnails are rendered that way).

Check them all quickly:

```sh
for s in glass light color sakura manga neon; do deskwidgets preview my_widget --style $s -o /tmp/$s.png; done
```

## Sharing

* Put the folder in a git repository — people clone it into `~/.config/deskwidgets/widgets/`.
* Or open a pull request that adds it to `widgets/` here (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
