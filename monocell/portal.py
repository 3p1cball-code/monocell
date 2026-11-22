"""Captive portals: does this network want a login, and where is its page?

From the inside a captive portal looks like a broken network: names resolve to
somewhere odd, requests come back with a page nobody asked for, HTTPS fails with
`wrong version number`. The probes below ask the same addresses a phone or a
laptop asks for exactly this purpose, and they read the answer instead of
following it:

  * a redirect is the portal's doing (its target is the login page),
  * a 511 is "network authentication required",
  * a page where a 204 or the word "success" is expected means somebody is in
    the way,
  * a name that does not resolve at all means the network is not there (yet).

Two probes have to agree before a portal is reported: a proxy that just blocks
one address must not turn into a login page that opens by itself.

`check()` is pure (it takes its request function), `Watch` runs it in the
background, notices when the network changes and tells the window manager.
"""
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import hardware
from .util import log, which

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36 monocell/1.0')

# (url, expected status, expected body) - the endpoints phones and laptops use
PROBES = (
    ('http://connectivitycheck.gstatic.com/generate_204', 204, b''),
    ('http://detectportal.firefox.com/success.txt', 200, b'success'),
    ('http://nmcheck.gnome.org/check_network_status.txt', 200, b'NetworkManager'),
)
REDIRECTS = (301, 302, 303, 307, 308)

# what a failed request means, in words the UI can show
REASONS = {
    'dns': 'no name resolution',
    'timeout': 'no answer',
    'refused': 'connection refused',
    'tls': 'connection not encrypted',
    'net': 'network unreachable',
    'offline': 'offline',
}


def _reason(e):
    """Short label for a failed request (see REASONS)."""
    text = str(e).lower()
    if isinstance(e, urllib.error.URLError):
        e = e.reason if isinstance(e.reason, BaseException) else e.reason
        text = (str(e) + ' ' + text).lower()
    if isinstance(e, socket.gaierror) or 'name or service not known' in text or \
            'name resolution' in text or 'getaddrinfo' in text or 'nodename nor servname' in text:
        return 'dns'
    if isinstance(e, (socket.timeout, TimeoutError)) or 'timed out' in text:
        return 'timeout'
    if isinstance(e, ConnectionRefusedError) or 'refused' in text:
        return 'refused'
    if isinstance(e, ssl.SSLError) or 'wrong version number' in text or 'certificate' in text:
        return 'tls'
    if isinstance(e, OSError) and getattr(e, 'errno', None) in (101, 113, 10065):
        return 'net'
    if 'unreachable' in text:
        return 'net'
    return 'offline'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand a redirect back instead of following it: its target IS the portal."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url, timeout=4.0, max_bytes=2048):
    """One plain HTTP GET. Never raises: a failure is part of the answer."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*',
                                               'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
                                               'Connection': 'close'})
    try:
        with opener.open(req, timeout=timeout) as r:
            return {'status': r.status, 'url': r.geturl(), 'location': '',
                    'ctype': r.headers.get('Content-Type', ''),
                    'body': r.read(max_bytes)}
    except urllib.error.HTTPError as e:
        body = b''
        try:
            body = e.read(max_bytes)
        except Exception:
            pass
        finally:
            e.close()
        headers = e.headers or {}
        return {'status': e.code, 'url': e.geturl() or url,
                'location': headers.get('Location', '') or '',
                'ctype': headers.get('Content-Type', '') or '', 'body': body}
    except (urllib.error.URLError, OSError, ValueError, ssl.SSLError) as e:
        return {'status': 0, 'url': url, 'location': '', 'ctype': '', 'body': b'',
                'error': _reason(e)}


def verdict(res, status, body, url):
    """'online' | 'portal' | 'fail' for one probe answer, with a URL and a reason."""
    if res.get('error'):
        return 'fail', '', res['error']
    code = res.get('status') or 0
    if code == 511:
        return 'portal', res.get('url') or url, 'login required (511)'
    if code in REDIRECTS:
        target = urllib.parse.urljoin(url, res.get('location') or '')
        return 'portal', target or res.get('url') or url, 'redirect'
    if code != status:
        return 'portal', res.get('url') or url, 'HTTP %d instead of %d' % (code, status)
    got = (res.get('body') or b'').strip().lower()
    want = (body or b'').strip().lower()
    if want and want not in got:
        return 'portal', res.get('url') or url, 'intercepted page'
    if not want and got:
        return 'portal', res.get('url') or url, 'intercepted page'
    return 'online', '', 'ok'


LOGIN_WORDS = ('login', 'log in', 'log-in', 'sign in', 'sign-in', 'anmeld', 'willkommen',
               'welcome', 'hotspot', 'portal', 'passwort', 'password', 'nutzungsbeding',
               'terms of', 'agb', 'gastzugang', 'internetzugang')


def login_page(body):
    """Does an answer read like a login page? (a router's admin page does not)"""
    low = (body or b'').lower()
    if not low:
        return False
    return any(w.encode() in low for w in LOGIN_WORDS)


def resolvers():
    """The DNS servers this machine was told about (/etc/resolv.conf)."""
    out = []
    try:
        with open('/etc/resolv.conf') as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[0] == 'nameserver' and not p[1].startswith(('127.', '::1')):
                    out.append(p[1])
    except OSError:
        pass
    return out


def dhcp_hint():
    """A portal address the DHCP server named (RFC 8910, as far as nmcli shows it)."""
    rc, out = nmcli(['-f', 'DHCP4', 'device', 'show'])
    if rc != 0:
        return []
    found = []
    for line in (out or '').splitlines():
        low = line.lower()
        if 'http://' not in low and 'https://' not in low:
            continue
        url = 'http' + line.split('http', 1)[1].strip().split()[0]
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ''
        if host and not host.replace('.', '').isdigit():
            continue                    # needs a name, and names do not resolve here
        if parts.port and parts.port != 80:
            host = '%s:%d' % (host, parts.port)
        if host and host not in found:
            found.append(host)
    return found


def local_candidates(gateway=None, dns=None, extra=None):
    """Addresses a portal can hide behind when no name resolves, in the order to try."""
    out = []
    for host in ([gateway] if gateway else []) + list(dns or []) + list(extra or []):
        host = (host or '').strip()
        if host and host not in out:
            out.append(host)
    return out


def check(timeout=4.0, gateway=None, probes=PROBES, request=_request):
    """Is this network open, behind a login page, or simply not there?

    Returns {'state': 'online'|'portal'|'offline', 'url', 'detail', 'probe',
    'gateway', 'at'}. `url` is the address to open when a login is needed."""
    gw = gateway if gateway is not None else hardware.default_gateway()

    def result(state, url='', detail='', probe=''):
        return {'state': state, 'url': url, 'detail': detail, 'probe': probe,
                'gateway': gw or '', 'at': time.time()}

    found = []              # probes that look like a portal: [(url, why)]
    fails = []              # probes that did not answer: [reason]
    for url, status, body in probes:
        res = request(url, timeout)
        kind, page, why = verdict(res, status, body, url)
        if kind == 'online':
            return result('online', '', 'reachable', url)
        if kind == 'portal':
            found.append((page or url, why))
            if len(found) >= 2:
                break
        else:
            fails.append(why)
    if found:
        url, why = found[0]
        detail = why if len(found) > 1 else '%s (not confirmed)' % why
        return result('portal', url, detail, probes[0][0])

    # Nothing answered. Some hotel, train and airport systems hand out no DNS at
    # all until the login is done, so the portal is only reachable by address: the
    # gateway, the DNS server (often the portal itself) or whatever the DHCP server
    # named. A redirect or a 511 there is proof; an ordinary page counts only when it
    # reads like a login, so a router's admin page does not open by itself.
    answers = ''
    for host in local_candidates(gw, resolvers(), dhcp_hint()):
        url = 'http://%s/' % host
        res = request(url, min(3.0, timeout))
        code = res.get('status') or 0
        if code in REDIRECTS or code == 511:
            target = urllib.parse.urljoin(url, res.get('location') or '') or url
            if urllib.parse.urlsplit(target).hostname in (None, ''):
                target = url
            return result('portal', target, '%s asks for a login' % host, url)
        if code == 200 and login_page(res.get('body')):
            return result('portal', url, 'login page on %s' % host, url)
        if code:
            answers = answers or url
    why = REASONS.get(fails[0], fails[0]) if fails else 'offline'
    if len(set(fails)) > 1:
        why = ', '.join(REASONS.get(f, f) for f in dict.fromkeys(fails))
    if answers:
        return result('offline', answers, '%s - %s answers' % (why, answers.split('//')[1].strip('/')),
                      answers)
    return result('offline', '', why, probes[0][0])


def looks_like_network_error(text):
    """Does a browser failure really mean 'the network', not a broken page?"""
    t = (text or '').lower()
    return any(k in t for k in ('name or service not known', 'name resolution', 'getaddrinfo',
                                'timed out', 'timeout', 'connection refused', 'network is unreachable',
                                'wrong version number', 'connection reset', 'nodename nor servname',
                                'temporary failure'))


def nmcli(args, timeout=6):
    """(returncode, stdout) of one nmcli call; (127, '') without NetworkManager."""
    if not which('nmcli'):
        return 127, ''
    try:
        p = subprocess.run(['nmcli'] + args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 124, ''
    return p.returncode, p.stdout


def connection_info():
    """(connection, connectivity, ssid) from NetworkManager, best effort."""
    conn = ssid = ''
    rc, out = nmcli(['-t', '-f', 'CONNECTIVITY', 'general'])
    connectivity = (out or '').strip() if rc == 0 else ''
    rc, out = nmcli(['-t', '-f', 'ACTIVE,SSID', 'device', 'wifi'])
    if rc == 0:
        for line in (out or '').splitlines():
            parts = line.split(':')
            if len(parts) >= 2 and parts[0] == 'yes':
                ssid = ':'.join(parts[1:])
                break
    rc, out = nmcli(['-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'])
    if rc == 0:
        for line in (out or '').splitlines():
            name, _, kind = line.rpartition(':')
            if kind in ('802-11-wireless', '802-3-ethernet') and name:
                conn = name
                break
    return conn, connectivity, ssid


def in_monitor(text):
    """Which event does one `nmcli monitor` line carry? ('' for none)"""
    low = (text or '').lower()
    if 'connectivity is now' in low:
        return low.split('connectivity is now', 1)[1].strip().strip("'\".")
    if 'networkmanager is now in the' in low:
        return 'state'
    return ''


class Watch:
    """Background look-out for a login page, owned by the window manager.

    `nmcli monitor` reports when NetworkManager changes its mind about the
    connection; a slow tick covers the cases where it says nothing (a wifi that
    came up while Monocell was already running, a portal that only starts asking
    after a while). Both lead into one `check()` at a time, and the result goes
    back into the UI thread - opening a window from a worker thread is not a
    thing this desktop does.
    """

    def __init__(self, wm, interval=90.0, auto_open=True):
        self.wm = wm
        self.interval = interval
        self.auto_open = auto_open
        self.state = None            # 'online' | 'portal' | 'offline'
        self.url = ''
        self.detail = ''
        self.at = 0.0
        self.busy = False
        self.again = None            # reason of a probe asked for while busy
        self.opened = ''             # the page we opened for this episode
        self.stops = threading.Event()
        self.proc = None
        self.monitor = None
        self.probes = 0

    # ------------------------------------------------------------ lifecycle
    def start(self):
        tick = lambda: self.probe('tick')
        self.wm.loop.call_later(6.0, lambda: self.probe('start'))
        self.wm.loop.every(self.interval, tick)
        self.monitor = threading.Thread(target=self._read_monitor, name='portal-monitor',
                                        daemon=True)
        self.monitor.start()
        return self

    def stop(self):
        self.stops.set()
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _read_monitor(self):
        """`nmcli monitor` lines -> a probe a moment later (the network settles)."""
        if not which('nmcli'):
            return
        try:
            self.proc = subprocess.Popen(['nmcli', 'monitor'], stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            return
        for line in self.proc.stdout:
            if self.stops.is_set():
                break
            what = in_monitor(line)
            if not what:
                continue
            log('portal: nmcli %s' % line.strip())
            self.wm.loop.call_soon_threadsafe(lambda w=what: self._later(w))

    def _later(self, what):
        """UI thread: give the network a moment, then look (debounced)."""
        if what in ('full', 'online'):
            self.wm.loop.call_later(2.0, lambda: self.probe('nm:' + what))
            return
        self.wm.loop.call_later(3.0, lambda: self.probe('nm:' + what))

    # ------------------------------------------------------------ probing
    def probe(self, reason=''):
        if self.stops.is_set():
            return
        if self.busy:
            self.again = reason or 'queued'
            return
        if reason == 'tick' and not self.wm.net:
            return                      # no link at all: nothing to look for
        self.busy = True
        self.probes += 1

        def work():
            return check()

        def done(res):
            self.busy = False
            self.apply(res, reason)
            if self.again:
                why, self.again = self.again, None
                self.wm.loop.call_later(1.0, lambda: self.probe(why))

        def fail(e):
            self.busy = False
            log('portal: %s' % e)

        self.wm.loop.run_in_thread(work, done, fail)

    def apply(self, res, reason=''):
        """UI thread: remember the result and act on a change."""
        prev = self.state
        self.state = res.get('state') or 'offline'
        self.url = res.get('url') or ''
        self.detail = res.get('detail') or ''
        self.at = res.get('at') or time.time()
        self.wm.set_portal(self.state, self.url, self.detail, self.at)
        log('portal: %s %s (%s, %s)' % (self.state, self.url or '-', self.detail, reason))
        if self.state == 'portal':
            if prev != 'portal':
                self.opened = ''
                self.announce()
            self.open_page()
        elif self.state == 'online' and prev == 'portal':
            self.opened = ''
            self.wm.toast('WLAN: online', 'green')

    def announce(self):
        conn, _connectivity, ssid = connection_info()
        name = ssid or conn or 'this network'
        self.wm.toast('WLAN "%s" wants a login - opening the sign-in page' % name, 'yellow',
                      secs=6)

    # ------------------------------------------------------------ the page
    def browser_showing(self, url=''):
        """A browser window that already shows this page (or is in graphics mode)."""
        from .apps.browser import Browser
        for win in self.wm.windows:
            app = win.app
            if not isinstance(app, Browser) or win.closed:
                continue
            if app.gfx:
                return True
            if url and app.url and (app.url == url or app.url.rstrip('/') == url.rstrip('/')):
                return True
        return False

    def open_page(self):
        """Open the login page once per portal episode (never over a page the user closed)."""
        url = self.url
        if not url or not self.auto_open:
            return False
        if self.opened or self.browser_showing(url):
            return False
        self.opened = url
        self.wm.open('browser', url=url, gfx=True)
        return True


def start(wm, **kw):
    """Start the look-out (called once from the entry point, not in tests)."""
    watch = Watch(wm, **kw).start()
    wm.portal_watch = watch
    return watch
