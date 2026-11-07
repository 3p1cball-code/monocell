"""FIGlet fonts (.flf) for the Writer's large text.

A small, dependency-free reader for FIGlet 2 fonts plus the horizontal layout
of figlet 2.2.5: full width, fitting (kerning) and smushing, universal or with
the six controlled rules. The output is plain ASCII, so it works on the Linux
console and on a dot-matrix printer.

compose() returns the rows of a run *and* where every glyph starts and ends,
so the Writer can map a cursor position to a column inside the big text.

Bundled fonts (monocell/fonts/figlet): Graceful (Mikhael Goikhman, 2002),
Standard, Small, Slant, Big (Glenn Chappell & Ian Chai, figlet distribution)
and Block / Block 3D (built from the public-domain misc-fixed 6x10 by
tools/make_block_font.py).
"""
import os
import re

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'figlet')
USER_FONT_DIR = os.path.expanduser('~/.local/share/monocell/figlet')
DEUTSCH = (196, 214, 220, 228, 246, 252, 223)

SM_EQUAL, SM_LOWLINE, SM_HIERARCHY, SM_PAIR, SM_BIGX, SM_HARDBLANK = 1, 2, 4, 8, 16, 32
SM_KERN, SM_SMUSH = 64, 128

_FONTS = {}

# umlauts the font does not carry: base letter + two dots
_DOTTED = {'Ä': 'A', 'Ö': 'O', 'Ü': 'U', 'ä': 'a', 'ö': 'o', 'ü': 'u', 'ë': 'e', 'ï': 'i',
           'Ë': 'E', 'Ï': 'I', 'ÿ': 'y'}
_FALLBACK = {'ß': 'ss', 'é': 'e', 'è': 'e', 'ê': 'e', 'á': 'a', 'à': 'a', 'â': 'a', 'ó': 'o',
             'ò': 'o', 'ô': 'o', 'ú': 'u', 'ù': 'u', 'û': 'u', 'í': 'i', 'ì': 'i', 'î': 'i',
             'ç': 'c', 'ñ': 'n', 'É': 'E', 'È': 'E', 'Á': 'A', 'À': 'A', 'Ó': 'O', 'Ú': 'U',
             '€': 'E', '–': '-', '—': '-', '„': '"', '“': '"', '”': '"', '‚': "'", '‘': "'",
             '’': "'", '…': '...'}


class Font:
    def __init__(self, name, height, baseline, hardblank, mode, glyphs):
        self.name = name
        self.height = height
        self.baseline = baseline        # rows from the top to the baseline (1-based)
        self.hardblank = hardblank
        self.mode = mode                # SM_* bits
        self.glyphs = glyphs            # codepoint -> list of rows (equal width)
        self._cache = {}

    # ------------------------------------------------------------ glyphs
    def glyph(self, ch):
        """Rows of one character (with hardblanks); synthesises missing umlauts."""
        g = self.glyphs.get(ord(ch))
        if g is not None:
            return g
        if ch in self._cache:
            return self._cache[ch]
        g = None
        base = _DOTTED.get(ch)
        if base and ord(base) in self.glyphs:
            g = self._dotted(self.glyphs[ord(base)])
        elif ch in _FALLBACK:
            parts = [self.glyph(c) for c in _FALLBACK[ch]]
            g = [''.join(p[r] for p in parts) for r in range(self.height)]
        elif ch.isspace():
            g = self.glyphs.get(32)
        else:
            low = ch.lower() if ch.isupper() else ch.upper()
            g = self.glyphs.get(ord(low)) if len(low) == 1 else None
            if g is None:
                g = self.glyphs.get(ord('?')) or [' '] * self.height
        self._cache[ch] = g
        return g

    def _dotted(self, rows):
        rows = list(rows)
        w = len(rows[0]) if rows else 0
        ink = [i for i in range(w) if any(r[i] not in (' ', self.hardblank) for r in rows)]
        if len(ink) < 2:
            return rows
        a, b = ink[0], ink[-1]
        for r in range(len(rows)):           # first row above the ink with room
            line = rows[r]
            p, q = a + (b - a) // 4, b - (b - a) // 4
            if p == q:
                q = min(w - 1, p + 1)
            if line[p] == ' ' and line[q] == ' ':
                s = list(line)
                s[p] = s[q] = '.'
                rows[r] = ''.join(s)
                return rows
            if any(c not in (' ', self.hardblank) for c in line):
                if line[p] in ' _' and line[q] in ' _':     # no room above: dots in the top row
                    s = list(line)
                    s[p] = s[q] = '.'
                    rows[r] = ''.join(s)
                return rows
        return rows

    @property
    def space_width(self):
        g = self.glyphs.get(32)
        return len(g[0]) if g and g[0] else 1

    # ------------------------------------------------------------ layout
    def _smushem(self, lch, rch, lw, rw):
        hb = self.hardblank
        mode = self.mode
        if lch == ' ':
            return rch
        if rch == ' ':
            return lch
        if lw < 2 or rw < 2:
            return None
        if not mode & SM_SMUSH:
            return None
        if not mode & 63:
            if lch == hb:
                return rch
            if rch == hb:
                return lch
            return rch
        if mode & SM_HARDBLANK and lch == hb and rch == hb:
            return lch
        if lch == hb or rch == hb:
            return None
        if mode & SM_EQUAL and lch == rch:
            return lch
        if mode & SM_LOWLINE:
            if lch == '_' and rch in '|/\\[]{}()<>':
                return rch
            if rch == '_' and lch in '|/\\[]{}()<>':
                return lch
        if mode & SM_HIERARCHY:
            for cls, rest in (('|', '/\\[]{}()<>'), ('/\\', '[]{}()<>'), ('[]', '{}()<>'),
                              ('{}', '()<>'), ('()', '<>')):
                if lch in cls and rch in rest:
                    return rch
                if rch in cls and lch in rest:
                    return lch
        if mode & SM_PAIR and lch + rch in ('[]', '][', '{}', '}{', '()', ')('):
            return '|'
        if mode & SM_BIGX:
            if lch == '/' and rch == '\\':
                return '|'
            if lch == '\\' and rch == '/':
                return 'Y'
            if lch == '>' and rch == '<':
                return 'X'
        return None

    def _smushamt(self, out, glyph, prev_w):
        if not self.mode & (SM_SMUSH | SM_KERN):
            return 0
        cur_w = len(glyph[0]) if glyph else 0
        best = cur_w
        outlen = len(out[0]) if out else 0
        for row in range(self.height):
            line = out[row]
            lb = len(line)
            ch1 = ''
            while True:
                ch1 = line[lb] if lb < len(line) else ''
                if lb > 0 and (not ch1 or ch1 == ' '):
                    lb -= 1
                    continue
                break
            cb = 0
            g = glyph[row]
            while cb < len(g) and g[cb] == ' ':
                cb += 1
            ch2 = g[cb] if cb < len(g) else ''
            amt = cb + outlen - 1 - lb
            if not ch1 or ch1 == ' ':
                amt += 1
            elif ch2 and self._smushem(ch1, ch2, prev_w, cur_w) is not None:
                amt += 1
            best = min(best, amt)
        return max(0, best)

    def compose(self, text):
        """text -> (rows, spans); spans[i] = (start, end) columns of glyph i.

        Rows keep the font height; hardblanks become spaces.
        """
        key = text
        hit = self._cache.get(('run', key))
        if hit is not None:
            return hit
        out = [''] * self.height
        spans = []
        prev_w = 0
        for ch in text:
            g = self.glyph(ch)
            w = len(g[0]) if g else 0
            amt = min(self._smushamt(out, g, prev_w), w)
            outlen = len(out[0])
            start = max(0, outlen - amt)
            for row in range(self.height):
                line = list(out[row])
                gr = g[row] if row < len(g) else ''
                gr = gr.ljust(w)
                for k in range(amt):
                    col = outlen - amt + k
                    if col < 0:
                        continue
                    r = self._smushem(line[col], gr[k], prev_w, w)
                    line[col] = r if r is not None else gr[k]
                out[row] = ''.join(line) + gr[amt:]
            spans.append((start, len(out[0])))
            prev_w = w
        rows = [r.replace(self.hardblank, ' ') for r in out]
        res = (rows, spans)
        if len(self._cache) < 4000:
            self._cache[('run', key)] = res
        return res

    def render(self, text):
        return self.compose(text)[0]


def parse(data, name='font'):
    lines = data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    head = lines[0].split()
    if not head or not head[0].startswith('flf2a'):
        raise ValueError('not a FIGlet font: %s' % name)
    hardblank = head[0][5] if len(head[0]) > 5 else '$'
    nums = [int(x) for x in head[1:] if x.lstrip('-').isdigit()]
    height, baseline = nums[0], nums[1]
    old = nums[3] if len(nums) > 3 else 0
    comments = nums[4] if len(nums) > 4 else 0
    full = nums[6] if len(nums) > 6 else None
    if full is None:
        if old < 0:
            mode = 0
        elif old == 0:
            mode = SM_KERN
        else:
            mode = (old & 63) | SM_SMUSH
    else:
        mode = full & 255
    pos = 1 + comments

    def read_glyph():
        nonlocal pos
        rows = []
        for _ in range(height):
            if pos >= len(lines):
                return None
            ln = lines[pos].rstrip('\n')
            pos += 1
            ln = ln.rstrip()
            if ln:
                end = ln[-1]
                ln = ln.rstrip(end)
            rows.append(ln)
        w = max((len(r) for r in rows), default=0)
        return [r.ljust(w) for r in rows]

    tag_re = re.compile(r'^-?(0[xX][0-9a-fA-F]+|\d+)(\s|$)')
    glyphs = {}
    for code in list(range(32, 127)) + list(DEUTSCH):
        if code in DEUTSCH and pos < len(lines) and tag_re.match(lines[pos]) \
                and not lines[pos].rstrip().endswith('@'):
            break                      # font without the German block
        g = read_glyph()
        if g is None:
            break
        if any(r.replace(hardblank, ' ').strip() for r in g) or code == 32:
            glyphs[code] = g
    while pos < len(lines):
        tag = lines[pos].split()
        pos += 1
        if not tag:
            continue
        try:
            code = int(tag[0], 0)
        except ValueError:
            try:
                code = int(tag[0], 8) if tag[0].startswith('0') else None
            except ValueError:
                code = None
        g = read_glyph()
        if g is None:
            break
        if code is not None and code >= 0 and any(r.replace(hardblank, ' ').strip() for r in g):
            glyphs[code] = g
    return Font(name, height, baseline, hardblank, mode, glyphs)


ALIASES = {'3d-ascii': 'block3d', '3d': 'block3d'}
LABELS = {'block3d': 'Block 3D', 'block': 'Block', 'graceful': 'Graceful', 'big': 'Big',
          'standard': 'Standard', 'small': 'Small', 'slant': 'Slant'}
ORDER = ['block3d', 'block', 'big', 'standard', 'graceful', 'slant', 'small']


def canonical(name):
    name = (name or '').strip().lower()
    return ALIASES.get(name, name)


def label(name):
    return LABELS.get(name, name.replace('-', ' ').replace('_', ' ').title())


def available():
    names = [n for n in _available() if n not in ALIASES]
    return sorted(names, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER), n))


def _available():
    names = []
    for d in (FONT_DIR, USER_FONT_DIR):
        try:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith('.flf'):
                    n = f[:-4].lower()
                    if n not in names:
                        names.append(n)
        except OSError:
            pass
    return names


def load(name):
    name = canonical(name or 'graceful')
    if name in _FONTS:
        return _FONTS[name]
    for d in (USER_FONT_DIR, FONT_DIR):
        p = os.path.join(d, name + '.flf')
        if os.path.exists(p):
            with open(p, encoding='latin-1') as f:
                font = parse(f.read(), name)
            _FONTS[name] = font
            return font
    if name != 'graceful':
        return load('graceful')
    raise FileNotFoundError('no FIGlet font found in %s' % FONT_DIR)
