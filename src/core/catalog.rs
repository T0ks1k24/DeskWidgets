//! Widget types are folders with a widget.json manifest and a widget.lua drawing file.
//!
//! Built-in widgets are unpacked from the executable, your own live in ~/.config/deskwidgets/widgets
//! (a folder there with the same id overrides the built-in one). See docs/creating-widgets.md.
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use regex::Regex;
use serde_json::{Map, Value};

use super::i18n::{loc, lang, tr};
use super::layout::Instance;
use super::{paths, util::log};

pub const OS: &str = if cfg!(windows) { "windows" } else { "linux" };
pub const SIZE_KEYS: [&str; 3] = ["small", "medium", "large"];
pub const CATEGORIES: [&str; 6] = ["time", "info", "system", "anime", "personal", "other"];
pub const STYLE_KEYS: [&str; 9] =
    ["glass", "graphite", "light", "color", "sakura", "manga", "neon", "clear_dark", "clear_light"];
pub const OPTION_TYPES: [&str; 8] = ["bool", "text", "longtext", "date", "choice", "multi", "file", "number"];

pub const ACCENTS: [(&str, Option<&str>); 13] = [
    ("auto", None),
    ("red", Some("#FF453A")),
    ("orange", Some("#FF9F0A")),
    ("yellow", Some("#FFD60A")),
    ("green", Some("#30D158")),
    ("mint", Some("#63E6E2")),
    ("teal", Some("#40C8E0")),
    ("blue", Some("#0A84FF")),
    ("indigo", Some("#5E5CE6")),
    ("purple", Some("#BF5AF2")),
    ("pink", Some("#FF375F")),
    ("sakura", Some("#FF7EB6")),
    ("graphite", Some("#8E8E93")),
];

/// Grid cells of a size name: a small card is 1x1, medium 2x1, large 2x2.
pub fn cells_of(size: &str) -> (i32, i32) {
    match size {
        "medium" => (2, 1),
        "large" => (2, 2),
        _ => (1, 1),
    }
}

pub fn is_size(size: &str) -> bool {
    SIZE_KEYS.contains(&size)
}
pub fn is_style(style: &str) -> bool {
    STYLE_KEYS.contains(&style)
}
pub fn is_accent(accent: &str) -> bool {
    ACCENTS.iter().any(|(k, _)| *k == accent)
}

pub fn size_name(size: &str) -> String {
    match size {
        "small" => tr("Маленький", "Small").into(),
        "medium" => tr("Середній", "Medium").into(),
        "large" => tr("Великий", "Large").into(),
        other => other.into(),
    }
}

pub fn category_name(cat: &str) -> String {
    match cat {
        "time" => tr("Час і дата", "Time & date").into(),
        "info" => tr("Інформація", "Information").into(),
        "system" => tr("Система", "System").into(),
        "anime" => tr("Аніме", "Anime").into(),
        "personal" => tr("Особисте", "Personal").into(),
        "other" => tr("Інше", "Other").into(),
        other => other.into(),
    }
}

pub fn styles() -> Vec<(&'static str, &'static str)> {
    vec![
        ("glass", tr("Скло", "Glass")),
        ("graphite", tr("Графіт", "Graphite")),
        ("light", tr("Світлий", "Light")),
        ("color", tr("Кольоровий", "Colour")),
        ("sakura", tr("Сакура (аніме)", "Sakura (anime)")),
        ("manga", tr("Манґа (аніме)", "Manga (anime)")),
        ("neon", tr("Неон (аніме)", "Neon (anime)")),
        ("clear_dark", tr("Без фону · темний текст", "No card · dark text")),
        ("clear_light", tr("Без фону · світлий текст", "No card · light text")),
    ]
}

pub fn accent_name(key: &str) -> String {
    match key {
        "auto" => tr("Як задумано", "Default"),
        "red" => tr("Червоний", "Red"),
        "orange" => tr("Помаранчевий", "Orange"),
        "yellow" => tr("Жовтий", "Yellow"),
        "green" => tr("Зелений", "Green"),
        "mint" => tr("М’ятний", "Mint"),
        "teal" => tr("Бірюзовий", "Teal"),
        "blue" => tr("Синій", "Blue"),
        "indigo" => tr("Індиго", "Indigo"),
        "purple" => tr("Фіолетовий", "Purple"),
        "pink" => tr("Рожевий", "Pink"),
        "sakura" => tr("Сакура", "Sakura"),
        "graphite" => tr("Графітовий", "Graphite"),
        other => return other.into(),
    }
    .into()
}

#[derive(Debug, Clone, PartialEq)]
pub struct OptionDef {
    pub key: String,
    pub kind: String,
    pub default: Value,
    pub label: Value,
    pub hint: Value,
    pub choices: Vec<(String, Value)>, // (value, label)
    pub limit: i64,
    pub min: f64,
    pub max: f64,
    pub step: f64,
}

impl OptionDef {
    pub fn title(&self) -> String {
        let t = loc(&self.label, None);
        if t.is_empty() { self.key.clone() } else { t }
    }

    pub fn hint_text(&self) -> String {
        loc(&self.hint, None)
    }

    pub fn default_value(&self, language: Option<&str>) -> Value {
        match &self.default {
            Value::Object(_) => Value::String(loc(&self.default, language)),
            other => other.clone(),
        }
    }

    pub fn choice_label(&self, value: &str) -> String {
        self.choices
            .iter()
            .find(|(v, _)| v == value)
            .map(|(v, label)| {
                let l = loc(label, None);
                if l.is_empty() { v.clone() } else { l }
            })
            .unwrap_or_else(|| value.to_string())
    }

    /// `value` (from the gallery, the CLI or a hand-edited layout.json) made valid for this
    /// option's type. Errors when it cannot be.
    pub fn coerce(&self, value: &Value) -> Result<Value, String> {
        let key = &self.key;
        match self.kind.as_str() {
            "bool" => match value {
                Value::Bool(_) => Ok(value.clone()),
                _ => Err(format!("{key}: expected true or false")),
            },
            "number" => {
                let number = match value {
                    Value::Number(n) => n.as_f64(),
                    Value::String(s) => s.trim().parse::<f64>().ok(),
                    _ => None,
                }
                .filter(|n| !n.is_nan())
                .ok_or_else(|| format!("{key}: expected a number"))?;
                let number = number.clamp(self.min, self.max);
                if self.step.fract() == 0.0 && number.fract() == 0.0 {
                    Ok(Value::from(number as i64))
                } else {
                    Ok(Value::from(number))
                }
            }
            kind => {
                let is_text = matches!(value, Value::String(_));
                let is_list = matches!(value, Value::Array(_));
                if !is_text && !is_list {
                    return Err(format!("{key}: expected text"));
                }
                match kind {
                    "choice" => match value.as_str() {
                        Some(s) if self.choices.iter().any(|(v, _)| v == s) => Ok(value.clone()),
                        _ => Err(format!("{key}: {value} is not one of the choices")),
                    },
                    "multi" => {
                        let chosen: Vec<String> = match value {
                            Value::String(s) => s.split(',').map(str::to_string).collect(),
                            Value::Array(items) => items.iter().map(value_text).collect(),
                            _ => Vec::new(),
                        };
                        let picked: Vec<&str> =
                            self.choices.iter().map(|(v, _)| v.as_str()).filter(|v| chosen.iter().any(|c| c == v)).collect();
                        Ok(Value::String(picked.join(",")))
                    }
                    _ => {
                        if is_list {
                            return Err(format!("{key}: expected text"));
                        }
                        let s = value.as_str().unwrap_or_default();
                        if kind == "date" && !s.is_empty() && !valid_date(s) {
                            return Err(format!("{key}: Invalid isoformat string: '{s}'"));
                        }
                        Ok(value.clone())
                    }
                }
            }
        }
    }
}

fn value_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn valid_date(text: &str) -> bool {
    static SHAPE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let shape = SHAPE.get_or_init(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap());
    shape.is_match(text) && chrono::NaiveDate::parse_from_str(text, "%Y-%m-%d").is_ok()
}

#[derive(Debug, Clone, PartialEq)]
pub struct WidgetType {
    pub id: String,
    pub dir: PathBuf,
    pub name: Value,
    pub description: Value,
    pub category: String,
    pub sizes: Vec<String>,
    pub interval: f64,
    pub fast_if: String,
    pub style: String,
    pub accent: String,
    pub color_accent: String,
    pub options: Vec<OptionDef>,
    pub uses: Vec<String>,      // services the widget needs: "media", "events", "docker", "batteries"
    pub platforms: Vec<String>, // operating systems it works on ("linux", "windows"); empty = all
    pub author: String,
    pub version: String,
    pub user: bool, // from the user's widgets folder
}

impl WidgetType {
    /// False when the widget cannot work on this operating system.
    pub fn supported(&self) -> bool {
        self.platforms.is_empty() || self.platforms.iter().any(|p| p == OS)
    }

    pub fn title(&self) -> String {
        let t = loc(&self.name, None);
        if t.is_empty() { self.id.clone() } else { t }
    }

    pub fn title_in(&self, language: &str) -> String {
        let t = loc(&self.name, Some(language));
        if t.is_empty() { self.id.clone() } else { t }
    }

    pub fn desc(&self) -> String {
        loc(&self.description, None)
    }

    /// The default size: the first one listed.
    pub fn size(&self) -> &str {
        self.sizes.first().map(String::as_str).unwrap_or("small")
    }

    pub fn option(&self, key: &str) -> Option<&OptionDef> {
        self.options.iter().find(|o| o.key == key)
    }

    pub fn interval_for(&self, values: &Map<String, Value>) -> f64 {
        if !self.fast_if.is_empty() {
            if let Some(opt) = self.option(&self.fast_if) {
                let value = values.get(&opt.key).cloned().unwrap_or_else(|| opt.default_value(None));
                let on = match &value {
                    Value::Bool(b) => *b,
                    Value::String(s) => s == "true" || s == "1",
                    Value::Number(n) => n.as_f64() == Some(1.0),
                    _ => false,
                };
                if on {
                    return 1.0;
                }
            }
        }
        self.interval
    }
}

pub type Types = Arc<BTreeMap<String, WidgetType>>;

fn load_one(folder: &std::path::Path, user: bool) -> Result<Option<WidgetType>, String> {
    let manifest = folder.join("widget.json");
    let script = folder.join("widget.lua");
    if !(manifest.is_file() && script.is_file()) {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&manifest).map_err(|e| e.to_string())?;
    let m: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let folder_name = folder.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let id = m.get("id").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string).unwrap_or(folder_name);
    let str_of = |key: &str, default: &str| m.get(key).and_then(Value::as_str).unwrap_or(default).to_string();
    let list_of = |key: &str| -> Vec<String> {
        m.get(key).and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect()).unwrap_or_default()
    };
    let mut sizes: Vec<String> = list_of("sizes").into_iter().filter(|s| is_size(s)).collect();
    if sizes.is_empty() {
        sizes.push("small".into());
    }
    let mut options = Vec::new();
    for o in m.get("options").and_then(Value::as_array).cloned().unwrap_or_default() {
        let key = o.get("key").and_then(Value::as_str).unwrap_or("");
        let kind = o.get("type").and_then(Value::as_str).unwrap_or("");
        if key.is_empty() || !OPTION_TYPES.contains(&kind) {
            log(format!("{id}: skipped an option without key/type: {o}"));
            continue;
        }
        let choices = o
            .get("choices")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|c| match c {
                        Value::Object(map) => {
                            let value = map.get("value").map(value_text).unwrap_or_default();
                            let label = map.get("label").cloned().unwrap_or_else(|| Value::String(value.clone()));
                            (value, label)
                        }
                        other => (value_text(other), other.clone()),
                    })
                    .collect()
            })
            .unwrap_or_default();
        options.push(OptionDef {
            key: key.to_string(),
            kind: kind.to_string(),
            default: o.get("default").cloned().unwrap_or_else(|| Value::String(String::new())),
            label: o.get("label").cloned().unwrap_or_else(|| Value::String(key.to_string())),
            hint: o.get("hint").cloned().unwrap_or_else(|| Value::String(String::new())),
            choices,
            limit: o.get("limit").and_then(Value::as_i64).unwrap_or(0),
            min: o.get("min").and_then(Value::as_f64).unwrap_or(0.0),
            max: o.get("max").and_then(Value::as_f64).unwrap_or(100.0),
            step: o.get("step").and_then(Value::as_f64).unwrap_or(1.0),
        });
    }
    let category = str_of("category", "other");
    let style = str_of("style", "glass");
    let accent = str_of("accent", "auto");
    Ok(Some(WidgetType {
        id,
        dir: folder.to_path_buf(),
        name: m.get("name").cloned().unwrap_or(Value::String(String::new())),
        description: m.get("description").cloned().unwrap_or(Value::String(String::new())),
        category: if CATEGORIES.contains(&category.as_str()) { category } else { "other".into() },
        sizes,
        interval: m.get("interval").and_then(Value::as_f64).unwrap_or(5.0),
        fast_if: str_of("fast_if", ""),
        style: if is_style(&style) { style } else { "glass".into() },
        accent: if is_accent(&accent) { accent } else { "auto".into() },
        color_accent: str_of("color_accent", "indigo"),
        options,
        uses: list_of("uses"),
        platforms: list_of("platforms"),
        author: str_of("author", ""),
        version: m.get("version").map(value_text).unwrap_or_default(),
        user,
    }))
}

/// {type id: WidgetType}. User widgets override built-ins with the same id.
pub fn discover() -> Types {
    let mut types = BTreeMap::new();
    for (base, user) in [(paths::builtin_widgets(), false), (paths::user_widgets(), true)] {
        let Ok(entries) = std::fs::read_dir(&base) else { continue };
        let mut folders: Vec<PathBuf> = entries.filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.is_dir()).collect();
        folders.sort();
        for folder in folders {
            match load_one(&folder, user) {
                Ok(Some(t)) => {
                    types.insert(t.id.clone(), t);
                }
                Ok(None) => {}
                Err(e) => log(format!("widget {}: {e}", folder.display())),
            }
        }
    }
    Arc::new(types)
}

/// Options of an instance with the (localised) type defaults filled in.
pub fn option_values(inst: &Instance, t: &WidgetType, language: Option<&str>) -> Map<String, Value> {
    let mut values = inst.options.clone();
    for o in &t.options {
        if !values.contains_key(&o.key) {
            values.insert(o.key.clone(), o.default_value(language));
        }
    }
    values
}

/// The "colour" style needs a real colour when the accent is left on auto.
pub fn resolved_accent(inst: &Instance, t: &WidgetType) -> String {
    let accent = if inst.accent.is_empty() { "auto" } else { &inst.accent };
    if accent == "auto" && inst.style == "color" {
        t.color_accent.clone()
    } else {
        accent.to_string()
    }
}

/// Gallery subtitle, e.g. "Medium · Glass · Purple".
pub fn describe(inst: &Instance) -> String {
    let style_name = styles().into_iter().find(|(k, _)| *k == inst.style).map(|(_, n)| n).unwrap_or("");
    let mut parts = vec![size_name(&inst.size), style_name.to_string()];
    if !inst.accent.is_empty() && inst.accent != "auto" {
        parts.push(accent_name(&inst.accent));
    }
    parts.retain(|p| !p.is_empty());
    parts.join(" · ")
}

/// Used by the gallery to show a title for the current language.
pub fn title_for(t: &WidgetType) -> String {
    t.title_in(lang())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn opt(kind: &str) -> OptionDef {
        OptionDef {
            key: "k".into(),
            kind: kind.into(),
            default: json!(""),
            label: json!("k"),
            hint: json!(""),
            choices: vec![("a".into(), json!("A")), ("b".into(), json!("B")), ("c".into(), json!("C"))],
            limit: 0,
            min: 1.0,
            max: 10.0,
            step: 1.0,
        }
    }

    #[test]
    fn coerce_follows_the_option_type() {
        assert_eq!(opt("bool").coerce(&json!(true)), Ok(json!(true)));
        assert!(opt("bool").coerce(&json!("true")).is_err());
        assert_eq!(opt("number").coerce(&json!("50")), Ok(json!(10)));
        assert_eq!(opt("number").coerce(&json!(0)), Ok(json!(1)));
        assert!(opt("number").coerce(&json!(true)).is_err());
        assert!(opt("choice").coerce(&json!("z")).is_err());
        assert_eq!(opt("choice").coerce(&json!("b")), Ok(json!("b")));
        assert_eq!(opt("multi").coerce(&json!("c,a,zzz")), Ok(json!("a,c")));
        assert_eq!(opt("multi").coerce(&json!(["b", "a"])), Ok(json!("a,b")));
        assert!(opt("date").coerce(&json!("2024-13-40")).is_err());
        assert!(opt("date").coerce(&json!("2024-1-5")).is_err());
        assert_eq!(opt("date").coerce(&json!("2024-02-29")), Ok(json!("2024-02-29")));
        assert_eq!(opt("date").coerce(&json!("")), Ok(json!("")));
        assert!(opt("text").coerce(&json!(["x"])).is_err());
    }

    #[test]
    fn cells() {
        assert_eq!(cells_of("large"), (2, 2));
        assert_eq!(cells_of("nonsense"), (1, 1));
    }
}
