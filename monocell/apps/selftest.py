"""System test: detect the hardware, then test what was found.

The app is a thin UI around `run_checks`: the worker fills Check objects,
the draw loop only reads them.
"""
import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
import wave

from .. import hardware
from ..app import App
from ..util import fit, human_size, which
from .system import read, cpu_model, meminfo, ip_addrs, battery


class Warn(Exception):
    pass


class Check:
    __slots__ = ('key', 'label', 'detail', 'metric', 'status', 'progress',
                 'error', 'data')

    def __init__(self, key, label):
        self.key = key
        self.label = label
        self.detail = ''
        self.metric = ''
        self.status = 'wait'
        self.progress = 0.0
        self.error = ''
        self.data = {}

    def step(self, p):
        self.progress = max(0.0, min(1.0, p))


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _removable(dev):
    value = dev.get('rm')
    return value is True or str(value) in ('1', 'true', 'True')


def _tran(dev):
    return str(dev.get('tran') or '').lower()


def _root_dev():
    for line in read('/proc/mounts').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == '/':
            return os.path.basename(parts[0])
    return ''


def block_devices():
    rc, out, _ = hardware.run(
        ['lsblk', '-b', '-J', '-o', 'NAME,SIZE,MODEL,RM,TYPE,TRAN'], timeout=4)
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []

    def walk(items):
        for item in items:
            yield item
            yield from walk(item.get('children') or [])

    return list(walk(data.get('blockdevices') or []))


def input_devices():
    names = []
    for block in read('/proc/bus/input/devices').split('\n\n'):
        match = re.search(r'^N: Name="(.+)"', block, re.M)
        if match:
            names.append(match.group(1))
    return names


def usb_devices():
    found = []
    for path in glob.glob('/sys/bus/usb/devices/*'):
        if not re.search(r'/[0-9]+-[0-9.]+$', path):
            continue
        product = read(os.path.join(path, 'product')).strip()
        maker = read(os.path.join(path, 'manufacturer')).strip()
        if product or maker:
            found.append((maker, product))
    return found


def detect_cpu(chk):
    model = cpu_model()
    cores = os.cpu_count() or 1
    mhz = 0.0
    for line in read('/proc/cpuinfo').splitlines():
        if line.startswith('cpu MHz'):
            try:
                mhz = max(mhz, float(line.split(':', 1)[1]))
            except (ValueError, IndexError):
                pass
    chk.data.update(cores=cores, mhz=mhz)
    return '%s · %d Kerne' % (model, cores) if model else None


def test_cpu(chk):
    n = 2000000
    step = max(1, n // 20)
    total = 0
    started = time.perf_counter()
    for i in range(0, n, step):
        for j in range(i, min(n, i + step)):
            total += j * j
        chk.step(min(1.0, (i + step) / n))
    elapsed = max(1e-6, time.perf_counter() - started)
    want = (n - 1) * n * (2 * n - 1) // 6
    if total != want:
        raise RuntimeError('CPU-Checksumme falsch')
    bits = ['%.1f Mops/s' % (n / elapsed / 1e6)]
    mhz = chk.data.get('mhz') or 0
    if mhz:
        bits.append('%.0f MHz' % mhz)
    temp = hardware.cpu_temp()
    if temp:
        bits.append('%.0f °C' % temp)
    chk.metric = ' · '.join(bits)


def detect_ram(chk):
    total = meminfo().get('MemTotal', 0)
    if not total:
        return None
    chk.data['total'] = total
    return human_size(total) + ' nutzbar'


def test_ram(chk):
    size = 32 * 1024 * 1024
    chunk = 4 * 1024 * 1024
    block = bytes([0x5a]) * chunk
    buf = bytearray(size)
    started = time.perf_counter()
    for off in range(0, size, chunk):
        buf[off:off + chunk] = block
        chk.step(0.5 * (off + chunk) / size)
    for off in range(0, size, chunk):
        if buf[off:off + chunk] != block:
            raise RuntimeError('RAM-Muster stimmt nicht')
        chk.step(0.5 + 0.5 * (off + chunk) / size)
    elapsed = max(1e-6, time.perf_counter() - started)
    free = meminfo().get('MemAvailable', 0)
    chk.metric = '%.0f MB/s · %s frei' % (size * 2 / elapsed / 1e6,
                                          human_size(free))


def detect_ssd(chk):
    disks = [d for d in block_devices()
             if d.get('type') == 'disk' and not _removable(d)]
    if not disks:
        return None
    root = _root_dev()
    pick = None
    for dev in disks:
        names = [dev.get('name')] + [c.get('name') for c in (dev.get('children') or [])]
        if root and root in names:
            pick = dev
            break
    if pick is None:
        pick = max(disks, key=lambda d: _int(d.get('size')))
    size = _int(pick.get('size'))
    model = (pick.get('model') or pick.get('name') or 'SSD').strip()
    speeds = []
    for path in glob.glob('/sys/class/ata_link/link*/sata_spd'):
        value = read(path).strip()
        if value:
            speeds.append(value)
    chk.data.update(dev=pick.get('name'), size=size, sata=speeds)
    detail = '%s · %s' % (model, human_size(size))
    if speeds:
        detail += ' · SATA ' + '/'.join(sorted(set(speeds)))
    return detail


def test_ssd(chk):
    path = os.path.expanduser('~/.cache/monocell/selftest.bin')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    size = 16 * 1024 * 1024
    chunk = 1024 * 1024
    block = os.urandom(chunk)
    try:
        started = time.perf_counter()
        with open(path, 'wb') as handle:
            for i in range(size // chunk):
                handle.write(block)
                chk.step(0.5 * (i + 1) / (size // chunk))
            handle.flush()
            os.fsync(handle.fileno())
        write_time = max(1e-6, time.perf_counter() - started)
        started = time.perf_counter()
        with open(path, 'rb') as handle:
            try:
                os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            except (AttributeError, OSError):
                pass
            for i in range(size // chunk):
                data = handle.read(chunk)
                if data != block:
                    raise RuntimeError('Disk-Daten stimmen nicht')
                chk.step(0.5 + 0.5 * (i + 1) / (size // chunk))
        read_time = max(1e-6, time.perf_counter() - started)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    chk.metric = 'schreiben %.0f MB/s · lesen %.0f MB/s' % (
        size / write_time / 1e6, size / read_time / 1e6)


STATUS_TEXT = {'Discharging': 'entlädt', 'Charging': 'lädt',
               'Full': 'voll', 'Not charging': 'hält'}


def detect_battery(chk):
    batteries = battery()
    if not batteries:
        return None
    name, cap, status, health = batteries[0]
    model = read('/sys/class/power_supply/%s/model_name' % name).strip()
    tech = read('/sys/class/power_supply/%s/technology' % name).strip()
    chk.data.update(name=name, cap=cap, status=status, health=health)
    text = name
    if model:
        text += ' ' + model
    if tech:
        text += ' · ' + tech
    return text


def test_battery(chk):
    bits = ['%d %%' % chk.data['cap'],
            STATUS_TEXT.get(chk.data['status'], chk.data['status'])]
    if chk.data.get('health'):
        bits.append('Gesundheit ' + chk.data['health'])
    remaining = hardware.battery_time()
    if remaining:
        bits.append('%s %s' % (hardware.format_minutes(remaining[0]),
                               'lädt' if remaining[1] == 'charging' else 'Rest'))
    chk.metric = ' · '.join(bits)


def detect_ac(chk):
    base = '/sys/class/power_supply'
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        if read(os.path.join(base, name, 'type')).strip() == 'Mains':
            online = read(os.path.join(base, name, 'online')).strip()
            chk.data.update(name=name, online=online)
            return '%s · %s' % (name, 'online' if online == '1' else 'offline')
    return None


def test_ac(chk):
    online = read('/sys/class/power_supply/%s/online' % chk.data['name']).strip()
    chk.metric = 'online' if online == '1' else 'offline (Akku)'
    if online != '1':
        chk.status = 'warn'


def interfaces(prefixes):
    found = []
    try:
        names = sorted(os.listdir('/sys/class/net'))
    except OSError:
        return found
    ips = dict(ip_addrs())
    for name in names:
        if name == 'lo' or not name.startswith(prefixes):
            continue
        state = read('/sys/class/net/%s/operstate' % name).strip()
        found.append((name, state, ips.get(name, '')))
    return found


def detect_lan(chk):
    ports = interfaces(('e', 'en', 'eth'))
    if not ports:
        return None
    chk.data['ports'] = ports
    return ', '.join(name for name, _, _ in ports)


def test_lan(chk):
    up = [x for x in chk.data['ports'] if x[1] == 'up' and x[2]]
    if not up:
        chk.status = 'warn'
        chk.metric = 'Link down'
        return
    gateway = hardware.default_gateway()
    if not gateway:
        chk.status = 'warn'
        chk.metric = 'kein Gateway'
        return
    rc, out, _ = hardware.run(['ping', '-c', '1', '-W', '1', gateway], timeout=3)
    match = re.search(r'time[=<]([\d.]+)\s*ms', out or '')
    if rc == 0:
        chk.metric = 'Gateway %s · Ping %s' % (
            gateway, (match.group(1) + ' ms') if match else 'ok')
    else:
        chk.status = 'warn'
        chk.metric = 'Gateway %s · kein Ping' % gateway


def detect_wifi(chk):
    ports = interfaces(('wl', 'wlan'))
    if not ports:
        return None
    chk.data['ports'] = ports
    return ', '.join(name for name, _, _ in ports)


def test_wifi(chk):
    name, state, _ = chk.data['ports'][0]
    signal = hardware.wifi_signal(name)
    ssid = ''
    rc, out, _ = hardware.run(['iw', 'dev', name, 'link'], timeout=2)
    match = re.search(r'SSID:\s*(.+)', out or '')
    if match:
        ssid = match.group(1).strip()
    if signal is None:
        chk.status = 'warn'
        chk.metric = 'kein Signal'
        return
    chk.metric = 'Signal %d %% · %s' % (signal, ssid or state)


def detect_bt(chk):
    base = '/sys/class/bluetooth'
    try:
        adapters = [n for n in sorted(os.listdir(base)) if n.startswith('hci')]
    except OSError:
        return None
    if not adapters:
        return None
    adapter = adapters[0]
    address = read(os.path.join(base, adapter, 'address')).strip()
    if not address:
        for command, pattern in (
                (['bluetoothctl', 'show'], r'Controller\s+([0-9A-Fa-f:]{17})'),
                (['hciconfig', adapter], r'BD Address:\s*([0-9A-Fa-f:]{17})')):
            rc, out, _ = hardware.run(command, timeout=2)
            match = re.search(pattern, out or '')
            if match:
                address = match.group(1)
                break
    chk.data.update(adapter=adapter, address=address)
    return ('%s %s' % (adapter, address)).strip()


def test_bt(chk):
    state = ''
    for path in glob.glob('/sys/class/rfkill/rfkill*'):
        if read(os.path.join(path, 'type')).strip() == 'bluetooth':
            state = read(os.path.join(path, 'state')).strip()
            break
    chk.metric = '%s · %s' % (chk.data.get('address') or '?',
                              'an' if state == '1' else 'aus/blockiert')
    if state != '1':
        chk.status = 'warn'


def detect_display(chk):
    drm = '/sys/class/drm'
    cards = []
    try:
        cards = [n for n in sorted(os.listdir(drm)) if re.fullmatch(r'card\d+', n)]
    except OSError:
        pass
    fb = '/sys/class/graphics/fb0'
    if not cards and not os.path.isdir(fb):
        return None
    driver = '?'
    if cards:
        match = re.search(r'DRIVER=(\S+)',
                          read(os.path.join(drm, cards[0], 'device/uevent')))
        if match:
            driver = match.group(1)
    resolution = read(os.path.join(fb, 'virtual_size')).strip()
    if resolution:
        resolution = resolution.replace(',', '×')
    elif cards:
        modes = read(os.path.join(drm, cards[0], 'modes'))
        resolution = modes.splitlines()[0] if modes else '?'
    chk.data.update(driver=driver, resolution=resolution or '?')
    return '%s · %s' % (driver, resolution or '?')


def test_display(chk):
    try:
        columns, rows = os.get_terminal_size()
    except OSError:
        columns = rows = 0
    bits = [chk.data.get('resolution') or '?']
    if columns:
        bits.append('Konsole %d×%d' % (columns, rows))
    font = chk.data.get('font') or ''
    if font:
        bits.append(font)
    chk.metric = ' · '.join(bits)


def detect_sound(chk):
    card = ''
    rc, out, _ = hardware.run(['aplay', '-l'], timeout=3)
    if rc == 0:
        match = re.search(r'card \d+: (.+?) \[', out or '')
        card = match.group(1) if match else 'ALSA'
    else:
        rc, out, _ = hardware.run(['pactl', 'info'], timeout=3)
        if rc == 0:
            match = re.search(r'Sink #\d+\s*\n\s*Name: (.+)', out or '')
            card = match.group(1) if match else 'PulseAudio/PipeWire'
    if not card:
        return None
    chk.data['card'] = card
    return card


def test_sound(chk):
    path = '/tmp/monocell-selftest.wav'
    try:
        with wave.open(path, 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            frames = bytearray()
            for i in range(4410):
                frames += struct.pack(
                    '<h', int(0.06 * 32767 * math.sin(2 * math.pi * 440 * i / 44100)))
            handle.writeframes(bytes(frames))
        for command in (['paplay', path], ['aplay', '-q', path], ['pw-play', path]):
            if which(command[0]):
                rc, _, _ = hardware.run(command, timeout=3)
                if rc == 0:
                    chk.metric = '%s · 100 ms Ton' % chk.data.get('card', 'Audio')
                    return
        chk.status = 'warn'
        chk.metric = 'Sink erkannt, Ton fehlgeschlagen'
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def detect_sensors(chk):
    cores = hardware.cpu_cores()
    fan = hardware.fan_rpm()
    if not cores and fan is None:
        return None
    chk.data.update(cores=cores, fan=fan)
    return 'coretemp' if cores else 'thinkpad fan'


def test_sensors(chk):
    cores = hardware.cpu_cores()
    fan = hardware.fan_rpm()
    bits = []
    if cores:
        bits.append('  '.join('%s %.0f °C' % (name, value) for name, value in cores[:4]))
    if fan:
        bits.append('Lüfter %d rpm' % fan)
    chk.metric = ' · '.join(bits) or 'keine Werte'
    if not cores and fan is None:
        chk.status = 'warn'


def detect_keyboard(chk):
    names = [n for n in input_devices()
             if 'keyboard' in n.lower() or 'AT Translated' in n]
    if not names:
        return None
    chk.data['names'] = names
    return '; '.join(names[:2])


def test_keyboard(chk):
    chk.metric = '%d Gerät(e) · evdev' % len(chk.data['names'])


def detect_trackpoint(chk):
    names = [n for n in input_devices() if 'trackpoint' in n.lower()]
    if not names:
        return None
    chk.data['names'] = names
    return '; '.join(names)


def test_trackpoint(chk):
    chk.metric = 'TPPS/2 · %d Gerät' % len(chk.data['names'])


def detect_usb(chk):
    devices = usb_devices()
    if not devices:
        return None
    chk.data['devices'] = devices
    return '%d Geräte' % len(devices)


def test_usb(chk):
    names = [' '.join(x for x in (maker, product) if x)
             for maker, product in chk.data['devices']]
    chk.metric = '; '.join(names[:3]) + (' …' if len(names) > 3 else '')


def detect_pccard(chk):
    pcmcia = False
    try:
        pcmcia = bool(os.listdir('/sys/bus/pcmcia/devices'))
    except OSError:
        pass
    microdrive = None
    for dev in block_devices():
        if dev.get('type') != 'disk' or not _removable(dev):
            continue
        if _tran(dev) in ('usb', 'mmc'):
            continue
        size = _int(dev.get('size'))
        if 500_000_000 < size < 2_500_000_000:
            microdrive = (dev, size)
            break
    if not pcmcia and not microdrive:
        return None
    chk.data.update(pcmcia=pcmcia, microdrive=microdrive)
    if microdrive:
        return 'IBM Microdrive %s' % human_size(microdrive[1])
    return 'PC Card-Adapter'


def test_pccard(chk):
    if chk.data.get('microdrive'):
        chk.metric = 'PC Card · %s' % human_size(chk.data['microdrive'][1])
    else:
        chk.metric = 'PC Card-Adapter erkannt'


def detect_sd(chk):
    for dev in block_devices():
        if dev.get('type') == 'disk' and _tran(dev) == 'mmc':
            size = _int(dev.get('size'))
            chk.data['card'] = (dev, size)
            return 'SD-Karte %s' % human_size(size)
    return None


def test_sd(chk):
    chk.metric = '%s · erkannt' % human_size(chk.data['card'][1])


def detect_ultrabase(chk):
    optical = [d for d in block_devices()
               if str(d.get('type')) == 'rom'
               or str(d.get('name', '')).startswith('sr')]
    serial = os.path.exists('/dev/ttyS0')
    parallel = os.path.exists('/dev/lp0') or os.path.exists('/dev/parport0')
    if not optical and not serial and not parallel:
        return None
    chk.data.update(optical=optical, serial=serial, parallel=parallel)
    bits = []
    if optical:
        bits.append('DVD/CD')
    if serial:
        bits.append('seriell')
    if parallel:
        bits.append('parallel')
    return 'Ultrabase · ' + ', '.join(bits)


def test_ultrabase(chk):
    bits = []
    optical = chk.data.get('optical') or []
    if optical:
        model = (optical[0].get('model') or '').strip()
        if model:
            bits.append(model)
        bits.append('Disc' if _int(optical[0].get('size')) else 'kein Disc')
    if chk.data.get('serial'):
        bits.append('seriell')
    if chk.data.get('parallel'):
        bits.append('parallel')
    chk.metric = ' · '.join(bits) or 'erkannt'


def detect_fingerprint(chk):
    for maker, product in usb_devices():
        low = (maker + ' ' + product).lower()
        if any(word in low for word in ('fingerprint', 'biometric', 'upek',
                                        'authentec', 'synaptics')):
            chk.data['name'] = (maker + ' ' + product).strip()
            return chk.data['name']
    return None


def test_fingerprint(chk):
    chk.metric = 'USB-Gerät erkannt'


def detect_bios(chk):
    product = read('/sys/class/dmi/id/product_name').strip()
    version = read('/sys/class/dmi/id/product_version').strip()
    bios = read('/sys/class/dmi/id/bios_version').strip()
    date = read('/sys/class/dmi/id/bios_date').strip()
    ec = read('/sys/class/dmi/id/ec_firmware_release').strip()
    if not product and not bios:
        return None
    chk.data.update(product=product, version=version, bios=bios, date=date, ec=ec)
    return ('%s %s' % (product, version)).strip()


def test_bios(chk):
    bits = []
    if chk.data.get('bios'):
        bits.append('BIOS %s' % chk.data['bios'])
    if chk.data.get('ec'):
        bits.append('EC %s' % chk.data['ec'])
    if chk.data.get('date'):
        bits.append(chk.data['date'])
    chk.metric = ' · '.join(bits) or 'erkannt'


def detect_thinklight(chk):
    leds = glob.glob('/sys/class/leds/*thinklight*')
    if not leds:
        return None
    chk.data['led'] = leds[0]
    return os.path.basename(leds[0])


def test_thinklight(chk):
    value = read(os.path.join(chk.data['led'], 'brightness')).strip()
    chk.metric = 'vorhanden · %s' % ('an' if value not in ('', '0') else 'aus')


def detect_buttons(chk):
    names = [n for n in input_devices()
             if 'extra buttons' in n.lower() or 'thinkpad' in n.lower()]
    if not names:
        return None
    code = None
    for path in glob.glob('/sys/class/input/event*'):
        name = read(os.path.join(path, 'device/name')).strip()
        if 'extra buttons' not in name.lower():
            continue
        caps = read(os.path.join(path, 'device/capabilities/key')).split()
        try:
            bits = int(''.join(word.rjust(16, '0') for word in caps), 16) if caps else 0
        except ValueError:
            bits = 0
        for candidate in (148, 202, 203, 240):
            if bits & (1 << candidate):
                code = candidate
                break
    chk.data['code'] = code
    return '; '.join(names[:2])


def test_buttons(chk):
    codes = {148: 'KEY_PROG1 (ThinkVantage)', 202: 'KEY_PROG3',
             203: 'KEY_PROG4', 240: 'KEY_UNKNOWN'}
    code = chk.data.get('code')
    chk.metric = '%s · Fn-Tasten' % codes.get(code, 'kein ThinkVantage-Code')
    if not code:
        chk.status = 'warn'


CHECKS = [
    ('cpu', 'CPU', detect_cpu, test_cpu),
    ('ram', 'RAM', detect_ram, test_ram),
    ('ssd', 'SSD', detect_ssd, test_ssd),
    ('battery', 'Akku', detect_battery, test_battery),
    ('ac', 'Netzteil', detect_ac, test_ac),
    ('lan', 'LAN', detect_lan, test_lan),
    ('wifi', 'WLAN', detect_wifi, test_wifi),
    ('bt', 'Bluetooth', detect_bt, test_bt),
    ('display', 'Display', detect_display, test_display),
    ('sound', 'Sound', detect_sound, test_sound),
    ('sensors', 'Sensoren', detect_sensors, test_sensors),
    ('keyboard', 'Tastatur', detect_keyboard, test_keyboard),
    ('trackpoint', 'TrackPoint', detect_trackpoint, test_trackpoint),
    ('usb', 'USB', detect_usb, test_usb),
    ('pccard', 'PC Card', detect_pccard, test_pccard),
    ('sd', 'SD-Karte', detect_sd, test_sd),
    ('ultrabase', 'Ultrabase', detect_ultrabase, test_ultrabase),
    ('fingerprint', 'Fingerprint', detect_fingerprint, test_fingerprint),
    ('bios', 'BIOS', detect_bios, test_bios),
    ('thinklight', 'ThinkLight', detect_thinklight, test_thinklight),
    ('buttons', 'Fn-Tasten', detect_buttons, test_buttons),
]

TESTS = {key: test for key, _, _, test in CHECKS}


def run_checks(checks=None, font='', on_phase=None, cancelled=None):
    checks = checks if checks is not None else []
    found = []
    total = len(CHECKS)
    for index, (key, label, detect, _test) in enumerate(CHECKS, 1):
        if cancelled and cancelled():
            return checks
        chk = Check(key, label)
        chk.data['font'] = font
        checks.append(chk)
        if on_phase:
            on_phase('detect', index - 1, total)
        chk.status = 'detect'
        try:
            detail = detect(chk)
        except Exception as exc:
            detail = None
            chk.status = 'fail'
            chk.error = 'Erkennung: %s' % exc
        if chk.status != 'fail':
            if detail is None:
                chk.status = 'absent'
                chk.detail = chk.detail or 'nicht vorhanden'
            else:
                chk.status = 'found'
                chk.detail = detail
                found.append(chk)
        if on_phase:
            on_phase('detect', index, total)
        time.sleep(0.04)
    count = len(found)
    if on_phase:
        on_phase('test', 0, count)
    for index, chk in enumerate(found, 1):
        if cancelled and cancelled():
            return checks
        test = TESTS[chk.key]
        chk.status = 'run'
        chk.progress = 0.0
        try:
            test(chk)
            if chk.status not in ('warn', 'fail'):
                chk.status = 'ok'
        except Warn as exc:
            chk.status = 'warn'
            chk.error = str(exc)
        except Exception as exc:
            chk.status = 'fail'
            chk.error = str(exc)
        chk.progress = 1.0
        if on_phase:
            on_phase('test', index, count)
    if on_phase:
        on_phase('done', count, count)
    return checks


class SelfTest(App):
    name = 'selftest'
    title = 'Systemtest'
    icon = '[!]'
    size = (94, 40)
    min_size = (54, 18)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.checks = []
        self.phase = 'detect'
        self.done = 0
        self.total = len(CHECKS)
        self.top = 0
        self.running = False
        self.cancelled = False
        self.error = ''
        self.every(0.15, self.tick)
        self.start()

    def start(self):
        if self.running:
            return
        self.checks = []
        self.phase = 'detect'
        self.done = 0
        self.total = len(CHECKS)
        self.top = 0
        self.error = ''
        self.cancelled = False
        self.running = True
        self.invalidate()
        self.thread(self.work, self.finished, self.failed)

    def work(self):
        run_checks(self.checks, font=self.wm.cfg.get('font', ''),
                   on_phase=self.phase_update,
                   cancelled=lambda: self.cancelled)

    def start_full_ram(self):
        if self.running:
            return
        self.checks = []
        self.phase = 'memory'
        self.done = 0
        self.total = 1
        self.top = 0
        self.error = ''
        self.cancelled = False
        self.running = True
        self.invalidate()
        self.thread(self.full_ram_work, self.finished, self.failed)

    def full_ram_work(self):
        chk = Check('ram_full', 'RAM vollständig')
        chk.status = 'run'
        self.checks = [chk]
        available = meminfo().get('MemAvailable', 0)
        mib = max(64, min(int(available * 0.8 / (1024 * 1024)), 3072))
        self.phase = 'memory'
        self.done = 0
        self.total = 1
        memtester = shutil.which('memtester')
        if not memtester:
            for candidate in ('/usr/sbin/memtester', '/usr/bin/memtester'):
                if os.path.exists(candidate):
                    memtester = candidate
                    break
        if not memtester:
            chk.status = 'warn'
            chk.metric = 'memtester nicht installiert'
            chk.progress = 1.0
            self.done = 1
            self.phase = 'done'
            self.invalidate()
            return
        try:
            proc = subprocess.Popen(
                [memtester, '%dM' % mib, '1'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                if self.cancelled:
                    proc.terminate()
                    break
                line = line.strip()
                if line:
                    chk.metric = line[:78]
            rc = proc.wait()
            if rc == 0:
                chk.status = 'ok'
                chk.metric = '%d MB · alle Muster ok' % mib
            elif self.cancelled:
                chk.status = 'warn'
                chk.metric = 'abgebrochen'
            else:
                chk.status = 'fail'
                chk.metric = 'memtester exit %d' % rc
        except FileNotFoundError:
            chk.status = 'warn'
            chk.metric = 'memtester nicht installiert'
        chk.progress = 1.0
        self.done = 1
        self.phase = 'done'
        self.invalidate()

    def phase_update(self, phase, done, total):
        self.phase = phase
        self.done = done
        self.total = max(1, total)

    def finished(self, _result=None):
        self.running = False
        self.invalidate()

    def failed(self, error):
        self.running = False
        self.error = str(error)
        self.invalidate()

    def tick(self):
        if self.running:
            self.invalidate()

    def on_close(self):
        self.cancelled = True
        return True

    def reopen(self, **kw):
        if not self.running:
            self.start()

    def progress(self):
        if self.phase == 'done':
            return 1.0
        return max(0.0, min(1.0, self.done / max(1, self.total)))

    def clamp_top(self, rows):
        self.top = max(0, min(self.top, max(0, len(self.checks) - rows)))

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if w < 36 or h < 10:
            cv.put(0, 0, fit('Systemtest: Fenster zu klein', w), th.c('dim', 'win'))
            return
        phase = {'detect': 'Erkenne Hardware', 'test': 'Teste Komponenten',
                 'memory': 'RAM-Volltest', 'done': 'Fertig'}.get(self.phase, '')
        cv.put(0, 0, 'SYSTEMTEST', th.c('accent', 'win', 1))
        cv.put(12, 0, fit('%s · %d/%d' % (phase, self.done, self.total), w - 12),
               th.c('bright', 'win'))
        hardware_bar = self.progress()
        from ..widgets import hbar
        hbar(cv, 0, 1, w, hardware_bar, th)
        top = 3
        rows = max(1, h - top - 2)
        self.clamp_top(rows)
        for i in range(rows):
            index = self.top + i
            if index >= len(self.checks):
                break
            self.draw_check(cv, 0, top + i, w, self.checks[index])
        message = ''
        if self.running:
            current = next((c for c in self.checks if c.status in ('detect', 'run')), None)
            message = 'Aktuell: %s' % (current.label if current else '...')
        elif self.phase == 'done':
            ok = sum(1 for c in self.checks if c.status == 'ok')
            warn = sum(1 for c in self.checks if c.status == 'warn')
            fail = sum(1 for c in self.checks if c.status == 'fail')
            absent = sum(1 for c in self.checks if c.status == 'absent')
            message = '%d OK · %d Warnung(en) · %d Fehler · %d nicht vorhanden' % (
                ok, warn, fail, absent)
        if self.error:
            message = self.error
        cv.put(0, h - 1, fit(message, max(1, w - 24)),
               th.c('red' if self.error else 'dim', 'win'))
        cv.put(max(0, w - 26), h - 1, 'r neu · m RAM · Esc zu', th.c('dim', 'win'))

    def draw_check(self, cv, x, y, w, chk):
        th = self.th
        icon, role = {
            'wait': ('[ ]', 'dim'), 'detect': ('[..]', 'yellow'),
            'found': ('[--]', 'accent'), 'absent': ('[-]', 'dim'),
            'run': ('[~]', 'yellow'), 'ok': ('[OK]', 'green'),
            'warn': ('[!]', 'yellow'), 'fail': ('[X]', 'red'),
        }.get(chk.status, ('[ ]', 'dim'))
        bar_width = 6
        name_width = 10
        detail_width = 24 if w >= 80 else 14
        metric_x = x + 5 + name_width + detail_width
        metric_width = max(6, w - (metric_x - x) - bar_width - 2)
        cv.put(x, y, icon, th.c(role, 'win'))
        cv.put(x + 5, y, fit(chk.label, name_width),
               th.c('bright' if chk.status in ('ok', 'run') else 'text', 'win'))
        cv.put(x + 5 + name_width, y, fit(chk.detail, detail_width), th.c('dim', 'win'))
        text = chk.metric or chk.error
        cv.put(metric_x, y, fit(text, metric_width),
               th.c(role if chk.status in ('warn', 'fail') else 'text', 'win'))
        fraction = chk.progress
        if chk.status in ('wait', 'found', 'absent'):
            fraction = 0.0
        bar_role = {'ok': 'accent', 'run': 'accent', 'warn': 'yellow',
                    'fail': 'red'}.get(chk.status, 'dim')
        from ..widgets import hbar
        hbar(cv, x + w - bar_width, y, bar_width, fraction, th, fg=bar_role)

    def on_key(self, ev):
        key = ev.key
        rows = max(1, self.ch - 5)
        if key == 'r':
            self.start()
            return True
        if key == 'm':
            self.start_full_ram()
            return True
        if key in ('up', 'down', 'pgup', 'pgdn', 'home', 'end'):
            if key == 'up':
                self.top -= 1
            elif key == 'down':
                self.top += 1
            elif key == 'pgup':
                self.top -= rows
            elif key == 'pgdn':
                self.top += rows
            elif key == 'home':
                self.top = 0
            else:
                self.top = 10 ** 9
            self.clamp_top(rows)
            self.invalidate()
            return True
        return False


APP = SelfTest


def main():
    checks = []

    def phase(state, done, total):
        if state in ('detect', 'test'):
            print('\r%s %d/%d' % (state, done, total), end='', flush=True)

    run_checks(checks, on_phase=phase)
    print()
    for chk in checks:
        extra = chk.metric or chk.error
        print('%-12s %-6s %s%s' % (chk.label, chk.status, chk.detail,
                                    (' | ' + extra) if extra else ''))


if __name__ == '__main__':
    main()
