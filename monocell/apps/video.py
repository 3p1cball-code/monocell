"""Video player: ffmpeg decodes straight to cell resolution, frames are rebuilt as ASCII/dithered
cells; sound runs through the audio pipeline which also serves as the master clock.

On the Linux console `g` switches to a real graphics mode (fbmode): the console
leaves text mode like a DOS game and the video goes into the framebuffer as hard,
dithered pixels in the colours of a classic PC graphics adapter."""
import fcntl
import os
import subprocess
import time

from .. import fbmode, imaging
from ..app import App
from ..audio import AudioPlayer, probe
from ..util import CACHE_DIR, HOME, fit, log, which
from ..widgets import button, hbar
from .music import fmt_t

MODES = ['blend', 'pixel', 'grey', 'ascii', 'mono']
# how far the picture may fall behind the sound before it is restarted (seconds)
LAG_LIMIT = float(os.environ.get('MONOCELL_GFX_LAG', '3'))
DEBUG = bool(os.environ.get('MONOCELL_GFX_DEBUG'))   # per-second numbers in the log


class Video(App):
    name = 'video'
    preserve_colors = True
    title = 'Video'
    icon = '[>]'
    size = (96, 34)
    min_size = (30, 8)

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.path = None
        self.info = None
        self.audio = AudioPlayer(wm, self, on_end=None, on_error=lambda m: wm.toast(m, 'red'))
        self.dec = None
        self.buf = bytearray()
        self.frames = []           # list of (index, rgb bytes)
        self.frame_cells = None
        self.shown = -1
        self.dims = (0, 0)
        self.fps = wm.cfg.get('video_fps', 15)
        self.offset = 0.0          # media time of frame 0 of the current decoder
        self.clock0 = 0.0
        self.last_audio = 0.0      # last position reported by the audio master clock
        self.last_audio_at = 0.0
        self.paused = False
        self.pause_pos = 0.0
        self.chrome = True
        self.eof = False
        self.err = ''
        self.stats = [0, 0.0]      # frames shown, render ms
        self.restart_timer = None
        self.ended = False
        self.audio_err_shown = False
        self.gfx = None            # fbmode.Session while the graphics mode is on
        self.gfx_chrome = True     # window state to go back to
        self.gfx_pal = None        # (path, file) of the tuned VGA palette
        self.gfx_cap = 0           # frame rate cap after the CPU fell behind
        self.gfx_cur = None
        self.gfx_fill = 0
        self.gfx_free = []
        self.resyncs = []
        self.last_resync = 0.0
        self.last_start = 0.0
        self.reading = False
        self.tuning = None
        if path:
            self.open(path)
        self.every(1.0 / 60, self.tick)

    @property
    def mode(self):
        return self.wm.cfg.get('video_mode', 'pixel')

    def proc_info(self):
        return '%dx%d @%dfps' % (self.dims[0], self.dims[1], self.fps) if self.dec else ''

    def pick(self):
        d = os.path.join(HOME, 'Videos')
        self.wm.pick_file(self.open, 'open', start=d if os.path.isdir(d) else HOME, parent=self.win, title='Open video')

    def reopen(self, path=None, **kw):
        if path:
            self.open(path)

    def open(self, path):
        self.stop()
        self.path = path
        self.gfx_cap = 0
        self.info = None
        self.err = ''
        self.set_title(os.path.basename(path))
        if not which('ffmpeg'):
            self.err = 'ffmpeg is required for video playback'
            return

        def done(info):
            if self.path != path:
                return
            self.info = info
            if not info.get('video'):
                self.err = 'no video stream (playing audio only)' if info.get('audio') else 'unsupported file'
            self.start(0.0)
            self.invalidate()
        self.thread(lambda: probe(path), done)

    # ------------------------------------------------------------ geometry
    def area(self):
        w, h = self.cw, self.ch
        return (w, h - 2) if self.chrome else (w, h)

    def target_dims(self):
        aw, ah = self.area()
        v = (self.info or {}).get('video') or {}
        vw, vh = v.get('w', 16) * v.get('sar', 1.0), v.get('h', 9)
        return imaging.fit_size(vw, vh, max(1, aw), max(1, ah), imaging.cell_aspect(self.wm))

    # ------------------------------------------------------------ pipeline
    def start(self, t, audio=True):
        self.stop_decoder()
        self.ended = False
        self.offset = max(0.0, t)
        self.frames = []
        self.buf = bytearray()
        self.eof = False
        self.shown = -1
        self.next_index = 0
        self.fps = self.wm.cfg.get('video_fps', 15)
        self.last_start = time.monotonic()
        info = self.info or {}
        if info.get('video') and self.gfx:
            self.start_gfx_decoder(info['video'])
        elif info.get('video'):
            w, h = self.target_dims()
            self.dims = (w, h)
            vf = 'fps=%d,scale=%d:%d:flags=area' % (self.fps, w, h)
            cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', '%.3f' % self.offset, '-i', self.path, '-an', '-sn',
                   '-vf', vf, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
            try:
                self.dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL)
                os.set_blocking(self.dec.stdout.fileno(), False)
                self.wm.loop.add_reader(self.dec.stdout.fileno(), self.on_data)
                self.reading = True
            except OSError as e:
                self.err = str(e)
        # while paused only the picture is decoded; sound starts on resume, so that
        # nothing keeps playing out of the sink buffer behind a frozen frame
        if info.get('audio') and not self.paused and audio:
            if not self.audio.play(self.path, self.offset):
                if not self.audio_err_shown:
                    self.audio_err_shown = True
                    self.wm.toast('Audio: %s' % self.audio.error)
        elif self.paused:
            self.audio.stop()
        # a picture-only restart (audio=False) leaves the running sound alone
        self.clock0 = time.monotonic()
        self.last_audio = self.offset
        self.last_audio_at = 0.0
        if self.paused:
            self.pause_pos = self.offset

    # ------------------------------------------------------------ graphics mode
    def gfx_rate(self):
        """Frames per second in graphics mode. 'auto' is 25: at a quarter resolution a
        Core 2 Duo (X61s) holds that even for 1080p h264, with sound."""
        v = (self.info or {}).get('video') or {}
        f = int(self.wm.cfg.get('gfx_fps', 0) or 25)
        if v.get('fps'):
            f = min(f, max(1, int(round(v['fps']))))
        if self.gfx_cap:
            f = min(f, self.gfx_cap)
        return f

    def gfx_settings(self):
        cfg = self.wm.cfg
        look = cfg.get('gfx_look', 'vga')
        look = fbmode.OLD_LOOKS.get(look, look)
        look = look if look in fbmode.LOOKS else 'vga'
        dither = cfg.get('gfx_dither', 'bayer')
        # always a quarter of the screen resolution: more is not smooth on old hardware
        return look, fbmode.SCALE, dither, bool(cfg.get('gfx_scanlines', False))

    def start_gfx_decoder(self, video):
        look, scale, dither, scan = self.gfx_settings()
        g = self.gfx
        self.fps = self.gfx_rate()
        th = self.th
        pal = self.gfx_pal[1] if self.gfx_pal and self.gfx_pal[0] == self.path else None
        try:
            cmd, cv = fbmode.command(self.path, self.offset, video, g.screen, g.fb.line_px,
                                     g.fb.pix_fmt, theme_rgb=th.rgb,
                                     accent=th.rgb[th.r['accent']], look=look, scale=scale,
                                     dither=dither, scanlines=scan, fps=self.fps,
                                     vga_palette=pal)
        except (OSError, ValueError) as e:
            self.err = str(e)
            return
        self.dims = cv
        g.scale = scale
        self.gfx_cur = None
        self.gfx_fill = 0
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            log = open(os.path.join(CACHE_DIR, 'gfx-ffmpeg.log'), 'ab')
        except OSError:
            log = subprocess.DEVNULL
        try:
            self.dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log,
                                        stdin=subprocess.DEVNULL, bufsize=0)
            fd = self.dec.stdout.fileno()
            try:                       # 64 kB default means 48 reads for a 3 MB frame
                fcntl.fcntl(fd, 1031, 1 << 20)          # F_SETPIPE_SZ
            except OSError:
                pass
            os.set_blocking(fd, False)
            self.wm.loop.add_reader(self.dec.stdout.fileno(), self.on_gfx_data)
            self.reading = True
        except OSError as e:
            self.err = str(e)
        finally:
            if log is not subprocess.DEVNULL:
                log.close()

    def on_gfx_data(self, fd):
        """Whole framebuffer frames straight into reused buffers.

        Reads until the pipe is empty, not just once: one callback per select()
        woke the loop for every few hundred kB, and that overhead alone kept the
        player at about 18 frames a second."""
        fs = self.gfx.frame_size if self.gfx else 0
        if not fs or not self.dec:
            return
        if DEBUG:
            self.rx = getattr(self, 'rx', 0)
        for _ in range(64):
            if len(self.frames) >= 4:            # a few frames ahead are plenty
                self._gfx_pause()
                return
            if self.gfx_cur is None:
                # reuse the buffers: a new 3 MB bytearray per frame is 75 MB/s of zeroing
                self.gfx_cur = self.gfx_free.pop() if self.gfx_free else bytearray(fs)
                self.gfx_fill = 0
            try:
                n = self.dec.stdout.readinto(memoryview(self.gfx_cur)[self.gfx_fill:])
            except BlockingIOError:
                return
            except (OSError, ValueError):
                n = 0
            if n is None:
                return
            if n == 0:
                self.eof = True
                self._gfx_pause()
                return
            self.gfx_fill += n
            if DEBUG:
                self.rx += n
                self.ncalls = getattr(self, 'ncalls', 0) + 1
            if self.gfx_fill >= fs:
                self.frames.append((self.next_index, self.gfx_cur))
                self.next_index += 1
                self.gfx_cur = None
                self.invalidate()

    def _gfx_pause(self):
        if self.dec:
            try:
                self.wm.loop.remove_reader(self.dec.stdout.fileno())
            except (ValueError, OSError):
                pass
        self.reading = False

    def gfx_label(self):
        look, scale, dither, scan = self.gfx_settings()
        cw, ch = fbmode.canvas(self.gfx.screen, scale) if self.gfx else (0, 0)
        parts = [fbmode.LOOKS[look][0], '%dX%d' % (cw, ch), dither, '%d FPS' % self.gfx_rate()]
        if scan:
            parts.append('SCANLINES')
        return '  '.join(parts)

    def gfx_note(self, *extra, secs=3.0):
        if self.gfx:
            self.gfx.note([self.gfx_label()] + [e for e in extra if e], secs)

    def enter_gfx(self):
        ok, why = fbmode.available(self.wm.term)
        if not ok:
            self.wm.toast(why, 'red')
            return
        if not (self.info or {}).get('video'):
            self.wm.toast('Graphics mode needs a video', 'red')
            return
        th = self.th
        rgb = th.rgb
        colours = {'bg': rgb[th.r['shadow']], 'fg': rgb[th.r['bright']], 'dim': rgb[th.r['dim']],
                   'accent': rgb[th.r['accent']], 'field': rgb[th.r['field']]}
        try:
            sess = fbmode.Session(self.wm.term.fd_out, fbmode.open_framebuffer(), colours)
        except OSError as e:
            self.wm.toast('Graphics mode: %s' % e, 'red')
            return
        pos = self.media_time()
        self.gfx_chrome = self.chrome
        if self.chrome:
            self.chrome = False
            self.set_borderless(True)
        try:
            sess.start()
        except OSError as e:
            sess.close()
            self.wm.toast('Graphics mode: %s' % e, 'red')
            if self.gfx_chrome:
                self.chrome = True
                self.set_borderless(False)
            return
        self.gfx = sess
        self.wm.enter_gfx(self)
        self.resyncs = []
        self.gfx_t0 = (time.monotonic(), self.stats[0], 0)
        tuning = self.tune_palette()
        self.gfx_note('G EXIT  C LOOK  D DITHER  S SCANLINES',
                      'TUNING THE PALETTE...' if tuning else None, secs=5.0)
        self.start(pos)

    def leave_gfx(self, restart=True):
        if not self.gfx:
            return
        label = self.gfx_label()
        sess, self.gfx = self.gfx, None
        t0, n0, _ = getattr(self, 'gfx_t0', (time.monotonic(), self.stats[0], 0))
        dt = max(0.001, time.monotonic() - t0)
        log('gfx: %s, %.1f s, %.1f frames/s shown (target %d), %.1f ms per frame write'
            % (label, dt, (self.stats[0] - n0) / dt, self.gfx_rate(), self.stats[1]))
        pos = self.media_time()
        self.stop_decoder()
        self.frames = []
        sess.close()
        self.wm.leave_gfx()
        if self.gfx_chrome and restart:
            self.chrome = True
            self.set_borderless(False)
        self.frame_cells = None
        if restart:
            self.start(pos)
        self.invalidate()

    def tune_palette(self):
        """VGA: compute the video's own 256 colours once in the background (cached).
        True while that runs (until then VGA uses an even colour cube)."""
        path = self.path
        if self.gfx_settings()[0] != 'vga':
            return False                      # only the 256-colour look tunes one
        if not path or (self.gfx_pal and self.gfx_pal[0] == path):
            return False
        if getattr(self, 'tuning', None) == path:
            return True
        cached = fbmode.video_palette_path(path)
        if cached and os.path.exists(cached):
            self.gfx_pal = (path, cached)
            return False
        self.tuning = path

        def done(res):
            self.tuning = None
            if res and self.path == path:
                self.gfx_pal = (path, res)
                if self.gfx and self.gfx_settings()[0] == 'vga' and not self.ended:
                    self.start(self.media_time())       # switch to the tuned colours
                    self.gfx_note('PALETTE TUNED TO THIS VIDEO')

        def failed(e):
            self.tuning = None
        self.thread(lambda: fbmode.make_video_palette(path), done, failed)
        return True

    def gfx_change(self, key, values):
        cfg = self.wm.cfg
        cur = cfg.get(key, values[0])
        if key == 'gfx_look':
            cur = fbmode.OLD_LOOKS.get(cur, cur)
        cfg[key] = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
        cfg.save()
        tuning = key == 'gfx_look' and cfg[key] == 'vga' and self.tune_palette()
        self.gfx_cap = 0
        self.start(self.media_time())
        self.gfx_note(fbmode.LOOKS[self.gfx_settings()[0]][2] if key == 'gfx_look' else None,
                      'TUNING THE PALETTE...' if tuning else None)

    def gfx_key(self, k):
        """Keys while the console shows graphics; True when handled here."""
        if k in ('g', 'esc', 'q', 'f', 'enter'):
            self.leave_gfx()
        elif k == 'c':
            self.gfx_change('gfx_look', fbmode.LOOK_ORDER)
        elif k == 'd':
            self.gfx_change('gfx_dither', list(fbmode.DITHERS))
        elif k == 's':
            self.gfx_change('gfx_scanlines', [False, True])
        elif k in ('+', '=', '-'):
            self.audio.volume = (min(1.5, self.audio.volume + 0.1) if k != '-'
                                 else max(0, self.audio.volume - 0.1))
            self.gfx.note(['VOLUME %d%%' % round(self.audio.volume * 100)], 1.5)
        elif k in ('m', 'o', 'ctrl+o'):
            pass                                # no text UI to show in graphics mode
        else:
            return False
        return True

    def gfx_tick(self, t):
        g = self.gfx
        dur = (self.info or {}).get('duration') or 0
        g.tick(time.monotonic(), t, dur, self.paused)
        # The CPU fell behind. A fresh decoder needs a moment before its first
        # frame, so a short lag is normal and must not restart anything: that
        # would only lose more frames (the restart takes about as long as the
        # lag it is meant to fix). Only a real, lasting lag is corrected, and at
        # most every 10 s. The sound keeps running - only the picture restarts.
        now = time.monotonic()
        if self.paused or not self.dec or self.shown < 0 or now - self.last_start < 8:
            return
        want = int((t - self.offset) * self.fps)
        if DEBUG and now - getattr(self, '_dbg', 0) > 2:
            dt = max(0.001, now - getattr(self, '_dbg', now - 2))
            self._dbg = now
            log('gfx dbg: t=%.2f want=%d shown=%d recv=%d ahead=%d queued=%d reading=%s '
                'rx=%.1fMB/s calls=%.0f/s ticks=%.0f/s pipe=%d'
                % (t, want, self.shown, self.next_index, want - self.shown,
                   len(self.frames), self.reading,
                   getattr(self, 'rx', 0) / dt / 1e6, getattr(self, 'ncalls', 0) / dt,
                   getattr(self, 'nticks', 0) / dt,
                   fcntl.fcntl(self.dec.stdout.fileno(), 1032) if self.dec else 0))
            self.rx = self.ncalls = self.nticks = 0
        if want - self.shown > LAG_LIMIT * self.fps:
            self.resyncs = [r for r in self.resyncs if now - r < 30]
            if now - getattr(self, 'last_resync', 0) >= 10:
                self.last_resync = now
                log('gfx: %.1f s behind the sound, catching up'
                    % ((want - self.shown) / max(1, self.fps)))
                self.resyncs.append(now)
                if len(self.resyncs) >= 2 and self.fps > 12:
                    self.gfx_cap = max(12, self.fps - 5)
                    self.resyncs = []
                    self.gfx_note('TOO SLOW - DOWN TO %d FPS' % self.gfx_rate())
                self.start(t, audio=False)

    def stop_decoder(self):
        if self.dec:
            try:
                self.wm.loop.remove_reader(self.dec.stdout.fileno())
            except (ValueError, OSError):
                pass
            try:
                self.dec.kill()
                self.dec.wait(timeout=1)
            except Exception:
                pass
            try:
                self.dec.stdout.close()
            except OSError:
                pass
        self.dec = None

    def stop(self):
        self.stop_decoder()
        self.audio.stop()

    def on_close(self):
        self.leave_gfx(restart=False)
        self.stop()
        return True

    def on_data(self, fd):
        try:
            data = os.read(fd, 1 << 18)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self.eof = True
            self.wm.loop.remove_reader(fd)
            self.reading = False
            return
        self.buf += data
        fs = self.dims[0] * self.dims[1] * 3
        while fs and len(self.buf) >= fs:
            self.frames.append((self.next_index, bytes(self.buf[:fs])))
            self.next_index += 1
            del self.buf[:fs]
        if len(self.frames) > self.fps * 2:
            self.wm.loop.remove_reader(fd)
            self.reading = False

    def media_time(self):
        if self.paused:
            return self.pause_pos
        if (self.info or {}).get('audio'):
            if self.audio.dec:
                self.last_audio = self.audio.position
                self.last_audio_at = time.monotonic()
                return self.last_audio
            if self.last_audio_at:       # sound ended (or failed): keep counting from there
                return self.last_audio + (time.monotonic() - self.last_audio_at)
        return self.offset + (time.monotonic() - self.clock0)

    def tick(self):
        if not self.info:
            return
        if DEBUG:
            self.nticks = getattr(self, 'nticks', 0) + 1
        t = self.media_time()
        # the sound is the master clock; the picture is rendered a frame's length
        # ahead so that it is on screen when its sound is due, and av_offset lets
        # the user trim the last few milliseconds by hand
        extra = 0.0 if self.paused else (min(1.0 / self.fps, self.stats[1] / 1000.0) +
                                         self.wm.cfg.get('av_offset', 0) / 1000.0)
        if self.dec or self.frames:
            want = max(0, int((t - self.offset + extra) * self.fps))
            frame = None
            while self.frames and self.frames[0][0] <= want:
                frame = self.frames.pop(0)
            ahead = 2 if self.gfx else self.fps
            if self.dec and not self.reading and len(self.frames) < ahead and not self.eof:
                try:
                    self.wm.loop.add_reader(self.dec.stdout.fileno(),
                                            self.on_gfx_data if self.gfx else self.on_data)
                    self.reading = True
                except (ValueError, OSError):
                    pass
            if frame and frame[0] != self.shown:
                t0 = time.monotonic()
                if self.gfx:
                    # the session keeps the frame it just showed; only the one before
                    # that is free to be filled again
                    prev = self.gfx.frame
                    self.gfx.show(frame[1])
                    if (prev is not None and prev is not self.gfx.frame
                            and len(self.gfx_free) < 4):
                        self.gfx_free.append(prev)
                else:
                    w, h = self.dims
                    self.frame_cells = imaging.render(self.wm, frame[1], w, h, self.mode)
                self.stats[0] += 1
                self.stats[1] = self.stats[1] * 0.9 + (time.monotonic() - t0) * 1000 * 0.1
                self.shown = frame[0]
                self.invalidate()
        dur = (self.info or {}).get('duration') or 0
        if not self.ended and dur and t >= dur - 0.05 and not self.frames and (self.eof or not self.dec):
            self.ended = True
            self.paused = True
            self.pause_pos = dur
            self.audio.stop()
        if self.gfx:
            self.gfx_tick(t)
            return
        if not self.paused:
            self.invalidate() if int(t * 4) != getattr(self, '_last_q', None) else None
            self._last_q = int(t * 4)

    def toggle(self):
        if self.ended:
            self.paused = False
            self.start(0)
            return
        if self.paused:
            self.paused = False
            self.start(self.pause_pos)
        else:
            # freeze picture and sound together: stopping the sink here means the
            # buffer cannot keep playing while the frame stands still
            self.pause_pos = self.media_time()
            self.paused = True
            self.stop()

    def seek(self, t):
        dur = (self.info or {}).get('duration') or 0
        t = max(0.0, min(t, max(0.0, dur - 0.5))) if dur else max(0.0, t)
        if self.ended:
            self.ended = False
            self.paused = False
        self.start(t)

    def on_resize(self, w, h):
        if self.info and self.info.get('video') and self.target_dims() != self.dims:
            if self.restart_timer:
                self.restart_timer.cancel()
            self.restart_timer = self.later(0.3, lambda: self.start(self.media_time()) if not self.ended else None)

    def on_theme(self):
        self.frame_cells = None

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        aw, ah = self.area()
        cv.fill(0, 0, aw, ah, th.c('dim', 'shadow'))
        if self.frame_cells:
            fw, fh = self.dims
            cv.sub(0, 0, aw, ah).blit_cells((aw - fw) // 2, (ah - fh) // 2, self.frame_cells)
        msg = self.err or ('' if self.info else ('Loading...' if self.path else ''))
        if msg:
            cv.put(2, 1, fit(msg, aw - 4), th.c('red' if self.err else 'dim', 'shadow'))
        if not self.info and not self.err and not self.path:
            cy = max(1, ah // 2 - 2)
            cv.put(2, cy, 'No video open', th.c('bright', 'shadow'))
            cv.put(2, cy + 1, 'ffmpeg rebuilds every frame as ASCII or 8-bit cells.',
                   th.c('dim', 'shadow'))
            cv.put(2, cy + 2, fit('On the Linux console, g plays it as real pixels (VGA/EGA/CGA).',
                                  aw - 4), th.c('dim', 'shadow'))
            button(self, cv, 2, cy + 4, 'Open video', 'open', primary=True)
            self.zone(0, 0, aw, ah, 'screen')
            return
        self.zone(0, 0, aw, ah, 'screen')
        if not self.chrome:
            return
        y = h - 2
        cv.fill(0, y, w, 2, th.c('text', 'raised'))
        dur = (self.info or {}).get('duration') or 0
        pos = self.media_time() if self.info else 0
        t0, t1 = fmt_t(pos), fmt_t(dur)
        cv.put(1, y, t0, th.c('text', 'raised'))
        cv.put(w - 1 - len(t1), y, t1, th.c('dim', 'raised'))
        bx = len(t0) + 2
        bw = w - bx - len(t1) - 2
        hbar(cv, bx, y, bw, pos / dur if dur else 0, th, bg='field')
        self.zone(bx, y, bw, 1, ('seek', bx, bw))
        x = 1
        y += 1
        for label, key in (('>' if self.paused or not self.info else '||', 'play'), ('<<', 'rew'), ('>>', 'ffw'),
                           ('Open', 'open'), (self.mode, 'mode'), ('Full', 'full'), ('Gfx', 'gfx')):
            x += button(self, cv, x, y, label, key, primary=key == 'play') + 1
        st = '%dx%d cells  %dfps  %.0fms/frame' % (self.dims[0], self.dims[1], self.fps, self.stats[1])
        if x + len(st) + 2 < w:
            cv.put(w - len(st) - 1, y, st, th.c('dim', 'raised'))

    def on_click(self, key, ev):
        if key == 'play':
            self.toggle()
        elif key == 'rew':
            self.seek(self.media_time() - 10)
        elif key == 'ffw':
            self.seek(self.media_time() + 10)
        elif key == 'open':
            self.pick()
        elif key == 'mode':
            cur = self.mode if self.mode in MODES else MODES[0]
            self.wm.cfg['video_mode'] = MODES[(MODES.index(cur) + 1) % len(MODES)]
            self.wm.cfg.save()
            self.wm.settings_changed()
        elif key == 'full':
            self.set_fullscreen(self.chrome)
        elif key == 'gfx':
            self.enter_gfx()
        elif key == 'screen':
            if ev.clicks >= 2:
                self.fullscreen(self.chrome)
            else:
                self.toggle()
        elif isinstance(key, tuple) and key[0] == 'seek':
            dur = (self.info or {}).get('duration') or 0
            if dur:
                self.seek(dur * (ev.x - key[1]) / max(1, key[2] - 1))

    def set_fullscreen(self, on):
        if on == (not self.chrome):
            return
        pos = self.media_time()
        if on:
            self.chrome = False
            self.set_borderless(True)
        else:
            self.chrome = True
            self.set_borderless(False)
        self.start(pos)
        self.invalidate()

    def fullscreen(self, on):
        self.set_fullscreen(on)

    def on_mouse(self, ev):
        if self.gfx:
            if ev.kind == 'down' and ev.button == 0:
                self.toggle()
                self.gfx.poke()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if self.gfx:
            if self.gfx_key(k):
                return True
            if k in (' ', 'k', 'p', 'left', 'right', 'up', 'down'):
                self.gfx.poke()
        if k == 'g':
            self.enter_gfx()
        elif k in (' ', 'k', 'p'):
            self.toggle()
        elif k == 'right':
            self.seek(self.media_time() + 5)
        elif k == 'left':
            self.seek(self.media_time() - 5)
        elif k == 'up':
            self.seek(self.media_time() + 60)
        elif k == 'down':
            self.seek(self.media_time() - 60)
        elif k == 'm':
            self.on_click('mode', None)
        elif k in ('f', 'enter'):
            self.set_fullscreen(self.chrome)
        elif k == 'esc' and not self.chrome:
            self.set_fullscreen(False)
        elif k in ('+', '='):
            self.audio.volume = min(1.5, self.audio.volume + 0.1)
        elif k == '-':
            self.audio.volume = max(0, self.audio.volume - 0.1)
        elif k in ('o', 'ctrl+o'):
            self.pick()
        else:
            return False
        self.invalidate()
        return True


APP = Video
