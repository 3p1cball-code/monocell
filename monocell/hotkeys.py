"""Fn/hotkey handling on the Linux console: brightness, volume, wifi.

Linux delivers Fn combinations as evdev key codes (KEY_BRIGHTNESSUP, KEY_VOLUMEUP,
...) instead of characters on the tty, so they never reach the normal input
parser.  We listen to the input devices that carry them - on a ThinkPad the
internal keyboard and "ThinkPad Extra Buttons" - and forward the action names.
"""
import os
import struct
import time

from .util import log

EVENT = struct.Struct('llHHi')
KEY_E = 18                 # KEY_E
META = (125, 126)          # KEY_LEFTMETA, KEY_RIGHTMETA
ACTIONS = {
    113: 'mute',
    114: 'volume-down',
    115: 'volume-up',
    224: 'brightness-down',
    225: 'brightness-up',
    243: 'brightness-up',
    227: 'display',
    228: 'thinklight',
    236: 'battery',
    238: 'wifi',
}


def candidates():
    """(path, name) of input devices that can carry media/brightness keys."""
    out = []
    try:
        names = sorted(n for n in os.listdir('/dev/input') if n.startswith('event'))
    except OSError:
        return out
    for ev in names:
        dev = os.path.join('/sys/class/input', ev, 'device')
        try:
            with open(os.path.join(dev, 'name')) as f:
                name = f.read().strip()
        except OSError:
            continue
        low = name.lower()
        if 'mouse' in low or 'touchpad' in low or 'trackpoint' in low:
            continue
        try:
            with open(os.path.join(dev, 'capabilities/key')) as f:
                words = f.read().split()
            # sysfs prints the bitmap words high to low; each word is 64 bits
            bits = int(''.join(w.rjust(16, '0') for w in words), 16)
        except (OSError, ValueError):
            bits = 0
        wanted = 0
        for code in list(ACTIONS) + [KEY_E] + list(META):
            wanted |= 1 << code
        if bits & wanted or 'thinkpad extra buttons' in low:
            out.append((os.path.join('/dev/input', ev), name))
    return out


class Hotkeys:
    def __init__(self, loop, callback):
        self.loop = loop
        self.callback = callback
        self.fds = {}
        self.last = {}
        self.meta = {}      # per device: is a Meta key currently held?
        self.meta_at = {}   # when it was pressed
        self.meta_used = {} # did another key follow while it was held?
        for path, name in candidates():
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log('hotkeys: cannot open %s (%s): %s' % (path, name, e))
                continue
            self.fds[fd] = (path, name)
            loop.add_reader(fd, lambda fd=fd: self.read(fd))

    def read(self, fd):
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            self.close(fd)
            return
        if not data:
            self.close(fd)
            return
        self.feed(fd, data)

    def feed(self, fd, data):
        """Decode a block of input events (kept separate so it can be tested)."""
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, kind, code, value = EVENT.unpack_from(data, off)
            if kind != 1:
                continue
            now = time.monotonic()
            if code in META:
                if value:
                    self.meta[fd] = True
                    self.meta_at[fd] = now
                    self.meta_used[fd] = False
                    continue
                self.meta[fd] = False
                # a plain tap of the Windows key opens the start menu; anything
                # typed while it was held (Win+E) already did its own thing
                if self.meta_used.get(fd) or now - self.meta_at.get(fd, 0.0) > 0.6:
                    continue
                action = 'startmenu'
            else:
                if value not in (1, 2):
                    continue
                if self.meta.get(fd):
                    self.meta_used[fd] = True
                # Win+E: the console drops the combination, so it only exists here
                if code == KEY_E and self.meta.get(fd) and value == 1:
                    action = 'files'
                else:
                    action = ACTIONS.get(code)
            if not action:
                continue
            if now - self.last.get(action, 0.0) < 0.06:
                continue
            self.last[action] = now
            try:
                self.callback(action)
            except Exception:
                log('hotkey %s failed' % action)

    def close(self, fd):
        self.loop.remove_reader(fd)
        self.fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass

    def close_all(self):
        for fd in list(self.fds):
            self.close(fd)
