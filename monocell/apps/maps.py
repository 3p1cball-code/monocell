"""Maps: OpenStreetMap as real pixels on the Linux console.

Same graphics mode as the video player (`g` there): the console leaves text mode,
frames go into the framebuffer, and the picture is reduced to a classic adapter's
colours with ordered dithering. The map is composed from OSM raster tiles into a
quarter-resolution canvas and then handed to exactly the player's pipeline
(fbmode.image_command), so the looks, dithering and scanlines are the same code.

Keys in graphics mode:  arrows pan, + / - zoom, wheel zooms, drag pans,
    B or / place search (Enter jumps to the result), C look, D dither,
    S scanlines, T tile source, R refetch, G / Esc back to the window.
"""
import time

from .. import fbmode, maps
from ..app import App
from ..util import ascii_upper, fit, log
from ..widgets import button

PAN_PIXELS = 32          # canvas pixels per arrow press
MAP_BAYER = 4            # 0..5: 4 keeps flat map areas clean (2 is the player's, for photos)


class Maps(App):
    name = 'maps'
    title = 'Maps'
    icon = '[m]'
    size = (72, 20)
    min_size = (40, 12)
    single = True

    def __init__(self, wm, win, lat=None, lon=None, zoom=None, **kw):
        super().__init__(wm, win)
        cfg = wm.cfg
        self.view = maps.MapView(
            lat if lat is not None else cfg.get('map_lat', 52.5200),
            lon if lon is not None else cfg.get('map_lon', 13.4050),
            zoom if zoom is not None else cfg.get('map_zoom', 12),
            256, 192)
        src = cfg.get('map_tiles', maps.DEFAULT_SOURCE)
        self.store = maps.TileStore(maps.source_url(src))
        self.gfx = None
        self.pipe = None
        self.frame = None
        self.fetching = False
        self.offline_until = 0.0
        self.err = ''
        self.why = ''
        self.query = ''
        self.results = []
        self.sel = 0
        self.search = ''            # '' | input | results
        self.searching = False
        self.drag = None
        self.gfx_chrome = False
        self.dirty_map = False
        self.last_render = 0.0
        self.renders = 0
        self.pipe_fails = 0
        self.every(0.04, self.tick_render)
        self.every(0.5, self.idle_tick)
        self.later(0.2, self.auto_gfx)

    # ------------------------------------------------------------ helpers
    def settings(self):
        cfg = self.wm.cfg
        look = cfg.get('gfx_look', 'vga')
        look = fbmode.OLD_LOOKS.get(look, look)
        look = look if look in fbmode.LOOKS else 'vga'
        dither = cfg.get('gfx_dither', 'bayer')
        return look, fbmode.SCALE, dither, bool(cfg.get('gfx_scanlines', False))

    def label(self):
        look, scale, dither, scan = self.settings()
        cw, ch = fbmode.canvas(self.gfx.screen, scale) if self.gfx else (0, 0)
        return '%s  %dX%d  %s%s' % (fbmode.LOOKS[look][0], cw, ch, dither,
                                    '  SCANLINES' if scan else '')

    def source(self):
        return maps.source_info(self.wm.cfg.get('map_tiles', maps.DEFAULT_SOURCE))

    def save_view(self):
        cfg = self.wm.cfg
        cfg['map_lat'] = round(self.view.lat, 5)
        cfg['map_lon'] = round(self.view.lon, 5)
        cfg['map_zoom'] = int(self.view.zoom)
        cfg.save()

    def note(self, *lines, secs=3.0):
        if self.gfx:
            self.gfx.note([self.label()] + [l for l in lines if l], secs)

    # ------------------------------------------------------------ window
    def auto_gfx(self):
        if self.gfx:
            return
        ok, why = fbmode.available(self.wm.term)
        if ok:
            self.enter_gfx()
        else:
            self.why = why
            self.invalidate()

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.fill(0, 0, w, h, th.c('text', 'field'))
        y = 1
        cv.put(2, y, 'MAPS', th.c('bright', 'field'))
        cv.put(8, y, self.source()[0], th.c('dim', 'field'))
        y += 2
        cv.put(2, y, 'CENTRE', th.c('dim', 'field'))
        cv.put(10, y, maps.fmt_latlon(self.view.lat, self.view.lon), th.c('text', 'field'))
        cv.put(2, y + 1, 'ZOOM', th.c('dim', 'field'))
        cv.put(10, y + 1, 'Z%d  %.1f KM ACROSS' % (self.view.zoom,
                                                   self.view.mpp() * self.view.width / 1000.0),
               th.c('text', 'field'))
        y += 3
        x = 2
        for label, key in (('Graphics', 'gfx'), ('Zoom +', 'zin'),
                           ('Zoom -', 'zout'), ('Berlin', 'home')):
            x += button(self, cv, x, y, label, key, primary=key == 'gfx') + 1
        y += 2
        if self.gfx:
            cv.put(2, y, 'The map is on the framebuffer.', th.c('dim', 'field'))
        else:
            if self.why:
                cv.put(2, y, fit(self.why, w - 4), th.c('red', 'field'))
                y += 1
            cv.put(2, y, 'G  GRAPHICS MODE - REAL PIXELS (THE PLAYER\'S LOOKS)',
                   th.c('text', 'field'))
            cv.put(2, y + 1, 'ARROWS PAN   + - ZOOM   B SEARCH   C LOOK   D DITHER',
                   th.c('dim', 'field'))
            cv.put(2, y + 2, 'S SCANLINES   T TILE SOURCE   G/ESC BACK', th.c('dim', 'field'))
        self.zone(0, 0, w, h, 'screen')
        if self.err:
            cv.put(2, h - 1, fit(self.err, w - 4), th.c('red', 'field'))

    def on_click(self, key, ev):
        if key == 'gfx':
            self.enter_gfx()
        elif key == 'zin':
            self.zoom(1)
        elif key == 'zout':
            self.zoom(-1)
        elif key == 'home':
            self.goto_berlin()

    def on_key(self, ev):
        if self.gfx:
            return self.gfx_key(ev)
        k = ev.key
        if k in ('g', 'enter'):
            self.enter_gfx()
        elif k in ('+', '='):
            self.zoom(1)
        elif k == '-':
            self.zoom(-1)
        elif k == 'up':
            self.view.pan_pixels(0, -PAN_PIXELS * 2)
        elif k == 'down':
            self.view.pan_pixels(0, PAN_PIXELS * 2)
        elif k == 'left':
            self.view.pan_pixels(-PAN_PIXELS * 2, 0)
        elif k == 'right':
            self.view.pan_pixels(PAN_PIXELS * 2, 0)
        elif k == 't':
            self.cycle_source()
        else:
            return False
        self.invalidate()
        return True

    def on_close(self):
        self.leave_gfx()
        self.save_view()
        return True

    # ------------------------------------------------------------ graphics mode
    def enter_gfx(self):
        if self.gfx:
            return
        ok, why = fbmode.available(self.wm.term)
        if not ok:
            self.why = why
            self.wm.toast(why, 'red')
            self.invalidate()
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
        self.gfx_chrome = self.win.borderless
        try:
            sess.start()
        except OSError as e:
            sess.close()
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        self.gfx = sess
        self.wm.enter_gfx(self)
        self.set_borderless(True)
        cw, ch = fbmode.canvas(sess.screen, fbmode.SCALE)
        self.view.resize(cw, ch)
        self.frame = None
        try:
            self.start_pipe()
        except OSError as e:
            self.leave_gfx()
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        self.note('B SEARCH  +/- ZOOM  ARROWS PAN', 'C LOOK  D DITHER  S SCANLINES  G EXIT',
                  secs=5.0)
        self.request_render()

    def leave_gfx(self):
        if not self.gfx:
            return
        self.save_view()
        self.stop_pipe()
        sess, self.gfx = self.gfx, None
        sess.close()
        self.wm.leave_gfx()
        if not self.gfx_chrome:
            self.set_borderless(False)
        self.search = ''
        self.invalidate()

    def start_pipe(self):
        self.stop_pipe()
        g = self.gfx
        look, scale, dither, scan = self.settings()
        cw, ch = fbmode.canvas(g.screen, scale)
        pal = maps.palette_path() if look == 'vga' else None
        self.pipe = fbmode.StillPipeline(
            (cw, ch), g.fb, theme_rgb=self.th.rgb,
            accent=self.th.rgb[self.th.r['accent']], look=look, scale=scale,
            dither=dither, scanlines=scan, vga_palette=pal, bayer_scale=MAP_BAYER)
        g.scale = scale

    def stop_pipe(self):
        if self.pipe:
            self.pipe.close()
            self.pipe = None

    def request_render(self):
        self.dirty_map = True

    def tick_render(self):
        if self.dirty_map and self.gfx and self.pipe:
            self.dirty_map = False
            self.render()

    def render(self):
        """Compose the map, run it through the pipeline and draw the HUD."""
        g = self.gfx
        if not g or not self.pipe:
            return
        t0 = time.monotonic()
        img = maps.compose(self.view, self.store)
        try:
            frame = bytearray(self.pipe.frame(img.tobytes()))
        except OSError as e:
            self.pipeline_failed(e)
            return
        self.err = ''
        self.pipe_fails = 0
        self.draw_hud(frame)
        g.show(frame)
        self.renders += 1
        self.last_render = time.monotonic() - t0
        self.prefetch()

    def pipeline_failed(self, e):
        """ffmpeg died (killed, out of memory): restart it, but not forever."""
        self.err = str(e)
        log('maps: %s' % e)
        self.stop_pipe()
        self.pipe_fails += 1
        if self.pipe_fails <= 2:
            try:
                self.start_pipe()
            except OSError as e2:
                self.err = str(e2)
                return
            self.request_render()
            return
        self.note('GRAPHICS PIPELINE: %s' % str(e)[:40], secs=10.0)
        if self.gfx:
            blank = bytearray(self.gfx.frame_size)
            self.draw_hud(blank)
            self.gfx.show(blank)

    # ------------------------------------------------------------ HUD
    def P(self):
        return max(2, min(3, self.gfx.fb.w // 400)) if self.gfx else 2

    def rect(self, buf, x, y, w, h, rgb):
        self.gfx.rect(buf, 0, x, y, w, h, rgb)

    def text(self, buf, x, y, s, px, rgb):
        return self.gfx.text(buf, 0, x, y, s, px, rgb)

    def draw_hud(self, buf):
        """Status bar at the bottom, crosshair, search box - drawn over the pixels."""
        g = self.gfx
        W, H = g.screen
        px = self.P()
        bh = 8 * px
        y0 = H - bh
        self.rect(buf, 0, y0, W, bh, g.c_bg)
        self.rect(buf, 0, y0, W, px, g.c_acc)
        x = 2 * px
        ty = y0 + 2 * px
        info = 'Z%d %s' % (self.view.zoom, maps.fmt_latlon(self.view.lat, self.view.lon))
        x = self.text(buf, x, ty, info, px, g.c_fg) + 2 * px
        m, length = maps.scale_bar(self.view.mpp(),
                                   target=max(40, W // 16 // fbmode.SCALE))
        if x + length * px + 8 * 4 * px < W:
            self.rect(buf, x, ty + 4 * px, length * px, px, g.c_fg)
            self.rect(buf, x, ty + 3 * px, px, 3 * px, g.c_fg)
            self.rect(buf, x + length * px - px, ty + 3 * px, px, 3 * px, g.c_fg)
            self.text(buf, x + length * px + 2 * px, ty, maps.fmt_metres(m), px, g.c_dim)
        attr = self.source()[2]
        aw = fbmode.text_width(attr, px)
        if aw + 4 * px < W:
            self.text(buf, W - aw - 2 * px, ty, attr, px, g.c_dim)
        # crosshair: where a search result lands
        cx, cy = W // 2, H // 2
        for dx in range(-3, 4):
            self.rect(buf, cx + dx * px, cy, px, px, g.c_acc)
            self.rect(buf, cx, cy + dx * px, px, px, g.c_acc)
        if self.search:
            self.draw_search(buf)
        if self.err:
            self.rect(buf, 0, 0, W, 7 * px, g.c_bg)
            self.text(buf, 2 * px, 2 * px, ascii_upper(self.err)[:80], px, g.c_fg)

    def draw_search(self, buf):
        g = self.gfx
        W, H = g.screen
        px = self.P()
        bx, by = 2 * px, 2 * px
        bw = min(W - 4 * px, 60 * 4 * px + 4 * px)
        if self.search == 'results':
            lines = []
            for i, r in enumerate(self.results[:6]):
                lines.append((i, r['name'] + (' / ' + r['detail'] if r['detail'] else '')))
            bh = (len(lines) + 2) * 7 * px + 3 * px
            self.rect(buf, bx, by, bw, bh, g.c_bg)
            self.rect(buf, bx, by, px, bh, g.c_acc)
            y = by + 2 * px
            self.text(buf, bx + 2 * px, y, 'SUCHE: ' + ascii_upper(self.query)[:40],
                      px, g.c_fg)
            y += 7 * px
            if self.searching:
                self.text(buf, bx + 2 * px, y, 'SUCHE LAEUFT...', px, g.c_dim)
            elif not self.results:
                self.text(buf, bx + 2 * px, y, 'NICHTS GEFUNDEN', px, g.c_dim)
            for i, line in lines:
                if i == self.sel:
                    self.rect(buf, bx + px, y, bw - 2 * px, 6 * px, g.c_field)
                self.text(buf, bx + 2 * px, y, ascii_upper(line)[:bw // (4 * px) - 2], px,
                          g.c_fg if i == self.sel else g.c_dim)
                y += 7 * px
            self.text(buf, bx + 2 * px, y, 'ENTER SPRINGT  ESC ABBRUCH', px, g.c_fg)
        else:
            bh = 14 * px
            self.rect(buf, bx, by, bw, bh, g.c_bg)
            self.rect(buf, bx, by, px, bh, g.c_acc)
            self.text(buf, bx + 2 * px, by + 2 * px,
                      'SUCHE: ' + ascii_upper(self.query)[:44] + '_', px, g.c_fg)
            self.text(buf, bx + 2 * px, by + 9 * px,
                      'ENTER SUCHT  ESC ABBRICHT', px, g.c_fg)

    # ------------------------------------------------------------ tiles
    def prefetch(self):
        if self.fetching or not self.gfx:
            return
        if time.monotonic() < self.offline_until:
            return
        keys = maps.missing_tiles(self.view, self.store)
        if not keys:
            return
        self.fetching = True
        keys = keys[:12]
        self.thread(lambda: self.store.load(keys), self.prefetched)

    def prefetched(self, n):
        self.fetching = False
        if not n:
            self.offline_until = time.monotonic() + 30
            self.note('NO TILES: %s' % (self.store.error or 'OFFLINE'), secs=4.0)
        self.request_render()

    def fetch_again(self):
        self.offline_until = 0.0
        self.fetching = False
        self.request_render()

    # ------------------------------------------------------------ interaction
    def zoom(self, delta):
        if self.view.zoom_by(delta):
            self.save_view()
            self.request_render()
            if not self.gfx:
                self.invalidate()

    def pan(self, dx, dy):
        self.view.pan_pixels(dx, dy)
        self.request_render()

    def goto_berlin(self):
        self.view.set_centre(52.5200, 13.4050)
        self.view.zoom = 12
        self.save_view()
        self.request_render()
        if not self.gfx:
            self.invalidate()

    def gfx_key(self, ev):
        k = ev.key
        if self.search == 'input':
            if k == 'esc':
                self.search = ''
                self.request_render()
            elif k == 'enter':
                self.start_search()
            elif k == 'backspace':
                self.query = self.query[:-1]
                self.request_render()
            else:
                c = ev.char
                if c and 32 <= ord(c) < 127 and len(self.query) < 60:
                    self.query += c
                    self.request_render()
            return True
        if self.search == 'results':
            if k == 'esc':
                self.search = ''
                self.request_render()
            elif k == 'up':
                self.sel = (self.sel - 1) % max(1, len(self.results))
                self.request_render()
            elif k == 'down':
                self.sel = (self.sel + 1) % max(1, len(self.results))
                self.request_render()
            elif k == 'enter' and self.results:
                self.goto(self.results[self.sel])
            elif k == 'backspace':
                self.search = 'input'
                self.request_render()
            return True
        if k in ('g', 'esc', 'q'):
            self.leave_gfx()
        elif k == 'c':
            self.change('gfx_look', fbmode.LOOK_ORDER)
        elif k == 'd':
            self.change('gfx_dither', list(fbmode.DITHERS))
        elif k == 's':
            self.change('gfx_scanlines', [False, True])
        elif k in ('b', '/'):
            self.search = 'input'
            self.query = ''
            self.request_render()
        elif k in ('+', '='):
            self.zoom(1)
        elif k == '-':
            self.zoom(-1)
        elif k == 'up':
            self.pan(0, -PAN_PIXELS)
        elif k == 'down':
            self.pan(0, PAN_PIXELS)
        elif k == 'left':
            self.pan(-PAN_PIXELS, 0)
        elif k == 'right':
            self.pan(PAN_PIXELS, 0)
        elif k in ('home', 'h'):
            self.goto_berlin()
        elif k == 'r':
            self.fetch_again()
        elif k == 't':
            self.cycle_source()
        else:
            return False
        return True

    def change(self, key, values):
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
            self.err = str(e)
            return
        self.note(fbmode.LOOKS[self.settings()[0]][2] if key == 'gfx_look' else None)
        self.request_render()

    def cycle_source(self):
        key = self.wm.cfg.get('map_tiles', maps.DEFAULT_SOURCE)
        nxt = maps.SOURCE_ORDER[(maps.SOURCE_ORDER.index(key) + 1) % len(maps.SOURCE_ORDER)] \
            if key in maps.SOURCE_ORDER else maps.SOURCE_ORDER[0]
        self.wm.cfg['map_tiles'] = nxt
        self.wm.cfg.save()
        self.store.set_source(nxt)
        self.offline_until = 0.0
        self.note('SOURCE: %s' % maps.SOURCES[nxt][0].upper())
        self.request_render()
        self.invalidate()

    # ------------------------------------------------------------ search
    def start_search(self):
        q = self.query.strip()
        if not q or self.searching:
            return
        self.searching = True
        self.search = 'results'
        self.results = []
        self.sel = 0
        self.request_render()

        def done(res):
            self.searching = False
            self.results = res
            self.sel = 0
            self.request_render()

        def failed(e):
            self.searching = False
            self.results = []
            self.note('SUCHE: %s' % str(e)[:50], secs=4.0)
            self.request_render()

        self.thread(lambda: maps.geocode(q, limit=6, viewbox=self.view.bounds()),
                    done, failed)

    def goto(self, r):
        self.view.set_centre(r['lat'], r['lon'])
        z = maps.zoom_for_bbox(r.get('bbox'), self.view.width, self.view.height)
        if z:
            self.view.zoom = max(2, min(19, z))
        self.search = ''
        self.save_view()
        self.note(ascii_upper(r['name'])[:40])
        self.request_render()

    # ------------------------------------------------------------ mouse / idle
    def on_mouse(self, ev):
        if not self.gfx:
            return super().on_mouse(ev)
        g = self.gfx
        term = self.wm.term
        sx = g.screen[0] / float(max(1, term.cols))
        sy = g.screen[1] / float(max(1, term.rows))
        x, y = ev.x * sx, ev.y * sy
        if ev.kind == 'wheelup':
            self.zoom(1)
            return True
        if ev.kind == 'wheeldown':
            self.zoom(-1)
            return True
        if ev.kind == 'down' and ev.button == 0:
            self.drag = (x, y)
            self.gfx.poke()
            return True
        if ev.kind in ('drag', 'move') and self.drag:
            lx, ly = self.drag
            self.drag = (x, y)
            s = fbmode.SCALE
            if abs(x - lx) >= s * 0.5 or abs(y - ly) >= s * 0.5:
                self.pan(-(x - lx) / s, -(y - ly) / s)
            return True
        if ev.kind == 'up':
            self.drag = None
            return True
        return True

    def idle_tick(self):
        if not self.gfx:
            return
        # repaint after the VT came back (the console does not redraw graphics)
        self.gfx.tick(time.monotonic(), 0, 0, False)


APP = Maps
