"""Bluetooth via BlueZ: one long-lived `bluetoothctl` process per window.

`bluetoothctl` is the only BlueZ client that brings its own pairing agent, so the
session runs it with `--agent=NoInputNoOutput` (Just Works is confirmed
automatically; devices that ask for a PIN still need the terminal tool).  The
output is parsed into a small model: adapter, known devices and the last result.
Everything here is plain stdlib; the UI polls `rev`.
"""
import collections
import os
import re
import shutil
import subprocess
import threading
import time

ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
PROMPT = re.compile(r'\[[^\]\n]*\]>\s*')
BACKSPACE = re.compile(r'[^\x08]?\x08')
ADDR = r'[0-9A-F]{2}(?::[0-9A-F]{2}){5}'
RE_EVENT = re.compile(r'^\[(NEW|CHG|DEL)\]\s+(Device|Controller)\s+(' + ADDR + r')\s*(.*)$')
RE_LIST = re.compile(r'^(Device|Controller)\s+(' + ADDR + r')\s*(?:\(([^)]*)\))?\s*(.*)$')
RE_GONE = re.compile(r'^Device\s+(' + ADDR + r')\s+not available$')
RE_REMOVED = re.compile(r'^Device\s+(' + ADDR + r')\s+removed$')
RE_PROP = re.compile(r'^([A-Za-z][A-Za-z0-9 /_-]*?):\s*(.*)$')
RE_NIL = re.compile(r'^([A-Za-z][A-Za-z0-9 /_-]*?)\s+is\s+nil$')

BOOL_PROPS = ('Paired', 'Trusted', 'Blocked', 'Connected', 'LegacyPairing',
              'ServicesResolved', 'WakeAllowed')

ICON_TAGS = {
    'audio-card': 'AUD', 'audio-headset': 'AUD', 'audio-headphones': 'AUD',
    'phone': 'TEL', 'smartphone': 'TEL',
    'input-keyboard': 'KBD', 'input-mouse': 'MOU', 'input-gaming': 'GAM',
    'input-tablet': 'TAB', 'computer': 'PC', 'printer': 'PRN', 'scanner': 'SCN',
    'camera-photo': 'CAM', 'camera-video': 'CAM', 'video-display': 'DSP',
    'modem': 'MOD', 'network-wireless': 'NET', 'multimedia-player': 'MPL',
}

UUID_TAGS = {'1108': 'AUD', '110a': 'AUD', '110b': 'AUD', '111e': 'AUD',
             '111f': 'AUD', '1124': 'HID', '1101': 'SPP', '1105': 'FT',
             '1106': 'FT', '1115': 'TEL', '1116': 'TEL', '1117': 'TEL'}

VERSIONS = {0x6: '4.0', 0x7: '4.1', 0x8: '4.2', 0x9: '5.0', 0xa: '5.1',
            0xb: '5.2', 0xc: '5.3', 0xd: '5.4', 0xe: '5.5'}

QUIET = ('attempting to', 'waiting to', 'requesting', 'setdiscoveryfilter',
         'hci0 type', 'agent registered', 'agent unregistered', 'authorize',
         'confirm', 'request', 'removing')


def strip_ansi(line):
    return ANSI.sub('', line).replace('\r', '').rstrip('\n')


def clean_line(line):
    """Remove colours, the readline prompt (it can be '[bluetoothctl]>' or
    '[DeviceName]>') and backspace redraws, leaving the actual output."""
    line = ANSI.sub('', line)
    line = PROMPT.sub(' ', line)
    while '\x08' in line:
        line = BACKSPACE.sub('', line, count=1)
    return line.replace('\r', '').rstrip('\n')


def is_address_name(name, address):
    """BlueZ names unnamed devices after their address ('4E-87-C0-18-8F-54')."""
    if not name:
        return True
    return name.upper().replace('-', ':') == (address or '').upper()


def numeric(value):
    """'-42', '0xffffffd6 (-42)', '0x64 (100)', 'nil' -> int or None."""
    value = (value or '').strip()
    if not value or value == 'nil':
        return None
    m = re.search(r'\((-?\d+)\)', value)
    if m:
        return int(m.group(1))
    m = re.match(r'0x([0-9a-fA-F]+)', value)
    if m:
        n = int(m.group(1), 16)
        return n - (1 << 32) if n >= (1 << 31) else n
    m = re.match(r'-?\d+', value)
    return int(m.group(0)) if m else None


UUID_PRIORITY = ('AUD', 'HID', 'TEL', 'FT', 'SPP')


def device_tag(dev):
    tag = ICON_TAGS.get((dev.get('icon') or '').lower(), '')
    if tag:
        return tag
    found = set()
    for uuid in dev.get('uuids') or []:
        m = re.search(r'\(([0-9a-fA-F]{8})-', uuid)
        if m:
            short = m.group(1)[4:].lower()
            if short in UUID_TAGS:
                found.add(UUID_TAGS[short])
    for tag in UUID_PRIORITY:
        if tag in found:
            return tag
    return ''


def display_name(dev):
    name = dev.get('alias') or dev.get('name') or ''
    return name if not is_address_name(name, dev.get('address', '')) else ''


def sort_key(dev):
    return (not dev.get('connected'), not dev.get('paired'),
            -(dev.get('rssi') if dev.get('rssi') is not None else -999),
            display_name(dev).lower(), dev.get('address', ''))


def primary_action(dev):
    """What Enter does on this row: pair, connect or disconnect."""
    if not dev:
        return None
    if dev.get('connected'):
        return 'disconnect'
    if dev.get('paired'):
        return 'connect'
    return 'pair'


class Session:
    """A running bluetoothctl with parsed state.  start() once, stop() at the end."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.thread = None
        self.rev = 0
        self.adapter = {}
        self.devices = {}
        self.msg = ''
        self.msg_bad = False
        self.result_seq = 0
        self.result = ''
        self.result_kind = ''
        self.scanning = False
        self.scan_since = 0.0
        self.err = ''
        self.auto_trust = True
        self._context = None
        self._sent = collections.deque(maxlen=40)
        self._pairing = ''
        self._connecting = ''
        self._disconnecting = ''
        self.stopped = False

    # ---------------------------------------------------------- process
    def start(self):
        exe = shutil.which('bluetoothctl')
        if not exe:
            raise RuntimeError('bluetoothctl not installed')
        argv = [exe, '--agent=NoInputNoOutput']
        stdbuf = shutil.which('stdbuf')
        if stdbuf:
            argv = [stdbuf, '-oL', '-eL'] + argv
        env = dict(os.environ)
        env['TERM'] = 'dumb'
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, bufsize=0, env=env)
        except OSError as e:
            raise RuntimeError(str(e))
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self.refresh(full=True)

    def _reader(self):
        try:
            for raw in iter(self.proc.stdout.readline, b''):
                try:
                    self.feed(raw.decode('utf-8', 'replace'))
                except Exception as e:      # a parser bug must not kill the reader
                    self.err = 'parse: %s' % e
        finally:
            self.stopped = True
            self.rev += 1

    def stop(self):
        if self.proc is None:
            return
        self.send('quit')
        try:
            self.proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except Exception:
                pass
        self.proc = None

    def send(self, command):
        if self.proc is None or self.proc.stdin is None:
            return False
        with self.lock:
            try:
                self.proc.stdin.write((command + '\n').encode())
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                self.err = str(e)
                return False
            self._sent.append(command.strip())
        return True

    # ---------------------------------------------------------- commands
    def refresh(self, full=False):
        self.send('show')
        self.send('devices')

    def scan(self, on=True):
        if on:
            if self.send('scan on'):
                self.scanning = True
                self.scan_since = time.monotonic()
        else:
            self.send('scan off')
            self.scanning = False
        self.rev += 1

    def pair(self, address):
        self._pairing = address
        self.send('pair ' + address)

    def connect(self, address):
        self._connecting = address
        self._disconnecting = ''
        self.send('connect ' + address)

    def disconnect(self, address):
        self._disconnecting = address
        self._connecting = ''
        self.send('disconnect ' + address)

    def trust(self, address, on=True):
        self.send('trust %s %s' % (address, 'on' if on else 'off'))

    def remove(self, address):
        if self._pairing == address:
            self._pairing = ''
        self.send('remove ' + address)
        self.devices.pop(address, None)
        self.rev += 1

    def power(self, on):
        self.send('power ' + ('on' if on else 'off'))
        if not on:
            self.scanning = False

    def discoverable(self, on=True):
        self.send('discoverable ' + ('on' if on else 'off'))
        if on:
            self.send('pairable on')
            self.send('discoverable-timeout 180')

    # ---------------------------------------------------------- parsing
    def _ordered(self):
        return sorted(self.devices.values(), key=sort_key)

    def _dev(self, address):
        dev = self.devices.get(address)
        if dev is None:
            dev = {'address': address}
            self.devices[address] = dev
        return dev

    def _result(self, text, bad=False, kind='other'):
        self.msg, self.msg_bad = text, bad
        self.result, self.result_kind = text, kind
        self.result_seq += 1
        self.rev += 1

    def feed(self, line):
        """Parse one bluetoothctl output line (also used by the tests)."""
        raw = clean_line(line)
        if not raw.strip():
            return
        indented = raw[:1] in (' ', '\t')
        line = raw.strip()
        if line in self._sent:
            self._context = None
            return

        m = RE_EVENT.match(line)
        if m:
            kind, what, address, rest = m.groups()
            self._context = None
            if what == 'Controller':
                if kind != 'NEW' and ':' in rest:
                    key, value = rest.split(':', 1)
                    self._adapter_prop(key, value.strip())
                return
            if kind == 'NEW':
                dev = self._dev(address)
                if rest and not is_address_name(rest, address):
                    dev['name'] = rest
                self.send('info ' + address)
                self.rev += 1
                return
            if kind == 'DEL':
                self.devices.pop(address, None)
                self.rev += 1
                return
            self._device_prop(address, rest)
            return

        m = RE_GONE.match(line)
        if m:
            self.devices.pop(m.group(1), None)
            self._result('device not found', True)
            return

        m = RE_REMOVED.match(line)
        if m:
            self.devices.pop(m.group(1), None)
            self._result('forgotten', False, 'remove')
            return

        m = RE_LIST.match(line)
        if m:
            what, address, transport, rest = m.groups()
            if what == 'Device':
                self._context = ('device', address)
                dev = self._dev(address)
                if rest and not is_address_name(rest, address):
                    dev['name'] = rest
                if transport is None:
                    self.send('info ' + address)
            else:
                self._context = ('adapter', address)
                self.adapter.setdefault('address', address)
            self.rev += 1
            return

        if indented and self._context:
            kind, address = self._context
            m = RE_NIL.match(line) or RE_PROP.match(line)
            if m:
                value = None if len(m.groups()) == 1 else m.group(2).strip()
                if kind == 'device':
                    self._apply_device(address, m.group(1), value)
                else:
                    self._adapter_prop(m.group(1), value)
                return
        self._context = None
        self._message(line)

    def _device_prop(self, address, text):
        m = RE_NIL.match(text)
        if m:
            key, value = m.group(1), None
        else:
            m = RE_PROP.match(text)
            if not m:
                return
            key, value = m.group(1), m.group(2).strip()
        self._apply_device(address, key, value)

    def _apply_device(self, address, key, value):
        dev = self._dev(address)
        if key in BOOL_PROPS:
            dev[key.lower()] = (value == 'yes')
            if key == 'Paired' and value == 'yes':
                if self.auto_trust:
                    self.send('trust ' + address)
                if self._pairing == address:
                    self._pairing = ''
                    self._result('paired', False, 'pair')
            if key == 'Paired' and value == 'no':
                if self._pairing == address:
                    self._pairing = ''
            if key == 'Connected' and value == 'yes' and self._connecting == address:
                self._connecting = ''
                self._result('connected', False, 'connect')
            if key == 'Connected' and value == 'no' and self._disconnecting == address:
                self._disconnecting = ''
                self._result('disconnected', False, 'disconnect')
        elif key in ('Name', 'Alias'):
            if value:
                dev[key.lower()] = value
        elif key == 'Icon':
            dev['icon'] = value or ''
        elif key == 'Class':
            dev['class'] = value
        elif key == 'RSSI':
            dev['rssi'] = numeric(value)
        elif key == 'TxPower':
            dev['txpower'] = numeric(value)
        elif key == 'Battery Percentage':
            dev['battery'] = numeric(value)
        elif key == 'Adapter':
            dev['adapter'] = value
        elif key == 'UUID':
            uuids = dev.setdefault('uuids', [])
            if value not in uuids:
                uuids.append(value)
        self.rev += 1

    def _adapter_prop(self, key, value):
        ad = self.adapter
        if key in ('Powered', 'Discovering', 'Discoverable', 'Pairable'):
            ad[key.lower()] = (value == 'yes')
            if key == 'Discovering':
                self.scanning = (value == 'yes')
                if self.scanning and not self.scan_since:
                    self.scan_since = time.monotonic()
                if not self.scanning:
                    self.scan_since = 0.0
            if key == 'Powered' and value != 'yes':
                self.scanning = False
                self._connecting = self._disconnecting = ''
        elif key in ('Name', 'Alias'):
            if value:
                ad['name'] = value
        elif key == 'Address':
            ad['address'] = value
        elif key == 'Version':
            ad['version'] = numeric(value)
        elif key == 'Manufacturer':
            ad['manufacturer'] = value
        elif key == 'Class':
            ad['class'] = value
        elif key == 'DiscoverableTimeout':
            ad['discoverable_timeout'] = numeric(value)
        self.rev += 1

    def _message(self, line):
        low = line.lower()
        if low.startswith(QUIET) or low.startswith('[agent]'):
            return
        if low.startswith('pairing successful'):
            self._result('paired', False, 'pair')
        elif low.startswith('connection successful'):
            self._result('connected', False, 'connect')
        elif low.startswith(('successful disconnected', 'disconnection successful')):
            self._result('disconnected', False, 'disconnect')
        elif low.startswith('discovery started'):
            self.scanning = True
            self._result('scanning', False, 'scan')
        elif low.startswith('discovery stopped'):
            self.scanning = False
            self.scan_since = 0.0
            self._result('scan stopped', False, 'scan')
        elif re.match(r'^device .+ removed$', low):
            self._result('forgotten', False, 'remove')
        elif low.startswith(('failed', 'org.bluez.error', 'authentication',
                             'not available')):
            self._result(_terse(line), True, _fail_kind(low))
        elif (low.startswith('changing') and 'succeeded' in low
              and any(word in low for word in ('power', 'pairable', 'discoverable'))):
            self._result(_terse(line), False,
                         'power' if 'power' in low else 'adapter')
        elif low.startswith('no default controller'):
            self.adapter = {}
            self._result('no Bluetooth adapter', True, 'adapter')
        else:
            self.msg = _terse(line)


def _terse(text):
    return re.sub(r'\s+', ' ', text).strip()[:80]


def _fail_kind(low):
    for kind in ('disconnect', 'connect', 'pair', 'remove', 'power', 'trust'):
        if kind in low:
            return kind
    return 'other'
