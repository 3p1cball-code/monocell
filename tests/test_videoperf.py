"""Video/image speed paths must not change what is on screen.

- imaging: the numpy renderers give exactly the cells of the pure-python loops
- term.flush: short SGR deltas and C-level diffing leave the terminal in the
  same state as writing every cell with its full SGR
"""
import os
import random
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

from monocell import imaging  # noqa: E402
from monocell.screen import Buffer  # noqa: E402
from monocell.term import Terminal  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402
from monocell.vt import VTScreen  # noqa: E402


class _NS:
    pass


def picture(w, h, seed=1):
    rnd = random.Random(seed)
    rgb = bytearray()
    for y in range(h):
        for x in range(w):
            rgb += bytes([(x * 4 + rnd.randrange(40)) % 256, (y * 11 + rnd.randrange(60)) % 256,
                          (x * y + rnd.randrange(255)) % 256])
    rgb[:30] = b'\0' * 30
    rgb[30:60] = b'\xff' * 30
    return bytes(rgb)


def theme():
    os.environ.setdefault('XDG_CONFIG_HOME', '/nonexistent-monocell-test')
    return Theme(Config())


class TestNumpyRender(unittest.TestCase):
    def setUp(self):
        if imaging.numpy() is None:
            self.skipTest('numpy not installed')

    def tearDown(self):
        os.environ.pop('MONOCELL_NO_NUMPY', None)
        imaging._np = None

    def render(self, wm, rgb, w, h, mode, dither, use_np):
        if use_np:
            os.environ.pop('MONOCELL_NO_NUMPY', None)
        else:
            os.environ['MONOCELL_NO_NUMPY'] = '1'
        imaging._np = None
        imaging._renderer = None
        imaging._grey = None
        return imaging.render(wm, rgb, w, h, mode, dither)

    def test_same_cells_as_the_python_loops(self):
        w, h = 61, 23
        rgb = picture(w, h)
        th = theme()
        for allowed in (list(range(16)), [0, 1, 2, 3, 8, 9, 10, 11]):
            for mode in ('ascii', 'mono', 'blend', 'pixel', 'grey'):
                for dither in ('bayer4', 'bayer8', 'none', 'fs'):
                    for cs in ('full', 'simple', 'shades'):
                        wm = _NS()
                        wm.theme, wm.term, wm.cfg = th, _NS(), {'ascii_charset': cs}
                        wm.term.allowed_bg = allowed
                        a = self.render(wm, rgb, w, h, mode, dither, False)
                        b = self.render(wm, rgb, w, h, mode, dither, True)
                        self.assertEqual(a, b, (allowed, mode, dither, cs))

    def test_nearest_is_deterministic(self):
        R = imaging.Renderer(list(theme().rgb), list(range(16)), 6, 0)
        first = R.nearest(1, 130, 250)
        R2 = imaging.Renderer(list(theme().rgb), list(range(16)), 6, 0)
        R2.nearest(2, 129, 251)            # same bucket, other colour first
        self.assertEqual(R2.nearest(1, 130, 250), first)


class TestFlush(unittest.TestCase):
    def term(self, mode, w, h):
        null = os.open(os.devnull, os.O_WRONLY)
        self.addCleanup(os.close, null)
        t = Terminal(theme(), mode, fd_in=null, fd_out=null)
        t.w, t.h = w, h
        t.back = Buffer(w, h)
        t.front = None
        t.apply_palette()
        sent = []
        t.write = sent.append
        return t, sent

    def check(self, mode):
        w, h = 37, 9
        t, sent = self.term(mode, w, h)
        rnd = random.Random(mode)
        got = VTScreen(w, h, reply=lambda s: None)
        want = VTScreen(w, h, reply=lambda s: None)
        flags = [0, 0, 0, 1, 2] if mode != 'linux' else [0]
        for frame in range(40):
            # some frames change a little (UI), some nearly everything (video)
            churn = rnd.choice((0.02, 0.3, 1.0))
            for y in range(h):
                for x in range(w):
                    if rnd.random() < churn:
                        t.back.ch[y][x] = rnd.choice('ab .#%@')
                        t.back.at[y][x] = rnd.randrange(16) | (rnd.randrange(16) << 4) | \
                            (rnd.choice(flags) << 8)
            del sent[:]
            t.flush()
            got.feed(''.join(sent))
            ref = ['\x1b[H']
            for y in range(h):
                ref.append('\x1b[%d;1H' % (y + 1))
                for x in range(w):
                    ref.append(t.sgr(t.back.at[y][x]) + t.back.ch[y][x])
            want.feed(''.join(ref))
            self.assertEqual(got.lines, want.lines, '%s frame %d' % (mode, frame))

    def test_linux(self):
        self.check('linux')

    def test_palette(self):
        self.check('palette')

    def test_truecolor(self):
        self.check('truecolor')

    def test_deltas_are_short(self):
        t, sent = self.term('linux', 4, 1)
        a = 3 | (0 << 4)
        b = 5 | (0 << 4)
        full = t.sgr(b)
        delta = t.sgr_to(a, b)
        self.assertLess(len(delta), len(full))
        self.assertEqual(t.sgr_to(a, a), '')


if __name__ == '__main__':
    unittest.main()
