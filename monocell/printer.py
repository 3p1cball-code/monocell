"""IBM Proprinter X24E output (PPDS) for the Writer.

The X24E is a 24-wire, 80-column printer. Text goes through the printer's own
font: 10 or 12 cpi, 6 or 8 lpi, draft or letter quality, emphasized print for
bold. Character set 2 is selected so code page 437 (the umlauts) prints.

Pictures can be printed as bit images: rows of the page that contain such a
picture are rasterised (text of those rows included, see printraster) and sent
as 24-dot bands with ESC [ g mode 11 (180 x 180 dpi). The vertical unit is set
to 1/180 inch (ESC [ \\) so ESC J moves exactly one band; text rows before and
after keep the normal line spacing. Paper: top of form is where the paper
stands when printing starts (ESC C sets it).

Everything here works on "sheets" (see Sheet): the page as the Writer shows it,
margins, header and footer included.
"""
import subprocess

from .screen import BOLD

ESC = b'\x1b'
QUALITY = {'draft': b'\x1bI\x00', 'letter': b'\x1bI\x02'}
GFX_DPI = 180


class Sheet:
    """One printed page.

    rows: [(chars str, attrs list)] of page.lines rows, page.cols wide.
    gfx:  [(x, y, w, h, image)] picture areas in cells, image = imaging.Image
          (or None) - printed as bit images, their cells are not printed as text.
    """

    def __init__(self, number, rows, gfx=()):
        self.number = number
        self.rows = rows
        self.gfx = list(gfx)

    def gfx_rows(self):
        out = set()
        for x, y, w, h, _img in self.gfx:
            out.update(range(y, y + h))
        return out

    def masked(self, r):
        """Columns of row r that belong to a picture printed as graphics."""
        cols = set()
        for x, y, w, h, _img in self.gfx:
            if y <= r < y + h:
                cols.update(range(x, x + w))
        return cols


def cp437(text):
    return text.encode('cp437', 'replace')


def bold_runs(chars, attrs):
    runs = []
    cur_bold = None
    cur = []
    for i, ch in enumerate(chars):
        bold = bool(((attrs[i] if i < len(attrs) else 0) >> 8) & BOLD)
        if bold != cur_bold:
            if cur:
                runs.append((cur_bold, ''.join(cur)))
                cur = []
            cur_bold = bold
        cur.append(ch)
    if cur:
        runs.append((cur_bold, ''.join(cur)))
    return runs


def setup(page_lines=66, cpi=10, lpi=6, quality='draft'):
    out = bytearray()
    out += ESC + b'6'                         # character set 2: code page 437 with umlauts
    out += b'\x12' if cpi == 10 else ESC + b':'      # DC2 = 10 cpi, ESC : = 12 cpi
    out += ESC + b'A\x0c' + ESC + b'2' if lpi == 6 else ESC + b'0'
    out += ESC + b'C' + bytes([max(1, min(127, page_lines))])
    out += QUALITY.get(quality, b'')
    return bytes(out)


def text_line(chars, attrs, skip=()):
    out = bytearray()
    chars = list(chars)
    for x in skip:
        if x < len(chars):
            chars[x] = ' '
    line = ''.join(chars).rstrip()
    for bold, part in bold_runs(line, attrs):
        if bold and part.strip():
            out += ESC + b'E' + cp437(part) + ESC + b'F'
        else:
            out += cp437(part)
    return bytes(out)


def band(bits, width):
    """24 rows of dots (list of bytearrays/bytes, `width` dots each, 0/1 per dot)
    -> ESC [ g mode 11 with trailing blank columns cut off."""
    cols = bytearray()
    last = 0
    for x in range(width):
        b0 = b1 = b2 = 0
        for r in range(8):
            if x < len(bits[r]) and bits[r][x]:
                b0 |= 0x80 >> r
            if x < len(bits[8 + r]) and bits[8 + r][x]:
                b1 |= 0x80 >> r
            if x < len(bits[16 + r]) and bits[16 + r][x]:
                b2 |= 0x80 >> r
        cols += bytes((b0, b1, b2))
        if b0 or b1 or b2:
            last = x + 1
    if not last:
        return b''
    data = bytes(cols[:last * 3])
    n = len(data) + 1
    return ESC + b'[g' + bytes((n & 255, n >> 8, 11)) + data


def vunits(n):
    """ESC [ \\ : vertical units for ESC J / ESC 3 (180 for graphics, 216 = normal)."""
    return ESC + b'[\\' + bytes((4, 0, 0, 0, 0, n))


def feed(dots):
    out = bytearray()
    while dots > 0:
        k = min(255, dots)
        out += ESC + b'J' + bytes((k,))
        dots -= k
    return bytes(out)


def encode_sheets(sheets, page, quality='draft', raster=None, copies=1):
    """Sheets -> PPDS bytes. raster(sheet, r0, r1) -> list of dot rows (bytes, one
    byte per dot) for page rows r0..r1-1 at 180 dpi, needed for bit-image rows."""
    out = bytearray(setup(page.lines, page.cpi, page.lpi, quality))
    dot_h = GFX_DPI / float(page.lpi)
    for _copy in range(max(1, copies)):
        for sh in sheets:
            gfx_rows = sh.gfx_rows() if raster else set()
            last = len(sh.rows)
            while last > 0 and not sh.rows[last - 1][0].strip() and (last - 1) not in gfx_rows:
                last -= 1
            r = 0
            while r < last:
                if r in gfx_rows:
                    r1 = r
                    while r1 < last and r1 in gfx_rows:
                        r1 += 1
                    out += gfx_block(sh, r, r1, page, raster, dot_h)
                    r = r1
                    continue
                chars, attrs = sh.rows[r]
                out += text_line(chars, attrs) + b'\r\n'
                r += 1
            out += b'\f'
    return bytes(out)


def gfx_block(sheet, r0, r1, page, raster, dot_h):
    y0, y1 = int(round(r0 * dot_h)), int(round(r1 * dot_h))
    dots = raster(sheet, r0, r1)          # rows of y1-y0 dot lines
    width = len(dots[0]) if dots else 0
    out = bytearray(vunits(180))
    y = 0
    total = y1 - y0
    while y < total:
        rows = dots[y:y + 24]
        while len(rows) < 24:
            rows.append(bytes(width))
        out += b'\r' + band(rows, width)
        step = min(24, total - y)
        out += b'\r' + feed(step)
        y += step
    out += vunits(216)
    return bytes(out)


def test_sheet(page):
    """A test page: ruler, the character set, bold, a frame of text with a
    graphics frame inside it (both must line up), and a grey ramp."""
    W = page.cols
    rows = []

    def add(text, bold=False):
        attrs = [(BOLD << 8) if bold else 0] * W
        rows.append((text[:W].ljust(W), attrs))

    ruler = ''.join(str(i // 10 % 10) if i % 10 == 0 else ('+' if i % 5 == 0 else '.')
                    for i in range(W))
    add('MONOCELL WRITER - PRINTER TEST  %s' % page.describe(), True)
    add('')
    add(ruler)
    add('')
    add('Normal:  The quick brown fox jumps over the lazy dog 0123456789')
    add('Bold:    The quick brown fox jumps over the lazy dog 0123456789', True)
    add('Umlauts: Ae Oe Ue -> \u00c4 \u00d6 \u00dc \u00e4 \u00f6 \u00fc \u00df  section \u00a7  degree \u00b0')
    add('ASCII:   !"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')
    add('')
    add('Text frame with a graphics frame inside - the two must line up:')
    top = len(rows)
    add('+' + '-' * 38 + '+')
    for _ in range(6):
        add('|' + ' ' * 38 + '|')
    add('+' + '-' * 38 + '+')
    add('')
    add('Grey ramp (bit image):')
    ramp = len(rows)
    for _ in range(3):
        add('')
    add('')
    add('If the frames do not line up vertically, adjust the line feed of the')
    add('printer; if nothing prints in the frames, ESC [ g is not supported.')
    gfx = [(2, top + 1, 36, 6, 'frame'), (0, ramp, 60, 3, 'ramp')]
    lines = page.lines
    while len(rows) < lines:
        add('')
    return Sheet(1, rows[:lines], gfx)


def send(data, target):
    """Write to a device path or hand the raw data to a CUPS queue."""
    target = (target or '').strip()
    if not target:
        raise ValueError('no printer configured')
    if target.startswith('/'):
        with open(target, 'wb') as f:
            f.write(data)
        return
    try:
        p = subprocess.run(['lp', '-d', target, '-o', 'raw'], input=data,
                           capture_output=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError('lp is not installed')
    except subprocess.TimeoutExpired:
        raise RuntimeError('lp timed out')
    if p.returncode:
        msg = (p.stderr or p.stdout or b'lp failed').decode('utf-8', 'replace').strip()
        raise RuntimeError(msg or 'lp failed')


# ------------------------------------------------------------------ old interface
def encode(rows, width=80, page_lines=66, init=True):
    """Plain rows -> PPDS bytes (kept for callers of the first version)."""
    out = bytearray()
    if init:
        out += setup(page_lines)
    lines = 0
    for row in rows:
        if isinstance(row, str):
            chars, attrs = row[:width], []
        else:
            chars, attrs = row[0][:width], row[1]
        out += text_line(chars, attrs) + b'\r\n'
        lines += 1
        if lines % page_lines == 0:
            out += b'\f'
    if lines % page_lines:
        out += b'\f'
    return bytes(out)
