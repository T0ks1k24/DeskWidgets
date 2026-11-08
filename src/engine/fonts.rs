//! Text fonts. Widgets ask for "DW Light", "DW Semibold" ... and for the bundled icon font.
//!
//! The rasteriser has no system font stack, so this module finds fonts itself (fontdb scans the
//! system font folders on both Linux and Windows) and turns text into glyph outlines (ttf-parser):
//! * Linux: each "DW <weight>" alias is the user's font (default Ubuntu Sans) at that weight; variable
//!   fonts get their `wght` axis set, families with separate files pick the nearest one.
//! * Windows: each alias maps to a Segoe UI family (or the chosen font).
//! * The bundled Material Design Icons font is always available.
//!
//! Layout is plain advance-by-advance, like Cairo's "toy" text API: no shaping, no kerning, with a
//! per-character fallback to a CJK font for the anime widgets.
use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use fontdb::{Database, Family, Query, Stretch, Style, Weight};
use tiny_skia::{Path as SkPath, PathBuilder};
use ttf_parser::{Face, OutlineBuilder, Tag};

use super::canvas::{FontExtents, TextExtents};
use crate::core::{paths, util};

pub const ICON_FAMILY: &str = "Material Design Icons";
pub const DEFAULT_LINUX_FONT: &str = "Ubuntu Sans";
pub const SYSTEM_FONT_WINDOWS: &str = "Segoe UI";

const WEIGHTS: [(&str, u16); 6] =
    [("Light", 300), ("Regular", 400), ("Medium", 500), ("Semibold", 600), ("Bold", 700), ("Heavy", 800)];

pub fn default_family() -> &'static str {
    if cfg!(windows) { SYSTEM_FONT_WINDOWS } else { DEFAULT_LINUX_FONT }
}

#[derive(Clone, Debug, PartialEq)]
pub struct FontSpec {
    pub family: String,
    pub size: f64,
    pub bold: bool,
    pub italic: bool,
}

impl Default for FontSpec {
    fn default() -> Self {
        FontSpec { family: "sans-serif".into(), size: 10.0, bold: false, italic: false }
    }
}

/// One font file at one weight, ready to read glyphs from.
struct LoadedFace {
    key: usize,
    data: Arc<Vec<u8>>,
    index: u32,
    weight: Option<f32>, // value for the `wght` axis of a variable font
    upem: f32,
    ascent: f32,
    descent: f32,
    line_gap: f32,
}

struct GlyphData {
    advance: f32, // em
    path: Option<Arc<SkPath>>, // em units, y up
    bbox: Option<[f32; 4]>, // xmin, ymin, xmax, ymax in em, y up
}

pub struct Glyph {
    pub x: f64, // pen position in em
    pub path: Option<Arc<SkPath>>,
}

pub struct Laid {
    pub glyphs: Vec<Glyph>,
    pub advance: f64, // em
    bbox: Option<[f64; 4]>, // em, y up
}

pub struct FontMap {
    db: Database,
    family: String, // the user's choice, "auto" = the system's
    windows: bool,
    faces: Mutex<HashMap<(String, u16), Option<Arc<LoadedFace>>>>,
    glyphs: Mutex<HashMap<(usize, char), Arc<GlyphData>>>,
    cjk: OnceLock<String>,
    fallbacks: OnceLock<Vec<String>>,
    next_key: AtomicUsize,
}

impl FontMap {
    pub fn new(family: &str) -> FontMap {
        FontMap::new_for(family, cfg!(windows))
    }

    /// `windows` picks how the "DW <weight>" aliases are resolved (tests check both).
    pub fn new_for(family: &str, windows: bool) -> FontMap {
        let mut db = Database::new();
        db.load_system_fonts();
        let bundled = paths::fonts().join("MaterialDesignIcons.ttf");
        if bundled.is_file() {
            let _ = db.load_font_file(&bundled);
        } else {
            util::log(format!("the icon font {} is missing", bundled.display()));
        }
        if let Some(generic) = generic_sans(&db) {
            db.set_sans_serif_family(generic.clone());
            db.set_serif_family(generic.clone());
            db.set_monospace_family(generic);
        }
        FontMap {
            db,
            family: if family.is_empty() { "auto".into() } else { family.into() },
            windows,
            faces: Mutex::new(HashMap::new()),
            glyphs: Mutex::new(HashMap::new()),
            cjk: OnceLock::new(),
            fallbacks: OnceLock::new(),
            next_key: AtomicUsize::new(1),
        }
    }

    pub fn has_family(&self, name: &str) -> bool {
        self.db.faces().any(|f| f.families.iter().any(|(n, _)| n.eq_ignore_ascii_case(name)))
    }

    /// Family of a font with Japanese glyphs (anime widgets), or "" when the system has none.
    pub fn cjk(&self) -> &str {
        self.cjk.get_or_init(|| self.find_cjk())
    }

    fn find_cjk(&self) -> String {
        if self.windows {
            return "Yu Gothic UI".into();
        }
        if util::which("fc-match").is_some() {
            let (_, out) = util::run_command("fc-match", &["-f", "%{family[0]}", ":lang=ja"], std::time::Duration::from_secs(10));
            let name = out.trim().to_string();
            if !name.is_empty() && self.has_family(&name) {
                return name;
            }
        }
        for name in ["Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans", "IPAexGothic", "IPAGothic", "TakaoGothic", "VL Gothic", "Droid Sans Fallback", "WenQuanYi Zen Hei", "Yu Gothic UI", "Meiryo"] {
            if self.has_family(name) {
                return name.into();
            }
        }
        String::new()
    }

    fn fallback_families(&self) -> &[String] {
        self.fallbacks.get_or_init(|| {
            let mut list = Vec::new();
            let cjk = self.cjk();
            if !cjk.is_empty() {
                list.push(cjk.to_string());
            }
            for name in ["DejaVu Sans", "Noto Sans Symbols", "Noto Sans Symbols 2", "Noto Sans Math", "Segoe UI Symbol", "Segoe UI Emoji"] {
                if self.has_family(name) {
                    list.push(name.to_string());
                }
            }
            list
        })
    }

    /// (family, weight) a widget's font request really means.
    pub fn resolve(&self, spec: &FontSpec) -> (String, u16) {
        let weight = if spec.bold { 700 } else { 400 };
        let name = spec.family.as_str();
        let Some(alias) = name.strip_prefix("DW ") else { return (name.to_string(), weight) };
        let custom = self.family != "auto";
        if self.windows {
            let base = if custom { self.family.clone() } else { SYSTEM_FONT_WINDOWS.to_string() };
            return match alias {
                "Light" => (if custom { base } else { "Segoe UI Light".into() }, 400),
                "Semibold" => if custom { (base, 700) } else { ("Segoe UI Semibold".into(), 400) },
                "Bold" => (base, 700),
                "Heavy" => if custom { (base, 700) } else { ("Segoe UI Black".into(), 400) },
                _ => (base, 400),
            };
        }
        let base = if custom { self.family.clone() } else { DEFAULT_LINUX_FONT.to_string() };
        let weight = WEIGHTS.iter().find(|(a, _)| *a == alias).map(|(_, w)| *w).unwrap_or(400);
        (base, weight)
    }

    fn face(&self, family: &str, weight: u16) -> Option<Arc<LoadedFace>> {
        let key = (family.to_string(), weight);
        if let Some(found) = self.faces.lock().unwrap().get(&key) {
            return found.clone();
        }
        let loaded = self.load_face(family, weight);
        self.faces.lock().unwrap().insert(key, loaded.clone());
        loaded
    }

    fn load_face(&self, family: &str, weight: u16) -> Option<Arc<LoadedFace>> {
        let families = [Family::Name(family), Family::SansSerif];
        let query = Query { families: &families, weight: Weight(weight), stretch: Stretch::Normal, style: Style::Normal };
        let id = self.db.query(&query)?;
        let info = self.db.face(id)?;
        let data = match &info.source {
            fontdb::Source::File(path) => std::fs::read(path).ok()?,
            fontdb::Source::Binary(bytes) => bytes.as_ref().as_ref().to_vec(),
            fontdb::Source::SharedFile(path, _) => std::fs::read(path).ok()?,
        };
        let parsed = Face::parse(&data, info.index).ok()?;
        let mut wght = None;
        if parsed.is_variable() {
            if let Some(axis) = parsed.variation_axes().into_iter().find(|a| a.tag == Tag::from_bytes(b"wght")) {
                wght = Some((weight as f32).clamp(axis.min_value, axis.max_value));
            }
        }
        let upem = parsed.units_per_em() as f32;
        let loaded = LoadedFace {
            key: self.next_key.fetch_add(1, Ordering::Relaxed),
            index: info.index,
            weight: wght,
            upem,
            ascent: parsed.ascender() as f32 / upem,
            descent: -(parsed.descender() as f32) / upem,
            line_gap: parsed.line_gap() as f32 / upem,
            data: Arc::new(data),
        };
        Some(Arc::new(loaded))
    }

    fn glyph(&self, face: &LoadedFace, ch: char) -> Option<Arc<GlyphData>> {
        let key = (face.key, ch);
        if let Some(found) = self.glyphs.lock().unwrap().get(&key) {
            return Some(found.clone());
        }
        let mut parsed = Face::parse(&face.data, face.index).ok()?;
        if let Some(w) = face.weight {
            parsed.set_variation(Tag::from_bytes(b"wght"), w);
        }
        let gid = parsed.glyph_index(ch)?;
        let upem = face.upem;
        let advance = parsed.glyph_hor_advance(gid).unwrap_or(0) as f32 / upem;
        let mut sink = Outline { pb: PathBuilder::new(), scale: 1.0 / upem };
        let bbox = parsed.outline_glyph(gid, &mut sink).map(|r| {
            [r.x_min as f32 / upem, r.y_min as f32 / upem, r.x_max as f32 / upem, r.y_max as f32 / upem]
        });
        let data = Arc::new(GlyphData { advance, path: sink.pb.finish().map(Arc::new), bbox });
        self.glyphs.lock().unwrap().insert(key, data.clone());
        Some(data)
    }

    /// The glyph of `ch`: the main face first, then the fallbacks.
    fn glyph_for(&self, primary: Option<&Arc<LoadedFace>>, ch: char) -> Option<Arc<GlyphData>> {
        if let Some(face) = primary {
            if let Some(g) = self.glyph(face, ch) {
                return Some(g);
            }
        }
        for family in self.fallback_families() {
            if let Some(face) = self.face(family, 400) {
                if let Some(g) = self.glyph(&face, ch) {
                    return Some(g);
                }
            }
        }
        None
    }

    pub fn layout(&self, spec: &FontSpec, text: &str) -> Laid {
        let (family, weight) = self.resolve(spec);
        let primary = self.face(&family, weight);
        let mut glyphs = Vec::new();
        let mut pen = 0.0f64;
        let mut bbox: Option<[f64; 4]> = None;
        for ch in text.chars() {
            if ch == '\n' || ch == '\r' || matches!(ch, '\u{200b}'..='\u{200f}' | '\u{fe00}'..='\u{fe0f}' | '\u{feff}') {
                continue;
            }
            let g = self.glyph_for(primary.as_ref(), ch);
            let Some(g) = g else {
                // unknown character: leave a gap about as wide as a space
                pen += 0.3;
                continue;
            };
            if let Some(b) = g.bbox {
                let b = [pen + b[0] as f64, b[1] as f64, pen + b[2] as f64, b[3] as f64];
                bbox = Some(match bbox {
                    None => b,
                    Some(o) => [o[0].min(b[0]), o[1].min(b[1]), o[2].max(b[2]), o[3].max(b[3])],
                });
            }
            glyphs.push(Glyph { x: pen, path: g.path.clone() });
            pen += g.advance as f64;
        }
        Laid { glyphs, advance: pen, bbox }
    }

    pub fn text_extents(&self, spec: &FontSpec, text: &str) -> TextExtents {
        let laid = self.layout(spec, text);
        let s = spec.size;
        match laid.bbox {
            Some([x0, y0, x1, y1]) => TextExtents {
                x_bearing: x0 * s,
                y_bearing: -y1 * s,
                width: (x1 - x0) * s,
                height: (y1 - y0) * s,
                x_advance: laid.advance * s,
                y_advance: 0.0,
            },
            None => TextExtents { x_advance: laid.advance * s, ..TextExtents::default() },
        }
    }

    pub fn font_extents(&self, spec: &FontSpec) -> FontExtents {
        let (family, weight) = self.resolve(spec);
        let s = spec.size;
        match self.face(&family, weight) {
            Some(f) => FontExtents {
                ascent: f.ascent as f64 * s,
                descent: f.descent as f64 * s,
                height: (f.ascent + f.descent + f.line_gap) as f64 * s,
                max_x_advance: s,
                max_y_advance: 0.0,
            },
            None => FontExtents { ascent: 0.9 * s, descent: 0.2 * s, height: 1.1 * s, max_x_advance: s, max_y_advance: 0.0 },
        }
    }
}

struct Outline {
    pb: PathBuilder,
    scale: f32,
}

impl OutlineBuilder for Outline {
    fn move_to(&mut self, x: f32, y: f32) {
        self.pb.move_to(x * self.scale, y * self.scale);
    }
    fn line_to(&mut self, x: f32, y: f32) {
        self.pb.line_to(x * self.scale, y * self.scale);
    }
    fn quad_to(&mut self, x1: f32, y1: f32, x: f32, y: f32) {
        let s = self.scale;
        self.pb.quad_to(x1 * s, y1 * s, x * s, y * s);
    }
    fn curve_to(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, x: f32, y: f32) {
        let s = self.scale;
        self.pb.cubic_to(x1 * s, y1 * s, x2 * s, y2 * s, x * s, y * s);
    }
    fn close(&mut self) {
        self.pb.close();
    }
}

/// The family the system calls "sans-serif": fontconfig's answer when it is there, else a well-known one.
fn generic_sans(db: &Database) -> Option<String> {
    let has = |name: &str| db.faces().any(|f| f.families.iter().any(|(n, _)| n.eq_ignore_ascii_case(name)));
    if util::which("fc-match").is_some() {
        let (_, out) = util::run_command("fc-match", &["-f", "%{family[0]}", "sans-serif"], std::time::Duration::from_secs(10));
        let name = out.trim();
        if !name.is_empty() && has(name) {
            return Some(name.to_string());
        }
    }
    ["Ubuntu Sans", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Cantarell", "Segoe UI", "Arial", "Helvetica"]
        .into_iter()
        .find(|n| has(n))
        .map(str::to_string)
}

/// Every font family on this system, sorted (the font choice in the gallery).
pub fn list_families() -> Vec<String> {
    let mut db = Database::new();
    db.load_system_fonts();
    let mut names: Vec<String> = db.faces().filter_map(|f| f.families.first().map(|(n, _)| n.clone())).collect();
    names.sort_by_key(|n| n.to_lowercase());
    names.dedup();
    names
}

/// The file the bundled icon font is expected in (for messages).
pub fn icon_font_path() -> std::path::PathBuf {
    paths::fonts().join("MaterialDesignIcons.ttf")
}

pub fn is_font_file(path: &Path) -> bool {
    matches!(path.extension().and_then(|e| e.to_str()).map(str::to_lowercase).as_deref(), Some("ttf" | "otf" | "ttc"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dw_aliases_resolve_to_weights() {
        let map = FontMap::new("auto");
        let spec = |name: &str| FontSpec { family: name.into(), ..FontSpec::default() };
        if cfg!(windows) {
            assert_eq!(map.resolve(&spec("DW Light")).0, "Segoe UI Light");
        } else {
            assert_eq!(map.resolve(&spec("DW Light")), ("Ubuntu Sans".to_string(), 300));
            assert_eq!(map.resolve(&spec("DW Heavy")).1, 800);
        }
        assert_eq!(map.resolve(&spec("Material Design Icons")), ("Material Design Icons".to_string(), 400));
    }

    #[test]
    fn windows_aliases_map_to_segoe_families_or_the_chosen_font() {
        let spec = |name: &str| FontSpec { family: name.into(), ..FontSpec::default() };
        let windows = FontMap::new_for("auto", true);
        assert_eq!(windows.resolve(&spec("DW Light")), ("Segoe UI Light".to_string(), 400));
        assert_eq!(windows.resolve(&spec("DW Bold")), ("Segoe UI".to_string(), 700));
        assert_eq!(windows.resolve(&spec("DW Heavy")).0, "Segoe UI Black");
        let custom = FontMap::new_for("Inter", true);
        assert_eq!(custom.resolve(&spec("DW Regular")), ("Inter".to_string(), 400));
        assert_eq!(custom.resolve(&spec("DW Semibold")), ("Inter".to_string(), 700));
        let linux = FontMap::new_for("Inter", false);
        assert_eq!(linux.resolve(&spec("DW Semibold")), ("Inter".to_string(), 600));
    }

    #[test]
    fn text_has_width_and_the_icon_font_has_glyphs() {
        let map = FontMap::new("auto");
        let spec = FontSpec { family: "DW Regular".into(), size: 20.0, ..FontSpec::default() };
        let e = map.text_extents(&spec, "Hello");
        assert!(e.x_advance > 20.0 && e.width > 20.0 && e.height > 8.0, "{e:?}");
        assert!(e.y_bearing < 0.0);
        let wide = map.text_extents(&spec, "Hello Hello");
        assert!(wide.x_advance > e.x_advance * 1.8);
        let icon = FontSpec { family: ICON_FAMILY.into(), size: 20.0, ..FontSpec::default() };
        let laid = map.layout(&icon, "\u{F0150}");
        assert!(laid.glyphs.iter().any(|g| g.path.is_some()), "the clock icon is in the bundled font");
        assert_eq!(map.text_extents(&spec, "  ").width, 0.0);
    }
}
