"""Text screenshots: the whole screen as a Markdown file with its colours.

Colours are inline HTML inside the Markdown: every preview that allows HTML (Kate's
Document Preview / Okular, browsers, VS Code, Obsidian) shows them; GitHub strips the
styles and shows the text.
"""
import datetime
import os
import re
import sys


def _esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def html_block(rows, attrs, palette):
    """<pre> with one coloured <span> per run. Markdown previews that allow inline HTML
    (Kate/Okular, browsers, VS Code, Obsidian) show the colours; GitHub strips the styles
    and still shows the text."""
    counts = {}
    for at in attrs:
        for a in at:
            b = (a >> 4) & 15
            counts[b] = counts.get(b, 0) + 1
    base = max(counts, key=counts.get) if counts else 0
    out = []
    for line, at in zip(rows, attrs):
        buf = []
        cur = None
        run = []

        def flush():
            if run:
                fg, bg = palette[cur & 15], palette[(cur >> 4) & 15]
                buf.append('<span style="color:#%s;background-color:#%s">%s</span>'
                           % (fg, bg, _esc(''.join(run))))
                run.clear()
        for ch, a in zip(line, at):
            a &= 0xff
            if a != cur:
                flush()
                cur = a
            run.append(ch if ch >= ' ' and ch != '\x7f' else ' ')
        flush()
        out.append(''.join(buf))
    return ('<pre style="display:inline-block;background-color:#%s;line-height:1.0;'
            'font-family:monospace">'
            % palette[base] + '\n'.join(out) + '</pre>')


def markdown(buf, palette, palette_name, host, version, when=None, title='Monocell'):
    """Build the Markdown text for a screen buffer (Buffer.ch / Buffer.at)."""
    when = when or datetime.datetime.now()
    rows = [''.join(r) for r in buf.ch]
    attrs = [list(a) for a in buf.at]
    stamp = when.strftime('%Y-%m-%d %H:%M:%S')
    out = ['# %s screenshot — %s' % (title, stamp), '',
           '%d×%d · host `%s` · palette `%s` · v%s' % (buf.w, buf.h, host, palette_name, version),
           '',
           html_block(rows, attrs, palette), '',
           'Colours: open the Markdown preview (Kate: Document Preview, Okular, a browser,',
           'VS Code). GitHub removes inline colours and shows the text only.', '',
           '## Palette', '']
    pairs = ['`%d #%s`' % (i, h) for i, h in enumerate(palette)]
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


SGR = re.compile(r'\x1b\[([0-9;]*)m')


def convert_legacy(text):
    """Turn a screenshot from the first (ANSI) format into the HTML format."""
    m = re.search(r'^(`{3,})ansi\n(.*?)^\1$', text, re.S | re.M)
    if not m:
        return None
    lines = []
    for raw in m.group(2).rstrip('\n').split('\n'):
        fg, bg = 'c4c4c4', '000000'
        out, pos = [], 0
        for sm in list(SGR.finditer(raw)) + [None]:
            chunk = raw[pos:sm.start() if sm else len(raw)]
            if chunk:
                out.append('<span style="color:#%s;background-color:#%s">%s</span>'
                           % (fg, bg, _esc(chunk)))
            if sm is None:
                break
            pos = sm.end()
            ps = [int(x) for x in sm.group(1).split(';') if x]
            if ps[:2] == [38, 2] and len(ps) >= 5:
                fg = '%02x%02x%02x' % tuple(ps[2:5])
            elif ps[:2] == [48, 2] and len(ps) >= 5:
                bg = '%02x%02x%02x' % tuple(ps[2:5])
        lines.append(''.join(out))
    pre = ('<pre style="display:inline-block;background-color:#0c0c0c;line-height:1.0;'
           'font-family:monospace">' + '\n'.join(lines) + '</pre>')
    title = text.split('\n', 1)[0]
    meta = re.search(r'^\d+×\d+ ·.*$', text, re.M)
    pal = text[text.index('## Palette'):].rstrip('\n') if '## Palette' in text else ''
    out = [title, '']
    if meta:
        out += [meta.group(0), '']
    out += [pre, '',
            'Colours: open the Markdown preview (Kate: Document Preview, Okular, a browser,',
            'VS Code). GitHub removes inline colours and shows the text only.', '']
    if pal:
        out += [pal, '']
    return '\n'.join(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != 'convert':
        print('usage: python3 -m monocell.shot convert FILE.md [...]   (old ANSI shots, in place)')
        return 2
    rc = 0
    for path in argv[1:]:
        text = open(path, encoding='utf-8').read()
        new = convert_legacy(text)
        if new is None:
            print('%s: no ANSI block, left as is' % path)
            continue
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new)
        print('%s: converted' % path)
    return rc


if __name__ == '__main__':
    sys.exit(main())
