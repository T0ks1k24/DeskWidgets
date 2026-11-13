"""Command line: `deskwidgets [command]` (no command = run the service)."""
import argparse
import json
import sys
from pathlib import Path

from . import VERSION
from .core import catalog, paths, settings as settings_mod
from .core.i18n import set_lang, tr


def remote(command, **args):
    """Sends a command to the service (starting it if needed). Returns the answer, or None after printing why not."""
    from .service import ipc
    if not ipc.ensure_running():
        print(tr('Сервіс DeskWidgets не запустився (журнал: ', 'The DeskWidgets service did not start (log: ')
              + str(paths.CACHE / 'service.log') + ')', file=sys.stderr)
        return None
    try:
        return ipc.request(command, **args)
    except (RuntimeError, TimeoutError, ipc.ServiceNotRunning) as e:
        print(f'deskwidgets: {e}', file=sys.stderr)
        return None


def _remote_status(command, **args):
    """Exit code for commands that only need to be delivered."""
    from .service import ipc
    if not ipc.ensure_running():
        return 0 if remote(command, **args) is not None else 1
    try:
        ipc.request(command, **args)
        return 0
    except (RuntimeError, TimeoutError, ipc.ServiceNotRunning) as e:
        print(f'deskwidgets: {e}', file=sys.stderr)
        return 1


def _layout():
    try:
        return json.loads(paths.LAYOUT.read_text(encoding='utf-8')).get('instances', [])
    except (OSError, ValueError, AttributeError):
        return []


def cmd_run(_a):
    from .service.daemon import run
    return run()


def cmd_gallery(a):
    try:
        from .gallery import main as gallery_main
    except (ImportError, ValueError) as e:
        print(tr('Для галереї потрібні PyGObject, GTK 4 і libadwaita (', 'The gallery needs PyGObject, GTK 4 and libadwaita (')
              + f'{e}); deskwidgets doctor', file=sys.stderr)
        return 1
    return gallery_main(['--configure', a.id] if getattr(a, 'id', None) else [])


def cmd_add(a):
    return _remote_status('add', type=a.type)


def cmd_remove(a):
    return _remote_status('remove', id=a.id)


def cmd_simple(a):
    if a.command == 'quit':
        from .service import ipc
        if not ipc.is_running():
            return 0
    return _remote_status(a.command)


def cmd_notify(a):
    return _remote_status('notify', title=a.title, body=a.body)


def cmd_status(_a):
    from .service import ipc
    if not ipc.is_running():
        print(tr('Сервіс не запущено', 'The service is not running'))
        return 1
    print(json.dumps(ipc.request('status'), indent=2, ensure_ascii=False))
    return 0


def cmd_list(_a):
    for i in _layout():
        print(f"{i['id']:18} {i['type']:15} {i['size']:7} cell=({i['col']},{i['row']}) {i['style']}/{i['accent']}")
    return 0


def cmd_types(_a):
    for t in sorted(catalog.discover().values(), key=lambda t: (t.category, t.id)):
        where = tr('мій', 'mine') if t.user else tr('вбудований', 'built-in')
        note = '' if t.supported else '  [' + tr('не в цій системі', 'not on this system') + ']'
        print(f'{t.id:16} {t.title:28} {",".join(t.sizes):22} {catalog.category_name(t.category)} · {where}{note}')
    return 0


def cmd_new(a):
    from .core import scaffold
    try:
        folder = scaffold.create(a.id, a.name)
    except FileExistsError as e:
        print(tr('Вже існує: ', 'Already exists: ') + str(e), file=sys.stderr)
        return 1
    print(folder)
    print(tr(f'Відредагуйте widget.lua, потім: deskwidgets reload && deskwidgets add {Path(folder).name}',
             f'Edit widget.lua, then: deskwidgets reload && deskwidgets add {Path(folder).name}'))
    return 0


def cmd_preview(a):
    from .engine import render
    t = catalog.discover().get(a.type)
    if t is None:
        print(tr('Немає такого віджета: ', 'No such widget: ') + a.type, file=sys.stderr)
        return 1
    s = settings_mod.load()
    options = {}
    for item in a.option:
        key, _, text = item.partition('=')
        option = t.option(key)
        if option is None:
            print(tr('У віджета немає параметра ', 'The widget has no option ') + key, file=sys.stderr)
            return 1
        try:
            options[key] = option.coerce(text == 'true' if option.type == 'bool' else text)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    out = Path(a.output or f'{t.id}-{a.size or t.size}.png').resolve()
    ok = render.render_png(t, out, size=a.size, style=a.style, accent=a.accent, lang=a.lang or settings_mod.language(s),
                           scale=a.scale, demo=not a.live, settings=s, options=options)
    print(out if ok else tr('Не вдалося відрендерити', 'Rendering failed'))
    return 0 if ok else 1


def cmd_previews(a):
    """Gallery thumbnails, one PNG per widget (the gallery runs this in a separate process)."""
    from .engine import render
    lang = a.lang or settings_mod.language()
    types = catalog.discover()
    if a.only:
        types = {k: t for k, t in types.items() if k in a.only.split(',')}
    return 0 if render.render_all(paths.PREVIEWS / VERSION / lang, lang, types=types, settings=settings_mod.load()) else 1


def cmd_setup(a):
    from .platform import integration
    done = integration.setup(autostart=not a.no_autostart, hotkey=not a.no_hotkey)
    s = settings_mod.load()
    s.update({'autostart': not a.no_autostart, 'hotkey': not a.no_hotkey, '_setup_done': True})
    settings_mod.save(s)
    for k, v in done.items():
        print(f'{k}: {"ok" if v else "-"}')
    return 0


def udev_command():
    return (f'sudo install -m 644 {paths.UDEV_RULES} /etc/udev/rules.d/ && sudo udevadm control --reload '
            f'&& sudo udevadm trigger --subsystem-match=hidraw')


def cmd_mice(_a):
    from .providers import hid_mice
    code = hid_mice.main()
    if any(not d['access'] for d in hid_mice.find()):
        print(tr('Щоб читати заряд миші без root, один раз виконайте:', 'To read the mouse battery without root, run once:'))
        print('  ' + udev_command())
    return code


def cmd_doctor(_a):
    from .core.doctor import run_checks
    return run_checks()


def build_parser():
    p = argparse.ArgumentParser(prog='deskwidgets', description=tr(
        'Віджети робочого столу в стилі macOS для Linux і Windows.', 'macOS-style desktop widgets for Linux and Windows.'))
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
    sub.add_parser('status', help=tr('стан сервісу', 'state of the service')).set_defaults(func=cmd_status)
    sub.add_parser('list', help=tr('віджети на столі', 'widgets on the desktop')).set_defaults(func=cmd_list)
    sub.add_parser('types', help=tr('усі доступні віджети', 'all available widgets')).set_defaults(func=cmd_types)
    c = sub.add_parser('new', help=tr('створити свій віджет', 'create your own widget'))
    c.add_argument('id'); c.add_argument('--name'); c.set_defaults(func=cmd_new)
    c = sub.add_parser('preview', help=tr('відрендерити віджет у PNG', 'render a widget to PNG'))
    c.add_argument('type'); c.add_argument('--size', choices=list(catalog.SIZES)); c.add_argument('--style')
    c.add_argument('--accent'); c.add_argument('--lang', choices=['uk', 'en']); c.add_argument('--scale', type=float, default=1.0)
    c.add_argument('--live', action='store_true', help=tr('справжні дані замість прикладу', 'real data instead of sample data'))
    c.add_argument('--option', action='append', default=[], metavar='KEY=VALUE',
                   help=tr('значення параметра віджета (можна кілька разів)', 'a widget option value (repeatable)'))
    c.add_argument('-o', '--output'); c.set_defaults(func=cmd_preview)
    c = sub.add_parser('previews', help=tr('оновити мініатюри галереї', 'refresh gallery thumbnails'))
    c.add_argument('--lang', choices=['uk', 'en'])
    c.add_argument('--only', help=tr('лише ці віджети (через кому)', 'only these widgets (comma separated)'))
    c.set_defaults(func=cmd_previews)
    c = sub.add_parser('setup', help=tr('автозапуск, гаряча клавіша, пункт меню', 'autostart, hotkey, menu entry'))
    c.add_argument('--no-autostart', action='store_true'); c.add_argument('--no-hotkey', action='store_true')
    c.set_defaults(func=cmd_setup)
    c = sub.add_parser('notify', help=tr('показати сповіщення', 'show a notification'))
    c.add_argument('title'); c.add_argument('body', nargs='?', default=''); c.set_defaults(func=cmd_notify)
    sub.add_parser('doctor', help=tr('перевірити залежності', 'check dependencies')).set_defaults(func=cmd_doctor)
    sub.add_parser('mice', help=tr('заряд бездротових ігрових мишей', 'battery of wireless gaming mice')).set_defaults(func=cmd_mice)
    return p


def main(argv=None):
    set_lang(settings_mod.language())
    args = build_parser().parse_args(argv)
    if not getattr(args, 'func', None):
        return cmd_run(args) or 0
    return args.func(args) or 0
