#!/usr/bin/env python3
"""Build minimal-cell, Monocell's own tiny console font (PSF2).

    tools/make_minimal_cell.py              # writes monocell/fonts/MinimalCell-*.psf.gz
    tools/make_minimal_cell.py --preview DIR  # plus PNG previews

Two cells come out of one design:

    MinimalCell-5x7.psf.gz   5x7 pixels - 204x109 characters on 1024x768
    MinimalCell-5x8.psf.gz   5x8, the same glyphs with one more row of line
                             spacing - 204x96

Metrics of the 5x7 cell (rows 0..6, columns 0..4):

    row 0      accents, umlaut dots, i/j dots, tops of b d f h k l
    rows 1-5   capitals and digits (5 px)
    rows 2-5   x-height (4 px)
    row 6      descenders (g j p q y , ;) and the bottom of ( ) [ ] { } |
    column 4   the gap to the next character

So capitals of two lines are two rows apart (row 6 and the next row 0 are
empty unless a descender or an accent is there), and ascenders stand one
pixel above the capitals, which keeps h/n, b/o, d/a and l/I/1 apart even at
this size. Glyphs are 4 px wide; letters that need a centre stem (I T Y i l
1 ! | + and the like) are 3 px wide in columns 1-3, which is the exact centre
of the 5-pixel cell. M W m w X are the exceptions: in four columns they turn
into H-like blobs (tried, unreadable), so they take all five and touch their
neighbour - 'summer' and 'MAXIMUM' stay readable that way. Capitals with a
flat top (E I N U Y) get a 4-row body under an accent, so Ü is not just a
taller U.

Box drawing and block elements use the whole cell, all 5x7 (5x8) pixels, so
lines join and blocks tile without gaps: the font goes from empty (space)
through the shades (20 %, ~50 %, 80 %) to full (U+2588). Half and quadrant
blocks split the cell the same way everywhere (upper 3 / lower 4 rows - 4/4
in 5x8 - and left 2 / right 3 columns), so an upper half next to a lower half
in inverted colours fits exactly.

Glyph positions follow Latin-1 (ASCII at 32-126, Latin-1 at 160-255), so the
font also works without its unicode table; everything else sits in the free
slots; glyphs that look the same share one. The table maps many more
characters onto the nearest glyph (accented Latin letters onto their base
letter, heavy and mixed box lines onto the light ones), and U+FFFD onto a box
with a question mark.

License: the glyphs are an original design for Monocell (MIT, like Monocell).
"""
import argparse
import gzip
import os
import struct
import sys
import unicodedata

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUT_DIR = os.path.join(ROOT, 'monocell', 'fonts')
W = 5
H = 7

# ---------------------------------------------------------------- the design
# One glyph per line: seven rows of five pixels, '#' ink, '.' empty.
# Rows that are left out at the end are empty.
DESIGN = r"""
  ..... ..... ..... ..... ..... ..... .....
! ..... ..#.. ..#.. ..#.. ..... ..#.. .....
" ..... .#.#. .#.#. ..... ..... ..... .....
# ..... .#.#. ####. .#.#. ####. .#.#. .....
$ .#... .###. #.... .##.. ...#. ###.. .#...
% ..... #..#. ...#. ..#.. .#... #..#. .....
& ..... .#... #.#.. .#... #.##. .#.#. .....
' ..... ..#.. ..#.. ..... ..... ..... .....
( ..... ...#. ..#.. ..#.. ..#.. ..#.. ...#.
) ..... .#... ..#.. ..#.. ..#.. ..#.. .#...
* ..... .#.#. ..#.. .#.#. ..... ..... .....
+ ..... ..... ..#.. .###. ..#.. ..... .....
, ..... ..... ..... ..... ..... ..#.. .#...
- ..... ..... ..... .###. ..... ..... .....
. ..... ..... ..... ..... ..... ..#.. .....
/ ..... ...#. ...#. ..#.. .#... .#... .....
0 ..... .##.. #..#. #.##. ##.#. .##.. .....
1 ..... ..#.. .##.. ..#.. ..#.. .###. .....
2 ..... .##.. #..#. ..#.. .#... ####. .....
3 ..... ###.. ...#. .##.. ...#. ###.. .....
4 ..... #..#. #..#. ####. ...#. ...#. .....
5 ..... ####. #.... ###.. ...#. ###.. .....
6 ..... .##.. #.... ###.. #..#. .##.. .....
7 ..... ####. ...#. ..#.. .#... .#... .....
8 ..... .##.. #..#. .##.. #..#. .##.. .....
9 ..... .##.. #..#. .###. ...#. .##.. .....
: ..... ..... ..#.. ..... ..... ..#.. .....
; ..... ..... ..#.. ..... ..... ..#.. .#...
< ..... ...#. ..#.. .#... ..#.. ...#. .....
= ..... ..... .###. ..... .###. ..... .....
> ..... .#... ..#.. ...#. ..#.. .#... .....
? ..... .##.. #..#. ..#.. ..... ..#.. .....
@ ..... .##.. #.##. #.##. #.... .##.. .....
A ..... .##.. #..#. ####. #..#. #..#. .....
B ..... ###.. #..#. ###.. #..#. ###.. .....
C ..... .###. #.... #.... #.... .###. .....
D ..... ###.. #..#. #..#. #..#. ###.. .....
E ..... ####. #.... ###.. #.... ####. .....
F ..... ####. #.... ###.. #.... #.... .....
G ..... .###. #.... #.##. #..#. .###. .....
H ..... #..#. #..#. ####. #..#. #..#. .....
I ..... .###. ..#.. ..#.. ..#.. .###. .....
J ..... ...#. ...#. ...#. #..#. .##.. .....
K ..... #..#. #.#.. ##... #.#.. #..#. .....
L ..... #.... #.... #.... #.... ####. .....
M ..... #...# ##.## #.#.# #...# #...# .....
N ..... #..#. ##.#. #.##. #..#. #..#. .....
O ..... .##.. #..#. #..#. #..#. .##.. .....
P ..... ###.. #..#. ###.. #.... #.... .....
Q ..... .##.. #..#. #..#. #.#.. .#.#. .....
R ..... ###.. #..#. ###.. #.#.. #..#. .....
S ..... .###. #.... .##.. ...#. ###.. .....
T ..... .###. ..#.. ..#.. ..#.. ..#.. .....
U ..... #..#. #..#. #..#. #..#. .##.. .....
V ..... #..#. #..#. #..#. .##.. .##.. .....
W ..... #...# #...# #.#.# #.#.# .#.#. .....
X ..... #...# .#.#. ..#.. .#.#. #...# .....
Y ..... .#.#. .#.#. ..#.. ..#.. ..#.. .....
Z ..... ####. ..#.. .#... #.... ####. .....
[ ..... .###. .#... .#... .#... .#... .###.
\ ..... .#... .#... ..#.. ...#. ...#. .....
] ..... .###. ...#. ...#. ...#. ...#. .###.
^ ..... ..#.. .#.#. ..... ..... ..... .....
_ ..... ..... ..... ..... ..... ..... ####.
` ..... .#... ..#.. ..... ..... ..... .....
a ..... ..... .##.. ...#. .###. #.##. .....
b #.... #.... ###.. #..#. #..#. ###.. .....
c ..... ..... .###. #.... #.... .###. .....
d ...#. ...#. .###. #..#. #..#. .###. .....
e ..... ..... .##.. ####. #.... .###. .....
f ..##. .#... ###.. .#... .#... .#... .....
g ..... ..... .###. #..#. .###. ...#. ###..
h #.... #.... ###.. #..#. #..#. #..#. .....
i ..#.. ..... .##.. ..#.. ..#.. .###. .....
j ...#. ..... ..##. ...#. ...#. ...#. .##..
k #.... #.... #.#.. ##... #.#.. #..#. .....
l .##.. ..#.. ..#.. ..#.. ..#.. ...#. .....
m ..... ..... ####. #.#.# #.#.# #.#.# .....
n ..... ..... ###.. #..#. #..#. #..#. .....
o ..... ..... .##.. #..#. #..#. .##.. .....
p ..... ..... ###.. #..#. ###.. #.... #....
q ..... ..... .###. #..#. .###. ...#. ...#.
r ..... ..... #.##. ##... #.... #.... .....
s ..... ..... .###. ##... ..##. ###.. .....
t ..... .#... ###.. .#... .#... ..##. .....
u ..... ..... #..#. #..#. #..#. .###. .....
v ..... ..... .#.#. .#.#. .#.#. ..#.. .....
w ..... ..... #.#.# #.#.# #.#.# .#.#. .....
x ..... ..... #..#. .##.. .##.. #..#. .....
y ..... ..... #..#. #..#. .###. ...#. .##..
z ..... ..... ####. ..#.. .#... ####. .....
{ ..... ...#. ..#.. .##.. ..#.. ..#.. ...#.
| ..... ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
} ..... .#... ..#.. ..##. ..#.. ..#.. .#...
~ ..... ..... .#.#. #.#.. ..... ..... .....
¡ ..... ..#.. ..... ..#.. ..#.. ..#.. .....
¢ ..... ..#.. .###. #.#.. #.#.. .###. ..#..
£ ..... ..##. .#... ###.. .#... ####. .....
¤ ..... #..#. .##.. #..#. .##.. #..#. .....
¥ ..... .#.#. .#.#. .###. ..#.. .###. ..#..
¦ ..... ..#.. ..#.. ..... ..#.. ..#.. .....
§ ..##. .#... ..#.. .#.#. ..#.. ...#. .##..
¨ ..... .#.#. ..... ..... ..... ..... .....
© .###. #...# #.#.# #.#.# #...# .###. .....
ª ..... .##.. .###. ..... .###. ..... .....
« ..... ..... .#.#. #.#.. .#.#. ..... .....
¬ ..... ..... ..... ####. ...#. ..... .....
® .###. #...# #.#.# #.#.# #...# .###. .....
¯ ..... .###. ..... ..... ..... ..... .....
° ..... .#... #.#.. .#... ..... ..... .....
± ..... ..#.. .###. ..#.. ..... .###. .....
² .##.. ...#. ..#.. .###. ..... ..... .....
³ .##.. ..##. ...#. .##.. ..... ..... .....
´ ..... ..#.. .#... ..... ..... ..... .....
µ ..... ..... #..#. #..#. #..#. ###.. #....
¶ ..... .###. ##.#. .#.#. .#.#. .#.#. .....
· ..... ..... ..... ..#.. ..... ..... .....
¸ ..... ..... ..... ..... ..... ..#.. .#...
¹ ..#.. .##.. ..#.. ..#.. ..... ..... .....
º ..... .#... #.#.. .#... ###.. ..... .....
» ..... ..... #.#.. .#.#. #.#.. ..... .....
¼ #.... #.... #.#.. ..#.. .##.. ..#.. .....
½ #.... #.... #.##. ...#. ..#.. ..##. .....
¾ ##... .#... ##... ..#.. .##.. ..#.. .....
¿ ..... ..#.. ..... ..#.. #.... .###. .....
Æ ..... .###. #.#.. ####. #.#.. #.##. .....
Ç ..... .###. #.... #.... .###. ..#.. .#...
Ð ..... ###.. #..#. ##.#. #..#. ###.. .....
× ..... ..... .#.#. ..#.. .#.#. ..... .....
Ø ...#. .##.. #.##. #..#. ##.#. .##.. #....
Þ ..... #.... ###.. #..#. ###.. #.... .....
ß .##.. #..#. #.#.. #..#. #..#. #.#.. .....
æ ..... ..... .###. .###. ##... .###. .....
ç ..... ..... .###. #.... .###. ..#.. .#...
ð ..... .#.#. ..#.. .###. #..#. .##.. .....
÷ ..... ..#.. ..... .###. ..... ..#.. .....
ø ..... ...#. .##.. #.##. ##.#. .##.. #....
þ ..... #.... ###.. #..#. ###.. #.... #....
"""

# Accented letters: base glyph + accent. Capitals keep their body in rows
# 1-5 and the accent goes into row 0; lower-case bodies start in row 2, so
# their accents may use rows 0 and 1.
ACCENT_CAP = {          # one row, row 0
    'grave': '.#...', 'acute': '..#..', 'circ': '.##..', 'tilde': '####.',
    'diaer': '#..#.', 'ring': '.##..',
}
ACCENT_LOW = {          # rows 0 and 1
    'grave': ('.#...', '..#..'), 'acute': ('..#..', '.#...'),
    'circ': ('.##..', '#..#.'), 'tilde': ('.#.#.', '#.#..'),
    'diaer': ('#..#.', '.....'), 'ring': ('.##..', '.##..'),
}
# Capitals with a flat top would melt into their accent (an Ü would only look
# like a taller U), so these get a 4-row body under the accent and a blank row
# in between - like the umlauts of the X11 5x7 font.
CAP4 = {
    'E': ('####.', '###..', '#....', '####.'),
    'I': ('.###.', '..#..', '..#..', '.###.'),
    'N': ('#..#.', '##.#.', '#.##.', '#..#.'),
    'U': ('#..#.', '#..#.', '#..#.', '.##..'),
    'Y': ('.#.#.', '.#.#.', '..#..', '..#..'),
}
ACCENTED = {
    'À': ('A', 'grave'), 'Á': ('A', 'acute'), 'Â': ('A', 'circ'), 'Ã': ('A', 'tilde'),
    'Ä': ('A', 'diaer'), 'Å': ('A', 'ring'), 'È': ('E', 'grave'), 'É': ('E', 'acute'),
    'Ê': ('E', 'circ'), 'Ë': ('E', 'diaer'), 'Ì': ('I', 'grave'), 'Í': ('I', 'acute'),
    'Î': ('I', 'circ'), 'Ï': ('I', 'diaer'), 'Ñ': ('N', 'tilde'), 'Ò': ('O', 'grave'),
    'Ó': ('O', 'acute'), 'Ô': ('O', 'circ'), 'Õ': ('O', 'tilde'), 'Ö': ('O', 'diaer'),
    'Ù': ('U', 'grave'), 'Ú': ('U', 'acute'), 'Û': ('U', 'circ'), 'Ü': ('U', 'diaer'),
    'Ý': ('Y', 'acute'),
    'à': ('a', 'grave'), 'á': ('a', 'acute'), 'â': ('a', 'circ'), 'ã': ('a', 'tilde'),
    'ä': ('a', 'diaer'), 'å': ('a', 'ring'), 'è': ('e', 'grave'), 'é': ('e', 'acute'),
    'ê': ('e', 'circ'), 'ë': ('e', 'diaer'), 'ì': ('ı', 'grave'), 'í': ('ı', 'acute'),
    'î': ('ı', 'circ'), 'ï': ('ı', 'diaer'), 'ñ': ('n', 'tilde'), 'ò': ('o', 'grave'),
    'ó': ('o', 'acute'), 'ô': ('o', 'circ'), 'õ': ('o', 'tilde'), 'ö': ('o', 'diaer'),
    'ù': ('u', 'grave'), 'ú': ('u', 'acute'), 'û': ('u', 'circ'), 'ü': ('u', 'diaer'),
    'ý': ('y', 'acute'), 'ÿ': ('y', 'diaer'),
}

# more symbols (outside Latin-1); full-cell graphics are generated below
EXTRA = r"""
ı ..... ..... .##.. ..#.. ..#.. .###. .....
€ ..... ..##. .#... ###.. .#... ..##. .....
‘ ..... ...#. ..#.. ..... ..... ..... .....
’ ..... ..#.. .#... ..... ..... ..... .....
“ ..... .#.#. #.#.. ..... ..... ..... .....
” ..... .#.#. #.#.. ..... ..... ..... .....
„ ..... ..... ..... ..... ..... .#.#. #.#..
‹ ..... ..... ..#.. .#... ..#.. ..... .....
› ..... ..... .#... ..#.. .#... ..... .....
– ..... ..... ..... ####. ..... ..... .....
… ..... ..... ..... ..... ..... #.#.# .....
• ..... ..... .##.. .##.. ..... ..... .....
‣ ..... ..... .#... .##.. .#... ..... .....
† ..... ..#.. .###. ..#.. ..#.. ..#.. .....
← ..... ..... .#... ####. .#... ..... .....
→ ..... ..... ..#.. ####. ..#.. ..... .....
↑ ..... ..#.. .###. ..#.. ..#.. ..#.. .....
↓ ..... ..#.. ..#.. ..#.. .###. ..#.. .....
↔ ..... ..... .#.#. ##### .#.#. ..... .....
↕ ..... ..#.. .###. ..#.. .###. ..#.. .....
▲ ..... ..... ..#.. .###. ##### ..... .....
▼ ..... ..... ##### .###. ..#.. ..... .....
▶ ..... .#... .##.. .###. .##.. .#... .....
◀ ..... ...#. ..##. .###. ..##. ...#. .....
■ ..... ..... .###. .###. .###. ..... .....
□ ..... ..... .###. .#.#. .###. ..... .....
▪ ..... ..... ..... .##.. .##.. ..... .....
● ..... ..... .###. .###. .###. ..... .....
○ ..... ..... .###. .#.#. .###. ..... .....
◆ ..... ..#.. .###. ##### .###. ..#.. .....
♥ ..... .#.#. ##### .###. ..#.. ..... .....
♦ ..... ..#.. .###. ##### .###. ..#.. .....
♣ ..... .###. .###. ##### #.#.# ..#.. .....
♠ ..... ..#.. .###. ##### ##### ..#.. .....
♪ ..... ..##. ..#.# ..#.. ##... ##... .....
✓ ..... ..... ...#. ..#.. #.#.. .#... .....
✗ ..... ..... #..#. .##.. .##.. #..#. .....
≈ ..... ..... .#.#. #.#.. .#.#. #.#.. .....
≠ ..... ...#. .###. ..#.. .###. .#... .....
≤ ..... ...#. ..#.. ...#. ..... .###. .....
≥ ..... .#... ..#.. .#... ..... .###. .....
≡ ..... .###. ..... .###. ..... .###. .....
∞ ..... ..... ..... ##.## #.#.# ##.## .....
√ ..... ...## ...#. ...#. #.#.. .#... .....
π ..... ..... ####. .#.#. .#.#. .#.#. .....
Σ ..... ####. .#... ..#.. .#... ####. .....
Ω ..... .##.. #..#. #..#. .##.. #..#. .....
α ..... ..... .#.#. #.#.. #.#.. .#.#. .....
β ..... .##.. #..#. ###.. #..#. ###.. #....
� ##### #.### ##.## ##### ##.## ##### .....
"""

# characters drawn with an existing glyph (besides the NFKD fallback)
ALIASES = {
    '\u2007': ' ', '\u2009': ' ', '\u202f': ' ',
    '\u2010': '-', '\u2011': '-', '\u2212': '-', '\u2043': '-',
    '\u2012': '–', '\u02c6': '^', '\u02dc': '~', '\u2032': "'", '\u2033': '"',
    '\u201a': ',', '\u201b': '‘', '\u201f': '“', '\u00b5': 'µ', '\u2219': '·',
    '\u22c5': '·', '\u2027': '·', '\u25cf': '●', '\u2b24': '●', '\u25c9': '●',
    '\u25cb': '○', '\u25ef': '○', '\u25e6': '○', '\u25aa': '▪', '\u25fc': '■',
    '\u25a0': '■', '\u25fb': '□', '\u25a1': '□', '\u25ab': '□', '\u25b8': '▶',
    '\u25ba': '▶', '\u25b6': '▶', '\u23f5': '▶', '\u25c2': '◀', '\u25c4': '◀',
    '\u25b4': '▲', '\u25be': '▼', '\u2714': '✓', '\u2713': '✓', '\u2718': '✗',
    '\u2715': '✗', '\u2716': '✗', '\u00d7': '×', '\u2665': '♥', '\u2764': '♥',
    '\u266b': '♪', '\u266c': '♪', '\u2666': '♦', '\u25c6': '◆', '\u03bc': 'µ',
    '\u2126': 'Ω', '\u2211': 'Σ', '\u220f': 'π', '\u0131': 'ı', '\u1e9e': 'ß',
    '\u2192': '→', '\u27f6': '→', '\u2794': '→', '\u279c': '→', '\u2190': '←',
    '\u27f5': '←', '\u21d2': '→', '\u21d0': '←', '\u2264': '≤', '\u2265': '≥',
    '\u2261': '≡', '\u2248': '≈', '\u2260': '≠', '\u2022': '•', '\u2023': '‣',
    '\u2030': '%', '\u2044': '/', '\u2215': '/', '\u2216': '\\', '\u2223': '|',
    '\u02bc': '’', '\u02bb': '‘', '\u00b4': '´', '\u2018': '‘', '\u2019': '’',
    '\u201c': '“', '\u201d': '”', '\u201e': '„', '\u2039': '‹', '\u203a': '›',
    '\u276f': '›', '\u276e': '‹', '\u27e9': '›', '\u27e8': '‹', '\u2026': '…',
    '\u2014': '─', '\u2015': '─', '\u2500': '─', '\u23af': '─',
}


# ---------------------------------------------------------------- generated glyphs
# 20 % / ~50 % / 80 % shades. 5 and 7 are odd, so a plain checkerboard shows a
# seam at every cell edge; these patterns tile with the cell itself. The 20/80 %
# ones are a lattice (one pixel per row, two columns further each row), the
# middle one was found by a small optimisation for even coverage on the 5xH
# torus (simulated annealing, minimising same-colour neighbours under a small
# Gaussian; the result is fixed here so the build stays reproducible).
SHADE_MID = {
    7: ('#.#..', '.#..#', '#.##.', '.#..#', '#.##.', '.#..#', '.#.##'),
    8: ('.#.##', '#.#..', '.#.##', '#.#..', '#..##', '.##..', '.#.##', '#.#..'),
}
UP_ROWS = {7: 3, 8: 4}          # upper half block: rows 0..n-1
LEFT_COLS = 2                   # left half block: columns 0..1
CY, CX = 3, 2                   # where box lines cross: text middle, cell centre


def blank(h):
    return [[0] * W for _ in range(h)]


def box(h, up, down, left, right):
    """Box drawing from arm weights (0 none, 1 single, 2 double); the lines run
    to the cell edges, so neighbours join. Double lines are two strokes; at
    corners and junctions the strokes meet like the CP437 ones."""
    g = blank(h)
    double = 2 in (up, down, left, right)
    vs = (CX - 1, CX + 1) if double and (up or down) else (CX,)
    hs = (CY - 1, CY + 1) if double and (left or right) else (CY,)

    def put(r, c):
        g[r][c] = 1

    for arm, sign in ((up, -1), (down, 1)):
        if not arm:
            continue
        for c in vs:
            if len(hs) == 1:
                end = CY
            else:
                # the stroke on the side of a horizontal arm stops at the near
                # horizontal stroke, the other one runs on to the far stroke
                side = left if c < CX else right
                near, far = (CY - 1, CY + 1) if sign < 0 else (CY + 1, CY - 1)
                end = near if side else far
            for r in (range(0, end + 1) if sign < 0 else range(end, h)):
                put(r, c)
    for arm, sign in ((left, -1), (right, 1)):
        if not arm:
            continue
        for r in hs:
            if len(vs) == 1:
                end = CX
            else:
                side = up if r < CY else down
                near, far = (CX - 1, CX + 1) if sign < 0 else (CX + 1, CX - 1)
                end = near if side else far
            for c in (range(0, end + 1) if sign < 0 else range(end, W)):
                put(r, c)
    return g


def parse_box_name(name):
    """(up, down, left, right) weights from a BOX DRAWINGS name - heavy counts
    as single (one weight for the light and the heavy line), double as 2."""
    if not name.startswith('BOX DRAWINGS ') or any(
            w in name for w in ('DASH', 'DIAGONAL', 'ARC')):
        return None
    arms = [0, 0, 0, 0]
    weight = 1
    for w in name[len('BOX DRAWINGS '):].split():
        if w in ('LIGHT', 'HEAVY', 'SINGLE'):
            weight = 1
        elif w == 'DOUBLE':
            weight = 2
        elif w == 'HORIZONTAL':
            arms[2] = arms[3] = weight
        elif w == 'VERTICAL':
            arms[0] = arms[1] = weight
        elif w in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            arms[('UP', 'DOWN', 'LEFT', 'RIGHT').index(w)] = weight
    return tuple(arms) if any(arms) else None


def box_set():
    """{char: arms}: every light/heavy/double/mixed box character of U+2500-257F."""
    out = {}
    for cp in range(0x2500, 0x2580):
        arms = parse_box_name(unicodedata.name(chr(cp), ''))
        if arms:
            out[chr(cp)] = arms
    return out


def box_glyphs(h):
    """Pure light and pure double lines get their own glyph; mixed and heavy
    ones are drawn with the light glyph of the same shape (see unicode_table)."""
    out = {}
    for ch, arms in box_set().items():
        name = unicodedata.name(ch)
        weights = {a for a in arms if a}
        if 'HEAVY' in name or len(weights) > 1:
            continue
        out[ch] = box(h, *arms)
    for ch, arms in (('╭', (0, 1, 0, 1)), ('╮', (0, 1, 1, 0)),
                     ('╯', (1, 0, 1, 0)), ('╰', (1, 0, 0, 1))):
        g = box(h, *arms)
        g[CY][CX] = 0                   # the corner pixel goes: a round corner
        out[ch] = g
    rise, fall = blank(h), blank(h)
    for r in range(h):
        rise[r][round((h - 1 - r) * (W - 1) / (h - 1))] = 1
        fall[r][round(r * (W - 1) / (h - 1))] = 1
    out['╱'], out['╲'] = rise, fall
    out['╳'] = [[a | b for a, b in zip(x, y)] for x, y in zip(rise, fall)]
    return out


def block_glyphs(h):
    """Block elements, shades and quadrants - always the whole cell."""
    def rect(r0, r1, c0, c1):
        g = blank(h)
        for r in range(r0, r1):
            for c in range(c0, c1):
                g[r][c] = 1
        return g

    up, lc = UP_ROWS[h], LEFT_COLS
    out = {'█': rect(0, h, 0, W), '▀': rect(0, up, 0, W), '▄': rect(up, h, 0, W),
           '▌': rect(0, h, 0, lc), '▐': rect(0, h, lc, W),
           '▔': rect(0, 1, 0, W), '▕': rect(0, h, W - 1, W)}
    for n, ch in enumerate('▁▂▃▄▅▆▇', 1):           # lower n eighths
        if ch not in out:
            rows = max(1, min(h - 1, round(n * h / 8)))
            out[ch] = rect(h - rows, h, 0, W)
    for n, ch in enumerate('▏▎▍▌▋▊▉', 1):           # left n eighths
        if ch not in out:
            out[ch] = rect(0, h, 0, max(1, min(W - 1, round(n * W / 8))))
    quads = {'▘': 'a', '▝': 'b', '▖': 'c', '▗': 'd', '▚': 'ad', '▞': 'bc',
             '▙': 'acd', '▛': 'abc', '▜': 'abd', '▟': 'bcd'}
    for ch, parts in quads.items():
        g = blank(h)
        for p in parts:
            part = rect(*(((0, up) if p in 'ab' else (up, h)) +
                          ((0, lc) if p in 'ac' else (lc, W))))
            g = [[a | b for a, b in zip(x, y)] for x, y in zip(g, part)]
        out[ch] = g
    out['░'] = [[1 if (c + 2 * r) % 5 == 0 else 0 for c in range(W)] for r in range(h)]
    out['▒'] = [[1 if p == '#' else 0 for p in row] for row in SHADE_MID[h]]
    out['▓'] = [[0 if (c + 2 * r) % 5 == 0 else 1 for c in range(W)] for r in range(h)]
    return out


# ---------------------------------------------------------------- assembly
def parse(text):
    out = {}
    for line in text.strip('\n').split('\n'):
        if not line.strip():
            continue
        ch, rows = line[0], line[2:].split()
        if len(rows) > H or any(len(r) != W or set(r) - set('.#') for r in rows):
            raise ValueError('bad glyph %r: %r' % (ch, line))
        if ch in out:
            raise ValueError('duplicate glyph %r' % ch)
        rows += ['.' * W] * (H - len(rows))
        out[ch] = [[1 if p == '#' else 0 for p in r] for r in rows]
    return out


def accented(glyphs, base, acc):
    cap = base.isupper()
    if cap and base in CAP4:
        rows = (ACCENT_CAP[acc], '.....') + CAP4[base] + ('.....',)
        return [[1 if p == '#' else 0 for p in r] for r in rows]
    g = [row[:] for row in glyphs[base]]
    pats = (ACCENT_CAP[acc],) if cap else ACCENT_LOW[acc]
    for r, pat in enumerate(pats):
        g[r] = [1 if p == '#' else 0 for p in pat]
    return g


def design(h):
    """{char: rows} for cell height h (7, or 8 with one more row of spacing)."""
    glyphs = parse(DESIGN)
    for ch, g in parse(EXTRA).items():
        if ch in glyphs:
            raise ValueError('duplicate glyph %r' % ch)
        glyphs[ch] = g
    for ch, (base, acc) in ACCENTED.items():
        glyphs[ch] = accented(glyphs, base, acc)
    glyphs['\u00a0'] = glyphs[' ']         # no-break space, soft hyphen: their own
    glyphs['\u00ad'] = glyphs['-']         # Latin-1 slots, for use without the table
    for ch, g in glyphs.items():
        glyphs[ch] = g + blank(h - H)
    glyphs.update(box_glyphs(h))
    glyphs.update(block_glyphs(h))
    return glyphs


def order(glyphs):
    """Glyph slots: Latin-1 at its own code points (the font also works without
    its unicode table), the other glyphs in the free slots. Glyphs that look the
    same share a slot."""
    slots = [None] * 512
    seen = {}
    for ch in sorted(glyphs, key=ord):
        o = ord(ch)
        if 32 <= o < 127 or 160 <= o < 256:
            slots[o] = ch
            seen.setdefault(bits(glyphs[ch]), ch)
    free = [i for i in list(range(1, 32)) + list(range(127, 160)) + list(range(256, 512))]
    shared = {}
    for ch in sorted(glyphs, key=ord):
        o = ord(ch)
        if 32 <= o < 127 or 160 <= o < 256:
            continue
        key = bits(glyphs[ch])
        if key in seen:
            shared[ch] = seen[key]
            continue
        if not free:
            raise ValueError('more than 512 glyphs')
        slots[free.pop(0)] = ch
        seen[key] = ch
    return slots, shared


def bits(g):
    return tuple(tuple(r) for r in g)


def unicode_table(slots, shared):
    """[code points] per slot: the glyph, its look-alikes, the aliases, heavy and
    mixed box lines (onto the light shape) and accented Latin letters (onto the
    base letter)."""
    table = [[] for _ in slots]
    where = {ch: i for i, ch in enumerate(slots) if ch is not None}
    taken = set(where)

    def add(ch, target):
        target = shared.get(target, target)
        if ch in taken or target not in where:
            return
        table[where[target]].append(ord(ch))
        taken.add(ch)

    for i, ch in enumerate(slots):
        if ch is not None:
            table[i].append(ord(ch))
    for ch, target in sorted(shared.items()):
        add(ch, target)
    for ch, target in ALIASES.items():
        add(ch, target)
    light = {}
    for ch, arms in box_set().items():
        if ch in where:
            light.setdefault(tuple(min(a, 1) for a in arms), ch)
    for ch, arms in box_set().items():
        add(ch, light.get(tuple(min(a, 1) for a in arms)))
    for ch in '┄┅┈┉╌╍':
        add(ch, '─')
    for ch in '┆┇┊┋╎╏':
        add(ch, '│')
    for cp in list(range(0x100, 0x250)) + list(range(0x1e00, 0x1f00)):
        ch = chr(cp)
        base = ''.join(c for c in unicodedata.normalize('NFKD', ch)
                       if not unicodedata.combining(c))
        if len(base) == 1:
            add(ch, base)
    return table


def build(h):
    """(psf2 bytes, {char: rows}) for cell height h."""
    glyphs = design(h)
    slots, shared = order(glyphs)
    table = unicode_table(slots, shared)
    bitmap = bytearray()
    for ch in slots:
        for row in (glyphs[ch] if ch is not None else blank(h)):
            bitmap.append(sum(0x80 >> c for c, p in enumerate(row) if p))
    uni = bytearray()
    for cps in table:
        for cp in cps:
            uni += chr(cp).encode('utf-8')
        uni += b'\xff'
    header = struct.pack('<IIIIIIII', 0x864AB572, 0, 32, 1, len(slots), h, h, W)
    return header + bytes(bitmap) + bytes(uni), glyphs


def write(h, out_dir):
    data, glyphs = build(h)
    path = os.path.join(out_dir, 'MinimalCell-%dx%d.psf.gz' % (W, h))
    with open(path, 'wb') as f:          # mtime=0: same design, same bytes
        f.write(gzip.compress(data, mtime=0))
    return path, glyphs


# ---------------------------------------------------------------- previews
SAMPLE = [
    'minimal-cell 5x7 - Monocell console font',
    'The quick brown fox jumps over the lazy dog.',
    'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG!',
    'Falsches Üben von Xylophonmusik quält jeden größeren Zwerg.',
    '0123456789  Il1| O0Q  rn m  vw  hn bd pq  g9  5S 2Z 8B',
    '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~  ÄÖÜäöüß€§°',
    'def render(img, w=80):  # "{}" [x] <a href=x>  a->b  50%',
    'ls -la /home/ariedel/.config/monocell && echo $HOME',
    '┌──┬──┐ ╔══╦══╗ ░░▒▒▓▓██ ▀▄▌▐ ▖▗▘▝ ←→↑↓ ▲▼◀▶ ■●○•…',
    '├──┼──┤ ╠══╬══╣ ░░▒▒▓▓██ ▀▀▄▄ ▙▛▜▟ ✓✗ ±×÷ ≤≥≈≠ ♥♪©®',
    '└──┴──┘ ╚══╩══╝ ░░▒▒▓▓██ ▄▄▀▀ ▚▞▚▞ ½¼¾ ²³¹ «»‹› “”„',
]


def charmap(h):
    """{char: rows} for everything the unicode table maps - what the console shows."""
    glyphs = design(h)
    slots, shared = order(glyphs)
    out = {}
    for ch, cps in zip(slots, unicode_table(slots, shared)):
        for cp in cps:
            out[chr(cp)] = glyphs[ch]
    return out


def lookup(cmap, ch):
    return cmap.get(ch) or cmap['\ufffd']


def preview(glyphs, h, path, scale=4, lines=SAMPLE, fg=(235, 225, 200), bg=(28, 28, 30)):
    from PIL import Image
    cols = max(len(s) for s in lines)
    img = Image.new('RGB', (cols * W, len(lines) * h), bg)
    px = img.load()
    for y, s in enumerate(lines):
        for x, ch in enumerate(s):
            g = lookup(glyphs, ch)
            for r in range(h):
                for c in range(W):
                    if g[r][c]:
                        px[x * W + c, y * h + r] = fg
    img = img.resize((img.size[0] * scale, img.size[1] * scale), Image.NEAREST)
    img.save(path)


def chart(glyphs, h, path, scale=6):
    """All glyphs on a grid, with a dim cell background to show the metrics."""
    from PIL import Image
    chars = sorted(glyphs, key=ord)
    per = 32
    rows = (len(chars) + per - 1) // per
    pad = 1
    cw, chh = W + pad, h + pad
    img = Image.new('RGB', (per * cw + pad, rows * chh + pad), (10, 10, 10))
    px = img.load()
    for i, ch in enumerate(chars):
        x0, y0 = pad + (i % per) * cw, pad + (i // per) * chh
        g = glyphs[ch]
        for r in range(h):
            for c in range(W):
                if g[r][c]:
                    col = (240, 235, 220)
                elif r in (1, 5) or r == 6:
                    col = (40, 40, 52)
                else:
                    col = (30, 30, 34)
                px[x0 + c, y0 + r] = col
    img = img.resize((img.size[0] * scale, img.size[1] * scale), Image.NEAREST)
    img.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=OUT_DIR)
    ap.add_argument('--preview', metavar='DIR', help='also write PNG previews to DIR')
    a = ap.parse_args(argv)
    for h in (7, 8):
        path, glyphs = write(h, a.out)
        print('%s: %d glyphs' % (os.path.relpath(path), len(glyphs)))
        if a.preview:
            os.makedirs(a.preview, exist_ok=True)
            cmap = charmap(h)
            preview(cmap, h, os.path.join(a.preview, 'sample-5x%d.png' % h))
            preview(cmap, h, os.path.join(a.preview, 'sample-5x%d-1x.png' % h), scale=2)
            chart(glyphs, h, os.path.join(a.preview, 'chart-5x%d.png' % h))
    return 0


if __name__ == '__main__':
    sys.exit(main())
