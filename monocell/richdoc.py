"""Rich-text document model and WYSIWYG cell layout for the Writer.

The file format stays the Writer's Markdown dialect (see writer_doc), so a
document can still be edited in any text editor and diffed in git. In memory
it is a list of paragraphs (one per source line) with a style per character,
plus floating pictures that are anchored to a paragraph like in Word:

    ![alt](path){x=4 dy=1 w=30 wrap=square}     <- line before the anchor

The layout puts everything on the cell grid as it will look:

* large text (<size2>/<size3>, headings H1/H2) is set with FIGlet fonts,
  mixed with normal text on a shared baseline,
* underlined text gets a row of '-' (double: '=') below its line; the lines
  after it move down,
* pictures float: text wraps around them (square) or skips them (block).

Every character gets a box (x0, x1, y, h), so the editor can put the cursor,
the selection and mouse clicks straight onto the rendered page.

Document settings (fonts of the large sizes, paper, pitch, header/footer) live
in an HTML comment on the first line, invisible in other Markdown viewers:

    <!-- writer: font3=block3d paper=a4 cpi=10 footer="|Seite {page}|" -->

Paragraph extras: a trailing {.center} / {.right} / {.justify} aligns it, a line
<pagebreak> starts a new sheet, lines starting with '|' form a table.
"""
import itertools
import os
import re
import shlex
from collections import namedtuple

from . import figlet, imaging, util
from .screen import BOLD
from .writer_doc import HEAD_RE, IMAGE_RE, LIST_RE, QUOTE_RE, RULE_RE
from .writer_doc import parse_inline as _parse_inline


def parse_inline(text):
    return _parse_inline(text, italic=False)      # no italic: the printer has none

ITALIC = 4          # attribute flag: SGR 3 where the terminal has italics

Style = namedtuple('Style', 'bold italic code ul size link')
PLAIN = Style(False, False, False, 0, 1, '')

_rev = itertools.count(1)
_uid = itertools.count(1)

FONT_DEFAULTS = {2: 'graceful', 3: 'block3d'}
HEAD_SIZE = {1: 3, 2: 2}
MIN_SEG = 4          # narrower gaps next to a picture stay empty
MAX_FLOAT_H = 80
ALIGNS = ('left', 'center', 'right', 'justify')
ALIGN_RE = re.compile(r'\s*\{\.(left|center|right|justify)\}\s*$')
PAGEBREAK_RE = re.compile(r'^\s*<pagebreak\s*/?>\s*$', re.I)
SETTINGS_RE = re.compile(r'^\s*<!--\s*writer:(.*?)-->\s*$')
TABLE_RE = re.compile(r'^\s*\|')
TSEP_CELL_RE = re.compile(r'^\s*(:?)-{1,}(:?)\s*$')

# ------------------------------------------------------------ page geometry
# paper: (width, height) in inches, label. The X24E prints an 8 inch line.
PAPERS = {
    'a4': (8.27, 11.69, 'A4'),
    'letter': (8.5, 11.0, 'Letter'),
    'fanfold12': (9.5, 12.0, 'Fanfold 12"'),
    'fanfold11': (9.5, 11.0, 'Fanfold 11"'),
}
PAPER_ORDER = ['a4', 'letter', 'fanfold12', 'fanfold11']
PRINT_LINE_IN = 8.0
DOC_DEFAULTS = {
    'font2': '', 'font3': '',          # '' = the default (cfg / FONT_DEFAULTS)
    'paper': 'a4', 'cpi': '10', 'lpi': '6',
    'left': '', 'right': '', 'top': '', 'bottom': '',     # '' = automatic margins
    'header': '', 'footer': '|Seite {page} von {pages}|',
}


def setting(settings, key, cfg=None):
    v = (settings or {}).get(key)
    if v is None or v == '':
        if key in ('font2', 'font3') and cfg is not None:
            v = cfg.get('writer_' + key) or ''
        if not v:
            v = DOC_DEFAULTS.get(key, '')
    if key in ('font2', 'font3'):
        v = figlet.canonical(v or FONT_DEFAULTS[int(key[-1])])
    return v


class Page:
    """Sheet geometry of a document in characters and lines."""

    def __init__(self, settings=None):
        s = settings or {}
        self.paper = s.get('paper') if s.get('paper') in PAPERS else 'a4'
        self.cpi = 12 if str(s.get('cpi')) == '12' else 10
        self.lpi = 8 if str(s.get('lpi')) == '8' else 6
        pw, ph, self.paper_label = PAPERS[self.paper]
        self.cols = int(round(min(PRINT_LINE_IN, pw) * self.cpi))
        self.lines = int(ph * self.lpi + 0.01)

        def num(key, auto, lo, hi):
            try:
                v = int(s.get(key))
            except (TypeError, ValueError):
                v = auto
            return max(lo, min(hi, v))
        auto_lr = int(round(0.6 * self.cpi))
        auto_tb = 3 if self.lpi == 6 else 4
        self.left = num('left', auto_lr, 0, self.cols // 3)
        self.right = num('right', auto_lr, 0, self.cols // 3)
        self.top = num('top', auto_tb, 0, self.lines // 4)
        self.bottom = num('bottom', auto_tb, 0, self.lines // 4)
        self.text_w = max(12, self.cols - self.left - self.right)
        self.rows = max(4, self.lines - self.top - self.bottom)
        self.header = s.get('header', DOC_DEFAULTS['header'])
        self.footer = s.get('footer', DOC_DEFAULTS['footer'])

    @property
    def key(self):
        return (self.paper, self.cpi, self.lpi, self.left, self.right, self.top, self.bottom,
                self.header, self.footer)

    def describe(self):
        return '%s  %d cpi  %d lpi  %dx%d' % (self.paper_label, self.cpi, self.lpi,
                                             self.cols, self.lines)

    def margin_line(self, which, page, pages, title='', date=''):
        """Header/footer text for one sheet: 'left|centre|right' with fields."""
        tpl = self.header if which == 'header' else self.footer
        if not tpl:
            return ''
        parts = tpl.split('|')
        if len(parts) == 1:
            parts = [parts[0], '', '']
        while len(parts) < 3:
            parts.append('')
        fields = {'page': str(page), 'pages': str(pages), 'title': title, 'date': date}

        def fill(t):
            return re.sub(r'\{(page|pages|title|date)\}', lambda m: fields[m.group(1)], t)
        left, mid, right = (fill(p) for p in parts[:3])
        w = self.text_w
        line = [' '] * w
        for k, c in enumerate(left[:w]):
            line[k] = c
        if mid:
            x = max(0, (w - len(mid)) // 2)
            for k, c in enumerate(mid[:w - x]):
                line[x + k] = c
        if right:
            x = max(0, w - len(right))
            for k, c in enumerate(right[:w - x]):
                line[x + k] = c
        return ''.join(line).rstrip()


def parse_settings(line):
    m = SETTINGS_RE.match(line)
    if not m:
        return None
    out = {}
    try:
        toks = shlex.split(m.group(1))
    except ValueError:
        toks = m.group(1).split()
    for tok in toks:
        if '=' in tok:
            k, v = tok.split('=', 1)
            out[k.strip().lower()] = v
    return out


def settings_line(settings):
    parts = []
    for k in ('font2', 'font3', 'paper', 'cpi', 'lpi', 'left', 'right', 'top', 'bottom',
              'header', 'footer'):
        if k not in settings:
            continue
        v = str(settings[k])
        if k not in ('header', 'footer') and v == '':
            continue
        if re.fullmatch(r'[\w.\-]+', v):
            parts.append('%s=%s' % (k, v))
        else:
            parts.append('%s="%s"' % (k, v.replace('\\', '\\\\').replace('"', '\\"')))
    for k in sorted(settings):                # keys of newer versions survive
        if k not in DOC_DEFAULTS:
            parts.append('%s=%s' % (k, shlex.quote(str(settings[k]))))
    return '<!-- writer: %s -->' % ' '.join(parts)


# ====================================================================== model
class Para:
    """One line of the source: kind p | h | li | quote | code | rule | pagebreak |
    table (cells separated by '|') | tsep (table header rule; marker = aligns)."""
    __slots__ = ('uid', 'rev', 'kind', 'level', 'indent', 'marker', 'lang', 'text', 'st',
                 'align')

    def __init__(self, kind='p', text='', st=None, level=0, indent=0, marker='-', lang='',
                 align=''):
        self.uid = next(_uid)
        self.rev = next(_rev)
        self.kind = kind
        self.level = level
        self.indent = indent
        self.marker = marker
        self.lang = lang
        self.text = text
        self.st = list(st) if st is not None else [PLAIN] * len(text)
        self.align = align

    def touch(self):
        self.rev = next(_rev)

    def set(self, text, st):
        self.text = text
        self.st = list(st)
        self.touch()

    def copy(self):
        p = Para.__new__(Para)
        for k in Para.__slots__:
            setattr(p, k, getattr(self, k))
        p.st = list(self.st)
        return p

    def same(self, o):
        return (self.kind, self.level, self.indent if self.kind == 'li' else 0,
                self.marker if self.kind in ('li', 'tsep') else '', self.text, self.st,
                self.align or '') == \
               (o.kind, o.level, o.indent if o.kind == 'li' else 0,
                o.marker if o.kind in ('li', 'tsep') else '', o.text, o.st, o.align or '')

    def __repr__(self):
        return 'Para(%s%s %r)' % (self.kind, self.level or '', self.text)


class Float:
    """A picture anchored to a paragraph. x/w None = legacy: centred, fitted.

    out: how the printer puts it on paper - 'gfx' as a dithered bit image (the
    24-pin graphics of the Proprinter), 'text' as the characters on the page."""
    __slots__ = ('uid', 'path', 'alt', 'anchor', 'x', 'dy', 'w', 'wrap', 'out')

    def __init__(self, path, alt='', anchor=None, x=None, dy=0, w=None, wrap='block', out='gfx'):
        self.uid = next(_uid)
        self.path = path
        self.alt = alt
        self.anchor = anchor
        self.x = x
        self.dy = dy
        self.w = w
        self.wrap = wrap
        self.out = out

    def copy(self, anchor):
        f = Float.__new__(Float)
        for k in Float.__slots__:
            setattr(f, k, getattr(self, k))
        f.anchor = anchor
        return f


class Doc:
    def __init__(self, paras=None, floats=None, settings=None):
        self.paras = paras or [Para()]
        self.floats = floats or []
        self.settings = dict(settings or {})

    def copy(self):
        paras = [p.copy() for p in self.paras]
        by = {o.uid: n for o, n in zip(self.paras, paras)}
        floats = [f.copy(by.get(f.anchor.uid, paras[0]) if f.anchor else paras[0])
                  for f in self.floats]
        return Doc(paras, floats, self.settings)

    def page(self):
        return Page(self.settings)

    def index(self, para):
        for i, p in enumerate(self.paras):
            if p is para:
                return i
        return -1

    def floats_of(self, para):
        return [f for f in self.floats if f.anchor is para]


# ====================================================================== parse
def spans_to_chars(spans):
    text, st = [], []
    for s in spans:
        style = Style(s.bold, s.italic, s.code, min(3, s.ul), s.size,
                      (s.url or '#') if s.link else '')
        text.append(s.text)
        st.extend([style] * len(s.text))
    return ''.join(text), st


def split_cells(line):
    """'| a | b\\|c |' -> ['a', 'b\\|c'] (escaped pipes stay inside a cell)."""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|') and not s.endswith('\\|'):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if s[i] == '|':
            cells.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    cells.append(''.join(cur).strip())
    return cells


def parse_table_line(line):
    cells = split_cells(line)
    if cells and all(TSEP_CELL_RE.match(c) for c in cells):
        aligns = ''
        for c in cells:
            m = TSEP_CELL_RE.match(c)
            aligns += 'c' if m.group(1) and m.group(2) else ('r' if m.group(2) else 'l')
        return Para('tsep', marker=aligns)
    text, st = [], []
    for n, c in enumerate(cells):
        if n:
            text.append('|')
            st.append(PLAIN)
        t, s = spans_to_chars(parse_inline(c.replace('\\|', '\u2502')))
        t = t.replace('\u2502', '/')            # a pipe inside a cell cannot survive
        text.append(t)
        st.extend(s)
    return Para('table', ''.join(text), st)


def parse_line(line):
    align = ''
    m = ALIGN_RE.search(line)
    if m and not line[:m.start()].endswith('\\') and not TABLE_RE.match(line):
        align = '' if m.group(1) == 'left' else m.group(1)
        line = line[:m.start()]
    if PAGEBREAK_RE.match(line):
        return Para('pagebreak')
    m = HEAD_RE.match(line)
    if m:
        t, st = spans_to_chars(parse_inline(m.group(2)))
        return Para('h', t, st, level=len(m.group(1)), align=align)
    if RULE_RE.match(line):
        return Para('rule')
    if TABLE_RE.match(line):
        return parse_table_line(line)
    m = LIST_RE.match(line)
    if m:
        t, st = spans_to_chars(parse_inline(m.group(3)))
        return Para('li', t, st, indent=len(m.group(1).expandtabs(4)), marker=m.group(2),
                    align=align)
    m = QUOTE_RE.match(line)
    if m:
        t, st = spans_to_chars(parse_inline(m.group(1)))
        return Para('quote', t, st, align=align)
    t, st = spans_to_chars(parse_inline(line))
    return Para('p', t, st, align=align)


def _attrs(s):
    out = {}
    for tok in (s or '').split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            out[k.strip().lower()] = v.strip()
    return out


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse(text):
    lines = text.replace('\r\n', '\n').split('\n')
    paras, floats, pending = [], [], []
    i = 0
    settings = parse_settings(lines[0]) if lines else None
    if settings is not None:
        i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('```'):
            lang = line.strip()[3:].strip()
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                block.append(lines[i])
                i += 1
            i += 1                                   # closing fence
            for n, ln in enumerate(block or ['']):
                p = Para('code', ln.expandtabs(4), lang=lang if n == 0 else '')
                paras.append(p)
                for f in pending:
                    f.anchor = p
                pending = []
            continue
        m = IMAGE_RE.match(line)
        if m:
            a = _attrs(m.group(3))
            w = _int(a.get('w'))
            f = Float(m.group(2).strip(), m.group(1), None,
                      x=_int(a.get('x')), dy=max(0, _int(a.get('dy'), 0)), w=w,
                      wrap=a.get('wrap', 'square' if w else 'block'),
                      out='text' if a.get('print') == 'text' else 'gfx')
            if f.wrap not in ('square', 'block'):
                f.wrap = 'square'
            floats.append(f)
            pending.append(f)
            i += 1
            continue
        p = parse_line(line)
        paras.append(p)
        for f in pending:
            f.anchor = p
        pending = []
        i += 1
    if not paras or pending:
        paras.append(Para())
        for f in pending:
            f.anchor = paras[-1]
    return Doc(paras, floats, settings)


# ================================================================== serialize
TEXT_ESC = '\\`*_[<{'


def _esc(t, level):
    if not level:
        return t
    return ''.join('\\' + c if c in TEXT_ESC else c for c in t)


def _runs(text, st):
    runs = []
    for ch, s in zip(text, st):
        if runs and runs[-1][1] == s:
            runs[-1][0].append(ch)
        else:
            runs.append([[ch], s])
    return [(''.join(c), s) for c, s in runs]


def inline_md(text, st, level=0):
    """Characters + styles -> Writer Markdown. level 0 natural, 1 escaped, 2 tags."""
    out = []
    stack = []

    def close(tag):
        kind, n = tag
        if kind == 'size':
            return '</size%d>' % n
        if kind == 'u':
            return '</u>' if n == 1 else '</u%d>' % n
        return '</%s>' % kind

    def open_(tag):
        kind, n = tag
        if kind == 'size':
            return '<size%d>' % n
        if kind == 'u':
            return '<u>' if n == 1 else '<u%d>' % n
        return '<%s>' % kind

    for t, s in _runs(text, st):
        plain_leaf = not s.code and not s.link and '*' not in t and t.strip()
        sugar = None
        if level < 2 and plain_leaf and s.bold != s.italic:
            sugar = '**' if s.bold else '*'
            if sugar == '*' and (t[0] in ' _' or t[-1] in ' _'):
                sugar = None
        want = []
        if s.size > 1:
            want.append(('size', s.size))
        if s.ul:
            want.append(('u', s.ul))
        if s.bold and not sugar:
            want.append(('b', 0))
        if s.italic and not sugar:
            want.append(('i', 0))
        k = 0
        while k < len(stack) and k < len(want) and stack[k] == want[k]:
            k += 1
        while len(stack) > k:
            out.append(close(stack.pop()))
        for tag in want[k:]:
            out.append(open_(tag))
            stack.append(tag)
        if s.code and '`' not in t:
            leaf = '`%s`' % t
        elif s.link and ']' not in t and '\n' not in t:
            leaf = '[%s](%s)' % (t, s.link)
        elif sugar:
            leaf = sugar + t + sugar
        else:
            leaf = _esc(t, level)
        out.append(leaf)
    while stack:
        out.append(close(stack.pop()))
    return ''.join(out)


def _guard_start(line):
    """A plain paragraph must not look like a heading, list, quote, rule or image."""
    if not line:
        return line
    if (HEAD_RE.match(line) or LIST_RE.match(line) or QUOTE_RE.match(line)
            or RULE_RE.match(line) or IMAGE_RE.match(line) or line.lstrip().startswith('```')
            or re.match(r'^\s*#', line) or TABLE_RE.match(line) or PAGEBREAK_RE.match(line)
            or SETTINGS_RE.match(line)):
        n = len(line) - len(line.lstrip())
        m = re.match(r'\s*\d+([.)])', line)
        if m:                                   # 1. -> 1\.  (digits cannot be escaped)
            return line[:m.end(1) - 1] + '\\' + line[m.end(1) - 1:]
        return line[:n] + '\\' + line[n:]
    return line


def table_md(p, level=0):
    if p.kind == 'tsep':
        cells = []
        for a in p.marker or 'l':
            cells.append({'c': ':---:', 'r': '---:'}.get(a, '---'))
        return '|' + '|'.join(cells) + '|'
    cells, k = [], 0
    for part in p.text.split('|'):
        cells.append(inline_md(part, p.st[k:k + len(part)], level).replace('|', '\\|'))
        k += len(part) + 1
    return '| ' + ' | '.join(cells) + ' |'


def para_md(p, level=0):
    if p.kind in ('table', 'tsep'):
        return table_md(p, level)
    if p.kind == 'pagebreak':
        return '<pagebreak>'
    if p.kind == 'rule':
        return '---'
    body = inline_md(p.text, p.st, level)
    suffix = ' {.%s}' % p.align if p.align in ALIGNS and p.align != 'left' else ''
    if p.kind == 'h':
        return '#' * max(1, min(6, p.level)) + ' ' + body + suffix
    if p.kind == 'li':
        return ' ' * p.indent + p.marker + ' ' + body + suffix
    if p.kind == 'quote':
        return '> ' + body + suffix
    if suffix and not body:
        body = ' '
    return _guard_start(body) + suffix


def para_line(p):
    """Shortest serialisation that parses back to the same paragraph."""
    for level in (0, 1, 2):
        line = para_md(p, level)
        if parse_line(line).same(p):
            return line
    return line


def float_md(f):
    alt = (f.alt or '').replace(']', '').replace('\n', ' ')
    if f.w is None and f.x is None and f.wrap == 'block' and not f.dy and f.out != 'text':
        return '![%s](%s)' % (alt, f.path)
    attrs = []
    if f.x is not None:
        attrs.append('x=%d' % f.x)
    if f.dy:
        attrs.append('dy=%d' % f.dy)
    if f.w is not None:
        attrs.append('w=%d' % f.w)
    attrs.append('wrap=%s' % f.wrap)
    if f.out == 'text':
        attrs.append('print=text')
    return '![%s](%s){%s}' % (alt, f.path, ' '.join(attrs))


def serialize(doc):
    out = []
    byanchor = {}
    for f in doc.floats:
        byanchor.setdefault(id(f.anchor), []).append(f)
    in_code = False
    if doc.settings:
        out.append(settings_line(doc.settings))
    for p in doc.paras:
        if in_code and p.kind != 'code':
            out.append('```')
            in_code = False
        for f in byanchor.get(id(p), ()):
            if in_code:
                out.append('```')
                in_code = False
            out.append(float_md(f))
        if p.kind == 'code':
            if not in_code:
                out.append('```' + (p.lang or ''))
                in_code = True
            out.append(p.text)
            continue
        out.append(para_line(p))
    if in_code:
        out.append('```')
    return '\n'.join(out)


def export_text(doc):
    """Plain text for a .txt export: no markup, lists keep their marker."""
    out = []
    by = {}
    for f in doc.floats:
        by.setdefault(id(f.anchor), []).append(f)
    for p in doc.paras:
        for f in by.get(id(p), ()):
            out.append('[Bild: %s]' % (f.alt or os.path.basename(f.path)))
        if p.kind == 'code':
            out.append(p.text)
        elif p.kind == 'rule':
            out.append('-' * 40)
        elif p.kind == 'pagebreak':
            out.append('\f')
        elif p.kind == 'tsep':
            out.append('-' * 40)
        elif p.kind == 'table':
            out.append(' | '.join(c.strip() for c in p.text.split('|')))
        elif p.kind == 'li':
            out.append(' ' * min(p.indent, 12) + '- ' + p.text)
        elif p.kind == 'quote':
            out.append('> ' + p.text)
        else:
            out.append(p.text)
    return '\n'.join(out)


def plain(doc, a=None, b=None):
    """Plain text of the document (or of a range of positions)."""
    if a is None:
        return '\n'.join(p.text for p in doc.paras)
    (p0, i0), (p1, i1) = a, b
    if p0 == p1:
        return doc.paras[p0].text[i0:i1]
    parts = [doc.paras[p0].text[i0:]]
    parts += [doc.paras[k].text for k in range(p0 + 1, p1)]
    parts.append(doc.paras[p1].text[:i1])
    return '\n'.join(parts)


# ===================================================================== images
class ImageStore:
    """Loads pictures (off the UI thread when a runner is given) and caches cells."""

    def __init__(self, runner=None, on_ready=None):
        self.runner = runner            # fn(work, done) -> runs work in a thread
        self.on_ready = on_ready
        self.imgs = {}                  # path -> (mtime, img or None, error)
        self.pending = set()
        self.cells = {}

    def get(self, path, sync=False):
        try:
            mt = os.stat(path).st_mtime
        except OSError:
            return None, 'missing'
        hit = self.imgs.get(path)
        if hit and hit[0] == mt:
            return hit[1], hit[2]
        if sync or self.runner is None:
            self._load(path, mt)
            hit = self.imgs[path]
            return hit[1], hit[2]
        if path not in self.pending:
            self.pending.add(path)

            def work():
                return self._decode(path)

            def done(res):
                self.pending.discard(path)
                self.imgs[path] = (mt,) + res
                if self.on_ready:
                    self.on_ready()
            self.runner(work, done)
        return None, None

    def _decode(self, path):
        try:
            return imaging.load(path), None
        except Exception as e:          # broken files must not break the document
            return None, str(e) or e.__class__.__name__

    def _load(self, path, mt):
        self.imgs[path] = (mt,) + self._decode(path)

    def size(self, path):
        hit = self.imgs.get(path)
        if hit and hit[1] is not None:
            return hit[1].w, hit[1].h
        return None

    def cells_for(self, wm, path, img, w, h, mode, bg_role='win'):
        th = wm.theme
        allowed = tuple(getattr(getattr(wm, 'term', None), 'allowed_bg', ()))
        key = (path, id(img), w, h, mode, wm.cfg.get('dither'), wm.cfg.get('ascii_charset'),
               tuple(th.rgb), allowed, bg_role)
        rows = self.cells.get(key)
        if rows is None:
            rgb = img.scaled(w, h)
            if mode == 'ink':
                paper = th.c('text', bg_role)
                rows = imaging.render_ink(wm, rgb, w, h, wm.cfg.get('dither'), ink=paper,
                                          paper=paper)
            else:
                rows = imaging.render(wm, rgb, w, h, mode, wm.cfg.get('dither', 'bayer4'))
            if len(self.cells) > 64:
                self.cells.clear()
            self.cells[key] = rows
        return rows


# ===================================================================== layout
class Atom:
    __slots__ = ('i0', 'i1', 'space', 'w', 'B', 'D', 'pieces', 'ul')

    def __init__(self, i0, i1, space, w, B, D, pieces, ul):
        self.i0, self.i1, self.space = i0, i1, space
        self.w, self.B, self.D = w, B, D
        self.pieces = pieces            # [(xoff, Piece)]
        self.ul = ul


class Piece:
    __slots__ = ('i0', 'i1', 'font', 'rows', 'spans', 'w', 'B', 'h')

    def __init__(self, i0, i1, font, rows, spans, w, B, h):
        self.i0, self.i1, self.font = i0, i1, font
        self.rows, self.spans = rows, spans
        self.w, self.B, self.h = w, B, h


class ParaLayout:
    """A laid-out paragraph in coordinates relative to its top row."""
    __slots__ = ('rows', 'lines', 'boxes', 'end', 'height')

    def __init__(self, rows, lines, boxes, end):
        self.rows = rows                # [(chars list, attrs list)]
        self.lines = lines              # [(a, b, y, h)]
        self.boxes = boxes              # per char: (x0, x1, y, h)
        self.end = end                  # caret after the last char: (x, y, h)
        self.height = len(rows)


class Layout:
    """Lays a Doc out on a grid `width` cells wide.

    After run(): rows, tops (per paragraph), paras (ParaLayout), floats
    [(Float, x, y, w, h)], height.
    """
    _cache = {}

    def __init__(self, wm, width, base_dir='', images=None, image_mode=None, vt=None,
                 sync=False, mono=False, page=None, bg_role='win', settings=None):
        self.wm = wm
        self.th = wm.theme
        self.width = max(8, width)
        self.base_dir = base_dir or ''
        self.images = images or ImageStore()
        self.image_mode = 'ink' if mono else (image_mode or wm.cfg.get('image_mode', 'blend'))
        self.mono = mono            # printed page: one ink, no backgrounds
        self.page = page or 0       # text rows per page (0 = endless)
        self.bg_role = bg_role if mono else 'win'
        if vt is None:
            vt = getattr(getattr(wm, 'term', None), 'mode', 'linux') == 'linux'
        self.vt = vt
        self.sync = sync
        self.bg = self.ink(0)
        cfg = wm.cfg
        self.font_names = {2: setting(settings, 'font2', cfg), 3: setting(settings, 'font3', cfg)}
        self.fonts = {k: figlet.load(v) for k, v in self.font_names.items()}
        self._attr = {}
        self.key = (self.width, tuple(sorted(self.font_names.items())), self.vt,
                    tuple(self.th.rgb), id(self.th), self.mono, self.bg_role)
        self.bullet = '-' if (self.vt or getattr(util, 'STRICT_ASCII', False)) else '\u2022'

    # ------------------------------------------------------------ styles
    def ink(self, fl=0):
        """The single ink colour of the printed page (bold/italic still allowed)."""
        return self.th.c('text', self.bg_role, fl)

    def attr(self, s, role=None):
        key = (s, role)
        a = self._attr.get(key)
        if a is not None:
            return a
        if self.mono:
            # the page shows what the printer can do: bold is emphasized print
            # (brighter ink), italic does not exist on a Proprinter (PPDS)
            if s.bold or role == 'accent':
                a = self.th.c('bright', self.bg_role, BOLD)
            else:
                a = self.ink(0)
            self._attr[key] = a
            return a
        fg, bg, fl = 'text', 'win', 0
        if s.code:
            fg, bg = 'cyan', 'field'
        elif s.link:
            fg = 'blue'
        if role:
            fg = role
        if s.bold:
            fl |= BOLD
            if not role and not s.code and not s.link:
                fg = 'bright'
        if s.italic:
            if self.vt:
                if not role and not s.code and not s.link and not s.bold:
                    fg = 'accent2'
            else:
                fl |= ITALIC
        a = self.th.c(fg, bg, fl)
        self._attr[key] = a
        return a

    def role_of(self, p):
        if self.mono:
            return 'accent' if p.kind == 'h' else None
        if p.kind == 'h':
            return 'accent' if p.level <= 1 else ('accent2' if p.level == 2 else 'bright')
        if p.kind == 'quote':
            return 'dim'
        return None

    def size_of(self, p, s):
        if p.kind == 'code':
            return 1
        hs = HEAD_SIZE.get(p.level, 1) if p.kind == 'h' else 1
        return max(s.size, hs)

    def para_font(self, p):
        if p.kind == 'h':
            return self.fonts.get(HEAD_SIZE.get(p.level, 1))
        return None

    # ------------------------------------------------------------ atoms
    def _piece(self, p, i0, i1, font, italic):
        text = p.text[i0:i1]
        if font is None:
            return Piece(i0, i1, None, [text], [(k, k + 1) for k in range(len(text))],
                         len(text), 1, 1)
        rows, spans = font.compose(text)
        w = len(rows[0]) if rows else 0
        if italic:
            hgt = font.height
            shift = [(hgt - 1 - r) // 2 for r in range(hgt)]
            extra = shift[0] if shift else 0
            rows = [' ' * shift[r] + rows[r] + ' ' * (extra - shift[r]) for r in range(hgt)]
            mid = shift[font.baseline - 1] if font.baseline - 1 < hgt else 0
            spans = [(a + mid, b + mid) for a, b in spans]
            w += extra
        return Piece(i0, i1, font, rows, spans, w, font.baseline, font.height)

    def _word(self, p, i0, i1):
        pieces = []
        x = 0
        B = D = 0
        ul = 0
        k = i0
        while k < i1:
            s = p.st[k]
            size = self.size_of(p, s)
            font = self.fonts.get(size) if size > 1 else None
            it = False
            j = k + 1
            while j < i1:
                s2 = p.st[j]
                sz2 = self.size_of(p, s2)
                f2 = self.fonts.get(sz2) if sz2 > 1 else None
                if f2 is not font:
                    break
                j += 1
            pc = self._piece(p, k, j, font, it)
            pieces.append((x, pc))
            x += pc.w
            B = max(B, pc.B)
            D = max(D, pc.h - pc.B)
            k = j
        for k in range(i0, i1):
            if p.st[k].ul:
                ul = 1
                break
        return Atom(i0, i1, False, x, B, D, pieces, ul)

    def _space(self, p, i):
        s = p.st[i]
        size = self.size_of(p, s)
        font = self.fonts.get(size) if size > 1 else None
        if font is None:
            pc = Piece(i, i + 1, None, [' '], [(0, 1)], 1, 1, 1)
        else:
            w = font.space_width
            pc = Piece(i, i + 1, font, [' ' * w] * font.height, [(0, w)], w,
                       font.baseline, font.height)
        return Atom(i, i + 1, True, pc.w, pc.B, pc.h - pc.B, [(0, pc)], 1 if s.ul else 0)

    def atoms(self, p):
        key = ('atoms', p.uid, p.rev, self.key)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        out = []
        text = p.text
        n = len(text)
        if p.kind == 'code':
            out = [self._word(p, i, i + 1) for i in range(n)]
        else:
            i = 0
            while i < n:
                if text[i].isspace():
                    out.append(self._space(p, i))
                    i += 1
                    continue
                j = i
                while j < n and not text[j].isspace():
                    j += 1
                out.append(self._word(p, i, j))
                i = j
        self._remember(key, out)
        return out

    def _remember(self, key, val):
        if len(self._cache) > 6000:
            self._cache.clear()
        self._cache[key] = val

    # ------------------------------------------------------------ flow
    def _segments(self, excl, y, h, need):
        W = self.width
        blocked = sorted((max(0, x0), min(W, x1)) for x0, x1, y0, y1 in excl
                         if y0 < y + h and y1 > y)
        if not blocked:
            return [(0, W)]
        segs = []
        x = 0
        for a, b in blocked:
            if a > x:
                segs.append((x, a))
            x = max(x, b)
        if x < W:
            segs.append((x, W))
        out = [s for s in segs if s[1] - s[0] >= MIN_SEG]
        if out and out[0][1] - out[0][0] < need + 1:
            out = out[1:] if len(out) > 1 else []
        return out

    def _split(self, p, a, avail):
        """Longest prefix of word atom `a` that fits into `avail` cells (>= 1 char)."""
        lo = a.i0 + 1
        best = lo
        for k in range(a.i0 + 1, a.i1):
            if self._word(p, a.i0, k).w <= avail:
                best = k
            else:
                break
        return self._word(p, a.i0, best), self._word(p, best, a.i1)

    def _fill(self, p, q, ai, segs, pre_w):
        placed = []
        si = 0
        x0, x1 = segs[0]
        start = x0 + min(pre_w, max(0, x1 - x0 - 1))
        x = start
        full = self.width - pre_w
        while ai < len(q):
            a = q[ai]
            if a.space:
                if x + a.w <= x1:
                    placed.append((a, x, False))
                    x += a.w
                else:
                    placed.append((a, min(x, x1), True))
                ai += 1
                continue
            if x + a.w <= x1:
                placed.append((a, x, False))
                x += a.w
                ai += 1
                continue
            if x == start and (a.w > full or si == len(segs) - 1 and a.w > x1 - start
                               and not any(e - s >= a.w for s, e in segs[si + 1:])):
                if a.w > x1 - start and (a.i1 - a.i0) > 1:
                    left, right = self._split(p, a, x1 - start)
                    if left.w <= x1 - start or a.w > full:
                        q[ai:ai + 1] = [left, right]
                        placed.append((left, x, False))
                        x += left.w
                        ai += 1
                elif a.i1 - a.i0 == 1 and not placed:
                    placed.append((a, x, False))       # one glyph wider than the page
                    x += a.w
                    ai += 1
            si += 1
            if si >= len(segs):
                break
            x0, x1 = segs[si]
            start = x = x0
        B = D = ul = 0
        for a, _x, hang in placed:
            if hang:
                continue
            B = max(B, a.B)
            D = max(D, a.D)
            if a.ul and not a.space:
                ul = 1
        if ul == 0 and any(a.ul and not h for a, _x, h in placed):
            # underlined spaces count only between underlined words
            ul = 0
        return placed, ai, B, D, ul

    def empty_metrics(self, p):
        f = self.para_font(p)
        if f is None:
            return 1, 0
        return f.baseline, f.height - f.baseline

    def flow(self, p, excl=(), phase=0, table=None):
        excl = tuple(excl)
        key = ('flow', p.uid, p.rev, self.key, excl, table, p.align)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if p.kind == 'rule':
            res = self._flow_rule(p, excl)
        elif p.kind == 'pagebreak':
            res = self._flow_pagebreak(p)
        elif p.kind in ('table', 'tsep') and table is not None:
            res = self._flow_table(p, table)
        else:
            res = self._flow_text(p, excl)
        self._remember(key, res)
        return res

    # ------------------------------------------------------------ page break, tables
    def _flow_pagebreak(self, p):
        rows = self._blank_rows(1)
        label = ' page break '
        W = self.width
        line = ('-' * W)
        x = max(0, (W - len(label)) // 2)
        line = line[:x] + label + line[x + len(label):]
        attr = self.th.c('dim', self.bg_role if self.mono else 'win')
        for k, c in enumerate(line[:W]):
            if c != '-' or k % 2 == 0:
                rows[0][0][k] = c
                rows[0][1][k] = attr
        return ParaLayout(rows, [(0, 0, 0, 1)], [], (0, 0, 1))

    @staticmethod
    def table_groups(paras):
        """{index: (widths, aligns, first, last)} for every table/tsep paragraph."""
        out = {}
        i, n = 0, len(paras)
        while i < n:
            if paras[i].kind not in ('table', 'tsep'):
                i += 1
                continue
            j = i
            while j < n and paras[j].kind in ('table', 'tsep'):
                j += 1
            widths, aligns = [], ''
            for k in range(i, j):
                p = paras[k]
                if p.kind == 'tsep':
                    if not aligns:
                        aligns = p.marker or ''
                    while len(widths) < len(p.marker or ''):
                        widths.append(1)
                    continue
                for c, cell in enumerate(p.text.split('|')):
                    if c >= len(widths):
                        widths.append(1)
                    widths[c] = max(widths[c], len(cell))
            aligns = (aligns + 'l' * len(widths))[:len(widths)]
            info = tuple(widths), aligns
            for k in range(i, j):
                out[k] = info + (k == i, k == j - 1)
            i = j
        return out

    def _table_border(self, widths, ch='-', joint='+'):
        return joint + joint.join(ch * (w + 2) for w in widths) + joint

    def _flow_table(self, p, table):
        widths, aligns, first, last = table
        W = self.width
        role = None
        rows = []
        border = self.attr(PLAIN) if self.mono else self.th.c('dim', 'win')
        y = 0

        def put_line(text, attr):
            r = self._blank_rows(1)[0]
            for k, c in enumerate(text[:W]):
                r[0][k] = c
                r[1][k] = attr
            rows.append(r)

        if first and p.kind != 'tsep':
            put_line(self._table_border(widths), border)
            y += 1
        if p.kind == 'tsep':
            put_line(self._table_border(widths, '='), border)
            lines = [(0, 0, y, 1)]
            if last:
                put_line(self._table_border(widths), border)
            return ParaLayout(rows, lines, [], (min(2, W - 1), y, 1))
        line = [' '] * W
        ats = [self.bg] * W
        boxes = [None] * len(p.text)
        cells = p.text.split('|')
        x = 0
        k = 0                                   # char index in p.text
        for c, w in enumerate(widths):
            if x < W:
                line[x], ats[x] = '|', border
            cell = cells[c] if c < len(cells) else ''
            a = aligns[c] if c < len(aligns) else 'l'
            pad = w - len(cell)
            off = pad // 2 if a == 'c' else (pad if a == 'r' else 0)
            cx = x + 2 + off
            if c < len(cells):
                for j, ch in enumerate(cell):
                    col = cx + j
                    if col < W:
                        line[col] = ch
                        ats[col] = self.attr(p.st[k + j], role)
                    boxes[k + j] = (min(col, W - 1), min(col + 1, W), y, 1)
                k += len(cell)
                if k < len(p.text):              # the '|' after this cell
                    sep = x + w + 3
                    boxes[k] = (min(cx + len(cell), W - 1), min(sep, W), y, 1)
                    k += 1
            x += w + 3
        if x < W:
            line[x], ats[x] = '|', border
        rows.append((line, ats))
        if last:
            put_line(self._table_border(widths), border)
        # caret after the last character
        if p.text:
            bx = boxes[len(p.text) - 1]
            end = (min(bx[1], W - 1), y, 1)
        else:
            end = (min(2, W - 1), y, 1)
        for i in range(len(boxes)):
            if boxes[i] is None:
                boxes[i] = (end[0], end[0], y, 1)
        return ParaLayout(rows, [(0, len(p.text), y, 1)], boxes, end)

    def _blank_rows(self, n, attr=None):
        attr = self.bg if attr is None else attr
        return [([' '] * self.width, [attr] * self.width) for _ in range(n)]

    def _flow_rule(self, p, excl):
        y = 0
        while True:
            segs = self._segments(excl, y, 1, 0)
            if segs or y > 4000:
                break
            y += 1
        rows = self._blank_rows(y + 1)
        ch = '-'
        attr = self.th.c('dim', 'win')
        for x0, x1 in segs or [(0, self.width)]:
            for x in range(x0, x1):
                rows[y][0][x] = ch
                rows[y][1][x] = attr
        x0 = segs[0][0] if segs else 0
        return ParaLayout(rows, [(0, 0, y, 1)], [], (x0, y, 1))

    def _prefix(self, p):
        if p.kind == 'li':
            marker = p.marker
            if marker in ('-', '*', '+'):
                marker = self.bullet
            pre = ' ' * min(p.indent, 12) + marker + ' '
            return pre, len(pre), self.th.c('accent', 'win')
        if p.kind == 'quote':
            return '| ', 2, self.th.c('dim', 'win')
        return '', 0, self.bg

    @staticmethod
    def _align(placed, x1, align, last):
        """Shift (center/right) or spread (justify) the atoms of one line.

        Returns the new placed list and {index: extra width} for widened spaces."""
        idx = [n for n, (a, _x, hang) in enumerate(placed) if not hang]
        while idx and placed[idx[-1]][0].space:
            idx.pop()
        if not idx:
            return placed, {}
        end = placed[idx[-1]][1] + placed[idx[-1]][0].w
        free = x1 - end
        if free <= 0:
            return placed, {}
        out = list(placed)
        widen = {}
        if align == 'justify':
            if last:
                return placed, {}
            gaps = [n for n in idx if placed[n][0].space and n > idx[0]]
            if not gaps:
                return placed, {}
            per, rest = divmod(free, len(gaps))
            shift = 0
            gi = 0
            for n, (a, ax, hang) in enumerate(placed):
                out[n] = (a, ax + shift, hang)
                if gi < len(gaps) and n == gaps[gi]:
                    add = per + (1 if gi < rest else 0)
                    widen[n] = add
                    shift += add
                    gi += 1
            return out, widen
        shift = free // 2 if align == 'center' else free
        for n, (a, ax, hang) in enumerate(placed):
            out[n] = (a, min(ax + shift, x1) if hang else ax + shift, hang)
        return out, widen

    def _flow_text(self, p, excl, phase=0):
        W = self.width
        q = list(self.atoms(p))
        pre, pre_w, pre_attr = self._prefix(p)
        role = self.role_of(p)
        code = p.kind == 'code'
        field = self.th.c('text', 'field')
        rows = []
        lines = []
        boxes = [None] * len(p.text)
        end = None
        y = 0
        ai = 0
        first = True
        eB, eD = self.empty_metrics(p)
        guard = 0
        while True:
            guard += 1
            nxt = q[ai] if ai < len(q) else None
            B, D = (nxt.B, nxt.D) if nxt is not None else (eB, eD)
            B, D = max(B, 1 if nxt is None else B), max(D, 0)
            ul = 0
            segs = []
            result = None
            for _ in range(5):
                h = B + D + ul
                segs = self._segments(excl, y, h, pre_w)
                if not segs:
                    break
                result = self._fill(p, q, ai, segs, pre_w if first else pre_w)
                _pl, _ai, B2, D2, ul2 = result
                if not _pl:
                    B2, D2 = B, D
                if B2 <= B and D2 <= D and ul2 <= ul:
                    break
                B, D, ul = max(B, B2), max(D, D2), max(ul, ul2)
            if not segs and guard < 6000:
                rows.extend(self._blank_rows(1))
                y += 1
                continue
            if not segs:                         # give up on the pictures
                segs = [(0, W)]
                result = self._fill(p, q, ai, segs, pre_w)
            placed, ai2, _b, _d, _u = result
            h = B + D + ul
            widen = {}
            if p.align in ('center', 'right', 'justify') and placed and len(segs) == 1 \
                    and not code:
                placed, widen = self._align(placed, segs[0][1], p.align, ai2 >= len(q))
            lrows = self._blank_rows(h, field if code else None)
            if code:
                for r in range(h):
                    rr = lrows[r][1]
                    for x in range(W):
                        rr[x] = self.bg
                    for x0, x1 in segs:
                        for x in range(x0, x1):
                            rr[x] = field
            # prefix (bullet / quote bar) on the baseline row
            sx = segs[0][0]
            if pre:
                txt = pre if first else (' ' * pre_w if p.kind == 'li' else pre)
                for k, c in enumerate(txt):
                    if sx + k < W:
                        lrows[B - 1][0][sx + k] = c
                        lrows[B - 1][1][sx + k] = pre_attr
                if p.kind == 'quote':
                    for r in range(h):
                        lrows[r][0][sx] = '|'
                        lrows[r][1][sx] = pre_attr
            a_first = placed[0][0].i0 if placed else (q[ai].i0 if ai < len(q) else len(p.text))
            for n_at, (a, ax, hang) in enumerate(placed):
                extra = widen.get(n_at, 0)
                for xoff, pc in a.pieces:
                    top = B - pc.B
                    px = ax + xoff
                    attrs_by_glyph = [self.attr(p.st[k], role) for k in range(pc.i0, pc.i1)]
                    if not hang:
                        for r, line in enumerate(pc.rows):
                            yy = top + r
                            if yy >= h:
                                continue
                            chars, ats = lrows[yy]
                            g = 0
                            for c, ch in enumerate(line):
                                col = px + c
                                if col >= W:
                                    break
                                while g + 1 < len(pc.spans) and pc.spans[g + 1][0] <= c:
                                    g += 1
                                if ch != ' ' or code:
                                    chars[col] = ch
                                    if not code or ch != ' ':
                                        ats[col] = attrs_by_glyph[min(g, len(attrs_by_glyph) - 1)]
                                    else:
                                        ats[col] = field
                    for g, (s0, s1) in enumerate(pc.spans):
                        k = pc.i0 + g
                        if k >= len(boxes):
                            break
                        if hang:
                            boxes[k] = (min(ax, W), min(ax, W), y + top, pc.h)
                        else:
                            boxes[k] = (px + s0, px + s1 + extra, y + top, pc.h)
                    # underline row
                    if ul and not hang:
                        urow = lrows[B + D]
                        for g, (s0, s1) in enumerate(pc.spans):
                            k = pc.i0 + g
                            lvl = p.st[k].ul
                            if not lvl:
                                continue
                            if a.space:
                                # only between underlined neighbours
                                if not (k > 0 and p.st[k - 1].ul and k + 1 < len(p.st)
                                        and p.st[k + 1].ul):
                                    continue
                            mark = '-' if lvl == 1 else '='
                            for col in range(px + s0, min(W, px + s1 + extra)):
                                urow[0][col] = mark
                                urow[1][col] = attrs_by_glyph[g]
            b_last = ai2 if ai2 <= len(q) else len(q)
            b_idx = q[b_last].i0 if b_last < len(q) else len(p.text)
            lines.append((a_first, b_idx, y, h))
            rows.extend(lrows)
            ai = ai2
            if ai >= len(q):
                # caret after the last character
                if placed:
                    last = None
                    for a, ax, hang in placed:
                        last = (a, ax, hang)
                    a, ax, hang = last
                    k = a.i1 - 1
                    bx = boxes[k]
                    if p.text and bx is not None:
                        end = (min(bx[1], W - 1), bx[2], bx[3])
                if end is None:
                    end = (min(sx + pre_w, W - 1), y + B - eB, max(1, eB + eD))
                break
            first = False
            y += h
            if guard > 20000:
                break
        if end is None:
            end = (0, 0, 1)
        for k in range(len(boxes)):
            if boxes[k] is None:
                boxes[k] = (end[0], end[0], end[1], end[2])
        return ParaLayout(rows, lines, boxes, end)

    def paginate(self, pl, y0):
        """Insert the blank rows between sheets; cheap (no glyph layout again)."""
        page = self.page
        if not page or pl.height <= 0:
            return pl
        pads = []
        shift = 0
        for a, b, ly, h in pl.lines:
            if h >= page:
                continue
            pos = (y0 + ly + shift) % page
            if h and pos + h > page:
                pad = page - pos
                pads.append((ly, pad, shift))
                shift += pad
        if not pads:
            return pl
        def sh(y):
            n = 0
            for ly, pad, _s in pads:
                if ly <= y:
                    n += pad
                else:
                    break
            return n
        bg = pl.rows[0][1][0] if pl.rows and pl.rows[0][1] else self.bg
        blank = ([' '] * self.width, [bg] * self.width)
        rows = []
        src = 0
        for ly, pad, _s in pads:
            rows.extend(pl.rows[src:ly])
            rows.extend([(list(blank[0]), list(blank[1])) for _ in range(pad)])
            src = ly
        rows.extend(pl.rows[src:])
        lines = [(a, b, ly + sh(ly), h) for a, b, ly, h in pl.lines]
        boxes = [(x0, x1, y + sh(y), h) for x0, x1, y, h in pl.boxes]
        ex, ey, eh = pl.end
        end = (ex, ey + sh(ey), eh)
        out = ParaLayout(rows, lines, boxes, end)
        return out

    # ------------------------------------------------------------ floats
    def resolve(self, path):
        path = os.path.expanduser((path or '').strip())
        if path and not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        return os.path.normpath(path) if path else path

    def float_geom(self, f):
        W = self.width
        path = self.resolve(f.path)
        img, err = (None, 'needs a local file') if path.startswith(('http:', 'https:')) \
            else self.images.get(path, self.sync)
        size = (img.w, img.h) if img is not None else None
        aspect = imaging.cell_aspect(self.wm)
        iw, ih = size or (4, 3)
        if f.w is None:
            w, h = imaging.fit_size(iw, ih, W, 24, aspect)
        else:
            w = max(2, min(f.w, W))
            h = int(round(w * ih / (iw * aspect)))
        h = max(1, min(MAX_FLOAT_H, h))
        if f.x is None:
            x = (W - w) // 2
        else:
            x = max(0, min(f.x, W - w))
        return x, w, h, path, img, err

    def _draw_float(self, rows, f, x, y, w, h, path, img, err):
        need = y + h - len(rows)
        if need > 0:
            rows.extend(self._blank_rows(need))
        if img is not None:
            try:
                cells = self.images.cells_for(self.wm, path, img, w, h, self.image_mode, self.bg_role)
            except Exception as e:      # noqa: BLE001 - keep the page drawable
                cells, err = None, str(e)
            if cells:
                for r, (cs, ats) in enumerate(cells[:h]):
                    ch, at = rows[y + r]
                    n = min(len(cs), self.width - x)
                    ch[x:x + n] = list(cs[:n])
                    at[x:x + n] = list(ats[:n])
                return
        attr = self.th.c('dim', 'win')
        name = os.path.basename(f.path) or '?'
        label = ('loading %s' % name) if err is None else ('%s: %s' % (name, err))
        for r in range(h):
            ch, at = rows[y + r]
            if r == 0 or r == h - 1:
                line = '+' + '-' * max(0, w - 2) + '+' if w > 1 else '+'
            else:
                line = '|' + ' ' * max(0, w - 2) + '|' if w > 1 else '|'
            if r == h // 2 and w > 4:
                lab = label[:w - 4]
                line = '| ' + lab.ljust(w - 4) + ' |'
            line = line[:w]
            for k, c in enumerate(line):
                ch[x + k] = c
                at[x + k] = attr if r != h // 2 or c in '|+' else self.th.c(
                    'text' if err is None else 'red', 'win')

    # ------------------------------------------------------------ document
    def run(self, doc):
        anchored = {}
        for f in doc.floats:
            anchored.setdefault(id(f.anchor), []).append(f)
        rows = []
        tops = []
        pls = []
        excl = []
        frects = []
        noprint = set()
        tables = self.table_groups(doc.paras)
        y = 0
        for pi, p in enumerate(doc.paras):
            if p.kind == 'pagebreak':
                pl = self.flow(p)
                tops.append(y)
                pls.append(pl)
                rows.extend(pl.rows)
                noprint.add(y)
                y += pl.height
                if self.page and y % self.page:
                    pad = self.page - y % self.page
                    rows.extend(self._blank_rows(pad))
                    y += pad
                for f in anchored.get(id(p), ()):      # pictures go after the break
                    x, w, h, path, img, err = self.float_geom(f)
                    frects.append((f, x, y, w, h, path, img, err))
                    excl.append((0, self.width, y, y + h + 1))
                continue
            if pi in tables:
                pl = self.paginate(self.flow(p, table=tables[pi]), y)
                tops.append(y)
                pls.append(pl)
                rows.extend(pl.rows)
                y += pl.height
                continue
            for f in anchored.get(id(p), ()):
                x, w, h, path, img, err = self.float_geom(f)
                fy = y + max(0, f.dy or 0)
                if self.page and h <= self.page and fy % self.page + h > self.page:
                    fy += self.page - fy % self.page      # picture moves to the next page
                frects.append((f, x, fy, w, h, path, img, err))
                # one free row below the picture keeps text off its edge
                if f.wrap == 'block':
                    excl.append((0, self.width, fy, fy + h + 1))
                else:
                    excl.append((x - 1, x + w + 1, fy, fy + h + 1))
            free = self.flow(p)
            rel = ()
            if excl:
                bottom = y + free.height
                hits = [e for e in excl if e[2] < bottom + 64 and e[3] > y]
                if any(e[2] < bottom and e[3] > y for e in hits):
                    rel = tuple((x0, x1, y0 - y, y1 - y) for x0, x1, y0, y1 in hits)
            pl = self.flow(p, rel) if rel else free
            pl = self.paginate(pl, y)
            tops.append(y)
            pls.append(pl)
            rows.extend(pl.rows)
            y += pl.height
        # floats paint over (copies of) the rows they cover
        if frects:
            touched = set()
            for f, x, fy, w, h, path, img, err in frects:
                if fy + h > len(rows):
                    rows.extend(self._blank_rows(fy + h - len(rows)))
                for r in range(fy, fy + h):
                    if r not in touched:
                        rows[r] = (list(rows[r][0]), list(rows[r][1]))
                        touched.add(r)
                self._draw_float(rows, f, x, fy, w, h, path, img, err)
        self.rows = rows
        self.tops = tops
        self.paras = pls
        self.floats = [(f, x, fy, w, h) for f, x, fy, w, h, *_ in frects]
        self.height = len(rows)
        self.noprint = noprint
        return self

    # ------------------------------------------------------------ queries
    def caret(self, pi, i):
        """Absolute (x, y, h) of the cursor before char i of paragraph pi."""
        pl = self.paras[pi]
        top = self.tops[pi]
        if i >= len(pl.boxes):
            x, y, h = pl.end
            return x, top + y, h
        x0, _x1, y, h = pl.boxes[i]
        return x0, top + y, h

    def all_lines(self):
        """[(pi, li, a, b, y, h)] in reading order, absolute y."""
        out = []
        for pi, pl in enumerate(self.paras):
            top = self.tops[pi]
            for li, (a, b, y, h) in enumerate(pl.lines):
                out.append((pi, li, a, b, top + y, h))
        return out

    def line_of(self, pi, i):
        lines = self.paras[pi].lines
        for li, (a, b, _y, _h) in enumerate(lines):
            if i < b:
                return li
        return len(lines) - 1

    def pos_in_line(self, pi, li, x):
        """Char index on line li of paragraph pi closest to column x."""
        pl = self.paras[pi]
        a, b, _y, _h = pl.lines[li]
        last = li == len(pl.lines) - 1
        hi = b if last else max(a, b - 1)
        best, best_d = a, None
        for k in range(a, hi + 1):
            if k < len(pl.boxes):
                x0, x1 = pl.boxes[k][0], pl.boxes[k][1]
                if x0 <= x < x1:
                    return k if x - x0 < x1 - x or x1 - x0 == 1 else min(k + 1, hi)
            else:
                x0 = pl.end[0]
            d = abs(x0 - x)
            if best_d is None or d < best_d:
                best, best_d = k, d
        return best

    def hit(self, x, y):
        """Document position under an absolute cell (x, y)."""
        lines = self.all_lines()
        if not lines:
            return 0, 0
        cand = lines[0]
        for ln in lines:
            if ln[4] > y:
                break
            cand = ln
        return cand[0], self.pos_in_line(cand[0], cand[1], x)

    def float_at(self, x, y):
        for f, fx, fy, w, h in reversed(self.floats):
            if fx <= x < fx + w and fy <= y < fy + h:
                return f, fx, fy, w, h
        return None

    def para_at_row(self, y):
        """Last paragraph whose top is at or above row y."""
        best = 0
        for pi, t in enumerate(self.tops):
            if t <= y:
                best = pi
            else:
                break
        return best


def sheets(lay, page, title='', date='', pick=None):
    """A laid-out document (rows per page = page.rows) -> printer.Sheet list.

    Margins, header and footer are added; page-break markers are dropped;
    pictures printed as graphics become gfx areas (their cells are masked).
    pick: iterable of sheet numbers (1-based) to keep, None = all."""
    from .printer import Sheet
    rows_pp = page.rows
    n = max(1, (lay.height + rows_pp - 1) // rows_pp)
    blank = ' ' * page.cols
    out = []
    gfx_by_page = {}
    for f, x, fy, w, h in lay.floats:
        if getattr(f, 'out', 'gfx') != 'gfx':
            continue
        k, local = divmod(fy, rows_pp)
        img, _err = lay.images.get(lay.resolve(f.path), True) \
            if not lay.resolve(f.path).startswith(('http:', 'https:')) else (None, 'url')
        if img is None:
            continue                      # missing picture: the placeholder prints as text
        gfx_by_page.setdefault(k, []).append(
            (page.left + x, page.top + local, w, min(h, rows_pp - local), img))
    want = set(pick) if pick else None
    for k in range(n):
        if want is not None and k + 1 not in want:
            continue
        rows = [(blank, [0] * page.cols) for _ in range(page.lines)]
        for r in range(rows_pp):
            gy = k * rows_pp + r
            if gy >= lay.height or gy in lay.noprint:
                continue
            chars, attrs = lay.rows[gy]
            line = (' ' * page.left + ''.join(chars))[:page.cols].ljust(page.cols)
            at = [0] * page.left + list(attrs)
            rows[page.top + r] = (line, (at + [0] * page.cols)[:page.cols])
        for which, y in (('header', max(0, page.top - 2)),
                         ('footer', min(page.lines - 1, page.lines - page.bottom + 1))):
            t = page.margin_line(which, k + 1, n, title, date)
            if t and (which == 'header' and page.top >= 1 or which == 'footer' and page.bottom >= 1):
                line = (' ' * page.left + t)[:page.cols].ljust(page.cols)
                rows[y] = (line, [0] * page.cols)
        out.append(Sheet(k + 1, rows, gfx_by_page.get(k, [])))
    return out


def render_rows(wm, text, width, base_dir='', image_mode=None, vt=None, images=None):
    """Markdown source -> list of (chars:str, attrs:list), trailing blanks trimmed."""
    doc = parse(text)
    lay = Layout(wm, width, base_dir, images, image_mode, vt, sync=True).run(doc)
    rows = [(''.join(c), list(a)) for c, a in lay.rows]
    while rows and not rows[-1][0].strip():
        rows.pop()
    return rows
