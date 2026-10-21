"""Web browser: lite (urllib) or headless Chromium engine, laid out as clean text."""
import os
import re
import urllib.parse

from .. import imaging, web
from ..app import App
from .. import util
from ..util import clean, fit, wrap_text
from ..widgets import MenuItem, TextField, button, sb_draw, sb_geom

HOME_URL = 'about:home'


class Line:
    __slots__ = ('cells', 'hits')

    def __init__(self):
        self.cells = []    # list of (x, text, attr)
        self.hits = []     # list of (x0, x1, target) target = ('link', href, id) | ('w', widget)


class Layout:
    """Turns the block model into positioned text lines."""

    def __init__(self, app, doc, width):
        self.app = app
        self.th = app.th
        self.doc = doc
        self.width = width
        self.lines = []
        self.focusables = []      # (line index, x0, x1, target)
        self.images = []          # (line index, height, block)
        self.anchors = {}
        self.build()

    def nl(self):
        ln = Line()
        self.lines.append(ln)
        return ln

    def blank(self):
        if self.lines and (self.lines[-1].cells or self.lines[-1].hits):
            self.nl()

    def span_attr(self, sp, base):
        th = self.th
        if sp.get('h'):
            return th.c('accent', 'win', 2)
        if sp.get('c'):
            return th.c('cyan', 'win')
        if sp.get('b'):
            return th.c('bright', 'win', 1)
        return base

    def flow(self, spans, indent, width, base, first_prefix=None, prefix_attr=None):
        """Word-wrap spans into lines."""
        ln = self.nl()
        x = indent
        if first_prefix:
            ln.cells.append((indent - len(first_prefix), first_prefix, prefix_attr or base))
        line_start = True
        for sp in spans:
            if sp.get('w'):
                tok = self.widget_text(sp)
                if x + len(tok) > indent + width and not line_start:
                    ln = self.nl()
                    x = indent
                a = self.widget_attr(sp)
                ln.cells.append((x, tok, a))
                ln.hits.append((x, x + len(tok), ('w', sp)))
                self.focusables.append((len(self.lines) - 1, x, x + len(tok), ('w', sp)))
                x += len(tok) + 1
                line_start = False
                continue
            text = clean(sp.get('x', ''))
            attr = self.span_attr(sp, base)
            href = sp.get('h')
            parts = re.split(r'(\n| )', text)
            for p in parts:
                if p == '\n':
                    ln = self.nl()
                    x = indent
                    line_start = True
                    continue
                if p == ' ':
                    if not line_start and x < indent + width:
                        if href and ln.hits and ln.hits[-1][2][0] == 'link' and ln.hits[-1][2][1] == href and ln.hits[-1][1] == x:
                            ln.cells.append((x, ' ', attr))
                            x0, _, tgt = ln.hits[-1]
                            ln.hits[-1] = (x0, x + 1, tgt)
                        else:
                            x += 1
                            continue
                        x += 1
                    continue
                if not p:
                    continue
                while len(p) > width:
                    if x > indent:
                        ln = self.nl()
                        x = indent
                    self.put_word(ln, x, p[:width], attr, href, sp)
                    p = p[width:]
                    ln = self.nl()
                    x = indent
                if x + len(p) > indent + width and not line_start:
                    # strip trailing space-link extension
                    ln = self.nl()
                    x = indent
                self.put_word(ln, x, p, attr, href, sp)
                x += len(p)
                line_start = False
        return ln

    def put_word(self, ln, x, word, attr, href, sp):
        ln.cells.append((x, word, attr))
        if href:
            tgt = ('link', href, sp.get('id'))
            if ln.hits and ln.hits[-1][2] == tgt and ln.hits[-1][1] >= x - 1:
                x0 = ln.hits[-1][0]
                ln.hits[-1] = (x0, x + len(word), tgt)
                f = self.focusables[-1]
                self.focusables[-1] = (f[0], f[1], x + len(word), f[3])
            else:
                ln.hits.append((x, x + len(word), tgt))
                self.focusables.append((len(self.lines) - 1, x, x + len(word), tgt))

    def widget_text(self, w):
        k = w['w']
        vals = self.app.values
        if k == 'input':
            v = vals.get(w['id'], w.get('val', ''))
            shown = ('*' * len(v)) if w.get('pw') else v
            width = 28 if not w.get('multi') else 40
            if not v:
                shown = w.get('ph', '')
            return '[' + fit(shown, width) + ']'
        if k == 'button':
            return ' %s ' % fit(clean(w.get('x', 'Submit')), 30).strip()
        if k == 'check':
            on = vals.get(w['id'], w.get('on'))
            if w.get('radio'):
                return util.sym('radio_on', '(*)') if on else util.sym('radio_off', '( )')
            return util.sym('box_on', '[x]') if on else util.sym('box_off', '[ ]')
        if k == 'select':
            return '[%s v]' % fit(clean(vals.get(('sel', w['id']), w.get('x', ''))), 18).strip()
        return '?'

    def widget_attr(self, w):
        th = self.th
        if w['w'] == 'input':
            v = self.app.values.get(w['id'], w.get('val', ''))
            return th.c('text' if v else 'dim', 'field')
        if w['w'] == 'button':
            return th.c('bright', 'raised')
        return th.c('accent', 'win')

    def build(self):
        th = self.th
        W = self.width
        text = th.c('text', 'win')
        if getattr(self.app, 'notice', ''):
            self.flow([{'x': '! ', 'b': 1}, {'x': self.app.notice}], 0, W, th.c('yellow', 'win'))
            self.nl()
        blocks = self.doc.get('blocks', [])
        for b in blocks:
            t = b['t']
            if t == 'h':
                lvl = b.get('l', 2)
                self.blank()
                spans = [dict(s, b=1) if not s.get('w') else s for s in b['s']]
                attr = th.c('bright' if lvl == 1 else 'accent', 'win', 1)
                start = len(self.lines)
                self.flow(spans, 0, W, attr)
                for ln in self.lines[start:]:
                    ln.cells = [(x, s, attr if not any(h[0] <= x < h[1] for h in ln.hits) else a) for x, s, a in ln.cells]
                if lvl <= 2:
                    longest = max([sum(len(s) for _, s, _ in ln.cells) for ln in self.lines[start:]] + [1])
                    ln = self.nl()
                    ln.cells.append((0, ('=' if lvl == 1 else '-') * min(W, max(longest, 3) + 2), th.c('sel', 'win')))
                self.nl()
            elif t == 'p':
                if not b['s']:
                    continue
                self.flow(b['s'], 0, W, text)
                self.nl()
            elif t == 'q':
                start = len(self.lines)
                self.flow(b['s'], 2, W - 2, th.c('dim', 'win'))
                for ln in self.lines[start:]:
                    ln.cells.insert(0, (0, '|', th.c('accent', 'win')))
                self.nl()
            elif t == 'li':
                d = max(1, b.get('d', 1))
                ind = min(W // 2, 2 * d + 1)
                bullet = ('%d.' % b['n']) if b.get('n') else (
                    util.sym('bullet', '*') if d == 1 else util.sym('bullet2', '-') if d == 2 else 'o')
                self.flow(b['s'], ind + 1, W - ind - 1, text, bullet.rjust(ind), th.c('accent', 'win'))
            elif t == 'pre':
                self.blank()
                for raw in b.get('x', '').split('\n')[:400]:
                    ln = self.nl()
                    s = clean(raw.expandtabs(4))
                    ln.cells.append((0, ' ' + s[:W - 1].ljust(W - 1), th.c('text', 'field')))
                self.nl()
            elif t == 'hr':
                ln = self.nl()
                ln.cells.append((0, '-' * W, th.c('sel', 'win')))
                self.nl()
            elif t == 'table':
                self.table(b['rows'])
            elif t == 'img':
                self.image(b)

    def table(self, rows):
        th = self.th
        W = self.width
        ncol = max(len(r) for r in rows)
        rows = [[clean(c) for c in r] + [''] * (ncol - len(r)) for r in rows]
        widths = [max(len(r[i]) for r in rows) for i in range(ncol)]
        cap = max(12, int(W * 0.48))
        widths = [min(w, cap) for w in widths]
        avail = W - (ncol + 1) * 3 + 2
        if sum(widths) > avail:
            # shrink widest columns first
            cap = max(4, avail // max(1, ncol))
            while sum(widths) > avail and max(widths) > 4:
                i = widths.index(max(widths))
                widths[i] -= 1
            widths = [max(1, w) for w in widths]
        border = th.c('sel', 'win')
        sep = '+' + '+'.join('-' * (w + 2) for w in widths) + '+'
        self.blank()
        self.nl().cells.append((0, sep[:W], border))
        for ri, r in enumerate(rows):
            cells = [wrap_text(c, max(1, widths[i])) or [''] for i, c in enumerate(r)]
            hgt = min(6, max(len(c) for c in cells))
            for k in range(hgt):
                ln = self.nl()
                x = 0
                ln.cells.append((0, '|', border))
                for i, w in enumerate(widths):
                    s = cells[i][k] if k < len(cells[i]) else ''
                    a = th.c('bright', 'win', 1) if ri == 0 else th.c('text', 'win')
                    ln.cells.append((x + 2, fit(s, w), a))
                    x += w + 3
                    ln.cells.append((x, '|', border))
            if ri == 0 or ri == len(rows) - 1:
                self.nl().cells.append((0, sep[:W], border))
        self.nl()

    def image(self, b):
        app = self.app
        if not app.cfg.get('web_images', True):
            if b.get('alt'):
                ln = self.nl()
                ln.cells.append((0, '[img: %s]' % clean(b['alt'])[:self.width - 7], self.th.c('dim', 'win')))
            return
        W = self.width
        bw = b.get('w') or 0
        bh = b.get('h') or 0
        maxw = min(W, 110)
        if bw:
            maxw = min(maxw, max(24, int(bw / 6)))
        maxw = max(16, maxw)
        cached = app.img_cache.get((b['src'], maxw))
        if cached and cached != 'err':
            cells, cw, chh = cached
            self.blank()
            start = len(self.lines)
            for row in cells:
                ln = self.nl()
                ln.cells.append((0, row, None))   # None attr marks pre-rendered cells
                if b.get('href'):
                    ln.hits.append((0, cw, ('link', b['href'], b.get('id'))))
            if b.get('alt'):
                ln = self.nl()
                ln.cells.append((0, fit(clean(b['alt']), W), self.th.c('dim', 'win')))
            self.nl()
            return
        if cached == 'err':
            if b.get('alt'):
                ln = self.nl()
                ln.cells.append((0, '[%s]' % fit(clean(b['alt']), W - 2).strip(), self.th.c('dim', 'win')))
            return
        # placeholder
        hgt = max(2, min(28, int(maxw * (bh / bw) / 2))) if bw and bh else 4
        self.blank()
        for i in range(hgt):
            ln = self.nl()
            ln.cells.append((0, ':' * maxw if i == 0 else ' ' * maxw, self.th.c('sel', 'raised')))
        self.images.append((b, maxw))
        self.nl()


class Browser(App):
    name = 'browser'
    preserve_colors = True
    title = 'Browser'
    icon = '(@)'
    size = (100, 32)
    min_size = (40, 10)

    def __init__(self, wm, win, url=None, **kw):
        super().__init__(wm, win)
        self.cfg = wm.cfg
        self.addr = TextField(placeholder='Search or enter address', on_enter=self.go_text)
        self.addr_focus = False
        self.doc = None
        self.url = None
        self.history = []
        self.future = []
        self.scroll = 0
        self.layout = None
        self.layout_key = None
        self.values = {}
        self.focus_idx = -1
        self.loading = False
        self.status = ''
        self.hover_url = ''
        self.engine = None
        self.engine_name = self.pick_engine()
        self.img_cache = {}
        self.img_loading = set()
        self.edit = None           # (widget, TextField) while typing into a page field
        self.find = None
        self.reader = True
        self.navigate(url or self.cfg.get('web_home') or HOME_URL)

    def pick_engine(self):
        pref = self.cfg.get('web_engine', 'auto')
        if pref == 'lite':
            return 'lite'
        if pref == 'chromium' or (pref == 'auto' and web.Chromium.find(self.cfg.get('chromium', ''))):
            return 'chromium'
        return 'lite'

    def get_engine(self):
        if self.engine is None:
            if self.engine_name == 'chromium':
                try:
                    self.engine = web.CDPEngine(self.cfg.get('chromium', ''))
                except Exception as e:
                    self.wm.loop.call_soon_threadsafe(lambda e=e: self.wm.toast('Chromium unavailable, using lite: %s' % e, 'red'))
                    self.engine_name = 'lite'
                    self.engine = web.LiteEngine()
            else:
                self.engine = web.LiteEngine()
            self.engine.reader = self.reader
        return self.engine

    def proc_info(self):
        return 'engine %s' % self.engine_name

    def reopen(self, url=None, **kw):
        if url:
            self.navigate(url)

    # ------------------------------------------------------------ navigation
    def go_text(self, t):
        t = t.strip()
        self.addr_focus = False
        if not t:
            return
        if t.startswith(('http://', 'https://', 'file://', 'about:')):
            url = t
        elif os.path.exists(os.path.expanduser(t)):
            url = 'file://' + urllib.parse.quote(os.path.abspath(os.path.expanduser(t)))
        elif re.match(r'^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$', t) and ' ' not in t:
            url = 'https://' + t
        else:
            url = self.cfg.get('search_url', 'https://html.duckduckgo.com/html/?q=%s') % urllib.parse.quote_plus(t)
        self.navigate(url)

    def navigate(self, url, data=None, record=True, action=None):
        if record and self.url and self.url != url:
            self.history.append((self.url, self.scroll))
            self.future = []
        self.loading = True
        self.status = 'Loading ' + url
        self.addr.set(url if not url.startswith('about:') else '')
        self.edit = None
        self.invalidate()
        self.load_id = getattr(self, 'load_id', 0) + 1
        lid = self.load_id
        if url == HOME_URL:
            self.show(self.home_doc())
            return
        target = url

        def work():
            eng = self.get_engine()
            if action:
                return eng.action(*action)
            return eng.load(target, data)

        def done(doc):
            if doc is None or lid != self.load_id or self.win.closed:
                return
            self.show(doc)

        def fail(e):
            if lid != self.load_id or self.win.closed:
                return
            self.loading = False
            self.status = ''
            self.show({'title': 'Error', 'url': url, 'blocks': [
                {'t': 'h', 'l': 1, 's': [{'x': 'Could not load the page'}]},
                {'t': 'p', 's': [{'x': str(e)}]}, {'t': 'p', 's': [{'x': url, 'h': url}]}]})
        self.thread(work, done, fail)

    def show(self, doc):
        if self.win.closed:
            return
        self.doc = doc
        self.url = doc.get('url') or self.url
        self.loading = False
        self.status = ''
        self.scroll = 0
        self.values = {}
        self.focus_idx = -1
        self.layout_key = None
        self.notice = ''
        if doc.get('needs_js') and self.engine_name == 'lite':
            self.notice = 'This page is mostly JavaScript - switch the engine to Chromium in the menu (==).'
        if self.url and not self.url.startswith('about:'):
            self.addr.set(self.url)
        self.set_title(clean(doc.get('title') or self.url or 'Browser')[:60])
        self.invalidate()

    def back(self):
        if not self.history:
            return
        url, sc = self.history.pop()
        self.future.append((self.url, self.scroll))
        self.navigate(url, record=False)
        self.pending_scroll = sc

    def forward(self):
        if not self.future:
            return
        url, sc = self.future.pop()
        self.history.append((self.url, self.scroll))
        self.navigate(url, record=False)

    def home_doc(self):
        bm = self.cfg.get('bookmarks', [])
        blocks = [{'t': 'h', 'l': 1, 's': [{'x': 'monocell web'}]},
                  {'t': 'p', 's': [{'x': 'Type an address or a search into the bar above (Ctrl+L). Engine: '},
                                   {'x': self.engine_name, 'b': 1},
                                   {'x': ' - switch in the menu.'}]},
                  {'t': 'h', 'l': 2, 's': [{'x': 'Bookmarks'}]}]
        for name, url in bm:
            blocks.append({'t': 'li', 'd': 1, 's': [{'x': name, 'h': url}, {'x': '  ' + url, 'c': 0}]})
        blocks.append({'t': 'h', 'l': 2, 's': [{'x': 'Keys'}]})
        for k in ('Ctrl+L address bar   Tab/Shift+Tab next/previous link   Enter open',
                  'Alt+Left/Right or Backspace history   Space/PgDn scroll   Ctrl+D bookmark   Ctrl+F find',
                  'Click links, fields and buttons with the mouse; the wheel scrolls.'):
            blocks.append({'t': 'p', 's': [{'x': k}]})
        return {'title': 'Home', 'url': HOME_URL, 'blocks': blocks}

    # ------------------------------------------------------------ images
    def request_images(self):
        if not self.layout or not self.cfg.get('web_images', True):
            return
        for b, maxw in self.layout.images:
            key = (b['src'], maxw)
            if key in self.img_cache or key in self.img_loading or len(self.img_loading) >= 4:
                continue
            self.img_loading.add(key)

            def work(src=b['src'], maxw=maxw):
                _, ct, data = web.fetch(src, max_bytes=6_000_000, headers={'Accept': 'image/*'})
                if 'svg' in ct or src.lower().endswith('.svg'):
                    return None          # vector images are skipped quietly
                return imaging.from_bytes(data, max_side=800)

            def done(img, key=key, maxw=maxw):
                self.img_loading.discard(key)
                if img is None:
                    self.img_cache[key] = 'err'
                    self.layout_key = None
                    self.invalidate()
                    return
                rows, cw, chh = imaging.render_fit(self.wm, img, maxw, 42)
                self.img_cache[key] = (rows, cw, chh)
                self.layout_key = None
                self.invalidate()

            def fail(e, key=key):
                self.img_loading.discard(key)
                self.img_cache[key] = 'err'
                self.layout_key = None
                self.invalidate()
            self.thread(work, done, fail)

    def on_theme(self):
        self.img_cache = {}
        self.layout_key = None

    # ------------------------------------------------------------ draw
    def content_geom(self, w, h):
        cw = min(w - 2, 110)
        x0 = max(0, (w - 1 - cw) // 2)
        return x0, 2, cw, h - 3

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        # toolbar
        x = 0
        x += button(self, cv, x, 0, '<', 'back', enabled=bool(self.history)) + 1
        x += button(self, cv, x, 0, '>', 'fwd', enabled=bool(self.future)) + 1
        x += button(self, cv, x, 0, 'x' if self.loading else 'R', 'reload') + 1
        aw = w - x - 12
        self.addr.draw(cv, x, 0, aw, th, focused and self.addr_focus)
        self.zone(x, 0, aw, 1, 'addr')
        self.addr_x = x
        bx = x + aw + 1
        button(self, cv, bx, 0, 'Aa', 'reader', active=self.reader)
        button(self, cv, w - 5, 0, util.sym('menu', '=='), 'menu')
        x0, y0, cw, ch = self.content_geom(w, h)
        if self.doc is None:
            cv.put(2, 3, 'Loading...', th.c('dim', 'win'))
        else:
            key = (cw, id(self.doc), len(self.img_cache), tuple(sorted((k, v) for k, v in self.values.items()
                                                                       if isinstance(k, int)))[:50])
            if key != self.layout_key:
                self.layout = Layout(self, self.doc, cw)
                self.layout_key = key
                self.request_images()
                if getattr(self, 'pending_scroll', None) is not None:
                    self.scroll = self.pending_scroll
                    self.pending_scroll = None
            L = self.layout
            total = len(L.lines)
            self.scroll = max(0, min(self.scroll, max(0, total - ch)))
            focus_t = L.focusables[self.focus_idx][3] if 0 <= self.focus_idx < len(L.focusables) else None
            for i in range(ch):
                li = self.scroll + i
                if li >= total:
                    break
                ln = L.lines[li]
                for cx, s, a in ln.cells:
                    if a is None:
                        cv.sub(x0, y0 + i, cw, 1).blit_cells(cx, 0, [s])
                    else:
                        cv.put(x0 + cx, y0 + i, s, a)
                for hx0, hx1, tgt in ln.hits:
                    hov = self.hover == ('hit', li, hx0)
                    if tgt == focus_t or hov:
                        if tgt[0] == 'w' or len(ln.cells) and ln.cells[0][2] is not None:
                            cv.set_attr(x0 + hx0, y0 + i, hx1 - hx0, 1, th.c('on_accent', 'accent'))
                    self.zone(x0 + hx0, y0 + i, hx1 - hx0, 1, ('hit', li, hx0))
                if self.edit and any(t == ('w', self.edit[0]) for _, _, t in ln.hits):
                    for hx0, hx1, tgt in ln.hits:
                        if tgt == ('w', self.edit[0]):
                            self.edit[1].draw(cv, x0 + hx0, y0 + i, hx1 - hx0, th, focused)
                if self.find and self.find.text:
                    t = self.find.text.lower()
                    for cx, s, a in ln.cells:
                        if a is None:
                            continue
                        j = s.lower().find(t)
                        while j >= 0:
                            cv.set_attr(x0 + cx + j, y0 + i, len(t), 1, th.c('on_accent', 'yellow'))
                            j = s.lower().find(t, j + 1)
            g = sb_geom(w - 1, y0, ch, total, ch, self.scroll)
            sb_draw(cv, g, th)
            self.sb = g
        # status
        if self.find is not None:
            cv.put(0, h - 1, 'Find', th.c('dim', 'win'))
            self.find.draw(cv, 5, h - 1, min(40, w - 6), th, focused)
        else:
            st = self.hover_url or self.status or ''
            pct = ''
            if self.layout and len(self.layout.lines) > 8:
                pct = '  %d%%' % (100 * self.scroll // max(1, len(self.layout.lines) - 1))
            right = self.engine_name + pct
            cv.put(0, h - 1, fit(st, w - len(right) - 2), th.c('accent' if self.loading else 'dim', 'win'))
            cv.put(w - len(right), h - 1, right, th.c('dim', 'win'))

    # ------------------------------------------------------------ activation
    def activate(self, tgt):
        if tgt[0] == 'link':
            href = tgt[1]
            if not href or href.startswith('javascript:') or (self.engine_name == 'chromium' and tgt[2] and
                                                              (href.endswith('#') or href == self.url + '#')):
                if self.engine_name == 'chromium' and tgt[2]:
                    self.navigate(self.url, record=True, action=('click', tgt[2]))
                return
            if href.split('#')[0] == (self.url or '').split('#')[0] and '#' in href:
                return
            self.navigate(href)
        elif tgt[0] == 'w':
            wd = tgt[1]
            k = wd['w']
            if k == 'input':
                tf = TextField(self.values.get(wd['id'], wd.get('val', '')), password=wd.get('pw'),
                               on_enter=lambda t, wd=wd: self.submit_field(wd, t))
                self.edit = (wd, tf)
                self.addr_focus = False
            elif k == 'check':
                cur = self.values.get(wd['id'], wd.get('on'))
                self.values[wd['id']] = not cur
                if self.engine_name == 'chromium':
                    self.navigate(self.url, record=False, action=('click', wd['id']))
            elif k == 'select':
                opts = wd.get('opts', [])
                items = [MenuItem(clean(o.get('x') or o.get('v') or '')[:40],
                                  (lambda o=o: self.choose(wd, o))) for o in opts[:30]]
                if items:
                    mx, my = self.wm.mouse_pos
                    self.wm.menu(items, mx, my + 1)
            elif k == 'button':
                if self.engine_name == 'chromium':
                    self.sync_values(then=('click', wd['id']))
                elif wd.get('submit', True):
                    self.submit_form(wd)

    def choose(self, wd, o):
        self.values[wd['id']] = o.get('v') if o.get('v') is not None else o.get('x')
        self.values[('sel', wd['id'])] = o.get('x') or o.get('v')
        self.layout_key = None
        if self.engine_name == 'chromium':
            self.navigate(self.url, record=False, action=('select', wd['id'], self.values[wd['id']]))

    def submit_field(self, wd, text):
        self.values[wd['id']] = text
        self.edit = None
        self.layout_key = None
        if self.engine_name == 'chromium':
            self.sync_values(then=('submit', wd['id']))
        else:
            self.submit_form(wd)

    def sync_values(self, then=None):
        vals = {k: v for k, v in self.values.items() if isinstance(k, int)}
        eng_action = then

        def work():
            eng = self.get_engine()
            for wid, v in vals.items():
                if isinstance(v, str):
                    eng.action('set', wid, v)
            return eng.action(*eng_action) if eng_action else None

        def done(doc):
            if doc:
                if self.url:
                    self.history.append((self.url, self.scroll))
                self.show(doc)
        self.loading = True
        self.status = 'Working...'
        self.thread(work, done)

    def submit_form(self, wd):
        eng = self.get_engine()
        url, data = eng.submit(self.doc, wd, self.values)
        if url:
            self.navigate(url, data)

    def bookmark(self):
        if not self.url or self.url.startswith('about:'):
            return
        bm = self.cfg.get('bookmarks', [])
        if any(u == self.url for _, u in bm):
            self.wm.toast('Already bookmarked')
            return
        bm.append([self.doc.get('title') or self.url, self.url])
        self.cfg['bookmarks'] = bm
        self.cfg.save()
        self.wm.toast('Bookmarked')

    def menu(self):
        items = [MenuItem('Home', lambda: self.navigate(HOME_URL)),
                 MenuItem('Bookmark this page', self.bookmark, 'Ctrl+D'),
                 None]
        for name, url in self.cfg.get('bookmarks', [])[:12]:
            items.append(MenuItem(fit(name, 30).strip(), (lambda u=url: self.navigate(u))))
        items += [None,
                  MenuItem('Engine: lite (fast, no JS)', lambda: self.switch_engine('lite'), checked=self.engine_name == 'lite'),
                  MenuItem('Engine: Chromium (JS)', lambda: self.switch_engine('chromium'), checked=self.engine_name == 'chromium'),
                  MenuItem('Reader mode', self.toggle_reader, checked=self.reader),
                  MenuItem('Show images', self.toggle_images, checked=bool(self.cfg.get('web_images', True))),
                  MenuItem('Find in page', self.open_find, 'Ctrl+F'),
                  MenuItem('Copy address', lambda: self.wm.set_clipboard(self.url or ''))]
        self.wm.menu(items, self.win.x + self.win.w - 30, self.win.y + 2)

    def toggle_reader(self):
        self.reader = not self.reader
        if self.engine:
            self.engine.reader = self.reader
        if self.url:
            self.navigate(self.url, record=False)

    def toggle_images(self):
        self.cfg['web_images'] = not self.cfg.get('web_images', True)
        self.cfg.save()
        self.layout_key = None

    def switch_engine(self, name):
        if name == self.engine_name:
            return
        if self.engine:
            eng = self.engine
            self.thread(eng.close)
        self.engine = None
        self.engine_name = name
        if self.url:
            self.navigate(self.url, record=False)

    def open_find(self):
        self.find = TextField(placeholder='find in page', on_enter=lambda t: self.find_next())

    def find_next(self):
        if not self.find or not self.layout:
            return
        t = self.find.text.lower()
        n = len(self.layout.lines)
        for k in range(1, n + 1):
            li = (self.scroll + k) % n
            if any(a is not None and t in s.lower() for _, s, a in self.layout.lines[li].cells):
                self.scroll = max(0, li - 3)
                return
        self.wm.toast('Not found')

    def on_close(self):
        if self.engine:
            eng = self.engine
            self.engine = None
            try:
                eng.close()
            except Exception:
                pass
            if not any(isinstance(w.app, Browser) and w.app is not self for w in self.wm.windows):
                web.shutdown_all()
        return True

    # ------------------------------------------------------------ input
    def move_focus(self, d):
        if not self.layout or not self.layout.focusables:
            return
        n = len(self.layout.focusables)
        if self.focus_idx < 0:
            # first focusable in view
            for i, f in enumerate(self.layout.focusables):
                if f[0] >= self.scroll:
                    self.focus_idx = i if d > 0 else max(0, i - 1)
                    break
            else:
                self.focus_idx = 0
        else:
            self.focus_idx = (self.focus_idx + d) % n
        li = self.layout.focusables[self.focus_idx][0]
        ch = self.content_geom(self.cw, self.ch)[3]
        if li < self.scroll or li >= self.scroll + ch:
            self.scroll = max(0, li - ch // 3)
        tgt = self.layout.focusables[self.focus_idx][3]
        self.hover_url = tgt[1] if tgt[0] == 'link' else ''

    def on_click(self, key, ev):
        if key == 'back':
            self.back()
        elif key == 'fwd':
            self.forward()
        elif key == 'reload':
            if self.url:
                self.navigate(self.url, record=False)
        elif key == 'addr':
            self.addr_focus = True
            self.edit = None
            self.addr.click(ev.x - self.addr_x)
        elif key == 'menu':
            self.menu()
        elif key == 'reader':
            self.toggle_reader()
        elif key[0] == 'hit':
            li, hx0 = key[1], key[2]
            for x0, x1, tgt in self.layout.lines[li].hits:
                if x0 == hx0:
                    self.addr_focus = False
                    if tgt[0] == 'link' and ev.button == 1:
                        self.wm.open('browser', url=tgt[1])
                    else:
                        self.activate(tgt)
                    break

    def on_mouse(self, ev):
        g = getattr(self, 'sb', None)
        if ev.kind == 'up' and getattr(self, 'sb_grab', None) is not None:
            self.sb_grab = None
            self.invalidate()
            return True
        if g and (g.hit(ev) or (ev.kind == 'drag' and getattr(self, 'sb_grab', None) is not None)):
            ch = self.content_geom(self.cw, self.ch)[3]
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
        if ev.kind == 'wheelup':
            self.scroll = max(0, self.scroll - 3)
            self.invalidate()
            return True
        if ev.kind == 'wheeldown':
            self.scroll += 3
            self.invalidate()
            return True
        if ev.kind == 'down' and ev.button == 0:
            k = self.zone_at(ev.x, ev.y)
            if k != 'addr':
                self.addr_focus = False
            if not (isinstance(k, tuple) and k[0] == 'hit'):
                self.edit = None
        if ev.kind == 'down' and ev.button == 1:
            k = self.zone_at(ev.x, ev.y)
            if isinstance(k, tuple) and k[0] == 'hit':
                self.on_click(k, ev)
                return True
        if ev.kind == 'down' and ev.button == 0 and ev.x == self.cw - 1 and ev.y >= 2 and self.layout:
            total = len(self.layout.lines)
            ch = self.content_geom(self.cw, self.ch)[3]
            self.scroll = int((ev.y - 2) / max(1, ch - 1) * max(0, total - ch))
            self.invalidate()
            return True
        r = super().on_mouse(ev)
        k = self.hover
        url = ''
        if isinstance(k, tuple) and k[0] == 'hit' and self.layout and k[1] < len(self.layout.lines):
            for x0, x1, tgt in self.layout.lines[k[1]].hits:
                if x0 == k[2] and tgt[0] == 'link':
                    url = tgt[1]
        if url != self.hover_url:
            self.hover_url = url
            self.invalidate()
        return r

    def on_key(self, ev):
        k = ev.key
        if self.addr_focus:
            if k == 'esc':
                self.addr_focus = False
                self.addr.set(self.url or '')
            else:
                self.addr.key(ev)
            self.invalidate()
            return True
        if self.edit:
            wd, tf = self.edit
            if k == 'esc':
                self.values[wd['id']] = tf.text
                self.edit = None
                self.layout_key = None
            elif k in ('tab', 'shift+tab'):
                self.values[wd['id']] = tf.text
                self.edit = None
                self.layout_key = None
                self.move_focus(-1 if k == 'shift+tab' else 1)
            else:
                tf.key(ev)
            self.invalidate()
            return True
        if self.find is not None:
            if k == 'esc':
                self.find = None
            elif k == 'enter':
                self.find_next()
            else:
                self.find.key(ev)
            self.invalidate()
            return True
        ch = self.content_geom(self.cw, self.ch)[3]
        if k in ('ctrl+l', 'f6', 'alt+d', 'o'):
            self.addr_focus = True
            self.addr.set(self.url if self.url and not self.url.startswith('about:') else '')
            self.addr.sel_all = True
        elif k in ('down', 'j'):
            self.scroll += 1
        elif k in ('up', 'k'):
            self.scroll = max(0, self.scroll - 1)
        elif k in ('pgdn', ' '):
            self.scroll += ch - 2
        elif k in ('pgup', 'shift+ '):
            self.scroll = max(0, self.scroll - ch + 2)
        elif k in ('home', 'g'):
            self.scroll = 0
        elif k in ('end', 'G'):
            self.scroll = 1 << 30
        elif k in ('tab', 'shift+tab'):
            self.move_focus(-1 if k == 'shift+tab' else 1)
        elif k == 'enter' and self.layout and 0 <= self.focus_idx < len(self.layout.focusables):
            self.activate(self.layout.focusables[self.focus_idx][3])
        elif k in ('alt+left', 'backspace', 'h'):
            self.back()
        elif k in ('alt+right', 'l'):
            self.forward()
        elif k in ('ctrl+r', 'f5', 'r'):
            if self.url:
                self.navigate(self.url, record=False)
        elif k == 'ctrl+d':
            self.bookmark()
        elif k in ('ctrl+f', '/'):
            self.open_find()
        elif k == 'ctrl+t':
            self.wm.open('browser')
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        t = text.strip().split('\n')[0]
        if self.edit:
            self.edit[1].insert(t)
        elif self.find is not None:
            self.find.insert(t)
        else:
            self.addr_focus = True
            self.addr.set(t)
        self.invalidate()


APP = Browser
