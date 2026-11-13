"""Shared test setup: every test works in its own temporary config/cache/run folders."""
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from deskwidgets.core import paths


@contextmanager
def isolated_paths():
    """Points DeskWidgets' folders (config, cache, run) at a fresh temporary directory."""
    with tempfile.TemporaryDirectory(prefix='deskwidgets-test-') as tmp:
        root = Path(tmp)
        config, cache, run = root / 'config', root / 'cache', root / 'run'
        with patch.multiple(paths, CONFIG=config, USER_WIDGETS=config / 'widgets', LAYOUT=config / 'layout.json',
                            SETTINGS=config / 'settings.json', CACHE=cache, PREVIEWS=cache / 'previews',
                            IMAGES=cache / 'images', RUN=run, SOCKET=run / 'control', AUTH_KEY=run / 'control.key'):
            paths.ensure_dirs()
            yield root


class IsolatedTestCase(unittest.TestCase):
    """A test case whose config, cache and run folders live in a temporary directory for its whole run."""

    def setUp(self):
        stack = ExitStack()
        self.root = stack.enter_context(isolated_paths())
        self.addCleanup(stack.close)
