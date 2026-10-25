"""Fn keys and Win+E arrive as evdev key codes, not as characters on the tty."""
import struct
import unittest

from monocell.hotkeys import EVENT, Hotkeys


def ev(kind, code, value):
    return EVENT.pack(0, 0, kind, code, value)


class FakeHotkeys(Hotkeys):
    """Hotkeys without devices: events are fed in by hand."""

    def __init__(self):
        self.loop = None
        self.fds = {}
        self.last = {}
        self.meta = {}
        self.meta_at = {}
        self.meta_used = {}
        self.got = []
        self.callback = self.got.append


class TestHotkeys(unittest.TestCase):
    def test_meta_e_opens_the_file_manager(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1) + ev(1, 18, 1))
        self.assertEqual(h.got, ['files'])

    def test_meta_e_only_on_press(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1) + ev(1, 18, 1) + ev(1, 18, 2) + ev(1, 18, 0))
        self.assertEqual(h.got, ['files'])

    def test_plain_e_is_left_to_the_terminal(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 18, 1) + ev(1, 18, 0))
        self.assertEqual(h.got, [])

    def test_meta_on_another_device_does_not_count(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1))
        h.feed(2, ev(1, 18, 1))
        self.assertEqual(h.got, [])

    def test_released_meta_stops_the_combo(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1) + ev(1, 125, 0) + ev(1, 18, 1))
        # the tap opens the start menu, the later E is a plain key press
        self.assertEqual(h.got, ['startmenu'])

    def test_windows_key_tap_opens_the_start_menu(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1) + ev(1, 125, 0))
        self.assertEqual(h.got, ['startmenu'])

    def test_windows_key_with_another_key_does_not_open_the_menu(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 125, 1) + ev(1, 18, 1) + ev(1, 125, 0))
        self.assertEqual(h.got, ['files'])

    def test_function_keys_still_work(self):
        h = FakeHotkeys()
        h.feed(1, ev(1, 115, 1) + ev(1, 114, 1) + ev(1, 113, 1) + ev(1, 238, 1))
        self.assertEqual(h.got, ['volume-up', 'volume-down', 'mute', 'wifi'])

    def test_other_event_types_are_ignored(self):
        h = FakeHotkeys()
        h.feed(1, struct.pack('llHHi', 0, 0, 2, 18, 1))     # EV_REL
        h.feed(1, struct.pack('llHHi', 0, 0, 0, 0, 0))      # EV_SYN
        self.assertEqual(h.got, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
