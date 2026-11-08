//! Regenerates docs/screenshots/*.png from real widget renders (sample data).
//!
//!     make screenshots          (or: cargo run --release --example screenshots)
//!
//! `DESKWIDGETS_SHOTS_OUT=<dir>` writes somewhere else.
use std::f64::consts::PI;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use deskwidgets::core::catalog::{self, Types};
use deskwidgets::core::i18n::set_lang;
use deskwidgets::core::settings;
use deskwidgets::engine::canvas::{new_surface, Context, Pattern, PatternKind, Surface};
use deskwidgets::engine::fonts::FontMap;
use deskwidgets::engine::render::{render_surface, Options};
use serde_json::{json, Map, Value};

const PITCH: i32 = 186;

struct Shots {
    types: Types,
    fonts: Arc<FontMap>,
    out: PathBuf,
    lang: &'static str,
}

type Job = (&'static str, &'static str, &'static str, &'static str, Value); // type, size, style, accent, options

impl Shots {
    fn render(&self, (t, size, style, accent, options): Job) -> Surface {
        let opts = Options {
            size: Some(size.into()),
            style: Some(style.into()),
            accent: Some(accent.into()),
            lang: self.lang.into(),
            options: options.as_object().cloned().unwrap_or_else(Map::new),
            ..Options::default()
        };
        render_surface(&self.types[t], &opts, &settings::load(), self.fonts.clone(), None).unwrap_or_else(|e| panic!("could not render {t}: {e}"))
    }

    fn canvas(&self, w: i32, h: i32) -> (Surface, Context) {
        let surface = new_surface(w as u32, h as u32).unwrap();
        let mut cr = Context::new(surface.clone(), self.fonts.clone());
        let g = Pattern::new(PatternKind::Linear { p0: (0.0, 0.0), p1: (w as f64, h as f64), stops: vec![] });
        for (stop, c) in [(0.0, [0.80, 0.78, 0.98]), (0.45, [0.98, 0.80, 0.90]), (1.0, [0.70, 0.86, 0.98])] {
            g.borrow_mut().add_stop(stop, [c[0], c[1], c[2], 1.0]);
        }
        cr.set_source(g);
        cr.paint();
        for (cx, cy, r, c) in [(0.2, 0.25, 0.35, [0.55f32, 0.45, 0.95]), (0.85, 0.7, 0.4, [1.0, 0.55, 0.75]), (0.55, 0.95, 0.3, [0.45, 0.75, 1.0])] {
            let rg = Pattern::new(PatternKind::Radial { c0: (cx * w as f64, cy * h as f64), r0: 0.0, c1: (cx * w as f64, cy * h as f64), r1: r * w as f64, stops: vec![] });
            rg.borrow_mut().add_stop(0.0, [c[0], c[1], c[2], 0.45]);
            rg.borrow_mut().add_stop(1.0, [c[0], c[1], c[2], 0.0]);
            cr.set_source(rg);
            cr.paint();
        }
        (surface, cr)
    }

    fn save(&self, surface: &Surface, name: &str) {
        let path = self.out.join(format!("{name}-{}.png", self.lang));
        surface.borrow().save_png(&path).unwrap();
        println!("{}", path.display());
    }
}

fn place(cr: &mut Context, picture: &Surface, x: i32, y: i32) {
    cr.set_source_surface(picture.clone(), x as f64, y as f64);
    cr.paint();
}

fn label(cr: &mut Context, text: &str, x: f64, y: f64, size: f64) {
    cr.select_font_face("DW Bold", 0, 0);
    cr.set_font_size(size);
    let extents = cr.text_extents(text);
    cr.set_source_rgba(0.15, 0.12, 0.2, 1.0);
    cr.move_to(x - extents.x_advance / 2.0, y);
    cr.show_text(text);
}

fn at(col: i32, row: i32) -> (i32, i32) {
    (40 + col * PITCH - 16, 40 + row * PITCH - 16)
}

fn hero(s: &Shots) {
    let city = if s.lang == "uk" { "Київ" } else { "Kyiv" };
    let jobs: Vec<(&str, Job, (i32, i32))> = vec![
        ("clock", ("clock", "small", "glass", "auto", json!({})), (0, 0)),
        ("calendar", ("calendar", "medium", "glass", "auto", json!({})), (1, 0)),
        ("weather", ("weather", "large", "glass", "auto", json!({"city": "Kyiv", "title": city})), (0, 1)),
        ("system", ("system", "medium", "graphite", "auto", json!({})), (2, 1)),
        ("media", ("media", "medium", "glass", "auto", json!({})), (2, 2)),
        ("note", ("note", "small", "color", "yellow", json!({})), (2, 3)),
        ("countdown", ("countdown", "small", "glass", "purple", json!({})), (3, 3)),
        ("battery", ("battery", "small", "glass", "auto", json!({})), (3, 0)),
        ("events", ("events", "medium", "light", "auto", json!({})), (4, 0)),
        ("anime", ("anime_schedule", "medium", "sakura", "auto", json!({})), (4, 1)),
        ("manga", ("manga_clock", "small", "manga", "auto", json!({})), (6, 0)),
        ("world", ("worldclock", "medium", "glass", "auto", json!({})), (4, 2)),
    ];
    let (surface, mut cr) = s.canvas(40 * 2 + 8 * PITCH - 16, 40 * 2 + 4 * PITCH - 16);
    for (_, job, (col, row)) in jobs {
        let (x, y) = at(col, row);
        place(&mut cr, &s.render(job), x, y);
    }
    s.save(&surface, "hero");
}

fn styles_sheet(s: &Shots) {
    let styles = catalog::styles();
    let (w, h) = (3 * (202 + 388 + 40) + 40, 3 * (202 + 40) + 40);
    let (surface, mut cr) = s.canvas(w, h);
    for (i, (key, name)) in styles.iter().enumerate() {
        let (col, row) = ((i % 3) as i32, (i / 3) as i32);
        let (x, y) = (30 + col * (202 + 388 + 40), 20 + row * 242);
        place(&mut cr, &s.render(("calendar", "small", key, "auto", json!({}))), x, y);
        place(&mut cr, &s.render(("system", "medium", key, "auto", json!({}))), x + 202, y);
        label(&mut cr, name, x as f64 + (202.0 + 388.0) / 2.0, y as f64 + 226.0, 14.0);
    }
    s.save(&surface, "styles");
}

fn accents_sheet(s: &Shots) {
    let keys: Vec<&str> = catalog::ACCENTS.iter().map(|(k, _)| *k).collect();
    let cols = 7;
    let (surface, mut cr) = s.canvas(cols * 190 + 40, ((keys.len() as f64 / cols as f64).ceil() as i32) * 230 + 20);
    for (i, key) in keys.iter().enumerate() {
        let (x, y) = (20 + (i as i32 % cols) * 190, 10 + (i as i32 / cols) * 230);
        place(&mut cr, &s.render(("clock", "small", "glass", key, json!({}))), x - 6, y);
        label(&mut cr, &catalog::accent_name(key), x as f64 + 95.0, y as f64 + 218.0, 13.0);
    }
    s.save(&surface, "accents");
}

fn anime_sheet(s: &Shots) {
    let (surface, mut cr) = s.canvas(40 + 388 + 388 + 388 + 30, 40 + 388 + 10);
    place(&mut cr, &s.render(("anime_schedule", "large", "sakura", "auto", json!({}))), 20, 20);
    place(&mut cr, &s.render(("anime_quote", "medium", "sakura", "auto", json!({}))), 20 + 388, 20);
    place(&mut cr, &s.render(("manga_clock", "medium", "manga", "auto", json!({}))), 20 + 388, 20 + 186);
    place(&mut cr, &s.render(("digital", "medium", "neon", "auto", json!({}))), 20 + 2 * 388, 20);
    place(&mut cr, &s.render(("anime_schedule", "medium", "neon", "auto", json!({}))), 20 + 2 * 388, 20 + 186);
    s.save(&surface, "anime");
}

fn sizes_sheet(s: &Shots) {
    let (surface, mut cr) = s.canvas(40 + 202 + 388 + 388 + 20, 40 + 388);
    place(&mut cr, &s.render(("weather", "small", "glass", "auto", json!({"city": "Kyiv"}))), 20, 20);
    place(&mut cr, &s.render(("weather", "medium", "glass", "auto", json!({"city": "Kyiv"}))), 20 + 202, 20);
    place(&mut cr, &s.render(("weather", "large", "glass", "auto", json!({"city": "Kyiv"}))), 20 + 202 + 388, 20);
    s.save(&surface, "sizes");
}

fn main() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let out = std::env::var_os("DESKWIDGETS_SHOTS_OUT").map(PathBuf::from).unwrap_or_else(|| root.join("docs").join("screenshots"));
    std::fs::create_dir_all(&out).unwrap();
    let fonts = Arc::new(FontMap::new(&settings::load().font));
    let types = catalog::discover();
    for lang in ["en", "uk"] {
        set_lang(lang);
        let shots = Shots { types: types.clone(), fonts: fonts.clone(), out: out.clone(), lang };
        hero(&shots);
        styles_sheet(&shots);
        accents_sheet(&shots);
        anime_sheet(&shots);
        sizes_sheet(&shots);
    }
    let _ = PI;
}
