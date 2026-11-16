"""Paint: ASCII-art drawing - freehand, lines, rectangles, ellipses, fill, text.

The document model (PaintDoc) and the shape rasterisers are independent of the
window system so the tests can drive them without a terminal.

Pen pressure: on the Linux console Monocell reads the touchscreen and the pen
of a convertible (e.g. ThinkPad X1 Yoga, Wacom AES) straight from evdev while
Paint has the focus.  The devices are grabbed (EVIOCGRAB) so mousedev does not
turn the absolute touch into a wandering relative pointer as well.  The pen's
pressure picks the density of the character (` .:-=+*#%@`) and optionally the
brush size; the eraser end of the pen erases.  In a terminal emulator the
normal mouse works, without pressure.
"""
import fcntl
import json
import os
import re
import struct

from ..app import App
from ..util import fit, log
from ..widgets import MenuItem

RAMP = ' .:-=+*#%@'
CHARS = '#*@%&+=-:.oO0xX/\\|_~^"\'`,;!?<>()[]{}$'
TOOLS = (('pen', 'P', 'Pen'), ('line', 'L', 'Line'), ('rect', 'R', 'Rect'),
         ('oval', 'O', 'Oval'), ('fill', 'F', 'Fill'), ('erase', 'E', 'Erase'),
         ('text', 'T', 'Text'), ('pick', 'I', 'Pick'))
MODES = ('char', 'ramp', 'smart')
MAX_UNDO = 60
EXT = '.mcpaint'


# --- geometry ----------------------------------------------------------
def line_cells(x0, y0, x1, y1):
    """Bresenham, both ends included."""
    pts = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return pts
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def slope_char(dx, dy):
    """ASCII stroke for a direction; cells are about twice as tall as wide."""
    ax, ay = abs(dx), abs(dy) * 2
    if ax == 0 and ay == 0:
        return '*'
    if ay * 2 < ax:
        return '-'
    if ax * 2 < ay:
        return '|'
    return '\\' if (dx > 0) == (dy > 0) else '/'


def rect_cells(x0, y0, x1, y1, filled=False):
    """[(x, y, kind)] with kind corner/h/v/in."""
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    out = []
    for y in range(ya, yb + 1):
        for x in range(xa, xb + 1):
            edge_x = x in (xa, xb)
            edge_y = y in (ya, yb)
            if edge_x and edge_y:
                out.append((x, y, 'corner'))
            elif edge_y:
                out.append((x, y, 'h'))
            elif edge_x:
                out.append((x, y, 'v'))
            elif filled:
                out.append((x, y, 'in'))
    return out


def oval_cells(x0, y0, x1, y1, filled=False):
    """[(x, y, char_for_smart_mode)] of the ellipse in the bounding box."""
    import math
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    cx, cy = (xa + xb) / 2.0, (ya + yb) / 2.0
    rx, ry = (xb - xa) / 2.0, (yb - ya) / 2.0
    if rx < 0.5 or ry < 0.5:
        return [(x, y, slope_char(xb - xa, yb - ya)) for x, y in line_cells(xa, ya, xb, yb)]
    seen = {}
    if filled:
        for y in range(ya, yb + 1):
            t = (y - cy) / ry
            if abs(t) > 1:
                continue
            half = rx * math.sqrt(max(0.0, 1 - t * t))
            for x in range(int(round(cx - half)), int(round(cx + half)) + 1):
                seen[(x, y)] = '#'
    n = max(24, int(6 * (rx + ry)))
    for i in range(n):
        a = 2 * math.pi * i / n
        x = int(round(cx + rx * math.cos(a)))
        y = int(round(cy + ry * math.sin(a)))
        # tangent direction
        seen[(x, y)] = slope_char(-rx * math.sin(a), ry * math.cos(a))
    return [(x, y, c) for (x, y), c in seen.items()]


def stamp_offsets(size):
    """Brush footprint: [(dx, dy, falloff 0..1)], 1 = centre.  Width doubles
    because a cell is about twice as tall as it is wide."""
    if size <= 1:
        return [(0, 0, 1.0)]
    r = size - 1
    out = []
    for dy in range(-r, r + 1):
        for dx in range(-2 * r, 2 * r + 1):
            d = ((dx / 2.0) ** 2 + dy * dy) ** 0.5
            if d <= r + 0.35:
                out.append((dx, dy, max(0.0, 1.0 - d / (r + 1.0))))
    return out


def ramp_char(level):
    """level 0..1 -> density character (never blank for level > 0)."""
    if level <= 0:
        return ' '
    i = int(round(level * (len(RAMP) - 1)))
    return RAMP[max(1, min(len(RAMP) - 1, i))]


def ramp_level(ch):
    i = RAMP.find(ch)
    return i if i > 0 else (0 if ch == ' ' else None)


# --- document ----------------------------------------------------------
class PaintDoc:
    """w x h cells of (char, fg, bg); bg -1 = canvas background."""

    def __init__(self, w=80, h=30, fg=15, bg=-1):
        self.w, self.h = max(1, int(w)), max(1, int(h))
        self.fg, self.bg = fg, bg
        self.ch = [[' '] * self.w for _ in range(self.h)]
        self.cf = [[fg] * self.w for _ in range(self.h)]
        self.cb = [[-1] * self.w for _ in range(self.h)]
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False

    # snapshots
    def snap(self):
        return ([r[:] for r in self.ch], [r[:] for r in self.cf], [r[:] for r in self.cb],
                self.w, self.h)

    def restore(self, s):
        ch, cf, cb, self.w, self.h = s
        self.ch = [r[:] for r in ch]
        self.cf = [r[:] for r in cf]
        self.cb = [r[:] for r in cb]

    def checkpoint(self):
        self.undo_stack.append(self.snap())
        del self.undo_stack[:-MAX_UNDO]
        self.redo_stack = []
        self.dirty = True

    def undo(self):
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.snap())
        self.restore(self.undo_stack.pop())
        self.dirty = True
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.snap())
        self.restore(self.redo_stack.pop())
        self.dirty = True
        return True

    # cells
    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def get(self, x, y):
        return self.ch[y][x], self.cf[y][x], self.cb[y][x]

    def set(self, x, y, ch, fg, bg=None):
        if not self.inside(x, y):
            return False
        self.ch[y][x] = ch
        self.cf[y][x] = fg
        if bg is not None:
            self.cb[y][x] = bg
        return True

    def clear(self):
        for y in range(self.h):
            self.ch[y] = [' '] * self.w
            self.cf[y] = [self.fg] * self.w
            self.cb[y] = [-1] * self.w

    def resize(self, w, h):
        w, h = max(1, int(w)), max(1, int(h))
        for grid, blank in ((self.ch, ' '), (self.cf, self.fg), (self.cb, -1)):
            for r in grid:
                r[:] = (r + [blank] * w)[:w]
            del grid[h:]
            while len(grid) < h:
                grid.append([blank] * w)
        self.w, self.h = w, h

    def flood(self, x, y, ch, fg, bg=None):
        if not self.inside(x, y):
            return 0
        target = self.get(x, y)
        new = (ch, fg, target[2] if bg is None else bg)
        if target == new:
            return 0
        n, todo = 0, [(x, y)]
        while todo:
            px, py = todo.pop()
            if not self.inside(px, py) or self.get(px, py) != target:
                continue
            self.set(px, py, *new)
            n += 1
            todo.extend(((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)))
        return n

    def bbox(self):
        xs, ys = [], []
        for y in range(self.h):
            for x in range(self.w):
                if self.ch[y][x] != ' ' or self.cb[y][x] >= 0:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    # files
    def to_text(self):
        return '\n'.join(''.join(r).rstrip() for r in self.ch).rstrip('\n') + '\n'

    def to_json(self):
        hexs = '0123456789abcdef'
        return json.dumps({
            'format': 'monocell-paint', 'version': 1, 'w': self.w, 'h': self.h,
            'fg': self.fg, 'rows': [''.join(r) for r in self.ch],
            'fgs': [''.join(hexs[c & 15] for c in r) for r in self.cf],
            'bgs': [''.join('.' if c < 0 else hexs[c & 15] for c in r) for r in self.cb],
        }, ensure_ascii=False, indent=0)

    def to_ansi(self, rgb, canvas_bg):
        """24-bit ANSI with the theme's actual colours."""
        out = []
        for y in range(self.h):
            row = self.ch[y]
            end = len(''.join(row).rstrip())
            for x in range(self.w):
                if self.cb[y][x] >= 0:
                    end = max(end, x + 1)
            last = None
            parts = []
            for x in range(end):
                f = rgb[self.cf[y][x] & 15]
                b = rgb[(self.cb[y][x] if self.cb[y][x] >= 0 else canvas_bg) & 15]
                if (f, b) != last:
                    parts.append('\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm' % (f + b))
                    last = (f, b)
                parts.append(row[x])
            out.append(''.join(parts) + ('\x1b[0m' if parts else ''))
        while out and not out[-1]:
            out.pop()
        return '\n'.join(out) + '\n'

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)
        rows = d.get('rows') or ['']
        w = int(d.get('w') or max(len(r) for r in rows))
        h = int(d.get('h') or len(rows))
        doc = cls(w, h, int(d.get('fg', 15)))
        for y in range(h):
            r = rows[y] if y < len(rows) else ''
            fgs = (d.get('fgs') or [])[y] if y < len(d.get('fgs') or []) else ''
            bgs = (d.get('bgs') or [])[y] if y < len(d.get('bgs') or []) else ''
            for x in range(w):
                if x < len(r):
                    doc.ch[y][x] = r[x]
                if x < len(fgs):
                    doc.cf[y][x] = int(fgs[x], 16)
                if x < len(bgs) and bgs[x] != '.':
                    doc.cb[y][x] = int(bgs[x], 16)
        return doc

    @classmethod
    def from_text(cls, text, fg=15, min_w=1, min_h=1):
        text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text).expandtabs(8)
        lines = text.rstrip('\n').split('\n') if text.strip('\n') else ['']
        w = max(min_w, max(len(l) for l in lines))
        doc = cls(w, max(min_h, len(lines)), fg)
        for y, l in enumerate(lines):
            for x, c in enumerate(l):
                doc.ch[y][x] = c if c.isprintable() else ' '
        return doc


class Brush:
    """What a tool lays down: character, colours, size, mode."""

    def __init__(self):
        self.char = '#'
        self.fg = 15
        self.bg = None          # None = leave background, int = paint background too
        self.size = 1
        self.mode = 'char'      # char | ramp | smart
        self.level = 0.6        # density without pressure (ramp mode)
        self.press_size = False

    def cell(self, pressure=None, falloff=1.0, direction=None):
        """Character for one cell of a stroke."""
        if self.mode == 'ramp':
            lv = self.level if pressure is None else pressure
            return ramp_char(lv * falloff)
        if self.mode == 'smart' and direction is not None:
            return slope_char(*direction)
        return self.char

    def eff_size(self, pressure=None):
        if self.press_size and pressure is not None and self.size > 1:
            return max(1, int(round(1 + pressure * (self.size - 1))))
        return self.size


def dab(doc, brush, x, y, pressure=None, direction=None, erase=False):
    """One brush stamp; ramp strokes only ever darken (shading builds up)."""
    n = 0
    for dx, dy, fo in stamp_offsets(brush.eff_size(pressure)):
        px, py = x + dx, y + dy
        if not doc.inside(px, py):
            continue
        if erase:
            doc.set(px, py, ' ', doc.fg, -1)
            n += 1
            continue
        c = brush.cell(pressure, fo, direction)
        if brush.mode == 'ramp':
            if c == ' ':
                continue
            old = ramp_level(doc.ch[py][px])
            if old is not None and old >= RAMP.find(c) and doc.cf[py][px] == brush.fg:
                continue
        doc.set(px, py, c, brush.fg, brush.bg)
        n += 1
    return n


def shape_cells(tool, brush, x0, y0, x1, y1, filled=False):
    """[(x, y, char)] for line/rect/oval with the brush's character rules."""
    out = []
    if tool == 'line':
        d = (x1 - x0, y1 - y0)
        for x, y in line_cells(x0, y0, x1, y1):
            out.append((x, y, brush.cell(None, 1.0, d)))
    elif tool == 'rect':
        for x, y, kind in rect_cells(x0, y0, x1, y1, filled):
            if brush.mode == 'smart':
                c = {'corner': '+', 'h': '-', 'v': '|'}.get(kind, brush.char)
            else:
                c = brush.cell()
            out.append((x, y, c))
    elif tool == 'oval':
        for x, y, sc in oval_cells(x0, y0, x1, y1, filled):
            out.append((x, y, sc if brush.mode == 'smart' and sc != '#' else brush.cell()))
    return out


# --- pen / touch input (Linux console) ----------------------------------
EV = struct.Struct('llHHi')
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0, 1, 24
ABS_MT_X, ABS_MT_Y = 53, 54
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH = 320, 321, 330
BTN_STYLUS, BTN_STYLUS2 = 331, 332
INPUT_PROP_DIRECT = 1
EVIOCGRAB = 0x40044590


def EVIOCGABS(code):
    return 0x80184540 + code          # _IOR('E', 0x40 + code, 6 * int)


def _bits(path):
    try:
        with open(path) as f:
            words = f.read().split()
        return int(''.join(w.rjust(16, '0') for w in words), 16)
    except (OSError, ValueError):
        return 0


def direct_devices():
    """[(path, name, has_pressure)] touchscreens and display pens (INPUT_PROP_DIRECT)."""
    out = []
    try:
        names = sorted((n for n in os.listdir('/dev/input') if n.startswith('event')),
                       key=lambda n: int(n[5:]) if n[5:].isdigit() else 0)
    except OSError:
        return out
    for ev in names:
        dev = os.path.join('/sys/class/input', ev, 'device')
        props = _bits(os.path.join(dev, 'properties'))
        absb = _bits(os.path.join(dev, 'capabilities/abs'))
        if not (props >> INPUT_PROP_DIRECT) & 1 or not (absb & 1 and absb & 2):
            continue
        try:
            with open(os.path.join(dev, 'name')) as f:
                name = f.read().strip()
        except OSError:
            name = ev
        out.append((os.path.join('/dev/input', ev), name, bool((absb >> ABS_PRESSURE) & 1)))
    return out


def screen_pixels():
    try:
        with open('/sys/class/graphics/fb0/virtual_size') as f:
            w, h = f.read().strip().split(',')
        return int(w), int(h)
    except (OSError, ValueError):
        return None


class PenState:
    """Collects evdev events of one device until SYN_REPORT."""

    def __init__(self, ranges, has_pressure):
        self.ranges = ranges            # code -> (min, max)
        self.has_pressure = has_pressure
        self.x = self.y = None
        self.pressure = 0
        self.touch = False
        self.rubber = False
        self.side = False
        self.prox = False

    def feed(self, typ, code, value):
        """True on SYN_REPORT."""
        if typ == EV_ABS:
            if code in (ABS_X, ABS_MT_X) and (code == ABS_X or self.x is None):
                self.x = value
            elif code in (ABS_Y, ABS_MT_Y) and (code == ABS_Y or self.y is None):
                self.y = value
            elif code == ABS_PRESSURE:
                self.pressure = value
        elif typ == EV_KEY:
            if code == BTN_TOUCH:
                self.touch = bool(value)
            elif code == BTN_TOOL_RUBBER:
                self.rubber = bool(value)
                self.prox = bool(value) or self.prox
            elif code == BTN_TOOL_PEN:
                self.prox = bool(value)
            elif code in (BTN_STYLUS, BTN_STYLUS2):
                self.side = bool(value)
        elif typ == EV_SYN and code == 0:
            return True
        return False

    def frac(self, code, v):
        lo, hi = self.ranges.get(code, (0, 0))
        if hi <= lo or v is None:
            return 0.0
        return max(0.0, min(1.0, (v - lo) / float(hi - lo)))

    def level(self):
        if not self.has_pressure:
            return None
        return self.frac(ABS_PRESSURE, self.pressure)


class PenInput:
    """Absolute pen/touch -> synthesised Mouse events at cell positions."""

    def __init__(self, app):
        self.app = app
        self.wm = app.wm
        self.fds = {}
        self.cell = None

    def open(self):
        if self.fds:
            return
        for path, name, press in direct_devices():
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log('paint: cannot open %s (%s): %s' % (path, name, e))
                continue
            ranges = {}
            for code in (ABS_X, ABS_Y, ABS_PRESSURE):
                try:
                    buf = fcntl.ioctl(fd, EVIOCGABS(code), b'\0' * 24)
                    _v, lo, hi = struct.unpack('6i', buf)[:3]
                    ranges[code] = (lo, hi)
                except OSError:
                    pass
            ranges[ABS_MT_X] = ranges.get(ABS_X, (0, 0))
            ranges[ABS_MT_Y] = ranges.get(ABS_Y, (0, 0))
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError as e:
                log('paint: grab %s failed: %s' % (name, e))
            self.fds[fd] = PenState(ranges, press)
            self.wm.loop.add_reader(fd, lambda fd=fd: self.read(fd))
            log('paint: pen/touch %s (%s)%s' % (path, name, ', pressure' if press else ''))

    def close(self):
        for fd, st in list(self.fds.items()):
            if st.touch and self.cell:
                self.emit('up', *self.cell)
            try:
                self.wm.loop.remove_reader(fd)
            except Exception:
                pass
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = {}
        self.app.ext_pressure = None
        self.app.ext_erase = False

    def to_cell(self, st):
        term = self.wm.term
        cols, rows = max(1, term.w), max(1, term.h)
        fx, fy = st.frac(ABS_X, st.x), st.frac(ABS_Y, st.y)
        px = screen_pixels()
        if px:
            cw, chh = max(1, px[0] // cols), max(1, px[1] // rows)
            cx, cy = int(fx * (px[0] - 1)) // cw, int(fy * (px[1] - 1)) // chh
        else:
            cx, cy = int(fx * cols), int(fy * rows)
        return min(cols - 1, max(0, cx)), min(rows - 1, max(0, cy))

    def read(self, fd):
        st = self.fds.get(fd)
        if st is None:
            return
        try:
            data = os.read(fd, EV.size * 64)
        except OSError:
            return
        for i in range(0, len(data) - EV.size + 1, EV.size):
            _s, _u, typ, code, value = EV.unpack_from(data, i)
            was = getattr(st, '_was', False)
            if st.feed(typ, code, value):
                self.frame(st, was)
                st._was = st.touch

    def frame(self, st, was):
        if st.x is None or st.y is None:
            return
        cell = self.to_cell(st)
        app = self.app
        app.ext_pressure = st.level()
        app.ext_erase = st.rubber
        mice = getattr(self.wm.term, 'mice', None)
        if mice is not None:          # the trackpoint continues from here
            mice.fx, mice.fy = float(cell[0]), float(cell[1])
        button = 2 if st.side else 0
        if st.touch and not was:
            self.emit('down', cell[0], cell[1], button)
        elif st.touch and was:
            if cell != self.cell or app.ext_pressure is not None:
                self.emit('drag', cell[0], cell[1], button)
        elif was and not st.touch:
            self.emit('up', cell[0], cell[1], button)
        elif cell != self.cell:
            self.emit('move', cell[0], cell[1])
        self.cell = cell

    def emit(self, kind, x, y, button=0):
        from ..term import Mouse
        try:
            self.wm.handle(Mouse(kind, x, y, button))
        except Exception as e:
            log('paint: pen event failed: %s' % e)
        self.wm.dirty = True


# --- app ---------------------------------------------------------------
class Paint(App):
    name = 'paint'
    title = 'Paint'
    icon = '[~]'
    size = (96, 32)
    min_size = (40, 12)
    preserve_colors = True
    shift_select = True

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.brush = Brush()
        try:
            self.brush.fg = int(wm.cfg.get('paint_fg', self.th.r.get('bright', 15)))
        except (TypeError, ValueError):
            pass
        try:
            self.brush.bg = int(wm.cfg.get('paint_bg'))
        except (TypeError, ValueError):
            self.brush.bg = None
        if self.brush.bg is not None and not 0 <= self.brush.bg < 16:
            self.brush.bg = None
        self.tool = 'pen'
        self.prev_tool = 'pen'
        self.filled = False
        self.path = None
        w, h = max(20, self.cw), max(5, self.ch - self.TOP - 1)
        self.doc = PaintDoc(w, h, self.brush.fg)
        self.vx = self.vy = 0
        self.drag_from = None
        self.drag_to = None
        self.last = None
        self.text_at = None
        self.text_x0 = 0
        self.ext_pressure = None
        self.ext_erase = False
        self.pointer = None
        self.stroke_erase = False
        self.pen = None
        self.pen_n = 0
        if getattr(getattr(wm, 'term', None), 'mice', None) is not None:
            self.pen = PenInput(self)
        if path:
            self.load(path)
        self.later(0, self._sync_pen)

    # -- pen grab follows focus ----------------------------------------
    def _sync_pen(self):
        if not self.pen:
            return
        if self.focused() and not self.win.closed:
            self.pen.open()
            self.pen_n = len(self.pen.fds)
        else:
            self.pen.close()
        self.invalidate()

    def on_focus(self, focused):
        if self.pen and not focused:
            self.pen.close()
        elif self.pen:
            self.pen.open()
            self.pen_n = len(self.pen.fds)

    def on_close(self):
        if self.doc.dirty and not getattr(self, '_force_close', False):
            def ans(r, _v=None):
                if r == 'Save':
                    self.save(then_close=True)
                elif r == 'Discard':
                    self._force_close = True
                    self.close()
            self.wm.dialog('Paint', 'Unsaved drawing. Save it?',
                           ('Save', 'Discard', 'Cancel'), ans, parent=self.win)
            return False
        if self.pen:
            self.pen.close()
        try:
            self.wm.cfg['paint_fg'] = self.brush.fg
            self.wm.cfg['paint_bg'] = '' if self.brush.bg is None else self.brush.bg
        except Exception:
            pass
        return True

    # -- layout --------------------------------------------------------
    TOP = 3

    def view(self):
        return 0, self.TOP, self.cw, max(1, self.ch - self.TOP - 1)

    def to_doc(self, x, y):
        vx, vy, vw, vh = self.view()
        return x - vx + self.vx, y - vy + self.vy

    def scroll(self, dx, dy):
        vw, vh = self.view()[2:]
        self.vx = max(0, min(max(0, self.doc.w - vw), self.vx + dx))
        self.vy = max(0, min(max(0, self.doc.h - vh), self.vy + dy))
        self.invalidate()

    def on_resize(self, w, h):
        vw, vh = max(1, w), max(1, h - self.TOP - 1)
        if not self.doc.dirty and not self.path and (vw > self.doc.w or vh > self.doc.h):
            self.doc.resize(max(self.doc.w, vw), max(self.doc.h, vh))
        self.scroll(0, 0)

    # -- painting ------------------------------------------------------
    def pressure(self):
        return self.ext_pressure

    def erasing(self):
        return self.tool == 'erase' or self.ext_erase or self.stroke_erase

    def stroke(self, x, y):
        p = self.pressure()
        if self.last is None:
            dab(self.doc, self.brush, x, y, p, None, self.erasing())
        else:
            lx, ly = self.last
            d = (x - lx, y - ly)
            pts = line_cells(lx, ly, x, y)
            for px, py in pts[1:] if len(pts) > 1 else pts:
                dab(self.doc, self.brush, px, py, p, d, self.erasing())
        self.last = (x, y)

    def commit_shape(self):
        if self.drag_from is None or self.drag_to is None:
            return
        (x0, y0), (x1, y1) = self.drag_from, self.drag_to
        if self.stroke_erase:
            for x, y, _c in shape_cells(self.tool, self.brush, x0, y0, x1, y1, self.filled):
                self.doc.set(x, y, ' ', self.doc.fg, -1)
            return
        for x, y, c in shape_cells(self.tool, self.brush, x0, y0, x1, y1, self.filled):
            self.doc.set(x, y, c, self.brush.fg, self.brush.bg)

    def pick(self, x, y):
        if not self.doc.inside(x, y):
            return
        c, f, b = self.doc.get(x, y)
        if c != ' ':
            self.brush.char = c
            if self.brush.mode == 'ramp' and ramp_level(c):
                self.brush.level = ramp_level(c) / (len(RAMP) - 1.0)
        self.brush.fg = f
        self.brush.bg = b if b >= 0 else None
        self.wm.toast("Picked '%s'" % c, 'accent', 1.5)
        self.set_tool(self.prev_tool if self.prev_tool != 'pick' else 'pen')

    def set_tool(self, t):
        if t != self.tool:
            self.end_text()
            if self.tool != 'pick':
                self.prev_tool = self.tool
            self.tool = t
        self.invalidate()

    def end_text(self):
        self.text_at = None

    def type_char(self, c):
        x, y = self.text_at
        if self.doc.inside(x, y):
            self.doc.set(x, y, c, self.brush.fg, self.brush.bg)
            self.doc.dirty = True
        self.text_at = (min(self.doc.w - 1, x + 1), y)

    # -- input ---------------------------------------------------------
    def on_mouse(self, ev):
        k = self.track_hover(ev)
        vx, vy, vw, vh = self.view()
        inside = vx <= ev.x < vx + vw and vy <= ev.y < vy + vh
        dx, dy = self.to_doc(ev.x, ev.y)
        self.pointer = (dx, dy) if inside else None
        if ev.kind in ('wheelup', 'wheeldown'):
            n = -3 if ev.kind == 'wheelup' else 3
            if 'shift' in ev.mods:
                self.scroll(n * 2, 0)
            else:
                self.scroll(0, n)
            return True
        if ev.kind == 'down':
            if k is not None and not inside:
                self.on_click(k, ev)
                self.invalidate()
                return True
            if not inside or ev.button == 1:
                return False
            self.stroke_erase = ev.button == 2
            self.drag_from = self.drag_to = (dx, dy)
            self.last = None
            t = self.tool
            if t == 'pick' or (ev.button == 2 and 'ctrl' in ev.mods):
                self.pick(dx, dy)
                self.drag_from = None
                return True
            if t == 'text':
                if self.doc.inside(dx, dy):
                    self.doc.checkpoint()
                    self.text_at = (dx, dy)
                    self.text_x0 = dx
                self.drag_from = None
                self.invalidate()
                return True
            self.doc.checkpoint()
            if t == 'fill':
                if self.stroke_erase:
                    self.doc.flood(dx, dy, ' ', self.doc.fg, -1)
                else:
                    self.doc.flood(dx, dy, self.brush.cell(self.pressure()),
                                   self.brush.fg, self.brush.bg)
                self.drag_from = None
            elif t in ('pen', 'erase'):
                self.stroke(dx, dy)
            self.invalidate()
            return True
        if ev.kind == 'drag' and self.drag_from is not None:
            self.drag_to = (dx, dy)
            if self.tool in ('pen', 'erase'):
                self.stroke(dx, dy)
            self.invalidate()
            return True
        if ev.kind == 'up' and self.drag_from is not None:
            self.drag_to = (dx, dy)
            if self.tool in ('line', 'rect', 'oval'):
                self.commit_shape()
            self.drag_from = self.drag_to = self.last = None
            self.stroke_erase = False
            self.invalidate()
            return True
        if ev.kind == 'move':
            self.invalidate()
        return inside

    def on_click(self, key, ev):
        kind, val = key
        if kind == 'tool':
            self.set_tool(val)
        elif kind == 'mode':
            self.cycle_mode()
        elif kind == 'char':
            self.choose_char(ev)
        elif kind == 'fg':
            self.brush.fg = val
        elif kind == 'bg':
            self.brush.bg = val
        elif kind == 'size':
            self.brush.size = max(1, min(9, self.brush.size + val))
        elif kind == 'filled':
            self.filled = not self.filled
        elif kind == 'psize':
            self.brush.press_size = not self.brush.press_size
        elif kind == 'level':
            self.brush.level = val
        elif kind == 'menu':
            self.file_menu(ev)
        elif kind == 'undo':
            self.doc.undo()
        elif kind == 'redo':
            self.doc.redo()

    def cycle_mode(self):
        i = MODES.index(self.brush.mode)
        self.brush.mode = MODES[(i + 1) % len(MODES)]
        self.invalidate()

    def cycle_bg(self, d):
        """Step through 0..15 and then 'no fill' (None)."""
        cur = -1 if self.brush.bg is None else self.brush.bg
        nxt = (cur + d + 1) % 17 - 1
        self.brush.bg = None if nxt < 0 else nxt
        self.invalidate()

    def choose_char(self, ev):
        M = MenuItem
        items = [M(c if c != ' ' else 'space', (lambda c=c: self._set_char(c))) for c in CHARS]
        items.append(None)
        items.append(M('other...', self._ask_char))
        self.wm.menu(items, ev.x + self.win.x + 1, ev.y + self.win.y + 2)

    def _set_char(self, c):
        self.brush.char = c
        if self.brush.mode != 'char':
            self.brush.mode = 'char'
        self.invalidate()

    def _ask_char(self):
        self.wm.prompt('Paint', 'Brush character:', self.brush.char,
                       lambda v: v and self._set_char(v[0]), parent=self.win)

    def file_menu(self, ev):
        M = MenuItem
        items = [M('New...', self.ask_new, 'Ctrl+N'),
                 M('Open...', self.ask_open, 'Ctrl+O'),
                 M('Save', self.save, 'Ctrl+S'),
                 M('Save as...', lambda: self.save(ask=True)),
                 None,
                 M('Export text (.txt)', lambda: self.export('txt')),
                 M('Export colour (.ans)', lambda: self.export('ans')),
                 None,
                 M('Canvas size...', self.ask_size),
                 M('Clear', self.clear),
                 None,
                 M('Help', self.help, 'F1')]
        self.wm.menu(items, ev.x + self.win.x + 1, ev.y + self.win.y + 2)

    def clear(self):
        self.doc.checkpoint()
        self.doc.clear()
        self.invalidate()

    def ask_new(self):
        vw, vh = self.view()[2:]
        self.wm.prompt('New drawing', 'Size (cols x rows):', '%dx%d' % (vw, vh),
                       lambda v: self._new(v), parent=self.win)

    def _parse_size(self, v):
        m = re.match(r'\s*(\d+)\s*[x* ,]\s*(\d+)', v or '')
        if not m:
            self.wm.toast('Size like 80x30', 'red')
            return None
        return max(1, min(1000, int(m.group(1)))), max(1, min(1000, int(m.group(2))))

    def _new(self, v):
        s = self._parse_size(v)
        if s:
            self.doc = PaintDoc(s[0], s[1], self.brush.fg)
            self.path = None
            self.vx = self.vy = 0
            self.set_title('Paint')
            self.invalidate()

    def ask_size(self):
        self.wm.prompt('Canvas size', 'Size (cols x rows):', '%dx%d' % (self.doc.w, self.doc.h),
                       self._resize_doc, parent=self.win)

    def _resize_doc(self, v):
        s = self._parse_size(v)
        if s:
            self.doc.checkpoint()
            self.doc.resize(*s)
            self.scroll(0, 0)

    def ask_open(self):
        self.wm.pick_file(self.load, mode='open', start=self._dir(), parent=self.win)

    def _dir(self):
        if self.path:
            return os.path.dirname(self.path)
        d = os.path.expanduser('~/Pictures')
        return d if os.path.isdir(d) else os.path.expanduser('~')

    def load(self, path):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            if text.lstrip().startswith('{'):
                doc = PaintDoc.from_json(text)
            else:
                vw, vh = self.view()[2:]
                doc = PaintDoc.from_text(text, self.brush.fg, vw, vh)
        except (OSError, ValueError) as e:
            self.wm.toast('Paint: %s' % e, 'red')
            return
        self.doc = doc
        self.path = path if path.endswith(EXT) else None
        self.vx = self.vy = 0
        self.set_title('Paint - ' + os.path.basename(path))
        self.invalidate()

    def save(self, ask=False, then_close=False):
        if self.path and not ask:
            self._write(self.path, then_close)
            return
        name = os.path.basename(self.path) if self.path else 'drawing' + EXT
        self.wm.pick_file(lambda p: self._write(p, then_close), mode='save',
                          start=self._dir(), name=name, parent=self.win)

    def _write(self, path, then_close=False):
        if not path.endswith(EXT):
            path += EXT
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.doc.to_json())
        except OSError as e:
            self.wm.toast('Paint: %s' % e, 'red')
            return
        self.path = path
        self.doc.dirty = False
        self.set_title('Paint - ' + os.path.basename(path))
        self.wm.toast('Saved ' + os.path.basename(path), 'green')
        if then_close:
            self.close()

    def export(self, kind):
        base = os.path.splitext(os.path.basename(self.path or 'drawing'))[0]
        if base.endswith('.mcpaint'):
            base = base[:-8]

        def write(p):
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    if kind == 'txt':
                        f.write(self.doc.to_text())
                    else:
                        f.write(self.doc.to_ansi(self.th.rgb, self.canvas_bg()))
            except OSError as e:
                self.wm.toast('Paint: %s' % e, 'red')
                return
            self.wm.toast('Exported ' + os.path.basename(p), 'green')
        self.wm.pick_file(write, mode='save', start=self._dir(),
                          name=base + '.' + kind, parent=self.win)

    def help(self):
        self.wm.dialog('Paint', '\n'.join((
            'P pen  L line  R rect  O oval  F fill  E erase  T text  I pick',
            'M mode: char / ramp (density, pen pressure) / smart (- | / \\ +)',
            'C character   1-9 0 density   [ ] size   Z size by pressure',
            'B filled shapes   Left/Right text colour   Alt+Left/Right background',
            'TXT chips = text colour, BG chips = background colour (- = none)',
            'right button erases   Ctrl+Z/Y undo/redo   Ctrl+S save   Ctrl+O open',
            'Wheel scrolls (Shift = sideways), arrows scroll the canvas',
            'Pen: pressure sets the density, the eraser end erases.')),
            ('OK',), lambda r, v=None: None, parent=self.win)

    def on_key(self, ev):
        k = ev.key or ''
        if self.text_at is not None:
            if k == 'esc':
                self.end_text()
            elif k == 'enter':
                self.text_at = (self.text_x0, min(self.doc.h - 1, self.text_at[1] + 1))
            elif k == 'backspace':
                x, y = self.text_at
                x = max(0, x - 1)
                self.doc.set(x, y, ' ', self.doc.fg)
                self.text_at = (x, y)
            elif k in ('left', 'right', 'up', 'down'):
                x, y = self.text_at
                x += {'left': -1, 'right': 1}.get(k, 0)
                y += {'up': -1, 'down': 1}.get(k, 0)
                if self.doc.inside(x, y):
                    self.text_at = (x, y)
            elif ev.char and ev.char.isprintable() and not k.startswith(('ctrl+', 'alt+')):
                self.type_char(ev.char)
            else:
                return self._global_key(k)
            self.invalidate()
            return True
        return self._global_key(k, ev)

    def _global_key(self, k, ev=None):
        tool_keys = {key.lower(): t for t, key, _n in TOOLS}
        if k == 'ctrl+z':
            self.doc.undo()
        elif k in ('ctrl+y', 'ctrl+shift+z'):
            self.doc.redo()
        elif k == 'ctrl+s':
            self.save()
        elif k == 'ctrl+o':
            self.ask_open()
        elif k == 'ctrl+n':
            self.ask_new()
        elif k == 'f1':
            self.help()
        elif ev is None:
            return False
        elif k in tool_keys:
            self.set_tool(tool_keys[k])
        elif k == 'm':
            self.cycle_mode()
        elif k == 'c':
            self._ask_char()
        elif k == 'b':
            self.filled = not self.filled
        elif k == 'z':
            self.brush.press_size = not self.brush.press_size
        elif k == '[':
            self.brush.size = max(1, self.brush.size - 1)
        elif k == ']':
            self.brush.size = min(9, self.brush.size + 1)
        elif len(k) == 1 and k.isdigit():
            self.brush.level = (int(k) or 10) / 10.0
            self.brush.mode = 'ramp'
        elif k in ('left', 'right') and 'shift' not in k:
            self.brush.fg = (self.brush.fg + (1 if k == 'right' else -1)) % 16
        elif k in ('alt+left', 'alt+right'):
            self.cycle_bg(1 if k == 'alt+right' else -1)
        elif k in ('up', 'down'):
            self.scroll(0, -2 if k == 'up' else 2)
        elif k in ('pgup', 'pageup', 'pgdn', 'pagedown'):
            self.scroll(0, -(self.view()[3]) if k.startswith(('pgu', 'pageu')) else self.view()[3])
        elif k in ('shift+left', 'shift+right'):
            self.scroll(-4 if k == 'shift+left' else 4, 0)
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.text_at is None:
            return
        for c in text:
            if c == '\n':
                self.text_at = (self.text_x0, min(self.doc.h - 1, self.text_at[1] + 1))
            elif c.isprintable():
                self.type_char(c)
        self.invalidate()

    # -- drawing -------------------------------------------------------
    def canvas_bg(self):
        r = self.th.r
        return r.get('field', r.get('win', 0))

    def draw(self, cv, focused):
        th = self.th
        self.zones = []
        w, h = cv.w, cv.h
        bar = th.c('text', 'win')
        cv.fill(0, 0, w, self.TOP, bar)
        # row 0: menu + tools + undo
        x = row = 0

        def button(label, key, active=False, attr=None):
            nonlocal x
            if x + len(label) > w:
                return
            a = attr if attr is not None else (
                th.c('on_accent', 'accent', 1) if active else
                th.c('bright', 'raised') if self.hover == key else th.c('text', 'raised'))
            cv.put(x, row, label, a)
            self.zone(x, row, len(label), 1, key)
            x += len(label) + 1

        button(' = ', ('menu', None))
        for t, key, name in TOOLS:
            lab = ' %s ' % (name if w >= 90 else key)
            button(lab, ('tool', t), self.tool == t)
        button(' < ', ('undo', None))
        button(' > ', ('redo', None))
        p = self.ext_pressure
        info = ('pen %3d%%' % int(p * 100)) if p is not None else (
            'pen ready' if self.pen_n else '')
        if info and x + len(info) <= w:
            cv.put(w - len(info), 0, info, th.c('dim', 'win'))
        # row 1: mode, char, size, density
        x, row = 0, 1
        b = self.brush
        bg_now = b.bg if b.bg is not None else self.canvas_bg()
        mode_lab = {'char': " '%s' " % b.char, 'ramp': ' ramp ', 'smart': ' smart '}[b.mode]
        button(' M ', ('mode', None))
        button(mode_lab, ('char', None), attr=th.c(b.fg, bg_now, 1))
        button(' - ', ('size', -1))
        if x + 6 <= w:
            cv.put(x, 1, 'size %d' % b.size, th.c('text', 'win'))
            x += 7
        button(' + ', ('size', 1))
        button(' Z ' if not b.press_size else '[Z]', ('psize', None), b.press_size)
        button(' B ' if not self.filled else '[B]', ('filled', None), self.filled)
        # density chips
        for i in range(1, len(RAMP)):
            lv = i / (len(RAMP) - 1.0)
            if x + 1 > w:
                break
            act = b.mode == 'ramp' and abs(b.level - lv) < 0.06
            cv.putc(x, 1, RAMP[i], th.c('on_accent', 'accent') if act else th.c('text', 'raised'))
            self.zone(x, 1, 1, 1, ('level', lv))
            x += 1

        # row 2: text colour (TXT) and background colour (BG), picked separately.
        # The TXT chips show the brush character in that colour on the canvas; the
        # BG chips are solid swatches of the colour the strokes will sit on.
        x, row = 0, 2
        sample = b.cell()
        if not sample.strip():
            sample = '#'
        cv.put(x, row, 'TXT', th.c('dim', 'win'))
        x += 4
        for i in range(16):
            if x + 2 > w:
                break
            sel = b.fg == i
            cv.put(x, row, '[' if sel else ' ', th.c('accent', 'win'))
            cv.put(x + 1, row, sample, th.c(i, bg_now, 1 if sel else 0))
            self.zone(x, row, 2, 1, ('fg', i))
            x += 2
        x += 1
        if x + 5 <= w:
            cv.put(x, row, 'BG', th.c('dim', 'win'))
            x += 3
            for i in range(16):
                if x + 2 > w:
                    break
                sel = b.bg == i
                cv.put(x, row, '[' if sel else ' ', th.c('accent', 'win'))
                cv.put(x + 1, row, ' ', th.c(i, i))
                self.zone(x, row, 2, 1, ('bg', i))
                x += 2
            if x + 2 <= w:
                sel = b.bg is None
                cv.put(x, row, '[' if sel else ' ', th.c('accent', 'win'))
                cv.put(x + 1, row, '-', th.c('dim', 'win'))
                self.zone(x, row, 2, 1, ('bg', None))

        # canvas
        vx, vy, vw, vh = self.view()
        doc = self.doc
        cbg = self.canvas_bg()
        outside = th.c('dim', 'win')
        preview = {}
        if self.drag_from is not None and self.drag_to is not None and \
                self.tool in ('line', 'rect', 'oval'):
            (x0, y0), (x1, y1) = self.drag_from, self.drag_to
            for px, py, c in shape_cells(self.tool, b, x0, y0, x1, y1, self.filled):
                preview[(px, py)] = ' ' if self.stroke_erase else c
        for ry in range(vh):
            dy = ry + self.vy
            if dy >= doc.h:
                cv.fill(vx, vy + ry, vw, 1, outside, ' ')
                continue
            cs, ats = [], []
            rch, rcf, rcb = doc.ch[dy], doc.cf[dy], doc.cb[dy]
            for rx in range(vw):
                dx = rx + self.vx
                if dx >= doc.w:
                    cs.append(' ')
                    ats.append(outside)
                    continue
                c = preview.get((dx, dy))
                bgc = rcb[dx] if rcb[dx] >= 0 else cbg
                if c is not None:
                    cs.append(c)
                    ats.append(th.c(b.fg, bgc))
                else:
                    cs.append(rch[dx])
                    ats.append(th.c(rcf[dx], bgc))
            cv.blit_cells(vx, vy + ry, [(''.join(cs), ats)])
        # text cursor / brush outline under the pointer
        if self.text_at is not None:
            tx, ty = self.text_at[0] - self.vx + vx, self.text_at[1] - self.vy + vy
            if vx <= tx < vx + vw and vy <= ty < vy + vh:
                c = doc.ch[self.text_at[1]][self.text_at[0]]
                cv.putc(tx, ty, c if c != ' ' else '_', th.c('on_accent', 'accent', 1))
        # status
        sy = h - 1
        if sy >= self.TOP:
            pos = ''
            if self.pointer and doc.inside(*self.pointer):
                pos = '%d,%d' % self.pointer
            name = os.path.basename(self.path) if self.path else 'untitled'
            hint = 'F1 help  = menu  right btn erases'
            left = '%s%s  %dx%d  %s' % (name, '*' if doc.dirty else '', doc.w, doc.h, pos)
            cv.fill(0, sy, w, 1, th.c('dim', 'win'))
            cv.put(0, sy, fit(left, w), th.c('dim', 'win'))
            if len(left) + len(hint) + 2 <= w:
                cv.put(w - len(hint), sy, hint, th.c('dim', 'win'))


APP = Paint
