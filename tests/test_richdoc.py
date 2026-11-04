"""Writer page: FIGlet fonts, Markdown round trip, WYSIWYG layout with floats."""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import figlet, richdoc  # noqa: E402
from monocell.richdoc import PLAIN, Style  # noqa: E402
from monocell.theme import DEFAULT_ON, PRESETS, ROLE_DEFAULTS, Theme  # noqa: E402


class FakeWM:
    def __init__(self, mode='linux', **cfg):
        self.theme = Theme({'palette': PRESETS['Ember'], 'roles': dict(ROLE_DEFAULTS),
                           'layout': [], 'on': dict(DEFAULT_ON), 'preset': 'Ember'})
        self.cfg = dict({'image_mode': 'ascii', 'dither': 'bayer4', 'ascii_charset': 'full',
                         'cell_aspect': 0}, **cfg)
        self.term = types.SimpleNamespace(allowed_bg=list(range(8)), cell_aspect=2.0, mode=mode)


def lay_out(text, width=40, base='', mono=False, page=None, **cfg):
    doc = richdoc.parse(text)
    lay = richdoc.Layout(FakeWM(**cfg), width, base, sync=True, mono=mono, page=page).run(doc)
    return doc, lay, [''.join(c) for c, _ in lay.rows]


class TestFiglet(unittest.TestCase):
    def test_graceful_matches_figlet(self):
        f = figlet.load('graceful')
        self.assertEqual(f.render('Hello'), [
            ' _  _  ____  __    __     __  ',
            '/ )( \\(  __)(  )  (  )   /  \\ ',
            ') __ ( ) _) / (_/\\/ (_/\\(  O )',
            '\\_)(_/(____)\\____/\\____/ \\__/ '])

    def test_standard_smushes(self):
        rows = figlet.load('standard').render('Hi')
        self.assertEqual(rows[1], '| | | (_)')

    def test_spans_cover_every_glyph(self):
        rows, spans = figlet.load('big').compose('Wörld')
        self.assertEqual(len(spans), 5)
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(rows[0]))
        self.assertTrue(all(a < b for a, b in spans))

    def test_missing_umlauts_are_synthesised(self):
        f = figlet.load('graceful')
        self.assertNotEqual(f.glyph('Ä'), f.glyph('A'))
        self.assertIn('.', ''.join(f.glyph('Ä')))
        self.assertGreater(len(f.glyph('ß')[0]), len(f.glyph('s')[0]))

    def test_available_and_fallback(self):
        names = figlet.available()
        for n in ('graceful', 'big', 'standard', 'small', 'slant'):
            self.assertIn(n, names)
        self.assertEqual(figlet.load('does-not-exist').name, 'graceful')


class TestRoundTrip(unittest.TestCase):
    SAMPLES = [
        '# Titel\n\nText mit **fett**, *kursiv*, `code` und [Link](https://x.org).\n',
        'a <u>b</u> <u2>c</u2> <size2>Gross</size2> <size3>RIESIG</size3>\n',
        '- eins\n  - zwei\n1. drei\n> Zitat\n---\nEnde',
        '```python\nprint(1)\n\n```\nnach dem Code',
        '![Bild](a.png){x=4 dy=1 w=20 wrap=square}\nText daneben\n![alt](b.png)\nmehr',
        '**fett** und <size2><u>gross unterstrichen</u></size2>',
        '2*3*4 und *kein kursiv* bleiben Text',
    ]

    def test_samples_survive(self):
        for src in self.SAMPLES:
            self.assertEqual(richdoc.serialize(richdoc.parse(src)), src, src)

    def test_styles_round_trip_through_markdown(self):
        bold = PLAIN._replace(bold=True)
        big_ul = Style(False, False, False, 2, 2, '')
        bold_big = Style(True, False, False, 1, 3, '')
        p = richdoc.Para('p', 'abcdefghij',
                         [bold, bold, bold_big, PLAIN, PLAIN, PLAIN, big_ul, big_ul, bold, PLAIN])
        doc = richdoc.Doc([p])
        back = richdoc.parse(richdoc.serialize(doc)).paras[0]
        self.assertEqual(back.text, p.text)
        self.assertEqual(back.st, p.st)

    def test_markup_characters_in_text_are_escaped(self):
        for text in ('2*3*4 = 24', '# kein Titel', '- kein Punkt', '<u>wörtlich</u>',
                     '![kein](bild.png)', '1. keine Liste', '---', r'C:\Users\*'):
            doc = richdoc.Doc([richdoc.Para('p', text)])
            back = richdoc.parse(richdoc.serialize(doc)).paras[0]
            self.assertEqual((back.kind, back.text), ('p', text), text)
            self.assertEqual(back.st, [PLAIN] * len(text), text)

    def test_no_italic_in_the_writer(self):
        doc = richdoc.parse('*a* _b_ <i>c</i> **d**')
        p = doc.paras[0]
        self.assertEqual(p.text, '*a* _b_ c d')
        self.assertFalse(any(s.italic for s in p.st))

    def test_float_anchor_follows_its_paragraph(self):
        doc = richdoc.parse('eins\n![x](x.png){x=2 w=10 wrap=square}\nzwei\ndrei')
        self.assertIs(doc.floats[0].anchor, doc.paras[1])
        copy = doc.copy()
        self.assertIs(copy.floats[0].anchor, copy.paras[1])
        self.assertIsNot(copy.paras[1], doc.paras[1])


class TestLayout(unittest.TestCase):
    def test_underline_row_pushes_text_down(self):
        _doc, _lay, rows = lay_out('Hallo <u>Welt</u> <u2>zwei</u2>\nweiter')
        self.assertEqual(rows[0].rstrip(), 'Hallo Welt zwei')
        self.assertEqual(rows[1].rstrip(), '      ---- ====')
        self.assertTrue(rows[2].startswith('weiter'))

    def test_underline_spans_spaces_between_underlined_words(self):
        _doc, _lay, rows = lay_out('<u>ab cd</u> ef')
        self.assertEqual(rows[1].rstrip(), '-----')

    def test_big_text_shares_the_baseline(self):
        _doc, lay, rows = lay_out('ab <size2>Hi</size2> cd', 60)
        f = figlet.load('graceful')
        self.assertEqual(len(rows), f.height)
        self.assertTrue(rows[f.baseline - 1].startswith('ab '))
        self.assertTrue(rows[f.baseline - 1].rstrip().endswith(' cd'))
        x, y, h = lay.caret(0, 3)                  # before the big H
        self.assertEqual((y, h), (0, f.height))
        x2, y2, h2 = lay.caret(0, 0)
        self.assertEqual((y2, h2), (f.baseline - 1, 1))

    def test_big_underline_spans_the_glyphs(self):
        _doc, lay, rows = lay_out('<size2><u2>Hi</u2></size2>', 40)
        f = figlet.load('graceful')
        w = len(f.render('Hi')[0])
        self.assertEqual(rows[f.height].rstrip(), '=' * w)

    def test_headings_use_the_configured_fonts(self):
        _d, _l, rows = lay_out('# Hi', 40, writer_font3='small')
        self.assertEqual(len(rows), figlet.load('small').height)
        _d, _l, rows = lay_out('## Hi', 40)
        self.assertEqual(rows[:4], [r.ljust(40) for r in figlet.load('graceful').render('HI')])

    def test_wrapping_and_hit_testing(self):
        doc, lay, rows = lay_out('eins zwei drei vier fuenf sechs', 12)
        self.assertEqual([r.rstrip() for r in rows], ['eins zwei', 'drei vier', 'fuenf sechs'])
        self.assertEqual(lay.hit(0, 1), (0, 10))
        self.assertEqual(lay.hit(2, 2), (0, 22))
        self.assertEqual(lay.caret(0, 31), (11, 2, 1))

    def test_long_word_is_broken(self):
        _doc, _lay, rows = lay_out('x' * 25, 10)
        self.assertEqual([r.rstrip() for r in rows], ['x' * 10, 'x' * 10, 'x' * 5])

    def test_list_hangs_and_quote_bars(self):
        _doc, _lay, rows = lay_out('- eins zwei drei\n> zitat', 12)
        self.assertEqual(rows[0].rstrip(), '- eins zwei')
        self.assertEqual(rows[1].rstrip(), '  drei')
        self.assertTrue(rows[2].startswith('| zitat'))

    def test_text_wraps_around_a_picture(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (40, 40), (200, 0, 0)).save(os.path.join(d, 'q.png'))
            text = '![q](q.png){x=0 w=10 wrap=square}\n' + ' '.join(['wort'] * 60)
            doc, lay, rows = lay_out(text, 40, d)
            f, x, y, w, h = lay.floats[0]
            self.assertEqual((x, y, w), (0, 0, 10))
            self.assertEqual(h, 5)                      # 40x40 px at cell aspect 2
            for r in range(h):
                self.assertTrue(rows[r][11:].startswith('wort'), rows[r])
            self.assertTrue(rows[h + 1].startswith('wort'))  # below the picture + gap
            # clicking on the picture finds it, clicking next to it finds text
            self.assertIs(lay.float_at(3, 2)[0], f)
            self.assertIsNone(lay.float_at(20, 2))

    def test_block_picture_pushes_text_below(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (40, 40), (0, 200, 0)).save(os.path.join(d, 'q.png'))
            doc, lay, rows = lay_out('![q](q.png){x=10 w=10 wrap=block}\ntext', 40, d)
            self.assertEqual(lay.tops[0], 0)
            self.assertTrue(rows[6].startswith('text'))
            self.assertEqual(lay.caret(0, 0)[1], 6)

    def test_missing_picture_placeholder(self):
        _doc, lay, rows = lay_out('![weg](weg.png){x=0 w=20 wrap=square}\nabc', 40)
        self.assertTrue(any('weg.png: missing' in r for r in rows))
        self.assertTrue(rows[0][21:].startswith('abc'))

    def test_cache_reuses_unchanged_paragraphs(self):
        doc = richdoc.parse('eins\nzwei')
        wm = FakeWM()
        a = richdoc.Layout(wm, 30, sync=True).run(doc)
        doc.paras[1].set('drei', [PLAIN] * 4)
        b = richdoc.Layout(wm, 30, sync=True).run(doc)
        self.assertIs(a.paras[0], b.paras[0])
        self.assertIsNot(a.paras[1], b.paras[1])

    def test_vt_has_ascii_only(self):
        _doc, _lay, rows = lay_out('# Grüße\n- Punkt\n<size2>Öl</size2> *kursiv*', 60)
        self.assertTrue(all(ord(c) < 128 for r in rows for c in r))


if __name__ == '__main__':
    unittest.main()


class TestPagesAndInk(unittest.TestCase):
    def test_page_break_moves_a_line_that_would_be_cut(self):
        doc = richdoc.parse('a\nb\nc\n<u>d</u>')
        lay = richdoc.Layout(FakeWM(), 40, sync=True, page=4).run(doc)
        rows = [''.join(c) for c, _ in lay.rows]
        self.assertEqual(rows[3].strip(), '')          # pad to the page break
        self.assertTrue(rows[4].startswith('d'))
        self.assertTrue(rows[5].startswith('-'))
        self.assertEqual((lay.tops[3], lay.tops[0]), (3, 0))

    def test_page_phase_keeps_pages_aligned(self):
        doc = richdoc.parse('x\n\ny')
        lay = richdoc.Layout(FakeWM(), 40, sync=True, page=2).run(doc)
        self.assertEqual(lay.tops, [0, 1, 2])

    def test_picture_moves_to_the_next_page(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (40, 40), (255, 255, 255)).save(os.path.join(d, 'q.png'))
            doc = richdoc.parse('a\nb\n![q](q.png){x=0 w=10 wrap=square}\nc')
            lay = richdoc.Layout(FakeWM(), 40, d, sync=True, page=5).run(doc)
            f, x, y, w, h = lay.floats[0]
            self.assertEqual(y, 5)                     # 5-row picture needs a full page

    def test_ink_mode_makes_black_transparent(self):
        from monocell import imaging
        wm = FakeWM()
        rgb = bytes([0, 0, 0] * 10 + [255, 255, 255] * 10)
        rows = imaging.render_ink(wm, rgb, 10, 2, 'none')
        text = ''.join(c for c, _ in rows)
        self.assertEqual(text[:10], ' ' * 10)
        self.assertEqual(text[10:], imaging.ASCII_FULL[-1] * 10)

    def test_ink_mode_uses_the_viewer_characters(self):
        from monocell import imaging
        wm = FakeWM()
        rgb = bytes(v for g in range(0, 256, 8) for v in (g, g, g))
        ink = imaging.render_ink(wm, rgb, 32, 1)
        art = imaging.render(wm, rgb, 32, 1, 'ascii')
        self.assertEqual(ink[0][0], art[0][0])          # same characters as the viewer
        self.assertEqual(len(set(ink[0][1])), 1)        # but one ink, no colours

    def test_mono_page_has_no_background_colours(self):
        doc, lay, rows = lay_out('Text **fett** `code` > q', 40, mono=True)
        bgs = {(a >> 4) & 0xF for _c, attrs in lay.rows for a in attrs}
        self.assertEqual(len(bgs), 1)

    def test_mono_page_has_one_ink(self):
        doc, lay, rows = lay_out('# Hi <u>x</u> **b** `c` [l](https://x)', 40, mono=True)
        inks = {a & 0xF for chars, attrs in lay.rows for c, a in zip(chars, attrs) if c != ' '}
        self.assertEqual(len(inks), 1)
        head_h = figlet.load('big').height
        bolds = {bool((a >> 8) & 1) for chars, attrs in lay.rows[:head_h]
                 for c, a in zip(chars, attrs) if c != ' '}
        self.assertEqual(bolds, {True})

    def test_mono_image_uses_characters_only(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (40, 40), (0, 0, 0)).save(os.path.join(d, 'k.png'))
            doc = richdoc.parse('![k](k.png){x=0 w=10 wrap=block}\ntext')
            lay = richdoc.Layout(FakeWM(), 40, d, sync=True, mono=True, page=60).run(doc)
            rows = [''.join(c) for c, _ in lay.rows]
            self.assertEqual(rows[0][:10].strip(), '')   # black: nothing prints
            self.assertTrue(rows[6].startswith('text'))
