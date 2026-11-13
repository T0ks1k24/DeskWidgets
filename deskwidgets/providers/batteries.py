"""Batteries of things that are not the laptop: Bluetooth mice and headphones (UPower) and
wireless gaming mice (their vendor HID protocol, see hid_mice.py). Linux only; elsewhere the
list is simply empty."""
import re
import sys
import time

from ..core.util import clean
from . import hid_mice
from .base import PollingProvider, run_command

HID_SLEEP_GRACE = 1800   # a sleeping mouse keeps showing its last charge for this long (seconds)
_KIND = re.compile(r'^  ([a-z][\w-]*)\s*$', re.M)


def read_upower():
    """Devices UPower knows that are not power supplies: [{name, percent, charging, online, kind}]."""
    code, output = run_command(['upower', '-d'], timeout=10)
    if code != 0:
        return []
    devices = []
    for block in re.split(r'^Device:', output, flags=re.M)[1:]:
        path = block.split('\n', 1)[0].strip()
        supply = re.search(r'power supply:\s*(\w+)', block)
        percent = re.search(r'percentage:\s*([\d.]+)%', block)
        if not percent or (supply and supply.group(1) != 'no') or 'DisplayDevice' in path:
            continue
        kind = _KIND.search(block)
        model = re.search(r'model:\s*(.*)', block)
        state = re.search(r'state:\s*([\w-]+)', block)
        devices.append({'name': clean(model.group(1) if model else '') or (kind.group(1) if kind else ''),
                        'percent': float(percent.group(1)), 'charging': bool(state and state.group(1) == 'charging'),
                        'online': True, 'kind': kind.group(1) if kind else ''})
    return devices


class Batteries(PollingProvider):
    name = 'batteries'
    every = 30.0

    def __init__(self):
        super().__init__()
        self._last_hid = {}   # node -> (reading, time)

    def poll(self):
        if sys.platform == 'win32':
            self.publish({'devices': [], 'denied': ''})
            return
        devices, denied = [], []
        for dev in hid_mice.find():
            if not dev['access']:
                denied.append(dev['name'])
                continue
            reading = hid_mice.read(dev)
            now = time.time()
            if reading and reading['online']:
                self._last_hid[dev['node']] = (reading, now)
            elif dev['node'] in self._last_hid and now - self._last_hid[dev['node']][1] < HID_SLEEP_GRACE:
                reading = dict(self._last_hid[dev['node']][0], online=False)
            if reading is not None:
                devices.append({'name': clean(dev['name']), 'percent': reading['pct'], 'charging': reading['charging'],
                                'online': reading['online'], 'kind': 'mouse'})
        self.publish({'devices': devices + read_upower(), 'denied': ', '.join(denied)})
