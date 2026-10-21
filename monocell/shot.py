"""Text screenshots: the whole screen as a Markdown file with ANSI colours.

The file opens in any text editor (line breaks and characters are plain text). A plain
copy is visible everywhere; the collapsed block keeps exact 24-bit ANSI colours for
terminals such as cat, bat or less -R.
"""
import datetime
import os

FENCE_MIN = 3


def _rgb(hex_colour):
    h = hex_colour.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _sgr(attr, palette):
    """24-bit SGR with the exact palette colours (widely supported by terminals
    and by GitHub's `ansi` code blocks)."""
    fg = _rgb(palette[attr & 15])
    bg = _rgb(palette[(attr >> 4) & 15])
    return ('\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm' % (fg + bg))


def _fence(rows):
    longest = 0
    for line in rows:
        run = 0
        for ch in line:
            run = run + 1 if ch == '`' else 0
            longest = max(longest, run)
    return '`' * max(FENCE_MIN, longest + 1)


def colour_block(rows, attrs, palette):
    """ANSI version: one SGR run per colour change, reset at every line end."""
    out = []
    for line, at in zip(rows, attrs):
        buf = []
        cur = None
        for ch, a in zip(line, at):
            a &= 0xff
            if a != cur:
                buf.append(_sgr(a, palette))
                cur = a
            buf.append(ch if ch >= ' ' and ch != '\x7f' else ' ')
        buf.append('\x1b[0m')
        out.append(''.join(buf))
    return out


def markdown(buf, palette, palette_name, host, version, when=None, title='Monocell'):
    """Build the Markdown text for a screen buffer (Buffer.ch / Buffer.at)."""
    when = when or datetime.datetime.now()
    rows = [''.join(r) for r in buf.ch]
    attrs = [list(a) for a in buf.at]
    fence = _fence(rows)
    ansi = colour_block(rows, attrs, palette)
    # plain fallback: colour blocks (e.g. the logo) are drawn as # so the shape survives
    plain = []
    for line, at in zip(rows, attrs):
        plain.append(''.join('#' if ch == ' ' and (a & 15) == ((a >> 4) & 15) else ch
                             for ch, a in zip(line, at)).rstrip())
    stamp = when.strftime('%Y-%m-%d %H:%M:%S')
    cols, lines = buf.w, buf.h
    out = ['# %s screenshot — %s' % (title, stamp), '',
           '%d×%d · host `%s` · palette `%s` · v%s' % (cols, lines, host, palette_name, version),
           '', fence + 'text']
    out += plain
    out += [fence, '',
            '> GitHub shows this plain copy. The colour version below stores the exact 24-bit',
            '> palette; `cat`, `bat`, `less -R` or an ANSI-aware viewer render it.',
            '', '<details><summary>Colour version (ANSI, exact palette)</summary>', '',
            fence + 'ansi']
    out += ansi
    out += [fence, '', '</details>', '', '## Palette', '']
    pairs = ['`%d %s`' % (i, '#' + h) for i, h in enumerate(palette)]
    out += [' '.join(pairs[:8]), '', ' '.join(pairs[8:]), '']
    return '\n'.join(out)


def save(buf, palette, palette_name, host, version, directory, when=None, title='Monocell'):
    when = when or datetime.datetime.now()
    directory = os.path.expanduser(directory or '~/monocell-shots')
    if not os.path.isabs(directory):
        directory = os.path.join(os.path.expanduser('~'), directory)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    base = 'monocell-%s' % when.strftime('%Y%m%d-%H%M%S')
    path = os.path.join(directory, base + '.md')
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, '%s-%d.md' % (base, n))
        n += 1
    text = markdown(buf, palette, palette_name, host, version, when, title)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)
    return path, len(text.encode('utf-8'))
