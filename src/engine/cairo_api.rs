//! The Cairo API that widgets see: `cairo_arc(cr, ...)`, `CAIRO_LINE_CAP_ROUND`, ...
//!
//! Widgets were written against conky's Cairo bindings, so the same C-style names are provided here on
//! top of `canvas::Context`. Objects (contexts, surfaces, patterns) are Lua userdata; the destroy
//! functions do nothing because the garbage collector owns them.
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::Arc;

use mlua::{AnyUserData, Lua, Result as LuaResult, Table, UserData, UserDataRef, Value, Variadic};

use super::canvas::{filter_quality, new_surface, spread_mode, Context, Pattern, PatternKind, PatternRef, Surface};
use super::fonts::FontMap;
use super::images;

#[derive(Clone)]
pub struct Cr(pub Rc<RefCell<Context>>);
impl UserData for Cr {}

#[derive(Clone)]
pub struct Surf(pub Surface);
impl UserData for Surf {}

#[derive(Clone)]
pub struct Pat(pub PatternRef);
impl UserData for Pat {}

#[derive(Clone)]
pub struct FontOpts;
impl UserData for FontOpts {}

const CONSTANTS: &[(&str, i64)] = &[
    ("FORMAT_ARGB32", 0), ("FORMAT_RGB24", 1), ("FORMAT_A8", 2), ("FORMAT_A1", 3),
    ("FONT_SLANT_NORMAL", 0), ("FONT_SLANT_ITALIC", 1), ("FONT_SLANT_OBLIQUE", 2),
    ("FONT_WEIGHT_NORMAL", 0), ("FONT_WEIGHT_BOLD", 1),
    ("LINE_CAP_BUTT", 0), ("LINE_CAP_ROUND", 1), ("LINE_CAP_SQUARE", 2),
    ("LINE_JOIN_MITER", 0), ("LINE_JOIN_ROUND", 1), ("LINE_JOIN_BEVEL", 2),
    ("ANTIALIAS_DEFAULT", 0), ("ANTIALIAS_NONE", 1), ("ANTIALIAS_GRAY", 2), ("ANTIALIAS_SUBPIXEL", 3),
    ("ANTIALIAS_FAST", 4), ("ANTIALIAS_GOOD", 5), ("ANTIALIAS_BEST", 6),
    ("HINT_STYLE_DEFAULT", 0), ("HINT_STYLE_NONE", 1), ("HINT_STYLE_SLIGHT", 2), ("HINT_STYLE_MEDIUM", 3), ("HINT_STYLE_FULL", 4),
    ("HINT_METRICS_DEFAULT", 0), ("HINT_METRICS_OFF", 1), ("HINT_METRICS_ON", 2),
    ("FILTER_FAST", 0), ("FILTER_GOOD", 1), ("FILTER_BEST", 2), ("FILTER_NEAREST", 3), ("FILTER_BILINEAR", 4), ("FILTER_GAUSSIAN", 5),
    ("EXTEND_NONE", 0), ("EXTEND_REPEAT", 1), ("EXTEND_REFLECT", 2), ("EXTEND_PAD", 3),
    ("OPERATOR_CLEAR", 0), ("OPERATOR_SOURCE", 1), ("OPERATOR_OVER", 2), ("OPERATOR_IN", 3), ("OPERATOR_OUT", 4),
    ("OPERATOR_ATOP", 5), ("OPERATOR_DEST", 6), ("OPERATOR_DEST_OVER", 7), ("OPERATOR_DEST_IN", 8),
    ("OPERATOR_DEST_OUT", 9), ("OPERATOR_DEST_ATOP", 10), ("OPERATOR_XOR", 11), ("OPERATOR_ADD", 12),
    ("OPERATOR_SATURATE", 13), ("OPERATOR_MULTIPLY", 14), ("OPERATOR_SCREEN", 15), ("OPERATOR_OVERLAY", 16),
    ("OPERATOR_DARKEN", 17), ("OPERATOR_LIGHTEN", 18),
    ("FILL_RULE_WINDING", 0), ("FILL_RULE_EVEN_ODD", 1),
    ("SUBPIXEL_ORDER_DEFAULT", 0), ("SUBPIXEL_ORDER_RGB", 1), ("SUBPIXEL_ORDER_BGR", 2),
    ("SUBPIXEL_ORDER_VRGB", 3), ("SUBPIXEL_ORDER_VBGR", 4),
    ("CONTENT_COLOR", 0x1000), ("CONTENT_ALPHA", 0x2000), ("CONTENT_COLOR_ALPHA", 0x3000),
];

macro_rules! cr_fn {
    ($lua:expr, $g:expr, $name:literal, |$cx:ident $(, $a:ident : $t:ty)*| $body:expr) => {
        $g.set(
            $name,
            $lua.create_function(|_, (cr, $($a,)*): (UserDataRef<Cr>, $($t,)*)| {
                #[allow(unused_mut)]
                let mut $cx = cr.0.borrow_mut();
                #[allow(clippy::unused_unit)]
                Ok($body)
            })?,
        )?;
    };
}

macro_rules! any_fn {
    ($lua:expr, $g:expr, $name:literal, |$($a:ident : $t:ty),*| $body:expr) => {
        $g.set($name, $lua.create_function(|_, ($($a,)*): ($($t,)*)| Ok($body))?)?;
    };
}

fn text(s: &mlua::String) -> String {
    s.to_string_lossy().to_string()
}

/// Installs the cairo_* functions and CAIRO_* constants into a Lua state.
pub fn install(lua: &Lua, fonts: Arc<FontMap>) -> LuaResult<()> {
    let g = lua.globals();
    for (name, value) in CONSTANTS {
        g.set(format!("CAIRO_{name}"), *value)?;
    }

    // state and transformations
    cr_fn!(lua, g, "cairo_save", |cx| cx.save());
    cr_fn!(lua, g, "cairo_restore", |cx| cx.restore());
    cr_fn!(lua, g, "cairo_translate", |cx, x: f64, y: f64| cx.translate(x, y));
    cr_fn!(lua, g, "cairo_scale", |cx, x: f64, y: f64| cx.scale(x, y));
    cr_fn!(lua, g, "cairo_rotate", |cx, a: f64| cx.rotate(a));
    cr_fn!(lua, g, "cairo_identity_matrix", |cx| cx.identity_matrix());

    // path
    cr_fn!(lua, g, "cairo_new_path", |cx| cx.new_path());
    cr_fn!(lua, g, "cairo_new_sub_path", |cx| cx.new_sub_path());
    cr_fn!(lua, g, "cairo_close_path", |cx| cx.close_path());
    cr_fn!(lua, g, "cairo_move_to", |cx, x: f64, y: f64| cx.move_to(x, y));
    cr_fn!(lua, g, "cairo_line_to", |cx, x: f64, y: f64| cx.line_to(x, y));
    cr_fn!(lua, g, "cairo_rel_move_to", |cx, x: f64, y: f64| cx.rel_move_to(x, y));
    cr_fn!(lua, g, "cairo_rel_line_to", |cx, x: f64, y: f64| cx.rel_line_to(x, y));
    cr_fn!(lua, g, "cairo_curve_to", |cx, a: f64, b: f64, c: f64, d: f64, e: f64, f: f64| cx.curve_to(a, b, c, d, e, f));
    cr_fn!(lua, g, "cairo_rel_curve_to", |cx, a: f64, b: f64, c: f64, d: f64, e: f64, f: f64| cx.rel_curve_to(a, b, c, d, e, f));
    cr_fn!(lua, g, "cairo_arc", |cx, x: f64, y: f64, r: f64, a: f64, b: f64| cx.arc(x, y, r, a, b));
    cr_fn!(lua, g, "cairo_arc_negative", |cx, x: f64, y: f64, r: f64, a: f64, b: f64| cx.arc_negative(x, y, r, a, b));
    cr_fn!(lua, g, "cairo_rectangle", |cx, x: f64, y: f64, w: f64, h: f64| cx.rectangle(x, y, w, h));
    cr_fn!(lua, g, "cairo_get_current_point", |cx| cx.current_point().unwrap_or((0.0, 0.0)));

    // painting
    cr_fn!(lua, g, "cairo_fill", |cx| cx.fill());
    cr_fn!(lua, g, "cairo_fill_preserve", |cx| cx.fill_preserve());
    cr_fn!(lua, g, "cairo_stroke", |cx| cx.stroke());
    cr_fn!(lua, g, "cairo_stroke_preserve", |cx| cx.stroke_preserve());
    cr_fn!(lua, g, "cairo_paint", |cx| cx.paint());
    cr_fn!(lua, g, "cairo_paint_with_alpha", |cx, a: f64| cx.paint_with_alpha(a));
    cr_fn!(lua, g, "cairo_clip", |cx| cx.clip());
    cr_fn!(lua, g, "cairo_clip_preserve", |cx| cx.clip_preserve());
    cr_fn!(lua, g, "cairo_reset_clip", |cx| cx.reset_clip());

    // sources and drawing settings
    cr_fn!(lua, g, "cairo_set_source_rgb", |cx, r: f64, gr: f64, b: f64| cx.set_source_rgba(r, gr, b, 1.0));
    cr_fn!(lua, g, "cairo_set_source_rgba", |cx, r: f64, gr: f64, b: f64, a: f64| cx.set_source_rgba(r, gr, b, a));
    cr_fn!(lua, g, "cairo_set_source", |cx, p: UserDataRef<Pat>| cx.set_source(p.0.clone()));
    cr_fn!(lua, g, "cairo_set_source_surface", |cx, s: UserDataRef<Surf>, x: f64, y: f64| cx.set_source_surface(s.0.clone(), x, y));
    cr_fn!(lua, g, "cairo_get_source", |cx| Pat(cx.get_source()));
    cr_fn!(lua, g, "cairo_set_line_width", |cx, w: f64| cx.set_line_width(w));
    cr_fn!(lua, g, "cairo_set_line_cap", |cx, v: i32| cx.set_line_cap(v));
    cr_fn!(lua, g, "cairo_set_line_join", |cx, v: i32| cx.set_line_join(v));
    cr_fn!(lua, g, "cairo_set_miter_limit", |cx, v: f64| cx.set_miter_limit(v));
    cr_fn!(lua, g, "cairo_set_fill_rule", |cx, v: i32| cx.set_fill_rule(v));
    cr_fn!(lua, g, "cairo_set_operator", |cx, v: i32| cx.set_operator(v));
    cr_fn!(lua, g, "cairo_set_antialias", |_cx, _v: i32| ());
    cr_fn!(lua, g, "cairo_set_tolerance", |_cx, _v: f64| ());
    cr_fn!(lua, g, "cairo_set_font_options", |_cx, _v: Value| ());
    g.set(
        "cairo_set_dash",
        lua.create_function(|_, (cr, dashes, a, b): (UserDataRef<Cr>, Table, Option<Value>, Option<f64>)| {
            let list: Vec<f64> = dashes.sequence_values::<f64>().filter_map(|v| v.ok()).collect();
            // C signature: (cr, dashes, num_dashes, offset); pycairo: (dashes, offset)
            let offset = match (&a, b) {
                (_, Some(b)) => b,
                (Some(Value::Number(n)), None) => *n,
                (Some(Value::Integer(n)), None) => *n as f64,
                _ => 0.0,
            };
            cr.0.borrow_mut().set_dash(list, offset);
            Ok(())
        })?,
    )?;

    // text
    cr_fn!(lua, g, "cairo_select_font_face", |cx, family: mlua::String, slant: i32, weight: i32| cx.select_font_face(&text(&family), slant, weight));
    cr_fn!(lua, g, "cairo_set_font_size", |cx, size: f64| cx.set_font_size(size));
    cr_fn!(lua, g, "cairo_show_text", |cx, s: mlua::String| cx.show_text(&text(&s)));
    cr_fn!(lua, g, "cairo_text_path", |cx, s: mlua::String| cx.text_path(&text(&s)));
    g.set(
        "cairo_text_extents",
        lua.create_function(|_, (cr, s, out): (UserDataRef<Cr>, mlua::String, Table)| {
            let e = cr.0.borrow().text_extents(&text(&s));
            out.set("x_bearing", e.x_bearing)?;
            out.set("y_bearing", e.y_bearing)?;
            out.set("width", e.width)?;
            out.set("height", e.height)?;
            out.set("x_advance", e.x_advance)?;
            out.set("y_advance", e.y_advance)?;
            Ok(())
        })?,
    )?;
    g.set(
        "cairo_font_extents",
        lua.create_function(|_, (cr, out): (UserDataRef<Cr>, Table)| {
            let e = cr.0.borrow().font_extents();
            out.set("ascent", e.ascent)?;
            out.set("descent", e.descent)?;
            out.set("height", e.height)?;
            out.set("max_x_advance", e.max_x_advance)?;
            out.set("max_y_advance", e.max_y_advance)?;
            Ok(())
        })?,
    )?;
    // `cairo_text_extents_t:create()` is conky's way to get a table the extents are written into
    for name in ["cairo_text_extents_t", "cairo_font_extents_t"] {
        let t = lua.create_table()?;
        t.set("create", lua.create_function(|lua, _: Variadic<Value>| lua.create_table())?)?;
        g.set(name, t)?;
    }

    // contexts and surfaces
    let for_context = fonts.clone();
    g.set(
        "cairo_create",
        lua.create_function(move |_, surface: UserDataRef<Surf>| {
            Ok(Cr(Rc::new(RefCell::new(Context::new(surface.0.clone(), for_context.clone())))))
        })?,
    )?;
    g.set(
        "cairo_image_surface_create",
        lua.create_function(|_, (_format, w, h): (i32, f64, f64)| {
            new_surface(w.max(1.0) as u32, h.max(1.0) as u32).map(Surf).ok_or_else(|| mlua::Error::runtime("surface is too large"))
        })?,
    )?;
    g.set(
        "cairo_image_surface_create_from_png",
        lua.create_function(|_, path: mlua::String| {
            Ok(images::load_surface(std::path::Path::new(&text(&path)), u32::MAX).map(Surf))
        })?,
    )?;
    any_fn!(lua, g, "cairo_image_surface_get_width", |s: Option<UserDataRef<Surf>>| s.map(|s| s.0.borrow().width()).unwrap_or(0));
    any_fn!(lua, g, "cairo_image_surface_get_height", |s: Option<UserDataRef<Surf>>| s.map(|s| s.0.borrow().height()).unwrap_or(0));
    any_fn!(lua, g, "cairo_surface_write_to_png", |s: UserDataRef<Surf>, path: mlua::String| s.0.borrow().save_png(text(&path)).is_ok());
    for name in ["cairo_surface_flush", "cairo_surface_finish", "cairo_surface_mark_dirty"] {
        g.set(name, lua.create_function(|_, _: Variadic<Value>| Ok(()))?)?;
    }
    for name in ["cairo_destroy", "cairo_surface_destroy", "cairo_pattern_destroy", "cairo_font_options_destroy"] {
        g.set(name, lua.create_function(|_, _: Variadic<Value>| Ok(()))?)?;
    }

    // patterns
    any_fn!(lua, g, "cairo_pattern_create_linear", |x0: f64, y0: f64, x1: f64, y1: f64| Pat(Pattern::new(PatternKind::Linear { p0: (x0, y0), p1: (x1, y1), stops: vec![] })));
    any_fn!(lua, g, "cairo_pattern_create_radial", |x0: f64, y0: f64, r0: f64, x1: f64, y1: f64, r1: f64| Pat(Pattern::new(PatternKind::Radial { c0: (x0, y0), r0, c1: (x1, y1), r1, stops: vec![] })));
    any_fn!(lua, g, "cairo_pattern_create_rgb", |r: f64, gr: f64, b: f64| Pat(Pattern::solid(r, gr, b, 1.0)));
    g.set(
        "cairo_pattern_create_rgba",
        lua.create_function(|_, (r, gr, b, a): (f64, f64, f64, Option<f64>)| Ok(Pat(Pattern::solid(r, gr, b, a.unwrap_or(1.0)))))?,
    )?;
    any_fn!(lua, g, "cairo_pattern_create_for_surface", |s: UserDataRef<Surf>| Pat(Pattern::new(PatternKind::Image { surface: s.0.clone(), ox: 0.0, oy: 0.0 })));
    any_fn!(lua, g, "cairo_pattern_add_color_stop_rgb", |p: UserDataRef<Pat>, o: f64, r: f64, gr: f64, b: f64| p.0.borrow_mut().add_stop(o, [r as f32, gr as f32, b as f32, 1.0]));
    any_fn!(lua, g, "cairo_pattern_add_color_stop_rgba", |p: UserDataRef<Pat>, o: f64, r: f64, gr: f64, b: f64, a: f64| p.0.borrow_mut().add_stop(o, [r as f32, gr as f32, b as f32, a as f32]));
    any_fn!(lua, g, "cairo_pattern_set_filter", |p: UserDataRef<Pat>, v: i32| p.0.borrow_mut().filter = filter_quality(v));
    any_fn!(lua, g, "cairo_pattern_set_extend", |p: UserDataRef<Pat>, v: i32| p.0.borrow_mut().extend = spread_mode(v));

    // font options are accepted and ignored: the rasteriser always uses grayscale anti-aliasing
    g.set("cairo_font_options_create", lua.create_function(|_, ()| Ok(FontOpts))?)?;
    for name in [
        "cairo_font_options_set_antialias",
        "cairo_font_options_set_hint_style",
        "cairo_font_options_set_hint_metrics",
        "cairo_font_options_set_subpixel_order",
    ] {
        g.set(name, lua.create_function(|_, _: Variadic<Value>| Ok(()))?)?;
    }
    let _: Option<AnyUserData> = None;
    Ok(())
}
