"""Background data sources for widgets. Each provider keeps one snapshot (plain dicts and lists)
that `L.service(name)` hands to Lua; widgets only ever see the latest complete snapshot."""
import subprocess
import sys
import threading

from ..core.util import log

NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


class Provider:
    name = ''

    def __init__(self):
        self._snapshot = None

    def snapshot(self):
        return self._snapshot

    def publish(self, data):
        self._snapshot = data   # replaced as a whole: readers never see a half-built one

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def control(self, command):
        """Remote control (media players); True if it was understood."""
        return False


class PollingProvider(Provider):
    """Calls `poll()` every `every` seconds on a daemon thread. A failing poll is logged once and retried."""
    every = 5.0

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._thread = None
        self._failed = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name=f'provider-{self.name}', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.poll()
                self._failed = False
            except Exception as e:   # a broken data source must not take the widgets down
                if not self._failed:
                    log(f'{self.name}: {e}')
                self._failed = True
            self._stop.wait(self.every)

    def poll(self):
        raise NotImplementedError


def run_command(command, timeout=5):
    """(returncode, stdout) of a command without a console window; (None, '') if it cannot run."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=timeout,
                              creationflags=NO_WINDOW)
        return done.returncode, done.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ''
