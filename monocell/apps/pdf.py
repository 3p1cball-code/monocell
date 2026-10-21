"""PDF reader: pages rasterised with poppler (pdftocairo/pdftoppm) and rebuilt as text cells.

Two views: 'page' (dithered/ASCII rendering of the page) and 'text' (extracted text,
selectable-ish reading view). Pages are rendered in a worker thread, cells are cached.
"""
import os
import re
import subprocess

from .. import imaging
from ..app import App
from ..textarea import TextArea
from ..util import fit, shorten_path, which
from ..widgets import button

MODES = ['blend', 'pixel', 'grey', 'mono']      # raster styles for the page view
VIEWS = [('page', 'Page'), ('text', 'Text'), ('ascii', 'ASCII')]
INVERT = bytes(255 - i for i in range(256))
DITHERS = ['bayer4', 'bayer8', 'fs', 'none']


def png_page(path, page, max_px=1700):
    """Render one page to PNG bytes (poppler). page is 0-based."""
    if which('pdftocairo'):
        cmd = ['pdftocairo', '-png', '-singlefile', '-r', '150', '-scale-to', str(max_px),
               '-f', str(page + 1), '-l', str(page + 1), path, '-']
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        if p.returncode == 0 and p.stdout[:8] == b'\x89PNG\r\n\x1a\n':
            return p.stdout
    if which('pdftoppm'):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'page')
            cmd = ['pdftoppm', '-png', '-singlefile', '-r', '150', '-scale-to', str(max_px),
                   '-f', str(page + 1), '-l', str(page + 1), path, out]
            p = subprocess.run(cmd, capture_output=True, timeout=60)
            f = out + '.png'
            if p.returncode == 0 and os.path.exists(f):
                with open(f, 'rb') as fh:
                    return fh.read()
    raise RuntimeError('pdftocairo/pdftoppm failed (poppler-utils installed?)')


def pdf_info(path):
    info = {'pages': 0, 'title': '', 'page_w': 595.0, 'page_h': 842.0}
    if which('pdfinfo'):
        try:
            out = subprocess.run(['pdfinfo', path], capture_output=True, text=True, timeout=20).stdout
        except (subprocess.SubprocessError, OSError):
            out = ''
        for line in out.splitlines():
            k, _, v = line.partition(':')
            k, v = k.strip().lower(), v.strip()
            if k == 'pages':
                try:
                    info['pages'] = int(v)
                except ValueError:
                    pass
            elif k == 'title':
                info['title'] = v
            elif k == 'page size':
                m = re.match(r'([\d.]+) x ([\d.]+)', v)
                if m:
                    info['page_w'], info['page_h'] = float(m.group(1)), float(m.group(2))
    if not info['pages'] and which('pdftoppm'):
        try:
            out = subprocess.run(['pdfinfo', path], capture_output=True, text=True, timeout=20).stdout
        except (subprocess.SubprocessError, OSError):
            pass
    return info


def page_text(path, page):
    if not which('pdftotext'):
        return ''
    try:
        p = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', '-f', str(page + 1), '-l', str(page + 1),
                            path, '-'], capture_output=True, timeout=30)
        return p.stdout.decode('utf-8', 'replace')
    except (subprocess.SubprocessError, OSError):
        return ''


class PDF(App):
    name = 'pdf'
    preserve_colors = True
    title = 'PDF'
    icon = '[P]'
    size = (92, 32)
    min_size = (30, 8)

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.path = None
        self.info = {'pages': 0, 'title': '', 'page_w': 595.0, 'page_h': 842.0}
        self.page = 0
        self.mode = wm.cfg.get('image_mode', 'blend')
        if self.mode not in MODES:
            self.mode = 'blend'
        self.dither = wm.cfg.get('dither', 'bayer4')
        self.fit_mode = 'page'
        self.zoom = 1.0
        self.scroll = 0
        self.hscroll = 0
        self.view = 'page'
        self.textarea = None
        self.text_page = None
        self.img_cache = {}
        self.cells = None
        self.cells_key = None
        self.loading = False
        self.gen = 0
        if path:
            self.open(path)

    # ------------------------------------------------------------ loading
    def open(self, path):
        if not which('pdftocairo') and not which('pdftoppm'):
            self.wm.message('PDF', 'Reading PDFs needs poppler-utils (pdftocairo/pdftoppm).', parent=self.win)
            return
        self.path = os.path.abspath(path)
        self.page = 0
        self.img_cache = {}
        self.cells = None
        self.textarea = None
        self.text_page = None
        self.scroll = self.hscroll = 0
        self.zoom = 1.0
        self.set_title(os.path.basename(path))
        self.loading = True
        self.invalidate()

        def work():
            return pdf_info(self.path)

        def done(info):
            self.info = info
            self.loading = False
            if info.get('title') and len(info['title']) > 3:
                self.set_title(info['title'][:60])
            if info['pages'] <= 0:
                self.wm.toast('Could not read page count', 'red')
            if self.view == 'text':
                self.load_text()
            self.invalidate()
        self.thread(work, done)

    def load_text(self):
        if not self.path:
            return
        gen = self.gen = self.gen + 1
        page = self.page
        self.text_page = None
        self.invalidate()

        def work():
            return page_text(self.path, page)

        def done(txt):
            if gen != self.gen:
                return
            self.text_page = page
            self.textarea = TextArea(txt or '(no extractable text on this page)', wrap=True)
            self.textarea.read_only = True
            self.invalidate()
        self.thread(work, done)

    def page_image(self, page):
        img = self.img_cache.get(page)
        if img is not None:
            return img

        def work():
            return imaging.from_bytes(png_page(self.path, page))

        def done(img):
            if self.path is None:
                return
            self.img_cache[page] = img
            if len(self.img_cache) > 4:
                for k in list(self.img_cache)[:-4]:
                    self.img_cache.pop(k, None)
            if page == self.page:
                self.cells_key = None
                self.invalidate()
        self.thread(work, done, lambda e: self.wm.toast('Page %d: %s' % (page + 1, e), 'red'))
        return None

    # ------------------------------------------------------------ geometry
    def area(self):
        return self.cw, self.ch - 2 if self.ch > 4 else self.ch

    def target_cells(self):
        aw, ah = self.area()
        pw, ph = self.info.get('page_w', 595), self.info.get('page_h', 842)
        aspect = imaging.cell_aspect(self.wm)
        if self.fit_mode == 'page':
            cw = min(aw, int(ah * aspect * pw / max(1.0, ph)))
            chh = int(cw * ph / (aspect * pw)) if cw else 0
        else:
            cw = aw
            chh = int(cw * ph / (aspect * pw)) if cw else 0
        cw = max(8, int(cw * self.zoom))
        chh = max(8, int(chh * self.zoom))
        # keep one page render affordable (pure-python dithering)
        limit = 90000
        while cw * chh > limit:
            cw = int(cw * 0.9)
            chh = int(chh * 0.9)
        return max(8, cw), max(8, chh)

    def ensure_cells(self):
        if not self.path or self.info.get('pages', 0) <= 0:
            return
        img = self.img_cache.get(self.page)
        if img is None:
            key = ('load', self.page)
            if self.cells_key != key:
                self.cells_key = key
                self.page_image(self.page)
            return
        cw, chh = self.target_cells()
        mode = 'ascii' if self.view == 'ascii' else self.mode
        key = (self.page, cw, chh, mode, self.dither, self.wm.cfg.get('ascii_charset'),
               tuple(self.th.rgb))
        if key == self.cells_key and self.cells is not None:
            return
        self.cells_key = key
        rgb = img.scaled(cw, chh)
        if mode == 'ascii':
            # ink becomes characters on the dark background (paper would be a wall of '$')
            rgb = bytes(rgb).translate(INVERT)
        self.cells = imaging.render(self.wm, rgb, cw, chh, mode, self.dither)

    # ------------------------------------------------------------ drawing
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        x = 0
        fit_label = 'Fit W' if self.fit_mode == 'width' else 'Fit P'
        tools = [('Open', 'open'), ('<', 'prev'), ('>', 'next')]
        tools += [(label, ('view', v)) for v, label in VIEWS]
        tools += [('Go', 'goto')]
        if self.view != 'text':
            tools += [('-', 'zout'), ('+', 'zin'), (fit_label, 'fit')]
        if self.view == 'page':
            tools += [(self.mode, 'mode')]
        if self.view != 'text':
            tools += [(self.dither, 'dither')]
        for label, key in tools:
            if x + len(label) + 3 > w:
                break
            if key == 'prev':
                x += button(self, cv, x, 0, label, key, enabled=self.page > 0) + 1
            elif key == 'next':
                x += button(self, cv, x, 0, label, key, enabled=self.page + 1 < self.info.get('pages', 0)) + 1
            elif isinstance(key, tuple):
                x += button(self, cv, x, 0, label, key, active=self.view == key[1]) + 1
                if key[1] == 'ascii':
                    x += 1           # small gap after the view switch
            else:
                x += button(self, cv, x, 0, label, key) + 1
        # content
        if not self.path:
            cy = max(1, h // 2 - 3)
            cv.put(2, cy, 'No PDF open', th.c('bright', 'win'))
            cv.put(2, cy + 1, 'Pages are rasterised with poppler and rebuilt from text cells.',
                   th.c('dim', 'win'))
            button(self, cv, 2, cy + 3, 'Open PDF', 'open', primary=True)
            return
        pages = self.info.get('pages', 0)
        if self.view == 'text':
            self.draw_text(cv)
        else:
            self.ensure_cells()
            if self.cells:
                cw, chh = self.target_cells()
                aw, ah = self.area()
                max_y = max(0, chh - ah)
                max_x = max(0, cw - aw)
                self.scroll = max(0, min(self.scroll, max_y))
                self.hscroll = max(0, min(self.hscroll, max_x))
                ox = (aw - cw) // 2 - self.hscroll if cw > aw else (aw - cw) // 2
                oy = 2 - self.scroll
                cv.sub(0, 2, aw, ah).blit_cells(ox, oy - 2, self.cells)
                self.zone(0, 2, aw, ah, 'page')
                if cw > aw or chh > ah:
                    from ..widgets import sb_draw, sb_geom
                    g = sb_geom(w - 1, 2, max(1, ah), chh, ah, self.scroll)
                    sb_draw(cv, g, th)
                    self.sb = g
            elif self.loading:
                cv.put(2, 3, 'Reading PDF...', th.c('dim', 'win'))
            else:
                cv.put(2, 3, 'Rendering page %d...' % (self.page + 1), th.c('dim', 'win'))
        # status
        left = shorten_path(self.path, max(12, w // 2))
        st = 'page %d/%d  %d%%  %s' % (self.page + 1, max(pages, 1), int(self.zoom * 100),
                                       dict(VIEWS)[self.view].lower())
        cv.put(0, h - 1, fit(left, w - len(st) - 2), th.c('dim', 'win'))
        cv.put(w - len(st), h - 1, st, th.c('text', 'win'))

    def draw_text(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        if self.text_page != self.page:
            if self.textarea is None:
                self.load_text()
            cv.put(2, 3, 'Extracting text...', th.c('dim', 'win'))
            return
        if self.textarea is None:
            return
        self.textarea.draw(cv, 1, 2, w - 3, h - 3, th, False)
        self.zone(0, 2, w - 2, h - 3, 'text')

    # ------------------------------------------------------------ actions
    def go_page(self, p):
        pages = self.info.get('pages', 0)
        if not pages:
            return
        p = max(0, min(pages - 1, p))
        if p == self.page:
            return
        self.page = p
        self.scroll = self.hscroll = 0
        self.cells = None
        self.cells_key = None
        self.textarea = None
        self.text_page = None
        if self.view == 'text':
            self.load_text()
        self.invalidate()

    def zoom_by(self, f):
        self.zoom = max(0.5, min(6.0, self.zoom * f))
        self.cells_key = None
        self.scroll = self.hscroll = 0
        self.invalidate()

    def on_click(self, key, ev):
        if key == 'open':
            self.wm.pick_file(self.open, 'open', start=self.path, parent=self.win, title='Open PDF')
        elif key == 'prev':
            self.go_page(self.page - 1)
        elif key == 'next':
            self.go_page(self.page + 1)
        elif key == 'goto':
            def ok(t):
                try:
                    self.go_page(int(t) - 1)
                except ValueError:
                    pass
            self.wm.prompt('Go to page', 'Page (1-%d):' % max(1, self.info.get('pages', 1)),
                           str(self.page + 1), ok, parent=self.win)
        elif key == 'zin':
            self.zoom_by(1.25)
        elif key == 'zout':
            self.zoom_by(0.8)
        elif key == 'fit':
            self.fit_mode = 'page' if self.fit_mode == 'width' else 'width'
            self.zoom = 1.0
            self.cells_key = None
            self.scroll = self.hscroll = 0
        elif key == 'mode':
            self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)] if self.mode in MODES else 'blend'
            self.cells_key = None
        elif key == 'dither':
            self.dither = DITHERS[(DITHERS.index(self.dither) + 1) % len(DITHERS)] if self.dither in DITHERS else 'bayer4'
            self.cells_key = None
        elif isinstance(key, tuple) and key[0] == 'view':
            self.set_view(key[1])
        elif key in ('view', 'text'):
            self.set_view('page' if self.view == 'text' else 'text')

    def set_view(self, view):
        if view == self.view:
            return
        self.view = view
        if view == 'text':
            self.load_text()
        else:
            self.text_page = None
        self.cells = None
        self.cells_key = None
        self.scroll = self.hscroll = 0
        self.invalidate()

    def selection_text(self):
        if self.view == 'text' and self.textarea:
            return self.textarea.selected_text() or ''
        return ''

    def on_mouse(self, ev):
        g = getattr(self, 'sb', None)
        if ev.kind == 'up' and getattr(self, 'sb_grab', None) is not None:
            self.sb_grab = None
            self.invalidate()
            return True
        if g and (g.hit(ev) or (ev.kind == 'drag' and getattr(self, 'sb_grab', None) is not None)):
            aw, ah = self.area()
            if ev.kind == 'down':
                rel = ev.y - g.y
                if g.ty <= rel < g.ty + g.th:
                    self.sb_grab = rel - g.ty
                else:
                    self.sb_grab = max(0, g.th // 2)
                    self.scroll = g.top_at(ev.y, self.sb_grab)
            elif ev.kind == 'drag' and self.sb_grab is not None:
                self.scroll = g.top_at(ev.y, self.sb_grab)
            elif ev.kind == 'up':
                self.sb_grab = None
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if (self.view == 'text' and self.textarea and ev.button == 0
                and (k == 'text' or ev.kind in ('drag', 'up'))
                and ev.kind in ('down', 'drag', 'up', 'wheelup', 'wheeldown')):
            # the text view selects with a plain drag, like the editor
            self.textarea.mouse(ev, ev.x - 1, ev.y - 2)
            if ev.kind == 'up':
                t = self.textarea.selected_text()
                if t:
                    self.wm.set_clipboard(t)
            self.invalidate()
            return True
        if ev.kind in ('wheelup', 'wheeldown', 'drag') and k in ('page', 'text'):
            if self.view == 'text' and self.textarea:
                self.textarea.mouse(ev, ev.x, ev.y - 2)
            else:
                d = -3 if ev.kind == 'wheelup' else 3
                self.scroll += d
                self.invalidate()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if (self.view == 'text' and self.textarea and k in ('up', 'down', 'pgup', 'pgdn', 'home', 'end')):
            if self.textarea.key(ev):
                self.textarea.ensure_visible()
                self.invalidate()
                return True
        if k in ('pgdn', ' ', 'n'):
            self.go_page(self.page + 1)
        elif k in ('pgup', 'p'):
            self.go_page(self.page - 1)
        elif k == 'home':
            self.go_page(0)
        elif k == 'end':
            self.go_page(self.info.get('pages', 1) - 1)
        elif k in ('down', 'j'):
            self.scroll += 2
        elif k in ('up', 'k'):
            self.scroll -= 2
        elif k in ('+', '='):
            self.zoom_by(1.25)
        elif k == '-':
            self.zoom_by(0.8)
        elif k == 'f':
            self.on_click('fit', None)
        elif k == 'm':
            self.on_click('mode', None)
        elif k == 'd':
            self.on_click('dither', None)
        elif k == 't':
            self.set_view('page' if self.view == 'text' else 'text')
        elif k == 'a':
            self.set_view('page' if self.view == 'ascii' else 'ascii')
        elif k == 'v':
            order = [v for v, _ in VIEWS]
            self.set_view(order[(order.index(self.view) + 1) % len(order)])
        elif k in ('ctrl+c', 'ctrl+shift+c') and self.view == 'text' and self.textarea:
            self.wm.copy_text(self.textarea.selected_text() or '')
        elif k == 'ctrl+a' and self.view == 'text' and self.textarea:
            self.textarea.key(ev)
        elif k == 'g':
            self.on_click('goto', None)
        elif k == 'ctrl+o' or k == 'o':
            self.on_click('open', None)
        elif self.view == 'text' and self.textarea:
            if self.textarea.key(ev):
                self.textarea.ensure_visible()
            else:
                return False
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        pass

    def reopen(self, path=None, **kw):
        if path:
            self.open(path)


APP = PDF
