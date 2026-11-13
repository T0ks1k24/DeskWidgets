"""The gallery shortcut (GNOME) and how the gallery command leaves traces; no real gsettings is ever touched."""
import io
import sys
import unittest
from unittest.mock import patch

from deskwidgets import cli
from deskwidgets.gallery import common
from deskwidgets.service import ipc
from deskwidgets.core import paths
from deskwidgets.platform import integration

from helpers import IsolatedTestCase

LIST_KEY = 'custom-keybindings'


class FakeGsettings:
    """Just enough of `gsettings get/set` for the custom shortcut keys."""

    def __init__(self, shortcuts=()):
        self.values = {(integration.MEDIA_KEYS, LIST_KEY): str(list(shortcuts))}
        self.calls = []

    def __call__(self, action, schema, key, value=None):
        self.calls.append((action, schema, key, value))
        if action == 'get':
            stored = self.values.get((schema, key))
            return None if stored is None else (stored if schema == integration.MEDIA_KEYS else f"'{stored}'")
        self.values[(schema, key)] = value
        return ''

    def sets(self):
        return [c for c in self.calls if c[0] == 'set']


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        for target, value in (('is_gnome', True),):
            patcher = patch.object(integration, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, shortcuts=()):
        gs = FakeGsettings(shortcuts)
        patcher = patch.object(integration, '_gsettings', gs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return gs

    def test_setting_the_hotkey_registers_the_shortcut_and_removing_it_unregisters(self):
        gs = self.fake(['/other/'])
        self.assertTrue(integration.set_hotkey(True))
        schema = integration._keybinding_schema()
        self.assertEqual(gs.values[(schema, 'binding')], integration.HOTKEY)
        self.assertIn('deskwidgets', gs.values[(schema, 'command')])
        self.assertIn(integration.KEYBINDING_PATH, gs.values[(integration.MEDIA_KEYS, LIST_KEY)])
        self.assertIn('/other/', gs.values[(integration.MEDIA_KEYS, LIST_KEY)])
        self.assertTrue(integration.set_hotkey(False))
        self.assertNotIn(integration.KEYBINDING_PATH, gs.values[(integration.MEDIA_KEYS, LIST_KEY)])
        self.assertIn('/other/', gs.values[(integration.MEDIA_KEYS, LIST_KEY)])

    def test_a_stale_command_is_pointed_at_this_install_and_the_key_is_left_alone(self):
        gs = self.fake([integration.KEYBINDING_PATH])
        schema = integration._keybinding_schema()
        gs.values[(schema, 'binding')] = '<Super>g'   # rebound by the user in GNOME Settings
        gs.values[(schema, 'command')] = '/gone/venv/bin/python -m deskwidgets gallery'
        self.assertTrue(integration.refresh_hotkey())
        self.assertEqual(gs.values[(schema, 'command')], integration._shell_command('gallery'))
        self.assertEqual(gs.values[(schema, 'binding')], '<Super>g')
        self.assertEqual([c[2] for c in gs.sets()], ['command'])

    def test_a_correct_shortcut_is_not_rewritten(self):
        gs = self.fake([integration.KEYBINDING_PATH])
        gs.values[(integration._keybinding_schema(), 'binding')] = integration.HOTKEY
        gs.values[(integration._keybinding_schema(), 'command')] = integration._shell_command('gallery')
        self.assertFalse(integration.refresh_hotkey())
        self.assertFalse(gs.sets())

    def test_a_shortcut_the_user_deleted_is_not_brought_back(self):
        gs = self.fake(['/other/'])
        self.assertFalse(integration.refresh_hotkey())
        self.assertFalse(gs.sets())

    def test_nothing_happens_outside_gnome(self):
        gs = self.fake([integration.KEYBINDING_PATH])
        with patch.object(integration, 'is_gnome', return_value=False):
            self.assertIsNone(integration.hotkey_state())
            self.assertFalse(integration.refresh_hotkey())
            self.assertFalse(integration.set_hotkey(True))
        self.assertFalse(gs.sets())

    def test_state_reports_what_gnome_has(self):
        gs = self.fake([integration.KEYBINDING_PATH])
        gs.values[(integration._keybinding_schema(), 'binding')] = integration.HOTKEY
        gs.values[(integration._keybinding_schema(), 'command')] = 'x'
        state = integration.hotkey_state()
        self.assertEqual((state['registered'], state['binding'], state['command']), (True, integration.HOTKEY, 'x'))
        self.assertEqual(state['wanted'], integration._shell_command('gallery'))


class StartTests(IsolatedTestCase):
    def test_the_service_repairs_the_shortcut_before_it_starts(self):
        calls = []
        with patch.object(integration, 'refresh_hotkey', side_effect=lambda: calls.append(1) or True), \
                patch('deskwidgets.service.daemon.run', return_value=0), patch.object(sys, 'platform', 'linux'):
            self.assertEqual(cli.cmd_run(None), 0)
        self.assertEqual(calls, [1])

    def test_a_broken_gsettings_never_stops_the_service(self):
        with patch.object(integration, 'refresh_hotkey', side_effect=RuntimeError('boom')), \
                patch('deskwidgets.service.daemon.run', return_value=0) as run, patch.object(sys, 'platform', 'linux'):
            self.assertEqual(cli.cmd_run(None), 0)
        run.assert_called_once()

    def test_the_hotkey_setting_off_leaves_the_shortcut_alone(self):
        paths.SETTINGS.write_text('{"hotkey": false}')
        with patch.object(integration, 'refresh_hotkey') as refresh, patch('deskwidgets.service.daemon.run', return_value=0), \
                patch.object(sys, 'platform', 'linux'):
            cli.cmd_run(None)
        refresh.assert_not_called()


class GalleryTraceTests(IsolatedTestCase):
    def run_gallery(self, argv):
        stream = io.StringIO()
        stream.isatty = lambda: False
        with patch.object(sys, 'stderr', stream), patch.object(sys, 'argv', ['deskwidgets'] + argv), \
                patch('deskwidgets.gallery.main', return_value=0) as main:
            args = cli.build_parser().parse_args(argv)
            code = cli.cmd_gallery(args)
            opened = sys.stderr
            if opened is not stream:
                opened.close()
        return code, main

    def test_a_start_without_a_terminal_leaves_a_line_in_the_log(self):
        code, main = self.run_gallery(['gallery'])
        self.assertEqual(code, 0)
        main.assert_called_once_with([])
        self.assertIn('gallery started', (paths.CACHE / 'gallery.log').read_text())

    def test_configure_passes_the_widget_on(self):
        _code, main = self.run_gallery(['configure', 'clock'])
        main.assert_called_once_with(['--configure', 'clock'])


class ServiceRestartTests(unittest.TestCase):
    def test_a_service_that_does_not_know_monitors_is_outdated(self):
        with patch.object(ipc, 'request', side_effect=RuntimeError('unknown command: monitors')):
            self.assertTrue(common.service_outdated())

    def test_other_failures_do_not_make_it_outdated(self):
        for error in (RuntimeError('boom'), TimeoutError(), ipc.ServiceNotRunning()):
            with patch.object(ipc, 'request', side_effect=error):
                self.assertFalse(common.service_outdated(), error)
        with patch.object(ipc, 'request', return_value=[]):
            self.assertFalse(common.service_outdated())

    def test_restart_stops_the_old_service_before_starting_the_new_one(self):
        running = [True, True, False]   # still there after `quit`, then gone
        events = []
        with patch.object(ipc, 'is_running', side_effect=lambda: running.pop(0) if running else False), \
                patch.object(ipc, 'request', side_effect=lambda command, **a: events.append(command)), \
                patch.object(ipc, 'ensure_running', side_effect=lambda: events.append('start') or True), \
                patch.object(common.time, 'sleep'):
            self.assertTrue(common.restart_service())
        self.assertEqual(events, ['quit', 'start'])

    def test_restart_starts_a_service_that_was_not_running(self):
        with patch.object(ipc, 'is_running', return_value=False), patch.object(ipc, 'request') as request, \
                patch.object(ipc, 'ensure_running', return_value=True):
            self.assertTrue(common.restart_service())
        request.assert_not_called()

    def test_restart_gives_up_waiting_for_a_service_that_will_not_stop(self):
        with patch.object(ipc, 'is_running', return_value=True), patch.object(ipc, 'request'), \
                patch.object(ipc, 'ensure_running', return_value=False) as start, patch.object(common.time, 'sleep'), \
                patch.object(common.time, 'monotonic', side_effect=[0, 1, 100, 200]):
            self.assertFalse(common.restart_service(stop_wait=8))
        start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
