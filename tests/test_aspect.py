"""Cell aspect: the console font decides, so video and pictures stay undistorted."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import imaging, theme  # noqa: E402
from monocell.term import Terminal  # noqa: E402


def make_term(font):
    cfg = theme.Config()
    cfg['font'] = font
    th = theme.Theme(cfg)
    r, w = os.pipe()
    try:
        return Terminal(th, 'linux', fd_in=r, fd_out=w), w
    finally:
        os.close(r)


class TestCellAspect(unittest.TestCase):
    def test_vt_uses_the_console_font_cell(self):
        t, w = make_term('Lat15-3270.psf.gz')
        self.assertAlmostEqual(t.cell_aspect, 16 / 9)
        os.close(w)
        t, w = make_term('Lat15-Fixed16.psf.gz')
        self.assertAlmostEqual(t.cell_aspect, 2.0)
        os.close(w)

    def test_config_override_wins(self):
        class WM:
            cfg = {'cell_aspect': 1.5}
            term = type('T', (), {'cell_aspect': 16 / 9})()
        self.assertEqual(imaging.cell_aspect(WM), 1.5)
        WM.cfg = {'cell_aspect': 0}
        self.assertAlmostEqual(imaging.cell_aspect(WM), 16 / 9)

    def test_4_by_3_fills_a_4_by_3_screen(self):
        # 113x48 cells at 9x16 px = 1017x768, 4:3. Without the font aspect the
        # picture was fitted to 113x42 and got bars.
        self.assertEqual(imaging.fit_size(640, 480, 113, 48, 16 / 9), (113, 48))
        self.assertEqual(imaging.fit_size(640, 480, 113, 48, 2.0), (113, 42))
        # a 16:9 film stays undistorted and gets bars (113x36 at 4:3)
        self.assertEqual(imaging.fit_size(1920, 1080, 113, 48, 16 / 9), (113, 36))
        self.assertEqual(imaging.fit_size(640, 480, 128, 48, 2.0), (128, 48))


if __name__ == '__main__':
    unittest.main()
