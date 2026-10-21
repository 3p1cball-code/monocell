"""The Monocell desktop mark: an extruded pixel wordmark drawn with palette roles.

Glyphs are solid colour cells (background fill, not a font glyph), so the mark
looks identical in every terminal font and on the Linux console. Cells are about
twice as tall as they are wide, so each glyph pixel is two cells wide and one
cell tall to keep the letters square.
"""
from . import __version__

# 5x7 pixel letters (only the glyphs needed for the name)
GLYPHS = {
    'M': ['#   #', '## ##', '# # #', '# # #', '#   #', '#   #', '#   #'],
    'O': [' ### ', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '],
    'N': ['#   #', '##  #', '# # #', '# # #', '#  ##', '#   #', '#   #'],
    'C': [' ### ', '#   #', '#    ', '#    ', '#    ', '#   #', ' ### '],
    'E': ['#####', '#    ', '#### ', '#    ', '#    ', '#    ', '#####'],
    'L': ['#    ', '#    ', '#    ', '#    ', '#    ', '#    ', '#####'],
}
WORD = 'MONOCELL'
GAP = 2                          # gap between letters, in cells
GW, GH = 5, 7                    # glyph box in glyph pixels
SX, SY = 2, 1                    # cells per glyph pixel (a cell is ~1:2 tall)
SHIFT_X, SHIFT_Y = 2, 1          # extrusion offset in cells
SUB = 'a text desktop for Linux'


def _size(sx=None):
    sx = SX if sx is None else sx
    return len(WORD) * (GW * sx + GAP) - GAP, GH * SY


def _luma(th, slot):
    r, g, b = th.rgb[slot]
    return r * 299 + g * 587 + b * 114


def _slots(th):
    """Face, lit top edge and extrusion slots derived from the accent colour."""
    face = th.idx('accent')
    raised = th.idx('accent2')
    top = raised if _luma(th, raised) > _luma(th, face) else face
    r, g, b = th.rgb[face]
    deep = th.nearest((int(r * 0.35), int(g * 0.35), int(b * 0.35)))
    return face, top, deep


def _draw_word(cv, x, y, th, dx, dy, slots, sx=None):
    sx = SX if sx is None else sx
    step = GW * sx + GAP
    face, top, deep = slots
    for i, letter in enumerate(WORD):
        g = GLYPHS.get(letter)
        if not g:
            continue
        ox = x + i * step + dx
        for row, line in enumerate(g):
            slot = top if row == 0 else face
            a = th.c(slot, slot)
            for col, c in enumerate(line):
                if c == '#':
                    cv.fill(ox + col * sx, y + row * SY + dy, sx, SY, a)


def measure(sx=None):
    w, h = _size(sx)
    return w + SHIFT_X, h + SHIFT_Y


def block_height(hint=True, version=False, sx=None):
    return _size(sx)[1] + SHIFT_Y + (3 if (hint or version) else 1)


def measure_for(cv_w, hint=True):
    """Pick the widest wordmark that fits: (width, height, glyph scale)."""
    for sx in (SX, 1):
        bw = _size(sx)[0] + SHIFT_X
        if cv_w >= bw + 6:
            return bw, block_height(hint, sx=sx), sx
    return len('monocell') + 1, 2, 0


def draw(cv, x, y, th, compact=False, hint=True, version=False):
    """Draw the wordmark; x/y is the top-left of the face layer."""
    bw, _, sx = measure_for(cv.w)
    if compact or sx == 0:
        label = 'monocell'
        cv.put(x + 1, y + 1, label, th.c('sel', 'desk'))
        cv.put(x, y, label, th.c('accent', 'desk', 1))
        return len(label) + 1, 2
    slots = _slots(th)
    _draw_word(cv, x, y, th, SHIFT_X, SHIFT_Y, (slots[2],) * 3, sx)
    _draw_word(cv, x, y, th, 0, 0, slots, sx)
    bottom = y + _size()[1] + SHIFT_Y
    sx = x + (bw - len(SUB)) // 2
    cv.put(sx, bottom + 1, SUB, th.c('text', 'desk'))
    if version:
        ver = 'v' + __version__
        cv.put(x + bw - len(ver), bottom + 2, ver, th.c('dim', 'desk'))
    if hint:
        label = 'F12  start menu'
        hx = x + (bw - len(label)) // 2
        cv.put(hx, bottom + 2, label, th.c('dim', 'desk'))
    return bw, block_height(hint, version)
