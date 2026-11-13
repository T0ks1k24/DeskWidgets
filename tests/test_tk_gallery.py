"""The Tk gallery (Windows, and Linux without GTK 4) against a real service on its control socket."""
import gc
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:   # a Python without Tk
    tk = None

from deskwidgets.core import paths
from deskwidgets.core.monitors import Monitor
from deskwidgets.core.i18n import set_lang
from deskwidgets.platform.null import NullBackend
from deskwidgets.service.daemon import Daemon

from helpers import IsolatedTestCase
from test_service import FakeHub


def widgets_below(widget):
    for child in widget.winfo_children():
        yield child
        yield from widgets_below(child)


@unittest.skipIf(tk is None, 'Tk is not installed')
class TkCase(IsolatedTestCase):
    """A service on its control socket (with a null backend) and the helpers to drive a Tk gallery."""
    monitors = None   # the monitors the null backend reports
    settings = {'language': 'en'}   # the texts below are English whatever the machine speaks

    def setUp(self):
        super().setUp()
        from deskwidgets.gallery import common, tk_app
        self.tk_app, self.common = tk_app, common
        try:
            probe = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f'no display for Tk: {e}')
        probe.destroy()
        paths.SETTINGS.write_text(json.dumps(self.settings))
        self.addCleanup(set_lang, None)
        for target in ('deskwidgets.service.daemon.legacy.stop_old_versions',):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        # nothing may touch the developer's autostart entries, registry, or start child render processes
        self.setup_mock = Mock(return_value={})
        for target, value in (('deskwidgets.gallery.tk_app.integration.setup', self.setup_mock),
                              ('deskwidgets.gallery.tk_app.integration.set_autostart', Mock(return_value=True)),
                              ('deskwidgets.gallery.tk_app.integration.set_hotkey', Mock(return_value=True)),
                              ('deskwidgets.gallery.common.start_thumbnails', Mock(return_value=Mock(poll=Mock(return_value=0)))),
                              ('deskwidgets.gallery.common.start_preview',
                               Mock(return_value=Mock(poll=Mock(return_value=0), returncode=1)))):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = NullBackend(monitors=self.monitors)
        self.daemon = Daemon(self.backend)
        self.daemon.hub = FakeHub()
        self.daemon.startup()
        self.loop = threading.Thread(target=self.backend.run, daemon=True)
        self.loop.start()
        self.addCleanup(self.stop_service)
        self.apps = []

    def stop_service(self):
        for app in self.apps:
            try:
                app.shutdown()
            except tk.TclError:
                pass   # already gone
        self.apps.clear()
        gc.collect()   # Tk variables must be freed on this thread, the one that owns Tk, not on the service's
        self.backend.quit()
        self.loop.join(5)
        self.daemon.shutdown()

    def gallery(self, configure=None):
        app = self.tk_app.Gallery(configure)
        self.apps.append(app)
        return app

    def pump(self, app, seconds=0.15):
        end = time.time() + seconds
        while time.time() < end:
            app.root.update()
            time.sleep(0.01)

    def wait_for(self, app, condition, seconds=4):
        end = time.time() + seconds
        while time.time() < end:
            self.pump(app, 0.05)
            if condition():
                return True
        return False

    def restart_buttons(self, app):
        return [w for w in widgets_below(app.prefs_page) if isinstance(w, ttk.Button) and w.cget('text') in ('Restart', 'Restarting…')]

    def combobox(self, window, containing):
        for widget in widgets_below(window):
            if isinstance(widget, ttk.Combobox) and any(containing in str(v) for v in widget.cget('values')):
                return widget
        self.fail(f'no choice with {containing!r}')

    def choose(self, app, box, label):
        box.set(label)
        box.event_generate('<<ComboboxSelected>>')
        self.pump(app)

    def ids(self):
        return [i['id'] for i in json.loads(paths.LAYOUT.read_text())['instances']]


class TkGalleryTests(TkCase):
    # the gallery window
    def test_my_widgets_lists_what_is_on_the_desktop(self):
        app = self.gallery()
        self.pump(app)
        self.assertEqual(list(app.mine.get_children()), [i['id'] for i in self.daemon.layout.instances])

    def test_first_run_sets_up_the_desktop_once(self):
        self.gallery()
        self.setup_mock.assert_called_once()
        self.assertTrue(json.loads(paths.SETTINGS.read_text())['_setup_done'])
        self.gallery()
        self.setup_mock.assert_called_once()

    def test_adding_a_widget_from_the_catalog(self):
        app = self.gallery()
        before = self.ids()
        app.catalog.selection_set('clock')
        app.add_selected()
        self.assertEqual(len(self.ids()), len(before) + 1)
        self.assertIn('clock-2', self.ids())
        self.assertIn('clock-2', app.mine.get_children())

    def test_a_category_row_adds_nothing(self):
        app = self.gallery()
        before = self.ids()
        app.catalog.selection_set('cat:time')
        app.shown = None
        app.add_selected()
        self.assertEqual(self.ids(), before)

    def test_removing_the_selected_widget(self):
        app = self.gallery()
        app.mine.selection_set('clock')
        app.remove_selected()
        self.assertNotIn('clock', self.ids())
        self.assertNotIn('clock', app.mine.get_children())

    def test_the_list_follows_changes_made_elsewhere(self):
        app = self.gallery()
        self.daemon.handle('add', {'type': 'clock'})
        self.assertNotIn('clock-2', app.mine.get_children())
        app.poll_mine()
        self.assertIn('clock-2', app.mine.get_children())

    # one widget's settings
    def test_the_settings_window_changes_the_widget_on_the_desktop(self):
        app = self.gallery()
        self.assertTrue(app.open_settings('clock'))
        window = app.windows['clock']
        self.choose(app, self.combobox(window, 'Neon'), 'Neon (anime)')
        self.assertEqual(self.daemon.layout.get('clock')['style'], 'neon')
        self.choose(app, self.combobox(window, 'Medium'), 'Medium')
        self.assertEqual(self.daemon.layout.get('clock')['size'], 'medium')

    def test_options_are_sent_too(self):
        app = self.gallery()
        app.open_settings('clock')
        window = app.windows['clock']
        boxes = [w for w in widgets_below(window) if isinstance(w, ttk.Checkbutton)]
        self.assertTrue(boxes)
        boxes[0].invoke()   # the first option of the clock: the second hand
        self.pump(app)
        self.assertEqual(self.daemon.layout.get('clock')['options'], {'seconds': False})

    def test_the_accent_swatches_pick_a_colour(self):
        app = self.gallery()
        app.open_settings('clock')
        window = app.windows['clock']
        self.pump(app)   # clicks only reach widgets that are on the screen
        swatches = [w for w in widgets_below(window) if isinstance(w, tk.Label) and w.cget('cursor') == 'hand2']
        self.assertEqual(len(swatches), 13)
        swatches[3].event_generate('<Button-1>')   # yellow
        self.pump(app)
        self.assertEqual(self.daemon.layout.get('clock')['accent'], 'yellow')

    def test_removing_from_the_settings_window(self):
        app = self.gallery()
        app.open_settings('clock')
        window = app.windows['clock']
        window.remove()
        self.assertNotIn('clock', self.ids())
        self.assertNotIn('clock', app.windows)

    def test_opening_the_same_settings_twice_reuses_the_window(self):
        app = self.gallery()
        app.open_settings('clock')
        first = app.windows['clock']
        app.open_settings('clock')
        self.assertIs(app.windows['clock'], first)
        self.assertEqual(len(app.windows), 1)

    # what right click does: `deskwidgets configure <id>`
    def test_configure_mode_shows_only_that_widgets_settings(self):
        app = self.gallery('clock')
        self.assertEqual(app.root.state(), 'withdrawn')
        self.assertIn('clock', app.windows)
        self.assertEqual(app.windows['clock'].title(), 'Clock — settings')
        app.windows['clock'].close()
        with self.assertRaises(tk.TclError):
            app.root.winfo_exists()   # the process ends with the window

    def test_configure_mode_for_a_missing_widget_just_ends(self):
        app = self.gallery('no-such-widget')
        with self.assertRaises(tk.TclError):
            app.root.winfo_exists()

    def test_main_passes_the_widget_to_the_gallery(self):
        with patch.object(self.tk_app, 'Gallery') as gallery, patch.object(self.tk_app, 'make_dpi_aware'):
            gallery.return_value.run.return_value = 0
            self.assertEqual(self.tk_app.main(['--configure', 'clock']), 0)
        gallery.assert_called_once_with('clock')

    def test_a_second_open_brings_the_first_window_forward(self):
        with patch.object(self.tk_app, 'Gallery') as gallery, patch.object(self.tk_app, 'raise_existing', return_value=True) as raised:
            self.assertEqual(self.tk_app.main([]), 0)
        raised.assert_called_once_with('Widgets')
        gallery.assert_not_called()

    # global settings
    def test_language_change_builds_the_window_again(self):
        app = self.gallery()
        box = self.combobox(app.prefs_page, 'English')
        self.choose(app, box, 'Українська')
        self.assertEqual(json.loads(paths.SETTINGS.read_text())['language'], 'uk')
        self.assertEqual(app.book.tab(0, 'text'), 'Мої віджети')

    # widget size
    def spinbox(self, window, lowest):
        for widget in widgets_below(window):
            if isinstance(widget, ttk.Spinbox) and float(widget.cget('from')) == lowest:
                return widget
        self.fail('no spin box')

    def test_widget_size_runs_from_50_to_120_percent_and_the_grid_follows(self):
        app = self.gallery()
        spin = self.spinbox(app.prefs_page, 50)
        self.assertEqual(float(spin.cget('to')), 120)
        self.assertEqual(spin.get(), '100')
        cols = self.daemon.grid.cols
        spin.set('60')
        self.assertTrue(self.wait_for(app, lambda: self.daemon.settings['scale'] == 0.6))
        self.assertGreater(self.daemon.grid.cols, cols)
        spin.set('500')   # too big: 120 at most
        self.assertTrue(self.wait_for(app, lambda: self.daemon.settings['scale'] == 1.2))
        spin.set('abc')   # not a number: nothing happens
        self.pump(app, 1.0)
        self.assertEqual(self.daemon.settings['scale'], 1.2)

    def test_there_are_no_grid_settings(self):
        app = self.gallery()
        spins = [w for w in widgets_below(app.prefs_page) if isinstance(w, ttk.Spinbox)]
        self.assertEqual(len(spins), 1, 'only the widget size is a number to set')
        frames = [w.cget('text') for w in widgets_below(app.prefs_page) if isinstance(w, ttk.Labelframe)]
        self.assertNotIn('Grid', frames)

    def test_a_current_service_shows_no_restart_banner(self):
        app = self.gallery()
        self.assertFalse(self.restart_buttons(app))

    def test_an_old_service_offers_to_restart_and_the_window_builds_again_afterwards(self):
        with patch.object(self.common, 'service_outdated', return_value=True):
            app = self.gallery()
            (button,) = self.restart_buttons(app)
            with patch.object(self.common, 'restart_service', return_value=True) as restart:
                with patch.object(self.common, 'service_outdated', return_value=False):
                    button.invoke()
                    self.assertEqual(str(button.cget('state')), 'disabled')
                    self.assertTrue(self.wait_for(app, lambda: not self.restart_buttons(app) and restart.called))
        restart.assert_called_once_with()
        self.assertEqual(app.book.index(app.book.select()), 2, 'it stays on the Settings page')

    def test_one_monitor_means_no_monitor_choices(self):
        app = self.gallery()
        self.assertFalse([w for w in widgets_below(app.prefs_page) if isinstance(w, ttk.Labelframe) and w.cget('text') == 'Monitors'])
        app.open_settings('clock')
        self.assertFalse([w for w in widgets_below(app.windows['clock']) if isinstance(w, ttk.Combobox)
                          and any('1920' in str(v) for v in w.cget('values'))])


def two_monitors():
    return [Monitor('DP-1', 0, 0, 1920, 1080, True, 1.0), Monitor('HDMI-1', 1920, 0, 1280, 720, False, 1.0)]


class TkMonitorTests(TkCase):
    monitors = two_monitors()
    settings = {'language': 'en', 'monitors': ['DP-1']}

    def ticks(self, app):
        return {w.cget('text'): w for w in widgets_below(app.prefs_page) if isinstance(w, ttk.Checkbutton) and 'Mon' not in w.cget('text')
                and '×' in w.cget('text')}

    def test_every_monitor_has_a_check_box_that_is_ticked_when_it_shows_widgets(self):
        app = self.gallery()
        ticks = self.ticks(app)
        self.assertEqual(len(ticks), 2)
        dp, hdmi = ticks['DP-1 · 1920×1080 · primary'], ticks['HDMI-1 · 1280×720']
        self.assertTrue(dp.instate(['selected']))
        self.assertFalse(hdmi.instate(['selected']))

    def test_ticking_a_monitor_puts_a_grid_on_it(self):
        app = self.gallery()
        self.ticks(app)['HDMI-1 · 1280×720'].invoke()
        self.assertTrue(self.wait_for(app, lambda: list(self.daemon.screens) == ['DP-1', 'HDMI-1']))
        self.assertEqual(json.loads(paths.SETTINGS.read_text())['monitors'], ['DP-1', 'HDMI-1'])

    def test_the_last_monitor_cannot_be_switched_off(self):
        app = self.gallery()
        dp = self.ticks(app)['DP-1 · 1920×1080 · primary']
        dp.invoke()
        self.pump(app, 0.3)
        self.assertTrue(dp.instate(['selected']))
        self.assertEqual(json.loads(paths.SETTINGS.read_text())['monitors'], ['DP-1'])
        self.assertIn('At least one monitor', app.status.get())

    def test_a_widget_picks_its_monitor_in_its_settings_window(self):
        self.daemon.settings['monitors'] = ['DP-1', 'HDMI-1']
        paths.SETTINGS.write_text(json.dumps({'language': 'en', 'monitors': ['DP-1', 'HDMI-1']}))
        self.daemon.handle('settings', {})
        app = self.gallery()
        app.open_settings('clock')
        window = app.windows['clock']
        box = self.combobox(window, 'HDMI-1')
        self.assertEqual(box.get(), 'DP-1 · 1920×1080 · primary')
        self.choose(app, box, 'HDMI-1 · 1280×720')
        self.assertEqual(self.daemon.layout.get('clock')['monitor'], 'HDMI-1')
        self.assertGreaterEqual(self.daemon.instances['clock'].window.x, 1920)


if __name__ == '__main__':
    unittest.main()
