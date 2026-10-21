"""Multi-line text editing widget (used by Editor and Writer)."""
import re
import time

from .util import clean, wrap
from .widgets import SBar, sb_geom, sb_draw

WORD = re.compile(r'\w+')


def vcol(line, cx, tabw=4):
    return len(line[:cx].expandtabs(tabw))


def cx_from_vcol(line, vx, tabw=4):
    col = 0
    for i, ch in enumerate(line):
        nxt = (col // tabw + 1) * tabw if ch == '\t' else col + 1
        if nxt > vx:
            return i if vx - col < nxt - vx or ch != '\t' else i + 1
        col = nxt
    return len(line)


class TextArea:
    def __init__(self, text='', wrap=False, gutter=False, tabw=4):
        self.lines = text.split('\n') if text else ['']
        self.cy = self.cx = 0
        self.anchor = None
        self.top = 0             # first visual row (index into visual rows)
        self.left = 0
        self.wrap = wrap
        self.gutter = gutter
        self.tabw = tabw
        self.width = 80
        self.height = 20
        self.undo_stack = []
        self.redo_stack = []
        self.last_edit = (None, 0)
        self.modified = False
        self.want_vx = None
        self.style = None        # fn(line_index, text) -> list of (start, end, attr)
        self.find_text = ''
        self.read_only = False
        self.scrollbar = False
        self.sb = None
        self.sb_grab = None
        self._vrows = None
        self._vkey = None

    # ------------------------------------------------------------ text
    def text(self):
        return '\n'.join(self.lines)

    def set_text(self, text):
        self.lines = text.split('\n') if text else ['']
        self.cy = self.cx = 0
        self.anchor = None
        self.top = 0
        self.undo_stack = []
        self.redo_stack = []
        self.modified = False
        self._vrows = None

    def snapshot(self, kind):
        now = time.monotonic()
        lk, lt = self.last_edit
        if kind != lk or now - lt > 1.0 or kind == 'other':
            self.undo_stack.append((list(self.lines), self.cy, self.cx))
            if len(self.undo_stack) > 300:
                self.undo_stack.pop(0)
            self.redo_stack = []
        self.last_edit = (kind, now)
        self.modified = True
        self._vrows = None

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append((list(self.lines), self.cy, self.cx))
            self.lines, self.cy, self.cx = self.undo_stack.pop()
            self.anchor = None
            self.last_edit = (None, 0)
            self._vrows = None
            self.modified = True

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append((list(self.lines), self.cy, self.cx))
            self.lines, self.cy, self.cx = self.redo_stack.pop()
            self.anchor = None
            self._vrows = None

    # ------------------------------------------------------------ selection
    def sel_range(self):
        if self.anchor is None or self.anchor == (self.cy, self.cx):
            return None
        a, b = self.anchor, (self.cy, self.cx)
        return (a, b) if a < b else (b, a)

    def selected_text(self):
        r = self.sel_range()
        if not r:
            return ''
        (y0, x0), (y1, x1) = r
        if y0 == y1:
            return self.lines[y0][x0:x1]
        out = [self.lines[y0][x0:]] + self.lines[y0 + 1:y1] + [self.lines[y1][:x1]]
        return '\n'.join(out)

    def delete_selection(self):
        r = self.sel_range()
        if not r:
            return False
        (y0, x0), (y1, x1) = r
        self.lines[y0:y1 + 1] = [self.lines[y0][:x0] + self.lines[y1][x1:]]
        self.cy, self.cx = y0, x0
        self.anchor = None
        self._vrows = None
        return True

    def select_all(self):
        self.anchor = (0, 0)
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[-1])

    # ------------------------------------------------------------ editing
    def insert(self, s, kind='type'):
        if self.read_only:
            return
        self.snapshot(kind if len(s) == 1 else 'other')
        self.delete_selection()
        s = s.replace('\r\n', '\n').replace('\r', '\n')
        line = self.lines[self.cy]
        before, after = line[:self.cx], line[self.cx:]
        parts = s.split('\n')
        if len(parts) == 1:
            self.lines[self.cy] = before + s + after
            self.cx += len(s)
        else:
            new = [before + parts[0]] + parts[1:-1] + [parts[-1] + after]
            self.lines[self.cy:self.cy + 1] = new
            self.cy += len(parts) - 1
            self.cx = len(parts[-1])
        self.want_vx = None
        self._vrows = None

    def newline(self):
        if self.read_only:
            return
        line = self.lines[self.cy]
        indent = re.match(r'[ \t]*', line).group() if not self.wrap else ''
        if self.wrap:
            m = re.match(r'(\s*)([-*+]|\d+[.)])\s+', line)
            if m and self.cx >= m.end():
                if line[m.end():].strip() == '':
                    # empty list item: end the list
                    self.snapshot('other')
                    self.lines[self.cy] = ''
                    self.cx = 0
                    return
                mark = m.group(2)
                if mark[0].isdigit():
                    mark = '%d%s' % (int(mark[:-1]) + 1, mark[-1])
                indent = m.group(1) + mark + ' '
        self.insert('\n' + indent, 'other')

    def backspace(self):
        if self.read_only:
            return
        self.snapshot('del')
        if self.delete_selection():
            return
        if self.cx > 0:
            line = self.lines[self.cy]
            n = 1
            if not self.wrap and line[:self.cx].endswith(' ' * self.tabw) and line[:self.cx].strip() == '':
                n = self.tabw
            self.lines[self.cy] = line[:self.cx - n] + line[self.cx:]
            self.cx -= n
        elif self.cy > 0:
            self.cx = len(self.lines[self.cy - 1])
            self.lines[self.cy - 1] += self.lines.pop(self.cy)
            self.cy -= 1
        self._vrows = None

    def delete(self):
        if self.read_only:
            return
        self.snapshot('del')
        if self.delete_selection():
            return
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] += self.lines.pop(self.cy + 1)
        self._vrows = None

    def delete_word_left(self):
        if self.anchor is None:
            x = self.word_left()
            self.anchor = (self.cy, x[1]) if x[0] == self.cy else x
        self.backspace()

    def indent(self, back=False):
        self.snapshot('other')
        r = self.sel_range()
        ys = range(r[0][0], r[1][0] + 1) if r else [self.cy]
        pad = ' ' * self.tabw
        for y in ys:
            if back:
                ln = self.lines[y]
                n = len(ln) - len(ln.lstrip(' '))
                k = min(n, self.tabw)
                self.lines[y] = ln[k:]
                if y == self.cy:
                    self.cx = max(0, self.cx - k)
            else:
                self.lines[y] = pad + self.lines[y]
                if y == self.cy:
                    self.cx += self.tabw
        if r:
            self.anchor = (r[0][0], 0)

    def duplicate_line(self):
        self.snapshot('other')
        self.lines.insert(self.cy, self.lines[self.cy])
        self.cy += 1

    def delete_line(self):
        self.snapshot('other')
        if len(self.lines) > 1:
            self.lines.pop(self.cy)
            self.cy = min(self.cy, len(self.lines) - 1)
        else:
            self.lines = ['']
        self.cx = min(self.cx, len(self.lines[self.cy]))

    # ------------------------------------------------------------ visual rows
    def text_width(self):
        return max(1, self.width - (self.gutter_w() if self.gutter else 0))

    def gutter_w(self):
        return len(str(len(self.lines))) + 2

    def vrows(self):
        """List of (line index, start, end) for every visual row."""
        tw = self.text_width()
        key = (tw, self.wrap, len(self.lines))
        if self._vrows is not None and self._vkey == key:
            return self._vrows
        rows = []
        if self.wrap:
            for i, ln in enumerate(self.lines):
                for a, b in wrap(ln, tw):
                    rows.append((i, a, b))
        else:
            rows = [(i, 0, len(ln)) for i, ln in enumerate(self.lines)]
        self._vrows = rows
        self._vkey = key
        return rows

    def cursor_vrow(self):
        rows = self.vrows()
        # binary search for first row of line cy
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] < self.cy:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        while i + 1 < len(rows) and rows[i + 1][0] == self.cy and rows[i + 1][1] <= self.cx:
            i += 1
        return i

    def ensure_visible(self):
        r = self.cursor_vrow()
        if r < self.top:
            self.top = r
        elif r >= self.top + self.height:
            self.top = r - self.height + 1
        if not self.wrap:
            vx = vcol(self.lines[self.cy], self.cx, self.tabw)
            tw = self.text_width()
            if vx < self.left:
                self.left = max(0, vx - 8)
            elif vx >= self.left + tw - 1:
                self.left = vx - tw + 8
        self.top = max(0, min(self.top, max(0, len(self.vrows()) - 1)))

    # ------------------------------------------------------------ movement
    def _move(self, shift):
        if shift:
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
        else:
            self.anchor = None

    def word_left(self):
        y, x = self.cy, self.cx
        if x == 0:
            return (y - 1, len(self.lines[y - 1])) if y > 0 else (0, 0)
        ln = self.lines[y]
        i = x
        while i > 0 and not (ln[i - 1].isalnum() or ln[i - 1] == '_'):
            i -= 1
        while i > 0 and (ln[i - 1].isalnum() or ln[i - 1] == '_'):
            i -= 1
        return (y, i)

    def word_right(self):
        y, x = self.cy, self.cx
        ln = self.lines[y]
        if x >= len(ln):
            return (y + 1, 0) if y < len(self.lines) - 1 else (y, x)
        i = x
        while i < len(ln) and not (ln[i].isalnum() or ln[i] == '_'):
            i += 1
        while i < len(ln) and (ln[i].isalnum() or ln[i] == '_'):
            i += 1
        return (y, i)

    def move_v(self, d):
        rows = self.vrows()
        r = self.cursor_vrow()
        ln, a, b = rows[r]
        vx = self.want_vx if self.want_vx is not None else (
            self.cx - a if self.wrap else vcol(self.lines[self.cy], self.cx, self.tabw))
        nr = max(0, min(len(rows) - 1, r + d))
        if nr == r:
            if d < 0:
                self.cx = 0 if self.wrap else self.cx
            return
        ln2, a2, b2 = rows[nr]
        self.cy = ln2
        if self.wrap:
            end = b2 if nr + 1 >= len(rows) or rows[nr + 1][0] != ln2 else b2 - 1
            self.cx = min(a2 + vx, max(a2, end))
        else:
            self.cx = cx_from_vcol(self.lines[ln2], vx, self.tabw)
        self.want_vx = vx

    def key(self, ev):
        """Returns True if handled."""
        k = ev.key
        shift = 'shift+' in k
        base = k.replace('shift+', '')
        if ev.char and ev.char not in '\x7f':
            self.insert(ev.char)
            return True
        if base in ('left', 'right', 'up', 'down', 'home', 'end', 'pgup', 'pgdn',
                    'ctrl+left', 'ctrl+right', 'ctrl+home', 'ctrl+end'):
            self._move(shift)
            if base == 'left':
                if self.cx > 0:
                    self.cx -= 1
                elif self.cy > 0:
                    self.cy -= 1
                    self.cx = len(self.lines[self.cy])
                self.want_vx = None
            elif base == 'right':
                if self.cx < len(self.lines[self.cy]):
                    self.cx += 1
                elif self.cy < len(self.lines) - 1:
                    self.cy += 1
                    self.cx = 0
                self.want_vx = None
            elif base == 'up':
                self.move_v(-1)
            elif base == 'down':
                self.move_v(1)
            elif base == 'pgup':
                self.move_v(-(self.height - 1))
            elif base == 'pgdn':
                self.move_v(self.height - 1)
            elif base == 'home':
                ln = self.lines[self.cy]
                ind = len(ln) - len(ln.lstrip())
                if self.wrap:
                    r = self.vrows()[self.cursor_vrow()]
                    self.cx = r[1]
                else:
                    self.cx = ind if self.cx != ind else 0
                self.want_vx = None
            elif base == 'end':
                if self.wrap:
                    rows = self.vrows()
                    i = self.cursor_vrow()
                    r = rows[i]
                    last = i + 1 >= len(rows) or rows[i + 1][0] != r[0]
                    self.cx = r[2] if last else r[2] - 1
                else:
                    self.cx = len(self.lines[self.cy])
                self.want_vx = None
            elif base == 'ctrl+left':
                self.cy, self.cx = self.word_left()
            elif base == 'ctrl+right':
                self.cy, self.cx = self.word_right()
            elif base == 'ctrl+home':
                self.cy, self.cx = 0, 0
            elif base == 'ctrl+end':
                self.cy = len(self.lines) - 1
                self.cx = len(self.lines[-1])
            if not shift:
                self.anchor = None
            return True
        if self.read_only and k not in ('ctrl+a', 'ctrl+c'):
            return False
        if k == 'enter':
            self.newline()
        elif k == 'backspace':
            self.backspace()
        elif k in ('ctrl+w', 'ctrl+backspace', 'alt+backspace'):
            self.delete_word_left()
        elif k == 'delete':
            self.delete()
        elif k == 'tab':
            if self.sel_range():
                self.indent()
            else:
                self.insert(' ' * (self.tabw - vcol(self.lines[self.cy], self.cx, self.tabw) % self.tabw)
                            if not self.wrap else '    ', 'other')
        elif k == 'shift+tab':
            self.indent(back=True)
        elif k == 'ctrl+z':
            self.undo()
        elif k in ('ctrl+y', 'ctrl+shift+z'):
            self.redo()
        elif k == 'ctrl+a':
            self.select_all()
        elif k == 'ctrl+d':
            self.duplicate_line()
        elif k == 'ctrl+k':
            self.delete_line()
        else:
            return False
        return True

    # ------------------------------------------------------------ mouse
    def pos_from_xy(self, x, y):
        rows = self.vrows()
        r = max(0, min(len(rows) - 1, self.top + y))
        ln, a, b = rows[r]
        gx = self.gutter_w() if self.gutter else 0
        x -= gx
        if self.wrap:
            last = r + 1 >= len(rows) or rows[r + 1][0] != ln
            cx = max(a, min(a + max(0, x), b if last else max(a, b - 1)))
        else:
            cx = cx_from_vcol(self.lines[ln], max(0, x) + self.left, self.tabw)
        return ln, cx

    def mouse(self, ev, x, y):
        """x, y relative to the text area origin."""
        g = self.sb
        if ev.kind == 'up' and self.sb_grab is not None:
            self.sb_grab = None
            return True
        if g and (g.hit(ev) or (ev.kind == 'drag' and self.sb_grab is not None)):
            if ev.kind == 'down':
                rel = ev.y - g.y
                if g.ty <= rel < g.ty + g.th:
                    self.sb_grab = rel - g.ty
                else:
                    self.sb_grab = max(0, g.th // 2)
                    self.top = g.top_at(ev.y, self.sb_grab)
                self.top = max(0, min(self.top, max(0, len(self.vrows()) - 1)))
                return True
            if ev.kind == 'drag' and self.sb_grab is not None:
                self.top = g.top_at(ev.y, self.sb_grab)
                self.top = max(0, min(self.top, max(0, len(self.vrows()) - 1)))
                return True
            if ev.kind == 'up':
                self.sb_grab = None
                return True
            return True
        if ev.kind == 'wheelup':
            self.top = max(0, self.top - 3)
            return True
        if ev.kind == 'wheeldown':
            self.top = max(0, min(len(self.vrows()) - self.height // 2, self.top + 3))
            return True
        if ev.kind == 'down' and ev.button == 0:
            ln, cx = self.pos_from_xy(x, y)
            if 'shift' in ev.mods:
                if self.anchor is None:
                    self.anchor = (self.cy, self.cx)
            else:
                self.anchor = (ln, cx)
            self.cy, self.cx = ln, cx
            self.want_vx = None
            if ev.clicks >= 2:
                line = self.lines[ln]
                for m in WORD.finditer(line):
                    if m.start() <= cx <= m.end():
                        self.anchor = (ln, m.start())
                        self.cx = m.end()
                        break
            return True
        if ev.kind == 'drag' and ev.button == 0:
            if y < 0:
                self.top = max(0, self.top - 1)
            elif y >= self.height:
                self.top = min(len(self.vrows()) - 1, self.top + 1)
            ln, cx = self.pos_from_xy(x, max(0, min(self.height - 1, y)))
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
            self.cy, self.cx = ln, cx
            return True
        return False

    # ------------------------------------------------------------ search
    def find(self, text, forward=True, case=False):
        if not text:
            return False
        self.find_text = text
        t = text if case else text.lower()
        n = len(self.lines)
        y, x = self.cy, self.cx
        for step in range(n + 1):
            yy = (y + step) % n if forward else (y - step) % n
            ln = self.lines[yy] if case else self.lines[yy].lower()
            if forward:
                i = ln.find(t, x if step == 0 else 0)
            else:
                i = ln.rfind(t, 0, max(0, x - 1) if step == 0 else len(ln))
            if i >= 0:
                self.anchor = (yy, i)
                self.cy, self.cx = yy, i + len(text)
                return True
        return False

    def replace_all(self, a, b):
        if not a:
            return 0
        self.snapshot('other')
        n = 0
        for i, ln in enumerate(self.lines):
            c = ln.count(a)
            if c:
                n += c
                self.lines[i] = ln.replace(a, b)
        self.cx = min(self.cx, len(self.lines[self.cy]))
        return n

    # ------------------------------------------------------------ drawing
    def draw(self, cv, x, y, w, h, th, focused, bg='win'):
        sw = 1 if self.scrollbar else 0
        self.width, self.height = w - sw, h
        rows = self.vrows()
        if self.top > max(0, len(rows) - 1):
            self.top = max(0, len(rows) - 1)
        gw = self.gutter_w() if self.gutter else 0
        tw = w - gw
        normal = th.c('text', bg)
        sel_a = th.c('bright', 'sel')
        r = self.sel_range()
        curvr = self.cursor_vrow()
        for i in range(h):
            vr = self.top + i
            if vr >= len(rows):
                if self.gutter:
                    cv.put(x, y + i, '~'.rjust(gw - 2), th.c('sel', bg))
                continue
            ln, a, b = rows[vr]
            line = self.lines[ln]
            if self.gutter:
                first = a == 0
                ga = th.c('accent' if ln == self.cy else 'dim', bg)
                cv.put(x, y + i, (str(ln + 1) if first else '').rjust(gw - 2), ga)
            if self.wrap:
                seg = line[a:b]
                off = a
            else:
                seg = line.expandtabs(self.tabw)[self.left:self.left + tw] if '\t' in line else line[self.left:self.left + tw]
                off = self.left
            cv.put(x + gw, y + i, clean(seg), normal)
            # syntax/markup styling
            if self.style:
                for s0, s1, attr in self.style(ln, line):
                    s0, s1 = max(s0, a if self.wrap else 0), min(s1, b if self.wrap else len(line))
                    if s1 <= s0:
                        continue
                    if self.wrap:
                        cv.set_attr(x + gw + s0 - a, y + i, s1 - s0, 1, attr)
                    elif '\t' not in line:
                        cv.set_attr(x + gw + s0 - self.left, y + i, s1 - s0, 1, attr)
            # selection
            if r:
                (y0, x0), (y1, x1) = r
                if y0 <= ln <= y1:
                    s0 = x0 if ln == y0 else 0
                    s1 = x1 if ln == y1 else len(line) + 1
                    if self.wrap:
                        s0, s1 = max(s0, a), min(s1, b + (1 if b == len(line) else 0))
                        if s1 > s0:
                            cv.set_attr(x + gw + s0 - a, y + i, s1 - s0, 1, sel_a)
                    else:
                        v0 = vcol(line, s0, self.tabw) - self.left
                        v1 = (vcol(line, min(s1, len(line)), self.tabw) + (1 if s1 > len(line) else 0)) - self.left
                        v0, v1 = max(0, v0), min(tw, v1)
                        if v1 > v0:
                            cv.set_attr(x + gw + v0, y + i, v1 - v0, 1, sel_a)
            # cursor
            if focused and vr == curvr:
                if self.wrap:
                    cxv = self.cx - a
                else:
                    cxv = vcol(line, self.cx, self.tabw) - self.left
                cxv = min(cxv, tw - 1) if tw > 0 else 0
                if 0 <= cxv < tw:
                    ch = line[self.cx] if self.cx < len(line) and line[self.cx] != '\t' else ' '
                    cv.put(x + gw + cxv, y + i, clean(ch) or ' ', th.c('on_accent', 'accent'))
        self.sb = None
        if sw and len(rows) > h:
            g = sb_geom(x + self.width, y, h, len(rows), h, self.top)
            sb_draw(cv, g, th, bg, active=self.sb_grab is not None)
            if g:
                self.sb = SBar(self.width, 0, h, g.th, g.ty, len(rows), h)
