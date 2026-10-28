"""Background services the widgets can ask for (`L.service('media')`), started only while a widget needs them.

    hub = ServiceHub()
    hub.sync({'media', 'docker'})        # start what is wanted, stop the rest
    hub.snapshot('media')                # -> dict for Lua, or None
    hub.media_control('PlayPause')

A service that cannot run here (no D-Bus, no Evolution Data Server, a Windows-only API on Linux)
is skipped with one message in the log; its widget shows an empty state.
"""
import sys

from ..core.util import log

WINDOWS = sys.platform == 'win32'


def _create(name):
    """The provider object for `name`, or None when this system has none."""
    if name == 'docker':
        from .docker import Docker
        return Docker()
    if name == 'batteries':
        from .batteries import Batteries
        return Batteries()
    if name == 'media':
        if WINDOWS:
            from .media_windows import Media
        else:
            from . import glib_loop
            if not glib_loop.available():
                raise RuntimeError('needs PyGObject (python3-gi)')
            from .media_mpris import Media
        return Media()
    if name == 'events':
        if WINDOWS:
            return None
        from . import glib_loop
        if not glib_loop.available():
            raise RuntimeError('needs PyGObject (python3-gi)')
        from .events_eds import Events
        return Events()
    return None


class ServiceHub:
    def __init__(self):
        self._running = {}
        self._unavailable = set()

    def sync(self, wanted):
        """Starts the providers in `wanted`, stops the ones nobody uses any more."""
        for name in list(self._running):
            if name not in wanted:
                self._running.pop(name).stop()
        for name in wanted:
            if name in self._running or name in self._unavailable:
                continue
            try:
                provider = _create(name)
                if provider is None:
                    self._unavailable.add(name)
                    continue
                provider.start()
                self._running[name] = provider
            except Exception as e:   # a broken provider must not take the widgets down
                log(f'service "{name}" is not available: {e}')
                self._unavailable.add(name)

    def snapshot(self, name):
        provider = self._running.get(name)
        return provider.snapshot() if provider else None

    def media_control(self, command):
        provider = self._running.get('media')
        return bool(provider and provider.control(command))

    def stop(self):
        for provider in self._running.values():
            provider.stop()
        self._running.clear()
