"""Text screenshots: a readable Markdown file plus a coloured HTML companion.

The Markdown is plain text (line breaks and characters) and opens in any editor. The
HTML file keeps the exact palette in inline styles and opens in any browser (and in
Kate's/Okular's preview); GitHub shows the HTML only as source.
"""
import datetime
import os


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


def plain_rows(rows):
    return [line.rstrip() for line in rows]


def markdown_plain(rows, palette_name, host, version, when, colour_name, title='Monocell'):
    stamp = when.strftime('%Y-%m-%d %H:%M:%S')
    out = ['# %s screenshot — %s' % (title, stamp), '',
           '%d×%d · host `%s` · palette `%s` · v%s' % (len(rows[0]) if rows else 0, len(rows),
                                                       host, palette_name, version), '',
           '```text']
    out += plain_rows(rows)
    out += ['```', '',
            'Farbfassung: [%s](%s) – im Browser öffnen (exakte Farben, Text bleibt markierbar).'
            % (colour_name, colour_name), '']
    return '\n'.join(out)


def html_document(rows, attrs, palette, palette_name, host, version, when, title='Monocell'):
    stamp = when.strftime('%Y-%m-%d %H:%M:%S')
    pre = html_block(rows, attrs, palette)
    pairs = ' · '.join('%d #%s' % (i, h) for i, h in enumerate(palette))
    return ('<!doctype html>\n<html><head><meta charset="utf-8">\n'
            '<title>%s screenshot %s</title></head>\n'
            '<body style="margin:0;padding:16px;background:#111;color:#c4c4c4;'
            'font:12px/1.35 monospace">\n'
            '<h1 style="font-size:16px;color:#ff8a1c">%s screenshot — %s</h1>\n'
            '<p>%d×%d · host %s · palette %s · v%s</p>\n%s\n'
            '<p style="color:#7c7c7c">%s</p>\n</body></html>\n'
            % (_esc(title), stamp, _esc(title), stamp, len(rows[0]) if rows else 0, len(rows),
               _esc(host), _esc(palette_name), version, pre, pairs))


def save(buf, palette, palette_name, host, version, directory, when=None, title='Monocell'):
    when = when or datetime.datetime.now()
    directory = os.path.expanduser(directory or '~/monocell-shots')
    if not os.path.isabs(directory):
        directory = os.path.join(os.path.expanduser('~'), directory)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    rows = [''.join(r) for r in buf.ch]
    attrs = [list(a) for a in buf.at]
    base = 'monocell-%s' % when.strftime('%Y%m%d-%H%M%S')
    path = os.path.join(directory, base + '.md')
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, '%s-%d.md' % (base, n))
        n += 1
    stem = path[:-3]
    colour = stem + '-colour.html'
    md = markdown_plain(rows, palette_name, host, version, when, os.path.basename(colour), title)
    html = html_document(rows, attrs, palette, palette_name, host, version, when, title)
    for target, text in ((path, md), (colour, html)):
        tmp = target + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, target)
    return path, colour, len(md.encode('utf-8')), len(html.encode('utf-8'))

