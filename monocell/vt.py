"""A compact VT100/xterm-subset emulator (screen model only).

Used by the Terminal app and by the screenshot test harness.
Output stays strictly cell-aligned: wide/box/line-drawing glyphs are mapped to ASCII.
"""
import re
import unicodedata

from .util import char_ascii

DEF = 16                      # 'default colour' marker
F_BOLD, F_UL, F_REV, F_DIM, F_INV = 1, 2, 4, 8, 16
DEFAULT_ATTR = DEF | (DEF << 5)

DEC_GFX = {'j': '+', 'k': '+', 'l': '+', 'm': '+', 'n': '+', 'q': '-', 't': '+', 'u': '+',
           'v': '+', 'w': '+', 'x': '|', '`': '*', 'a': ':', 'f': 'o', 'g': '#', '~': '*',
           'o': '-', 'p': '-', 'r': '-', 's': '_', 'y': '<', 'z': '>', '{': 'p', '|': '!', '}': 'L',
           'h': '#', 'i': '#', '0': '#'}

TEXT_RE = re.compile(r'[^\x00-\x1f\x7f-\x9f]+')
_wide = {}


def glyph(c):
    """Return the ASCII replacement for a non-ASCII char, padded to its cell width."""
    r = _wide.get(c)
    if r is None:
        eaw = unicodedata.east_asian_width(c)
        cat = unicodedata.category(c)
        if cat.startswith('M') or cat == 'Cf':
            r = ''
        else:
            r = char_ascii(c) or '?'
            r = (r + ' ')[:2] if eaw in 'WF' else r[:1]
        _wide[c] = r
    return r


class VTScreen:
    def __init__(self, cols=80, rows=24, scrollback=2000, reply=None):
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.max_history = scrollback
        self.reply = reply or (lambda s: None)
        self.history = []          # list of (chars list, attrs list)
        self.palette = {}          # OSC 4 changes (used by test harness)
        self.rgb_palette = None    # optional 16 RGB tuples to map truecolor back
        self.keep_unicode = False  # test harness: keep single-width symbols
        self.title = ''
        self.bell = False
        self.reset()

    def reset(self):
        self.lines = [self._blank_line() for _ in range(self.rows)]
        self.alt_lines = None
        self.cx = self.cy = 0
        self.attr = DEFAULT_ATTR
        self.top, self.bottom = 0, self.rows - 1
        self.autowrap = True
        self.wrapnext = False
        self.cursor_visible = True
        self.app_cursor = False
        self.app_keypad = False
        self.mouse_mode = 0
        self.mouse_sgr = False
        self.bracketed_paste = False
        self.origin = False
        self.insert = False
        self.g = ['B', 'B']
        self.gl = 0
        self.saved = (0, 0, DEFAULT_ATTR, ['B', 'B'], 0)
        self.state = 0             # 0 ground, 1 esc, 2 csi, 3 osc, 4 charset, 5 esc-intermediate
        self.seq = ''
        self.charset_slot = 0
        self.tabs = set(range(8, 1000, 8))
        self.dirty = True

    def _blank_line(self):
        a = self._erase_attr()
        return [[' '] * self.cols, [a] * self.cols]

    def _erase_attr(self):
        # erased cells keep the background colour but no flags
        a = getattr(self, 'attr', DEFAULT_ATTR)
        return DEF | (a & (0x1f << 5))

    # ------------------------------------------------------------ public
    def resize(self, cols, rows):
        cols, rows = max(1, cols), max(1, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        for buf in (self.lines, self.alt_lines):
            if buf is None:
                continue
            for ln in buf:
                if cols > self.cols:
                    ln[0].extend([' '] * (cols - self.cols))
                    ln[1].extend([DEFAULT_ATTR] * (cols - self.cols))
                else:
                    del ln[0][cols:]
                    del ln[1][cols:]
        old_cols = self.cols
        self.cols = cols
        if rows < self.rows:
            cut = max(0, self.cy + 1 - rows)
            for i in range(cut):
                ln = self.lines.pop(0)
                if self.alt_lines is None:
                    self._push_history(ln)
            del self.lines[rows:]
            if self.alt_lines is not None:
                del self.alt_lines[:max(0, len(self.alt_lines) - rows)]
            self.cy -= cut
        else:
            while len(self.lines) < rows:
                if self.history and self.alt_lines is None and False:
                    pass
                self.lines.append([[' '] * cols, [DEFAULT_ATTR] * cols])
            if self.alt_lines is not None:
                while len(self.alt_lines) < rows:
                    self.alt_lines.append([[' '] * cols, [DEFAULT_ATTR] * cols])
        self.rows = rows
        self.top, self.bottom = 0, rows - 1
        self.cx = min(self.cx, cols - 1)
        self.cy = max(0, min(self.cy, rows - 1))
        self.wrapnext = False
        self.dirty = True

    def _push_history(self, ln):
        self.history.append(ln)
        if len(self.history) > self.max_history:
            del self.history[:len(self.history) - self.max_history]

    def feed(self, s):
        self.dirty = True
        i, n = 0, len(s)
        while i < n:
            if self.state == 0:
                m = TEXT_RE.match(s, i)
                if m:
                    self._text(m.group())
                    i = m.end()
                    continue
                self._control(s[i])
                i += 1
            else:
                c = s[i]
                i += 1
                self._escape_char(c)

    # ------------------------------------------------------------ text
    def _text(self, t):
        if not t.isascii() and not self.keep_unicode:
            t = ''.join(c if ord(c) < 128 else glyph(c) for c in t)
        if self.g[self.gl] == '0':
            t = ''.join(DEC_GFX.get(c, c) for c in t)
        cols = self.cols
        a = self.attr
        while t:
            if self.wrapnext:
                if self.autowrap:
                    self.cx = 0
                    self._linefeed()
                self.wrapnext = False
            room = cols - self.cx
            chunk, t = t[:room], t[room:]
            ln = self.lines[self.cy]
            k = len(chunk)
            if self.insert:
                del ln[0][cols - k:]
                del ln[1][cols - k:]
                ln[0][self.cx:self.cx] = chunk
                ln[1][self.cx:self.cx] = [a] * k
            else:
                ln[0][self.cx:self.cx + k] = chunk
                ln[1][self.cx:self.cx + k] = [a] * k
            self.cx += k
            if self.cx >= cols:
                self.cx = cols - 1
                self.wrapnext = True

    # ------------------------------------------------------------ controls
    def _control(self, c):
        if c == '\x1b':
            self.state = 1
            self.seq = ''
        elif c == '\r':
            self.cx = 0
            self.wrapnext = False
        elif c in '\n\x0b\x0c':
            self._linefeed()
        elif c == '\x08':
            if self.cx > 0:
                self.cx -= 1
            self.wrapnext = False
        elif c == '\t':
            nx = min([t for t in self.tabs if t > self.cx] + [self.cols - 1])
            self.cx = min(nx, self.cols - 1)
        elif c == '\x07':
            self.bell = True
        elif c == '\x0e':
            self.gl = 1
        elif c == '\x0f':
            self.gl = 0
        elif c == '\x9b':
            self.state = 2
            self.seq = ''

    def _linefeed(self):
        if self.cy == self.bottom:
            self.scroll_up(1)
        elif self.cy < self.rows - 1:
            self.cy += 1
        self.wrapnext = False

    def scroll_up(self, n):
        top, bot = self.top, self.bottom
        for _ in range(n):
            ln = self.lines.pop(top)
            if top == 0 and self.alt_lines is None:
                self._push_history(ln)
            self.lines.insert(bot, self._blank_line())

    def scroll_down(self, n):
        top, bot = self.top, self.bottom
        for _ in range(n):
            self.lines.pop(bot)
            self.lines.insert(top, self._blank_line())

    # ------------------------------------------------------------ escapes
    def _escape_char(self, c):
        st = self.state
        if st == 1:      # after ESC
            if c == '[':
                self.state = 2
                self.seq = ''
            elif c == ']':
                self.state = 3
                self.seq = ''
            elif c in '()*+':
                self.state = 4
                self.charset_slot = 0 if c == '(' else 1
            elif c in '#%':
                self.state = 5
            elif c == 'P' or c == '_' or c == '^':
                self.state = 3        # DCS/APC/PM: swallow until ST like OSC
                self.seq = '\x00'
            else:
                self.state = 0
                self._esc(c)
        elif st == 2:    # CSI
            if '\x40' <= c <= '\x7e':
                self.state = 0
                self._csi(self.seq, c)
            elif c == '\x1b':
                self.state = 1
            elif c in '\r\n\x08':
                self._control(c)
            else:
                self.seq += c
                if len(self.seq) > 64:
                    self.state = 0
        elif st == 3:    # OSC / DCS string
            if c == 'P' and not self.seq:
                self.state = 7
                self.seq = ''
                return
            if c == '\x07':
                self.state = 0
                self._osc(self.seq)
            elif c == '\x1b':
                self.state = 6
            else:
                self.seq += c
                if len(self.seq) > 4096:
                    self.state = 0
        elif st == 6:    # ESC inside OSC -> expect '\'
            self.state = 0
            self._osc(self.seq)
            if c != '\\':
                self._escape_char_ground(c)
        elif st == 7:    # Linux console palette: P <slot> <rrggbb>
            self.seq += c
            if len(self.seq) >= 7:
                self.state = 0
                try:
                    idx = int(self.seq[0], 16)
                    self.palette[idx] = (int(self.seq[1:3], 16), int(self.seq[3:5], 16), int(self.seq[5:7], 16))
                except ValueError:
                    pass
        elif st == 4:
            self.g[self.charset_slot] = c
            self.state = 0
        elif st == 5:
            self.state = 0

    def _escape_char_ground(self, c):
        self.state = 1
        self._escape_char(c)

    def _esc(self, c):
        if c == '7':
            self.saved = (self.cx, self.cy, self.attr, list(self.g), self.gl)
        elif c == '8':
            self.cx, self.cy, self.attr, g, self.gl = self.saved
            self.g = list(g)
            self.wrapnext = False
        elif c == 'D':
            self._linefeed()
        elif c == 'E':
            self.cx = 0
            self._linefeed()
        elif c == 'M':
            if self.cy == self.top:
                self.scroll_down(1)
            elif self.cy > 0:
                self.cy -= 1
        elif c == 'c':
            self.reset()
        elif c == '=':
            self.app_keypad = True
        elif c == '>':
            self.app_keypad = False
        elif c == 'H':
            self.tabs.add(self.cx)

    def _osc(self, s):
        if s.startswith('\x00'):
            return
        num, _, rest = s.partition(';')
        if num in ('0', '2'):
            self.title = rest
        elif num == '4':
            parts = rest.split(';')
            for j in range(0, len(parts) - 1, 2):
                try:
                    idx = int(parts[j])
                    spec = parts[j + 1]
                    if spec.startswith('rgb:'):
                        r, g, b = (int(x[:2], 16) for x in spec[4:].split('/'))
                        self.palette[idx] = (r, g, b)
                except (ValueError, IndexError):
                    pass
        elif num == '104':
            self.palette = {}

    def _params(self, seq, default=0):
        out = []
        for p in seq.split(';'):
            p = p.split(':')[0]
            out.append(int(p) if p.isdigit() else default)
        return out

    def _csi(self, seq, f):
        priv = ''
        if seq and seq[0] in '?>=<':
            priv, seq = seq[0], seq[1:]
        inter = ''
        while seq and seq[-1] in ' !"$\'':
            inter = seq[-1] + inter
            seq = seq[:-1]
        if f == 'm':
            if priv:
                return
            self._sgr(self._params(seq) if seq else [0])
            return
        p = self._params(seq) if seq else []

        def arg(i=0, d=1):
            v = p[i] if len(p) > i else 0
            return v if v else d

        cols, rows = self.cols, self.rows
        if priv == '?':
            if f in 'hl':
                for v in p:
                    self._mode(v, f == 'h')
            return
        if priv and priv in '>=<':
            if f == 'c':
                self.reply('\x1b[>0;0;0c')
            return
        if inter:
            if inter == ' ' and f == 'q':
                return    # cursor style
            if inter == '!' and f == 'p':
                self.reset()
            return
        self.wrapnext = False
        if f == 'A':
            self.cy = max(self.top if self.cy >= self.top else 0, self.cy - arg())
        elif f == 'B' or f == 'e':
            self.cy = min(self.bottom if self.cy <= self.bottom else rows - 1, self.cy + arg())
        elif f == 'C' or f == 'a':
            self.cx = min(cols - 1, self.cx + arg())
        elif f == 'D':
            self.cx = max(0, self.cx - arg())
        elif f == 'E':
            self.cy = min(rows - 1, self.cy + arg())
            self.cx = 0
        elif f == 'F':
            self.cy = max(0, self.cy - arg())
            self.cx = 0
        elif f == 'G' or f == '`':
            self.cx = max(0, min(cols - 1, arg() - 1))
        elif f in 'Hf':
            y = arg(0) - 1
            x = arg(1) - 1
            if self.origin:
                y += self.top
            self.cy = max(0, min(rows - 1, y))
            self.cx = max(0, min(cols - 1, x))
        elif f == 'd':
            self.cy = max(0, min(rows - 1, arg() - 1 + (self.top if self.origin else 0)))
        elif f == 'J':
            m = p[0] if p else 0
            if m == 0:
                self._erase_line(self.cy, self.cx, cols)
                for y in range(self.cy + 1, rows):
                    self.lines[y] = self._blank_line()
            elif m == 1:
                for y in range(0, self.cy):
                    self.lines[y] = self._blank_line()
                self._erase_line(self.cy, 0, self.cx + 1)
            elif m in (2, 3):
                for y in range(rows):
                    self.lines[y] = self._blank_line()
                if m == 3:
                    self.history = []
        elif f == 'K':
            m = p[0] if p else 0
            if m == 0:
                self._erase_line(self.cy, self.cx, cols)
            elif m == 1:
                self._erase_line(self.cy, 0, self.cx + 1)
            else:
                self._erase_line(self.cy, 0, cols)
        elif f == 'X':
            self._erase_line(self.cy, self.cx, min(cols, self.cx + arg()))
        elif f == 'L':
            if self.top <= self.cy <= self.bottom:
                for _ in range(min(arg(), self.bottom - self.cy + 1)):
                    self.lines.pop(self.bottom)
                    self.lines.insert(self.cy, self._blank_line())
                self.cx = 0
        elif f == 'M':
            if self.top <= self.cy <= self.bottom:
                for _ in range(min(arg(), self.bottom - self.cy + 1)):
                    self.lines.pop(self.cy)
                    self.lines.insert(self.bottom, self._blank_line())
                self.cx = 0
        elif f == 'P':
            n = min(arg(), cols - self.cx)
            ln = self.lines[self.cy]
            del ln[0][self.cx:self.cx + n]
            del ln[1][self.cx:self.cx + n]
            ln[0].extend([' '] * n)
            ln[1].extend([self._erase_attr()] * n)
        elif f == '@':
            n = min(arg(), cols - self.cx)
            ln = self.lines[self.cy]
            ln[0][self.cx:self.cx] = [' '] * n
            ln[1][self.cx:self.cx] = [self._erase_attr()] * n
            del ln[0][cols:]
            del ln[1][cols:]
        elif f == 'S':
            self.scroll_up(arg())
        elif f == 'T':
            self.scroll_down(arg())
        elif f == 'r':
            t = arg(0) - 1
            b = (p[1] if len(p) > 1 and p[1] else rows) - 1
            if 0 <= t < b < rows:
                self.top, self.bottom = t, b
            else:
                self.top, self.bottom = 0, rows - 1
            self.cx, self.cy = 0, self.top if self.origin else 0
        elif f == 's':
            self.saved = (self.cx, self.cy, self.attr, list(self.g), self.gl)
        elif f == 'u':
            self.cx, self.cy, self.attr, g, self.gl = self.saved
            self.g = list(g)
        elif f == 'n':
            if p and p[0] == 6:
                self.reply('\x1b[%d;%dR' % (self.cy + 1, self.cx + 1))
            elif p and p[0] == 5:
                self.reply('\x1b[0n')
        elif f == 'c':
            self.reply('\x1b[?62;22c')
        elif f == 'g':
            m = p[0] if p else 0
            if m == 0:
                self.tabs.discard(self.cx)
            elif m == 3:
                self.tabs = set()
        elif f == 'h' or f == 'l':
            for v in p:
                if v == 4:
                    self.insert = f == 'h'
        elif f == 'b':
            pass
        elif f == 't':
            pass

    def _erase_line(self, y, x0, x1):
        if x1 <= x0:
            return
        ln = self.lines[y]
        ln[0][x0:x1] = [' '] * (x1 - x0)
        ln[1][x0:x1] = [self._erase_attr()] * (x1 - x0)

    def _mode(self, v, on):
        if v == 1:
            self.app_cursor = on
        elif v == 6:
            self.origin = on
        elif v == 7:
            self.autowrap = on
        elif v == 25:
            self.cursor_visible = on
        elif v in (9, 1000, 1002, 1003):
            self.mouse_mode = v if on else 0
        elif v == 1006:
            self.mouse_sgr = on
        elif v == 2004:
            self.bracketed_paste = on
        elif v in (47, 1047, 1049):
            if on and self.alt_lines is None:
                if v == 1049:
                    self.saved = (self.cx, self.cy, self.attr, list(self.g), self.gl)
                self.alt_lines = self.lines
                self.lines = [[[' '] * self.cols, [DEFAULT_ATTR] * self.cols] for _ in range(self.rows)]
            elif not on and self.alt_lines is not None:
                self.lines = self.alt_lines
                self.alt_lines = None
                if v == 1049:
                    self.cx, self.cy, self.attr, g, self.gl = self.saved
                    self.g = list(g)
            self.top, self.bottom = 0, self.rows - 1

    def _sgr(self, p):
        a = self.attr
        fg, bg, fl = a & 31, (a >> 5) & 31, a >> 10
        i = 0
        n = len(p)
        while i < n:
            v = p[i]
            if v == 0:
                fg, bg, fl = DEF, DEF, 0
            elif v == 1:
                fl |= F_BOLD
            elif v == 2:
                fl |= F_DIM
            elif v == 4:
                fl |= F_UL
            elif v == 7:
                fl |= F_REV
            elif v == 8:
                fl |= F_INV
            elif v == 21 or v == 22:
                fl &= ~(F_BOLD | F_DIM)
            elif v == 24:
                fl &= ~F_UL
            elif v == 27:
                fl &= ~F_REV
            elif v == 28:
                fl &= ~F_INV
            elif 30 <= v <= 37:
                fg = v - 30
            elif v == 39:
                fg = DEF
            elif 40 <= v <= 47:
                bg = v - 40
            elif v == 49:
                bg = DEF
            elif 90 <= v <= 97:
                fg = v - 82
            elif 100 <= v <= 107:
                bg = v - 92
            elif v in (38, 48) and i + 1 < n:
                if p[i + 1] == 5 and i + 2 < n:
                    c = xterm256_to16(p[i + 2])
                    i += 2
                elif p[i + 1] == 2 and i + 4 < n:
                    if self.rgb_palette:
                        c = min(range(16), key=lambda k: sum((p[i + 2 + j] - self.rgb_palette[k][j]) ** 2
                                                             for j in range(3)))
                    else:
                        c = rgb_to16(p[i + 2], p[i + 3], p[i + 4])
                    i += 4
                else:
                    c = DEF
                    i += 1
                if v == 38:
                    fg = c
                else:
                    bg = c
            i += 1
        self.attr = fg | (bg << 5) | (fl << 10)

    # ------------------------------------------------------------ input encoding
    def encode_key(self, key, char=None):
        """Translate a monocell key name into bytes for the child program."""
        if char:
            return char
        k = key
        alt = ''
        mods = 1
        if k.startswith('alt+') and len(k) > 4:
            base = k[4:]
            if len(base) == 1:
                return '\x1b' + base
        parts = k.split('+')
        base = parts[-1]
        mset = set(parts[:-1])
        if 'shift' in mset:
            mods += 1
        if 'alt' in mset:
            mods += 2
        if 'ctrl' in mset:
            mods += 4
        if base in ('up', 'down', 'right', 'left', 'home', 'end'):
            code = {'up': 'A', 'down': 'B', 'right': 'C', 'left': 'D', 'home': 'H', 'end': 'F'}[base]
            if mods > 1:
                return '\x1b[1;%d%s' % (mods, code)
            return ('\x1bO' if self.app_cursor else '\x1b[') + code
        tilde = {'insert': 2, 'delete': 3, 'pgup': 5, 'pgdn': 6, 'f5': 15, 'f6': 17, 'f7': 18, 'f8': 19,
                 'f9': 20, 'f10': 21, 'f11': 23, 'f12': 24}
        if base in tilde:
            return '\x1b[%d%s~' % (tilde[base], ';%d' % mods if mods > 1 else '')
        if base in ('f1', 'f2', 'f3', 'f4'):
            c = 'PQRS'[int(base[1]) - 1]
            return '\x1b[1;%d%s' % (mods, c) if mods > 1 else '\x1bO' + c
        if base == 'enter':
            return ('\x1b' if 'alt' in mset else '') + '\r'
        if base == 'tab':
            return '\x1b[Z' if 'shift' in mset else '\t'
        if base == 'backspace':
            return ('\x1b' if 'alt' in mset else '') + ('\x08' if 'ctrl' in mset else '\x7f')
        if base == 'esc':
            return '\x1b'
        if base == 'space' and 'ctrl' in mset:
            return '\x00'
        if 'ctrl' in mset and len(base) == 1:
            c = base.lower()
            if 'a' <= c <= 'z':
                return ('\x1b' if 'alt' in mset else '') + chr(ord(c) - 96)
            m = {'\\': '\x1c', ']': '\x1d', '^': '\x1e', '_': '\x1f', '@': '\x00', '[': '\x1b'}
            if c in m:
                return m[c]
        if len(base) == 1:
            return ('\x1b' if 'alt' in mset else '') + base
        return ''

    def encode_mouse(self, kind, x, y, button=0):
        if not self.mouse_mode:
            return ''
        if kind in ('move',) and self.mouse_mode != 1003:
            return ''
        if kind == 'drag' and self.mouse_mode not in (1002, 1003):
            return ''
        if kind in ('wheelup', 'wheeldown'):
            b = 64 if kind == 'wheelup' else 65
        elif kind == 'move':
            b = 35
        elif kind == 'drag':
            b = 32 + button
        else:
            b = button
        if self.mouse_sgr:
            return '\x1b[<%d;%d;%d%s' % (b, x + 1, y + 1, 'm' if kind == 'up' else 'M')
        if kind == 'up':
            b = 3
        if x > 222 or y > 222:
            return ''
        return '\x1b[M%s%s%s' % (chr(32 + b), chr(33 + x), chr(33 + y))


_X256 = None


def xterm256_to16(n):
    if n < 16:
        return n
    global _X256
    if _X256 is None:
        _X256 = {}
    r = _X256.get(n)
    if r is None:
        if n < 232:
            n2 = n - 16
            rgb = [(n2 // 36) % 6, (n2 // 6) % 6, n2 % 6]
            rgb = [0 if c == 0 else 55 + c * 40 for c in rgb]
        else:
            v = 8 + (n - 232) * 10
            rgb = [v, v, v]
        r = rgb_to16(*rgb)
        _X256[n] = r
    return r


ANSI_RGB = [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205), (0, 205, 205),
            (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0), (92, 92, 255),
            (255, 0, 255), (0, 255, 255), (255, 255, 255)]


def rgb_to16(r, g, b):
    best, bd = 0, 1 << 30
    for i, (pr, pg, pb) in enumerate(ANSI_RGB):
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < bd:
            best, bd = i, d
    return best
