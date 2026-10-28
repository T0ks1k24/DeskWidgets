"""A small subset of conky's `${variable}` syntax, so widgets written for DeskWidgets 1.x/2.x
(`L.cp('${cpu cpu0}')`) keep working. New widgets should use `L.sys` instead."""
import re

_VARIABLE = re.compile(r'\$\{([^}]*)\}|\$([a-z_]+)')


def conky_parse(text, sysinfo):
    return _VARIABLE.sub(lambda m: str(_value((m.group(1) or m.group(2)).split(), sysinfo)), str(text))


def _value(words, sysinfo):
    if not words:
        return ''
    name, args = words[0], words[1:]
    arg = args[0] if args else ''
    if name == 'cpu':
        cpu = sysinfo.cpu()
        if arg.startswith('cpu') and arg[3:].isdigit():
            index = int(arg[3:])
            return cpu['total'] if index == 0 else (cpu['cores'][index - 1] if index <= len(cpu['cores']) else 0)
        return cpu['total']
    if name == 'processes':
        return sysinfo.cpu()['processes']
    if name == 'loadavg':
        return sysinfo.cpu()['load' + (arg if arg in ('1', '5', '15') else '1')]
    if name == 'freq_g':
        return sysinfo.cpu()['freq_ghz']
    if name in ('mem', 'memmax', 'memperc', 'swap', 'swapmax', 'swapperc'):
        m = sysinfo.memory()
        total, used = (m['total'], m['used']) if name.startswith('mem') else (m['swap_total'], m['swap_used'])
        if name.endswith('perc'):
            return round(100 * used / total) if total else 0
        return _size(total if name.endswith('max') else used)
    if name == 'top':
        rows = sysinfo.top(int(args[1]) if len(args) > 1 and args[1].isdigit() else 1)
        index = int(args[1]) - 1 if len(args) > 1 and args[1].isdigit() else 0
        if index >= len(rows):
            return ''
        row = rows[index]
        return {'name': row['name'], 'cpu': row['cpu'], 'mem_res': _size(row['mem'])}.get(arg, '')
    if name in ('downspeedf', 'upspeedf'):
        return '%.1f' % sysinfo.net()['down' if name == 'downspeedf' else 'up']
    if name in ('totaldown', 'totalup'):
        return _size(sysinfo.net()['total_down' if name == 'totaldown' else 'total_up'])
    if name == 'addr':
        return sysinfo.net()['ip']
    if name == 'wireless_essid':
        return sysinfo.net()['name'] if sysinfo.net()['kind'] == 'wifi' else ''
    if name in ('fs_used_perc', 'fs_used', 'fs_size'):
        usage = sysinfo.disk(arg or None)
        if name == 'fs_used_perc':
            return round(100 * usage['used'] / usage['total']) if usage['total'] else 0
        return _size(usage['used' if name == 'fs_used' else 'total'])
    return ''


def _size(number):
    """Like conky: 1.5GiB, 120MiB."""
    number = float(number)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if number < 1024 or unit == 'TiB':
            return f'{number:.0f}{unit}' if unit in ('B', 'KiB', 'MiB') else f'{number:.1f}{unit}'
        number /= 1024
