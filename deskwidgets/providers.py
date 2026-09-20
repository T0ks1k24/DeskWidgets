"""Live data the widgets cannot get on their own:
* Media: follows MPRIS players on the session bus, writes RUN/media + cover art (widget "media")
* Events: watches GNOME calendars via Evolution Data Server, writes RUN/events (widgets "events", "calendar")
"""
import hashlib
import os
import threading
import time
import urllib.request

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

from .paths import RUN
from .util import clean, log, write_atomic


def player_name(identity):
    """Turns 'Mozilla firefox_firefox' (snap) into 'Firefox'."""
    name = identity.replace('Mozilla ', '').split('_')[0].strip()
    return name[:1].upper() + name[1:]


# now playing: MPRIS players
MPRIS = 'org.mpris.MediaPlayer2'
MPRIS_PATH = '/org/mpris/MediaPlayer2'
ART_SIZE = 276   # 2x the 138 px cover in the widget


class Media:
    """Follows the MPRIS players on the session bus, writes RUN/media and the cover art."""

    def __init__(self):
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        self.GdkPixbuf = GdkPixbuf
        self.path = os.path.join(RUN, 'media')
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.players = {}   # well-known name -> state
        self.owners = {}    # unique bus name -> well-known name
        self.art_url, self.art_path = None, ''
        self.write_id = 0
        flags = Gio.DBusSignalFlags.NONE
        self.subs = [
            self.bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                                      '/org/freedesktop/DBus', None, flags, self._owner_changed),
            self.bus.signal_subscribe(None, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                                      MPRIS_PATH, None, flags, self._props_changed),
            self.bus.signal_subscribe(None, MPRIS + '.Player', 'Seeked', MPRIS_PATH, None, flags, self._seeked),
        ]
        self._dbus('ListNames', None, '(as)', self._got_names)
        self.write()

    def stop(self):
        for sid in self.subs:
            self.bus.signal_unsubscribe(sid)
        if self.write_id:
            GLib.source_remove(self.write_id)
        for f in os.listdir(RUN):
            if f == 'media' or f.startswith('art-'):
                os.unlink(os.path.join(RUN, f))

    # D-Bus plumbing
    def _dbus(self, method, args, reply, callback):
        self.bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', method, args,
                      GLib.VariantType(reply), Gio.DBusCallFlags.NONE, -1, None, callback)

    def _props_call(self, name, method, args, reply, callback):
        self.bus.call(name, MPRIS_PATH, 'org.freedesktop.DBus.Properties', method, args,
                      GLib.VariantType(reply), Gio.DBusCallFlags.NONE, 3000, None, callback)

    def _finish(self, result):
        try:
            return self.bus.call_finish(result).unpack()
        except GLib.Error:
            return None   # player went away, or AppArmor denied the call

    # players
    def _got_names(self, _bus, result):
        names = self._finish(result)
        for name in (names[0] if names else []):
            if name.startswith(MPRIS + '.'):
                self._add(name)

    def _add(self, name):
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
            self.write()

    def _got_identity(self, name, result):
        value = self._finish(result)
        if value and name in self.players:
            self.players[name]['identity'] = value[0]
            self.write()

    def _owner_changed(self, _conn, _sender, _path, _iface, _signal, params):
        name, old, new = params.unpack()
        if not name.startswith(MPRIS + '.'):
            return
        if old:
            self.owners.pop(old, None)
            self.players.pop(name, None)
        if new:
            self._add(name)
        self.write()

    def _props_changed(self, _conn, sender, _path, _iface, _signal, params):
        iface, changed, _invalidated = params.unpack()
        name = self.owners.get(sender)
        if iface != MPRIS + '.Player' or name not in self.players:
            return
        self._apply(self.players[name], changed)
        if 'PlaybackStatus' in changed or 'Metadata' in changed:
            self._props_call(name, 'Get', GLib.Variant('(ss)', (MPRIS + '.Player', 'Position')), '(v)',
                             lambda b, r: self._got_position(name, r))
        self.write()

    def _got_position(self, name, result):
        value = self._finish(result)
        if value and name in self.players:
            p = self.players[name]
            p['pos'], p['ts'] = value[0] / 1e6, time.time()
            self.write()

    def _seeked(self, _conn, sender, _path, _iface, _signal, params):
        name = self.owners.get(sender)
        if name in self.players:
            p = self.players[name]
            p['pos'], p['ts'] = params.unpack()[0] / 1e6, time.time()
            self.write()

    def _apply(self, p, props):
        now = time.time()
        if 'Metadata' in props:
            meta = props['Metadata'] or {}
            if (meta.get('mpris:trackid'), meta.get('xesam:title')) != \
                    (p['meta'].get('mpris:trackid'), p['meta'].get('xesam:title')):
                p['pos'], p['ts'] = 0.0, now
            p['meta'] = meta
            p['changed'] = now
        if 'PlaybackStatus' in props:
            if p['status'] == 'Playing':   # freeze the extrapolated position
                p['pos'] += (now - p['ts']) * p['rate']
                p['ts'] = now
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

    # cache
    def write(self):
        if not self.write_id:
            self.write_id = GLib.timeout_add(150, self._write_now)

    def _write_now(self):
        self.write_id = 0
        p = self._active()
        if not p:
            lines = ['status=none']
        else:
            meta = p['meta']
            artist = meta.get('xesam:artist') or ''
            if isinstance(artist, (list, tuple)):
                artist = ', '.join(artist)
            lines = [
                f"status={p['status']}",
                f"bus={p['name']}",
                f"player={clean(player_name(p['identity']))}",
                f"title={clean(meta.get('xesam:title', ''))}",
                f"artist={clean(artist)}",
                f"album={clean(meta.get('xesam:album', ''))}",
                f"length={(meta.get('mpris:length') or 0) / 1e6:.1f}",
                f"pos={p['pos']:.1f}",
                f"ts={p['ts']:.0f}",
                f"rate={p['rate']:g}",
                f"art={self._art(meta.get('mpris:artUrl') or '')}",
            ]
        write_atomic(self.path, '\n'.join(lines) + '\n')
        return False

    # cover art, saved as a square-ish PNG in RUN for the Lua widget
    def _art(self, url):
        if url == self.art_url:
            return self.art_path
        self.art_url, self.art_path = url, ''
        if url:
            out = os.path.join(RUN, 'art-' + hashlib.sha1(url.encode()).hexdigest()[:16] + '.png')
            threading.Thread(target=self._fetch_art, args=(url, out), daemon=True).start()
        return ''

    def _fetch_art(self, url, out):
        try:
            if url.startswith('file://'):
                with open(Gio.File.new_for_uri(url).get_path(), 'rb') as f:
                    data = f.read(16 << 20)
            elif url.startswith(('https://', 'http://')):
                req = urllib.request.Request(url, headers={'User-Agent': 'deskwidgets/1.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read(16 << 20)
            else:
                return
            loader = self.GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
            scale = ART_SIZE / min(pix.get_width(), pix.get_height())
            if scale < 1:
                pix = pix.scale_simple(max(1, round(pix.get_width() * scale)),
                                       max(1, round(pix.get_height() * scale)),
                                       self.GdkPixbuf.InterpType.HYPER)
            pix.savev(out + '.tmp', 'png', [], [])
            os.replace(out + '.tmp', out)
        except (OSError, GLib.Error, ValueError) as e:
            log(f'cover art {url[:80]}: {e}')
            return
        GLib.idle_add(self._art_ready, url, out)

    def _art_ready(self, url, out):
        if url == self.art_url:
            self.art_path = out
            for f in os.listdir(RUN):
                if f.startswith('art-') and os.path.join(RUN, f) != out:
                    os.unlink(os.path.join(RUN, f))
            self.write()
        return False


# events: Evolution Data Server calendars
class Events:
    """Watches the selected GNOME calendars and writes the upcoming events to RUN/events."""
    DAYS = 8
    MAX = 12

    def __init__(self):
        gi.require_version('EDataServer', '1.2')
        gi.require_version('ECal', '2.0')
        gi.require_version('ICalGLib', '3.0')
        from gi.repository import EDataServer, ECal, ICalGLib
        self.EDS, self.ECal, self.ICal = EDataServer, ECal, ICalGLib
        self.path = os.path.join(RUN, 'events')
        self.clients = {}   # source uid -> {'source', 'client', 'view'}, client is None while connecting
        self.refresh_id = 0
        self.registry = EDataServer.SourceRegistry.new_sync(None)
        self.reg_handlers = [self.registry.connect(sig, lambda *a: self._sync_sources())
                             for sig in ('source-added', 'source-removed', 'source-changed',
                                         'source-enabled', 'source-disabled')]
        self.tick_id = GLib.timeout_add_seconds(300, lambda: (self.schedule(), True)[1])
        self._sync_sources()
        self.schedule()

    def stop(self):
        for h in self.reg_handlers:
            self.registry.disconnect(h)
        GLib.source_remove(self.tick_id)
        if self.refresh_id:
            GLib.source_remove(self.refresh_id)
        for uid in list(self.clients):
            self._drop(uid)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _wanted(self):
        ext_name = self.EDS.SOURCE_EXTENSION_CALENDAR
        return {s.get_uid(): s for s in self.registry.list_sources(ext_name)
                if s.get_enabled() and s.get_extension(ext_name).get_selected()}

    def _sync_sources(self):
        wanted = self._wanted()
        for uid in list(self.clients):
            if uid not in wanted:
                self._drop(uid)
        for uid, source in wanted.items():
            if uid not in self.clients:
                self.clients[uid] = {'source': source, 'client': None, 'view': None}
                self.ECal.Client.connect(source, self.ECal.ClientSourceType.EVENTS, 30, None,
                                         self._connected, uid)
        self.schedule()

    def _drop(self, uid):
        entry = self.clients.pop(uid, None)
        if entry and entry['view']:
            try:
                entry['view'].stop()
            except GLib.Error:
                pass

    def _connected(self, _src, result, uid):
        try:
            client = self.ECal.Client.connect_finish(result)
        except GLib.Error as e:
            log(f'calendar {uid}: {e.message}')
            return
        entry = self.clients.get(uid)
        if not entry:
            return
        entry['client'] = client
        client.get_view('#t', None, self._got_view, uid)
        self.schedule()

    def _got_view(self, client, result, uid):
        try:
            res = client.get_view_finish(result)
        except GLib.Error as e:
            log(f'calendar view {uid}: {e.message}')
            return
        view = res[1] if isinstance(res, tuple) else res
        entry = self.clients.get(uid)
        if not entry or view is None:
            return
        entry['view'] = view
        for sig in ('objects-added', 'objects-modified', 'objects-removed'):
            view.connect(sig, lambda *a: self.schedule())
        view.start()

    def schedule(self):
        if not self.refresh_id:
            self.refresh_id = GLib.timeout_add(1200, self._refresh)

    def _epoch(self, t):
        if t.is_date():
            return time.mktime((t.get_year(), t.get_month(), t.get_day(), 0, 0, 0, 0, 0, -1))
        zone = t.get_timezone()
        if zone is None and t.is_utc():
            zone = self.ICal.Timezone.get_utc_timezone()
        if zone is not None:
            return t.as_timet_with_zone(zone)
        return time.mktime((t.get_year(), t.get_month(), t.get_day(),
                            t.get_hour(), t.get_minute(), t.get_second(), 0, 0, -1))

    def _refresh(self):
        self.refresh_id = 0
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        start, end = int(midnight), int(midnight + self.DAYS * 86400)
        found = []
        cal_ext = self.EDS.SOURCE_EXTENSION_CALENDAR
        for entry in self.clients.values():
            client = entry['client']
            if client is None:
                continue
            source = entry['source']
            color = source.get_extension(cal_ext).get_color() or ''

            def add(comp, istart, iend, *_):
                allday = istart.is_date()
                s = self._epoch(istart)
                e = self._epoch(iend) if iend is not None and not iend.is_null_time() else s
                if e <= s:
                    e = s + (86400 if allday else 1800)
                found.append((s, e, allday, color, clean(source.get_display_name()),
                              clean(comp.get_summary() or '')))
                return True

            try:
                client.generate_instances_sync(start, end, None, add, None)
            except GLib.Error as e:
                log(f'calendar {source.get_display_name()}: {e.message}')
        upcoming = sorted((ev for ev in found if ev[1] > now), key=lambda ev: (ev[0], not ev[2], ev[5]))
        upcoming = upcoming[:self.MAX]
        lines = [f'updated={int(now)}', f'count={len(upcoming)}']
        for i, (s, e, allday, color, cal, title) in enumerate(upcoming, 1):
            lines.append(f'e{i}={int(s)}|{int(e)}|{int(allday)}|{color}|{cal}|{title}')
        write_atomic(self.path, '\n'.join(lines) + '\n')
        return False




# wireless mice that report their charge over a vendor HID protocol
class HidBatteries:
    """Polls receivers found by hidbattery.py once a minute (in a thread: each query may wait up
    to a second) and writes RUN/hid-batteries for the battery widget. While a mouse sleeps its
    last known charge is shown for half an hour."""
    EVERY = 60

    def __init__(self):
        self.path = os.path.join(RUN, 'hid-batteries')
        self.busy = False
        self.last = {}   # node -> (reading, time)
        self.tick_id = GLib.timeout_add_seconds(self.EVERY, self._tick)
        self._tick()

    def stop(self):
        GLib.source_remove(self.tick_id)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _tick(self):
        if not self.busy:
            self.busy = True
            threading.Thread(target=self._poll, daemon=True).start()
        return True

    def _poll(self):
        from . import hidbattery
        lines, denied = [], []
        try:
            devices = hidbattery.find()
        except OSError:
            devices = []
        for dev in devices:
            if not dev['access']:
                denied.append(dev['name'])
                continue
            reading = hidbattery.read(dev)
            if reading and reading['online']:
                self.last[dev['node']] = (reading, time.time())
            elif dev['node'] in self.last and time.time() - self.last[dev['node']][1] < 1800:
                reading = dict(self.last[dev['node']][0], online=False)
            if reading is None:
                continue
            pct = '' if reading['pct'] is None else reading['pct']
            lines.append(f"d{len(lines) + 1}={clean(dev['name'])}|{pct}|{int(reading['charging'])}|{int(reading['online'])}|mouse")
        text = '\n'.join([f'count={len(lines)}', f"denied={clean(', '.join(denied))}"] + lines) + '\n'
        GLib.idle_add(self._write, text)

    def _write(self, text):
        write_atomic(self.path, text)
        self.busy = False
        return False
