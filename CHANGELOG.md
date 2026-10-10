# Changelog

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
