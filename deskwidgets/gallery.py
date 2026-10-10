"""The "Widgets" gallery and settings window (Super+Alt+W).

* My widgets: what is on the desktop. Open settings or remove.
* Add: every widget type (built-in and your own) by category. Add as many copies as you like.
* Settings: language, widget size, grid spacing, font, defaults, autostart, hotkey, grid helper,
  your own widgets folder and "create a widget".
Each widget's settings window is built from its widget.json, so third-party widgets get one too.
Right-clicking a widget runs `deskwidgets configure <id>`, which opens just its settings.
"""
import json
import os
import subprocess
import sys
import threading
from datetime import date

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from . import VERSION, catalog, desktop, engine, paths, settings as settings_mod
from .catalog import ACCENTS, CATEGORIES, accent_name, category_name, describe, option_values, size_name, styles
from .i18n import loc, set_lang, tr

CSS = '''
.widget-tile { padding: 14px; border-radius: 18px; }
.widget-preview { border-radius: 14px;
  background-image: linear-gradient(135deg, alpha(#bf5af2, 0.16), alpha(#5e5ce6, 0.07)); }
.widget-title { font-weight: 700; font-size: 1.05em; }
.category-title { font-weight: 800; font-size: 1.15em; margin-top: 10px; }
.hint { padding: 2px 6px 12px 6px; }
.swatch { min-width: 24px; min-height: 24px; padding: 0; border-radius: 999px; }
.swatch:checked { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px @accent_color; }
.swatch-auto { background-image: conic-gradient(#FF453A, #FFD60A, #30D158, #0A84FF, #BF5AF2, #FF453A); }
.live-preview { border-radius: 16px; padding: 8px;
  background-image: linear-gradient(135deg, alpha(#bf5af2, 0.18), alpha(#40c8e0, 0.10)); }
''' + ''.join(f'.swatch-{key} {{ background: {color}; }}\n' for key, color in ACCENTS if color)

THUMB_SCALE = 0.6


def thumbs_dir():
    return os.path.join(paths.PREVIEWS, VERSION, settings_mod.language())


def thumbnail(type_id, size_px, height=None, scale=THUMB_SCALE):
    path = os.path.join(thumbs_dir(), f'{type_id}.png')
    if not os.path.exists(path):
        return None
    w, h = size_px
    if height:
        scale = height / h
    try:
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, round(w * scale), round(h * scale), True)
    except GLib.Error:
        return None
    return Gdk.Texture.new_for_pixbuf(pix)


def window_px(size):
    from .grid import Grid
    return Grid(1920, 1080, {**settings_mod.DEFAULTS, 'scale': 1.0}).window_px(size)


def read_layout():
    try:
        with open(paths.LAYOUT, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('instances', []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def instance_title(inst, t):
    opts = inst.get('options') or {}
    extra = opts.get('title') or (opts.get('city') if inst['type'] == 'weather' else None)
    return f'{t.title} — {extra}' if extra and extra != t.title else t.title


def plural_widgets(n):
    if settings_mod.language() == 'en':
        return f'{n} widget' + ('' if n == 1 else 's')
    if n % 10 == 1 and n % 100 != 11:
        return f'{n} віджет'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f'{n} віджети'
    return f'{n} віджетів'


def escape_shortcut(window):
    keys = Gtk.ShortcutController()
    keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string('Escape'),
                                   action=Gtk.CallbackAction.new(lambda *a: window.close() or True)))
    window.add_controller(keys)


# settings of one widget
class SettingsWindow(Adw.Window):
    def __init__(self, app, inst, t):
        super().__init__(application=app, title=tr(f'{t.title} — налаштування', f'{t.title} — settings'),
                         default_width=600, default_height=760)
        self.app, self.inst, self.t = app, json.loads(json.dumps(inst)), t
        self.pending = {}
        self.timer = 0
        self.preview_timer = 0
        self.preview_proc = None

        page = Adw.PreferencesPage()
        top = Adw.PreferencesGroup()
        frame = Gtk.Box(halign=Gtk.Align.CENTER)
        frame.add_css_class('live-preview')
        self.picture = Gtk.Picture(can_shrink=False)
        frame.append(self.picture)
        top.add(frame)
        page.add(top)

        look = Adw.PreferencesGroup(title=tr('Вигляд', 'Look'),
                                    description=tr('Зміни одразу видно на робочому столі.', 'Changes show on the desktop right away.'))
        if len(t.sizes) > 1:
            keys = list(t.sizes)
            row = Adw.ComboRow(title=tr('Розмір', 'Size'), model=Gtk.StringList.new([size_name(k) for k in keys]))
            row.set_selected(keys.index(inst['size']) if inst['size'] in keys else 0)
            row.connect('notify::selected', lambda r, _p: self.send({'size': keys[r.get_selected()]}))
            look.add(row)
        style_list = styles()
        style_keys = [k for k, _n in style_list]
        row = Adw.ComboRow(title=tr('Стиль', 'Style'), model=Gtk.StringList.new([n for _k, n in style_list]))
        row.set_selected(style_keys.index(inst['style']) if inst['style'] in style_keys else 0)
        row.connect('notify::selected', lambda r, _p: self.send({'style': style_keys[r.get_selected()]}))
        look.add(row)
        look.add(self.accent_row(inst.get('accent') or 'auto'))
        page.add(look)

        if t.options:
            values = option_values(inst, t)
            hints = [f'• {o.title}: {o.hint_text}' for o in t.options if o.hint_text and o.type not in ('longtext', 'file')]
            params = Adw.PreferencesGroup(title=tr('Параметри', 'Options'), description='\n'.join(hints) or None)
            page.add(params)
            for opt in t.options:
                self.option_row(page, params, opt, values.get(opt.key, opt.default_value()))

        danger = Adw.PreferencesGroup()
        remove = Gtk.Button(label=tr('Прибрати з робочого столу', 'Remove from the desktop'), halign=Gtk.Align.CENTER)
        remove.add_css_class('destructive-action')
        remove.add_css_class('pill')
        remove.connect('clicked', self.on_remove)
        danger.add(remove)
        page.add(danger)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_content(view)
        escape_shortcut(self)
        self.connect('close-request', lambda *a: self.flush() or False)
        self.render_preview()

    # look
    def accent_row(self, current):
        row = Adw.ActionRow(title=tr('Колір', 'Colour'))
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=13, min_children_per_line=7,
                          column_spacing=4, row_spacing=4, valign=Gtk.Align.CENTER)
        group = None
        for key, _color in ACCENTS:
            btn = Gtk.ToggleButton(tooltip_text=accent_name(key))
            btn.add_css_class('swatch')
            btn.add_css_class(f'swatch-{key}')
            if group:
                btn.set_group(group)
            group = group or btn
            btn.set_active(key == current)
            btn.connect('toggled', lambda b, k=key: b.get_active() and self.send({'accent': k}))
            box.append(btn)
        row.add_suffix(box)
        return row

    # options generated from widget.json
    def option_row(self, page, group, opt, value):
        key = opt.key
        if opt.type == 'bool':
            row = Adw.SwitchRow(title=opt.title, active=value in (True, 'true', '1', 1))
            row.connect('notify::active', lambda r, _p: self.send({'options': {key: r.get_active()}}))
            group.add(row)
        elif opt.type in ('text', 'date'):
            row = Adw.EntryRow(title=opt.title, text=str(value or ''))
            if opt.hint_text:
                row.set_tooltip_text(opt.hint_text)
            row.connect('changed', lambda r: self.on_text(r, opt))
            group.add(row)
        elif opt.type == 'number':
            adj = Gtk.Adjustment(lower=opt.min, upper=opt.max, step_increment=opt.step, page_increment=opt.step * 10)
            row = Adw.SpinRow(title=opt.title, adjustment=adj, digits=0 if float(opt.step).is_integer() else 1)
            try:
                row.set_value(float(value))
            except (TypeError, ValueError):
                row.set_value(opt.min)
            row.connect('notify::value', lambda r, _p: self.send({'options': {key: r.get_value()}}, delay=500))
            group.add(row)
        elif opt.type == 'choice':
            values = [v for v, _l in opt.choices]
            row = Adw.ComboRow(title=opt.title, model=Gtk.StringList.new([loc(l) for _v, l in opt.choices]))
            row.set_selected(values.index(value) if value in values else 0)
            row.connect('notify::selected', lambda r, _p: self.send({'options': {key: values[r.get_selected()]}}))
            group.add(row)
        elif opt.type == 'multi':
            selected = [v for v in str(value or '').split(',') if v]
            row = Adw.ExpanderRow(title=opt.title)
            switches = {}

            def update_subtitle():
                names = [loc(l) for v, l in opt.choices if switches[v].get_active()]
                extra = tr(f' · показуються перші {opt.limit}', f' · the first {opt.limit} are shown') \
                    if opt.limit and len(names) > opt.limit else ''
                row.set_subtitle((', '.join(n.split(' — ')[0] for n in names) or tr('нічого не вибрано', 'nothing selected')) + extra)

            def toggled(*_a):
                update_subtitle()
                self.send({'options': {key: ','.join(v for v, _l in opt.choices if switches[v].get_active())}})

            for v, label in opt.choices:
                sw = Adw.SwitchRow(title=loc(label), active=v in selected)
                switches[v] = sw
                row.add_row(sw)
            update_subtitle()
            for sw in switches.values():
                sw.connect('notify::active', toggled)
            group.add(row)
        elif opt.type == 'file':
            row = Adw.ActionRow(title=opt.title, subtitle=os.path.basename(str(value)) if value else (opt.hint_text or tr('не вибрано', 'none')))
            pick = Gtk.Button(icon_name='document-open-symbolic', valign=Gtk.Align.CENTER, tooltip_text=tr('Обрати…', 'Choose…'))
            pick.add_css_class('flat')
            clear = Gtk.Button(icon_name='edit-clear-symbolic', valign=Gtk.Align.CENTER, tooltip_text=tr('Прибрати', 'Clear'))
            clear.add_css_class('flat')

            def chosen(dialog, result):
                try:
                    f = dialog.open_finish(result)
                except GLib.Error:
                    return
                row.set_subtitle(os.path.basename(f.get_path()))
                self.send({'options': {key: f.get_path()}})

            def open_dialog(_b):
                dialog = Gtk.FileDialog(title=opt.title)
                filt = Gtk.FileFilter(name=tr('Зображення', 'Images'))
                filt.add_pixbuf_formats()
                store = Gio.ListStore.new(Gtk.FileFilter)
                store.append(filt)
                dialog.set_filters(store)
                dialog.open(self, None, chosen)

            pick.connect('clicked', open_dialog)
            clear.connect('clicked', lambda _b: (row.set_subtitle(tr('не вибрано', 'none')), self.send({'options': {key: ''}})))
            row.add_suffix(pick)
            row.add_suffix(clear)
            group.add(row)
        elif opt.type == 'longtext':
            sub = Adw.PreferencesGroup(title=opt.title, description=opt.hint_text or None)
            view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                left_margin=12, right_margin=12)
            view.get_buffer().set_text(str(value or ''))
            view.get_buffer().connect('changed', lambda b: self.send(
                {'options': {key: b.get_text(b.get_start_iter(), b.get_end_iter(), False)}}, delay=700))
            frame = Gtk.Frame(child=Gtk.ScrolledWindow(min_content_height=120, hscrollbar_policy=Gtk.PolicyType.NEVER, child=view))
            frame.add_css_class('view')
            sub.add(frame)
            page.add(sub)

    def on_text(self, row, opt):
        text = row.get_text().strip()
        if opt.type == 'date' and text:
            try:
                date.fromisoformat(text)
            except ValueError:
                row.add_css_class('error')
                return
        row.remove_css_class('error')
        self.send({'options': {opt.key: text}}, delay=700)

    # applying
    def send(self, patch, delay=0):
        for key, value in patch.items():
            if key == 'options':
                self.pending.setdefault('options', {}).update(value)
                self.inst.setdefault('options', {}).update(value)
            else:
                self.pending[key] = value
                self.inst[key] = value
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if delay:
            self.timer = GLib.timeout_add(delay, self.flush)
        else:
            self.flush()

    def flush(self):
        self.timer = 0
        if self.pending:
            self.app.call('configure', GLib.Variant('(ss)', (self.inst['id'], json.dumps(self.pending))))
            self.pending = {}
            self.render_preview()
        return False

    def on_remove(self, _btn):
        self.pending = {}
        self.app.call('remove', GLib.Variant('s', self.inst['id']))
        self.close()

    # live preview of exactly these settings
    def render_preview(self):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
        self.preview_timer = GLib.timeout_add(250, self._render)

    def _render(self):
        self.preview_timer = 0
        out = os.path.join(paths.RUN, f'settings-preview-{self.inst["id"]}.png')
        inst = self.inst
        self.preview_proc = engine.render(self.t, out, size=inst['size'], style=inst['style'], accent=inst.get('accent'),
                                          lang=settings_mod.language(), options=inst.get('options'), demo=True, wait=False)
        proc = self.preview_proc

        def done():
            if proc.poll() is None:
                return True
            if proc is self.preview_proc and os.path.exists(out):
                w, h = window_px(inst['size'])
                k = 0.85 if inst['size'] != 'large' else 0.7
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(out, round(w * k), round(h * k), True)
                    self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pix))
                except GLib.Error:
                    pass
            return False
        GLib.timeout_add(150, done)
        return False


# the gallery window
class GalleryWindow(Adw.ApplicationWindow):
    def __init__(self, app, page='mine'):
        super().__init__(application=app, title=tr('Віджети', 'Widgets'), default_width=1020, default_height=820)
        self.app = app
        self.types = catalog.discover()
        self.rows = []
        self.tiles = []
        self.known_ids = None
        self.waiting_for = None

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self.build_mine(), 'mine', tr('Мої віджети', 'My widgets'), 'view-grid-symbolic')
        self.stack.add_titled_with_icon(self.build_add(), 'add', tr('Додати', 'Add'), 'list-add-symbolic')
        self.stack.add_titled_with_icon(self.build_prefs(), 'prefs', tr('Налаштування', 'Settings'), 'emblem-system-symbolic')

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        menu = Gio.Menu()
        menu.append(tr('Розставити по сітці', 'Arrange on the grid'), 'app.arrange')
        menu.append(tr('Перезапустити віджети', 'Reload widgets'), 'app.reload')
        menu.append(tr('Оновити мініатюри', 'Refresh thumbnails'), 'app.previews')
        menu.append(tr('Про програму', 'About'), 'app.about')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, tooltip_text=tr('Меню', 'Menu')))

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)
        escape_shortcut(self)

        self.monitor = Gio.File.new_for_path(paths.LAYOUT).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect('changed', lambda *a: self.refresh())
        self.refresh()
        self.stack.set_visible_child_name(page)
        self.stack.connect('notify::visible-child', lambda *a: self.scroll_top())
        self.connect('map', lambda *a: self.scroll_top())

    def toast(self, text, button=None, callback=None):
        t = Adw.Toast(title=text, timeout=5)
        if button:
            t.set_button_label(button)
            t.connect('button-clicked', lambda *_a: callback())
        self.toasts.add_toast(t)

    def scroll_top(self):
        def reset():
            self.set_focus(None)
            for s in self.scrollers:
                s.get_vadjustment().set_value(0)
            return False
        # GTK scrolls to the first focusable child after layout, so reset after that
        GLib.timeout_add(80, reset)
        GLib.timeout_add(350, reset)

    def scroller(self, child, width):
        s = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=Adw.Clamp(maximum_size=width, child=child))
        self.scrollers = getattr(self, 'scrollers', []) + [s]
        return s

    # my widgets
    def build_mine(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, label=tr(
            'Затисніть Super і перетягніть віджет — він стане в найближчу клітинку сітки.\n'
            'Права кнопка миші на віджеті — його налаштування.',
            'Hold Super and drag a widget — it snaps into the nearest grid cell.\n'
            'Right-click a widget for its settings.'))
        hint.add_css_class('dim-label')
        hint.add_css_class('hint')
        box.append(hint)
        self.group = Adw.PreferencesGroup(title=tr('На робочому столі', 'On the desktop'))
        box.append(self.group)
        self.empty = Adw.StatusPage(icon_name='view-grid-symbolic', title=tr('Віджетів немає', 'No widgets yet'),
                                    description=tr('Відкрийте вкладку «Додати».', 'Open the "Add" tab.'))
        box.append(self.empty)
        return self.scroller(box, 780)

    def refresh(self):
        instances = read_layout()
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        for inst in instances:
            t = self.types.get(inst.get('type'))
            if t is None:
                continue
            row = Adw.ActionRow(title=instance_title(inst, t), subtitle=describe(inst), activatable=True)
            thumb = Adw.Bin(width_request=96)
            tex = thumbnail(t.id, window_px(t.size), height=44)
            if tex:
                thumb.set_child(Gtk.Picture(paintable=tex, can_shrink=False, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER))
            row.add_prefix(thumb)
            gear = Gtk.Button(icon_name='emblem-system-symbolic', valign=Gtk.Align.CENTER, tooltip_text=tr('Налаштувати', 'Settings'))
            gear.add_css_class('flat')
            gear.connect('clicked', lambda _b, i=inst['id']: self.app.open_settings(i, self))
            trash = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER, tooltip_text=tr('Прибрати', 'Remove'))
            trash.add_css_class('flat')
            trash.connect('clicked', lambda _b, i=inst['id']: self.app.call('remove', GLib.Variant('s', i)))
            row.add_suffix(gear)
            row.add_suffix(trash)
            row.connect('activated', lambda _r, i=inst['id']: self.app.open_settings(i, self))
            self.group.add(row)
            self.rows.append(row)
        self.group.set_description(plural_widgets(len(self.rows)) if self.rows else None)
        self.group.set_visible(bool(self.rows))
        self.empty.set_visible(not self.rows)

        ids = {i['id'] for i in instances}
        if self.known_ids is not None and self.waiting_for:
            new = [i for i in instances if i['id'] not in self.known_ids and i['type'] == self.waiting_for]
            if new:
                inst = new[0]
                t = self.types[inst['type']]
                self.toast(tr(f'«{t.title}» на робочому столі', f'"{t.title}" is on the desktop'),
                           tr('Налаштувати', 'Settings'), lambda: self.app.open_settings(inst['id'], self))
                self.waiting_for = None
        self.known_ids = ids

    # add
    def build_add(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=18,
                      margin_start=18, margin_end=18)
        by_cat = {}
        for t in self.types.values():
            by_cat.setdefault(t.category, []).append(t)
        for cat in CATEGORIES:
            if cat not in by_cat:
                continue
            label = Gtk.Label(label=category_name(cat), xalign=0)
            label.add_css_class('category-title')
            box.append(label)
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=3,
                               min_children_per_line=1, column_spacing=14, row_spacing=14, valign=Gtk.Align.START)
            for t in sorted(by_cat[cat], key=lambda t: t.title):
                flow.append(self.tile(t))
            box.append(flow)
        return self.scroller(box, 1000)

    def tile(self, t):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class('card')
        box.add_css_class('widget-tile')
        frame = Gtk.Box(hexpand=True)
        frame.set_size_request(-1, 136)
        frame.add_css_class('widget-preview')
        picture = Gtk.Picture(can_shrink=False, hexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        tex = thumbnail(t.id, window_px(t.size))
        if tex:
            picture.set_paintable(tex)
        frame.append(picture)
        box.append(frame)
        self.tiles.append((t, picture))

        row = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label=t.title + (tr('  · мій', '  · mine') if t.user else ''), xalign=0)
        title.add_css_class('widget-title')
        desc = Gtk.Label(label=t.desc, xalign=0, wrap=True, max_width_chars=28)
        desc.add_css_class('dim-label')
        desc.add_css_class('caption')
        sizes = Gtk.Label(label=' · '.join(size_name(s) for s in t.sizes), xalign=0)
        sizes.add_css_class('caption')
        sizes.add_css_class('accent')
        for w in (title, desc, sizes):
            text.append(w)
        add = Gtk.Button(icon_name='list-add-symbolic', valign=Gtk.Align.CENTER, tooltip_text=tr('Додати на робочий стіл', 'Add to the desktop'))
        add.add_css_class('circular')
        add.add_css_class('suggested-action')
        add.connect('clicked', lambda _b: self.add(t.id))
        row.append(text)
        row.append(add)
        box.append(row)
        return box

    def add(self, type_id):
        self.waiting_for = type_id
        self.app.call('add', GLib.Variant('s', type_id))

    def reload_thumbnails(self):
        for t, picture in self.tiles:
            tex = thumbnail(t.id, window_px(t.size))
            if tex:
                picture.set_paintable(tex)
        self.refresh()

    # settings
    def build_prefs(self):
        s = self.app.settings
        page = Adw.PreferencesPage()

        look = Adw.PreferencesGroup(title=tr('Вигляд', 'Appearance'))
        langs = [('auto', tr('Як у системі', 'System')), ('uk', 'Українська'), ('en', 'English')]
        row = Adw.ComboRow(title=tr('Мова', 'Language'), model=Gtk.StringList.new([n for _k, n in langs]))
        row.set_selected([k for k, _n in langs].index(s.get('language', 'auto')) if s.get('language') in ('auto', 'uk', 'en') else 0)
        row.connect('notify::selected', lambda r, _p: self.app.change_setting('language', langs[r.get_selected()][0], rebuild=True))
        look.add(row)

        scales = [0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.5]
        row = Adw.ComboRow(title=tr('Розмір віджетів', 'Widget size'), model=Gtk.StringList.new([f'{round(x * 100)} %' for x in scales]))
        row.set_selected(min(range(len(scales)), key=lambda i: abs(scales[i] - float(s['scale']))))
        row.connect('notify::selected', lambda r, _p: self.app.change_setting('scale', scales[r.get_selected()]))
        look.add(row)

        font_row = Adw.ActionRow(title=tr('Шрифт', 'Font'), subtitle=tr('Іконки вбудовані', 'Icons are built in'))
        font_btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), level=Gtk.FontLevel.FAMILY, valign=Gtk.Align.CENTER)
        try:
            from gi.repository import Pango
            font_btn.set_font_desc(Pango.FontDescription.from_string(s['font']))
        except Exception:
            pass
        font_btn.connect('notify::font-desc', lambda b, _p: b.get_font_desc() and
                         self.app.change_setting('font', b.get_font_desc().get_family()))
        font_row.add_suffix(font_btn)
        look.add(font_row)

        style_list = [('auto', tr('Як задумано у віджеті', 'Each widget’s own'))] + styles()
        row = Adw.ComboRow(title=tr('Стиль нових віджетів', 'Style for new widgets'),
                           model=Gtk.StringList.new([n for _k, n in style_list]))
        keys = [k for k, _n in style_list]
        row.set_selected(keys.index(s['default_style']) if s['default_style'] in keys else 0)
        row.connect('notify::selected', lambda r, _p: self.app.change_setting('default_style', keys[r.get_selected()], apply=False))
        look.add(row)
        page.add(look)

        grid = Adw.PreferencesGroup(title=tr('Сітка', 'Grid'),
                                    description=tr('Віджети стоять по клітинках; відступи — в пікселях.',
                                                   'Widgets sit in grid cells; spacing is in pixels.'))
        for key, title, hi in (('gap', tr('Між віджетами', 'Between widgets'), 64),
                               ('margin_top', tr('Згори (верхня панель)', 'Top (top bar)'), 400),
                               ('margin_bottom', tr('Знизу (док)', 'Bottom (dock)'), 400),
                               ('margin_side', tr('З боків', 'Sides'), 400)):
            spin = Adw.SpinRow(title=title, adjustment=Gtk.Adjustment(lower=0, upper=hi, step_increment=2, page_increment=10))
            spin.set_value(float(s[key]))
            spin.connect('notify::value', lambda r, _p, k=key: self.app.change_setting(k, int(r.get_value()), delay=700))
            grid.add(spin)
        page.add(grid)

        system = Adw.PreferencesGroup(title=tr('Система', 'System'))
        row = Adw.SwitchRow(title=tr('Запускати під час входу', 'Start at login'), active=s['autostart'])
        row.connect('notify::active', lambda r, _p: (desktop.set_autostart(r.get_active()),
                                                     self.app.change_setting('autostart', r.get_active(), apply=False)))
        system.add(row)
        if desktop.is_gnome():
            row = Adw.SwitchRow(title=tr('Гаряча клавіша Super+Alt+W', 'Hotkey Super+Alt+W'),
                                subtitle=tr('Відкриває це вікно', 'Opens this window'), active=s['hotkey'])
            row.connect('notify::active', lambda r, _p: (desktop.set_hotkey(r.get_active()),
                                                         self.app.change_setting('hotkey', r.get_active(), apply=False)))
            system.add(row)
            state = desktop.extension_state()
            sub = {'active': tr('Працює: сітка видна під час перетягування', 'Active: the grid shows while you drag'),
                   'pending': tr('Увімкнено — запрацює після повторного входу', 'Enabled — starts after you log in again'),
                   'off': tr('Сітка під час перетягування і миттєве примагнічування', 'Grid while dragging, instant snapping'),
                   'unavailable': tr('Лише для GNOME', 'GNOME only')}[state]
            row = Adw.SwitchRow(title=tr('Підказка-сітка GNOME', 'GNOME grid helper'), subtitle=sub,
                                active=state in ('active', 'pending'))
            row.connect('notify::active', lambda r, _p: self.toggle_extension(r))
            system.add(row)
        page.add(system)

        own = Adw.PreferencesGroup(title=tr('Свої віджети', 'Your own widgets'),
                                   description=tr('Віджет — це тека з widget.json і widget.lua. Інструкція: docs/creating-widgets.md',
                                                  'A widget is a folder with widget.json and widget.lua. Guide: docs/creating-widgets.md'))
        row = Adw.ActionRow(title=tr('Тека моїх віджетів', 'My widgets folder'), subtitle=paths.USER_WIDGETS, activatable=True)
        row.add_suffix(Gtk.Image(icon_name='folder-open-symbolic'))
        row.connect('activated', lambda *_a: self.open_folder(paths.USER_WIDGETS))
        own.add(row)
        row = Adw.ActionRow(title=tr('Створити віджет…', 'Create a widget…'),
                            subtitle=tr('Заготовка з прикладом, яку можна редагувати', 'A working template to edit'), activatable=True)
        row.add_suffix(Gtk.Image(icon_name='list-add-symbolic'))
        row.connect('activated', lambda *_a: self.create_widget())
        own.add(row)
        page.add(own)

        self.prefs_scroller = page
        return page

    def toggle_extension(self, row):
        on = row.get_active()
        ok = desktop.set_extension(on)
        self.app.change_setting('grid_overlay', on, apply=False)
        if on and ok:
            row.set_subtitle(tr('Увімкнено — запрацює після повторного входу', 'Enabled — starts after you log in again'))
            self.toast(tr('Вийдіть і зайдіть у систему, щоб GNOME підхопив підказку-сітку',
                          'Log out and back in so GNOME loads the grid helper'))

    def open_folder(self, folder):
        os.makedirs(folder, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(folder)).launch(self, None, None)

    def create_widget(self):
        dialog = Adw.AlertDialog(heading=tr('Новий віджет', 'New widget'),
                                 body=tr('Назва латиницею, наприклад my_widget', 'A name in latin letters, e.g. my_widget'))
        entry = Gtk.Entry(placeholder_text='my_widget', activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', tr('Скасувати', 'Cancel'))
        dialog.add_response('create', tr('Створити', 'Create'))
        dialog.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('create')

        def response(_d, resp):
            if resp != 'create' or not entry.get_text().strip():
                return
            from . import scaffold
            try:
                folder = scaffold.create(entry.get_text().strip())
            except FileExistsError:
                self.toast(tr('Такий віджет уже є', 'That widget already exists'))
                return
            self.app.call('reload')
            self.open_folder(folder)
            self.toast(tr('Створено. Він уже у вкладці «Додати» (після перезапуску галереї)',
                          'Created. It is in the "Add" tab (after reopening the gallery)'))
        dialog.connect('response', response)
        dialog.present(self)


class Gallery(Adw.Application):
    def __init__(self):
        super().__init__(application_id=paths.GALLERY_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None
        self.settings_windows = {}
        self.settings = settings_mod.load()
        self.setting_timer = 0

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, handler in (('arrange', lambda *a: self.call('arrange')), ('reload', lambda *a: self.call('reload')),
                              ('previews', lambda *a: self.render_thumbnails()), ('about', lambda *a: self.about())):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            self.add_action(action)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.remote = Gio.DBusActionGroup.get(self.bus, paths.APP_ID, paths.APP_PATH)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if '--configure' in args and args.index('--configure') + 1 < len(args):
            self.first_run()
            self.open_settings(args[args.index('--configure') + 1], None)
        else:
            self.activate()
        return 0

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self.first_run()
        self.win = GalleryWindow(self)
        self.win.connect('close-request', lambda *a: setattr(self, 'win', None) or False)
        self.win.present()
        if not all(os.path.exists(os.path.join(thumbs_dir(), f'{t}.png')) for t in self.win.types):
            self.render_thumbnails()

    def first_run(self):
        from .cli import ensure_service
        if not self.settings.get('_setup_done'):
            desktop.setup(autostart=self.settings['autostart'], hotkey=self.settings['hotkey'],
                          extension=self.settings['grid_overlay'])
            self.settings['_setup_done'] = True
            settings_mod.save(self.settings)
        ensure_service()

    def open_settings(self, inst_id, parent):
        inst = next((i for i in read_layout() if i.get('id') == inst_id), None)
        types = self.win.types if self.win else catalog.discover()
        if inst is None or inst.get('type') not in types:
            return
        win = self.settings_windows.get(inst_id)
        if win is None:
            win = SettingsWindow(self, inst, types[inst['type']])
            win.connect('close-request', lambda *a: self.settings_windows.pop(inst_id, None) and False)
            self.settings_windows[inst_id] = win
        if parent:
            win.set_transient_for(parent)
        win.present()

    def call(self, action, param=None):
        from .cli import ensure_service
        ensure_service()
        self.remote.activate_action(action, param)

    def change_setting(self, key, value, apply=True, rebuild=False, delay=0):
        self.settings[key] = value
        settings_mod.save(self.settings)
        if rebuild:   # language: rebuild the UI in the new language
            set_lang(settings_mod.language(self.settings))
            self.call('settings')
            if self.win:
                old = self.win
                self.win = GalleryWindow(self, page='prefs')
                self.win.connect('close-request', lambda *a: setattr(self, 'win', None) or False)
                self.win.present()
                old.destroy()
            self.render_thumbnails()
            return
        if not apply:
            return
        if self.setting_timer:
            GLib.source_remove(self.setting_timer)
        self.setting_timer = GLib.timeout_add(delay or 50, lambda: (self.call('settings'), setattr(self, 'setting_timer', 0), False)[2])

    def render_thumbnails(self):
        def work():
            from .cli import render_thumbnails
            render_thumbnails()
            GLib.idle_add(lambda: self.win and self.win.reload_thumbnails())
        threading.Thread(target=work, daemon=True).start()

    def about(self):
        about = Adw.AboutDialog(application_name=tr('Віджети', 'DeskWidgets'), application_icon='preferences-desktop-theme',
                                version=VERSION, developer_name='DeskWidgets contributors', license_type=Gtk.License.MIT_X11,
                                website='https://github.com/T0ks1k24/DeskWidgets',
                                comments=tr('Віджети робочого столу в стилі macOS для Linux. Легко додати свій віджет.',
                                            'macOS-style desktop widgets for Linux. Easy to add your own.'))
        about.present(self.win)


def main(argv=None):
    set_lang(settings_mod.language())
    return Gallery().run([sys.argv[0]] + list(argv or []))
