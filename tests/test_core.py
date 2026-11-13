import concurrent.futures
import json
import unittest
from pathlib import Path

from deskwidgets.core import catalog, paths, settings
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
            self.assertEqual(loaded['scale'], 2.0)
            self.assertEqual(loaded['margin_top'], 0)
            self.assertIs(loaded['lock'], settings.DEFAULTS['lock'])
            self.assertNotIn('unknown', loaded)

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

    def test_unknown_types_stay_in_the_file_after_a_reload(self):
        with isolated_paths():
            layout = self.make()
            inst = layout.add('clock')
            layout.reload_types({})
            self.assertIsNotNone(layout.get(inst['id']))


if __name__ == '__main__':
    unittest.main()
