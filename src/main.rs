// A console window would flash up on Windows when the service starts at login; the console commands
// attach to the parent console explicitly (see `attach_console`).
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

fn main() {
    #[cfg(windows)]
    attach_console();
    std::process::exit(deskwidgets::cli::main_with(std::env::args().collect()));
}

/// With the "windows" subsystem a process has no console of its own; commands started from a terminal
/// should still print there.
#[cfg(windows)]
fn attach_console() {
    use windows::Win32::System::Console::{AttachConsole, ATTACH_PARENT_PROCESS};
    unsafe {
        let _ = AttachConsole(ATTACH_PARENT_PROCESS);
    }
}
