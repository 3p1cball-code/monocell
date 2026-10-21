"""Writer: distraction-free word processor (Markdown model) with .docx/.odt import and .docx export."""
import os
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..app import App
from ..textarea import TextArea
from ..util import fit, shorten_path
from ..widgets import button

W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    out = []
    for p in root.iter(W_NS + 'p'):
        style = ''
        ppr = p.find(W_NS + 'pPr')
        if ppr is not None:
            ps = ppr.find(W_NS + 'pStyle')
            if ps is not None:
                style = ps.get(W_NS + 'val', '')
            is_list = ppr.find(W_NS + 'numPr') is not None
        else:
            is_list = False
        parts = []
        for r in p.iter(W_NS + 'r'):
            rpr = r.find(W_NS + 'rPr')
            bold = rpr is not None and rpr.find(W_NS + 'b') is not None
            ital = rpr is not None and rpr.find(W_NS + 'i') is not None
            t = ''.join((x.text or '') if x.tag == W_NS + 't' else ('\t' if x.tag == W_NS + 'tab' else '')
                        for x in r)
            if not t:
                continue
            if bold and t.strip():
                t = '**%s**' % t
            elif ital and t.strip():
                t = '*%s*' % t
            parts.append(t)
        text = ''.join(parts)
        m = re.match(r'(?i)heading\s*(\d)|title', style)
        if m:
            lvl = int(m.group(1)) if m.group(1) else 1
            text = '#' * lvl + ' ' + re.sub(r'\*\*', '', text)
        elif is_list or style.lower().startswith('list'):
            text = '- ' + text
        out.append(text)
    return '\n\n'.join(t for t in out).replace('\n\n\n', '\n\n')


def read_odt(path):
    ns = {'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    out = []
    T = '{%s}' % ns['text']
    for el in root.iter():
        if el.tag == T + 'h':
            lvl = int(el.get(T + 'outline-level', '1'))
            out.append('#' * lvl + ' ' + ''.join(el.itertext()))
        elif el.tag == T + 'p':
            out.append(''.join(el.itertext()))
    return '\n\n'.join(out)


def _runs(text):
    """Markdown inline -> list of (text, bold, italic)."""
    out = []
    for tok in re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_)', text):
        if not tok:
            continue
        if tok.startswith('**') and tok.endswith('**') and len(tok) > 4:
            out.append((tok[2:-2], True, False))
        elif (tok.startswith('*') and tok.endswith('*') or tok.startswith('_') and tok.endswith('_')) and len(tok) > 2:
            out.append((tok[1:-1], False, True))
        else:
            out.append((tok, False, False))
    return out


def write_docx(path, text):
    paras = []
    for block in text.split('\n'):
        style = None
        t = block
        m = re.match(r'(#{1,6})\s+(.*)', t)
        if m:
            style = 'Heading%d' % len(m.group(1))
            t = m.group(2)
        else:
            m = re.match(r'\s*[-*+]\s+(.*)', t)
            if m:
                style = 'ListBullet'
                t = m.group(1)
        if not t.strip() and style is None:
            continue
        ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ''
        runs = ''
        for s, b, i in _runs(t):
            rpr = ('<w:rPr>%s%s</w:rPr>' % ('<w:b/>' if b else '', '<w:i/>' if i else '')) if (b or i) else ''
            runs += '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(s))
        paras.append('<w:p>%s%s</w:p>' % (ppr, runs))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1417" w:bottom="1134" '
           'w:left="1417" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>') % ''.join(paras)
    styles = ['<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:pPr><w:spacing w:after="160"/></w:pPr><w:rPr><w:sz w:val="22"/></w:rPr></w:style>']
    for lvl, size in ((1, 36), (2, 30), (3, 26), (4, 24), (5, 22), (6, 22)):
        styles.append('<w:style w:type="paragraph" w:styleId="Heading%d"><w:name w:val="heading %d"/>'
                      '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/>'
                      '<w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="%d"/></w:pPr>'
                      '<w:rPr><w:b/><w:sz w:val="%d"/></w:rPr></w:style>' % (lvl, lvl, lvl - 1, size))
    styles.append('<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
                  '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:style>')
    styles_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles '
                  'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">%s</w:styles>') % ''.join(styles)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    docrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', doc)
        z.writestr('word/styles.xml', styles_xml)
        z.writestr('word/_rels/document.xml.rels', docrels)


INLINE = re.compile(r'(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(`[^`\n]+`)|(\[[^\]\n]+\]\([^)\n]+\))')


class Writer(App):
    name = 'writer'
    title = 'Writer'
    icon = '[W]'
    size = (84, 28)
    min_size = (34, 8)
    shift_select = True

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.ta = TextArea(wrap=True)
        self.ta.scrollbar = True
        self.ta.style = self.style
        self.path = None
        self.focus_mode = False
        self.col = 72
        if path:
            self.load(path)
        else:
            self.ta.set_text('# Untitled\n\nStart writing. Use **bold**, *italic*, # headings and - lists.\n')
            self.ta.cy = len(self.ta.lines) - 1
            self.ta.modified = False
        self.update_title()

    def style(self, i, line):
        th = self.th
        if line.startswith('#'):
            m = re.match(r'(#+)\s?', line)
            return [(0, m.end(), th.c('dim', 'win')), (m.end(), len(line), th.c('accent', 'win', 1))]
        out = []
        m = re.match(r'(\s*)([-*+]|\d+[.)])\s', line)
        if m:
            out.append((m.start(2), m.end(2), th.c('accent', 'win')))
        if line.startswith('>'):
            out.append((0, len(line), th.c('dim', 'win')))
        for m in INLINE.finditer(line):
            if m.group(1):
                out += [(m.start(), m.start() + 2, th.c('sel', 'win')), (m.start() + 2, m.end() - 2, th.c('bright', 'win', 1)),
                        (m.end() - 2, m.end(), th.c('sel', 'win'))]
            elif m.group(2):
                out += [(m.start(), m.start() + 1, th.c('sel', 'win')), (m.start() + 1, m.end() - 1, th.c('text', 'win', 2)),
                        (m.end() - 1, m.end(), th.c('sel', 'win'))]
            elif m.group(3):
                out.append((m.start(), m.end(), th.c('cyan', 'win')))
            elif m.group(4):
                out.append((m.start(), m.end(), th.c('blue', 'win', 2)))
        return out

    def update_title(self):
        name = os.path.basename(self.path) if self.path else 'untitled.md'
        self.set_title(('* ' if self.ta.modified else '') + name)

    def load(self, path):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == '.docx':
                txt = read_docx(path)
            elif ext == '.odt':
                txt = read_odt(path)
            else:
                with open(path, encoding='utf-8', errors='replace') as f:
                    txt = f.read()
            self.ta.set_text(txt.replace('\r\n', '\n').replace('\t', '    '))
            self.path = path
        except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
            self.wm.toast('Cannot open: %s' % e, 'red')

    def save(self, path=None, done=None):
        path = path or self.path
        if not path or path.lower().endswith('.odt'):
            return self.save_as(done)
        try:
            if path.lower().endswith('.docx'):
                write_docx(path, self.ta.text())
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.ta.text())
            self.path = path
            self.ta.modified = False
            self.wm.toast('Saved ' + shorten_path(path, 50), 'green')
            self.update_title()
            if done:
                done()
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def save_as(self, done=None):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.md' if self.path else 'document.md'
        self.wm.pick_file(lambda p: self.save(p, done), 'save', start=os.path.dirname(self.path) if self.path else None,
                          name=name, parent=self.win)

    def export_docx(self):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.docx' if self.path else 'document.docx'

        def ok(p):
            try:
                write_docx(p, self.ta.text())
                self.wm.toast('Exported ' + os.path.basename(p), 'green')
            except OSError as e:
                self.wm.toast(str(e), 'red')
        self.wm.pick_file(ok, 'save', name=name, parent=self.win)

    def on_close(self):
        if not self.ta.modified:
            return True

        def res(r, v):
            if r == 'Save':
                self.save(done=lambda: self.wm.close(self.win, force=True))
            elif r == 'Discard':
                self.wm.close(self.win, force=True)
        self.wm.dialog('Unsaved changes', 'Save changes to this document?', ('Save', 'Discard', 'Cancel'), res,
                       parent=self.win)
        return False

    def outline(self):
        return [(i, l) for i, l in enumerate(self.ta.lines) if l.startswith('#')]

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        top = 0
        if not self.focus_mode:
            x = 0
            for label, key in (('New', 'new'), ('Open', 'open'), ('Save', 'save'), ('Export .docx', 'docx'),
                               ('B', 'bold'), ('I', 'ital'), ('H', 'head'), ('List', 'list'), ('Focus', 'focus')):
                if x + len(label) + 3 > w:
                    break
                x += button(self, cv, x, 0, label, key) + 1
            top = 2
        side = 24 if w >= 110 and not self.focus_mode else 0
        if side:
            cv.put(0, top, 'OUTLINE', th.c('dim', 'win'))
            for j, (i, l) in enumerate(self.outline()[:h - top - 3]):
                lvl = len(l) - len(l.lstrip('#'))
                a = th.c('accent', 'raised') if i == self.ta.cy else (th.c('bright', 'sel') if self.hover == ('ol', i) else th.c('text', 'win'))
                cv.put(0, top + 1 + j, fit('  ' * (lvl - 1) + l.lstrip('#').strip(), side - 2), a)
                self.zone(0, top + 1 + j, side - 2, 1, ('ol', i))
        avail = w - side
        colw = min(self.col, avail - 2)
        x0 = side + (avail - colw) // 2
        self.text_x = x0
        self.text_y = top
        self.ta.draw(cv, x0, top, colw, h - top - 1, th, focused)
        self.zone(side, top, avail, h - top - 1, 'text')
        words = len(re.findall(r'\w+', self.ta.text()))
        st = '%d words  %d chars  ~%d min read' % (words, len(self.ta.text()), max(1, words // 200))
        cv.put(0, h - 1, fit(shorten_path(self.path, 40) if self.path else 'not saved', w - len(st) - 2), th.c('dim', 'win'))
        cv.put(w - len(st), h - 1, st, th.c('dim', 'win'))

    def wrap_sel(self, mark):
        t = self.ta.selected_text()
        if t:
            self.ta.snapshot('other')
            self.ta.delete_selection()
            self.ta.insert(mark + t + mark, 'other')
        else:
            self.ta.insert(mark + mark, 'other')
            self.ta.cx -= len(mark)

    def line_prefix(self, prefix):
        ln = self.ta.lines[self.ta.cy]
        self.ta.snapshot('other')
        stripped = re.sub(r'^(#+\s|[-*+]\s)', '', ln)
        if ln.startswith(prefix):
            self.ta.lines[self.ta.cy] = stripped
            self.ta.cx = max(0, self.ta.cx - len(prefix))
        else:
            self.ta.lines[self.ta.cy] = prefix + stripped
            self.ta.cx = min(len(self.ta.lines[self.ta.cy]), self.ta.cx + len(prefix) - (len(ln) - len(stripped)))
        self.ta._vrows = None

    def on_click(self, key, ev):
        acts = {'new': lambda: self.wm.open('writer'), 'save': self.save, 'docx': self.export_docx,
                'bold': lambda: self.wrap_sel('**'), 'ital': lambda: self.wrap_sel('*'),
                'head': lambda: self.line_prefix('## '), 'list': lambda: self.line_prefix('- '),
                'focus': self.toggle_focus}
        if key in acts:
            acts[key]()
        elif key == 'open':
            def ok(p):
                if self.ta.modified or self.path:
                    self.wm.open('writer', path=p)
                else:
                    self.load(p)
                self.update_title()
            self.wm.pick_file(ok, 'open', parent=self.win)
        elif key[0] == 'ol':
            self.ta.cy, self.ta.cx = key[1], 0
            self.ta.anchor = None
            self.ta.ensure_visible()
        self.update_title()

    def toggle_focus(self):
        self.focus_mode = not self.focus_mode
        if self.focus_mode and not self.win.snap:
            self.wm.toggle_max(self.win)

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.ta.sb_grab is not None:
            self.ta.mouse(ev, ev.x - getattr(self, 'text_x', 0), ev.y - getattr(self, 'text_y', 0))
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if k == 'text' or (ev.kind == 'drag' and self.pressed == 'text') or ev.kind in ('wheelup', 'wheeldown'):
            if ev.kind == 'down':
                self.pressed = 'text'
            if self.ta.mouse(ev, ev.x - getattr(self, 'text_x', 0), ev.y - getattr(self, 'text_y', 0)):
                self.invalidate()
                return True
        if ev.kind == 'up':
            self.pressed = None
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 'ctrl+s':
            self.save()
        elif k == 'ctrl+b':
            self.wrap_sel('**')
        elif k == 'ctrl+i' and False:
            self.wrap_sel('*')
        elif k == 'ctrl+e':
            self.export_docx()
        elif k == 'f11' or k == 'ctrl+t':
            self.toggle_focus()
        elif k == 'esc' and self.focus_mode:
            self.focus_mode = False
        elif k == 'ctrl+c':
            t = self.ta.selected_text()
            if t:
                self.wm.set_clipboard(t)
        elif k == 'ctrl+x':
            t = self.ta.selected_text()
            if t:
                self.wm.set_clipboard(t)
                self.ta.snapshot('other')
                self.ta.delete_selection()
        elif k == 'ctrl+v':
            if self.wm.clipboard:
                self.ta.insert(self.wm.clipboard, 'other')
        elif self.ta.key(ev):
            self.ta.ensure_visible()
        else:
            return False
        self.update_title()
        self.invalidate()
        return True

    def selection_text(self):
        return self.ta.selected_text() or ''

    def on_paste(self, text):
        self.ta.insert(text, 'other')
        self.ta.ensure_visible()
        self.update_title()
        self.invalidate()


APP = Writer
