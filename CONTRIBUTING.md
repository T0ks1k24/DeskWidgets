# Contributing

Thanks for helping! The easiest and most welcome contribution is **a new widget**.

## Adding a widget

1. `deskwidgets new my_widget` — or copy a similar widget from `widgets/` into
   `~/.config/deskwidgets/widgets/` and change it. Read [docs/creating-widgets.md](docs/creating-widgets.md).
2. Make it work in **all its sizes** and in the styles: `make previews` or
   `deskwidgets preview my_widget --size large --style manga -o /tmp/w.png`.
3. Provide both languages (`L.tr('…', '…')`, `{"uk": …, "en": …}` in widget.json) — if you don't speak
   one of them, write it in English and say so in the PR; someone will translate.
4. Show sample data when `L.DEMO` is set (the gallery thumbnails use it).
5. No blocking work in `draw()`: network and slow commands go into a helper started from `tick()`
   behind `L.due(...)`.
6. Move the folder into `widgets/` and open a pull request with a screenshot.

## Code

* Python: the standard library + PyGObject only; run `make check`.
* Lua: Lua 5.3/5.4 as embedded in conky; keep widgets self-contained, put shared helpers in
  `engine/lib.lua` (and document them).
* Keep the UI bilingual (`tr('український', 'English')`).
* `make deb` must still build.

## Releases

Tag `vX.Y.Z` after bumping `VERSION` in `deskwidgets/__init__.py` and `CHANGELOG.md`; the GitHub
workflow builds the `.deb` and attaches it to the release.
