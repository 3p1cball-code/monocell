"""Writer: WYSIWYG word processor on the cell grid, made for a dot-matrix printer.

What you see is the sheet the IBM Proprinter X24E prints: page size, pitch and
margins come from the document (File > Page setup), large text is set with
FIGlet fonts chosen per document (Format > Fonts), pictures float with text
wrap and print either as characters or as 24-pin bit images. F6 shows the
paper in dots (framebuffer), Ctrl+P prints. Menus: F10 or Alt+F/E/O/I/V.

Stored as the Writer Markdown dialect (see richdoc). .docx/.odt import,
.docx/.txt export.
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
from ..term import Key
from ..util import log as util_log
from ..widgets import button, sb_draw, sb_geom, Menu, MenuItem, TextField
from .. import figlet, imaging, printer, richdoc, writer_doc

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
    tbl = []                       # rows of the table being collected

    def flush_table():
        if not tbl:
            return
        ncol = max(len(r) for r in tbl)
        out = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>']
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            out.append('<w:%s w:val="single" w:sz="4" w:space="0" w:color="000000"/>' % side)
        out.append('</w:tblBorders></w:tblPr>')
        for cells in tbl:
            out.append('<w:tr>')
            for c in range(ncol):
                xml = cells[c] if c < len(cells) else ''
                out.append('<w:tc><w:p>%s</w:p></w:tc>' % xml)
            out.append('</w:tr>')
        out.append('</w:tbl>')
        paras.append(''.join(out))
        del tbl[:]

    for p in doc.paras:
        if p.kind == 'tsep':
            continue
        if p.kind == 'table':
            cells, k = [], 0
            for part in p.text.split('|'):
                q = richdoc.Para('p', part, p.st[k:k + len(part)])
                cells.append(_docx_runs(q))
                k += len(part) + 1
            tbl.append(cells)
            continue
        flush_table()
        if p.kind == 'pagebreak':
            paras.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            continue
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
        jc = {'center': 'center', 'right': 'right', 'justify': 'both'}.get(p.align or '')
        if not p.text and style is None and not jc:
            paras.append('<w:p/>')
            continue
        props = ('<w:pStyle w:val="%s"/>' % style if style else '') + \
            ('<w:jc w:val="%s"/>' % jc if jc else '')
        ppr = '<w:pPr>%s</w:pPr>' % props if props else ''
        paras.append('<w:p>%s%s</w:p>' % (ppr, _docx_runs(p)))
    flush_table()
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

# paragraph styles of the Format bar: (label, kind, level / list marker)
STYLES = [('Text', 'p', 0), ('Heading 1', 'h', 1), ('Heading 2', 'h', 2), ('Heading 3', 'h', 3),
          ('Quote', 'quote', 0), ('Bullet list', 'li', '-'), ('Numbered list', 'li', '1.'),
          ('Code', 'code', 0)]
SIZES = [(1, 'Normal'), (2, 'Large'), (3, 'Huge')]
ALIGN_NAMES = [('', 'Left', 'L'), ('center', 'Center', 'C'), ('right', 'Right', 'R'),
               ('justify', 'Justify', 'J')]
MENUS = [('File', 'alt+f'), ('Edit', 'alt+e'), ('Format', 'alt+o'), ('Insert', 'alt+i'),
         ('View', 'alt+v')]

TEMPLATES = {
    'letter': ('letter.md', '''<!-- writer: footer="" -->
Vorname Name
Strasse 1
12345 Ort

Firma
z. Hd. Frau Muster
Strasse 2
12345 Ort

Ort, den {date} {.right}

**Betreff: Ihr Schreiben vom ...**

Sehr geehrte Damen und Herren,

hier steht der Text des Briefes. Absaetze im Blocksatz lesen sich auf dem Nadeldrucker ruhig und sauber, deshalb steht dieser hier im Blocksatz. {.justify}

Mit freundlichen Gruessen



Vorname Name
'''),
    'flyer': ('flyer.md', '''<!-- writer: font3=block3d footer="" -->
# Einladung {.center}

## Zum Sommerfest {.center}

---

Am **Samstag, 1. August**, ab 15 Uhr im Hof. {.center}

| Was | Wann |
|---|---:|
| Kaffee und Kuchen | 15:00 |
| Grill | 18:00 |
| Musik | 20:00 |

Bitte bringt gute Laune mit. {.center}
'''),
    'notes': ('notes.md', '''# Notizen

{date}

- erster Punkt
- zweiter Punkt
'''),
}


def parse_pages(spec, total):
    out = []
    for part in (spec or '').replace(' ', '').split(','):
        if not part:
            continue
        m = re.fullmatch(r'(\d+)(?:-(\d*))?', part)
        if not m:
            return []
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else (total if part.endswith('-') else a)
        out.extend(range(max(1, a), min(total, b) + 1))
    return sorted(set(out))


def today():
    return time.strftime('%d.%m.%Y')


class BarMenu(Menu):
    """A menu of the Writer's menu bar: Left/Right open the neighbouring menu."""

    def __init__(self, wm, items, x, y, owner, index):
        super().__init__(wm, items, x, y)
        self.owner = owner
        self.index = index

    def on_key(self, ev):
        if ev.key in ('left', 'right'):
            self.wm.close_popup(self)
            self.owner.open_menu(self.index + (1 if ev.key == 'right' else -1))
            return True
        if ev.key in ('f10',):
            self.wm.close_popup(self)
            return True
        return super().on_key(ev)



class Form:
    """A small dialog drawn over the page: choices, text fields and buttons.

    rows: dicts with key, label, kind ('choice' | 'text' | 'info'), values,
    labels (choice), field (text). on_button(name) is called for buttons; the
    first button is the default (Enter)."""

    def __init__(self, kind, title, rows, buttons, on_button, extra=None, extra_h=0, width=66):
        self.kind = kind
        self.title = title
        self.rows = rows
        self.buttons = buttons
        self.on_button = on_button
        self.extra = extra
        self.extra_h = extra_h
        self.width = width
        self.sel = next((i for i, r in enumerate(rows) if r['kind'] != 'info'), len(rows))
        self.rect = (0, 0, 0, 0)

    # ------------------------------------------------------------ values
    def row(self, key):
        for r in self.rows:
            if r.get('key') == key:
                return r
        return None

    def get(self, key):
        r = self.row(key)
        if r is None:
            return None
        if r['kind'] == 'text':
            return r['field'].text
        return r.get('value')

    def items(self):
        """Focusable things in order: row indices, then ('btn', name)."""
        out = [i for i, r in enumerate(self.rows) if r['kind'] != 'info']
        return out + [('btn', b) for b in self.buttons]

    def focus(self):
        it = self.items()
        return it[self.sel] if 0 <= self.sel < len(it) else None

    def move(self, d):
        n = len(self.items())
        if n:
            self.sel = (self.sel + d) % n

    def change(self, i, d):
        r = self.rows[i]
        if r['kind'] != 'choice':
            return
        vals = r['values']
        k = vals.index(r['value']) if r['value'] in vals else 0
        r['value'] = vals[(k + d) % len(vals)]
        if r.get('on_change'):
            r['on_change'](r['value'])

    # ------------------------------------------------------------ input
    def key(self, ev):
        k = ev.key
        f = self.focus()
        if k == 'esc':
            self.on_button(self.buttons[-1])
            return True
        if k in ('tab', 'down'):
            self.move(1)
            return True
        if k in ('shift+tab', 'up'):
            self.move(-1)
            return True
        if k == 'enter':
            if isinstance(f, tuple):
                self.on_button(f[1])
            elif f is not None and self.rows[f]['kind'] == 'choice' and self.rows[f].get('enter_next'):
                self.change(f, 1)
            else:
                self.on_button(self.buttons[0])
            return True
        if isinstance(f, tuple):
            if k in ('left', 'right'):
                bi = self.buttons.index(f[1]) + (1 if k == 'right' else -1)
                if 0 <= bi < len(self.buttons):
                    self.sel = len(self.items()) - len(self.buttons) + bi
            return True
        if f is None:
            return True
        r = self.rows[f]
        if r['kind'] == 'choice':
            if k in ('left', 'right', ' ', 'space'):
                self.change(f, -1 if k == 'left' else 1)
            elif ev.char and ev.char.isalnum():       # jump to a value by its first letter
                labels = r.get('labels') or [str(v) for v in r['values']]
                for j in range(1, len(labels) + 1):
                    n = (r['values'].index(r['value']) + j) % len(labels) \
                        if r['value'] in r['values'] else j - 1
                    if labels[n].lower().startswith(ev.char.lower()):
                        r['value'] = r['values'][n]
                        if r.get('on_change'):
                            r['on_change'](r['value'])
                        break
            return True
        if r['kind'] == 'text':
            r['field'].key(ev)
            if r.get('on_change'):
                r['on_change'](r['field'].text)
            return True
        return True

    def click(self, zone, ev):
        kind = zone[0]
        if kind == 'fbtn':
            self.on_button(zone[1])
            return
        i = zone[1]
        its = self.items()
        if i in its:
            self.sel = its.index(i)
        r = self.rows[i]
        if kind == 'form<':
            self.change(i, -1)
        elif kind in ('form>', 'formv'):
            self.change(i, 1)
        elif kind == 'formt' and r['kind'] == 'text':
            r['field'].click(max(0, ev.x - zone[2]))

    def paste(self, text):
        f = self.focus()
        if isinstance(f, int) and self.rows[f]['kind'] == 'text':
            r = self.rows[f]
            r['field'].insert(text.replace('\n', ' '))
            if r.get('on_change'):
                r['on_change'](r['field'].text)

    # ------------------------------------------------------------ drawing
    def draw(self, app, cv, x0, y0, w0, h0):
        th = app.th
        W = min(self.width, w0 - 2)
        H = min(h0 - 1, len(self.rows) + 5 + self.extra_h)
        x = x0 + max(0, (w0 - W) // 2)
        y = y0 + 1
        self.rect = (x, y, W, H)
        bg = th.c('text', 'raised')
        cv.fill(x, y, W, H, bg)
        edge = th.c('dim', 'raised')
        cv.put(x, y, '+' + '-' * (W - 2) + '+', edge)
        cv.put(x, y + H - 1, '+' + '-' * (W - 2) + '+', edge)
        for yy in range(y + 1, y + H - 1):
            cv.putc(x, yy, '|', edge)
            cv.putc(x + W - 1, yy, '|', edge)
        cv.put(x + 2, y, ' %s ' % self.title, th.c('bright', 'raised', 1))
        lw = min(26, max(len(r['label']) for r in self.rows) + 2) if self.rows else 10
        foc = self.focus()
        yy = y + 2
        for i, r in enumerate(self.rows):
            if yy >= y + H - 3:
                break
            on = foc == i
            la = th.c('accent', 'raised', 1) if on else th.c('text', 'raised')
            if r['kind'] == 'info':
                cv.put(x + 2, yy, fit(r['label'], W - 4), th.c('dim', 'raised'))
                yy += 1
                continue
            cv.put(x + 2, yy, fit(r['label'], lw), la)
            vx = x + 2 + lw
            vw = W - 4 - lw
            if r['kind'] == 'choice':
                labels = r.get('labels') or [str(v) for v in r['values']]
                k = r['values'].index(r['value']) if r['value'] in r['values'] else 0
                text = fit(labels[k], max(4, vw - 4))
                va = th.c('on_accent', 'accent') if on else th.c('bright', 'field')
                cv.put(vx, yy, '<', th.c('accent', 'raised', 1))
                cv.put(vx + 1, yy, ' %s ' % text, va)
                cv.put(vx + 3 + len(text), yy, '>', th.c('accent', 'raised', 1))
                app.zone(vx, yy, 1, 1, ('form<', i))
                app.zone(vx + 1, yy, len(text) + 2, 1, ('formv', i))
                app.zone(vx + 3 + len(text), yy, 1, 1, ('form>', i))
            elif r['kind'] == 'text':
                r['field'].draw(cv, vx, yy, vw, th, on)
                app.zone(vx, yy, vw, 1, ('formt', i, vx))
            app.zone(x + 1, yy, lw, 1, ('formv', i) if r['kind'] == 'choice' else ('formt', i, vx))
            yy += 1
        if self.extra:
            ey = yy + 1
            eh = y + H - 3 - ey
            if eh > 0:
                self.extra(cv, x + 2, ey, W - 4, eh)
        bx = x + 2
        by = y + H - 2
        for b in self.buttons:
            active = foc == ('btn', b)
            n = len(b) + 2
            a = th.c('on_accent', 'accent') if active else th.c('bright', 'field')
            cv.put(bx, by, ' %s ' % b, a)
            app.zone(bx, by, n, 1, ('fbtn', b))
            bx += n + 2


class Writer(App):
    """WYSIWYG word processor: what you edit is what is printed."""
    name = 'writer'
    title = 'Writer'
    icon = '[W]'
    size = (88, 30)
    min_size = (40, 10)
    shift_select = True

    def __init__(self, wm, win, path=None, template=None, **kw):
        super().__init__(wm, win)
        self.ta = TextArea(wrap=True)          # source view (F5)
        self.ta.scrollbar = True
        self.ta.style = self.style
        self.path = None
        self.focus_mode = False
        self.show_outline = True
        self.show_ruler = True
        self.source = False
        self.doc = richdoc.Doc()
        self.pg = self.doc.page()
        self.cur = (0, 0)
        self.anchor = None
        self.want_x = None
        self.type_style = None
        self.sel_float = None
        self.drag = None
        self.top = 0
        self.hx = 0
        self.view_h = 1
        self.colw = 40
        self.block = 69
        self.pages = 1
        self.page_x = 0
        self.side = 0
        self.avail = 80
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
        self.form = None                       # open dialog (Form)
        self.find = None                       # find/replace bar
        self.menu_x = []
        self.gfx = None                        # graphics print preview (fbmode.Session)
        self.printing = False
        self.images = richdoc.ImageStore(runner=self._run_bg, on_ready=self._image_ready)
        if path:
            self.load(path)
        elif template:
            self.new_from_template(template, replace=True)
        else:
            self.set_doc(richdoc.parse('# Untitled\n\nStart writing. **Bold**, '
                                       '<u>underlined</u>, <size2>big</size2>, pictures with '
                                       'Insert > Picture. F10 opens the menu.\n'))
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
        self.hx = 0
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
        cfg = self.wm.cfg
        pg = self.pg = self.doc.page()
        key = (self.ver, pg.key, self.img_ver, cfg.get('image_mode'), cfg.get('dither'),
               cfg.get('ascii_charset'), round(imaging.cell_aspect(self.wm), 3),
               richdoc.setting(self.doc.settings, 'font2', cfg),
               richdoc.setting(self.doc.settings, 'font3', cfg), tuple(self.th.rgb), id(self.th),
               getattr(getattr(self.wm, 'term', None), 'mode', None), self.base_dir)
        if self.lay is None or self.lay_key != key:
            self.lay = richdoc.Layout(self.wm, pg.text_w, self.base_dir, self.images, mono=True,
                                      page=pg.rows, bg_role='win_on',
                                      settings=self.doc.settings).run(self.doc)
            self.lay_key = key
        self.colw = pg.text_w
        return self.lay

    @property
    def rows_per_page(self):
        return self.pg.rows

    def doc_title(self):
        for p in self.doc.paras:
            if p.kind == 'h' and p.text.strip():
                return p.text.strip()
        return os.path.splitext(os.path.basename(self.path))[0] if self.path else 'Untitled'

    # ================================================================ source view
    def style(self, i, line):
        th = self.th
        if line.startswith('<!--'):
            return [(0, len(line), th.c('dim', 'win'))]
        if line.startswith('#'):
            m = re.match(r'(#+)\s?', line)
            return [(0, m.end(), th.c('dim', 'win')), (m.end(), len(line), th.c('accent', 'win', 1))]
        out = []
        m = re.match(r'(\s*)([-*+]|\d+[.)])\s', line)
        if m:
            out.append((m.start(2), m.end(2), th.c('accent', 'win')))
        if line.startswith('>') or line.lstrip().startswith('|'):
            out.append((0, len(line), th.c('dim', 'win')))
        for m in INLINE.finditer(line):
            if m.group(1):
                out += [(m.start(), m.start() + 2, th.c('sel', 'win')),
                        (m.start() + 2, m.end() - 2, th.c('bright', 'win', 1)),
                        (m.end() - 2, m.end(), th.c('sel', 'win'))]
            elif m.group(3):
                out.append((m.start(), m.end(), th.c('cyan', 'win')))
            elif m.group(4):
                out.append((m.start(), m.end(), th.c('blue', 'win')))
        for m in TAG_RE.finditer(line):
            out.append((m.start(), m.end(), th.c('magenta', 'win')))
        m = richdoc.ALIGN_RE.search(line)
        if m:
            out.append((m.start(), m.end(), th.c('magenta', 'win')))
        if writer_doc.IMAGE_RE.match(line) or richdoc.PAGEBREAK_RE.match(line):
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
            self.ta.cy = min(self.cur[0] + (1 if self.doc.settings else 0), len(self.ta.lines) - 1)
            self.ta.cx = 0
            self.ta.modified = False
            self.source = True
        self.form = None
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

    def new_from_template(self, name, replace=False):
        fname, text = TEMPLATES[name]
        text = text.replace('{date}', today())
        if not replace and (self.modified or self.path):
            self.wm.open('writer', template=name)
            return
        self.path = None
        self.set_doc(richdoc.parse(text))
        self.modified = False
        self.update_title()

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

    def open_file(self):
        def ok(p):
            if self.modified or self.path:
                self.wm.open('writer', path=p)
            else:
                self.load(p)
            self.update_title()
        self.wm.pick_file(ok, 'open', parent=self.win)

    def export_txt(self):
        name = os.path.splitext(os.path.basename(self.path))[0] + '.txt' if self.path else 'document.txt'

        def ok(p):
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(self.page_text())
                self.wm.toast('Exported ' + os.path.basename(p), 'green')
            except OSError as e:
                self.wm.toast(str(e), 'red')
        self.wm.pick_file(ok, 'save', name=name, parent=self.win)

    def page_text(self):
        """The document as it looks on paper, as plain text: pages split by form feeds."""
        _pg, sheets = self.make_sheets(gfx=False)
        out = []
        for sh in sheets:
            lines = [ch.rstrip() for ch, _a in sh.rows]
            while lines and not lines[-1]:
                lines.pop()
            out.append('\n'.join(lines))
        return '\n\f\n'.join(out) + '\n'

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
        self.leave_gfx()
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

    # ================================================================ printing
    def print_layout(self):
        """A fresh layout for paper: pictures loaded synchronously."""
        doc = richdoc.parse(self.doc_text())
        pg = doc.page()
        lay = richdoc.Layout(self.wm, pg.text_w, self.base_dir, self.images, mono=True,
                             page=pg.rows, bg_role='win', sync=True,
                             settings=doc.settings).run(doc)
        return doc, pg, lay

    def make_sheets(self, pick=None, gfx=True):
        doc, pg, lay = self.print_layout()
        sheets = richdoc.sheets(lay, pg, self.doc_title(), today(), pick)
        if not gfx:
            for sh in sheets:
                sh.gfx = []
        return pg, sheets

    def raster(self, pg):
        # the printed page uses a fine font, whatever the screen shows: a 6x12
        # console font would print as blocks
        name = self.wm.cfg.get('print_font') or 'Lat15-Fixed16.psf.gz'
        try:
            from .. import printraster
            return printraster.Raster(pg, font_name=name)
        except Exception as e:                   # noqa: BLE001 - PIL missing etc.
            util_log('writer: no raster for bit images: %s' % e)
            return None

    def print_doc(self, pick=None, copies=1, quality=None, target=None, test=False):
        cfg = self.wm.cfg
        quality = quality or cfg.get('printer_quality', 'draft')
        target = (cfg.get('printer_device') or '').strip() if target is None else target
        pictures = cfg.get('printer_pictures', 'gfx')
        if self.printing:
            self.wm.toast('Still printing...', 'dim')
            return

        def work():
            if test:
                pg = self.doc.page()
                sheets = [printer.test_sheet(pg)]
            else:
                pg, sheets = self.make_sheets(pick)
            if pictures == 'text':
                for sh in sheets:
                    sh.gfx = []
            ras = self.raster(pg) if any(sh.gfx for sh in sheets) else None
            data = printer.encode_sheets(sheets, pg, quality, ras.dot_rows if ras else None,
                                         copies)
            if target:
                printer.send(data, target)
            return data, len(sheets)

        def done(res):
            self.printing = False
            data, n = res
            if target:
                self.wm.toast('Printed %d page%s (%d kB) on %s' % (n, '' if n == 1 else 's',
                                                                   len(data) // 1024 + 1, target),
                              'green')
                return
            name = os.path.splitext(os.path.basename(self.path))[0] + '.prn' if self.path \
                else 'document.prn'

            def ok(path):
                try:
                    with open(path, 'wb') as f:
                        f.write(data)
                    self.wm.toast('Print file saved: %s (%d bytes)' % (os.path.basename(path),
                                                                       len(data)), 'green')
                except OSError as e:
                    self.wm.toast(str(e), 'red')
            self.wm.pick_file(ok, 'save', name=name, parent=self.win, title='Save printer file')

        def fail(e):
            self.printing = False
            self.wm.toast('Print failed: %s' % e, 'red')

        self.printing = True
        self.wm.toast('Printing...', 'accent')
        self.thread(work, done, fail)
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
            if P0.kind in ('rule', 'pagebreak', 'tsep'):
                P0.kind, P0.level, P0.indent, P0.marker = P1.kind, P1.level, P1.indent, P1.marker
                P0.align = P1.align
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
        if p.kind in ('rule', 'pagebreak'):
            p.kind = 'p'
        elif p.kind == 'tsep':
            self.wm.toast('This line is the header rule of the table', 'dim')
            return
        style = style or self.style_here()
        if p.kind == 'table':
            style = style._replace(size=1, ul=0)
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
        elif p.kind in ('rule', 'pagebreak', 'tsep'):
            q.kind, q.marker = 'p', '-'
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
        if p.kind == 'table' and not p.text.replace('|', '').strip():
            p.kind = 'p'                          # an empty row ends the table
            p.set('', [])
            return
        if p.kind == 'p' and p.text.startswith('|') and p.text.count('|') >= 2 \
                and i == len(p.text):
            q = richdoc.parse_table_line(richdoc.para_md(p))
            p.kind, p.marker = q.kind, q.marker
            p.set(q.text, q.st)
            self.cur = (pi, len(p.text))
            if p.kind == 'tsep':
                self.doc.paras.insert(pi + 1, richdoc.Para(
                    'table', '|' * (len(p.marker) - 1)))
                self.cur = (pi + 1, 0)
                return
        if p.kind == 'table':
            n = p.text.count('|')
            q = richdoc.Para('table', '|' * n)
            self.doc.paras.insert(pi + 1, q)
            self.cur = (pi + 1, 0)
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
        if p.kind in ('li', 'quote', 'h', 'code', 'rule', 'pagebreak', 'tsep') or p.align:
            if p.align and p.kind not in ('rule', 'pagebreak', 'tsep'):
                p.align = ''
            else:
                p.kind, p.level = 'p', 0
            p.touch()
            return
        if pi == 0:
            return
        prev = self.doc.paras[pi - 1]
        if p.kind == 'table' and prev.kind in ('table', 'tsep'):
            self.cur = (pi - 1, len(prev.text))   # rows are not merged: just move up
            return
        if prev.kind in ('rule', 'pagebreak', 'tsep'):
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
        if p.kind == 'table':
            self.table_tab(back)
            return
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
        elif k in ('g', 'G'):
            self.snapshot()
            f.out = 'text' if f.out == 'gfx' else 'gfx'
            self.wm.toast('Printed as %s' % ('bit image (graphics)' if f.out == 'gfx'
                                              else 'characters (as on screen)'), 'accent')
            self.changed()
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


    # ================================================================ paragraph styles
    def para_style(self, p=None):
        p = p or self.para()
        for n, (label, kind, lv) in enumerate(STYLES):
            if p.kind != kind:
                continue
            if kind == 'h' and p.level != lv:
                continue
            if kind == 'li' and (lv == '1.') != bool(re.match(r'\d', p.marker or '')):
                continue
            return n
        if p.kind == 'h':
            return 3
        return -1

    def style_label(self, p=None):
        p = p or self.para()
        n = self.para_style(p)
        if n >= 0:
            return STYLES[n][0]
        return {'table': 'Table', 'tsep': 'Table', 'pagebreak': 'Page break',
                'rule': 'Line'}.get(p.kind, 'Text')

    def set_style(self, n):
        label, kind, lv = STYLES[n]
        self.snapshot()
        number = 1
        for pi in self.para_range():
            p = self.doc.paras[pi]
            if p.kind in ('rule', 'pagebreak', 'tsep', 'table'):
                continue
            p.kind = kind
            p.level = lv if kind == 'h' else 0
            if kind == 'li':
                p.marker = '%d.' % number if lv == '1.' else '-'
                number += 1
            if kind == 'code':
                p.align = ''
                p.set(p.text, [richdoc.PLAIN] * len(p.text))
            p.touch()
        self.after_edit()

    def set_align(self, align):
        self.snapshot()
        for pi in self.para_range():
            p = self.doc.paras[pi]
            if p.kind in ('rule', 'pagebreak', 'tsep', 'table', 'code'):
                continue
            p.align = align
            p.touch()
        self.after_edit()

    def cycle_align(self):
        order = [a for a, _l, _s in ALIGN_NAMES]
        cur = self.para().align or ''
        self.set_align(order[(order.index(cur) + 1) % len(order)] if cur in order else '')
        label = dict((a, l) for a, l, _s in ALIGN_NAMES)[self.para().align or '']
        self.wm.toast('Align: %s' % label, 'accent')

    # ================================================================ tables
    def cell_bounds(self, pi, i):
        t = self.doc.paras[pi].text
        a = t.rfind('|', 0, i) + 1
        b = t.find('|', i)
        return a, (len(t) if b < 0 else b)

    def table_tab(self, back=False):
        pi, i = self.cur
        p = self.doc.paras[pi]
        a, b = self.cell_bounds(pi, i)
        if not back:
            if b < len(p.text):
                self.cur = (pi, b + 1)
            else:
                nxt = pi + 1
                while nxt < len(self.doc.paras) and self.doc.paras[nxt].kind == 'tsep':
                    nxt += 1
                if nxt < len(self.doc.paras) and self.doc.paras[nxt].kind == 'table':
                    self.cur = (nxt, 0)
                else:                              # last cell: a new row
                    self.snapshot()
                    q = richdoc.Para('table', '|' * p.text.count('|'))
                    self.doc.paras.insert(pi + 1, q)
                    self.cur = (pi + 1, 0)
        else:
            if a > 0:
                a2, _b2 = self.cell_bounds(pi, a - 1)
                self.cur = (pi, a2)
            else:
                prv = pi - 1
                while prv >= 0 and self.doc.paras[prv].kind == 'tsep':
                    prv -= 1
                if prv >= 0 and self.doc.paras[prv].kind == 'table':
                    t = self.doc.paras[prv].text
                    self.cur = (prv, t.rfind('|') + 1)
        # select the cell, like spreadsheets do
        pi, i = self.cur
        a, b = self.cell_bounds(pi, i)
        self.anchor = (pi, a) if b > a else None
        self.cur = (pi, b)
        self.after_edit()

    def insert_table(self, spec=None):
        if spec is None:
            self.wm.prompt('Insert table', 'Columns x rows (header row included):', '3x4',
                           lambda v: self.insert_table(v or '3x4'), parent=self.win)
            return
        m = re.match(r'\s*(\d+)\s*[x*, ]\s*(\d+)', spec or '')
        cols, nrows = (int(m.group(1)), int(m.group(2))) if m else (3, 4)
        cols, nrows = max(1, min(12, cols)), max(1, min(60, nrows))
        self.snapshot()
        pi = self.cur[0]
        head = '|'.join('Spalte %d' % (c + 1) for c in range(cols))
        new = [richdoc.Para('table', head), richdoc.Para('tsep', marker='l' * cols)]
        new += [richdoc.Para('table', '|' * (cols - 1)) for _ in range(max(0, nrows - 1))]
        p = self.doc.paras[pi]
        at = pi + 1
        if not p.text and p.kind == 'p':
            self.doc.paras[pi:pi + 1] = new
            at = pi
        else:
            self.doc.paras[at:at] = new
        self.doc.paras.insert(at + len(new), richdoc.Para())
        self.cur = (at, 0)
        self.anchor = (at, 0)
        self.cur = (at, len('Spalte 1'))
        self.after_edit()

    def insert_block(self, kind):
        """A page break or a horizontal line after the cursor's paragraph."""
        self.snapshot()
        pi, i = self.cur
        p = self.doc.paras[pi]
        if i > 0 and i < len(p.text):
            self.split_para()
            pi = self.cur[0] - 1
        elif i == 0 and p.text:
            pi -= 1
        self.doc.paras.insert(pi + 1, richdoc.Para(kind))
        if pi + 2 >= len(self.doc.paras):
            self.doc.paras.append(richdoc.Para())
        self.cur = (pi + 2, 0)
        self.anchor = None
        self.after_edit()

    def insert_date(self):
        self.snapshot('type')
        self.delete_sel()
        self.insert_chars(today())
        self.after_edit()

    # ================================================================ geometry
    def outline(self):
        return [(i, p) for i, p in enumerate(self.doc.paras) if p.kind == 'h']

    def frame(self, w):
        """Sheet geometry: border + page columns; scrolls sideways when too wide."""
        pg = self.pg
        sw = pg.cols + 2
        want_side = self.show_outline and not self.focus_mode
        side = 24 if want_side and w - 1 - 24 >= sw else 0
        avail = w - side - 1                      # one column for the scrollbar
        self.side, self.avail = side, avail
        self.block = pg.lines + 3
        if avail >= sw:
            self.hx = 0
            self.page_x = side + (avail - sw) // 2
        else:
            self.hx = max(0, min(self.hx, sw - avail))
            self.page_x = side - self.hx
        self.text_x = self.page_x + 1 + pg.left
        return side, self.page_x, pg.text_w, avail

    # --- page geometry: layout row <-> screen row -------------------------
    def y_scr(self, gy):
        rows = self.pg.rows
        k, local = divmod(max(0, gy), rows)
        return k * self.block + 1 + self.pg.top + local

    def y_doc(self, sy):
        rows = self.pg.rows
        k, local = divmod(max(0, sy), self.block)
        top = 1 + self.pg.top
        if local < top:
            return k * rows
        if local < top + rows:
            return k * rows + local - top
        return (k + 1) * rows - 1

    # ================================================================ drawing: bars
    def menu_bar(self, cv, w):
        th = self.th
        cv.fill(0, 0, w, 1, th.c('text', 'raised'))
        x = 1
        self.menu_x = []
        open_i = self.open_menu_index()
        for i, (label, _k) in enumerate(MENUS):
            self.menu_x.append(x)
            on = open_i == i or self.hover == ('menu', i)
            a = th.c('on_accent', 'accent') if on else th.c('text', 'raised')
            cv.put(x, 0, ' %s ' % label, a)
            cv.put(x + 1, 0, label[0], th.c('on_accent', 'accent', 1) if on
                   else th.c('accent', 'raised', 1))
            self.zone(x, 0, len(label) + 2, 1, ('menu', i))
            x += len(label) + 3
        info = '%s  F10 menu' % self.pg.describe()
        if x + len(info) + 2 < w:
            cv.put(w - len(info) - 1, 0, info, th.c('dim', 'raised'))

    def open_menu_index(self):
        for p in getattr(self.wm, 'popups', ()):
            if isinstance(p, BarMenu) and p.owner is self:
                return p.index
        return None

    def format_bar(self, cv, w):
        th = self.th
        y = 1
        x = 0
        if self.source:
            cv.put(1, y, fit('Markdown source - F5 returns to the page. Settings line: '
                             '<!-- writer: ... -->', w - 2), th.c('dim', 'win'))
            return
        if self.sel_float is not None:
            f = self.sel_float
            fr = self.float_rect(f) or (0, 0, 0, 0)
            cv.put(x, y, fit('Picture %s %dx%d' % (os.path.basename(f.path), fr[2], fr[3]), 30),
                   th.c('accent', 'win', 1))
            x = 31
            for label, k, act in (('Wrap: ' + ('around' if f.wrap == 'square' else 'block'),
                                   'fwrap', False),
                                  ('Print: ' + ('graphics' if f.out == 'gfx' else 'ASCII'),
                                   'fout', f.out == 'gfx'),
                                  ('-', 'fsmall', False), ('+', 'fbig', False),
                                  ('Delete', 'fdel', False)):
                if x + len(label) + 3 > w:
                    break
                x += button(self, cv, x, y, label, k, active=act) + 1
            return
        sty = self.style_chars() or [self.style_here()]
        s0 = sty[0] if sty else richdoc.PLAIN
        p = self.para()
        label = '%-13s v' % fit(self.style_label(p), 13)
        self._style_x = x
        x += button(self, cv, x, y, label, 'stylemenu') + 1
        x += 1
        for lab, k, act in (('B', 'bold', s0.bold), ('U', 'underline', s0.ul == 1),
                            ('U2', 'underline2', s0.ul >= 2)):
            x += button(self, cv, x, y, lab, k, active=act) + 1
        size = dict(SIZES).get(s0.size, 'Normal')
        x += 1
        self._size_x = x
        x += button(self, cv, x, y, '%-6s v' % size, 'sizemenu') + 2
        for a, _lab, short in ALIGN_NAMES:
            if x + 4 > w:
                break
            x += button(self, cv, x, y, short, ('align', a), active=(p.align or '') == a) + 1
        x += 1
        for lab, k in (('Table', 'table'), ('Img', 'image'), ('Fonts', 'fonts')):
            if x + len(lab) + 3 > w:
                break
            x += button(self, cv, x, y, lab, k) + 1

    def ruler(self, cv, y, w):
        """Columns of the text area: '[' and ']' at the margins, numbers every ten."""
        th = self.th
        pg = self.pg
        a = th.c('dim', 'win')
        caret_x = None
        if not self.source and self.lay is not None:
            caret_x = self.lay.caret(*self.cur)[0]
        for c in range(pg.cols):
            sx = self.page_x + 1 + c
            if not (self.side <= sx < w - 1):
                continue
            rel = c - pg.left
            if rel == -1:
                ch = '['
            elif rel == pg.text_w:
                ch = ']'
            elif 0 <= rel < pg.text_w:
                ch = str(rel // 10 % 10) if rel % 10 == 0 and rel else ('+' if rel % 5 == 0 else '.')
            else:
                ch = ' '
            at = a
            if caret_x is not None and rel == caret_x:
                at = th.c('on_accent', 'accent')
            cv.put(sx, y, ch, at)

    def status_line(self, cv, w, h, lay):
        th = self.th
        if self.sel_float is not None and not self.source:
            f = self.sel_float
            st = 'drag: move  # corner: size  arrows  +/-  W wrap  G print as %s  Del' % (
                'ASCII' if f.out == 'gfx' else 'graphics')
            cv.put(0, h - 1, fit(st, w), th.c('accent', 'win'))
            return
        if self.source:
            st = 'source  F5 back to the page'
        else:
            if getattr(self, '_count', (None,))[0] != self.ver:
                text = richdoc.plain(self.doc)
                self._count = (self.ver, len(re.findall(r'\w+', text)), len(text))
            cx, cy, _ch = lay.caret(*self.cur)
            page_no = cy // self.pg.rows + 1
            st = 'page %d/%d  line %d  col %d  %d words' % (
                page_no, self.pages, cy % self.pg.rows + 1, cx + 1, self._count[1])
            sw = self.pg.cols + 2
            if self.avail < sw:                 # the sheet is wider than the window
                st = 'cols %d-%d/%d  ' % (self.hx + 1, self.hx + self.avail, sw) + st
        left = shorten_path(self.path, 40) if self.path else 'not saved'
        cv.put(0, h - 1, fit(left, max(0, w - len(st) - 2)), th.c('dim', 'win'))
        cv.put(max(0, w - len(st)), h - 1, st[-w:], th.c('dim', 'win'))

    def find_bar(self, cv, y, w):
        th = self.th
        f = self.find
        cv.fill(0, y, w, 1, th.c('text', 'raised'))
        x = 1
        cv.put(x, y, 'Find', th.c('accent' if f['focus'] == 'q' else 'text', 'raised', 1))
        x += 5
        fw = max(8, min(24, (w - 50) // 2))
        f['q'].draw(cv, x, y, fw, th, f['focus'] == 'q')
        self.zone(x, y, fw, 1, ('findfield', 'q', x))
        x += fw + 2
        cv.put(x, y, 'Replace', th.c('accent' if f['focus'] == 'r' else 'text', 'raised', 1))
        x += 8
        f['r'].draw(cv, x, y, fw, th, f['focus'] == 'r')
        self.zone(x, y, fw, 1, ('findfield', 'r', x))
        x += fw + 2
        for lab, k in (('Next', 'fnext'), ('Replace', 'frepl'), ('All', 'fall'), ('x', 'fclose')):
            if x + len(lab) + 2 > w:
                break
            x += button(self, cv, x, y, lab, k) + 1
        if f.get('note') and x + len(f['note']) + 1 < w:
            cv.put(x + 1, y, f['note'], th.c('dim', 'raised'))

    # ================================================================ drawing: page
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        lay = self.layout() if not self.source else self.lay
        top = 0
        if not self.focus_mode:
            self.menu_bar(cv, w)
            self.format_bar(cv, w)
            top = 2
        side, page_x, colw, avail = self.frame(w)
        if not self.focus_mode and self.show_ruler and not self.source:
            self.ruler(cv, 2, w)
            top = 3
        elif not self.focus_mode:
            top = 3
        bottom = 1 + (1 if self.find else 0)
        if side:
            cv.put(0, top, 'OUTLINE', th.c('dim', 'win'))
            for j, (i, p) in enumerate(self.outline()[:h - top - 3]):
                cur = (not self.source and i == self.cur[0])
                a = th.c('accent', 'raised') if cur else (
                    th.c('bright', 'sel') if self.hover == ('ol', i) else th.c('text', 'win'))
                cv.put(0, top + 1 + j, fit('  ' * (p.level - 1) + p.text.strip(), side - 2), a)
                self.zone(0, top + 1 + j, side - 2, 1, ('ol', i))
        self.text_y = top
        view_h = max(1, h - top - bottom)
        self.view_h = view_h
        if self.find:
            self.find_bar(cv, h - 2, w)
        if self.source:
            tx = max(side, (w - side - 82) // 2 + side)
            self.ta.draw(cv, tx, top, min(82, w - tx - 1), view_h, th, focused)
            self.text_x = tx
            self.status_line(cv, w, h, lay)
            self.zone(side, top, avail, view_h, 'text')
            return
        rows = self.pg.rows
        self.pages = max(1, (lay.height + rows - 1) // rows)
        total = self.pages * self.block
        self.top = max(0, min(self.top, max(0, total - view_h)))
        paper = th.c('text', 'win_on')
        sw = self.pg.cols + 2
        x0 = max(page_x, side)
        clip = x0 - page_x
        self.header_cache = {}
        for i in range(view_h):
            sy = self.top + i
            k, local = divmod(sy, self.block)
            if k >= self.pages:
                break
            chars, attrs = self.page_line(lay, k, local, self.pages, paper)
            n = min(sw - clip, w - 1 - x0)
            if n > 0:
                cv.blit_cells(x0, top + i, [(chars[clip:clip + n], attrs[clip:clip + n])])
        self.zone(x0, top, min(sw, w - 1 - x0), view_h, 'text')
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
                for kk in range(a, b):
                    bx0, bx1, by, bh = pl.boxes[kk]
                    sy = self.y_scr(pt + by)
                    if sy + bh < self.top or sy > self.top + view_h:
                        continue
                    self.recolor_clip(cv, self.text_x + bx0, top + sy - self.top, max(1, bx1 - bx0),
                                      bh, sel_a, top, view_h, w)
                if pi < p1:
                    ex, ey, eh = pl.end
                    self.recolor_clip(cv, self.text_x + ex, top + self.y_scr(pt + ey) - self.top,
                                      1, 1, sel_a, top, view_h, w)
        # cursor
        if focused and self.sel_float is None and not self.form and \
                not (self.find and self.find['focus']):
            cx, cy, ch = lay.caret(*self.cur)
            cx = min(cx, colw - 1)
            ca = th.c('on_accent', 'accent')
            for yy in range(cy, cy + ch):
                ry = self.y_scr(yy) - self.top
                sx = self.text_x + cx
                if 0 <= ry < view_h and side <= sx < w - 1:
                    c = lay.rows[yy][0][cx] if yy < len(lay.rows) else ' '
                    cv.put(sx, top + ry, c, ca)
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
        gx = w - 1
        self.sb = sb_geom(gx, top, view_h, total, view_h, self.top)
        sb_draw(cv, self.sb, th, 'win', active=self.sb_grab is not None)
        if self.sb:
            self.zone(gx, top, 1, view_h, 'sbar')
        self.status_line(cv, w, h, lay)
        if self.form:
            self.form.draw(self, cv, side, top, w - side - 1, view_h)

    def recolor_clip(self, cv, x, y, w, h, a, top, view_h, cw):
        x0, x1 = max(x, self.side), min(x + w, cw - 1)
        y0, y1 = max(y, top), min(y + h, top + view_h)
        if x1 > x0 and y1 > y0:
            cv.recolor(x0, y0, x1 - x0, y1 - y0, lambda at, s=a: s)

    def margin_text(self, k, pages):
        hit = self.header_cache.get(k) if hasattr(self, 'header_cache') else None
        if hit is None:
            title = self.doc_title()
            hit = (self.pg.margin_line('header', k + 1, pages, title, today()),
                   self.pg.margin_line('footer', k + 1, pages, title, today()))
            self.header_cache[k] = hit
        return hit

    def page_line(self, lay, k, local, pages, paper):
        """One screen row of sheet k: border, margins, header/footer, text, gap."""
        th = self.th
        pg = self.pg
        W = pg.cols + 2
        edge = th.c('dim', 'win_on')
        chars = [' '] * W
        attrs = [paper] * W
        if local == 0 or local == pg.lines + 1:
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
            pr = local - 1
            x = 1 + pg.left
            if pg.top <= pr < pg.top + pg.rows:
                gy = k * pg.rows + (pr - pg.top)
                if gy < len(lay.rows):
                    text, tat = lay.rows[gy]
                    n = max(0, min(len(text), pg.text_w))
                    chars[x:x + n] = list(text[:n])
                    attrs[x:x + n] = tat[:n]
            else:
                head, foot = self.margin_text(k, pages)
                t = None
                if pr == max(0, pg.top - 2) and pg.top >= 1:
                    t = head
                elif pr == min(pg.lines - 1, pg.lines - pg.bottom + 1) and pg.bottom >= 1:
                    t = foot
                if t:
                    da = th.c('dim', 'win_on')
                    for j, c in enumerate(t[:pg.text_w]):
                        if c != ' ':
                            chars[x + j] = c
                            attrs[x + j] = da
        for x in (0, W - 1):
            attrs[x] = edge
        if chars[0] == '+':
            for x in range(W):
                attrs[x] = edge
        return chars, attrs

    def ensure_visible(self):
        lay = self.layout()
        cx, y, h = lay.caret(*self.cur)
        s0, s1 = self.y_scr(y), self.y_scr(y + max(0, h - 1))
        if s0 < self.top:
            self.top = s0
        elif s1 >= self.top + self.view_h:
            self.top = s1 - self.view_h + 1
        sw = self.pg.cols + 2
        if self.avail < sw:                       # sideways: keep the caret on screen
            col = 1 + self.pg.left + cx            # column on the sheet
            if col - self.hx < 2:
                self.hx = max(0, col - 4)
            elif col - self.hx > self.avail - 3:
                self.hx = min(sw - self.avail, col - self.avail + 6)

    def after_edit(self, visible=True):
        self.changed()
        if visible:
            self.ensure_visible()
        self.update_title()

    # ================================================================ menus
    def abs_xy(self, x, y):
        b = 0 if getattr(self.win, 'borderless', False) else 1
        return getattr(self.win, 'x', 0) + b + x, getattr(self.win, 'y', 0) + b + y

    def open_menu(self, i):
        i %= len(MENUS)
        if self.focus_mode:
            self.focus_mode = False
            self.invalidate()
        if self.source and MENUS[i][0] == 'Format':
            pass
        items = getattr(self, 'menu_' + MENUS[i][0].lower())()
        x = self.menu_x[i] if i < len(self.menu_x) else 1 + i * 8
        ax, ay = self.abs_xy(x, 1)
        if hasattr(self.wm, 'popup'):
            self.wm.close_popup()
            self.wm.popup(BarMenu(self.wm, items, ax, ay, self, i))
        self.invalidate()

    def menu_file(self):
        M = MenuItem
        return [M('New', lambda: self.wm.open('writer'), 'Ctrl+N'),
                M('New letter', lambda: self.new_from_template('letter')),
                M('New flyer', lambda: self.new_from_template('flyer')),
                M('New notes', lambda: self.new_from_template('notes')),
                None,
                M('Open...', self.open_file, 'Ctrl+O'),
                M('Save', self.save, 'Ctrl+S'),
                M('Save as...', self.save_as),
                M('Export text (.txt)', self.export_txt, 'as printed'),
                M('Export Word (.docx)', self.export_docx),
                None,
                M('Page setup...', self.page_form),
                M('Print preview', self.enter_gfx, 'F6'),
                M('Print...', self.print_form, 'Ctrl+P'),
                None,
                M('Close', lambda: self.wm.close(self.win))]

    def menu_edit(self):
        M = MenuItem
        sel = bool(self.sel_range())
        return [M('Undo', self.undo, 'Ctrl+Z', enabled=bool(self.undo_stack)),
                M('Redo', self.redo, 'Ctrl+Y', enabled=bool(self.redo_stack)),
                None,
                M('Cut', lambda: self.on_key(Key('ctrl+x')), 'Ctrl+X', enabled=sel),
                M('Copy', self.copy_sel, 'Ctrl+C', enabled=sel),
                M('Paste', lambda: self.on_key(Key('ctrl+v')), 'Ctrl+V',
                  enabled=bool(self.wm.clipboard)),
                M('Select all', lambda: self.on_key(Key('ctrl+a')), 'Ctrl+A'),
                None,
                M('Find...', lambda: self.open_find('q'), 'Ctrl+F'),
                M('Replace...', lambda: self.open_find('r')),
                M('Find next', self.find_next, 'F3'),
                None,
                M('Markdown source', self.toggle_source, 'F5', checked=self.source)]

    def menu_format(self):
        M = MenuItem
        s0 = (self.style_chars() or [self.style_here()])[0]
        p = self.para()
        cur = self.para_style(p)
        items = [M('Bold', lambda: self.toggle('bold'), 'Ctrl+B', checked=s0.bold),
                 M('Underline', lambda: self.cycle_ul(1), 'Ctrl+U', checked=s0.ul == 1),
                 M('Double underline', lambda: self.cycle_ul(2), 'Alt+U', checked=s0.ul >= 2),
                 None]
        for size, label in SIZES:
            items.append(M('Size: %s' % label, lambda s=size: self.cycle_size(s),
                           'Ctrl+G' if size == 1 else '', checked=s0.size == size))
        items.append(None)
        for n, (label, _k, _l) in enumerate(STYLES):
            items.append(M(label, lambda n=n: self.set_style(n), checked=cur == n))
        items.append(None)
        for a, label, _s in ALIGN_NAMES:
            items.append(M('Align %s' % label.lower(), lambda a=a: self.set_align(a),
                           'Ctrl+E' if not a else '', checked=(p.align or '') == a))
        items += [None, M('Fonts...', self.fonts_form)]
        return items

    def menu_insert(self):
        M = MenuItem
        return [M('Picture...', self.insert_image),
                M('Table...', lambda: self.insert_table()),
                M('Page break', lambda: self.insert_block('pagebreak'), 'Ctrl+Enter'),
                M('Horizontal line', lambda: self.insert_block('rule')),
                M("Today's date", self.insert_date)]

    def menu_view(self):
        M = MenuItem

        def flip(attr):
            setattr(self, attr, not getattr(self, attr))
            self.invalidate()
        return [M('Outline', lambda: flip('show_outline'), checked=self.show_outline),
                M('Ruler', lambda: flip('show_ruler'), checked=self.show_ruler),
                M('Focus mode', self.toggle_focus, 'Ctrl+T', checked=self.focus_mode),
                None,
                M('Print preview (graphics)', self.enter_gfx, 'F6'),
                M('Markdown source', self.toggle_source, 'F5', checked=self.source)]

    def popup_at(self, items, x, y):
        ax, ay = self.abs_xy(x, y)
        self.wm.menu(items, ax, ay)

    def style_menu(self):
        cur = self.para_style()
        items = [MenuItem(label, lambda n=n: self.set_style(n), checked=cur == n)
                 for n, (label, _k, _l) in enumerate(STYLES)]
        self.popup_at(items, getattr(self, '_style_x', 0), 2)

    def size_menu(self):
        s0 = (self.style_chars() or [self.style_here()])[0]
        items = []
        for size, label in SIZES:
            name = richdoc.setting(self.doc.settings, 'font%d' % size, self.wm.cfg) \
                if size > 1 else ''
            hint = figlet.label(name) if name else 'printer text'
            items.append(MenuItem(label, lambda s=size: self.cycle_size(s), hint,
                                  checked=s0.size == size))
        items += [None, MenuItem('Fonts...', self.fonts_form)]
        self.popup_at(items, getattr(self, '_size_x', 22), 2)

    # ================================================================ forms
    def close_form(self, *_a):
        self.form = None
        self.invalidate()

    def fonts_form(self):
        names = figlet.available()
        labels = [figlet.label(n) for n in names]
        cfg = self.wm.cfg
        cur2 = richdoc.setting(self.doc.settings, 'font2', cfg)
        cur3 = richdoc.setting(self.doc.settings, 'font3', cfg)
        sample = self.doc_title()[:24] or 'Ueberschrift'

        def preview(cv, x, y, w, h):
            th = self.th
            yy = y
            for key, what in (('font3', 'Huge'), ('font2', 'Large')):
                name = form.get(key)
                font = figlet.load(name)
                rows, _sp = font.compose(sample)
                width = len(rows[0]) if rows else 0
                per = max(1, int(round(len(sample) * self.pg.text_w / float(max(1, width)))))
                cv.put(x, yy, fit('%s: %s - %d rows, about %d letters per line' % (
                    what, figlet.label(name), font.height, per), w), th.c('dim', 'raised'))
                yy += 1
                for r in rows:
                    if yy >= y + h:
                        return
                    cv.put(x, yy, fit(r.rstrip(), w), th.c('bright', 'raised'))
                    yy += 1
                yy += 1

        def done(b):
            if b == 'OK':
                self.snapshot()
                self.doc.settings['font2'] = form.get('font2')
                self.doc.settings['font3'] = form.get('font3')
                self.lay = None
                self.after_edit()
            elif b == 'Default for new':
                cfg['writer_font2'] = form.get('font2')
                cfg['writer_font3'] = form.get('font3')
                try:
                    cfg.save()
                except Exception:              # noqa: BLE001
                    pass
                self.wm.toast('New documents use %s / %s' % (
                    figlet.label(form.get('font2')), figlet.label(form.get('font3'))), 'green')
                return
            self.close_form()

        rows = [
            {'key': 'font2', 'label': 'Large  (S2, Heading 2)', 'kind': 'choice',
             'values': names, 'labels': labels, 'value': cur2 if cur2 in names else names[0]},
            {'key': 'font3', 'label': 'Huge   (S3, Heading 1)', 'kind': 'choice',
             'values': names, 'labels': labels, 'value': cur3 if cur3 in names else names[0]},
            {'label': 'The fonts belong to this document (saved with it).', 'kind': 'info'},
        ]
        form = Form('fonts', 'Fonts of this document', rows, ['OK', 'Default for new', 'Cancel'],
                    done, extra=preview, extra_h=26, width=max(60, min(110, self.pg.text_w + 6)))
        self.form = form
        self.invalidate()

    def page_form(self):
        s = dict(self.doc.settings)
        pg = self.doc.page()
        margin_vals = [''] + [str(v) for v in range(0, 21)]
        margin_lbl = ['auto'] + [str(v) for v in range(0, 21)]

        def refresh(_v=None):
            p = richdoc.Page(collect())
            info['label'] = 'Text area %d x %d characters on %s (%d x %d)' % (
                p.text_w, p.rows, p.paper_label, p.cols, p.lines)
            self.invalidate()

        def collect():
            out = dict(s)
            for key in ('paper', 'cpi', 'lpi', 'left', 'right', 'top', 'bottom'):
                out[key] = form.get(key)
            out['header'] = form.get('header')
            out['footer'] = form.get('footer')
            return out

        def done(b):
            if b == 'OK':
                self.snapshot()
                new = collect()
                for k in list(new):
                    if new[k] == richdoc.DOC_DEFAULTS.get(k) and k not in ('footer', 'header'):
                        new.pop(k)
                self.doc.settings = new
                self.lay = None
                self.after_edit()
            self.close_form()

        def ch(key, label, values, labels, value):
            return {'key': key, 'label': label, 'kind': 'choice', 'values': values,
                    'labels': labels, 'value': value if value in values else values[0],
                    'on_change': refresh}

        def tf(key, label, value):
            f = TextField(value)
            return {'key': key, 'label': label, 'kind': 'text', 'field': f, 'on_change': refresh}

        papers = richdoc.PAPER_ORDER
        rows = [
            ch('paper', 'Paper', papers, [richdoc.PAPERS[p][2] for p in papers], pg.paper),
            ch('cpi', 'Characters per inch', ['10', '12'], ['10 cpi (Pica, 80 columns)',
                                                         '12 cpi (Elite, 96 columns)'], str(pg.cpi)),
            ch('lpi', 'Lines per inch', ['6', '8'], ['6 lpi', '8 lpi'], str(pg.lpi)),
            ch('left', 'Left margin', margin_vals, margin_lbl, str(s.get('left', ''))),
            ch('right', 'Right margin', margin_vals, margin_lbl, str(s.get('right', ''))),
            ch('top', 'Top margin (lines)', margin_vals[:12], margin_lbl[:12], str(s.get('top', ''))),
            ch('bottom', 'Bottom margin (lines)', margin_vals[:12], margin_lbl[:12],
               str(s.get('bottom', ''))),
            tf('header', 'Header', pg.header),
            tf('footer', 'Footer', pg.footer),
            {'label': 'Header/footer: left|centre|right, fields {page} {pages} {title} {date}',
             'kind': 'info'},
            {'label': '', 'kind': 'info'},
        ]
        info = rows[-1]
        form = Form('page', 'Page setup', rows, ['OK', 'Cancel'], done, width=76)
        self.form = form
        refresh()

    def print_form(self):
        cfg = self.wm.cfg
        lay = self.layout()
        cur_page = lay.caret(*self.cur)[1] // self.pg.rows + 1

        def done(b):
            if b == 'Cancel':
                self.close_form()
                return
            cfg['printer_device'] = form.get('device').strip()
            cfg['printer_quality'] = form.get('quality')
            cfg['printer_pictures'] = form.get('pictures')
            try:
                cfg.save()
            except Exception:                  # noqa: BLE001
                pass
            if b == 'Preview':
                self.close_form()
                self.enter_gfx()
                return
            pick = None
            which = form.get('pages')
            if which == 'current':
                pick = [cur_page]
            elif which == 'range':
                pick = parse_pages(form.get('range'), self.pages)
                if not pick:
                    self.wm.toast('Pages: e.g. 1-3,5', 'red')
                    return
            self.close_form()
            self.print_doc(pick=pick, copies=int(form.get('copies')), test=b == 'Test page')

        rows = [
            {'key': 'device', 'label': 'Printer', 'kind': 'text',
             'field': TextField(cfg.get('printer_device', ''),
                                placeholder='/dev/lp0, /dev/usb/lp0 or CUPS queue')},
            {'key': 'quality', 'label': 'Quality', 'kind': 'choice',
             'values': ['draft', 'letter'], 'labels': ['Draft (fast)', 'Letter quality'],
             'value': cfg.get('printer_quality', 'draft')},
            {'key': 'pages', 'label': 'Pages', 'kind': 'choice',
             'values': ['all', 'current', 'range'],
             'labels': ['All (%d)' % self.pages, 'Current (%d)' % cur_page, 'Range'],
             'value': 'all'},
            {'key': 'range', 'label': 'Range', 'kind': 'text', 'field': TextField('1-%d' % self.pages)},
            {'key': 'copies', 'label': 'Copies', 'kind': 'choice',
             'values': [str(v) for v in range(1, 10)], 'value': '1'},
            {'key': 'pictures', 'label': 'Pictures', 'kind': 'choice', 'values': ['gfx', 'text'],
             'labels': ['As set per picture', 'All as ASCII (no graphics)'],
             'value': cfg.get('printer_pictures', 'gfx')},
            {'label': '%s - IBM Proprinter X24E (PPDS)' % self.pg.describe(), 'kind': 'info'},
            {'label': 'Paper: put the top edge at the tear bar before printing.', 'kind': 'info'},
        ]
        form = Form('print', 'Print', rows, ['Print', 'Preview', 'Test page', 'Cancel'], done,
                    width=72)
        self.form = form
        self.invalidate()

    # ================================================================ find / replace
    def open_find(self, focus='q'):
        if not self.find:
            sel = self.selection_text()
            self.find = {'q': TextField(sel if sel and '\n' not in sel else ''),
                         'r': TextField(''), 'focus': focus, 'note': ''}
        self.find['focus'] = focus
        self.invalidate()

    def find_next(self, wrap=True):
        if not self.find or not self.find['q'].text:
            self.open_find('q')
            return False
        q = self.find['q'].text.lower()
        pi, i = self.sel_range()[1] if self.sel_range() else self.cur
        n = len(self.doc.paras)
        for step in range(n + 1):
            k = (pi + step) % n
            if step == n and not wrap:
                break
            t = self.doc.paras[k].text.lower()
            j = t.find(q, i if step == 0 else 0)
            if j >= 0:
                self.anchor = (k, j)
                self.cur = (k, j + len(q))
                self.find['note'] = ''
                self.ensure_visible()
                self.invalidate()
                return True
        self.find['note'] = 'not found'
        self.invalidate()
        return False

    def replace_one(self):
        r = self.sel_range()
        q = self.find['q'].text
        if r and q and self.selection_text().lower() == q.lower():
            self.snapshot()
            (p0, i0), _ = r
            style = self.doc.paras[p0].st[i0] if i0 < len(self.doc.paras[p0].st) else None
            self.delete_sel()
            self.insert_chars(self.find['r'].text, style)
            self.after_edit()
        return self.find_next()

    def replace_all(self):
        q = self.find['q'].text
        if not q:
            return
        rep = self.find['r'].text
        self.snapshot()
        count = 0
        low = q.lower()
        for p in self.doc.paras:
            if p.kind in ('tsep', 'pagebreak', 'rule'):
                continue
            t, st = p.text, p.st
            out_t, out_s, i = [], [], 0
            while True:
                j = t.lower().find(low, i)
                if j < 0:
                    break
                out_t.append(t[i:j])
                out_s.extend(st[i:j])
                out_t.append(rep)
                out_s.extend([st[j]] * len(rep))
                i = j + len(q)
                count += 1
            if i:
                out_t.append(t[i:])
                out_s.extend(st[i:])
                p.set(''.join(out_t), out_s)
        self.cur = self._clamp(self.cur)
        self.anchor = None
        self.find['note'] = '%d replaced' % count
        self.after_edit()

    def find_key(self, ev):
        f = self.find
        k = ev.key
        if k == 'esc':
            self.find = None
        elif k == 'tab' or k == 'shift+tab':
            f['focus'] = 'r' if f['focus'] == 'q' else 'q'
        elif k == 'enter':
            if f['focus'] == 'r':
                self.replace_one()
            else:
                self.find_next()
        elif k in ('f3', 'down'):
            self.find_next()
        elif k == 'alt+a':
            self.replace_all()
        else:
            f[f['focus']].key(ev)
            f['note'] = ''
        self.invalidate()
        return True


    # ================================================================ graphics preview
    def enter_gfx(self):
        """F6: the sheets as the printer puts them on paper - dots on the framebuffer."""
        if self.gfx:
            return
        try:
            from .. import fbmode, printraster      # noqa: F401
        except ImportError as e:
            self.wm.toast('Print preview: %s' % e, 'red')
            return
        ok, why = fbmode.available(self.wm.term)
        if not ok:
            self.wm.toast('Print preview: ' + why, 'red')
            return
        if self.source:
            self.toggle_source()
        th = self.th
        rgb = th.rgb
        colours = {'bg': rgb[th.r['shadow']], 'fg': rgb[th.r['bright']],
                   'dim': rgb[th.r['dim']], 'accent': rgb[th.r['accent']],
                   'field': rgb[th.r['field']]}
        try:
            pg, sheets = self.make_sheets()
            ras = printraster.Raster(pg, font_name=self.wm.cfg.get('font'))
        except Exception as e:                       # noqa: BLE001
            self.wm.toast('Print preview: %s' % e, 'red')
            return
        if self.wm.cfg.get('printer_pictures') == 'text':
            for sh in sheets:
                sh.gfx = []
        try:
            sess = fbmode.Session(self.wm.term.fd_out, fbmode.open_framebuffer(), colours)
            sess.start()
        except OSError as e:
            self.wm.toast('Print preview: %s' % e, 'red')
            return
        self.gfx = sess
        self.wm.enter_gfx(self)
        lay = self.layout()
        cur = lay.caret(*self.cur)[1] // self.pg.rows
        self.pv = {'sheets': sheets, 'ras': ras, 'page': min(cur, len(sheets) - 1), 'y': 0,
                   'x': 0, 'zoom': 'width', 'cache': {}, 'pg': pg}
        self.gfx.note(['PRINT PREVIEW  %s' % pg.describe().upper(),
                       'PGUP/PGDN PAGE  ARROWS SCROLL  Z ZOOM  P PRINT  ESC BACK'], 5.0)
        self.gfx_paint()

    def leave_gfx(self):
        if not self.gfx:
            return
        sess, self.gfx = self.gfx, None
        try:
            sess.close()
        finally:
            self.wm.leave_gfx()
            self.pv = None
            self.invalidate()

    PAPER_RGB = (244, 240, 226)
    INK_RGB = (28, 28, 44)
    DESK_RGB = (52, 52, 56)

    def gfx_page(self, k):
        """The sheet as an 'L' image at 180 dpi (ink = 255), cached."""
        pv = self.pv
        img = pv['cache'].get(('dots', k))
        if img is None:
            img = pv['ras'].render(pv['sheets'][k])
            pv['cache'][('dots', k)] = img
        return img

    def gfx_scaled(self, k, W, H):
        from PIL import Image, ImageOps
        pv = self.pv
        key = ('scaled', k, pv['zoom'], W, H)
        img = pv['cache'].get(key)
        if img is None:
            dots = self.gfx_page(k)
            if pv['zoom'] == 'dots':
                sw, sh = dots.size
            elif pv['zoom'] == 'page':
                sh = H - 24
                sw = int(dots.size[0] * sh / float(dots.size[1]))
            else:
                sw = W - 48
                sh = int(dots.size[1] * sw / float(dots.size[0]))
            small = dots.resize((max(1, sw), max(1, sh)), Image.BOX) if (sw, sh) != dots.size \
                else dots
            img = ImageOps.colorize(ImageOps.invert(small), self.INK_RGB, self.PAPER_RGB)
            pv['cache'][key] = img
        return img

    def gfx_paint(self):
        if not self.gfx or not self.pv:
            return
        from PIL import Image
        g = self.gfx
        pv = self.pv
        W, H = g.screen
        page = self.gfx_scaled(pv['page'], W, H)
        pw, ph = page.size
        pv['y'] = max(0, min(pv['y'], max(0, ph - H + 40)))
        pv['x'] = max(0, min(pv['x'], max(0, pw - W + 40)))
        canvas = Image.new('RGB', (g.fb.line_px, H), self.DESK_RGB)
        x = (W - pw) // 2 if pw <= W else 20 - pv['x']
        y = 12 - pv['y']
        canvas.paste((18, 18, 20), (x + 6, y + 6, x + pw + 6, y + ph + 6))      # shadow
        canvas.paste(page, (x, y))
        target = {32: 'BGRX', 24: 'BGR', 16: 'BGR;16'}[g.fb.bpp]
        frame = bytearray(canvas.tobytes('raw', target))
        self.gfx_hud(frame)
        g.show(frame)

    def gfx_hud(self, buf):
        g = self.gfx
        pv = self.pv
        W, H = g.screen
        px = 2
        bh = 8 * px
        y0 = H - bh
        g.rect(buf, 0, 0, y0, W, bh, g.c_bg)
        g.rect(buf, 0, 0, y0, W, px, g.c_acc)
        ty = y0 + 2 * px
        name = (os.path.basename(self.path) if self.path else 'UNTITLED').upper()
        g.text(buf, 0, 2 * px, ty, name[:40], px, g.c_fg)
        info = 'PAGE %d/%d  %s  %s' % (pv['page'] + 1, len(pv['sheets']),
                                       {'width': 'FIT WIDTH', 'page': 'WHOLE PAGE',
                                        'dots': '180 DPI'}[pv['zoom']],
                                       pv['pg'].describe().upper())
        iw = fbmode_text_width(info, px)
        g.text(buf, 0, W - iw - 2 * px, ty, info, px, g.c_dim)

    def gfx_key(self, ev):
        k = ev.key
        pv = self.pv
        n = len(pv['sheets'])
        step = 64
        if k in ('esc', 'f6', 'g', 'q'):
            self.leave_gfx()
            return True
        if k in ('pgdn', ' ', 'space', 'n'):
            if pv['page'] + 1 < n:
                pv['page'] += 1
                pv['y'] = 0
        elif k in ('pgup', 'b', 'backspace'):
            if pv['page'] > 0:
                pv['page'] -= 1
                pv['y'] = 0
        elif k == 'home':
            pv['page'], pv['y'] = 0, 0
        elif k == 'end':
            pv['page'], pv['y'] = n - 1, 0
        elif k == 'down':
            pv['y'] += step
        elif k == 'up':
            pv['y'] -= step
        elif k == 'right':
            pv['x'] += step
        elif k == 'left':
            pv['x'] -= step
        elif k in ('z', '+', '-', '0'):
            order = ['width', 'page', 'dots']
            pv['zoom'] = order[(order.index(pv['zoom']) + 1) % 3]
            pv['y'] = pv['x'] = 0
        elif k == 'p':
            self.leave_gfx()
            self.print_form()
            return True
        else:
            return True
        self.gfx_paint()
        return True

    def gfx_mouse(self, ev):
        if ev.kind in ('wheelup', 'wheeldown'):
            self.pv['y'] += -64 if ev.kind == 'wheelup' else 64
            self.gfx_paint()
        elif ev.kind == 'down' and ev.button == 2:
            self.leave_gfx()
        return True

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
        if isinstance(key, tuple) and key[0] in ('form<', 'form>', 'formv', 'formt', 'fbtn'):
            if self.form:
                self.form.click(key, ev)
            self.invalidate()
            return
        if isinstance(key, tuple) and key[0] == 'menu':
            self.open_menu(key[1])
            return
        if isinstance(key, tuple) and key[0] == 'findfield':
            self.find['focus'] = key[1]
            self.find[key[1]].click(max(0, ev.x - key[2]))
            self.invalidate()
            return
        if isinstance(key, tuple) and key[0] == 'align':
            if not self.source:
                self.set_align(key[1])
            return
        acts = {'bold': lambda: self.toggle('bold'),
                'underline': lambda: self.cycle_ul(1), 'underline2': lambda: self.cycle_ul(2),
                'stylemenu': self.style_menu, 'sizemenu': self.size_menu,
                'image': self.insert_image, 'table': lambda: self.insert_table(),
                'fonts': self.fonts_form,
                'fwrap': lambda: self.float_key('w', ev), 'fsmall': lambda: self.float_key('-', ev),
                'fbig': lambda: self.float_key('+', ev), 'fdel': lambda: self.float_key('delete', ev),
                'fout': lambda: self.float_key('g', ev),
                'fnext': self.find_next, 'frepl': self.replace_one, 'fall': self.replace_all,
                'fclose': lambda: setattr(self, 'find', None),
                # names of the first version (scripts, tests)
                'size1': lambda: self.cycle_size(1), 'size2': lambda: self.cycle_size(2),
                'size3': lambda: self.cycle_size(3), 'source': self.toggle_source,
                'print': self.print_form, 'head': self.cycle_heading, 'list': self.toggle_list,
                'focus': self.toggle_focus, 'save': self.save, 'docx': self.export_docx,
                'txt': self.export_txt, 'open': self.open_file,
                'new': lambda: self.wm.open('writer')}
        if self.source and key in ('bold', 'underline', 'underline2', 'size1', 'size2', 'size3',
                                   'head', 'list', 'image', 'table', 'stylemenu', 'sizemenu'):
            self.wm.toast('Formatting works on the page (F5)', 'dim')
            return
        if key in acts:
            acts[key]()
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
        if self.focus_mode and not getattr(self.win, 'snap', None):
            self.wm.toggle_max(self.win)
        self.invalidate()

    def doc_xy(self, ev):
        """Mouse event -> (x on the page, row on the screen across all sheets)."""
        return ev.x - self.text_x, ev.y - self.text_y + self.top

    def total_rows(self):
        return self.pages * self.block

    def on_mouse(self, ev):
        if self.gfx:
            return self.gfx_mouse(ev)
        if self.form:
            k = self.track_hover(ev)
            if ev.kind == 'down' and ev.button == 0 and isinstance(k, tuple) and \
                    k[0] in ('form<', 'form>', 'formv', 'formt', 'fbtn'):
                self.on_click(k, ev)
                return True
            if ev.kind == 'down':
                x, y, w, h = self.form.rect
                if not (x <= ev.x < x + w and y <= ev.y < y + h):
                    self.close_form()
            return True
        k0 = self.zone_at(ev.x, ev.y)
        if isinstance(k0, tuple) and k0[0] in ('menu', 'findfield', 'align') or \
                k0 in ('stylemenu', 'sizemenu', 'fnext', 'frepl', 'fall', 'fclose'):
            if ev.kind == 'down' and ev.button == 0:
                self.track_hover(ev)
                self.on_click(k0, ev)
                return True
            return super().on_mouse(ev)
        if self.find and ev.kind == 'down' and k0 == 'text':
            self.find['focus'] = None
        if self.source:
            return self.source_mouse(ev)
        k = k0
        lay = self.layout()
        if ev.kind in ('wheelup', 'wheeldown') and k in ('text', 'sbar'):
            sw = self.pg.cols + 2
            if 'shift' in ev.mods and self.avail < sw:
                self.hx = max(0, min(sw - self.avail,
                                     self.hx + (-4 if ev.kind == 'wheelup' else 4)))
            else:
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

    def global_key(self, k):
        """Keys that work everywhere (page and source view)."""
        if k == 'f5':
            self.toggle_source()
        elif k == 'f6':
            self.enter_gfx()
        elif k == 'f10':
            self.open_menu(0)
        elif k in [a for _l, a in MENUS]:
            self.open_menu([a for _l, a in MENUS].index(k))
        elif k == 'ctrl+s':
            self.save()
        elif k == 'ctrl+p':
            self.print_form()
        elif k == 'ctrl+o':
            self.open_file()
        elif k == 'ctrl+n':
            self.wm.open('writer')
        elif k == 'ctrl+t':
            self.toggle_focus()
        elif k == 'ctrl+f':
            self.open_find('q')
        elif k == 'f3':
            self.find_next()
        else:
            return False
        self.invalidate()
        return True

    def on_key(self, ev):
        k = ev.key
        if self.gfx:
            return self.gfx_key(ev)
        if self.form:
            self.form.key(ev)
            self.invalidate()
            return True
        if self.find and self.find['focus'] and k not in ('f5', 'f6', 'f10', 'ctrl+s', 'ctrl+p'):
            return self.find_key(ev)
        if self.global_key(k):
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
        elif k == 'ctrl+enter':
            self.insert_block('pagebreak')
            return True
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
        elif k == 'ctrl+e':
            self.cycle_align()
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
            if self.find:
                self.find = None
            elif self.anchor is not None:
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
        if self.form:
            self.form.paste(text)
            self.invalidate()
            return
        if self.find and self.find['focus']:
            self.find[self.find['focus']].insert(text.replace('\n', ' '))
            self.invalidate()
            return
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


def fbmode_text_width(text, px):
    from .. import fbmode
    return fbmode.text_width(text, px)


APP = Writer
