# Configuration

Everything can be changed in the gallery (**Settings** tab and each widget's settings window).
The files are plain JSON if you prefer an editor or want to keep them in your dotfiles.

| path | |
|---|---|
| `~/.config/deskwidgets/settings.json` | global settings (below) |
| `~/.config/deskwidgets/layout.json` | widgets on the desktop: type, size, style, accent, options, grid cell |
| `~/.config/deskwidgets/widgets/` | your own widgets (one folder each) |
| `~/.config/deskwidgets/network_known.txt` | known LAN devices for the Network widget: `mac|name` per line |
| `~/.cache/deskwidgets/previews/` | gallery thumbnails |
| `$XDG_RUNTIME_DIR/deskwidgets/` | runtime caches, generated font config, grid description |

## settings.json

```json
{
  "language": "auto",
  "scale": 1.0,
  "gap": 16,
  "margin_top": 44,
  "margin_bottom": 88,
  "margin_side": 24,
  "font": "Ubuntu Sans",
  "default_style": "auto",
  "default_accent": "auto",
  "autostart": true,
  "hotkey": true,
  "grid_overlay": true
}
```

| key | |
|---|---|
| `language` | `auto` (from `LANG`), `uk`, `en` |
| `scale` | widget size, 0.6 – 2.0 (a small card is 170 px at 1.0) |
| `gap` | pixels between widgets |
| `margin_top`, `margin_bottom`, `margin_side` | free space around the grid (top bar, dock) |
| `font` | text font family; icons are bundled |
| `default_style`, `default_accent` | style and colour for newly added widgets (`auto` = the widget's own) |
| `autostart` | start with the session (`~/.config/autostart/io.github.toksik.DeskWidgets.desktop`) |
| `hotkey` | GNOME custom shortcut <kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> for the gallery |
| `grid_overlay` | GNOME Shell helper (`deskwidgets@toksik.github.io`) |

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
(GNOME, KDE Plasma, …). Moving with <kbd>Super</kbd>+drag (KDE: <kbd>Alt</kbd>/<kbd>Meta</kbd>+drag) is
the window manager's feature; the snapping is done by DeskWidgets everywhere. The hotkey and the grid
helper are GNOME-only — elsewhere bind `deskwidgets gallery` to a shortcut yourself.

## Wireless mouse battery

Gaming mice on Compx/CX receivers (Pulsar, Kysona, ATK, VXE …) don't expose their battery to the kernel
yet; DeskWidgets asks the mouse through `/dev/hidraw*` with the same read-only commands the Linux
`hid-pulsar` driver uses. `data/udev/70-deskwidgets-hid.rules` gives the logged-in user access to the
vendor interface of the listed **mouse** receivers only (never keyboards — their interfaces carry
typing). `deskwidgets mice` shows what is found and readable. To add your mouse, copy a line of the
rule with your `idVendor`/`idProduct` from `lsusb` and open an issue so it can be added for everyone.

## Troubleshooting

* `deskwidgets doctor` lists missing pieces.
* Logs: `journalctl --user -f` (or run `deskwidgets` in a terminal).
* A widget shows an error on its card: the message comes from its `widget.lua`.
* "Now playing" is empty with snap Firefox when DeskWidgets was started from an app that has its own
  AppArmor profile (e.g. a VS Code terminal): start it from the session (autostart, app menu) instead.
