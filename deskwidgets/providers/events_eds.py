"""Up next on Linux: the selected GNOME calendars (Evolution Data Server, so Google calendars too)."""
import time

from ..core.util import clean, log
from . import glib_loop
from .base import Provider


class Events(Provider):
    """Watches the calendars and keeps the next few events: {updated, events: [{start, end, allday, color, calendar, title}]}."""
    name = 'events'
    DAYS = 8
    MAX = 12

    def start(self):
        glib_loop.call_sync(self._setup)

    def stop(self):
        try:
            glib_loop.call_sync(self._teardown)
        except (TimeoutError, OSError):
            pass

    # runs on the GLib thread
    def _setup(self):
        import gi
        gi.require_version('EDataServer', '1.2')
        gi.require_version('ECal', '2.0')
        gi.require_version('ICalGLib', '3.0')
        from gi.repository import EDataServer, ECal, GLib, ICalGLib
        self.EDS, self.ECal, self.ICal, self.GLib = EDataServer, ECal, ICalGLib, GLib
        self.clients = {}   # source uid -> {'source', 'client', 'view'}, client is None while connecting
        self.refresh_id = 0
        self.registry = EDataServer.SourceRegistry.new_sync(None)
        self.reg_handlers = [self.registry.connect(sig, lambda *a: self._sync_sources())
                             for sig in ('source-added', 'source-removed', 'source-changed',
                                         'source-enabled', 'source-disabled')]
        self.tick_id = GLib.timeout_add_seconds(300, lambda: (self.schedule(), True)[1])
        self._sync_sources()
        self.schedule()

    def _teardown(self):
        for h in self.reg_handlers:
            self.registry.disconnect(h)
        self.GLib.source_remove(self.tick_id)
        if self.refresh_id:
            self.GLib.source_remove(self.refresh_id)
        for uid in list(self.clients):
            self._drop(uid)

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
            except self.GLib.Error:
                pass

    def _connected(self, _src, result, uid):
        try:
            client = self.ECal.Client.connect_finish(result)
        except self.GLib.Error as e:
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
        except self.GLib.Error as e:
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
            self.refresh_id = self.GLib.timeout_add(1200, self._refresh)

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

            def add(comp, istart, iend, *_, source=source, color=color):
                allday = istart.is_date()
                s = self._epoch(istart)
                e = self._epoch(iend) if iend is not None and not iend.is_null_time() else s
                if e <= s:
                    e = s + (86400 if allday else 1800)
                found.append((s, e, allday, color, clean(source.get_display_name()), clean(comp.get_summary() or '')))
                return True

            try:
                client.generate_instances_sync(start, end, None, add, None)
            except self.GLib.Error as e:
                log(f'calendar {source.get_display_name()}: {e.message}')
        upcoming = sorted((ev for ev in found if ev[1] > now), key=lambda ev: (ev[0], not ev[2], ev[5]))[:self.MAX]
        self.publish({'updated': int(now), 'events': [
            {'start': int(s), 'end': int(e), 'allday': allday, 'color': color, 'calendar': cal, 'title': title}
            for s, e, allday, color, cal, title in upcoming]})
        return False
