"""Battery warnings, suspend at the end, and the lock-screen hook."""
import sys
import os
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.wm import WM  # noqa: E402


class Stub:
    def __init__(self):
        self.cfg = {'battery_suspend': True}
        self.toasts = []
        self.later_cbs = []
        self.powered = []
        self.term = mock.Mock()
        self.dirty = False
        self.resized = 0

    def toast(self, text, colour='accent', secs=3.5):
        self.toasts.append((text, colour))

    def later(self, delay, cb):
        self.later_cbs.append(cb)

    def power(self, action):
        self.powered.append(action)

    def on_resize(self):
        self.resized += 1


class TestBattery(unittest.TestCase):
    def setUp(self):
        self.wm = Stub()

    def warn(self, cap, status='Discharging'):
        WM.check_battery(self.wm, cap, status)

    def test_no_warning_while_high(self):
        self.warn(80)
        self.assertEqual(self.wm.toasts, [])

    def test_warns_once_per_level(self):
        for cap in (19, 18, 17, 9, 8, 4, 4):
            self.warn(cap)
        texts = [t for t, _c in self.wm.toasts]
        self.assertEqual(len(texts), 3)
        self.assertIn('19%', texts[0])
        self.assertIn('9%', texts[1])
        self.assertIn('4%', texts[2])

    def test_charging_resets_the_levels(self):
        self.warn(19)
        self.warn(80, 'Charging')
        self.warn(19)
        self.assertEqual(len(self.wm.toasts), 2)

    def test_critical_battery_suspends(self):
        self.warn(3)
        self.assertEqual(self.wm.powered, [])
        self.assertEqual(len(self.wm.later_cbs), 1)
        self.wm.later_cbs[0]()
        self.assertEqual(self.wm.powered, ['suspend'])
        self.assertEqual(self.wm.toasts[0][1], 'red')

    def test_suspend_can_be_switched_off(self):
        self.wm.cfg['battery_suspend'] = False
        self.warn(3)
        self.assertEqual(self.wm.later_cbs, [])
        self.assertTrue(self.wm.toasts)


class TestLock(unittest.TestCase):
    def test_without_vlock_it_explains(self):
        wm = Stub()
        with mock.patch('shutil.which', return_value=None):
            WM.lock_screen(wm)
        self.assertIn('vlock', wm.toasts[0][0])
        wm.term.stop.assert_not_called()

    def test_vlock_takes_and_returns_the_console(self):
        wm = Stub()
        with mock.patch('shutil.which', return_value='/usr/bin/vlock'), \
                mock.patch('subprocess.run') as run:
            WM.lock_screen(wm)
        wm.term.stop.assert_called_once()
        wm.term.start.assert_called_once()
        self.assertEqual(run.call_args[0][0], ['/usr/bin/vlock', '-a'])
        self.assertEqual(wm.resized, 1)

    def test_start_menu_has_lock_and_suspend(self):
        from monocell.wm import StartMenu
        from monocell.widgets import TextField
        menu = StartMenu.__new__(StartMenu)
        menu.search = TextField()
        labels = [r[2] for r in menu.all_rows() if r[0] == 'foot']
        self.assertIn('Lock screen', labels)
        self.assertIn('Suspend', labels)


if __name__ == '__main__':
    unittest.main()
