//! The gallery: add, configure and arrange widgets in a window (egui, the same on Linux and Windows).
//!
//! * My widgets: what is on the desktop. Open settings or remove.
//! * Add: every widget type (built-in and your own) by category. Add as many copies as you like.
//! * Settings: language, widget size, grid spacing, font, defaults, autostart, hotkey, grid helper,
//!   your own widgets folder and "create a widget".
//! Each widget's settings page is built from its widget.json, so third-party widgets get one too.
//! Right-clicking a widget runs `deskwidgets configure <id>`, which opens just its settings.
#[cfg(feature = "gallery")]
mod app;
#[cfg(feature = "gallery")]
mod ui;
#[cfg(feature = "gallery")]
mod worker;

/// Opens the gallery (or one widget's settings). Returns the exit code.
#[cfg(feature = "gallery")]
pub fn run(configure: Option<&str>, page: Option<&str>) -> i32 {
    app::run(configure, page)
}

#[cfg(not(feature = "gallery"))]
pub fn run(_configure: Option<&str>, _page: Option<&str>) -> i32 {
    eprintln!("this build of DeskWidgets has no gallery window (it was built without the `gallery` feature)");
    1
}
