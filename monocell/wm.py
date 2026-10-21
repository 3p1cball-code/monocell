"""Window manager: windows, z-order, drag/resize/snap, taskbar, start menu, toasts, dialogs."""
import base64
import os
import random
import socket
import subprocess
import time
import traceback

from . import clipboard as clip
from .app import REGISTRY, load_app
from .screen import Canvas
from .term import Key, Mouse, Paste
from .util import fit, log, sym
from .widgets import Dialog, Menu, MenuItem, TextField, scrollbar

DOUBLE_CLICK = 0.4
PASTE_KEYS = ('ctrl+shift+v', 'shift+insert', 'ctrl+v')
COPY_KEYS = ('ctrl+shift+c', 'ctrl+insert', 'ctrl+c')


class Window:
    _ids = 0

    def __init__(self, wm, title, x, y, w, h):
        Window._ids += 1
        self.id = Window._ids
        self.wm = wm
        self.title = title
        self.x, self.y, self.w, self.h = x, y, w, h
        self.app = None
        self.minimized = False
        self.maximized = False
        self.snap = None              # None | 'max' | 'left' | 'right'
        self.restore = None
        self.closed = False
        self.modal_for = None
        self.borderless = False
        self.resizable = True
        self.min_w, self.min_h = 18, 4
        self.icon = '*'
        self.icon_ascii = '*'
        self.icon_key = None
        self.urgent = False

    @property
    def cw(self):
        return self.w if self.borderless else max(0, self.w - 2)

    @property
    def ch(self):
        return self.h if self.borderless else max(0, self.h - 2)

    def rect(self):
        return self.x, self.y, self.w, self.h

    def contains(self, mx, my):
        return self.x <= mx < self.x + self.w and self.y <= my < self.y + self.h


class Rain:
    """Matrix-style falling characters for the desktop background."""
    KATA = 'ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ'
    ASCII = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<>[]{}+-*/=:.'

    def __init__(self, w, h, density=1.0, speed=1.0, randomness=0.6):
        from . import util
        self.w, self.h = w, h
        self.density, self.speed, self.randomness = density, speed, randomness
        self.key = (density, speed, randomness)
        self.set = self.KATA if util.UNICODE else self.ASCII
        self.drops = []
        for _ in range(max(4, int(w / 14 * density))):
            self.spawn(start=True)

    def spawn(self, start=False):
        length = random.randint(5, 18) + int(self.randomness * 6)
        y = random.randint(-self.h // 3, self.h) if start else -random.randint(0, 8)
        sp = (1 if random.random() < 0.82 else 2) * self.speed
        self.drops.append({'x': random.randrange(self.w), 'y': y, 'len': length,
                           'chars': [random.choice(self.set) for _ in range(length)],
                           'speed': sp})

    def step(self):
        if random.random() < min(0.95, 0.6 * self.density):
            self.spawn()
        alive = []
        for d in self.drops:
            d['y'] += d['speed']
            for _ in range(max(1, int(round(d['speed'])))):
                d['chars'].insert(0, random.choice(self.set))
                d['chars'].pop()
            if random.random() < 0.25 * self.randomness and d['len'] > 3:
                d['chars'][random.randrange(1, d['len'])] = random.choice(self.set)
            if d['y'] - d['len'] < self.h:
                alive.append(d)
        self.drops = alive[:max(4, int(self.w * 0.7 * self.density))]

    def draw(self, cv, y0, th):
        head, hot, body, tail = (th.c('bright', 'desk'), th.c('green', 'desk'),
                                 th.c('green', 'desk'), th.c('dim', 'desk'))
        for d in self.drops:
            x = d['x']
            for k, ch in enumerate(d['chars']):
                y = int(d['y']) - k
                if 0 <= y < self.h:
                    a = head if k == 0 else hot if k < 3 else body if k < 9 else tail
                    cv.putc(x, y0 + y, ch, a)


class StartMenu:
    """Start menu popup with type-to-search and scrolling on small screens."""

    def __init__(self, wm):
        self.wm = wm
        self.th = wm.theme
        self.search = TextField(placeholder='Type to search')
        self.sel = 0
        self.top = 0
        self.hover = None
        self.closed = False
        self.layout()

    def all_rows(self):
        q = self.search.text.lower().strip()
        out = []
        last = None
        for name, mod, label, icon, desc, cat in REGISTRY:
            if q and q not in label.lower() and q not in desc.lower() and q not in name:
                continue
            if not q and cat != last:
                out.append(('hdr', cat))
                last = cat
            out.append(('app', name, label, icon, desc))
        out.append(('sep',))
        out += [('foot', 'help', 'Help & shortcuts'), ('foot', 'exit', 'Exit monocell'),
                ('foot', 'reboot', 'Reboot'), ('foot', 'shutdown', 'Shut down')]
        return out

    def selectable(self):
        return [i for i, r in enumerate(self.all_rows()) if r[0] in ('app', 'foot')]

    def layout(self):
        wm = self.wm
        self.w = min(wm.w, 56 if wm.w >= 120 else 36)
        self.avail = max(4, min(wm.desk_h - 4, len(self.all_rows())))
        self.h = min(wm.desk_h, self.avail + 4)
        self.avail = self.h - 4
        self.x = 0
        self.y = wm.desk_y + wm.desk_h - self.h if wm.cfg['taskbar'] == 'bottom' else wm.desk_y

    def contains(self, mx, my):
        return self.x <= mx < self.x + self.w and self.y <= my < self.y + self.h

    def draw(self, cv):
        th = self.th
        self.layout()
        rows = self.all_rows()
        sel = self.selectable()
        if sel:
            self.sel = max(0, min(self.sel, len(sel) - 1))
            cur = sel[self.sel]
        else:
            cur = -1
        # keep the selected row visible
        if cur >= 0:
            if cur < self.top:
                self.top = cur
            elif cur >= self.top + self.avail:
                self.top = cur - self.avail + 1
        maxtop = max(0, len(rows) - self.avail)
        self.top = max(0, min(self.top, maxtop))
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.wm.cfg['shadows']:
            self.wm.shade(cv, x + w, y + 1, 1, h)
        cv.fill(x, y, w, h, th.c('text', 'menu'))
        cv.fill(x, y, 1, h, th.c('text', 'accent'))
        host = self.wm.hostname
        cv.put(x + 2, y + 1, fit('monocell', 12), th.c('accent', 'menu', 1))
        cv.put(x + w - 2 - len(host[:16]), y + 1, host[:16], th.c('dim', 'menu'))
        self.search.draw(cv, x + 2, y + 2, w - 3, th, True, bg='field')
        for i in range(self.avail):
            idx = self.top + i
            if idx >= len(rows):
                break
            r = rows[idx]
            ry = y + 4 + i
            if r[0] == 'hdr':
                cv.put(x + 2, ry, r[1].upper(), th.c('dim', 'menu'))
            elif r[0] == 'sep':
                cv.hline(x + 2, ry, w - 3, th.c('sel', 'menu'), '-')
            elif r[0] == 'app':
                on = idx == cur
                a = th.c('on_accent', 'accent') if on else th.c('text', 'menu')
                cv.fill(x + 1, ry, w - 1, 1, a)
                cv.put(x + 2, ry, sym(r[1], r[3]), a if on else th.c('accent', 'menu'))
                cv.put(x + 7, ry, fit(r[2], 17), a if on else th.c('bright', 'menu'))
                d = r[4] if r[4] != r[2] else ''
                if d and w >= 46:
                    cv.put(x + 24, ry, fit(d, w - 26), a if on else th.c('dim', 'menu'))
            elif r[0] == 'foot':
                on = idx == cur
                a = th.c('on_accent', 'accent') if on else th.c('text', 'menu')
                cv.fill(x + 1, ry, w - 1, 1, a)
                icon = {'help': ' ? ', 'exit': ' x ', 'reboot': ' ^ ', 'shutdown': ' v '}[r[1]]
                cv.put(x + 2, ry, icon, a if on else th.c('accent', 'menu'))
                cv.put(x + 7, ry, r[2], a)
        if len(rows) > self.avail:
            scrollbar(cv, x + w - 1, y + 4, self.avail, len(rows), self.avail, self.top, th, bg='menu')

    def activate(self):
        rows = self.all_rows()
        sel = self.selectable()
        if not sel:
            return
        r = rows[sel[self.sel]]
        self.wm.close_popup(self)
        if r[0] == 'app':
            self.wm.open(r[1])
        elif r[1] == 'exit':
            self.wm.confirm_exit()
        elif r[1] == 'help':
            self.wm.show_help()
        elif r[1] == 'reboot':
            self.wm.confirm_reboot()
        elif r[1] == 'shutdown':
            self.wm.confirm_shutdown()

    def on_key(self, ev):
        k = ev.key
        n = len(self.selectable())
        if k in ('esc', 'f12', 'alt+ '):
            self.wm.close_popup(self)
        elif k == 'up':
            self.sel = (self.sel - 1) % max(1, n)
        elif k in ('down', 'tab'):
            self.sel = (self.sel + 1) % max(1, n)
        elif k in ('pgup', 'pgdn'):
            self.sel = max(0, min(n - 1, self.sel + (-self.avail + 1 if k == 'pgup' else self.avail - 1)))
        elif k in ('home', 'end'):
            self.sel = 0 if k == 'home' else max(0, n - 1)
        elif k == 'enter':
            self.activate()
        elif self.search.key(ev):
            self.sel = 0
            self.top = 0
        return True

    def on_paste(self, text):
        self.search.insert(text)

    def on_mouse(self, ev):
        rows = self.all_rows()
        sel = self.selectable()
        if ev.kind in ('wheelup', 'wheeldown') and self.contains(ev.x, ev.y):
            d = -2 if ev.kind == 'wheelup' else 2
            self.top = max(0, min(max(0, len(rows) - self.avail), self.top + d))
            return True
        i = ev.y - (self.y + 4)
        idx = self.top + i
        if 0 <= i < self.avail and idx in sel and self.x < ev.x < self.x + self.w:
            if ev.kind in ('move', 'drag'):
                self.sel = sel.index(idx)
            elif ev.kind == 'up':
                self.sel = sel.index(idx)
                self.activate()
        return True


class WM:
    def __init__(self, term, loop, cfg, theme):
        self.term = term
        self.loop = loop
        self.cfg = cfg
        self.theme = theme
        self.windows = []        # z-order, last = top
        self.focus = None
        self.popups = []
        self.toasts = []
        self.dirty = True
        self.drag = None
        self.mouse_capture = None
        self.last_click = (0, -1, -1, -1)
        self.hover_part = None
        self.mouse_pos = (-1, -1)
        self.snap_preview = None
        self._clip = clip.load()
        self._clip_mtime = clip.mtime()
        self.middle_scroll = None
        self.middle_last = (0, 0)
        self.middle_acc = 0.0
        self.middle_moved = False
        self.tsel = None                # screen text selection: {'win', 'a', 'b', 'drag'}
        self.hostname = (os.environ.get('MONOCELL_HOSTNAME')
                         or socket.gethostname().split('.')[0])
        self.battery = None
        self.last_frame = 0.0
        self.bar_zones = []
        self.cascade_n = 0
        self.w, self.h = term.w, term.h
        self.layout()
        loop.on_error = self.on_error
        loop.every(1.0, self.tick)
        loop.every(30.0, self.read_battery)
        self.read_battery()
        self.net = None
        self.bt = None
        self.sound = None
        loop.every(6.0, self.refresh_tray)
        self.refresh_tray()
        self.wallpaper_cells = None
        self.wp_key = None
        self.rain = None
        self.sync_symbols()
        self.load_wallpaper()
        loop.every(0.09, self.rain_tick)

    # ---------------------------------------------------------- geometry
    def layout(self):
        self.w, self.h = self.term.w, self.term.h
        if self.cfg['taskbar'] == 'top':
            self.bar_y, self.desk_y = 0, 1
        else:
            self.bar_y, self.desk_y = self.h - 1, 0
        self.desk_h = self.h - 1

    def on_resize(self):
        self.term.resize()
        self.layout()
        self.load_wallpaper()
        for win in self.windows:
            if win.snap:
                self.apply_snap(win, win.snap)
            else:
                self.clamp(win)
            self.notify_resize(win)
        self.dirty = True

    def clamp(self, win):
        win.w = max(win.min_w, min(win.w, self.w))
        win.h = max(win.min_h, min(win.h, self.desk_h))
        win.x = max(0, min(win.x, max(0, self.w - 10)))
        win.y = max(self.desk_y, min(win.y, self.desk_y + self.desk_h - 2))

    def notify_resize(self, win):
        if win.app:
            try:
                win.app.on_resize(win.cw, win.ch)
            except Exception as e:
                self.on_error(e, traceback.format_exc())

    # ---------------------------------------------------------- windows
    def open(self, name, **kw):
        try:
            cls = load_app(name)
        except Exception as e:
            log(traceback.format_exc())
            self.toast('Cannot start %s: %s' % (name, e), 'red')
            return None
        launcher = getattr(cls, 'launch', None)
        if launcher is not None:
            try:
                return launcher(self)
            except Exception as e:
                log(traceback.format_exc())
                self.toast('Cannot start %s: %s' % (name, e), 'red')
                return None
        if cls.single:
            for win in self.windows:
                if isinstance(win.app, cls):
                    self.activate(win)
                    if kw and hasattr(win.app, 'reopen'):
                        win.app.reopen(**kw)
                    return win.app
        return self.open_cls(cls, **kw)

    def open_cls(self, cls, modal_for=None, size=None, title=None, pos=None, **kw):
        cw, ch = size or cls.size
        w, h = cw + 2, ch + 2
        w = min(w, self.w)
        h = min(h, self.desk_h)
        if pos:
            x, y = pos
        elif modal_for is not None:
            p = modal_for
            x = p.x + (p.w - w) // 2
            y = p.y + max(2, (p.h - h) // 3)
        else:
            n = self.cascade_n % 6
            self.cascade_n += 1
            x = (self.w - w) // 2 - 6 + n * 3
            y = self.desk_y + max(0, (self.desk_h - h) // 2 - 3) + n
        win = Window(self, title or cls.title, x, y, w, h)
        win.icon_key = cls.name
        win.icon_ascii = cls.icon
        win.icon = sym(cls.name, cls.icon)
        win.resizable = cls.resizable
        win.min_w, win.min_h = cls.min_size[0] + 2, cls.min_size[1] + 2
        win.modal_for = modal_for
        if modal_for is not None:
            win.y = max(self.desk_y, min(win.y, self.desk_y + self.desk_h - win.h))
            win.x = max(0, min(win.x, self.w - win.w))
        self.clamp(win)
        if (self.w < 90 or self.desk_h < 26) and modal_for is None and cls.resizable:
            win.restore = (win.x, win.y, win.w, win.h)
            self.apply_snap(win, 'max')
        self.windows.append(win)
        try:
            win.app = cls(self, win, **kw)
        except Exception as e:
            self.windows.remove(win)
            log(traceback.format_exc())
            self.toast('Cannot start %s: %s' % (cls.title, e), 'red')
            return None
        self.activate(win)
        self.notify_resize(win)
        self.dirty = True
        return win.app

    def activate(self, win):
        if win is None:
            self.set_focus(None)
            return
        # a window with an open modal child forwards focus to it
        for w in reversed(self.windows):
            if w.modal_for is win and not w.closed:
                win = w
                break
        if win.minimized:
            win.minimized = False
        if win in self.windows:
            self.windows.remove(win)
            self.windows.append(win)
            # keep its modal parent directly below
        self.set_focus(win)
        win.urgent = False
        self.dirty = True

    def set_focus(self, win):
        if self.focus is win:
            return
        old = self.focus
        self.focus = win
        for w, f in ((old, False), (win, True)):
            if w and w.app and not w.closed:
                try:
                    w.app.on_focus(f)
                except Exception:
                    pass

    def top_visible(self):
        for w in reversed(self.windows):
            if not w.minimized and not w.closed:
                return w
        return None

    def close(self, win, force=False):
        if win.closed:
            return
        if not force and win.app:
            try:
                if not win.app.on_close():
                    return
            except Exception as e:
                self.on_error(e, traceback.format_exc())
        for child in [w for w in self.windows if w.modal_for is win]:
            self.close(child, True)
        win.closed = True
        self.loop.cancel_owner(win.app)
        if win in self.windows:
            self.windows.remove(win)
        if self.mouse_capture is win:
            self.mouse_capture = None
        if self.drag and self.drag[1] is win:
            self.drag = None
        if self.focus is win:
            self.focus = None
            nxt = win.modal_for if win.modal_for and not win.modal_for.closed else self.top_visible()
            self.activate(nxt)
        self.dirty = True

    def minimize(self, win):
        win.minimized = True
        if self.focus is win:
            self.focus = None
            self.activate(self.top_visible())
        self.dirty = True

    def toggle_max(self, win):
        if not win.resizable:
            return
        if win.snap:
            self.unsnap(win)
        else:
            win.restore = (win.x, win.y, win.w, win.h)
            self.apply_snap(win, 'max')
        self.notify_resize(win)
        self.dirty = True

    def apply_snap(self, win, mode):
        win.snap = mode
        win.maximized = mode == 'max'
        dy, dh = self.desk_y, self.desk_h
        if mode == 'max':
            win.x, win.y, win.w, win.h = 0, dy, self.w, dh
        elif mode == 'left':
            win.x, win.y, win.w, win.h = 0, dy, self.w // 2, dh
        elif mode == 'right':
            win.x, win.y, win.w, win.h = self.w // 2, dy, self.w - self.w // 2, dh
        elif mode in ('tl', 'tr', 'bl', 'br'):
            hw, hh = self.w // 2, dh // 2
            win.x = 0 if mode[1] == 'l' else hw
            win.w = hw if mode[1] == 'l' else self.w - hw
            win.y = dy if mode[0] == 't' else dy + hh
            win.h = hh if mode[0] == 't' else dh - hh

    def unsnap(self, win, keep_pos=None):
        win.snap = None
        win.maximized = False
        if win.restore:
            x, y, w, h = win.restore
            if keep_pos:
                x = max(0, min(self.w - w, keep_pos[0] - w // 2))
                y = keep_pos[1]
            win.x, win.y, win.w, win.h = x, y, w, h
            self.clamp(win)

    def tile(self):
        wins = [w for w in self.windows if not w.minimized and w.resizable and w.modal_for is None]
        n = len(wins)
        if not n:
            return
        cols = 1 if n == 1 else 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        for i, win in enumerate(wins):
            c, r = i % cols, i // cols
            if not win.snap:
                win.restore = (win.x, win.y, win.w, win.h)
            win.snap = 'tile'
            win.maximized = False
            ncols = cols if r < rows - 1 else n - cols * (rows - 1)
            cw = self.w // ncols
            rh = self.desk_h // rows
            win.x = c * cw
            win.w = cw if c < ncols - 1 else self.w - c * cw
            win.y = self.desk_y + r * rh
            win.h = rh if r < rows - 1 else self.desk_h - r * rh
            self.notify_resize(win)
        self.dirty = True

    def cascade(self):
        for i, win in enumerate([w for w in self.windows if not w.minimized and w.modal_for is None]):
            if win.snap:
                self.unsnap(win)
            win.x = 2 + i * 3
            win.y = self.desk_y + 1 + i * 2
            self.clamp(win)
            self.notify_resize(win)
        self.dirty = True

    def show_desktop(self):
        for w in self.windows:
            w.minimized = True
        self.set_focus(None)
        self.dirty = True

    def cycle(self, d=1):
        wins = [w for w in self.windows if w.modal_for is None]
        if not wins:
            return
        if self.focus in wins:
            i = wins.index(self.focus)
            nxt = wins[(i + d) % len(wins)]
        else:
            nxt = wins[0] if d > 0 else wins[-1]
        self.activate(nxt)

    # ---------------------------------------------------------- dialogs/helpers
    def dialog(self, title, text, buttons=('OK',), on_result=None, field=None, parent=None, password=False, width=None):
        parent = parent if parent is not None else self.focus
        w, h = Dialog.measure(text, buttons, field, width)
        return self.open_cls(Dialog, modal_for=parent, size=(w - 2, h), title=title, text=text,
                             buttons=buttons, on_result=on_result, field=field, password=password)

    def message(self, title, text, parent=None):
        return self.dialog(title, text, ('OK',), parent=parent)

    def confirm(self, title, text, on_yes, yes='OK', no='Cancel', parent=None):
        return self.dialog(title, text, (yes, no), lambda r, v: on_yes() if r == yes else None, parent=parent)

    def prompt(self, title, text, default, on_ok, parent=None, password=False):
        return self.dialog(title, text, ('OK', 'Cancel'),
                           lambda r, v: on_ok(v) if r == 'OK' else None, field=default, parent=parent,
                           password=password)

    def pick_file(self, on_ok, mode='open', start=None, title=None, name='', parent=None, dirs=False):
        from .apps.files import FileDialog
        return self.open_cls(FileDialog, modal_for=parent if parent is not None else self.focus,
                             size=(min(70, self.w - 6), min(20, self.desk_h - 4)),
                             title=title or ('Open' if mode == 'open' else 'Save as'),
                             mode=mode, start=start, on_ok=on_ok, name=name, dirs=dirs)

    def toast(self, text, color='accent', secs=3.5):
        self.toasts.append([text, color, time.monotonic() + secs])
        self.toasts = self.toasts[-4:]
        self.dirty = True

    def copy_text(self, text):
        if not text:
            self.toast('Nothing selected - Shift+drag selects text', 'yellow')
            return
        self.set_clipboard(text)
        n = len(text)
        self.toast('Copied %d character%s' % (n, '' if n == 1 else 's'))

    @staticmethod
    def accepts_paste(app):
        from .app import App
        return app is not None and type(app).on_paste is not App.on_paste

    def paste_into(self, win):
        if not win or not win.app or not self.accepts_paste(win.app):
            return
        if not self.clipboard:
            self.toast('Clipboard is empty', 'yellow')
            return
        win.app.on_paste(self.clipboard)
        self.dirty = True

    def content_origin(self, win):
        return (win.x, win.y) if win.borderless else (win.x + 1, win.y + 1)

    def tsel_rows(self):
        """(y, x0, x1) screen rows covered by the text selection, clipped to the content."""
        t = self.tsel
        win = t['win']
        ox, oy = self.content_origin(win)
        if win.cw <= 0 or win.ch <= 0:
            return []
        (ax, ay), (bx, by) = t['a'], t['b']
        x_hi, y_hi = ox + win.cw - 1, oy + win.ch - 1
        if t.get('block'):
            xa, xb = sorted((max(ox, min(x_hi, ax)), max(ox, min(x_hi, bx))))
            ya, yb = sorted((max(oy, min(y_hi, ay)), max(oy, min(y_hi, by))))
            return [(y, xa, xb) for y in range(ya, yb + 1)]
        if (ay, ax) > (by, bx):
            (ax, ay), (bx, by) = (bx, by), (ax, ay)
        if ay < oy:
            ax, ay = ox, oy
        if by > y_hi:
            bx, by = x_hi, y_hi
        ax, bx = max(ox, min(x_hi, ax)), max(ox, min(x_hi, bx))
        rows = []
        for y in range(ay, by + 1):
            rows.append((y, ax if y == ay else ox, bx if y == by else x_hi))
        return rows

    def tsel_text(self):
        if not self.tsel:
            return ''
        buf = self.term.back
        lines = []
        for y, x0, x1 in self.tsel_rows():
            if 0 <= y < buf.h:
                lines.append(''.join(buf.ch[y][x0:x1 + 1]).rstrip())
        return '\n'.join(lines).strip('\n')

    def edit_menu(self, win, mx, my):
        sel = self.tsel_text() if self.tsel and self.tsel['win'] is win else \
            (win.app.selection_text() or '')
        items = [MenuItem('Copy', lambda: self.copy_text(sel), 'Ctrl+Shift+C', enabled=bool(sel))]
        if self.accepts_paste(win.app):
            items.append(MenuItem('Paste', lambda: self.paste_into(win), 'Ctrl+Shift+V',
                                  enabled=bool(self.clipboard)))
        items += [None, MenuItem('Shift+drag selects text', enabled=False)]
        self.menu(items, mx, my)

    @property
    def clipboard(self):
        """Shared clipboard; picks up changes made by monocell-clip or another session."""
        m = clip.mtime()
        if m != self._clip_mtime:
            self._clip = clip.load()
            self._clip_mtime = m
        return self._clip

    def set_clipboard(self, text):
        self._clip = text
        clip.save(text)
        self._clip_mtime = clip.mtime()
        if self.term.mode != 'linux' and len(text) < 100000:
            try:
                self.term.write('\x1b]52;c;%s\x07' % base64.b64encode(text.encode()).decode())
            except Exception:
                pass

    def on_error(self, e, tb=''):
        self.toast('Error: %s' % e, 'red', 6)

    def confirm_exit(self):
        self.confirm('Exit monocell', 'Close all windows and leave monocell?', self.quit, yes='Exit')

    def confirm_reboot(self):
        self.confirm('Reboot', 'Reboot the computer now?', lambda: self.power('reboot'), yes='Reboot')

    def confirm_shutdown(self):
        self.confirm('Shut down', 'Shut down the computer now?', lambda: self.power('poweroff'),
                     yes='Shut down')

    def power(self, action):
        try:
            p = subprocess.run(['systemctl', action], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            self.toast('%s: %s' % (action, e), 'red')
            return
        if p.returncode != 0:
            msg = (p.stderr or p.stdout).strip().splitlines()[-1:] or ['failed']
            self.toast('%s: %s' % (action, msg[0][:120]), 'red')

    def quit(self):
        for win in list(self.windows):
            self.close(win, force=True)
        self.loop.running = False

    def show_help(self):
        self.message('Help', HELP_TEXT)

    def popup(self, p):
        self.popups.append(p)
        self.dirty = True

    def close_popup(self, p=None):
        if p is None:
            self.popups.clear()
        elif p in self.popups:
            self.popups.remove(p)
        self.dirty = True

    def menu(self, items, x, y, **kw):
        m = Menu(self, items, x, y, **kw)
        self.popup(m)
        return m

    def toggle_start(self):
        for p in self.popups:
            if isinstance(p, StartMenu):
                self.close_popup(p)
                return
        self.close_popup()
        self.popup(StartMenu(self))

    def sync_symbols(self):
        """Decide between single-width unicode symbols and ASCII fallbacks."""
        from . import util
        pref = self.cfg.get('symbols', 'auto')
        if pref == 'on':
            on = True
        elif pref == 'off':
            on = False
        else:
            on = not self.term.termname.startswith('linux')
        util.UNICODE = on
        util.clear_cache()
        for win in self.windows:
            if win.icon_key:
                win.icon = sym(win.icon_key, win.icon_ascii)
        self.dirty = True

    def background_mode(self):
        b = self.cfg.get('background', 'auto')
        path = self.cfg.get('wallpaper') or ''
        if b == 'auto':
            if path and os.path.isfile(path):
                return 'image'
            if self.cfg.get('preset') == 'Matrix':
                return 'matrix'
            return 'logo' if self.cfg.get('desktop_logo', True) else 'plain'
        if b == 'image' and not (path and os.path.isfile(path)):
            return 'logo' if self.cfg.get('desktop_logo', True) else 'plain'
        return b

    def set_background(self, mode):
        self.cfg['background'] = mode
        self.cfg.save()
        if mode == 'matrix':
            self.rain = None
        self.wp_key = None
        self.load_wallpaper()
        self.dirty = True

    def choose_wallpaper(self):
        def ok(path):
            self.cfg['wallpaper'] = path
            self.cfg['background'] = 'image'
            self.cfg.save()
            self.wp_key = None
            self.load_wallpaper()
        self.pick_file(ok, 'open', title='Choose wallpaper', parent=self.focus)

    def rain_tick(self):
        if self.background_mode() != 'matrix':
            return
        dens = float(self.cfg.get('rain_density', 1.0))
        speed = float(self.cfg.get('rain_speed', 1.0))
        rnd = float(self.cfg.get('rain_random', 0.6))
        if self.rain is None or self.rain.w != self.w or self.rain.h != self.desk_h \
                or self.rain.key != (dens, speed, rnd):
            self.rain = Rain(self.w, max(1, self.desk_h), dens, speed, rnd)
        self.rain.step()
        self.dirty = True

    def load_wallpaper(self):
        from . import imaging
        path = self.cfg.get('wallpaper') or ''
        w, h = self.w, self.desk_h
        key = (path, w, h, tuple(self.theme.rgb), self.background_mode(),
               self.cfg.get('image_mode'), self.cfg.get('dither'),
               self.cfg.get('ascii_charset'), self.cfg.get('cell_aspect'))
        if key == self.wp_key:
            return
        self.wp_key = key
        self.wallpaper_cells = None
        self.dirty = True
        if self.background_mode() != 'image':
            return
        if not path or not os.path.isfile(path):
            return

        def work():
            img = imaging.load(path, 1400)
            return imaging.render_cover(self, img, w, max(1, h - 1),
                                        self.cfg.get('image_mode', 'blend'), 1.0, 1.0,
                                        self.cfg.get('dither', 'bayer4'), False), w, max(1, h - 1)

        def done(res):
            if self.wp_key != key:
                return
            self.wallpaper_cells = res
            self.dirty = True
        self.loop.run_in_thread(work, done)

    def settings_changed(self):
        """Tell apps and the wallpaper to re-read shared image settings."""
        for w in self.windows:
            if w.app:
                try:
                    w.app.on_theme()
                except Exception:
                    pass
        self.wp_key = None
        self.load_wallpaper()
        self.dirty = True

    def apply_theme(self):
        self.theme.reload()
        self.term.apply_palette()
        from . import util
        util.STRICT_ASCII = self.cfg['strict_ascii']
        self.sync_symbols()
        self.rain = None
        for w in self.windows:
            if w.app:
                try:
                    w.app.on_theme()
                except Exception:
                    pass
        self.layout()
        self.load_wallpaper()
        self.dirty = True

    def if_ip(self, name):
        try:
            import fcntl
            import struct
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915,
                                                    struct.pack('256s', name[:15].encode()))[20:24])
            finally:
                s.close()
        except OSError:
            return ''

    def refresh_tray(self):
        # network: prefer an up wifi link over a wired one
        best = None
        try:
            names = sorted(os.listdir('/sys/class/net'))
        except OSError:
            names = []
        for n in names:
            if n == 'lo' or n.startswith(('br-', 'docker', 'veth', 'virbr')):
                continue
            try:
                with open('/sys/class/net/%s/operstate' % n) as f:
                    if f.read().strip() != 'up':
                        continue
            except OSError:
                continue
            wifi = os.path.exists('/sys/class/net/%s/wireless' % n) or n.startswith(('wl', 'wlan'))
            cand = ('wifi' if wifi else 'lan', n, self.if_ip(n))
            if best is None or cand[0] == 'wifi':
                best = cand
        self.net = best
        # bluetooth adapter + rfkill state
        bt = None
        try:
            if os.path.isdir('/sys/class/bluetooth') and os.listdir('/sys/class/bluetooth'):
                bt = True
                if os.path.isdir('/sys/class/rfkill'):
                    for r in os.listdir('/sys/class/rfkill'):
                        base = '/sys/class/rfkill/' + r
                        try:
                            with open(base + '/type') as f:
                                if f.read().strip() != 'bluetooth':
                                    continue
                            with open(base + '/soft') as f:
                                soft = f.read().strip()
                            with open(base + '/hard') as f:
                                hard = f.read().strip()
                            bt = (soft == '0' and hard == '0')
                        except OSError:
                            continue
        except OSError:
            bt = None
        self.bt = bt
        try:
            from .audio import pick_sinks
            self.sound = bool(pick_sinks(self.cfg.get('audio_sink', 'auto')))
        except Exception:
            self.sound = False
        self.dirty = True

    def battery_text(self):
        cap, st = self.battery
        segs = 4
        filled = max(0, min(segs, int(round(cap / 100.0 * segs))))
        s = '[' + '=' * filled + ' ' * (segs - filled) + ']'
        if st == 'Charging':
            s += '+'
        return '%s %d%%' % (s, cap)

    # ---------------------------------------------------------- periodic
    def tick(self):
        now = time.monotonic()
        if self.toasts:
            self.toasts = [t for t in self.toasts if t[2] > now]
        self.dirty = True   # clock

    def read_battery(self):
        fake = os.environ.get('MONOCELL_FAKE_BATTERY')
        if fake:
            try:
                cap, st = fake.split(':', 1)
                self.battery = (int(cap), st)
                return
            except ValueError:
                pass
        base = '/sys/class/power_supply'
        bat = None
        try:
            for n in sorted(os.listdir(base)):
                p = os.path.join(base, n)
                try:
                    with open(os.path.join(p, 'type')) as f:
                        if f.read().strip() != 'Battery':
                            continue
                    with open(os.path.join(p, 'capacity')) as f:
                        cap = int(f.read().strip())
                    st = ''
                    try:
                        with open(os.path.join(p, 'status')) as f:
                            st = f.read().strip()
                    except OSError:
                        pass
                    bat = (cap, st)
                    break
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        self.battery = bat

    # ---------------------------------------------------------- drawing
    def shade(self, cv, x, y, w, h):
        th = self.theme
        sa = th.c('dim', 'shadow')
        dimf = th.r['dim']
        sh = th.r['shadow']
        cv.recolor(x, y, w, h, lambda a: dimf | (sh << 4))

    def render(self):
        th = self.theme
        buf = self.term.back
        cv = Canvas(buf)
        self.draw_desktop(cv)
        for win in self.windows:
            if win.minimized:
                continue
            self.draw_window(cv, win, win is self.focus)
        if self.tsel:
            w = self.tsel['win']
            if w.closed or w.minimized or w not in self.windows:
                self.tsel = None
            else:
                hl = th.r['bright'] | (th.r['sel'] << 4)
                for y, x0, x1 in self.tsel_rows():
                    cv.recolor(x0, y, x1 - x0 + 1, 1, lambda a: (a & ~0xff) | hl)
        if self.snap_preview:
            x, y, w, h = self.snap_preview
            acc = th.c('accent', 'accent')
            cv.recolor(x, y, w, 1, lambda a: (a & ~0xf0) | (th.r['accent'] << 4))
            cv.recolor(x, y + h - 1, w, 1, lambda a: (a & ~0xf0) | (th.r['accent'] << 4))
            cv.recolor(x, y, 1, h, lambda a: (a & ~0xf0) | (th.r['accent'] << 4))
            cv.recolor(x + w - 1, y, 1, h, lambda a: (a & ~0xf0) | (th.r['accent'] << 4))
        if not (self.focus and self.focus.borderless):
            self.draw_taskbar(cv)
        for p in self.popups:
            p.draw(cv)
        self.draw_toasts(cv)
        if self.term.mice:
            mx, my = self.mouse_pos
            if 0 <= mx < self.w and 0 <= my < self.h:
                cur = th.r['cursor']
                cv.set_attr(mx, my, 1, 1, th.c(th.contrast(cur), cur))
        self.term.flush()

    def draw_desktop(self, cv):
        th = self.theme
        cv.fill(0, self.desk_y, self.w, self.desk_h, th.c('desk_fg', 'desk'))
        mode = self.background_mode()
        if mode == 'image' and self.wallpaper_cells:
            cells, ww, wh = self.wallpaper_cells
            cv.sub(0, self.desk_y, self.w, self.desk_h).blit_cells(0, 0, cells)
            if self.cfg['desktop_logo'] and self.desk_h > 16 and self.w > 50:
                cv.put(2, self.desk_y + self.desk_h - 3, 'monocell', th.c('desk_fg', 'shadow'))
            return
        if mode == 'matrix':
            if self.rain is None:
                self.rain = Rain(self.w, max(1, self.desk_h))
            self.rain.draw(cv, self.desk_y, th)
            return
        if mode != 'logo':
            return
        if self.cfg['desktop_logo'] and self.desk_h > 12 and self.w > 44:
            from . import logo
            ww, hh, _sx = logo.measure_for(self.w)
            x = (self.w - ww) // 2
            y = self.desk_y + max(1, (self.desk_h - hh) // 2)
            logo.draw(cv, x, y, th)
            sub = 'right-click the desktop for more'
            cv.put((self.w - len(sub)) // 2, y + hh + 2, sub, th.c('dim', 'desk'))

    def win_buttons(self, win):
        """Return list of (part, x0, x1) for the title buttons (absolute x)."""
        out = []
        x = win.x + win.w - 1
        parts = ['close']
        if win.resizable:
            parts += ['max']
        if win.modal_for is None:
            parts += ['min']
        for p in parts:
            out.append((p, x - 3, x))
            x -= 3
        return out

    def draw_window(self, cv, win, active):
        th = self.theme
        x, y, w, h = win.rect()
        if win.borderless:
            cv.fill(x, y, w, h, th.c('text', 'win'))
            if win.app:
                sub = cv.sub(x, y, w, h)
                win.app.zones = []
                try:
                    win.app.draw(sub, True)
                except Exception as e:
                    log(traceback.format_exc())
                    sub.put(0, 0, 'draw error: %s' % e, th.c('red', 'win'))
            return
        lines = self.cfg['frame'] == 'lines'
        if self.cfg['shadows'] and not win.maximized and win.snap is None:
            self.shade(cv, x + w, y + 1, 1, h)
            self.shade(cv, x + 1, y + h, w, 1)
        body = th.c('text', 'win')
        cv.fill(x, y, w, h, body)
        tbg = 'title' if active else 'win'
        cv.fill(x, y, w, 1, th.c('text', tbg))
        if lines:
            ba = th.c('accent' if active else 'sel', 'win')
            for yy in range(y + 1, y + h - 1):
                cv.putc(x, yy, '|', ba)
                cv.putc(x + w - 1, yy, '|', ba)
            cv.put(x, y + h - 1, '+' + '-' * (w - 2) + '+', ba)
        else:
            if active:
                cv.putc(x, y, ' ', th.c('accent', 'accent'))
        # title text
        icon = win.icon
        cv.put(x + 2, y, icon, th.c('accent' if active else 'dim', tbg))
        btns = self.win_buttons(win)
        tw = btns[-1][1] - (x + 3 + len(icon)) - 1
        title = fit(win.title, max(0, tw))
        cv.put(x + 3 + len(icon), y, title, th.c('bright' if active else 'dim', tbg, 1 if active else 0))
        hp = self.hover_part
        for part, bx0, bx1 in btns:
            hov = hp and hp[0] is win and hp[1] == part
            g = {'close': ' x ', 'max': ' o ' if not win.snap else ' = ', 'min': ' - '}[part]
            if hov:
                a = th.c('bright', 'red') if part == 'close' else th.c('on_accent', 'accent')
            else:
                a = th.c('text' if active else 'dim', tbg)
            cv.put(bx0, y, g, a)
        # resize grip
        if win.resizable and not win.snap:
            gr = hp and hp[0] is win and hp[1] in ('br', 'b', 'r', 'bl', 'l', 'tl', 'tr')
            cv.put(x + w - 2, y + h - 1, '.:' if not lines else ':+', th.c('accent' if gr else 'sel', 'win'))
        if win.app:
            sub = cv.sub(x + 1, y + 1, w - 2, h - 2)
            win.app.zones = []
            try:
                win.app.draw(sub, active)
            except Exception as e:
                log(traceback.format_exc())
                sub.put(0, 0, 'draw error: %s' % e, th.c('red', 'win'))
        if active:
            # focused window sits one palette step above the others; media windows keep their colours
            wslot, tslot, sslot = th.r['win'], th.r['title'], th.r['sel']
            lift = {wslot: tslot, tslot: sslot}

            def _up(a):
                bg = (a >> 4) & 15
                return (a & ~0xf0) | (lift.get(bg, bg) << 4)
            if getattr(win.app, 'preserve_colors', False):
                cv.recolor(x, y, w, 1, _up)
            else:
                cv.recolor(x, y, w, h, _up)

    def draw_taskbar(self, cv):
        th = self.theme
        y = self.bar_y
        bar = th.c('text', 'bar')
        cv.fill(0, y, self.w, 1, bar)
        self.bar_zones = []
        start_open = any(isinstance(p, StartMenu) for p in self.popups)
        hov = self.hover_part
        s = ' :: '
        a = th.c('on_accent', 'accent') if start_open or (hov and hov[0] == 'bar' and hov[1] == 'start') else th.c('accent', 'raised', 1)
        cv.put(0, y, s, a)
        self.bar_zones.append((0, len(s), 'start'))
        # tray (right): status symbols, host, date, clock
        tray = []
        if self.bt is not None:
            tray.append(('bt', sym('bt', 'B'), 'accent' if self.bt else 'dim', 'bluetooth'))
        if self.net:
            kind, name, ip = self.net
            tray.append(('net', sym('wifi' if kind == 'wifi' else 'lan', ')))' if kind == 'wifi' else '[=]'),
                         'accent', name))
        if self.sound is not None:
            tray.append(('sound', sym('sound', '~'), 'accent' if self.sound else 'dim', 'audio'))
        if self.battery:
            cap, st = self.battery
            col = ('accent' if st == 'Charging' else 'red' if cap <= 15 and st == 'Discharging'
                   else 'yellow' if cap <= 30 else 'text')
            tray.append(('bat', self.battery_text(), col, 'battery'))
        tray.append(('host', self.hostname, 'dim', 'host'))
        now = time.localtime()
        fmt = ('%H:%M:%S' if self.cfg['clock_seconds'] else '%H:%M') if self.cfg['clock_24h'] else \
              ('%I:%M:%S %p' if self.cfg['clock_seconds'] else '%I:%M %p')
        tray.append(('date', time.strftime('%a %d %b', now), 'dim', 'date'))
        tray.append(('clock', time.strftime(fmt, now), 'bright', 'clock'))
        drop = ['date', 'host', 'bt', 'net', 'sound']
        tw = sum(len(t[1]) + 2 for t in tray) + 1
        while self.w - tw < 30 and drop:
            k = drop.pop(0)
            tray = [t for t in tray if t[0] != k]
            tw = sum(len(t[1]) + 2 for t in tray) + 1
        x = self.w - tw
        tx = x
        for key, txt, col, hint in tray:
            on = hov and hov[0] == 'bar' and hov[1] == key
            cv.put(tx, y, ' %s ' % txt, th.c('on_accent', 'accent') if on else th.c(col, 'bar'))
            self.bar_zones.append((tx, tx + len(txt) + 2, key))
            tx += len(txt) + 2
        # window buttons
        bx = len(s) + 1
        wins = [w for w in self.windows if w.modal_for is None]
        avail = x - bx - 1
        if wins:
            bw = max(6, min(22, avail // len(wins)))
            for win in sorted(wins, key=lambda w: w.id):
                if bx + bw > x - 1:
                    cv.put(bx, y, '..', th.c('dim', 'bar'))
                    break
                on = hov and hov[0] == 'bar' and hov[1] is win
                if win is self.focus or (self.focus and self.focus.modal_for is win):
                    a, ia = th.c('bright', 'raised'), th.c('accent', 'raised')
                elif on:
                    a, ia = th.c('bright', 'sel'), th.c('accent', 'sel')
                elif win.minimized:
                    a, ia = th.c('dim', 'bar'), th.c('dim', 'bar')
                else:
                    a, ia = th.c('text', 'bar'), th.c('dim', 'bar')
                if win.urgent:
                    a, ia = th.c('on_accent', 'accent'), th.c('on_accent', 'accent')
                cv.fill(bx, y, bw, 1, a)
                cv.put(bx + 1, y, win.icon[:3], ia)
                cv.put(bx + 1 + min(3, len(win.icon)) + 1, y, fit(win.title, bw - 6), a)
                self.bar_zones.append((bx, bx + bw, win))
                bx += bw + 1

    def draw_toasts(self, cv):
        th = self.theme
        y = self.desk_y + self.desk_h - 2 if self.cfg['taskbar'] == 'bottom' else self.desk_y + 1
        for text, color, _ in reversed(self.toasts):
            t = fit(text, min(60, self.w - 4))
            w = len(t) + 4
            x = self.w - w - 1
            cv.fill(x, y, w, 1, th.c('bright', 'raised'))
            cv.put(x, y, ' ', th.c('on_accent', color if color in th.r else 'accent'))
            cv.put(x + 2, y, t, th.c('bright', 'raised'))
            y += -1 if self.cfg['taskbar'] == 'bottom' else 1

    # ---------------------------------------------------------- hit testing
    def window_at(self, mx, my):
        for win in reversed(self.windows):
            if not win.minimized and win.contains(mx, my):
                return win
        return None

    def part_at(self, win, mx, my):
        if win.borderless:
            return 'content'
        x, y, w, h = win.rect()
        if my == y:
            for part, bx0, bx1 in self.win_buttons(win):
                if bx0 <= mx < bx1:
                    return part
            if win.resizable and not win.snap:
                if mx == x:
                    return 'tl'
                if mx == x + w - 1:
                    return 'tr'
            return 'title'
        if win.resizable and not win.snap:
            if my == y + h - 1:
                if mx <= x + 1:
                    return 'bl'
                if mx >= x + w - 2:
                    return 'br'
                return 'b'
            if mx == x:
                return 'l'
            if mx == x + w - 1:
                return 'r'
        if x < mx < x + w - 1 and y < my < y + h - 1:
            return 'content'
        return 'frame'

    # ---------------------------------------------------------- events
    def handle(self, ev):
        try:
            if isinstance(ev, Mouse):
                self.handle_mouse(ev)
            elif isinstance(ev, Key):
                self.handle_key(ev)
            elif isinstance(ev, Paste):
                if self.popups and hasattr(self.popups[-1], 'on_paste'):
                    self.popups[-1].on_paste(ev.text)
                elif self.focus and self.focus.app:
                    self.focus.app.on_paste(ev.text)
                self.dirty = True
        except Exception as e:
            log(traceback.format_exc())
            self.on_error(e)

    def handle_key(self, ev):
        k = ev.key
        self.dirty = True
        if k in ('f12', 'alt+ ', 'ctrl+alt+space', 'alt+ctrl+space'):
            self.toggle_start()
            return
        if self.popups:
            p = self.popups[-1]
            if not p.on_key(ev) and k in PASTE_KEYS and hasattr(p, 'on_paste') and self.clipboard:
                p.on_paste(self.clipboard)
            return
        win = self.focus
        if k in COPY_KEYS and self.tsel and not self.tsel['drag']:
            self.copy_text(self.tsel_text())
            return
        if k in ('alt+tab', 'alt+`', 'alt+n'):
            self.cycle(1)
            return
        if k in ('alt+shift+tab', 'alt+p'):
            self.cycle(-1)
            return
        if k == 'alt+enter':
            self.open('terminal')
            return
        if k == 'alt+d':
            self.show_desktop()
            return
        if k == 'alt+t':
            self.tile()
            return
        if len(k) == 5 and k.startswith('alt+') and k[4] in '123456789':
            wins = sorted([w for w in self.windows if w.modal_for is None], key=lambda w: w.id)
            i = int(k[4]) - 1
            if i < len(wins):
                self.activate(wins[i])
            return
        if win:
            if k in ('alt+q', 'alt+f4', 'alt+w'):
                self.close(win)
                return
            if k in ('alt+m', 'f11', 'alt+up'):
                if k == 'alt+up' and win.snap == 'max':
                    return
                self.toggle_max(win)
                return
            if k in ('alt+h', 'alt+down'):
                if k == 'alt+down' and win.snap:
                    self.unsnap(win)
                    self.notify_resize(win)
                else:
                    self.minimize(win)
                return
            if k in ('alt+left', 'alt+right') and win.resizable:
                if not win.snap:
                    win.restore = win.rect()
                self.apply_snap(win, 'left' if k == 'alt+left' else 'right')
                self.notify_resize(win)
                return
            if k.startswith('alt+shift+') or k.startswith('ctrl+alt+'):
                d = k.split('+')[-1]
                dx = {'left': -2, 'right': 2}.get(d, 0)
                dy = {'up': -1, 'down': 1}.get(d, 0)
                if dx or dy:
                    if win.snap:
                        win.snap = None
                        win.maximized = False
                    if k.startswith('alt+shift+') and win.resizable:
                        win.w = max(win.min_w, win.w + dx)
                        win.h = max(win.min_h, win.h + dy)
                        self.clamp(win)
                        self.notify_resize(win)
                    else:
                        win.x += dx
                        win.y += dy
                        self.clamp(win)
                    return
            if win.app:
                if win.app.on_key(ev):
                    return
                if k in PASTE_KEYS:
                    self.paste_into(win)
                    return
                if k in COPY_KEYS and k != 'ctrl+c':
                    self.copy_text(win.app.selection_text() or '')
                    return
                if ev.orig and ev.orig != k:
                    # the app does not know the console-detected modifier: plain key again
                    ev.key = ev.orig
                    if win.app.on_key(ev):
                        return
            if k == 'esc' and win.modal_for is not None:
                self.close(win)
            return
        if k == 'enter' or k == 'esc':
            self.toggle_start()

    def handle_mouse(self, ev):
        now = time.monotonic()
        mx, my = ev.x, ev.y
        if (mx, my) != self.mouse_pos:
            self.mouse_pos = (mx, my)
            if self.term.mice:
                self.dirty = True
        if self.middle_scroll is not None:
            win = self.middle_scroll
            if ev.kind == 'drag':
                self.middle_acc += ev.y - self.middle_last[1]
                self.middle_acc = max(-8.0, min(8.0, self.middle_acc))
                self.middle_last = (mx, my)
                n = 0
                while abs(self.middle_acc) >= 1.0 and n < 4:
                    kind = 'wheeldown' if self.middle_acc > 0 else 'wheelup'
                    self.middle_acc -= 1.0 if self.middle_acc > 0 else -1.0
                    n += 1
                    if win.closed or not win.app:
                        continue
                    ox, oy = self.content_origin(win)
                    rx = max(0, min(win.cw - 1, mx - ox))
                    ry = max(0, min(win.ch - 1, my - oy))
                    win.app.on_mouse(Mouse(kind, rx, ry, 0, ev.mods))
                    self.middle_moved = True
                if n:
                    self.dirty = True
                return
            if ev.kind == 'up':
                self.middle_scroll = None
                if not self.middle_moved and win.app and not win.closed:
                    # a plain middle click: let the app use it (terminal paste, browser
                    # "open in new window"), otherwise paste the clipboard like X11
                    ox, oy = self.content_origin(win)
                    rx = max(0, min(win.cw - 1, mx - ox))
                    ry = max(0, min(win.ch - 1, my - oy))
                    used = win.app.on_mouse(Mouse('down', rx, ry, 1, ev.mods, ev.clicks))
                    win.app.on_mouse(Mouse('up', rx, ry, 1, ev.mods, ev.clicks))
                    if not used and self.clipboard and self.accepts_paste(win.app):
                        win.app.on_paste(self.clipboard)
                self.dirty = True
                return
        if self.tsel and self.tsel['drag']:
            if ev.kind == 'drag':
                self.tsel['b'] = (mx, my)
                self.dirty = True
                return
            if ev.kind == 'up':
                self.tsel['drag'] = False
                t = self.tsel_text()
                if t and self.tsel['a'] != self.tsel['b']:
                    self.copy_text(t)
                else:
                    self.tsel = None
                self.dirty = True
                return
        if ev.kind == 'down' and self.tsel:
            self.tsel = None
            self.dirty = True
        if ev.kind == 'down':
            lt, lx, ly, lb = self.last_click
            if now - lt < DOUBLE_CLICK and (lx, ly, lb) == (mx, my, ev.button):
                ev.clicks = 2
                self.last_click = (0, -1, -1, -1)
            else:
                self.last_click = (now, mx, my, ev.button)
        # ongoing window drag
        if self.drag:
            self.do_drag(ev)
            return
        # captured app (button held inside content)
        if self.mouse_capture and ev.kind in ('drag', 'up'):
            win = self.mouse_capture
            if ev.kind == 'up':
                self.mouse_capture = None
            if win.app and not win.closed:
                win.app.on_mouse(ev.moved(win.x + 1, win.y + 1))
            self.dirty = True
            return
        # popups
        if self.popups:
            p = self.popups[-1]
            if p.contains(mx, my):
                p.on_mouse(ev)
                self.dirty = True
                return
            if ev.kind == 'down':
                self.close_popup()
                # clicking the start button again just closes the menu
                if my == self.bar_y and isinstance(p, StartMenu):
                    for x0, x1, key in self.bar_zones:
                        if x0 <= mx < x1 and key == 'start':
                            return
            elif ev.kind in ('move', 'drag', 'up', 'wheelup', 'wheeldown'):
                return
        # taskbar
        if my == self.bar_y:
            key = None
            for x0, x1, k in self.bar_zones:
                if x0 <= mx < x1:
                    key = k
            hp = ('bar', key) if key is not None else None
            if hp != self.hover_part:
                self.hover_part = hp
                self.dirty = True
            if ev.kind == 'down':
                self.bar_click(key, ev)
            elif ev.kind in ('wheelup', 'wheeldown'):
                self.cycle(1 if ev.kind == 'wheeldown' else -1)
            return
        win = self.window_at(mx, my)
        if win is None:
            if self.hover_part is not None:
                self.hover_part = None
                self.dirty = True
            if ev.kind == 'down':
                self.set_focus(None)
                self.dirty = True
                if ev.button == 2:
                    self.desktop_menu(mx, my)
            return
        # modal blocking
        for w in reversed(self.windows):
            if w.modal_for is win and not w.closed:
                if ev.kind == 'down':
                    self.activate(w)
                return
        part = self.part_at(win, mx, my)
        hp = (win, part) if part not in ('content', 'frame') else None
        if hp != self.hover_part:
            self.hover_part = hp
            self.dirty = True
        if ev.kind == 'down':
            if win is not self.focus or self.windows[-1] is not win:
                self.activate(win)
            if ev.button == 1 and part == 'content' and win.app and 'alt' not in ev.mods:
                self.middle_scroll = win
                self.middle_last = (mx, my)
                self.middle_acc = 0.0
                self.middle_moved = False
                self.dirty = True
                return
            if (ev.button == 0 and part == 'content' and win.app and 'shift' in ev.mods
                    and not win.app.shift_select):
                # Shift+drag: select any text on screen (like gpm), copied on release
                self.tsel = {'win': win, 'a': (mx, my), 'b': (mx, my), 'drag': True,
                             'block': 'ctrl' in ev.mods}      # Ctrl+Shift+drag: rectangle
                self.dirty = True
                return
            if part == 'content' and 'alt' not in ev.mods:
                if win.app:
                    self.mouse_capture = win if ev.button == 0 else None
                    npop = len(self.popups)
                    used = win.app.on_mouse(ev.moved(win.x + 1, win.y + 1))
                    if ev.button == 2 and not used and len(self.popups) == npop:
                        self.edit_menu(win, mx, my)
            elif ev.button == 0:
                if part == 'title' or 'alt' in ev.mods:
                    if ev.clicks >= 2 and part == 'title':
                        self.toggle_max(win)
                    else:
                        self.drag = ('move', win, mx, my, win.rect(), False)
                elif part in ('close', 'max', 'min'):
                    self.drag = ('button', win, part)
                elif part in ('tl', 'tr', 'bl', 'br', 'b', 'l', 'r'):
                    self.drag = ('resize', win, mx, my, win.rect(), part)
            elif ev.button == 2 and part == 'title':
                self.window_menu(win, mx, my)
            self.dirty = True
            return
        if part == 'content' and win.app:
            if ev.kind in ('wheelup', 'wheeldown', 'move') or ev.kind == 'drag':
                if ev.kind != 'move' or win is self.focus or True:
                    win.app.on_mouse(ev.moved(win.x + 1, win.y + 1))
        elif win.app and win.app.hover is not None:
            win.app.hover = None
            self.dirty = True

    def bar_click(self, key, ev):
        self.dirty = True
        if key == 'start':
            self.toggle_start()
        elif isinstance(key, Window):
            if ev.button == 2:
                self.window_menu(key, ev.x, self.bar_y)
            elif key is self.focus and not key.minimized:
                self.minimize(key)
            else:
                self.activate(key)
        elif key in ('clock', 'date'):
            self.open('calendar')
        elif key in ('bat', 'host'):
            self.open('system')
        elif key == 'net':
            kind, name, ip = self.net
            self.toast('%s %s%s' % ('WiFi' if kind == 'wifi' else 'LAN', name, ('  ' + ip) if ip else ''))
        elif key == 'bt':
            self.toast('Bluetooth: ' + ('on' if self.bt else 'off'))
        elif key == 'sound':
            from .audio import pick_sinks
            sinks = pick_sinks(self.cfg.get('audio_sink', 'auto'))
            self.toast('Audio: ' + (', '.join(sinks) if sinks else 'no output found'))

    def do_drag(self, ev):
        d = self.drag
        self.dirty = True
        if d[0] == 'button':
            win, part = d[1], d[2]
            if ev.kind == 'up':
                self.drag = None
                if win.contains(ev.x, ev.y) and self.part_at(win, ev.x, ev.y) == part:
                    if part == 'close':
                        self.close(win)
                    elif part == 'max':
                        self.toggle_max(win)
                    elif part == 'min':
                        self.minimize(win)
            return
        kind, win, sx, sy, (rx, ry, rw, rh), extra = d
        dx, dy = ev.x - sx, ev.y - sy
        if kind == 'move':
            if win.snap and (dx or dy):
                # tear a snapped window off
                frac = (sx - win.x) / max(1, win.w)
                self.unsnap(win)
                win.x = ev.x - int(frac * win.w)
                win.y = max(self.desk_y, ev.y)
                self.drag = ('move', win, ev.x, ev.y, win.rect(), True)
                self.notify_resize(win)
                return
            win.x = max(0, min(rx + dx, max(0, self.w - 10)))
            win.y = max(self.desk_y, min(ry + dy, self.desk_y + self.desk_h - 2))
            # snap preview
            self.snap_preview = None
            snap = self.snap_target(ev.x, ev.y) if win.resizable else None
            if snap:
                tmp = Window(self, '', 0, 0, 1, 1)
                self.apply_snap(tmp, snap)
                self.snap_preview = tmp.rect()
            if ev.kind == 'up':
                self.drag = None
                self.snap_preview = None
                if snap:
                    win.restore = (rx, ry, rw, rh) if not win.restore or not extra else win.restore
                    win.restore = (win.x, max(self.desk_y, win.y), rw, rh)
                    self.apply_snap(win, snap)
                    self.notify_resize(win)
        elif kind == 'resize':
            part = extra
            x, y, w, h = rx, ry, rw, rh
            if 'r' in part:
                w = max(win.min_w, rw + dx)
            if 'b' in part:
                h = max(win.min_h, min(rh + dy, self.desk_y + self.desk_h - ry))
            if 'l' in part:
                nw = max(win.min_w, rw - dx)
                x = rx + rw - nw
                w = nw
            if 't' in part:
                nh = max(win.min_h, rh - dy)
                ny = ry + rh - nh
                if ny < self.desk_y:
                    ny = self.desk_y
                    nh = ry + rh - ny
                y, h = ny, nh
            if (x, y, w, h) != win.rect():
                win.x, win.y, win.w, win.h = x, y, w, h
                self.notify_resize(win)
            if ev.kind == 'up':
                self.drag = None

    def snap_target(self, mx, my):
        top = self.desk_y
        bottom = self.desk_y + self.desk_h - 1
        if mx <= 2:
            if my <= top + 1:
                return 'tl'
            if my >= bottom - 1:
                return 'bl'
            return 'left'
        if mx >= self.w - 3:
            if my <= top + 1:
                return 'tr'
            if my >= bottom - 1:
                return 'br'
            return 'right'
        if my <= top:
            return 'max'
        return None

    def window_menu(self, win, mx, my):
        items = [
            MenuItem('Restore' if win.snap else 'Maximize', lambda: self.toggle_max(win), 'Alt+M', enabled=win.resizable),
            MenuItem('Minimize', lambda: self.minimize(win), 'Alt+H', enabled=win.modal_for is None),
            MenuItem('Snap left', lambda: self.snap_key(win, 'left'), 'Alt+Left', enabled=win.resizable),
            MenuItem('Snap right', lambda: self.snap_key(win, 'right'), 'Alt+Right', enabled=win.resizable),
            None,
            MenuItem('Close', lambda: self.close(win), 'Alt+Q'),
        ]
        above = my == self.bar_y and self.cfg['taskbar'] == 'bottom'
        self.menu(items, mx, my - 1 if above else my + 1, above=above)

    def snap_key(self, win, mode):
        if not win.snap:
            win.restore = win.rect()
        self.apply_snap(win, mode)
        self.notify_resize(win)
        self.dirty = True

    def desktop_menu(self, mx, my):
        bg = self.background_mode()
        items = [
            MenuItem('Terminal', lambda: self.open('terminal'), 'Alt+Enter', sym('terminal', '>_')),
            MenuItem('Files', lambda: self.open('files'), '', sym('files', '[/]')),
            MenuItem('Browser', lambda: self.open('browser'), '', sym('browser', '(@)')),
            None,
            MenuItem('Background: logo', lambda: self.set_background('logo'), checked=bg == 'logo'),
            MenuItem('Background: plain', lambda: self.set_background('plain'), checked=bg == 'plain'),
            MenuItem('Background: matrix rain', lambda: self.set_background('matrix'), checked=bg == 'matrix'),
            MenuItem('Background: wallpaper...', self.choose_wallpaper, checked=bg == 'image'),
            None,
            MenuItem('Tile windows', self.tile, 'Alt+T', '##'),
            MenuItem('Cascade windows', self.cascade, '', '//'),
            MenuItem('Show desktop', self.show_desktop, 'Alt+D', '__'),
            None,
            MenuItem('Settings', lambda: self.open('settings'), '', sym('settings', '{o}')),
        ]
        self.menu(items, mx, my)

    # ---------------------------------------------------------- frame pacing
    def idle(self):
        if not self.dirty:
            return None
        now = time.monotonic()
        dt = now - self.last_frame
        if dt < 1 / 60.0:
            return 1 / 60.0 - dt
        self.dirty = False
        self.last_frame = now
        self.render()
        return None


HELP_TEXT = """Mouse: drag title bars to move, drag edges/corners to resize, double-click a title to maximize. Drag a window to the screen top to maximize, to the left/right edge to tile half, to a corner for a quarter. Alt+drag moves from anywhere. Right-click title bars, the taskbar or the desktop for menus.

F12 / Alt+Space   start menu (type to search)
Alt+Tab / Alt+N   next window      Alt+P  previous
Alt+1..9          focus window N
Alt+Q             close window     Alt+H  minimize
Alt+M / F11       maximize         Alt+D  show desktop
Alt+Left/Right    snap half        Alt+T  tile all
Alt+Shift+Arrows  resize           Ctrl+Alt+Arrows  move
Alt+Enter         new terminal

Hold the middle button and move the mouse/trackpoint to scroll the window
under the pointer.  Copy & paste: Shift+drag selects text in any window and
copies it (Ctrl+Shift+drag: rectangle); in the terminal and editor a plain
drag selects.  Ctrl+Shift+C copies, Ctrl+Shift+V / Shift+Insert / a middle
click pastes - also on the Linux console.  Right-click shows Copy/Paste.
monocell-clip prints or sets the shared clipboard in any shell.  KDE Connect (System menu): pair the phone, send or
pull the clipboard, share files.  Browser: Ctrl+L address, Tab links, Enter
opens, Ctrl+D bookmark, Ctrl+F find.  Music: Space plays, n/p next.
"""
