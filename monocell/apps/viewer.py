"""Picture viewer: images rebuilt from ASCII + 16 colours (blend / pixel / ascii / mono).

On the Linux console `g` (or the Gfx button) shows the picture in the same real
graphics mode as the video player: the console leaves text mode, the framebuffer
is written directly, and the picture goes through the identical palette/dither/
scanline chain (fbmode), as hard pixels at a quarter of the screen resolution."""
import os
import time

from .. import fbmode, imaging
from ..app import App
from ..util import HOME, ascii_upper, fit, human_size, log
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
        self.gfx = None            # fbmode.Session while the graphics mode is on
        self.pipe = None           # fbmode.StillPipeline
        self.gpal = None           # (path, tuned VGA palette file)
        self.gtuning = None        # path whose palette is being computed
        self.gzoom = 1.0
        self.gpanx = self.gpany = 0
        self.gdirty = False
        self.gpipe_fails = 0
        self.gdrag = None
        self.gchrome = True        # chrome state to go back to
        if path:
            self.open(path)
        self.every(0.04, self.gfx_tick)
        self.every(0.5, self.gfx_idle)

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
        self.gzoom = 1.0
        self.gpanx = self.gpany = 0
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
                if self.gfx:
                    self.tune_palette()
                    self.gfx_request()

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
                               (self.wm.cfg.get('ascii_charset', 'full'), 'chars'), ('Full', 'full'),
                               ('Gfx', 'gfx')):
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
        elif key == 'gfx':
            self.enter_gfx()
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
        if self.gfx:
            return self.gfx_mouse(ev)
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
        if self.gfx:
            return self.gfx_key(ev)
        if k == 'g':
            self.enter_gfx()
            return True
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

    # ------------------------------------------------------------ graphics mode
    def gfx_settings(self):
        cfg = self.wm.cfg
        look = cfg.get('gfx_look', 'vga')
        look = fbmode.OLD_LOOKS.get(look, look)
        look = look if look in fbmode.LOOKS else 'vga'
        return look, fbmode.SCALE, cfg.get('gfx_dither', 'bayer'), \
            bool(cfg.get('gfx_scanlines', False))

    def gfx_label(self):
        look, scale, dither, scan = self.gfx_settings()
        cw, ch = fbmode.canvas(self.gfx.screen, scale) if self.gfx else (0, 0)
        return '%s  %dX%d  %s%s' % (fbmode.LOOKS[look][0], cw, ch, dither,
                                    '  SCANLINES' if scan else '')

    def gfx_note(self, *extra, secs=3.0):
        if self.gfx:
            self.gfx.note([self.gfx_label()] + [e for e in extra if e], secs)

    def enter_gfx(self):
        if self.gfx:
            return
        if not self.path:
            self.wm.toast('Graphics mode needs a picture', 'red')
            return
        ok, why = fbmode.available(self.wm.term)
        if not ok:
            self.wm.toast(why, 'red')
            return
        th = self.th
        rgb = th.rgb
        colours = {'bg': rgb[th.r['shadow']], 'fg': rgb[th.r['bright']],
                   'dim': rgb[th.r['dim']], 'accent': rgb[th.r['accent']],
                   'field': rgb[th.r['field']]}
        try:
            sess = fbmode.Session(self.wm.term.fd_out, fbmode.open_framebuffer(), colours)
        except OSError as e:
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        self.gchrome = self.chrome
        if self.chrome:
            self.chrome = False
            self.set_borderless(True)
        try:
            sess.start()
        except OSError as e:
            sess.close()
            if self.gchrome:
                self.chrome = True
                self.set_borderless(False)
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        self.gfx = sess
        self.wm.enter_gfx(self)
        self.gzoom = 1.0
        self.gpanx = self.gpany = 0
        try:
            self.start_pipe()
        except OSError as e:
            self.leave_gfx()
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        tuning = self.tune_palette()
        self.gfx_note('G EXIT  C LOOK  D DITHER  S SCANLINES  +/- ZOOM  N/P NEXT',
                      'TUNING THE PALETTE...' if tuning else None, secs=5.0)
        self.gfx_request()

    def leave_gfx(self):
        if not self.gfx:
            return
        self.stop_pipe()
        sess, self.gfx = self.gfx, None
        sess.close()
        self.wm.leave_gfx()
        if self.gchrome:
            self.chrome = True
            self.set_borderless(False)
        self.key = None
        self.invalidate()

    def on_close(self):
        self.leave_gfx()
        return True

    def start_pipe(self):
        self.stop_pipe()
        g = self.gfx
        look, scale, dither, scan = self.gfx_settings()
        cw, ch = fbmode.canvas(g.screen, scale)
        pal = self.gpal[1] if (self.gpal and self.gpal[0] == self.path
                               and look == 'vga') else None
        self.pipe = fbmode.StillPipeline(
            (cw, ch), g.fb, theme_rgb=self.th.rgb,
            accent=self.th.rgb[self.th.r['accent']], look=look, scale=scale,
            dither=dither, scanlines=scan, vga_palette=pal)
        g.scale = scale

    def stop_pipe(self):
        if self.pipe:
            self.pipe.close()
            self.pipe = None

    def tune_palette(self):
        """VGA: the picture's own 256 colours, computed once in the background."""
        path = self.path
        if self.gfx_settings()[0] != 'vga' or not path or not self.img:
            return False
        if self.gpal and self.gpal[0] == path:
            return False
        if self.gtuning == path:
            return True
        cached = fbmode.video_palette_path(path)
        if not cached:
            return False
        if os.path.exists(cached):
            self.gpal = (path, cached)
            return False
        self.gtuning = path

        def work():
            w, h = self.img.w, self.img.h
            if max(w, h) > 512:
                if w >= h:
                    w, h = 512, max(1, int(round(h * 512.0 / w)))
                else:
                    w, h = max(1, int(round(w * 512.0 / h))), 512
            return fbmode.palette_from_frame(self.img.scaled(w, h), (w, h), cached)

        def done(res):
            self.gtuning = None
            if res and self.path == path:
                self.gpal = (path, res)
                if self.gfx and self.gfx_settings()[0] == 'vga':
                    try:
                        self.start_pipe()          # the running chain uses the old palette
                    except OSError:
                        return
                    self.gfx_note('PALETTE TUNED TO THIS PICTURE')
                    self.gfx_request()

        def failed(e):
            self.gtuning = None
            log('viewer gfx: palette: %s' % e)

        self.thread(work, done, failed)
        return True

    # ------------------------------------------------------------ graphics drawing
    def gfx_canvas(self):
        return fbmode.canvas(self.gfx.screen, fbmode.SCALE)

    def gfx_picture(self):
        """The canvas: the picture fitted or zoomed, on black (no UI chrome)."""
        from PIL import Image as PImage
        cw, ch = self.gfx_canvas()
        out = PImage.new('RGB', (cw, ch), (0, 0, 0))
        im = self.img
        if im is None:
            return out
        src = im.pil
        if src is None and im.data:
            try:
                src = PImage.frombytes('RGB', (im.w, im.h), im.data)
            except Exception:
                return out
        if src is None:
            return out
        k = min(cw / float(im.w), ch / float(im.h))
        w = max(1, int(round(im.w * k * self.gzoom)))
        h = max(1, int(round(im.h * k * self.gzoom)))
        # 0 keeps the zoomed picture centred; the limits are the edges
        limx = max(0.0, (w - cw) / 2.0)
        limy = max(0.0, (h - ch) / 2.0)
        self.gpanx = max(-limx, min(self.gpanx, limx))
        self.gpany = max(-limy, min(self.gpany, limy))
        filt = PImage.BOX if w < im.w else PImage.BILINEAR
        if w <= cw and h <= ch:
            out.paste(src.resize((w, h), filt), ((cw - w) // 2, (ch - h) // 2))
            return out
        vw, vh = im.w * cw / float(w), im.h * ch / float(h)
        x0 = (im.w - vw) / 2.0 + self.gpanx * im.w / float(w)
        y0 = (im.h - vh) / 2.0 + self.gpany * im.h / float(h)
        box = (int(round(x0)), int(round(y0)),
               int(round(x0 + vw)), int(round(y0 + vh)))
        out.paste(src.crop(box).resize((cw, ch), filt), (0, 0))
        return out

    def gfx_request(self):
        self.gdirty = True

    def gfx_tick(self):
        if self.gdirty and self.gfx and self.pipe:
            self.gdirty = False
            self.gfx_render()

    def gfx_render(self):
        g = self.gfx
        if not g or not self.pipe:
            return
        try:
            frame = bytearray(self.pipe.frame(self.gfx_picture().tobytes()))
        except OSError as e:
            self.gfx_failed(e)
            return
        self.gpipe_fails = 0
        self.gfx_hud(frame)
        g.show(frame)

    def gfx_failed(self, e):
        """ffmpeg died: restart it, but not forever."""
        log('viewer gfx: %s' % e)
        self.stop_pipe()
        self.gpipe_fails += 1
        if self.gpipe_fails <= 2:
            try:
                self.start_pipe()
            except OSError:
                return
            self.gfx_request()
            return
        self.gfx_note('GRAPHICS PIPELINE: %s' % str(e)[:40], secs=10.0)
        if self.gfx:
            blank = bytearray(self.gfx.frame_size)
            self.gfx_hud(blank)
            self.gfx.show(blank)

    def gfx_hud(self, buf):
        g = self.gfx
        W, H = g.screen
        px = 2
        bh = 8 * px
        y0 = H - bh
        g.rect(buf, 0, 0, y0, W, bh, g.c_bg)
        g.rect(buf, 0, 0, y0, W, px, g.c_acc)
        ty = y0 + 2 * px
        info = ''
        if self.img:
            pos = ''
            if self.path in self.siblings:
                pos = '%d/%d  ' % (self.siblings.index(self.path) + 1, len(self.siblings))
            info = '%s%dx%d  %d%%' % (pos, self.img.w, self.img.h, round(self.gzoom * 100))
            info = info[:70]
        iw = fbmode.text_width(info, px)
        room = max(1, (W - iw - 8 * px) // (4 * px))
        name = ascii_upper(fit(os.path.basename(self.path or ''), room))
        g.text(buf, 0, 2 * px, ty, name, px, g.c_fg)
        if info:
            g.text(buf, 0, W - iw - 2 * px, ty, info, px, g.c_dim)
        if self.loading:
            g.text(buf, 0, W // 2 - fbmode.text_width('LOADING...', px) // 2,
                   H // 2, 'LOADING...', px, g.c_fg)

    def gfx_key(self, ev):
        k = ev.key
        if k in ('g', 'esc', 'f'):
            self.leave_gfx()
        elif k == 'c':
            self.gfx_change('gfx_look', fbmode.LOOK_ORDER)
        elif k == 'd':
            self.gfx_change('gfx_dither', list(fbmode.DITHERS))
        elif k == 's':
            self.gfx_change('gfx_scanlines', [False, True])
        elif k in ('+', '='):
            self.gzoom = min(16.0, self.gzoom * 1.5)
            self.gfx_request()
        elif k == '-':
            self.gzoom = max(1.0, self.gzoom / 1.5)
            self.gfx_request()
        elif k in ('0', 'z'):
            self.gzoom = 1.0
            self.gpanx = self.gpany = 0
            self.gfx_request()
        elif k in ('n', ' ', 'pgdn'):
            self.step(1)
        elif k in ('p', 'backspace', 'pgup'):
            self.step(-1)
        elif k == 'right':
            if self.gzoom <= 1:
                self.step(1)
            else:
                self.gpanx += 16
                self.gfx_request()
        elif k == 'left':
            if self.gzoom <= 1:
                self.step(-1)
            else:
                self.gpanx -= 16
                self.gfx_request()
        elif k == 'up':
            self.gpany -= 16
            self.gfx_request()
        elif k == 'down':
            self.gpany += 16
            self.gfx_request()
        else:
            return False
        return True

    def gfx_change(self, key, values):
        cfg = self.wm.cfg
        cur = cfg.get(key, values[0])
        if key == 'gfx_look':
            cur = fbmode.OLD_LOOKS.get(cur, cur)
        cfg[key] = values[(values.index(cur) + 1) % len(values)] if cur in values \
            else values[0]
        cfg.save()
        try:
            self.start_pipe()
        except OSError as e:
            log('viewer gfx: %s' % e)
            return
        tuning = key == 'gfx_look' and cfg[key] == 'vga' and self.tune_palette()
        self.gfx_note(fbmode.LOOKS[self.gfx_settings()[0]][2] if key == 'gfx_look' else None,
                      'TUNING THE PALETTE...' if tuning else None)
        self.gfx_request()

    def gfx_mouse(self, ev):
        g = self.gfx
        term = self.wm.term
        sx = g.screen[0] / float(max(1, term.cols))
        sy = g.screen[1] / float(max(1, term.rows))
        x, y = ev.x * sx, ev.y * sy
        if ev.kind == 'wheelup':
            self.gzoom = min(16.0, self.gzoom * 1.25)
        elif ev.kind == 'wheeldown':
            self.gzoom = max(1.0, self.gzoom / 1.25)
        elif ev.kind == 'down' and ev.button == 0:
            self.gdrag = (x, y)
            return True
        elif ev.kind in ('drag', 'move') and self.gdrag:
            lx, ly = self.gdrag
            self.gdrag = (x, y)
            s = fbmode.SCALE
            if abs(x - lx) >= s * 0.5 or abs(y - ly) >= s * 0.5:
                self.gpanx -= (x - lx) / s
                self.gpany -= (y - ly) / s
        elif ev.kind == 'up':
            self.gdrag = None
            return True
        else:
            return True
        self.gfx_request()
        return True

    def gfx_idle(self):
        if not self.gfx:
            return
        self.gfx.tick(time.monotonic(), 0, 0, False)

    def reopen(self, path=None, **kw):
        if path:
            self.open(path)


APP = Viewer
