//! What is on the desktop: the widget instances and where they sit on the grid.
//!
//! `Layout` is plain data plus rules (no windows, no processes), so everything here is easy to test.
//! layout.json: {"version": 3, "instances": [{id, type, size, style, accent, options, col, row}]}
use std::collections::HashSet;

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use super::catalog::{is_accent, is_style, Types};
use super::grid::{Cell, Grid};
use super::settings::Settings;
use super::util::{log, write_atomic};
use super::paths;

pub const VERSION: i64 = 3;

/// First-run layout: (type, size, col, row); negative columns count from the right edge.
const DEFAULT_LAYOUT: [(&str, &str, i32, i32); 7] = [
    ("clock", "small", 0, 0),
    ("calendar", "small", 1, 0),
    ("weather", "medium", 0, 1),
    ("system", "medium", 0, 2),
    ("events", "medium", -2, 0),
    ("media", "medium", -2, 1),
    ("anime_quote", "medium", -2, 2),
];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Instance {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub size: String,
    pub style: String,
    pub accent: String,
    #[serde(default)]
    pub options: Map<String, Value>,
    pub col: i32,
    pub row: i32,
}

impl Instance {
    pub fn cells(&self) -> HashSet<Cell> {
        Grid::cells(self.col, self.row, &self.size)
    }
}

pub struct Layout {
    pub grid: Grid,
    pub types: Types,
    pub settings: Settings,
    pub instances: Vec<Instance>,
}

fn id_pattern() -> &'static Regex {
    static ID: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    ID.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$").unwrap())
}

impl Layout {
    pub fn new(grid: Grid, types: Types, settings: Settings) -> Layout {
        Layout { grid, types, settings, instances: Vec::new() }
    }

    // loading and saving
    pub fn load(&mut self) {
        let mut data = read_json(&paths::layout());
        if data.is_none() && paths::old_layout().exists() {
            data = self.import_v1(read_json(&paths::old_layout()));
        }
        let raw: Vec<Value> = match data.as_ref().and_then(|d| d.get("instances")).and_then(Value::as_array) {
            Some(list) => list.clone(),
            None => self.defaults(),
        };
        self.instances.clear();
        let mut taken: HashSet<Cell> = HashSet::new();
        for item in &raw {
            let Some(mut inst) = self.normalise(item) else { continue };
            let col = item.get("col").and_then(Value::as_i64).filter(|c| i32::try_from(*c).is_ok());
            let row = item.get("row").and_then(Value::as_i64).filter(|r| i32::try_from(*r).is_ok());
            let ok = match (col, row) {
                (Some(c), Some(r)) => {
                    self.grid.fits(c as i32, r as i32, &inst.size)
                        && Grid::cells(c as i32, r as i32, &inst.size).is_disjoint(&taken)
                }
                _ => false,
            };
            if ok {
                inst.col = col.unwrap() as i32;
                inst.row = row.unwrap() as i32;
            } else {
                let spot = self.grid.free_near(col.unwrap_or(0) as i32, row.unwrap_or(0) as i32, &inst.size, &taken);
                match spot {
                    Some((c, r)) => {
                        inst.col = c;
                        inst.row = r;
                    }
                    None => {
                        log(format!("{}: no room left on the grid, skipped", inst.id));
                        continue;
                    }
                }
            }
            taken.extend(inst.cells());
            self.instances.push(inst);
        }
    }

    pub fn save(&self) {
        let text = serde_json::to_string_pretty(&json!({"version": VERSION, "instances": self.instances})).unwrap_or_default() + "\n";
        if let Err(e) = write_atomic(&paths::layout(), &text) {
            log(format!("cannot save the layout: {e}"));
        }
    }

    /// Imports DeskWidgets 1.x widgets (~/.config/conky/mac), keeping their Ukrainian UI.
    fn import_v1(&self, data: Option<Value>) -> Option<Value> {
        log("importing widgets from ~/.config/conky/mac");
        let known = paths::old_known_devices();
        let new_known = paths::config().join("network_known.txt");
        if known.exists() && !new_known.exists() {
            let _ = std::fs::create_dir_all(paths::config());
            let _ = std::fs::copy(&known, &new_known);
        }
        let mut data = data?;
        if let Some(list) = data.get_mut("instances").and_then(Value::as_array_mut) {
            for inst in list.iter_mut().filter(|i| i.is_object()) {
                let kind = inst.get("type").and_then(Value::as_str).unwrap_or("").to_string();
                let obj = inst.as_object_mut().unwrap();
                let options = obj.entry("options").or_insert_with(|| json!({}));
                if let Some(options) = options.as_object_mut() {
                    if kind == "network" {
                        options.entry("lan").or_insert(json!(true)); // 1.x always watched the LAN
                    }
                    if kind == "weather" {
                        options.entry("city").or_insert(json!("Lutsk")); // 1.x had Lutsk built in
                        options.entry("title").or_insert(json!("Луцьк"));
                    }
                }
            }
        }
        Some(data)
    }

    fn defaults(&self) -> Vec<Value> {
        DEFAULT_LAYOUT
            .iter()
            .filter(|(kind, ..)| self.types.contains_key(*kind))
            .map(|(kind, size, col, row)| {
                json!({"type": kind, "size": size, "row": row, "col": if *col >= 0 { *col } else { self.grid.cols + col }})
            })
            .collect()
    }

    fn normalise(&self, item: &Value) -> Option<Instance> {
        let kind = item.get("type").and_then(Value::as_str).unwrap_or("");
        let Some(t) = self.types.get(kind) else {
            if item.is_object() {
                log(format!("{}: unknown widget type {:?}, skipped", item.get("id").map(|v| v.to_string()).unwrap_or("None".into()), item.get("type")));
            }
            return None;
        };
        let wanted = item.get("id").and_then(Value::as_str);
        let id = match wanted {
            Some(w) if id_pattern().is_match(w) && !self.instances.iter().any(|i| i.id == w) => w.to_string(),
            _ => self.new_id(&t.id),
        };
        let mut options = Map::new();
        if let Some(stored) = item.get("options").and_then(Value::as_object) {
            for (key, value) in stored {
                if let Some(option) = t.option(key) {
                    match option.coerce(value) {
                        Ok(v) => {
                            options.insert(key.clone(), v);
                        }
                        Err(e) => log(format!("{id}: {e}, using the default")),
                    }
                }
            }
        }
        let size = item.get("size").and_then(Value::as_str).filter(|s| t.sizes.iter().any(|x| x == s)).unwrap_or(t.size());
        let style = item.get("style").and_then(Value::as_str).filter(|s| is_style(s)).map(str::to_string).unwrap_or_else(|| self.default_style(&t.style));
        let accent = item.get("accent").and_then(Value::as_str).filter(|s| is_accent(s)).map(str::to_string).unwrap_or_else(|| self.default_accent(&t.accent));
        Some(Instance { id, kind: t.id.clone(), size: size.to_string(), style, accent, options, col: 0, row: 0 })
    }

    pub fn default_style(&self, type_style: &str) -> String {
        let style = self.settings.default_style.as_str();
        if is_style(style) { style.into() } else { type_style.into() }
    }

    pub fn default_accent(&self, type_accent: &str) -> String {
        let accent = self.settings.default_accent.as_str();
        if is_accent(accent) && accent != "auto" { accent.into() } else { type_accent.into() }
    }

    // queries
    pub fn ids(&self) -> HashSet<&str> {
        self.instances.iter().map(|i| i.id.as_str()).collect()
    }

    pub fn get(&self, id: &str) -> Option<&Instance> {
        self.instances.iter().find(|i| i.id == id)
    }

    pub fn get_mut(&mut self, id: &str) -> Option<&mut Instance> {
        self.instances.iter_mut().find(|i| i.id == id)
    }

    pub fn taken(&self, exclude: Option<&str>) -> HashSet<Cell> {
        let mut cells = HashSet::new();
        for i in &self.instances {
            if Some(i.id.as_str()) != exclude {
                cells.extend(i.cells());
            }
        }
        cells
    }

    pub fn new_id(&self, type_id: &str) -> String {
        let used = self.ids();
        if !used.contains(type_id) {
            return type_id.to_string();
        }
        let mut n = 2;
        while used.contains(format!("{type_id}-{n}").as_str()) {
            n += 1;
        }
        format!("{type_id}-{n}")
    }

    // changes; each returns what happened so the caller can react
    /// Puts a new widget on the first free spot. Returns the instance, or None when it does not fit.
    pub fn add(&mut self, type_id: &str) -> Option<Instance> {
        let t = match self.types.get(type_id) {
            Some(t) => t.clone(),
            None => {
                log(format!("{type_id}: no such widget"));
                return None;
            }
        };
        let taken = self.taken(None);
        let mut size = t.size().to_string();
        let mut spot = self.grid.first_free(&size, &taken);
        if spot.is_none() {
            for alt in ["small", "medium"] {
                if t.sizes.iter().any(|s| s == alt) {
                    if let Some(found) = self.grid.first_free(alt, &taken) {
                        spot = Some(found);
                        size = alt.to_string();
                        break;
                    }
                }
            }
        }
        let Some((col, row)) = spot else {
            log(format!("{type_id}: the grid is full"));
            return None;
        };
        let inst = Instance {
            id: self.new_id(type_id),
            kind: type_id.to_string(),
            size,
            style: self.default_style(&t.style),
            accent: self.default_accent(&t.accent),
            options: Map::new(),
            col,
            row,
        };
        self.instances.push(inst.clone());
        Some(inst)
    }

    pub fn remove(&mut self, id: &str) -> Option<Instance> {
        let pos = self.instances.iter().position(|i| i.id == id)?;
        Some(self.instances.remove(pos))
    }

    /// Applies size/style/accent/options from the gallery or the CLI. Invalid values are ignored.
    /// Returns the instance (None if there is no such one).
    pub fn configure(&mut self, id: &str, patch: &Value) -> Option<Instance> {
        let pos = self.instances.iter().position(|i| i.id == id)?;
        let patch = patch.as_object()?;
        let t = self.types.get(&self.instances[pos].kind)?.clone();
        let old_size = self.instances[pos].size.clone();
        {
            let inst = &mut self.instances[pos];
            if let Some(size) = patch.get("size").and_then(Value::as_str).filter(|s| t.sizes.iter().any(|x| x == s)) {
                inst.size = size.to_string();
            }
            if let Some(style) = patch.get("style").and_then(Value::as_str).filter(|s| is_style(s)) {
                inst.style = style.to_string();
            }
            if let Some(accent) = patch.get("accent").and_then(Value::as_str).filter(|s| is_accent(s)) {
                inst.accent = accent.to_string();
            }
            if let Some(options) = patch.get("options").and_then(Value::as_object) {
                for (key, value) in options {
                    let Some(option) = t.option(key) else { continue };
                    match option.coerce(value) {
                        Ok(v) => {
                            inst.options.insert(key.clone(), v);
                        }
                        Err(e) => log(format!("{id}: {e}")),
                    }
                }
            }
        }
        if self.instances[pos].size != old_size {
            let taken = self.taken(Some(id));
            let (col, row, size) = {
                let i = &self.instances[pos];
                (i.col, i.row, i.size.clone())
            };
            let here = self.grid.fits(col, row, &size) && Grid::cells(col, row, &size).is_disjoint(&taken);
            let spot = if here { Some((col, row)) } else { self.grid.free_near(col, row, &size, &taken) };
            match spot {
                None => {
                    log(format!("{id}: no room for {size}, size unchanged"));
                    self.instances[pos].size = old_size;
                }
                Some((c, r)) => {
                    self.instances[pos].col = c;
                    self.instances[pos].row = r;
                }
            }
        }
        Some(self.instances[pos].clone())
    }

    /// Snaps a widget to the free spot nearest to (col, row). Returns the cell, or None.
    pub fn move_to(&mut self, id: &str, col: i32, row: i32) -> Option<Cell> {
        let size = self.get(id)?.size.clone();
        let spot = self.grid.free_near(col, row, &size, &self.taken(Some(id)))?;
        let inst = self.get_mut(id)?;
        inst.col = spot.0;
        inst.row = spot.1;
        Some(spot)
    }

    /// Packs every widget onto the grid again, outer columns first. Returns the ids of moved instances.
    pub fn arrange(&mut self) -> Vec<String> {
        let mut taken: HashSet<Cell> = HashSet::new();
        let mut moved = Vec::new();
        let half = self.grid.cols / 2;
        let mut order: Vec<usize> = (0..self.instances.len()).collect();
        order.sort_by_key(|&i| {
            let inst = &self.instances[i];
            (inst.col > half, inst.row, inst.col)
        });
        for i in order {
            let size = self.instances[i].size.clone();
            let current = (self.instances[i].col, self.instances[i].row);
            let spot = self.grid.first_free(&size, &taken).unwrap_or(current);
            if spot != current {
                moved.push(self.instances[i].id.clone());
            }
            self.instances[i].col = spot.0;
            self.instances[i].row = spot.1;
            taken.extend(Grid::cells(spot.0, spot.1, &size));
        }
        moved
    }

    /// The screen or the grid settings changed: keep cells where possible, else the nearest free one.
    pub fn regrid(&mut self, grid: Grid) {
        self.grid = grid;
        let mut taken: HashSet<Cell> = HashSet::new();
        for i in 0..self.instances.len() {
            let (col, row, size) = {
                let inst = &self.instances[i];
                (inst.col, inst.row, inst.size.clone())
            };
            let spot = if self.grid.fits(col, row, &size) && Grid::cells(col, row, &size).is_disjoint(&taken) {
                (col, row)
            } else {
                self.grid.free_near(col, row, &size, &taken).unwrap_or((0, 0))
            };
            self.instances[i].col = spot.0;
            self.instances[i].row = spot.1;
            taken.extend(Grid::cells(spot.0, spot.1, &size));
        }
    }

    /// New or edited widget folders. Instances of a type that is gone stay in layout.json
    /// (a typo in widget.json must not delete them), the service just does not run them.
    pub fn reload_types(&mut self, types: Types) {
        self.types = types;
    }
}

fn read_json(path: &std::path::Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// The instances stored in layout.json, for programs that do not own a `Layout` (the CLI, the gallery).
pub fn read_instances() -> Vec<Instance> {
    let Some(data) = read_json(&paths::layout()) else { return Vec::new() };
    data.get("instances")
        .and_then(Value::as_array)
        .map(|list| list.iter().filter_map(|v| serde_json::from_value(v.clone()).ok()).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::catalog::{OptionDef, WidgetType};
    use std::collections::BTreeMap;
    use std::sync::Arc;

    fn types() -> Types {
        let mut map = BTreeMap::new();
        for (id, sizes) in [("clock", vec!["small", "medium"]), ("weather", vec!["medium", "small", "large"])] {
            map.insert(
                id.to_string(),
                WidgetType {
                    id: id.into(),
                    dir: ".".into(),
                    name: json!(id),
                    description: json!(""),
                    category: "other".into(),
                    sizes: sizes.into_iter().map(String::from).collect(),
                    interval: 5.0,
                    fast_if: String::new(),
                    style: "glass".into(),
                    accent: "auto".into(),
                    color_accent: "indigo".into(),
                    options: vec![OptionDef {
                        key: "city".into(),
                        kind: "text".into(),
                        default: json!("Kyiv"),
                        label: json!("City"),
                        hint: json!(""),
                        choices: vec![],
                        limit: 0,
                        min: 0.0,
                        max: 100.0,
                        step: 1.0,
                    }],
                    uses: vec![],
                    platforms: vec![],
                    author: String::new(),
                    version: String::new(),
                    user: false,
                },
            );
        }
        Arc::new(map)
    }

    fn layout() -> Layout {
        Layout::new(Grid::new(1920, 1080, 1.0, 16, 24, 44, 88), types(), Settings::default())
    }

    #[test]
    fn add_remove_and_unique_ids() {
        let mut l = layout();
        let a = l.add("clock").unwrap();
        let b = l.add("clock").unwrap();
        assert_eq!((a.id.as_str(), b.id.as_str()), ("clock", "clock-2"));
        assert_ne!((a.col, a.row), (b.col, b.row));
        assert!(l.remove("clock").is_some());
        assert_eq!(l.new_id("clock"), "clock");
        assert!(l.add("nothing").is_none());
    }

    #[test]
    fn configure_validates_and_resizes() {
        let mut l = layout();
        l.add("weather").unwrap();
        let inst = l.configure("weather", &json!({"size": "large", "style": "neon", "options": {"city": "Lviv", "nope": 1}})).unwrap();
        assert_eq!((inst.size.as_str(), inst.style.as_str()), ("large", "neon"));
        assert_eq!(inst.options.get("city"), Some(&json!("Lviv")));
        assert!(!inst.options.contains_key("nope"));
        let inst = l.configure("weather", &json!({"size": "huge", "style": "bogus"})).unwrap();
        assert_eq!((inst.size.as_str(), inst.style.as_str()), ("large", "neon"));
    }

    #[test]
    fn move_snaps_to_a_free_cell_and_arrange_packs() {
        let mut l = layout();
        let a = l.add("clock").unwrap();
        l.add("clock").unwrap();
        let spot = l.move_to(&a.id, 0, 1).unwrap();
        assert_ne!(spot, (l.get("clock-2").unwrap().col, l.get("clock-2").unwrap().row));
        l.move_to("clock", 5, 3).unwrap();
        l.arrange();
        let mut cells: Vec<_> = l.instances.iter().map(|i| (i.col, i.row)).collect();
        cells.sort();
        assert_eq!(cells, vec![(0, 0), (0, 1)]);
    }

    #[test]
    fn regrid_keeps_everything_on_a_smaller_screen() {
        let mut l = layout();
        for _ in 0..4 {
            l.add("weather").unwrap();
        }
        l.regrid(Grid::new(800, 600, 1.0, 16, 24, 44, 88));
        let mut seen = HashSet::new();
        for i in &l.instances {
            assert!(l.grid.fits(i.col, i.row, &i.size));
            assert!(seen.is_disjoint(&i.cells()));
            seen.extend(i.cells());
        }
    }
}
