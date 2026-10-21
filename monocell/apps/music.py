"""Music player: folder playlist, transport, seek bar, volume and a live ASCII spectrum."""
import os
import random

from ..app import App
from ..audio import AudioPlayer, Spectrum, probe
from ..util import HOME, fit
from ..widgets import ListView, button, hbar
from .files import AUDIO


def fmt_t(s):
    s = int(max(0, s))
    return '%d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else '%d:%02d' % (s // 60, s % 60)


def scan(folder, depth=2):
    out = []
    try:
        for n in sorted(os.listdir(folder), key=str.lower):
            if n.startswith('.'):
                continue
            p = os.path.join(folder, n)
            if os.path.isdir(p) and depth > 0:
                out += scan(p, depth - 1)
            elif n.lower().endswith(AUDIO):
                out.append(p)
    except OSError:
        pass
    return out[:5000]


class Music(App):
    name = 'music'
    title = 'Music'
    icon = '(~)'
    size = (84, 24)
    min_size = (36, 10)
    single = True

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.player = AudioPlayer(wm, self, on_end=self.on_end, on_error=lambda m: wm.toast(m, 'red'))
        self.spec = Spectrum(24)
        self.list = ListView()
        self.list.on_activate = lambda p: self.play_index(self.list.sel)
        self.current = None
        self.info = {}
        self.shuffle = False
        self.repeat = False
        self.folder = None
        self.levels = [0.0] * 24
        if path and os.path.isfile(path):
            self.load_folder(os.path.dirname(path), select=path)
            self.play_path(path)
        else:
            d = path if path and os.path.isdir(path) else (wm.cfg.get('music_dir') or os.path.join(HOME, 'Music'))
            self.load_folder(d if os.path.isdir(d) else HOME)
        self.every(1 / 15, self.tick)

    def proc_info(self):
        return 'playing' if self.player.playing else ''

    def reopen(self, path=None, **kw):
        if path and os.path.isfile(path):
            if os.path.dirname(path) != self.folder:
                self.load_folder(os.path.dirname(path), select=path)
            self.play_path(path)

    def load_folder(self, folder, select=None):
        self.folder = folder
        self.wm.cfg['music_dir'] = folder
        self.wm.cfg.save()

        def done(files):
            self.list.set_items(files)
            if select in files:
                self.list.select(files.index(select))
            self.invalidate()
        self.thread(lambda: scan(folder), done)

    def play_index(self, i):
        if 0 <= i < len(self.list.items):
            self.list.select(i)
            self.play_path(self.list.items[i])

    def play_path(self, path):
        self.current = path
        self.info = {'title': os.path.splitext(os.path.basename(path))[0]}
        self.set_title(self.info['title'])
        if not self.player.play(path):
            self.wm.toast(self.player.error, 'red')

        def done(info):
            if self.current == path:
                self.info = info
                if info.get('title'):
                    self.set_title(info['title'])
                self.invalidate()
        self.thread(lambda: probe(path), done)

    def on_end(self):
        if self.repeat and self.current:
            self.play_path(self.current)
        else:
            self.next()

    def next(self, d=1):
        n = len(self.list.items)
        if not n:
            return
        if self.shuffle:
            self.play_index(random.randrange(n))
            return
        try:
            i = self.list.items.index(self.current)
        except ValueError:
            i = -1 if d > 0 else 0
        self.play_index((i + d) % n)

    def tick(self):
        if self.player.dec and not self.player.paused:
            self.levels = self.player.levels(self.spec)
            self.invalidate()
        elif any(v > 0.01 for v in self.levels):
            self.levels = [v * 0.8 for v in self.levels]
            self.spec.peak = list(self.levels)
            self.invalidate()

    def on_close(self):
        self.player.stop()
        return True

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        side = w >= 70
        lw = w * 2 // 5 if side else w
        if side:
            self.draw_list(cv, 0, 0, lw - 1, h, focused)
            px, pw = lw + 1, w - lw - 1
            ph = h
        else:
            ph = min(h, 11)
            px, pw = 0, w
            self.draw_list(cv, 0, ph + 1, w, h - ph - 1, focused)
        self.zone_off = (px, 0)
        self.draw_player(cv.sub(px, 0, pw, ph), focused)
        self.zone_off = (0, 0)

    def draw_list(self, cv, x, y, w, h, focused):
        th = self.th
        if h < 3:
            return
        folder = self.folder or ''
        if len(folder) + 8 > w - 9:
            folder = '...' + folder[-(max(4, w - 9 - 11)):]
        cv.put(x, y, fit('FOLDER ' + folder, w - 9), th.c('dim', 'win'))
        button(self, cv, x + w - 8, y, 'Open', 'folder')

        def row(cv2, rx, ry, rw, p, idx, sel):
            playing = p == self.current
            if sel and focused:
                a = th.c('on_accent', 'accent')
            elif playing:
                a = th.c('accent', 'raised')
            elif sel:
                a = th.c('bright', 'sel')
            else:
                a = th.c('text', 'win')
            cv2.fill(rx, ry, rw, 1, a)
            mark = ('>' if self.player.playing else '=') if playing else ' '
            cv2.put(rx, ry, mark, a)
            name = os.path.splitext(os.path.relpath(p, self.folder))[0]
            cv2.put(rx + 2, ry, fit(name, rw - 3), a)
        if not self.list.items:
            cv.put(x, y + 2, 'No audio files here', th.c('dim', 'win'))
        self.list.draw(cv, x, y + 2, w, h - 2, row, th)
        self.zone(x, y + 2, w, h - 2, 'list')
        self.list_y = y + 2

    def draw_player(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.fill(0, 0, w, h, th.c('text', 'raised'))
        sel = self.list.current()
        title = self.info.get('title') or (os.path.splitext(os.path.basename(sel))[0] if sel else 'Nothing playing')
        artist = ' - '.join(x for x in (self.info.get('artist'), self.info.get('album')) if x)
        if not artist:
            artist = 'Enter plays the selected track' if sel and not self.player.dec else ''
        cv.put(2, 1, fit(title, w - 4), th.c('bright', 'raised', 1))
        cv.put(2, 2, fit(artist, w - 4), th.c('dim', 'raised'))
        # visualiser
        vh = h - 9
        if vh >= 3 and not self.player.dec:
            for i in range(vh - 1):
                cv.put(2, 3 + i, ('.' * (w - 4)) if i % 3 == 2 else '', th.c('sel', 'raised'))
        if vh >= 2:
            n = len(self.levels)
            bw = max(1, (w - 4) // n)
            used = bw * n
            vx = 2 + (w - 4 - used) // 2
            for i, lv in enumerate(self.levels):
                bar = lv * vh
                full = int(bar)
                for r in range(vh):
                    y = 3 + vh - r
                    if r < full:
                        a = th.c('on_accent', 'accent' if r < vh * 0.7 else 'accent2')
                        cv.fill(vx + i * bw, y, max(1, bw - 1), 1, a)
                    elif r == full and bar - full > 0.3:
                        cv.put(vx + i * bw, y, '_' * max(1, bw - 1), th.c('accent', 'raised'))
        # progress
        pos = self.player.position
        dur = self.info.get('duration') or 0
        y = h - 5
        t0, t1 = fmt_t(pos), fmt_t(dur) if dur else '--:--'
        cv.put(2, y, t0, th.c('text', 'raised'))
        cv.put(w - 2 - len(t1), y, t1, th.c('dim', 'raised'))
        bx = 3 + len(t0)
        bw = w - bx - len(t1) - 3
        hbar(cv, bx, y, bw, pos / dur if dur else 0, th, bg='field')
        self.zone(bx, y, bw, 1, ('seek', bx + self.zone_off[0], bw))
        # transport
        y = h - 3
        btns = [('|<', 'prev'), ('<<', 'rew'), ('||' if self.player.playing else '>', 'play'), ('>>', 'ffw'), ('>|', 'next')]
        total = sum(len(b[0]) + 3 for b in btns)
        x = max(1, (w - total) // 2)
        for label, key in btns:
            x += button(self, cv, x, y, label, key, primary=key == 'play') + 1
        # volume / modes
        y = h - 1
        cv.put(2, y, 'vol', th.c('dim', 'raised'))
        hbar(cv, 6, y, 10, self.player.volume / 1.5, th, fg='accent2', bg='field')
        self.zone(6, y, 10, 1, ('vol', 6 + self.zone_off[0], 10))
        x = w - 2
        for label, key, on in (('repeat', 'repeat', self.repeat), ('shuffle', 'shuffle', self.shuffle)):
            x -= len(label) + 2
            a = th.c('on_accent', 'accent') if on or self.hover == key else th.c('dim', 'raised')
            cv.put(x, y, ' %s ' % label, a)
            self.zone(x, y, len(label) + 2, 1, key)
            x -= 1

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        p = self.player
        if key == 'play':
            if not p.dec and not p.path:
                self.play_index(self.list.sel)
            else:
                p.toggle()
        elif key == 'next':
            self.next(1)
        elif key == 'prev':
            if p.position > 3:
                p.seek(0)
            else:
                self.next(-1)
        elif key == 'rew':
            p.seek(max(0, p.position - 10))
        elif key == 'ffw':
            p.seek(p.position + 10)
        elif key == 'shuffle':
            self.shuffle = not self.shuffle
        elif key == 'repeat':
            self.repeat = not self.repeat
        elif key == 'folder':
            def ok(d):
                self.load_folder(d)
            self.wm.pick_file(ok, 'open', start=self.folder, title='Choose music folder', parent=self.win, dirs=True)
        elif isinstance(key, tuple) and key[0] == 'seek':
            dur = self.info.get('duration') or 0
            if dur:
                self.player.seek(dur * (ev.x - key[1]) / max(1, key[2] - 1))
        elif isinstance(key, tuple) and key[0] == 'vol':
            self.player.volume = max(0.0, min(1.5, 1.5 * (ev.x - key[1]) / (key[2] - 1)))

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.list.sb_grab is not None:
            self.list.mouse(ev, 0)
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k == 'list'):
            if self.list.mouse(ev, ev.y - self.list_y):
                self.invalidate()
                return True
        if ev.kind == 'drag' and isinstance(self.pressed, tuple) and self.pressed[0] == 'vol':
            self.on_click(self.pressed, ev)
            self.invalidate()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        p = self.player
        if k in (' ', 'k'):
            self.on_click('play', None)
        elif k in ('n', '>'):
            self.next(1)
        elif k in ('p', '<'):
            self.next(-1)
        elif k == 'right':
            p.seek(p.position + 5)
        elif k == 'left':
            p.seek(max(0, p.position - 5))
        elif k in ('+', '='):
            p.volume = min(1.5, p.volume + 0.1)
        elif k == '-':
            p.volume = max(0.0, p.volume - 0.1)
        elif k == 's':
            self.shuffle = not self.shuffle
        elif k == 'r':
            self.repeat = not self.repeat
        elif self.list.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True


APP = Music
