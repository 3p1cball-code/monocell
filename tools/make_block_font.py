#!/usr/bin/env python3
"""Build the Writer's pixel FIGlet fonts (Block, Block 3D).

    tools/make_block_font.py monocell/fonts/figlet

The letters are the 3x5 pixel alphabet of monocell/fbmode.py (the one the
graphics-mode OSD uses), two cells per pixel - a console cell is about twice
as tall as wide, so the letters stay square, like the Monocell wordmark
(monocell/logo.py). Block 3D adds an extrusion one cell to the right and one
row down, so the font height is 7 rows: five of letter, one blank row on top
(the dots of the umlauts go there) and the shadow.

Face '#', extrusion ':' - one ink, so it also works on the Linux console
(which has no block graphics) and on a dot-matrix printer. Lower-case comes
from the upper-case glyphs (as in the OSD); the umlauts are the base letter
with two dots in the top row.

Rows of 3 pixels are narrow enough that about nine letters fit on a 68-column
line - a heading font for real documents, not a poster. The extrusion only
fills cells the face does not use, so it stays a thin edge and not a second
stroke inside the letters.
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.fbmode import FONT as PIX          # noqa: E402

SX = 2                      # cells per pixel
FACE, SIDE = '#', ':'
HARD = '$'
HEIGHT = 7                  # 5 pixel rows + umlaut row + shadow row
W = 3                       # glyph width in pixels

# a few glyphs the OSD font does not need but a heading might
EXTRA = {
    '!': [' # ', ' # ', ' # ', '   ', ' # '], '"': ['# #', '# #', '   ', '   ', '   '],
    '#': ['# #', '###', '# #', '###', '# #'], '$': [' ##', '#  ', ' # ', '  #', '## '],
    '&': [' # ', '# #', ' # ', '# #', ' ##'], "'": [' # ', ' # ', '   ', '   ', '   '],
    '*': ['   ', '# #', ' # ', '# #', '   '], ';': ['   ', ' # ', '   ', ' # ', '#  '],
    '<': ['  #', ' # ', '#  ', ' # ', '  #'], '>': ['#  ', ' # ', '  #', ' # ', '#  '],
    '?': ['###', '  #', ' ##', '   ', ' # '], '@': [' ##', '# #', '###', '#  ', ' ##'],
    '[': [' ##', ' # ', ' # ', ' # ', ' ##'], '\\': ['#  ', '#  ', ' # ', '  #', '  #'],
    ']': ['## ', ' # ', ' # ', ' # ', '## '], '^': [' # ', '# #', '   ', '   ', '   '],
    '`': ['#  ', ' # ', '   ', '   ', '   '], '{': [' ##', ' # ', '#  ', ' # ', ' ##'],
    '|': [' # ', ' # ', ' # ', ' # ', ' # '], '}': ['## ', ' # ', '  #', ' # ', '## '],
    '~': ['   ', ' # ', '# #', '   ', '   '],
    '\u00df': ['## ', '# #', '## ', '# #', ' ##'],          # ß
    '\u00a7': [' ##', '## ', ' # ', ' ##', '## '],           # §
    '\u00b0': [' # ', '# #', ' # ', '   ', '   '],           # °
    '\u20ac': [' ##', '## ', '###', '## ', ' ##'],           # €
    '\u2013': ['   ', '   ', '###', '   ', '   '],           # –
}
UMLAUT = {'\u00c4': 'A', '\u00d6': 'O', '\u00dc': 'U', '\u00e4': 'A', '\u00f6': 'O',
          '\u00fc': 'U'}
CHARS = list(range(32, 127))


def pixels(ch):
    """5 rows x 3 columns of the glyph; lower-case falls back to upper-case."""
    if ch in EXTRA:
        return [list(r) for r in EXTRA[ch]]
    base = UMLAUT.get(ch, ch)
    g = PIX.get(base.upper() if ch.isascii() else base) or PIX.get(ch) or PIX[' ']
    rows = [list(r) for r in g]
    if ch in UMLAUT:                       # two dots in the blank row on top
        return rows
    return rows


def cells(px, offset):
    """5 pixel rows + one blank row (umlaut dots) + one shadow row.

    `offset` is the extrusion in cells: SX gives a clean one-pixel diagonal,
    a smaller value a finer (but muddier) edge."""
    rows = [[' '] * W] + px
    h = len(rows)
    Wc = W * SX + offset
    out = [[' '] * Wc for _ in range(HEIGHT)]
    face = set()
    for r in range(h):
        for c in range(W):
            if rows[r][c] == '#':
                for k in range(SX):
                    face.add((r, c * SX + k))
    for r, x in face:                      # the extrusion, one cell right and down
        if offset and r + 1 < HEIGHT and x + offset < Wc and (r + 1, x + offset) not in face:
            out[r + 1][x + offset] = SIDE
    for r, x in face:
        out[r][x] = FACE
    return [''.join(row) for row in out]


def glyph(ch, offset):
    if ch == ' ':
        return [' ' * (3 * SX + offset)] * HEIGHT
    if ch in UMLAUT:
        px = [list(r) for r in (EXTRA.get(ch) or
                                (PIX.get(UMLAUT[ch].upper()) or PIX[' ']))]
        rows = cells(px, offset)
        # dots into row 0 above the two upper strokes
        ink = [c for c in range(len(rows[0])) if any(rows[r][c] not in ' ' for r in range(2, 7))]
        if ink:
            a, b = ink[0], ink[-1]
            p, q = a + (b - a) // 3, b - (b - a) // 3
            s = list(rows[0])
            for x in (p, q if q != p else min(len(s) - 1, p + 2)):
                s[x] = FACE
            rows[0] = ''.join(s)
        return rows
    return cells(pixels(ch), offset)


def write_flf(path, offset, title):
    comments = ['%s - Monocell Writer pixel font' % title,
                '3x5 pixel letters of monocell/fbmode.py, two cells per pixel;',
                'face "#", extrusion ":" one cell right and one row down.',
                'Built by tools/make_block_font.py.']
    out = ['flf2a%s %d 6 -1 -1 %d 0 0' % (HARD, HEIGHT, len(comments))]
    out += comments
    for code in CHARS:
        g = glyph(chr(code), offset)
        out += [line + '@' for line in g[:-1]] + [g[-1] + '@@']
    for ch in sorted(set(EXTRA) | set(UMLAUT)):
        g = glyph(ch, offset)
        out.append('%d  U+%04X' % (ord(ch), ord(ch)))
        out += [line + '@' for line in g[:-1]] + [g[-1] + '@@']
    with open(path, 'w', encoding='latin-1') as f:
        f.write('\n'.join(out) + '\n')


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    write_flf(os.path.join(argv[0], 'block.flf'), 0, 'Block')
    write_flf(os.path.join(argv[0], 'block3d.flf'), 1, 'Block 3D')
    print("wrote block.flf and block3d.flf to", argv[0])


if __name__ == '__main__':
    main(sys.argv[1:])
