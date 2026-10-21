"""Picture viewer: images rebuilt from ASCII + 16 colours (blend / pixel / ascii / mono)."""
import os

from .. import imaging
from ..app import App
from ..util import HOME, fit, human_size
from ..widgets import button

MODES = ['blend', 'pixel', 'grey', 'ascii', 'mono']
DITHERS = ['bayer4', 'bayer8', 'fs', 'none']


class Viewer(App):
    name = 'viewer'
    preserve_colors = True
    title = 'Pictures'
    icon = '[*]'
    size = (90, 32)
    min_size = (24, 6)

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.path = None
        self.img = None
        self.err = ''
        self.loading = False
        self.zoom = 1.0
        self.panx = self.pany = 0
        self.cells = None
        self.key = None
        self.drag = None
        self.siblings = []
        self.chrome = True
        if path:
            self.open(path)

    def pick(self):
        self.wm.pick_file(self.open, 'open', start=os.path.join(HOME, 'Pictures') if os.path.isdir(
            os.path.join(HOME, 'Pictures')) else HOME, parent=self.win, title='Open image')

    def open(self, path):
        self.path = path
        self.img = None
        self.cells = None
        self.key = None
        self.err = ''
        self.zoom = 1.0
        self.panx = self.pany = 0
        self.loading = True
        self.set_title(os.path.basename(path))
        d = os.path.dirname(path)
        try:
            self.siblings = sorted([os.path.join(d, n) for n in os.listdir(d) if imaging.is_image(n)],
                                   key=lambda p: os.path.basename(p).lower())
        except OSError:
            self.siblings = [path]

        def done(img):
            if self.path == path:
                self.img = img
                self.loading = False
                self.invalidate()

        def fail(e):
            self.err = str(e)
            self.loading = False
            self.invalidate()
        self.thread(lambda: imaging.load(path, 1600), done, fail)

    @property
    def mode(self):
        return self.wm.cfg.get('image_mode', 'blend')

    @property
    def dither(self):
        return self.wm.cfg.get('dither', 'bayer4')

    def step(self, d):
        if not self.siblings or not self.path:
            return
        try:
            i = self.siblings.index(self.path)
        except ValueError:
            i = 0
        self.open(self.siblings[(i + d) % len(self.siblings)])

    def on_theme(self):
        self.key = None

    def area(self, w, h):
        return (0, 2, w, h - 3) if self.chrome else (0, 0, w, h)

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        ax, ay, aw, ah = self.area(w, h)
        cv.fill(ax, ay, aw, ah, th.c('dim', 'shadow'))
        if self.chrome:
            x = 0
            for label, key in (('Open', 'open'), ('<', 'prev'), ('>', 'next'), ('-', 'zout'), ('+', 'zin'),
                               ('Fit', 'fit'), (self.mode, 'mode'), (self.dither, 'dither'),
                               (self.wm.cfg.get('ascii_charset', 'full'), 'chars'), ('Full', 'full')):
                if x + len(label) + 3 > w:
                    break
                x += button(self, cv, x, 0, label, key, active=(key == 'mode')) + 1
        if not self.path:
            cv.put(ax + 2, ay + max(1, ah // 2 - 2), 'No image open', th.c('bright', 'shadow'))
            cv.put(ax + 2, ay + max(1, ah // 2 - 1), 'Blend, pixel, grey, ASCII or mono - pick a file to start.',
                   th.c('dim', 'shadow'))
            button(self, cv, ax + 2, ay + max(1, ah // 2 + 1), 'Open image', 'open', primary=True)
        elif self.err:
            cv.put(ax + 2, ay + 1, fit('Cannot open image: ' + self.err, aw - 4), th.c('red', 'shadow'))
        elif self.loading:
            cv.put(ax + 2, ay + 1, 'Loading ' + os.path.basename(self.path or ''), th.c('dim', 'shadow'))
        elif self.img:
            key = (self.path, aw, ah, self.mode, self.dither, self.zoom, tuple(th.rgb),
                   self.wm.cfg.get('cell_aspect'), self.wm.cfg.get('ascii_charset'))
            if key != self.key or not self.cells:
                self.key = key
                max_w, max_h = aw, ah
                cw, chh = imaging.fit_size(self.img.w, self.img.h, max_w, max_h, imaging.cell_aspect(self.wm))
                cw, chh = max(1, int(cw * self.zoom)), max(1, int(chh * self.zoom))
                # cap the work for extreme zoom
                cw, chh = min(cw, 1200), min(chh, 600)
                rgb = self.img.scaled(cw, chh)
                self.cells = (imaging.render(self.wm, rgb, cw, chh, self.mode, self.dither), cw, chh)
            cells, cw, chh = self.cells
            self.panx = max(0, min(self.panx, cw - aw))
            self.pany = max(0, min(self.pany, chh - ah))
            ox = ax + max(0, (aw - cw) // 2) - (self.panx if cw > aw else 0)
            oy = ay + max(0, (ah - chh) // 2) - (self.pany if chh > ah else 0)
            cv.sub(ax, ay, aw, ah).blit_cells(ox - ax, oy - ay, cells)
            self.zone(ax, ay, aw, ah, 'img')
        if self.chrome:
            info = ''
            if self.img:
                try:
                    size = human_size(os.path.getsize(self.path))
                except OSError:
                    size = ''
                pos = ''
                if self.path in self.siblings:
                    pos = '%d/%d  ' % (self.siblings.index(self.path) + 1, len(self.siblings))
                info = '%s%dx%d px  %s  zoom %d%%' % (pos, self.img.w, self.img.h, size, self.zoom * 100)
            cv.put(0, h - 1, fit(os.path.basename(self.path or ''), max(1, w - len(info) - 2)), th.c('text', 'win'))
            cv.put(w - len(info), h - 1, info, th.c('dim', 'win'))

    def on_click(self, key, ev):
        if key == 'open':
            self.pick()
        elif key == 'prev':
            self.step(-1)
        elif key == 'next':
            self.step(1)
        elif key == 'zin':
            self.zoom = min(8.0, self.zoom * 1.5)
        elif key == 'zout':
            self.zoom = max(0.25, self.zoom / 1.5)
        elif key == 'fit':
            self.zoom = 1.0
        elif key == 'mode':
            cur = self.mode if self.mode in MODES else MODES[0]
            self.wm.cfg['image_mode'] = MODES[(MODES.index(cur) + 1) % len(MODES)]
            self.wm.cfg.save()
            self.wm.settings_changed()
        elif key == 'dither':
            cur = self.dither if self.dither in DITHERS else DITHERS[0]
            self.wm.cfg['dither'] = DITHERS[(DITHERS.index(cur) + 1) % len(DITHERS)]
            self.wm.cfg.save()
            self.wm.settings_changed()
        elif key == 'chars':
            opts = ['full', 'simple', 'shades']
            cur = self.wm.cfg.get('ascii_charset', 'full')
            new = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else 'full'
            self.wm.cfg['ascii_charset'] = new
            self.wm.cfg.save()
            self.wm.settings_changed()
        elif key == 'full':
            self.set_fullscreen(self.chrome)
        elif key == 'img':
            if ev.clicks >= 2:
                self.chrome = not self.chrome
            else:
                self.drag = (ev.x, ev.y, self.panx, self.pany)

    def set_fullscreen(self, on):
        if on:
            self.chrome = False
            self.zoom = 1.0
            self.panx = self.pany = 0
            self.set_borderless(True)
            self.wm.toast('Fullscreen - Esc or F leaves.', 'accent', 2.0)
        else:
            self.chrome = True
            self.set_borderless(False)
        self.key = None
        self.invalidate()

    def on_mouse(self, ev):
        if ev.kind == 'drag' and self.drag:
            sx, sy, px, py = self.drag
            self.panx = px - (ev.x - sx)
            self.pany = py - (ev.y - sy)
            self.invalidate()
            return True
        if ev.kind == 'up':
            self.drag = None
        if ev.kind == 'wheelup':
            if 'ctrl' in ev.mods or self.zoom > 1:
                self.zoom = min(8.0, self.zoom * 1.25)
            else:
                self.step(-1)
            self.invalidate()
            return True
        if ev.kind == 'wheeldown':
            if 'ctrl' in ev.mods or self.zoom > 1:
                self.zoom = max(0.25, self.zoom / 1.25)
            else:
                self.step(1)
            self.invalidate()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k in ('right', 'n', ' ', 'pgdn') and self.zoom <= 1:
            self.step(1)
        elif k in ('left', 'p', 'backspace', 'pgup') and self.zoom <= 1:
            self.step(-1)
        elif k in ('left', 'right', 'up', 'down'):
            dx = {'left': -4, 'right': 4}.get(k, 0)
            dy = {'up': -2, 'down': 2}.get(k, 0)
            self.panx += dx
            self.pany += dy
        elif k in ('+', '='):
            self.on_click('zin', None)
        elif k == '-':
            self.on_click('zout', None)
        elif k in ('0', 'z'):
            self.zoom = 1.0
        elif k == 'm':
            self.on_click('mode', None)
        elif k == 'd':
            self.on_click('dither', None)
        elif k == 'c':
            self.on_click('chars', None)
        elif k in ('f', 'enter'):
            self.set_fullscreen(self.chrome)
        elif k == 'esc' and not self.chrome:
            self.set_fullscreen(False)
        elif k == 'ctrl+o' or k == 'o':
            self.pick()
        else:
            return False
        self.invalidate()
        return True

    def reopen(self, path=None, **kw):
        if path:
            self.open(path)


APP = Viewer
