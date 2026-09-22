"""Command line: `deskwidgets [command]` (no command = run the service)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from . import VERSION, catalog, paths, settings as settings_mod
from .i18n import set_lang, tr


def _bus():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    return Gio, GLib, Gio.bus_get_sync(Gio.BusType.SESSION)


def service_running():
    Gio, GLib, bus = _bus()
    reply = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                          GLib.Variant('(s)', (paths.APP_ID,)), GLib.VariantType('(b)'), Gio.DBusCallFlags.NONE, -1, None)
    return reply.unpack()[0]


def ensure_service():
    if service_running():
        return True
    subprocess.Popen([paths.bin_path(), 'run'], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.1)
        if service_running():
            return True
    return False


def remote(action, param=None):
    """Activates an action of the running service (starting it if needed)."""
    if not ensure_service():
        print(tr('Сервіс DeskWidgets не запустився', 'The DeskWidgets service did not start'), file=sys.stderr)
        return 1
    Gio, GLib, bus = _bus()
    Gio.DBusActionGroup.get(bus, paths.APP_ID, paths.APP_PATH).activate_action(action, param)
    bus.flush_sync(None)
    return 0


def _layout():
    try:
        with open(paths.LAYOUT, encoding='utf-8') as f:
            return json.load(f).get('instances', [])
    except (OSError, ValueError):
        return []


def cmd_run(_a):
    from .service import run
    return run()


def cmd_gallery(a):
    from .gallery import main
    return main(['--configure', a.id] if getattr(a, 'id', None) else [])


def cmd_add(a):
    from gi.repository import GLib
    return remote('add', GLib.Variant('s', a.type))


def cmd_remove(a):
    from gi.repository import GLib
    return remote('remove', GLib.Variant('s', a.id))


def cmd_simple(a):
    return remote(a.command)


def cmd_list(_a):
    for i in _layout():
        print(f"{i['id']:18} {i['type']:15} {i['size']:7} cell=({i['col']},{i['row']}) {i['style']}/{i['accent']}")
    return 0


def cmd_types(_a):
    for t in sorted(catalog.discover().values(), key=lambda t: (t.category, t.id)):
        where = tr('мій', 'mine') if t.user else tr('вбудований', 'built-in')
        print(f'{t.id:16} {t.title:28} {",".join(t.sizes):22} {catalog.category_name(t.category)} · {where}')
    return 0


def cmd_new(a):
    from . import scaffold
    try:
        folder = scaffold.create(a.id, a.name)
    except FileExistsError as e:
        print(tr('Вже існує: ', 'Already exists: ') + str(e), file=sys.stderr)
        return 1
    print(folder)
    print(tr(f'Відредагуйте widget.lua, потім: deskwidgets reload && deskwidgets add {os.path.basename(folder)}',
             f'Edit widget.lua, then: deskwidgets reload && deskwidgets add {os.path.basename(folder)}'))
    return 0


def cmd_preview(a):
    from . import engine
    types = catalog.discover()
    t = types.get(a.type)
    if t is None:
        print(tr('Немає такого віджета: ', 'No such widget: ') + a.type, file=sys.stderr)
        return 1
    s = settings_mod.load()
    paths.ensure_dirs()
    engine.write_fontconf(s)
    out = os.path.abspath(a.output or f'{t.id}-{a.size or t.size}.png')
    ok = engine.render(t, out, size=a.size, style=a.style, accent=a.accent, lang=a.lang or settings_mod.language(s),
                       scale=a.scale, demo=not a.live)
    print(out if ok else tr('Не вдалося відрендерити', 'Rendering failed'))
    return 0 if ok else 1


def render_thumbnails(types=None, lang=None):
    """Renders all thumbnails at once to ~/.cache/deskwidgets/previews/<version>/<lang>/<type>.png."""
    from . import engine
    s = settings_mod.load()
    lang = lang or settings_mod.language(s)
    paths.ensure_dirs()
    engine.write_fontconf(s)
    out_dir = os.path.join(paths.PREVIEWS, VERSION, lang)
    os.makedirs(out_dir, exist_ok=True)
    jobs = [(t, engine.render(t, os.path.join(out_dir, f'{t.id}.png'), lang=lang, wait=False))
            for t in (types or catalog.discover()).values()]
    ok = True
    for t, proc in jobs:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        ok &= os.path.exists(os.path.join(out_dir, f'{t.id}.png'))
    return ok


def cmd_previews(_a):
    return 0 if render_thumbnails() else 1


def cmd_setup(a):
    from . import desktop
    done = desktop.setup(autostart=not a.no_autostart, hotkey=not a.no_hotkey, extension=not a.no_extension)
    s = settings_mod.load()
    s.update({'autostart': not a.no_autostart, 'hotkey': not a.no_hotkey, 'grid_overlay': not a.no_extension,
              '_setup_done': True})
    settings_mod.save(s)
    for k, v in done.items():
        print(f'{k}: {"ok" if v else "-"}')
    if done.get('extension'):
        print(tr('Підказка-сітка GNOME запрацює після повторного входу в систему.',
                 'The GNOME grid helper starts working after you log out and back in.'))
    return 0


def udev_command():
    rules = os.path.join(paths.DATA, 'data', 'udev', '70-deskwidgets-hid.rules')
    return (f'sudo install -m 644 {rules} /etc/udev/rules.d/ && sudo udevadm control --reload '
            f'&& sudo udevadm trigger --subsystem-match=hidraw')


def cmd_mice(_a):
    from . import hidbattery
    code = hidbattery.main()
    if any(not d['access'] for d in hidbattery.find()):
        print(tr('Щоб читати заряд миші без root, один раз виконайте:', 'To read the mouse battery without root, run once:'))
        print('  ' + udev_command())
    return code


def cmd_doctor(_a):
    from . import desktop, hidbattery
    ok = True

    def row(name, good, note=''):
        nonlocal ok
        ok &= bool(good) or note.startswith('(')
        print(f"{'✔' if good else '✘'} {name}{(' — ' + note) if note else ''}")

    conky = shutil.which('conky')
    row('conky', conky, tr('встановіть conky-all', 'install conky-all') if not conky else '')
    if conky:
        info = subprocess.run(['conky', '-v'], capture_output=True, text=True).stdout
        row(tr('conky з Lua/Cairo', 'conky with Lua/Cairo'), 'Cairo' in info, tr('потрібен пакет conky-all', 'needs conky-all'))
    for mod, ver, pkg in (('Gtk', '4.0', 'gir1.2-gtk-4.0'), ('Adw', '1', 'gir1.2-adw-1'), ('GdkPixbuf', '2.0', 'gir1.2-gdkpixbuf-2.0'),
                          ('ECal', '2.0', 'gir1.2-ecal-2.0 (' + tr('лише для «Події»', 'only for "Up next"') + ')')):
        try:
            import gi
            gi.require_version(mod, ver)
            __import__('gi.repository.' + mod)
            row(f'{mod} {ver}', True)
        except (ImportError, ValueError):
            row(f'{mod} {ver}', False, pkg if '(' not in pkg else '(' + pkg)
    s = settings_mod.load()
    font = subprocess.run(['fc-list', s['font']], capture_output=True, text=True).stdout.strip()
    row(tr('шрифт ', 'font ') + s['font'], font, tr('буде запасний sans-serif', 'a fallback sans-serif will be used'))
    cjk = subprocess.run(['fc-list', ':lang=ja'], capture_output=True, text=True).stdout.strip()
    row(tr('японський шрифт (аніме-віджети)', 'Japanese font (anime widgets)'), cjk, '(fonts-noto-cjk)')
    for tool, why in (('nmap', tr('перевірка мережі', 'LAN watch')), ('docker', 'Docker'), ('upower', tr('батарея', 'batteries'))):
        row(tool, shutil.which(tool), f'({tr("необов’язково", "optional")}: {why})')
    for m in hidbattery.find():
        row(tr('заряд миші ', 'mouse battery ') + m['name'], m['access'],
            tr('потрібне правило udev, виконайте: ', 'needs the udev rule, run: ') + udev_command())
    row(tr('сервіс працює', 'service running'), service_running(), f'({tr("запустіть", "start it")}: deskwidgets)')
    row(tr('підказка-сітка GNOME', 'GNOME grid helper'), desktop.extension_state() == 'active', f'({desktop.extension_state()})')
    print(f'{tr("Віджети", "Widgets")}: {paths.BUILTIN_WIDGETS}  ·  {tr("мої", "mine")}: {paths.USER_WIDGETS}')
    return 0 if ok else 1


def main(argv=None):
    set_lang(settings_mod.language())
    p = argparse.ArgumentParser(prog='deskwidgets', description=tr(
        'Віджети робочого столу в стилі macOS (conky).', 'macOS-style desktop widgets for Linux (conky).'))
    p.add_argument('--version', action='version', version=f'deskwidgets {VERSION}')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('run', help=tr('запустити сервіс (за замовчуванням)', 'run the service (default)')).set_defaults(func=cmd_run)
    sub.add_parser('gallery', help=tr('відкрити галерею', 'open the gallery')).set_defaults(func=cmd_gallery)
    c = sub.add_parser('configure', help=tr('налаштування віджета', 'settings of a widget'))
    c.add_argument('id'); c.set_defaults(func=cmd_gallery)
    c = sub.add_parser('add', help=tr('додати віджет на стіл', 'add a widget to the desktop'))
    c.add_argument('type'); c.set_defaults(func=cmd_add)
    c = sub.add_parser('remove', help=tr('прибрати віджет', 'remove a widget'))
    c.add_argument('id'); c.set_defaults(func=cmd_remove)
    for name, h in (('arrange', tr('розставити по сітці', 'pack widgets onto the grid')),
                    ('reload', tr('перечитати віджети й перезапустити', 're-read widget folders and restart')),
                    ('quit', tr('зупинити сервіс', 'stop the service'))):
        sub.add_parser(name, help=h).set_defaults(func=cmd_simple)
    sub.add_parser('list', help=tr('віджети на столі', 'widgets on the desktop')).set_defaults(func=cmd_list)
    sub.add_parser('types', help=tr('усі доступні віджети', 'all available widgets')).set_defaults(func=cmd_types)
    c = sub.add_parser('new', help=tr('створити свій віджет', 'create your own widget'))
    c.add_argument('id'); c.add_argument('--name'); c.set_defaults(func=cmd_new)
    c = sub.add_parser('preview', help=tr('відрендерити віджет у PNG', 'render a widget to PNG'))
    c.add_argument('type'); c.add_argument('--size', choices=list(catalog.SIZES)); c.add_argument('--style')
    c.add_argument('--accent'); c.add_argument('--lang', choices=['uk', 'en']); c.add_argument('--scale', type=float, default=1.0)
    c.add_argument('--live', action='store_true', help=tr('справжні дані замість прикладу', 'real data instead of sample data'))
    c.add_argument('-o', '--output'); c.set_defaults(func=cmd_preview)
    sub.add_parser('previews', help=tr('оновити мініатюри галереї', 'refresh gallery thumbnails')).set_defaults(func=cmd_previews)
    c = sub.add_parser('setup', help=tr('автозапуск, гаряча клавіша, підказка-сітка', 'autostart, hotkey, grid helper'))
    c.add_argument('--no-autostart', action='store_true'); c.add_argument('--no-hotkey', action='store_true')
    c.add_argument('--no-extension', action='store_true'); c.set_defaults(func=cmd_setup)
    sub.add_parser('doctor', help=tr('перевірити залежності', 'check dependencies')).set_defaults(func=cmd_doctor)
    sub.add_parser('mice', help=tr('заряд бездротових ігрових мишей', 'battery of wireless gaming mice')).set_defaults(func=cmd_mice)
    a = p.parse_args(argv)
    if not getattr(a, 'func', None):
        return cmd_run(a)
    return a.func(a) or 0
