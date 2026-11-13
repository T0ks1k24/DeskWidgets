# Changelog

## 3.1.0 — 2026-11-13

* **Widget size is the only size setting**, 50 % to 120 % (it was 60 % – 200 % in the file and 80 % – 150 % in the
  gallery). The grid follows it by itself, and every widget stays where it was on the screen when you change it.
* The **Grid** group is gone from the gallery settings (spacing and margins): the grid follows the widget size on its own.
  `gap` and the `margin_*` values stay in settings.json for anyone who needs them.
* The settings page says when the **service is older than the gallery** (an upgrade does not restart it) and restarts it
  with one button.
* **Several monitors**: Settings → Monitors (`monitors` in settings.json) chooses the monitors that show widgets.
  Each has its own grid (on Windows its own DPI scale too); drag a widget from one to another, or pick the monitor in its settings window.
  `layout.json` gets a `monitor` per widget. Backends report every monitor (`Backend.monitors()`: XRandR on Linux,
  `EnumDisplayMonitors` on Windows). The default is still the primary monitor only.
* Fix: **right-clicking a widget did nothing** (Linux and Windows). It started `deskwidgets gallery --configure <id>`,
  which the command line rejected, and the error was thrown away. The service now runs `deskwidgets configure <id>`,
  `gallery --configure` is accepted as well, and errors of the gallery go to `gallery.log` next to `service.log`.
* Fix: **the Windows gallery did not exist** (`gallery/tk_app.py` was referenced but never added), so the tray item,
  the `Ctrl+Alt+W` hotkey and right click opened nothing on Windows. The Tk gallery is added: My widgets, Add,
  Settings, and each widget's settings window with a live preview; it also serves Linux without GTK 4.
* GNOME shortcut: the service points the gallery shortcut at itself on every start (a reinstall or a move left it
  pointing at a Python that was gone, and the key silently did nothing; only the command is touched, never the key
  you chose), every start of the gallery without a terminal leaves a line in `gallery.log`, and `deskwidgets doctor`
  has a hotkey row.

## 3.0.0

DeskWidgets no longer needs conky: widgets are drawn by DeskWidgets itself, which is what makes Windows possible.

* **Windows 10/11** support (layered windows, tray icon, autostart, Start-menu entry, `Ctrl+Alt+W`).
* **Any Linux**: plain Python (pycairo, lupa, psutil, Pillow), no conky, no GNOME-only parts; the GTK 4 gallery is
  optional (a Tk gallery is used without it); X11 and Xwayland.
* New **engine**: `widget.lua` runs in an embedded Lua, drawing is Cairo; one service process instead of one conky
  per widget; widgets can be rendered headless (`deskwidgets preview`), so every widget is tested in CI.
* Dragging is done by DeskWidgets: drag a widget with the mouse, the grid appears, it snaps on release. The GNOME
  Shell helper is gone (it is not needed any more); `lock` setting to forbid dragging.
* Cross-platform widget API: `L.sys` (CPU, memory, disks, network, battery, temperatures, processes), `L.service`
  (media, events, docker, batteries), `L.run`, `L.python`, `L.open_url`, `L.notify`, `L.tz_offset`. Built-in widgets
  no longer read `/proc`, run `df`/`upower`/`gdbus` or `bash`. `L.cp`, `L.spawn`, `L.popen` stay for old widgets.
* New layout: everything is inside the `deskwidgets` Python package (`core`, `engine`, `platform`, `providers`,
  `service`, `gallery`, `widgets`), installable with pip; control through a local socket instead of D-Bus.
* Fixes: widgets that did not load an image after it was downloaded, the "OFFLINE" state of the LAN watch showing
  "all devices known", layout files with odd ids or values could break widgets, a failing widget could take the others
  down, option values from the gallery are validated, many races around restarting widgets.
* Widget manifests can say `"platforms": ["linux"]`; the `uses` service `hid-batteries` is now `batteries`.
* Upgrading: widgets of your own that read `L.RUN/media`, `L.RUN/events`, `L.RUN/docker` must use `L.service(...)`
  (see docs/creating-widgets.md). Old 1.x/2.x services and conky processes are stopped on first start.

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
