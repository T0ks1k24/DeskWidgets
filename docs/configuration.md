# Configuration

Everything can be changed in the gallery (**Settings** tab and each widget's settings window).
The files are plain JSON if you prefer an editor or want to keep them in your dotfiles.

| Linux | Windows | |
|---|---|---|
| `~/.config/deskwidgets/settings.json` | `%APPDATA%\DeskWidgets\settings.json` | global settings (below) |
| `~/.config/deskwidgets/layout.json` | `%APPDATA%\DeskWidgets\layout.json` | widgets on the desktop: type, size, style, accent, options, grid cell |
| `~/.config/deskwidgets/widgets/` | `%APPDATA%\DeskWidgets\widgets\` | your own widgets (one folder each) |
| `~/.config/deskwidgets/network_known.txt` | the same folder | known LAN devices for the Network widget: `mac\|name` per line |
| `~/.cache/deskwidgets/` | `%LOCALAPPDATA%\DeskWidgets\cache\` | gallery thumbnails, converted pictures, `service.log` |
| `$XDG_RUNTIME_DIR/deskwidgets/` | `%LOCALAPPDATA%\DeskWidgets\run\` | widget caches, the control socket, the generated font config |

## settings.json

```json
{
  "language": "auto",
  "scale": 1.0,
  "gap": 16,
  "margin_top": 44,
  "margin_bottom": 88,
  "margin_side": 24,
  "monitors": [],
  "font": "auto",
  "default_style": "auto",
  "default_accent": "auto",
  "autostart": true,
  "hotkey": true,
  "show_grid": true,
  "lock": false
}
```

| key | |
|---|---|
| `language` | `auto` (from `LANG`), `uk`, `en` |
| `scale` | widget size, 0.5 – 1.2 (50 % – 120 %; a small card is 170 px at 1.0). The grid follows: a cell is a small card, so smaller widgets mean more, smaller cells |
| `gap` | pixels between widgets (not in the gallery any more: the grid takes care of itself; edit it here if you must) |
| `monitors` | ids of the monitors that show widgets, e.g. `["DP-1", "HDMI-1"]` (names from `xrandr` on Linux, `\\.\DISPLAY2` on Windows; the gallery lists them). `[]` = the primary monitor only. Monitors that are not plugged in are skipped; if none of them is, the primary one is used |
| `margin_top`, `margin_bottom`, `margin_side` | free space around the grid (top bar, dock); also file-only |
| `font` | text font family; `auto` = Ubuntu Sans if installed, else the system's sans-serif (Linux), Segoe UI (Windows). Applies after the service restarts (done for you when you change it in the gallery). Icons are bundled |
| `default_style`, `default_accent` | style and colour for newly added widgets (`auto` = the widget's own) |
| `autostart` | start with the session (`~/.config/autostart/io.github.toksik.DeskWidgets.desktop`; the `Run` registry key on Windows) |
| `hotkey` | <kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> (GNOME custom shortcut) or <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> (Windows) opens the gallery |
| `show_grid` | draw the grid while a widget is being dragged |
| `lock` | widgets cannot be dragged with the mouse |

After editing by hand run `deskwidgets reload` (widgets) — the gallery applies its own changes itself.

## layout.json

```json
{
  "version": 3,
  "instances": [
    {"id": "weather", "type": "weather", "size": "medium", "style": "glass", "accent": "auto",
     "options": {"city": "Lviv", "title": "Львів"}, "monitor": "DP-1", "col": 0, "row": 1}
  ]
}
```

`col`/`row` are grid cells counted from the top-left of the widget's `monitor`, whose grid is centred on that monitor.
When the widget size changes, every widget stays where it was on the screen (one at the right or bottom edge stays
at that edge) and the numbers are converted. A widget that does not fit (another screen size) moves to the nearest
free cell, and one whose monitor is unplugged or switched off moves to the first monitor and stays there. A file
without `monitor` (older versions) puts everything on the first monitor.

## Several monitors

Settings → Monitors (shown when more than one is connected) switches monitors on and off; at least one stays on.
Every monitor that is on has its own grid, fitted to its own resolution (and, on Windows, drawn at its own DPI scale,
so a widget has the same physical size on a 4K and on a full HD screen; Linux has one DPI setting for all). While you drag a widget the grid follows it from monitor to monitor and it snaps into the
cell it is over when you let go. A widget's settings window has a **Monitor** choice too, the service's `add` and `move` commands accept a
`monitor`, and **Arrange on the grid** packs each monitor separately.

## Styles and accents

Styles: `glass`, `graphite`, `light`, `color`, `sakura`, `manga`, `neon`, `clear_dark`, `clear_light`.
Accents: `auto`, `red`, `orange`, `yellow`, `green`, `mint`, `teal`, `blue`, `indigo`, `purple`, `pink`,
`sakura`, `graphite`.

## Other desktops

The widgets are X11 windows, so they work on any X11 desktop and on Wayland compositors with Xwayland
(GNOME, KDE Plasma, …). You drag them with the mouse; DeskWidgets does the snapping itself everywhere, so
moving one with the window manager (<kbd>Super</kbd>+drag on GNOME, <kbd>Alt</kbd>/<kbd>Meta</kbd>+drag on KDE) works too. The hotkey is set up only on
GNOME — elsewhere bind `deskwidgets gallery` to a shortcut yourself.

On Windows the widgets are layered windows kept below your other windows; <kbd>Win</kbd>+<kbd>D</kbd>
(show desktop) hides them like it hides everything. The tray icon opens the gallery.

## Wireless mouse battery

Gaming mice on Compx/CX receivers (Pulsar, Kysona, ATK, VXE …) don't expose their battery to the kernel
yet; DeskWidgets asks the mouse through `/dev/hidraw*` with the same read-only commands the Linux
`hid-pulsar` driver uses. `deskwidgets/assets/udev/70-deskwidgets-hid.rules` gives the logged-in user access to the
vendor interface of the listed **mouse** receivers only (never keyboards — their interfaces carry
typing). `deskwidgets mice` shows what is found and readable. To add your mouse, copy a line of the
rule with your `idVendor`/`idProduct` from `lsusb` and open an issue so it can be added for everyone.

## Troubleshooting

* `deskwidgets doctor` lists missing pieces.
* Logs: `~/.cache/deskwidgets/service.log` (`%LOCALAPPDATA%\DeskWidgets\cache\service.log`), or run `deskwidgets run` in a terminal.
* The gallery (hotkey, tray icon, right click) did not open: its errors are in `gallery.log` next to `service.log`;
  `deskwidgets gallery` in a terminal shows them directly.
* `deskwidgets status` shows whether the service runs.
* A widget makes everything stutter: the log says "a frame took …s" — slow work belongs in a helper script (see the widget guide).
* A widget shows an error on its card: the message comes from its `widget.lua`.
* "Now playing" is empty with snap Firefox when DeskWidgets was started from an app that has its own
  AppArmor profile (e.g. a VS Code terminal): start it from the session (autostart, app menu) instead.
