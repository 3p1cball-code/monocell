"""Start menu: categories open a submenu to the right, typing searches flat."""
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.app import REGISTRY  # noqa: E402
from monocell.wm import StartMenu  # noqa: E402


def key(name, char=None):
    return types.SimpleNamespace(key=name, char=char or '')


def mouse(kind, x, y, button=0):
    return types.SimpleNamespace(kind=kind, x=x, y=y, button=button, clicks=1)


class Stub:
    def __init__(self):
        self.w = 113
        self.h = 48
        self.desk_y = 0
        self.desk_h = 47
        self.cfg = {'taskbar': 'bottom', 'shadows': False}
        self.theme = mock.Mock()
        self.dirty = False
        self.hostname = 'X61s'
        self.opened = []
        self.closed = 0

    def close_popup(self, p=None):
        self.closed += 1

    def open(self, name, **kw):
        self.opened.append(name)

    def shade(self, *a, **kw):
        pass


class TestStartMenu(unittest.TestCase):
    def setUp(self):
        self.wm = Stub()
        self.menu = StartMenu(self.wm)

    def test_one_row_per_category(self):
        cats = self.menu.categories()
        self.assertEqual([c for c, _apps in cats],
                         ['Apps', 'Office', 'Media', 'Games', 'System', 'Tools', 'About'])
        seen = sum(len(apps) for _c, apps in cats)
        self.assertEqual(seen, len(REGISTRY))

    def test_main_menu_is_short(self):
        rows = self.menu.main_rows()
        self.assertEqual(len(rows), 7 + 1 + 7)
        self.assertEqual([r[0] for r in rows].count('cat'), 7)
        self.assertEqual([r[0] for r in rows].count('foot'), 7)

    def test_right_opens_submenu(self):
        self.assertIsNone(self.menu.sub_cat)
        self.menu.on_key(key('right'))
        self.assertEqual(self.menu.sub_cat, 'Apps')
        self.assertEqual(self.menu.sub_sel, 0)

    def test_enter_on_category_opens_instead_of_launching(self):
        self.menu.on_key(key('enter'))
        self.assertEqual(self.menu.sub_cat, 'Apps')
        self.assertEqual(self.wm.opened, [])
        self.assertEqual(self.wm.closed, 0)

    def test_enter_in_submenu_launches(self):
        self.menu.on_key(key('right'))
        first = self.menu.sub_apps()[0][0]
        self.menu.on_key(key('down'))
        second = self.menu.sub_apps()[1][0]
        self.menu.on_key(key('enter'))
        self.assertEqual(self.wm.opened, [second])
        self.assertEqual(self.wm.closed, 1)
        self.assertNotEqual(first, second)

    def test_esc_closes_submenu_first(self):
        self.menu.on_key(key('right'))
        self.menu.on_key(key('esc'))
        self.assertIsNone(self.menu.sub_cat)
        self.assertEqual(self.wm.closed, 0)
        self.menu.on_key(key('esc'))
        self.assertEqual(self.wm.closed, 1)

    def test_left_closes_submenu(self):
        self.menu.on_key(key('right'))
        self.menu.on_key(key('left'))
        self.assertIsNone(self.menu.sub_cat)
        self.menu.on_key(key('left'))
        self.assertIsNone(self.menu.sub_cat)
        self.assertEqual(self.wm.closed, 0)

    def test_menu_reopens_on_the_previous_category(self):
        for _ in range(3):
            self.menu.on_key(key('down'))
        cat = self.menu.current_row()[1]
        self.menu.on_key(key('right'))
        self.assertEqual(self.menu.sub_cat, cat)

    def test_submenu_to_the_right_and_on_screen(self):
        self.menu.on_key(key('right'))
        g = self.menu.sub_geom()
        self.assertIsNotNone(g)
        sx, sy, sw, sh = g
        self.assertEqual(sx, self.menu.x + self.menu.w)
        self.assertLessEqual(sx + sw, self.wm.w)
        self.assertGreaterEqual(sy, self.wm.desk_y)
        self.assertLessEqual(sy + sh, self.wm.desk_y + self.wm.desk_h)
        self.assertEqual(sh, len(self.menu.sub_apps()) + 1)

    def test_submenu_width_follows_content(self):
        self.menu.open_sub('Office')
        office = self.menu.sub_geom()[2]
        self.menu.open_sub('System')
        system = self.menu.sub_geom()[2]
        self.assertLess(office, system)
        self.assertLessEqual(system, 52)
        self.assertGreaterEqual(office, 22)

    def test_contains_includes_the_submenu(self):
        self.menu.on_key(key('right'))
        g = self.menu.sub_geom()
        self.assertTrue(self.menu.contains(g[0] + 1, g[1] + 1))
        self.assertTrue(self.menu.contains(2, self.menu.y + 5))
        self.assertFalse(self.menu.contains(self.wm.w - 1, self.wm.desk_h - 1)
                         if g[0] + g[2] < self.wm.w else False)

    def test_hover_opens_submenu(self):
        y = self.menu.y + 4
        self.menu.on_mouse(mouse('move', 5, y))
        self.assertEqual(self.menu.sub_cat, 'Apps')

    def test_hover_other_category_switches(self):
        y = self.menu.y + 4
        self.menu.on_mouse(mouse('move', 5, y))
        self.menu.on_mouse(mouse('move', 5, y + 2))
        self.assertEqual(self.menu.sub_cat, 'Media')

    def test_hover_footer_closes_submenu(self):
        y = self.menu.y + 4
        self.menu.on_mouse(mouse('move', 5, y))
        self.assertIsNotNone(self.menu.sub_cat)
        rows = self.menu.main_rows()
        foot = [i for i, r in enumerate(rows) if r[0] == 'foot'][0]
        self.menu.on_mouse(mouse('move', 5, self.menu.y + 4 + foot))
        self.assertIsNone(self.menu.sub_cat)

    def test_click_in_submenu_launches(self):
        self.menu.on_key(key('right'))
        g = self.menu.sub_geom()
        app = self.menu.sub_apps()[1][0]
        self.menu.on_mouse(mouse('up', g[0] + 4, g[1] + 2))
        self.assertEqual(self.wm.opened, [app])

    def test_wheel_in_submenu_scrolls(self):
        self.menu.open_sub('Office')
        self.menu.sub_top = 0
        g = self.menu.sub_geom()
        self.menu.on_mouse(mouse('wheeldown', g[0] + 2, g[1] + 1))
        if len(self.menu.sub_apps()) > g[3] - 1:
            self.assertGreater(self.menu.sub_top, 0)
        else:
            self.assertEqual(self.menu.sub_top, 0)

    def test_typing_closes_submenu_and_filters(self):
        self.menu.on_key(key('right'))
        self.menu.on_key(key('b', 'b'))
        self.menu.on_key(key('l', 'l'))
        self.menu.on_key(key('u', 'u'))
        self.assertIsNone(self.menu.sub_cat)
        self.assertEqual(self.menu.search.text, 'blu')
        rows = self.menu.rows()
        self.assertNotIn('cat', [r[0] for r in rows])
        self.assertIn('Bluetooth', [r[2] for r in rows if r[0] == 'app'])

    def test_typing_selects_first_hit(self):
        self.menu.on_key(key('n', 'n'))
        rows = self.menu.rows()
        first = rows[self.menu.selectable()[self.menu.sel]]
        self.assertEqual(first[0], 'app')

    def test_search_widens_for_descriptions(self):
        self.assertEqual(self.menu.w, 25)
        self.menu.search.insert('net')
        self.menu.layout()
        self.assertEqual(self.menu.w, 52)

    def test_no_matches_notice(self):
        self.menu.search.insert('zzzz')
        kinds = [r[0] for r in self.menu.all_rows()]
        self.assertIn('hdr', kinds)
        self.assertEqual([r for r in self.menu.all_rows() if r[0] == 'hdr'],
                         [('hdr', 'no matches')])

    def test_search_footer_still_present(self):
        self.menu.search.insert('net')
        labels = [r[2] for r in self.menu.all_rows() if r[0] == 'foot']
        self.assertIn('Lock screen', labels)
        self.assertIn('Shut down', labels)

    def test_paste_closes_submenu(self):
        self.menu.on_key(key('right'))
        self.menu.on_paste('note')
        self.assertIsNone(self.menu.sub_cat)
        self.assertEqual(self.menu.search.text, 'note')

    def test_narrow_screen(self):
        wm = Stub()
        wm.w = 80
        wm.h = 24
        wm.desk_h = 23
        menu = StartMenu(wm)
        menu.on_key(key('right'))
        g = menu.sub_geom()
        self.assertEqual(menu.w, 25)
        self.assertLessEqual(g[0] + g[2], wm.w)
        self.assertGreaterEqual(g[1], 0)
        self.assertLessEqual(g[1] + g[3], wm.desk_y + wm.desk_h)


if __name__ == '__main__':
    unittest.main()
