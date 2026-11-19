"""Maps: OpenStreetMap as real pixels on the Linux console.

Same graphics mode as the video player (`g` there): the console leaves text mode,
frames go into the framebuffer, and the picture is reduced to a classic adapter's
colours with ordered dithering. The map is composed from OSM raster tiles into a
quarter-resolution canvas and then handed to exactly the player's pipeline
(fbmode.image_command), so the looks, dithering and scanlines are the same code.

Keys in graphics mode:  arrows pan, + / - zoom, wheel zooms, drag pans,
    B or / place search (Enter jumps to the result), C look, D dither,
    S scanlines, T tile source, R refetch, W rain radar, , / . radar time
    (-2 h .. +2 h), N back to now, G / Esc back to the window.
"""
import time

from .. import fbmode, maps, radar
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
        self.goriginal = False      # 'o': full resolution, full colour (this session only)
        self.gzoff = 0              # zoom levels added for it (keeps the same area)
        self.radar = radar.RadarLayer()
        self.radar_on = bool(cfg.get('map_radar', False))
        self.radar_offset = 0       # minutes around now, -SPAN .. +SPAN
        self.radar_busy = False     # a picture is being fetched
        self.radar_range_busy = False
        self.radar_retry = 0.0      # monotonic time before the next try
        self.radar_when = None      # frame time on screen
        self.radar_hold = None      # (box, picture) kept while the next one flies
        self.radar_note = ''        # 'LAEDT' or what went wrong
        self.every(0.04, self.tick_render)
        self.every(0.5, self.idle_tick)
        self.every(20, self.radar_check)
        self.later(0.2, self.auto_gfx)

    # ------------------------------------------------------------ helpers
    def settings(self):
        cfg = self.wm.cfg
        look = cfg.get('gfx_look', 'vga')
        look = fbmode.OLD_LOOKS.get(look, look)
        look = look if look in fbmode.LOOKS else 'vga'
        dither = cfg.get('gfx_dither', 'bayer4')
        scale = 1 if self.goriginal else fbmode.SCALE
        return look, scale, dither, bool(cfg.get('gfx_scanlines', False))

    def label(self):
        look, scale, dither, scan = self.settings()
        cw, ch = fbmode.canvas(self.gfx.screen, scale) if self.gfx else (0, 0)
        if self.goriginal:
            return '%s  %dX%d  FULL COLOUR' % (fbmode.ORIGINAL_LABEL, cw, ch)
        return '%s  %dX%d  %s%s' % (fbmode.LOOKS[look][0], cw, ch, fbmode.dither_name(dither),
                                    '  SCANLINES' if scan else '')

    def source(self):
        return maps.source_info(self.wm.cfg.get('map_tiles', maps.DEFAULT_SOURCE))

    def save_view(self):
        cfg = self.wm.cfg
        cfg['map_lat'] = round(self.view.lat, 5)
        cfg['map_lon'] = round(self.view.lon, 5)
        cfg['map_zoom'] = max(1, int(self.view.zoom) - self.gzoff)
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
        if self.radar_on:
            cv.put(2, y, 'RADAR', th.c('dim', 'field'))
            cv.put(10, y, fit(self.radar_state(), w - 12), th.c('text', 'field'))
            y += 1
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
            cv.put(2, y + 3, 'W RAIN RADAR (DWD)   , . RADAR TIME   N NOW',
                   th.c('dim', 'field'))
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
        elif k == 'w':
            self.toggle_radar()
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
        cw, ch = fbmode.canvas(sess.screen, self.settings()[1])
        self.view.resize(cw, ch)
        self.frame = None
        try:
            self.start_pipe()
        except OSError as e:
            self.leave_gfx()
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        self.note('B SEARCH  +/- ZOOM  ARROWS PAN  O ORIGINAL',
                  'C LOOK  D DITHER  S SCANLINES  W RADAR  G EXIT',
                  secs=5.0)
        self.request_render()

    def leave_gfx(self):
        if not self.gfx:
            return
        self.save_view()
        self.stop_pipe()
        self.set_original(False, restart=False)     # one stay in graphics mode only
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
        pal = maps.palette_path(radar.RAMP if self.radar_on else ()) if look == 'vga' \
            else None
        self.pipe = fbmode.StillPipeline(
            (cw, ch), g.fb, theme_rgb=self.th.rgb,
            accent=self.th.rgb[self.th.r['accent']], look=look, scale=scale,
            dither=dither, scanlines=scan, vga_palette=pal, bayer_scale=MAP_BAYER,
            original=self.goriginal)

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
        img = maps.compose(self.view, self.store,
                           radar=self.radar_picture() if self.radar_on else None)
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

    # ------------------------------------------------------------ rain radar
    def radar_state(self):
        """One line for the window: what the layer is doing right now."""
        if not self.radar_on:
            return 'off'
        when = self.radar_when
        bits = ['on']
        if when:
            bits.append('%s %s' % (radar.clock(when),
                                   radar.offset_label(radar.offset_minutes(when,
                                                                           time.time()))))
        if self.radar_note:
            bits.append(self.radar_note.lower())
        return '  '.join(bits) + '  (' + radar.NAME + ')'

    def radar_picture(self):
        """The radar canvas for this view, or None while it is still coming."""
        if not self.radar_on:
            return None
        if self.radar.range is None or self.radar.range_stale():
            self.radar_note = self.radar.range_error or 'LAEDT'
            self.radar_load_range()
            if self.radar.range is None:
                return None
        if not self.radar.covers(self.view):
            self.radar_note = 'NUR UEBER DEUTSCHLAND'
            self.radar_when = None
            return None
        req = self.radar.request(self.view, self.radar_offset)
        if req is None:
            self.radar_note = 'KEIN BILD ZU DIESER ZEIT'
            return None
        img = self.radar.image(req)
        if img is None:
            self.radar_note = 'LAEDT'
            self.radar_load(req)
            if self.radar_hold and self.radar_hold[0] == req.box:
                return self.radar_hold[1]      # the frame before, same area
            return None
        self.radar_when = req.when
        self.radar_hold = (req.box, img)
        self.radar_note = self.radar.error or ''
        return img

    def radar_load(self, req):
        """Fetch one frame in the background (one at a time)."""
        if self.radar_busy or time.monotonic() < self.radar_retry:
            return
        self.radar_busy = True

        def work():
            try:
                return self.radar.load(req)
            finally:
                self.radar.prune()

        def done(_):
            self.radar_busy = False
            self.request_render()
            self.radar_prefetch()

        def failed(e):
            self.radar_busy = False
            self.radar_retry = time.monotonic() + 60
            self.radar_note = str(e)[:50]
            self.note('RADAR: %s' % str(e)[:44], secs=4.0)
            self.request_render()

        self.thread(work, done, failed)

    def radar_prefetch(self):
        """After a picture arrives, quietly collect its neighbours in time, so
        that , and . answer from the disk instead of waiting for the DWD."""
        if not self.radar_on or not self.gfx or self.radar_busy:
            return
        if time.monotonic() < self.radar_retry:
            return
        for step in (radar.STEP_UI, -radar.STEP_UI):
            off = self.radar_offset + step
            if abs(off) > radar.SPAN:
                continue
            req = self.radar.request(self.view, off)
            if req is not None and self.radar.image(req) is None:
                self.radar_load(req)
                return

    def radar_load_range(self):
        """The valid times come from the capabilities (~1 MB, every half hour)."""
        if self.radar_range_busy or time.monotonic() < self.radar_retry:
            return
        self.radar_range_busy = True

        def done(_):
            self.radar_range_busy = False
            self.radar_note = ''
            self.request_render()

        def failed(e):
            self.radar_range_busy = False
            self.radar_retry = time.monotonic() + 60
            self.radar_note = str(e)[:50]
            self.note('RADAR: %s' % str(e)[:44], secs=4.0)
            self.request_render()

        self.thread(self.radar.load_range, done, failed)

    def radar_check(self):
        """Every 20 s: advance with the nowcast, retry, refresh the time range."""
        if not self.radar_on or not self.gfx:
            return
        if self.radar.range_stale():
            self.radar_load_range()
            return
        req = self.radar.request(self.view, self.radar_offset)
        if req is not None and req.when != self.radar_when and not self.radar_busy:
            self.request_render()

    def toggle_radar(self):
        self.radar_on = not self.radar_on
        self.radar_offset = 0
        self.radar_when = None
        self.radar_note = ''
        self.radar_retry = 0.0
        self.wm.cfg['map_radar'] = self.radar_on
        self.wm.cfg.save()
        if self.gfx:                        # the palette holds the radar classes
            try:
                self.start_pipe()
            except OSError as e:
                self.err = str(e)
                return
            self.note('REGENRADAR AN (%s)' % radar.NAME if self.radar_on
                      else 'REGENRADAR AUS')
            if self.radar_on:
                self.radar_load_range()
        self.request_render()
        self.invalidate()

    def step_radar_time(self, delta):
        """Move through the nowcast: -2 h ... +2 h in ten-minute steps."""
        if not self.radar_on:
            self.note('W SCHALTET DAS REGENRADAR EIN')
            return
        new = max(-radar.SPAN, min(radar.SPAN, self.radar_offset + delta))
        if new == self.radar_offset:
            self.note('RADAR: %s' % radar.offset_label(new))
            return
        self.radar_offset = new
        self.radar_note = ''
        self.request_render()

    def radar_now(self):
        if not self.radar_on:
            return
        self.radar_offset = 0
        self.radar_note = ''
        self.request_render()

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
                                   target=max(40, W // 16 // self.settings()[1]))
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
        if self.radar_on:
            self.draw_radar_hud(buf)
        if self.search:
            self.draw_search(buf)
        if self.err:
            self.rect(buf, 0, 0, W, 7 * px, g.c_bg)
            self.text(buf, 2 * px, 2 * px, ascii_upper(self.err)[:80], px, g.c_fg)

    def draw_radar_hud(self, buf):
        """Legend and time of the radar layer, above the status bar."""
        g = self.gfx
        W, H = g.screen
        px = self.P()
        cell = 4 * px
        strip = len(radar.RAMP) * cell
        when = self.radar_when
        if when:
            head = '%s %s  %s' % (
                radar.NAME, radar.clock(when),
                'LAEDT' if self.radar_note == 'LAEDT'
                else radar.offset_label(radar.offset_minutes(when, time.time())))
        else:
            head = '%s %s' % (radar.NAME, self.radar_note or 'LAEDT')
        tail = 'MM/H'
        while head and fbmode.text_width(head, px) > W - 8 * px:
            head = head[:-1]
        w = max(strip, fbmode.text_width(head, px) + 6 * px
                + fbmode.text_width(tail, px)) + 4 * px
        h = 20 * px
        if w > W - 4 * px:                  # narrow screen: drop the tail first
            tail = ''
            w = max(strip, fbmode.text_width(head, px)) + 4 * px
        x = W - w - 2 * px
        y = H - 29 * px
        self.rect(buf, x, y, w, h, g.c_bg)
        self.rect(buf, x, y, w, px, g.c_acc)
        tx = x + 2 * px
        self.text(buf, tx, y + 2 * px, head, px, g.c_fg)
        if tail:
            self.text(buf, x + w - fbmode.text_width(tail, px) - 2 * px, y + 2 * px,
                      tail, px, g.c_dim)
        sy = y + 9 * px
        for i, rgb in enumerate(radar.RAMP):
            self.rect(buf, tx + i * cell, sy, cell - px, 3 * px, rgb)
        ly = y + 13 * px
        for i in radar.TICKS:
            label = radar.RAMP_MM[i]
            lx = tx + i * cell
            if i == len(radar.RAMP) - 1:     # the last class would stick out
                lx = tx + strip - fbmode.text_width(label, px)
            self.text(buf, lx, ly, label, px, g.c_dim)

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
        elif k == 'o':
            self.set_original(not self.goriginal)
            self.note('O: BACK TO THE LOOK' if self.goriginal else 'O: ORIGINAL')
            self.request_render()
        elif k == 'c':
            self.change('gfx_look', fbmode.LOOK_ORDER)
        elif k == 'd':
            self.change('gfx_dither', list(fbmode.DITHERS))
        elif k == 's':
            self.change('gfx_scanlines', [False, True])
        elif k == 'w':
            self.toggle_radar()
        elif k == ',':
            self.step_radar_time(-radar.STEP_UI)
        elif k == '.':
            self.step_radar_time(radar.STEP_UI)
        elif k == 'n':
            self.radar_now()
        elif k in ('b', '/'):
            self.search = 'input'
            self.query = ''
            self.request_render()
        elif k in ('+', '='):
            self.zoom(1)
        elif k == '-':
            self.zoom(-1)
        elif k in ('up', 'down', 'left', 'right'):
            d = PAN_PIXELS * fbmode.SCALE // self.settings()[1]   # same distance on screen
            self.pan(*{'up': (0, -d), 'down': (0, d), 'left': (-d, 0), 'right': (d, 0)}[k])
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
        elif key == 'gfx_dither':
            cur = fbmode.OLD_DITHERS.get(cur, cur)
        cfg[key] = values[(values.index(cur) + 1) % len(values)] if cur in values \
            else values[0]
        cfg.save()
        if self.goriginal:                  # a look key means: show me the look
            self.set_original(False, restart=False)
        try:
            self.start_pipe()
        except OSError as e:
            self.err = str(e)
            return
        self.note(fbmode.change_note(key, cfg, self.settings()[0]))
        self.request_render()

    def set_original(self, on, restart=True):
        """Full resolution, full colour - or back to the look. The map keeps the same
        area: four times the pixels are two tile zoom levels."""
        if on == self.goriginal:
            return
        self.goriginal = on
        if on:
            z0 = self.view.zoom
            self.view.zoom_by(2)
            self.gzoff = self.view.zoom - z0
        else:
            self.view.zoom_by(-self.gzoff)
            self.gzoff = 0
        if self.gfx:
            self.view.resize(*fbmode.canvas(self.gfx.screen, self.settings()[1]))
            if restart:
                try:
                    self.start_pipe()
                except OSError as e:
                    self.err = str(e)

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
        sx = g.screen[0] / float(max(1, term.w))
        sy = g.screen[1] / float(max(1, term.h))
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
            s = self.settings()[1]
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
