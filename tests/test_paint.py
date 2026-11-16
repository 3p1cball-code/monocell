"""Paint: geometry, document model, brush rules, pen input and the app without a screen."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell.apps import paint as P  # noqa: E402
from monocell.screen import Buffer, Canvas  # noqa: E402
from monocell.term import Key, Mouse  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402


class FakeCfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def save(self):
        pass


class FakeLoop:
    def __init__(self):
        self.later = []
        self.readers = {}

    def call_later(self, delay, cb, owner=None):
        self.later.append(cb)

    def every(self, *a, **kw):
        return None

    def add_reader(self, fd, cb):
        self.readers[fd] = cb

    def remove_reader(self, fd):
        self.readers.pop(fd, None)


class FakeWin:
    def __init__(self, cw=96, ch=32):
        self.cw, self.ch = cw, ch
        self.closed = False
        self.x = self.y = 0
        self.title = ''


class FakeTerm:
    w, h = 113, 48


class FakeWM:
    def __init__(self):
        self.cfg = FakeCfg()
        self.theme = Theme(Config())
        self.loop = FakeLoop()
        self.term = FakeTerm()
        self.focus = None
        self.dirty = False
        self.toasts, self.menus, self.prompts, self.dialogs, self.picks = [], [], [], [], []
        self.handled = []

    def toast(self, text, color=None, secs=None):
        self.toasts.append(text)

    def menu(self, items, x, y):
        self.menus.append(items)

    def prompt(self, title, text, default, on_ok, parent=None):
        self.prompts.append((text, default, on_ok))

    def dialog(self, title, text, buttons, on_result, parent=None):
        self.dialogs.append((text, buttons, on_result))

    def pick_file(self, on_ok, mode='open', start=None, name=None, parent=None):
        self.picks.append((mode, name, on_ok))

    def handle(self, ev):
        self.handled.append((ev.kind, ev.x, ev.y, ev.button))


def app(cw=96, ch=32):
    wm = FakeWM()
    win = FakeWin(cw, ch)
    a = P.Paint(wm, win)
    a.close = lambda: setattr(win, 'closed', True)
    wm.focus = win
    return a


def click(a, kind, x, y, button=0, mods=frozenset()):
    return a.on_mouse(Mouse(kind, x, y, button, mods))


TOP = P.Paint.TOP


class TestGeometry(unittest.TestCase):
    def test_line_includes_both_ends(self):
        pts = P.line_cells(0, 0, 5, 2)
        self.assertEqual(pts[0], (0, 0))
        self.assertEqual(pts[-1], (5, 2))
        self.assertEqual(len(pts), 6)

    def test_line_single_point(self):
        self.assertEqual(P.line_cells(3, 3, 3, 3), [(3, 3)])

    def test_slope_chars(self):
        self.assertEqual(P.slope_char(5, 0), '-')
        self.assertEqual(P.slope_char(0, 5), '|')
        self.assertEqual(P.slope_char(2, 1), '\\')
        self.assertEqual(P.slope_char(2, -1), '/')
        self.assertEqual(P.slope_char(0, 0), '*')

    def test_rect_kinds(self):
        cells = {(x, y): k for x, y, k in P.rect_cells(4, 3, 0, 0)}
        self.assertEqual(cells[(0, 0)], 'corner')
        self.assertEqual(cells[(4, 3)], 'corner')
        self.assertEqual(cells[(2, 0)], 'h')
        self.assertEqual(cells[(0, 1)], 'v')
        self.assertNotIn((2, 1), cells)
        filled = {(x, y): k for x, y, k in P.rect_cells(0, 0, 4, 3, True)}
        self.assertEqual(filled[(2, 1)], 'in')
        self.assertEqual(len(filled), 20)

    def test_oval_is_closed_and_inside_box(self):
        cells = P.oval_cells(0, 0, 20, 10)
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (0, 20, 0, 10))
        d = {(x, y): c for x, y, c in cells}
        self.assertEqual(d[(10, 0)], '-')       # top: horizontal tangent
        self.assertEqual(d[(0, 5)], '|')        # left: vertical tangent

    def test_oval_filled_covers_centre(self):
        d = {(x, y) for x, y, _c in P.oval_cells(0, 0, 20, 10, True)}
        self.assertIn((10, 5), d)
        self.assertNotIn((0, 0), d)

    def test_degenerate_oval_is_line(self):
        self.assertEqual(len(P.oval_cells(0, 3, 6, 3)), 7)

    def test_stamp_sizes(self):
        self.assertEqual(P.stamp_offsets(1), [(0, 0, 1.0)])
        st = P.stamp_offsets(2)
        offs = {(dx, dy) for dx, dy, _f in st}
        self.assertIn((2, 0), offs)              # twice as wide
        self.assertIn((0, 1), offs)
        self.assertNotIn((0, 2), offs)
        self.assertEqual(max(f for *_x, f in st), 1.0)

    def test_ramp(self):
        self.assertEqual(P.ramp_char(0), ' ')
        self.assertEqual(P.ramp_char(0.01), '.')
        self.assertEqual(P.ramp_char(1), '@')
        self.assertEqual(P.ramp_level('@'), 9)
        self.assertEqual(P.ramp_level(' '), 0)
        self.assertIsNone(P.ramp_level('x'))


class TestDoc(unittest.TestCase):
    def test_undo_redo(self):
        d = P.PaintDoc(10, 4)
        d.checkpoint()
        d.set(1, 1, 'x', 3)
        self.assertTrue(d.undo())
        self.assertEqual(d.get(1, 1)[0], ' ')
        self.assertTrue(d.redo())
        self.assertEqual(d.get(1, 1), ('x', 3, -1))
        self.assertFalse(d.redo())

    def test_undo_limit(self):
        d = P.PaintDoc(2, 2)
        for _ in range(P.MAX_UNDO + 10):
            d.checkpoint()
        self.assertEqual(len(d.undo_stack), P.MAX_UNDO)

    def test_set_outside(self):
        d = P.PaintDoc(3, 3)
        self.assertFalse(d.set(3, 0, 'x', 1))
        self.assertFalse(d.set(-1, 0, 'x', 1))

    def test_flood_stops_at_border(self):
        d = P.PaintDoc(10, 5)
        for x, y, _k in P.rect_cells(0, 0, 9, 4):
            d.set(x, y, '#', 15)
        n = d.flood(5, 2, '.', 4)
        self.assertEqual(n, 8 * 3)
        self.assertEqual(d.get(5, 2), ('.', 4, -1))
        self.assertEqual(d.get(0, 0)[0], '#')
        self.assertEqual(d.flood(5, 2, '.', 4), 0)   # same again: nothing

    def test_resize_keeps_content(self):
        d = P.PaintDoc(4, 2)
        d.set(1, 1, 'a', 2)
        d.resize(8, 5)
        self.assertEqual((d.w, d.h, len(d.ch), len(d.ch[4])), (8, 5, 5, 8))
        self.assertEqual(d.get(1, 1)[0], 'a')
        d.resize(1, 1)
        self.assertEqual((d.w, d.h, d.ch), (1, 1, [[' ']]))

    def test_bbox(self):
        d = P.PaintDoc(10, 10)
        self.assertIsNone(d.bbox())
        d.set(2, 3, 'x', 1)
        d.set(7, 5, ' ', 1, 4)
        self.assertEqual(d.bbox(), (2, 3, 7, 5))

    def test_json_roundtrip(self):
        d = P.PaintDoc(6, 3, fg=7)
        d.set(0, 0, 'ä', 12, 3)
        d.set(5, 2, '@', 1)
        e = P.PaintDoc.from_json(d.to_json())
        self.assertEqual((e.w, e.h, e.fg), (6, 3, 7))
        self.assertEqual(e.get(0, 0), ('ä', 12, 3))
        self.assertEqual(e.get(5, 2), ('@', 1, -1))
        self.assertEqual(json.loads(d.to_json())['format'], 'monocell-paint')

    def test_text(self):
        d = P.PaintDoc(6, 3)
        d.set(1, 0, 'a', 1)
        self.assertEqual(d.to_text(), ' a\n')
        e = P.PaintDoc.from_text('ab\n\tc\n', min_w=3, min_h=4)
        self.assertEqual((e.w, e.h), (9, 4))
        self.assertEqual(e.get(8, 1)[0], 'c')

    def test_ansi_roundtrip_strips_codes(self):
        th = Theme(Config())
        d = P.PaintDoc(5, 2)
        d.set(0, 0, 'X', 2)
        d.set(2, 1, 'Y', 3, 5)
        ans = d.to_ansi(th.rgb, 0)
        self.assertIn('\x1b[38;2;', ans)
        self.assertTrue(ans.endswith('\x1b[0m\n'))
        e = P.PaintDoc.from_text(ans)
        self.assertEqual(e.get(0, 0)[0], 'X')
        self.assertEqual(e.get(2, 1)[0], 'Y')


class TestBrush(unittest.TestCase):
    def test_char_mode(self):
        b = P.Brush()
        self.assertEqual(b.cell(0.1, 0.5, (1, 0)), '#')

    def test_ramp_pressure(self):
        b = P.Brush()
        b.mode = 'ramp'
        self.assertEqual(b.cell(1.0), '@')
        self.assertEqual(b.cell(0.12), '.')
        self.assertEqual(b.cell(None), P.ramp_char(0.6))

    def test_ramp_only_darkens(self):
        d = P.PaintDoc(5, 1)
        b = P.Brush()
        b.mode = 'ramp'
        P.dab(d, b, 2, 0, 1.0)
        self.assertEqual(d.get(2, 0)[0], '@')
        P.dab(d, b, 2, 0, 0.2)
        self.assertEqual(d.get(2, 0)[0], '@')
        P.dab(d, b, 3, 0, 0.2)
        P.dab(d, b, 3, 0, 0.7)
        self.assertEqual(d.get(3, 0)[0], P.ramp_char(0.7))

    def test_ramp_other_colour_overpaints(self):
        d = P.PaintDoc(3, 1)
        b = P.Brush()
        b.mode = 'ramp'
        P.dab(d, b, 1, 0, 1.0)
        b.fg = 4
        P.dab(d, b, 1, 0, 0.2)
        self.assertEqual(d.get(1, 0)[:2], (P.ramp_char(0.2), 4))

    def test_pressure_size(self):
        b = P.Brush()
        b.size = 5
        self.assertEqual(b.eff_size(0.0), 5)
        b.press_size = True
        self.assertEqual(b.eff_size(0.0), 1)
        self.assertEqual(b.eff_size(1.0), 5)
        self.assertEqual(b.eff_size(None), 5)

    def test_erase(self):
        d = P.PaintDoc(3, 1)
        d.set(1, 0, 'x', 2, 4)
        P.dab(d, P.Brush(), 1, 0, erase=True)
        self.assertEqual(d.get(1, 0), (' ', d.fg, -1))

    def test_smart_shapes(self):
        b = P.Brush()
        b.mode = 'smart'
        r = {(x, y): c for x, y, c in P.shape_cells('rect', b, 0, 0, 3, 2)}
        self.assertEqual((r[(0, 0)], r[(1, 0)], r[(0, 1)]), ('+', '-', '|'))
        line = {c for _x, _y, c in P.shape_cells('line', b, 0, 0, 8, 0)}
        self.assertEqual(line, {'-'})
        diag = {c for _x, _y, c in P.shape_cells('line', b, 0, 0, 4, 2)}
        self.assertEqual(diag, {'\\'})


def ev(typ, code, value):
    return typ, code, value


class TestPen(unittest.TestCase):
    RANGES = {P.ABS_X: (0, 1000), P.ABS_Y: (0, 500), P.ABS_PRESSURE: (0, 4095)}

    def test_feed_and_frac(self):
        st = P.PenState(dict(self.RANGES), True)
        self.assertFalse(st.feed(P.EV_ABS, P.ABS_X, 500))
        st.feed(P.EV_ABS, P.ABS_Y, 250)
        st.feed(P.EV_ABS, P.ABS_PRESSURE, 4095)
        st.feed(P.EV_KEY, P.BTN_TOOL_PEN, 1)
        st.feed(P.EV_KEY, P.BTN_TOUCH, 1)
        self.assertTrue(st.feed(P.EV_SYN, 0, 0))
        self.assertEqual((st.x, st.y, st.touch, st.prox), (500, 250, True, True))
        self.assertAlmostEqual(st.frac(P.ABS_X, st.x), 0.5)
        self.assertEqual(st.level(), 1.0)
        self.assertEqual(st.frac(P.ABS_X, 5000), 1.0)
        self.assertEqual(st.frac(99, 3), 0.0)

    def test_touch_has_no_pressure(self):
        st = P.PenState(dict(self.RANGES), False)
        st.feed(P.EV_ABS, P.ABS_PRESSURE, 300)
        self.assertIsNone(st.level())

    def test_mt_only_when_no_abs(self):
        st = P.PenState(dict(self.RANGES), False)
        st.feed(P.EV_ABS, P.ABS_MT_X, 10)
        st.feed(P.EV_ABS, P.ABS_X, 20)
        st.feed(P.EV_ABS, P.ABS_MT_X, 30)
        self.assertEqual(st.x, 20)

    def test_rubber_and_side(self):
        st = P.PenState(dict(self.RANGES), True)
        st.feed(P.EV_KEY, P.BTN_TOOL_RUBBER, 1)
        st.feed(P.EV_KEY, P.BTN_STYLUS, 1)
        self.assertTrue(st.rubber and st.side and st.prox)

    def _input(self):
        a = app()
        pen = P.PenInput(a)
        P.screen_pixels, self._orig = (lambda: (1130, 480)), P.screen_pixels
        self.addCleanup(setattr, P, 'screen_pixels', self._orig)
        return a, pen

    def test_frame_down_drag_up(self):
        a, pen = self._input()
        st = P.PenState(dict(self.RANGES), True)
        st.x, st.y, st.pressure, st.touch = 500, 250, 2048, True
        pen.frame(st, False)
        st.x = 600
        pen.frame(st, True)
        st.touch = False
        pen.frame(st, True)
        kinds = [h[0] for h in a.wm.handled]
        self.assertEqual(kinds, ['down', 'drag', 'up'])
        # 1130 px / 113 cols = 10 px per cell, 480 / 48 = 10;
        # 0.5 * 1129 px -> col 56, 0.5 * 479 px -> row 23, 0.6 * 1129 -> col 67
        self.assertEqual(a.wm.handled[0][1:3], (56, 23))
        self.assertEqual(a.wm.handled[1][1:3], (67, 23))
        self.assertAlmostEqual(a.ext_pressure, 2048 / 4095.0)

    def test_side_button_is_right_button(self):
        a, pen = self._input()
        st = P.PenState(dict(self.RANGES), True)
        st.x, st.y, st.touch, st.side = 10, 10, True, True
        pen.frame(st, False)
        self.assertEqual(a.wm.handled[0][3], 2)

    def test_hover_moves(self):
        a, pen = self._input()
        st = P.PenState(dict(self.RANGES), True)
        st.x, st.y = 10, 10
        pen.frame(st, False)
        pen.frame(st, False)            # same cell: no second move
        st.x = 900
        pen.frame(st, False)
        self.assertEqual([h[0] for h in a.wm.handled], ['move', 'move'])

    def test_to_cell_clamps(self):
        a, pen = self._input()
        st = P.PenState(dict(self.RANGES), True)
        st.x, st.y = 1000, 500
        self.assertEqual(pen.to_cell(st), (112, 47))
        P.screen_pixels = lambda: None
        st.x, st.y = 0, 0
        self.assertEqual(pen.to_cell(st), (0, 0))

    def test_no_pen_without_mice(self):
        self.assertIsNone(app().pen)


class TestApp(unittest.TestCase):
    def test_canvas_fills_view(self):
        a = app(96, 32)
        self.assertEqual((a.doc.w, a.doc.h), (96, 28))     # 32 - TOP - status line
        self.assertEqual(a.view(), (0, TOP, 96, 28))

    def test_freehand_stroke_is_connected(self):
        a = app()
        click(a, 'down', 2, TOP + 1)
        click(a, 'drag', 12, TOP + 1)
        click(a, 'up', 12, TOP + 1)
        row = ''.join(a.doc.ch[1])
        self.assertEqual(row[2:13], '#' * 11)
        self.assertTrue(a.doc.dirty)
        a.on_key(Key('ctrl+z'))
        self.assertEqual(''.join(a.doc.ch[1]).strip(), '')

    def test_pressure_from_pen(self):
        a = app()
        a.brush.mode = 'ramp'
        a.ext_pressure = 0.15
        click(a, 'down', 5, TOP + 2)
        a.ext_pressure = 1.0
        click(a, 'drag', 6, TOP + 2)
        click(a, 'up', 6, TOP + 2)
        self.assertEqual(a.doc.ch[2][5], P.ramp_char(0.15))
        self.assertEqual(a.doc.ch[2][6], '@')

    def test_right_button_erases(self):
        a = app()
        a.doc.set(5, 3, 'x', 2)
        click(a, 'down', 5, TOP + 3, button=2)
        click(a, 'up', 5, TOP + 3, button=2)
        self.assertEqual(a.doc.ch[3][5], ' ')
        self.assertFalse(a.stroke_erase)

    def test_pen_rubber_erases(self):
        a = app()
        a.doc.set(5, 3, 'x', 2)
        a.ext_erase = True
        click(a, 'down', 5, TOP + 3)
        self.assertEqual(a.doc.ch[3][5], ' ')

    def test_middle_button_passes(self):
        self.assertFalse(click(app(), 'down', 5, TOP + 3, button=1))

    def test_rect_preview_then_commit(self):
        a = app()
        a.on_key(Key('r', 'r'))
        a.brush.mode = 'smart'
        click(a, 'down', 1, TOP + 1)
        click(a, 'drag', 6, TOP + 4)
        self.assertEqual(a.doc.ch[1][1], ' ')    # only previewed
        buf = Buffer(96, 32)
        a.draw(Canvas(buf), True)
        click(a, 'up', 6, TOP + 4)
        self.assertEqual(a.doc.ch[1][1], '+')
        self.assertEqual(a.doc.ch[1][3], '-')
        self.assertEqual(a.doc.ch[2][1], '|')
        self.assertEqual(a.doc.ch[4][6], '+')

    def test_fill(self):
        a = app(40, 12)
        a.on_key(Key('f', 'f'))
        a.brush.char = '.'
        click(a, 'down', 0, TOP)
        self.assertEqual(a.doc.ch[0].count('.'), a.doc.w)

    def test_pick(self):
        a = app()
        a.doc.set(4, 4, '%', 9)
        a.on_key(Key('l', 'l'))
        a.on_key(Key('i', 'i'))
        click(a, 'down', 4, TOP + 4)
        self.assertEqual((a.brush.char, a.brush.fg, a.tool), ('%', 9, 'line'))

    def test_text_tool(self):
        a = app()
        a.on_key(Key('t', 't'))
        click(a, 'down', 3, TOP + 2)
        for c in 'Hi':
            a.on_key(Key(c, c))
        a.on_key(Key('enter'))
        a.on_key(Key('x', 'x'))         # typed, not a tool key
        self.assertEqual(''.join(a.doc.ch[2][3:5]), 'Hi')
        self.assertEqual(a.doc.ch[3][3], 'x')
        self.assertEqual(a.tool, 'text')
        a.on_key(Key('backspace'))
        self.assertEqual(a.doc.ch[3][3], ' ')
        a.on_paste('ab\ncd')
        self.assertEqual(a.doc.ch[4][3:5], ['c', 'd'])
        a.on_key(Key('esc'))
        self.assertIsNone(a.text_at)

    def test_keys(self):
        a = app()
        a.on_key(Key('7', '7'))
        self.assertEqual((a.brush.mode, a.brush.level), ('ramp', 0.7))
        a.on_key(Key('m', 'm'))
        self.assertEqual(a.brush.mode, 'smart')
        a.on_key(Key(']', ']'))
        a.on_key(Key(']', ']'))
        self.assertEqual(a.brush.size, 3)
        fg = a.brush.fg
        a.on_key(Key('right'))
        self.assertEqual(a.brush.fg, (fg + 1) % 16)
        a.on_key(Key('b', 'b'))
        self.assertTrue(a.filled)
        self.assertFalse(a.on_key(Key('f7')))

    def test_toolbar_clicks(self):
        a = app()
        a.draw(Canvas(Buffer(96, 32)), True)
        zones = {k: (x, y) for x, y, _w, _h, k in a.zones}
        x, y = zones[('tool', 'oval')]
        click(a, 'down', x, y)
        self.assertEqual(a.tool, 'oval')
        x, y = zones[('fg', 4)]
        click(a, 'down', x, y)
        self.assertEqual(a.brush.fg, 4)
        x, y = zones[('menu', None)]
        click(a, 'down', x, y)
        self.assertEqual(len(a.wm.menus), 1)

    def test_toolbar_rows_do_not_overlap(self):
        a = app()
        a.draw(Canvas(Buffer(96, 32)), True)
        rows = {k: y for _x, y, _w, _h, k in a.zones}
        self.assertEqual(rows[('menu', None)], 0)
        self.assertEqual(rows[('mode', None)], 1)
        self.assertEqual(a.zone_at(0, 0), ('menu', None))

    def test_scroll_limits(self):
        a = app(40, 12)
        a.doc.resize(100, 50)
        a.on_mouse(Mouse('wheeldown', 5, TOP + 1))
        self.assertEqual(a.vy, 3)
        a.scroll(1000, 1000)
        self.assertEqual((a.vx, a.vy), (100 - 40, 50 - 8))   # 12 - TOP - status line
        click(a, 'down', 0, TOP)
        self.assertEqual(a.doc.ch[a.vy][a.vx], '#')

    def test_draw_shows_doc_and_status(self):
        a = app(60, 14)
        a.doc.set(0, 0, 'Q', 3)
        buf = Buffer(60, 14)
        a.draw(Canvas(buf), True)
        text = [''.join(c[0] if isinstance(c, tuple) else c for c in row)
                for row in getattr(buf, 'chars', getattr(buf, 'cells', []))]
        if text:
            self.assertEqual(text[TOP][0], 'Q')
            self.assertIn('untitled', text[13])

    def test_save_load_roundtrip(self):
        a = app()
        a.doc.set(2, 2, 'Z', 5)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'bild')
            a._write(p)
            self.assertEqual(a.path, p + P.EXT)
            self.assertFalse(a.doc.dirty)
            b = app()
            b.load(a.path)
            self.assertEqual(b.doc.get(2, 2)[:2], ('Z', 5))
            self.assertEqual(b.path, a.path)
            txt = os.path.join(d, 'x.txt')
            with open(txt, 'w') as f:
                f.write('\x1b[31mhey\x1b[0m\n')
            b.load(txt)
            self.assertIsNone(b.path)
            self.assertEqual(''.join(b.doc.ch[0][:3]), 'hey')

    def test_export(self):
        a = app()
        a.doc.set(0, 0, 'E', 2)
        a.export('ans')
        mode, name, cb = a.wm.picks[-1]
        self.assertEqual((mode, name), ('save', 'drawing.ans'))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name)
            cb(p)
            with open(p) as f:
                self.assertIn('\x1b[38;2;', f.read())

    def test_close_asks_when_dirty(self):
        a = app()
        self.assertTrue(a.on_close())
        click(a, 'down', 3, TOP + 3)
        self.assertFalse(a.on_close())
        _t, buttons, cb = a.wm.dialogs[-1]
        self.assertIn('Discard', buttons)
        cb('Discard')
        self.assertTrue(a.win.closed)
        self.assertTrue(a.on_close())

    def test_new_and_size(self):
        a = app()
        a._new('20x7')
        self.assertEqual((a.doc.w, a.doc.h), (20, 7))
        a._resize_doc('30 x 9')
        self.assertEqual((a.doc.w, a.doc.h), (30, 9))
        a._new('quatsch')
        self.assertEqual(a.wm.toasts[-1], 'Size like 80x30')


if __name__ == '__main__':
    unittest.main()
