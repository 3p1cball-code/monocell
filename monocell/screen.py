"""Cell buffer + clipped canvas views.

An attribute is a small int:  fg | bg << 4 | flags << 8
fg/bg are indices into the 16-colour palette.
"""
from .util import clean

BOLD = 1
UL = 2


def A(fg, bg, fl=0):
    return fg | (bg << 4) | (fl << 8)


def fg_of(a):
    return a & 15


def bg_of(a):
    return (a >> 4) & 15


class Buffer:
    __slots__ = ('w', 'h', 'ch', 'at')

    def __init__(self, w, h, attr=0):
        self.w = w
        self.h = h
        self.ch = [[' '] * w for _ in range(h)]
        self.at = [[attr] * w for _ in range(h)]

    def clear(self, attr):
        w = self.w
        for y in range(self.h):
            self.ch[y][:] = ' ' * w
            self.at[y][:] = [attr] * w


class Canvas:
    """A view into a Buffer: origin (ox, oy), size w x h, clip rect in absolute coords."""
    __slots__ = ('buf', 'ox', 'oy', 'w', 'h', 'x0', 'y0', 'x1', 'y1')

    def __init__(self, buf, ox=0, oy=0, w=None, h=None, clip=None):
        self.buf = buf
        self.ox = ox
        self.oy = oy
        self.w = buf.w if w is None else w
        self.h = buf.h if h is None else h
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + self.w, buf.w), min(oy + self.h, buf.h)
        if clip:
            x0, y0 = max(x0, clip[0]), max(y0, clip[1])
            x1, y1 = min(x1, clip[2]), min(y1, clip[3])
        self.x0, self.y0, self.x1, self.y1 = x0, y0, max(x0, x1), max(y0, y1)

    def sub(self, x, y, w, h):
        return Canvas(self.buf, self.ox + x, self.oy + y, w, h, (self.x0, self.y0, self.x1, self.y1))

    def put(self, x, y, s, attr):
        ay = self.oy + y
        if ay < self.y0 or ay >= self.y1 or not s:
            return
        if not (s.isascii() and s.isprintable()):
            s = clean(s)
        ax = self.ox + x
        if ax < self.x0:
            s = s[self.x0 - ax:]
            ax = self.x0
        n = len(s)
        if ax + n > self.x1:
            n = self.x1 - ax
            s = s[:n]
        if n <= 0:
            return
        self.buf.ch[ay][ax:ax + n] = s
        self.buf.at[ay][ax:ax + n] = [attr] * n

    def putc(self, x, y, c, attr):
        """Single char, no sanitising (caller guarantees ASCII)."""
        ax, ay = self.ox + x, self.oy + y
        if self.x0 <= ax < self.x1 and self.y0 <= ay < self.y1:
            self.buf.ch[ay][ax] = c
            self.buf.at[ay][ax] = attr

    def fill(self, x, y, w, h, attr, ch=' '):
        ax0 = max(self.ox + x, self.x0)
        ax1 = min(self.ox + x + w, self.x1)
        ay0 = max(self.oy + y, self.y0)
        ay1 = min(self.oy + y + h, self.y1)
        n = ax1 - ax0
        if n <= 0:
            return
        cs = ch * n
        at = [attr] * n
        for ay in range(ay0, ay1):
            self.buf.ch[ay][ax0:ax1] = cs
            self.buf.at[ay][ax0:ax1] = at

    def clear(self, attr):
        self.fill(0, 0, self.w, self.h, attr)

    def recolor(self, x, y, w, h, fn):
        """Apply fn(attr)->attr to a region (used for shadows/highlights)."""
        ax0 = max(self.ox + x, self.x0)
        ax1 = min(self.ox + x + w, self.x1)
        ay0 = max(self.oy + y, self.y0)
        ay1 = min(self.oy + y + h, self.y1)
        if ax1 <= ax0:
            return
        for ay in range(ay0, ay1):
            row = self.buf.at[ay]
            row[ax0:ax1] = [fn(a) for a in row[ax0:ax1]]

    def set_attr(self, x, y, w, h, attr):
        ax0 = max(self.ox + x, self.x0)
        ax1 = min(self.ox + x + w, self.x1)
        ay0 = max(self.oy + y, self.y0)
        ay1 = min(self.oy + y + h, self.y1)
        n = ax1 - ax0
        if n <= 0:
            return
        for ay in range(ay0, ay1):
            self.buf.at[ay][ax0:ax1] = [attr] * n

    def hline(self, x, y, w, attr, ch='-'):
        self.fill(x, y, w, 1, attr, ch)

    def vline(self, x, y, h, attr, ch='|'):
        self.fill(x, y, 1, h, attr, ch)

    def box(self, x, y, w, h, attr, title=None, tattr=None):
        if w < 2 or h < 2:
            return
        self.put(x, y, '+' + '-' * (w - 2) + '+', attr)
        self.put(x, y + h - 1, '+' + '-' * (w - 2) + '+', attr)
        for yy in range(y + 1, y + h - 1):
            self.putc(x, yy, '|', attr)
            self.putc(x + w - 1, yy, '|', attr)
        if title:
            self.put(x + 2, y, ' ' + title[:max(0, w - 6)] + ' ', tattr if tattr is not None else attr)

    def blit_cells(self, x, y, rows):
        """rows: list of (chars:str, attrs:list) — e.g. a pre-rendered image."""
        for i, (cs, ats) in enumerate(rows):
            ay = self.oy + y + i
            if ay < self.y0 or ay >= self.y1:
                continue
            ax = self.ox + x
            a, b = 0, len(cs)
            if ax < self.x0:
                a = self.x0 - ax
            if ax + b > self.x1:
                b = self.x1 - ax
            if b <= a:
                continue
            self.buf.ch[ay][ax + a:ax + b] = cs[a:b]
            self.buf.at[ay][ax + a:ax + b] = ats[a:b]
