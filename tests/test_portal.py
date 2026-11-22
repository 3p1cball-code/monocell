"""Captive portal detection: probes, verdicts, the background watch, browser hooks."""
import http.server
import os
import socket
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import portal  # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    """Answers like a normal network, a portal, or a broken one."""

    routes = {}
    hits = []

    def do_GET(self):
        Handler.hits.append(self.path)
        status, headers, body = Handler.routes.get(self.path, (404, {}, b'nope'))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Server:
    def __init__(self, routes):
        Handler.routes = routes
        Handler.hits = []
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path=''):
        return 'http://127.0.0.1:%d%s' % (self.port, path)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def probes(server, paths=('/gen204', '/txt')):
    return tuple((server.url(p), 204 if p == '/gen204' else 200,
                  b'' if p == '/gen204' else b'success') for p in paths)


class TestVerdict(unittest.TestCase):
    def v(self, res, status=204, body=b''):
        return portal.verdict(res, status, body, 'http://probe/')

    def test_expected_answer_is_online(self):
        self.assertEqual(self.v({'status': 204, 'body': b''}), ('online', '', 'ok'))
        self.assertEqual(self.v({'status': 200, 'body': b'success'}, 200, b'success')[0], 'online')

    def test_redirect_names_the_login_page(self):
        kind, url, why = self.v({'status': 302, 'location': '/portal/login?a=1', 'url': 'http://probe/'})
        self.assertEqual((kind, url, why), ('portal', 'http://probe/portal/login?a=1', 'redirect'))

    def test_200_instead_of_204_is_an_interception(self):
        kind, url, why = self.v({'status': 200, 'body': b'<html>login</html>', 'url': 'http://probe/'})
        self.assertEqual(kind, 'portal')
        self.assertEqual(url, 'http://probe/')
        self.assertIn('200', why)

    def test_wrong_body_is_an_interception(self):
        kind, _u, why = self.v({'status': 200, 'body': b'<html>Hotspot</html>'}, 200, b'success')
        self.assertEqual((kind, why), ('portal', 'intercepted page'))

    def test_auth_required(self):
        self.assertEqual(self.v({'status': 511, 'url': 'http://probe/'})[:2],
                         ('portal', 'http://probe/'))

    def test_errors_are_failures(self):
        kind, _url, why = self.v({'status': 0, 'error': 'dns'})
        self.assertEqual((kind, why), ('fail', 'dns'))


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()

    def serve(self, routes):
        s = Server(routes)
        self.servers.append(s)
        return s

    def test_open_network(self):
        s = self.serve({'/gen204': (204, {}, b''), '/txt': (200, {}, b'success')})
        res = portal.check(probes=probes(s), gateway='')
        self.assertEqual(res['state'], 'online')
        self.assertEqual(res['url'], '')

    def test_hijacked_requests(self):
        s = self.serve({'/gen204': (302, {'Location': '/hotspot'}, b''),
                        '/txt': (302, {'Location': '/hotspot'}, b'')})
        res = portal.check(probes=probes(s), gateway='')
        self.assertEqual(res['state'], 'portal')
        self.assertEqual(res['url'], s.url('/hotspot'))
        self.assertEqual(res['detail'], 'redirect')

    def test_interception_page_without_redirect(self):
        s = self.serve({'/gen204': (200, {'Content-Type': 'text/html'}, b'<html>Bitte anmelden</html>'),
                        '/txt': (200, {'Content-Type': 'text/html'}, b'<html>Bitte anmelden</html>')})
        res = portal.check(probes=probes(s), gateway='')
        self.assertEqual((res['state'], res['url']), ('portal', s.url('/gen204')))

    def test_one_blocked_probe_does_not_invent_a_portal(self):
        # a proxy that refuses one address but the other one answers normally
        s = self.serve({'/gen204': (404, {}, b''), '/txt': (200, {}, b'success')})
        self.assertEqual(portal.check(probes=probes(s), gateway='')['state'], 'online')

    def test_auth_required_answer(self):
        s = self.serve({'/gen204': (511, {}, b''), '/txt': (511, {}, b'')})
        res = portal.check(probes=probes(s), gateway='')
        self.assertEqual(res['state'], 'portal')
        self.assertIn('511', res['detail'])

    def test_nothing_answers(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()                                    # closed port: connection refused
        pr = (('http://127.0.0.1:%d/gen204' % port, 204, b''),
              ('http://127.0.0.1:%d/txt' % port, 200, b'success'))
        res = portal.check(timeout=1.0, probes=pr, gateway='')
        self.assertEqual(res['state'], 'offline')
        self.assertIn('refused', res['detail'])

    def test_no_dns(self):
        def dead(url, timeout=4.0, max_bytes=2048):
            if url.startswith('http://127.0.0.1:'):     # the gateway still answers
                return portal._request(url, timeout)
            return {'status': 0, 'url': url, 'location': '', 'ctype': '', 'body': b'',
                    'error': 'dns'}
        res = portal.check(probes=portal.PROBES, gateway='', request=dead)
        self.assertEqual((res['state'], res['detail']), ('offline', 'no name resolution'))

    def test_gateway_that_asks_for_a_login(self):
        s = self.serve({'/': (302, {'Location': 'http://portal.example/login'}, b'')})

        def dead(url, timeout=4.0, max_bytes=2048):
            if url.startswith('http://127.0.0.1:'):     # the gateway still answers
                return portal._request(url, timeout)
            return {'status': 0, 'url': url, 'location': '', 'ctype': '', 'body': b'',
                    'error': 'dns'}
        res = portal.check(probes=portal.PROBES, request=dead,
                           gateway='127.0.0.1:%d' % s.port)
        self.assertEqual(res['state'], 'portal')
        self.assertEqual(res['url'], 'http://portal.example/login')

    def test_gateway_admin_page_is_not_a_portal(self):
        s = self.serve({'/': (200, {'Content-Type': 'text/html'}, b'<html>FRITZ!Box</html>')})

        def dead(url, timeout=4.0, max_bytes=2048):
            if url.startswith('http://127.0.0.1:'):     # the gateway still answers
                return portal._request(url, timeout)
            return {'status': 0, 'url': url, 'location': '', 'ctype': '', 'body': b'',
                    'error': 'dns'}
        res = portal.check(probes=portal.PROBES, request=dead,
                           gateway='127.0.0.1:%d' % s.port)
        self.assertEqual(res['state'], 'offline')
        self.assertIn('gateway answers', res['detail'])
        self.assertTrue(res['url'].startswith('http://127.0.0.1:'))


class TestReasons(unittest.TestCase):
    def test_network_errors_from_the_lite_engine(self):
        for text in ('<urlopen error [Errno -3] Temporary failure in name resolution>',
                     '[SSL: WRONG_VERSION_NUMBER] wrong version number',
                     'timed out', 'Connection refused', 'Network is unreachable'):
            self.assertTrue(portal.looks_like_network_error(text), text)
        for text in ('403 Forbidden', 'HTTP Error 404: Not Found', 'invalid URL'):
            self.assertFalse(portal.looks_like_network_error(text), text)


class TestMonitor(unittest.TestCase):
    def test_connectivity_lines(self):
        self.assertEqual(portal.in_monitor("Connectivity is now 'portal'"), 'portal')
        self.assertEqual(portal.in_monitor("Connectivity is now 'full'"), 'full')
        self.assertEqual(portal.in_monitor("Networkmanager is now in the 'connected (site only)' state"),
                         'state')
        self.assertEqual(portal.in_monitor('Wi-Fi is now enabled'), '')

    def test_connection_info_without_nmcli(self):
        with mock.patch.object(portal, 'nmcli', return_value=(127, '')):
            self.assertEqual(portal.connection_info(), ('', '', ''))


class FakeWM:
    def __init__(self):
        self.windows = []
        self.opened = []
        self.toasts = []
        self.dirty = False
        self.portal_state = None
        self.portal_url = ''
        self.portal_detail = ''
        self.loop = types.SimpleNamespace(run_in_thread=lambda *a, **k: None,
                                          call_later=lambda *a, **k: None,
                                          call_soon_threadsafe=lambda cb: None,
                                          every=lambda *a, **k: None)
        self.net = ('wifi', 'wlan0', '10.0.0.2')
        self.portal_watch = None

    def set_portal(self, state, url='', detail='', at=0.0):
        self.portal_state, self.portal_url = state, url

    def toast(self, text, color='accent', secs=3.5):
        self.toasts.append((text, color))

    def open(self, name, **kw):
        self.opened.append((name, kw))
        return None


class TestWatch(unittest.TestCase):
    def setUp(self):
        self.wm = FakeWM()
        self.watch = portal.Watch(self.wm)
        self.wm.portal_watch = self.watch

    def result(self, state, url='', detail='', at=1.0):
        return {'state': state, 'url': url, 'detail': detail, 'at': at}

    def test_portal_opens_the_login_page_once(self):
        with mock.patch.object(portal, 'connection_info', return_value=('WIFI@DB', 'portal', 'WIFI@DB')):
            self.watch.apply(self.result('portal', 'http://portal.example/login', 'redirect'))
            self.watch.apply(self.result('portal', 'http://portal.example/login', 'redirect', 2.0))
        self.assertEqual(len(self.wm.opened), 1)
        self.assertEqual(self.wm.opened[0], ('browser', {'url': 'http://portal.example/login',
                                                         'gfx': True}))
        self.assertEqual(self.wm.portal_state, 'portal')
        self.assertTrue(any('WIFI@DB' in t[0] for t in self.wm.toasts))

    def test_a_new_episode_opens_again(self):
        self.watch.apply(self.result('portal', 'http://portal.example/login'))
        self.watch.apply(self.result('online', '', 'reachable', 2.0))
        self.watch.apply(self.result('portal', 'http://portal.example/login', 'redirect', 3.0))
        self.assertEqual(len(self.wm.opened), 2)

    def test_an_open_browser_counts(self):
        win = types.SimpleNamespace(closed=False, app=types.SimpleNamespace(
            __class__=type('Browser', (), {})))
        # not a Browser subclass -> ignored
        self.wm.windows = [win]
        self.assertFalse(self.watch.browser_showing('http://x/'))
        from monocell.apps.browser import Browser
        b = Browser.__new__(Browser)
        b.gfx = None
        b.url = 'http://portal.example/login'
        self.wm.windows = [types.SimpleNamespace(closed=False, app=b)]
        self.assertTrue(self.watch.browser_showing('http://portal.example/login'))

    def test_online_again_clears_the_episode(self):
        self.watch.apply(self.result('portal', 'http://p/login'))
        self.watch.apply(self.result('online', '', 'reachable'))
        self.assertEqual(self.watch.opened, '')
        self.assertIn(('WLAN: online', 'green'), self.wm.toasts)

    def test_offline_opens_nothing(self):
        self.watch.apply(self.result('offline', '', 'no name resolution'))
        self.assertEqual(self.wm.opened, [])
        self.assertEqual(self.wm.portal_state, 'offline')

    def test_dry_run_never_opens(self):
        self.watch.auto_open = False
        self.watch.apply(self.result('portal', 'http://p/login'))
        self.assertEqual(self.wm.opened, [])


class TestBrowserHooks(unittest.TestCase):
    def test_menu_offers_the_sign_in_page(self):
        from monocell.apps.browser import Browser
        b = Browser.__new__(Browser)
        b.cfg = {'bookmarks': []}
        b.url = 'http://example.com/'
        b.win = types.SimpleNamespace(x=0, y=0, w=80)
        b.reader = True
        captured = []
        b.wm = types.SimpleNamespace(menu=lambda items, x, y: captured.append(items))
        b.menu()
        labels = [i.label for i in captured[0] if i is not None]
        self.assertIn('Find the network sign-in page', labels)

    def test_portal_now_uses_a_known_portal(self):
        from monocell.apps.browser import Browser
        b = Browser.__new__(Browser)
        b.want_gfx = False
        b.status = ''
        seen = []
        b.navigate = lambda url, **kw: seen.append(url)
        b.wm = types.SimpleNamespace(portal_state='portal', portal_url='http://p/login',
                                     portal_watch=object())
        Browser.portal_now(b)
        self.assertTrue(b.want_gfx)
        self.assertEqual(seen, ['http://p/login'])

    def test_find_portal_navigates_in_graphics_mode(self):
        from monocell.apps.browser import Browser
        b = Browser.__new__(Browser)
        b.portal_tries = 0
        b.status = ''
        b.want_gfx = False
        b.notice = ''
        b.invalidate = lambda: None
        b.navigate = lambda url, **kw: None
        seen = []
        b.navigate = lambda url, **kw: seen.append(url)

        class WM:
            portal_state = None
            portal_url = ''

            def set_portal(self, *a):
                self.got = a

            def toast(self, *a, **k):
                pass

        b.wm = WM()

        def run(work, done=None, error=None):
            done({'state': 'portal', 'url': 'http://portal.example/login',
                  'detail': 'redirect', 'at': 1.0})
        b.thread = run
        with mock.patch.object(portal, 'check', return_value={'state': 'portal',
                                                              'url': 'http://portal.example/login',
                                                              'detail': 'redirect', 'at': 1.0}):
            b.find_portal()
        self.assertTrue(b.want_gfx)
        self.assertEqual(seen, ['http://portal.example/login'])

    def test_find_portal_gives_up_after_two_tries(self):
        from monocell.apps.browser import Browser
        b = Browser.__new__(Browser)
        b.portal_tries = 2
        b.find_portal()
        self.assertEqual(b.portal_tries, 2)


if __name__ == '__main__':
    unittest.main()
