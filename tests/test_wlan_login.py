"""WLAN login pages: Bahn portal URLs, browser home/menu entries, graphics start."""
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.apps.browser import Browser, WLAN_LOGINS, HOME_URL  # noqa: E402
from monocell.apps.network import known_portal, portal_address  # noqa: E402


class TestPortalAddress(unittest.TestCase):
    def test_ice_login(self):
        self.assertEqual(portal_address('WIFIonICE'), 'http://login.wifionice.de/')
        self.assertEqual(portal_address('WIFIonICE 12'), 'http://login.wifionice.de/')
        self.assertEqual(portal_address('ICE 571'), 'http://login.wifionice.de/')

    def test_wifi_at_db(self):
        self.assertEqual(portal_address('WIFI@DB'), 'http://wifi.bahn.de/')
        self.assertEqual(portal_address('WIFI@DB Berlin'), 'http://wifi.bahn.de/')
        self.assertEqual(portal_address('Bahn WLAN'), 'http://wifi.bahn.de/')

    def test_other_and_plain(self):
        self.assertEqual(portal_address('HomeNet', '192.168.1.1'), 'http://192.168.1.1/')
        self.assertEqual(portal_address('HomeNet'), 'http://neverssl.com/')
        self.assertFalse(known_portal('HomeNet'))
        self.assertFalse(known_portal('office'))       # 'ice' inside a word is not ICE
        self.assertTrue(known_portal('WIFI@DB'))
        self.assertTrue(known_portal('WIFIonICE'))

    def test_login_list(self):
        urls = [u for _n, u in WLAN_LOGINS]
        self.assertIn('http://wifi.bahn.de/', urls)
        self.assertIn('http://login.wifionice.de/', urls)


class TestBrowserHome(unittest.TestCase):
    def make(self, bookmarks=None):
        b = Browser.__new__(Browser)
        b.cfg = {'bookmarks': bookmarks or []}
        return b

    def test_home_lists_the_rail_logins(self):
        doc = self.make().home_doc()
        links = []
        text = []
        for block in doc['blocks']:
            for seg in block.get('s', []):
                text.append(seg.get('x', ''))
                if 'h' in seg:
                    links.append(seg['h'])
        self.assertIn('http://wifi.bahn.de/', links)
        self.assertIn('http://login.wifionice.de/', links)
        self.assertIn('WLAN login pages', ' '.join(text))

    def test_menu_lists_the_rail_logins(self):
        b = self.make()
        b.url = 'http://example.com/'
        b.win = types.SimpleNamespace(x=0, y=0, w=80)
        b.reader = True
        captured = []
        b.wm = types.SimpleNamespace(menu=lambda items, x, y: captured.append(items))
        b.menu()
        labels = [i.label for i in captured[0] if i is not None]
        self.assertTrue(any('WIFI@DB' in l for l in labels))
        self.assertTrue(any('WIFIonICE' in l for l in labels))
        self.assertTrue(any('Graphics mode' in l for l in labels))

    def test_menu_labels_are_distinct(self):
        b = self.make()
        b.url = 'http://example.com/'
        b.win = types.SimpleNamespace(x=0, y=0, w=80)
        b.reader = True
        captured = []
        b.wm = types.SimpleNamespace(menu=lambda items, x, y: captured.append(items))
        b.menu()
        labels = [i.label for i in captured[0] if i is not None]
        self.assertEqual(len(labels), len(set(labels)))


class TestBrowserGfx(unittest.TestCase):
    def test_reopen_with_gfx(self):
        b = Browser.__new__(Browser)
        b.want_gfx = False
        seen = []
        b.navigate = lambda url, **kw: seen.append(url)
        Browser.reopen(b, 'http://wifi.bahn.de/', gfx=True)
        self.assertTrue(b.want_gfx)
        self.assertEqual(seen, ['http://wifi.bahn.de/'])
        Browser.reopen(b, 'http://example.com/')
        self.assertFalse(b.want_gfx)

    def test_show_enters_graphics_mode_once(self):
        b = Browser.__new__(Browser)
        b.want_gfx = True
        calls = []
        b.enter_gfx = lambda: calls.append('gfx')
        b.doc = {}
        b.url = HOME_URL
        b.loading = True
        b.status = ''
        b.scroll = 0
        b.values = {}
        b.focus_idx = -1
        b.layout_key = None
        b.notice = ''
        b.cfg = {'bookmarks': []}
        b.addr = types.SimpleNamespace(set=lambda v: None)
        b.set_title = lambda t: None
        b.invalidate = lambda: None
        b.win = types.SimpleNamespace(closed=False)
        b.show({'title': 'Portal', 'url': 'http://login.wifionice.de/'})
        self.assertEqual(calls, ['gfx'])
        self.assertFalse(b.want_gfx)


if __name__ == '__main__':
    unittest.main()
