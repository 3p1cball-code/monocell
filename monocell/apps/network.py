"""Network: wifi networks, signal, connect/disconnect and the radio switch."""
import subprocess

from .. import hardware
from ..app import App
from ..util import fit, which
from ..widgets import ListView, button


def terse_fields(line):
    """Split one `nmcli -t` line; backslash escapes ':' and '\\'."""
    out = []
    cur = []
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == '\\':
            esc = True
        elif ch == ':':
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out


def portal_address(connection, gateway=None):
    """URL that a captive portal will intercept: known ICE portal or the gateway."""
    low = (connection or '').lower()
    if 'ice' in low or 'bahn' in low:
        return 'http://wifi.bahn.de/'
    return 'http://%s/' % gateway if gateway else 'http://neverssl.com/'


def parse_networks(text):
    nets = {}
    for line in (text or '').splitlines():
        f = terse_fields(line)
        if len(f) < 4:
            continue
        in_use = f[0].strip() == '*'
        ssid = f[1]
        try:
            signal = int(f[2])
        except ValueError:
            signal = 0
        if not ssid:
            continue
        old = nets.get(ssid)
        if old is None or signal > old['signal']:
            nets[ssid] = {'ssid': ssid, 'signal': signal, 'security': f[3].strip(),
                          'in_use': in_use or bool(old and old['in_use'])}
        elif in_use:
            old['in_use'] = True
    return sorted(nets.values(), key=lambda n: (not n['in_use'], -n['signal'], n['ssid'].lower()))


class Network(App):
    name = 'network'
    title = 'Network'
    icon = ')))'
    size = (78, 24)
    min_size = (44, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.list = ListView()
        self.list.on_activate = self.connect
        self.networks = []
        self.state = {}
        self.err = ''
        self.msg = ''
        self.busy = ''
        self.installed = bool(which('nmcli'))
        self.scan(rescan=True)
        self.every(20, self.refresh)

    # ------------------------------------------------------------ commands
    def run(self, args, timeout=20):
        try:
            p = subprocess.run(['nmcli'] + args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return 127, '', 'nmcli not installed'
        except subprocess.TimeoutExpired:
            return 124, '', 'timeout'
        except OSError as e:
            return 1, '', str(e)
        return p.returncode, p.stdout, p.stderr

    def scan(self, rescan=False):
        if self.busy or not self.installed:
            return

        def work():
            if rescan:
                self.run(['device', 'wifi', 'rescan'], 12)
            dev = self.run(['-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device'])
            radio = self.run(['radio', 'wifi'])
            nets = self.run(['-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY',
                             'device', 'wifi', 'list', '--rescan', 'no'], 25)
            conn = self.run(['-t', '-f', 'CONNECTIVITY', 'general'])
            return dev, radio, nets, conn

        def done(res):
            dev, radio, nets, conn = res
            self.busy = ''
            state = {'radio': 'enabled' in (radio[1] or ''), 'device': '', 'state': '',
                     'connection': '', 'connectivity': (conn[1] or '').strip()}
            for line in (dev[1] or '').splitlines():
                f = terse_fields(line)
                if len(f) >= 4 and f[1] == 'wifi':
                    state.update(device=f[0], state=f[2], connection=f[3])
                    break
            self.state = state
            if nets[0] == 0:
                self.networks = parse_networks(nets[1])
                self.err = ''
            else:
                self.err = ((nets[2] or nets[1]).strip().splitlines() or ['wifi list failed'])[-1][:160]
            self.list.set_items(self.networks, keep=True)
            self.invalidate()

        def fail(e):
            self.busy = ''
            self.err = str(e)
            self.invalidate()
        self.busy = 'scanning...'
        self.thread(work, done, fail)

    def refresh(self):
        self.scan(rescan=False)

    def action(self, args, msg, timeout=60, ok=None):
        if self.busy or not self.installed:
            return
        self.busy = msg
        self.msg = ''
        self.err = ''
        self.invalidate()

        def work():
            return self.run(args, timeout)

        def done(res):
            rc, out, err = res
            self.busy = ''
            text = (err or out or '').strip()
            if rc == 0:
                self.msg = ok or msg
                self.scan(rescan=False)
            else:
                self.err = (text.splitlines() or ['failed'])[-1][:160]
                self.invalidate()

        def fail(e):
            self.busy = ''
            self.err = str(e)
            self.invalidate()
        self.thread(work, done, fail)

    def connect(self, net=None):
        net = net or self.list.current()
        if not net or self.busy:
            return
        if net.get('in_use'):
            self.wm.toast('Already connected to ' + net['ssid'])
            return
        sec = net.get('security') or ''
        if '802.1X' in sec or 'EAP' in sec:
            self.wm.toast('Enterprise network - configure it with nmcli', 'yellow')
            return
        if sec:
            self.wm.prompt('Connect to %s' % net['ssid'],
                           'Password for "%s" (%s):' % (net['ssid'], sec), '',
                           lambda pw: self.connect_with(net, pw) if pw else None,
                           parent=self.win, password=True)
        else:
            self.connect_with(net, '')

    def portal_url(self):
        name = self.state.get('connection') or ''
        for n in self.networks:
            if n.get('in_use'):
                name += ' ' + n['ssid']
                break
        return portal_address(name, hardware.default_gateway())

    def open_portal(self):
        if not self.state.get('connection'):
            self.wm.toast('Connect to a network first', 'yellow')
            return
        self.wm.open('browser', url=self.portal_url())

    def connect_with(self, net, password):
        if self.busy:
            return
        args = ['device', 'wifi', 'connect', net['ssid']]
        if password:
            args += ['password', password]
        self.action(args, 'connecting to %s...' % net['ssid'], ok='connected to %s' % net['ssid'])

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if not self.installed:
            cv.put(1, 1, 'NetworkManager (nmcli) is not installed.', th.c('bright', 'win'))
            cv.put(1, 3, 'On Debian: sudo apt install network-manager', th.c('dim', 'win'))
            return
        st = self.state
        conn = st.get('connection') or ''
        on = st.get('radio', True)
        portal = conn and st.get('connectivity') in ('portal', 'limited')
        cv.put(1, 0, 'Network', th.c('accent', 'win', 1))
        chip = 'sign-in' if portal else 'connected' if conn else 'disconnected'
        col = 'yellow' if portal else 'accent' if conn else 'dim'
        cv.put(10, 0, ' %s ' % chip, th.c('on_accent' if conn else 'shadow', col))
        cv.put(max(22, w - 12), 0, 'radio: ' + ('on' if on else 'off'),
               th.c('text' if on else 'red', 'win'))
        dev = st.get('device') or '-'
        ip = self.wm.if_ip(st['device']) if st.get('device') else ''
        line = dev
        if conn:
            line += '  ' + conn
        if ip:
            line += '  ' + ip
        cv.put(1, 1, fit(line, w - 20), th.c('bright', 'win'))
        if conn:
            pct = hardware.wifi_signal(st['device'])
            if pct is not None:
                cv.put(w - 12, 1, '%s %3d%%' % (hardware.signal_bars(pct), pct),
                       th.c('accent', 'win'))
        x = 1
        x += button(self, cv, x, 3, 'Rescan', 'rescan') + 1
        x += button(self, cv, x, 3, 'Wi-Fi off' if on else 'Wi-Fi on', 'radio') + 1
        if conn:
            x += button(self, cv, x, 3, 'Disconnect', 'off') + 1
            x += button(self, cv, x, 3, 'Portal', 'portal',
                        primary=bool(portal)) + 1
        y = 5
        cv.put(1, 4, '%d networks' % len(self.networks), th.c('dim', 'win'))

        def row(cv2, rx, ry, rw, n, idx, sel):
            a = th.c('on_accent', 'accent') if sel and focused else \
                (th.c('bright', 'sel') if sel else th.c('text', 'win'))
            if sel:
                cv2.fill(rx, ry, rw, 1, a)
            cv2.put(rx + 1, ry, '*' if n['in_use'] else ' ',
                    a if sel else th.c('accent' if n['in_use'] else 'dim', 'win'))
            bars = hardware.signal_bars(n['signal'])
            cv2.put(rx + 3, ry, bars,
                    a if sel else th.c('green' if n['signal'] >= 67 else
                                       'yellow' if n['signal'] >= 34 else 'red', 'win'))
            sec = n['security'] or 'open'
            sw = min(14, max(4, len(sec)))
            namew = max(6, rw - 15 - sw - 2)
            cv2.put(rx + 7, ry, fit(n['ssid'], namew), a)
            cv2.put(rx + rw - sw - 1, ry, fit(sec, sw),
                    a if sel else th.c('dim', 'win'))
        self.list.draw(cv, 0, y, w, max(1, h - y - 2), row, th)
        self.zone(0, y, w, max(1, h - y - 2), 'list')
        msg = self.busy or self.err or self.msg
        if msg:
            cv.put(1, h - 1, fit(msg, w - 2), th.c('red' if self.err else 'dim', 'win'))
        else:
            cv.put(1, h - 1,
                   fit('enter connect  r rescan  w wifi on/off  d disconnect  p portal', w - 2),
                   th.c('dim', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'rescan':
            self.scan(rescan=True)
        elif key == 'radio':
            off = self.state.get('radio', True)
            self.action(['radio', 'wifi', 'off' if off else 'on'],
                        'turning wifi %s' % ('off' if off else 'on'),
                        ok='wifi ' + ('off' if off else 'on'))
        elif key == 'off':
            if self.state.get('device'):
                self.action(['device', 'disconnect', self.state['device']], 'disconnecting', ok='disconnected')
        elif key == 'portal':
            self.open_portal()
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
        if k == 'r':
            self.scan(rescan=True)
        elif k == 'w':
            off = self.state.get('radio', True)
            self.action(['radio', 'wifi', 'off' if off else 'on'],
                        'turning wifi %s' % ('off' if off else 'on'),
                        ok='wifi ' + ('off' if off else 'on'))
        elif k == 'd':
            if self.state.get('device') and self.state.get('connection'):
                self.action(['device', 'disconnect', self.state['device']], 'disconnecting', ok='disconnected')
        elif k == 'c':
            self.connect()
        elif k == 'p':
            self.open_portal()
        elif self.list.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True


APP = Network
