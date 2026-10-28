"""Picture viewer in graphics mode: the canvas geometry (fit, zoom, pan), without a screen."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image as PImage  # noqa: E402

from monocell import imaging  # noqa: E402
from monocell.apps.viewer import Viewer  # noqa: E402

W, H = 256, 192          # the canvas on a 1024x768 screen


class FakeCfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def save(self):
        pass


class FakeLoop:
    def every(self, *a, **kw):
        return None


class FakeTheme:
    rgb = [(0, 0, 0)] * 16
    r = {'accent': 1, 'dim': 2, 'shadow': 0, 'bright': 15, 'field': 3}


class FakeWM:
    def __init__(self):
        self.cfg = FakeCfg(gfx_look='vga', gfx_dither='bayer', gfx_scanlines=False)
        self.theme = FakeTheme()
        self.loop = FakeLoop()


class FakeSession:
    screen = (1024, 768)

    def note(self, *a, **kw):
        pass


def solid(w, h, colour=(200, 100, 50)):
    return imaging.Image(w, h, pil=PImage.new('RGB', (w, h), colour))


def viewer(img, cfg=None):
    v = Viewer(FakeWM(), None)
    if cfg:
        v.wm.cfg.update(cfg)
    v.img = img
    v.gfx = FakeSession()
    return v


class TestCanvas(unittest.TestCase):
    def test_no_image_is_black(self):
        img = viewer(None).gfx_picture()
        self.assertEqual(img.size, (W, H))
        self.assertEqual(img.getpixel((10, 10)), (0, 0, 0))

    def test_landscape_is_letterboxed(self):
        img = viewer(solid(1000, 500, (10, 40, 90))).gfx_picture()
        self.assertEqual(img.size, (W, H))
        self.assertEqual(img.getpixel((W // 2, 8)), (0, 0, 0))          # above
        self.assertEqual(img.getpixel((W // 2, H // 2)), (10, 40, 90))

    def test_portrait_is_pillarboxed(self):
        img = viewer(solid(500, 1000, (90, 10, 40))).gfx_picture()
        self.assertEqual(img.getpixel((8, H // 2)), (0, 0, 0))          # left
        self.assertEqual(img.getpixel((W // 2, H // 2)), (90, 10, 40))

    def test_aspect_is_kept(self):
        img = viewer(solid(1000, 500, (1, 2, 3))).gfx_picture()
        top = min(y for y in range(H) if img.getpixel((W // 2, y)) != (0, 0, 0))
        bottom = max(y for y in range(H) if img.getpixel((W // 2, y)) != (0, 0, 0))
        self.assertAlmostEqual(bottom - top + 1, W // 2, delta=2)       # 2:1 in a 4:3 box
        self.assertAlmostEqual(top, (H - (W // 2)) / 2, delta=2)

    def test_zoom_fills_the_canvas(self):
        v = viewer(solid(1000, 500, (7, 7, 7)))
        v.gzoom = 2.0
        img = v.gfx_picture()
        for y in (0, H // 2, H - 1):
            self.assertEqual(img.getpixel((W // 2, y)), (7, 7, 7), y)

    def test_pan_keeps_the_picture_centred_at_zero(self):
        v = viewer(solid(1000, 500, (5, 5, 5)))
        v.gzoom = 4.0
        v.gpanx = 0
        v.gpany = 0
        v.gfx_picture()
        self.assertEqual((v.gpanx, v.gpany), (0, 0))

    def test_pan_is_limited_to_the_edges(self):
        v = viewer(solid(1000, 500, (5, 5, 5)))
        v.gzoom = 4.0
        v.gpanx = 10_000
        v.gpany = -10_000
        v.gfx_picture()
        # 1000x500 fitted to 256x128, at 4x: 1024x512 -> half of the overflow each way
        self.assertAlmostEqual(v.gpanx, (1024 - W) / 2.0, delta=1)
        self.assertAlmostEqual(v.gpany, -(512 - H) / 2.0, delta=1)

    def test_zoom_out_drops_the_pan(self):
        v = viewer(solid(1000, 500))
        v.gzoom = 3.0
        v.gpanx = v.gpany = 50
        v.gfx_picture()
        v.gzoom = 1.0
        v.gfx_picture()
        self.assertEqual((v.gpanx, v.gpany), (0, 0))

    def test_pan_limits_follow_the_zoomed_size(self):
        v = viewer(solid(500, 1000, (9, 9, 9)))
        v.gzoom = 3.0
        v.gpanx = v.gpany = 10_000
        v.gfx_picture()
        # 500x1000 fitted to 96x192, at 3x: 288x576 -> 16 px each side, 192 vertical
        self.assertAlmostEqual(v.gpanx, (288 - W) / 2.0, delta=1)
        self.assertAlmostEqual(v.gpany, (576 - H) / 2.0, delta=1)

    def test_missing_pil_data_falls_back_to_rgb_bytes(self):
        data = bytes((1, 2, 3)) * (80 * 40)
        img = imaging.Image(80, 40, data=data)
        out = viewer(img).gfx_picture()
        self.assertEqual(out.getpixel((W // 2, H // 2)), (1, 2, 3))


if __name__ == '__main__':
    unittest.main()
