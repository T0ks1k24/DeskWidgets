"""Monitors, and which of them show widgets.

A backend (`platform/`) reports every connected monitor as a `Monitor`. `settings.json` names the ones
that should carry widgets (`"monitors": ["DP-1", "HDMI-1"]`); an empty list means the primary monitor only,
which is what DeskWidgets did before it knew about more than one.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    """One display. `id` is a stable name (the RandR output name on Linux, the device name such as
    `\\\\.\\DISPLAY2` on Windows) that settings.json can refer to. x, y, width, height are the usable area
    in virtual-screen pixels (the top-left of the primary monitor is not always 0, 0). `scale` is the DPI
    scale of this monitor: 1.0 at 96 dpi."""
    id: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    scale: float = 1.0

    @property
    def rect(self):
        return self.x, self.y, self.width, self.height

    def distance_to(self, px, py):
        """0 when the point is on this monitor, else how far it is (pixels) from the nearest edge."""
        dx = max(self.x - px, 0, px - (self.x + self.width - 1))
        dy = max(self.y - py, 0, py - (self.y + self.height - 1))
        return (dx * dx + dy * dy) ** 0.5


def ordered(monitors):
    """Primary first, then left to right, top to bottom."""
    return sorted(monitors, key=lambda m: (not m.primary, m.x, m.y, m.id))


def select(connected, wanted):
    """The monitors that carry widgets: the connected ones named in `wanted`, in `ordered` order. Nothing
    named (or none of them plugged in) means the primary monitor, so the widgets never vanish from the screen."""
    connected = ordered(connected)
    names = set(wanted) if isinstance(wanted, (list, tuple, set)) else set()
    return [m for m in connected if m.id in names] or connected[:1]


def at_point(monitors, px, py):
    """The monitor a point is on, or the nearest one."""
    return min(monitors, key=lambda m: m.distance_to(px, py))


def label(monitor):
    """"DP-1 · 2560×1440 · primary" for lists in the gallery."""
    text = f'{monitor.id} · {monitor.width}×{monitor.height}'
    return text + (' · ' + _primary() if monitor.primary else '')


def _primary():
    from .i18n import tr
    return tr('головний', 'primary')
