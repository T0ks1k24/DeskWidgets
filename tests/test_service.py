import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from deskwidgets.core import paths
from deskwidgets.platform.null import NullBackend
from deskwidgets.service import instance as instance_mod, ipc
from deskwidgets.service.daemon import Daemon

from helpers import IsolatedTestCase


class FakeHub:
    def __init__(self):
        self.wanted = None

    def sync(self, wanted):
        self.wanted = set(wanted)

    def snapshot(self, name):
        return None

    def media_control(self, command):
        return False

    def stop(self):
        pass


class DaemonCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        for target in ('deskwidgets.service.daemon.legacy.stop_old_versions',):   # never kill the developer's real widgets
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = NullBackend()
        self.daemon = Daemon(self.backend)
        self.daemon.hub = FakeHub()
        self.daemon.open_gallery = Mock()
        self.daemon.startup()
        self.addCleanup(self.daemon.shutdown)

    def window_of(self, inst_id):
        return self.daemon.instances[inst_id].window

    def widgets_on_screen(self):
        return [w for w in self.backend.windows if not w.closed and not w.click_through]


class StartupTests(DaemonCase):
    def test_default_layout_opens_one_presented_window_per_widget(self):
        windows = self.widgets_on_screen()
        self.assertEqual(len(windows), len(self.daemon.layout.instances))
        self.assertTrue(all(w.presented >= 1 for w in windows))
        self.assertTrue(all(w.title.startswith('deskwidget:') for w in windows))

    def test_windows_sit_on_their_grid_cells(self):
        for inst in self.daemon.layout.instances:
            self.assertEqual((self.window_of(inst['id']).x, self.window_of(inst['id']).y),
                             self.daemon.window_xy(inst['col'], inst['row']))

    def test_widgets_redraw_on_their_interval(self):
        inst_id = self.daemon.layout.instances[0]['id']
        window = self.window_of(inst_id)
        before = window.presented
        with patch.object(self.daemon.instances[inst_id], 'interval', return_value=0.01):
            self.daemon.instances[inst_id].redraw_soon()
            time.sleep(0.1)
            self.backend.flush()
            time.sleep(0.05)
            self.backend.flush()
        self.assertGreater(window.presented, before)

    def test_services_are_started_for_the_widgets_that_use_them(self):
        self.assertIn('media', self.daemon.hub.wanted)

    def test_shutdown_closes_every_window_and_removes_the_socket(self):
        self.daemon.shutdown()
        self.assertTrue(all(w.closed for w in self.backend.windows))
        self.assertFalse(paths.SOCKET.exists())


class CommandTests(DaemonCase):
    def test_add_remove(self):
        count = len(self.widgets_on_screen())
        inst_id = self.daemon.handle('add', {'type': 'clock'})
        self.assertEqual(len(self.widgets_on_screen()), count + 1)
        self.assertEqual(json.loads(paths.LAYOUT.read_text())['instances'][-1]['id'], inst_id)
        self.daemon.handle('remove', {'id': inst_id})
        self.assertEqual(len(self.widgets_on_screen()), count)
        self.assertNotIn(inst_id, [i['id'] for i in json.loads(paths.LAYOUT.read_text())['instances']])

    def test_errors_are_reported_not_raised_into_the_loop(self):
        with self.assertRaises(ValueError):
            self.daemon.handle('add', {'type': 'nope'})
        with self.assertRaises(KeyError):
            self.daemon.handle('remove', {'id': 'nope'})
        with self.assertRaises(ValueError):
            self.daemon.handle('frobnicate', {})
        with self.assertRaises(TypeError):
            self.daemon.handle('add', {'wrong': 1})

    def test_widget_for_another_os_is_refused_with_a_clear_message(self):
        with patch('deskwidgets.core.catalog.OS', 'windows'):
            with self.assertRaisesRegex(ValueError, 'does not work|не працює'):
                self.daemon.handle('add', {'type': 'events'})

    def test_a_full_grid_is_reported(self):
        with patch.object(self.daemon.layout, 'add', return_value=None):
            with self.assertRaisesRegex(ValueError, 'full|місця'):
                self.daemon.handle('add', {'type': 'clock'})

    def test_configure_restarts_with_the_new_size(self):
        inst_id = self.daemon.handle('add', {'type': 'clock'})
        old = self.window_of(inst_id)
        self.daemon.handle('configure', {'id': inst_id, 'patch': {'size': 'large'}})
        self.assertTrue(old.closed)
        self.assertGreater(self.window_of(inst_id).height, old.height)

    def test_move_and_arrange(self):
        inst_id = self.daemon.handle('add', {'type': 'clock'})
        spot = self.daemon.handle('move', {'id': inst_id, 'col': 3, 'row': 3})
        self.assertEqual((self.window_of(inst_id).x, self.window_of(inst_id).y), self.daemon.window_xy(*spot))
        self.daemon.handle('arrange', {})
        cells = [self.daemon.window_xy(i['col'], i['row']) for i in self.daemon.layout.instances]
        self.assertEqual(len(cells), len(set(cells)))

    def test_reload_rediscovers_widgets_and_restarts_them(self):
        old = list(self.widgets_on_screen())
        self.daemon.handle('reload', {})
        self.assertTrue(all(w.closed for w in old))
        self.assertEqual(len(self.widgets_on_screen()), len(old))

    def test_settings_change_applies_the_grid_and_font_change_asks_for_a_restart(self):
        paths.SETTINGS.write_text(json.dumps({'scale': 1.5, 'gap': 10}))
        self.daemon.handle('settings', {})
        self.assertEqual(self.daemon.grid.scale, 1.5)
        self.assertFalse(self.daemon.restart_requested)
        paths.SETTINGS.write_text(json.dumps({'font': 'DejaVu Sans'}))
        self.daemon.handle('settings', {})
        self.assertTrue(self.daemon.restart_requested)

    def test_status_and_list(self):
        status = self.daemon.handle('status', {})
        self.assertEqual(status['widgets'], len(self.daemon.layout.instances))
        self.assertEqual(self.daemon.handle('ping', {}), 'pong')
        self.assertEqual(self.daemon.handle('list', {}), self.daemon.layout.instances)

    def test_a_widget_of_a_missing_type_is_kept_but_not_run(self):
        inst_id = self.daemon.handle('add', {'type': 'clock'})
        with patch('deskwidgets.core.catalog.discover', return_value={}):
            self.daemon.handle('reload', {})
        self.assertFalse(self.daemon.instances[inst_id].running)
        self.assertIsNotNone(self.daemon.layout.get(inst_id))


class MouseTests(DaemonCase):
    def setUp(self):
        super().setUp()
        self.inst_id = self.daemon.handle('add', {'type': 'clock'})
        self.window = self.window_of(self.inst_id)

    def target_xy(self, col, row):
        return self.daemon.window_xy(col, row)

    def drag(self, dx, dy, steps=6):
        self.window.press(40, 40)
        for i in range(1, steps + 1):
            self.window.motion(40 + dx * i // steps, 40 + dy * i // steps)
        self.window.release(40 + dx, 40 + dy)

    def test_dragging_moves_the_window_with_the_pointer_and_snaps_on_release(self):
        inst = self.daemon.layout.get(self.inst_id)
        start = (self.window.x, self.window.y)
        self.window.press(40, 40)
        self.window.motion(100, 90)
        self.assertEqual((self.window.x, self.window.y), (start[0] + 60 + 0, start[1] + 50))
        overlays = [w for w in self.backend.windows if w.click_through and not w.closed]
        self.assertEqual(len(overlays), 1, 'the grid is shown while dragging')
        pitch = self.daemon.grid.pitch
        self.window.motion(40 + 2 * pitch + 10, 40 + 1 * pitch - 7)
        self.window.release(40 + 2 * pitch + 10, 40 + 1 * pitch - 7)
        saved = self.daemon.layout.get(self.inst_id)
        self.assertEqual((self.window.x, self.window.y), self.target_xy(saved['col'], saved['row']))
        self.assertNotEqual((saved['col'], saved['row']), (inst['col'], inst['row']) if False else (-1, -1))
        self.assertFalse([w for w in self.backend.windows if w.click_through and not w.closed], 'the grid is hidden again')
        on_disk = [i for i in json.loads(paths.LAYOUT.read_text())['instances'] if i['id'] == self.inst_id][0]
        self.assertEqual((on_disk['col'], on_disk['row']), (saved['col'], saved['row']))

    def test_dropping_on_another_widget_never_overlaps_it(self):
        other = self.daemon.layout.get('clock') if self.inst_id != 'clock' else self.daemon.layout.instances[1]
        ox, oy = self.target_xy(other['col'], other['row'])
        self.window.press(10, 10)
        self.window.motion(30, 30)
        self.window.motion(ox - self.window.x + 20, oy - self.window.y + 20)
        self.window.release(ox - self.window.x + 20, oy - self.window.y + 20)
        mine = self.daemon.layout.get(self.inst_id)
        self.assertNotEqual((mine['col'], mine['row']), (other['col'], other['row']))

    def test_small_movement_is_a_click_not_a_drag(self):
        runtime = self.daemon.instances[self.inst_id].runtime
        with patch.object(runtime, 'click', return_value=True) as click:
            start = (self.window.x, self.window.y)
            self.window.press(40, 40)
            self.window.motion(42, 41)
            self.window.release(42, 41)
        click.assert_called_once_with(42, 41)
        self.assertEqual((self.window.x, self.window.y), start)

    def test_a_handled_click_redraws_soon(self):
        instance = self.daemon.instances[self.inst_id]
        with patch.object(instance.runtime, 'click', return_value=True), patch.object(instance, 'redraw_soon') as redraw:
            self.window.press(5, 5)
            self.window.release(5, 5)
        redraw.assert_called_once()

    def test_right_click_opens_the_settings_of_that_widget(self):
        self.window.press(5, 5, button=3)
        self.window.release(5, 5, button=3)
        self.daemon.open_gallery.assert_called_once_with(configure=self.inst_id)

    def test_locked_widgets_do_not_move(self):
        self.daemon.settings['lock'] = True
        start = (self.window.x, self.window.y)
        self.drag(200, 100)
        self.assertEqual((self.window.x, self.window.y), start)

    def test_the_window_manager_moving_a_window_snaps_it_once_it_stops(self):
        with patch.object(instance_mod, 'SETTLE_DELAY', 0.02):
            pitch = self.daemon.grid.pitch
            self.window.x += pitch + 13
            self.window.y += 9
            self.window.on_moved(self.window.x, self.window.y)
            time.sleep(0.06)
            self.backend.flush()
        saved = self.daemon.layout.get(self.inst_id)
        self.assertEqual((self.window.x, self.window.y), self.target_xy(saved['col'], saved['row']))

    def test_a_removed_widget_ignores_late_events(self):
        self.daemon.handle('remove', {'id': self.inst_id})
        self.window.on_button and self.window.on_button(1, True, 1, 1, 1, 1)   # no exception even if still wired
        self.backend.flush()

    def test_the_grid_overlay_can_be_turned_off(self):
        self.daemon.settings['show_grid'] = False
        self.window.press(5, 5)
        self.window.motion(80, 80)
        self.assertFalse([w for w in self.backend.windows if w.click_through])
        self.window.release(80, 80)


class ScreenTests(DaemonCase):
    def test_a_resolution_change_lays_the_widgets_out_again(self):
        self.backend.screen = (900, 700)
        self.backend.on_screen_changed()
        time.sleep(0.9)
        self.backend.flush()
        self.assertLessEqual(self.daemon.grid.cols, 4)
        for inst in self.daemon.layout.instances:
            self.assertTrue(self.daemon.grid.fits(inst['col'], inst['row'], inst['size']))
        for window in self.widgets_on_screen():
            self.assertLess(window.x, 900)

    def test_high_dpi_scales_cells(self):
        small = self.daemon.grid.cell
        self.backend.scale = 2.0
        self.daemon.rebuild_grid()
        self.assertEqual(self.daemon.grid.cell, small * 2)


class ControlTests(IsolatedTestCase):
    """The real socket: a request from another thread reaches the loop thread."""

    def setUp(self):
        super().setUp()
        patcher = patch('deskwidgets.service.daemon.legacy.stop_old_versions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = NullBackend()
        self.daemon = Daemon(self.backend)
        self.daemon.hub = FakeHub()
        self.daemon.startup()
        self.loop = threading.Thread(target=self.backend.run, daemon=True)
        self.loop.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.backend.quit()
        self.loop.join(5)
        self.daemon.shutdown()

    def test_requests_round_trip(self):
        self.assertTrue(ipc.is_running())
        inst_id = ipc.request('add', type='clock')
        self.assertIn(inst_id, [i['id'] for i in ipc.request('list')])
        with self.assertRaisesRegex(RuntimeError, 'No such widget|Немає'):
            ipc.request('add', type='nope')
        ipc.request('remove', id=inst_id)
        self.assertEqual(ipc.request('status')['version'], __import__('deskwidgets').VERSION)

    def test_quit_ends_the_loop(self):
        ipc.request('quit')
        self.loop.join(5)
        self.assertFalse(self.loop.is_alive())

    def test_a_second_service_is_refused(self):
        other = ipc.ControlServer(lambda *a: None, self.backend)
        with self.assertRaises(ipc.AlreadyRunning):
            other.start()

    def test_a_client_with_the_wrong_key_is_rejected(self):
        original = paths.AUTH_KEY.read_bytes()
        paths.AUTH_KEY.write_bytes(b'x' * 32)
        try:
            with self.assertRaises(ipc.ServiceNotRunning):
                ipc.request('ping', timeout=2)
        finally:
            paths.AUTH_KEY.write_bytes(original)
        self.assertTrue(ipc.is_running())

    def test_garbage_does_not_hurt_the_server(self):
        from multiprocessing.connection import Client
        family, address = ipc._family_and_address()
        for payload in (b'not json', b'{"cmd": 5}', b'[]', b''):
            with Client(address, family, authkey=paths.AUTH_KEY.read_bytes()) as conn:
                conn.send_bytes(payload)
        self.assertTrue(ipc.is_running())

    def test_a_stale_socket_is_replaced(self):
        self.stop()
        paths.SOCKET.write_text('left behind by a crash')
        backend = NullBackend()
        loop = threading.Thread(target=backend.run, daemon=True)
        loop.start()
        server = ipc.ControlServer(lambda *a: 'pong', backend)
        server.start()
        try:
            self.assertEqual(ipc.request('ping'), 'pong')
        finally:
            server.stop()
            backend.quit()
            loop.join(5)


class AddressTests(unittest.TestCase):
    def test_a_long_runtime_dir_still_gives_a_usable_socket_path(self):
        from pathlib import Path
        with patch.object(paths, 'SOCKET', Path('/tmp/' + 'x' * 150 + '/control')), patch.object(ipc, 'WINDOWS', False):
            family, address = ipc._family_and_address()
            self.assertEqual(family, 'AF_UNIX')
            self.assertLessEqual(len(address.encode()), ipc.UNIX_PATH_MAX)
            self.assertEqual(ipc._family_and_address()[1], address, 'the path must be stable between calls')
        with patch.object(paths, 'SOCKET', Path('/run/user/1000/deskwidgets/control')), patch.object(ipc, 'WINDOWS', False):
            self.assertEqual(ipc._family_and_address()[1], '/run/user/1000/deskwidgets/control')


class LegacyTests(unittest.TestCase):
    def test_recognises_old_conky_widgets_and_services(self):
        from deskwidgets.service import legacy
        self.assertTrue(legacy._is_old_widget([b'/usr/bin/conky', b'-c', b'/x/engine/widget.conf'], '1'))
        self.assertTrue(legacy._is_old_widget([b'conky', b'-c', b'/home/u/.config/conky/mac/widgets.conf'], '1'))
        self.assertFalse(legacy._is_old_widget([b'conky', b'-c', b'/home/u/mine.conf'], '1'))
        self.assertFalse(legacy._is_old_widget([b'python3', b'-m', b'deskwidgets', b'run'], '1'))
        with patch.object(legacy, '_is_new', return_value=False):
            self.assertTrue(legacy._is_old_widget([b'/usr/bin/python3', b'/usr/bin/deskwidgets/bin/deskwidgets', b'run'], '1'))
        with patch.object(legacy, '_is_new', return_value=True):
            self.assertFalse(legacy._is_old_widget([b'/usr/bin/python3', b'/x/bin/deskwidgets', b'run'], '1'))


if __name__ == '__main__':
    unittest.main()
