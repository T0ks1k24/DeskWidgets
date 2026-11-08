# Changelog

## 3.0.0

DeskWidgets no longer needs conky, and it is no longer a Python program: widgets are drawn by DeskWidgets itself,
which is what makes Windows possible, and the whole thing is **one executable written in Rust**.

* **Windows 10/11** support (layered windows, tray icon, autostart, Start-menu entry, `Ctrl+Alt+W`).
* **One binary, nothing to install around it**: no Python, no Cairo, no GTK. Widgets, fonts and icons are inside it;
  rendering is pure Rust (tiny-skia, fonts read with ttf-parser), so Linux and Windows draw identically. X11 is spoken
  directly over the socket (x11rb), D-Bus with zbus, the gallery is egui.
* New **engine**: `widget.lua` runs in an embedded Lua 5.4, drawing is a Cairo-style API (`cairo_arc`, `cairo_fill` …
  as before, so existing widgets work unchanged); one service process instead of one conky per widget; widgets can be
  rendered headless (`deskwidgets preview`), so every widget is tested in CI.
* Dragging is done by DeskWidgets: drag a widget with the mouse, the grid appears, it snaps on release. The GNOME
  Shell helper is gone (it is not needed any more); `lock` setting to forbid dragging.
* Cross-platform widget API: `L.sys` (CPU, memory, disks, network, battery, temperatures, processes), `L.service`
  (media, events, docker, batteries), `L.run`, `L.open_url`, `L.notify`, `L.tz_offset`. Built-in widgets
  no longer read `/proc`, run `df`/`upower`/`gdbus` or `bash`. `L.cp`, `L.spawn`, `L.popen` stay for old widgets.
* **Downloads from Lua**: `L.http`, `L.download`, `L.json`, `L.json_encode`, `L.urlencode` fetch data in the
  background without ever blocking a frame. The built-in weather, rates and anime widgets use them, so none of them
  needs a helper script any more (`L.python` still runs one for widgets of your own, if Python is installed). The
  LAN watch of the Network widget is `deskwidgets netwatch`.
* "Now playing" follows MPRIS players by polling D-Bus once a second and on Windows uses the system media controls
  directly (no `winsdk`); "Up next" reads Evolution Data Server over D-Bus and expands recurring events itself.
* The gallery draws its pictures with the same engine, in-process; changing the font no longer restarts the service.
* Fixes: widgets that did not load an image after it was downloaded, the "OFFLINE" state of the LAN watch showing
  "all devices known", layout files with odd ids or values could break widgets, a failing widget could take the others
  down, option values from the gallery are validated, many races around restarting widgets.
* Widget manifests can say `"platforms": ["linux"]`; the `uses` service `hid-batteries` is now `batteries`.
* Upgrading: widgets of your own that read `L.RUN/media`, `L.RUN/events`, `L.RUN/docker` must use `L.service(...)`
  (see docs/creating-widgets.md). Old 1.x/2.x conky widgets and the Python service of a 3.0 preview are stopped on
  first start. Install with `./install.sh` (needs the Rust toolchain), the `.deb` or the Windows zip.

## 2.0.0 — 2026-10-10

First public release.

* Every widget is its own window on a **grid**; drag with Super, it snaps into the nearest free cell.
  GNOME Shell helper shows the grid and the target cell while dragging.
* **21 widgets**, each in small, medium and large: clock, digital clock, world clock, calendar, weather,
  up next, now playing, system, processor, memory, disks, network, batteries, Docker, note, countdown,
  picture, NBU rates, anime next episodes (AniList), manga clock, anime buddy.
* **Nine styles** (glass, graphite, light, colour, sakura, manga, neon, borderless ×2) and **13 accent
  colours** that tint the card.
* Per-widget settings generated from `widget.json`, with a live preview; right-click a widget to open
  them; several copies of the same widget.
* Widgets are plugins: `~/.config/deskwidgets/widgets/<id>/widget.json + widget.lua`, `deskwidgets new`,
  `deskwidgets preview`, documented drawing API.
* Global settings: language (uk/en), widget size, grid spacing, font, defaults, autostart, hotkey.
* Bundled icon font, `.deb` package, `install.sh` for other distributions.
* Imports the layout of the earlier personal version (`~/.config/conky/mac`).
