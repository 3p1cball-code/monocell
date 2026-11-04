"""Writer: WYSIWYG word processor on the cell grid.

Big FIGlet text, -/= underlines, floating pictures with text wrap; stored as
the Writer Markdown dialect (see richdoc). .docx/.odt import, .docx export.
"""
import os
import re
import time
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..app import App
from ..textarea import TextArea
from ..util import fit, shorten_path
from ..widgets import button, sb_draw, sb_geom
from .. import imaging, printer, richdoc, writer_doc

W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
TAG_RE = re.compile(r'</?(?:u[123]?|size[23]|b|i)>', re.I)


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
            t = ''.join((x.text or '') if x.tag == W_NS + 't' else ('\t' if x.tag == W_NS + 'tab' else '')
                        for x in r)
            if not t:
                continue
            if bold and t.strip():
                t = '**%s**' % t
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


DOCX_IMG_TYPES = {'.png': ('png', 'image/png'), '.jpg': ('jpeg', 'image/jpeg'),
                  '.jpeg': ('jpeg', 'image/jpeg'), '.gif': ('gif', 'image/gif'),
                  '.bmp': ('bmp', 'image/bmp')}
EMU_INCH = 914400
EMU_CM = 360000
MAX_W = int(15 * EMU_CM)          # 15 cm inside the 16 cm text column
MAX_H = int(18 * EMU_CM)


def _docx_image_data(path):
    """(bytes, ext, content_type, px_w, px_h) for a picture, or None.

    PNG/JPEG/GIF/BMP go in unchanged; other formats are converted through Pillow.
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        from .. import imaging as imaging_mod
        img = imaging_mod.load(path)
        pw, ph = img.w, img.h
    except Exception:
        pw = ph = 0
    if ext in DOCX_IMG_TYPES:
        kind, ctype = DOCX_IMG_TYPES[ext]
        return raw, kind, ctype, pw, ph
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(raw)) as im:
            if pw <= 0:
                pw, ph = im.size
            buf = io.BytesIO()
            im.convert('RGB').save(buf, 'PNG')
            return buf.getvalue(), 'png', 'image/png', pw, ph
    except Exception:
        return None


def _drawing_xml(rid, pid, alt, cx, cy, fname):
    return ('<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            '<wp:docPr id="%d" name="Bild %d" descr="%s"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr><pic:cNvPr id="%d" name="%s"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
            % (cx, cy, pid, pid, escape(alt), pid, escape(fname), rid, cx, cy))


def _docx_runs(p):
    out = []
    text, st = p.text, p.st
    i = 0
    while i < len(text):
        s = st[i]
        j = i + 1
        while j < len(text) and st[j] == s:
            j += 1
        props = ''
        if s.bold:
            props += '<w:b/>'
        if s.ul:
            props += '<w:u w:val="%s"/>' % ('single', 'double', 'thick')[min(s.ul, 3) - 1]
        if s.size != 1:
            props += '<w:sz w:val="%d"/>' % (44 if s.size == 2 else 64)
        rpr = '<w:rPr>%s</w:rPr>' % props if props else ''
        out.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(text[i:j])))
        i = j
    return ''.join(out)


def write_docx(path, text, base_dir=''):
    """Markdown/WYSIWYG source -> .docx, with pictures inside the file."""
    from .. import richdoc
    doc = richdoc.parse(text)
    floats = {}
    for f in doc.floats:
        floats.setdefault(id(f.anchor), []).append(f)
    media = []          # (name, bytes, content_type)
    rels = [('rId1', 'styles', 'styles.xml')]
    paras = []
    for p in doc.paras:
        for f in floats.get(id(p), ()):
            src = os.path.expanduser(f.path)
            if not os.path.isabs(src):
                src = os.path.join(base_dir or os.getcwd(), src)
            data = _docx_image_data(src)
            if data is None:
                alt = f.alt or os.path.basename(f.path)
                paras.append('<w:p><w:r><w:t xml:space="preserve">[Bild: %s]</w:t></w:r></w:p>'
                             % escape(alt))
                continue
            raw, kind, ctype, pw, ph = data
            n = len(media) + 1
            name = 'image%d.%s' % (n, kind)
            media.append((name, raw, ctype))
            rid = 'rId%d' % (n + 1)
            rels.append((rid, 'image', 'media/' + name))
            cx = max(1, pw) * 9525
            cy = max(1, ph) * 9525
            scale = min(1.0, MAX_W / cx, MAX_H / cy)
            cx, cy = max(1, int(cx * scale)), max(1, int(cy * scale))
            alt = f.alt or os.path.splitext(os.path.basename(f.path))[0]
            paras.append(_drawing_xml(rid, n, alt, cx, max(1, cy), name))
        style = None
        if p.kind == 'h':
            style = 'Heading%d' % max(1, min(6, p.level))
        elif p.kind == 'li':
            style = 'ListBullet'
        elif p.kind == 'rule':
            paras.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" '
                         'w:color="888888"/></w:pBdr></w:pPr></w:p>')
            continue
        elif p.kind == 'code':
            style = 'Code'
        if not p.text and style is None:
            paras.append('<w:p/>')
            continue
        ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ''
        paras.append('<w:p>%s%s</w:p>' % (ppr, _docx_runs(p)))
    doc_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
               'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
               'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
               'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>%s'
               '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1417" '
               'w:bottom="1134" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/>'
               '</w:sectPr></w:body></w:document>') % ''.join(paras)
    styles = ['<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:pPr><w:spacing w:after="160"/></w:pPr><w:rPr><w:sz w:val="22"/></w:rPr></w:style>']
    for lvl, size in ((1, 36), (2, 30), (3, 26), (4, 24), (5, 22), (6, 22)):
        styles.append('<w:style w:type="paragraph" w:styleId="Heading%d"><w:name w:val="heading %d"/>'
                      '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/>'
                      '<w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="%d"/></w:pPr>'
                      '<w:rPr><w:b/><w:sz w:val="%d"/></w:rPr></w:style>' % (lvl, lvl, lvl - 1, size))
    styles.append('<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
                  '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:style>')
    styles.append('<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/>'
                  '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0"/></w:pPr>'
                  '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/><w:sz w:val="20"/></w:rPr></w:style>')
    styles_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles '
                  'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">%s</w:styles>') % ''.join(styles)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>']
    seen = set()
    for _name, _raw, ctype in media:
        ext = _name.rsplit('.', 1)[-1]
        if ext not in seen:
            seen.add(ext)
            ct.append('<Default Extension="%s" ContentType="%s"/>' % (ext, ctype))
    ct.append('<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
              'officedocument.wordprocessingml.document.main+xml"/>')
    ct.append('<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
              'officedocument.wordprocessingml.styles+xml"/>')
    ct.append('</Types>')
    ct_xml = ''.join(ct)
    rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
                '</Relationships>') % ''.join(
        '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/%s" Target="%s"/>' % (rid, kind, target) for rid, kind, target in rels)
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct_xml)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('word/document.xml', doc_xml)
        z.writestr('word/styles.xml', styles_xml)
        z.writestr('word/_rels/document.xml.rels', rels_xml)
        for name, raw, _ctype in media:
            z.writestr('word/media/' + name, raw)


INLINE = re.compile(r'(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(`[^`\n]+`)|(\[[^\]\n]+\]\([^)\n]+\))')
WORD = re.compile(r'\w+|[^\w\s]+')
AUTO_PREFIX = re.compile(r'(#{1,6})|([-*+])|(\d+[.)])|(>)')
HELP = ('Ctrl+B bold  Ctrl+U underline  Alt+U double  Ctrl+G size  '
        'Img: drag = move, corner # = resize, W = wrap  F5 source')


class Writer(App):
    """WYSIWYG word processor: what you edit is what is printed."""
    name = 'writer'
    title = 'Writer'
    icon = '[W]'
    size = (84, 28)
    min_size = (34, 8)
    shift_select = True

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.ta = TextArea(wrap=True)          # source view (F5)
        self.ta.scrollbar = True
        self.ta.style = self.style
        self.path = None
        self.focus_mode = False
        self.col = 72
        self.source = False
        self.doc = richdoc.Doc()
        self.cur = (0, 0)
        self.anchor = None
        self.want_x = None
        self.type_style = None
        self.sel_float = None
        self.drag = None
        self.top = 0
        self.view_h = 1
        self.colw = 40
        self.page_w = 80
        self.p_margin = 5
        self.p_margin_v = 3
        self.rows_per_page = 60
        self.block = 69
        self.pages = 1
        self.page_x = 0
        self.sb = None
        self.sb_grab = None
        self.text_x = 0
        self.text_y = 0
        self.undo_stack = []
        self.redo_stack = []
        self.last_kind = None
        self.last_time = 0.0
        self.modified = False
        self.ver = 0
        self.img_ver = 0
        self.lay = None
        self.lay_key = None
        self.rich_clip = None
        self.images = richdoc.ImageStore(runner=self._run_bg, on_ready=self._image_ready)
        if path:
            self.load(path)
        else:
            self.set_doc(richdoc.parse('# Untitled\n\nStart writing. **Bold**, '
                                       '<u>underlined</u>, <size2>big</size2>, pictures with Img.\n'))
            self.cur = (len(self.doc.paras) - 1, 0)
            self.modified = False
        self.update_title()

    # ================================================================ plumbing
    def _run_bg(self, work, done):
        self.thread(work, done)

    def _image_ready(self):
        self.img_ver += 1
        self.invalidate()

    def set_doc(self, doc):
        self.doc = doc
        self.cur = (0, 0)
        self.anchor = None
        self.sel_float = None
        self.top = 0
        self.undo_stack = []
        self.redo_stack = []
        self.changed(modified=False)

    def changed(self, modified=True):
        self.ver += 1
        if modified:
            self.modified = True
        self.invalidate()

    def snapshot(self, kind='other'):
        now = time.monotonic()
        if kind == 'type' and self.last_kind == 'type' and now - self.last_time < 2.0:
            self.last_time = now
            return
        self.undo_stack.append((self.doc.copy(), self.cur, self.anchor))
        if len(self.undo_stack) > 300:
            del self.undo_stack[0]
        self.redo_stack = []
        self.last_kind = kind
        self.last_time = now

    def _restore(self, state):
        doc, cur, anchor = state
        self.doc = doc
        self.cur = self._clamp(cur)
        self.anchor = self._clamp(anchor) if anchor else None
        self.sel_float = None
        self.last_kind = None
        self.changed()

    def undo(self):
        if not self.undo_stack:
            self.wm.toast('Nothing to undo', 'dim')
            return
        self.redo_stack.append((self.doc.copy(), self.cur, self.anchor))
        self._restore(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.doc.copy(), self.cur, self.anchor))
        self._restore(self.redo_stack.pop())

    def _clamp(self, pos):
        pi, i = pos
        pi = max(0, min(pi, len(self.doc.paras) - 1))
        return pi, max(0, min(i, len(self.doc.paras[pi].text)))

    @property
    def base_dir(self):
        return os.path.dirname(os.path.abspath(self.path)) if self.path else os.path.expanduser('~')

    def layout(self, colw=None):
        colw = colw or self.colw
        cfg = self.wm.cfg
        key = (self.ver, colw, self.img_ver, cfg.get('image_mode'), cfg.get('dither'),
               cfg.get('ascii_charset'), round(imaging.cell_aspect(self.wm), 3),
               cfg.get('writer_font2'),
               cfg.get('writer_font3'), tuple(self.th.rgb), id(self.th),
               getattr(getattr(self.wm, 'term', None), 'mode', None), self.base_dir,
               self.rows_per_page)
        lines = int(cfg.get('writer_page_lines') or 66)
        self.rows_per_page = max(4, lines - 2 * self.p_margin_v)
        if self.lay is None or self.lay_key != key:
            self.lay = richdoc.Layout(self.wm, colw, self.base_dir, self.images, mono=True,
                                      page=self.rows_per_page, bg_role='win_on').run(self.doc)
            self.lay_key = key
        return self.lay

    # ================================================================ source view
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
                out += [(m.start(), m.start() + 2, th.c('sel', 'win')),
                        (m.start() + 2, m.end() - 2, th.c('bright', 'win', 1)),
                        (m.end() - 2, m.end(), th.c('sel', 'win'))]
            elif m.group(2):
                out += [(m.start(), m.start() + 1, th.c('sel', 'win')),
                        (m.start() + 1, m.end() - 1, th.c('accent2', 'win')),
                        (m.end() - 1, m.end(), th.c('sel', 'win'))]
            elif m.group(3):
                out.append((m.start(), m.end(), th.c('cyan', 'win')))
            elif m.group(4):
                out.append((m.start(), m.end(), th.c('blue', 'win')))
        for m in TAG_RE.finditer(line):
            out.append((m.start(), m.end(), th.c('magenta', 'win')))
        if writer_doc.IMAGE_RE.match(line):
            out.append((0, len(line), th.c('blue', 'win')))
        return out

    def toggle_source(self):
        if self.source:
            text = self.ta.text()
            if text != self.src_before:
                self.snapshot()
                cy = self.ta.cy
                self.doc = richdoc.parse(text)
                self.sel_float = None
                self.anchor = None
                self.cur = self._clamp((cy, 0))
                self.changed()
            self.source = False
        else:
            text = richdoc.serialize(self.doc)
            self.src_before = text
            self.ta.set_text(text)
            self.ta.cy = min(self.cur[0], len(self.ta.lines) - 1)
            self.ta.cx = 0
            self.ta.modified = False
            self.source = True
        self.update_title()
        self.invalidate()

    def doc_text(self):
        if self.source:
            return self.ta.text()
        return richdoc.serialize(self.doc)

    # ================================================================ files
    def update_title(self):
        name = os.path.basename(self.path) if self.path else 'untitled.md'
        if self.source:
            name += ' [source]'
        dirty = self.modified or (self.source and self.ta.text() != getattr(self, 'src_before', ''))
        self.set_title(('* ' if dirty else '') + name)

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
            self.path = path
            self.set_doc(richdoc.parse(txt.replace('\r\n', '\n').replace('\t', '    ')))
            if self.source:
                self.source = False
        except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
            self.wm.toast('Cannot open: %s' % e, 'red')

    def save(self, path=None, done=None):
        path = path or self.path
        if not path or path.lower().endswith('.odt'):
            return self.save_as(done)
        try:
            text = self.doc_text()
            if path.lower().endswith('.docx'):
                write_docx(path, text, self.base_dir)
            elif path.lower().endswith('.txt'):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(richdoc.export_text(self.doc))
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
            self.path = path
            self.modified = False
            if self.source:
                self.src_before = self.ta.text()
            self.wm.toast('Saved ' + shorten_path(path, 50), 'green')
            self.update_title()
            if done:
                done()
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def save_as(self, done=None):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.md' if self.path else 'document.md'
        self.wm.pick_file(lambda p: self.save(p, done), 'save',
                          start=os.path.dirname(self.path) if self.path else None,
                          name=name, parent=self.win)

    def export_txt(self):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.txt' if self.path else 'document.txt'

        def ok(p):
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(richdoc.export_text(self.doc))
                self.wm.toast('Exported ' + os.path.basename(p), 'green')
            except OSError as e:
                self.wm.toast(str(e), 'red')
        self.wm.pick_file(ok, 'save', name=name, parent=self.win)

    def export_docx(self):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.docx' if self.path else 'document.docx'

        def ok(p):
            try:
                write_docx(p, self.doc_text(), self.base_dir)
                self.wm.toast('Exported ' + os.path.basename(p), 'green')
            except OSError as e:
                self.wm.toast(str(e), 'red')
        self.wm.pick_file(ok, 'save', name=name, parent=self.win)

    def on_close(self):
        self.update_title()
        if not (self.modified or self.win.title.startswith('* ')):
            return True

        def res(r, v):
            if r == 'Save':
                self.save(done=lambda: self.wm.close(self.win, force=True))
            elif r == 'Discard':
                self.wm.close(self.win, force=True)
        self.wm.dialog('Unsaved changes', 'Save changes to this document?', ('Save', 'Discard', 'Cancel'),
                       res, parent=self.win)
        return False

    def print_doc(self):
        """The sheet as the printer sees it: one ink, characters only, page by page."""
        text = self.doc_text()
        cfg = self.wm.cfg
        page_w = int(cfg.get('writer_page_w') or 80)
        lines_pp = int(cfg.get('writer_page_lines') or 66)
        mv = self.p_margin_v
        rows_pp = max(4, lines_pp - 2 * mv)
        margin = 5 if page_w >= 56 else 2
        text_w = page_w - 2 - 2 * margin
        bottom = self.th.c('text', 'win')
        try:
            lay = richdoc.Layout(self.wm, text_w, self.base_dir, self.images, mono=True,
                                 page=rows_pp, bg_role='win', sync=True).run(richdoc.parse(text))
            pages = max(1, (lay.height + rows_pp - 1) // rows_pp)
            out = []
            for k in range(pages):
                for j in range(mv):
                    out.append(('', []))
                for r in range(rows_pp):
                    idx = k * rows_pp + r
                    chars, attrs = lay.rows[idx] if idx < len(lay.rows) else ([], [])
                    chars = ''.join(chars)
                    line = (' ' * (margin + 1) + chars)[:page_w].ljust(page_w)
                    a = [bottom] * page_w
                    for j, at in enumerate(attrs[:len(chars)]):
                        if margin + 1 + j < page_w:
                            a[margin + 1 + j] = at
                    out.append((line, a))
                for j in range(mv):
                    out.append(('', []))
                label = 'Seite %d/%d' % (k + 1, pages)
                out[k * lines_pp] = (label.rjust(page_w), [])
            data = printer.encode(out, width=page_w, page_lines=lines_pp)
        except Exception as e:
            self.wm.toast('Print failed: %s' % e, 'red')
            return
        target = (self.wm.cfg.get('printer_device') or '').strip()
        if target:
            try:
                printer.send(data, target)
                self.wm.toast('Sent %d bytes to %s' % (len(data), target), 'green')
            except Exception as e:
                self.wm.toast('Print failed: %s' % e, 'red')
            return
        name = os.path.splitext(os.path.basename(self.path))[0] + '.prn' if self.path else 'document.prn'

        def ok(path):
            try:
                with open(path, 'wb') as f:
                    f.write(data)
                self.wm.toast('Print file saved: %s (%d bytes)' % (os.path.basename(path), len(data)),
                              'green')
            except OSError as e:
                self.wm.toast(str(e), 'red')

        self.wm.pick_file(ok, 'save', name=name, parent=self.win, title='Save printer file')

    # ================================================================ selection
    def sel_range(self):
        if self.anchor is None or self.anchor == self.cur:
            return None
        return (self.anchor, self.cur) if self.anchor < self.cur else (self.cur, self.anchor)

    def selection_text(self):
        if self.source:
            return self.ta.selected_text() or ''
        r = self.sel_range()
        return richdoc.plain(self.doc, *r) if r else ''

    def copy_sel(self):
        r = self.sel_range()
        if not r:
            return False
        (p0, i0), (p1, i1) = r
        frags = []
        for pi in range(p0, p1 + 1):
            p = self.doc.paras[pi]
            a = i0 if pi == p0 else 0
            b = i1 if pi == p1 else len(p.text)
            q = p.copy()
            q.text, q.st = p.text[a:b], p.st[a:b]
            frags.append(q)
        text = richdoc.plain(self.doc, *r)
        self.rich_clip = (text, frags)
        self.wm.set_clipboard(text)
        return True

    # ================================================================ editing core
    def para(self, pi=None):
        return self.doc.paras[self.cur[0] if pi is None else pi]

    def style_here(self):
        if self.type_style is not None:
            return self.type_style
        pi, i = self.cur
        p = self.doc.paras[pi]
        if p.kind == 'code':
            return richdoc.PLAIN
        if i > 0:
            s = p.st[i - 1]
        elif p.st:
            s = p.st[0]
        else:
            s = richdoc.PLAIN
        if s.link and not (i < len(p.st) and p.st[i].link == s.link):
            s = s._replace(link='')
        if s.code and not (i < len(p.st) and p.st[i].code):
            s = s._replace(code=False)
        return s

    def delete_range(self, a, b):
        (p0, i0), (p1, i1) = a, b
        paras = self.doc.paras
        P0, P1 = paras[p0], paras[p1]
        if p0 == p1:
            P0.set(P0.text[:i0] + P0.text[i1:], P0.st[:i0] + P0.st[i1:])
        else:
            if P0.kind == 'rule':
                P0.kind, P0.level, P0.indent, P0.marker = P1.kind, P1.level, P1.indent, P1.marker
            P0.set(P0.text[:i0] + P1.text[i1:], P0.st[:i0] + P1.st[i1:])
            gone = paras[p0 + 1:p1 + 1]
            for f in self.doc.floats:
                if any(f.anchor is g for g in gone):
                    f.anchor = P0
            del paras[p0 + 1:p1 + 1]
        self.cur = (p0, i0)
        self.anchor = None

    def delete_sel(self):
        r = self.sel_range()
        if not r:
            return False
        self.delete_range(*r)
        return True

    def insert_chars(self, text, style=None):
        """Insert one line of text (no newlines) at the cursor."""
        if not text:
            return
        pi, i = self.cur = self._clamp(self.cur)
        p = self.doc.paras[pi]
        if p.kind == 'rule':
            p.kind = 'p'
        style = style or self.style_here()
        if p.kind == 'code':
            style = richdoc.PLAIN
        p.set(p.text[:i] + text + p.text[i:], p.st[:i] + [style] * len(text) + p.st[i:])
        self.cur = (pi, i + len(text))

    def split_para(self):
        pi, i = self.cur
        p = self.doc.paras[pi]
        q = p.copy()
        q.uid = next(richdoc._uid)
        q.lang = ''
        q.set(p.text[i:], p.st[i:])
        before_style = p.st[i - 1] if i > 0 else None
        p.set(p.text[:i], p.st[:i])
        if p.kind == 'h':
            if i == 0 and q.text:
                p.kind, p.level = 'p', 0
            else:
                q.kind, q.level = 'p', 0
        elif p.kind == 'li':
            m = re.match(r'(\d+)([.)])$', p.marker)
            if m:
                q.marker = '%d%s' % (int(m.group(1)) + 1, m.group(2))
        elif p.kind == 'rule':
            q.kind = 'p'
        self.doc.paras.insert(pi + 1, q)
        self.cur = (pi + 1, 0)
        if before_style is not None and not q.text:
            self.type_style = before_style._replace(link='')

    def insert_text(self, text, rich=None):
        self.delete_sel()
        if rich and rich[0] == text:
            frags = rich[1]
            for n, fr in enumerate(frags):
                if n:
                    self.split_para()
                    if fr.kind != 'p':
                        q = self.para()
                        q.kind, q.level, q.indent, q.marker = fr.kind, fr.level, fr.indent, fr.marker
                        q.touch()
                if fr.text:
                    pi, i = self.cur
                    p = self.doc.paras[pi]
                    p.set(p.text[:i] + fr.text + p.text[i:], p.st[:i] + list(fr.st) + p.st[i:])
                    self.cur = (pi, i + len(fr.text))
            return
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\t', '    ')
        for n, line in enumerate(text.split('\n')):
            if n:
                self.split_para()
            self.insert_chars(''.join(c for c in line if c.isprintable()))

    def autoformat_space(self):
        """'# ' '- ' '1. ' '> ' at the start of a paragraph switch its kind."""
        pi, i = self.cur
        p = self.doc.paras[pi]
        if p.kind != 'p' or i == 0 or i > 7:
            return False
        m = AUTO_PREFIX.fullmatch(p.text[:i])
        if not m:
            return False
        self.snapshot()
        if m.group(1):
            p.kind, p.level = 'h', len(m.group(1))
        elif m.group(2) or m.group(3):
            p.kind, p.marker, p.indent = 'li', m.group(0) if m.group(3) else '-', 0
        else:
            p.kind = 'quote'
        p.set(p.text[i:], p.st[i:])
        self.cur = (pi, 0)
        return True

    def enter(self):
        pi, i = self.cur
        p = self.doc.paras[pi]
        self.snapshot()
        self.delete_sel()
        pi, i = self.cur
        p = self.doc.paras[pi]
        if p.kind == 'p' and p.text.strip() in ('---', '***', '___') and i == len(p.text):
            p.kind = 'rule'
            p.set('', [])
            self.doc.paras.insert(pi + 1, richdoc.Para())
            self.cur = (pi + 1, 0)
            return
        if p.kind == 'p' and p.text.startswith('```') and i == len(p.text):
            p.kind, p.lang = 'code', p.text[3:].strip()
            p.set('', [])
            self.cur = (pi, 0)
            return
        if p.kind in ('li', 'quote', 'code') and not p.text:
            p.kind = 'p'
            p.touch()
            return
        self.split_para()

    def backspace(self):
        if self.sel_range():
            self.snapshot()
            self.delete_sel()
            return
        pi, i = self.cur
        p = self.doc.paras[pi]
        if i > 0:
            self.snapshot('type')
            self.delete_range((pi, i - 1), (pi, i))
            return
        self.snapshot()
        if p.kind in ('li', 'quote', 'h', 'code', 'rule'):
            p.kind, p.level = 'p', 0
            p.touch()
            return
        if pi == 0:
            return
        prev = self.doc.paras[pi - 1]
        if prev.kind == 'rule':
            for f in self.doc.floats:
                if f.anchor is prev:
                    f.anchor = p
            del self.doc.paras[pi - 1]
            self.cur = (pi - 1, 0)
            return
        self.delete_range((pi - 1, len(prev.text)), (pi, 0))

    def delete(self):
        if self.sel_range():
            self.snapshot()
            self.delete_sel()
            return
        pi, i = self.cur
        p = self.doc.paras[pi]
        if i < len(p.text):
            self.snapshot('type')
            self.delete_range((pi, i), (pi, i + 1))
        elif pi + 1 < len(self.doc.paras):
            self.snapshot()
            self.delete_range((pi, i), (pi + 1, 0))

    def word_left(self):
        pi, i = self.cur
        if i == 0:
            return (pi - 1, len(self.doc.paras[pi - 1].text)) if pi > 0 else (0, 0)
        t = self.doc.paras[pi].text
        k = i
        while k > 0 and t[k - 1].isspace():
            k -= 1
        while k > 0 and not t[k - 1].isspace():
            k -= 1
        return pi, k

    def word_right(self):
        pi, i = self.cur
        t = self.doc.paras[pi].text
        if i >= len(t):
            return (pi + 1, 0) if pi + 1 < len(self.doc.paras) else (pi, i)
        k = i
        while k < len(t) and not t[k].isspace():
            k += 1
        while k < len(t) and t[k].isspace():
            k += 1
        return pi, k

    # ================================================================ formatting
    def word_range(self):
        """The word at the cursor, for formatting without a selection (as in Word)."""
        pi, i = self.cur
        t = self.doc.paras[pi].text
        if not t or self.doc.paras[pi].kind == 'code':
            return None
        if not (0 < i < len(t) and not t[i - 1].isspace() and not t[i].isspace()):
            return None                    # only *inside* a word, as in Word
        a = i
        s0 = a
        while s0 > 0 and not t[s0 - 1].isspace():
            s0 -= 1
        s1 = a + 1
        while s1 < len(t) and not t[s1].isspace():
            s1 += 1
        return (pi, s0), (pi, s1)

    def format_range(self):
        return self.sel_range() or self.word_range()

    def style_chars(self):
        r = self.format_range()
        if not r:
            return []
        (p0, i0), (p1, i1) = r
        out = []
        for pi in range(p0, p1 + 1):
            p = self.doc.paras[pi]
            a = i0 if pi == p0 else 0
            b = i1 if pi == p1 else len(p.text)
            out.extend(p.st[a:b])
        return out

    def restyle(self, fn):
        r = self.format_range()
        self.type_style = fn(self.style_here())
        if not r:
            return
        self.snapshot()
        (p0, i0), (p1, i1) = r
        for pi in range(p0, p1 + 1):
            p = self.doc.paras[pi]
            a = i0 if pi == p0 else 0
            b = i1 if pi == p1 else len(p.text)
            if b > a:
                p.set(p.text, p.st[:a] + [fn(s) for s in p.st[a:b]] + p.st[b:])
        self.changed()

    def toggle(self, field):
        chars = self.style_chars() or [self.style_here()]
        on = not all(getattr(s, field) for s in chars)
        self.restyle(lambda s: s._replace(**{field: on}))
        self.after_edit()

    def cycle_ul(self, level=None):  # noqa: D401 - see help text
        """Ctrl+U: single underline on/off; U2 / Ctrl+Shift+U: double on/off."""
        chars = self.style_chars() or [self.style_here()]
        want = level or 1
        nxt = 0 if all(s.ul == want for s in chars) or (level is None and chars[0].ul) else want
        self.restyle(lambda s: s._replace(ul=nxt))
        self.after_edit()

    def cycle_size(self, size=None):
        chars = self.style_chars() or [self.style_here()]
        cur = chars[0].size
        nxt = size if size is not None else {1: 2, 2: 3}.get(cur, 1)
        if size is not None and all(s.size == size for s in chars):
            nxt = 1
        self.restyle(lambda s: s._replace(size=nxt))
        self.after_edit()

    def para_range(self):
        r = self.sel_range()
        if r:
            return range(r[0][0], r[1][0] + 1)
        return range(self.cur[0], self.cur[0] + 1)

    def cycle_heading(self):
        self.snapshot()
        first = self.doc.paras[self.para_range()[0]]
        if first.kind != 'h':
            kind, level = 'h', 1
        elif first.level < 3:
            kind, level = 'h', first.level + 1
        else:
            kind, level = 'p', 0
        for pi in self.para_range():
            p = self.doc.paras[pi]
            if p.kind == 'rule':
                continue
            p.kind, p.level = kind, level
            p.touch()
        self.after_edit()

    def toggle_list(self):
        self.snapshot()
        first = self.doc.paras[self.para_range()[0]]
        to_list = first.kind != 'li'
        for pi in self.para_range():
            p = self.doc.paras[pi]
            if p.kind == 'rule':
                continue
            p.kind = 'li' if to_list else 'p'
            p.level = 0
            if to_list:
                p.marker, p.indent = '-', 0
            p.touch()
        self.after_edit()

    def indent(self, back=False):
        p = self.para()
        if p.kind != 'li':
            if not back:
                self.snapshot('type')
                self.insert_chars('    ')
                self.after_edit()
            return
        self.snapshot()
        for pi in self.para_range():
            q = self.doc.paras[pi]
            if q.kind == 'li':
                q.indent = max(0, q.indent + (-2 if back else 2))
                q.touch()
        self.after_edit()

    # ================================================================ pictures
    def insert_image(self):
        start = os.path.dirname(self.path) if self.path else os.path.expanduser('~')

        def ok(path):
            if not path:
                return
            try:
                rel = os.path.relpath(path, self.base_dir)
            except ValueError:
                rel = path
            target = path if rel.startswith('..') else rel
            alt = os.path.splitext(os.path.basename(path))[0]
            self.snapshot()
            p = self.para()
            w = max(8, min(36, self.colw // 2))
            f = richdoc.Float(target, alt, p, x=0, dy=0, w=w, wrap='square')
            self.doc.floats.append(f)
            self.sel_float = f
            self.anchor = None
            self.changed()
            self.update_title()

        self.wm.pick_file(ok, 'open', start=start, title='Insert image', parent=self.win)

    @staticmethod
    def _float_state(doc):
        idx = {id(p): n for n, p in enumerate(doc.paras)}
        return [(f.path, f.x, f.dy, f.w, f.wrap, idx.get(id(f.anchor))) for f in doc.floats]

    def float_rect(self, f):
        for g, x, y, w, h in self.layout().floats:
            if g is f:
                return x, y, w, h
        return None

    def fix_float(self, f):
        """Legacy pictures (fitted, centred) get explicit numbers before they move."""
        r = self.float_rect(f)
        if r and (f.w is None or f.x is None):
            f.x, f.w = r[0], r[2]

    def move_float(self, f, x, y):
        lay = self.layout()
        r = self.float_rect(f)
        if not r:
            return
        self.fix_float(f)
        w = r[2]
        f.x = max(0, min(lay.width - w, x))
        y = max(0, y)
        pi = lay.para_at_row(y)
        f.anchor = self.doc.paras[pi]
        f.dy = max(0, y - lay.tops[pi])
        self.changed()

    def resize_float(self, f, w):
        lay = self.layout()
        self.fix_float(f)
        f.w = max(4, min(lay.width - (f.x or 0), w))
        self.changed()

    def float_key(self, k, ev):
        f = self.sel_float
        r = self.float_rect(f)
        if r is None:
            self.sel_float = None
            return False
        x, y, w, h = r
        step = 4 if 'shift+' in k else 1
        base = k.replace('shift+', '')
        if base in ('left', 'right', 'up', 'down'):
            self.snapshot('float')
            dx = {'left': -step, 'right': step}.get(base, 0)
            dy = {'up': -step, 'down': step}.get(base, 0)
            self.move_float(f, x + dx, y + dy)
        elif k in ('+', '=', 'ctrl+right'):
            self.snapshot('float')
            self.resize_float(f, w + 2)
        elif k in ('-', 'ctrl+left'):
            self.snapshot('float')
            self.resize_float(f, w - 2)
        elif k in ('w', 'W'):
            self.snapshot()
            self.fix_float(f)
            f.wrap = 'block' if f.wrap == 'square' else 'square'
            self.wm.toast('Text wrap: %s' % ('around the picture' if f.wrap == 'square'
                                             else 'above and below'), 'accent')
            self.changed()
        elif k in ('delete', 'backspace'):
            self.snapshot()
            self.doc.floats = [g for g in self.doc.floats if g is not f]
            self.sel_float = None
            self.changed()
        elif k in ('esc', 'enter'):
            self.sel_float = None
        elif k == 'ctrl+z':
            self.undo()
        elif k in ('ctrl+y', 'ctrl+shift+z'):
            self.redo()
        elif k == 'ctrl+s':
            self.save()
        else:
            self.sel_float = None
            return None                 # let the text editor have the key
        self.update_title()
        self.invalidate()
        return True

    # ================================================================ drawing
    def outline(self):
        return [(i, p) for i, p in enumerate(self.doc.paras) if p.kind == 'h']

    def frame(self, w):
        """Sheet geometry: a printer page, text area inside its margins."""
        side = 24 if w >= 110 and not self.focus_mode else 0
        avail = w - side - 1                      # one column for the scrollbar
        cfg = self.wm.cfg
        lines = int(cfg.get('writer_page_lines') or 66)
        self.rows_per_page = max(4, lines - 2 * self.p_margin_v)
        self.block = 2 * self.p_margin_v + self.rows_per_page + 3
        page_w = int(cfg.get('writer_page_w') or 80)
        page_w = max(24, min(page_w, avail - 2))
        margin = 5 if page_w >= 56 else 2
        text_w = page_w - 2 - 2 * margin
        if text_w < 12:                           # very narrow window
            margin = 1
            text_w = max(8, page_w - 2 - 2 * margin)
        self.page_w, self.p_margin = page_w, margin
        self.page_x = side + max(1, (avail - page_w) // 2)
        self.text_x = self.page_x + 1 + margin
        return side, self.page_x, text_w, avail

    # --- page geometry: layout row <-> screen row -------------------------
    def y_scr(self, gy):
        rows = self.rows_per_page
        k, local = divmod(max(0, gy), rows)
        return k * self.block + 1 + self.p_margin_v + local

    def y_doc(self, sy):
        rows = self.rows_per_page
        k, local = divmod(max(0, sy), self.block)
        top = 1 + self.p_margin_v
        if local < top:
            return k * rows
        if local < top + rows:
            return k * rows + local - top
        return (k + 1) * rows - 1

    def toolbar(self, cv, w):
        sty = self.style_chars() or [self.style_here()] if not self.source else []
        s0 = sty[0] if sty else richdoc.PLAIN
        p = self.para() if not self.source else None
        rows = [
            [('New', 'new', False), ('Open', 'open', False), ('Save', 'save', False),
             ('DOCX', 'docx', False), ('TXT', 'txt', False), ('Print', 'print', False), None,
             ('Img', 'image', False), ('Source' if not self.source else 'Page', 'source', self.source),
             ('Focus', 'focus', self.focus_mode)],
            [('B', 'bold', s0.bold), ('U1', 'underline', s0.ul == 1),
             ('U2', 'underline2', s0.ul >= 2), ('S1', 'size1', s0.size == 1),
             ('S2', 'size2', s0.size == 2),
             ('S3', 'size3', s0.size == 3), None,
             ('H', 'head', bool(p and p.kind == 'h')), ('List', 'list', bool(p and p.kind == 'li'))],
        ]
        if self.sel_float is not None and not self.source:
            f = self.sel_float
            rows[1] += [None, ('Wrap:%s' % ('around' if f.wrap == 'square' else 'block'), 'fwrap', False),
                        ('-', 'fsmall', False), ('+', 'fbig', False), ('Del', 'fdel', False)]
        for row, items in enumerate(rows):
            x = 0
            for it in items:
                if it is None:
                    x += 1
                    continue
                label, key, active = it
                if x + len(label) + 3 > w:
                    break
                x += button(self, cv, x, row, label, key, active=active) + 1

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        top = 0
        if not self.focus_mode:
            self.toolbar(cv, w)
            top = 3
        side, page_x, colw, avail = self.frame(w)
        self.colw = colw
        if side:
            cv.put(0, top, 'OUTLINE', th.c('dim', 'win'))
            for j, (i, p) in enumerate(self.outline()[:h - top - 3]):
                cur = (not self.source and i == self.cur[0])
                a = th.c('accent', 'raised') if cur else (
                    th.c('bright', 'sel') if self.hover == ('ol', i) else th.c('text', 'win'))
                cv.put(0, top + 1 + j, fit('  ' * (p.level - 1) + p.text.strip(), side - 2), a)
                self.zone(0, top + 1 + j, side - 2, 1, ('ol', i))
        self.text_y = top
        view_h = max(1, h - top - 1)
        self.view_h = view_h
        if self.source:
            self.ta.draw(cv, self.text_x, top, colw, view_h, th, focused)
            st = 'source  F5 back to the page'
            cv.put(0, h - 1, fit(shorten_path(self.path, 40) if self.path else 'not saved',
                                 w - len(st) - 2), th.c('dim', 'win'))
            cv.put(w - len(st), h - 1, st, th.c('dim', 'win'))
            self.zone(side, top, avail, view_h, 'text')
            return
        lay = self.layout(colw)
        rows = self.rows_per_page
        self.pages = max(1, (lay.height + rows - 1) // rows)
        total = self.pages * self.block
        self.top = max(0, min(self.top, max(0, total - view_h)))
        paper = th.c('text', 'win_on')
        for i in range(view_h):
            sy = self.top + i
            k, local = divmod(sy, self.block)
            if k >= self.pages:
                break
            chars, attrs = self.page_line(lay, k, local, self.pages, paper)
            cv.blit_cells(page_x, top + i, [(chars, attrs)])
        self.zone(page_x, top, self.page_w, view_h, 'text')
        # selection (paper keeps its background, only the ink turns bright)
        r = self.sel_range()
        if r:
            sel_a = th.c('bright', 'sel')
            (p0, i0), (p1, i1) = r
            for pi in range(p0, p1 + 1):
                pl = lay.paras[pi]
                pt = lay.tops[pi]
                a = i0 if pi == p0 else 0
                b = i1 if pi == p1 else len(pl.boxes)
                for k in range(a, b):
                    bx0, bx1, by, bh = pl.boxes[k]
                    sy = self.y_scr(pt + by)
                    if sy + bh < self.top or sy > self.top + view_h:
                        continue
                    cv.recolor(self.text_x + bx0, top + sy - self.top, max(1, bx1 - bx0), bh,
                               lambda at, s=sel_a: s)
                if pi < p1:
                    ex, ey, eh = pl.end
                    cv.set_attr(self.text_x + ex, top + self.y_scr(pt + ey) - self.top, 1, 1, sel_a)
        # cursor
        if focused and self.sel_float is None:
            cx, cy, ch = lay.caret(*self.cur)
            cx = min(cx, colw - 1)
            ca = th.c('on_accent', 'accent')
            for yy in range(cy, cy + ch):
                ry = self.y_scr(yy) - self.top
                if 0 <= ry < view_h:
                    c = lay.rows[yy][0][cx] if yy < len(lay.rows) else ' '
                    cv.put(self.text_x + cx, top + ry, c, ca)
        # selected picture: frame + resize handle
        if self.sel_float is not None:
            fr = self.float_rect(self.sel_float)
            if fr is None:
                self.sel_float = None
            else:
                fx, fy, fw, fh = fr
                fa = th.c('accent', 'win', 1)
                X, Y = self.text_x + fx - 1, top + self.y_scr(fy) - 1 - self.top
                for yy in range(fh + 2):
                    ry = Y + yy
                    if not (top <= ry < top + view_h):
                        continue
                    if yy == 0 or yy == fh + 1:
                        cv.put(X, ry, '+' + '-' * fw + '+', fa)
                    else:
                        cv.putc(X, ry, '|', fa)
                        cv.putc(X + fw + 1, ry, '|', fa)
                if top <= Y + fh + 1 < top + view_h:
                    cv.putc(X + fw + 1, Y + fh + 1, '#', th.c('on_accent', 'accent'))
        # scrollbar
        gx = min(w - 1, page_x + self.page_w + 1)
        self.sb = sb_geom(gx, top, view_h, total, view_h, self.top)
        sb_draw(cv, self.sb, th, 'win', active=self.sb_grab is not None)
        if self.sb:
            self.zone(gx, top, 1, view_h, 'sbar')
        # status line
        if self.sel_float is not None:
            f = self.sel_float
            fr = self.float_rect(f) or (0, 0, 0, 0)
            st = '%s  %dx%d at %d  drag: move  # corner: size  arrows  +/-  W wrap  Del' % (
                os.path.basename(f.path), fr[2], fr[3], fr[0])
            cv.put(0, h - 1, fit(st, w), th.c('accent', 'win'))
        else:
            if getattr(self, '_count', (None,))[0] != self.ver:
                text = richdoc.plain(self.doc)
                self._count = (self.ver, len(re.findall(r'\w+', text)), len(text))
            page_no = self.y_scr(lay.caret(*self.cur)[1]) // self.block + 1
            st = '%d words  page %d/%d  F5 source' % (self._count[1], page_no, self.pages)
            cv.put(0, h - 1, fit(shorten_path(self.path, 40) if self.path else 'not saved',
                                 w - len(st) - 2), th.c('dim', 'win'))
            cv.put(w - len(st), h - 1, st, th.c('dim', 'win'))

    def page_line(self, lay, k, local, pages, paper):
        """One screen row of sheet k: border, margin, text or gap between sheets."""
        th = self.th
        W = self.page_w
        edge = th.c('dim', 'win_on')
        chars = [' '] * W
        attrs = [paper] * W
        mv, rows = self.p_margin_v, self.rows_per_page
        bottom_border = 1 + mv + rows + mv
        if local == 0 or local == bottom_border:
            chars[0] = chars[W - 1] = '+'
            for x in range(1, W - 1):
                chars[x] = '-'
            if local == 0:
                t = ' Seite %d/%d ' % (k + 1, pages)
                x = max(2, (W - len(t)) // 2)
                for j, c in enumerate(t):
                    if x + j < W - 1:
                        chars[x + j] = c
        elif local == self.block - 1:            # gap between two sheets
            win = th.c('text', 'win')
            return chars, [win] * W
        else:
            chars[0] = chars[W - 1] = '|'
            if mv < local <= mv + rows:
                gy = k * rows + (local - 1 - mv)
                if gy < len(lay.rows):
                    text, tat = lay.rows[gy]
                    x = 1 + self.p_margin
                    n = max(0, min(len(text), W - 2 - 2 * self.p_margin))
                    chars[x:x + n] = list(text[:n])
                    attrs[x:x + n] = tat[:n]
        for x in range(W):
            if chars[x] in '+|':
                attrs[x] = edge
        return chars, attrs

    def ensure_visible(self):
        lay = self.layout()
        _x, y, h = lay.caret(*self.cur)
        s0, s1 = self.y_scr(y), self.y_scr(y + max(0, h - 1))
        if s0 < self.top:
            self.top = s0
        elif s1 >= self.top + self.view_h:
            self.top = s1 - self.view_h + 1

    def after_edit(self, visible=True):
        self.changed()
        if visible:
            self.ensure_visible()
        self.update_title()

    # ================================================================ movement
    def move_v(self, d):
        lay = self.layout()
        pi, i = self.cur
        if self.want_x is None:
            self.want_x = lay.caret(pi, i)[0]
        lines = lay.all_lines()
        li = lay.line_of(pi, i)
        idx = next((n for n, ln in enumerate(lines) if ln[0] == pi and ln[1] == li), 0)
        nidx = max(0, min(len(lines) - 1, idx + d))
        if nidx == idx:
            if d < 0:
                self.cur = (pi, 0) if pi == 0 and li == 0 else self.cur
            else:
                self.cur = (pi, len(self.doc.paras[pi].text)) if nidx == len(lines) - 1 else self.cur
            return
        npi, nli = lines[nidx][0], lines[nidx][1]
        self.cur = (npi, lay.pos_in_line(npi, nli, self.want_x))

    def move_page(self, d):
        lay = self.layout()
        if self.want_x is None:
            self.want_x = lay.caret(*self.cur)[0]
        total = self.pages * self.block
        step = max(1, self.view_h - 2)
        self.top = max(0, min(max(0, total - self.view_h), self.top + d * step))
        sy = self.top + (0 if d < 0 else self.view_h - 1)
        gy = min(self.y_doc(sy), max(0, lay.height - 1))
        self.cur = lay.hit(self.want_x, gy)

    def move_key(self, base, shift):
        if shift:
            if self.anchor is None:
                self.anchor = self.cur
        else:
            r = self.sel_range()
            if r and base in ('left', 'right'):
                self.cur = r[0] if base == 'left' else r[1]
                self.anchor = None
                self.want_x = None
                return
            self.anchor = None
        pi, i = self.cur
        lay = self.layout()
        if base not in ('up', 'down', 'pgup', 'pgdn'):
            self.want_x = None
        if base == 'left':
            self.cur = (pi, i - 1) if i > 0 else ((pi - 1, len(self.doc.paras[pi - 1].text)) if pi else (0, 0))
        elif base == 'right':
            n = len(self.doc.paras[pi].text)
            self.cur = (pi, i + 1) if i < n else ((pi + 1, 0) if pi + 1 < len(self.doc.paras) else (pi, n))
        elif base == 'up':
            self.move_v(-1)
        elif base == 'down':
            self.move_v(1)
        elif base == 'pgup':
            self.move_page(-1)
        elif base == 'pgdn':
            self.move_page(1)
        elif base in ('home', 'end'):
            li = lay.line_of(pi, i)
            pl = lay.paras[pi]
            a, b, _y, _h = pl.lines[li]
            last = li == len(pl.lines) - 1
            if base == 'home':
                self.cur = (pi, a)
            else:
                self.cur = (pi, b if last else max(a, b - 1))
        elif base == 'ctrl+left':
            self.cur = self.word_left()
        elif base == 'ctrl+right':
            self.cur = self.word_right()
        elif base == 'ctrl+home':
            self.cur = (0, 0)
        elif base == 'ctrl+end':
            self.cur = (len(self.doc.paras) - 1, len(self.doc.paras[-1].text))
        if self.anchor == self.cur:
            self.anchor = None if not shift else self.anchor
        self.type_style = None

    # ================================================================ input
    def on_click(self, key, ev):
        acts = {'new': lambda: self.wm.open('writer'), 'save': self.save, 'docx': self.export_docx,
                'bold': lambda: self.toggle('bold'),
                'underline': lambda: self.cycle_ul(1), 'underline2': lambda: self.cycle_ul(2),
                'size1': lambda: self.cycle_size(1),
                'size2': lambda: self.cycle_size(2), 'size3': lambda: self.cycle_size(3),
                'image': self.insert_image, 'source': self.toggle_source,
                'print': self.print_doc, 'head': self.cycle_heading, 'list': self.toggle_list,
                'focus': self.toggle_focus,
                'fwrap': lambda: self.float_key('w', ev), 'fsmall': lambda: self.float_key('-', ev),
                'fbig': lambda: self.float_key('+', ev), 'fdel': lambda: self.float_key('delete', ev)}
        if self.source and key in ('bold', 'underline', 'underline2', 'size1', 'size2', 'size3',
                                   'head', 'list', 'image'):
            self.wm.toast('Formatting works on the page (F5)', 'dim')
            return
        if key == 'txt':
            self.export_txt()
            return
        if key in acts:
            acts[key]()
        elif key == 'open':
            def ok(p):
                if self.modified or self.path:
                    self.wm.open('writer', path=p)
                else:
                    self.load(p)
                self.update_title()
            self.wm.pick_file(ok, 'open', parent=self.win)
        elif isinstance(key, tuple) and key[0] == 'ol':
            if self.source:
                self.toggle_source()
            self.cur = (key[1], 0)
            self.anchor = None
            self.sel_float = None
            self.top = max(0, self.y_scr(self.layout().tops[key[1]]) - 2)
        self.update_title()
        self.invalidate()

    def toggle_focus(self):
        self.focus_mode = not self.focus_mode
        if self.focus_mode and not self.win.snap:
            self.wm.toggle_max(self.win)

    def doc_xy(self, ev):
        """Mouse event -> (x on the page, row on the screen across all sheets)."""
        return ev.x - self.text_x, ev.y - self.text_y + self.top

    def total_rows(self):
        return self.pages * self.block

    def on_mouse(self, ev):
        if self.source:
            return self.source_mouse(ev)
        k = self.zone_at(ev.x, ev.y)
        lay = self.layout()
        if ev.kind in ('wheelup', 'wheeldown') and k in ('text', 'sbar'):
            self.top = max(0, min(max(0, self.total_rows() - self.view_h),
                                  self.top + (-3 if ev.kind == 'wheelup' else 3)))
            self.invalidate()
            return True
        g = self.sb
        if g and ev.button == 0 and (
                (ev.kind == 'down' and k == 'sbar') or (ev.kind == 'drag' and self.sb_grab is not None)):
            if ev.kind == 'down':
                rel = ev.y - g.y
                if g.ty <= rel < g.ty + g.th:
                    self.sb_grab = rel - g.ty          # grab the thumb where it was hit
                else:
                    self.sb_grab = g.th // 2           # click in the groove: jump there
                    self.top = g.top_at(ev.y, self.sb_grab)
            else:
                self.top = g.top_at(ev.y, self.sb_grab)
            self.top = max(0, min(self.top, max(0, self.total_rows() - self.view_h)))
            self.invalidate()
            return True
        if ev.kind == 'up' and self.sb_grab is not None:
            self.sb_grab = None
            self.invalidate()
            return True
        dx, sy = self.doc_xy(ev)
        dy = self.y_doc(sy)
        if ev.kind == 'down' and ev.button == 0 and k == 'text':
            self.track_hover(ev)
            fr = self.float_rect(self.sel_float) if self.sel_float is not None else None
            if fr and (dx, dy) in ((fr[0] + fr[2], fr[1] + fr[3]), (fr[0] + fr[2] - 1, fr[1] + fr[3] - 1)):
                self.drag = ('size', self.sel_float, self.doc.copy(), self.cur, fr)
                self.pressed = 'text'
                return True
            hit = lay.float_at(dx, dy)
            if hit:
                f, fx, fy, fw, fh = hit
                self.sel_float = f
                self.anchor = None
                self.drag = ('move', f, self.doc.copy(), self.cur, (dx - fx, dy - fy))
                self.pressed = 'text'
                self.invalidate()
                return True
            self.sel_float = None
            pos = lay.hit(dx, dy)
            if 'shift' in ev.mods:
                if self.anchor is None:
                    self.anchor = self.cur
            else:
                self.anchor = pos
            self.cur = pos
            self.want_x = None
            self.type_style = None
            if ev.clicks >= 2:
                t = self.doc.paras[pos[0]].text
                for m in WORD.finditer(t):
                    if m.start() <= pos[1] <= m.end():
                        self.anchor = (pos[0], m.start())
                        self.cur = (pos[0], m.end())
                        break
                if ev.clicks >= 3:
                    self.anchor, self.cur = (pos[0], 0), (pos[0], len(t))
            self.pressed = 'text'
            self.invalidate()
            return True
        if ev.kind == 'drag' and self.drag is not None:
            mode, f, before, cur, info = self.drag
            if mode == 'move':
                gx, gy = info
                self.move_float(f, dx - gx, dy - gy)
            else:
                self.resize_float(f, dx - info[0])
            if sy < self.top:
                self.top = max(0, self.top - 1)
            elif sy >= self.top + self.view_h:
                self.top = min(max(0, self.total_rows() - self.view_h), self.top + 1)
            self.update_title()
            return True
        if ev.kind == 'drag' and self.pressed == 'text' and ev.button == 0:
            if sy < self.top:
                self.top = max(0, self.top - 1)
            elif sy >= self.top + self.view_h:
                self.top = min(max(0, self.total_rows() - self.view_h), self.top + 1)
            gy = min(self.y_doc(self.top + max(0, min(self.view_h - 1, sy - self.top))),
                     max(0, lay.height - 1))
            pos = lay.hit(dx, gy)
            if self.anchor is None:
                self.anchor = self.cur
            self.cur = pos
            self.invalidate()
            return True
        if ev.kind == 'up':
            if self.drag is not None:
                mode, f, before, cur, info = self.drag
                if self._float_state(before) != self._float_state(self.doc):
                    self.undo_stack.append((before, cur, None))
                    self.redo_stack = []
                    self.last_kind = None
                self.drag = None
            self.pressed = None
        return super().on_mouse(ev)

    def source_mouse(self, ev):
        if ev.kind == 'up' and self.ta.sb_grab is not None:
            self.ta.mouse(ev, ev.x - self.text_x, ev.y - self.text_y)
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if k == 'text' or (ev.kind == 'drag' and self.pressed == 'text') or ev.kind in ('wheelup', 'wheeldown'):
            if ev.kind == 'down':
                self.pressed = 'text'
            if self.ta.mouse(ev, ev.x - self.text_x, ev.y - self.text_y):
                self.invalidate()
                return True
        if ev.kind == 'up':
            self.pressed = None
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 'f5':
            self.toggle_source()
            return True
        if k == 'ctrl+s':
            self.save()
            return True
        if k == 'ctrl+p':
            self.print_doc()
            return True
        if k == 'ctrl+e':
            self.export_docx()
            return True
        if k == 'ctrl+t':
            self.toggle_focus()
            self.invalidate()
            return True
        if self.source:
            return self.source_key(ev)
        if self.sel_float is not None:
            res = self.float_key(k, ev)
            if res is not None:
                return res
        shift = 'shift+' in k
        base = k.replace('shift+', '')
        if ev.char and ev.char not in '\x7f' and len(ev.char) == 1 and ev.char.isprintable():
            if ev.char == ' ' and not self.sel_range() and self.autoformat_space():
                self.after_edit()
                return True
            self.snapshot('type')
            self.delete_sel()
            self.insert_chars(ev.char)
            self.want_x = None
            self.after_edit()
            return True
        if base in ('left', 'right', 'up', 'down', 'home', 'end', 'pgup', 'pgdn',
                    'ctrl+left', 'ctrl+right', 'ctrl+home', 'ctrl+end'):
            self.move_key(base, shift)
            self.ensure_visible()
            self.invalidate()
            return True
        if k == 'enter':
            self.enter()
        elif k == 'backspace':
            self.backspace()
        elif k == 'delete':
            self.delete()
        elif k in ('ctrl+w', 'ctrl+backspace', 'alt+backspace'):
            self.snapshot()
            if not self.delete_sel():
                self.delete_range(self.word_left(), self.cur)
        elif k == 'tab':
            self.indent()
            return True
        elif k == 'shift+tab':
            self.indent(back=True)
            return True
        elif k == 'ctrl+z':
            self.undo()
        elif k in ('ctrl+y', 'ctrl+shift+z'):
            self.redo()
        elif k == 'ctrl+a':
            self.anchor = (0, 0)
            self.cur = (len(self.doc.paras) - 1, len(self.doc.paras[-1].text))
            self.invalidate()
            return True
        elif k == 'ctrl+b':
            self.toggle('bold')
            return True
        elif k == 'ctrl+u':
            self.cycle_ul()
            return True
        elif k in ('ctrl+shift+u', 'alt+u'):
            self.cycle_ul(2)
            return True
        elif k == 'ctrl+g':
            self.cycle_size()
            return True
        elif k == 'ctrl+l':
            self.toggle_list()
            return True
        elif k in ('ctrl+c', 'ctrl+insert'):
            self.copy_sel()
            return True
        elif k in ('ctrl+x', 'shift+delete'):
            if self.copy_sel():
                self.snapshot()
                self.delete_sel()
            else:
                return True
        elif k in ('ctrl+v', 'shift+insert'):
            if self.wm.clipboard:
                self.snapshot()
                self.insert_text(self.wm.clipboard, self.rich_clip)
            else:
                return True
        elif k == 'esc':
            if self.anchor is not None:
                self.anchor = None
            elif self.focus_mode:
                self.focus_mode = False
            self.invalidate()
            return True
        else:
            return False
        self.want_x = None
        self.type_style = self.type_style if k == 'enter' else None
        self.after_edit()
        return True

    def source_key(self, ev):
        k = ev.key
        if k == 'ctrl+c':
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
        elif k == 'esc' and self.focus_mode:
            self.focus_mode = False
        elif self.ta.key(ev):
            self.ta.ensure_visible()
        else:
            return False
        self.update_title()
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.source:
            self.ta.insert(text, 'other')
            self.ta.ensure_visible()
        else:
            self.snapshot()
            self.sel_float = None
            self.insert_text(text, self.rich_clip)
            self.after_edit()
        self.update_title()
        self.invalidate()


APP = Writer
