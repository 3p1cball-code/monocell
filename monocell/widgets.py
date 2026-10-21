"""Reusable widgets: buttons, scrollbars, text fields, list views, popup menus, dialogs."""
from .app import App
from .util import clean, fit


def button(app, cv, x, y, label, key, primary=False, active=False, enabled=True):
    """Draw ` label ` and register a click zone. Returns width."""
    th = app.th
    s = ' %s ' % label
    if not enabled:
        a = th.c('dim', 'raised')
    elif app.hover == key or active:
        a = th.c('on_accent', 'accent')
    elif primary:
        a = th.c('accent', 'raised')
    else:
        a = th.c('text', 'raised')
    cv.put(x, y, s, a)
    if enabled:
        app.zone(x, y, len(s), 1, key)
    return len(s)


def toolbar(app, cv, x, y, items, gap=1):
    """items: list of (label, key) or None as spacer. Returns end x."""
    for it in items:
        if it is None:
            x += 2
            continue
        label, key = it[0], it[1]
        x += button(app, cv, x, y, label, key, active=len(it) > 2 and it[2]) + gap
    return x


def flat_button(app, cv, x, y, label, key, attr=None):
    """Text-only button (no background), highlighted on hover."""
    th = app.th
    a = th.c('on_accent', 'accent') if app.hover == key else (attr if attr is not None else th.c('text', 'win'))
    cv.put(x, y, label, a)
    app.zone(x, y, len(label), 1, key)
    return len(label)


class SBar:
    """Scrollbar geometry + hit testing (click the track, drag the thumb)."""
    __slots__ = ('x', 'y', 'h', 'th', 'ty', 'total', 'visible')

    def __init__(self, x, y, h, th, ty, total, visible):
        self.x, self.y, self.h = x, y, h
        self.th, self.ty = th, ty
        self.total, self.visible = total, visible

    def hit(self, ev):
        return self.x == ev.x and self.y <= ev.y < self.y + self.h

    def top_at(self, my, grab=0):
        span = self.h - self.th
        rel = my - self.y - grab
        frac = 0.0 if span <= 0 else max(0.0, min(1.0, rel / span))
        return int(round(frac * (self.total - self.visible)))


def sb_geom(x, y, h, total, visible, top):
    if h <= 0 or total <= visible or total <= 0:
        return None
    th = min(h, max(2, int(h * visible / total + 0.5)))
    span = h - th
    ty = int(span * top / max(1, total - visible) + 0.5) if span > 0 else 0
    return SBar(x, y, h, th, ty, total, visible)


def sb_draw(cv, g, th, bg='win', active=False):
    """A visible groove (field) with a lighter thumb; accent while dragging."""
    if g is None:
        return
    cv.fill(g.x, g.y, 1, g.h, th.c('dim', 'field'), ' ')
    cv.fill(g.x, g.y + g.ty, 1, g.th, th.c('on_accent' if active else 'shadow',
                                            'accent' if active else 'dim'), ' ')


def scrollbar(cv, x, y, h, total, visible, top, th, bg='win'):
    """Draw-only helper (kept for callers that do not handle dragging)."""
    g = sb_geom(x, y, h, total, visible, top)
    sb_draw(cv, g, th, bg)
    return (g.ty, g.th) if g else (0, 0)


def scroll_from_click(ry, h, total, visible):
    if total <= visible:
        return 0
    return max(0, min(total - visible, int((ry / max(1, h - 1)) * (total - visible) + 0.5)))


def hbar(cv, x, y, w, frac, th, fg='accent', bg='raised', ch=' '):
    """Progress/level bar using background colours only."""
    frac = max(0.0, min(1.0, frac))
    n = int(w * frac + 0.5)
    cv.fill(x, y, n, 1, th.c('on_accent', fg), ch)
    cv.fill(x + n, y, w - n, 1, th.c('dim', bg), ch)


class TextField:
    def __init__(self, text='', placeholder='', password=False, on_enter=None, on_change=None):
        self.text = text
        self.cur = len(text)
        self.scroll = 0
        self.placeholder = placeholder
        self.password = password
        self.on_enter = on_enter
        self.on_change = on_change
        self.w = 10
        self.sel_all = False

    def set(self, text):
        self.text = text
        self.cur = len(text)
        self.scroll = 0

    def draw(self, cv, x, y, w, th, focused, bg='field'):
        self.w = w
        a = th.c('text', bg)
        cv.fill(x, y, w, 1, a)
        inner = w - 2
        if inner < 1:
            return
        if self.cur < self.scroll:
            self.scroll = self.cur
        if self.cur > self.scroll + inner - 1:
            self.scroll = self.cur - inner + 1
        t = self.text if not self.password else '*' * len(self.text)
        if not t and not focused:
            cv.put(x + 1, y, fit(self.placeholder, inner), th.c('dim', bg))
            return
        if not t and focused and self.placeholder:
            cv.put(x + 2, y, fit(self.placeholder, inner - 1), th.c('dim', bg))
        cv.put(x + 1, y, clean(t[self.scroll:self.scroll + inner]), a)
        if focused:
            cx = self.cur - self.scroll
            c = t[self.cur] if self.cur < len(t) else ' '
            cv.put(x + 1 + cx, y, c, th.c('on_accent', 'accent'))

    def click(self, rx):
        self.cur = max(0, min(len(self.text), self.scroll + rx - 1))

    def insert(self, s):
        s = s.replace('\n', ' ').replace('\t', ' ')
        self.text = self.text[:self.cur] + s + self.text[self.cur:]
        self.cur += len(s)
        self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change(self.text)

    def key(self, ev):
        k = ev.key
        t = self.text
        if ev.char:
            if self.sel_all:
                self.text = ''
                self.cur = 0
                self.sel_all = False
            self.insert(ev.char)
        elif k == 'backspace':
            if self.cur > 0:
                self.text = t[:self.cur - 1] + t[self.cur:]
                self.cur -= 1
                self._changed()
        elif k in ('delete', 'ctrl+d'):
            if self.cur < len(t):
                self.text = t[:self.cur] + t[self.cur + 1:]
                self._changed()
        elif k in ('left', 'ctrl+b'):
            self.cur = max(0, self.cur - 1)
        elif k in ('right', 'ctrl+f'):
            self.cur = min(len(t), self.cur + 1)
        elif k == 'ctrl+a':
            self.sel_all = True
        elif k == 'home':
            self.cur = 0
        elif k in ('end', 'ctrl+e'):
            self.cur = len(t)
        elif k in ('ctrl+left', 'alt+b'):
            i = self.cur
            while i > 0 and t[i - 1] == ' ':
                i -= 1
            while i > 0 and t[i - 1] != ' ':
                i -= 1
            self.cur = i
        elif k in ('ctrl+right', 'alt+f'):
            i = self.cur
            while i < len(t) and t[i] == ' ':
                i += 1
            while i < len(t) and t[i] != ' ':
                i += 1
            self.cur = i
        elif k == 'ctrl+u':
            self.text = t[self.cur:]
            self.cur = 0
            self._changed()
        elif k == 'ctrl+k':
            self.text = t[:self.cur]
            self._changed()
        elif k in ('ctrl+w', 'alt+backspace', 'ctrl+backspace'):
            i = self.cur
            while i > 0 and t[i - 1] == ' ':
                i -= 1
            while i > 0 and t[i - 1] != ' ':
                i -= 1
            self.text = t[:i] + t[self.cur:]
            self.cur = i
            self._changed()
        elif k == 'enter':
            if self.on_enter:
                self.on_enter(self.text)
        elif k.startswith('ctrl+') or k.startswith('alt+') or k in ('up', 'down', 'tab', 'esc'):
            pass
        else:
            return False
        return True


class ListView:
    """Scrollable single-selection list. Rendering is delegated to a row callback."""

    def __init__(self, items=None):
        self.items = items or []
        self.sel = 0
        self.top = 0
        self.h = 1
        self.on_activate = None
        self.on_select = None
        self.sb = None
        self.sb_grab = None

    def set_items(self, items, keep=False):
        self.items = items
        if not keep:
            self.sel = 0
            self.top = 0
        self.clamp()

    def clamp(self):
        n = len(self.items)
        self.sel = max(0, min(self.sel, n - 1)) if n else 0
        self.top = max(0, min(self.top, max(0, n - self.h)))

    def ensure(self):
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.h:
            self.top = self.sel - self.h + 1
        self.clamp()

    def current(self):
        return self.items[self.sel] if self.items else None

    def select(self, i):
        old = self.sel
        self.sel = i
        self.ensure()
        if self.on_select and old != self.sel:
            self.on_select(self.current())

    def draw(self, cv, x, y, w, h, row, th, scroll=True, bg='win'):
        """row(cv, x, y, w, item, index, selected)"""
        self.h = h
        self.clamp()
        sw = 1 if scroll and len(self.items) > h else 0
        for i in range(h):
            idx = self.top + i
            if idx >= len(self.items):
                break
            row(cv, x, y + i, w - sw, self.items[idx], idx, idx == self.sel)
        self.sb = sb_geom(x + w - 1, y, h, len(self.items), h, self.top) if sw else None
        sb_draw(cv, self.sb, th, bg, active=self.sb_grab is not None)

    def key(self, ev):
        k = ev.key
        n = len(self.items)
        if not n:
            return False
        if k == 'up':
            self.select(max(0, self.sel - 1))
        elif k == 'down':
            self.select(min(n - 1, self.sel + 1))
        elif k == 'pgup':
            self.select(max(0, self.sel - self.h + 1))
        elif k == 'pgdn':
            self.select(min(n - 1, self.sel + self.h - 1))
        elif k == 'home':
            self.select(0)
        elif k == 'end':
            self.select(n - 1)
        elif k == 'enter':
            if self.on_activate:
                self.on_activate(self.current())
        else:
            return False
        return True

    def mouse(self, ev, ry):
        """ry: row relative to list top. Returns True if handled."""
        n = len(self.items)
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
                    self.clamp()
                return True
            if ev.kind == 'drag' and self.sb_grab is not None:
                self.top = g.top_at(ev.y, self.sb_grab)
                self.clamp()
                return True
            if ev.kind == 'up':
                self.sb_grab = None
                return True
            return True
        if ev.kind == 'wheelup':
            self.top = max(0, self.top - 3)
            return True
        if ev.kind == 'wheeldown':
            self.top = max(0, min(max(0, n - self.h), self.top + 3))
            return True
        if ev.kind in ('down', 'drag') and ev.button == 0:
            idx = self.top + ry
            if 0 <= idx < n:
                self.select(idx)
                if ev.kind == 'down' and ev.clicks >= 2 and self.on_activate:
                    self.on_activate(self.current())
            return True
        return False


class MenuItem:
    __slots__ = ('label', 'action', 'hint', 'icon', 'enabled', 'checked')

    def __init__(self, label, action=None, hint='', icon='', enabled=True, checked=None):
        self.label = label
        self.action = action
        self.hint = hint
        self.icon = icon
        self.enabled = enabled
        self.checked = checked


class Menu:
    """Popup menu (absolute screen coordinates). Items: MenuItem or None (separator)."""

    def __init__(self, wm, items, x, y, width=None, title=None, above=False):
        self.wm = wm
        self.th = wm.theme
        self.items = items
        self.title = title
        self.sel = self._first()
        icon_w = max([len(i.icon) for i in items if i] + [0])
        self.icon_w = icon_w + 1 if icon_w else 0
        lw = max([len(i.label) + (len(i.hint) + 3 if i.hint else 0) for i in items if i] + [8])
        self.w = width or min(wm.w, lw + self.icon_w + 4 + (2 if any(i and i.checked is not None for i in items) else 0))
        self.h = len(items) + 2 + (1 if title else 0)
        if above:
            y = y - self.h + 1
        self.x = max(0, min(x, wm.w - self.w))
        self.y = max(0, min(y, wm.h - self.h))
        self.closed = False

    def _first(self):
        for i, it in enumerate(self.items):
            if it and it.enabled:
                return i
        return -1

    def draw(self, cv):
        th = self.th
        bg = th.c('text', 'menu')
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.wm.cfg['shadows']:
            self.wm.shade(cv, x + 1, y + h, w, 1)
            self.wm.shade(cv, x + w, y + 1, 1, h)
        cv.fill(x, y, w, h, bg)
        yy = y + 1
        if self.title:
            cv.put(x + 2, y, fit(self.title, w - 4), th.c('dim', 'menu'))
            yy = y + 1
        for i, it in enumerate(self.items):
            ry = yy + i
            if it is None:
                cv.hline(x + 1, ry, w - 2, th.c('sel', 'menu'), '-')
                continue
            on = i == self.sel and it.enabled
            a = th.c('on_accent', 'accent') if on else (th.c('text', 'menu') if it.enabled else th.c('dim', 'menu'))
            cv.fill(x + 1, ry, w - 2, 1, a)
            cx = x + 2
            if it.checked is not None:
                cv.put(cx, ry, '*' if it.checked else ' ', a if on else th.c('accent', 'menu'))
                cx += 2
            if self.icon_w:
                cv.put(cx, ry, it.icon, a if on else th.c('accent', 'menu'))
                cx += self.icon_w
            cv.put(cx, ry, fit(it.label, w - (cx - x) - 2 - (len(it.hint) + 1 if it.hint else 0)), a)
            if it.hint:
                cv.put(x + w - 2 - len(it.hint), ry, it.hint, a if on else th.c('dim', 'menu'))

    def item_at(self, mx, my):
        yy = self.y + 1
        i = my - yy
        if self.x + 1 <= mx < self.x + self.w - 1 and 0 <= i < len(self.items):
            return i
        return -1

    def contains(self, mx, my):
        return self.x <= mx < self.x + self.w and self.y <= my < self.y + self.h

    def activate(self, i):
        it = self.items[i] if 0 <= i < len(self.items) else None
        if it and it.enabled:
            self.wm.close_popup(self)
            if it.action:
                it.action()

    def on_key(self, ev):
        k = ev.key
        n = len(self.items)
        if k in ('esc', 'f12', 'alt+ '):
            self.wm.close_popup(self)
        elif k in ('up', 'down'):
            d = -1 if k == 'up' else 1
            i = self.sel
            for _ in range(n):
                i = (i + d) % n
                if self.items[i] and self.items[i].enabled:
                    self.sel = i
                    break
        elif k == 'enter':
            self.activate(self.sel)
        elif ev.char and ev.char.isalnum():
            for i, it in enumerate(self.items):
                if it and it.enabled and it.label.lower().startswith(ev.char.lower()):
                    self.sel = i
                    break
        else:
            return True   # swallow
        return True

    def on_mouse(self, ev):
        i = self.item_at(ev.x, ev.y)
        if ev.kind in ('move', 'drag'):
            if i >= 0 and self.items[i] and self.items[i].enabled and i != self.sel:
                self.sel = i
            return True
        if ev.kind == 'up' and i >= 0:
            self.activate(i)
            return True
        return True


# ------------------------------------------------------------------ dialogs
class Dialog(App):
    name = 'dialog'
    icon = '!'
    resizable = False
    min_size = (24, 3)

    def __init__(self, wm, win, text='', buttons=('OK',), on_result=None, field=None, password=False, **kw):
        super().__init__(wm, win)
        self.text = text
        self.buttons = list(buttons)
        self.on_result = on_result
        self.field = TextField(field, password=password, on_enter=lambda t: self.finish(self.buttons[0])) if field is not None else None
        self.sel = 0
        self.done = False

    @classmethod
    def measure(cls, text, buttons, field, width=None):
        w = width or max(34, min(70, max([len(l) for l in text.split('\n')] + [20]) + 4))
        lines = []
        for para in text.split('\n'):
            from .util import wrap_text
            lines += wrap_text(para, w - 4) or ['']
        h = len(lines) + 3 + (2 if field is not None else 0)
        return w, h

    def draw(self, cv, focused):
        th = self.th
        from .util import wrap_text
        lines = wrap_text(self.text, cv.w - 2)
        for i, l in enumerate(lines):
            cv.put(1, i, l, th.c('text', 'win'))
        y = len(lines) + 1
        if self.field is not None:
            self.field.draw(cv, 1, y - 1 + 1, cv.w - 2, th, focused)
            self.zone(1, y, cv.w - 2, 1, 'field')
            y += 2
        x = cv.w - 1
        for i in reversed(range(len(self.buttons))):
            b = self.buttons[i]
            x -= len(b) + 3
            button(self, cv, x, cv.h - 1, b, ('btn', i), primary=(i == self.sel), active=False)

    def finish(self, result):
        if self.done:
            return
        self.done = True
        val = self.field.text if self.field is not None else None
        self.close()
        if self.on_result:
            self.on_result(result, val)

    def on_click(self, key, ev):
        if key == 'field':
            self.field.click(ev.x - 1)
        elif key and key[0] == 'btn':
            self.finish(self.buttons[key[1]])

    def on_key(self, ev):
        k = ev.key
        if k == 'esc':
            self.finish(None)
        elif self.field is not None and k not in ('tab', 'shift+tab') and self.field.key(ev):
            pass
        elif k in ('tab', 'right', 'shift+tab', 'left'):
            d = -1 if k in ('shift+tab', 'left') else 1
            self.sel = (self.sel + d) % len(self.buttons)
        elif k == 'enter':
            self.finish(self.buttons[self.sel])
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.field is not None:
            self.field.insert(text)
            self.invalidate()
