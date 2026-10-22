"""Video player: ffmpeg decodes straight to cell resolution, frames are rebuilt as ASCII/dithered
cells; sound runs through the audio pipeline which also serves as the master clock."""
import os
import subprocess
import time

from .. import imaging
from ..app import App
from ..audio import AudioPlayer, probe
from ..util import HOME, fit, which
from ..widgets import button, hbar
from .music import fmt_t

MODES = ['blend', 'pixel', 'grey', 'ascii', 'mono']


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
    def start(self, t):
        self.stop_decoder()
        self.ended = False
        self.offset = max(0.0, t)
        self.frames = []
        self.buf = bytearray()
        self.eof = False
        self.shown = -1
        self.next_index = 0
        self.fps = self.wm.cfg.get('video_fps', 15)
        info = self.info or {}
        if info.get('video'):
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
        if info.get('audio') and not self.paused:
            if not self.audio.play(self.path, self.offset):
                if not self.audio_err_shown:
                    self.audio_err_shown = True
                    self.wm.toast('Audio: %s' % self.audio.error)
        elif self.paused:
            self.audio.stop()
        self.clock0 = time.monotonic()
        self.last_audio = self.offset
        self.last_audio_at = 0.0
        if self.paused:
            self.pause_pos = self.offset

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
            if self.dec and not self.reading and len(self.frames) < self.fps and not self.eof:
                try:
                    self.wm.loop.add_reader(self.dec.stdout.fileno(), self.on_data)
                    self.reading = True
                except (ValueError, OSError):
                    pass
            if frame and frame[0] != self.shown:
                t0 = time.monotonic()
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
            button(self, cv, 2, cy + 3, 'Open video', 'open', primary=True)
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
                           ('Open', 'open'), (self.mode, 'mode'), ('Full', 'full')):
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

    def on_key(self, ev):
        k = ev.key
        if k in (' ', 'k', 'p'):
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
