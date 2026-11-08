//! Two-language UI: every string is written as tr("Ukrainian", "English").
use std::sync::atomic::{AtomicU8, Ordering};

use serde_json::Value;

const UNSET: u8 = 0;
const UK: u8 = 1;
const EN: u8 = 2;
static LANG: AtomicU8 = AtomicU8::new(UNSET);

/// "uk" or "en", from the environment (Linux) or the user's locale (Windows).
pub fn system_language() -> &'static str {
    for var in ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(value) = std::env::var(var) {
            if !value.is_empty() {
                let first = value.split(':').next().unwrap_or("");
                return if first.starts_with("uk") { "uk" } else { "en" };
            }
        }
    }
    let locale = platform_locale().to_lowercase();
    if locale.starts_with("uk") || locale.starts_with("ukrainian") {
        "uk"
    } else {
        "en"
    }
}

#[cfg(windows)]
fn platform_locale() -> String {
    use windows::Win32::Globalization::GetUserDefaultLocaleName;
    let mut buffer = [0u16; 85];
    let n = unsafe { GetUserDefaultLocaleName(&mut buffer) };
    if n > 1 {
        String::from_utf16_lossy(&buffer[..(n as usize - 1)])
    } else {
        String::new()
    }
}

#[cfg(not(windows))]
fn platform_locale() -> String {
    String::new()
}

pub fn lang() -> &'static str {
    match LANG.load(Ordering::Relaxed) {
        UK => "uk",
        EN => "en",
        _ => {
            let value = super::settings::language(&super::settings::load());
            set_lang(&value);
            if value == "en" {
                "en"
            } else {
                "uk"
            }
        }
    }
}

pub fn set_lang(value: &str) {
    LANG.store(if value == "en" { EN } else { UK }, Ordering::Relaxed);
}

/// Picks the Ukrainian or the English string for the current language.
pub fn tr(uk: &'static str, en: &'static str) -> &'static str {
    if lang() == "en" {
        en
    } else {
        uk
    }
}

/// `tr` for strings built at run time.
pub fn tr_owned(uk: String, en: String) -> String {
    if lang() == "en" {
        en
    } else {
        uk
    }
}

/// A manifest value: a plain string, or {"uk": ..., "en": ...}.
pub fn loc(value: &Value, language: Option<&str>) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            let want: &str = match language {
                Some(l) => l,
                None => lang(),
            };
            for key in [want, "en", "uk"] {
                if let Some(Value::String(s)) = map.get(key) {
                    if !s.is_empty() {
                        return s.clone();
                    }
                }
            }
            map.values().find_map(|v| v.as_str().map(str::to_string)).unwrap_or_default()
        }
        Value::Null => String::new(),
        other => other.to_string(),
    }
}
