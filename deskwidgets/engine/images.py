"""Pictures for widgets: any format Pillow reads (JPEG, PNG, WebP ...) -> a Cairo surface.

Photos are shrunk while they are decoded, and the last few surfaces are kept, so a 24 MP photo
neither stalls a frame nor stays in memory at full size.
"""
import io
import time
from collections import OrderedDict
from pathlib import Path

import cairo

from ..core.util import log

MAX_SIDE = 768            # longest side kept; widgets draw pictures at most ~360 px
CACHE_ITEMS = 8
CACHE_BYTES = 32 * 1024 * 1024
RETRY_AFTER = 2.0         # seconds before a file that failed to load is tried again


class ImageCache:
    def __init__(self):
        self._items = OrderedDict()   # path -> (mtime, size, surface or None, loaded_at)
        self._bytes = 0

    def load(self, path):
        """A surface for `path`, or None (missing, still downloading, not an image)."""
        path = str(path)
        try:
            st = Path(path).stat()
        except OSError:
            self._forget(path)
            return None
        cached = self._items.get(path)
        if cached:
            mtime, size, surface, at = cached
            fresh = (mtime, size) == (st.st_mtime_ns, st.st_size)
            if fresh and (surface is not None or time.monotonic() - at < RETRY_AFTER):
                self._items.move_to_end(path)
                return surface
        surface = load_surface(path)
        self._forget(path)
        self._items[path] = (st.st_mtime_ns, st.st_size, surface, time.monotonic())
        if surface is not None:
            self._bytes += surface.get_width() * surface.get_height() * 4
        self._trim()
        return surface

    def _forget(self, path):
        old = self._items.pop(path, None)
        if old and old[2] is not None:
            self._bytes -= old[2].get_width() * old[2].get_height() * 4

    def _trim(self):
        while len(self._items) > 1 and (len(self._items) > CACHE_ITEMS or self._bytes > CACHE_BYTES):
            self._forget(next(iter(self._items)))


def load_surface(path, max_side=MAX_SIDE):
    """Decodes `path` into an ARGB32 surface no larger than `max_side`; None if that fails."""
    try:
        return _decode(path, max_side)
    except Exception as e:   # unreadable, truncated or unsupported
        log(f'image {path}: {e}')
        return None


def _decode(path, max_side):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return cairo.ImageSurface.create_from_png(path)   # PNG only, but never a crash
    with Image.open(path) as img:
        img.draft('RGB', (max_side, max_side))   # JPEG: decode at a reduced size
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        # Cairo wants premultiplied BGRA (little-endian ARGB32)
        data = bytearray(img.convert('RGBA').convert('RGBa').tobytes('raw', 'BGRa'))
        width, height = img.size
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, width, height, width * 4)


def encode_png(surface):
    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    return buffer.getvalue()
