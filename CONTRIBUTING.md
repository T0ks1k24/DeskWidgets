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
7. If it needs something Linux-only (or Windows-only), say so in `widget.json` (`"platforms": ["linux"]`), and
   prefer `L.sys`/`L.service`/`L.run` over shell commands so it works on both.

## Code

* Rust (stable, edition 2021). Platform-specific code stays in `src/platform/` and `src/providers/` behind
  `cfg(unix)` / `cfg(windows)`; everything else must build and behave the same on both. No `unsafe` outside the few
  system calls that need it (`libc`, `windows`).
* Lua: 5.4 (embedded by mlua); keep widgets self-contained, put shared helpers in `lua/lib.lua` (and document them in
  docs/creating-widgets.md). Network access from a widget goes through `L.http` / `L.download`, never a blocking call.
* Keep the UI bilingual (`tr("український", "English")`).
* `make deb` must still build.

```sh
cargo test                  # core, engine (every widget renders), service, providers
xvfb-run -a cargo test      # on a headless Linux box with Xvfb: also the X11 backend tests (they skip without Xvfb)
cargo check --target x86_64-pc-windows-gnu --all-targets   # the Windows backend compiles
make run                    # run from the checkout; widgets are read from widgets/
```

Every widget is rendered in every size in the tests, so a widget that errors fails the build. The X11 backend is tested
against a virtual X display (Xvfb, driven with XTest). The Win32 backend can only be compiled in CI-less setups like
this one: please test on a real Windows desktop before changing `src/platform/win32.rs`.

## Releases

Tag `vX.Y.Z` after bumping `version` in `Cargo.toml` and `CHANGELOG.md`; the GitHub
workflow tests on Linux and Windows, builds the `.deb` and the Windows zip and attaches them to the release.
