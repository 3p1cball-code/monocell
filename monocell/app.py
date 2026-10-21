"""App base class and lazy registry."""
import importlib


class App:
    name = 'app'
    title = 'App'
    icon = '*'
    size = (60, 20)          # content size (cols, rows)
    min_size = (16, 3)
    resizable = True
    single = False           # only one instance (focus existing)
    shift_select = False     # True: the app uses Shift+click itself (no screen text selection)

    def __init__(self, wm, win, **kw):
        self.wm = wm
        self.win = win
        self.th = wm.theme
        self.zones = []
        self.hover = None
        self.pressed = None

    # --- convenience ---
    def invalidate(self):
        self.wm.dirty = True

    def every(self, interval, cb):
        return self.wm.loop.every(interval, cb, owner=self)

    def later(self, delay, cb):
        return self.wm.loop.call_later(delay, cb, owner=self)

    def thread(self, fn, done=None, error=None):
        def ok(res):
            if not self.win.closed and done:
                done(res)
        def bad(e):
            if not self.win.closed:
                if error:
                    error(e)
                else:
                    self.wm.toast('%s: %s' % (self.title, e), 'red')
        return self.wm.loop.run_in_thread(fn, ok, bad)

    def set_borderless(self, on):
        """True fullscreen: no title bar, no taskbar, exact cell colours."""
        w = self.win
        if on == w.borderless:
            return
        if on:
            w.restore_fs = (w.x, w.y, w.w, w.h, w.snap)
            w.borderless = True
            w.snap = None
            w.x, w.y, w.w, w.h = 0, 0, self.wm.w, self.wm.h
        else:
            w.borderless = False
            if getattr(w, 'restore_fs', None):
                w.x, w.y, w.w, w.h, w.snap = w.restore_fs
            self.wm.clamp(w)
        self.wm.notify_resize(w)
        self.wm.dirty = True

    def set_title(self, t):
        self.win.title = t
        self.invalidate()

    @property
    def cw(self):
        return self.win.cw

    @property
    def ch(self):
        return self.win.ch

    def focused(self):
        return self.wm.focus is self.win

    # --- clickable zones registered while drawing ---
    zone_off = (0, 0)

    def zone(self, x, y, w, h, key):
        ox, oy = self.zone_off
        self.zones.append((x + ox, y + oy, w, h, key))

    def zone_at(self, x, y):
        for zx, zy, zw, zh, key in reversed(self.zones):
            if zx <= x < zx + zw and zy <= y < zy + zh:
                return key
        return None

    def track_hover(self, ev):
        """Update hover from a mouse event; returns zone key under pointer."""
        k = self.zone_at(ev.x, ev.y)
        if k != self.hover:
            self.hover = k
            self.invalidate()
        return k

    # --- overridables ---
    def draw(self, cv, focused):
        pass

    def on_key(self, ev):
        return False

    def on_mouse(self, ev):
        k = self.track_hover(ev)
        if ev.kind == 'down' and ev.button == 0:
            self.pressed = k
            if k is not None:
                self.on_click(k, ev)
                self.invalidate()
                return True
        elif ev.kind == 'up':
            self.pressed = None
        return False

    def on_click(self, key, ev):
        pass

    def on_paste(self, text):
        pass

    def selection_text(self):
        """Text the app has selected itself (used by Copy / Ctrl+Shift+C)."""
        return ''

    def on_resize(self, w, h):
        pass

    def on_focus(self, focused):
        pass

    def on_close(self):
        return True

    def on_theme(self):
        pass

    def close(self):
        self.wm.close(self.win)


# name -> (module, class, icon, label, category)
REGISTRY = [
    ('files', 'files', 'Files', '[/]', 'File manager', 'Apps'),
    ('browser', 'browser', 'Browser', '(@)', 'Web', 'Apps'),
    ('terminal', 'terminal', 'Terminal', '>_ ', 'Terminal', 'Apps'),
    ('editor', 'editor', 'Editor', '[=]', 'Text editor', 'Office'),
    ('writer', 'writer', 'Writer', '[W]', 'Documents', 'Office'),
    ('sheet', 'sheet', 'Sheets', '[#]', 'Spreadsheet', 'Office'),
    ('pdf', 'pdf', 'PDF', '[P]', 'PDF reader', 'Office'),
    ('calc', 'calc', 'Calculator', '[+]', 'Calculator', 'Office'),
    ('calendar', 'calendar', 'Calendar', '[31', 'Calendar', 'Office'),
    ('viewer', 'viewer', 'Pictures', '[*]', 'Image viewer', 'Media'),
    ('video', 'video', 'Video', '[>]', 'Video player', 'Media'),
    ('youtube', 'youtube', 'YouTube', '[Y]', 'Search & play YouTube', 'Media'),
    ('music', 'music', 'Music', '(~)', 'Music player', 'Media'),
    ('system', 'system', 'System', '[i]', 'System monitor', 'System'),
    ('kdeconnect', 'kdeconnect', 'KDE Connect', '[K]', 'Clipboard & file transfer', 'System'),
    ('settings', 'settings', 'Settings', '{o}', 'Settings', 'System'),
    ('opencode', 'opencode', 'opencode', '[O]', 'Agent TUI in a terminal', 'Tools'),
    ('tailscale', 'tailscale', 'Tailscale', '[T]', 'Tailnet status & connect', 'Tools'),
]


def app_info(name):
    for row in REGISTRY:
        if row[0] == name:
            return row
    return None


def load_app(name):
    row = app_info(name)
    mod = importlib.import_module('monocell.apps.' + (row[1] if row else name))
    return mod.APP
