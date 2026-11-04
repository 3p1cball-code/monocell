"""Writer app: WYSIWYG editing, formatting, pictures, undo and the source view."""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import figlet, richdoc  # noqa: E402
from monocell.apps.writer import Writer  # noqa: E402
from monocell.screen import Buffer, Canvas  # noqa: E402
from monocell.term import Key, Mouse  # noqa: E402
from monocell.theme import DEFAULT_ON, PRESETS, ROLE_DEFAULTS, Theme  # noqa: E402


class FakeLoop:
    def run_in_thread(self, fn, done=None, error=None):
        try:
            res = fn()
        except Exception as e:      # noqa: BLE001
            if error:
                error(e)
        else:
            if done:
                done(res)

    def every(self, *a, **kw):
        return None

    def call_later(self, *a, **kw):
        return None


class FakeWin:
    title = 'Writer'
    snap = None
    closed = False
    cw, ch = 90, 30


class FakeWM:
    def __init__(self):
        self.theme = Theme({'palette': PRESETS['Ember'], 'roles': dict(ROLE_DEFAULTS),
                           'layout': [], 'on': dict(DEFAULT_ON), 'preset': 'Ember'})
        self.cfg = {'image_mode': 'ascii', 'dither': 'bayer4', 'ascii_charset': 'full',
                    'cell_aspect': 0}
        self.term = types.SimpleNamespace(allowed_bg=list(range(8)), cell_aspect=2.0,
                                          mode='linux')
        self.loop = FakeLoop()
        self.dirty = False
        self.clipboard = ''
        self.toasts = []
        self.picked = None

    def toast(self, text, kind='info', secs=3.5):
        self.toasts.append(text)

    def set_clipboard(self, text):
        self.clipboard = text

    def pick_file(self, cb, *a, **kw):
        self.picked = cb


def key(name, char=None):
    if char is None and len(name) == 1:
        char = name
    return Key(name, char)


class WriterCase(unittest.TestCase):
    def make(self, text='', path=None, w=90, h=30):
        self.wm = FakeWM()
        if path is None and text is not None:
            self.tmp = tempfile.mkdtemp()
            path = os.path.join(self.tmp, 'doc.md')
            with open(path, 'w') as f:
                f.write(text)
        app = Writer(self.wm, FakeWin(), path=path)
        app.focus_mode = True
        self.draw(app, w, h)
        return app

    def draw(self, app, w=90, h=30):
        app.zones = []
        buf = Buffer(w, h)
        app.draw(Canvas(buf), True)
        return [''.join(r) for r in buf.ch]

    def type(self, app, text):
        for c in text:
            app.on_key(key(c, c))

    def src(self, app):
        return richdoc.serialize(app.doc)


class TestTyping(WriterCase):
    def test_typing_keeps_the_style_before_the_cursor(self):
        app = self.make('**fett**')
        app.cur = (0, 4)
        self.type(app, 'er')
        self.assertEqual(self.src(app), '**fetter**')

    def test_bold_toggle_without_selection_styles_the_next_chars(self):
        app = self.make('a ')
        app.cur = (0, 2)
        app.on_key(key('ctrl+b'))
        self.type(app, 'b')
        app.on_key(key('ctrl+b'))
        self.type(app, 'c')
        self.assertEqual(self.src(app), 'a **b**c')

    def test_u2_inside_a_word_underlines_it_double(self):
        app = self.make('Hallo Welt')
        app.cur = (0, 7)                  # inside "Welt"
        app.on_click('underline2', None)
        self.assertEqual(self.src(app), 'Hallo <u2>Welt</u2>')
        screen = self.draw(app)
        self.assertIn('====', ''.join(screen))
        app.on_click('underline2', None)
        self.assertEqual(self.src(app), 'Hallo Welt')

    def test_toolbar_labels_u1_u2_s1_s2_s3(self):
        app = self.make('x')
        app.focus_mode = False
        screen = '\n'.join(self.draw(app, 100, 30))
        for label in (' U1 ', ' U2 ', ' S1 ', ' S2 ', ' S3 '):
            self.assertIn(label, screen)

    def test_s1_back_to_normal_size(self):
        app = self.make('<size3>Gross</size3>')
        app.anchor, app.cur = (0, 0), (0, 5)
        app.on_click('size1', None)
        self.assertEqual(self.src(app), 'Gross')

    def test_alt_u_is_double_underline(self):
        app = self.make('Hallo Welt')
        app.anchor, app.cur = (0, 6), (0, 10)
        app.on_key(key('alt+u'))
        self.assertEqual(self.src(app), 'Hallo <u2>Welt</u2>')

    def test_selection_underline_toggles(self):
        app = self.make('ein Wort')
        app.anchor, app.cur = (0, 4), (0, 8)
        app.on_key(key('ctrl+u'))
        self.assertEqual(self.src(app), 'ein <u>Wort</u>')
        app.on_key(key('ctrl+shift+u'))
        self.assertEqual(self.src(app), 'ein <u2>Wort</u2>')
        app.on_key(key('ctrl+u'))
        self.assertEqual(self.src(app), 'ein Wort')
        app.on_click('underline2', None)
        app.on_click('underline2', None)
        self.assertEqual(self.src(app), 'ein Wort')

    def test_typing_underline_on_and_off(self):
        app = self.make('')
        app.on_key(key('ctrl+u'))
        self.type(app, 'ab')
        app.on_key(key('ctrl+u'))
        self.type(app, 'c')
        self.assertEqual(self.src(app), '<u>ab</u>c')

    def test_size_is_typed_directly(self):
        app = self.make('')
        app.on_click('size2', None)
        self.type(app, 'Hi')
        self.assertEqual(self.src(app), '<size2>Hi</size2>')
        screen = self.draw(app)
        big = figlet.load('graceful').render('HI')
        self.assertTrue(any(big[1].rstrip() in line for line in screen))

    def test_autoformat_heading_list_quote(self):
        app = self.make('')
        self.type(app, '## Titel')
        self.assertEqual((app.doc.paras[0].kind, app.doc.paras[0].level), ('h', 2))
        app.on_key(key('enter'))
        self.assertEqual(app.doc.paras[1].kind, 'p')
        self.type(app, '- a')
        app.on_key(key('enter'))
        self.type(app, 'b')
        app.on_key(key('enter'))
        app.on_key(key('enter'))                 # empty item ends the list
        self.type(app, '> z')
        self.assertEqual(self.src(app), '## Titel\n- a\n- b\n> z')

    def test_numbered_list_counts_on(self):
        app = self.make('')
        self.type(app, '1. a')
        app.on_key(key('enter'))
        self.type(app, 'b')
        self.assertEqual(self.src(app), '1. a\n2. b')

    def test_rule_and_code_fence(self):
        app = self.make('')
        self.type(app, '---')
        app.on_key(key('enter'))
        self.type(app, '```py')
        app.on_key(key('enter'))
        self.type(app, 'x = 1')
        app.on_key(key('enter'))
        app.on_key(key('enter'))
        self.type(app, 'ende')
        self.assertEqual(self.src(app), '---\n```py\nx = 1\n```\nende')

    def test_backspace_merges_and_unformats(self):
        app = self.make('# Titel\nText')
        app.cur = (1, 0)
        app.on_key(key('backspace'))
        self.assertEqual(self.src(app), '# TitelText')
        app.cur = (0, 0)
        app.on_key(key('backspace'))
        self.assertEqual(self.src(app), 'TitelText')

    def test_enter_splits_and_undo_restores(self):
        app = self.make('abcdef')
        app.cur = (0, 3)
        app.on_key(key('enter'))
        self.assertEqual(self.src(app), 'abc\ndef')
        app.on_key(key('ctrl+z'))
        self.assertEqual(self.src(app), 'abcdef')
        app.on_key(key('ctrl+y'))
        self.assertEqual(self.src(app), 'abc\ndef')

    def test_typing_is_one_undo_step(self):
        app = self.make('')
        self.type(app, 'hallo')
        app.on_key(key('ctrl+z'))
        self.assertEqual(self.src(app), '')

    def test_rich_copy_paste(self):
        app = self.make('a **fett** <u>b</u>\nzweite')
        app.anchor, app.cur = (0, 2), (1, 3)
        app.on_key(key('ctrl+c'))
        self.assertEqual(self.wm.clipboard, 'fett b\nzwe')
        app.anchor = None
        app.cur = (1, 6)
        app.on_key(key('ctrl+v'))
        self.assertEqual(self.src(app), 'a **fett** <u>b</u>\nzweite**fett** <u>b</u>\nzwe')

    def test_plain_paste_with_newlines(self):
        app = self.make('xy')
        app.cur = (0, 1)
        app.on_paste('1\n2')
        self.assertEqual(self.src(app), 'x1\n2y')


class TestCursorAndMouse(WriterCase):
    def test_click_places_cursor_on_the_page(self):
        app = self.make('eins zwei\ndrei')
        x0, y0 = app.text_x, app.text_y
        app.on_mouse(Mouse('down', x0 + 6, y0 + app.y_scr(0), 0))
        app.on_mouse(Mouse('up', x0 + 6, y0 + app.y_scr(0), 0))
        self.assertEqual(app.cur, (0, 6))
        app.on_mouse(Mouse('down', x0 + 2, y0 + app.y_scr(1), 0))
        self.assertEqual(app.cur, (1, 2))

    def test_up_down_across_an_underline_row(self):
        app = self.make('<u>abc</u>\ndef')
        app.cur = (0, 2)
        app.on_key(key('down'))
        self.assertEqual(app.cur, (1, 2))
        app.on_key(key('up'))
        self.assertEqual(app.cur, (0, 2))

    def test_double_click_selects_word(self):
        app = self.make('eins zwei')
        x0, y0 = app.text_x, app.text_y
        app.on_mouse(Mouse('down', x0 + 6, y0 + app.y_scr(0), 0, frozenset(), 2))
        self.assertEqual(app.sel_range(), ((0, 5), (0, 9)))

    def test_cursor_is_drawn_full_height_on_big_text(self):
        app = self.make('<size2>H</size2>')
        app.cur = (0, 0)
        lay = app.layout()
        self.assertEqual(lay.caret(0, 0)[2], figlet.load('graceful').height)


class TestPictures(WriterCase):
    def setUp(self):
        from PIL import Image
        self.tmpd = tempfile.mkdtemp()
        Image.new('RGB', (40, 40), (20, 90, 200)).save(os.path.join(self.tmpd, 'p.png'))

    def doc_with_picture(self, wrap='square'):
        text = '![p](p.png){x=0 w=10 wrap=%s}\n%s' % (wrap, ' '.join(['wort'] * 40))
        path = os.path.join(self.tmpd, 'd.md')
        with open(path, 'w') as f:
            f.write(text)
        return self.make(None, path)

    def test_insert_picture_floats_left_and_selects_it(self):
        app = self.make('Text')
        app.on_click('image', None)
        self.wm.picked(os.path.join(self.tmpd, 'p.png'))
        f = app.doc.floats[0]
        self.assertIs(app.sel_float, f)
        self.assertEqual(f.wrap, 'square')
        self.assertIn('wrap=square', self.src(app))

    def test_drag_moves_and_reanchors(self):
        app = self.doc_with_picture()
        x0, y0 = app.text_x, app.text_y
        app.on_mouse(Mouse('down', x0 + 2, y0 + app.y_scr(0), 0))
        self.assertIsNotNone(app.sel_float)
        app.on_mouse(Mouse('drag', x0 + 22, y0 + app.y_scr(3), 0))
        app.on_mouse(Mouse('up', x0 + 22, y0 + app.y_scr(3), 0))
        f = app.doc.floats[0]
        self.assertEqual((f.x, f.dy), (20, 3))
        self.assertIn('{x=20 dy=3 w=10 wrap=square}', self.src(app))
        app.on_key(key('ctrl+z'))
        self.assertEqual(app.doc.floats[0].x, 0)

    def test_corner_handle_resizes(self):
        app = self.doc_with_picture()
        x0, y0 = app.text_x, app.text_y
        app.on_mouse(Mouse('down', x0 + 2, y0 + app.y_scr(0), 0))
        app.on_mouse(Mouse('up', x0 + 2, y0 + app.y_scr(0), 0))
        fx, fy, fw, fh = app.float_rect(app.sel_float)
        hx, hy = x0 + fx + fw, y0 + app.y_scr(fy) + fh   # the '#' corner of the frame
        app.on_mouse(Mouse('down', hx, hy, 0))
        app.on_mouse(Mouse('drag', hx + 6, hy, 0))
        app.on_mouse(Mouse('up', hx + 6, hy, 0))
        self.assertEqual(app.doc.floats[0].w, 16)
        self.assertGreater(app.float_rect(app.doc.floats[0])[3], fh)

    def test_keys_move_resize_wrap_delete(self):
        app = self.doc_with_picture()
        app.sel_float = app.doc.floats[0]
        app.on_key(key('right'))
        app.on_key(key('shift+right'))
        self.assertEqual(app.doc.floats[0].x, 5)
        app.on_key(key('+', '+'))
        self.assertEqual(app.doc.floats[0].w, 12)
        app.on_key(key('w', 'w'))
        self.assertEqual(app.doc.floats[0].wrap, 'block')
        app.on_key(key('delete'))
        self.assertEqual(app.doc.floats, [])
        self.assertIsNone(app.sel_float)

    def test_text_wraps_beside_the_picture_on_screen(self):
        app = self.doc_with_picture()
        screen = self.draw(app)
        x0, y0 = app.text_x, app.text_y
        self.assertTrue(screen[y0 + app.y_scr(0)][x0 + 11:].startswith('wort'))

    def test_deleting_the_anchor_keeps_the_picture(self):
        app = self.make('eins\n![p](p.png){x=0 w=10 wrap=square}\nzwei', None)
        app.anchor, app.cur = (0, 2), (1, 2)
        app.on_key(key('delete'))
        self.assertIs(app.doc.floats[0].anchor, app.doc.paras[0])
        self.assertIn('![p](p.png)', self.src(app))


class TestExport(WriterCase):
    def test_txt_export_is_plain_text(self):
        from PIL import Image
        import tempfile
        d = tempfile.mkdtemp()
        Image.new('RGB', (8, 8), (0, 0, 0)).save(os.path.join(d, 'p.png'))
        path = os.path.join(d, 'd.md')
        with open(path, 'w') as f:
            f.write('# Titel\n\nText <u>unter</u> **fett**\n![Alt](p.png){x=0 w=6 wrap=square}\n')
        app = self.make(None, path)
        out = os.path.join(d, 'd.txt')
        app.export_txt()
        self.wm.picked(out)
        with open(out) as f:
            text = f.read()
        self.assertIn('Titel', text)
        self.assertIn('Text unter fett', text)
        self.assertIn('[Bild: Alt]', text)
        self.assertNotIn('**', text)
        self.assertNotIn('<u>', text)

    def test_save_as_txt(self):
        app = self.make('Text **fett**')
        p = os.path.join(tempfile.mkdtemp(), 'x.txt')
        app.save(p)
        with open(p) as f:
            self.assertEqual(f.read(), 'Text fett')

    def test_toolbar_has_txt(self):
        app = self.make('x')
        app.focus_mode = False
        self.assertIn(' TXT ', '\n'.join(self.draw(app, 110, 30)))

    def test_docx_export_embeds_the_picture(self):
        import zipfile
        from PIL import Image
        d = tempfile.mkdtemp()
        Image.new('RGB', (40, 20), (0, 90, 0)).save(os.path.join(d, 'p.png'))
        path = os.path.join(d, 'd.md')
        with open(path, 'w') as f:
            f.write('Text\n![Alt](p.png){x=0 w=8 wrap=square}\n')
        app = self.make(None, path)
        out = os.path.join(d, 'd.docx')
        app.export_docx()
        self.wm.picked(out)
        with zipfile.ZipFile(out) as z:
            self.assertIn('word/media/image1.png', z.namelist())
            self.assertIn('<w:drawing>', z.read('word/document.xml').decode())


class TestPage(WriterCase):
    def test_sheet_with_border_and_page_number(self):
        app = self.make('eins zwei')
        screen = self.draw(app, 100, 30)
        text = '\n'.join(screen)
        self.assertIn('Seite 1/1', text)
        self.assertTrue(any(line.strip().startswith('+---') and '---+' in line
                            for line in screen))
        self.assertIn('eins zwei', text)

    def test_screen_and_document_rows_map(self):
        app = self.make('a\nb')
        for gy in (0, 1, 59, 60, 61):
            self.assertLessEqual(abs(app.y_doc(app.y_scr(gy)) - gy), app.rows_per_page)
        self.assertEqual(app.y_doc(app.y_scr(0)), 0)
        self.assertEqual(app.y_doc(app.y_scr(1)), 1)

    def test_long_document_gets_several_pages(self):
        app = self.make('\n'.join('Zeile %d' % i for i in range(130)))
        screen = self.draw(app, 100, 30)
        self.assertGreaterEqual(app.pages, 3)
        self.assertIn('Seite 1/%d' % app.pages, '\n'.join(screen))

    def test_print_writes_pages_with_numbers(self):
        import monocell.printer as printer
        app = self.make('\n'.join('Zeile %d' % i for i in range(130)))
        sent = []
        orig = printer.send
        printer.send = lambda data, target: sent.append(data)
        try:
            self.wm.cfg['printer_device'] = '/dev/null'
            app.print_doc()
        finally:
            printer.send = orig
        self.assertTrue(sent)
        body = sent[0].split(b'\x1b2', 1)[1]      # after the init (ESC A 12/72 holds a \f)
        self.assertEqual(body.count(b'\x0c'), app.pages)
        self.assertIn(b'Seite 2/', sent[0])

    def test_page_break_pushes_content_down(self):
        app = self.make('x\n' * 59 + '<u>y</u>')
        lay = app.layout()
        first_y = lay.tops[-1] + lay.paras[-1].lines[0][2]
        self.assertEqual(first_y % app.rows_per_page, 0)   # new page starts clean
        self.assertEqual(first_y, app.rows_per_page)


class TestScrollbar(WriterCase):
    def test_shared_scrollbar_drag(self):
        app = self.make('\n'.join('Zeile %d' % i for i in range(200)))
        self.draw(app, 100, 30)
        g = app.sb
        self.assertIsNotNone(g)
        app.on_mouse(Mouse('down', g.x, g.y + g.ty, 0))
        app.on_mouse(Mouse('drag', g.x, g.y + g.h - 1, 0))
        app.on_mouse(Mouse('up', g.x, g.y + g.h - 1, 0))
        self.assertEqual(app.top, app.total_rows() - app.view_h)
        screen = self.draw(app, 100, 30)
        self.assertFalse(any(line[g.x] == '#' for line in screen[g.y:g.y + g.h]))


class TestSourceAndFiles(WriterCase):
    def test_source_view_round_trip(self):
        app = self.make('# T\n**b**')
        app.on_key(key('f5'))
        self.assertTrue(app.source)
        self.assertEqual(app.ta.text(), '# T\n**b**')
        app.ta.lines[1] = '<u>i</u>'
        app.on_key(key('f5'))
        self.assertFalse(app.source)
        self.assertEqual(app.doc.paras[1].st[0].ul, 1)

    def test_italic_shortcut_is_gone(self):
        app = self.make('Wort')
        app.anchor, app.cur = (0, 0), (0, 4)
        app.on_key(key('alt+i'))
        self.assertEqual(self.src(app), 'Wort')

    def test_save_writes_markdown(self):
        app = self.make('alt')
        app.cur = (0, 3)
        self.type(app, ' neu')
        app.save()
        with open(app.path) as f:
            self.assertEqual(f.read(), 'alt neu')
        self.assertFalse(app.modified)

    def test_unchanged_document_is_saved_byte_identical(self):
        text = ('# Titel\n\nAbsatz mit **fett**, *kursiv* und <u>unter</u>.\n- a\n- b\n\n'
                '```\ncode\n```\n> q\n---\n![x](x.png)\nende\n')
        app = self.make(text)
        app.save()
        with open(app.path) as f:
            self.assertEqual(f.read(), text)

    def test_print_renders_page(self):
        import monocell.printer as printer
        app = self.make('# Hi\n<u>x</u>')
        sent = []
        orig = printer.send
        printer.send = lambda data, target: sent.append(data)
        try:
            self.wm.cfg['printer_device'] = '/dev/null'
            app.print_doc()
        finally:
            printer.send = orig
        self.assertTrue(sent and b'-' in sent[0] and sent[0].startswith(b'\x1b@'))


if __name__ == '__main__':
    unittest.main()
