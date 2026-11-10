"""Talking to the running service: the CLI and the gallery send it one JSON request at a time.

The transport is `multiprocessing.connection` (a Unix socket on Linux, a named pipe on Windows),
protected by a random key that only the user can read, so other accounts on the machine cannot
drive your desktop. Messages are JSON, never pickles.
"""
import getpass
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from multiprocessing.connection import AuthenticationError, Client, Listener
from pathlib import Path

from ..core import paths
from ..core.util import log

TIMEOUT = 10.0
WINDOWS = sys.platform == 'win32'


class ServiceNotRunning(Exception):
    pass


class AlreadyRunning(Exception):
    pass


UNIX_PATH_MAX = 100   # sun_path is 108 bytes on Linux (104 on BSD), including the terminating NUL


def _family_and_address():
    if WINDOWS:
        return 'AF_PIPE', r'\\.\pipe\deskwidgets-' + getpass.getuser().replace('\\', '_')
    address = str(paths.SOCKET)
    if len(os.fsencode(address)) > UNIX_PATH_MAX:   # a very long $XDG_RUNTIME_DIR: use a short, stable path
        digest = hashlib.sha1(address.encode()).hexdigest()[:10]
        address = f'/tmp/deskwidgets-{os.getuid()}-{digest}.sock'
    return 'AF_UNIX', address


def _key(create=False):
    try:
        return paths.AUTH_KEY.read_bytes()
    except OSError:
        if not create:
            raise ServiceNotRunning('no control key') from None
    paths.RUN.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(paths.AUTH_KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(key)
    return key


# client
def request(command, timeout=TIMEOUT, **args):
    """Sends `command` to the service and returns its answer (any JSON value).
    Raises ServiceNotRunning when nobody listens, RuntimeError when the service refuses."""
    family, address = _family_and_address()
    key = _key()
    for attempt in range(3):
        try:
            conn = Client(address, family, authkey=key)
            break
        except (BlockingIOError, ConnectionRefusedError) as e:   # the service is busy accepting someone else
            if attempt == 2:
                raise ServiceNotRunning(str(e)) from None
            time.sleep(0.1)
        except (OSError, AuthenticationError, EOFError) as e:
            raise ServiceNotRunning(str(e)) from None
    with conn:
        conn.send_bytes(json.dumps({'cmd': command, 'args': args}).encode())
        if not conn.poll(timeout):
            raise TimeoutError(f'the service did not answer "{command}"')
        reply = json.loads(conn.recv_bytes())
    if not reply.get('ok'):
        raise RuntimeError(reply.get('error', 'request failed'))
    return reply.get('result')


def is_running():
    try:
        request('ping', timeout=2)
        return True
    except (ServiceNotRunning, TimeoutError, RuntimeError):
        return False


def start_service():
    """Starts `deskwidgets run` in the background, writing its messages to the log file."""
    python = sys.executable
    if WINDOWS:   # no console window
        windowless = Path(python).with_name('pythonw.exe')
        python = str(windowless) if windowless.exists() else python
    paths.CACHE.mkdir(parents=True, exist_ok=True)
    log_file = open(paths.CACHE / 'service.log', 'ab')
    flags = 0x08000000 | 0x00000008 if WINDOWS else 0   # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen([python, '-m', 'deskwidgets', 'run'], stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file,
                     start_new_session=not WINDOWS, creationflags=flags, close_fds=True)


def ensure_running(wait=8.0):
    """True when the service answers, starting it first if needed."""
    if is_running():
        return True
    start_service()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.15)
        if is_running():
            return True
    return False


# server
class ControlServer:
    """Accepts requests on a background thread and runs them on the service's loop thread."""

    def __init__(self, handler, backend):
        self.handler = handler        # handler(command, args) -> JSON-able result; raises to refuse
        self.backend = backend
        self.listener = None
        self.thread = None

    def start(self):
        family, address = _family_and_address()
        if not WINDOWS and Path(address).exists():
            try:
                Client(address, family, authkey=_key()).close()
                raise AlreadyRunning(address)
            except (OSError, AuthenticationError, EOFError, ServiceNotRunning):
                Path(address).unlink(missing_ok=True)   # left behind by a crashed service
        paths.RUN.mkdir(parents=True, exist_ok=True)
        self.listener = Listener(address, family, backlog=16, authkey=_key(create=True))
        self.thread = threading.Thread(target=self._serve, name='control', daemon=True)
        self.thread.start()

    def stop(self):
        listener, self.listener = self.listener, None
        if listener is not None:
            if WINDOWS:
                self._release_pipe()
            try:
                listener.close()
            except OSError:
                pass
        if not WINDOWS:
            Path(_family_and_address()[1]).unlink(missing_ok=True)

    def _release_pipe(self):
        """Windows: PipeListener.close() leaves open the pipe instance the accept thread is waiting on,
        and while it lives no new listener can take the same pipe name ("Access is denied"; that is
        what a service restart inside one process, i.e. the tests, runs into). A throw-away
        connection lets the accept thread finish, so its handle is closed."""
        from multiprocessing.connection import PipeClient
        try:
            PipeClient(_family_and_address()[1]).close()
        except OSError:
            pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(2)

    def _serve(self):
        while self.listener is not None:
            try:
                conn = self.listener.accept()
            except (OSError, EOFError):
                if self.listener is None:
                    return
                continue
            except AuthenticationError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            try:
                if not conn.poll(TIMEOUT):
                    return
                message = json.loads(conn.recv_bytes())
                command, args = message['cmd'], message.get('args') or {}
            except (OSError, EOFError, ValueError, KeyError, TypeError):
                return
            done, box = threading.Event(), {}

            def run():
                try:
                    box['reply'] = {'ok': True, 'result': self.handler(command, args)}
                except Exception as e:   # reported to the caller, the service carries on
                    box['reply'] = {'ok': False, 'error': str(e) or type(e).__name__}
                    if not isinstance(e, (KeyError, ValueError, LookupError)):
                        log(f'"{command}" failed: {e!r}')
                done.set()
            self.backend.call_soon(run)
            if not done.wait(TIMEOUT):
                box['reply'] = {'ok': False, 'error': 'the service is busy'}
            try:
                conn.send_bytes(json.dumps(box['reply']).encode())
            except (OSError, ValueError):
                pass
