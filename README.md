# DeskWidgets

**macOS-style desktop widgets for Linux and Windows** — clocks, calendar, weather, system monitors, now playing,
events, notes, exchange rates, Docker… and anime widgets. They snap to a grid, come in three sizes
and nine styles with any accent colour, every widget has its own settings, and writing your own
widget is a folder with two files.

🇺🇦 [Українською](README.uk.md)

![DeskWidgets on a desktop](docs/screenshots/hero-en.png)

## Features

* **21 widgets**: analog & digital clock, world clock, calendar (week/month), weather (now, hourly,
  3 days), up next (GNOME calendars, incl. Google), now playing (any MPRIS player, cover art, click to
  pause), system, processor, memory, disks, network (+ unknown-device watch), batteries (+ Bluetooth
  devices and Pulsar/Kysona-style 2.4 GHz gaming mice), Docker, note, countdown, picture, hryvnia exchange rates, and **anime**: next episodes from
  AniList, manga clock, anime buddy.
* **Grid**: drag a widget with the mouse — the grid appears and the widget snaps into the nearest free cell.
* **Three sizes** for every widget: small (1×1 cell), medium (2×1), large (2×2), plus a global
  widget size from 80 % to 150 %.
* **Nine styles** — Glass, Graphite, Light, Colour, Sakura, Manga, Neon, and borderless (dark or light
  text) — and **13 accent colours** that tint the card and the highlights.
* **Settings for each widget** (city, cities, currencies, text, dates, pictures…) with a live preview.
  Right-click a widget to open them. Add several copies of the same widget.
* **Write your own** widget in Lua with a small drawing API — `deskwidgets new my_widget` gives you a
  working template. See [docs/creating-widgets.md](docs/creating-widgets.md).
* Ukrainian and English UI. Runs on **Windows 10/11**, and on Linux under X11 and Wayland (through Xwayland).

| Styles | Sizes |
|---|---|
| ![styles](docs/screenshots/styles-en.png) | ![sizes](docs/screenshots/sizes-en.png) |

![anime widgets](docs/screenshots/anime-en.png)

## Install

DeskWidgets is **one program** written in Rust: the widgets, fonts and icons are inside it, drawing needs no
Cairo and no Python. No other widget program is needed.

### Windows 10 / 11

Download `deskwidgets-windows-x86_64.zip` from [Releases](../../releases), unpack it anywhere and in a terminal there:

```bat
deskwidgets setup       :: autostart and a Start-menu entry
deskwidgets             :: start; the tray icon opens the gallery
```

Widgets that need Linux services (Up next, mouse battery) say so in the gallery and are not offered.
*"Now playing"* works with any player that shows in the volume flyout.

### Debian, Ubuntu, Mint, Pop!_OS

Download `deskwidgets_<version>_amd64.deb` from [Releases](../../releases) and:

```sh
sudo apt install ./deskwidgets_*_amd64.deb
```

Then open **DeskWidgets** from the app menu (the first start sets up autostart and
<kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd>).

### Any Linux distribution, from source

Needs an X11 session or Xwayland (GNOME, KDE Plasma, … have it) and the [Rust toolchain](https://rustup.rs).
The gallery window needs OpenGL (Mesa) and `libxkbcommon`, which every desktop already has; for Japanese text in the
anime widgets install a CJK font; "Up next" needs Evolution Data Server (it comes with GNOME).

```sh
# Debian/Ubuntu                                    # Fedora                                  # Arch
sudo apt install build-essential fonts-noto-cjk    # sudo dnf install gcc google-noto-sans-cjk-fonts   # sudo pacman -S base-devel noto-fonts-cjk

git clone https://github.com/T0ks1k24/DeskWidgets
cd DeskWidgets
./install.sh            # builds and installs into ~/.local; or: sudo ./install.sh --system
deskwidgets setup       # autostart, Super+Alt+W (GNOME), menu entry
deskwidgets             # start
```

Try it without installing: `make run` from the checkout (the widgets are read from the folder, so edits show up
after `deskwidgets reload`). Check what is missing: `deskwidgets doctor`.

**Wireless gaming mice** (Pulsar, Kysona, ATK, VXE … on Compx/CX receivers) report their battery only
over a vendor protocol; DeskWidgets reads it itself. A from-source install needs a udev rule once
(the .deb and `--system` install it): `deskwidgets mice` prints the exact command and the result.

## Use

| | |
|---|---|
| <kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> or **DeskWidgets** in the app menu | the gallery: your widgets, add new ones, settings |
| drag with the left mouse button | move a widget; the grid shows and it snaps to the nearest free cell |
| right-click a widget | its settings (size, style, colour, options, remove) |
| left-click | widget actions: pause music, open the show on AniList… |

<kbd>Super</kbd> is the <kbd>Win</kbd> key. On Windows the gallery hotkey is <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd>
and the tray icon opens it too. Moving a widget with the window manager (<kbd>Super</kbd>+drag on GNOME) works as well.

The command line does the same:

```sh
deskwidgets types                 # every available widget
deskwidgets add weather           # add one (as many copies as you like)
deskwidgets list                  # what is on the desktop
deskwidgets configure weather-2   # settings window of one widget
deskwidgets arrange               # pack everything onto the grid
deskwidgets preview clock --size large --style sakura -o clock.png
deskwidgets quit
```

Configuration lives in `~/.config/deskwidgets/` (`%APPDATA%\\DeskWidgets` on Windows) — see [docs/configuration.md](docs/configuration.md).

## Make your own widget

```sh
deskwidgets new pomodoro
$EDITOR ~/.config/deskwidgets/widgets/pomodoro/widget.lua
deskwidgets reload && deskwidgets add pomodoro
```

A widget is `widget.json` (name, sizes, options → the settings window is generated from it) and
`widget.lua` (drawing). Read [docs/creating-widgets.md](docs/creating-widgets.md) and look at
[examples/](examples) and the built-in [widgets](widgets). Pull requests with new widgets are welcome —
see [CONTRIBUTING.md](CONTRIBUTING.md).

## How it works

One service (`deskwidgets run`) owns every widget window. Each widget runs `widget.lua` in its own embedded
Lua 5.4 ([mlua](https://github.com/mlua-rs/mlua)) and draws with a Cairo-style API on a pure-Rust rasteriser
([tiny-skia](https://github.com/linebender/tiny-skia), fonts through ttf-parser) into a transparent window; Windows
and Linux differ only in the small window layer (Win32 layered windows, or X11/Xwayland over a plain socket, no libX11).
The service keeps widgets on the grid, feeds them live data (media player over D-Bus, calendars, Docker, batteries)
and is controlled by the CLI and the gallery (egui) over a local socket. Downloads for widgets run in the background
(`L.http`), so a slow network never stops a frame.

```
src/
  core/         settings, widget catalog, grid, layout: plain logic, no windows
  engine/       the widget runtime: Lua + canvas, fonts, images, system info, downloads; renders headless too
  platform/     one file per OS behind mod.rs: x11.rs, win32.rs; autostart and hotkeys (integration.rs)
  providers/    background data: media player, calendars, Docker, batteries
  service/      the daemon: widget windows, dragging, the control socket
  gallery/      the gallery window (egui)
lua/            lib.lua (the widget API) and main.lua
widgets/        the built-in widgets: widget.json + widget.lua
assets/         icon font, icons, udev rule
tests/          integration tests: engine (every widget renders), service, X11 backend (Xvfb), core
examples/       widgets to copy from, the screenshot generator
packaging/      .deb build
```

## Credits

* [tiny-skia](https://github.com/linebender/tiny-skia) draws everything, [Lua](https://www.lua.org) (through [mlua](https://github.com/mlua-rs/mlua)) runs the widgets, [egui](https://www.egui.rs) is the gallery.
* Icons: [Material Design Icons](https://pictogrammers.com/library/mdi/) (Pictogrammers Free License / Apache 2.0, bundled in `fonts/`).
* Data: [wttr.in](https://wttr.in), [AniList](https://anilist.co), [National Bank of Ukraine](https://bank.gov.ua/en/open-data/api-dev).

## License

MIT — see [LICENSE](LICENSE). The bundled icon font keeps its own license.
