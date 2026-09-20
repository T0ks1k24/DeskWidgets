"""Battery of wireless mice that report it only over a vendor HID protocol.

Many 2.4 GHz gaming mice built on Compx/"CX" chips (Pulsar, Kysona, ATK, VXE and others) share
one protocol, documented by the Linux hid-pulsar and hid-kysona drivers. Where no kernel driver
exposes the battery yet, DeskWidgets asks the mouse itself through /dev/hidraw*:

  host -> device  output report 0x08, 17 bytes: [0x08, cmd, 14 zero bytes, checksum]
  device -> host  input report 0x08, 17 bytes, byte 1 echoes cmd
  checksum        (0x55 - sum of bytes 0..15) & 0xff
  cmd 0x03 STATUS byte 6 = 1 when the mouse is awake and linked to its receiver
  cmd 0x04 POWER  byte 6 = charge %, byte 7 = 1 while the cable is plugged in, bytes 8-9 = mV

Both commands are read-only. The hidraw node needs a udev rule (data/udev/70-deskwidgets-hid.rules)
that gives the logged-in user access to the receiver's vendor interface only.
"""
import os
import re
import select
import time

VENDORS = {0x379a: 'Compx', 0x3554: 'CX', 0x373b: 'CX'}
REPORT_ID = 0x08
LENGTH = 17
CMD_STATUS, CMD_POWER = 0x03, 0x04
SYSFS = '/sys/class/hidraw'


def _uevent(dev):
    try:
        with open(os.path.join(dev, 'uevent'), encoding='utf-8', errors='replace') as f:
            return dict(line.rstrip('\n').split('=', 1) for line in f if '=' in line)
    except OSError:
        return {}


def _speaks_protocol(descriptor):
    """Report 0x08 with 16 data bytes as both input and output (the command channel)."""
    return b'\x85\x08' in descriptor and b'\x95\x10' in descriptor and b'\x91' in descriptor


def pretty_name(hid_name):
    name = re.sub(r'\s+', ' ', hid_name).strip()
    for vendor in ('Compx ', 'CX ', 'I-CHIP '):
        if name.startswith(vendor):
            name = name[len(vendor):]
    return re.sub(r'\s+(Wireless|Receiver|Dongle)$', '', name) or hid_name


def find():
    """Receivers that speak the protocol and are not handled by a kernel driver."""
    found = []
    if not os.path.isdir(SYSFS):
        return found
    for node in sorted(os.listdir(SYSFS)):
        dev = os.path.realpath(os.path.join(SYSFS, node, 'device'))
        m = re.match(r'[0-9A-Fa-f]{4}:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\.', os.path.basename(dev))
        if not m or int(m.group(1), 16) not in VENDORS:
            continue
        if os.path.isdir(os.path.join(dev, 'power_supply')):   # a kernel driver reports it already
            continue
        try:
            with open(os.path.join(dev, 'report_descriptor'), 'rb') as f:
                if not _speaks_protocol(f.read()):
                    continue
        except OSError:
            continue
        name = _uevent(dev).get('HID_NAME', 'Wireless mouse')
        found.append({'node': '/dev/' + node, 'name': pretty_name(name), 'id': f'{m.group(1)}:{m.group(2)}'.lower(),
                      'access': os.access('/dev/' + node, os.R_OK | os.W_OK)})
    return found


def _packet(cmd):
    pkt = bytearray(LENGTH)
    pkt[0], pkt[1] = REPORT_ID, cmd
    pkt[-1] = (0x55 - sum(pkt[:16])) & 0xff
    return bytes(pkt)


def _request(fd, cmd, timeout=1.0):
    os.write(fd, _packet(cmd))
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            break
        data = os.read(fd, 64)
        if len(data) >= 10 and data[0] == REPORT_ID and data[1] == cmd:
            return data
    return None


def read(device):
    """{'online': bool, 'pct': int|None, 'charging': bool, 'mv': int} or None when unreadable."""
    try:
        fd = os.open(device['node'], os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        status = _request(fd, CMD_STATUS)
        if status is None or status[6] > 1:
            return None
        if status[6] != 1:
            return {'online': False, 'pct': None, 'charging': False, 'mv': 0}
        power = _request(fd, CMD_POWER)
        if power is None or power[6] > 100 or power[7] > 1:
            return None
        return {'online': True, 'pct': power[6], 'charging': power[7] == 1, 'mv': (power[8] << 8) | power[9]}
    except OSError:
        return None
    finally:
        os.close(fd)


def main():
    """`deskwidgets mice`: what DeskWidgets can read right now."""
    from .i18n import tr
    devices = find()
    if not devices:
        print(tr('Підтримуваних приймачів бездротових мишей не знайдено', 'No supported wireless mouse receivers found'))
        return 1
    for d in devices:
        head = f"{d['name']} ({d['id']}, {d['node']})"
        if not d['access']:
            print(f"{head}: " + tr('немає доступу — потрібне правило udev', 'no access — the udev rule is needed'))
            continue
        r = read(d)
        if r is None:
            print(f"{head}: " + tr('не відповідає', 'no answer'))
        elif not r['online']:
            print(f"{head}: " + tr('спить або не зв’язана з приймачем', 'asleep or not linked'))
        else:
            charging = tr(', заряджається', ', charging') if r['charging'] else ''
            print(f"{head}: {r['pct']}%{charging}, {r['mv']} mV")
    return 0
