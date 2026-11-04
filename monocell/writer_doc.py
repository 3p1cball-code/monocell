"""Small Markdown-ish document model for the Writer.

The source stays plain text, so it can be edited in the Writer's text area and
diffed in git. This module parses the blocks, wraps styled text, turns headings
and <sizeN> runs into block letters and embeds images as text-cell pictures in
the selected image mode.

Extra markup on top of Markdown:

    <u>text</u>          single underline
    <u2>text</u2>        double underline
    <u3>text</u3>        triple underline
    <size2>text</size2>  large block letters
    <size3>text</size3>  huge block letters

An image is a line of its own:  ![caption](path)
"""
import re
from collections import namedtuple

from . import imaging

Span = namedtuple('Span', 'text bold italic code ul size link url', defaults=('',))

HEAD_RE = re.compile(r'^(#{1,6})\s+(.*)$')
IMAGE_RE = re.compile(r'^\s*!\[([^\]]*)\]\((.+?)\)(?:\{([^}]*)\})?\s*$')
ESCAPABLE = '\\`*_[]<>#+-!.)={}|'
RULE_RE = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')
LIST_RE = re.compile(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$')
QUOTE_RE = re.compile(r'^\s*>\s?(.*)$')
TAG_RE = re.compile(r'<(/?)(u|u2|u3|size2|size3|b|i)>', re.I)
LINK_RE = re.compile(r'\[([^\]\n]+)\]\(([^)\n]+)\)')
CODE_RE = re.compile(r'`([^`\n]+)`')
BOLD_RE = re.compile(r'\*\*([^*\n]+)\*\*')
ITAL_RE = re.compile(r'(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!\w)_([^_\n]+)_(?!\w)')
IMAGE_SUFFIX = tuple(imaging.IMAGE_EXT)


def _same_style(a, b):
    return a[1:] == b[1:]


def parse_inline(text, italic=True):
    """italic=False: the Writer has no italic (the printer cannot) - *x* stays text."""
    spans = []
    state = {'bold': False, 'italic': False, 'code': False, 'ul': 0, 'size': 1}
    buf = []

    def flush():
        if buf:
            spans.append(Span(''.join(buf), state['bold'], state['italic'], state['code'],
                              state['ul'], state['size'], False))
            del buf[:]

    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i + 1] in ESCAPABLE:
            buf.append(text[i + 1])
            i += 2
            continue
        m = TAG_RE.match(text, i)
        if m:
            flush()
            closing = bool(m.group(1))
            name = m.group(2).lower()
            if name == 'b':
                state['bold'] = not closing
            elif name == 'i':
                state['italic'] = italic and not closing
            elif name.startswith('u'):
                state['ul'] = 0 if closing else int(name[1:] or 1)
            elif name.startswith('size'):
                state['size'] = 1 if closing else int(name[4:])
            i = m.end()
            continue
        m = CODE_RE.match(text, i)
        if m:
            flush()
            spans.append(Span(m.group(1), state['bold'], state['italic'], True,
                              state['ul'], state['size'], False))
            i = m.end()
            continue
        m = LINK_RE.match(text, i)
        if m:
            flush()
            spans.append(Span(m.group(1), state['bold'], state['italic'], state['code'],
                              state['ul'], state['size'], True, m.group(2)))
            i = m.end()
            continue
        m = BOLD_RE.match(text, i)
        if m:
            flush()
            spans.append(Span(m.group(1), True, state['italic'], state['code'],
                              state['ul'], state['size'], False))
            i = m.end()
            continue
        m = ITAL_RE.match(text, i) if italic else None
        if m:
            flush()
            content = m.group(1) if m.group(1) is not None else m.group(2)
            spans.append(Span(content, state['bold'], True, state['code'],
                              state['ul'], state['size'], False))
            i = m.end()
            continue
        buf.append(text[i])
        i += 1
    flush()
    out = []
    for s in spans:
        if s.text and out and _same_style(out[-1], s):
            out[-1] = out[-1]._replace(text=out[-1].text + s.text)
        elif s.text:
            out.append(s)
    return out


def plain_text(spans):
    return ''.join(s.text for s in spans)


def parse_blocks(text):
    lines = text.split('\n')
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            blocks.append({'kind': 'code', 'lines': code})
            continue
        m = HEAD_RE.match(line)
        if m:
            blocks.append({'kind': 'heading', 'level': len(m.group(1)),
                           'spans': parse_inline(m.group(2))})
            i += 1
            continue
        m = IMAGE_RE.match(line)
        if m:
            blocks.append({'kind': 'image', 'alt': m.group(1), 'path': m.group(2)})
            i += 1
            continue
        if RULE_RE.match(line):
            blocks.append({'kind': 'rule'})
            i += 1
            continue
        m = LIST_RE.match(line)
        if m:
            blocks.append({'kind': 'list', 'indent': len(m.group(1)),
                           'marker': m.group(2), 'spans': parse_inline(m.group(3))})
            i += 1
            continue
        m = QUOTE_RE.match(line)
        if m:
            blocks.append({'kind': 'quote', 'spans': parse_inline(m.group(1))})
            i += 1
            continue
        if not line.strip():
            blocks.append({'kind': 'blank'})
        else:
            blocks.append({'kind': 'para', 'spans': parse_inline(line)})
        i += 1
    return blocks


class Renderer:
    """Markdown source -> rendered cell rows (the Writer's page, see richdoc).

    Kept for the printer and other callers; the layout itself lives in
    richdoc.Layout.
    """

    def __init__(self, wm, width, base_dir='', cache=None, image_mode=None, half=None):
        self.wm = wm
        self.width = max(8, width)
        self.base_dir = base_dir or ''
        self.image_mode = image_mode
        self.images = cache if hasattr(cache, 'get') and hasattr(cache, 'cells_for') else None

    def render(self, text):
        from . import richdoc
        return richdoc.render_rows(self.wm, text, self.width, self.base_dir,
                                   image_mode=self.image_mode, images=self.images)
