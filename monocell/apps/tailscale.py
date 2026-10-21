"""Tailscale: mesh status and a small connect/disconnect tool."""
import json
import re
import subprocess

from ..app import App
from ..util import fit, sym, which
from ..widgets import ListView, button


def parse_status(data):
    me = data.get('Self') or {}
    peers = sorted((data.get('Peer') or {}).values(),
                   key=lambda p: (not p.get('Online'), (p.get('HostName') or '').lower()))
    tailnet = (data.get('CurrentTailnet') or {}).get('Name') or data.get('MagicDNSSuffix') or ''
    exit_id = (data.get('ExitNodeStatus') or {}).get('ID') or ''
    return {
        'state': data.get('BackendState') or '?',
        'self': me,
        'peers': peers,
        'tailnet': tailnet,
        'exit': exit_id,
        'ips': me.get('TailscaleIPs') or [],
    }


def first_ip(ips, v4=True):
    for ip in ips or []:
        if (':' in ip) != v4:
            return ip
    return ''


class Tailscale(App):
    name = 'tailscale'
    title = 'Tailscale'
    icon = '[T]'
    size = (86, 26)
    min_size = (44, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.list = ListView()
        self.list.on_activate = self.copy_peer
        self.data = None
        self.err = ''
        self.msg = ''
        self.login_url = ''
        self.busy = ''
        self.last = ''
        self.installed = bool(which('tailscale'))
        self.refresh()
        self.every(5, self.refresh)

    # ------------------------------------------------------------ commands
    def run(self, args, timeout=30):
        try:
            p = subprocess.run(['tailscale'] + args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return 127, '', 'tailscale not installed'
        except subprocess.TimeoutExpired:
            return 124, '', 'timeout'
        except OSError as e:
            return 1, '', str(e)
        return p.returncode, p.stdout, p.stderr

    def refresh(self):
        if self.busy or not self.installed:
            return

        def work():
            rc, out, err = self.run(['status', '--json'], 12)
            data = None
            if out.strip():
                try:
                    data = json.loads(out)
                except ValueError:
                    data = None
            return rc, data, (err or out)

        def done(res):
            rc, data, txt = res
            if data:
                self.data = parse_status(data)
                self.err = ''
                self.last = ''
            else:
                self.data = None
                self.err = (txt or 'tailscale status failed').strip().splitlines()[-1][:160]
            self.rebuild()
            self.invalidate()

        def fail(e):
            self.err = str(e)
            self.invalidate()
        self.thread(work, done, fail)

    def rebuild(self):
        peers = (self.data or {}).get('peers') or []
        self.list.set_items(peers, keep=True)

    def action(self, cmd):
        if self.busy or not self.installed:
            return
        self.busy = cmd
        self.msg = cmd + '...'
        self.login_url = ''
        self.invalidate()

        def work():
            rc, out, err = self.run([cmd], 60)
            return rc, (out or '') + ('\n' + err if err else '')

        def done(res):
            rc, txt = res
            self.busy = ''
            self.last = txt.strip()
            m = re.search(r'https://login\.tailscale\.com/\S+', txt)
            if m:
                self.login_url = m.group(0).rstrip('.,)')
            self.msg = self.last.splitlines()[-1][:160] if self.last else ('%s ok' % cmd)
            self.refresh()

        def fail(e):
            self.busy = ''
            self.msg = str(e)
            self.invalidate()
        self.thread(work, done, fail)

    def copy_peer(self, p):
        ip = first_ip(p.get('TailscaleIPs'), True)
        if ip:
            self.wm.set_clipboard(ip)
            self.wm.toast('Copied ' + ip)

    # ------------------------------------------------------------ draw
    def state_text(self):
        st = (self.data or {}).get('state', '')
        return {'Running': 'connected', 'Stopped': 'stopped', 'NeedsLogin': 'needs login',
                'Starting': 'starting', 'NoState': 'no state'}.get(st, st.lower() or 'unknown')

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if not self.installed:
            cv.put(1, 1, 'Tailscale is not installed.', th.c('bright', 'win'))
            for i, ln in enumerate(('On Debian: sudo apt install tailscale',
                                    'then: sudo tailscale up', '',
                                    'Status/connect needs the CLI and a running tailscaled.')):
                cv.put(1, 3 + i, ln, th.c('dim' if ln else 'win', 'win'))
            return
        cv.put(1, 0, 'Tailscale', th.c('accent', 'win', 1))
        st = self.state_text()
        col = 'accent' if st == 'connected' else 'yellow' if st == 'needs login' else 'dim'
        cv.put(11, 0, ' %s ' % st, th.c('on_accent' if col == 'accent' else 'shadow', col))
        net = (self.data or {}).get('tailnet') or ''
        cv.put(max(13, w - len(net) - 2), 0, net, th.c('dim', 'win'))
        me = (self.data or {}).get('self') or {}
        if me:
            host = me.get('HostName') or self.wm.hostname
            v4 = first_ip(me.get('TailscaleIPs'), True)
            v6 = first_ip(me.get('TailscaleIPs'), False)
            cv.put(1, 1, fit(host, 20), th.c('bright', 'win', 1))
            cv.put(23, 1, (v4 or '-') + ('  ' + v6 if v6 else ''), th.c('accent', 'win'))
        x = 1
        if (self.data or {}).get('state') == 'Running':
            x += button(self, cv, x, 3, 'Disconnect', 'down') + 1
        else:
            x += button(self, cv, x, 3, 'Connect', 'up', primary=True) + 1
        x += button(self, cv, x, 3, 'Refresh', 'refresh') + 1
        if me and v4:
            x += button(self, cv, x, 3, 'Copy IP', 'copyip') + 1
        if self.login_url:
            x += button(self, cv, x, 3, 'Open login', 'login') + 1
        # peers
        y = 5
        online = sum(1 for p in (self.data or {}).get('peers', []) if p.get('Online'))
        peers = (self.data or {}).get('peers', [])
        cv.put(1, 4, '%d peers, %d online' % (len(peers), online), th.c('dim', 'win'))

        def row(cv2, rx, ry, rw, p, idx, sel):
            a = th.c('on_accent', 'accent') if sel and focused else (th.c('bright', 'sel') if sel else th.c('text', 'win'))
            if sel:
                cv2.fill(rx, ry, rw, 1, a)
            dot = sym('bullet', '.') if p.get('Online') else sym('dot', '.')
            cv2.put(rx + 1, ry, dot, a if sel else th.c('green' if p.get('Online') else 'dim', 'win'))
            name = p.get('HostName') or (p.get('DNSName') or '').split('.')[0] or '?'
            if p.get('ExitNode'):
                name += ' [exit]'
            cv2.put(rx + 3, ry, fit(name, max(8, rw - 40)), a)
            cv2.put(rx + max(10, rw - 35), ry, first_ip(p.get('TailscaleIPs'), True) or '-', a)
            cv2.put(rx + max(12, rw - 18), ry, fit((p.get('OS') or '')[:8], 8), a)
        self.list.draw(cv, 0, y, w, h - y - 2, row, th)
        self.zone(0, y, w, h - y - 2, 'list')
        msg = self.busy or self.msg or self.err
        if msg:
            cv.put(1, h - 1, fit(msg, w - 2), th.c('red' if self.err else 'dim', 'win'))
        else:
            cv.put(1, h - 1, fit('c connect   d disconnect   r refresh   b login URL', w - 2),
                   th.c('dim', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'up':
            self.action('up')
        elif key == 'down':
            self.action('down')
        elif key == 'refresh':
            self.refresh()
        elif key == 'copyip':
            ip = first_ip(((self.data or {}).get('self') or {}).get('TailscaleIPs'), True)
            if ip:
                self.wm.set_clipboard(ip)
                self.wm.toast('Copied ' + ip)
        elif key == 'login' and self.login_url:
            self.wm.open('browser', url=self.login_url)
        elif key == 'list':
            self.pressed = 'list'

    def on_mouse(self, ev):
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k is not None):
            if self.list.mouse(ev, ev.y - 5):
                self.invalidate()
                return True
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 'c':
            self.action('up')
        elif k == 'd':
            self.action('down')
        elif k in ('r', 'f5'):
            self.refresh()
        elif k == 'b' and self.login_url:
            self.wm.open('browser', url=self.login_url)
        elif self.list.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True


APP = Tailscale
