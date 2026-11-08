//! The look of the gallery: one small design system, so every page lines up the same way.
//!
//! * a page is a centred column; a *section* is a title above a rounded *card*;
//! * a card is a list of *rows*: the title (and its hint) on the left, the control on the right, always the
//!   same split, and the control fills the right column, so the right edges line up down the whole card;
//! * accent colour for the one primary action and for what is selected, red only for removing things.
use std::time::Duration;

use eframe::egui::{self, Align, Color32, FontId, Layout, RichText, Sense, Stroke, Vec2};
use serde_json::{json, Value};

use crate::core::catalog::{self, OptionDef, ACCENTS};
use crate::core::i18n::{loc, tr, tr_owned};

pub const ACCENT: Color32 = Color32::from_rgb(0xBF, 0x5A, 0xF2);
pub const DANGER: Color32 = Color32::from_rgb(0xFF, 0x45, 0x3A);
const ROW_HEIGHT: f32 = 38.0;
const COLUMN_GAP: f32 = 16.0;

// ---------------------------------------------------------------------------------------------
// colours that follow the light/dark theme
pub fn card_fill(ui: &egui::Ui) -> Color32 {
    let v = ui.visuals();
    if v.dark_mode {
        v.panel_fill.lerp_to_gamma(Color32::WHITE, 0.055)
    } else {
        v.panel_fill.lerp_to_gamma(Color32::BLACK, 0.035)
    }
}

pub fn line_color(ui: &egui::Ui) -> Color32 {
    let v = ui.visuals();
    if v.dark_mode {
        Color32::from_white_alpha(18)
    } else {
        Color32::from_black_alpha(24)
    }
}

pub fn tile_fill(ui: &egui::Ui) -> Color32 {
    if ui.visuals().dark_mode {
        Color32::from_rgb(0x2E, 0x2A, 0x3C)
    } else {
        Color32::from_rgb(0xE6, 0xE0, 0xF2)
    }
}

pub fn style(ctx: &egui::Context) {
    use egui::{FontFamily, TextStyle};
    ctx.style_mut(|style| {
        style.spacing.item_spacing = Vec2::new(10.0, 8.0);
        style.spacing.button_padding = Vec2::new(14.0, 6.0);
        style.spacing.interact_size.y = 30.0;
        style.spacing.combo_width = 120.0;
        style.text_styles = [
            (TextStyle::Heading, FontId::new(22.0, FontFamily::Proportional)),
            (TextStyle::Body, FontId::new(14.0, FontFamily::Proportional)),
            (TextStyle::Button, FontId::new(14.0, FontFamily::Proportional)),
            (TextStyle::Small, FontId::new(12.0, FontFamily::Proportional)),
            (TextStyle::Monospace, FontId::new(13.0, FontFamily::Monospace)),
        ]
        .into();
        let radius = 9.0;
        for widget in [
            &mut style.visuals.widgets.noninteractive,
            &mut style.visuals.widgets.inactive,
            &mut style.visuals.widgets.hovered,
            &mut style.visuals.widgets.active,
            &mut style.visuals.widgets.open,
        ] {
            widget.corner_radius = radius.into();
        }
        style.visuals.window_corner_radius = 16.0.into();
        if style.visuals.dark_mode {
            style.visuals.extreme_bg_color = Color32::from_gray(21);
        }
        style.visuals.menu_corner_radius = 12.0.into();
        style.visuals.selection.bg_fill = ACCENT.gamma_multiply(0.75);
        style.visuals.hyperlink_color = ACCENT;
        style.visuals.widgets.hovered.bg_stroke = Stroke::new(1.0_f32, ACCENT.gamma_multiply(0.8));
    });
}

// ---------------------------------------------------------------------------------------------
// layout
/// A column of at most `max` points in the middle of the page; its content is left-aligned.
pub fn column(ui: &mut egui::Ui, max: f32, content: impl FnOnce(&mut egui::Ui)) {
    let width = (ui.available_width() - 12.0).min(max); // room for the scroll bar
    ui.vertical_centered(|ui| {
        ui.allocate_ui_with_layout(Vec2::new(width, 0.0), Layout::top_down(Align::Min), |ui| {
            ui.set_width(width);
            content(ui);
        });
    });
}

/// A title above a card with rows. `note` is a small line under the title.
pub fn section(ui: &mut egui::Ui, title: &str, note: Option<&str>, content: impl FnOnce(&mut egui::Ui, &mut Rows)) {
    ui.add_space(14.0);
    ui.label(RichText::new(title).size(16.0).strong());
    if let Some(note) = note {
        ui.label(RichText::new(note).weak().small());
    }
    ui.add_space(2.0);
    egui::Frame::new()
        .fill(card_fill(ui))
        .stroke(Stroke::new(1.0_f32, line_color(ui)))
        .corner_radius(14.0)
        .inner_margin(egui::Margin::symmetric(16, 4))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.spacing_mut().item_spacing.y = 0.0;
            content(ui, &mut Rows { first: true });
        });
}

/// The rows of one card: keeps count so thin lines go between them, not above the first.
pub struct Rows {
    first: bool,
}

impl Rows {
    fn divider(&mut self, ui: &mut egui::Ui) {
        if !std::mem::replace(&mut self.first, false) {
            let (rect, _) = ui.allocate_exact_size(Vec2::new(ui.available_width(), 1.0), Sense::hover());
            ui.painter().hline(rect.x_range(), rect.center().y, Stroke::new(1.0_f32, line_color(ui)));
        }
    }

    /// Title (and hint) on the left, the control on the right filling the rest of the row.
    pub fn row(&mut self, ui: &mut egui::Ui, title: &str, hint: &str, control: impl FnOnce(&mut egui::Ui)) {
        self.divider(ui);
        let width = ui.available_width();
        let label_width = (width * 0.38).clamp(130.0, 240.0);
        ui.add_space(4.0);
        ui.allocate_ui_with_layout(Vec2::new(width, ROW_HEIGHT), Layout::left_to_right(Align::Center), |ui| {
            ui.spacing_mut().item_spacing.x = COLUMN_GAP;
            // the box is exactly as tall as its text, so the row centres it
            let lines = ((hint.chars().count() as f32 * 5.6) / label_width).ceil().clamp(1.0, 3.0);
            let text_height = 17.0 + if hint.is_empty() { 0.0 } else { 2.0 + 14.0 * lines };
            ui.allocate_ui_with_layout(Vec2::new(label_width, text_height), Layout::top_down(Align::Min), |ui| {
                ui.set_width(label_width);
                ui.spacing_mut().item_spacing.y = 1.0;
                ui.label(RichText::new(title).size(14.0));
                if !hint.is_empty() {
                    ui.add(egui::Label::new(RichText::new(hint).weak().size(11.5)).wrap());
                }
            });
            let rest = ui.available_width();
            ui.allocate_ui_with_layout(Vec2::new(rest, ROW_HEIGHT), Layout::left_to_right(Align::Center), |ui| {
                ui.set_width(rest);
                control(ui);
            });
        });
        ui.add_space(4.0);
    }

    /// A row whose control needs the whole width under its title (a text box of several lines).
    pub fn block(&mut self, ui: &mut egui::Ui, title: &str, hint: &str, control: impl FnOnce(&mut egui::Ui)) {
        self.divider(ui);
        ui.add_space(10.0);
        ui.label(RichText::new(title).size(14.0));
        if !hint.is_empty() {
            ui.label(RichText::new(hint).weak().size(11.5));
        }
        ui.add_space(6.0);
        control(ui);
        ui.add_space(12.0);
    }
}

// ---------------------------------------------------------------------------------------------
// controls
/// An on/off switch.
pub fn toggle(ui: &mut egui::Ui, on: &mut bool) -> egui::Response {
    let (rect, mut response) = ui.allocate_exact_size(Vec2::new(40.0, 22.0), Sense::click());
    if response.clicked() {
        *on = !*on;
        response.mark_changed();
    }
    if ui.is_rect_visible(rect) {
        let t = ui.ctx().animate_bool_responsive(response.id, *on);
        let off = if ui.visuals().dark_mode { Color32::from_gray(78) } else { Color32::from_gray(190) };
        let radius = rect.height() / 2.0;
        ui.painter().rect_filled(rect, radius, off.lerp_to_gamma(ACCENT, t));
        let x = egui::lerp((rect.left() + radius)..=(rect.right() - radius), t);
        ui.painter().circle_filled(egui::pos2(x, rect.center().y), radius - 3.0, Color32::WHITE);
    }
    response
}

pub fn primary_button(ui: &mut egui::Ui, text: &str) -> egui::Response {
    ui.add(egui::Button::new(RichText::new(text).color(Color32::WHITE).strong()).fill(ACCENT).corner_radius(9.0))
}

pub fn danger_button(ui: &mut egui::Ui, text: &str) -> egui::Response {
    ui.add(egui::Button::new(RichText::new(text).color(DANGER)).fill(DANGER.gamma_multiply(0.12)).stroke(Stroke::new(1.0_f32, DANGER.gamma_multiply(0.5))))
}

/// A tab as a pill: filled with the accent colour when it is the current page.
pub fn tab(ui: &mut egui::Ui, text: &str, selected: bool) -> egui::Response {
    let color = if selected { Color32::WHITE } else { ui.visuals().text_color() };
    let galley = ui.painter().layout_no_wrap(text.to_string(), FontId::proportional(14.5), color);
    let size = galley.size() + Vec2::new(28.0, 14.0);
    let (rect, response) = ui.allocate_exact_size(size, Sense::click());
    let fill = if selected {
        ACCENT
    } else if response.hovered() {
        ui.visuals().widgets.hovered.weak_bg_fill
    } else {
        Color32::TRANSPARENT
    };
    ui.painter().rect_filled(rect, size.y / 2.0, fill);
    ui.painter().galley(rect.center() - galley.size() / 2.0, galley, color);
    response
}

/// A round colour choice; the current one gets a ring.
pub fn swatch(ui: &mut egui::Ui, key: &str, color: Option<&str>, selected: bool) -> egui::Response {
    let (rect, response) = ui.allocate_exact_size(Vec2::splat(20.0), Sense::click());
    let painter = ui.painter();
    let center = rect.center();
    match color {
        Some(hex) => {
            painter.circle_filled(center, 8.0, parse_hex(hex));
        }
        None => {
            // "as designed": a few colours in one
            for (i, c) in [0xFF453A_u32, 0xFFD60A, 0x30D158, 0x0A84FF, 0xBF5AF2].into_iter().enumerate() {
                let angle = i as f32 / 5.0 * std::f32::consts::TAU;
                painter.circle_filled(center + Vec2::angled(angle) * 4.0, 4.0, Color32::from_rgb((c >> 16) as u8, (c >> 8) as u8, c as u8));
            }
        }
    }
    if selected {
        painter.circle_stroke(center, 9.8, Stroke::new(2.0_f32, ui.visuals().strong_text_color()));
    } else if response.hovered() {
        painter.circle_stroke(center, 9.8, Stroke::new(1.5_f32, ui.visuals().weak_text_color()));
    }
    response.on_hover_text(catalog::accent_name(key))
}

fn parse_hex(hex: &str) -> Color32 {
    let v = u32::from_str_radix(hex.trim_start_matches('#'), 16).unwrap_or(0x888888);
    Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

/// The colour choices of a widget, spread over the control column; returns the key that was clicked.
pub fn accent_picker(ui: &mut egui::Ui, current: &str) -> Option<&'static str> {
    let mut picked = None;
    let count = ACCENTS.len() as f32;
    let size = 20.0;
    let gap = ((ui.available_width() - count * size - 6.0) / (count - 1.0)).clamp(2.0, 9.0);
    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing = Vec2::new(gap, gap);
        for (key, color) in ACCENTS {
            if swatch(ui, key, color, key == current).clicked() {
                picked = Some(key);
            }
        }
    });
    picked
}

/// A drop-down that fills the control column.
pub fn dropdown<'a>(ui: &mut egui::Ui, id: impl std::hash::Hash, shown: &str, options: impl IntoIterator<Item = (&'a str, &'a str)>, current: &str) -> Option<String> {
    let mut chosen = None;
    egui::ComboBox::from_id_salt(id).width(ui.available_width()).selected_text(shown).show_ui(ui, |ui| {
        for (key, name) in options {
            if ui.selectable_label(key == current, name).clicked() {
                chosen = Some(key.to_string());
            }
        }
    });
    chosen
}

/// A text box that fills the control column.
pub fn text_box(ui: &mut egui::Ui, text: &mut String, hint: &str, invalid: bool) -> egui::Response {
    let edit = egui::TextEdit::singleline(text).desired_width(f32::INFINITY).hint_text(hint).margin(egui::Margin::symmetric(8, 6));
    let edit = if invalid { edit.text_color(DANGER) } else { edit };
    ui.add(edit)
}

/// A slider with a value box; together they fill the control column exactly.
pub fn full_slider<N: egui::emath::Numeric>(ui: &mut egui::Ui, value: &mut N, range: std::ops::RangeInclusive<N>, suffix: &str, step: f64) -> egui::Response {
    const BOX_WIDTH: f32 = 78.0;
    let gap = 12.0;
    ui.spacing_mut().item_spacing.x = gap;
    ui.spacing_mut().slider_width = (ui.available_width() - BOX_WIDTH - gap).max(60.0);
    let mut response = ui.add(egui::Slider::new(value, range.clone()).show_value(false).step_by(step));
    let boxed = ui.add_sized([BOX_WIDTH, 28.0], egui::DragValue::new(value).range(range).suffix(suffix).speed(step.max(0.01)));
    if boxed.changed() {
        response.mark_changed();
    }
    response
}

// ---------------------------------------------------------------------------------------------
/// The control of one widget option inside a card; returns the change to send, if any.
pub fn option_row(ui: &mut egui::Ui, rows: &mut Rows, opt: &OptionDef, value: &Value) -> Option<(Value, Duration)> {
    let key = opt.key.as_str();
    let hint = opt.hint_text();
    let title = opt.title();
    let mut out: Option<(Value, Duration)> = None;
    let text_of = |v: &Value| match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    match opt.kind.as_str() {
        "bool" => {
            let mut on = matches!(value, Value::Bool(true)) || matches!(value.as_str(), Some("true") | Some("1")) || value.as_i64() == Some(1);
            let before = on;
            rows.row(ui, &title, &hint, |ui| {
                toggle(ui, &mut on);
            });
            if on != before {
                out = Some((json!({"options": {key: on}}), Duration::ZERO));
            }
        }
        "text" | "date" => {
            let id = ui.make_persistent_id(("option", key));
            let mut text: String = ui.data_mut(|d| d.get_temp::<String>(id)).unwrap_or_else(|| text_of(value));
            let before = text.clone();
            let is_date = opt.kind == "date";
            let valid = !is_date || text.trim().is_empty() || opt.coerce(&Value::String(text.trim().to_string())).is_ok();
            rows.row(ui, &title, &hint, |ui| {
                text_box(ui, &mut text, if is_date { "YYYY-MM-DD" } else { "" }, !valid);
            });
            if text != before {
                ui.data_mut(|d| d.insert_temp(id, text.clone()));
                if !is_date || text.trim().is_empty() || opt.coerce(&Value::String(text.trim().to_string())).is_ok() {
                    out = Some((json!({"options": {key: text.trim()}}), Duration::from_millis(700)));
                }
            }
        }
        "longtext" => {
            let id = ui.make_persistent_id(("option", key));
            let mut text: String = ui.data_mut(|d| d.get_temp::<String>(id)).unwrap_or_else(|| text_of(value));
            let before = text.clone();
            rows.block(ui, &title, &hint, |ui| {
                ui.add(egui::TextEdit::multiline(&mut text).desired_rows(5).desired_width(f32::INFINITY).margin(egui::Margin::symmetric(8, 6)));
            });
            if text != before {
                ui.data_mut(|d| d.insert_temp(id, text.clone()));
                out = Some((json!({"options": {key: text}}), Duration::from_millis(700)));
            }
        }
        "number" => {
            let whole = opt.step.fract() == 0.0;
            let mut number = value.as_f64().or_else(|| value.as_str().and_then(|s| s.parse().ok())).unwrap_or(opt.min);
            let before = number;
            rows.row(ui, &title, &hint, |ui| {
                full_slider(ui, &mut number, opt.min..=opt.max, "", opt.step.max(if whole { 1.0 } else { 0.01 }));
            });
            if number != before {
                let v = if whole { json!(number.round() as i64) } else { json!(number) };
                out = Some((json!({"options": {key: v}}), Duration::from_millis(500)));
            }
        }
        "choice" => {
            let current = text_of(value);
            let labels: Vec<(String, String)> = opt.choices.iter().map(|(v, l)| (v.clone(), { let t = loc(l, None); if t.is_empty() { v.clone() } else { t } })).collect();
            let mut chosen = None;
            rows.row(ui, &title, &hint, |ui| {
                chosen = dropdown(ui, ("option", key), &opt.choice_label(&current), labels.iter().map(|(a, b)| (a.as_str(), b.as_str())), &current);
            });
            if let Some(c) = chosen {
                out = Some((json!({"options": {key: c}}), Duration::ZERO));
            }
        }
        "multi" => {
            let selected: Vec<String> = text_of(value).split(',').filter(|s| !s.is_empty()).map(str::to_string).collect();
            let mut now = selected.clone();
            let names: Vec<String> = opt.choices.iter().filter(|(v, _)| selected.contains(v)).map(|(_, l)| loc(l, None).split(" — ").next().unwrap_or("").to_string()).collect();
            let note = if opt.limit > 0 && names.len() as i64 > opt.limit {
                tr_owned(format!(" · показуються перші {}", opt.limit), format!(" · the first {} are shown", opt.limit))
            } else {
                String::new()
            };
            let summary = if names.is_empty() { tr("нічого не вибрано", "nothing selected").to_string() } else { format!("{}{}", names.join(", "), note) };
            rows.row(ui, &title, &hint, |ui| {
                egui::ComboBox::from_id_salt(("multi", key))
                    .width(ui.available_width())
                    .selected_text(summary)
                    .close_behavior(egui::PopupCloseBehavior::CloseOnClickOutside)
                    .show_ui(ui, |ui| {
                        for (v, label) in &opt.choices {
                            let mut on = now.contains(v);
                            if ui.checkbox(&mut on, loc(label, None)).changed() {
                                if on {
                                    now.push(v.clone());
                                } else {
                                    now.retain(|x| x != v);
                                }
                            }
                        }
                    });
            });
            if now != selected {
                let ordered: Vec<&str> = opt.choices.iter().map(|(v, _)| v.as_str()).filter(|v| now.iter().any(|n| n == v)).collect();
                out = Some((json!({"options": {key: ordered.join(",")}}), Duration::ZERO));
            }
        }
        "file" => {
            let current = text_of(value);
            rows.row(ui, &title, &hint, |ui| {
                let total = ui.available_width();
                let buttons = if current.is_empty() { 108.0 } else { 176.0 };
                ui.spacing_mut().item_spacing.x = 8.0;
                ui.allocate_ui_with_layout(Vec2::new((total - buttons).max(40.0), ROW_HEIGHT), Layout::left_to_right(Align::Center), |ui| {
                    let name = if current.is_empty() {
                        tr("не вибрано", "none").to_string()
                    } else {
                        std::path::Path::new(&current).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| current.clone())
                    };
                    ui.add(egui::Label::new(RichText::new(name).weak()).truncate());
                });
                if ui.button(tr("Обрати…", "Choose…")).clicked() {
                    let picked = rfd::FileDialog::new()
                        .set_title(&title)
                        .add_filter(tr("Зображення", "Images"), &["png", "jpg", "jpeg", "webp", "gif", "bmp"])
                        .pick_file();
                    if let Some(path) = picked {
                        out = Some((json!({"options": {key: path.to_string_lossy()}}), Duration::ZERO));
                    }
                }
                if !current.is_empty() && ui.button(tr("Прибрати", "Clear")).clicked() {
                    out = Some((json!({"options": {key: ""}}), Duration::ZERO));
                }
            });
        }
        _ => {}
    }
    out
}
