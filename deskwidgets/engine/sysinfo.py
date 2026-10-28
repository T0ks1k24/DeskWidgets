"""System numbers for the widgets (CPU, memory, disks, network, battery ...), the same on every OS.

Widgets call these from `tick()`/`draw()`, so every getter is cheap: it returns the last snapshot,
and a background thread refreshes only the parts that widgets asked for recently.
"""
import ctypes
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from ..core.util import log

# seconds between refreshes of each part
EVERY = {'cpu': 1.0, 'top': 2.0, 'memory': 2.0, 'disks': 30.0, 'net': 1.0, 'battery': 10.0, 'temps': 5.0}
IDLE_AFTER = 20.0   # stop refreshing a part nobody asked for in this many seconds

# file systems that hold the user's data (the Disks widget skips pseudo and removable ones)
REAL_FS = {'ext2', 'ext3', 'ext4', 'btrfs', 'xfs', 'f2fs', 'ntfs', 'ntfs3', 'fuseblk', 'exfat', 'zfs', 'apfs', 'hfs',
           'refs', 'fat32', 'jfs', 'reiserfs'}
SKIP_MOUNTS = ('/snap', '/boot', '/var/lib/docker', '/var/snap')


def system_root():
    """The system disk: '/' on Linux, the Windows drive (usually C:\\)."""
    return Path(os.environ.get('SystemDrive', 'C:') + '\\') if sys.platform == 'win32' else Path('/')


class SysInfo:
    def __init__(self, threaded=True):
        self.threaded = threaded
        self._data = {}
        self._stamp = {}      # part -> time of the last refresh
        self._asked = {}      # part -> time a widget last asked for it
        self._failed = set()
        self._thread = None
        self._stop = threading.Event()
        self._net_prev = None
        self._wifi_name = ('', 0.0)
        self._lock = threading.Lock()

    # lifecycle
    def stop(self):
        self._stop.set()

    def _get(self, part):
        now = time.monotonic()
        self._asked[part] = now
        if self.threaded and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='sysinfo', daemon=True)
            self._thread.start()
        # the background thread keeps it fresh; without one (previews, tests) refresh on demand
        if part not in self._data or (not self.threaded and now - self._stamp[part] >= EVERY[part]):
            self._refresh(part)
        return self._data.get(part)

    def _loop(self):
        while not self._stop.wait(0.5):
            now = time.monotonic()
            for part, asked in list(self._asked.items()):
                if now - asked < IDLE_AFTER and now - self._stamp.get(part, 0) >= EVERY[part]:
                    self._refresh(part)

    def _refresh(self, part):
        try:
            with self._lock:
                self._data[part] = getattr(self, '_read_' + part)()
        except Exception as e:   # a sensor that misbehaves must not take the widgets down
            if part not in self._failed:
                self._failed.add(part)
                log(f'system info "{part}": {e}')
            self._data.setdefault(part, getattr(self, '_empty_' + part, lambda: None)())
        self._stamp[part] = time.monotonic()

    # what widgets call
    def cpu(self):
        """{total, cores: [..], count, load1, load5, load15, processes, freq_ghz}; percentages 0-100."""
        return self._get('cpu')

    def top(self, n=5):
        """The busiest processes: [{name, cpu, mem}] (cpu as in `top`, mem = resident bytes)."""
        return (self._get('top') or [])[:max(0, int(n))]

    def memory(self):
        """Bytes: {total, used, available, cached, swap_total, swap_used}."""
        return self._get('memory')

    def uptime(self):
        return max(0.0, time.time() - psutil.boot_time())

    def disks(self):
        """Real file systems: [{name, mount, device, fstype, total, used}], the system disk first."""
        return self._get('disks') or []

    def disk(self, path=None):
        """Usage of the disk holding `path` (the system disk by default): {total, used}."""
        path = path or str(system_root())
        try:
            usage = psutil.disk_usage(path)
        except OSError:
            return {'total': 0, 'used': 0}
        return {'total': usage.total, 'used': usage.used}

    def net(self):
        """{iface, kind: 'wifi'|'ethernet', name, ip, down, up (KiB/s), total_down, total_up (bytes)}."""
        return self._get('net')

    def battery(self):
        """{percent, plugged, charging, full, secs_left (or nil)}; nil when the machine has no battery."""
        return self._get('battery')

    def temps(self):
        """{cpu, gpu} in degrees Celsius; missing when the OS exposes no sensors."""
        return self._get('temps')

    # readers
    @staticmethod
    def _read_cpu():
        cores = psutil.cpu_percent(interval=None, percpu=True)
        total = sum(cores) / len(cores) if cores else 0.0
        load = psutil.getloadavg()
        freq = psutil.cpu_freq()
        return {'total': round(total), 'cores': [round(c) for c in cores], 'count': len(cores) or 1,
                'load1': round(load[0], 2), 'load5': round(load[1], 2), 'load15': round(load[2], 2),
                'processes': len(psutil.pids()), 'freq_ghz': round(freq.current / 1000, 1) if freq else 0}

    @staticmethod
    def _empty_cpu():
        return {'total': 0, 'cores': [], 'count': 1, 'load1': 0, 'load5': 0, 'load15': 0, 'processes': 0, 'freq_ghz': 0}

    @staticmethod
    def _read_top():
        rows = []
        for p in psutil.process_iter(['name', 'cpu_percent', 'memory_info']):
            info = p.info
            if info['name'] and info['memory_info'] is not None:
                rows.append({'name': info['name'], 'cpu': round(info['cpu_percent'] or 0.0, 1),
                             'mem': info['memory_info'].rss})
        rows.sort(key=lambda r: (-r['cpu'], -r['mem']))
        return rows[:12]

    @staticmethod
    def _read_memory():
        vm, swap = psutil.virtual_memory(), psutil.swap_memory()
        cached = getattr(vm, 'cached', 0) + getattr(vm, 'buffers', 0)
        return {'total': max(vm.total, 1), 'used': vm.total - vm.available, 'available': vm.available,
                'cached': cached, 'swap_total': swap.total, 'swap_used': swap.used}

    @staticmethod
    def _empty_memory():
        return {'total': 1, 'used': 0, 'available': 1, 'cached': 0, 'swap_total': 0, 'swap_used': 0}

    @staticmethod
    def _read_disks():
        root = system_root()
        home = Path.home()
        out, seen = [], set()
        for part in psutil.disk_partitions(all=False):
            if part.fstype.lower() not in REAL_FS or part.device in seen or 'cdrom' in part.opts:
                continue
            if part.mountpoint.startswith(SKIP_MOUNTS):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            seen.add(part.device)
            mount = Path(part.mountpoint)
            name = 'system' if mount == root else 'home' if mount == home else \
                (part.mountpoint.rstrip('\\/') if sys.platform == 'win32' else mount.name) or part.mountpoint
            out.append({'name': name, 'mount': part.mountpoint, 'device': part.device, 'fstype': part.fstype,
                        'total': usage.total, 'used': usage.used})
        out.sort(key=lambda d: d['mount'] != str(root))
        return out

    def _read_net(self):
        iface = default_interface()
        counters = psutil.net_io_counters(pernic=True).get(iface)
        now = time.monotonic()
        down = up = 0.0
        if counters and self._net_prev and self._net_prev[0] == iface:
            dt = max(now - self._net_prev[1], 1e-3)
            down = max(0.0, (counters.bytes_recv - self._net_prev[2]) / dt / 1024)
            up = max(0.0, (counters.bytes_sent - self._net_prev[3]) / dt / 1024)
        self._net_prev = (iface, now, counters.bytes_recv, counters.bytes_sent) if counters else None
        if not iface:
            return {'iface': '', 'kind': '', 'name': '', 'ip': '', 'down': 0.0, 'up': 0.0, 'total_down': 0, 'total_up': 0}
        wifi = is_wifi(iface)
        if wifi and now - self._wifi_name[1] > 30:
            self._wifi_name = (wifi_name(iface), now)
        return {'iface': iface, 'kind': 'wifi' if wifi else 'ethernet', 'name': self._wifi_name[0] if wifi else 'Ethernet',
                'ip': interface_ip(iface), 'down': down, 'up': up,
                'total_down': counters.bytes_recv if counters else 0, 'total_up': counters.bytes_sent if counters else 0}

    @staticmethod
    def _empty_net():
        return {'iface': '', 'kind': '', 'name': '', 'ip': '', 'down': 0.0, 'up': 0.0, 'total_down': 0, 'total_up': 0}

    @staticmethod
    def _read_battery():
        b = psutil.sensors_battery()
        if b is None:
            return None
        secs = b.secsleft if b.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) else None
        return {'percent': round(b.percent), 'plugged': bool(b.power_plugged),
                'charging': bool(b.power_plugged) and b.percent < 99.5, 'full': bool(b.power_plugged) and b.percent >= 99.5,
                'secs_left': secs}

    @staticmethod
    def _read_temps():
        sensors = getattr(psutil, 'sensors_temperatures', None)
        readings = sensors() if sensors else {}
        out = {}
        for key, names in (('cpu', ('k10temp', 'coretemp', 'cpu_thermal', 'zenpower', 'acpitz')),
                           ('gpu', ('amdgpu', 'nouveau', 'nvidia'))):
            for name in names:
                if readings.get(name):
                    out[key] = round(readings[name][0].current)
                    break
        return out

    @staticmethod
    def _empty_temps():
        return {}


def default_interface():
    """The interface the machine reaches the internet through. No packets are sent: connecting
    a UDP socket only makes the OS choose a route."""
    address = ''
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))   # TEST-NET-1: never routed anywhere
            address = s.getsockname()[0]
    except OSError:
        pass
    stats = psutil.net_if_stats()
    addresses = psutil.net_if_addrs()
    for name, items in addresses.items():
        if any(a.family == socket.AF_INET and a.address == address for a in items):
            return name
    for name, items in addresses.items():   # offline or odd routing: the first interface that is up
        if name in stats and stats[name].isup and not name.startswith(('lo', 'Loopback')) and \
                any(a.family == socket.AF_INET and not a.address.startswith('127.') for a in items):
            return name
    return ''


def interface_ip(iface):
    for a in psutil.net_if_addrs().get(iface, []):
        if a.family == socket.AF_INET:
            return a.address
    return ''


def is_wifi(iface):
    if sys.platform == 'win32':
        return any(word in iface.lower() for word in ('wi-fi', 'wifi', 'wireless', 'wlan'))
    return Path('/sys/class/net', iface, 'wireless').exists() or iface.startswith('wl')


def wifi_name(iface):
    """SSID of the network `iface` is connected to ('' when unknown)."""
    if sys.platform == 'win32':
        try:
            out = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], capture_output=True, text=True, timeout=5,
                                 creationflags=0x08000000).stdout   # CREATE_NO_WINDOW
        except (OSError, subprocess.SubprocessError):
            return ''
        for line in out.splitlines():
            key, _, value = line.partition(':')
            if key.strip() == 'SSID':
                return value.strip()
        return ''
    return _linux_ssid(iface)


def _linux_ssid(iface):
    """Wireless extensions ioctl SIOCGIWESSID, the same call `iwgetid` makes."""
    SIOCGIWESSID, IW_ESSID_MAX_SIZE = 0x8B1B, 32
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(IW_ESSID_MAX_SIZE + 1)
        # struct iwreq: char ifr_name[16]; union { struct iw_point { void *pointer; __u16 length; __u16 flags; } essid; }
        request = struct.pack('16sPHH', iface.encode()[:15], ctypes.addressof(buf), IW_ESSID_MAX_SIZE, 0)
        request = ctypes.create_string_buffer(request, 32)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            if libc.ioctl(s.fileno(), SIOCGIWESSID, request) < 0:
                return ''
        return buf.value.decode('utf-8', 'replace')
    except (OSError, AttributeError):
        return ''
