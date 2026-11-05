"""Web engines for the Browser app.

lite  : urllib + html.parser (no JavaScript; fast, works everywhere) - the text view
Page  : headless Chromium/Brave driven over the DevTools protocol, as pixels - the
        graphics mode for pages that need JavaScript

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


# Requests nobody misses: ads, trackers and consent scripts. On a slow machine they
# cost most of the load time (fonts and pictures are wanted: this is a real view).
AD_URLS = [
    '*doubleclick.net*', '*googlesyndication.com*', '*google-analytics.com*', '*googletagmanager.com*',
    '*googleadservices.com*', '*adservice.google.*', '*connect.facebook.net*', '*facebook.com/tr*',
    '*hotjar.com*', '*criteo.*', '*taboola.com*', '*outbrain.com*', '*amazon-adsystem.com*',
    '*scorecardresearch.com*', '*adnxs.com*', '*pubmatic.com*', '*rubiconproject.com*',
    '*moatads.com*', '*chartbeat.*', '*newrelic.com*', '*nr-data.net*', '*sentry.io*',
]


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
            raise RuntimeError('no Chromium/Brave found (install chromium or set it in Settings > Web)')
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
                '--mute-audio', '--window-size=1024,768', '--disable-extensions',
                '--autoplay-policy=user-gesture-required',
                # without a desktop session the keyring lookup blocks the first request for the
                # 25 s D-Bus timeout; cookies are encrypted with the built-in key instead
                '--password-store=basic',
                # no background traffic competing with the page on slow machines
                '--disable-background-networking', '--disable-component-update', '--disable-sync',
                '--disable-default-apps', '--no-pings', '--disable-breakpad', '--disable-notifications',
                '--disable-features=Translate,OptimizationHints,MediaRouter,DialMediaRouteProvider,'
                'InterestFeedContentSuggestions,AutofillServerCommunication',
                'about:blank']
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


# Runs in every document before its own scripts: keeps popups in this one tab (a
# headless browser has no second window to show) and reports focus, link under the
# pointer and title through the __mc binding.
PAGE_JS = r"""
(() => {
  if (window.__mcHooked) return; window.__mcHooked = 1;
  const send = (k, v) => { try { window.__mc(JSON.stringify({k: k, v: v})); } catch (e) {} };
  window.open = function (u) { if (u) location.href = u; return window; };
  document.addEventListener('click', e => {
    const a = e.target && e.target.closest && e.target.closest('a[target]');
    if (a) a.target = '_self';
  }, true);
  const editing = () => {
    let e = document.activeElement;
    while (e && e.shadowRoot && e.shadowRoot.activeElement) e = e.shadowRoot.activeElement;
    if (!e) return false;
    const t = e.tagName, ty = (e.type || '').toLowerCase();
    return !!(e.isContentEditable || t === 'TEXTAREA' || t === 'SELECT' || t === 'IFRAME' ||
      t === 'EMBED' || t === 'OBJECT' ||
      (t === 'INPUT' && !/^(button|submit|checkbox|radio|reset|image|range|color|file|hidden)$/.test(ty)));
  };
  let last = null;
  const focus = () => { const v = editing(); if (v !== last) { last = v; send('edit', v); } };
  document.addEventListener('focusin', focus, true);
  document.addEventListener('focusout', () => setTimeout(focus, 0), true);
  let hov = '';
  document.addEventListener('mouseover', e => {
    const a = e.target && e.target.closest && e.target.closest('a[href]');
    const h = a ? a.href : '';
    if (h !== hov) { hov = h; send('hover', h); }
  }, true);
  const title = () => send('title', document.title || '');
  window.addEventListener('DOMContentLoaded', () => { title(); focus(); });
  window.addEventListener('load', title);
  window.addEventListener('hashchange', title);
  window.addEventListener('popstate', () => setTimeout(title, 50));
})();
"""

COPY_JS = r"""(() => {
  const e = document.activeElement;
  if (e && (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && e.selectionStart != null)
    return e.value.slice(e.selectionStart, e.selectionEnd);
  return String(window.getSelection() || '');
})()"""

BLUR_JS = r"""(() => { let e = document.activeElement;
  if (e && e.blur) e.blur(); return 1; })()"""

# monocell key name -> (DOM key, windows virtual key code, text it types)
SPECIAL_KEYS = {
    'enter': ('Enter', 13, '\r'), 'tab': ('Tab', 9, ''), 'backspace': ('Backspace', 8, ''),
    'esc': ('Escape', 27, ''), 'space': (' ', 32, ' '), ' ': (' ', 32, ' '),
    'up': ('ArrowUp', 38, ''), 'down': ('ArrowDown', 40, ''), 'left': ('ArrowLeft', 37, ''),
    'right': ('ArrowRight', 39, ''), 'home': ('Home', 36, ''), 'end': ('End', 35, ''),
    'pgup': ('PageUp', 33, ''), 'pgdn': ('PageDown', 34, ''), 'insert': ('Insert', 45, ''),
    'delete': ('Delete', 46, ''),
}
for _i in range(1, 13):
    SPECIAL_KEYS['f%d' % _i] = ('F%d' % _i, 111 + _i, '')
PUNCT_VK = {';': 186, '=': 187, ',': 188, '-': 189, '.': 190, '/': 191, '`': 192,
            '[': 219, '\\': 220, ']': 221, "'": 222}
MOD_BITS = {'alt': 1, 'ctrl': 2, 'meta': 4, 'shift': 8}


def split_key(name):
    """'ctrl+shift+left' -> ({'ctrl', 'shift'}, 'left'); '+' and 'ctrl++' stay intact."""
    mods = set()
    while True:
        for m in MOD_BITS:
            if name.startswith(m + '+') and len(name) > len(m) + 1:
                mods.add(m)
                name = name[len(m) + 1:]
                break
        else:
            return mods, name


def key_events(name):
    """Input.dispatchKeyEvent parameters (down, up) for one monocell key, [] if unknown."""
    mods, base = split_key(name)
    bits = sum(MOD_BITS[m] for m in mods)
    if base in SPECIAL_KEYS:
        key, vk, text = SPECIAL_KEYS[base]
        code = 'Space' if key == ' ' else key
    elif len(base) == 1:
        key, text = base, base
        if base.isalpha() and base.isascii():
            vk, code = ord(base.upper()), 'Key' + base.upper()
            if 'shift' in mods:
                key = text = base.upper()
        elif base.isdigit():
            vk, code = ord(base), 'Digit' + base
        else:
            vk, code = PUNCT_VK.get(base, 0), ''
    else:
        return []
    if mods & {'ctrl', 'alt', 'meta'}:
        text = ''                               # a shortcut, nothing is typed
    down = {'type': 'keyDown' if text else 'rawKeyDown', 'key': key, 'code': code,
            'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk, 'modifiers': bits}
    if text:
        down['text'] = down['unmodifiedText'] = text
    up = {'type': 'keyUp', 'key': key, 'code': code, 'windowsVirtualKeyCode': vk,
          'nativeVirtualKeyCode': vk, 'modifiers': bits}
    return [down, up]


class Page:
    """One headless Chromium tab, seen as pixels: the graphics mode of the browser.

    Frames come by themselves (Page.startScreencast: only when something on the page
    changed), input goes in as real mouse and key events. A reader thread owns the
    socket: replies wake the calling thread, events go to `on_event(name, params)` -
    which runs on that reader thread and must never call() (that would wait for
    itself); send() is fine there.
    """

    def __init__(self, binary='', size=(1024, 748), on_event=None):
        self.browser = Chromium.get(binary)
        t = self.browser.new_target()
        self.tid = t['id']
        self.ws = WebSocket(t['webSocketDebuggerUrl'])
        self.ws.sock.settimeout(None)
        self.on_event = on_event or (lambda name, params: None)
        self.mid = 0
        self.lock = threading.Lock()
        self.waiting = {}
        self.dead = ''
        self.closed = False
        self.size = size
        self.cast = None
        self.main_frame = None
        self.reader = threading.Thread(target=self._read, name='cdp-reader', daemon=True)
        self.reader.start()
        self.call('Page.enable')
        self.call('Runtime.enable')
        self.call('Runtime.addBinding', {'name': '__mc'})
        self.call('Page.addScriptToEvaluateOnNewDocument', {'source': PAGE_JS})
        try:
            self.call('Network.enable')
            self.call('Network.setBlockedURLs', {'urls': AD_URLS})
        except RuntimeError:
            pass
        try:
            self.call('Emulation.setFocusEmulationEnabled', {'enabled': True})
        except RuntimeError:
            pass
        self.resize(*size)
        try:
            self.main_frame = self.call('Page.getFrameTree')['frameTree']['frame']['id']
        except (RuntimeError, KeyError, TypeError):
            pass

    # ---------------------------------------------------------- protocol
    def _read(self):
        while not self.closed:
            try:
                msg = json.loads(self.ws.recv())
            except Exception as e:                 # socket closed, browser gone
                self.dead = str(e) or 'connection lost'
                break
            if 'id' in msg:
                w = self.waiting.pop(msg['id'], None)
                if w:
                    w[1].append(msg)
                    w[0].set()
                continue
            name, params = msg.get('method', ''), msg.get('params', {})
            if name == 'Page.javascriptDialogOpening':
                # alert/confirm/prompt would stop the page: answer at once
                self.send('Page.handleJavaScriptDialog',
                          {'accept': True, 'promptText': params.get('defaultPrompt', '')})
            try:
                self.on_event(name, params)
            except Exception:
                pass
        for ev, box in list(self.waiting.values()):
            ev.set()
        if not self.closed:
            try:
                self.on_event('closed', {'reason': self.dead})
            except Exception:
                pass

    def _next(self):
        with self.lock:
            self.mid += 1
            return self.mid

    def send(self, method, params=None):
        """Fire and forget (mouse moves, frame acks): no waiting for the reply."""
        if self.dead or self.closed:
            return
        try:
            self.ws.send(json.dumps({'id': self._next(), 'method': method, 'params': params or {}}))
        except OSError as e:
            self.dead = str(e)

    def call(self, method, params=None, timeout=20):
        if self.dead:
            raise RuntimeError(self.dead)
        mid = self._next()
        ev, box = threading.Event(), []
        self.waiting[mid] = (ev, box)
        try:
            self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        except OSError as e:
            self.waiting.pop(mid, None)
            raise RuntimeError(str(e))
        if not ev.wait(timeout):
            self.waiting.pop(mid, None)
            raise RuntimeError('%s: no answer' % method)
        if not box:
            raise RuntimeError(self.dead or 'connection lost')
        msg = box[0]
        if 'error' in msg:
            raise RuntimeError(msg['error'].get('message', 'CDP error'))
        return msg.get('result', {})

    def evaluate(self, js, timeout=20):
        r = self.call('Runtime.evaluate', {'expression': js, 'returnByValue': True,
                                           'awaitPromise': True}, timeout)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'].get('text', 'JS error'))
        return r.get('result', {}).get('value')

    # ---------------------------------------------------------- view
    def resize(self, w, h):
        self.size = (int(w), int(h))
        self.call('Emulation.setDeviceMetricsOverride', {
            'width': self.size[0], 'height': self.size[1], 'deviceScaleFactor': 1, 'mobile': False})

    def start_screencast(self, quality=80, max_w=None, max_h=None):
        """Frames on every repaint. max_w/max_h let the browser downscale them itself:
        the retro look needs a quarter of the screen, not a full one per frame."""
        self.cast = (int(max_w or self.size[0]), int(max_h or self.size[1]))
        try:                   # a second start while running is an error
            self.call('Page.stopScreencast', timeout=5)
        except RuntimeError:
            pass
        self.call('Page.startScreencast', {'format': 'jpeg', 'quality': int(quality),
                                           'maxWidth': self.cast[0], 'maxHeight': self.cast[1],
                                           'everyNthFrame': 1})

    def ack(self, session_id):
        self.send('Page.screencastFrameAck', {'sessionId': session_id})

    def screenshot(self, quality=80):
        """One JPEG of the viewport now (when no screencast frame came)."""
        r = self.call('Page.captureScreenshot', {'format': 'jpeg', 'quality': int(quality)}, 20)
        return base64.b64decode(r.get('data', ''))

    # ---------------------------------------------------------- navigation
    def navigate(self, url):
        r = self.call('Page.navigate', {'url': url}, 30)
        if r.get('errorText'):
            raise RuntimeError(r['errorText'])
        return r

    def reload(self):
        self.call('Page.reload', {'ignoreCache': False})

    def history(self, delta):
        """One step back (-1) or forward (+1); False when there is none."""
        h = self.call('Page.getNavigationHistory')
        i = h.get('currentIndex', 0) + delta
        entries = h.get('entries', [])
        if not 0 <= i < len(entries):
            return False
        self.call('Page.navigateToHistoryEntry', {'entryId': entries[i]['id']})
        return True

    def state(self):
        """(url, title) of the page now."""
        v = self.evaluate('JSON.stringify([location.href, document.title])', 5)
        try:
            u, t = json.loads(v or '[]')
            return u, t
        except ValueError:
            return '', ''

    def copy_selection(self):
        return self.evaluate(COPY_JS, 5) or ''

    def blur(self):
        self.evaluate(BLUR_JS, 5)

    # ---------------------------------------------------------- input (never waits)
    def mouse(self, kind, x, y, button='none', clicks=1, buttons=0, modifiers=0):
        """kind: mouseMoved | mousePressed | mouseReleased."""
        self.send('Input.dispatchMouseEvent', {
            'type': kind, 'x': int(x), 'y': int(y), 'button': button, 'buttons': buttons,
            'clickCount': clicks, 'modifiers': modifiers})

    def wheel(self, x, y, dy, dx=0):
        self.send('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': int(x), 'y': int(y),
                                               'deltaX': dx, 'deltaY': dy})

    def key(self, name):
        evs = key_events(name)
        for e in evs:
            self.send('Input.dispatchKeyEvent', e)
        return bool(evs)

    def insert_text(self, text):
        self.send('Input.insertText', {'text': text})

    def close(self):
        self.closed = True
        try:
            self.send('Page.stopScreencast')
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass
        self.browser.close_target(self.tid)


def shutdown_all():
    if Chromium._inst is not None:
        Chromium._inst.shutdown()
        Chromium._inst = None
