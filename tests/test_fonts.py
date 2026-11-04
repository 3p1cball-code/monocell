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
