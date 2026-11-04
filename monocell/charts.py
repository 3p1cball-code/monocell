"""Tiny ASCII charts for Sheets (bars and line).

The chart module is deliberately independent of the window toolkit: it extracts
one numeric series from a cell range and returns a grid of (text, role) rows.
The roles are theme role names, so the app only has to map them to attributes.

All chart glyphs are ASCII (`#`, `*`, `-`, `|`, `/`, `\\`, `+`) so they work on
the Linux console and on a dot-matrix printer.
"""
from collections import namedtuple

from .formula import col_name, fmt_number

ChartData = namedtuple('ChartData', 'title labels values col r0 r1 header')


class Grid:
    def __init__(self, width, height):
        self.w = max(1, width)
        self.h = max(1, height)
        self.ch = [[' '] * self.w for _ in range(self.h)]
        self.role = [['text'] * self.w for _ in range(self.h)]

    def putc(self, x, y, c, role='text'):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.ch[y][x] = c
            self.role[y][x] = role

    def put(self, x, y, text, role='text'):
        for i, c in enumerate(text):
            self.putc(x + i, y, c, role)

    def rows(self):
        return [(''.join(row), list(roles)) for row, roles in zip(self.ch, self.role)]


def _fit(text, width):
    text = str(text)
    if width <= 0:
        return ''
    if len(text) <= width:
        return text.ljust(width)
    return text[:max(0, width - 1)] + '~'


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _count(model, r0, r1, c):
    return sum(1 for r in range(r0, r1 + 1) if _num(model.value(r, c)))


def extract(model, r0, c0, r1, c1, active_col):
    """One series from the selection, or None when there is nothing to plot.

    A single-cell selection charts the whole current column. If the active
    column contains no numbers, the first numeric column in the selection is
    used. Text in the same row (outside the value column) becomes the label;
    without one the row number is used.
    """
    rows, cols = model.extent()
    if rows == 0 or cols == 0:
        return None
    if r0 == r1 and c0 == c1:
        r0, r1, c0, c1 = 0, rows - 1, 0, cols - 1
    if r1 < r0 or c1 < c0:
        return None
    value_col = active_col if c0 <= active_col <= c1 else c0
    if _count(model, r0, r1, value_col) == 0:
        for c in range(c0, c1 + 1):
            if _count(model, r0, r1, c):
                value_col = c
                break
        else:
            return None
    top = model.value(r0, value_col)
    header = bool(isinstance(top, str) and top.strip() and not top.startswith('#'))
    start = r0 + 1 if header else r0
    labels = []
    values = []
    first_row = last_row = None
    for r in range(start, r1 + 1):
        v = model.value(r, value_col)
        if not _num(v):
            continue
        label = ''
        for c in range(c0, c1 + 1):
            if c == value_col:
                continue
            text, is_number = model.display(r, c)
            if not is_number and text.strip() and not text.startswith('#'):
                label = text.strip()
                break
        labels.append(label or str(r + 1))
        values.append(v)
        if first_row is None:
            first_row = r
        last_row = r
    if not values:
        return None
    title = str(top).strip() if header else col_name(value_col)
    return ChartData(title, labels, values, value_col, first_row, last_row, header)


def _format(v):
    return fmt_number(v, 10)


def bars_chart(data, width, height):
    """Horizontal bars with a zero axis; negative values grow to the left."""
    g = Grid(width, height)
    if width < 8 or height < 1 or not data.values:
        return g.rows()
    labels = [str(x) for x in data.labels]
    values = list(data.values)
    texts = [_format(v) for v in values]
    show = len(values)
    note = False
    if show > height:
        show = max(0, height - 1)
        note = True
    label_w = min(max(4, max((len(x) for x in labels), default=4)), max(4, width // 3))
    value_w = max(len(x) for x in texts)
    bar_w = width - label_w - value_w - 2
    if bar_w < 3:
        label_w = max(3, width // 4)
        value_w = min(value_w, max(3, width // 4))
        bar_w = width - label_w - value_w - 2
    if bar_w < 1:
        return g.rows()
    lo = min(0, min(values))
    hi = max(0, max(values))
    span = hi - lo or 1
    zero = int(round((0 - lo) / span * (bar_w - 1)))
    right = bar_w - 1 - zero
    left = zero
    for i in range(show):
        y = i
        g.put(0, y, _fit(labels[i], label_w), 'dim')
        g.put(label_w + 1, y, ' ' * bar_w)
        g.putc(label_w + 1 + zero, y, '|', 'sel')
        v = values[i]
        if v > 0 and hi > 0:
            n = max(1, int(round(v / hi * right)))
            g.put(label_w + 1 + zero + 1, y, '#' * n, 'accent')
        elif v < 0 and lo < 0:
            n = max(1, int(round(-v / (-lo) * left)))
            g.put(label_w + 1 + zero - n, y, '#' * n, 'red')
        g.put(width - value_w, y, texts[i].rjust(value_w), 'bright')
    if note:
        g.put(0, height - 1, '+%d more' % (len(values) - show), 'dim')
    return g.rows()


def _line_char(x0, y0, x1, y1):
    if y0 == y1:
        return '-'
    if x0 == x1:
        return '|'
    return '/' if (x1 - x0) * (y1 - y0) < 0 else '\\'


def line_chart(data, width, height):
    """Point-and-line plot with a y axis, x labels and an optional zero line."""
    g = Grid(width, height)
    if width < 12 or height < 4 or not data.values:
        return g.rows()
    values = list(data.values)
    labels = [str(x) for x in data.labels]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    pad = (hi - lo) * 0.05
    lo -= pad
    hi += pad
    lo_text, hi_text = _format(lo), _format(hi)
    label_w = max(5, len(hi_text) + 1, len(lo_text) + 1)
    label_w = min(label_w, max(5, width // 3))
    plot_x = label_w
    plot_w = width - plot_x
    plot_h = height - 2
    if plot_w < 2 or plot_h < 2:
        return g.rows()
    x0, y0 = plot_x - 1, 0
    g.putc(x0, 0, '+', 'dim')
    for y in range(plot_h):
        g.putc(x0, y, '|', 'dim')
    for x in range(plot_x - 1, width):
        g.putc(x, plot_h, '-', 'dim')
    g.putc(x0, plot_h, '+', 'dim')
    g.put(0, 0, _fit(hi_text, label_w - 1), 'dim')
    g.put(0, plot_h - 1, _fit(lo_text, label_w - 1), 'dim')
    mid = (hi + lo) / 2.0
    g.put(0, max(0, plot_h // 2), _fit(_format(mid), label_w - 1), 'dim')
    if lo < 0 < hi:
        zy = plot_h - 1 - int(round((0 - lo) / (hi - lo) * (plot_h - 1)))
        for x in range(plot_x, width):
            g.putc(x, zy, '.', 'dim')
    pts = []
    for i, v in enumerate(values):
        x = plot_x + (0 if plot_w <= 1 else int(round(i * (plot_w - 1) / max(1, len(values) - 1))))
        y = plot_h - 1 - int(round((v - lo) / (hi - lo) * (plot_h - 1)))
        x = max(plot_x, min(width - 1, x))
        y = max(0, min(plot_h - 1, y))
        pts.append((x, y))
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        ch = _line_char(xa, ya, xb, yb)
        dx, dy = abs(xb - xa), abs(yb - ya)
        sx = 1 if xa < xb else -1
        sy = 1 if ya < yb else -1
        err = dx - dy
        x, y = xa, ya
        while True:
            if (x, y) not in pts:
                g.putc(x, y, ch, 'accent')
            if x == xb and y == yb:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
    for x, y in pts:
        g.putc(x, y, '*', 'accent2')
    # x labels: at most eight, no overlap
    n = len(values)
    if n <= 8:
        idxs = list(range(n))
    else:
        idxs = sorted({int(round(i * (n - 1) / 7)) for i in range(8)})
    end = -1
    for i in idxs:
        x = pts[i][0]
        label = labels[i]
        room = 12
        if i + 1 < n:
            room = max(3, min(12, pts[i + 1][0] - x))
        text = _fit(label, room).rstrip()
        if x + len(text) > width:
            x = max(plot_x, width - len(text))
        if not text or x <= end:
            continue
        g.put(x, plot_h + 1, text, 'dim')
        end = x + len(text)
    return g.rows()
