"""Desktop cone for the ThinkLight LED in the X61s lid."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell.screen import Buffer, Canvas  # noqa: E402
from monocell import theme  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402
from monocell.wm import WM  # noqa: E402


class Stub(WM):
    """Only the bits poll_thinklight/draw_thinklight touch (no real WM init)."""

    def __init__(self, w=80, h=24):
        self.cfg = Config()
        self.theme = Theme(self.cfg)
        self.w, self.h = w, h
        self.desk_y, self.desk_h = 0, h - 1
        self.dirty = False
        self.thinklight = False
        self.thinklight_led = None
        self.panel_cm = (25, 18)   # the X61s panel


class TestThinkLight(unittest.TestCase):
    def setUp(self):
        fd, self.led = tempfile.mkstemp()
        os.close(fd)
        self.set_led('0')
        self.wm = Stub()

    def tearDown(self):
        os.unlink(self.led)

    def set_led(self, value):
        with open(self.led, 'w') as f:
            f.write(value)

    def test_follows_the_led_and_only_repaints_on_change(self):
        self.wm.thinklight_led = self.led
        WM.poll_thinklight(self.wm)
        self.assertFalse(self.wm.thinklight)
        self.assertFalse(self.wm.dirty)
        self.set_led('255\n')
        WM.poll_thinklight(self.wm)
        self.assertTrue(self.wm.thinklight)
        self.assertTrue(self.wm.dirty)
        self.wm.dirty = False
        WM.poll_thinklight(self.wm)          # still on: nothing to do
        self.assertFalse(self.wm.dirty)
        self.set_led('0')
        WM.poll_thinklight(self.wm)
        self.assertFalse(self.wm.thinklight)
        self.assertTrue(self.wm.dirty)

    def test_without_a_led_nothing_happens(self):
        WM.poll_thinklight(self.wm)
        self.assertFalse(self.wm.thinklight)
        self.assertFalse(self.wm.dirty)

    def test_led_that_disappears_ends_the_watch(self):
        self.wm.thinklight_led = self.led + '.gone'
        WM.poll_thinklight(self.wm)
        self.assertIsNone(self.wm.thinklight_led)
        self.assertFalse(self.wm.thinklight)

    def test_cone_appears_four_centimetres_from_the_left_only_while_on(self):
        buf = Buffer(self.wm.w, self.wm.h)
        WM.draw_thinklight(self.wm, Canvas(buf))
        self.assertEqual(''.join(buf.ch[0]).strip(), '')

        self.wm.thinklight = True
        WM.draw_thinklight(self.wm, Canvas(buf))
        cw = len(WM.THINKLIGHT_CONES['medium'][0])
        x = round(4 * self.wm.w / 25) - 3
        self.assertEqual(x, 10)                      # 80 cells on 25 cm, less 3
        rows = [''.join(r[x:x + cw]) for r in buf.ch[:len(WM.THINKLIGHT_CONES['medium'])]]
        self.assertEqual(rows, list(WM.THINKLIGHT_CONES['medium']))

    def test_spaces_between_the_rays_keep_the_wallpaper(self):
        self.wm.thinklight = True
        buf = Buffer(self.wm.w, self.wm.h)
        Canvas(buf).fill(0, 0, self.wm.w, self.wm.h, 0x1f, 'x')
        WM.draw_thinklight(self.wm, Canvas(buf))
        x = WM.thinklight_x(self.wm)
        self.assertEqual(buf.ch[1][x], 'x')          # left of the '/'
        self.assertEqual(buf.ch[1][x + 3], '/')
        self.assertEqual(buf.ch[4][x + 1], 'x')      # between '.', '|' and '.'

    def test_position_is_the_same_distance_for_every_font(self):
        # 4 cm on the 25 cm X61s panel, three cells further in: 24 columns in
        # Terminus 6x12 (170), 30 in minimal-cell 5x7 (204), 15 in 3270 (113)
        for w, cells in ((170, 24), (204, 30), (113, 15)):
            wm = Stub(w)
            self.assertEqual(WM.thinklight_x(wm), cells, w)
        wm = Stub(170)
        wm.panel_cm = None                           # no EDID: the same fraction
        self.assertEqual(WM.thinklight_x(wm), 24)

    def test_size_is_a_setting(self):
        wm = Stub(170)
        wm.thinklight = True
        stars = set()
        for size, width, height in (('tiny', 3, 2), ('mini', 5, 3),
                                    ('small', 7, 4), ('medium', 9, 5),
                                    ('large', 13, 7)):
            wm.cfg['thinklight_size'] = size
            rows = WM.thinklight_rows(wm)
            self.assertEqual((len(rows[0]), len(rows)), (width, height), size)
            buf = Buffer(wm.w, wm.h)
            WM.draw_thinklight(wm, Canvas(buf))
            x = WM.thinklight_x(wm, rows)
            self.assertEqual([''.join(r[x:x + width])
                              for r in buf.ch[:height]], list(rows), size)
            stars.add(''.join(buf.ch[0]).index('*'))
        self.assertEqual(stars, {28})       # the lamp does not move with the size

    def test_can_be_switched_off_on_the_desktop_page(self):
        from monocell.apps import settings
        self.wm.cfg['thinklight'] = False
        self.wm.thinklight = True
        buf = Buffer(self.wm.w, self.wm.h)
        WM.draw_thinklight(self.wm, Canvas(buf))
        self.assertEqual(''.join(''.join(r) for r in buf.ch).strip(), '')
        desktop = [o[0] for o in settings.OPTIONS['desktop']]
        self.assertIn('thinklight', desktop)
        self.assertIn('thinklight_size', desktop)
        keys = theme.DEFAULT_CONFIG
        self.assertTrue(keys['thinklight'])
        self.assertEqual(keys['thinklight_size'], 'medium')

    def test_panel_size_comes_from_the_built_in_edid(self):
        from monocell import hardware
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, 'card0-VGA-1'))
            open(os.path.join(root, 'card0-VGA-1', 'edid'), 'wb').close()
            os.makedirs(os.path.join(root, 'card0-eDP-1'))
            edid = bytearray(128)
            edid[:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'
            edid[21], edid[22] = 30, 19
            with open(os.path.join(root, 'card0-eDP-1', 'edid'), 'wb') as f:
                f.write(edid)
            self.assertEqual(hardware.panel_size_cm(root), (30, 19))
            self.assertIsNone(hardware.panel_size_cm(os.path.join(root, 'nope')))
        finally:
            import shutil
            shutil.rmtree(root)
        with mock.patch.dict(os.environ, {'MONOCELL_FAKE_PANEL_CM': '25x18'}):
            self.assertEqual(hardware.panel_size_cm(), (25.0, 18.0))

    def test_tiny_screens_skip_the_cone(self):
        for w, h in ((10, 24), (80, 4)):
            wm = Stub(w, h)
            wm.thinklight = True
            buf = Buffer(w, h)
            WM.draw_thinklight(wm, Canvas(buf))
            self.assertEqual(''.join(''.join(r) for r in buf.ch).strip(), '')

    def test_led_is_found_or_faked(self):
        with mock.patch.dict(os.environ, {'MONOCELL_FAKE_THINKLIGHT': self.led}):
            self.assertEqual(WM.find_thinklight(self.wm), self.led)
        with mock.patch.dict(os.environ, {'MONOCELL_FAKE_THINKLIGHT': ''}):
            self.assertIsNone(WM.find_thinklight(self.wm))
        env = {k: v for k, v in os.environ.items() if k != 'MONOCELL_FAKE_THINKLIGHT'}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('monocell.wm.glob.glob',
                           return_value=['/sys/class/leds/tpacpi::thinklight/brightness']):
            self.assertEqual(WM.find_thinklight(self.wm),
                             '/sys/class/leds/tpacpi::thinklight/brightness')
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('monocell.wm.glob.glob', return_value=[]):
            self.assertIsNone(WM.find_thinklight(self.wm))


if __name__ == '__main__':
    unittest.main()
