//! A Cairo-style drawing context on top of tiny-skia, so widgets keep drawing with `cairo_arc(cr, ...)`.
//!
//! Semantics follow Cairo where widgets can notice them:
//! * path points are transformed by the current matrix when they are added (not when painted);
//! * a source pattern keeps the matrix that was current when it was set;
//! * `clip()` consumes the path, `fill_preserve()` and `stroke_preserve()` keep it;
//! * `show_text()` paints glyphs at the current point and advances it.
//!
//! This is the only place that knows about the rasteriser; there is no system Cairo, so Linux and
//! Windows draw identically.
use std::cell::RefCell;
use std::f64::consts::PI;
use std::rc::Rc;
use std::sync::Arc;

use tiny_skia::{
    BlendMode, Color, FillRule, FilterQuality, GradientStop, LinearGradient, LineCap, LineJoin, Mask, Paint, Path,
    PathBuilder, Pattern as SkPattern, Pixmap, Point, RadialGradient, Rect, SpreadMode, Stroke, StrokeDash, Transform,
};

use super::fonts::{FontMap, FontSpec};

pub type Surface = Rc<RefCell<Pixmap>>;

pub fn new_surface(width: u32, height: u32) -> Option<Surface> {
    Pixmap::new(width.max(1), height.max(1)).map(|p| Rc::new(RefCell::new(p)))
}

// ---------------------------------------------------------------------------------------------
// matrix, in Cairo's layout: x' = xx*x + xy*y + x0 ; y' = yx*x + yy*y + y0
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Matrix {
    pub xx: f64,
    pub yx: f64,
    pub xy: f64,
    pub yy: f64,
    pub x0: f64,
    pub y0: f64,
}

impl Matrix {
    pub const IDENTITY: Matrix = Matrix { xx: 1.0, yx: 0.0, xy: 0.0, yy: 1.0, x0: 0.0, y0: 0.0 };

    pub fn apply(&self, x: f64, y: f64) -> (f64, f64) {
        (self.xx * x + self.xy * y + self.x0, self.yx * x + self.yy * y + self.y0)
    }

    /// `self` after `other`: the result maps p to self(other(p)).
    pub fn then_after(&self, other: &Matrix) -> Matrix {
        Matrix {
            xx: self.xx * other.xx + self.xy * other.yx,
            yx: self.yx * other.xx + self.yy * other.yx,
            xy: self.xx * other.xy + self.xy * other.yy,
            yy: self.yx * other.xy + self.yy * other.yy,
            x0: self.xx * other.x0 + self.xy * other.y0 + self.x0,
            y0: self.yx * other.x0 + self.yy * other.y0 + self.y0,
        }
    }

    pub fn translate(&mut self, tx: f64, ty: f64) {
        self.x0 += self.xx * tx + self.xy * ty;
        self.y0 += self.yx * tx + self.yy * ty;
    }

    pub fn scale(&mut self, sx: f64, sy: f64) {
        self.xx *= sx;
        self.yx *= sx;
        self.xy *= sy;
        self.yy *= sy;
    }

    pub fn rotate(&mut self, angle: f64) {
        let (s, c) = angle.sin_cos();
        let (xx, yx, xy, yy) = (self.xx, self.yx, self.xy, self.yy);
        self.xx = xx * c + xy * s;
        self.yx = yx * c + yy * s;
        self.xy = -xx * s + xy * c;
        self.yy = -yx * s + yy * c;
    }

    pub fn det(&self) -> f64 {
        self.xx * self.yy - self.xy * self.yx
    }

    pub fn invert(&self) -> Option<Matrix> {
        let det = self.det();
        if det == 0.0 || !det.is_finite() {
            return None;
        }
        let (xx, yx, xy, yy) = (self.yy / det, -self.yx / det, -self.xy / det, self.xx / det);
        Some(Matrix { xx, yx, xy, yy, x0: -(xx * self.x0 + xy * self.y0), y0: -(yx * self.x0 + yy * self.y0) })
    }

    /// The factor lengths grow by (exact for the uniform scales widgets use).
    pub fn scale_factor(&self) -> f64 {
        self.det().abs().sqrt()
    }

    pub fn to_transform(&self) -> Transform {
        Transform::from_row(self.xx as f32, self.yx as f32, self.xy as f32, self.yy as f32, self.x0 as f32, self.y0 as f32)
    }
}

// ---------------------------------------------------------------------------------------------
// patterns
#[derive(Clone, Debug)]
pub enum PatternKind {
    Solid([f32; 4]),
    Linear { p0: (f64, f64), p1: (f64, f64), stops: Vec<(f32, [f32; 4])> },
    Radial { c0: (f64, f64), r0: f64, c1: (f64, f64), r1: f64, stops: Vec<(f32, [f32; 4])> },
    Image { surface: Surface, ox: f64, oy: f64 },
}

pub struct Pattern {
    pub kind: PatternKind,
    pub filter: FilterQuality,
    pub extend: SpreadMode,
}

pub type PatternRef = Rc<RefCell<Pattern>>;

impl Pattern {
    pub fn new(kind: PatternKind) -> PatternRef {
        let extend = match kind {
            PatternKind::Image { .. } => SpreadMode::Pad,
            _ => SpreadMode::Pad,
        };
        Rc::new(RefCell::new(Pattern { kind, filter: FilterQuality::Bilinear, extend }))
    }

    pub fn solid(r: f64, g: f64, b: f64, a: f64) -> PatternRef {
        Pattern::new(PatternKind::Solid([r as f32, g as f32, b as f32, a as f32]))
    }

    pub fn add_stop(&mut self, offset: f64, rgba: [f32; 4]) {
        match &mut self.kind {
            PatternKind::Linear { stops, .. } | PatternKind::Radial { stops, .. } => stops.push((offset as f32, rgba)),
            _ => {}
        }
    }
}

fn clamp01(v: f32) -> f32 {
    v.clamp(0.0, 1.0)
}

fn color(rgba: [f32; 4], alpha: f32) -> Color {
    Color::from_rgba(clamp01(rgba[0]), clamp01(rgba[1]), clamp01(rgba[2]), clamp01(rgba[3] * alpha)).unwrap_or(Color::TRANSPARENT)
}

fn gradient_stops(stops: &[(f32, [f32; 4])], alpha: f32, remap: impl Fn(f32) -> f32) -> Vec<(f32, Color)> {
    let mut sorted: Vec<(f32, [f32; 4])> = stops.to_vec();
    sorted.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    sorted.into_iter().map(|(o, c)| (clamp01(remap(clamp01(o))), color(c, alpha))).collect()
}

fn to_stops(stops: Vec<(f32, Color)>) -> Vec<GradientStop> {
    stops.into_iter().map(|(o, c)| GradientStop::new(o, c)).collect()
}

// ---------------------------------------------------------------------------------------------
#[derive(Clone, Copy, Debug, PartialEq)]
enum Seg {
    Move(f64, f64),
    Line(f64, f64),
    Cubic(f64, f64, f64, f64, f64, f64),
    Close,
}

#[derive(Clone)]
struct GState {
    ctm: Matrix,
    source: PatternRef,
    source_ctm: Matrix,
    line_width: f64,
    cap: LineCap,
    join: LineJoin,
    miter: f64,
    dash: Vec<f64>,
    dash_offset: f64,
    fill_rule: FillRule,
    operator: i32,
    clip: Option<Mask>,
    font: FontSpec,
}

pub struct Context {
    surface: Surface,
    gs: GState,
    stack: Vec<GState>,
    path: Vec<Seg>,
    cur: Option<(f64, f64)>, // current point, device space
    start: Option<(f64, f64)>,
    fonts: Arc<FontMap>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct TextExtents {
    pub x_bearing: f64,
    pub y_bearing: f64,
    pub width: f64,
    pub height: f64,
    pub x_advance: f64,
    pub y_advance: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct FontExtents {
    pub ascent: f64,
    pub descent: f64,
    pub height: f64,
    pub max_x_advance: f64,
    pub max_y_advance: f64,
}

impl Context {
    pub fn new(surface: Surface, fonts: Arc<FontMap>) -> Context {
        Context {
            surface,
            gs: GState {
                ctm: Matrix::IDENTITY,
                source: Pattern::solid(0.0, 0.0, 0.0, 1.0),
                source_ctm: Matrix::IDENTITY,
                line_width: 2.0,
                cap: LineCap::Butt,
                join: LineJoin::Miter,
                miter: 10.0,
                dash: Vec::new(),
                dash_offset: 0.0,
                fill_rule: FillRule::Winding,
                operator: 2,
                clip: None,
                font: FontSpec::default(),
            },
            stack: Vec::new(),
            path: Vec::new(),
            cur: None,
            start: None,
            fonts,
        }
    }

    pub fn surface(&self) -> &Surface {
        &self.surface
    }

    // state
    pub fn save(&mut self) {
        self.stack.push(self.gs.clone());
    }

    pub fn restore(&mut self) {
        if let Some(gs) = self.stack.pop() {
            self.gs = gs;
        }
    }

    pub fn translate(&mut self, tx: f64, ty: f64) {
        self.gs.ctm.translate(tx, ty);
    }
    pub fn scale(&mut self, sx: f64, sy: f64) {
        self.gs.ctm.scale(sx, sy);
    }
    pub fn rotate(&mut self, angle: f64) {
        self.gs.ctm.rotate(angle);
    }
    pub fn identity_matrix(&mut self) {
        self.gs.ctm = Matrix::IDENTITY;
    }
    pub fn matrix(&self) -> Matrix {
        self.gs.ctm
    }

    // sources
    pub fn set_source_rgba(&mut self, r: f64, g: f64, b: f64, a: f64) {
        self.set_source(Pattern::solid(r, g, b, a));
    }

    pub fn set_source(&mut self, pattern: PatternRef) {
        self.gs.source = pattern;
        self.gs.source_ctm = self.gs.ctm;
    }

    pub fn get_source(&self) -> PatternRef {
        self.gs.source.clone()
    }

    pub fn set_source_surface(&mut self, surface: Surface, x: f64, y: f64) {
        self.set_source(Pattern::new(PatternKind::Image { surface, ox: x, oy: y }));
    }

    pub fn set_line_width(&mut self, w: f64) {
        self.gs.line_width = w.max(0.0);
    }
    pub fn set_line_cap(&mut self, cap: i32) {
        self.gs.cap = match cap {
            1 => LineCap::Round,
            2 => LineCap::Square,
            _ => LineCap::Butt,
        };
    }
    pub fn set_line_join(&mut self, join: i32) {
        self.gs.join = match join {
            1 => LineJoin::Round,
            2 => LineJoin::Bevel,
            _ => LineJoin::Miter,
        };
    }
    pub fn set_miter_limit(&mut self, m: f64) {
        self.gs.miter = m.max(1.0);
    }
    pub fn set_dash(&mut self, dashes: Vec<f64>, offset: f64) {
        self.gs.dash = dashes;
        self.gs.dash_offset = offset;
    }
    pub fn set_fill_rule(&mut self, rule: i32) {
        self.gs.fill_rule = if rule == 1 { FillRule::EvenOdd } else { FillRule::Winding };
    }
    pub fn set_operator(&mut self, op: i32) {
        self.gs.operator = op;
    }

    // path
    pub fn new_path(&mut self) {
        self.path.clear();
        self.cur = None;
        self.start = None;
    }

    pub fn new_sub_path(&mut self) {
        self.cur = None;
    }

    pub fn move_to(&mut self, x: f64, y: f64) {
        let p = self.gs.ctm.apply(x, y);
        self.move_to_device(p);
    }

    fn move_to_device(&mut self, p: (f64, f64)) {
        // two move_to in a row: the last one wins
        if let Some(Seg::Move(..)) = self.path.last() {
            self.path.pop();
        }
        self.path.push(Seg::Move(p.0, p.1));
        self.cur = Some(p);
        self.start = Some(p);
    }

    pub fn line_to(&mut self, x: f64, y: f64) {
        let p = self.gs.ctm.apply(x, y);
        if self.cur.is_none() {
            self.move_to_device(p);
            return;
        }
        self.path.push(Seg::Line(p.0, p.1));
        self.cur = Some(p);
    }

    pub fn curve_to(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, x3: f64, y3: f64) {
        let m = self.gs.ctm;
        let (a, b, c) = (m.apply(x1, y1), m.apply(x2, y2), m.apply(x3, y3));
        if self.cur.is_none() {
            self.move_to_device(a);
        }
        self.path.push(Seg::Cubic(a.0, a.1, b.0, b.1, c.0, c.1));
        self.cur = Some(c);
    }

    /// Current point in user space.
    pub fn current_point(&self) -> Option<(f64, f64)> {
        let cur = self.cur?;
        let inv = self.gs.ctm.invert()?;
        Some(inv.apply(cur.0, cur.1))
    }

    pub fn rel_move_to(&mut self, dx: f64, dy: f64) {
        if let Some((x, y)) = self.current_point() {
            self.move_to(x + dx, y + dy);
        }
    }
    pub fn rel_line_to(&mut self, dx: f64, dy: f64) {
        if let Some((x, y)) = self.current_point() {
            self.line_to(x + dx, y + dy);
        }
    }
    pub fn rel_curve_to(&mut self, dx1: f64, dy1: f64, dx2: f64, dy2: f64, dx3: f64, dy3: f64) {
        if let Some((x, y)) = self.current_point() {
            self.curve_to(x + dx1, y + dy1, x + dx2, y + dy2, x + dx3, y + dy3);
        }
    }

    pub fn close_path(&mut self) {
        if self.cur.is_none() {
            return;
        }
        self.path.push(Seg::Close);
        self.cur = self.start;
    }

    pub fn rectangle(&mut self, x: f64, y: f64, w: f64, h: f64) {
        self.move_to(x, y);
        self.line_to(x + w, y);
        self.line_to(x + w, y + h);
        self.line_to(x, y + h);
        self.close_path();
        self.move_to(x, y);
    }

    pub fn arc(&mut self, xc: f64, yc: f64, r: f64, a1: f64, mut a2: f64) {
        if r <= 0.0 {
            return;
        }
        if a2 < a1 {
            a2 += 2.0 * PI * ((a1 - a2) / (2.0 * PI)).ceil();
        }
        self.arc_segments(xc, yc, r, a1, a2);
    }

    pub fn arc_negative(&mut self, xc: f64, yc: f64, r: f64, mut a1: f64, a2: f64) {
        if r <= 0.0 {
            return;
        }
        if a2 > a1 {
            a1 += 2.0 * PI * ((a2 - a1) / (2.0 * PI)).ceil();
        }
        self.arc_segments(xc, yc, r, a1, a2);
    }

    /// An arc from `a1` to `a2` (either direction), drawn as cubic Béziers of at most 45 degrees.
    fn arc_segments(&mut self, xc: f64, yc: f64, r: f64, a1: f64, a2: f64) {
        let (sx, sy) = (xc + r * a1.cos(), yc + r * a1.sin());
        if self.cur.is_none() {
            self.move_to(sx, sy);
        } else {
            self.line_to(sx, sy);
        }
        let total = a2 - a1;
        let n = ((total.abs() / (PI / 4.0)).ceil() as usize).max(1);
        let step = total / n as f64;
        let h = 4.0 / 3.0 * (step / 4.0).tan();
        let mut a = a1;
        for _ in 0..n {
            let b = a + step;
            let (sa, ca) = a.sin_cos();
            let (sb, cb) = b.sin_cos();
            self.curve_to(
                xc + r * (ca - h * sa),
                yc + r * (sa + h * ca),
                xc + r * (cb + h * sb),
                yc + r * (sb - h * cb),
                xc + r * cb,
                yc + r * sb,
            );
            a = b;
        }
    }

    fn build_path(&self) -> Option<Path> {
        let mut pb = PathBuilder::new();
        for seg in &self.path {
            match *seg {
                Seg::Move(x, y) => pb.move_to(x as f32, y as f32),
                Seg::Line(x, y) => pb.line_to(x as f32, y as f32),
                Seg::Cubic(a, b, c, d, e, f) => pb.cubic_to(a as f32, b as f32, c as f32, d as f32, e as f32, f as f32),
                Seg::Close => pb.close(),
            }
        }
        pb.finish()
    }

    // painting
    fn blend_mode(&self) -> BlendMode {
        match self.gs.operator {
            0 => BlendMode::Clear,
            1 => BlendMode::Source,
            3 => BlendMode::SourceIn,
            4 => BlendMode::SourceOut,
            5 => BlendMode::SourceAtop,
            6 => BlendMode::Destination,
            7 => BlendMode::DestinationOver,
            8 => BlendMode::DestinationIn,
            9 => BlendMode::DestinationOut,
            10 => BlendMode::DestinationAtop,
            11 => BlendMode::Xor,
            12 => BlendMode::Plus,
            14 => BlendMode::Multiply,
            15 => BlendMode::Screen,
            16 => BlendMode::Overlay,
            17 => BlendMode::Darken,
            18 => BlendMode::Lighten,
            _ => BlendMode::SourceOver,
        }
    }

    /// Runs `draw` with a paint made from the current source (applying `alpha`).
    fn with_paint(&mut self, alpha: f32, draw: impl FnOnce(&mut Pixmap, &Paint, Option<&Mask>)) {
        let pattern = self.gs.source.clone();
        let pattern = pattern.borrow();
        let ctm = self.gs.source_ctm;
        let mut paint = Paint { anti_alias: true, blend_mode: self.blend_mode(), ..Paint::default() };
        let mask = self.gs.clip.as_ref();
        let dest = self.surface.clone();
        match &pattern.kind {
            PatternKind::Solid(rgba) => {
                paint.set_color(color(*rgba, alpha));
                draw(&mut dest.borrow_mut(), &paint, mask);
            }
            PatternKind::Linear { p0, p1, stops } => {
                let stops = to_stops(gradient_stops(stops, alpha, |o| o));
                let shader = match stops.len() {
                    0 => None,
                    1 => None,
                    _ => LinearGradient::new(
                        Point::from_xy(p0.0 as f32, p0.1 as f32),
                        Point::from_xy(p1.0 as f32, p1.1 as f32),
                        stops,
                        pattern.extend,
                        ctm.to_transform(),
                    ),
                };
                match shader {
                    Some(shader) => paint.shader = shader,
                    None => {
                        let c = pattern_single_color(&pattern.kind, alpha);
                        paint.set_color(c);
                    }
                }
                draw(&mut dest.borrow_mut(), &paint, mask);
            }
            PatternKind::Radial { c0, r0, c1, r1, stops } => {
                let (r0, r1) = (r0.max(0.0), r1.max(0.0));
                let ratio = if r1 > 0.0 { (r0 / r1) as f32 } else { 0.0 };
                let remapped = gradient_stops(stops, alpha, |o| ratio + o * (1.0 - ratio));
                let mut all = Vec::new();
                if ratio > 0.0 {
                    if let Some(first) = remapped.first() {
                        all.push((0.0, first.1));
                    }
                }
                all.extend(remapped);
                let all = to_stops(all);
                let shader = if all.len() >= 2 && r1 > 0.0 {
                    RadialGradient::new(
                        Point::from_xy(c0.0 as f32, c0.1 as f32),
                        Point::from_xy(c1.0 as f32, c1.1 as f32),
                        r1 as f32,
                        all,
                        pattern.extend,
                        ctm.to_transform(),
                    )
                } else {
                    None
                };
                match shader {
                    Some(shader) => paint.shader = shader,
                    None => paint.set_color(pattern_single_color(&pattern.kind, alpha)),
                }
                draw(&mut dest.borrow_mut(), &paint, mask);
            }
            PatternKind::Image { surface, ox, oy } => {
                let mut to_device = ctm;
                to_device.translate(*ox, *oy);
                let quality = pattern.filter;
                if Rc::ptr_eq(surface, &dest) {
                    let copy = dest.borrow().clone();
                    paint.shader = SkPattern::new(copy.as_ref(), pattern.extend, quality, alpha, to_device.to_transform());
                    draw(&mut dest.borrow_mut(), &paint, mask);
                } else {
                    let src = surface.borrow();
                    paint.shader = SkPattern::new(src.as_ref(), pattern.extend, quality, alpha, to_device.to_transform());
                    draw(&mut dest.borrow_mut(), &paint, mask);
                }
            }
        }
    }

    pub fn fill(&mut self) {
        self.fill_preserve();
        self.new_path();
    }

    pub fn fill_preserve(&mut self) {
        let Some(path) = self.build_path() else { return };
        let rule = self.gs.fill_rule;
        self.with_paint(1.0, |pixmap, paint, mask| {
            pixmap.fill_path(&path, paint, rule, Transform::identity(), mask);
        });
    }

    pub fn stroke(&mut self) {
        self.stroke_preserve();
        self.new_path();
    }

    pub fn stroke_preserve(&mut self) {
        let Some(path) = self.build_path() else { return };
        let k = self.gs.ctm.scale_factor();
        let mut stroke = Stroke {
            width: (self.gs.line_width * k) as f32,
            miter_limit: self.gs.miter as f32,
            line_cap: self.gs.cap,
            line_join: self.gs.join,
            dash: None,
        };
        if !self.gs.dash.is_empty() {
            let array: Vec<f32> = self.gs.dash.iter().map(|d| (d * k) as f32).collect();
            stroke.dash = StrokeDash::new(array, (self.gs.dash_offset * k) as f32);
        }
        self.with_paint(1.0, |pixmap, paint, mask| {
            pixmap.stroke_path(&path, paint, &stroke, Transform::identity(), mask);
        });
    }

    pub fn paint(&mut self) {
        self.paint_with_alpha(1.0);
    }

    pub fn paint_with_alpha(&mut self, alpha: f64) {
        let (w, h) = {
            let s = self.surface.borrow();
            (s.width() as f32, s.height() as f32)
        };
        let Some(rect) = Rect::from_xywh(0.0, 0.0, w, h) else { return };
        self.with_paint(alpha as f32, |pixmap, paint, mask| {
            pixmap.fill_rect(rect, paint, Transform::identity(), mask);
        });
    }

    pub fn clip(&mut self) {
        self.clip_preserve();
        self.new_path();
    }

    pub fn clip_preserve(&mut self) {
        let (w, h) = {
            let s = self.surface.borrow();
            (s.width(), s.height())
        };
        let rule = self.gs.fill_rule;
        match self.build_path() {
            Some(path) => match self.gs.clip.as_mut() {
                Some(mask) => mask.intersect_path(&path, rule, true, Transform::identity()),
                None => {
                    if let Some(mut mask) = Mask::new(w, h) {
                        mask.fill_path(&path, rule, true, Transform::identity());
                        self.gs.clip = Some(mask);
                    }
                }
            },
            None => {
                // an empty path clips everything away
                self.gs.clip = Mask::new(w, h);
            }
        }
    }

    pub fn reset_clip(&mut self) {
        self.gs.clip = None;
    }

    // text
    pub fn select_font_face(&mut self, name: &str, slant: i32, weight: i32) {
        self.gs.font.family = name.to_string();
        self.gs.font.bold = weight == 1;
        self.gs.font.italic = slant != 0;
    }

    pub fn set_font_size(&mut self, size: f64) {
        self.gs.font.size = size;
    }

    pub fn text_extents(&self, text: &str) -> TextExtents {
        self.fonts.text_extents(&self.gs.font, text)
    }

    pub fn font_extents(&self) -> FontExtents {
        self.fonts.font_extents(&self.gs.font)
    }

    /// The glyph outlines of `text` placed at the current point, in device space, and its advance.
    fn text_outline(&self, text: &str) -> Option<(Path, f64)> {
        let size = self.gs.font.size;
        if size <= 0.0 || text.is_empty() {
            return None;
        }
        let (ux, uy) = self.current_point().unwrap_or((0.0, 0.0));
        let laid = self.fonts.layout(&self.gs.font, text);
        let mut builder = PathBuilder::new();
        let mut any = false;
        for glyph in &laid.glyphs {
            let Some(outline) = &glyph.path else { continue };
            let mut m = self.gs.ctm;
            m.translate(ux + glyph.x * size, uy);
            m.scale(size, -size);
            if let Some(moved) = outline.as_ref().clone().transform(m.to_transform()) {
                builder.push_path(&moved);
                any = true;
            }
        }
        let advance = laid.advance * size;
        if !any {
            return None.or_else(|| Some((empty_path(), advance)));
        }
        builder.finish().map(|p| (p, advance))
    }

    pub fn show_text(&mut self, text: &str) {
        let Some((outline, advance)) = self.text_outline(text) else { return };
        if outline.len() > 1 {
            self.with_paint(1.0, |pixmap, paint, mask| {
                pixmap.fill_path(&outline, paint, FillRule::Winding, Transform::identity(), mask);
            });
        }
        let (ux, uy) = self.current_point().unwrap_or((0.0, 0.0));
        self.move_to(ux + advance, uy);
    }

    pub fn text_path(&mut self, text: &str) {
        let Some((outline, advance)) = self.text_outline(text) else { return };
        let (ux, uy) = self.current_point().unwrap_or((0.0, 0.0));
        let mut seg_count = 0;
        for seg in outline.segments() {
            use tiny_skia::PathSegment as S;
            match seg {
                S::MoveTo(p) => self.path.push(Seg::Move(p.x as f64, p.y as f64)),
                S::LineTo(p) => self.path.push(Seg::Line(p.x as f64, p.y as f64)),
                S::QuadTo(c, p) => {
                    // elevate to a cubic from the previous point
                    let (x0, y0) = last_point(&self.path);
                    let (cx, cy, px, py) = (c.x as f64, c.y as f64, p.x as f64, p.y as f64);
                    self.path.push(Seg::Cubic(
                        x0 + 2.0 / 3.0 * (cx - x0),
                        y0 + 2.0 / 3.0 * (cy - y0),
                        px + 2.0 / 3.0 * (cx - px),
                        py + 2.0 / 3.0 * (cy - py),
                        px,
                        py,
                    ));
                }
                S::CubicTo(a, b, c) => self.path.push(Seg::Cubic(a.x as f64, a.y as f64, b.x as f64, b.y as f64, c.x as f64, c.y as f64)),
                S::Close => self.path.push(Seg::Close),
            }
            seg_count += 1;
        }
        let _ = seg_count;
        self.move_to(ux + advance, uy);
    }
}

fn last_point(path: &[Seg]) -> (f64, f64) {
    for seg in path.iter().rev() {
        match *seg {
            Seg::Move(x, y) | Seg::Line(x, y) => return (x, y),
            Seg::Cubic(_, _, _, _, x, y) => return (x, y),
            Seg::Close => continue,
        }
    }
    (0.0, 0.0)
}

fn empty_path() -> Path {
    let mut pb = PathBuilder::new();
    pb.move_to(0.0, 0.0);
    pb.line_to(0.0, 0.0);
    pb.finish().unwrap_or_else(|| PathBuilder::from_rect(Rect::from_xywh(0.0, 0.0, 1.0, 1.0).unwrap()))
}

fn pattern_single_color(kind: &PatternKind, alpha: f32) -> Color {
    match kind {
        PatternKind::Linear { stops, .. } | PatternKind::Radial { stops, .. } => {
            stops.last().map(|(_, c)| color(*c, alpha)).unwrap_or(Color::TRANSPARENT)
        }
        PatternKind::Solid(c) => color(*c, alpha),
        PatternKind::Image { .. } => Color::TRANSPARENT,
    }
}

/// Maps Cairo's filter constants to tiny-skia's.
pub fn filter_quality(filter: i32) -> FilterQuality {
    match filter {
        3 => FilterQuality::Nearest,
        0 => FilterQuality::Bilinear,
        1 | 4 => FilterQuality::Bilinear,
        _ => FilterQuality::Bicubic,
    }
}

pub fn spread_mode(extend: i32) -> SpreadMode {
    match extend {
        1 => SpreadMode::Repeat,
        2 => SpreadMode::Reflect,
        _ => SpreadMode::Pad,
    }
}

/// Premultiplied RGBA8 to the BGRA byte order of X11's 32-bit visuals and Windows DIBs.
pub fn rgba_to_bgra(src: &[u8], dst: &mut [u8]) {
    for (s, d) in src.chunks_exact(4).zip(dst.chunks_exact_mut(4)) {
        d[0] = s[2];
        d[1] = s[1];
        d[2] = s[0];
        d[3] = s[3];
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context(w: u32, h: u32) -> Context {
        Context::new(new_surface(w, h).unwrap(), Arc::new(FontMap::new("auto")))
    }

    fn pixel(cx: &Context, x: u32, y: u32) -> [u8; 4] {
        let s = cx.surface().borrow();
        let p = s.pixel(x, y).unwrap();
        [p.red(), p.green(), p.blue(), p.alpha()]
    }

    #[test]
    fn fills_a_rectangle_with_the_source() {
        let mut cx = context(20, 20);
        cx.set_source_rgba(1.0, 0.0, 0.0, 1.0);
        cx.rectangle(5.0, 5.0, 10.0, 10.0);
        cx.fill();
        assert_eq!(pixel(&cx, 10, 10), [255, 0, 0, 255]);
        assert_eq!(pixel(&cx, 2, 2), [0, 0, 0, 0]);
    }

    #[test]
    fn path_points_use_the_matrix_at_the_time_they_are_added() {
        let mut cx = context(40, 40);
        cx.set_source_rgba(0.0, 1.0, 0.0, 1.0);
        cx.save();
        cx.translate(20.0, 20.0);
        cx.rectangle(0.0, 0.0, 10.0, 10.0);
        cx.restore();
        cx.fill(); // painted after restore, but the rectangle stays where it was built
        assert_eq!(pixel(&cx, 25, 25)[1], 255);
        assert_eq!(pixel(&cx, 5, 5)[3], 0);
    }

    #[test]
    fn clip_limits_painting_and_restore_lifts_it() {
        let mut cx = context(20, 20);
        cx.set_source_rgba(0.0, 0.0, 1.0, 1.0);
        cx.save();
        cx.rectangle(0.0, 0.0, 10.0, 20.0);
        cx.clip();
        cx.paint();
        cx.restore();
        assert_eq!(pixel(&cx, 5, 10)[2], 255);
        assert_eq!(pixel(&cx, 15, 10)[3], 0);
        cx.paint();
        assert_eq!(pixel(&cx, 15, 10)[2], 255);
    }

    #[test]
    fn gradient_keeps_the_matrix_of_set_source() {
        let mut cx = context(20, 20);
        cx.scale(2.0, 2.0);
        let g = Pattern::new(PatternKind::Linear { p0: (0.0, 0.0), p1: (0.0, 10.0), stops: vec![] });
        g.borrow_mut().add_stop(0.0, [1.0, 1.0, 1.0, 1.0]);
        g.borrow_mut().add_stop(1.0, [0.0, 0.0, 0.0, 1.0]);
        cx.set_source(g);
        cx.identity_matrix();
        cx.rectangle(0.0, 0.0, 20.0, 20.0);
        cx.fill();
        let top = pixel(&cx, 5, 1)[0];
        let bottom = pixel(&cx, 5, 18)[0];
        assert!(top > 200 && bottom < 40, "{top} {bottom}");
    }

    #[test]
    fn arc_strokes_a_ring() {
        let mut cx = context(40, 40);
        cx.set_source_rgba(1.0, 1.0, 1.0, 1.0);
        cx.set_line_width(4.0);
        cx.arc(20.0, 20.0, 12.0, 0.0, 2.0 * PI);
        cx.stroke();
        assert_eq!(pixel(&cx, 32, 20)[3], 255);
        assert_eq!(pixel(&cx, 20, 20)[3], 0);
    }

    #[test]
    fn matrix_inverse_round_trips() {
        let mut m = Matrix::IDENTITY;
        m.translate(10.0, 5.0);
        m.scale(2.0, 3.0);
        m.rotate(0.7);
        let (x, y) = m.apply(3.0, 4.0);
        let (bx, by) = m.invert().unwrap().apply(x, y);
        assert!((bx - 3.0).abs() < 1e-9 && (by - 4.0).abs() < 1e-9);
    }
}
