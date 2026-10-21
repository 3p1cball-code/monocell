"""Web engines for the Browser app.

lite     : urllib + html.parser (no JavaScript; fast, works everywhere)
chromium : headless Chromium/Brave driven over the DevTools protocol (JS, SPAs); the DOM is
           extracted into the same block model, so both engines share one text layout.

Document model:  {'title', 'url', 'blocks': [...]}
  block: {'t': 'p'|'h'|'li'|'q'|'pre'|'hr'|'img'|'table', ...}
         p/h/li/q carry 's': list of spans {'x': text, 'h': href, 'b', 'i', 'c', 'w': widget...}
"""
import base64
import gzip
import html
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

from .util import CACHE_DIR, which

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 monocell/0.1'


# ======================================================================= fetching
def fetch(url, data=None, timeout=20, max_bytes=8_000_000, headers=None):
    """Returns (final_url, content_type, bytes)."""
    if url.startswith('file://'):
        path = urllib.parse.unquote(url[7:])
        with open(path, 'rb') as f:
            body = f.read(max_bytes)
        ct = 'text/html' if path.lower().endswith(('.html', '.htm', '.xhtml')) else 'text/plain'
        return url, ct, body
    h = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
         'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8', 'Accept-Encoding': 'gzip, deflate'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(max_bytes)
        enc = r.headers.get('Content-Encoding', '')
        if enc == 'gzip':
            body = gzip.decompress(body)
        elif enc == 'deflate':
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = zlib.decompress(body, -15)
        return r.geturl(), r.headers.get('Content-Type', ''), body


def decode_html(body, ctype):
    m = re.search(r'charset=([\w-]+)', ctype or '', re.I)
    enc = m.group(1) if m else None
    if not enc:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I)
        enc = m.group(1).decode() if m else 'utf-8'
    try:
        return body.decode(enc, 'replace')
    except LookupError:
        return body.decode('utf-8', 'replace')


# ======================================================================= lite engine
BLOCK = {'p', 'div', 'section', 'article', 'main', 'header', 'footer', 'nav', 'aside', 'form', 'figure',
         'figcaption', 'dl', 'dt', 'dd', 'address', 'details', 'summary', 'fieldset', 'tr', 'body',
         'table', 'tbody', 'thead', 'tfoot', 'caption', 'legend'}
SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'iframe', 'canvas', 'object', 'embed',
        'video', 'audio', 'math', 'select_options'}
VOID = {'br', 'img', 'input', 'hr', 'meta', 'link', 'area', 'base', 'col', 'embed', 'source', 'track', 'wbr'}


JUNK = re.compile(r'(^|[-_ ])(nav|navbar|navbox|menu|sidebar|footer|cookie|consent|banner|share|social|toc|'
                  r'mw-editsection|noprint|breadcrumb|skip|related|advert|ads?|promo|newsletter|vector-|mw-jump)([-_ ]|$)', re.I)


class LiteParser(HTMLParser):
    def __init__(self, base, reader=True):
        super().__init__(convert_charrefs=True)
        self.reader = reader
        self.base = base
        self.blocks = []
        self.cur = None
        self.title = ''
        self.in_title = False
        self.skip = 0
        self.skip_tag = None
        self.stack = []          # open tags
        self.href = None
        self.b = self.i = self.c = 0
        self.depth = 0
        self.ol = []
        self.pre = None
        self.quote = 0
        self.forms = []
        self.form = None
        self.widgets = {}
        self.wid = 0
        self.select = None
        self.textarea = None
        self.table = None        # data table being collected
        self.row = None
        self.cell = None
        self.td_depth = 0
        self.row_cells = 0
        self.button = None

    def url(self, u):
        return urllib.parse.urljoin(self.base, html.unescape(u or '').strip())

    def _implicit_close(self, tag):
        """HTML allows omitting </td> and </tr>; unwind the open stack like a browser."""
        if tag in ('tr', 'tbody', 'thead', 'tfoot'):
            while self.stack and self.stack[-1] != 'table':
                self.handle_endtag(self.stack[-1])
        elif tag in ('td', 'th'):
            while self.stack and self.stack[-1] not in ('tr', 'table'):
                self.handle_endtag(self.stack[-1])

    def flush(self):
        if self.cur and any(s.get('w') or s['x'].strip() for s in self.cur['s']):
            self.blocks.append(self.cur)
        self.cur = None

    def ensure(self):
        if self.cur is None:
            self.cur = {'t': 'q' if self.quote else 'p', 's': []}
        return self.cur

    def add_span(self, text):
        if self.cell is not None:
            self.cell.append(text)
            return
        if self.button is not None:
            self.button['x'] += text
            return
        sp = {'x': text}
        if self.href:
            sp['h'] = self.href
        if self.b:
            sp['b'] = 1
        if self.i:
            sp['i'] = 1
        if self.c:
            sp['c'] = 1
        self.ensure()['s'].append(sp)

    def widget(self, kind, **kw):
        self.wid += 1
        w = dict(kw, w=kind, id=self.wid, form=len(self.forms) - 1 if self.form is not None else -1)
        self.widgets[self.wid] = w
        if self.form is not None:
            self.form['fields'].append(w)
        if self.cell is not None:
            return w
        self.ensure()['s'].append(w)
        return w

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        junk = False
        if self.reader and tag not in VOID:
            cid = (a.get('class') or '') + ' ' + (a.get('id') or '')
            junk = (tag in ('nav', 'aside', 'footer') or a.get('role') in ('navigation', 'banner', 'contentinfo', 'search')
                    or (JUNK.search(cid) is not None and tag not in ('body', 'html', 'main', 'article')))
        if tag in SKIP or junk or (tag not in VOID and (a.get('hidden') is not None or
                                                        'display:none' in (a.get('style') or '').replace(' ', ''))):
            self.skip = 1
            self.skip_tag = tag
            return
        if tag in ('tr', 'td', 'th', 'tbody', 'thead', 'tfoot'):
            self._implicit_close(tag)
        if tag == 'title':
            self.in_title = True
        elif tag == 'a':
            self.href = self.url(a.get('href')) if a.get('href') else None
        elif tag in ('b', 'strong'):
            self.b += 1
        elif tag in ('i', 'em'):
            self.i += 1
        elif tag in ('code', 'kbd', 'tt', 'samp'):
            self.c += 1
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.flush()
            self.cur = {'t': 'h', 'l': int(tag[1]), 's': []}
        elif tag in ('ul', 'ol'):
            self.flush()
            self.depth += 1
            self.ol.append(0 if tag == 'ol' else None)
        elif tag == 'li':
            self.flush()
            n = None
            if self.ol and self.ol[-1] is not None:
                self.ol[-1] += 1
                n = self.ol[-1]
            self.cur = {'t': 'li', 'd': max(1, self.depth), 'n': n, 's': []}
        elif tag == 'pre':
            self.flush()
            self.pre = []
        elif tag == 'blockquote':
            self.flush()
            self.quote += 1
        elif tag == 'br':
            if self.pre is not None:
                self.pre.append('\n')
            elif self.cell is not None:
                self.cell.append(' ')
            else:
                self.add_span('\n')
        elif tag == 'hr':
            self.flush()
            self.blocks.append({'t': 'hr'})
        elif tag == 'img':
            src = a.get('src') or a.get('data-src') or ''
            if not src and a.get('srcset'):
                src = a['srcset'].split(',')[0].split()[0]
            try:
                w = int(re.sub(r'\D', '', a.get('width', '0')) or 0)
                h = int(re.sub(r'\D', '', a.get('height', '0')) or 0)
            except ValueError:
                w = h = 0
            alt = a.get('alt', '')
            if src and not src.startswith('data:') and (w == 0 or w >= 48):
                if self.cell is None:
                    self.flush()
                    self.blocks.append({'t': 'img', 'src': self.url(src), 'alt': alt, 'w': w, 'h': h, 'href': self.href})
            elif alt and (w == 0 or w >= 16):
                self.add_span('[%s]' % alt)
        elif tag == 'table':
            cls = (a.get('class') or '') + ' ' + (a.get('border') or '')
            if self.table is None and ('wikitable' in cls or 'data' in cls or a.get('border') not in (None, '0')
                                       or 'infobox' in cls):
                self.flush()
                self.table = []
            elif self.table is not None:
                pass
            else:
                self.flush()
        elif tag == 'tr':
            self.row_cells = 0
            if self.table is not None:
                self.row = []
                self.table.append(self.row)
            else:
                self.flush()
        elif tag in ('td', 'th'):
            self.td_depth += 1
            self.row_cells += 1
            if self.table is not None and self.row is not None:
                self.cell = []
                self.cell_head = tag == 'th'
            else:
                self.add_span(' ')
        elif tag == 'form':
            self.flush()
            self.form = {'action': self.url(a.get('action') or self.base), 'method': (a.get('method') or 'get').lower(),
                         'fields': []}
            self.forms.append(self.form)
        elif tag == 'input':
            t = (a.get('type') or 'text').lower()
            name = a.get('name', '')
            if t == 'hidden':
                if self.form is not None:
                    self.form['fields'].append({'w': 'hidden', 'name': name, 'val': a.get('value', '')})
            elif t in ('submit', 'button', 'image', 'reset'):
                self.widget('button', name=name, x=a.get('value') or a.get('alt') or ('Search' if t == 'submit' else t),
                            val=a.get('value', ''), submit=t in ('submit', 'image'))
            elif t in ('checkbox', 'radio'):
                self.widget('check', name=name, val=a.get('value', 'on'), on=a.get('checked') is not None, radio=t == 'radio')
            else:
                self.widget('input', name=name, val=a.get('value', ''), ph=a.get('placeholder', '') or a.get('aria-label', ''),
                            pw=t == 'password')
        elif tag == 'textarea':
            self.textarea = self.widget('input', name=a.get('name', ''), val='', ph=a.get('placeholder', ''), multi=1)
        elif tag == 'select':
            self.select = self.widget('select', name=a.get('name', ''), val='', opts=[])
        elif tag == 'option' and self.select is not None:
            self.opt = {'v': a.get('value'), 'x': '', 'sel': a.get('selected') is not None}
            self.select['opts'].append(self.opt)
        elif tag == 'button':
            self.button = self.widget('button', name=a.get('name', ''), x='', val=a.get('value', ''),
                                      submit=(a.get('type') or 'submit').lower() == 'submit')
        elif tag in BLOCK:
            if self.row_cells == 0:
                self.flush()
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        if tag == 'title':
            self.in_title = False
        elif tag == 'a':
            self.href = None
        elif tag in ('b', 'strong'):
            self.b = max(0, self.b - 1)
        elif tag in ('i', 'em'):
            self.i = max(0, self.i - 1)
        elif tag in ('code', 'kbd', 'tt', 'samp'):
            self.c = max(0, self.c - 1)
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
            self.flush()
        elif tag in ('ul', 'ol'):
            self.flush()
            self.depth = max(0, self.depth - 1)
            if self.ol:
                self.ol.pop()
        elif tag == 'pre':
            if self.pre is not None:
                self.blocks.append({'t': 'pre', 'x': ''.join(self.pre).strip('\n')})
            self.pre = None
        elif tag == 'blockquote':
            self.flush()
            self.quote = max(0, self.quote - 1)
        elif tag in ('td', 'th'):
            self.td_depth = max(0, self.td_depth - 1)
            self.row_cells = max(0, self.row_cells - 1)
            if self.cell is not None:
                self.row.append(re.sub(r'\s+', ' ', ''.join(self.cell)).strip())
                self.cell = None
        elif tag == 'tr' and self.table is not None:
            self.row = None
        elif tag == 'table':
            if self.table is not None and self.stack.count('table') <= 1:
                rows = [r for r in self.table if any(c for c in r)]
                if rows:
                    self.blocks.append({'t': 'table', 'rows': rows})
                self.table = None
            else:
                self.flush()
        elif tag == 'form':
            self.flush()
            self.form = None
        elif tag == 'textarea':
            self.textarea = None
        elif tag == 'select':
            if self.select is not None:
                opts = self.select['opts']
                sel = next((o for o in opts if o['sel']), opts[0] if opts else None)
                if sel:
                    self.select['val'] = sel['v'] if sel['v'] is not None else sel['x']
                    self.select['x'] = sel['x']
            self.select = None
        elif tag == 'button':
            if self.button is not None:
                self.button['x'] = re.sub(r'\s+', ' ', self.button['x']).strip() or 'Submit'
            self.button = None
        elif tag in BLOCK:
            if self.row_cells == 0:
                self.flush()
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
            return
        if self.textarea is not None:
            self.textarea['val'] += data
            return
        if self.select is not None:
            if hasattr(self, 'opt') and self.opt in self.select['opts']:
                self.opt['x'] += data.strip()
            return
        if self.pre is not None:
            self.pre.append(data)
            return
        t = re.sub(r'\s+', ' ', data)
        if not t:
            return
        if t == ' ' and (self.cur is None or not self.cur['s']):
            return
        self.add_span(t)

    def result(self):
        self.flush()
        return {'title': re.sub(r'\s+', ' ', self.title).strip(), 'blocks': self.blocks}


def parse_html(text, base, reader=True):
    p = LiteParser(base, reader)
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass
    doc = p.result()
    doc['url'] = base
    doc['forms'] = p.forms
    doc['widgets'] = p.widgets
    return doc


def text_doc(text, url):
    return {'title': url.rsplit('/', 1)[-1], 'url': url, 'blocks': [{'t': 'pre', 'x': text}], 'forms': [], 'widgets': {}}


class LiteEngine:
    name = 'lite'
    reader = True

    def load(self, url, data=None):
        final, ct, body = fetch(url, data)
        if 'image/' in ct:
            return {'title': final.rsplit('/', 1)[-1], 'url': final, 'blocks': [{'t': 'img', 'src': final, 'alt': '', 'w': 0, 'h': 0}],
                    'forms': [], 'widgets': {}}
        text = decode_html(body, ct)
        if 'html' in ct or text.lstrip()[:200].lower().startswith(('<!doctype html', '<html')) or '<body' in text[:5000].lower():
            doc = parse_html(text, final, self.reader)
            shown = sum(len(''.join(s.get('x', '') for s in b.get('s', []))) for b in doc['blocks'])
            doc['needs_js'] = shown < 400 and '<script' in text.lower()
            return doc
        return text_doc(text, final)

    def submit(self, doc, widget, values):
        """values: {widget id: value}. Returns (url, data) to load."""
        fi = widget.get('form', -1)
        if fi < 0 or fi >= len(doc.get('forms', [])):
            return None, None
        form = doc['forms'][fi]
        pairs = []
        for f in form['fields']:
            name = f.get('name')
            if not name:
                continue
            kind = f['w']
            if kind == 'hidden':
                pairs.append((name, f['val']))
            elif kind == 'input' or kind == 'select':
                pairs.append((name, values.get(f['id'], f.get('val', ''))))
            elif kind == 'check':
                if values.get(f['id'], f.get('on')):
                    pairs.append((name, f['val']))
            elif kind == 'button' and f is widget and f.get('name'):
                pairs.append((name, f.get('val', '')))
        q = urllib.parse.urlencode(pairs)
        if form['method'] == 'post':
            return form['action'], q.encode()
        act = form['action'].split('#')[0]
        return act.split('?')[0] + '?' + q if '?' not in act or True else act + '&' + q, None

    def close(self):
        pass


# ======================================================================= CDP engine
class WebSocket:
    def __init__(self, url, timeout=30):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + ('?' + u.query if u.query else '')
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n') % (path, u.hostname, u.port or 80, key)
        self.sock.sendall(req.encode())
        resp = b''
        while b'\r\n\r\n' not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('websocket handshake failed')
            resp += chunk
        head, self.rest = resp.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise ConnectionError(head.split(b'\r\n')[0].decode())
        self.lock = threading.Lock()

    def _recv_exact(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(max(65536, n - len(self.rest)))
            if not chunk:
                raise ConnectionError('websocket closed')
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack('>H', n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack('>Q', n)
        mask = os.urandom(4)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data)) if n < 4096 else _mask(data, mask)
        with self.lock:
            self.sock.sendall(bytes(hdr) + masked)

    def recv(self):
        msg = b''
        while True:
            b0, b1 = self._recv_exact(2)
            op = b0 & 0x0f
            n = b1 & 0x7f
            if n == 126:
                n = struct.unpack('>H', self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._recv_exact(8))[0]
            if b1 & 0x80:
                m = self._recv_exact(4)
                payload = _mask(self._recv_exact(n), m)
            else:
                payload = self._recv_exact(n)
            if op == 9:   # ping
                continue
            if op == 8:
                raise ConnectionError('websocket closed by peer')
            msg += payload
            if b0 & 0x80:
                return msg.decode('utf-8', 'replace')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _mask(data, mask):
    n = len(data)
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(m, 'big')).to_bytes(n, 'big')


EXTRACT_JS = r"""
(() => {
  const READER = __READER__;
  const JUNK = /(^|[-_ ])(nav|navbar|navbox|menu|sidebar|footer|cookie|consent|banner|share|social|toc|mw-editsection|noprint|breadcrumb|skip|related|advert|ads?|promo|newsletter|vector-|mw-jump)([-_ ]|$)/i;
  const out = []; let cur = null; let wid = 0;
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','SVG','HEAD','IFRAME','CANVAS','OBJECT','EMBED','VIDEO','AUDIO','MATH','DIALOG']);
  const BLOCK = new Set(['P','DIV','SECTION','ARTICLE','MAIN','HEADER','FOOTER','NAV','ASIDE','FORM','FIGURE','FIGCAPTION','DL','DT','DD','ADDRESS','DETAILS','SUMMARY','FIELDSET','TR','TABLE','TBODY','CENTER','LABEL','LEGEND','CAPTION']);
  const flush = () => { if (cur && cur.s.some(x => x.w || x.x.trim())) out.push(cur); cur = null; };
  const ensure = (st) => { if (!cur) cur = {t: st.q ? 'q' : 'p', s: []}; return cur; };
  const reg = (el) => { wid++; el.setAttribute('data-twid', String(wid)); return wid; };
  const hidden = (el) => { const cs = getComputedStyle(el); return cs.display === 'none' || cs.visibility === 'hidden' || (cs.opacity === '0' && el.tagName !== 'INPUT') || el.getAttribute('aria-hidden') === 'true' && !el.querySelector('a,input,button'); };
  const span = (st, text) => { const s = {x: text}; if (st.h) { s.h = st.h; s.id = st.lid; } if (st.b) s.b = 1; if (st.i) s.i = 1; if (st.c) s.c = 1; ensure(st).s.push(s); };
  const dataTable = (t) => t.querySelector('th') && !t.querySelector('table') && t.rows.length < 300;
  function walk(n, st) {
    if (n.nodeType === 3) { let t = n.textContent.replace(/\s+/g, ' '); if (!t || (t === ' ' && (!cur || !cur.s.length))) return; span(st, t); return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName.toUpperCase();
    if (SKIP.has(tag)) return;
    if (hidden(n)) return;
    if (READER && n !== root) {
      if (['NAV','ASIDE','FOOTER'].includes(tag)) return;
      const role = n.getAttribute('role') || '';
      if (['navigation','banner','contentinfo','complementary','search'].includes(role)) return;
      const cid = ((typeof n.className === 'string' ? n.className : '') + ' ' + (n.id || ''));
      if (cid && JUNK.test(cid) && !n.querySelector('h1')) return;
    }
    if (n.shadowRoot) { for (const c of n.shadowRoot.childNodes) walk(c, st); }
    if (tag === 'A') { st = Object.assign({}, st, {h: n.href || null, lid: reg(n)}); }
    else if (tag === 'B' || tag === 'STRONG') st = Object.assign({}, st, {b: 1});
    else if (tag === 'I' || tag === 'EM') st = Object.assign({}, st, {i: 1});
    else if (tag === 'CODE' || tag === 'KBD' || tag === 'SAMP') st = Object.assign({}, st, {c: 1});
    if (/^H[1-6]$/.test(tag)) { flush(); cur = {t: 'h', l: +tag[1], s: []}; for (const c of n.childNodes) walk(c, st); flush(); return; }
    if (tag === 'UL' || tag === 'OL') { flush(); const d = (st.d || 0) + 1; let i = 0;
      for (const c of n.children) { if (c.tagName === 'LI') { if (hidden(c)) continue; i++; flush(); cur = {t: 'li', d: d, n: tag === 'OL' ? i : null, s: []};
        for (const cc of c.childNodes) walk(cc, Object.assign({}, st, {d: d})); flush(); } else walk(c, Object.assign({}, st, {d: d})); }
      flush(); return; }
    if (tag === 'PRE') { flush(); out.push({t: 'pre', x: n.innerText}); return; }
    if (tag === 'HR') { flush(); out.push({t: 'hr'}); return; }
    if (tag === 'BR') { span(st, '\n'); return; }
    if (tag === 'BLOCKQUOTE') { flush(); for (const c of n.childNodes) walk(c, Object.assign({}, st, {q: 1})); flush(); return; }
    if (tag === 'TABLE' && dataTable(n)) { flush(); const rows = [...n.rows].map(r => [...r.cells].map(c => c.innerText.replace(/\s+/g, ' ').trim()));
      out.push({t: 'table', rows: rows.filter(r => r.some(c => c))}); return; }
    if (tag === 'IMG' || tag === 'PICTURE') {
      const img = tag === 'IMG' ? n : n.querySelector('img'); if (!img) return;
      const r = img.getBoundingClientRect(); const src = img.currentSrc || img.src;
      if (src && r.width >= 48 && r.height >= 32) { flush(); out.push({t: 'img', src: src, alt: img.alt || '', w: Math.round(r.width), h: Math.round(r.height), href: st.h || null, id: st.lid}); }
      else if (img.alt && r.width >= 16) span(st, '[' + img.alt + ']');
      return; }
    if (tag === 'INPUT') { const t = (n.type || 'text').toLowerCase(); if (t === 'hidden') return;
      if (['submit','button','image','reset'].includes(t)) { ensure(st).s.push({w: 'button', id: reg(n), x: n.value || n.alt || 'Submit'}); return; }
      if (t === 'checkbox' || t === 'radio') { ensure(st).s.push({w: 'check', id: reg(n), on: n.checked, radio: t === 'radio'}); return; }
      ensure(st).s.push({w: 'input', id: reg(n), val: n.value || '', ph: n.placeholder || n.getAttribute('aria-label') || '', pw: t === 'password'}); return; }
    if (tag === 'TEXTAREA') { ensure(st).s.push({w: 'input', id: reg(n), val: n.value || '', ph: n.placeholder || '', multi: 1}); return; }
    if (tag === 'SELECT') { const o = n.options[n.selectedIndex]; ensure(st).s.push({w: 'select', id: reg(n), x: o ? o.text : '', opts: [...n.options].map(o => ({v: o.value, x: o.text}))}); return; }
    if (tag === 'BUTTON' || n.getAttribute('role') === 'button' && !n.querySelector('a,button')) { const t = n.innerText.replace(/\s+/g, ' ').trim() || n.getAttribute('aria-label') || '';
      if (t) { ensure(st).s.push({w: 'button', id: reg(n), x: t.slice(0, 40)}); } return; }
    if (tag === 'TR') { flush(); let ci = 0; for (const c of n.children) { if (ci++) span(st, ' '); walk(c, Object.assign({}, st, {td: 0})); } flush(); return; }
    if (tag === 'TD' || tag === 'TH') { const s2 = Object.assign({}, st, {td: 1}); for (const c of n.childNodes) walk(c, s2); return; }
    const isBlock = BLOCK.has(tag) || (getComputedStyle(n).display || '').match(/^(block|flex|grid|list-item|table)/);
    if (isBlock && !st.td) flush();
    for (const c of n.childNodes) walk(c, st);
    if (isBlock && !st.td) flush();
  }
  let root = document.body;
  if (READER) {
    for (const sel of ['#mw-content-text', 'article', 'main', '[role=main]', '#content', '#main', '.post', '.article']) {
      const c = document.querySelector(sel);
      if (c && c.innerText && c.innerText.length > 300) { root = c; break; }
    }
    const h1 = document.querySelector('h1');
    if (root !== document.body && h1 && !root.contains(h1)) { out.push({t: 'h', l: 1, s: [{x: h1.innerText.trim()}]}); }
  }
  walk(root, {});
  flush();
  return JSON.stringify({title: document.title, url: location.href, blocks: out});
})()
"""


class Chromium:
    """One shared headless browser process for all Browser windows."""
    _inst = None
    _lock = threading.Lock()

    @classmethod
    def get(cls, binary=''):
        with cls._lock:
            if cls._inst is None or not cls._inst.alive():
                cls._inst = Chromium(binary)
            return cls._inst

    @staticmethod
    def find(binary=''):
        for b in ([binary] if binary else []) + ['chromium', 'chromium-browser', 'brave-browser', 'brave',
                                                  'google-chrome', 'google-chrome-stable']:
            if b and (os.path.isfile(b) or which(b)):
                return b if os.path.isfile(b) else which(b)
        return None

    def __init__(self, binary=''):
        exe = self.find(binary)
        if not exe:
            raise RuntimeError('no Chromium/Brave found (set it in Settings > Web or use the lite engine)')
        self.dir = os.path.join(CACHE_DIR, 'chromium-profile')
        os.makedirs(self.dir, exist_ok=True)
        port_file = os.path.join(self.dir, 'DevToolsActivePort')
        self.proc = None
        # reuse a still running instance (e.g. left over from a crashed session)
        try:
            with open(port_file) as f:
                port = int(f.readline().strip())
            urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=1).read()
            self.port = port
            return
        except (OSError, ValueError):
            pass
        try:
            os.remove(port_file)
        except OSError:
            pass
        args = [exe, '--headless=new', '--remote-debugging-port=0', '--remote-allow-origins=*',
                '--user-data-dir=' + self.dir, '--no-first-run', '--no-default-browser-check', '--disable-gpu',
                '--mute-audio', '--window-size=1200,1600', '--disable-extensions', '--hide-scrollbars',
                '--blink-settings=imagesEnabled=true', 'about:blank']
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        self.port = None
        while time.time() - t0 < 20:
            if os.path.exists(port_file):
                try:
                    with open(port_file) as f:
                        self.port = int(f.readline().strip())
                    break
                except (OSError, ValueError):
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError('browser exited during start')
            time.sleep(0.1)
        if not self.port:
            raise RuntimeError('browser did not open a DevTools port')

    def alive(self):
        if self.proc is not None:
            return self.proc.poll() is None
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/json/version' % self.port, timeout=1).read()
            return True
        except OSError:
            return False

    def new_target(self):
        url = 'http://127.0.0.1:%d/json/new?about:blank' % self.port
        req = urllib.request.Request(url, method='PUT')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())

    def close_target(self, tid):
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/json/close/%s' % (self.port, tid), timeout=3).read()
        except Exception:
            pass

    def shutdown(self):
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, 15)
            except OSError:
                pass
            return
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % self.port, timeout=2) as r:
                ws = WebSocket(json.loads(r.read())['webSocketDebuggerUrl'], timeout=3)
            ws.send(json.dumps({'id': 1, 'method': 'Browser.close'}))
            ws.close()
        except Exception:
            pass


class CDPEngine:
    name = 'chromium'

    def __init__(self, binary=''):
        self.browser = Chromium.get(binary)
        t = self.browser.new_target()
        self.tid = t['id']
        self.ws = WebSocket(t['webSocketDebuggerUrl'])
        self.mid = 0
        self.lock = threading.Lock()
        self.call('Page.enable')
        self.call('Runtime.enable')
        self.call('Emulation.setDeviceMetricsOverride', {'width': 1200, 'height': 1600, 'deviceScaleFactor': 1,
                                                         'mobile': False})

    def call(self, method, params=None, timeout=30):
        with self.lock:
            self.mid += 1
            mid = self.mid
            self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
            self.ws.sock.settimeout(timeout)
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get('id') == mid:
                    if 'error' in msg:
                        raise RuntimeError(msg['error'].get('message', 'CDP error'))
                    return msg.get('result', {})

    def evaluate(self, js, timeout=30):
        r = self.call('Runtime.evaluate', {'expression': js, 'returnByValue': True, 'awaitPromise': True}, timeout)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'].get('text', 'JS error'))
        return r.get('result', {}).get('value')

    def wait_ready(self, timeout=15):
        t0 = time.time()
        time.sleep(0.3)
        while time.time() - t0 < timeout:
            try:
                if self.evaluate('document.readyState', 5) == 'complete':
                    break
            except RuntimeError:
                pass
            time.sleep(0.25)
        time.sleep(0.6)   # let client-side rendering settle

    reader = True

    def extract(self):
        doc = json.loads(self.evaluate(EXTRACT_JS.replace('__READER__', 'true' if self.reader else 'false'), 30) or '{}')
        doc.setdefault('blocks', [])
        doc['forms'] = []
        doc['widgets'] = {}
        return doc

    def load(self, url, data=None):
        self.call('Page.navigate', {'url': url})
        self.wait_ready()
        return self.extract()

    def action(self, kind, wid, value=None):
        sel = 'document.querySelector(\'[data-twid="%d"]\')' % wid
        if kind == 'click':
            js = '(() => { const e = %s; if (!e) return 0; e.scrollIntoView({block: "center"}); e.click(); return 1; })()' % sel
        elif kind == 'set':
            js = ('(() => { const e = %s; if (!e) return 0; e.focus(); const p = Object.getPrototypeOf(e); '
                  'const d = Object.getOwnPropertyDescriptor(p, "value"); if (d && d.set) d.set.call(e, %s); else e.value = %s; '
                  'e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); return 1; })()'
                  % (sel, json.dumps(value), json.dumps(value)))
        elif kind == 'submit':
            js = ('(() => { const e = %s; if (!e) return 0; e.focus(); if (e.form) { if (e.form.requestSubmit) e.form.requestSubmit(); else e.form.submit(); return 2; } return 1; })()' % sel)
        elif kind == 'select':
            js = ('(() => { const e = %s; if (!e) return 0; e.value = %s; e.dispatchEvent(new Event("change", {bubbles: true})); return 1; })()'
                  % (sel, json.dumps(value)))
        else:
            return None
        r = self.evaluate(js)
        if kind == 'submit' and r == 1:
            for t in ('keyDown', 'keyUp'):
                self.call('Input.dispatchKeyEvent', {'type': t, 'key': 'Enter', 'code': 'Enter',
                                                     'windowsVirtualKeyCode': 13, 'text': '\r' if t == 'keyDown' else ''})
        if kind in ('click', 'submit', 'select'):
            self.wait_ready(10)
            return self.extract()
        return None

    def back(self):
        self.evaluate('history.back()')
        self.wait_ready()
        return self.extract()

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.browser.close_target(self.tid)


def shutdown_all():
    if Chromium._inst is not None:
        Chromium._inst.shutdown()
        Chromium._inst = None
