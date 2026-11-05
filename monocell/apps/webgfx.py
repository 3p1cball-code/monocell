"""The browser's graphics mode: a real web page on the framebuffer.

The text view (lite engine) cannot run JavaScript. Here a headless Chromium renders
the page and sends a frame whenever something changed (Page.startScreencast); the
frame goes through the same chain as video, pictures and maps - the configured retro
look at a quarter resolution by default ('o' toggles full resolution and colour) -
and mouse and keyboard go back in as real input events. The pointer is drawn here
and updated row by row.

Keys that never reach the page: Ctrl+G (text view), Ctrl+O (original/look),
Ctrl+L (address), Ctrl+R/F5 (reload), Alt+Left/Right (history), Ctrl+V (paste the
monocell clipboard), Ctrl+C (copy the selection), Alt+C/D/S (look, dither,
scanlines). While no text field has the focus the single letters g o l r c d s,
Esc and Backspace do the same; in a field everything is typed, Esc leaves it.
"""
import base64
import io
import os
import threading
import time

from .. import fbmode, web
from ..util import log
from ..widgets import TextField

DEBUG = bool(os.environ.get('MONOCELL_GFX_DEBUG'))     # numbers in the log
HUD_H = 20                     # bottom bar in screen pixels (a multiple of fbmode.SCALE)
FRAME_GAP = 0.05               # at most ~20 frames a second: the page waits for our ack
MOVE_GAP = 0.03                # mouse moves to the page, coalesced
WHEEL_STEP = 120               # pixels per wheel notch
MIDDLE_STEP = 6.0              # middle button + TrackPoint: screen pixels per scroll step
ZOOMS = (50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300)   # percent

# classic arrow, '#' outline, '.' fill
POINTER = [
    '#', '##', '#.#', '#..#', '#...#', '#....#', '#.....#', '#......#', '#.......#',
    '#........#', '#.....#####', '#..#..#', '#.# #..#', '##  #..#', '#    #..#',
    '     #..#', '      ##',
]


def pointer_runs():
    """Per row: [(x, colour key), ...] of the pointer sprite."""
    out = []
    for row in POINTER:
        out.append([(x, c) for x, c in enumerate(row) if c in '#.'])
    return out


POINTER_RUNS = pointer_runs()
POINTER_W = max(len(r) for r in POINTER)
POINTER_H = len(POINTER)


def step_zoom(cur, delta):
    """Next zoom (as a factor) from the list, snapped to the nearest entry first."""
    pct = int(round(cur * 100))
    i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - pct))
    i = max(0, min(len(ZOOMS) - 1, i + int(delta)))
    return ZOOMS[i] / 100.0


def snap_to(v, origin, step, hi):
    """Nearest step boundary above `origin`, kept between 0 and `hi`."""
    v = origin + int(round((v - origin) / float(step))) * step
    return max(0, min(v, hi))


def page_size(screen):
    """Viewport of the page: the screen above the bar."""
    return screen[0], max(1, screen[1] - HUD_H)


def to_screen(ev_x, ev_y, cols, rows, screen):
    """Mouse position (fractional cells) -> screen pixels."""
    W, H = screen
    x = ev_x * W / float(max(1, cols))
    y = ev_y * H / float(max(1, rows))
    return max(0.0, min(W - 1.0, x)), max(0.0, min(H - 1.0, y))


def is_hotkey(k, editing):
    """Is key `k` ours (True) or the page's (False)?"""
    if k in ('ctrl+g', 'ctrl+o', 'ctrl+l', 'ctrl+r', 'f5', 'f6', 'alt+left', 'alt+right',
             'ctrl+v', 'ctrl+c', 'alt+c', 'alt+d', 'alt+s', 'esc',
             'ctrl++', 'ctrl+=', 'ctrl+-', 'ctrl+0',
             'alt++', 'alt+=', 'alt+-', 'alt+0'):      # Alt works in a terminal too
        return True
    if editing:
        return False
    # On the VT Ctrl++ cannot work: the console keymap turns Ctrl+'+' into Ctrl+^
    # (and Ctrl+'-' into Ctrl+_), so the bare keys and Alt+'+'/Alt+'-' (Meta_plus,
    # Meta_minus - they arrive as ESC + character) are what works there.
    return k in ('g', 'o', 'l', 'r', 'c', 'd', 's', 'backspace', '+', '=', '-')


class WebGfx:
    """One stay of a Browser window in graphics mode."""

    def __init__(self, app, url):
        self.app = app
        self.wm = app.wm
        self.url = url if url and not url.startswith('about:') else 'about:blank'
        self.title = ''
        self.sess = None
        self.page = None
        self.live_page = None          # set as soon as the tab exists (frame acks)
        self.pipe = None
        self.original = False          # starts in the configured retro look (quarter
        #                                resolution); Ctrl+O/o shows the original
        pct = int(self.wm.cfg.get('web_zoom', 100) or 100)
        self.zoom = max(0.5, min(3.0, pct / 100.0))
        self.font = None
        self.frame_img = None          # last page picture (PIL, viewport size)
        self.base = None               # frame bytes without the pointer
        self.px = self.py = 0.0
        self.drawn_ptr = None          # (x, y) where the pointer is in sess.frame
        self.buttons = 0
        self.last_press = (0.0, 0, 0, 0)    # time, x, y, clicks
        self.middle = None             # [start y, accumulated, moved] while scrolling
        self.pending_move = None
        self.move_timer = False
        self.pending_ack = None
        self.last_frame_at = 0.0
        self.editing = False
        self.hover = ''
        self.loading = True
        self.status = 'Starting Chromium...'
        self.addr = None               # TextField while the address bar is open
        self.closed = False
        self.got_frame = False
        self.lock = threading.Lock()
        self.incoming = None           # newest decoded frame from the reader thread
        self.stats = [0, 0.0, 0]       # frames composed, ms in compose, last JPEG bytes
        self.stat_at = time.monotonic()

    # ---------------------------------------------------------- lifecycle
    def start(self):
        """Enter graphics mode; False (with a toast) when it cannot run here."""
        ok, why = fbmode.available(self.wm.term)
        if not ok:
            self.wm.toast(why, 'red')
            return False
        if not web.Chromium.find(self.app.cfg.get('chromium', '')):
            self.wm.toast('Graphics mode needs Chromium or Brave (sudo apt install chromium)', 'red')
            return False
        th = self.app.th
        rgb = th.rgb
        colours = {'bg': rgb[th.r['shadow']], 'fg': rgb[th.r['bright']],
                   'dim': rgb[th.r['dim']], 'accent': rgb[th.r['accent']],
                   'field': rgb[th.r['field']]}
        try:
            sess = fbmode.Session(self.wm.term.fd_out, fbmode.open_framebuffer(), colours)
            sess.start()
        except OSError as e:
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return False
        self.sess = sess
        self.font = fbmode.console_font(self.wm.cfg.get('font'))
        W, H = sess.screen
        self.px, self.py = W / 2.0, H / 2.0
        try:
            self.start_pipe()
        except OSError as e:
            self.sess.close()
            self.sess = None
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return False
        self.wm.enter_gfx(self.app)
        self.compose()
        size = page_size(sess.screen)
        binary = self.app.cfg.get('chromium', '')
        url = self.url

        def work():
            p = web.Page(binary, size, on_event=self.on_event)
            self.live_page = p
            if self.closed:
                p.close()
                return None
            p.set_zoom(self.zoom)
            p.start_screencast(80, *self.cast_size())
            try:
                p.navigate(url)
            except RuntimeError as e:
                self.call_ui(self.failed_load, str(e))
            return p

        def done(p):
            if p is None:
                return
            if self.closed:
                self.app.thread(p.close)
                return
            self.page = p
            self.status = ''
            self.later_check(4.0)
            self.refresh()

        def fail(e):
            if self.closed:
                return
            log('webgfx: %s' % e)
            self.stop()
            self.wm.toast('Chromium: %s' % e, 'red')
        self.app.thread(work, done, fail)
        self.note('CTRL+G TEXT VIEW  CTRL+L ADDRESS  + - ZOOM  O ORIGINAL', secs=4.0)
        return True

    def stop(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, 'gfx', None) is self:
            self.app.gfx = None
            self.app.invalidate()
        p, self.page = self.page, None
        self.live_page = None
        if p:
            self.app.thread(p.close)
        if self.pipe:
            self.pipe.close()
            self.pipe = None
        if self.sess:
            self.sess.close()
            self.sess = None
        self.wm.leave_gfx()

    def call_ui(self, fn, *args):
        self.wm.loop.call_soon_threadsafe(lambda: None if self.closed else fn(*args))

    def later_check(self, secs):
        """No screencast frame yet (some pages stay still): take one screenshot."""
        def check():
            if self.closed or not self.page or self.got_frame:
                return
            page = self.page

            def work():
                return page.screenshot()
            self.app.thread(work, lambda jpg: self.show_jpeg(jpg), lambda e: None)
        self.app.later(secs, check)

    def failed_load(self, msg):
        self.loading = False
        self.status = 'Could not load: %s' % msg
        self.refresh()

    # ---------------------------------------------------------- pipeline
    def look(self):
        cfg = self.wm.cfg
        look = cfg.get('gfx_look', 'vga')
        look = fbmode.OLD_LOOKS.get(look, look)
        look = look if look in fbmode.LOOKS else 'vga'
        return (look, 1 if self.original else fbmode.SCALE, cfg.get('gfx_dither', 'bayer'),
                bool(cfg.get('gfx_scanlines', False)))

    def label(self):
        look, scale, dither, scan = self.look()
        cw, ch = fbmode.canvas(self.sess.screen, scale)
        if self.original:
            return '%s  %dX%d  FULL COLOUR' % (fbmode.ORIGINAL_LABEL, cw, ch)
        return '%s  %dX%d  %s%s' % (fbmode.LOOKS[look][0], cw, ch, dither,
                                    '  SCANLINES' if scan else '')

    def note(self, *extra, secs=3.0):
        if self.sess:
            self.sess.note([self.label()] + [e for e in extra if e], secs)

    def css_point(self, x, y):
        """Screen pixels -> the CSS coordinates the page expects at this zoom."""
        W, H = self.page.css_size() if self.page else (1, 1)
        z = self.zoom
        return (max(0.0, min(W - 1.0, x / z)), max(0.0, min(H - 1.0, y / z)))

    def cast_size(self):
        """Pixels the browser is asked for: the page area in pipeline pixels."""
        scale = self.look()[1]
        W, H = self.sess.screen
        return max(1, W // scale), max(1, (H - HUD_H) // scale)

    def start_pipe(self):
        if self.pipe:
            self.pipe.close()
        look, scale, dither, scan = self.look()
        fb = self.sess.fb
        th = self.app.th
        cv = fbmode.canvas(self.sess.screen, scale)
        self.pipe = fbmode.StillPipeline(cv, fb, original=self.original, theme_rgb=th.rgb,
                                         accent=th.rgb[th.r['accent']], look=look, scale=scale,
                                         dither=dither, scanlines=scan)
        want = self.cast_size()
        if self.page and self.page.cast != want:
            self.page.start_screencast(80, *want)

    def set_original(self, on):
        self.original = on
        try:
            self.start_pipe()
        except OSError as e:
            self.status = str(e)
        self.note('O: BACK TO THE LOOK' if on else 'O: ORIGINAL')
        self.refresh()

    def change(self, key, values):
        cfg = self.wm.cfg
        cur = cfg.get(key, values[0])
        if key == 'gfx_look':
            cur = fbmode.OLD_LOOKS.get(cur, cur)
        cfg[key] = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
        cfg.save()
        self.original = False              # a look key means: show me the look
        try:
            self.start_pipe()
        except OSError as e:
            self.status = str(e)
        self.note(fbmode.LOOKS[self.look()[0]][2] if key == 'gfx_look' else None)
        self.refresh()

    # ---------------------------------------------------------- frames
    def on_event(self, name, p):
        """Reader thread of the page: decode frames here, everything else to the UI."""
        if name == 'Page.screencastFrame':
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(base64.b64decode(p.get('data', '')))).convert('RGB')
            except Exception as e:
                log('webgfx: frame: %s' % e)
                img = None
            with self.lock:
                first = self.incoming is None
                self.incoming = (img, p.get('sessionId'))
                self.stats[2] = len(p.get('data', ''))
            if first:
                self.call_ui(self.take_frame)
            return
        if name == 'Runtime.bindingCalled' and p.get('name') == '__mc':
            self.call_ui(self.on_binding, p.get('payload', ''))
        elif name == 'Page.frameNavigated':
            fr = p.get('frame', {})
            if not fr.get('parentId'):
                self.call_ui(self.on_url, fr.get('url', ''))
        elif name == 'Page.navigatedWithinDocument':
            if not self.page or p.get('frameId') == self.page.main_frame:
                self.call_ui(self.on_url, p.get('url', ''))
        elif name == 'Page.frameStartedLoading':
            if not self.page or p.get('frameId') == self.page.main_frame:
                self.call_ui(self.on_loading, True)
        elif name == 'Page.frameStoppedLoading':
            if not self.page or p.get('frameId') == self.page.main_frame:
                self.call_ui(self.on_loading, False)
        elif name == 'Page.javascriptDialogOpening':
            self.call_ui(self.note, 'PAGE SAYS: ' + (p.get('message') or '')[:60], secs=5.0)
        elif name == 'closed':
            self.call_ui(self.on_closed, p.get('reason', ''))

    def take_frame(self):
        with self.lock:
            item, self.incoming = self.incoming, None
        if not item:
            return
        img, sid = item
        if img is not None:
            self.got_frame = True
            self.frame_img = img
            self.compose()
        self.last_frame_at = time.monotonic()
        # the page sends the next frame only after the ack: that is the frame limit
        page = self.live_page
        if page and sid is not None:
            self.app.later(FRAME_GAP, lambda: page.ack(sid))

    def show_jpeg(self, jpg):
        if self.closed or not jpg:
            return
        try:
            from PIL import Image
            self.frame_img = Image.open(io.BytesIO(jpg)).convert('RGB')
        except Exception:
            return
        self.compose()

    def on_binding(self, payload):
        import json
        try:
            m = json.loads(payload)
        except ValueError:
            return
        k, v = m.get('k'), m.get('v')
        if k == 'edit':
            self.editing = bool(v)
        elif k == 'hover':
            self.hover = v or ''
            self.refresh_hud()
        elif k == 'title':
            self.title = v or ''
            self.app.set_title(self.title[:60] or 'Browser')
            self.refresh_hud()

    def on_url(self, url):
        if url:
            self.url = url
            self.editing = False
            self.hover = ''
            self.refresh_hud()

    def on_loading(self, on):
        self.loading = on
        if not on:
            self.status = ''
            self.later_check(1.5)
        self.refresh_hud()

    def on_closed(self, reason):
        if self.closed:
            return
        self.stop()
        self.wm.toast('Chromium closed: %s' % (reason or 'gone'), 'red')

    # ---------------------------------------------------------- drawing
    def canvas_image(self):
        """The page fitted into the pipeline's input (screen or look canvas)."""
        from PIL import Image
        look, scale, dither, scan = self.look()
        cw, ch = fbmode.canvas(self.sess.screen, scale)
        out = Image.new('RGB', (cw, ch), (255, 255, 255) if self.frame_img is None else (0, 0, 0))
        img = self.frame_img
        if img is not None:
            W, H = page_size(self.sess.screen)
            tw, tht = max(1, W // scale), max(1, H // scale)
            if img.size != (tw, tht):
                img = img.resize((tw, tht), Image.BOX if scale > 1 else Image.BILINEAR)
            out.paste(img, (0, 0))
        return out

    def hud_image(self):
        from PIL import Image
        g = self.sess
        W = g.screen[0]
        img = Image.new('RGB', (W, HUD_H), g.c_bg)
        img.paste(g.c_acc, (0, 0, W, 2))
        f = self.font
        if f is None:
            return img
        y = 2 + (HUD_H - 2 - f.h) // 2
        if self.addr is not None:
            x = f.draw(img, 4, y, '> ', g.c_acc)
            room = max(1, (W - x - 8) // f.w)
            t = self.addr.text
            start = max(0, self.addr.cur - room + 1)
            vis = t[start:start + room]
            f.draw(img, x, y, vis, g.c_fg)
            cx = x + (self.addr.cur - start) * f.w
            img.paste(g.c_acc, (cx, y + f.h - 3, cx + f.w, y + f.h - 1))
            return img
        right = ('ORIGINAL' if self.original else fbmode.LOOKS[self.look()[0]][0]) + \
            '  Ctrl+G text  Ctrl+L address'
        if abs(self.zoom - 1.0) > 1e-3:
            right = '%d%%  %s' % (round(self.zoom * 100), right)
        rw = len(right) * f.w
        f.draw(img, W - rw - 4, y, right, g.c_dim)
        if self.hover:
            left, col = self.hover, g.c_acc
        elif self.status:
            left, col = self.status, g.c_fg
        else:
            left = (self.title + '  ' if self.title else '') + self.url
            col = g.c_fg
        if self.loading:
            left = ('|/-\\'[int(time.monotonic() * 6) % 4]) + ' ' + left
        f.draw(img, 4, y, left, col, limit=W - rw - 16)
        return img

    def compose(self):
        """Page + bar -> frame bytes (self.base), then the pointer on top."""
        if not self.sess or not self.pipe:
            return
        t0 = time.monotonic()
        try:
            data = bytearray(self.pipe.frame(self.canvas_image().tobytes()))
        except OSError as e:
            log('webgfx: %s' % e)
            try:
                self.original = True
                self.start_pipe()
                data = bytearray(self.pipe.frame(self.canvas_image().tobytes()))
            except OSError:
                return
        self.put_hud(data)
        self.base = data
        self.show()
        self.stats[0] += 1
        self.stats[1] += (time.monotonic() - t0) * 1000.0

    def put_hud(self, data):
        g = self.sess
        fb = g.fb
        hud = self.hud_image()
        if fb.line_px > hud.size[0]:
            from PIL import Image
            wide = Image.new('RGB', (fb.line_px, HUD_H), (0, 0, 0))
            wide.paste(hud, (0, 0))
            hud = wide
        raw = hud.tobytes('raw', fbmode.StillPipeline.PPM_TARGET[fb.bpp])
        off = (g.screen[1] - HUD_H) * fb.stride
        data[off:off + len(raw)] = raw

    def refresh(self):
        self.compose()

    def refresh_hud(self):
        if self.base is None or not self.sess:
            return
        self.put_hud(self.base)
        y0 = self.sess.screen[1] - HUD_H
        frame = self.sess.frame
        if frame is not None and len(frame) == len(self.base):
            st = self.sess.fb.stride
            frame[y0 * st:] = self.base[y0 * st:]
            self.draw_pointer(frame, self.px, self.py)
            self.sess.paint_rows(y0, self.sess.screen[1])
        else:
            self.show()

    def pointer_style(self):
        """(block size, outline colour, fill colour) of the pointer.

        With a retro look the pointer is one canvas pixel per block - as chunky as
        the picture - and takes the darkest and brightest colour of that look's
        palette, so it never breaks the look with a crisp full-colour arrow."""
        look, scale, dither, scan = self.look()
        if self.original:
            return 1, (0, 0, 0), (255, 255, 255)
        key = (look, scale, self.app.th.rgb[0])
        if getattr(self, '_pstyle_key', None) != key:
            th = self.app.th
            cols = fbmode.palette_colours(look, th.rgb, th.rgb[th.r['accent']])
            lum = [r * 299 + g * 587 + b * 114 for r, g, b in cols]
            self._pstyle = (max(1, scale // 2),    # half a canvas pixel: big enough,
                            cols[lum.index(min(lum))], cols[lum.index(max(lum))])
            self._pstyle_key = key
        return self._pstyle

    def pointer_origin(self):
        """(block size, x0, y0) of the grid the pointer snaps to.

        The grid is the picture's own: the pointer blocks line up with the canvas
        pixels (the block itself is half a canvas pixel in the looks)."""
        block = self.pointer_style()[0]
        scale = self.look()[1]
        W, H = self.sess.screen
        cw, ch = fbmode.canvas((W, H), scale)
        return block, (W - cw * scale) // 2, (H - ch * scale) // 2

    def show(self):
        """Full frame: base + pointer."""
        frame = bytearray(self.base)
        self.drawn_ptr = self.draw_pointer(frame, self.px, self.py)
        self.sess.show(frame)

    def draw_pointer(self, buf, x, y):
        """Draw the arrow; returns where it ended up (snapped to the pixel grid)."""
        g = self.sess
        fb = g.fb
        W, H = g.screen
        bp, st = fb.bytes_pp, fb.stride
        size, outline, fill = self.pointer_style()
        ink = {1: fb.pixel(outline), 0: fb.pixel(fill)}
        _, x0, y0 = self.pointer_origin()
        x = snap_to(int(x), x0, size, W - POINTER_W * size)
        y = snap_to(int(y), y0, size, H - POINTER_H * size)
        for r, runs in enumerate(POINTER_RUNS):
            yy = y + r * size
            if yy + size > H:
                break
            for dx, c in runs:
                xx = x + dx * size
                if xx + size > W:
                    continue
                pix = ink[1 if c == '#' else 0]
                block = pix * size
                for row in range(yy, yy + size):
                    o = row * st + xx * bp
                    buf[o:o + len(block)] = block
        return x, y

    def move_pointer(self, x, y):
        if not self.sess or self.base is None:
            self.px, self.py = x, y
            return
        frame = self.sess.frame
        if frame is None or len(frame) != len(self.base):
            self.px, self.py = x, y
            self.show()
            return
        size, x0, y0 = self.pointer_origin()
        W, H = self.sess.screen
        ox, oy = self.drawn_ptr or (int(self.px), int(self.py))
        self.px, self.py = x, y
        st = self.sess.fb.stride
        nx = snap_to(int(x), x0, size, W - POINTER_W * size)
        ny = snap_to(int(y), y0, size, H - POINTER_H * size)
        if (nx, ny) == (ox, oy):
            return
        for yy in range(oy, min(H, oy + POINTER_H * size)):  # erase the old one
            frame[yy * st:(yy + 1) * st] = self.base[yy * st:(yy + 1) * st]
        self.drawn_ptr = self.draw_pointer(frame, nx, ny)
        lo, hi = min(oy, ny), max(oy, ny) + POINTER_H * size
        self.sess.paint_rows(lo, min(H, hi))

    def idle(self):
        """Called twice a second: OSD timeouts, VT switches, the loading spinner."""
        if not self.sess:
            return
        self.sess.tick(time.monotonic(), 0, 0, False)
        if self.loading:
            self.refresh_hud()
        if DEBUG and time.monotonic() - self.stat_at > 2.0:
            dt = time.monotonic() - self.stat_at
            n, ms = self.stats[0], self.stats[1]
            log('webgfx: %d frames in %.0f s, %.0f ms per frame, last JPEG %d kB, %d X %d'
                % (n, dt, ms / n if n else 0, self.stats[2] // 1024, self.sess.screen[0],
                   self.sess.screen[1]))
            self.stats = [0, 0.0, 0]
            self.stat_at = time.monotonic()

    # ---------------------------------------------------------- mouse
    def on_mouse(self, ev):
        term = self.wm.term
        if not self.sess:
            return True
        x, y = to_screen(ev.x, ev.y, term.w, term.h, self.sess.screen)
        self.move_pointer(x, y)
        H = self.sess.screen[1]
        on_bar = y >= H - HUD_H
        page = self.page
        if ev.kind == 'down' and ev.button == 0 and on_bar:
            self.open_address()
            return True
        if ev.kind in ('wheelup', 'wheeldown'):
            if page and not on_bar:
                cx, cy = self.css_point(x, y)
                d = (-WHEEL_STEP if ev.kind == 'wheelup' else WHEEL_STEP) / self.zoom
                page.wheel(cx, cy, d)
            return True
        if ev.button == 1 or (self.middle is not None and ev.kind in ('drag', 'up')):
            return self.middle_scroll(ev, x, y)
        if not page:
            return True
        y = min(y, H - HUD_H - 1)
        x, y = self.css_point(x, y)
        if ev.kind in ('move', 'drag'):
            self.pending_move = (x, y)
            if not self.move_timer:
                self.move_timer = True
                self.app.later(MOVE_GAP, self.flush_move)
            return True
        if ev.kind in ('down', 'up'):
            self.flush_move()
            name, bit = ('left', 1) if ev.button == 0 else ('right', 2)
            if ev.kind == 'down':
                if self.addr is not None:
                    self.addr = None
                    self.refresh_hud()
                t, lx, ly, n = self.last_press
                now = time.monotonic()
                clicks = n + 1 if now - t < 0.45 and abs(x - lx) < 6 and abs(y - ly) < 6 else 1
                self.last_press = (now, x, y, clicks)
                self.buttons |= bit
                page.mouse('mousePressed', x, y, name, clicks, self.buttons)
            else:
                self.buttons &= ~bit
                page.mouse('mouseReleased', x, y, name, self.last_press[3], self.buttons)
        return True

    def flush_move(self):
        self.move_timer = False
        mv, self.pending_move = self.pending_move, None
        if mv and self.page:
            btn = 'left' if self.buttons & 1 else 'none'
            self.page.mouse('mouseMoved', mv[0], mv[1], btn, 0, self.buttons)

    def middle_scroll(self, ev, x, y):
        """Middle button + TrackPoint scrolls the page (as everywhere in monocell)."""
        if ev.kind == 'down':
            self.middle = [y, 0.0]
        elif ev.kind == 'drag' and self.middle is not None:
            self.middle[1] += y - self.middle[0]
            self.middle[0] = y
            steps = int(self.middle[1] / MIDDLE_STEP)
            if steps and self.page:
                self.middle[1] -= steps * MIDDLE_STEP
                cx, cy = self.css_point(x, y)
                self.page.wheel(cx, cy, steps * WHEEL_STEP / 3.0 / self.zoom)
        elif ev.kind == 'up':
            self.middle = None
        return True

    # ---------------------------------------------------------- keys
    def open_address(self):
        self.addr = TextField(self.url if self.url != 'about:blank' else '')
        self.addr.sel_all = True
        self.refresh_hud()

    def address_key(self, ev):
        k = ev.key
        if k == 'esc':
            self.addr = None
        elif k == 'enter':
            t, self.addr = self.addr.text, None
            url = self.app.resolve(t)
            if url:
                self.go(url)
        elif k == 'ctrl+v':
            self.addr.insert((self.wm.clipboard or '').strip().split('\n')[0])
        else:
            self.addr.key(ev)
        self.refresh_hud()
        return True

    def go(self, url):
        self.url = url
        self.loading = True
        self.status = 'Loading ' + url
        self.refresh_hud()
        page = self.page
        if not page:
            return

        def work():
            page.navigate(url)
        self.app.thread(work, lambda r: None, lambda e: self.failed_load(str(e)))

    def run(self, fn, done=None):
        page = self.page
        if page:
            self.app.thread(lambda: fn(page), done or (lambda r: None),
                            lambda e: log('webgfx: %s' % e))

    def on_key(self, ev):
        if self.addr is not None:
            return self.address_key(ev)
        k = ev.key
        if is_hotkey(k, self.editing):
            return self.hotkey(k)
        if self.page and not self.page.key(k):
            c = ev.char
            if c:
                self.page.insert_text(c)
        return True

    def hotkey(self, k):
        if k in ('ctrl+g', 'g'):
            self.app.leave_gfx()
        elif k == 'esc':
            if self.editing:
                self.editing = False
                if self.page:
                    self.page.key('esc')
                self.run(lambda p: p.blur())
            else:
                self.app.leave_gfx()
        elif k in ('ctrl+o', 'o'):
            self.set_original(not self.original)
        elif k in ('ctrl+l', 'l', 'f6'):
            self.open_address()
        elif k in ('ctrl+r', 'r', 'f5'):
            self.loading = True
            self.refresh_hud()
            self.run(lambda p: p.reload())
        elif k in ('alt+left', 'backspace'):
            self.run(lambda p: p.history(-1))
        elif k == 'alt+right':
            self.run(lambda p: p.history(1))
        elif k == 'ctrl+v':
            text = self.wm.clipboard or ''
            if text and self.page:
                self.page.insert_text(text)
        elif k == 'ctrl+c':
            def copied(t):
                if t:
                    self.wm.set_clipboard(t)
                    self.note('COPIED %d CHARACTERS' % len(t), secs=2.0)
            self.run(lambda p: p.copy_selection(), copied)
        elif k in ('alt+c', 'c'):
            self.change('gfx_look', fbmode.LOOK_ORDER)
        elif k in ('alt+d', 'd'):
            self.change('gfx_dither', list(fbmode.DITHERS))
        elif k in ('alt+s', 's'):
            self.change('gfx_scanlines', [False, True])
        elif k in ('ctrl++', 'ctrl+=', 'alt++', 'alt+=', '+', '='):
            self.set_zoom(step_zoom(self.zoom, 1))
        elif k in ('ctrl+-', 'alt+-', '-'):
            self.set_zoom(step_zoom(self.zoom, -1))
        elif k in ('ctrl+0', 'alt+0'):
            self.set_zoom(1.0)
        return True

    def set_zoom(self, z):
        z = max(ZOOMS[0] / 100.0, min(ZOOMS[-1] / 100.0, z))
        if abs(z - self.zoom) < 1e-3:
            return
        self.zoom = z
        self.wm.cfg['web_zoom'] = int(round(z * 100))
        self.wm.cfg.save()
        if self.page:
            self.page.set_zoom(z)
        self.note('ZOOM %d%%' % round(z * 100))
        if self.loading:
            self.refresh_hud()
