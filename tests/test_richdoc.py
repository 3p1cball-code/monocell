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
        for n in ('graceful', 'big', 'standard', 'small', 'slant', 'block', 'block3d'):
            self.assertIn(n, names)
        self.assertNotIn('3d-ascii', names)
        self.assertEqual(figlet.load('3d-ascii').name, 'block3d')     # old settings
        self.assertEqual(figlet.load('does-not-exist').name, 'graceful')

    def test_block3d_font(self):
        f = figlet.load('block3d')
        self.assertEqual((f.height, f.baseline), (7, 6))
        rows, spans = f.compose('3D')
        self.assertEqual(len(spans), 2)
        self.assertEqual(set(''.join(rows)), {'#', ':', ' '})
        self.assertEqual(rows[0].strip(), '')          # room for the umlaut dots
        self.assertTrue(rows[1].startswith('####'))    # face
        self.assertIn(':', rows[-1])                   # extrusion in the shadow row
        flat = figlet.load('block')
        self.assertEqual(flat.height, 7)
        self.assertNotIn(':', ''.join(flat.compose('DG')[0]))

    def test_block_fonts_carry_umlauts(self):
        f = figlet.load('block3d')
        for ch in 'ÄÖÜäöüß€':
            self.assertIn(ord(ch), f.glyphs)


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


class TestDocumentSettings(unittest.TestCase):
    def test_settings_line_round_trip(self):
        src = ('<!-- writer: font3=block paper=a4 cpi=12 left=4 footer="|Seite {page}|" -->\n'
               '# Titel\n\nText\n')
        doc = richdoc.parse(src)
        self.assertEqual(doc.settings['font3'], 'block')
        self.assertEqual(doc.settings['cpi'], '12')
        self.assertEqual(doc.settings['footer'], '|Seite {page}|')
        out = richdoc.serialize(doc)
        self.assertTrue(out.startswith('<!-- writer:'))
        self.assertEqual(richdoc.parse(out).settings, doc.settings)

    def test_a_document_without_settings_stays_without(self):
        doc = richdoc.parse('# T\n\nText\n')
        self.assertEqual(doc.settings, {})
        self.assertFalse(richdoc.serialize(doc).startswith('<!--'))

    def test_old_alias_maps_to_the_new_font(self):
        self.assertEqual(figlet.canonical('3d-ascii'), 'block3d')
        self.assertEqual(figlet.label('block3d'), 'Block 3D')

    def test_page_geometry_follows_paper_and_pitch(self):
        a4 = richdoc.Page({'paper': 'a4', 'cpi': '10', 'lpi': '6'})
        self.assertEqual((a4.cols, a4.lines), (80, 70))
        el = richdoc.Page({'paper': 'a4', 'cpi': '12', 'lpi': '8'})
        self.assertEqual((el.cols, el.lines), (96, 93))
        wide = richdoc.Page({'paper': 'fanfold12', 'cpi': '10'})
        self.assertEqual(wide.cols, 80)          # the printer's line is 8 inch
        self.assertEqual(wide.lines, 72)

    def test_margins_are_automatic_or_given(self):
        auto = richdoc.Page({'paper': 'a4'})
        self.assertEqual(auto.text_w, 68)
        given = richdoc.Page({'paper': 'a4', 'left': '2', 'right': '2', 'top': '1'})
        self.assertEqual(given.text_w, 76)
        self.assertEqual(given.top, 1)
        self.assertEqual(given.rows, 70 - 1 - 3)

    def test_header_footer_fields(self):
        p = richdoc.Page({'footer': 'links|{title}|Seite {page} von {pages}'})
        line = p.margin_line('footer', 2, 5, 'Brief', '')
        self.assertTrue(line.startswith('links'))
        self.assertIn('Brief', line)
        self.assertTrue(line.endswith('Seite 2 von 5'))
        self.assertEqual(len(line), p.text_w - (p.text_w - len(line.rstrip())))

    def test_settings_reach_the_layout(self):
        text = '<!-- writer: font3=standard -->\n# AB\n'
        _d, _l, rows = lay_out(text, 40)
        std = figlet.load('standard').height
        self.assertEqual(len([r for r in rows if not r.strip()]), len(rows) - std)


class TestAlignmentAndTables(unittest.TestCase):
    def test_center_and_right(self):
        _d, lay, rows = lay_out('Mitte {.center}\n\nRechts {.right}', 40)
        self.assertEqual(rows[0].rstrip(), ' ' * ((40 - 5) // 2) + 'Mitte')
        self.assertTrue(rows[2].rstrip().endswith('Rechts'))
        self.assertEqual(rows[2].rindex('R'), 40 - 6)

    def test_justify_uses_the_line(self):
        text = ('Dies ist ein langer Absatz, der ueber mehrere Zeilen laeuft, damit man '
                'die Verteilung der Leerzeichen am Zeilenende sieht. {.justify}')
        _d, _l, rows = lay_out(text, 40)
        self.assertEqual(len(rows[1].rstrip()), 40)          # the line is full ...
        self.assertIn('  ', rows[1][:25])                    # ... because it is spread
        self.assertEqual(len(rows[3].rstrip()), 6)           # last line is not

    def test_alignment_survives_save_and_load(self):
        d = richdoc.parse('# T {.center}\n\nx {.right}\n\ny {.justify}\n')
        out = richdoc.serialize(d)
        self.assertIn('{.center}', out)
        self.assertIn('{.right}', out)
        again = richdoc.parse(out)
        self.assertEqual([p.align for p in again.paras if p.align], ['center', 'right', 'justify'])

    def test_a_literal_brace_is_not_alignment(self):
        p = richdoc.parse_line('Text {.center}')
        self.assertEqual(p.align, 'center')
        q = richdoc.parse_line('Text \\{.center}')
        self.assertEqual(q.align, '')
        self.assertEqual(q.text, 'Text {.center}')

    def test_table_columns_and_alignment(self):
        text = '| Name | Preis |\n|---:|---:|\n| Brot | 2,50 |\n'
        _d, _l, rows = lay_out(text, 40)
        self.assertTrue(rows[0].startswith('+'))
        self.assertIn('| Name', rows[1])
        self.assertTrue(rows[2].startswith('+='))
        self.assertIn('Brot', rows[3])
        self.assertIn('2,50', rows[3])
        self.assertTrue(rows[4].startswith('+'))

    def test_table_round_trip(self):
        src = '| A | B |\n|---|---|\n| 1 | 2 |\n'
        out = richdoc.serialize(richdoc.parse(src))
        self.assertEqual(out.rstrip('\n'), src.rstrip('\n'))

    def test_page_break_pads_to_the_next_sheet(self):
        rows = richdoc.Page().rows
        _d, lay, out = lay_out('oben\n<pagebreak>\nunten', 40, page=rows)
        self.assertIn(' page break ', out[1])
        gy = [i for i, r in enumerate(out) if r.startswith('unten')][0]
        self.assertEqual(gy % rows, 0)
        self.assertIn(1, lay.noprint)          # the marker itself does not print

    def test_sheets_carry_margins_header_and_footer(self):
        from monocell.printer import Sheet
        pg = richdoc.Page({'paper': 'a4', 'footer': '|Seite {page} von {pages}|'})
        doc = richdoc.parse('Hallo\n\n<pagebreak>\n\nWelt\n')
        lay = richdoc.Layout(FakeWM(), pg.text_w, sync=True, mono=True, page=pg.rows).run(doc)
        sheets = richdoc.sheets(lay, pg, 'Titel', '07.11.2026')
        self.assertEqual(len(sheets), 2)
        self.assertIsInstance(sheets[0], Sheet)
        self.assertTrue(sheets[0].rows[pg.top][0].startswith(' ' * pg.left + 'Hallo'))
        self.assertIn('Seite 1 von 2', sheets[0].rows[pg.lines - pg.bottom + 1][0])
        self.assertTrue(any('Welt' in row[0] for row in sheets[1].rows))
        self.assertEqual(len(sheets[0].rows), pg.lines)

    def test_picture_printed_as_graphics_is_masked(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (30, 20), (200, 200, 200)).save(os.path.join(d, 'p.png'))
            pg = richdoc.Page({'paper': 'a4'})
            doc = richdoc.parse('Text\n![p](p.png){x=2 w=10 wrap=square}\n')
            lay = richdoc.Layout(FakeWM(), pg.text_w, d, sync=True, mono=True,
                                 page=pg.rows).run(doc)
            sheets = richdoc.sheets(lay, pg)
            self.assertEqual(len(sheets[0].gfx), 1)
            x, y, w, h, img = sheets[0].gfx[0]
            self.assertEqual(x, pg.left + 2)
            self.assertTrue(sheets[0].masked(y))
        with tempfile.TemporaryDirectory() as d:
            Image.new('RGB', (30, 20), (200, 200, 200)).save(os.path.join(d, 'p.png'))
            doc = richdoc.parse('![p](p.png){x=2 w=10 print=text}\n')
            lay = richdoc.Layout(FakeWM(), 60, d, sync=True, mono=True).run(doc)
            self.assertEqual(richdoc.sheets(lay, richdoc.Page())[0].gfx, [])
