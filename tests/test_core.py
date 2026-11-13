import concurrent.futures
import json
import unittest
from pathlib import Path

from deskwidgets.core import catalog, paths, settings
from deskwidgets.core import monitors
from deskwidgets.core.grid import Grid
from deskwidgets.core.layout import Layout
from deskwidgets.core.util import write_atomic

from helpers import isolated_paths


class FileTests(unittest.TestCase):
    def test_concurrent_atomic_writes_leave_one_complete_file(self):
        with isolated_paths() as root:
            target = root / 'state'
            texts = [str(i) * 10000 for i in range(32)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda text: write_atomic(target, text), texts))
            self.assertIn(target.read_text(), texts)
            self.assertEqual([p.name for p in root.iterdir() if p.is_file()], ['state'])


class SettingsTests(unittest.TestCase):
    def test_invalid_numbers_fall_back_to_defaults(self):
        with isolated_paths():
            for value in (float('nan'), float('inf'), -float('inf'), None, [], 'big'):
                paths.SETTINGS.write_text(json.dumps({'scale': value, 'gap': value}))
                loaded = settings.load()
                self.assertEqual(loaded['scale'], settings.DEFAULTS['scale'])
                self.assertEqual(loaded['gap'], settings.DEFAULTS['gap'])

    def test_numbers_are_clamped_and_flags_must_be_booleans(self):
        with isolated_paths():
            paths.SETTINGS.write_text(json.dumps({'scale': 9, 'margin_top': -5, 'lock': 'yes', 'unknown': 1}))
            loaded = settings.load()
            self.assertEqual(loaded['scale'], 1.2)   # widget size runs from 50 % to 120 %
            self.assertEqual(loaded['margin_top'], 0)
            paths.SETTINGS.write_text(json.dumps({'scale': 0.1}))
            self.assertEqual(settings.load()['scale'], 0.5)
            self.assertIs(loaded['lock'], settings.DEFAULTS['lock'])
            self.assertNotIn('unknown', loaded)

    def test_monitors_must_be_a_list_of_names(self):
        with isolated_paths():
            self.assertEqual(settings.load()['monitors'], [])
            paths.SETTINGS.write_text(json.dumps({'monitors': ['DP-1', 'HDMI-1']}))
            self.assertEqual(settings.load()['monitors'], ['DP-1', 'HDMI-1'])
            for bad in ('DP-1', [1, 2], ['DP-1', ''], {'DP-1': True}, None):
                paths.SETTINGS.write_text(json.dumps({'monitors': bad}))
                self.assertEqual(settings.load()['monitors'], [], bad)
            settings.load()['monitors'].append('x')
            self.assertEqual(settings.DEFAULTS['monitors'], [], 'the default list must not be shared')

    def test_corrupt_file_gives_defaults(self):
        with isolated_paths():
            paths.SETTINGS.write_text('{not json')
            self.assertEqual(settings.load(), settings.DEFAULTS)


class CatalogTests(unittest.TestCase):
    def test_every_builtin_widget_loads_with_valid_options(self):
        types = catalog.discover()
        self.assertGreaterEqual(len(types), 20)
        for t in types.values():
            self.assertTrue((t.dir / 'widget.lua').is_file(), t.id)
            self.assertTrue(t.sizes, t.id)
            for option in t.options:
                option.coerce(option.default_value('en'))   # the default of an option must be valid itself

    def test_user_widget_overrides_builtin(self):
        with isolated_paths():
            folder = paths.USER_WIDGETS / 'clock'
            folder.mkdir(parents=True)
            (folder / 'widget.json').write_text(json.dumps({'id': 'clock', 'name': 'Mine'}))
            (folder / 'widget.lua').write_text('return {}')
            self.assertTrue(catalog.discover()['clock'].user)

    def test_broken_manifest_is_skipped_not_fatal(self):
        with isolated_paths():
            folder = paths.USER_WIDGETS / 'broken'
            folder.mkdir(parents=True)
            (folder / 'widget.json').write_text('{oops')
            (folder / 'widget.lua').write_text('return {}')
            self.assertNotIn('broken', catalog.discover())
            self.assertIn('clock', catalog.discover())

    def test_option_coercion(self):
        number = catalog.Option('n', 'number', min=1, max=5, step=1)
        self.assertEqual(number.coerce('9'), 5)
        self.assertEqual(number.coerce(0), 1)
        with self.assertRaises(ValueError):
            number.coerce('nan')
        with self.assertRaises(ValueError):
            number.coerce(True)
        choice = catalog.Option('c', 'choice', choices=[('a', 'A'), ('b', 'B')])
        self.assertEqual(choice.coerce('a'), 'a')
        with self.assertRaises(ValueError):
            choice.coerce('z')
        multi = catalog.Option('m', 'multi', choices=[('a', 'A'), ('b', 'B'), ('c', 'C')])
        self.assertEqual(multi.coerce('c,zzz,a'), 'a,c')
        date = catalog.Option('d', 'date')
        self.assertEqual(date.coerce('2026-01-31'), '2026-01-31')
        self.assertEqual(date.coerce(''), '')
        with self.assertRaises(ValueError):
            date.coerce('31.01.2026')
        with self.assertRaises(ValueError):
            catalog.Option('b', 'bool').coerce('true')


class GridTests(unittest.TestCase):
    def setUp(self):
        self.grid = Grid(1920, 1080, settings.DEFAULTS)

    def test_sizes_and_cells(self):
        self.assertEqual(Grid.cells(1, 2, 'large'), {(1, 2), (2, 2), (1, 3), (2, 3)})
        self.assertTrue(self.grid.fits(0, 0, 'large'))
        self.assertFalse(self.grid.fits(self.grid.cols - 1, 0, 'medium'))

    def test_free_near_skips_taken_cells(self):
        taken = Grid.cells(2, 2, 'small')
        spot = self.grid.free_near(2, 2, 'small', taken)
        self.assertNotEqual(spot, (2, 2))
        self.assertAlmostEqual(abs(spot[0] - 2) + abs(spot[1] - 2), 1)

    def test_full_grid_has_no_free_spot(self):
        everything = {(c, r) for c in range(self.grid.cols) for r in range(self.grid.rows)}
        self.assertIsNone(self.grid.free_near(0, 0, 'small', everything))
        self.assertIsNone(self.grid.first_free('small', everything))

    def test_nearest_cell_round_trip(self):
        for col, row in ((0, 0), (3, 2), (self.grid.cols - 2, self.grid.rows - 2)):
            x, y = self.grid.window_xy(col, row)
            self.assertEqual(self.grid.nearest_cell(x, y, 'medium'), (col, row))

    def test_tiny_screen_still_has_a_usable_grid(self):
        tiny = Grid(300, 300, settings.DEFAULTS)
        self.assertGreaterEqual(tiny.cols, 2)
        self.assertGreaterEqual(tiny.rows, 2)


class MonitorTests(unittest.TestCase):
    LEFT = monitors.Monitor('DP-1', 0, 0, 1920, 1080, False)
    RIGHT = monitors.Monitor('HDMI-1', 1920, 0, 1280, 720, True)
    ABOVE = monitors.Monitor('DP-2', 0, -1080, 1920, 1080, False)

    def test_primary_first_then_left_to_right_top_to_bottom(self):
        self.assertEqual([m.id for m in monitors.ordered([self.LEFT, self.ABOVE, self.RIGHT])], ['HDMI-1', 'DP-2', 'DP-1'])

    def test_select_picks_the_named_connected_monitors(self):
        both = [self.LEFT, self.RIGHT]
        self.assertEqual([m.id for m in monitors.select(both, ['DP-1', 'HDMI-1'])], ['HDMI-1', 'DP-1'])
        self.assertEqual([m.id for m in monitors.select(both, ['DP-1'])], ['DP-1'])

    def test_nothing_usable_selected_means_the_primary_monitor(self):
        both = [self.LEFT, self.RIGHT]
        for wanted in ([], ['DP-9'], None, 'DP-1', 5):
            self.assertEqual([m.id for m in monitors.select(both, wanted)], ['HDMI-1'], wanted)
        self.assertEqual([m.id for m in monitors.select([self.LEFT], [])], ['DP-1'])

    def test_a_point_belongs_to_the_monitor_it_is_on_or_the_nearest(self):
        both = [self.LEFT, self.RIGHT]
        self.assertEqual(monitors.at_point(both, 100, 100).id, 'DP-1')
        self.assertEqual(monitors.at_point(both, 2000, 100).id, 'HDMI-1')
        self.assertEqual(monitors.at_point(both, 1919, 100).id, 'DP-1')
        self.assertEqual(monitors.at_point(both, 5000, 900).id, 'HDMI-1')   # below and beyond the right one
        self.assertEqual(monitors.at_point(both, -300, -300).id, 'DP-1')

    def test_labels_say_the_size_and_which_is_primary(self):
        self.assertEqual(monitors.label(self.LEFT), 'DP-1 · 1920×1080')
        self.assertIn('primary', monitors.label(self.RIGHT).replace('головний', 'primary'))


class LayoutTests(unittest.TestCase):
    def make(self):
        types = catalog.discover()
        grid = Grid(1920, 1080, settings.DEFAULTS)
        return Layout(grid, types, dict(settings.DEFAULTS))

    def test_first_run_uses_the_default_layout(self):
        with isolated_paths():
            layout = self.make()
            layout.load()
            self.assertTrue(layout.instances)
            self.assertEqual(len({i['id'] for i in layout.instances}), len(layout.instances))
            cells = [c for i in layout.instances for c in Grid.cells(i['col'], i['row'], i['size'])]
            self.assertEqual(len(cells), len(set(cells)), 'widgets overlap')

    def test_save_and_load_round_trip(self):
        with isolated_paths():
            layout = self.make()
            layout.load()
            inst = layout.add('clock')
            layout.save()
            again = self.make()
            again.load()
            self.assertEqual(again.get(inst['id'])['type'], 'clock')
            self.assertEqual(json.loads(paths.LAYOUT.read_text())['version'], 3)

    def test_hostile_layout_file_is_sanitised(self):
        with isolated_paths():
            paths.LAYOUT.write_text(json.dumps({'instances': [
                {'id': '../../etc/passwd', 'type': 'clock', 'size': 'huge', 'style': 'nope', 'col': 0, 'row': 0},
                {'id': 'clock', 'type': 'clock', 'col': 0, 'row': 0, 'options': {'seconds': 'maybe', 'evil': 1}},
                {'id': 'ghost', 'type': 'no_such_widget'},
                'not even a dict',
            ]}))
            layout = self.make()
            layout.load()
            ids = [i['id'] for i in layout.instances]
            self.assertEqual(len(ids), 2)
            self.assertNotIn('../../etc/passwd', ids)
            self.assertEqual(len(set(ids)), 2)
            first = layout.instances[0]
            self.assertEqual(first['size'], 'small')
            self.assertIn(first['style'], catalog.STYLE_KEYS)
            self.assertEqual(layout.instances[1]['options'], {})   # invalid values dropped, unknown keys ignored

    def test_overlapping_instances_are_pushed_apart(self):
        with isolated_paths():
            paths.LAYOUT.write_text(json.dumps({'instances': [
                {'type': 'clock', 'col': 0, 'row': 0}, {'type': 'clock', 'col': 0, 'row': 0}]}))
            layout = self.make()
            layout.load()
            a, b = layout.instances
            self.assertNotEqual((a['col'], a['row']), (b['col'], b['row']))

    def test_add_ids_are_unique_and_full_grid_is_reported(self):
        with isolated_paths():
            layout = self.make()
            ids = {layout.add('clock')['id'] for _ in range(4)}
            self.assertEqual(len(ids), 4)
            while layout.add('clock'):
                pass
            self.assertIsNone(layout.add('clock'))
            self.assertIsNone(layout.add('no_such_widget'))

    def test_configure_validates_and_resizes(self):
        with isolated_paths():
            layout = self.make()
            inst = layout.add('clock')
            layout.configure(inst['id'], {'size': 'large', 'style': 'neon', 'accent': 'bogus',
                                          'options': {'seconds': False, 'numbers': 'nope', 'nonsense': 1}})
            self.assertEqual((inst['size'], inst['style']), ('large', 'neon'))
            self.assertNotEqual(inst['accent'], 'bogus')
            self.assertEqual(inst['options'], {'seconds': False})
            self.assertIsNone(layout.configure('missing', {}))
            self.assertIsNone(layout.configure(inst['id'], 'not a dict'))

    def test_move_snaps_to_nearest_free_cell_and_never_overlaps(self):
        with isolated_paths():
            layout = self.make()
            a, b = layout.add('clock'), layout.add('clock')
            spot = layout.move(a['id'], b['col'], b['row'])
            self.assertNotEqual(spot, (b['col'], b['row']))
            cells = Grid.cells(a['col'], a['row'], a['size']) & Grid.cells(b['col'], b['row'], b['size'])
            self.assertFalse(cells)

    def test_arrange_packs_without_overlap(self):
        with isolated_paths():
            layout = self.make()
            for _ in range(5):
                layout.add('clock')
            for inst in layout.instances:
                inst['col'], inst['row'] = 5, 3
            layout.arrange()
            cells = [c for i in layout.instances for c in Grid.cells(i['col'], i['row'], i['size'])]
            self.assertEqual(len(cells), len(set(cells)))

    def test_regrid_keeps_everything_on_a_smaller_screen(self):
        with isolated_paths():
            layout = self.make()
            for _ in range(6):
                layout.add('clock')
            layout.regrid(Grid(700, 500, settings.DEFAULTS))
            for inst in layout.instances:
                self.assertTrue(layout.grid.fits(inst['col'], inst['row'], inst['size']))

    # several monitors, each with its own grid
    def two(self, second=None):
        types = catalog.discover()
        grids = {'DP-1': Grid(1920, 1080, settings.DEFAULTS), 'HDMI-1': second or Grid(1280, 720, settings.DEFAULTS)}
        return Layout(grids, types, dict(settings.DEFAULTS))

    def test_new_widgets_go_to_the_first_monitor_until_it_is_full(self):
        with isolated_paths():
            layout = self.two()
            self.assertEqual(layout.add('clock')['monitor'], 'DP-1')
            while layout.taken('DP-1') != {(c, r) for c in range(layout.grids['DP-1'].cols) for r in range(layout.grids['DP-1'].rows)}:
                if layout.add('clock') is None:
                    break
            self.assertEqual(layout.add('clock')['monitor'], 'HDMI-1')
            self.assertEqual(layout.add('clock', monitor='HDMI-1')['monitor'], 'HDMI-1')
            self.assertEqual(layout.add('clock', monitor='nope')['monitor'], 'HDMI-1')   # an unknown name is ignored

    def test_cells_are_per_monitor(self):
        with isolated_paths():
            layout = self.two()
            a = layout.add('clock')
            b = layout.add('clock', monitor='HDMI-1')
            b['col'], b['row'] = a['col'], a['row']   # the same cell on another monitor is not a clash
            self.assertFalse(layout.taken('HDMI-1', exclude=b['id']))
            self.assertIn((a['col'], a['row']), layout.taken('DP-1'))
            self.assertIn((b['col'], b['row']), layout.taken('HDMI-1'))

    def test_move_between_monitors(self):
        with isolated_paths():
            layout = self.two()
            inst = layout.add('clock')
            spot = layout.move(inst['id'], 3, 2, monitor='HDMI-1')
            self.assertEqual((inst['monitor'], (inst['col'], inst['row'])), ('HDMI-1', spot))
            layout.move(inst['id'], 0, 0)   # no monitor: it stays where it is
            self.assertEqual(inst['monitor'], 'HDMI-1')

    def test_configure_moves_to_another_monitor_and_ignores_unknown_ones(self):
        with isolated_paths():
            layout = self.two()
            inst = layout.add('clock')
            layout.configure(inst['id'], {'monitor': 'HDMI-1'})
            self.assertEqual(inst['monitor'], 'HDMI-1')
            self.assertTrue(layout.grids['HDMI-1'].fits(inst['col'], inst['row'], inst['size']))
            layout.configure(inst['id'], {'monitor': 'DP-9'})
            self.assertEqual(inst['monitor'], 'HDMI-1')

    def test_monitor_is_saved_and_loaded(self):
        with isolated_paths():
            layout = self.two()
            inst = layout.add('clock', monitor='HDMI-1')
            layout.save()
            again = self.two()
            again.load()
            self.assertEqual(again.get(inst['id'])['monitor'], 'HDMI-1')
            self.assertEqual((again.get(inst['id'])['col'], again.get(inst['id'])['row']), (inst['col'], inst['row']))

    def test_a_layout_from_before_monitors_lands_on_the_first_one(self):
        with isolated_paths():
            paths.LAYOUT.write_text(json.dumps({'instances': [{'type': 'clock', 'col': 2, 'row': 1}]}))
            layout = self.two()
            layout.load()
            self.assertEqual((layout.instances[0]['monitor'], layout.instances[0]['col'], layout.instances[0]['row']), ('DP-1', 2, 1))

    def test_a_saved_monitor_that_is_not_there_any_more_means_the_first_one(self):
        with isolated_paths():
            paths.LAYOUT.write_text(json.dumps({'instances': [
                {'type': 'clock', 'monitor': 'GONE-1', 'col': 2, 'row': 1}, {'type': 'clock', 'monitor': 7, 'col': 2, 'row': 1}]}))
            layout = self.two()
            layout.load()
            self.assertEqual([i['monitor'] for i in layout.instances], ['DP-1', 'DP-1'])
            self.assertNotEqual((layout.instances[0]['col'], layout.instances[0]['row']),
                                (layout.instances[1]['col'], layout.instances[1]['row']))

    def test_regrid_moves_widgets_of_a_removed_monitor_to_the_first_one(self):
        with isolated_paths():
            layout = self.two()
            layout.add('clock', monitor='HDMI-1')
            layout.add('clock', monitor='HDMI-1')
            layout.regrid({'DP-1': Grid(1920, 1080, settings.DEFAULTS)})
            self.assertEqual({i['monitor'] for i in layout.instances}, {'DP-1'})
            cells = [c for i in layout.instances for c in Grid.cells(i['col'], i['row'], i['size'])]
            self.assertEqual(len(cells), len(set(cells)))

    def test_arrange_works_on_each_monitor(self):
        with isolated_paths():
            layout = self.two()
            for _ in range(3):
                layout.add('clock')
                layout.add('clock', monitor='HDMI-1')
            for inst in layout.instances:
                inst['col'], inst['row'] = 4, 2
            layout.arrange()
            for monitor in layout.grids:
                cells = [c for i in layout.instances if i['monitor'] == monitor for c in Grid.cells(i['col'], i['row'], i['size'])]
                self.assertEqual(len(cells), len(set(cells)), monitor)
            self.assertEqual({i['monitor'] for i in layout.instances}, {'DP-1', 'HDMI-1'})

    # the widget size changes: the cells change size, the widgets keep their place on the screen
    def test_regrid_after_a_size_change_keeps_places_and_edges(self):
        with isolated_paths():
            layout = self.make()
            layout.load()
            before = {i['id']: (i['col'], i['row']) for i in layout.instances}
            right = [i['id'] for i in layout.instances if i['col'] + 2 >= layout.grid.cols]
            for scale in (0.5, 1.2, 1.0):
                layout.regrid(Grid(1920, 1080, {**settings.DEFAULTS, 'scale': scale}))
                grid = layout.grid
                cells = []
                for inst in layout.instances:
                    self.assertTrue(grid.fits(inst['col'], inst['row'], inst['size']), (scale, inst['id']))
                    cells += list(Grid.cells(inst['col'], inst['row'], inst['size']))
                self.assertEqual(len(cells), len(set(cells)), scale)
                for inst_id in right:
                    self.assertEqual(layout.get(inst_id)['col'] + 2, grid.cols, (scale, inst_id))
            self.assertEqual({i['id']: (i['col'], i['row']) for i in layout.instances}, before)

    def test_regrid_with_the_same_cells_changes_nothing(self):
        with isolated_paths():
            layout = self.make()
            layout.load()
            before = [(i['col'], i['row']) for i in layout.instances]
            layout.regrid(Grid(1920, 1080, settings.DEFAULTS))
            self.assertEqual([(i['col'], i['row']) for i in layout.instances], before)

    def test_unknown_types_stay_in_the_file_after_a_reload(self):
        with isolated_paths():
            layout = self.make()
            inst = layout.add('clock')
            layout.reload_types({})
            self.assertIsNotNone(layout.get(inst['id']))


if __name__ == '__main__':
    unittest.main()
