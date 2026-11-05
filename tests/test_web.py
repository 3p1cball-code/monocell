"""Browser graphics mode without a browser: key mapping, mouse mapping, hotkeys."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import web  # noqa: E402
from monocell.apps import webgfx  # noqa: E402


class TestKeys(unittest.TestCase):
    def test_split_key_keeps_plus_and_letter(self):
        self.assertEqual(web.split_key('ctrl+shift+left'), ({'ctrl', 'shift'}, 'left'))
        self.assertEqual(web.split_key('+'), (set(), '+'))
        self.assertEqual(web.split_key('ctrl++'), ({'ctrl'}, '+'))
        self.assertEqual(web.split_key('g'), (set(), 'g'))
        self.assertEqual(web.split_key('alt+ä'), ({'alt'}, 'ä'))

    def test_letter_key_types_and_sets_code(self):
        down, up = web.key_events('a')
        self.assertEqual(down['type'], 'keyDown')
        self.assertEqual((down['key'], down['text'], down['code']), ('a', 'a', 'KeyA'))
        self.assertEqual(down['windowsVirtualKeyCode'], 65)
        self.assertEqual((down['modifiers'], up['type']), (0, 'keyUp'))

    def test_shift_types_a_capital(self):
        down, _ = web.key_events('shift+a')
        self.assertEqual((down['key'], down['text'], down['modifiers']), ('A', 'A', 8))

    def test_shortcut_types_nothing(self):
        down, _ = web.key_events('ctrl+a')
        self.assertEqual(down['type'], 'rawKeyDown')       # the page sees a shortcut
        self.assertNotIn('text', down)
        self.assertEqual(down['modifiers'], 2)

    def test_special_keys_and_unknown(self):
        down, up = web.key_events('enter')
        self.assertEqual((down['key'], down['text'], down['windowsVirtualKeyCode']),
                         ('Enter', '\r', 13))
        self.assertEqual(web.key_events('pgdn')[0]['key'], 'PageDown')
        self.assertEqual(web.key_events('f5')[0]['windowsVirtualKeyCode'], 116)
        self.assertEqual(web.key_events('ctrl+alt+delete')[0]['key'], 'Delete')
        self.assertEqual(web.key_events('nonsense'), [])

    def test_umlaut_has_no_code_but_types(self):
        down, _ = web.key_events('ä')
        self.assertEqual((down['text'], down['windowsVirtualKeyCode']), ('ä', 0))


class TestGeometry(unittest.TestCase):
    def test_page_sits_above_the_bar(self):
        self.assertEqual(webgfx.page_size((1024, 768)), (1024, 768 - webgfx.HUD_H))

    def test_mouse_cells_become_pixels(self):
        screen = (1024, 768)
        self.assertEqual(webgfx.to_screen(0, 0, 113, 48, screen), (0.0, 0.0))
        x, y = webgfx.to_screen(56.5, 24, 113, 48, screen)
        self.assertAlmostEqual(x, 512.0, places=1)
        self.assertAlmostEqual(y, 384.0, places=1)
        self.assertEqual(webgfx.to_screen(999, 999, 113, 48, screen), (1023.0, 767.0))

    def test_pointer_is_a_block(self):
        self.assertEqual(len(webgfx.POINTER_RUNS), len(webgfx.POINTER))
        self.assertEqual(max(len(r) for r in webgfx.POINTER_RUNS), webgfx.POINTER_W)
        for runs in webgfx.POINTER_RUNS:
            self.assertTrue(runs)


class TestHotkeys(unittest.TestCase):
    def test_ctrl_keys_are_always_ours(self):
        for k in ('ctrl+g', 'ctrl+o', 'ctrl+l', 'ctrl+r', 'ctrl+v', 'ctrl+c',
                  'alt+left', 'alt+right', 'alt+c', 'f5', 'esc'):
            self.assertTrue(webgfx.is_hotkey(k, True), k)
            self.assertTrue(webgfx.is_hotkey(k, False), k)

    def test_letters_only_outside_fields(self):
        for k in ('g', 'o', 'l', 'r', 'c', 'd', 's', 'backspace'):
            self.assertTrue(webgfx.is_hotkey(k, False), k)
            self.assertFalse(webgfx.is_hotkey(k, True), k)

    def test_typing_goes_to_the_page(self):
        for k in ('a', 'space', 'up', 'tab', 'enter', 'delete', 'home', 'x'):
            self.assertFalse(webgfx.is_hotkey(k, False), k)
            self.assertFalse(webgfx.is_hotkey(k, True), k)


if __name__ == '__main__':
    unittest.main()
