"""YouTube: search (yt-dlp), recommendations, thumbnails and playback.

yt-dlp resolves searches and downloads a 480p-max MP4 into the cache; the existing video
app does the actual playback. Personal recommendations use the account cookies of a
browser or an exported Netscape cookies.txt - no API key, no password.
"""
import json
import os
import re
import subprocess
import threading
import time

from .. import imaging, web
from ..app import App
from ..util import CACHE_DIR, CONFIG_DIR, ensure_dirs, fit, sym, which
from ..widgets import ListView, TextField, button, hbar

URL_RE = re.compile(r'^(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/', re.I)
SHORTS_RE = re.compile(r'#shorts?\b', re.I)
BROWSERS = ('firefox', 'brave', 'chromium', 'chrome')
SIGNIN_CHOICES = ['off'] + list(BROWSERS) + ['cookies.txt']
DEFAULT_COOKIES = os.path.join(CONFIG_DIR, 'youtube-cookies.txt')
REC_LIMIT = 25
REC_TTL = 900             # seconds a cached recommendation list is shown before refreshing


def auth_args(cfg):
    """yt-dlp arguments for the configured sign-in; returns (args, error)."""
    mode = str(cfg.get('youtube_signin') or 'off')
    if mode in ('', 'off'):
        return [], ''
    if mode == 'cookies.txt':
        path = os.path.expanduser(str(cfg.get('youtube_cookies') or DEFAULT_COOKIES))
        if not os.path.isfile(path):
            return [], 'Cookies file not found: %s' % path
        return ['--cookies', path], ''
    if mode in BROWSERS:
        return ['--cookies-from-browser', mode], ''
    return [], 'Unknown sign-in mode: %s' % mode


def parse_entries(data):
    raw = data.get('entries') if data.get('entries') is not None else [data]
    out = []
    for e in raw or []:
        vid = e.get('id')
        if not vid:
            continue
        url = e.get('webpage_url') or e.get('url') or ''
        if not url.startswith('http'):
            url = 'https://www.youtube.com/watch?v=' + vid
        out.append({
            'id': vid,
            'title': e.get('title') or vid,
            'duration': e.get('duration'),
            'uploader': e.get('uploader') or e.get('channel') or '',
            'views': e.get('view_count'),
            'url': url,
        })
    return out


def recommendations(data, limit=REC_LIMIT):
    """Normal videos only: no Shorts, no mixes/playlists."""
    out = []
    for e in parse_entries(data):
        url = e['url']
        dur = e.get('duration')
        if '/shorts/' in url or '/playlist' in url:
            continue
        if '/watch' not in url and 'youtu.be/' not in url:
            continue
        if SHORTS_RE.search(e['title']):
            continue
        if dur is not None and dur <= 60:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _yt_error(p):
    text = (p.stderr or p.stdout or '').lower()
    if 'could not be decrypted' in text or 'cannot decrypt' in text:
        return ('Browser cookies are encrypted and the keyring is locked - '
                'use Firefox or a cookies.txt export.')
    lines = (p.stderr or p.stdout or '').strip().splitlines()
    return lines[-1][:160] if lines else 'yt-dlp failed'


def _fetch_with(args, limit):
    cmd = ['yt-dlp', '--flat-playlist', '--playlist-end', str(limit)] + args + [
        '-J', 'https://www.youtube.com/feed/recommended']
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError(_yt_error(p))
    try:
        data = json.loads(p.stdout or '{}')
    except ValueError:
        raise RuntimeError('yt-dlp returned no usable JSON')
    out = recommendations(data, limit)
    if not out:
        raise RuntimeError('YouTube returned no recommendations - is the sign-in valid?')
    return out


def fetch_recommendations(cfg, limit=REC_LIMIT):
    if not which('yt-dlp'):
        raise RuntimeError('yt-dlp is not installed (apt install yt-dlp)')
    args, err = auth_args(cfg)
    if err:
        raise RuntimeError(err)
    if not args:
        raise RuntimeError('Not signed in - Settings > YouTube')
    try:
        return _fetch_with(args, limit)
    except RuntimeError:
        # a browser cookie jar can be unreadable when the keyring is locked; a
        # cookies.txt exported from the same browser is the fallback
        if args[0] != '--cookies' and os.path.isfile(DEFAULT_COOKIES):
            try:
                return _fetch_with(['--cookies', DEFAULT_COOKIES], limit)
            except RuntimeError:
                pass
        raise


def fmt_dur(s):
    try:
        s = int(s or 0)
    except (TypeError, ValueError):
        return ''
    if s <= 0:
        return ''
    if s >= 3600:
        return '%d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60)
    return '%d:%02d' % (s // 60, s % 60)


def fmt_views(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ''
    if n <= 0:                # the home feed no longer reports view counts
        return ''
    if n >= 1_000_000_000:
        return '%.1fB views' % (n / 1e9)
    if n >= 1_000_000:
        return '%.1fM views' % (n / 1e6)
    if n >= 1000:
        return '%.1fk views' % (n / 1e3)
    return '%d views' % n


class Youtube(App):
    name = 'youtube'
    preserve_colors = True
    title = 'YouTube'
    icon = '[Y]'
    size = (98, 30)
    min_size = (48, 14)

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.search = TextField(placeholder='Search YouTube or paste a link', on_enter=self.start_search)
        self.search_focus = True
        self.list = ListView()
        self.list.on_activate = self.play_selected
        self.entries = []
        self.status = ''
        self.err = ''
        self.rec_error = ''
        self.view = 'idle'            # idle | search | recs
        self.loading_recs = False
        self.pct = 0
        self.downloading = False
        self.proc = None
        self.cache = os.path.join(CACHE_DIR, 'youtube')
        self.rec_cache = os.path.join(self.cache, 'recommendations.json')
        self.thumb = None
        self.thumb_id = None
        self.thumb_key = None
        self.thumb_cells = None
        self.thumb_loading = False
        ensure_dirs()
        if str(wm.cfg.get('youtube_signin') or 'off') != 'off':
            self.load_recommendations()

    # ------------------------------------------------------------ search
    def start_search(self, q):
        q = (q or '').strip()
        if not q:
            return
        self.search_focus = False
        self.view = 'search'
        self.rec_error = ''
        self.err = ''
        self.status = 'Searching...'
        self.invalidate()

        def work():
            if not which('yt-dlp'):
                raise RuntimeError('yt-dlp is not installed (apt install yt-dlp)')
            cmd = ['yt-dlp', '--no-warnings', '-J']
            if URL_RE.match(q):
                cmd += ['--no-playlist', q]
            else:
                cmd += ['--flat-playlist', 'ytsearch10:' + q]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if p.returncode != 0:
                line = (p.stderr or p.stdout).strip().splitlines()
                raise RuntimeError(line[-1][:160] if line else 'yt-dlp failed')
            data = json.loads(p.stdout or '{}')
            return parse_entries(data)

        def done(entries):
            self.entries = entries
            self.list.set_items(entries)
            self.status = '%d results' % len(entries) if entries else 'No results'
            if entries:
                self.load_thumb(entries[0])
            self.invalidate()

        def fail(e):
            self.err = str(e)
            self.status = ''
            self.invalidate()
        self.thread(work, done, fail)

    # ------------------------------------------------------ recommendations
    def load_recommendations(self, force=False):
        self.search_focus = False
        self.view = 'recs'
        self.err = ''
        args, err = auth_args(self.wm.cfg)
        if err or not args:
            self.rec_error = err or 'Not signed in - Settings > YouTube'
            self.invalidate()
            return
        cached = self._read_rec_cache()
        if cached and not force:
            self._show_recommendations(cached, cached=True)
        if self.loading_recs:
            return
        self.loading_recs = True
        self.rec_error = ''
        self.status = 'Loading recommendations...'
        self.invalidate()

        def work():
            return fetch_recommendations(self.wm.cfg)

        def done(entries):
            self.loading_recs = False
            self._show_recommendations(entries)
            self._write_rec_cache(entries)

        def fail(e):
            self.loading_recs = False
            self.rec_error = str(e)
            self.status = ''
            self.invalidate()
        self.thread(work, done, fail)

    def _show_recommendations(self, entries, cached=False):
        self.view = 'recs'
        self.entries = entries
        self.list.set_items(entries)
        self.status = '%d recommendations%s' % (len(entries), ' (cached)' if cached else '')
        self.rec_error = ''
        if entries:
            self.load_thumb(entries[0])
        self.invalidate()

    def _read_rec_cache(self):
        try:
            with open(self.rec_cache, encoding='utf-8') as f:
                data = json.load(f)
            if time.time() - float(data.get('at') or 0) < REC_TTL:
                return [e for e in data.get('entries') or [] if e.get('id')]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def _write_rec_cache(self, entries):
        try:
            os.makedirs(self.cache, exist_ok=True)
            tmp = self.rec_cache + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'at': time.time(), 'entries': entries}, f)
            os.replace(tmp, self.rec_cache)
        except OSError:
            pass

    def current(self):
        return self.list.current()

    # ------------------------------------------------------------ thumbnails
    def load_thumb(self, e):
        if not e or self.thumb_id == e['id']:
            return
        self.thumb_id = e['id']
        self.thumb = None
        self.thumb_cells = None
        self.thumb_key = None
        self.thumb_loading = True

        def work():
            url = 'https://i.ytimg.com/vi/%s/mqdefault.jpg' % e['id']
            _, ct, data = web.fetch(url, max_bytes=2_000_000, headers={'Accept': 'image/*'})
            return imaging.from_bytes(data)

        def done(img):
            if self.thumb_id != e['id']:
                return
            self.thumb = img
            self.thumb_loading = False
            self.invalidate()

        def fail(err):
            self.thumb_loading = False
            self.invalidate()
        self.thread(work, done, fail)

    def thumb_area(self, pw, ph):
        if self.thumb is None:
            return None
        key = (self.thumb_id, pw, ph, self.wm.cfg.get('image_mode'), self.wm.cfg.get('dither'),
               self.wm.cfg.get('ascii_charset'), tuple(self.th.rgb))
        if key != self.thumb_key or not self.thumb_cells:
            self.thumb_key = key
            self.thumb_cells = imaging.render_fit(self.wm, self.thumb, pw, ph)
        return self.thumb_cells

    # ------------------------------------------------------------ download/play
    def play_selected(self):
        e = self.current()
        if e:
            self.play(e)

    def play(self, e):
        if self.downloading:
            return
        path = os.path.join(self.cache, e['id'] + '.mp4')
        if os.path.isfile(path) and os.path.getsize(path) > 20000:
            self.wm.open('video', path=path)
            return
        if not which('yt-dlp'):
            self.err = 'yt-dlp is not installed (apt install yt-dlp)'
            return
        ensure_dirs()
        os.makedirs(self.cache, exist_ok=True)
        tmp = path
        cmd = ['yt-dlp', '--no-warnings', '--newline', '--progress',
               '-f', 'bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480][ext=mp4]/b[height<=480]/b',
               '--merge-output-format', 'mp4', '-o', tmp, e['url']]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1)
        except OSError as ex:
            self.err = str(ex)
            return
        self.downloading = True
        self.pct = 0
        self.status = 'Downloading...'
        self.err = ''
        self.invalidate()

        def pump():
            last = ''
            for line in self.proc.stdout:
                last = line.strip()
                m = re.search(r'\[download\]\s+([\d.]+)%', line)
                if m:
                    pct = float(m.group(1))
                    self.wm.loop.call_soon_threadsafe(lambda pct=pct, last=last: self.on_progress(pct, last))
            rc = self.proc.wait()
            self.wm.loop.call_soon_threadsafe(lambda: self.on_done(e, path, tmp, rc, last))

        threading.Thread(target=pump, daemon=True).start()

    def on_progress(self, pct, line):
        if self.win.closed:
            return
        self.pct = pct
        self.status = re.sub(r'^\[download\]\s*', '', line)[:110]
        self.invalidate()

    def on_done(self, e, path, tmp, rc, last):
        self.downloading = False
        self.proc = None
        if self.win.closed:
            return
        if rc == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 20000:
            try:
                os.replace(tmp, path)
            except OSError as ex:
                self.err = str(ex)
                self.invalidate()
                return
            self.status = 'Ready'
            self.wm.open('video', path=path)
        else:
            self.err = (last or 'download failed')[:160]
            self.status = ''
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.invalidate()

    def cancel(self):
        if self.proc and self.downloading:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.status = 'Cancelled'
            self.downloading = False

    def on_close(self):
        self.cancel()
        return True

    # ------------------------------------------------------------ actions
    def copy_url(self):
        e = self.current()
        if e:
            self.wm.set_clipboard(e['url'])
            self.wm.toast('Copied link')

    def open_browser(self):
        e = self.current()
        self.wm.open('browser', url=(e['url'] if e else 'https://www.youtube.com'))

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.zone_off = (0, 0)
        # search bar + personal recommendations
        bw = 9
        field_w = max(12, w - bw * 2 - 1)
        self.search.draw(cv, 0, 0, field_w, th, focused and self.search_focus)
        self.zone(0, 0, field_w, 1, 'search')
        button(self, cv, w - bw * 2 - 1, 0, 'For you', 'recs', active=self.view == 'recs')
        button(self, cv, w - bw, 0, 'Search', 'go', primary=True)
        side = w >= 82
        lw = w - 36 if side else w
        list_h = h - 3
        label = 'recommendations' if self.view == 'recs' else 'results'
        cv.put(1, 1, '%d %s%s' % (len(self.entries), label,
                                  '  ·  yt-dlp' if self.entries else ''), th.c('dim', 'win'))
        if not self.entries:
            from ..util import wrap_text
            cy = max(3, h // 2 - 2)
            if self.view == 'recs':
                cv.put(2, cy, 'Personal recommendations', th.c('bright', 'win'))
                msg = (self.rec_error or
                       ('Loading...' if self.loading_recs else 'Press v to load them again.'))
                for i, ln in enumerate(wrap_text(msg, max(8, w - 4))[:3]):
                    cv.put(2, cy + 1 + i, ln,
                           th.c('red' if self.rec_error else 'dim', 'win'))
                cv.put(2, cy + 5, 'Settings > YouTube: sign in or pick a cookies file (F2).',
                       th.c('dim', 'win'))
            else:
                cv.put(2, cy, 'Search YouTube or paste a video link.', th.c('bright', 'win'))
                cv.put(2, cy + 1, 'Results play in the video player; 480p, cached locally.',
                       th.c('dim', 'win'))
                line = cy + 3
                if not which('yt-dlp'):
                    cv.put(2, line, 'yt-dlp is not installed: apt install yt-dlp',
                           th.c('red', 'win'))
                    line += 1
                if not auth_args(self.wm.cfg)[0]:
                    cv.put(2, line, 'Personal recommendations: Settings > YouTube (press F2).',
                           th.c('dim', 'win'))
            self.list.draw(cv, 0, 3, w, max(1, h - 5), self._row, th)
            return
        self.list.draw(cv, 0, 2, lw, list_h, self._row, th)
        self.zone(0, 2, lw, list_h, 'list')
        e = self.current()
        if side and e:
            px = lw + 1
            pw = w - px
            if self.thumb_cells is None:
                self.thumb_area(pw - 1, 14)
            if self.thumb_cells:
                cells, cw, chh = self.thumb_cells
                cv.sub(px, 2, pw, 15).blit_cells((pw - cw) // 2, 0, cells)
            elif self.thumb_loading:
                cv.put(px, 2, 'loading thumbnail...', th.c('dim', 'win'))
            y = 16 if self.thumb_cells else 2
            from ..util import wrap_text
            for ln in wrap_text(e['title'], pw)[:3]:
                cv.put(px, y, ln, th.c('bright', 'win', 1))
                y += 1
            meta = '  '.join(x for x in (e.get('uploader'), fmt_dur(e.get('duration')),
                                         fmt_views(e.get('views'))) if x)
            for ln in wrap_text(meta, pw)[:2]:
                cv.put(px, y, ln, th.c('dim', 'win'))
                y += 1
            bx = px
            bx += button(self, cv, bx, y, 'Play', 'play', primary=True) + 1
            bx += button(self, cv, bx, y, 'Browser', 'browser') + 1
            button(self, cv, bx, y, 'Copy link', 'copy')
            self.zone(px, 2, pw, max(1, h - 4), 'preview')
        # bottom status
        y = h - 1
        if self.downloading:
            hbar(cv, 0, y - 1, w, self.pct / 100.0, th)
            cv.put(0, y, fit('%d%%  %s' % (self.pct, self.status), w - 12), th.c('text', 'win'))
            button(self, cv, w - 10, y, 'Cancel', 'cancel')
        else:
            hint = 'Enter plays  ·  F3 for you  ·  / search  ·  b browser  ·  c link'
            if self.err:
                cv.put(0, y, fit(self.err, w - 2), th.c('red', 'win'))
            elif self.rec_error and self.view == 'recs' and not self.entries:
                cv.put(0, y, fit(self.rec_error, w - 2), th.c('red', 'win'))
            else:
                cv.put(0, y, fit(self.status or hint, w - 2), th.c('dim', 'win'))

    def _row(self, cv, rx, ry, rw, e, idx, sel):
        th = self.th
        a = th.c('on_accent', 'accent') if sel else th.c('text', 'win')
        if sel:
            cv.fill(rx, ry, rw, 1, a)
        icon = sym('youtube', '>')
        cv.put(rx + 1, ry, icon, a if sel else th.c('red', 'win'))
        dur = fmt_dur(e.get('duration'))
        title_w = rw - 4 - (len(dur) + 2 if dur else 0)
        cv.put(rx + 3, ry, fit(e['title'], max(8, title_w)), a)
        if dur:
            cv.put(rx + rw - len(dur) - 1, ry, dur, a if sel else th.c('dim', 'win'))

    # ------------------------------------------------------------ input
    def ensure_thumb(self):
        e = self.current()
        if e:
            self.load_thumb(e)

    def on_click(self, key, ev):
        if key == 'go':
            self.start_search(self.search.text)
        elif key == 'recs':
            self.load_recommendations(force=True)
        elif key == 'search':
            self.search_focus = True
            self.search.click(ev.x)
        elif key == 'list':
            self.search_focus = False
            self.list.mouse(ev, ev.y - 2)
            self.ensure_thumb()
        elif key == 'play':
            self.play_selected()
        elif key == 'browser':
            self.open_browser()
        elif key == 'copy':
            self.copy_url()
        elif key == 'cancel':
            self.cancel()
        elif key == 'preview':
            if ev.clicks >= 2:
                self.play_selected()
        self.invalidate()

    def on_mouse(self, ev):
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k is not None):
            if k == 'list' and ev.kind == 'down':
                self.search_focus = False
            if self.list.mouse(ev, ev.y - 2):
                self.ensure_thumb()
                self.invalidate()
                return True
            return True
        if ev.kind == 'up' and self.list.sb_grab is not None:
            self.list.mouse(ev, 0)
            self.invalidate()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k in ('f2', 'ctrl+s'):
            self.wm.open('settings', section='youtube')
            self.invalidate()
            return True
        if k == 'f3':
            self.load_recommendations(force=True)
            self.invalidate()
            return True
        if self.search_focus:
            if k == 'esc':
                self.search_focus = False
            elif k in ('down', 'tab'):
                self.search_focus = False
            else:
                self.search.key(ev)
            self.invalidate()
            return True
        if k in ('/', 'ctrl+l'):
            self.search_focus = True
        elif k == 'v':
            self.load_recommendations(force=True)
        elif k == 's':
            self.wm.open('settings', section='youtube')
        elif k == 'enter':
            self.play_selected()
        elif k == 'b':
            self.open_browser()
        elif k == 'c':
            self.copy_url()
        elif k == 'ctrl+r' or k == 'f5':
            self.start_search(self.search.text)
        elif k == 'esc' and self.downloading:
            self.cancel()
        elif self.list.key(ev):
            self.ensure_thumb()
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.search_focus:
            self.search.insert(text.strip())
            self.invalidate()


APP = Youtube
