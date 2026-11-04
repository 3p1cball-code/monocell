#!/usr/bin/env python3
"""Build a Monocell login screen for agetty (/etc/issue).

agetty prints /etc/issue before the login prompt. This writes one that clears
the console, loads the Monocell theme palette into the VT (ESC ] P), draws the
extruded MONOCELL wordmark from coloured cells - the same mark as on the
desktop - and shows host, system, date/time and the fingerprint hint, centred
for the console size. Backslashes are agetty escapes (\\n host, \\d date,
\\t time, \\4 IPv4, \\S{PRETTY_NAME}).

usage: make_issue.py [--config ~/.config/monocell/config.json] [--cols N --rows N]
                     [--out /etc/issue] [--preview]
--preview prints the screen with ANSI colours instead of writing a file.
"""
import argparse
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

def _config_home_from_argv():
    """Monocell reads saved themes next to its config: point XDG there first."""
    argv = sys.argv
    if '--config' in argv[:-1]:
        cfg = os.path.abspath(os.path.expanduser(argv[argv.index('--config') + 1]))
        os.environ['XDG_CONFIG_HOME'] = os.path.dirname(os.path.dirname(cfg))

if __name__ == '__main__':
    _config_home_from_argv()

from monocell import logo, theme  # noqa: E402

ESC = '\x1b'
# VT palette slots used on the login screen (SGR 30-37 / 40-47 address 0-7)
DESK, FACE, TOP, DEEP, ACCENT, DIM, TEXT = 0, 1, 2, 3, 4, 5, 7

def console_size(font_name=None):
    """Columns/rows of the text console: framebuffer size / font cell."""
    try:
        with open('/sys/class/graphics/fb0/virtual_size') as f:
            pw, ph = (int(v) for v in f.read().strip().split(','))
    except (OSError, ValueError):
        return 80, 25
    cell = theme.console_font_cell(font_name) if font_name else None
    cw, ch = cell or (8, 16)
    return max(40, pw // cw), max(20, ph // ch)

def load_theme(config_path):
    if config_path:
        theme.Config.path = os.path.expanduser(config_path)
    cfg = theme.Config()
    return cfg, theme.Theme(cfg)

def wordmark(sx=logo.SX):
    """Cells of the extruded wordmark: dict (x, y) -> slot (FACE/TOP/DEEP)."""
    cells = {}
    step = logo.GW * sx + logo.GAP
    for layer, (dx, dy) in (('deep', (logo.SHIFT_X, logo.SHIFT_Y)), ('face', (0, 0))):
        for i, letter in enumerate(logo.WORD):
            g = logo.GLYPHS[letter]
            for row, line in enumerate(g):
                for col, c in enumerate(line):
                    if c != '#':
                        continue
                    slot = DEEP if layer == 'deep' else (TOP if row == 0 else FACE)
                    for k in range(sx):
                        cells[(i * step + col * sx + k + dx, row * logo.SY + dy)] = slot
    return cells

def palette_seq(th):
    face, top, deep = logo._slots(th)
    want = {DESK: th.idx('desk'), FACE: face, TOP: top, DEEP: deep,
            ACCENT: th.idx('accent'), DIM: th.idx('dim'), 6: th.idx('bright'), TEXT: th.idx('text')}
    out = []
    for slot in range(16):
        r, g, b = th.rgb[want[slot % 8]]
        out.append('%s]P%X%02x%02x%02x' % (ESC, slot, r, g, b))
    return ''.join(out)

def sgr(fg=TEXT, bg=DESK):
    return '%s[0;%d;%dm' % (ESC, 30 + fg, 40 + bg)

def esc_text(s):
    return s.replace('\\', '\\\\')

def build(th, cols, rows, user_hint=True):
    sx = logo.SX
    w = len(logo.WORD) * (logo.GW * sx + logo.GAP) - logo.GAP + logo.SHIFT_X
    if w + 4 > cols:
        sx = 1
        w = len(logo.WORD) * (logo.GW + logo.GAP) - logo.GAP + logo.SHIFT_X
    h = logo.GH * logo.SY + logo.SHIFT_Y
    cells = wordmark(sx)
    left = max(0, (cols - w) // 2)
    block = h + 9
    top = max(1, (rows - block) // 2 - 2)
    out = [palette_seq(th), sgr(), ESC + '[H' + ESC + '[2J']
    out.append('\n' * top)
    for y in range(h):
        line = [' ' * left]
        cur = None
        for x in range(w):
            slot = cells.get((x, y))
            if slot != cur:
                line.append(sgr(TEXT, slot) if slot is not None else sgr())
                cur = slot
            line.append(' ')
        line.append(sgr())
        out.append(''.join(line).rstrip() + sgr() + '\n')

    def centred(text, fg, width=None):
        n = width if width is not None else len(text)
        pad = max(0, (cols - n) // 2)
        return ' ' * pad + sgr(fg) + text + sgr() + '\n'

    out.append('\n')
    out.append(centred(esc_text(logo.SUB), TEXT))
    out.append('\n')
    # agetty escapes expand at run time; reserve their usual width for centring
    out.append(centred('\\n  -  \\S{PRETTY_NAME}', DIM, 36))
    out.append(centred('\\d  \\t', DIM, 28))
    out.append('\n')
    if user_hint:
        out.append(centred('Finger auf den Sensor legen oder Passwort eingeben', ACCENT))
    out.append('\n')
    # no newline at the end: agetty prints "host login:" right after, so the
    # padding centres the prompt under the mark
    out.append(sgr(TEXT, DESK) + ' ' * max(0, (cols - 24) // 2))
    return ''.join(out)

def preview(text, th):
    """Replace agetty escapes with sample values and VT palette with 24-bit colour."""
    import re
    import socket
    import time
    face, top, deep = logo._slots(th)
    rgb = {DESK: th.rgb[th.idx('desk')], FACE: th.rgb[face], TOP: th.rgb[top],
           DEEP: th.rgb[deep], ACCENT: th.rgb[th.idx('accent')], DIM: th.rgb[th.idx('dim')],
           6: th.rgb[th.idx('bright')], TEXT: th.rgb[th.idx('text')]}
    text = re.sub(r'\x1b\]P[0-9A-F][0-9a-f]{6}', '', text)

    def sub(m):
        fg, bg = int(m.group(1)) - 30, int(m.group(2)) - 40
        return '\x1b[0;38;2;%d;%d;%d;48;2;%d;%d;%dm' % (rgb[fg] + rgb[bg])
    text = re.sub(r'\x1b\[0;(\d+);(\d+)m', sub, text)
    host = os.environ.get('MONOCELL_HOSTNAME') or socket.gethostname().split('.')[0]
    text = text.replace('\\n', host)
    text = text.replace('\\S{PRETTY_NAME}', 'Debian GNU/Linux 13 (trixie)')
    text = text.replace('\\d', time.strftime('%a %b %d %Y')).replace('\\t', time.strftime('%H:%M:%S'))
    return text.replace('\\\\', '\\')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='~/.config/monocell/config.json')
    ap.add_argument('--cols', type=int)
    ap.add_argument('--rows', type=int)
    ap.add_argument('--out', default='')
    ap.add_argument('--preview', action='store_true')
    a = ap.parse_args()
    cfg, th = load_theme(a.config)
    cols, rows = console_size(cfg.get('font'))
    cols = a.cols or cols
    rows = a.rows or rows
    text = build(th, cols, rows)
    if a.preview:
        sys.stdout.write(preview(text, th) + '\x1b[0m\n')
        return 0
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('wrote %s (%dx%d console)' % (a.out, cols, rows))
    else:
        sys.stdout.write(text)
    return 0

if __name__ == '__main__':
    sys.exit(main())
