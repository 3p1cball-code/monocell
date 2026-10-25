"""Clock faces for the taskbar clock popup: ASCII analog face and big digital digits."""
import math

DIGITS = {
    '0': (' ### ', '#   #', '#  ##', '# # #', '##  #', '#   #', ' ### '),
    '1': ('  #  ', ' ##  ', '  #  ', '  #  ', '  #  ', '  #  ', ' ### '),
    '2': (' ### ', '#   #', '    #', '   # ', '  #  ', ' #   ', '#####'),
    '3': ('#### ', '    #', '    #', ' ### ', '    #', '    #', '#### '),
    '4': ('   # ', '  ## ', ' # # ', '#  # ', '#####', '   # ', '   # '),
    '5': ('#####', '#    ', '#### ', '    #', '    #', '#   #', ' ### '),
    '6': (' ### ', '#    ', '#    ', '#### ', '#   #', '#   #', ' ### '),
    '7': ('#####', '    #', '   # ', '  #  ', ' #   ', ' #   ', ' #   '),
    '8': (' ### ', '#   #', '#   #', ' ### ', '#   #', '#   #', ' ### '),
    '9': (' ### ', '#   #', '#   #', ' ####', '    #', '    #', ' ### '),
    ':': ('     ', '  #  ', '  #  ', '     ', '  #  ', '  #  ', '     '),
}
GW, GH, GAP = 5, 7, 1
FACES = ((51, 25), (41, 21), (31, 15), (27, 13), (23, 11), (17, 9))
FACE_W, FACE_H = FACES[-1]


def face_size(max_w=51, max_h=25):
    for w, h in FACES:
        if w <= max_w and h <= max_h:
            return w, h
    return FACES[-1]


def big_width(text):
    return len(text) * GW + max(0, len(text) - 1) * GAP


def draw_digital(cv, x, y, th, text):
    face = th.c('accent', 'accent')
    top = th.c('accent2', 'accent2')
    for i, ch in enumerate(text):
        glyph = DIGITS.get(ch)
        if not glyph:
            continue
        ox = x + i * (GW + GAP)
        for row, line in enumerate(glyph):
            a = top if row == 0 else face
            for col, c in enumerate(line):
                if c == '#':
                    cv.fill(ox + col, y + row, 1, 1, a)
    return big_width(text)


def draw_analog(cv, x, y, th, now, fw=FACE_W, fh=FACE_H):
    cx, cy = fw // 2, fh // 2
    rx = max(3.0, (fw - 1) / 2.0 - 2.0)
    ry = max(2.0, (fh - 1) / 2.0 - 2.0)
    rim = th.c('dim', 'menu')

    def rim_char(dx, dy):
        if abs(dx) > abs(dy) * 1.9:
            return '|'
        if abs(dy) > abs(dx) * 0.55:
            return '-'
        return '+'

    for yy in range(fh):
        dy = (yy - cy) / ry
        if abs(dy) > 1.0:
            continue
        dx = math.sqrt(max(0.0, 1.0 - dy * dy))
        for sx in (-1, 1):
            xx = int(round(cx + sx * rx * dx))
            if 0 <= xx < fw:
                cv.putc(x + xx, y + yy, rim_char(sx * dx, dy), rim)
    for xx in range(fw):
        dx = (xx - cx) / rx
        if abs(dx) > 1.0:
            continue
        dy = math.sqrt(max(0.0, 1.0 - dx * dx))
        for sy in (-1, 1):
            yy = int(round(cy + sy * ry * dy))
            if 0 <= yy < fh:
                cv.putc(x + xx, y + yy, rim_char(dx, sy * dy), rim)

    for m in range(60):
        a = m * math.pi / 30.0
        if m % 5 == 0:
            rr = 0.86
            ch = '+' if m % 15 == 0 else 'o'
            attr = th.c('accent', 'menu')
        else:
            rr = 0.92
            ch = '.'
            attr = th.c('dim', 'menu')
        tx = int(round(cx + math.sin(a) * rx * rr))
        ty = int(round(cy - math.cos(a) * ry * rr))
        if 0 <= tx < fw and 0 <= ty < fh:
            cv.putc(x + tx, y + ty, ch, attr)

    numeral = th.c('text', 'menu')
    cv.put(x + cx - 1, y + cy - int(ry * 0.62), '12', numeral)
    cv.put(x + cx + int(rx * 0.62) + 1, y + cy, '3', numeral)
    cv.put(x + cx, y + cy + int(ry * 0.62), '6', numeral)
    cv.put(x + cx - int(rx * 0.62) - 2, y + cy, '9', numeral)

    def hand(angle, length, ch, slot):
        attr = th.c(slot, 'menu')
        steps = max(6, int(length * max(rx, ry) * 3))
        for k in range(steps + 1):
            t = k / float(steps) * length
            hx = int(round(cx + math.sin(angle) * rx * t))
            hy = int(round(cy - math.cos(angle) * ry * t))
            if 0 <= hx < fw and 0 <= hy < fh:
                cv.putc(x + hx, y + hy, ch, attr)

    sec = now.tm_sec
    minute = now.tm_min + sec / 60.0
    hour = now.tm_hour % 12 + minute / 60.0
    hand(hour * math.pi / 6.0, 0.55, '#', 'bright')
    hand(minute * math.pi / 30.0, 0.80, 'o', 'accent')
    hand(sec * math.pi / 30.0, 0.93, '.', 'red')
    cv.putc(x + cx, y + cy, '+', th.c('bright', 'menu'))
