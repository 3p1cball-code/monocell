"""Raw terminal I/O: input decoding (keys, SGR mouse, paste), diff renderer, palette control.

Colour back-ends
  truecolor : 24-bit SGR with the palette RGB (any modern terminal)
  palette   : 16 indexed colours, terminal palette re-programmed via OSC 4
  linux     : Linux VT / fbcon: palette via ESC]P, only 8 background colours,
              logical colours are permuted so the UI backgrounds land in slots 0-7
  ansi      : 16 indexed colours, terminal palette left alone
"""
import codecs
import fcntl
import os
import re
import struct
import sys
import termios
import time
import tty

from .screen import Buffer


class Key:
    __slots__ = ('key', 'char', 'orig')

    def __init__(self, key, char=None):
        self.key = key
        self.char = char
        self.orig = None        # key name before VT modifier augmentation

    def __repr__(self):
        return 'Key(%r)' % self.key


class Mouse:
    __slots__ = ('kind', 'x', 'y', 'button', 'mods', 'clicks')

    def __init__(self, kind, x, y, button=0, mods=frozenset(), clicks=1):
        self.kind = kind      # down up drag move wheelup wheeldown
        self.x = x
        self.y = y
        self.button = button  # 0 left 1 middle 2 right
        self.mods = mods
        self.clicks = clicks

    def moved(self, dx, dy):
        return Mouse(self.kind, self.x - dx, self.y - dy, self.button, self.mods, self.clicks)

    def __repr__(self):
        return 'Mouse(%s %d,%d b%d)' % (self.kind, self.x, self.y, self.button)


class Paste:
    __slots__ = ('text',)

    def __init__(self, text):
        self.text = text


CSI_RE = re.compile(r'\x1b\[([<?>=]?)([\d;:]*)([\x20-\x2f]*)([\x40-\x7e])')
CSI_PARTIAL = re.compile(r'\x1b\[[<?>=]?[\d;:]*[\x20-\x2f]*$')
TILDE = {1: 'home', 2: 'insert', 3: 'delete', 4: 'end', 5: 'pgup', 6: 'pgdn', 7: 'home', 8: 'end',
         11: 'f1', 12: 'f2', 13: 'f3', 14: 'f4', 15: 'f5', 17: 'f6', 18: 'f7', 19: 'f8',
         20: 'f9', 21: 'f10', 23: 'f11', 24: 'f12'}
LETTER = {'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left', 'H': 'home', 'F': 'end',
          'P': 'f1', 'Q': 'f2', 'R': 'f3', 'S': 'f4', 'E': 'kp5'}


def _mods(n):
    m = n - 1
    p = ''
    if m & 4:
        p += 'ctrl+'
    if m & 2:
        p += 'alt+'
    if m & 1:
        p += 'shift+'
    return p


def char_key(c, alt=False):
    p = 'alt+' if alt else ''
    if c in '\r\n':
        return Key(p + 'enter')
    if c == '\t':
        return Key(p + 'tab')
    if c in '\x7f\x08':
        return Key(p + 'backspace')
    if c == '\x00':
        return Key(p + 'ctrl+space')
    o = ord(c)
    if o < 27:
        return Key(p + 'ctrl+' + chr(o + 96))
    if o < 32:
        return Key(p + 'ctrl+' + '\\]^_'[o - 28])
    if alt:
        return Key('alt+' + c.lower() if c.isalpha() and c.islower() else 'alt+' + c)
    return Key(c, c)


TIOCLINUX = 0x541C
VT_MOD_KEYS = ('insert', 'delete', 'home', 'end', 'pgup', 'pgdn', 'up', 'down', 'left', 'right')


def vt_state_mods(state):
    """Linux VT shift state (TIOCL_GETSHIFTSTATE bits) -> set of modifier names."""
    mods = set()
    if state & (1 | 16 | 32):          # KG_SHIFT, KG_SHIFTL, KG_SHIFTR
        mods.add('shift')
    if state & (4 | 64 | 128):         # KG_CTRL, KG_CTRLL, KG_CTRLR
        mods.add('ctrl')
    if state & 8:                      # KG_ALT
        mods.add('alt')
    return mods


def augment_key(key, mods):
    """Add modifiers the Linux console keymap swallowed (Ctrl+Shift+V arrives as Ctrl+V,
    Shift+Insert as Insert). Only control letters and navigation keys are touched."""
    parts = key.split('+')
    base, have = parts[-1], set(parts[:-1])
    if len(base) == 1 and 'ctrl' in have:
        add = {'shift'} & mods
    elif base in VT_MOD_KEYS:
        add = {'shift', 'ctrl'} & mods
    else:
        return key
    if not add - have:
        return key
    have |= add
    return ''.join(m + '+' for m in ('ctrl', 'alt', 'shift') if m in have) + base


class InputParser:
    def __init__(self):
        self.buf = ''
        self.paste = None

    def pending_escape(self):
        return self.buf.startswith('\x1b')

    def flush(self):
        """Called when no more bytes arrived after a lone ESC (or partial seq)."""
        out = []
        if self.buf:
            s, self.buf = self.buf, ''
            if s == '\x1b':
                out.append(Key('esc'))
            elif s.startswith('\x1b') and len(s) == 2:
                out.append(char_key(s[1], alt=True))
            else:
                for c in s:
                    out.append(Key('esc') if c == '\x1b' else char_key(c))
        return out

    def feed(self, data):
        s = self.buf + data
        out = []
        i = 0
        n = len(s)
        while i < n:
            if self.paste is not None:
                j = s.find('\x1b[201~', i)
                if j < 0:
                    keep = 0
                    for k in range(1, 6):
                        if s.endswith('\x1b[201~'[:k]):
                            keep = k
                    self.paste += s[i:n - keep]
                    i = n - keep
                    break
                self.paste += s[i:j]
                out.append(Paste(self.paste.replace('\r\n', '\n').replace('\r', '\n')))
                self.paste = None
                i = j + 6
                continue
            c = s[i]
            if c != '\x1b':
                out.append(char_key(c))
                i += 1
                continue
            if i + 1 >= n:
                break
            c2 = s[i + 1]
            if c2 == '[':
                if i + 2 < n and s[i + 2] == 'M':          # X10 mouse
                    if i + 6 > n:
                        break
                    b, x, y = (ord(ch) - 32 for ch in s[i + 3:i + 6])
                    out.append(self._mouse(b, x - 1, y - 1, True, x10=True))
                    i += 6
                    continue
                if i + 2 < n and s[i + 2] == '[':          # linux console F1-F5
                    if i + 3 >= n:
                        break
                    k = 'ABCDE'.find(s[i + 3])
                    out.append(Key('f%d' % (k + 1)) if k >= 0 else Key('esc'))
                    i += 4
                    continue
                m = CSI_RE.match(s, i)
                if not m:
                    if CSI_PARTIAL.match(s, i) and n - i < 32:
                        break
                    out.append(Key('esc'))
                    i += 1
                    continue
                i = m.end()
                ev = self._csi(m.group(1), m.group(2), m.group(4))
                if ev is not None:
                    out.append(ev)
            elif c2 == 'O':
                if i + 2 >= n:
                    break
                k = LETTER.get(s[i + 2])
                if s[i + 2] == 'M':
                    k = 'enter'
                out.append(Key(k) if k else Key('esc'))
                i += 3
            elif c2 == '\x1b':
                out.append(Key('esc'))
                i += 1
            else:
                out.append(char_key(c2, alt=True))
                i += 2
        self.buf = s[i:]
        return out

    def _csi(self, pre, params, final):
        ps = [int(p) if p.isdigit() else 0 for p in params.replace(':', ';').split(';')] if params else []
        if pre == '<' and final in 'Mm':
            if len(ps) < 3:
                return None
            return self._mouse(ps[0], ps[1] - 1, ps[2] - 1, final == 'M')
        if pre:
            return None
        if final == 'u' and ps:
            # xterm modifyOtherKeys / kitty keyboard protocol: ESC[<code>;<mods>u
            cp = ps[0]
            mod = _mods(ps[1]) if len(ps) > 1 else ''
            base = {13: 'enter', 27: 'esc', 9: 'tab', 127: 'backspace', 32: 'space'}.get(cp)
            if base:
                return Key(mod + base)
            if 32 <= cp < 0x10FFFF:
                c = chr(cp)
                if mod:
                    return Key(mod + (c.lower() if c.isalpha() else c))
                return Key(c, c)
            return None
        if final == '~':
            code = ps[0] if ps else 0
            if code == 200:
                self.paste = ''
                return None
            k = TILDE.get(code)
            if not k:
                return None
            return Key(_mods(ps[1]) + k if len(ps) > 1 else k)
        if final in LETTER:
            k = LETTER[final]
            if len(ps) > 1:
                return Key(_mods(ps[1]) + k)
            return Key(k)
        if final == 'Z':
            return Key('shift+tab')
        return None

    def _mouse(self, b, x, y, press, x10=False):
        mods = set()
        if b & 4:
            mods.add('shift')
        if b & 8:
            mods.add('alt')
        if b & 16:
            mods.add('ctrl')
        mods = frozenset(mods)
        btn = b & 3
        if b & 64:
            return Mouse('wheelup' if btn == 0 else 'wheeldown', x, y, 0, mods)
        if b & 32:
            return Mouse('move' if btn == 3 else 'drag', x, y, btn, mods)
        if x10 and btn == 3:
            return Mouse('up', x, y, 0, mods)
        return Mouse('down' if press else 'up', x, y, btn, mods)


class MiceReader:
    """Linux VT mouse: raw PS/2 packets from /dev/input/mice (needs group 'input')."""

    def __init__(self, cols, rows):
        self.fd = os.open('/dev/input/mice', os.O_RDWR | os.O_NONBLOCK)
        try:   # IMPS/2 magic -> 4-byte packets with wheel
            os.write(self.fd, bytes([0xf3, 200, 0xf3, 100, 0xf3, 80]))
            time.sleep(0.05)
            os.read(self.fd, 64)
        except OSError:
            pass
        self.wheel = True
        self.cols, self.rows = cols, rows
        self.fx, self.fy = cols / 2.0, rows / 2.0
        self.buttons = 0
        self.pending = b''
        self.speed = 0.12

    def read(self):
        out = []
        try:
            data = os.read(self.fd, 256)
        except OSError:
            return out
        data = self.pending + data
        step = 4 if self.wheel else 3
        while len(data) >= step:
            pkt, data = data[:step], data[step:]
            b0 = pkt[0]
            if not (b0 & 8):          # out of sync -> drop a byte
                data = pkt[1:] + data
                continue
            dx = pkt[1] - 256 if b0 & 16 else pkt[1]
            dy = pkt[2] - 256 if b0 & 32 else pkt[2]
            dz = 0
            if step == 4:
                dz = pkt[3] if pkt[3] < 128 else pkt[3] - 256
                if dz > 7 or dz < -8:   # not really IMPS/2
                    self.wheel = False
                    dz = 0
            ox, oy = int(self.fx), int(self.fy)
            self.fx = max(0, min(self.cols - 1, self.fx + dx * self.speed))
            self.fy = max(0, min(self.rows - 1, self.fy - dy * self.speed * 0.5))
            x, y = int(self.fx), int(self.fy)
            btns = b0 & 7
            for bit, bn in ((1, 0), (4, 1), (2, 2)):
                if (btns & bit) and not (self.buttons & bit):
                    out.append(Mouse('down', x, y, bn))
                elif not (btns & bit) and (self.buttons & bit):
                    out.append(Mouse('up', x, y, bn))
            if (x, y) != (ox, oy):
                held = [bn for bit, bn in ((1, 0), (4, 1), (2, 2)) if btns & bit]
                out.append(Mouse('drag', x, y, held[0]) if held else Mouse('move', x, y))
            if dz:
                out.append(Mouse('wheeldown' if dz > 0 else 'wheelup', x, y))
            self.buttons = btns
        self.pending = data
        return out


class Terminal:
    def __init__(self, theme, mode='auto', fd_in=None, fd_out=None):
        self.theme = theme
        self.fd_in = sys.stdin.fileno() if fd_in is None else fd_in
        self.fd_out = sys.stdout.fileno() if fd_out is None else fd_out
        self.termname = os.environ.get('TERM', '')
        if mode == 'auto':
            mode = os.environ.get('MONOCELL_COLOR', 'auto')
        if mode == 'auto':
            if self.termname.startswith('linux'):
                mode = 'linux'
            elif os.environ.get('COLORTERM', '') in ('truecolor', '24bit'):
                mode = 'truecolor'
            else:
                mode = 'palette'
        self.mode = mode
        self.parser = InputParser()
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.mice = None
        self.cell_aspect = 2.0
        self.w, self.h = self.query_size()
        self.front = None
        self.back = Buffer(self.w, self.h)
        self.sgr_cache = {}
        self.perm = list(range(16))     # logical -> physical (linux mode)
        self.bgmap = list(range(16))    # logical bg -> logical bg allowed
        self.allowed_bg = list(range(16))
        self.saved = None
        self.cursor = None              # (x, y) to show hardware cursor, else hidden
        self.bytes_out = 0
        try:
            name = os.ttyname(self.fd_in)
        except OSError:
            name = ''
        # on a kernel VT the keymap drops Shift from Ctrl+Shift+V etc.; ask the kernel instead
        self.on_vt = bool(re.match(r'/dev/tty\d+$', name)) and self.mode == 'linux'

    def vt_shift_state(self):
        if not self.on_vt:
            return 0
        try:
            buf = bytearray([6])        # TIOCL_GETSHIFTSTATE
            fcntl.ioctl(self.fd_in, TIOCLINUX, buf, True)
            return buf[0]
        except OSError:
            self.on_vt = False
            return 0

    def vt_mods(self):
        return frozenset(vt_state_mods(self.vt_shift_state())) if self.on_vt else frozenset()

    # ---------- setup ----------
    def query_size(self):
        try:
            rows, cols, xp, yp = struct.unpack('HHHH', fcntl.ioctl(self.fd_out, termios.TIOCGWINSZ, b'\0' * 8))
            if xp and yp and cols and rows:
                self.cell_aspect = (yp / rows) / (xp / cols)
            if cols and rows:
                return cols, rows
        except OSError:
            pass
        try:
            s = os.get_terminal_size(self.fd_out)
            return s.columns, s.lines
        except OSError:
            return 80, 24

    def start(self):
        try:
            self.saved = termios.tcgetattr(self.fd_in)
            tty.setraw(self.fd_in)
        except termios.error:
            self.saved = None
        seq = '\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h\x1b[?2004h\x1b[2J'
        self.write(seq)
        if self.mode == 'linux':
            from . import fbmode
            fbmode.ensure_text(self.fd_out)    # an earlier run may have died in graphics mode
            try:
                self.mice = MiceReader(self.w, self.h)
            except OSError:
                self.mice = None
        self.apply_palette()

    def stop(self):
        if self.mode == 'linux':
            from . import fbmode
            fbmode.restore()                   # never leave the console in graphics mode
        out = '\x1b[?1003l\x1b[?1002l\x1b[?1000l\x1b[?1006l\x1b[?2004l\x1b[0m\x1b[2J\x1b[H\x1b[?7h\x1b[?25h\x1b[?1049l'
        if self.mode == 'palette':
            out += '\x1b]104\x1b\\'
        elif self.mode == 'linux':
            out += '\x1b]R'
        self.write(out)
        if self.saved:
            termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, self.saved)
        if self.mice:
            os.close(self.mice.fd)

    def write(self, s):
        data = s.encode('utf-8', 'replace')
        self.bytes_out += len(data)
        while data:
            try:
                n = os.write(self.fd_out, data)
                data = data[n:]
            except BlockingIOError:
                time.sleep(0.002)
            except InterruptedError:
                continue

    def resize(self):
        self.w, self.h = self.query_size()
        self.back = Buffer(self.w, self.h)
        self.front = None
        if self.mice:
            self.mice.cols, self.mice.rows = self.w, self.h

    # ---------- colours ----------
    @property
    def exact_bg(self):
        """True when a palette index is shown as the surface colour it names.

        Only the kernel VT remaps surface colours: it has 16 text colours but just
        8 backgrounds, so the palette is reordered and anything used as a surface
        becomes the nearest of the eight the UI uses (see apply_palette). Every
        other backend shows the index as programmed.
        """
        return self.mode != 'linux'

    def apply_palette(self):
        """(Re)compute colour mapping and program the terminal palette if supported."""
        th = self.theme
        self.sgr_cache = {}
        if self.mode == 'linux':
            # the theme decides it: layout[:8] are the VT's background slots, in
            # order, everything after that is text only
            low = list(th.low)
            phys = list(th.layout)               # physical slot -> logical colour
            self.perm = [0] * 16
            for p, l in enumerate(phys):
                self.perm[l] = p
            self.allowed_bg = low
            self.bgmap = [i if i in low else th.nearest(th.rgb[i], low) for i in range(16)]
            out = ''.join('\x1b]P%X%s' % (p, '%02x%02x%02x' % th.rgb[l]) for p, l in enumerate(phys))
            self.write(out)
        else:
            self.perm = list(range(16))
            self.bgmap = list(range(16))
            self.allowed_bg = list(range(16))
            if self.mode == 'palette':
                out = ''.join('\x1b]4;%d;rgb:%02x/%02x/%02x\x1b\\' % ((i,) + th.rgb[i]) for i in range(16))
                self.write(out)
        self.front = None

    def sgr(self, a):
        s = self.sgr_cache.get(a)
        if s is not None:
            return s
        fg, bg, fl = a & 15, (a >> 4) & 15, a >> 8
        m = self.mode
        if m == 'truecolor':
            r, g, b = self.theme.rgb[fg]
            R, G, B = self.theme.rgb[bg]
            s = '\x1b[0;38;2;%d;%d;%d;48;2;%d;%d;%d' % (r, g, b, R, G, B)
            if fl & 2:
                s += ';4'
            if fl & 1:
                s += ';1'
            s += 'm'
        elif m == 'linux':
            pf = self.perm[fg]
            pb = self.perm[self.bgmap[bg]]
            s = '\x1b[0;%s3%d;4%dm' % ('1;' if pf >= 8 else '', pf & 7, pb & 7)
        else:
            s = '\x1b[0;%d;%d%s%sm' % (30 + fg if fg < 8 else 82 + fg, 40 + bg if bg < 8 else 92 + bg,
                                       ';4' if fl & 2 else '', ';1' if fl & 1 else '')
        self.sgr_cache[a] = s
        return s

    # ---------- output ----------
    def flush(self):
        back, w, h = self.back, self.w, self.h
        front = self.front
        out = []
        cur = None
        if front is None:
            front = Buffer(w, h, -1)
            out.append('\x1b[0m\x1b[2J')
        bch, bat, fch, fat = back.ch, back.at, front.ch, front.at
        sgr = self.sgr
        for y in range(h):
            bc, ba, fc, fa = bch[y], bat[y], fch[y], fat[y]
            if bc == fc and ba == fa:
                continue
            x = 0
            while x < w:
                if bc[x] == fc[x] and ba[x] == fa[x]:
                    x += 1
                    continue
                out.append('\x1b[%d;%dH' % (y + 1, x + 1))
                while x < w:
                    if bc[x] == fc[x] and ba[x] == fa[x]:
                        # look ahead: bridge small unchanged gaps
                        j = x + 1
                        while j < w and j < x + 5 and bc[j] == fc[j] and ba[j] == fa[j]:
                            j += 1
                        if j >= w or j >= x + 5:
                            break
                    a = ba[x]
                    if a != cur:
                        out.append(sgr(a))
                        cur = a
                    out.append(bc[x])
                    x += 1
            fc[:] = bc
            fa[:] = ba
        if self.cursor:
            cx, cy = self.cursor
            out.append('\x1b[%d;%dH\x1b[?25h' % (cy + 1, cx + 1))
        elif out:
            out.append('\x1b[?25l')
        self.front = front
        if out:
            self.write(''.join(out))

    # ---------- input ----------
    def read_events(self):
        try:
            data = os.read(self.fd_in, 65536)
        except BlockingIOError:
            return []
        if not data:
            return None
        evs = self.parser.feed(self.decoder.decode(data))
        if self.on_vt and evs:
            mods = None
            for ev in evs:
                if isinstance(ev, Key) and not ev.char:
                    if mods is None:
                        mods = vt_state_mods(self.vt_shift_state())
                    if mods:
                        k2 = augment_key(ev.key, mods)
                        if k2 != ev.key:
                            ev.orig, ev.key = ev.key, k2
        return evs
