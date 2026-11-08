//! The widget runtime: every built-in widget draws, errors land on the card, the Cairo-style API behaves.
mod common;

use std::path::Path;
use std::sync::Arc;

use deskwidgets::core::catalog::{self, Types, STYLE_KEYS};
use deskwidgets::core::layout::Instance;
use deskwidgets::core::settings::Settings;
use deskwidgets::core::util::capture_log;
use deskwidgets::core::scaffold;
use deskwidgets::engine::canvas::Surface;
use deskwidgets::engine::fonts::FontMap;
use deskwidgets::engine::render::{self, Options};
use deskwidgets::engine::runtime::{WidgetContext, WidgetRuntime};
use deskwidgets::engine::sysinfo::SysInfo;
use serde_json::{json, Map, Value};

fn fonts() -> Arc<FontMap> {
    static FONTS: std::sync::OnceLock<Arc<FontMap>> = std::sync::OnceLock::new();
    FONTS.get_or_init(|| Arc::new(FontMap::new("auto"))).clone()
}

fn types() -> Types {
    catalog::discover()
}

/// Share of pixels that are not fully transparent.
fn coverage(surface: &Surface) -> f64 {
    let s = surface.borrow();
    let opaque = s.data().chunks_exact(4).filter(|p| p[3] != 0).count();
    opaque as f64 / (s.width() * s.height()) as f64
}

fn draw(t: &catalog::WidgetType, opts: &Options) -> (Surface, Vec<String>) {
    let (result, logged) = capture_log(|| render::render_surface(t, opts, &Settings::default(), fonts(), None));
    (result.unwrap(), logged)
}

fn en() -> Options {
    Options { lang: "en".into(), ..Options::default() }
}

#[test]
fn every_widget_draws_in_every_size_without_lua_errors() {
    let _dir = common::isolated();
    for (id, t) in types().iter() {
        if !t.supported() {
            continue;
        }
        for size in &t.sizes {
            let (surface, logged) = draw(t, &Options { size: Some(size.clone()), ..en() });
            assert!(logged.is_empty(), "{id} {size}: {logged:?}");
            assert!(coverage(&surface) > 0.3, "{id} {size}: the card is missing");
        }
    }
}

#[test]
fn every_style_and_accent_draws() {
    let _dir = common::isolated();
    let types = types();
    for style in STYLE_KEYS {
        for accent in ["auto", "purple"] {
            let opts = Options { size: Some("medium".into()), style: Some(style.into()), accent: Some(accent.into()), ..en() };
            let (surface, logged) = draw(&types["system"], &opts);
            assert!(logged.is_empty(), "{style} {accent}: {logged:?}");
            assert!(coverage(&surface) > 0.05, "{style} {accent}");
        }
    }
}

#[test]
fn live_system_widgets_read_the_real_machine() {
    let _dir = common::isolated();
    let types = types();
    for id in ["cpu", "memory", "system", "disks", "network", "battery"] {
        for size in &types[id].sizes {
            let opts = Options { size: Some(size.clone()), demo: false, ..en() };
            let (surface, logged) = draw(&types[id], &opts);
            assert!(logged.is_empty(), "{id} {size}: {logged:?}");
            assert!(coverage(&surface) > 0.3, "{id} {size}");
        }
    }
}

#[test]
fn the_picture_widget_shows_a_photo_in_any_format() {
    let _dir = common::isolated();
    let types = types();
    let tmp = tempfile::tempdir().unwrap();
    for (name, format) in [("red.jpg", image::ImageFormat::Jpeg), ("red.png", image::ImageFormat::Png), ("red.webp", image::ImageFormat::WebP)] {
        let path = tmp.path().join(name);
        image::DynamicImage::ImageRgb8(image::RgbImage::from_pixel(600, 400, image::Rgb([230, 20, 20]))).save_with_format(&path, format).unwrap_or_else(|e| {
            // no WebP encoder in this build: that format is covered by the decoder test in `images`
            assert_eq!(name, "red.webp", "{e}");
        });
        if !path.exists() {
            continue;
        }
        let mut options = Map::new();
        options.insert("image".into(), json!(path.to_str().unwrap()));
        let opts = Options { size: Some("medium".into()), demo: false, options, ..en() };
        let (surface, logged) = draw(&types["picture"], &opts);
        assert!(logged.is_empty(), "{name}: {logged:?}");
        let s = surface.borrow();
        let reds = s.data().chunks_exact(4).filter(|p| p[0] > 180 && p[2] < 90).count();
        assert!(reds > 5000, "{name}: the picture was not drawn ({reds} red pixels)");
    }
}

#[test]
fn both_languages_draw() {
    let _dir = common::isolated();
    let types = types();
    for lang in ["uk", "en"] {
        let (_, logged) = draw(&types["calendar"], &Options { lang: lang.into(), size: Some("large".into()), ..Options::default() });
        assert!(logged.is_empty(), "{lang}: {logged:?}");
    }
}

#[test]
fn scale_changes_the_window_size() {
    let _dir = common::isolated();
    let types = types();
    let (small, _) = draw(&types["clock"], &Options { size: Some("small".into()), scale: 1.0, ..en() });
    let (big, _) = draw(&types["clock"], &Options { size: Some("small".into()), scale: 1.5, ..en() });
    assert_eq!(small.borrow().width(), 202);
    assert_eq!(big.borrow().width(), 303);
}

#[test]
fn render_png_writes_a_file() {
    let _dir = common::isolated();
    let types = types();
    let tmp = tempfile::tempdir().unwrap();
    let out = tmp.path().join("sub").join("clock.png");
    assert!(render::render_png(&types["clock"], &out, &en(), &Settings::default(), fonts()));
    assert!(std::fs::read(&out).unwrap().starts_with(b"\x89PNG"));
}

fn user_widget(id: &str, lua: &str) -> catalog::WidgetType {
    let folder = scaffold::create(id, None).unwrap();
    std::fs::write(folder.join("widget.lua"), lua).unwrap();
    catalog::discover()[id].clone()
}

#[test]
fn a_widget_that_fails_shows_the_error_on_its_card() {
    let _dir = common::isolated();
    let t = user_widget("boom", "local L = require \"lib\"\nerror(\"kaboom\")\nreturn {}");
    let (surface, logged) = draw(&t, &en());
    assert!(coverage(&surface) > 0.3);
    assert!(logged.iter().any(|l| l.contains("kaboom")), "{logged:?}");
}

#[test]
fn an_error_inside_draw_is_reported_once() {
    let _dir = common::isolated();
    let t = user_widget("flaky", "local L = require \"lib\"\nreturn { draw = function() error(\"bad frame\") end }");
    let (_, logged) = draw(&t, &Options { frames: 5, ..en() });
    assert_eq!(logged.len(), 1, "{logged:?}");
}

#[test]
fn a_scaffolded_widget_renders_whatever_its_name() {
    let _dir = common::isolated();
    let folder = scaffold::create("my_widget", Some("Фото \"Львів\"\nIt's mine\\test")).unwrap();
    assert!(folder.join("widget.lua").exists());
    let t = catalog::discover()["my_widget"].clone();
    let (surface, logged) = draw(&t, &Options { size: Some("medium".into()), ..en() });
    assert!(logged.is_empty(), "{logged:?}");
    assert!(coverage(&surface) > 0.3);
}

#[test]
fn invalid_utf8_text_does_not_break_a_frame() {
    let _dir = common::isolated();
    let t = user_widget(
        "bytes",
        "local L = require \"lib\"\nreturn { draw = function(cr, x, y, w, h)\n  L.card(cr, x, y, w, h); L.font(cr, L.F.regular, 12)\n  L.text(cr, \"ok \\255\\254 bad\", x + 10, y + 30) end }",
    );
    let (_, logged) = draw(&t, &en());
    assert!(logged.is_empty(), "{logged:?}");
}

// ---------------------------------------------------------------------------------------------
fn runtime(id: &str, lua: &str) -> WidgetRuntime {
    let t = user_widget(id, lua);
    let mut options = Map::new();
    options.insert("greeting".into(), json!("Привіт"));
    let inst = Instance { id: id.into(), kind: id.into(), size: "small".into(), style: "glass".into(), accent: "auto".into(), options, col: 0, row: 0 };
    let mut ctx = WidgetContext::new(inst, t, "en", 1.0, (202, 202), fonts(), SysInfo::new(false));
    ctx.demo = true;
    WidgetRuntime::new(ctx).unwrap()
}

fn global(rt: &WidgetRuntime, name: &str) -> Value {
    rt.eval_json(&format!("return {name}"))
}

#[test]
fn a_click_reaches_the_widget_in_card_coordinates() {
    let _dir = common::isolated();
    let rt = runtime("clicky", "local L = require 'lib'\nlocal W = {}\nfunction W.draw() end\nfunction W.click(x, y, w, h) clicked = {x, y, w, h}; return true end\nreturn W");
    assert!(rt.click(16.0 + 10.0, 16.0 + 20.0));
    let clicked: Vec<f64> = global(&rt, "clicked").as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
    assert_eq!(clicked, vec![10.0, 20.0, 170.0, 170.0]);
}

#[test]
fn a_widget_without_a_click_handler_or_with_a_failing_one() {
    let _dir = common::isolated();
    let rt = runtime("quiet", "return { draw = function() end }");
    assert!(!rt.click(50.0, 50.0));
    let rt = runtime("angry", "return { draw = function() end, click = function() error('no') end }");
    let (handled, logged) = capture_log(|| rt.click(50.0, 50.0));
    assert!(!handled);
    assert_eq!(logged.len(), 1);
}

#[test]
fn options_reach_lua_as_strings_and_services_are_empty_without_providers() {
    let _dir = common::isolated();
    let mut rt = runtime(
        "probe",
        "local L = require 'lib'\nseen = L.opt('greeting')\nmedia = L.service('media')\nok = L.media_control('PlayPause')\nreturn { draw = function() end }",
    );
    rt.render();
    assert_eq!(global(&rt, "seen"), json!("Привіт"));
    assert_eq!(global(&rt, "media"), Value::Null);
    assert_eq!(global(&rt, "ok"), json!(false));
}

#[test]
fn sys_tables_have_lua_arrays_and_downloads_never_block() {
    let _dir = common::isolated();
    let rt = runtime(
        "sysprobe",
        "local L = require 'lib'\nlocal c = L.sys.cpu()\ncores = #c.cores\ncount = c.count\ntop = #L.sys.top(3)\n\
         json = L.json('{\"a\":[1,2,{\"b\":null}],\"c\":\"x\"}')\narr = #json.a\nbad, err = L.json('{nope')\n\
         enc = L.json_encode({ q = 'a\"b', n = {1, 2, 3}, t = true })\nurl = L.urlencode('a b/ключ')\nreturn { draw = function() end }",
    );
    let (cores, count) = (global(&rt, "cores"), global(&rt, "count"));
    assert_eq!(cores, count);
    assert!(global(&rt, "top").as_i64().unwrap() <= 3);
    assert_eq!(global(&rt, "arr"), json!(3));
    assert_eq!(global(&rt, "bad"), Value::Null);
    assert!(global(&rt, "err").is_string());
    let enc: Value = serde_json::from_str(global(&rt, "enc").as_str().unwrap()).unwrap();
    assert_eq!(enc, json!({"q": "a\"b", "n": [1, 2, 3], "t": true}));
    assert_eq!(global(&rt, "url"), json!("a%20b/%D0%BA%D0%BB%D1%8E%D1%87"));
}

#[test]
fn the_cairo_shim_covers_surfaces_text_and_patterns() {
    let _dir = common::isolated();
    let rt = runtime("cairoprobe", "return { draw = function() end }");
    let result = rt.eval_json(
        r#"
        local s = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 20, 20)
        local cr = cairo_create(s)
        cairo_set_source_rgba(cr, 1, 0, 0, 1)
        cairo_rectangle(cr, 0, 0, 20, 20)
        cairo_fill(cr)
        cairo_select_font_face(cr, "DW Bold", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL)
        cairo_set_font_size(cr, 10)
        local e = cairo_text_extents_t:create()
        cairo_text_extents(cr, "Привіт", e)
        local missing = cairo_image_surface_create_from_png("/does/not/exist.png")
        local g = cairo_pattern_create_linear(0, 0, 10, 0)
        cairo_pattern_add_color_stop_rgba(g, 0, 1, 1, 1, 1)
        cairo_pattern_destroy(g)
        return { width = e.x_advance, missing = cairo_image_surface_get_width(missing), w = cairo_image_surface_get_width(s) }
        "#,
    );
    assert!(result["width"].as_f64().unwrap() > 10.0);
    assert_eq!(result["missing"], json!(0));
    assert_eq!(result["w"], json!(20));
}

#[test]
fn a_non_demo_weather_widget_asks_the_network_politely_and_still_draws() {
    // without network access the widget stays on "Loading…": no error, no blocking
    let _dir = common::isolated();
    let types = types();
    let started = std::time::Instant::now();
    let (surface, logged) = draw(&types["weather"], &Options { size: Some("medium".into()), demo: false, frames: 1, ..en() });
    let _ = Path::new("");
    assert!(logged.iter().all(|l| !l.contains("attempt to")), "{logged:?}");
    assert!(coverage(&surface) > 0.3);
    assert!(started.elapsed() < std::time::Duration::from_secs(40));
}
