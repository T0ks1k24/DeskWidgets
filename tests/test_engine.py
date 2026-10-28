import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cairo

from deskwidgets.core import catalog, scaffold, settings
from deskwidgets.engine import compat, fonts, hostapi, images, render
from deskwidgets.engine.runtime import WidgetContext, WidgetRuntime
from deskwidgets.engine.sysinfo import SysInfo

from helpers import IsolatedTestCase, isolated_paths

STYLES = sorted(catalog.STYLE_KEYS)


def coverage(surface):
    """Share of pixels that are not fully transparent."""
    data = surface.get_data()
    alpha = data[3::4]
    return sum(1 for a in alpha if a) / (len(data) // 4)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.types = catalog.discover()
        cls.font_map = fonts.setup(settings.DEFAULTS)

    def render(self, wtype, **kwargs):
        with patch('deskwidgets.engine.hostapi.log') as log:
            surface = render.render_surface(wtype, font_map=self.font_map, lang='en', **kwargs)
        return surface, log

    def test_every_widget_draws_in_every_size_without_lua_errors(self):
        for wid, t in self.types.items():
            for size in t.sizes:
                with self.subTest(widget=wid, size=size):
                    surface, log = self.render(t, size=size)
                    log.assert_not_called()
                    self.assertGreater(coverage(surface), 0.3, 'the card is missing')

    def test_every_style_and_accent_draws(self):
        for style in STYLES:
            for accent in ('auto', 'purple'):
                with self.subTest(style=style, accent=accent):
                    surface, log = self.render(self.types['system'], size='medium', style=style, accent=accent)
                    log.assert_not_called()
                    self.assertGreater(coverage(surface), 0.05)

    def test_live_system_widgets_read_the_real_machine(self):
        for wid in ('cpu', 'memory', 'system', 'disks', 'network', 'battery'):
            for size in self.types[wid].sizes:
                with self.subTest(widget=wid, size=size):
                    surface, log = self.render(self.types[wid], size=size, demo=False, sysinfo=SysInfo(threaded=False))
                    log.assert_not_called()
                    self.assertGreater(coverage(surface), 0.3)

    def test_picture_widget_shows_a_photo_in_any_format(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            for name, fmt in (('red.jpg', 'JPEG'), ('red.png', 'PNG'), ('red.webp', 'WEBP')):
                path = Path(tmp) / name
                Image.new('RGB', (600, 400), (230, 20, 20)).save(path, fmt)
                with self.subTest(format=fmt):
                    surface, log = self.render(self.types['picture'], size='medium', demo=False,
                                               options={'image': str(path)})
                    log.assert_not_called()
                    data = bytes(surface.get_data())
                    reds = sum(1 for i in range(0, len(data), 4) if data[i + 2] > 180 and data[i] < 90)
                    self.assertGreater(reds, 5000, 'the picture was not drawn')

    def test_both_languages(self):
        for lang in ('uk', 'en'):
            with patch('deskwidgets.engine.hostapi.log') as log:
                render.render_surface(self.types['calendar'], font_map=self.font_map, lang=lang, size='large')
            log.assert_not_called()

    def test_scale_changes_the_window_size(self):
        small, _ = self.render(self.types['clock'], size='small', scale=1.0)
        big, _ = self.render(self.types['clock'], size='small', scale=1.5)
        self.assertEqual(small.get_width(), 202)
        self.assertEqual(big.get_width(), 303)

    def test_render_png_writes_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'sub' / 'clock.png'
            self.assertTrue(render.render_png(self.types['clock'], out, font_map=self.font_map))
            self.assertTrue(out.read_bytes().startswith(b'\x89PNG'))

    def test_widget_that_fails_shows_the_error_on_its_card(self):
        with isolated_paths():
            folder = scaffold.create('boom')
            (folder / 'widget.lua').write_text('local L = require "lib"\nerror("kaboom")\nreturn {}')
            t = catalog.discover()['boom']
            with patch('deskwidgets.engine.hostapi.log') as log:
                surface = render.render_surface(t, font_map=self.font_map, lang='en')
            self.assertGreater(coverage(surface), 0.3)
            self.assertIn('kaboom', log.call_args[0][0])

    def test_error_inside_draw_is_reported_once(self):
        with isolated_paths():
            folder = scaffold.create('flaky')
            (folder / 'widget.lua').write_text(
                'local L = require "lib"\nreturn { draw = function() error("bad frame") end }')
            t = catalog.discover()['flaky']
            with patch('deskwidgets.engine.hostapi.log') as log:
                render.render_surface(t, font_map=self.font_map, lang='en', frames=5)
            self.assertEqual(log.call_count, 1)

    def test_scaffolded_widget_renders(self):
        with isolated_paths():
            scaffold.create('my_widget', name='Фото "Львів"\nIt\'s mine\\test')
            t = catalog.discover()['my_widget']
            surface, log = self.render(t, size='medium')
            log.assert_not_called()
            self.assertGreater(coverage(surface), 0.3)

    def test_invalid_utf8_text_does_not_break_a_frame(self):
        with isolated_paths():
            folder = scaffold.create('bytes')
            (folder / 'widget.lua').write_text(
                'local L = require "lib"\nreturn { draw = function(cr, x, y, w, h)\n'
                '  L.card(cr, x, y, w, h); L.font(cr, L.F.regular, 12)\n'
                '  L.text(cr, "ok \\255\\254 bad", x + 10, y + 30) end }')
            t = catalog.discover()['bytes']
            with patch('deskwidgets.engine.hostapi.log') as log:
                render.render_surface(t, font_map=self.font_map, lang='en')
            log.assert_not_called()


class RuntimeTests(IsolatedTestCase):
    count = 0

    def runtime(self, lua, demo=True):
        RuntimeTests.count += 1
        name = f'probe{RuntimeTests.count}'
        folder = scaffold.create(name)
        (folder / 'widget.lua').write_text(lua)
        t = catalog.discover()[name]
        inst = {'id': name, 'type': name, 'size': 'small', 'style': 'glass', 'accent': 'auto',
                'options': {'greeting': 'Привіт'}}
        ctx = WidgetContext(inst, t, lang='en', scale=1.0, size_px=(202, 202), fonts=fonts.FontMap(),
                            sysinfo=SysInfo(threaded=False), demo=demo)
        return WidgetRuntime(ctx)

    def test_click_reaches_the_widget_in_card_coordinates(self):
        rt = self.runtime('local L = require "lib"\nlocal W = {}\nfunction W.draw() end\n'
                          'function W.click(x, y, w, h) clicked = {x, y, w, h}; return true end\nreturn W')
        self.assertTrue(rt.click(16 + 10, 16 + 20))
        self.assertEqual(list(rt.lua.globals().clicked.values()), [10, 20, 170, 170])

    def test_click_on_a_widget_without_handler_or_with_a_failing_one(self):
        rt = self.runtime('return { draw = function() end }')
        self.assertFalse(rt.click(50, 50))
        rt = self.runtime('return { draw = function() end, click = function() error("no") end }')
        with patch('deskwidgets.engine.hostapi.log'):
            self.assertFalse(rt.click(50, 50))

    def test_options_reach_lua_as_strings(self):
        rt = self.runtime('local L = require "lib"\nseen = L.opt("greeting")\nreturn { draw = function() end }')
        self.assertEqual(rt.lua.globals().seen, 'Привіт')

    def test_host_services_are_empty_without_providers(self):
        rt = self.runtime('local L = require "lib"\nmedia = L.service("media")\n'
                          'ok = L.media_control("PlayPause")\nreturn { draw = function() end }')
        self.assertIsNone(rt.lua.globals().media)
        self.assertFalse(rt.lua.globals().ok)

    def test_sys_tables_have_lua_arrays(self):
        rt = self.runtime('local L = require "lib"\nlocal c = L.sys.cpu()\ncores = #c.cores\ncount = c.count\n'
                          'top = #L.sys.top(3)\nreturn { draw = function() end }', demo=False)
        g = rt.lua.globals()
        self.assertEqual(g.cores, g.count)
        self.assertLessEqual(g.top, 3)

    def test_time_zone_offsets(self):
        self.assertEqual(hostapi.tz_offset('UTC'), 0)
        self.assertEqual(hostapi.tz_offset('Not/AZone'), 0)
        self.assertIn(hostapi.tz_offset('Asia/Kolkata'), (19800,))


class CairoShimTests(IsolatedTestCase):
    def test_generic_and_special_functions(self):
        folder = scaffold.create('probe')
        (folder / 'widget.lua').write_text('return { draw = function() end }')
        inst = {'id': 'probe', 'type': 'probe', 'size': 'small', 'style': 'glass', 'accent': 'auto', 'options': {}}
        ctx = WidgetContext(inst, catalog.discover()['probe'], lang='en', scale=1.0, size_px=(202, 202),
                            fonts=fonts.FontMap(), sysinfo=SysInfo(threaded=False), demo=True)
        lua = WidgetRuntime(ctx).lua
        lua.execute('''
            local s = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 20, 20)
            local cr = cairo_create(s)
            cairo_set_source_rgba(cr, 1, 0, 0, 1)
            cairo_rectangle(cr, 0, 0, 20, 20)
            cairo_fill(cr)
            cairo_select_font_face(cr, "DW Bold", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL)
            cairo_set_font_size(cr, 10)
            local e = cairo_text_extents_t:create()
            cairo_text_extents(cr, "Привіт", e)
            width = e.x_advance
            missing = cairo_image_surface_create_from_png("/does/not/exist.png")
            missing_w = cairo_image_surface_get_width(missing)
            local g = cairo_pattern_create_linear(0, 0, 10, 0)
            cairo_pattern_add_color_stop_rgba(g, 0, 1, 1, 1, 1)
            cairo_pattern_destroy(g)
        ''')
        g = lua.globals()
        self.assertGreater(g.width, 10)
        self.assertEqual(g.missing_w, 0)


class SysInfoTests(unittest.TestCase):
    def test_snapshots_have_the_documented_shape(self):
        info = SysInfo(threaded=False)
        cpu = info.cpu()
        self.assertEqual(len(cpu['cores']), cpu['count'])
        memory = info.memory()
        self.assertGreater(memory['total'], 0)
        self.assertLessEqual(memory['used'], memory['total'])
        for disk in info.disks():
            self.assertLessEqual(disk['used'], disk['total'])
        self.assertIn('iface', info.net())
        self.assertGreaterEqual(info.uptime(), 0)
        self.assertEqual(len(info.top(2)), min(2, len(info.top(50))))

    def test_a_failing_sensor_does_not_raise(self):
        info = SysInfo(threaded=False)
        with patch('psutil.virtual_memory', side_effect=OSError('sensor gone')), patch('deskwidgets.engine.sysinfo.log'):
            self.assertEqual(info.memory()['used'], 0)

    def test_background_thread_refreshes_what_was_asked(self):
        info = SysInfo(threaded=True)
        try:
            first = info.cpu()
            self.assertIsNotNone(first)
            self.assertIsNotNone(info._thread)
        finally:
            info.stop()


class ConkyCompatTests(unittest.TestCase):
    def test_common_variables(self):
        info = Mock()
        info.cpu.return_value = {'total': 42, 'cores': [10, 20], 'processes': 300, 'load1': 0.5, 'load5': 0.4,
                                 'load15': 0.3, 'freq_ghz': 3.2}
        info.memory.return_value = {'total': 2 ** 30, 'used': 2 ** 29, 'swap_total': 0, 'swap_used': 0}
        info.top.return_value = [{'name': 'firefox', 'cpu': 12.5, 'mem': 300 * 2 ** 20}]
        info.net.return_value = {'down': 1.5, 'up': 0.5, 'ip': '10.0.0.2', 'kind': 'wifi', 'name': 'Home',
                                 'total_down': 0, 'total_up': 0}
        parse = lambda s: compat.conky_parse(s, info)
        self.assertEqual(parse('${cpu cpu0}%'), '42%')
        self.assertEqual(parse('${cpu cpu2}'), '20')
        self.assertEqual(parse('${memperc}'), '50')
        self.assertEqual(parse('${top name 1} ${top cpu 1} ${top mem_res 1}'), 'firefox 12.5 300MiB')
        self.assertEqual(parse('${downspeedf eth0} ${addr eth0} ${wireless_essid wlan0}'), '1.5 10.0.0.2 Home')
        self.assertEqual(parse('${loadavg 5} ${nonsense}'), '0.4 ')


class ImageTests(unittest.TestCase):
    def photo(self, directory, name, size, fmt):
        from PIL import Image
        path = Path(directory) / name
        Image.new('RGB', size, (255, 0, 0)).save(path, fmt)
        return str(path)

    def test_photos_are_downsampled_and_small_images_are_not_enlarged(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = images.load_surface(self.photo(tmp, 'big.jpg', (2400, 1200), 'JPEG'))
            self.assertEqual((big.get_width(), big.get_height()), (768, 384))
            small = images.load_surface(self.photo(tmp, 'small.png', (20, 10), 'PNG'))
            self.assertEqual((small.get_width(), small.get_height()), (20, 10))

    def test_pixels_are_premultiplied_bgra(self):
        with tempfile.TemporaryDirectory() as tmp:
            surface = images.load_surface(self.photo(tmp, 'red.png', (4, 4), 'PNG'))
            self.assertEqual(bytes(surface.get_data()[:4]), bytes([0, 0, 255, 255]))   # B, G, R, A

    def test_cache_reloads_changed_files_and_is_bounded(self):
        cache = images.ImageCache()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.photo(tmp, 'a.png', (8, 8), 'PNG')
            first = cache.load(path)
            self.assertIs(cache.load(path), first)
            self.photo(tmp, 'a.png', (16, 16), 'PNG')
            self.assertEqual(cache.load(path).get_width(), 16)
            for i in range(20):
                cache.load(self.photo(tmp, f'{i}.png', (8, 8), 'PNG'))
            self.assertLessEqual(len(cache._items), images.CACHE_ITEMS)

    def test_missing_and_broken_files_give_none(self):
        cache = images.ImageCache()
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / 'broken.png'
            broken.write_bytes(b'not an image')
            with patch('deskwidgets.engine.images.log'):
                self.assertIsNone(cache.load(broken))
            self.assertIsNone(cache.load(Path(tmp) / 'missing.png'))


class FontTests(unittest.TestCase):
    def test_fontconfig_file_defines_every_weight_and_escapes_the_family(self):
        with isolated_paths() as root:
            path = fonts.write_fontconf('A & B <font>', root / 'fonts.conf')
            text = path.read_text()
            for alias in fonts.WEIGHTS:
                self.assertIn(f'<string>DW {alias}</string>', text)
            self.assertIn('A &amp; B &lt;font&gt;', text)
            self.assertIn(str(fonts.paths.FONTS), text)

    def test_windows_face_mapping(self):
        windows = fonts.FontMap('auto', windows=True)
        self.assertEqual(windows.face('DW Light', 0), ('Segoe UI Light', cairo.FONT_WEIGHT_NORMAL))
        self.assertEqual(windows.face('DW Bold', 0)[1], cairo.FONT_WEIGHT_BOLD)
        self.assertEqual(windows.face('Material Design Icons', 0), ('Material Design Icons', 0))
        custom = fonts.FontMap('Inter', windows=True)
        self.assertEqual(custom.face('DW Regular', 0)[0], 'Inter')
        linux = fonts.FontMap('auto', windows=False)
        self.assertEqual(linux.face('DW Light', 0), ('DW Light', 0))


class HelperTests(unittest.TestCase):
    @unittest.skipIf(__import__('sys').platform == 'win32', 'shell quoting is checked with a POSIX shell')
    def test_quote_round_trips_awkward_text(self):
        import subprocess
        for text in ("it's", 'a b', '"quoted"', 'ключ', '$HOME', 'a;b'):
            out = subprocess.run(f'printf %s {hostapi.quote(text)}', shell=True, capture_output=True, text=True).stdout
            self.assertEqual(out, text)


if __name__ == '__main__':
    unittest.main()
