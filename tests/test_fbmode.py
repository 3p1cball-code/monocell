"""Graphics mode: framebuffer, filter graphs, palettes, OSD (no real /dev/fb0 needed)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import fbmode  # noqa: E402
from monocell.util import which  # noqa: E402

W, H = 64, 48
_CACHE = {}


def setUpModule():
    # palettes, masks and Bayer patterns go to a scratch dir, not the user's cache
    _CACHE['old'] = fbmode.CACHE_DIR
    fbmode.CACHE_DIR = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(fbmode.CACHE_DIR, ignore_errors=True)
    fbmode.CACHE_DIR = _CACHE['old']


class TestGeometry(unittest.TestCase):
    def test_canvas_is_a_fraction_of_the_screen(self):
        self.assertEqual(fbmode.canvas((1024, 768), 2), (512, 384))
        self.assertEqual(fbmode.canvas((1024, 768), 3), (341, 256))
        self.assertEqual(fbmode.canvas((1024, 768), 4), (256, 192))

    def test_fit_keeps_aspect_and_sar(self):
        self.assertEqual(fbmode.fit(1920, 1080, 1.0, 341, 256), (341, 192))
        self.assertEqual(fbmode.fit(1080, 1920, 1.0, 341, 256), (144, 256))
        # anamorphic DVD: 720x576 with 16:15 pixels shown as 4:3
        w, h = fbmode.fit(720, 576, 16 / 15, 256, 192)
        self.assertAlmostEqual(w / h, 4 / 3, delta=0.02)

    def test_read_geometry_from_sysfs(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for k, v in (('bits_per_pixel', '32'), ('stride', '4096'), ('modes', 'U:1024x768p-0'),
                     ('virtual_size', '1024,1536')):
            with open(os.path.join(d, k), 'w') as f:
                f.write(v + '\n')
        # the visible mode wins over a double-height virtual screen
        self.assertEqual(fbmode.read_geometry(d), (1024, 768, 32, 4096))


class TestFramebuffer(unittest.TestCase):
    def make(self, bpp=32, stride=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        fb = fbmode.Framebuffer(os.path.join(d, 'fb'),
                                geometry=(W, H, bpp, stride or W * bpp // 8))
        self.addCleanup(fb.close)
        return fb

    def test_pixel_formats(self):
        self.assertEqual(self.make(32).pixel((1, 2, 3)), bytes((3, 2, 1, 0)))
        self.assertEqual(self.make(24).pixel((1, 2, 3)), bytes((3, 2, 1)))
        v = (255 >> 3) << 11
        self.assertEqual(self.make(16).pixel((255, 0, 0)), bytes((v & 255, v >> 8)))
        self.assertEqual(self.make(16).pix_fmt, 'rgb565le')

    def test_padded_lines(self):
        fb = self.make(32, stride=W * 4 + 64)
        self.assertEqual(fb.line_px, W + 16)
        self.assertEqual(fb.size, (W * 4 + 64) * H)

    def test_write_and_clear(self):
        fb = self.make()
        fb.write(b'\xff' * fb.size)
        fb.clear()
        with open(fb.dev, 'rb') as f:
            self.assertEqual(f.read(), bytes(fb.size))


class TestGraphs(unittest.TestCase):
    video = {'w': 1920, 'h': 1080, 'sar': 1.0}

    def graph(self, look, **kw):
        cols = fbmode.palette_colours(look, None, (255, 138, 28))
        _, g, cv = fbmode.build(self.video, (1024, 768), 1024, 'bgr0', look, colours=cols,
                                palette='p.ppm', mask='m.pgm', pattern='b.pgm', **kw)
        return g, cv

    def test_hard_pixels(self):
        for look in fbmode.LOOK_ORDER:
            g, cv = self.graph(look)
            self.assertEqual(cv, (256, 192))                          # always a quarter
            self.assertIn('scale=1024:768:flags=neighbor', g, look)   # no smoothing on the way up
            self.assertTrue(g.endswith('format=bgr0[out]'), look)

    def test_palette_looks_use_paletteuse(self):
        for look in ('vga', 'ega', 'theme'):
            g, _ = self.graph(look)
            self.assertIn('[1:v]paletteuse=dither=bayer', g)
            self.assertNotIn('palettegen', g)          # per-frame palettes were too slow

    def test_luma_looks_dither_by_hand(self):
        for look in ('cga', 'amber', 'green'):
            g, _ = self.graph(look)
            self.assertIn('format=gray', g)
            self.assertIn('blend=all_mode=addition', g)
            self.assertIn('lutrgb=', g)
            g, _ = self.graph(look, dither='none')
            self.assertNotIn('blend', g)

    def test_scanlines(self):
        g, _ = self.graph('ega', scanlines=True)
        self.assertIn('blend=all_mode=multiply', g)
        self.assertNotIn('multiply', self.graph('ega')[0])

    def test_monitors_burn_out_to_near_white(self):
        for look in ('amber', 'green'):
            cols = fbmode.palette_colours(look)
            self.assertEqual(cols[0], (0, 0, 0))
            self.assertGreaterEqual(min(cols[-1]), 200, look)        # the highlight is nearly white
            lum = [r * 299 + g * 587 + b * 114 for r, g, b in cols]
            self.assertEqual(lum, sorted(lum), look)                  # darkest to brightest
        self.assertIn('amber', fbmode.LOOKS)
        self.assertEqual(fbmode.OLD_LOOKS['herc'], 'amber')

    def test_palette_files(self):
        path = fbmode.palette_file(fbmode.palette_colours('cga'))
        with open(path, 'rb') as f:
            data = f.read()
        self.assertTrue(data.startswith(b'P6\n16 16\n255\n'))
        self.assertEqual(len(data), len(b'P6\n16 16\n255\n') + 256 * 3)
        self.assertEqual(len(fbmode.uniform_256()), 256)

    def test_bayer_pattern_stays_below_one_step(self):
        path = fbmode.bayer_file(9, 6, 85)
        with open(path, 'rb') as f:
            data = f.read()
        px = data[len(b'P5\n9 6\n255\n'):]
        self.assertEqual(len(px), 54)
        self.assertLess(max(px), 85)
        self.assertEqual(len(set(px[:4] + px[9:13] + px[18:22] + px[27:31])), 16)

    def test_colourize_maps_levels_in_brightness_order(self):
        cols = [(0, 0, 0), (255, 85, 255), (85, 255, 255), (255, 255, 255)]
        lut = fbmode.colourize(cols)

        def ev(expr, val):
            py = expr.replace('if(', 'iff(').replace('lt(', 'lt_(')
            return eval(py, {'iff': lambda c, a, b: a if c else b,
                             'lt_': lambda a, b: a < b, 'val': val})
        r, g, b = (part.split('=', 1)[1].strip("'") for part in lut[len('lutrgb='):].split(':'))
        for level, want in zip(fbmode.grey_levels(4), cols):
            self.assertEqual((ev(r, level), ev(g, level), ev(b, level)), want)


@unittest.skipUnless(which('ffmpeg'), 'needs ffmpeg')
class TestPipeline(unittest.TestCase):
    """The real ffmpeg graph on a synthetic clip: only the adapter's colours come out."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.dir, 'clip.mkv')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=10:duration=1', '-c:v', 'ffv1', cls.clip],
                       check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def frame(self, look, scale=4, **kw):
        cmd, cv = fbmode.command(self.clip, 0.2, {'w': 320, 'h': 180, 'sar': 1.0}, (W * 4, H * 4),
                                 W * 4, 'bgr0', None, (255, 138, 28), look, scale, fps=10, **kw)
        out = subprocess.run(cmd[:-1] + ['-frames:v', '1', '-'], capture_output=True).stdout
        self.assertEqual(len(out), W * 4 * H * 4 * 4, look)
        return out, cv

    def colours(self, raw):
        return {(raw[i + 2], raw[i + 1], raw[i]) for i in range(0, len(raw), 4)}

    def test_only_adapter_colours(self):
        for look in ('ega', 'cga', 'amber', 'green'):
            raw, _ = self.frame(look)
            allowed = set(fbmode.palette_colours(look, None, (255, 138, 28)))
            got = self.colours(raw)
            self.assertTrue(got <= allowed, (look, got - allowed))
            self.assertGreater(len(got), 1, look)

    def test_vga_uses_a_256_colour_palette(self):
        raw, _ = self.frame('vga')
        got = self.colours(raw)
        self.assertLessEqual(len(got), 256)
        self.assertTrue(got <= set(fbmode.uniform_256()) | {(0, 0, 0)})

    def test_pixels_are_blocks(self):
        raw, (cw, ch) = self.frame('ega', scale=4)
        line = W * 4 * 4
        # inside the picture every 4x4 block is one colour
        for by in range(4, ch - 4, 7):
            for bx in range(4, cw - 4, 9):
                px = {raw[(by * 4 + y) * line + (bx * 4 + x) * 4:][:3]
                      for y in range(4) for x in range(4)}
                self.assertEqual(len(px), 1, (bx, by))

    def test_tuned_vga_palette(self):
        pal = fbmode.make_video_palette(self.clip)
        self.assertTrue(pal and os.path.exists(pal))
        self.assertTrue(pal.startswith(fbmode.CACHE_DIR))
        self.assertEqual(fbmode.make_video_palette(self.clip), pal)   # cached
        raw, _ = self.frame('vga', vga_palette=pal)
        self.assertLessEqual(len(self.colours(raw)), 257)


class TestSession(unittest.TestCase):
    def make(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        fb = fbmode.Framebuffer(os.path.join(d, 'fb'), geometry=(320, 240, 32, 320 * 4))
        active = os.path.join(d, 'active')
        with open(active, 'w') as f:
            f.write('tty1\n')
        s = fbmode.Session(-1, fb, active_path=active)
        s.tty = 'tty1'
        self.addCleanup(s.close)
        return s, fb, active

    def read(self, fb):
        with open(fb.dev, 'rb') as f:
            return f.read()

    def test_osd_only_touches_its_rows(self):
        s, fb, _ = self.make()
        frame = bytearray(b'\x10' * fb.size)
        s.show(frame)
        self.assertEqual(self.read(fb), bytes(frame))
        s.poke()
        s.tick(0, 61, 3600, False)
        out = self.read(fb)
        y0 = 240 - 9 * fbmode.SCALE
        self.assertEqual(out[:y0 * fb.stride], bytes(frame[:y0 * fb.stride]))
        self.assertNotEqual(out[y0 * fb.stride:], bytes(frame[y0 * fb.stride:]))

    def test_overlay_is_written_together_with_the_frame(self):
        # frame first and OSD afterwards would show the bare frame in between: flicker
        s, fb, _ = self.make()
        writes = []
        real = fb.write
        fb.write = lambda data, offset=0: (writes.append((offset, len(data))), real(data, offset))
        s.note(['VGA 256'], 10)
        s.poke(10)
        s.tick(0, 5, 60, False)
        for i in range(3):
            writes.clear()
            frame = bytearray(bytes([i + 1]) * fb.size)
            s.show(frame)
            self.assertEqual(writes, [(0, fb.size)])                  # one full write per frame
            out = self.read(fb)
            # the video stays untouched where there is no OSD, the frame object too
            mid = (120 * fb.stride) + 160 * 4
            self.assertEqual(out[mid:mid + 4], bytes([i + 1]) * 4)
            self.assertEqual(frame, bytes([i + 1]) * fb.size)
        # the OSD is drawn once and reused while nothing on it changes
        first = s.osd_cache
        s.show(bytearray(fb.size))
        self.assertIs(s.osd_cache, first)

    def test_no_drawing_while_another_vt_is_shown(self):
        s, fb, active = self.make()
        with open(active, 'w') as f:
            f.write('tty2\n')
        s.show(bytearray(b'\x22' * fb.size))
        self.assertEqual(self.read(fb), b'')                 # untouched (fresh file)
        with open(active, 'w') as f:
            f.write('tty1\n')
        s.tick(0, 0, 0, False)                               # back on our VT: repaint
        self.assertEqual(self.read(fb), b'\x22' * fb.size)

    def test_font_covers_every_osd_text(self):
        texts = ['G EXIT  C LOOK  D DITHER  S SCANLINES', 'TUNING THE PALETTE...',
                 'PALETTE TUNED TO THIS VIDEO', 'TOO SLOW - DOWN TO 15 FPS', 'VOLUME 80%',
                 '1:02:03 / 4:05:06', '341X256  BAYER  NONE  25 FPS  SCANLINES']
        texts += [l[0] for l in fbmode.LOOKS.values()] + [l[2].upper() for l in fbmode.LOOKS.values()]
        for t in texts:
            missing = {c for c in t if c not in fbmode.FONT}
            self.assertEqual(missing, set(), t)

    def test_clock(self):
        self.assertEqual(fbmode.clock(65), '1:05')
        self.assertEqual(fbmode.clock(3725), '1:02:05')


if __name__ == '__main__':
    unittest.main()
