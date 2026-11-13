"""Now playing on Linux: follows the MPRIS players on the session bus (Firefox, Spotify, VLC ...)."""
import hashlib
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ..core import paths
from ..core.util import clean, log
from . import glib_loop
from .base import Provider

MPRIS = 'org.mpris.MediaPlayer2'
MPRIS_PATH = '/org/mpris/MediaPlayer2'
COMMANDS = {'PlayPause', 'Play', 'Pause', 'Next', 'Previous', 'Stop'}
ART_LIMIT = 16 << 20


def player_name(identity):
    """Turns 'Mozilla firefox_firefox' (snap) into 'Firefox'."""
    name = identity.replace('Mozilla ', '').split('_')[0].strip()
    return name[:1].upper() + name[1:]


class Media(Provider):
    name = 'media'

    def __init__(self):
        super().__init__()
        self.players = {}   # well-known name -> state
        self.owners = {}    # unique bus name -> well-known name
        self.active_name = None
        self.art_url, self.art_path = None, ''
        self.write_id = 0
        self.subs = []
        self.bus = None

    def start(self):
        glib_loop.call_sync(self._setup)

    def stop(self):
        try:
            glib_loop.call_sync(self._teardown)
        except (TimeoutError, OSError):
            pass

    def control(self, command):
        if command not in COMMANDS or not self.active_name or self.bus is None:
            return False
        name = self.active_name
        glib_loop.call(self._call_player, name, command)
        return True

    # runs on the GLib thread
    def _setup(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        flags = Gio.DBusSignalFlags.NONE
        self.subs = [
            self.bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                                      '/org/freedesktop/DBus', None, flags, self._owner_changed),
            self.bus.signal_subscribe(None, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                                      MPRIS_PATH, None, flags, self._props_changed),
            self.bus.signal_subscribe(None, MPRIS + '.Player', 'Seeked', MPRIS_PATH, None, flags, self._seeked),
        ]
        self._dbus('ListNames', None, '(as)', self._got_names)
        self._write_now()

    def _teardown(self):
        for sid in self.subs:
            self.bus.signal_unsubscribe(sid)
        self.subs = []
        if self.write_id:
            self.GLib.source_remove(self.write_id)
            self.write_id = 0

    def _call_player(self, name, command):
        self.bus.call(name, MPRIS_PATH, MPRIS + '.Player', command, None, None, self.Gio.DBusCallFlags.NONE, 3000,
                      None, None)

    # D-Bus plumbing
    def _dbus(self, method, args, reply, callback):
        self.bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', method, args,
                      self.GLib.VariantType(reply), self.Gio.DBusCallFlags.NONE, -1, None, callback)

    def _props_call(self, name, method, args, reply, callback):
        self.bus.call(name, MPRIS_PATH, 'org.freedesktop.DBus.Properties', method, args,
                      self.GLib.VariantType(reply), self.Gio.DBusCallFlags.NONE, 3000, None, callback)

    def _finish(self, result):
        try:
            return self.bus.call_finish(result).unpack()
        except self.GLib.Error:
            return None   # player went away, or AppArmor denied the call

    # players
    def _got_names(self, _bus, result):
        names = self._finish(result)
        for name in (names[0] if names else []):
            if name.startswith(MPRIS + '.'):
                self._add(name)

    def _add(self, name):
        GLib = self.GLib

        def got_owner(_bus, result):
            owner = self._finish(result)
            if not owner:
                return
            self.owners[owner[0]] = name
            short = name[len(MPRIS) + 1:].split('.')[0]
            self.players[name] = {'name': name, 'owner': owner[0], 'status': 'Stopped', 'meta': {}, 'pos': 0.0,
                                  'ts': time.time(), 'rate': 1.0, 'identity': short.capitalize(),
                                  'changed': time.time()}
            self._props_call(name, 'GetAll', GLib.Variant('(s)', (MPRIS + '.Player',)), '(a{sv})',
                             lambda b, r: self._got_all(name, r))
            self._props_call(name, 'Get', GLib.Variant('(ss)', (MPRIS, 'Identity')), '(v)',
                             lambda b, r: self._got_identity(name, r))
        self._dbus('GetNameOwner', GLib.Variant('(s)', (name,)), '(s)', got_owner)

    def _got_all(self, name, result):
        props = self._finish(result)
        if props and name in self.players:
            self._apply(self.players[name], props[0])
            self._schedule()

    def _got_identity(self, name, result):
        value = self._finish(result)
        if value and name in self.players:
            self.players[name]['identity'] = value[0]
            self._schedule()

    def _owner_changed(self, _conn, _sender, _path, _iface, _signal, params):
        name, old, new = params.unpack()
        if not name.startswith(MPRIS + '.'):
            return
        if old:
            self.owners.pop(old, None)
            self.players.pop(name, None)
        if new:
            self._add(name)
        self._schedule()

    def _props_changed(self, _conn, sender, _path, _iface, _signal, params):
        iface, changed, _invalidated = params.unpack()
        name = self.owners.get(sender)
        if iface != MPRIS + '.Player' or name not in self.players:
            return
        self._apply(self.players[name], changed)
        if 'PlaybackStatus' in changed or 'Metadata' in changed:
            self._props_call(name, 'Get', self.GLib.Variant('(ss)', (MPRIS + '.Player', 'Position')), '(v)',
                             lambda b, r: self._got_position(name, r))
        self._schedule()

    def _got_position(self, name, result):
        value = self._finish(result)
        if value and name in self.players:
            p = self.players[name]
            p['pos'], p['ts'] = value[0] / 1e6, time.time()
            self._schedule()

    def _seeked(self, _conn, sender, _path, _iface, _signal, params):
        name = self.owners.get(sender)
        if name in self.players:
            p = self.players[name]
            p['pos'], p['ts'] = params.unpack()[0] / 1e6, time.time()
            self._schedule()

    @staticmethod
    def _apply(p, props):
        now = time.time()
        if 'PlaybackStatus' in props or 'Rate' in props:
            if p['status'] == 'Playing':
                p['pos'] += (now - p['ts']) * p['rate']
            # Resume starts extrapolating from now, never from before the pause.
            p['ts'] = now
        if 'Metadata' in props:
            meta = props['Metadata'] or {}
            if (meta.get('mpris:trackid'), meta.get('xesam:title')) != \
                    (p['meta'].get('mpris:trackid'), p['meta'].get('xesam:title')):
                p['pos'], p['ts'] = 0.0, now
            p['meta'] = meta
            p['changed'] = now
        if 'PlaybackStatus' in props:
            p['status'] = props['PlaybackStatus']
            p['changed'] = now
        if 'Rate' in props:
            p['rate'] = float(props['Rate'] or 1.0)
        if 'Position' in props:
            p['pos'], p['ts'] = props['Position'] / 1e6, now

    def _active(self):
        for status in ('Playing', 'Paused'):
            found = [p for p in self.players.values() if p['status'] == status and p['meta'].get('xesam:title')]
            if found:
                return max(found, key=lambda p: p['changed'])
        return None

    # the snapshot, debounced: a track change fires several signals
    def _schedule(self):
        if not self.write_id:
            self.write_id = self.GLib.timeout_add(150, self._write_now)

    def _write_now(self):
        self.write_id = 0
        p = self._active()
        self.active_name = p['name'] if p else None
        if not p:
            self.publish({'status': 'none'})
            return False
        meta = p['meta']
        artist = meta.get('xesam:artist') or ''
        if isinstance(artist, (list, tuple)):
            artist = ', '.join(artist)
        self.publish({
            'status': p['status'], 'player': clean(player_name(p['identity'])),
            'title': clean(meta.get('xesam:title', '')), 'artist': clean(artist), 'album': clean(meta.get('xesam:album', '')),
            'length': (meta.get('mpris:length') or 0) / 1e6, 'pos': p['pos'], 'ts': p['ts'], 'rate': p['rate'],
            'art': self._art(meta.get('mpris:artUrl') or ''),
        })
        return False

    # cover art: a local file is used as it is, a web address is downloaded once
    def _art(self, url):
        if url == self.art_url:
            return self.art_path
        self.art_url, self.art_path = url, ''
        if url.startswith('file://'):
            self.art_path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        elif url.startswith(('https://', 'http://')):
            threading.Thread(target=self._fetch_art, args=(url,), daemon=True).start()
        return self.art_path

    def _fetch_art(self, url):
        out = paths.IMAGES / ('art-' + hashlib.sha1(url.encode()).hexdigest()[:16])
        try:
            if not out.exists():
                request = urllib.request.Request(url, headers={'User-Agent': 'deskwidgets/3.0'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    data = response.read(ART_LIMIT)
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix('.tmp')
                tmp.write_bytes(data)
                tmp.replace(out)
        except (OSError, ValueError) as e:
            log(f'cover art {url[:80]}: {e}')
            return
        self.GLib.idle_add(self._art_ready, url, str(out))

    def _art_ready(self, url, path):
        if url == self.art_url:
            self.art_path = path
            _prune_art(Path(path))
            self._schedule()
        return False


def _prune_art(keep):
    """Cover art older than the current one is not needed any more."""
    files = sorted((f for f in paths.IMAGES.glob('art-*') if f != keep), key=lambda f: f.stat().st_mtime)
    for old in files[:-4]:
        try:
            old.unlink()
        except OSError:
            pass
