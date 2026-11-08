//! `deskwidgets doctor`: what works, what is missing, and what to install.
use super::i18n::tr;
use super::util::which;
use super::{paths, settings};

pub fn run_checks() -> i32 {
    let mut ok = true;
    let mut row = |name: String, good: bool, note: String| {
        // a note in parentheses marks an optional feature: its absence is not a failure
        ok &= good || note.starts_with('(');
        let tail = if !note.is_empty() && !good { format!(" — {note}") } else { String::new() };
        println!("{} {}{}", if good { "✔" } else { "✘" }, name, tail);
    };

    if cfg!(windows) {
        row("Windows".into(), true, String::new());
    } else {
        let display = std::env::var("DISPLAY").map(|d| !d.is_empty()).unwrap_or(false);
        row(tr("дисплей X11 (або Xwayland)", "X11 display (or Xwayland)").into(), display, "DISPLAY".into());
        if display {
            let can_open = crate::platform::probe_display();
            row(tr("сервер X11 відповідає і дає 32-бітне вікно", "X server answers and offers a 32-bit window").into(), can_open, tr("потрібен композитний сеанс", "needs a compositing session").into());
        }
    }

    let s = settings::load();
    let family = if s.font == "auto" { crate::engine::fonts::default_family().to_string() } else { s.font.clone() };
    let fonts = crate::engine::fonts::FontMap::new(&s.font);
    row(format!("{}{}", tr("шрифт ", "font "), family), fonts.has_family(&family), tr("буде запасний sans-serif", "a fallback sans-serif will be used").into());
    row(tr("японський шрифт (аніме-віджети)", "Japanese font (anime widgets)").into(), !fonts.cjk().is_empty(), "(fonts-noto-cjk)".into());

    let optional = tr("необов’язково", "optional");
    let mut tools: Vec<(&str, String)> = vec![("nmap", tr("перевірка мережі", "LAN watch").into()), ("docker", "Docker".into())];
    if !cfg!(windows) {
        tools.push(("upower", tr("батареї", "batteries").into()));
    }
    for (tool, why) in tools {
        row(tool.into(), which(tool).is_some(), format!("({optional}: {why})"));
    }
    #[cfg(unix)]
    {
        let bus = crate::providers::events_eds::available();
        row(tr("календарі GNOME (віджет «Події»)", "GNOME calendars (\"Up next\" widget)").into(), bus, "(evolution-data-server)".into());
    }
    if !cfg!(windows) {
        for m in crate::providers::hid_mice::find() {
            row(format!("{}{}", tr("заряд миші ", "mouse battery "), m.name), m.access, format!("{}{}", tr("потрібне правило udev, виконайте: ", "needs the udev rule, run: "), crate::cli::udev_command()));
        }
    }
    row(tr("сервіс працює", "service running").into(), crate::service::ipc::is_running(), format!("({}: deskwidgets)", tr("запустіть", "start it")));
    println!("{}: {}  ·  {}: {}", tr("Віджети", "Widgets"), paths::builtin_widgets().display(), tr("мої", "mine"), paths::user_widgets().display());
    if ok { 0 } else { 1 }
}
