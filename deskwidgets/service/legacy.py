"""DeskWidgets 1.x and 2.x drew every widget with its own `conky` process. When 3.x starts on
a machine that still has those running, stop them so the widgets do not appear twice."""
import os
import signal
import sys

MARKER = 'DESKWIDGETS_SERVICE'   # set in the environment of every 3.x service

def stop_old_versions():
    if sys.platform != 'linux' or os.environ.get('DESKWIDGETS_KEEP_OLD'):   # the variable is for testing
        return
    me = str(os.getpid())
    for pid in filter(str.isdigit, os.listdir('/proc')):
        if pid == me:
            continue
        try:
            if os.stat(f'/proc/{pid}').st_uid != os.getuid():
                continue
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = f.read().split(b'\0')
        except OSError:
            continue
        if _is_old_widget(argv, pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def _is_old_widget(argv, pid):
    program = os.path.basename(argv[0])
    if program == b'conky':
        return any(a.endswith((b'/widget.conf', b'mac/widgets.conf')) for a in argv)
    # the 2.x service: `python3 .../bin/deskwidgets run`
    return program.startswith(b'python') and len(argv) >= 3 and argv[1].endswith(b'/bin/deskwidgets') \
        and argv[2] == b'run' and not _is_new(pid)


def _is_new(pid):
    try:
        with open(f'/proc/{pid}/environ', 'rb') as f:
            return MARKER.encode() + b'=' in f.read()
    except OSError:
        return False
