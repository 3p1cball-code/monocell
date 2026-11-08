"""Bundled console fonts: the glyphs Monocell draws itself must be sane."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import fix_psf_equals  # noqa: E402
from monocell import theme  # noqa: E402


class TestConsoleFonts(unittest.TestCase):
    def font(self, name):
        path = os.path.join(ROOT, 'monocell', 'fonts', name)
        return fix_psf_equals.read(path)

    def test_all_bundled_fonts_are_readable(self):
        d = os.path.join(ROOT, 'monocell', 'fonts')
        for name in sorted(os.listdir(d)):
            if name.endswith(('.psf', '.psf.gz')):
                info = self.font(name)
                self.assertIn(info[0], ('psf1', 'psf2'), name)

    def test_equals_is_two_bars_everywhere(self):
        d = os.path.join(ROOT, 'monocell', 'fonts')
        for name in sorted(os.listdir(d)):
            if name.endswith(('.psf', '.psf.gz')):
                info = self.font(name)
                rows = [(r, v) for r, v in fix_psf_equals.glyph_rows(info, '=')
                        if fix_psf_equals.visible(info, v)]
                self.assertGreaterEqual(len(rows), 2, '%s: = has %r' % (name, rows))

    def test_font_cell_from_psf(self):
        self.assertEqual(theme.console_font_cell('Lat15-3270.psf.gz'), (9, 16))
        self.assertEqual(theme.console_font_cell('Lat15-Fixed16.psf.gz'), (8, 16))
        self.assertEqual(theme.console_font_cell('Lat15-Terminus16.psf.gz'), (8, 16))
        self.assertIsNone(theme.console_font_cell('../nope.psf.gz'))

    def test_psf_screenshot_renderer(self):
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, 'tools'))
        import snap
        from monocell.vt import VTScreen
        from PIL import Image
        w, h, glyphs = snap.load_psf(os.path.join(ROOT, 'monocell', 'fonts', 'Lat15-3270.psf.gz'))
        self.assertEqual((w, h), (9, 16))
        self.assertEqual(len(glyphs), 512)
        vt = VTScreen(10, 2, reply=lambda s: None)
        vt.feed('\x1b]P4ff0000\x1b[44m  \x1b[0m')
        out = os.path.join(tempfile.mkdtemp(), 'shot.png')
        snap.render_psf(vt, out, os.path.join(ROOT, 'monocell', 'fonts', 'Lat15-3270.psf.gz'))
        im = Image.open(out)
        self.assertEqual(im.size, (10 * 9, 2 * 16))
        self.assertEqual(im.getpixel((0, 0)), (255, 0, 0))

    def test_console_font_path(self):
        self.assertTrue(theme.console_font_path('Lat15-3270.psf.gz').endswith('Lat15-3270.psf.gz'))
        self.assertIsNone(theme.console_font_path('../etc/passwd'))
        self.assertIsNone(theme.console_font_path('/usr/share/consolefonts/x.psf.gz'))
        self.assertIsNone(theme.console_font_path(''))

    def test_minimal_cell_is_built_from_the_design(self):
        import gzip
        import make_minimal_cell as mc
        for h in (7, 8):
            path = os.path.join(ROOT, 'monocell', 'fonts', 'MinimalCell-5x%d.psf.gz' % h)
            with open(path, 'rb') as f:
                committed = gzip.decompress(f.read())
            self.assertEqual(mc.build(h)[0], committed,
                             'run tools/make_minimal_cell.py after changing the design')

    def test_minimal_cell_in_the_font_menu(self):
        names = dict((n, l) for l, n in theme.available_fonts())
        self.assertEqual(names.get('MinimalCell-5x7.psf.gz'), 'minimal-cell 5x7')
        self.assertEqual(names.get('MinimalCell-5x8.psf.gz'), 'minimal-cell 5x8')
        self.assertEqual(theme.console_font_cell('MinimalCell-5x7.psf.gz'), (5, 7))
        self.assertEqual(theme.console_font_cell('MinimalCell-5x8.psf.gz'), (5, 8))

    def test_minimal_cell_covers_latin1(self):
        from monocell.fbmode import load_psf
        for h in (7, 8):
            path = os.path.join(ROOT, 'monocell', 'fonts', 'MinimalCell-5x%d.psf.gz' % h)
            w, hh, glyphs, umap = load_psf(path)
            self.assertEqual((w, hh, len(glyphs)), (5, h, 512))   # fbcon: 256 or 512
            for o in list(range(32, 127)) + list(range(160, 256)):
                self.assertEqual(umap.get(chr(o)), o, 'U+%04X' % o)
            for ch in '█▀▄▌▐░▒▓─│┌┐└┘├┤┬┴┼═║╔╗╚╝╬━┃€…•←→\ufffd':
                self.assertIn(ch, umap, ch)
            self.assertEqual(umap['┏'], umap['┌'])         # heavy -> light shape
            self.assertEqual(umap['Ā'], umap['A'])         # accented -> base letter

    def test_minimal_cell_metrics(self):
        import make_minimal_cell as mc
        for h in (7, 8):
            g = mc.charmap(h)
            full = [[1] * 5 for _ in range(h)]
            self.assertEqual(g['█'], full)
            self.assertEqual(g[' '], [[0] * 5 for _ in range(h)])
            for a, b in (('▀', '▄'), ('▌', '▐'), ('▘', '▟'), ('▚', '▞'), ('░', '▓')):
                self.assertEqual([[x | y for x, y in zip(p, q)] for p, q in zip(g[a], g[b])], full)
                self.assertFalse(any(x & y for p, q in zip(g[a], g[b]) for x, y in zip(p, q)))
            # text keeps column 4 free (the gap) - but M W m w X need all five
            wide = set('MWmwX©®') | set('↔▲▼◆♥♦♣♠♪∞√')
            for o in list(range(33, 127)) + list(range(161, 256)):
                ch = chr(o)
                if ch not in wide:
                    self.assertFalse(any(row[4] for row in g[ch]), ch)
            # lines run to the cell edges, so neighbours join
            self.assertTrue(g['─'][3][0] and g['─'][3][4])
            self.assertTrue(g['│'][0][2] and g['│'][h - 1][2])
            # descender and line gap: nothing of the text below row 6
            self.assertTrue(all(not any(row) for row in g['g'][7:]))

    def test_dash_is_one_bar(self):
        d = os.path.join(ROOT, 'monocell', 'fonts')
        for name in sorted(os.listdir(d)):
            if name.endswith(('.psf', '.psf.gz')):
                info = self.font(name)
                rows = [(r, v) for r, v in fix_psf_equals.glyph_rows(info, '-')
                        if fix_psf_equals.visible(info, v)]
                self.assertEqual(len(rows), 1, '%s: - has %r' % (name, rows))


if __name__ == '__main__':
    unittest.main()
