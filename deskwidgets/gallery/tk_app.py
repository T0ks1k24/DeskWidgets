"""The gallery in Tk: the front end for Windows, and for Linux systems without GTK 4 + libadwaita.

Tk is part of every Python install, so nothing extra is needed. It has the same three pages as the GTK
one (`gtk_app.py`): My widgets, Add and Settings, and the same per-widget settings window, built from
the widget's widget.json. Right-clicking a widget runs `deskwidgets configure <id>`, which opens just
that window. Both front ends talk to the service through `common.py`.
"""
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font
import uuid
from datetime import date
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..core import paths, settings as settings_mod
from ..core.catalog import ACCENTS, CATEGORIES, category_name, describe, option_values, size_name, styles
from ..core.i18n import loc, set_lang, tr
from ..platform import integration
from . import common
from .common import call, instance_title, plural_widgets, read_layout, thumbnail_path

WIDGET_SIZE = (50, 120)   # percent
PAD = 12


def photo(path, max_w=None, max_h=None):
    """A PNG as a Tk image, shrunk by a whole factor to fit in max_w x max_h; None if it cannot be read."""
    try:
        image = tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None
    if max_w and max_h:
        factor = max(1, -(-image.width() // max_w), -(-image.height() // max_h))
        if factor > 1:
            image = image.subsample(factor)
    return image


def make_dpi_aware():
    """Windows scales a DPI-unaware window as a blurry bitmap."""
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def raise_existing(title):
    """Windows: a second hotkey press brings the open window forward instead of opening another one."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    except (AttributeError, OSError):
        pass
    return False


def open_folder(folder):
    os.makedirs(folder, exist_ok=True)
    if sys.platform == 'win32':
        os.startfile(folder)   # noqa: S606 - a folder we just made
    else:
        subprocess.Popen(['xdg-open', str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Scrolled(ttk.Frame):
    """A frame that scrolls vertically; put the content in `.body`."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(window, width=e.width))
        for widget in (self.canvas, self.body):
            widget.bind('<Enter>', self._grab_wheel)
            widget.bind('<Leave>', self._release_wheel)

    def _grab_wheel(self, _e):
        self.canvas.bind_all('<MouseWheel>', self._wheel)
        self.canvas.bind_all('<Button-4>', lambda _e: self.canvas.yview_scroll(-2, 'units'))
        self.canvas.bind_all('<Button-5>', lambda _e: self.canvas.yview_scroll(2, 'units'))

    def _release_wheel(self, _e):
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.canvas.unbind_all(sequence)

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')


def section(parent, title, hint=None):
    frame = ttk.LabelFrame(parent, text=title, padding=PAD)
    frame.pack(fill='x', padx=PAD, pady=(PAD, 0))
    frame.columnconfigure(1, weight=1)
    if hint:
        ttk.Label(frame, text=hint, wraplength=480, justify='left', foreground='gray40').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))
        frame.rows = 1
    else:
        frame.rows = 0
    return frame


def labelled(frame, label):
    """Next row of a section: the label on the left; returns the row number for the control."""
    row = frame.rows
    frame.rows += 1
    ttk.Label(frame, text=label, wraplength=200, justify='left').grid(row=row, column=0, sticky='nw', padx=(0, PAD), pady=4)
    return row


def settings_title(t):
    return tr(f'{t.title} — налаштування', f'{t.title} — settings')


class SettingsWindow(tk.Toplevel):
    """One widget's settings. Every change is sent to the service at once (after a short pause for text),
    so it shows on the desktop while you edit."""

    def __init__(self, app, inst, t, on_close=None):
        super().__init__(app.root)
        self.title(settings_title(t))
        self.geometry('580x760')
        self.minsize(480, 520)
        self.app, self.inst, self.t, self.on_close = app, json.loads(json.dumps(inst)), t, on_close
        self.pending, self.flush_job, self.preview_job, self.preview_proc = {}, None, None, None
        self.closed = False
        self.keep = []   # Tk variables and images must stay referenced

        self.picture = ttk.Label(self, anchor='center')
        self.picture.pack(fill='x', padx=PAD, pady=(PAD, 0))
        page = Scrolled(self)
        page.pack(fill='both', expand=True)
        body = page.body

        look = section(body, tr('Вигляд', 'Look'), tr('Зміни одразу видно на робочому столі.', 'Changes show on the desktop right away.'))
        if len(t.sizes) > 1:
            self.choice(look, tr('Розмір', 'Size'), [(k, size_name(k)) for k in t.sizes], inst['size'], 'size')
        shown = [m for m in common.monitors() if m.get('enabled')]
        if len(shown) > 1:
            self.choice(look, tr('Монітор', 'Monitor'), [(m['id'], m['label']) for m in shown], inst.get('monitor'), 'monitor')
        self.choice(look, tr('Стиль', 'Style'), styles(), inst['style'], 'style')
        self.accent_row(look, inst.get('accent') or 'auto')

        if t.options:
            hints = [f'• {o.title}: {o.hint_text}' for o in t.options if o.hint_text and o.type not in ('longtext', 'file')]
            params = section(body, tr('Параметри', 'Options'), '\n'.join(hints) or None)
            values = option_values(inst, t)
            for opt in t.options:
                self.option_row(params, opt, values.get(opt.key, opt.default_value()))

        danger = ttk.Frame(body)
        danger.pack(pady=PAD * 2)
        ttk.Button(danger, text=tr('Прибрати з робочого столу', 'Remove from the desktop'), command=self.remove).pack()

        self.bind('<Escape>', lambda _e: self.close())
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.render_preview()

    # controls
    def choice(self, frame, label, pairs, current, key, option=False):
        keys = [k for k, _n in pairs]
        names = [loc(n) for _k, n in pairs]
        row = labelled(frame, label)
        var = tk.StringVar(value=names[keys.index(current)] if current in keys else names[0])
        box = ttk.Combobox(frame, textvariable=var, values=names, state='readonly')
        box.grid(row=row, column=1, sticky='ew')
        box.bind('<<ComboboxSelected>>', lambda _e: self.send(
            {'options': {key: keys[names.index(var.get())]}} if option else {key: keys[names.index(var.get())]}))
        self.keep.append(var)

    def accent_row(self, frame, current):
        row = labelled(frame, tr('Колір', 'Colour'))
        swatches = ttk.Frame(frame)
        swatches.grid(row=row, column=1, sticky='w')
        cells = {}

        def pick(key):
            for k, cell in cells.items():
                cell.configure(highlightbackground='#222222' if k == key else '#d0d0d0', highlightthickness=3 if k == key else 1)
            self.send({'accent': key})

        for i, (key, color) in enumerate(ACCENTS):
            cell = tk.Label(swatches, width=2, height=1, bg=color or '#eeeeee', text='' if color else 'A', cursor='hand2',
                            highlightthickness=1, highlightbackground='#d0d0d0')
            cell.grid(row=i // 7, column=i % 7, padx=2, pady=2)
            cell.bind('<Button-1>', lambda _e, k=key: pick(k))
            cells[key] = cell
        for k, cell in cells.items():
            if k == current:
                cell.configure(highlightbackground='#222222', highlightthickness=3)

    def option_row(self, frame, opt, value):
        key = opt.key
        if opt.type == 'bool':
            row = labelled(frame, opt.title)
            var = tk.BooleanVar(value=value in (True, 'true', '1', 1))
            ttk.Checkbutton(frame, variable=var, command=lambda: self.send({'options': {key: var.get()}})).grid(row=row, column=1, sticky='w')
            self.keep.append(var)
        elif opt.type in ('text', 'date'):
            row = labelled(frame, opt.title)
            var = tk.StringVar(value=str(value or ''))
            entry = ttk.Entry(frame, textvariable=var)
            entry.grid(row=row, column=1, sticky='ew')
            var.trace_add('write', lambda *_a: self.on_text(entry, var, opt))
            self.keep.append(var)
        elif opt.type == 'number':
            row = labelled(frame, opt.title)
            var = tk.StringVar(value=str(value))
            spin = ttk.Spinbox(frame, from_=opt.min, to=opt.max, increment=opt.step, textvariable=var, width=10)
            spin.grid(row=row, column=1, sticky='w')

            def changed(*_a):
                try:
                    self.send({'options': {key: float(var.get())}}, delay=500)
                except ValueError:
                    pass
            var.trace_add('write', changed)
            self.keep.append(var)
        elif opt.type == 'choice':
            self.choice(frame, opt.title, [(v, l) for v, l in opt.choices], value, key, option=True)
        elif opt.type == 'multi':
            row = labelled(frame, opt.title)
            holder = ttk.Frame(frame)
            holder.grid(row=row, column=1, sticky='w')
            chosen = {v for v in str(value or '').split(',') if v}
            switches = {}

            def toggled():
                self.send({'options': {key: ','.join(v for v, _l in opt.choices if switches[v].get())}})
            for v, label in opt.choices:
                switches[v] = tk.BooleanVar(value=v in chosen)
                ttk.Checkbutton(holder, text=loc(label), variable=switches[v], command=toggled).pack(anchor='w')
            self.keep.append(switches)
        elif opt.type == 'file':
            row = labelled(frame, opt.title)
            holder = ttk.Frame(frame)
            holder.grid(row=row, column=1, sticky='ew')
            name = tk.StringVar(value=os.path.basename(str(value)) if value else tr('не вибрано', 'none'))
            ttk.Label(holder, textvariable=name).pack(side='left')

            def choose():
                path = filedialog.askopenfilename(parent=self, title=opt.title, filetypes=[
                    (tr('Зображення', 'Images'), '*.png *.jpg *.jpeg *.gif *.bmp *.webp'), (tr('Усі файли', 'All files'), '*.*')])
                if path:
                    name.set(os.path.basename(path))
                    self.send({'options': {key: path}})

            def clear():
                name.set(tr('не вибрано', 'none'))
                self.send({'options': {key: ''}})
            ttk.Button(holder, text=tr('Прибрати', 'Clear'), command=clear).pack(side='right')
            ttk.Button(holder, text=tr('Обрати…', 'Choose…'), command=choose).pack(side='right', padx=4)
            self.keep.append(name)
        elif opt.type == 'longtext':
            sub = ttk.Frame(frame)
            sub.grid(row=frame.rows, column=0, columnspan=2, sticky='ew', pady=4)
            frame.rows += 1
            ttk.Label(sub, text=opt.title + (f' — {opt.hint_text}' if opt.hint_text else ''), wraplength=480, justify='left').pack(anchor='w')
            text = tk.Text(sub, height=6, wrap='word', undo=True)
            text.insert('1.0', str(value or ''))
            text.pack(fill='x', pady=(4, 0))
            text.bind('<KeyRelease>', lambda _e: self.send({'options': {key: text.get('1.0', 'end-1c')}}, delay=700))

    def on_text(self, entry, var, opt):
        text = var.get().strip()
        if opt.type == 'date' and text:
            try:
                date.fromisoformat(text)
            except ValueError:
                entry.configure(foreground='#c01c28')
                return
        entry.configure(foreground='')
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
        if self.flush_job:
            self.after_cancel(self.flush_job)
            self.flush_job = None
        if delay:
            self.flush_job = self.after(delay, self.flush)
        else:
            self.flush()

    def flush(self):
        self.flush_job = None
        if self.pending:
            self.app.call('configure', id=self.inst['id'], patch=self.pending)
            self.pending = {}
            self.render_preview()

    def remove(self):
        self.pending = {}
        self.app.call('remove', id=self.inst['id'])
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.flush_job:
            self.after_cancel(self.flush_job)
        self.flush()
        self.cancel_preview()
        self.destroy()
        if self.on_close:
            self.on_close()

    # a picture of exactly these settings, made by a child process (Cairo fonts are fixed per process)
    def cancel_preview(self):
        if self.preview_job:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if self.preview_proc is not None:
            if self.preview_proc.poll() is None:
                self.preview_proc.kill()
                self.preview_proc.wait()
            self.preview_proc = None

    def render_preview(self):
        self.cancel_preview()
        if not self.closed:
            self.preview_job = self.after(250, self._render)

    def _render(self):
        self.preview_job = None
        paths.RUN.mkdir(parents=True, exist_ok=True)
        out = paths.RUN / f'settings-preview-{uuid.uuid4().hex}.png'
        inst = json.loads(json.dumps(self.inst))
        proc = self.preview_proc = common.start_preview(
            self.t.id, out, inst['size'], inst['style'], inst.get('accent'), inst.get('options'), settings_mod.language())

        def done():
            if self.closed:
                out.unlink(missing_ok=True)
                return
            if proc.poll() is None:
                self.preview_job = self.after(150, done)
                return
            self.preview_job = None
            if proc is self.preview_proc and proc.returncode == 0 and out.exists():
                image = photo(out, 520, 300)
                if image is not None:
                    self.picture.configure(image=image)
                    self.picture.image = image
            out.unlink(missing_ok=True)
        self.preview_job = self.after(150, done)


class Gallery:
    def __init__(self, configure=None):
        self.root = tk.Tk()
        self.root.title(tr('Віджети', 'Widgets'))
        self.settings = settings_mod.load()
        self.types = common.all_types()
        self.windows = {}
        self.setting_job = None
        self.thumbnail_proc = None
        self.vars = []   # Tk variables must stay referenced
        self.poll_job = None
        self.first_run()
        try:
            icon = tk.PhotoImage(file=str(paths.ICON_PNG))
            self.root.iconphoto(True, icon)
            self.icon = icon
        except tk.TclError:
            pass
        style = ttk.Style()
        for theme in ('vista', 'clam'):   # the native look on Windows, the least dated one elsewhere
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        if configure:
            self.root.withdraw()
            if not self.open_settings(configure, on_close=self.root.destroy):
                self.root.destroy()
        else:
            self.root.geometry('960x720')
            self.root.minsize(760, 520)
            self.build()
            self.root.bind('<Escape>', lambda _e: self.shutdown())
            if not all(thumbnail_path(t).exists() for t in self.types):
                self.render_thumbnails()

    def first_run(self):
        if not self.settings.get('_setup_done'):
            integration.setup(autostart=self.settings['autostart'], hotkey=self.settings['hotkey'])
            self.settings['_setup_done'] = True
            settings_mod.save(self.settings)

    def run(self):
        self.root.mainloop()
        return 0

    def shutdown(self):
        """Closes every window and cancels what was scheduled (the process normally just ends)."""
        for win in list(self.windows.values()):
            if win.winfo_exists():
                win.close()
        for job in (self.poll_job, self.setting_job):
            if job:
                self.root.after_cancel(job)
        self.poll_job = self.setting_job = None
        if self.root.winfo_exists():
            self.root.destroy()

    def call(self, command, **args):
        """Asks the service to do something; returns (ok, message) and shows a failure to the user."""
        ok, answer = call(command, **args)
        if not ok:
            self.toast(str(answer), error=True)
        elif command == 'reload':
            self.reload_catalog()
        return ok, answer

    def toast(self, text, error=False):
        if hasattr(self, 'status'):
            self.status.set(text)
            self.root.after(6000, lambda: self.status.get() == text and self.status.set(''))
        elif error:
            messagebox.showerror(tr('Віджети', 'Widgets'), text)

    # the window
    def build(self):
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.vars = []
        for child in self.root.winfo_children():
            child.destroy()
        self.status = tk.StringVar()
        self.book = ttk.Notebook(self.root)
        self.book.pack(fill='both', expand=True)
        self.mine_page, self.add_page = ttk.Frame(self.book), ttk.Frame(self.book)
        self.prefs_page = Scrolled(self.book)
        self.book.add(self.mine_page, text=tr('Мої віджети', 'My widgets'))
        self.book.add(self.add_page, text=tr('Додати', 'Add'))
        self.book.add(self.prefs_page, text=tr('Налаштування', 'Settings'))
        ttk.Label(self.root, textvariable=self.status, anchor='w', padding=(PAD, 4)).pack(fill='x')
        self.build_mine()
        self.build_add()
        self.build_prefs(self.prefs_page.body)

    # my widgets
    def build_mine(self):
        page = self.mine_page
        self.mine_title = ttk.Label(page, font=('', 12, 'bold'), padding=PAD)
        self.mine_title.pack(anchor='w')
        self.mine = ttk.Treeview(page, columns=('about',), show='tree headings', selectmode='browse')
        self.mine.heading('#0', text=tr('Віджет', 'Widget'))
        self.mine.heading('about', text=tr('Розмір · стиль · колір', 'Size · style · colour'))
        self.mine.column('#0', width=300)
        self.mine.column('about', width=360)
        self.mine.pack(fill='both', expand=True, padx=PAD)
        self.mine.bind('<Double-1>', lambda _e: self.configure_selected())
        self.mine.bind('<Return>', lambda _e: self.configure_selected())
        bar = ttk.Frame(page, padding=PAD)
        bar.pack(fill='x')
        ttk.Button(bar, text=tr('Налаштування…', 'Settings…'), command=self.configure_selected).pack(side='left')
        ttk.Button(bar, text=tr('Прибрати', 'Remove'), command=self.remove_selected).pack(side='left', padx=PAD)
        ttk.Button(bar, text=tr('Розставити по сітці', 'Arrange on the grid'), command=lambda: self.call('arrange')).pack(side='right')
        self.empty = ttk.Label(page, text=tr('Тут поки нічого немає. Додайте віджет у вкладці «Додати».',
                                             'Nothing here yet. Add a widget on the “Add” tab.'), padding=PAD)
        self.refresh_mine()
        self.poll_job = self.root.after(2000, self.poll_mine)

    def refresh_mine(self):
        self.mine.delete(*self.mine.get_children())
        shown = 0
        for inst in self.my_instances():
            self.mine.insert('', 'end', iid=inst['id'], text=instance_title(inst, self.types[inst['type']]), values=(describe(inst),))
            shown += 1
        self.mine_title.configure(text=plural_widgets(shown) if shown else '')
        if shown:
            self.empty.pack_forget()
        else:
            self.empty.pack(before=self.mine, anchor='w')
        self.listed = self.signature()

    def my_instances(self):
        return [i for i in read_layout() if i.get('type') in self.types and isinstance(i.get('id'), str)]

    def signature(self):
        return [(i['id'], describe(i)) for i in self.my_instances()]

    def poll_mine(self):
        """The layout changes under us (a drag on the desktop, the CLI, a settings window): keep the list current."""
        self.poll_job = None
        try:
            if self.signature() != self.listed:
                self.refresh_mine()
            self.poll_job = self.root.after(2000, self.poll_mine)
        except tk.TclError:
            pass   # the window is gone

    def selected_widget(self):
        chosen = self.mine.selection()
        return chosen[0] if chosen else None

    def configure_selected(self):
        inst_id = self.selected_widget()
        if inst_id:
            self.open_settings(inst_id)

    def remove_selected(self):
        inst_id = self.selected_widget()
        if inst_id and self.call('remove', id=inst_id)[0]:
            self.refresh_mine()

    def open_settings(self, inst_id, on_close=None):
        inst = next((i for i in read_layout() if i.get('id') == inst_id), None)
        if inst is None or inst.get('type') not in self.types:
            return False
        win = self.windows.get(inst_id)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return True

        def closed():
            self.windows.pop(inst_id, None)
            if on_close:
                on_close()
            elif hasattr(self, 'mine'):
                self.refresh_mine()
        win = self.windows[inst_id] = SettingsWindow(self, inst, self.types[inst['type']], on_close=closed)
        win.focus_force()
        return True

    # add
    def build_add(self):
        page = self.add_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.catalog = ttk.Treeview(page, show='tree', selectmode='browse')
        self.catalog.grid(row=0, column=0, sticky='nsew', padx=(PAD, 0), pady=PAD)
        side = ttk.Frame(page, padding=PAD, width=340)
        side.grid(row=0, column=1, sticky='ns')
        side.grid_propagate(False)
        self.preview = ttk.Label(side, anchor='center')
        self.preview.pack(fill='x')
        self.detail_title = ttk.Label(side, font=('', 12, 'bold'), wraplength=300, justify='left')
        self.detail_title.pack(anchor='w', pady=(PAD, 0))
        self.detail_text = ttk.Label(side, wraplength=300, justify='left')
        self.detail_text.pack(anchor='w', pady=4)
        self.detail_sizes = ttk.Label(side, wraplength=300, justify='left', foreground='gray40')
        self.detail_sizes.pack(anchor='w')
        ttk.Button(side, text=tr('Додати на робочий стіл', 'Add to the desktop'), command=self.add_selected).pack(anchor='w', pady=PAD)
        self.catalog.bind('<<TreeviewSelect>>', lambda _e: self.show_type())
        self.catalog.bind('<Double-1>', lambda _e: self.add_selected())
        self.fill_catalog()

    def fill_catalog(self):
        self.catalog.delete(*self.catalog.get_children())
        for cat in CATEGORIES:
            members = sorted((t for t in self.types.values() if t.category == cat), key=lambda t: t.title.lower())
            if not members:
                continue
            self.catalog.insert('', 'end', iid=f'cat:{cat}', text=category_name(cat), open=True)
            for t in members:
                self.catalog.insert(f'cat:{cat}', 'end', iid=t.id, text=t.title)
        first = next((t for t in self.types.values()), None)
        if first:
            self.show_type(first.id)

    def selected_type(self):
        chosen = self.catalog.selection()
        return self.types.get(chosen[0]) if chosen else None

    def show_type(self, type_id=None):
        t = self.types.get(type_id) if type_id else self.selected_type()
        if t is None:
            return
        self.shown = t
        self.detail_title.configure(text=t.title)
        self.detail_text.configure(text=t.desc)
        self.detail_sizes.configure(text=' · '.join(size_name(s) for s in t.sizes))
        image = photo(thumbnail_path(t.id), 300, 200) if thumbnail_path(t.id).exists() else None
        self.preview.configure(image=image or '')
        self.preview.image = image

    def add_selected(self):
        t = self.selected_type() or getattr(self, 'shown', None)
        if t is None:
            return
        ok, answer = self.call('add', type=t.id)
        if ok:
            self.toast(tr(f'«{t.title}» додано на робочий стіл', f'“{t.title}” added to the desktop'))
            self.refresh_mine()

    def reload_catalog(self):
        self.types = common.all_types()
        if hasattr(self, 'catalog'):
            self.fill_catalog()
            self.refresh_mine()

    # settings
    def build_prefs(self, body):
        s = self.settings
        if common.service_outdated():
            self.build_outdated(body)
        look = section(body, tr('Вигляд', 'Appearance'))
        self.pref_choice(look, tr('Мова', 'Language'), [('auto', tr('Як у системі', 'System')), ('uk', 'Українська'), ('en', 'English')],
                         s.get('language', 'auto'), lambda v: self.change_setting('language', v, rebuild=True))
        row = labelled(look, tr('Розмір віджетів, % (сітка підлаштовується сама)', 'Widget size, % (the grid adjusts itself)'))
        size = tk.StringVar(value=str(min(max(round(float(s['scale']) * 100), WIDGET_SIZE[0]), WIDGET_SIZE[1])))
        ttk.Spinbox(look, from_=WIDGET_SIZE[0], to=WIDGET_SIZE[1], increment=5, textvariable=size, width=8).grid(
            row=row, column=1, sticky='w')
        size.trace_add('write', lambda *_a: self.size_changed(size))
        self.keep_var(size)
        families = ['auto'] + sorted(set(tkinter.font.families()))
        self.pref_choice(look, tr('Шрифт (після перезапуску)', 'Font (after a restart)'),
                         [(f, tr('Як у системі', 'System') if f == 'auto' else f) for f in families], s['font'],
                         lambda v: self.change_setting('font', v))
        self.pref_choice(look, tr('Стиль нових віджетів', 'Style for new widgets'),
                         [('auto', tr('Як задумано у віджеті', 'Each widget’s own'))] + styles(), s['default_style'],
                         lambda v: self.change_setting('default_style', v, apply=False))

        monitors = common.monitors()
        if len(monitors) > 1:
            self.build_monitors(body, monitors)

        system = section(body, tr('Система', 'System'))
        hotkey = 'Ctrl+Alt+W' if sys.platform == 'win32' else 'Super+Alt+W'
        for key, title, action in (
                ('autostart', tr('Запускати під час входу', 'Start at login'), integration.set_autostart),
                ('hotkey', tr(f'Гаряча клавіша {hotkey} відкриває це вікно', f'Hotkey {hotkey} opens this window')
                 + (tr(' (після перезапуску)', ' (after a restart)') if sys.platform == 'win32' else ''),
                 integration.set_hotkey),
                ('show_grid', tr('Показувати сітку під час перетягування', 'Show the grid while dragging'), None),
                ('lock', tr('Заблокувати віджети (не перетягуються мишею)', 'Lock the widgets (no dragging with the mouse)'), None)):
            var = tk.BooleanVar(value=bool(s[key]))
            self.keep_var(var)
            ttk.Checkbutton(system, text=title, variable=var, command=lambda k=key, v=var, a=action: self.toggle(k, v, a)).grid(
                row=system.rows, column=0, columnspan=2, sticky='w', pady=3)
            system.rows += 1
        row = system.rows
        bar = ttk.Frame(system)
        bar.grid(row=row, column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(bar, text=tr('Перечитати віджети', 'Reload widgets'), command=lambda: self.call('reload')).pack(side='left')

        own = section(body, tr('Свої віджети', 'Your own widgets'),
                      tr('Віджет — це тека з widget.json і widget.lua. Інструкція: docs/creating-widgets.md',
                         'A widget is a folder with widget.json and widget.lua. Guide: docs/creating-widgets.md'))
        bar = ttk.Frame(own)
        bar.grid(row=own.rows, column=0, columnspan=2, sticky='w')
        ttk.Button(bar, text=tr('Тека моїх віджетів', 'My widgets folder'), command=lambda: open_folder(paths.USER_WIDGETS)).pack(side='left')
        ttk.Button(bar, text=tr('Створити віджет…', 'Create a widget…'), command=self.create_widget).pack(side='left', padx=PAD)
        ttk.Label(body, text=f'DeskWidgets {common.VERSION}', foreground='gray50').pack(pady=PAD * 2)

    def build_outdated(self, body):
        """The service was started before an upgrade and runs the old code, so it cannot list the monitors (or
        use whatever else is new). One click restarts it."""
        frame = ttk.LabelFrame(body, padding=PAD)
        frame.pack(fill='x', padx=PAD, pady=(PAD, 0))
        ttk.Label(frame, text=tr('Сервіс працює зі старою версією. Перезапустіть його, щоб з’явилися всі налаштування, '
                                 'зокрема монітори.',
                                 'The service is running an old version. Restart it to get all settings, monitors included.'),
                  wraplength=480, justify='left').pack(anchor='w')
        button = ttk.Button(frame, text=tr('Перезапустити', 'Restart'))
        button.pack(anchor='w', pady=(6, 0))

        def restart():
            button.configure(state='disabled', text=tr('Перезапуск…', 'Restarting…'))
            done = []
            threading.Thread(target=lambda: done.append(common.restart_service()), daemon=True).start()

            def poll():
                try:
                    if not done:
                        self.root.after(200, poll)
                        return
                    self.build()   # again, now that the service is the new one
                    self.book.select(self.prefs_page)
                except tk.TclError:
                    pass   # the window was closed meanwhile
            self.root.after(200, poll)
        button.configure(command=restart)

    def build_monitors(self, body, monitors):
        """One check box per connected monitor: widgets are shown on the ticked ones, each with its own grid."""
        frame = section(body, tr('Монітори', 'Monitors'),
                        tr('Віджети показуються на вибраних моніторах. Перетягніть віджет на інший монітор '
                           'або виберіть монітор у його налаштуваннях.',
                           'Widgets are shown on the ticked monitors. Drag a widget to another monitor '
                           'or pick one in its settings.'))
        ticks = {}

        def toggled(monitor_id):
            chosen = [m for m, var in ticks.items() if var.get()]
            if not chosen:   # the last monitor cannot be switched off: the widgets would have nowhere to be
                ticks[monitor_id].set(True)
                self.toast(tr('Хоча б один монітор має лишитися', 'At least one monitor has to stay on'), error=True)
                return
            self.change_setting('monitors', chosen, delay=400)

        for m in monitors:
            ticks[m['id']] = var = tk.BooleanVar(value=bool(m.get('enabled')))
            self.keep_var(var)
            ttk.Checkbutton(frame, text=m['label'], variable=var, command=lambda mid=m['id']: toggled(mid)).grid(
                row=frame.rows, column=0, columnspan=2, sticky='w', pady=3)
            frame.rows += 1

    def size_changed(self, var):
        try:
            percent = int(float(var.get()))
        except ValueError:
            return
        self.change_setting('scale', min(max(percent, WIDGET_SIZE[0]), WIDGET_SIZE[1]) / 100, delay=700)

    def keep_var(self, var):
        self.vars.append(var)

    def pref_choice(self, frame, label, pairs, current, on_change):
        keys = [k for k, _n in pairs]
        names = [str(n) for _k, n in pairs]
        row = labelled(frame, label)
        var = tk.StringVar(value=names[keys.index(current)] if current in keys else names[0])
        box = ttk.Combobox(frame, textvariable=var, values=names, state='readonly')
        box.grid(row=row, column=1, sticky='ew')
        box.bind('<<ComboboxSelected>>', lambda _e: on_change(keys[names.index(var.get())]))
        self.keep_var(var)

    def toggle(self, key, var, action):
        if action:
            try:
                action(var.get())
            except OSError as e:
                self.toast(str(e), error=True)
        self.change_setting(key, var.get(), apply=action is None)

    def change_setting(self, key, value, apply=True, rebuild=False, delay=0):
        self.settings[key] = value
        settings_mod.save(self.settings)
        if rebuild:   # language: build the window again in the new language
            set_lang(settings_mod.language(self.settings))
            self.call('settings')
            self.root.title(tr('Віджети', 'Widgets'))
            self.build()
            self.render_thumbnails()
            return
        if not apply:
            return
        if self.setting_job:
            self.root.after_cancel(self.setting_job)

        def send():
            self.setting_job = None
            self.call('settings')
        self.setting_job = self.root.after(delay or 50, send)

    def create_widget(self):
        name = simpledialog.askstring(tr('Новий віджет', 'New widget'),
                                      tr('Назва латиницею, наприклад my_widget', 'A name in latin letters, e.g. my_widget'),
                                      parent=self.root)
        if not name or not name.strip():
            return
        from ..core import scaffold
        try:
            folder = scaffold.create(name.strip())
        except FileExistsError:
            self.toast(tr('Такий віджет уже є', 'That widget already exists'), error=True)
            return
        except (OSError, ValueError) as e:
            self.toast(str(e), error=True)
            return
        self.call('reload')
        self.book.select(self.add_page)
        open_folder(folder)
        self.toast(tr('Створено. Натисніть «Додати на робочий стіл», щоб розмістити його.',
                      'Created. Click “Add to the desktop” to place it.'))

    # thumbnails come from a child process (see gallery/common.py)
    def render_thumbnails(self):
        if self.thumbnail_proc is not None and self.thumbnail_proc.poll() is None:
            self.thumbnail_proc.wait()
        proc = self.thumbnail_proc = common.start_thumbnails()

        def done():
            try:
                if proc.poll() is None:
                    self.root.after(250, done)
                elif hasattr(self, 'catalog') and self.catalog.winfo_exists():
                    self.show_type(getattr(getattr(self, 'shown', None), 'id', None))
            except tk.TclError:
                pass
        self.root.after(250, done)


def main(argv=None):
    set_lang(settings_mod.language())
    args = list(argv or [])
    widget = args[args.index('--configure') + 1] if '--configure' in args and args.index('--configure') + 1 < len(args) else None
    if widget:   # a second right click on the same widget brings its window forward
        inst = next((i for i in read_layout() if i.get('id') == widget), None)
        t = common.all_types().get(inst.get('type')) if inst else None
        title = settings_title(t) if t else None
    else:
        title = tr('Віджети', 'Widgets')
    if title and raise_existing(title):
        return 0
    make_dpi_aware()
    return Gallery(widget).run()
