# Creating widgets

A DeskWidgets widget is **a folder with two files**:

```
my_widget/
├── widget.json   # name, sizes, options — the gallery builds the settings window from it
└── widget.lua    # draws the card with Cairo (via the helper library `lib`)
```

Optional extras: images, extra Lua modules (`require 'mymodule'` finds `mymodule.lua` next to `widget.lua`), helper scripts, a README.

Your own widgets live in **`~/.config/deskwidgets/widgets/<id>/`** (`%APPDATA%\DeskWidgets\widgets\<id>\` on
Windows). A folder there with the same id as a built-in widget replaces it, so you can also fork a built-in one
by copying `widgets/<id>` from this repo there.

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
  "platforms": [],
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
| `uses` | background services the widget needs; each is started only while a widget uses it and read with `L.service(name)`: `"media"` (now playing), `"events"` (GNOME calendars, Linux only), `"docker"` (containers), `"batteries"` (Bluetooth and wireless-mouse batteries) |
| `platforms` | operating systems the widget works on, `["linux"]`, `["windows"]` or both; empty = all. Others do not offer it |

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

* `cr` is a Cairo context; the usual C-style Cairo calls are available (`cairo_arc`, `cairo_fill`,
  `cairo_pattern_create_linear`, `cairo_show_text` …, with the `CAIRO_*` constants), exactly as in
  [conky's Cairo bindings](https://github.com/brndnmtthws/conky/wiki/Using-Lua-scripts-in-conky). Most functions are
  `cairo_<method>(cr, …)` for the Cairo context method of that name (`arc`, `curve_to`, `clip`, `show_text` …).
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
| `L.RUN` | scratch directory for caches: `L.RUN .. L.SEP .. 'my-cache'` |
| `L.CONFIG` | DeskWidgets' config folder (user files, e.g. known network devices) |
| `L.WIDGET_DIR` | this widget's folder (scripts, images) |
| `L.HOME` | home directory |
| `L.OS`, `L.SEP` | `'linux'` or `'windows'`; the path separator (`/` or `\`) |

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
| `L.F.cjk`, `L.HAS_CJK` | a font with Japanese glyphs the system has, and whether there is one |

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
| `L.sys.cpu()`, `.memory()`, `.disks()`, `.disk(path)`, `.net()`, `.battery()`, `.temps()`, `.top(n)`, `.uptime()` | the machine, the same on Linux and Windows (tables below) |
| `L.service(name)` | latest data of a background service from `"uses"`: `'media'`, `'events'`, `'docker'`, `'batteries'`; `nil` when it is not running |
| `L.media_control('PlayPause')` | `'PlayPause'`, `'Next'`, `'Previous'` for the current media player |
| `L.http(url, opts)`, `L.download(url)`, `L.json(text)`, `L.json_encode(v)`, `L.urlencode(s)` | downloads that never block a frame, JSON in and out (see *Getting data from the internet*) |
| `L.run({'program', 'arg'})`, `L.python('helper.py', args…)` | run a program / a Python helper script of this widget in the background, no shell involved (`L.python` needs Python installed; `L.EXE` is the `deskwidgets` program itself) |
| `L.open_url(url)`, `L.notify(title, text)` | open a web address or file in the default app; show a notification |
| `L.tz_offset('Europe/Kyiv')` | seconds east of UTC right now in that time zone |
| `L.read_file(path)`, `L.read_kv(path)`, `L.fields('a\|b\|c')` | read files and caches written by a helper program |
| `L.key(s)` | a file-name-safe key for a string (cache file names) |
| `L.tr(uk, en)`, `L.day_month(t)`, `L.plural(n, one, few, many)` | language helpers |
| `L.WEEKDAY`, `L.WEEKDAY_CAP`, `L.WDAY_SHORT`, `L.WEEK_SHORT`, `L.MONTH`, `L.MONTH_GEN` | day and month names |
| `L.spawn(cmd)`, `L.popen(cmd)`, `L.quote(s)`, `L.cp('${cpu}')` | shell command line in the background / its output (**blocks drawing**), quoting, a few conky variables. Kept so older widgets work; they differ between Linux and Windows, prefer the functions above |

`L.sys` tables (percentages 0–100, sizes in bytes, speeds in KiB/s; a missing value is `nil`):

| call | fields |
|---|---|
| `cpu()` | `total`, `cores` (array), `count`, `load1`, `load5`, `load15`, `processes`, `freq_ghz` |
| `memory()` | `total`, `used`, `available`, `cached`, `swap_total`, `swap_used` |
| `disks()` | array of `{name, mount, device, fstype, total, used}`; `name` is `'system'` for the system disk, `'home'` for a home partition |
| `net()` | `iface`, `kind` (`'wifi'`/`'ethernet'`), `name` (SSID), `ip`, `down`, `up`, `total_down`, `total_up` |
| `battery()` | `percent`, `plugged`, `charging`, `full`, `secs_left`; `nil` on a desktop PC |
| `temps()` | `cpu`, `gpu` (°C) when the OS exposes sensors (Linux) |
| `top(n)` | array of the busiest processes `{name, cpu, mem}` |

## Getting data from the internet

Never download inside `draw()` or `tick()` by running a program and waiting for it — every widget is drawn by the same
service, so one slow call freezes them all (the log says "a frame took …s"). `L.http` does the waiting for you: the
first call starts the download in the background and returns `nil`, every later call returns what has arrived so far,
and the data is refreshed on its own every `every` seconds.

```lua
local data = {}
local stamp = -1

function W.tick()
    if L.DEMO then data = { temp = 21 } return end
    local url = 'https://wttr.in/' .. L.urlencode(L.opt('city', 'Kyiv')) .. '?format=j1'
    local body, status, age, st = L.http(url, { every = 900 })          -- every 15 minutes
    if body and st ~= stamp then          -- `stamp` grows with every successful download: parse only then
        stamp = st
        local json = L.json(body)         -- nil, error for bad JSON
        if json then data = { temp = json.current_condition[1].temp_C } end
    end
end
```

`L.http(url, opts)` returns `body, status, age, stamp` (`nil, 0, 0, 0` until the first answer). Options: `every`
(seconds, default 900), `method`, `body` (a string; a body makes it a `POST`), `headers` (a table). A failed download
is retried after 30 seconds and keeps the old data. `L.json_encode(table)` makes the JSON for a request body.

`L.download(url)` is the same for files: it returns the local path once the file has been downloaded (and `nil`
before), which `L.image(path)` can draw — pictures in any format. A file is downloaded once and kept.

If you need a program the network answers do not replace (a command-line tool, nmap …), start it with
`L.run({'program', 'arg'})` and read its output file in `tick()`; `L.python('helper.py', …)` runs a Python script that
sits next to `widget.lua` (the machine needs Python then). See `widgets/weather/widget.lua`, `widgets/anime_schedule/widget.lua`
and `examples/github_repo`.

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
