"""LAN device watch for the Network widget (runs in the background, started by widget.lua).

Ping-scans the subnet of the default route with nmap and flags every device whose MAC is not
listed in KNOWN ("mac|friendly name" per line). Notifies once per new unknown device.
Works on Linux (`ip neigh`) and Windows (`arp -a`).

Usage: netwatch.py CACHE KNOWN SEEN LOG [uk|en]
The result is written to CACHE: state (OK | ALERT | OFFLINE | NONMAP), device count,
unknown count, first unknown IP; one value per line.
"""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WINDOWS = sys.platform == 'win32'
NO_WINDOW = 0x08000000 if WINDOWS else 0
MAC = re.compile(r'^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$', re.I)


def run(command, timeout=60):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=timeout,
                              creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def write_cache(path, lines):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)


def default_gateway():
    if WINDOWS:
        for line in run(['route', 'print', '-4', '0.0.0.0']).splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == '0.0.0.0' and parts[1] == '0.0.0.0':
                return parts[2]
        return ''
    try:
        best = None
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            f = line.split()
            if f[1] == '00000000' and (best is None or int(f[6]) < best[0]):
                best = (int(f[6]), f[0], socket.inet_ntoa(bytes.fromhex(f[2])[::-1]))
        return best[2] if best else ''
    except (OSError, ValueError, IndexError):
        return ''


def local_subnet():
    """(interface, ip, 'a.b.c.d/nn') of the interface that reaches the internet, or None."""
    import psutil
    from deskwidgets.engine.sysinfo import default_interface
    iface = default_interface()
    for a in psutil.net_if_addrs().get(iface, []):
        if a.family == socket.AF_INET and a.netmask:
            net = ipaddress.ip_network(f'{a.address}/{a.netmask}', strict=False)
            return iface, a.address, str(net)
    return None


def neighbours(iface, ip):
    """[(ip, mac)] of the devices the OS has seen answer on the LAN."""
    found = []
    if WINDOWS:
        in_ours = False
        for line in run(['arp', '-a']).splitlines():
            if line.startswith('Interface:'):
                in_ours = ip in line
            elif in_ours:
                f = line.split()
                if len(f) >= 3 and MAC.match(f[1]) and f[2].lower() == 'dynamic':
                    found.append((f[0], f[1].replace('-', ':')))
        return found
    for line in run(['ip', 'neigh', 'show', 'dev', iface]).splitlines():
        f = line.split()
        if 'lladdr' in f and f[-1] != 'FAILED':
            mac = f[f.index('lladdr') + 1]
            if MAC.match(mac):
                found.append((f[0], mac))
    return found


def vendor(mac, nmap):
    prefix = re.sub('[:-]', '', mac).upper()[:6]
    candidates = [Path('/usr/share/nmap/nmap-mac-prefixes'), Path(nmap).parent / 'nmap-mac-prefixes']
    for db in candidates:
        try:
            for line in db.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith(prefix):
                    return line[len(prefix):].strip()
        except OSError:
            continue
    return ''


def notify(title, body):
    command = [sys.executable, '-m', 'deskwidgets', 'notify', title, body]
    try:
        subprocess.run(command, capture_output=True, timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def main(cache, known_path, seen_path, log_path, lang='uk'):
    def tr(uk, en):
        return en if lang == 'en' else uk

    nmap = shutil.which('nmap')
    if not nmap:
        write_cache(cache, ['NONMAP'])
        return
    for path in (known_path, seen_path, log_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch()

    where = local_subnet()
    if not where:
        write_cache(cache, ['OFFLINE'])
        return
    iface, ip, subnet = where
    gateway = default_gateway()
    run([nmap, '-sn', subnet], timeout=120)   # an unprivileged ping scan also fills the neighbour table

    known = {line.split('|')[0].strip().lower() for line in Path(known_path).read_text(encoding='utf-8').splitlines()
             if '|' in line}
    seen = {line.strip().lower() for line in Path(seen_path).read_text(encoding='utf-8').splitlines()}
    macs, unknown = set(), []
    for address, mac in neighbours(iface, ip):
        mac = mac.lower()
        if address == gateway or address == ip or mac in macs:
            continue
        macs.add(mac)
        if mac not in known:
            unknown.append((address, mac, vendor(mac, nmap)))

    for address, mac, maker in unknown:
        if mac in seen:
            continue
        with open(seen_path, 'a', encoding='utf-8') as f:
            f.write(mac + '\n')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {tr('НОВИЙ пристрій', 'NEW device')}: "
                    f"{address} · {mac} · {maker or '?'}\n")
        notify(tr('Новий пристрій у мережі', 'New device on your network'),
               f"{address} · {maker or tr('невідомий виробник', 'unknown vendor')}\nMAC: {mac}")

    if unknown:
        write_cache(cache, ['ALERT', str(len(macs)), str(len(unknown)), unknown[0][0]])
    else:
        write_cache(cache, ['OK', str(len(macs)), '', ''])


if __name__ == '__main__':
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    main(*sys.argv[1:6])
