//! Settings, the widget catalog and the layout file, against real files.
mod common;

use std::collections::HashSet;
use std::sync::Arc;

use deskwidgets::core::catalog::{self, Types};
use deskwidgets::core::grid::Grid;
use deskwidgets::core::layout::Layout;
use deskwidgets::core::settings::{self, Settings};
use deskwidgets::core::{paths, scaffold, util};
use serde_json::{json, Value};

fn layout_for(types: Types) -> Layout {
    Layout::new(Grid::new(1920, 1080, 1.0, 16, 24, 44, 88), types, Settings::default())
}

#[test]
fn concurrent_atomic_writes_leave_one_complete_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("data.txt");
    let threads: Vec<_> = (0..8)
        .map(|i| {
            let path = path.clone();
            std::thread::spawn(move || {
                for n in 0..50 {
                    util::write_atomic(&path, &format!("{i}:{n}:{}", "x".repeat(2000))).unwrap();
                }
            })
        })
        .collect();
    for t in threads {
        t.join().unwrap();
    }
    let text = std::fs::read_to_string(&path).unwrap();
    assert_eq!(text.len() - text.split(':').take(2).map(|p| p.len() + 1).sum::<usize>(), 2000, "a complete file");
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1, "no temporary files are left");
}

#[test]
fn settings_survive_corruption_and_keep_unknown_keys() {
    let _dir = common::isolated();
    std::fs::write(paths::settings(), "{ this is not json").unwrap();
    assert_eq!(settings::load(), Settings::default());
    std::fs::write(paths::settings(), json!({"scale": "1.25", "gap": 20.9, "lock": "yes", "_setup_done": true}).to_string()).unwrap();
    let s = settings::load();
    assert_eq!((s.scale, s.gap, s.lock), (1.25, 20, false));
    settings::save(&s).unwrap();
    let again: Value = serde_json::from_str(&std::fs::read_to_string(paths::settings()).unwrap()).unwrap();
    assert_eq!(again["_setup_done"], json!(true));
}

#[test]
fn every_builtin_widget_loads_with_valid_options() {
    let _dir = common::isolated();
    let types = catalog::discover();
    assert!(types.len() >= 21);
    for (id, t) in types.iter() {
        assert!(!t.title().is_empty(), "{id}");
        assert!(!t.sizes.is_empty());
        assert!(t.dir.join("widget.lua").is_file());
        for o in &t.options {
            // every default must be acceptable to its own option
            let default = o.default_value(Some("en"));
            if !(o.kind == "file" || o.kind == "date") {
                assert!(o.coerce(&default).is_ok(), "{id}.{}: default {default:?}", o.key);
            }
        }
    }
}

#[test]
fn a_user_widget_overrides_a_builtin_and_a_broken_manifest_is_skipped() {
    let _dir = common::isolated();
    let folder = scaffold::create("clock", Some("My clock")).unwrap();
    let types = catalog::discover();
    assert!(types["clock"].user);
    assert_eq!(types["clock"].title_in("en"), "My clock");
    assert_eq!(types["clock"].dir, folder);
    let broken = paths::user_widgets().join("broken");
    std::fs::create_dir_all(&broken).unwrap();
    std::fs::write(broken.join("widget.json"), "{ nope").unwrap();
    std::fs::write(broken.join("widget.lua"), "return {}").unwrap();
    let types = catalog::discover();
    assert!(!types.contains_key("broken"));
    assert!(types.contains_key("weather"), "the others are fine");
}

#[test]
fn first_run_uses_the_default_layout_and_save_load_round_trips() {
    let _dir = common::isolated();
    let types = catalog::discover();
    let mut layout = layout_for(types.clone());
    layout.load();
    let ids: Vec<&str> = layout.instances.iter().map(|i| i.id.as_str()).collect();
    assert_eq!(ids, vec!["clock", "calendar", "weather", "system", "events", "media", "anime_quote"]);
    let right = layout.get("events").unwrap();
    assert_eq!(right.col, layout.grid.cols - 2, "negative columns count from the right edge");
    layout.configure("weather", &json!({"options": {"city": "Lviv"}, "style": "sakura"}));
    layout.save();
    let mut again = layout_for(types);
    again.load();
    assert_eq!(again.instances, layout.instances);
    assert_eq!(again.get("weather").unwrap().options["city"], json!("Lviv"));
}

#[test]
fn a_hostile_layout_file_is_sanitised() {
    let _dir = common::isolated();
    let types = catalog::discover();
    let file = json!({"version": 3, "instances": [
        {"id": "../../etc/passwd", "type": "clock", "size": "huge", "style": "evil", "accent": 7, "col": "x", "row": 1e9},
        {"id": "ok-1", "type": "weather", "size": "medium", "options": {"city": ["not", "text"], "title": "Kyiv", "bogus": 1}, "col": -5, "row": 0},
        {"id": "ok-1", "type": "clock", "col": 0, "row": 0},
        {"type": "does-not-exist"},
        "a string",
        42,
    ]});
    std::fs::write(paths::layout(), file.to_string()).unwrap();
    let mut layout = layout_for(types);
    layout.load();
    assert_eq!(layout.instances.len(), 3, "{:?}", layout.instances.iter().map(|i| &i.id).collect::<Vec<_>>());
    let ids: HashSet<&str> = layout.instances.iter().map(|i| i.id.as_str()).collect();
    assert_eq!(ids.len(), 3, "ids are unique");
    assert!(layout.instances.iter().all(|i| !i.id.contains('/')));
    let first = &layout.instances[0];
    assert_eq!((first.size.as_str(), first.style.as_str()), ("small", "glass"));
    let weather = layout.instances.iter().find(|i| i.kind == "weather").unwrap();
    assert_eq!(weather.options.get("title"), Some(&json!("Kyiv")));
    assert!(!weather.options.contains_key("city") && !weather.options.contains_key("bogus"));
    // nothing overlaps and everything fits
    let mut taken: HashSet<(i32, i32)> = HashSet::new();
    for i in &layout.instances {
        assert!(layout.grid.fits(i.col, i.row, &i.size));
        assert!(taken.is_disjoint(&i.cells()));
        taken.extend(i.cells());
    }
}

#[test]
fn overlapping_instances_are_pushed_apart_and_unknown_types_are_not_loaded() {
    let _dir = common::isolated();
    let types = catalog::discover();
    let file = json!({"instances": [
        {"id": "a", "type": "clock", "col": 2, "row": 2},
        {"id": "b", "type": "clock", "col": 2, "row": 2},
        {"id": "c", "type": "weather", "size": "medium", "col": 1, "row": 2},
    ]});
    std::fs::write(paths::layout(), file.to_string()).unwrap();
    let mut layout = layout_for(types);
    layout.load();
    let mut taken: HashSet<(i32, i32)> = HashSet::new();
    for i in &layout.instances {
        assert!(taken.is_disjoint(&i.cells()), "{} overlaps", i.id);
        taken.extend(i.cells());
    }
    assert_eq!(layout.instances.len(), 3);
    // a type that disappears while the service runs stays in the layout
    let mut small: std::collections::BTreeMap<String, catalog::WidgetType> = (*layout.types).clone().into_iter().collect();
    small.remove("clock");
    layout.reload_types(Arc::new(small));
    assert!(layout.get("a").is_some());
}
