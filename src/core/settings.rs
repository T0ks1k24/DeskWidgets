//! Global settings (settings.json in the config folder) and the UI language.
use serde_json::{json, Map, Value};

use super::i18n::system_language;
use super::{paths, util};

#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub language: String,        // auto | uk | en
    pub scale: f64,              // size of every widget, 0.6-2.0
    pub gap: i32,                // px between widgets
    pub margin_top: i32,         // first grid row starts below the top bar
    pub margin_bottom: i32,      // keep free for a dock or the taskbar
    pub margin_side: i32,
    pub font: String,            // text font family; auto = the system's UI font
    pub default_style: String,   // style for new widgets, auto = the widget's own
    pub default_accent: String,
    pub autostart: bool,
    pub hotkey: bool,            // Super+Alt+W (Ctrl+Alt+W on Windows) opens the gallery
    pub show_grid: bool,         // draw the grid while a widget is being dragged
    pub lock: bool,              // widgets cannot be dragged
    /// keys this version does not know (`_setup_done` ...) are kept as they are
    pub extra: Map<String, Value>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            language: "auto".into(),
            scale: 1.0,
            gap: 16,
            margin_top: 44,
            margin_bottom: 88,
            margin_side: 24,
            font: "auto".into(),
            default_style: "auto".into(),
            default_accent: "auto".into(),
            autostart: true,
            hotkey: true,
            show_grid: true,
            lock: false,
            extra: Map::new(),
        }
    }
}

fn number(stored: &Map<String, Value>, key: &str) -> Option<f64> {
    let value = match stored.get(key)? {
        Value::Number(n) => n.as_f64()?,
        Value::String(s) => s.trim().parse::<f64>().ok()?,
        _ => return None,
    };
    value.is_finite().then_some(value)
}

fn text(stored: &Map<String, Value>, key: &str) -> Option<String> {
    stored.get(key).and_then(Value::as_str).map(str::to_string)
}

fn flag(stored: &Map<String, Value>, key: &str) -> Option<bool> {
    stored.get(key).and_then(Value::as_bool)
}

impl Settings {
    pub fn from_value(value: &Value) -> Settings {
        let mut s = Settings::default();
        let Some(stored) = value.as_object() else { return s };
        if let Some(v) = text(stored, "language") {
            s.language = v;
        }
        if let Some(v) = number(stored, "scale") {
            s.scale = v.clamp(0.6, 2.0);
        }
        for (key, field, hi) in [
            ("gap", &mut s.gap, 64.0),
            ("margin_top", &mut s.margin_top, 400.0),
            ("margin_bottom", &mut s.margin_bottom, 400.0),
            ("margin_side", &mut s.margin_side, 400.0),
        ] {
            if let Some(v) = number(stored, key) {
                *field = v.trunc().clamp(0.0, hi) as i32;
            }
        }
        if let Some(v) = text(stored, "font") {
            s.font = v;
        }
        if let Some(v) = text(stored, "default_style") {
            s.default_style = v;
        }
        if let Some(v) = text(stored, "default_accent") {
            s.default_accent = v;
        }
        for (key, field) in [
            ("autostart", &mut s.autostart),
            ("hotkey", &mut s.hotkey),
            ("show_grid", &mut s.show_grid),
            ("lock", &mut s.lock),
        ] {
            if let Some(v) = flag(stored, key) {
                *field = v;
            }
        }
        for (key, value) in stored {
            if key.starts_with('_') {
                s.extra.insert(key.clone(), value.clone());
            }
        }
        s
    }

    pub fn to_value(&self) -> Value {
        let mut map = Map::new();
        map.insert("language".into(), json!(self.language));
        map.insert("scale".into(), json!(self.scale));
        map.insert("gap".into(), json!(self.gap));
        map.insert("margin_top".into(), json!(self.margin_top));
        map.insert("margin_bottom".into(), json!(self.margin_bottom));
        map.insert("margin_side".into(), json!(self.margin_side));
        map.insert("font".into(), json!(self.font));
        map.insert("default_style".into(), json!(self.default_style));
        map.insert("default_accent".into(), json!(self.default_accent));
        map.insert("autostart".into(), json!(self.autostart));
        map.insert("hotkey".into(), json!(self.hotkey));
        map.insert("show_grid".into(), json!(self.show_grid));
        map.insert("lock".into(), json!(self.lock));
        for (k, v) in &self.extra {
            map.insert(k.clone(), v.clone());
        }
        Value::Object(map)
    }
}

pub fn load() -> Settings {
    match std::fs::read_to_string(paths::settings()) {
        Ok(text) => serde_json::from_str::<Value>(&text).map(|v| Settings::from_value(&v)).unwrap_or_default(),
        Err(_) => Settings::default(),
    }
}

pub fn save(settings: &Settings) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(&settings.to_value()).unwrap_or_default() + "\n";
    util::write_atomic(&paths::settings(), &text)
}

/// "uk" or "en": the chosen language, or the system's.
pub fn language(settings: &Settings) -> String {
    match settings.language.as_str() {
        "uk" => "uk".into(),
        "en" => "en".into(),
        _ => system_language().into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bad_values_fall_back_and_limits_apply() {
        let s = Settings::from_value(&json!({"scale": 9, "gap": "x", "margin_top": -5, "lock": "yes", "_setup_done": true}));
        assert_eq!(s.scale, 2.0);
        assert_eq!(s.gap, 16);
        assert_eq!(s.margin_top, 0);
        assert!(!s.lock);
        assert_eq!(s.extra.get("_setup_done"), Some(&json!(true)));
        let again = Settings::from_value(&s.to_value());
        assert_eq!(again, s);
    }
}
