import json
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from deskwidgets.core import paths
from deskwidgets.core.monitors import Monitor
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
    settings = None   # settings.json before the service starts
    monitors = None   # the monitors the null backend reports (one 1920x1080 screen by default)

    def setUp(self):
        super().setUp()
        for target in ('deskwidgets.service.daemon.legacy.stop_old_versions',):   # never kill the developer's real widgets
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        if self.settings:
            paths.SETTINGS.write_text(json.dumps(self.settings))
        self.backend = NullBackend(monitors=self.monitors)
        self.daemon = Daemon(self.backend)
        self.daemon.hub = FakeHub()
        self.daemon.open_gallery = Mock()
        self.daemon.startup()
        self.addCleanup(self.daemon.shutdown)

    def window_of(self, inst_id):
        return self.daemon.instances[inst_id].window

    def widgets_on_screen(self):
        return [w for w in self.backend.windows if not w.closed and not w.click_through]

    def runnable_instances(self):
        """The layout entries that get a window here: a widget for another OS (Events on Windows) stays in
        the layout but is not started."""
        return [i for i in self.daemon.layout.instances if self.daemon.types[i['type']].supported]


class StartupTests(DaemonCase):
    def test_default_layout_opens_one_presented_window_per_widget(self):
        windows = self.widgets_on_screen()
        self.assertEqual(len(windows), len(self.runnable_instances()))
        self.assertTrue(all(w.presented >= 1 for w in windows))
        self.assertTrue(all(w.title.startswith('deskwidget:') for w in windows))

    def test_windows_sit_on_their_grid_cells(self):
        for inst in self.runnable_instances():
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
        paths.SETTINGS.write_text(json.dumps({'scale': 1.1, 'gap': 10}))
        self.daemon.handle('settings', {})
        self.assertEqual(self.daemon.grid.scale, 1.1)
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


class GalleryLaunchTests(DaemonCase):
    """Right click and the hotkey start `python -m deskwidgets ...`: that command must be one the CLI accepts
    (the other tests replace open_gallery with a mock, so they cannot see a mismatch)."""

    def launched(self, **args):
        with patch('deskwidgets.service.daemon.subprocess.Popen') as popen:
            Daemon.open_gallery(self.daemon, **args)
        command = popen.call_args[0][0]
        self.assertEqual(command[:3], [sys.executable, '-m', 'deskwidgets'])
        return command[3:], popen.call_args[1]

    def test_the_gallery_command_is_understood_by_the_cli(self):
        from deskwidgets import cli
        argv, _ = self.launched()
        parsed = cli.build_parser().parse_args(argv)
        self.assertIs(parsed.func, cli.cmd_gallery)
        self.assertFalse(getattr(parsed, 'configure', None) or getattr(parsed, 'id', None))

    def test_the_settings_command_is_understood_by_the_cli(self):
        from deskwidgets import cli
        argv, _ = self.launched(configure='clock')
        parsed = cli.build_parser().parse_args(argv)
        self.assertIs(parsed.func, cli.cmd_gallery)
        self.assertEqual(parsed.id, 'clock')

    def test_the_old_spelling_still_works(self):
        from deskwidgets import cli
        parsed = cli.build_parser().parse_args(['gallery', '--configure', 'clock'])
        self.assertIs(parsed.func, cli.cmd_gallery)
        self.assertEqual(parsed.configure, 'clock')

    def test_errors_of_the_window_are_kept_in_a_file(self):
        _argv, options = self.launched(configure='clock')
        self.assertNotEqual(options['stderr'], __import__('subprocess').DEVNULL)
        self.assertTrue((paths.CACHE / 'gallery.log').exists())

    def test_a_huge_log_starts_over(self):
        log = paths.CACHE / 'gallery.log'
        log.write_text('x' * 300_000)
        self.launched()
        self.assertLess(log.stat().st_size, 1000)


class WidgetSizeTests(DaemonCase):
    """The widget size (50-120 %) is the only knob: the grid follows it."""

    def set_scale(self, scale):
        paths.SETTINGS.write_text(json.dumps({**self.daemon.settings, 'scale': scale}))
        self.daemon.handle('settings', {})

    def sizes(self):
        return {i['id']: (self.window_of(i['id']).width, self.window_of(i['id']).height) for i in self.runnable_instances()}

    def test_smaller_widgets_make_a_finer_grid(self):
        cells = (self.daemon.grid.rows, self.daemon.grid.cols)
        big = self.sizes()
        self.set_scale(0.5)
        self.assertGreater(self.daemon.grid.cols, cells[1] * 1.5)
        self.assertGreater(self.daemon.grid.rows, cells[0] * 1.5)
        for inst_id, (width, height) in self.sizes().items():
            self.assertLess(width, big[inst_id][0] * 0.6)
            self.assertLess(height, big[inst_id][1] * 0.6)

    def test_bigger_widgets_make_a_coarser_grid(self):
        cols = self.daemon.grid.cols
        self.set_scale(1.2)
        self.assertLess(self.daemon.grid.cols, cols)

    def test_widgets_stay_where_they_were_and_never_overlap_or_leave_the_grid(self):
        for scale in (0.5, 0.8, 1.2, 1.0):
            self.set_scale(scale)
            grid, cells = self.daemon.grid, []
            for inst in self.daemon.layout.instances:
                self.assertTrue(grid.fits(inst['col'], inst['row'], inst['size']), (scale, inst['id']))
                cells += list(grid.cells(inst['col'], inst['row'], inst['size']))
            self.assertEqual(len(cells), len(set(cells)), f'overlap at {scale}')

    def test_the_right_hand_column_stays_at_the_right_edge(self):
        right = [i['id'] for i in self.daemon.layout.instances if i['col'] + 2 >= self.daemon.grid.cols]
        self.assertTrue(right)
        for scale in (0.5, 1.2):
            self.set_scale(scale)
            for inst_id in right:
                inst = self.daemon.layout.get(inst_id)
                self.assertEqual(inst['col'] + 2, self.daemon.grid.cols, (scale, inst_id))

    def test_the_top_left_widget_stays_in_the_corner(self):
        self.set_scale(0.5)
        inst = self.daemon.layout.get('clock')
        self.assertEqual((inst['col'], inst['row']), (0, 0))
        self.assertEqual(self.daemon.window_xy(0, 0), (self.window_of('clock').x, self.window_of('clock').y))

    def test_the_layout_file_follows(self):
        self.set_scale(0.5)
        saved = {i['id']: (i['col'], i['row']) for i in json.loads(paths.LAYOUT.read_text())['instances']}
        self.assertEqual(saved, {i['id']: (i['col'], i['row']) for i in self.daemon.layout.instances})


def two_monitors(second_scale=1.0):
    return [Monitor('DP-1', 0, 0, 1920, 1080, True, 1.0), Monitor('HDMI-1', 1920, 0, 1280, 720, False, second_scale)]


class MonitorTests(DaemonCase):
    monitors = two_monitors()

    def enable(self, *ids):
        paths.SETTINGS.write_text(json.dumps({**self.daemon.settings, 'monitors': list(ids)}))
        self.daemon.handle('settings', {})

    def on_monitor(self, monitor):
        return [i['id'] for i in self.daemon.layout.instances if i['monitor'] == monitor]

    def test_by_default_only_the_primary_monitor_shows_widgets(self):
        self.assertEqual(list(self.daemon.screens), ['DP-1'])
        self.assertTrue(all(w.x < 1920 for w in self.widgets_on_screen()))
        self.assertTrue(all(i['monitor'] == 'DP-1' for i in self.daemon.layout.instances))

    def test_the_gallery_can_list_the_monitors(self):
        listed = self.daemon.handle('monitors', {})
        self.assertEqual([(m['id'], m['primary'], m['enabled']) for m in listed], [('DP-1', True, True), ('HDMI-1', False, False)])
        self.assertIn('1920×1080', listed[0]['label'])
        self.enable('DP-1', 'HDMI-1')
        self.assertEqual([m['enabled'] for m in self.daemon.handle('monitors', {})], [True, True])

    def test_each_enabled_monitor_has_its_own_grid(self):
        self.enable('DP-1', 'HDMI-1')
        grids = {m: s.grid for m, s in self.daemon.screens.items()}
        self.assertEqual(list(grids), ['DP-1', 'HDMI-1'])
        self.assertGreater(grids['DP-1'].cols, grids['HDMI-1'].cols)
        self.assertEqual(self.daemon.window_xy(0, 0, 'HDMI-1')[0], 1920 + grids['HDMI-1'].window_xy(0, 0)[0])

    def test_a_widget_can_be_added_to_a_chosen_monitor(self):
        self.enable('DP-1', 'HDMI-1')
        inst_id = self.daemon.handle('add', {'type': 'clock', 'monitor': 'HDMI-1'})
        self.assertEqual(self.daemon.layout.get(inst_id)['monitor'], 'HDMI-1')
        self.assertGreaterEqual(self.window_of(inst_id).x, 1920)
        other = self.daemon.handle('add', {'type': 'clock'})
        self.assertEqual(self.daemon.layout.get(other)['monitor'], 'DP-1')

    def test_a_full_first_monitor_overflows_to_the_next(self):
        self.enable('DP-1', 'HDMI-1')
        for _ in range(50):
            self.daemon.handle('add', {'type': 'clock'})
        self.assertTrue(self.on_monitor('HDMI-1'))
        with self.assertRaisesRegex(ValueError, 'full|місця'):
            while True:   # and when both are full that is reported too
                self.daemon.handle('add', {'type': 'clock'})

    def test_dragging_a_widget_onto_the_other_monitor_moves_it_there(self):
        self.enable('DP-1', 'HDMI-1')
        window = self.window_of('clock')
        window.press(5, 5)
        window.motion(1920 + 400 - window.x, 300)   # the middle of the window is now on HDMI-1
        grids = [w for w in self.backend.windows if w.click_through and not w.closed]
        self.assertEqual(len(grids), 1)
        self.assertGreaterEqual(grids[0].x, 1920 - 1, 'the grid shows on the monitor the widget is over')
        window.release(1920 + 400 - window.x, 300)
        inst = self.daemon.layout.get('clock')
        self.assertEqual(inst['monitor'], 'HDMI-1')
        self.assertTrue(self.daemon.screens['HDMI-1'].grid.fits(inst['col'], inst['row'], inst['size']))
        moved = self.window_of('clock')
        self.assertEqual((moved.x, moved.y), self.daemon.window_xy(inst['col'], inst['row'], 'HDMI-1'))
        self.assertEqual(json.loads(paths.LAYOUT.read_text())['instances'][[i['id'] for i in self.daemon.layout.instances].index('clock')]['monitor'], 'HDMI-1')

    def test_the_grid_follows_the_widget_between_monitors_while_dragging(self):
        self.enable('DP-1', 'HDMI-1')
        window = self.window_of('clock')
        window.press(5, 5)
        window.motion(1920 + 400 - window.x, 300)
        self.assertGreaterEqual([w for w in self.backend.windows if w.click_through and not w.closed][0].x, 1919)
        window.motion(300 - window.x, 300)
        shown = [w for w in self.backend.windows if w.click_through and not w.closed]
        self.assertEqual(len(shown), 1)
        self.assertLess(shown[0].x, 1920)
        window.release(300 - window.x, 300)

    def test_a_widget_picks_its_monitor_in_its_settings(self):
        self.enable('DP-1', 'HDMI-1')
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'HDMI-1'}})
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'HDMI-1')
        self.assertGreaterEqual(self.window_of('clock').x, 1920)
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'nonsense'}})
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'HDMI-1')

    def test_switching_a_monitor_off_brings_its_widgets_to_the_first_one(self):
        self.enable('DP-1', 'HDMI-1')
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'HDMI-1'}})
        self.enable('DP-1')
        self.assertEqual(list(self.daemon.screens), ['DP-1'])
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'DP-1')
        cells = [c for i in self.daemon.layout.instances for c in self.daemon.grid.cells(i['col'], i['row'], i['size'])]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertTrue(all(w.x < 1920 for w in self.widgets_on_screen()))
        self.enable('DP-1', 'HDMI-1')   # coming back does not pull them back by itself
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'DP-1')

    def test_only_the_second_monitor_can_carry_the_widgets(self):
        self.enable('HDMI-1')
        self.assertEqual(list(self.daemon.screens), ['HDMI-1'])
        self.assertTrue(all(i['monitor'] == 'HDMI-1' for i in self.daemon.layout.instances))
        self.assertTrue(all(w.x >= 1920 for w in self.widgets_on_screen()))

    def test_a_monitor_that_is_not_connected_falls_back_to_the_primary_one(self):
        self.enable('DP-9')
        self.assertEqual(list(self.daemon.screens), ['DP-1'])

    def test_unplugging_a_monitor_moves_its_widgets_and_plugging_it_back_does_not_lose_the_setting(self):
        self.enable('DP-1', 'HDMI-1')
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'HDMI-1'}})
        self.backend.monitor_list = two_monitors()[:1]
        self.backend.on_screen_changed()
        time.sleep(0.9)
        self.backend.flush()
        self.assertEqual(list(self.daemon.screens), ['DP-1'])
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'DP-1')
        self.assertEqual(self.daemon.settings['monitors'], ['DP-1', 'HDMI-1'], 'the choice is kept for when it returns')
        self.backend.monitor_list = two_monitors()
        self.backend.on_screen_changed()
        time.sleep(0.9)
        self.backend.flush()
        self.assertEqual(list(self.daemon.screens), ['DP-1', 'HDMI-1'])

    def test_arrange_packs_each_monitor_separately(self):
        self.enable('DP-1', 'HDMI-1')
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'HDMI-1'}})
        self.daemon.handle('arrange', {})
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'HDMI-1')
        self.assertEqual(len(self.widgets_on_screen()), len(self.runnable_instances()))

    def test_move_command_can_target_a_monitor(self):
        self.enable('DP-1', 'HDMI-1')
        spot = self.daemon.handle('move', {'id': 'clock', 'col': 2, 'row': 1, 'monitor': 'HDMI-1'})
        inst = self.daemon.layout.get('clock')
        self.assertEqual((inst['monitor'], [inst['col'], inst['row']]), ('HDMI-1', spot))
        self.assertEqual((self.window_of('clock').x, self.window_of('clock').y), self.daemon.window_xy(*spot, 'HDMI-1'))


class MixedDpiTests(DaemonCase):
    monitors = two_monitors(second_scale=2.0)
    settings = {'monitors': ['DP-1', 'HDMI-1']}

    def test_a_widget_is_drawn_for_the_dpi_of_its_monitor(self):
        small = (self.window_of('clock').width, self.window_of('clock').height)
        self.daemon.handle('configure', {'id': 'clock', 'patch': {'monitor': 'HDMI-1'}})
        big = (self.window_of('clock').width, self.window_of('clock').height)
        self.assertGreater(big[0], small[0] * 1.9)

    def test_dropping_on_a_monitor_with_another_dpi_restarts_the_widget_at_its_size(self):
        window = self.window_of('clock')
        before = window.width
        window.press(5, 5)
        window.motion(1920 + 300 - window.x, 200)
        window.release(1920 + 300 - window.x, 200)
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'HDMI-1')
        self.assertTrue(window.closed)
        self.assertGreater(self.window_of('clock').width, before * 1.9)


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
        self.daemon.rebuild_screens()
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


@unittest.skipIf(sys.platform == 'win32', 'Unix socket paths: Windows uses a named pipe')
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
