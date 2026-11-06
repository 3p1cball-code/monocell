"""Block letters for headings and large text.

The glyphs are 3x5 pixels. With half blocks (default) two pixel rows share one
cell, so one letter is 3 cells wide and 3 rows tall; setting `half` to false
gives the plain-ASCII variant with one cell per pixel (3x5). Lowercase is folded
to uppercase, umlauts lose their dots and `ß` expands to SS.
"""

FONT = {
    '0': ['###', '# #', '# #', '# #', '###'], '1': [' # ', '## ', ' # ', ' # ', '###'],
    '2': ['###', '  #', '###', '#  ', '###'], '3': ['###', '  #', ' ##', '  #', '###'],
    '4': ['# #', '# #', '###', '  #', '  #'], '5': ['###', '#  ', '###', '  #', '###'],
    '6': ['###', '#  ', '###', '# #', '###'], '7': ['###', '  #', '  #', ' # ', ' # '],
    '8': ['###', '# #', '###', '# #', '###'], '9': ['###', '# #', '###', '  #', '###'],
    'A': [' # ', '# #', '###', '# #', '# #'], 'B': ['## ', '# #', '## ', '# #', '## '],
    'C': [' ##', '#  ', '#  ', '#  ', ' ##'], 'D': ['## ', '# #', '# #', '# #', '## '],
    'E': ['###', '#  ', '## ', '#  ', '###'], 'F': ['###', '#  ', '## ', '#  ', '#  '],
    'G': [' ##', '#  ', '# #', '# #', ' ##'], 'H': ['# #', '# #', '###', '# #', '# #'],
    'I': ['###', ' # ', ' # ', ' # ', '###'], 'J': ['  #', '  #', '  #', '# #', ' # '],
    'K': ['# #', '# #', '## ', '# #', '# #'], 'L': ['#  ', '#  ', '#  ', '#  ', '###'],
    'M': ['# #', '###', '# #', '# #', '# #'], 'N': ['## ', '# #', '# #', '# #', '# #'],
    'O': [' # ', '# #', '# #', '# #', ' # '], 'P': ['## ', '# #', '## ', '#  ', '#  '],
    'Q': [' # ', '# #', '# #', '## ', ' ##'], 'R': ['## ', '# #', '## ', '# #', '# #'],
    'S': [' ##', '#  ', ' # ', '  #', '## '], 'T': ['###', ' # ', ' # ', ' # ', ' # '],
    'U': ['# #', '# #', '# #', '# #', '###'], 'V': ['# #', '# #', '# #', '# #', ' # '],
    'W': ['# #', '# #', '# #', '###', '# #'], 'X': ['# #', '# #', ' # ', '# #', '# #'],
    'Y': ['# #', '# #', ' # ', ' # ', ' # '], 'Z': ['###', '  #', ' # ', '#  ', '###'],
    ':': ['   ', ' # ', '   ', ' # ', '   '], '/': ['  #', '  #', ' # ', '#  ', '#  '],
    '.': ['   ', '   ', '   ', '   ', ' # '], '-': ['   ', '   ', '###', '   ', '   '],
    '+': ['   ', ' # ', '###', ' # ', '   '], '%': ['# #', '  #', ' # ', '#  ', '# #'],
    '=': ['   ', '###', '   ', '###', '   '], '_': ['   ', '   ', '   ', '   ', '###'],
    ' ': ['   '] * 5,
    '(': [' # ', '#  ', '#  ', '#  ', ' # '], ')': [' # ', '  #', '  #', '  #', ' # '],
    ',': ['   ', '   ', '   ', ' # ', '#  '],
    '!': [' # ', ' # ', ' # ', '   ', ' # '], '?': [' ##', '#  ', ' # ', '   ', ' # '],
    "'": [' # ', ' # ', '   ', '   ', '   '],
}
GAP = 1                        # blank pixel columns between glyphs
UMLAUTS = {'Ä': 'A', 'Ö': 'O', 'Ü': 'U', 'ä': 'A', 'ö': 'O', 'ü': 'U'}


def _chars(text):
    out = []
    for c in text:
        if c == 'ß':
            out.extend(('S', 'S'))
        elif c in UMLAUTS:
            out.append(UMLAUTS[c])
        elif c.upper() in FONT:
            out.append(c.upper())
        elif c == '\t':
            out.append(' ')
        else:
            out.append('?')
    return out


def cell_width(text, scale=1):
    chars = _chars(text)
    if not chars:
        return 0
    return len(chars) * (3 + GAP) * scale - GAP * scale


def _pixel_rows(line, scale):
    """Pixel matrix of one line, expanded by scale horizontally and vertically."""
    rows = []
    for src in range(5):
        row = []
        for i, c in enumerate(_chars(line)):
            glyph = FONT.get(c, FONT[' '])
            for px in glyph[src]:
                row.extend([px == '#'] * scale)
            if i + 1 < len(_chars(line)):
                row.extend([False] * (GAP * scale))
        for _ in range(scale):
            rows.append(row)
    return rows


def _half_rows(pixels):
    out = []
    for y in range(0, len(pixels), 2):
        top = pixels[y]
        bottom = pixels[y + 1] if y + 1 < len(pixels) else [False] * len(top)
        line = []
        for a, b in zip(top, bottom):
            line.append('█' if a and b else '▀' if a else '▄' if b else ' ')
        out.append(''.join(line))
    return out


def _ascii_rows(pixels):
    return [''.join('#' if p else ' ' for p in row) for row in pixels]


def render_line(text, scale=1, half=True):
    pixels = _pixel_rows(text, scale)
    return _half_rows(pixels) if half else _ascii_rows(pixels)


def render_lines(text, width, scale=1, half=True):
    """Wrap text into lines that fit `width` cells, then render each to rows."""
    words = text.split()
    if not words:
        return []
    max_word = max(1, (width + GAP * scale) // ((3 + GAP) * scale))
    lines = []
    cur = ''
    for word in words:
        while len(word) > max_word:
            chunk, word = word[:max_word], word[max_word:]
            if cur:
                lines.append(cur)
                cur = ''
            lines.append(chunk)
        if not cur:
            cur = word
        elif cell_width(cur) + GAP * scale + cell_width(word) <= width:
            cur += ' ' + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    out = []
    for i, line in enumerate(lines):
        if i:
            out.append('')
        out.extend(render_line(line, scale, half))
    return out


def colored_rows(text, width, scale=1, half=True):
    """(chars, mask) rows; mask marks the glyph pixels for per-cell colouring."""
    rows = render_lines(text, width, scale, half)
    out = []
    for row in rows:
        out.append((row, [c not in (' ',) for c in row]))
    return out
