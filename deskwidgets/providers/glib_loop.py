"""One GLib main loop on a background thread, for providers that talk D-Bus (Linux only).
PyGObject is imported here and nowhere else in the engine, so everything else runs without it."""
import threading

_thread = None
_glib = None
_lock = threading.Lock()


def available():
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import GLib   # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


def start():
    """Starts the loop once; returns the GLib module."""
    global _thread, _glib
    with _lock:
        if _thread is None:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import GLib
            _glib = GLib
            loop = GLib.MainLoop()
            _thread = threading.Thread(target=loop.run, name='glib', daemon=True)
            _thread.start()
        return _glib


def call(function, *args):
    """Runs `function` on the GLib thread (without waiting)."""
    glib = start()
    glib.idle_add(lambda: (function(*args), False)[1])


def call_sync(function, timeout=15):
    """Runs `function` on the GLib thread and returns its result (or raises its exception)."""
    done, box = threading.Event(), {}

    def run():
        try:
            box['value'] = function()
        except Exception as e:   # handed back to the caller
            box['error'] = e
        done.set()
    call(run)
    if not done.wait(timeout):
        raise TimeoutError('the GLib thread did not answer')
    if 'error' in box:
        raise box['error']
    return box.get('value')
