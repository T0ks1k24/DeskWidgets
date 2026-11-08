//! DeskWidgets: macOS-style desktop widgets for Linux and Windows, drawn from Lua.
//!
//! * `core`      settings, widget catalog, grid and layout: plain logic, no windows
//! * `engine`    the widget runtime: Lua + a Cairo-style canvas on tiny-skia, fonts, images, system info
//! * `platform`  one backend per OS behind a small interface: X11 and Win32; autostart and hotkeys
//! * `providers` background data: media player, calendars, Docker, batteries
//! * `service`   the daemon: widget windows, dragging, the control socket
//! * `gallery`   the gallery window (egui)

pub mod cli;
pub mod core;
pub mod engine;
pub mod gallery;
pub mod netwatch;
pub mod platform;
pub mod providers;
pub mod service;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
