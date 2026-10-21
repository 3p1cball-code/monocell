"""Sheets: spreadsheet with formulas (=SUM(A1:A5) ...), CSV/xlsx/ods import, CSV/xlsx/native export."""
import csv
import io
import json
import os
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..app import App
from ..formula import FormulaError, col_index, col_name, evaluate, fmt_number
from ..util import fit, shorten_path
from ..widgets import TextField, button

X_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def read_xlsx(path):
    cells = {}
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.iter(X_NS + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(X_NS + 't')))
        sheets = sorted(n for n in z.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml', n))
        root = ET.fromstring(z.read(sheets[0]))
    for c in root.iter(X_NS + 'c'):
        ref = c.get('r')
        m = re.match(r'([A-Z]+)(\d+)', ref or '')
        if not m:
            continue
        r, col = int(m.group(2)) - 1, col_index(m.group(1))
        t = c.get('t')
        f = c.find(X_NS + 'f')
        v = c.find(X_NS + 'v')
        if f is not None and f.text:
            cells[(r, col)] = '=' + f.text
        elif t == 's' and v is not None:
            cells[(r, col)] = shared[int(v.text)]
        elif t == 'inlineStr':
            cells[(r, col)] = ''.join(x.text or '' for x in c.iter(X_NS + 't'))
        elif v is not None and v.text is not None:
            cells[(r, col)] = v.text
    return cells


def read_ods(path):
    T = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    O = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
    cells = {}
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    table = next(root.iter(T + 'table'))
    r = 0
    for row in table.iter(T + 'table-row'):
        rep = int(row.get(T + 'number-rows-repeated', '1'))
        if rep > 1000:
            break
        c = 0
        for cell in row:
            crep = int(cell.get(T + 'number-columns-repeated', '1'))
            val = cell.get(O + 'value')
            txt = ''.join(cell.itertext())
            v = val if val is not None else txt
            if v and crep < 200:
                for k in range(crep):
                    cells[(r, c + k)] = v
            c += crep
        r += rep
    return cells


def write_xlsx(path, sheet):
    rows = {}
    for (r, c), v in sheet.cells.items():
        rows.setdefault(r, []).append((c, v))
    xml_rows = []
    for r in sorted(rows):
        cs = []
        for c, raw in sorted(rows[r]):
            ref = '%s%d' % (col_name(c), r + 1)
            if raw.startswith('='):
                val = sheet.value(r, c)
                vv = '<v>%s</v>' % escape(str(val)) if isinstance(val, (int, float)) else ''
                cs.append('<c r="%s"><f>%s</f>%s</c>' % (ref, escape(raw[1:]), vv))
            else:
                try:
                    float(raw)
                    cs.append('<c r="%s"><v>%s</v></c>' % (ref, raw))
                except ValueError:
                    cs.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(raw)))
        xml_rows.append('<row r="%d">%s</row>' % (r + 1, ''.join(cs)))
    ws = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet '
          'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>'
          % ''.join(xml_rows))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook '
          'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          '<sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types '
          'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
              'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
              'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', wbrels)
        z.writestr('xl/worksheets/sheet1.xml', ws)


class SheetModel:
    def __init__(self):
        self.cells = {}
        self.cache = {}
        self.colw = {}

    def set(self, r, c, raw):
        if raw == '':
            self.cells.pop((r, c), None)
        else:
            self.cells[(r, c)] = raw
        self.cache = {}

    def raw(self, r, c):
        return self.cells.get((r, c), '')

    def value(self, r, c, stack=None):
        k = (r, c)
        if k in self.cache:
            return self.cache[k]
        raw = self.cells.get(k, '')
        if raw.startswith('='):
            stack = stack or set()
            if k in stack:
                return '#CYCLE'
            stack.add(k)
            try:
                v = evaluate(raw[1:], ref=lambda rc: self._ref(rc, stack),
                             rng=lambda a, b: self._range(a, b, stack))
                if isinstance(v, str) and v.startswith('#'):
                    pass
            except FormulaError as e:
                v = '#ERR: %s' % e
            except RecursionError:
                v = '#DEPTH'
            stack.discard(k)
        else:
            v = raw
            if raw:
                try:
                    f = float(raw.replace(',', '.')) if re.fullmatch(r'-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?', raw) else None
                    if f is not None:
                        v = int(f) if f.is_integer() and '.' not in raw and ',' not in raw and 'e' not in raw.lower() else f
                except ValueError:
                    pass
        self.cache[k] = v
        return v

    def _ref(self, rc, stack):
        v = self.value(rc[0], rc[1], stack)
        if isinstance(v, str) and v.startswith('#'):
            raise FormulaError('ref ' + v)
        return v

    def _range(self, a, b, stack):
        (r0, c0), (r1, c1) = a, b
        out = []
        for r in range(min(r0, r1), max(r0, r1) + 1):
            for c in range(min(c0, c1), max(c0, c1) + 1):
                out.append(self.value(r, c, stack))
        return out

    def display(self, r, c):
        v = self.value(r, c)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return fmt_number(v, 14), True
        if isinstance(v, bool):
            return ('TRUE' if v else 'FALSE'), True
        return str(v), False

    def extent(self):
        if not self.cells:
            return 0, 0
        return max(r for r, c in self.cells) + 1, max(c for r, c in self.cells) + 1


class Sheet(App):
    name = 'sheet'
    title = 'Sheets'
    icon = '[#]'
    size = (90, 26)
    min_size = (36, 8)

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        self.m = SheetModel()
        self.r = self.c = 0
        self.anchor = None
        self.top = self.left = 0
        self.edit = None
        self.path = None
        self.modified = False
        self.undo = []
        self.drag_sel = False
        self.resize_col = None
        self.vis_rows = 20
        self.grid_w = 80
        if path:
            self.load(path)
        else:
            self.demo()
        self.update_title()

    def demo(self):
        data = [['Item', 'Qty', 'Price', 'Total'], ['Coffee', '2', '3.20', '=B2*C2'], ['Bread', '1', '2.80', '=B3*C3'],
                ['Cheese', '3', '1.95', '=B4*C4'], ['', '', 'Sum', '=SUM(D2:D4)'], ['', '', 'Avg', '=AVERAGE(D2:D4)']]
        for r, row in enumerate(data):
            for c, v in enumerate(row):
                if v:
                    self.m.set(r, c, v)
        self.modified = False

    def update_title(self):
        name = os.path.basename(self.path) if self.path else 'untitled'
        self.set_title(('* ' if self.modified else '') + name)

    def colw(self, c):
        return self.m.colw.get(c, 10)

    # ------------------------------------------------------------ io
    def load(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            cells = {}
            colw = {}
            if ext == '.xlsx':
                cells = read_xlsx(path)
            elif ext == '.ods':
                cells = read_ods(path)
            elif ext == '.tws':
                with open(path) as f:
                    d = json.load(f)
                cells = {tuple(int(x) for x in k.split(',')): v for k, v in d['cells'].items()}
                colw = {int(k): v for k, v in d.get('colw', {}).items()}
            else:
                with open(path, newline='', encoding='utf-8', errors='replace') as f:
                    data = f.read()
                try:
                    dialect = csv.Sniffer().sniff(data[:4096], delimiters=',;\t|')
                except csv.Error:
                    dialect = csv.excel_tab if ext == '.tsv' else csv.excel
                for r, row in enumerate(csv.reader(io.StringIO(data), dialect)):
                    for c, v in enumerate(row):
                        if v != '':
                            cells[(r, c)] = v
            self.m.cells = cells
            self.m.cache = {}
            self.m.colw = colw
            self.path = path
            self.modified = False
            # auto width
            for c in range(self.m.extent()[1]):
                if c not in colw:
                    mx = max([len(self.m.display(r, cc)[0]) for (r, cc) in cells if cc == c] + [6])
                    self.m.colw[c] = max(6, min(30, mx + 2))
        except Exception as e:
            self.wm.toast('Cannot open: %s' % e, 'red')

    def save(self, path=None, done=None):
        path = path or self.path
        if not path or path.lower().endswith('.ods'):
            return self.save_as(done)
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.xlsx':
                write_xlsx(path, self.m)
            elif ext == '.tws':
                with open(path, 'w') as f:
                    json.dump({'cells': {'%d,%d' % k: v for k, v in self.m.cells.items()},
                               'colw': self.m.colw}, f)
            else:
                rows, cols = self.m.extent()
                has_formula = any(v.startswith('=') for v in self.m.cells.values())
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f, delimiter='\t' if ext == '.tsv' else ',')
                    for r in range(rows):
                        w.writerow([self.m.raw(r, c) for c in range(cols)])
                if has_formula:
                    self.wm.toast('CSV keeps formulas as text (=...); use .xlsx or .tws to be safe')
            self.path = path
            self.modified = False
            self.wm.toast('Saved ' + shorten_path(path, 50), 'green')
            self.update_title()
            if done:
                done()
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def save_as(self, done=None):
        self.wm.pick_file(lambda p: self.save(p, done), 'save', start=self.path,
                          name='' if self.path else 'sheet.xlsx', parent=self.win)

    def on_close(self):
        if not self.modified:
            return True

        def res(r, v):
            if r == 'Save':
                self.save(done=lambda: self.wm.close(self.win, force=True))
            elif r == 'Discard':
                self.wm.close(self.win, force=True)
        self.wm.dialog('Unsaved changes', 'Save changes to this sheet?', ('Save', 'Discard', 'Cancel'), res,
                       parent=self.win)
        return False

    # ------------------------------------------------------------ editing
    def set_cell(self, r, c, raw):
        old = self.m.raw(r, c)
        if old == raw:
            return
        self.undo.append([(r, c, old)])
        self.undo = self.undo[-200:]
        self.m.set(r, c, raw)
        self.modified = True
        self.update_title()

    def sel_rect(self):
        if self.anchor is None:
            return self.r, self.c, self.r, self.c
        r0, c0 = self.anchor
        return min(r0, self.r), min(c0, self.c), max(r0, self.r), max(c0, self.c)

    def start_edit(self, text=None):
        raw = self.m.raw(self.r, self.c) if text is None else text
        self.edit = TextField(raw, on_enter=lambda t: self.commit(1, 0))

    def commit(self, dr=0, dc=0):
        if self.edit is not None:
            self.set_cell(self.r, self.c, self.edit.text)
            self.edit = None
        self.move(dr, dc)

    def move(self, dr, dc, extend=False):
        if extend and self.anchor is None:
            self.anchor = (self.r, self.c)
        elif not extend:
            self.anchor = None
        self.r = max(0, self.r + dr)
        self.c = max(0, self.c + dc)
        self.ensure()

    def ensure(self):
        rows = self.vis_rows
        if self.r < self.top:
            self.top = self.r
        elif self.r >= self.top + rows:
            self.top = self.r - rows + 1
        if self.c < self.left:
            self.left = self.c
        else:
            while True:
                x = 0
                for cc in range(self.left, self.c + 1):
                    x += self.colw(cc)
                if x <= self.grid_w or self.left >= self.c:
                    break
                self.left += 1

    def clear_sel(self):
        r0, c0, r1, c1 = self.sel_rect()
        ops = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                old = self.m.raw(r, c)
                if old:
                    ops.append((r, c, old))
                    self.m.set(r, c, '')
        if ops:
            self.undo.append(ops)
            self.modified = True
            self.update_title()

    def copy(self, cut=False):
        r0, c0, r1, c1 = self.sel_rect()
        rows = []
        for r in range(r0, r1 + 1):
            rows.append('\t'.join(self.m.display(r, c)[0] for c in range(c0, c1 + 1)))
        self.clip = {(r - r0, c - c0): self.m.raw(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        self.clip_origin = (r0, c0)
        self.wm.set_clipboard('\n'.join(rows))
        if cut:
            self.clear_sel()
        self.wm.toast('Copied %d cell(s)' % len(self.clip))

    def paste_cells(self):
        clip = getattr(self, 'clip', None)
        if not clip:
            if self.wm.clipboard:
                self.paste_text(self.wm.clipboard)
            return
        ops = []
        r0, c0 = self.clip_origin
        for (dr, dc), raw in clip.items():
            r, c = self.r + dr, self.c + dc
            if raw.startswith('='):
                raw = shift_refs(raw, self.r - r0, self.c - c0)
            ops.append((r, c, self.m.raw(r, c)))
            self.m.set(r, c, raw)
        self.undo.append(ops)
        self.modified = True
        self.update_title()

    def paste_text(self, text):
        ops = []
        for dr, line in enumerate(text.rstrip('\n').split('\n')):
            for dc, v in enumerate(line.split('\t')):
                r, c = self.r + dr, self.c + dc
                ops.append((r, c, self.m.raw(r, c)))
                self.m.set(r, c, v.strip())
        self.undo.append(ops)
        self.modified = True
        self.update_title()

    def do_undo(self):
        if not self.undo:
            return
        for r, c, old in reversed(self.undo.pop()):
            self.m.set(r, c, old)
        self.modified = True
        self.update_title()

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        x = 0
        for label, key in (('New', 'new'), ('Open', 'open'), ('Save', 'save'), ('Save as', 'saveas'),
                           ('Sum', 'sum'), ('Sort', 'sort'), ('Undo', 'undo')):
            if x + len(label) + 3 > w:
                break
            x += button(self, cv, x, 0, label, key) + 1
        # formula bar
        ref = '%s%d' % (col_name(self.c), self.r + 1)
        r0, c0, r1, c1 = self.sel_rect()
        if (r0, c0) != (r1, c1):
            ref = '%s%d:%s%d' % (col_name(c0), r0 + 1, col_name(c1), r1 + 1)
        cv.fill(0, 2, w, 1, th.c('text', 'field'))
        cv.put(1, 2, fit(ref, 10), th.c('accent', 'field', 1))
        cv.put(11, 2, '=' if self.m.raw(self.r, self.c).startswith('=') else ':', th.c('dim', 'field'))
        if self.edit is not None:
            self.edit.draw(cv, 12, 2, w - 12, th, focused)
        else:
            cv.put(13, 2, fit(self.m.raw(self.r, self.c), w - 14), th.c('bright', 'field'))
        self.zone(12, 2, w - 12, 1, 'fbar')
        # grid
        gy = 4
        rhw = max(4, len(str(self.top + h)) + 1)
        self.grid_x = rhw
        self.grid_w = w - rhw
        self.vis_rows = h - gy - 2
        hdr = th.c('dim', 'raised')
        cv.fill(0, gy - 1, w, 1, hdr)
        x = rhw
        c = self.left
        self.col_pos = []
        while x < w:
            cw = self.colw(c)
            insel = c0 <= c <= c1
            a = th.c('accent', 'raised', 1) if insel else hdr
            cv.put(x, gy - 1, fit(col_name(c), cw - 1, 'center'), a)
            cv.putc(x + cw - 1, gy - 1, '|', th.c('sel', 'raised'))
            self.col_pos.append((x, cw, c))
            self.zone(x, gy - 1, cw - 1, 1, ('colhdr', c))
            self.zone(x + cw - 1, gy - 1, 1, 1, ('colsz', c))
            x += cw
            c += 1
        for i in range(self.vis_rows):
            r = self.top + i
            y = gy + i
            insel = r0 <= r <= r1
            cv.put(0, y, str(r + 1).rjust(rhw - 1), th.c('accent' if insel else 'dim', 'raised'))
            cv.putc(rhw - 1, y, ' ', th.c('dim', 'raised'))
            for x, cw, c in self.col_pos:
                txt, num = self.m.display(r, c)
                cur = r == self.r and c == self.c
                sel = r0 <= r <= r1 and c0 <= c <= c1
                if cur:
                    a = th.c('on_accent', 'accent') if focused else th.c('bright', 'sel')
                elif sel:
                    a = th.c('bright', 'sel')
                elif txt.startswith('#') and not num:
                    a = th.c('red', 'win')
                else:
                    a = th.c('bright' if num else 'text', 'win' if r % 2 == 0 else 'win')
                cv.fill(x, y, cw, 1, a)
                if txt:
                    cv.put(x, y, fit(txt, cw - 1, 'right' if num else 'left'), a)
            self.zone(rhw, y, w - rhw, 1, ('row', r))
        # status
        nums = [v for v in (self.m.value(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1))
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        st = ''
        if len(nums) > 1 or (r0, c0) != (r1, c1):
            st = 'Sum %s  Avg %s  Count %d' % (fmt_number(sum(nums)), fmt_number(sum(nums) / len(nums)) if nums else '-', len(nums))
        cv.put(0, h - 1, fit(shorten_path(self.path, 40) if self.path else 'untitled', w - len(st) - 2), th.c('dim', 'win'))
        cv.put(w - len(st), h - 1, st, th.c('text', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'new':
            self.wm.open('sheet', path=None)
        elif key == 'open':
            def ok(p):
                if self.modified or self.path:
                    self.wm.open('sheet', path=p)
                else:
                    self.load(p)
                    self.update_title()
            self.wm.pick_file(ok, 'open', parent=self.win)
        elif key == 'save':
            self.save()
        elif key == 'saveas':
            self.save_as()
        elif key == 'undo':
            self.do_undo()
        elif key == 'sum':
            r0, c0, r1, c1 = self.sel_rect()
            if r1 > r0:
                tr, tc = r1 + 1, c0
                self.set_cell(tr, tc, '=SUM(%s%d:%s%d)' % (col_name(c0), r0 + 1, col_name(c0), r1 + 1))
                self.r, self.c, self.anchor = tr, tc, None
            else:
                self.start_edit('=SUM(')
        elif key == 'sort':
            self.sort_col()
        elif key == 'fbar':
            if self.edit is None:
                self.start_edit()
        elif key[0] == 'colhdr':
            self.commit()
            self.anchor = (0, key[1])
            self.c = key[1]
            self.r = max(0, self.m.extent()[0] - 1)
        elif key[0] == 'colsz':
            self.resize_col = (key[1], ev.x, self.colw(key[1]))

    def sort_col(self):
        rows, cols = self.m.extent()
        if rows < 3:
            return
        c = self.c
        body = list(range(1, rows))

        def keyf(r):
            v = self.m.value(r, c)
            return (0, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, str(v).lower())
        order = sorted(body, key=keyf)
        snap = {k: v for k, v in self.m.cells.items()}
        ops = [(r, cc, self.m.raw(r, cc)) for r in body for cc in range(cols)]
        for new_r, old_r in zip(body, order):
            for cc in range(cols):
                self.m.cells.pop((new_r, cc), None)
                v = snap.get((old_r, cc))
                if v:
                    self.m.cells[(new_r, cc)] = v
        self.m.cache = {}
        self.undo.append(ops)
        self.modified = True
        self.update_title()
        self.wm.toast('Sorted rows 2-%d by column %s' % (rows, col_name(c)))

    def cell_at(self, x, y):
        r = self.top + y - 4
        for cx, cw, c in getattr(self, 'col_pos', []):
            if cx <= x < cx + cw:
                return r, c
        return None

    def on_mouse(self, ev):
        if self.resize_col is not None:
            c, sx, w0 = self.resize_col
            if ev.kind == 'drag':
                self.m.colw[c] = max(3, w0 + ev.x - sx)
            elif ev.kind == 'up':
                self.resize_col = None
            self.invalidate()
            return True
        if ev.kind == 'wheelup':
            self.top = max(0, self.top - 3)
        elif ev.kind == 'wheeldown':
            self.top += 3
        elif ev.kind in ('down', 'drag') and ev.y >= 4:
            pos = self.cell_at(ev.x, ev.y)
            if pos and pos[0] >= 0:
                if ev.kind == 'down' and ev.button == 0:
                    if self.edit is not None and self.edit.text.startswith('=') and (pos != (self.r, self.c)):
                        self.edit.insert('%s%d' % (col_name(pos[1]), pos[0] + 1))
                        self.invalidate()
                        return True
                    self.commit()
                    if 'shift' in ev.mods:
                        if self.anchor is None:
                            self.anchor = (self.r, self.c)
                    else:
                        self.anchor = None
                    self.r, self.c = pos
                    self.drag_sel = True
                    if ev.clicks >= 2:
                        self.start_edit()
                elif ev.kind == 'drag' and self.drag_sel:
                    if self.anchor is None:
                        self.anchor = (self.r, self.c)
                    self.r, self.c = pos
            self.invalidate()
            return True
        elif ev.kind == 'up':
            self.drag_sel = False
        else:
            return super().on_mouse(ev)
        self.invalidate()
        return super().on_mouse(ev) if ev.kind == 'down' else True

    def on_key(self, ev):
        k = ev.key
        if self.edit is not None:
            if k == 'esc':
                self.edit = None
            elif k == 'enter':
                self.commit(1, 0)
            elif k == 'tab':
                self.commit(0, 1)
            elif k == 'shift+tab':
                self.commit(0, -1)
            elif k in ('up', 'down') :
                self.commit(-1 if k == 'up' else 1, 0)
            else:
                self.edit.key(ev)
            self.invalidate()
            return True
        moves = {'up': (-1, 0), 'down': (1, 0), 'left': (0, -1), 'right': (0, 1), 'tab': (0, 1), 'shift+tab': (0, -1),
                 'enter': (1, 0)}
        base = k.replace('shift+', '') if k.startswith('shift+') and k != 'shift+tab' else k
        if base in moves and base not in ('tab',) or k in ('tab', 'shift+tab'):
            dr, dc = moves.get(base, moves.get(k))
            self.move(dr, dc, extend=k.startswith('shift+') and k != 'shift+tab')
        elif k == 'pgdn':
            self.move(self.vis_rows - 1, 0)
        elif k == 'pgup':
            self.move(-(self.vis_rows - 1), 0)
        elif k in ('home', 'ctrl+home'):
            self.c = 0
            if k == 'ctrl+home':
                self.r = 0
            self.anchor = None
            self.ensure()
        elif k in ('f2',):
            self.start_edit()
        elif k in ('delete', 'backspace'):
            self.clear_sel()
        elif k == 'ctrl+c':
            self.copy()
        elif k == 'ctrl+x':
            self.copy(cut=True)
        elif k == 'ctrl+v':
            self.paste_cells()
        elif k == 'ctrl+z':
            self.do_undo()
        elif k == 'ctrl+s':
            self.save()
        elif k == 'ctrl+o':
            self.on_click('open', None)
        elif k == 'ctrl+a':
            rows, cols = self.m.extent()
            self.anchor = (0, 0)
            self.r, self.c = max(0, rows - 1), max(0, cols - 1)
        elif ev.char:
            self.start_edit('')
            self.edit.insert(ev.char)
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.edit is not None:
            self.edit.insert(text.split('\n')[0])
        else:
            self.paste_text(text)
        self.invalidate()

    def on_resize(self, w, h):
        self.vis_rows = max(1, h - 6)
        self.grid_w = w - 5


def shift_refs(formula, dr, dc):
    def rep(m):
        cabs, col, rabs, row = m.group(1), m.group(2), m.group(3), m.group(4)
        c = col_index(col) + (0 if cabs else dc)
        r = int(row) + (0 if rabs else dr)
        return '%s%s%s%d' % (cabs, col_name(max(0, c)), rabs, max(1, r))
    return re.sub(r'(\$?)([A-Z]{1,3})(\$?)(\d+)', rep, formula)


APP = Sheet
