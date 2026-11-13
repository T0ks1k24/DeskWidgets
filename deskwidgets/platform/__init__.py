"""Operating-system specific code behind the small interfaces in `base.py`."""
import os
import sys


def create_backend():
    """The backend for this system: Win32 on Windows, X11 (also Xwayland) on Linux."""
    if sys.platform == 'win32':
        from .win32 import Win32Backend
        return Win32Backend()
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('there is no X display: DeskWidgets draws through X11 '
                           '(Xwayland on Wayland sessions). Is DISPLAY set?')
    from .x11 import X11Backend
    return X11Backend()
