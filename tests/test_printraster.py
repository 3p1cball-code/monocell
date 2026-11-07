"""Raster for bit-image printing: text, pictures, dithering - what F6 shows."""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import printraster, richdoc  # noqa: E402
from monocell.printer import Sheet  # noqa: E402
from monocell.screen import BOLD  # noqa: E402


class WM:
    class theme:
        rgb = [(0, 0, 0)] * 16

        @staticmethod
        def c(fg, bg, fl=0):
            return fl
    cfg = {'image_mode': 'ascii', 'dither': 'bayer4', 'ascii_charset': 'full',
           'cell_aspect': 0, 'font': 'Lat15-3270.psf.gz'}
    term = types.SimpleNamespace(allowed_bg=list(range(8)), cell_aspect=1.67, mode='linux')


def sheet(page, rows):
    sheet = Sheet(1, rows)
    return sheet


class TestRaster(unittest.TestCase):
    def setUp(self):
        self.pg = richdoc.Page({'paper': 'a4', 'cpi': '10', 'lpi': '6'})
        self.r = printraster.Raster(self.pg)
        self.blank = (' ' * self.pg.cols, [0] * self.pg.cols)

    def test_size_is_180_dpi(self):
        self.assertEqual(self.r.width, self.pg.cols * 18)
        self.assertEqual(self.r.height, int(round(self.pg.lines * 30)))

    def test_text_becomes_ink(self):
        rows = [self.blank] * self.pg.lines
        rows[0] = (('A' * self.pg.cols).ljust(self.pg.cols), [0] * self.pg.cols)
        img = self.r.render(sheet(self.pg, rows))
        top = img.crop((0, 0, 18 * 5, 30)).tobytes()
        self.assertGreater(sum(1 for p in top if p), 40)             # the letter A
        below = img.crop((0, 40, 18 * 5, 60)).tobytes()
        self.assertEqual(sum(1 for p in below if p), 0)

    def test_bold_prints_wider(self):
        rows = [self.blank] * 3
        rows[0] = ('MMMM' + ' ' * (self.pg.cols - 4), [0] * self.pg.cols)
        rows[1] = ('MMMM' + ' ' * (self.pg.cols - 4), [(BOLD << 8)] * self.pg.cols)
        img = self.r.render(sheet(self.pg, rows), 0, 3)
        a = img.crop((0, 0, 18 * 4, 30)).tobytes()
        b = img.crop((0, 30, 18 * 4, 60)).tobytes()
        self.assertGreater(sum(1 for p in b if p), sum(1 for p in a if p))

    def test_dot_rows_width(self):
        rows = [self.blank] * self.pg.lines
        dots = self.r.dot_rows(sheet(self.pg, rows), 0, 1)
        self.assertTrue(dots)
        self.assertEqual(len(dots[0]), self.r.width)

    def test_picture_area_is_dither_noise(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'g.png')
            Image.linear_gradient('L').resize((60, 40)).save(p)
            from monocell import imaging
            img = imaging.load(p)
            sh = sheet(self.pg, [self.blank] * self.pg.lines)
            sh.gfx = [(2, 2, 10, 5, img)]
            out = self.r.render(sh, 0, 10)
            area = out.crop((2 * 18, 2 * 30, 12 * 18, 7 * 30))
            vals = [p for p in area.tobytes() if p]
            self.assertGreater(len(vals), 100)          # ink
            self.assertLess(len(vals), area.size[0] * area.size[1])   # but not solid

    def test_test_patterns(self):
        sh = sheet(self.pg, [self.blank] * self.pg.lines)
        sh.gfx = [(0, 0, 20, 4, 'frame'), (0, 10, 30, 3, 'ramp')]
        img = self.r.render(sh, 0, 20)
        self.assertGreater(sum(1 for p in img.crop((0, 0, 360, 120)).tobytes() if p), 100)
