//! The gallery window.
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant, SystemTime};

use eframe::egui::{self, Align, Layout, RichText, Sense, Stroke, Vec2};
use serde_json::{json, Map, Value};

use super::ui;
use super::worker::{spawn_ipc, spawn_renderer, Done, Ipc, IpcCall, Job, Renderer};
use crate::core::catalog::{self, Types, WidgetType, CATEGORIES};
use crate::core::i18n::{set_lang, tr, tr_owned};
use crate::core::layout::{read_instances, Instance};
use crate::core::settings::{self, Settings};
use crate::core::{paths, scaffold};
use crate::engine::hostapi::open_url;
use crate::engine::render::Options;
use crate::platform::integration;
use crate::VERSION;


#[derive(Clone, Copy, PartialEq)]
enum Page {
    Mine,
    Add,
    Prefs,
}

/// The settings of one widget, with the changes that have not reached the service yet.
struct WidgetView {
    inst: Instance,
    wtype: WidgetType,
    pending: Map<String, Value>,
    flush_at: Option<Instant>,
}

enum Action {
    Add(String),
    Remove(String),
    OpenSettings(String),
    CloseSettings,
    Call(&'static str),
    Thumbnails,
    About,
    CreateWidget,
    OpenFolder,
}

pub struct Gallery {
    types: Types,
    instances: Vec<Instance>,
    layout_stamp: Option<SystemTime>,
    settings: Settings,
    page: Page,
    only_settings: bool,
    textures: HashMap<String, egui::TextureHandle>,
    requested: HashSet<String>,
    renderer: Renderer,
    ipc: Ipc,
    toasts: Vec<(String, Instant)>,
    view: Option<WidgetView>,
    new_widget: String,
    show_about: bool,
    last_poll: Instant,
    catalog_stamp: Option<SystemTime>,
    settings_apply_at: Option<Instant>,
    lang: String,
    families: Vec<String>,
    font_filter: String,
}

pub fn run(configure: Option<&str>, page: Option<&str>) -> i32 {
    let configure = configure.map(str::to_string);
    let page = page.map(str::to_string);
    let only_settings = configure.is_some();
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(if only_settings { tr("Налаштування віджета", "Widget settings") } else { tr("Віджети", "Widgets") })
        .with_app_id(paths::GALLERY_ID)
        .with_inner_size(if only_settings { [620.0, 780.0] } else { [1020.0, 820.0] })
        .with_min_inner_size([480.0, 420.0]);
    if let Ok(img) = image::open(paths::icon_png()) {
        let rgba = img.resize(128, 128, image::imageops::FilterType::Lanczos3).to_rgba8();
        viewport = viewport.with_icon(egui::IconData { width: rgba.width(), height: rgba.height(), rgba: rgba.into_raw() });
    }
    let options = eframe::NativeOptions { viewport, ..Default::default() };
    let result = eframe::run_native(
        "DeskWidgets",
        options,
        Box::new(move |cc| Ok(Box::new(Gallery::new(cc, configure.clone(), page.as_deref())))),
    );
    match result {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("deskwidgets: the gallery cannot open a window: {e}");
            1
        }
    }
}

impl Gallery {
    fn new(cc: &eframe::CreationContext<'_>, configure: Option<String>, page: Option<&str>) -> Gallery {
        paths::ensure_dirs();
        let mut settings = settings::load();
        set_lang(&settings::language(&settings));
        first_run(&mut settings);
        ui::style(&cc.egui_ctx);
        let types: Types = std::sync::Arc::new(catalog::discover().iter().filter(|(_, t)| t.supported()).map(|(k, v)| (k.clone(), v.clone())).collect());
        let renderer = spawn_renderer(cc.egui_ctx.clone(), settings.font.clone());
        let ipc = spawn_ipc(cc.egui_ctx.clone());
        let mut gallery = Gallery {
            types,
            instances: Vec::new(),
            layout_stamp: None,
            lang: settings::language(&settings),
            settings,
            page: match page {
                Some("add") => Page::Add,
                Some("prefs") => Page::Prefs,
                _ => Page::Mine,
            },
            only_settings: configure.is_some(),
            textures: HashMap::new(),
            requested: HashSet::new(),
            renderer,
            ipc,
            toasts: Vec::new(),
            view: None,
            new_widget: String::new(),
            show_about: false,
            last_poll: Instant::now(),
            catalog_stamp: catalog_stamp(),
            settings_apply_at: None,
            families: Vec::new(),
            font_filter: String::new(),
        };
        gallery.reload_layout();
        if let Some(id) = configure {
            gallery.open_settings(&id);
            if gallery.view.is_none() {
                std::process::exit(0); // no such widget on the desktop
            }
        }
        gallery.request_thumbnails(None);
        gallery
    }

    // data
    fn reload_layout(&mut self) {
        self.layout_stamp = std::fs::metadata(paths::layout()).and_then(|m| m.modified()).ok();
        self.instances = read_instances();
        if self.view.is_some() && !self.instances.is_empty() {
            let id = self.view.as_ref().unwrap().inst.id.clone();
            if !self.instances.iter().any(|i| i.id == id) && self.view.as_ref().unwrap().pending.is_empty() {
                // removed from elsewhere (the CLI): the page has nothing to show any more
                self.close_view();
            }
        }
    }

    fn poll(&mut self) {
        let stamp = std::fs::metadata(paths::layout()).and_then(|m| m.modified()).ok();
        if stamp != self.layout_stamp {
            self.reload_layout();
        }
        let now = catalog_stamp();
        if now != self.catalog_stamp {
            self.catalog_stamp = now;
            let fresh: Types = std::sync::Arc::new(catalog::discover().iter().filter(|(_, t)| t.supported()).map(|(k, v)| (k.clone(), v.clone())).collect());
            let changed: Vec<String> = fresh.iter().filter(|(k, t)| self.types.get(*k) != Some(t)).map(|(k, _)| k.clone()).collect();
            self.types = fresh;
            if !changed.is_empty() {
                self.request_thumbnails(Some(&changed));
            }
        }
    }

    fn thumb_key(&self, type_id: &str) -> String {
        format!("thumb/{}/{}", self.lang, type_id)
    }

    fn request_thumbnails(&mut self, only: Option<&[String]>) {
        for (id, t) in self.types.iter() {
            if only.map(|o| !o.contains(id)).unwrap_or(false) {
                continue;
            }
            let key = self.thumb_key(id);
            self.requested.insert(key.clone());
            let opts = Options { lang: self.lang.clone(), ..Options::default() };
            let _ = self.renderer.jobs.send(Job::Render { key, wtype: Box::new(t.clone()), opts: Box::new(opts), settings: Box::new(self.settings.clone()) });
        }
    }

    fn request_preview(&self) {
        let Some(view) = &self.view else { return };
        let opts = Options {
            size: Some(view.inst.size.clone()),
            style: Some(view.inst.style.clone()),
            accent: Some(view.inst.accent.clone()),
            lang: self.lang.clone(),
            options: view.inst.options.clone(),
            ..Options::default()
        };
        let _ = self.renderer.jobs.send(Job::Render { key: "preview".into(), wtype: Box::new(view.wtype.clone()), opts: Box::new(opts), settings: Box::new(self.settings.clone()) });
    }

    fn receive(&mut self, ctx: &egui::Context) {
        while let Ok(Done { key, image }) = self.renderer.done.try_recv() {
            let handle = ctx.load_texture(key.clone(), image, egui::TextureOptions::LINEAR);
            self.textures.insert(key, handle);
        }
        while let Ok(answer) = self.ipc.answers.try_recv() {
            match answer.result {
                Err(e) => self.toast(e),
                Ok(_) if answer.command == "reload" => {
                    self.catalog_stamp = None; // look at the widget folders again
                }
                Ok(_) => {}
            }
        }
    }

    fn call(&self, command: &str, args: Value) {
        let _ = self.ipc.calls.send(IpcCall { command: command.to_string(), args });
    }

    fn toast(&mut self, text: String) {
        self.toasts.push((text, Instant::now()));
    }

    // actions
    fn add(&mut self, type_id: &str) {
        self.call("add", json!({"type": type_id}));
        let title = self.types.get(type_id).map(|t| t.title()).unwrap_or_default();
        self.toast(tr_owned(format!("«{title}» додано на робочий стіл"), format!("\"{title}\" added to the desktop")));
    }

    fn open_settings(&mut self, id: &str) {
        let Some(inst) = self.instances.iter().find(|i| i.id == id).cloned() else { return };
        let Some(t) = self.types.get(&inst.kind).cloned() else { return };
        self.view = Some(WidgetView { inst, wtype: t, pending: Map::new(), flush_at: None });
        self.request_preview();
    }

    fn close_view(&mut self) {
        self.flush_view();
        self.view = None;
        self.textures.remove("preview");
        if self.only_settings {
            std::process::exit(0);
        }
    }

    /// Changes from the settings page: `delay` collects quick edits (typing) into one request.
    fn send(&mut self, patch: Value, delay: Duration) {
        let Some(view) = self.view.as_mut() else { return };
        let Some(patch) = patch.as_object() else { return };
        for (key, value) in patch {
            if key == "options" {
                if let Some(options) = value.as_object() {
                    let slot = view.pending.entry("options").or_insert_with(|| json!({}));
                    for (k, v) in options {
                        slot[k] = v.clone();
                        view.inst.options.insert(k.clone(), v.clone());
                    }
                }
            } else {
                view.pending.insert(key.clone(), value.clone());
                match key.as_str() {
                    "size" => view.inst.size = value.as_str().unwrap_or("small").to_string(),
                    "style" => view.inst.style = value.as_str().unwrap_or("glass").to_string(),
                    "accent" => view.inst.accent = value.as_str().unwrap_or("auto").to_string(),
                    _ => {}
                }
            }
        }
        view.flush_at = Some(Instant::now() + delay);
        self.request_preview();
    }

    fn flush_view(&mut self) {
        let Some(view) = self.view.as_mut() else { return };
        view.flush_at = None;
        if view.pending.is_empty() {
            return;
        }
        let patch = Value::Object(std::mem::take(&mut view.pending));
        let id = view.inst.id.clone();
        self.call("configure", json!({"id": id, "patch": patch}));
    }

    fn change_setting(&mut self, apply_after: Option<Duration>) {
        if let Err(e) = settings::save(&self.settings) {
            self.toast(e.to_string());
        }
        self.settings_apply_at = apply_after.map(|d| Instant::now() + d);
    }

    fn language_changed(&mut self) {
        let lang = settings::language(&self.settings);
        set_lang(&lang);
        self.lang = lang;
        self.change_setting(Some(Duration::from_millis(50)));
        self.request_thumbnails(None);
        self.request_preview();
    }

    fn act(&mut self, action: Action) {
        match action {
            Action::Add(id) => self.add(&id),
            Action::Remove(id) => {
                self.call("remove", json!({"id": id}));
                if self.view.as_ref().map(|v| v.inst.id == id).unwrap_or(false) {
                    if let Some(v) = self.view.as_mut() {
                        v.pending.clear();
                    }
                    self.close_view();
                }
            }
            Action::OpenSettings(id) => self.open_settings(&id),
            Action::CloseSettings => self.close_view(),
            Action::Call(command) => self.call(command, json!({})),
            Action::Thumbnails => {
                self.textures.retain(|k, _| !k.starts_with("thumb/"));
                self.request_thumbnails(None);
            }
            Action::About => self.show_about = true,
            Action::CreateWidget => {
                let name = self.new_widget.trim().to_string();
                if name.is_empty() {
                    return;
                }
                match scaffold::create(&name, None) {
                    Ok(folder) => {
                        self.new_widget.clear();
                        self.call("reload", json!({}));
                        self.page = Page::Add;
                        open_url(&folder.to_string_lossy());
                        self.toast(tr("Створено. Натисніть + у вкладці «Додати», щоб розмістити його на робочому столі.", "Created. Click + in the \"Add\" tab to place it on the desktop.").to_string());
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => self.toast(tr("Такий віджет уже є", "That widget already exists").to_string()),
                    Err(e) => self.toast(e.to_string()),
                }
            }
            Action::OpenFolder => {
                let _ = std::fs::create_dir_all(paths::user_widgets());
                open_url(&paths::user_widgets().to_string_lossy());
            }
        }
    }

    // pictures
    /// A rounded tile with the picture of a widget centred in it (never enlarged beyond `cap`).
    fn preview_tile(&self, ui: &mut egui::Ui, key: &str, size_px: (i32, i32), tile: Vec2, cap: f32) {
        let (rect, _) = ui.allocate_exact_size(tile, Sense::hover());
        ui.painter().rect_filled(rect, 12.0, ui::tile_fill(ui));
        let (w, h) = (size_px.0 as f32, size_px.1 as f32);
        let k = ((tile.x - 20.0) / w).min((tile.y - 20.0) / h).min(cap);
        let image_rect = egui::Rect::from_center_size(rect.center(), Vec2::new(w * k, h * k));
        match self.textures.get(key) {
            Some(texture) => {
                egui::Image::from_texture(egui::load::SizedTexture::new(texture.id(), image_rect.size())).paint_at(ui, image_rect);
            }
            None => {
                egui::Spinner::new().paint_at(ui, egui::Rect::from_center_size(rect.center(), Vec2::splat(22.0)));
            }
        }
    }

    fn window_px(&self, size: &str) -> (i32, i32) {
        crate::core::grid::Grid::from_settings(1920, 1080, &self.settings, 1.0).window_px(size)
    }

    // pages
    fn page_mine(&self, ui: &mut egui::Ui, actions: &mut Vec<Action>) {
        ui::column(ui, 900.0, |ui| {
            ui.horizontal(|ui| {
                ui.vertical(|ui| {
                    ui.label(RichText::new(tr("Мої віджети", "My widgets")).size(22.0).strong());
                    ui.label(RichText::new(plural_widgets(self.instances.len())).weak());
                });
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if ui::primary_button(ui, &format!("+ {}", tr("Додати віджет", "Add a widget"))).clicked() {
                        actions.push(Action::Call("")); // switched to the Add page by the caller
                    }
                });
            });
            ui.add_space(12.0);
            if self.instances.is_empty() {
                ui.add_space(60.0);
                ui.vertical_centered(|ui| {
                    ui.label(RichText::new(tr("Тут поки порожньо", "Nothing here yet")).size(20.0).strong());
                    ui.label(RichText::new(tr("Додайте перший віджет на робочий стіл.", "Add your first widget to the desktop.")).weak());
                });
                return;
            }
            for inst in &self.instances {
                let Some(t) = self.types.get(&inst.kind) else { continue };
                egui::Frame::new().fill(ui::card_fill(ui)).stroke(Stroke::new(1.0_f32, ui::line_color(ui))).corner_radius(14.0).inner_margin(12.0).show(ui, |ui| {
                    let width = ui.available_width();
                    let (tile, gap, buttons) = (156.0, 16.0, 210.0);
                    ui.allocate_ui_with_layout(Vec2::new(width, 104.0), Layout::left_to_right(Align::Center), |ui| {
                        ui.spacing_mut().item_spacing.x = gap;
                        self.preview_tile(ui, &self.thumb_key(&inst.kind), self.window_px(&inst.size), Vec2::new(tile, 104.0), 0.62);
                        let text_width = (width - tile - buttons - 2.0 * gap).max(80.0);
                        ui.allocate_ui_with_layout(Vec2::new(text_width, 42.0), Layout::top_down(Align::Min), |ui| {
                            ui.set_width(text_width);
                            ui.spacing_mut().item_spacing.y = 3.0;
                            ui.add(egui::Label::new(RichText::new(instance_title(inst, t)).strong().size(16.0)).truncate());
                            ui.add(egui::Label::new(RichText::new(catalog::describe(inst)).weak()).truncate());
                        });
                        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                            ui.spacing_mut().item_spacing.x = 8.0;
                            if ui::danger_button(ui, tr("Прибрати", "Remove")).clicked() {
                                actions.push(Action::Remove(inst.id.clone()));
                            }
                            if ui.button(tr("Налаштування", "Settings")).clicked() {
                                actions.push(Action::OpenSettings(inst.id.clone()));
                            }
                        });
                    });
                });
                ui.add_space(8.0);
            }
        });
    }

    fn page_add(&self, ui: &mut egui::Ui, actions: &mut Vec<Action>) {
        ui::column(ui, 1000.0, |ui| {
            ui.label(RichText::new(tr("Додати віджет", "Add a widget")).size(22.0).strong());
            ui.label(RichText::new(tr("Кожен віджет можна додати кілька разів — з різними налаштуваннями.", "Every widget can be added several times, each with its own settings.")).weak());
            for category in CATEGORIES {
                let mut list: Vec<&WidgetType> = self.types.values().filter(|t| t.category == category).collect();
                if list.is_empty() {
                    continue;
                }
                list.sort_by_key(|t| t.title());
                ui.add_space(18.0);
                ui.label(RichText::new(catalog::category_name(category)).size(16.0).strong());
                ui.add_space(4.0);
                let spacing = 12.0;
                let available = ui.available_width();
                let columns = (((available + spacing) / 250.0).floor() as usize).max(1);
                let tile_width = (available - spacing * (columns - 1) as f32) / columns as f32;
                let inner = tile_width - 26.0;
                for chunk in list.chunks(columns) {
                    ui.horizontal_top(|ui| {
                        ui.spacing_mut().item_spacing = Vec2::new(spacing, spacing);
                        for t in chunk {
                            egui::Frame::new().fill(ui::card_fill(ui)).stroke(Stroke::new(1.0_f32, ui::line_color(ui))).corner_radius(14.0).inner_margin(12.0).show(ui, |ui| {
                                ui.vertical(|ui| {
                                    ui.set_width(inner);
                                    ui.spacing_mut().item_spacing.y = 6.0;
                                    self.preview_tile(ui, &self.thumb_key(&t.id), self.window_px(t.size()), Vec2::new(inner, 126.0), 0.6);
                                    ui.add(egui::Label::new(RichText::new(t.title()).strong().size(15.0)).truncate());
                                    ui.allocate_ui_with_layout(Vec2::new(inner, 34.0), Layout::top_down(Align::Min), |ui| {
                                        ui.set_min_height(34.0);
                                        ui.add(egui::Label::new(RichText::new(clip_text(&t.desc(), inner)).weak().size(12.0)).wrap());
                                    });
                                    ui.horizontal(|ui| {
                                        if ui::primary_button(ui, &format!("+ {}", tr("Додати", "Add"))).clicked() {
                                            actions.push(Action::Add(t.id.clone()));
                                        }
                                        if t.user {
                                            ui.label(RichText::new(tr("мій", "mine")).weak().small());
                                        }
                                    });
                                });
                            });
                        }
                    });
                    ui.add_space(0.0);
                }
            }
        });
    }

    fn page_prefs(&mut self, ui: &mut egui::Ui, actions: &mut Vec<Action>) {
        let mut changed_lang = false;
        let mut apply: Option<Duration> = None;
        ui::column(ui, 720.0, |ui| {
            ui.label(RichText::new(tr("Налаштування", "Settings")).size(22.0).strong());
            ui::section(ui, tr("Вигляд", "Appearance"), None, |ui, rows| {
                rows.row(ui, tr("Мова", "Language"), "", |ui| {
                    let langs = [("auto", tr("Як у системі", "System")), ("uk", "Українська"), ("en", "English")];
                    let shown = langs.iter().find(|(k, _)| *k == self.settings.language).map(|(_, n)| *n).unwrap_or("");
                    if let Some(key) = ui::dropdown(ui, "language", shown, langs, &self.settings.language) {
                        self.settings.language = key;
                        changed_lang = true;
                    }
                });
                rows.row(ui, tr("Розмір віджетів", "Widget size"), "", |ui| {
                    let mut percent = (self.settings.scale * 100.0).round() as i32;
                    if ui::full_slider(ui, &mut percent, 60..=200, " %", 5.0).changed() {
                        self.settings.scale = percent as f64 / 100.0;
                        apply = Some(Duration::from_millis(500));
                    }
                });
                rows.row(ui, tr("Шрифт", "Font"), tr("Іконки вбудовані.", "Icons are built in."), |ui| {
                    let shown = if self.settings.font == "auto" { tr("Шрифт системи", "System font").to_string() } else { self.settings.font.clone() };
                    if self.families.is_empty() {
                        self.families = crate::engine::fonts::list_families();
                    }
                    egui::ComboBox::from_id_salt("font").width(ui.available_width()).selected_text(shown).height(360.0).show_ui(ui, |ui| {
                        ui.add(egui::TextEdit::singleline(&mut self.font_filter).hint_text(tr("пошук", "search")));
                        if ui.selectable_label(self.settings.font == "auto", tr("Шрифт системи", "System font")).clicked() {
                            self.settings.font = "auto".into();
                            apply = Some(Duration::from_millis(50));
                        }
                        let filter = self.font_filter.to_lowercase();
                        for family in self.families.iter().filter(|f| filter.is_empty() || f.to_lowercase().contains(&filter)) {
                            if ui.selectable_label(&self.settings.font == family, family).clicked() {
                                self.settings.font = family.clone();
                                apply = Some(Duration::from_millis(50));
                            }
                        }
                    });
                });
                rows.row(ui, tr("Стиль нових віджетів", "Style for new widgets"), "", |ui| {
                    let mut list = vec![("auto", tr("Як задумано у віджеті", "Each widget’s own"))];
                    list.extend(catalog::styles());
                    let shown = list.iter().find(|(k, _)| *k == self.settings.default_style).map(|(_, n)| *n).unwrap_or("");
                    if let Some(key) = ui::dropdown(ui, "default_style", shown, list.iter().copied(), &self.settings.default_style) {
                        self.settings.default_style = key;
                        self.change_setting(None);
                    }
                });
            });
            ui::section(ui, tr("Сітка", "Grid"), Some(tr("Віджети стоять по клітинках; відступи — в пікселях.", "Widgets sit in grid cells; spacing is in pixels.")), |ui, rows| {
                for (title, hi, field) in [
                    (tr("Між віджетами", "Between widgets"), 64, 0),
                    (tr("Згори (верхня панель)", "Top (top bar)"), 400, 1),
                    (tr("Знизу (док)", "Bottom (dock)"), 400, 2),
                    (tr("З боків", "Sides"), 400, 3),
                ] {
                    rows.row(ui, title, "", |ui| {
                        let value = match field {
                            0 => &mut self.settings.gap,
                            1 => &mut self.settings.margin_top,
                            2 => &mut self.settings.margin_bottom,
                            _ => &mut self.settings.margin_side,
                        };
                        if ui::full_slider(ui, value, 0..=hi, " px", 1.0).changed() {
                            apply = Some(Duration::from_millis(700));
                        }
                    });
                }
            });
            ui::section(ui, tr("Система", "System"), None, |ui, rows| {
                rows.row(ui, tr("Запускати під час входу", "Start at login"), "", |ui| {
                    if ui::toggle(ui, &mut self.settings.autostart).changed() {
                        integration::set_autostart(self.settings.autostart);
                        self.change_setting(None);
                    }
                });
                if integration::is_gnome() {
                    rows.row(ui, tr("Гаряча клавіша Super+Alt+W", "Hotkey Super+Alt+W"), tr("Відкриває це вікно", "Opens this window"), |ui| {
                        if ui::toggle(ui, &mut self.settings.hotkey).changed() {
                            integration::set_hotkey(self.settings.hotkey);
                            self.change_setting(None);
                        }
                    });
                } else if cfg!(windows) {
                    rows.row(ui, tr("Гаряча клавіша Ctrl+Alt+W", "Hotkey Ctrl+Alt+W"), tr("Відкриває це вікно", "Opens this window"), |ui| {
                        if ui::toggle(ui, &mut self.settings.hotkey).changed() {
                            apply = Some(Duration::from_millis(50));
                        }
                    });
                }
                rows.row(ui, tr("Сітка під час перетягування", "Grid while dragging"), tr("Показувати клітинки, поки тягнете віджет", "Show the cells while a widget is dragged"), |ui| {
                    if ui::toggle(ui, &mut self.settings.show_grid).changed() {
                        apply = Some(Duration::from_millis(50));
                    }
                });
                rows.row(ui, tr("Заблокувати віджети", "Lock the widgets"), tr("Їх не можна перетягнути мишею", "They cannot be dragged with the mouse"), |ui| {
                    if ui::toggle(ui, &mut self.settings.lock).changed() {
                        apply = Some(Duration::from_millis(50));
                    }
                });
            });
            ui::section(
                ui,
                tr("Свої віджети", "Your own widgets"),
                Some(tr("Віджет — це тека з widget.json і widget.lua. Інструкція: docs/creating-widgets.md", "A widget is a folder with widget.json and widget.lua. Guide: docs/creating-widgets.md")),
                |ui, rows| {
                    rows.row(ui, tr("Тека моїх віджетів", "My widgets folder"), &paths::user_widgets().display().to_string(), |ui| {
                        if ui.button(tr("Відкрити теку", "Open the folder")).clicked() {
                            actions.push(Action::OpenFolder);
                        }
                    });
                    rows.row(ui, tr("Створити віджет", "Create a widget"), tr("Назва латиницею", "A name in latin letters"), |ui| {
                        let width = ui.available_width();
                        ui.spacing_mut().item_spacing.x = 8.0;
                        ui.add(egui::TextEdit::singleline(&mut self.new_widget).hint_text("my_widget").desired_width(width - 100.0).margin(egui::Margin::symmetric(8, 6)));
                        if ui.button(tr("Створити", "Create")).clicked() {
                            actions.push(Action::CreateWidget);
                        }
                    });
                },
            );
            ui.add_space(24.0);
        });
        if changed_lang {
            self.language_changed();
        } else if apply.is_some() {
            self.change_setting(apply);
        }
    }

    fn page_widget(&mut self, ui: &mut egui::Ui, actions: &mut Vec<Action>) {
        let Some(view) = self.view.as_ref() else { return };
        let (inst, t) = (view.inst.clone(), view.wtype.clone());
        let mut patches: Vec<(Value, Duration)> = Vec::new();
        let px = self.window_px(&inst.size);
        ui::column(ui, 720.0, |ui| {
            ui.horizontal(|ui| {
                if !self.only_settings && ui.button(format!("←  {}", tr("Назад", "Back"))).clicked() {
                    actions.push(Action::CloseSettings);
                }
                ui.add(egui::Label::new(RichText::new(instance_title(&inst, &t)).size(22.0).strong()).truncate());
            });
            ui.add_space(10.0);
            let cap = if inst.size == "large" { 0.7 } else { 0.85 };
            let tile_height = (px.1 as f32 * cap + 44.0).max(150.0);
            self.preview_tile(ui, "preview", px, Vec2::new(ui.available_width(), tile_height), cap);
            ui.add_space(4.0);
            ui::section(ui, tr("Вигляд", "Look"), Some(tr("Зміни одразу видно на робочому столі.", "Changes show on the desktop right away.")), |ui, rows| {
                if t.sizes.len() > 1 {
                    rows.row(ui, tr("Розмір", "Size"), "", |ui| {
                        let names: Vec<(String, String)> = t.sizes.iter().map(|k| (k.clone(), catalog::size_name(k))).collect();
                        let shown = catalog::size_name(&inst.size);
                        if let Some(size) = ui::dropdown(ui, "size", &shown, names.iter().map(|(a, b)| (a.as_str(), b.as_str())), &inst.size) {
                            patches.push((json!({"size": size}), Duration::ZERO));
                        }
                    });
                }
                rows.row(ui, tr("Стиль", "Style"), "", |ui| {
                    let styles = catalog::styles();
                    let shown = styles.iter().find(|(k, _)| *k == inst.style).map(|(_, n)| *n).unwrap_or("");
                    if let Some(style) = ui::dropdown(ui, "style", shown, styles.iter().copied(), &inst.style) {
                        patches.push((json!({"style": style}), Duration::ZERO));
                    }
                });
                rows.row(ui, tr("Колір", "Colour"), "", |ui| {
                    let current = if inst.accent.is_empty() { "auto" } else { inst.accent.as_str() };
                    if let Some(key) = ui::accent_picker(ui, current) {
                        patches.push((json!({"accent": key}), Duration::ZERO));
                    }
                });
            });
            if !t.options.is_empty() {
                let values = catalog::option_values(&inst, &t, None);
                ui::section(ui, tr("Параметри", "Options"), None, |ui, rows| {
                    for opt in &t.options {
                        let value = values.get(&opt.key).cloned().unwrap_or_else(|| opt.default_value(None));
                        if let Some(patch) = ui::option_row(ui, rows, opt, &value) {
                            patches.push(patch);
                        }
                    }
                });
            }
            ui.add_space(18.0);
            let remove = egui::Button::new(RichText::new(tr("Прибрати з робочого столу", "Remove from the desktop")).color(ui::DANGER))
                .fill(ui::DANGER.gamma_multiply(0.12))
                .stroke(Stroke::new(1.0_f32, ui::DANGER.gamma_multiply(0.5)));
            if ui.add_sized([ui.available_width(), 38.0], remove).clicked() {
                actions.push(Action::Remove(inst.id.clone()));
            }
            ui.add_space(24.0);
        });
        for (patch, delay) in patches {
            self.send(patch, delay);
        }
    }

    fn about(&mut self, ctx: &egui::Context) {
        let mut open = self.show_about;
        egui::Window::new(tr("Про програму", "About")).open(&mut open).collapsible(false).resizable(false).anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0]).show(ctx, |ui| {
            ui.vertical_centered(|ui| {
                ui.label(RichText::new("DeskWidgets").size(22.0).strong());
                ui.label(format!("{VERSION} · MIT"));
                ui.add_space(6.0);
                ui.label(tr("Віджети робочого столу в стилі macOS для Linux і Windows. Легко додати свій віджет.", "macOS-style desktop widgets for Linux and Windows. Easy to add your own."));
                ui.add_space(6.0);
                if ui.link("https://github.com/T0ks1k24/DeskWidgets").clicked() {
                    open_url("https://github.com/T0ks1k24/DeskWidgets");
                }
            });
        });
        self.show_about = open;
    }
}

impl eframe::App for Gallery {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.receive(ctx);
        if self.last_poll.elapsed() > Duration::from_millis(400) {
            self.last_poll = Instant::now();
            self.poll();
        }
        let now = Instant::now();
        if let Some(view) = self.view.as_ref() {
            if view.flush_at.map(|t| t <= now).unwrap_or(false) {
                self.flush_view();
            }
        }
        if self.settings_apply_at.map(|t| t <= now).unwrap_or(false) {
            self.settings_apply_at = None;
            let _ = settings::save(&self.settings);
            self.call("settings", json!({}));
            let font = self.settings.font.clone();
            let _ = self.renderer.jobs.send(Job::Fonts(font));
            self.request_thumbnails(None);
            self.request_preview();
        }
        self.toasts.retain(|(_, at)| at.elapsed() < Duration::from_secs(5));

        let mut actions: Vec<Action> = Vec::new();
        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            if self.view.is_some() {
                actions.push(Action::CloseSettings);
            } else {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }
        if !self.only_settings {
            egui::TopBottomPanel::top("tabs")
                .frame(egui::Frame::side_top_panel(&ctx.style()).inner_margin(egui::Margin::symmetric(20, 10)))
                .show(ctx, |ui| {
                    ui.horizontal(|ui| {
                        ui.spacing_mut().item_spacing.x = 4.0;
                        for (page, name) in [(Page::Mine, tr("Мої віджети", "My widgets")), (Page::Add, tr("Додати", "Add")), (Page::Prefs, tr("Налаштування", "Settings"))] {
                            let selected = self.view.is_none() && self.page == page;
                            if ui::tab(ui, name, selected).clicked() {
                                self.page = page;
                                if self.view.is_some() {
                                    actions.push(Action::CloseSettings);
                                }
                            }
                        }
                        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                            ui.menu_button(RichText::new("☰").size(17.0), |ui| {
                                if ui.button(tr("Розставити по сітці", "Arrange on the grid")).clicked() {
                                    actions.push(Action::Call("arrange"));
                                    ui.close_menu();
                                }
                                if ui.button(tr("Перезапустити віджети", "Reload widgets")).clicked() {
                                    actions.push(Action::Call("reload"));
                                    ui.close_menu();
                                }
                                if ui.button(tr("Оновити мініатюри", "Refresh thumbnails")).clicked() {
                                    actions.push(Action::Thumbnails);
                                    ui.close_menu();
                                }
                                ui.separator();
                                if ui.button(tr("Про програму", "About")).clicked() {
                                    actions.push(Action::About);
                                    ui.close_menu();
                                }
                            });
                        });
                    });
                });
        }
        let page_frame = egui::Frame::new().fill(ctx.style().visuals.panel_fill).inner_margin(egui::Margin::symmetric(24, 18));
        egui::CentralPanel::default().frame(page_frame).show(ctx, |ui| {
            egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                if self.view.is_some() {
                    self.page_widget(ui, &mut actions);
                } else {
                    match self.page {
                        Page::Mine => self.page_mine(ui, &mut actions),
                        Page::Add => self.page_add(ui, &mut actions),
                        Page::Prefs => self.page_prefs(ui, &mut actions),
                    }
                }
            });
        });
        // toasts
        if !self.toasts.is_empty() {
            egui::Area::new(egui::Id::new("toasts")).anchor(egui::Align2::CENTER_BOTTOM, [0.0, -16.0]).show(ctx, |ui| {
                for (text, _) in &self.toasts {
                    egui::Frame::popup(ui.style()).corner_radius(10.0).show(ui, |ui| {
                        ui.label(text);
                    });
                }
            });
            ctx.request_repaint_after(Duration::from_millis(500));
        }
        if self.show_about {
            self.about(ctx);
        }
        for action in actions {
            match action {
                Action::Call("") => self.page = Page::Add,
                other => self.act(other),
            }
        }
        // the service writes layout.json, the widget folders change: look again soon
        ctx.request_repaint_after(Duration::from_millis(400));
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.flush_view();
        if self.settings_apply_at.take().is_some() {
            let _ = settings::save(&self.settings);
            self.call("settings", json!({}));
        }
        // give the IPC thread a moment to deliver the last request
        std::thread::sleep(Duration::from_millis(300));
    }
}

// ---------------------------------------------------------------------------------------------
fn first_run(settings: &mut Settings) {
    if !settings.extra.contains_key("_setup_done") {
        integration::setup(settings.autostart, settings.hotkey);
        settings.extra.insert("_setup_done".into(), Value::Bool(true));
        let _ = settings::save(settings);
    }
}

/// The newest change in the user's widget folders.
fn catalog_stamp() -> Option<SystemTime> {
    fn newest(dir: &std::path::Path, depth: u32) -> Option<SystemTime> {
        let mut best = std::fs::metadata(dir).and_then(|m| m.modified()).ok();
        if depth == 0 {
            return best;
        }
        for entry in std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()) {
            let path = entry.path();
            let stamp = if path.is_dir() { newest(&path, depth - 1) } else { entry.metadata().ok().and_then(|m| m.modified().ok()) };
            best = best.max(stamp);
        }
        best
    }
    newest(&paths::user_widgets(), 2)
}

fn instance_title(inst: &Instance, t: &WidgetType) -> String {
    let opts = &inst.options;
    let text = |k: &str| opts.get(k).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string);
    let extra = text("title").or_else(|| if inst.kind == "weather" { text("city") } else { None });
    match extra {
        Some(extra) if extra != t.title() => format!("{} — {}", t.title(), extra),
        _ => t.title(),
    }
}

fn plural_widgets(n: usize) -> String {
    if crate::core::i18n::lang() == "en" {
        return format!("{n} widget{}", if n == 1 { "" } else { "s" });
    }
    let (n10, n100) = (n % 10, n % 100);
    if n10 == 1 && n100 != 11 {
        format!("{n} віджет")
    } else if (2..=4).contains(&n10) && !(12..=14).contains(&n100) {
        format!("{n} віджети")
    } else {
        format!("{n} віджетів")
    }
}

/// A description cut to about two lines of a tile of this width.
fn clip_text(text: &str, width: f32) -> String {
    let per_line = (width / 6.4).floor() as usize;
    let max = per_line.saturating_mul(2).saturating_sub(4).max(20);
    if text.chars().count() <= max {
        return text.to_string();
    }
    let cut: String = text.chars().take(max).collect();
    format!("{}…", cut.trim_end())
}
