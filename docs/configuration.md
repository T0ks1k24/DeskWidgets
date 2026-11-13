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
| `scale` | widget size, 0.6 – 2.0 (a small card is 170 px at 1.0) |
| `gap` | pixels between widgets |
| `margin_top`, `margin_bottom`, `margin_side` | free space around the grid (top bar, dock) |
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
     "options": {"city": "Lviv", "title": "Львів"}, "col": 0, "row": 1}
  ]
}
```

`col`/`row` are grid cells counted from the top-left; the grid is centred on the screen. Widgets that
don't fit (another screen size) move to the nearest free cell.

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
* `deskwidgets status` shows whether the service runs.
* A widget makes everything stutter: the log says "a frame took …s" — slow work belongs in a helper script (see the widget guide).
* A widget shows an error on its card: the message comes from its `widget.lua`.
* "Now playing" is empty with snap Firefox when DeskWidgets was started from an app that has its own
  AppArmor profile (e.g. a VS Code terminal): start it from the session (autostart, app menu) instead.
