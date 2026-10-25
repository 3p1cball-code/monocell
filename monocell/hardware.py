"""Hardware controls without extra dependencies: backlight, sound volume, wifi signal."""
import os
import re
import socket
import struct
import subprocess

from .util import which

BACKLIGHT = '/sys/class/backlight'
POWER_SUPPLY = '/sys/class/power_supply'


def run(argv, timeout=5):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, '', '%s: not found' % argv[0]
    except subprocess.TimeoutExpired:
        return 124, '', '%s: timeout' % argv[0]
    except OSError as e:
        return 1, '', str(e)


# ------------------------------------------------------------------ backlight
def backlight():
    """(device, current, maximum) of the active backlight, or None."""
    names = []
    try:
        names = os.listdir(BACKLIGHT)
    except OSError:
        return None
    order = sorted(names, key=lambda n: (n.startswith('acpi_video'), n))
    for name in order:
        base = os.path.join(BACKLIGHT, name)
        try:
            with open(os.path.join(base, 'brightness')) as f:
                cur = int(f.read().strip())
            with open(os.path.join(base, 'max_brightness')) as f:
                top = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if top > 0:
            return name, cur, top
    return None


def brightness_percent():
    b = backlight()
    if not b:
        return None
    _, cur, top = b
    return int(round(100.0 * cur / top))


def _session_path():
    sid = os.environ.get('XDG_SESSION_ID')
    if sid:
        return '/org/freedesktop/login1/session/' + sid
    if not which('busctl'):
        return None
    rc, out, _ = run(['busctl', 'call', 'org.freedesktop.login1', '/org/freedesktop/login1',
                      'org.freedesktop.login1.Manager', 'GetSessionByPID', 'u', str(os.getpid())])
    m = re.search(r'"(/[^"]+)"', out or '')
    return m.group(1) if rc == 0 and m else None


def _set_brightness_value(name, value):
    path = os.path.join(BACKLIGHT, name, 'brightness')
    try:
        with open(path, 'w') as f:
            f.write('%d\n' % value)
        return True
    except OSError:
        pass
    path = _session_path()
    if path and which('busctl'):
        rc, _, _ = run(['busctl', 'call', 'org.freedesktop.login1', path,
                        'org.freedesktop.login1.Session', 'SetBrightness', 'ssu',
                        'backlight', name, str(value)])
        if rc == 0:
            return True
    return False


def set_brightness_percent(percent):
    b = backlight()
    if not b:
        return None
    name, _, top = b
    value = max(1, min(top, int(round(percent / 100.0 * top))))
    if not _set_brightness_value(name, value):
        return None
    return int(round(100.0 * value / top))


def step_brightness(steps=1):
    b = backlight()
    if not b:
        return None
    name, cur, top = b
    value = max(1, min(top, cur + int(steps)))
    if not _set_brightness_value(name, value):
        return None
    return int(round(100.0 * value / top))


# ------------------------------------------------------------------ volume
def _pactl_volume():
    rc, out, _ = run(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'])
    m = re.search(r'(\d+)%', out or '')
    if rc != 0 or not m:
        return None
    rc2, out2, _ = run(['pactl', 'get-sink-mute', '@DEFAULT_SINK@'])
    m2 = re.search(r'Mute:\s*(yes|no)', out2 or '')
    return {'volume': int(m.group(1)), 'muted': bool(m2 and m2.group(1) == 'yes'),
            'backend': 'pactl'}


def _wpctl_volume():
    rc, out, _ = run(['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'])
    m = re.search(r'Volume:\s*([\d.]+)', out or '')
    if rc != 0 or not m:
        return None
    return {'volume': int(round(float(m.group(1)) * 100)),
            'muted': '[MUTED]' in (out or ''), 'backend': 'wpctl'}


def _amixer_volume():
    rc, out, _ = run(['amixer', 'get', 'Master'])
    m = re.search(r'\[(\d+)%\]', out or '')
    if rc != 0 or not m:
        return None
    return {'volume': int(m.group(1)), 'muted': '[off]' in (out or ''),
            'backend': 'amixer'}


def audio_state():
    for probe in (_pactl_volume, _wpctl_volume, _amixer_volume):
        st = probe()
        if st:
            return st
    return None


def set_volume(percent):
    percent = max(0, min(100, int(percent)))
    for cmd in (['pactl', 'set-sink-volume', '@DEFAULT_SINK@', '%d%%' % percent],
                ['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', '%.2f' % (percent / 100.0)],
                ['amixer', '-q', 'sset', 'Master', '%d%%' % percent]):
        if which(cmd[0]) and run(cmd)[0] == 0:
            return percent
    return None


def set_muted(muted):
    arg = '1' if muted else '0'
    for cmd in (['pactl', 'set-sink-mute', '@DEFAULT_SINK@', arg],
                ['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', arg],
                ['amixer', '-q', 'sset', 'Master', 'mute' if muted else 'unmute']):
        if which(cmd[0]) and run(cmd)[0] == 0:
            return muted
    return None


# ------------------------------------------------------------------ wifi
def wifi_signal(iface):
    """Signal strength in percent (0-100) for a wireless interface, or None."""
    try:
        with open('/proc/net/wireless') as f:
            lines = f.readlines()[2:]
    except OSError:
        lines = []
    for line in lines:
        p = line.split()
        if not p or p[0].rstrip(':') != iface:
            continue
        try:
            quality = float(p[2].rstrip('.'))
            if quality > 70:
                return max(0, min(100, int(round(quality))))
            return max(0, min(100, int(round(quality * 100.0 / 70.0))))
        except (ValueError, IndexError):
            break
    if which('iw'):
        rc, out, _ = run(['iw', 'dev', iface, 'link'])
        m = re.search(r'signal:\s*(-?\d+)\s*dBm', out or '')
        if rc == 0 and m:
            return max(0, min(100, int(round((int(m.group(1)) + 90) * 100.0 / 60.0))))
    return None


def signal_bars(percent, width=3):
    if percent is None:
        return ''
    level = 3 if percent >= 67 else 2 if percent >= 34 else 1 if percent > 0 else 0
    level = min(width, level)
    return ')' * level + '-' * (width - level)


# ------------------------------------------------------------------ battery
def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _int(path):
    try:
        return int(_read(path))
    except ValueError:
        return 0


def battery_time(base=None):
    """(minutes, 'discharging'|'charging') estimated from the first battery, or None."""
    base = base or POWER_SUPPLY
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None

    def estimate(p, charging):
        energy = _int(os.path.join(p, 'energy_now'))
        full = _int(os.path.join(p, 'energy_full'))
        power = _int(os.path.join(p, 'power_now'))
        current = _int(os.path.join(p, 'current_now'))
        volt = _int(os.path.join(p, 'voltage_now'))
        if energy > 0 and full >= energy:
            remaining = full - energy if charging else energy
            if remaining > 0:
                if not power and current > 0 and volt > 0:
                    power = current * volt // 1000000
                if power > 0:
                    return max(1, int(remaining * 60 / power))
        charge = _int(os.path.join(p, 'charge_now'))
        charge_full = _int(os.path.join(p, 'charge_full'))
        if charge > 0:
            remaining = charge_full - charge if charging else charge
            if remaining > 0:
                if current > 0:
                    return max(1, int(remaining * 60 / current))
                if power > 0 and volt > 0:
                    energy_uwh = remaining * volt // 1000000
                    if energy_uwh > 0:
                        return max(1, int(energy_uwh * 60 / power))
        return None

    for name in names:
        p = os.path.join(base, name)
        if _read(os.path.join(p, 'type')) != 'Battery':
            continue
        status = _read(os.path.join(p, 'status'))
        if status == 'Discharging':
            secs = _int(os.path.join(p, 'time_to_empty_now'))
            if secs > 0:
                return max(1, secs // 60), 'discharging'
            mins = estimate(p, False)
            if mins:
                return mins, 'discharging'
        elif status == 'Charging':
            secs = _int(os.path.join(p, 'time_to_full_now'))
            if secs > 0:
                return max(1, secs // 60), 'charging'
            mins = estimate(p, True)
            if mins:
                return mins, 'charging'
    return None

def format_minutes(minutes):
    if not minutes or minutes <= 0:
        return ''
    h, m = divmod(int(minutes), 60)
    return '%dh%02d' % (h, m) if h else '%dm' % m


def default_gateway():
    try:
        with open('/proc/net/route') as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) > 2 and p[1] == '00000000':
                    return socket.inet_ntoa(struct.pack('<L', int(p[2], 16)))
    except (OSError, ValueError):
        pass
    return None
