"""The gallery: add, configure and arrange widgets in a window.

Linux uses GTK 4 + libadwaita (`gtk_app.py`); Windows, and Linux systems without GTK 4, use Tk
(`tk_app.py`, part of every Python install). Both talk to the service through `common.py`.
"""
import sys


def main(argv=None):
    """Opens the gallery (or one widget's settings with ['--configure', id]). Returns the exit code."""
    errors = []
    if sys.platform != 'win32':
        try:
            from .gtk_app import main as run
            return run(argv)
        except (ImportError, ValueError) as e:   # no PyGObject / GTK 4 / libadwaita
            errors.append(f'GTK 4 + libadwaita: {e}')
    try:
        from .tk_app import main as run
    except ImportError as e:
        errors.append(f'Tk: {e}')
        raise ImportError('; '.join(errors)) from None
    return run(argv)
