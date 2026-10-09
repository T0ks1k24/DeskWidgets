#!/usr/bin/python3
"""Regenerates docs/screenshots/*.png from real widget renders (sample data).
Run from the repository: `make screenshots` (needs conky-all and pycairo)."""
import math
import os
import sys
import tempfile

import cairo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from deskwidgets import catalog, engine, paths, settings  # noqa: E402

OUT = os.path.join(ROOT, 'docs', 'screenshots')
TMP = tempfile.mkdtemp(prefix='deskwidgets-shots-')
PITCH = 186
types = catalog.discover()


def renders(jobs, lang):
    """jobs: {name: (type, size, style, accent, options)}, returns {name: png path}"""
    procs = {}
    for name, (t, size, style, accent, opts) in jobs.items():
        out = os.path.join(TMP, f'{lang}-{name}.png')
        procs[name] = (out, engine.render(types[t], out, size=size, style=style, accent=accent, lang=lang,
                                          options=opts, wait=False))
    for out, proc in procs.values():
        proc.wait(timeout=40)
    return {name: out for name, (out, _p) in procs.items()}


def wallpaper(w, h):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, w, h)
    for stop, c in ((0, (0.80, 0.78, 0.98)), (0.45, (0.98, 0.80, 0.90)), (1, (0.70, 0.86, 0.98))):
        g.add_color_stop_rgb(stop, *c)
    cr.set_source(g)
    cr.paint()
    for cx, cy, r, c in ((0.2, 0.25, 0.35, (0.55, 0.45, 0.95)), (0.85, 0.7, 0.4, (1.0, 0.55, 0.75)),
                         (0.55, 0.95, 0.3, (0.45, 0.75, 1.0))):
        rg = cairo.RadialGradient(cx * w, cy * h, 0, cx * w, cy * h, r * w)
        rg.add_color_stop_rgba(0, *c, 0.45)
        rg.add_color_stop_rgba(1, *c, 0)
        cr.set_source(rg)
        cr.paint()
    return s, cr


def place(cr, png, x, y):
    img = cairo.ImageSurface.create_from_png(png)
    cr.set_source_surface(img, x, y)
    cr.paint()


def label(cr, text, x, y, size=15, color=(0.15, 0.12, 0.2)):
    cr.select_font_face('Ubuntu Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.set_source_rgb(*color)
    cr.move_to(x - ext.x_advance / 2, y)
    cr.show_text(text)


def at(col, row):
    return 40 + col * PITCH - 16, 40 + row * PITCH - 16


def hero(lang):
    jobs = {
        'clock': ('clock', 'small', 'glass', 'auto', {}), 'calendar': ('calendar', 'medium', 'glass', 'auto', {}),
        'weather': ('weather', 'large', 'glass', 'auto', {'city': 'Kyiv', 'title': 'Київ' if lang == 'uk' else 'Kyiv'}),
        'system': ('system', 'medium', 'graphite', 'auto', {}), 'media': ('media', 'medium', 'glass', 'auto', {}),
        'events': ('events', 'medium', 'light', 'auto', {}), 'anime': ('anime_schedule', 'medium', 'sakura', 'auto', {}),
        'note': ('note', 'small', 'color', 'yellow', {}), 'countdown': ('countdown', 'small', 'glass', 'purple', {}),
        'manga': ('manga_clock', 'small', 'manga', 'auto', {}), 'battery': ('battery', 'small', 'glass', 'auto', {}),
        'world': ('worldclock', 'medium', 'glass', 'auto', {}),
    }
    r = renders(jobs, lang)
    s, cr = wallpaper(40 * 2 + 8 * PITCH - 16, 40 * 2 + 4 * PITCH - 16)
    layout = {'clock': (0, 0), 'calendar': (1, 0), 'weather': (0, 1), 'system': (2, 1), 'media': (2, 2),
              'note': (2, 3), 'countdown': (3, 3), 'battery': (3, 0), 'events': (4, 0), 'anime': (4, 1),
              'manga': (6, 0), 'world': (4, 2)}
    for name, (c, row) in layout.items():
        place(cr, r[name], *at(c, row))
    s.write_to_png(os.path.join(OUT, f'hero-{lang}.png'))


def styles_sheet(lang):
    keys = [k for k, _n in catalog.styles()]
    names = dict(catalog.styles())
    jobs = {k: ('calendar', 'small', k, 'auto', {}) for k in keys}
    jobs.update({k + '-sys': ('system', 'medium', k, 'auto', {}) for k in keys})
    r = renders(jobs, lang)
    w, h = 3 * (202 + 388 + 40) + 40, 3 * (202 + 40) + 40
    s, cr = wallpaper(w, h)
    for i, k in enumerate(keys):
        col, row = i % 3, i // 3
        x, y = 30 + col * (202 + 388 + 40), 20 + row * 242
        place(cr, r[k], x, y)
        place(cr, r[k + '-sys'], x + 202, y)
        label(cr, names[k], x + (202 + 388) / 2, y + 226, 14)
    s.write_to_png(os.path.join(OUT, f'styles-{lang}.png'))


def accents_sheet(lang):
    keys = [k for k, _c in catalog.ACCENTS]
    r = renders({k: ('clock', 'small', 'glass', k, {}) for k in keys}, lang)
    cols = 7
    s, cr = wallpaper(cols * 190 + 40, math.ceil(len(keys) / cols) * 230 + 20)
    for i, k in enumerate(keys):
        x, y = 20 + (i % cols) * 190, 10 + (i // cols) * 230
        place(cr, r[k], x - 6, y)
        label(cr, catalog.accent_name(k), x + 95, y + 218, 13)
    s.write_to_png(os.path.join(OUT, f'accents-{lang}.png'))


def anime_sheet(lang):
    jobs = {
        'schedule': ('anime_schedule', 'large', 'sakura', 'auto', {}), 'buddy': ('anime_quote', 'medium', 'sakura', 'auto', {}),
        'manga': ('manga_clock', 'medium', 'manga', 'auto', {}), 'neon': ('digital', 'medium', 'neon', 'auto', {}),
        'neon2': ('anime_schedule', 'medium', 'neon', 'auto', {}),
    }
    r = renders(jobs, lang)
    s, cr = wallpaper(40 + 388 + 388 + 388 + 30, 40 + 388 + 10)
    place(cr, r['schedule'], 20, 20)
    place(cr, r['buddy'], 20 + 388, 20)
    place(cr, r['manga'], 20 + 388, 20 + 186)
    place(cr, r['neon'], 20 + 2 * 388, 20)
    place(cr, r['neon2'], 20 + 2 * 388, 20 + 186)
    s.write_to_png(os.path.join(OUT, f'anime-{lang}.png'))


def sizes_sheet(lang):
    r = renders({s: ('weather', s, 'glass', 'auto', {'city': 'Kyiv'}) for s in ('small', 'medium', 'large')}, lang)
    s, cr = wallpaper(40 + 202 + 388 + 388 + 20, 40 + 388)
    place(cr, r['small'], 20, 20)
    place(cr, r['medium'], 20 + 202, 20)
    place(cr, r['large'], 20 + 202 + 388, 20)
    s.write_to_png(os.path.join(OUT, f'sizes-{lang}.png'))


def main():
    os.makedirs(OUT, exist_ok=True)
    paths.ensure_dirs()
    engine.write_fontconf(settings.load())
    for lang in ('en', 'uk'):
        hero(lang)
        styles_sheet(lang)
        accents_sheet(lang)
        anime_sheet(lang)
        sizes_sheet(lang)
    print(OUT)


if __name__ == '__main__':
    main()
