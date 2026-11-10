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
6. Move the folder into `deskwidgets/widgets/` and open a pull request with a screenshot.
7. If it needs something Linux-only (or Windows-only), say so in `widget.json` (`"platforms": ["linux"]`), and
   prefer `L.sys`/`L.service`/`L.run` over shell commands so it works on both.

## Code

* Python 3.12+; runtime dependencies are pycairo, lupa, psutil and Pillow only (PyGObject is optional and
  imported lazily, only for the GTK gallery and the D-Bus services). No `if sys.platform` outside `platform/`,
  `providers/` and the few places that already have one.
* Lua: 5.4 (as embedded by lupa; 5.3 works too); keep widgets self-contained, put shared helpers in
  `deskwidgets/engine/lua/lib.lua` (and document them in docs/creating-widgets.md).
* Keep the UI bilingual (`tr('український', 'English')`).
* `make deb` must still build.

```sh
pip install pytest pycairo lupa psutil pillow
python -m pytest            # core, engine (every widget renders), service, providers, backends
xvfb-run -a python -m pytest   # on a headless Linux box: also the X11 backend tests
```

Every widget is rendered in every size in the tests, so a widget that errors fails the build. The X11 and
Win32 backends are tested against a virtual X display and (for Win32) as far as Wine allows; please test on
real Windows/Linux desktops before changing `platform/`.

## Releases

Tag `vX.Y.Z` after bumping `VERSION` in `deskwidgets/__init__.py` and `CHANGELOG.md`; the GitHub
workflow tests on Linux and Windows, builds the `.deb` and attaches it to the release. Publish to PyPI with
`python -m build && twine upload dist/*`.
