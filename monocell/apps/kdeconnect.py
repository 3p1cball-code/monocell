"""KDE Connect: devices, clipboard bridge and file transfer.

Uses the kdeconnect-cli tools. On a text console there is no system clipboard, so
``deploy/x61s/install-kdeconnect.sh`` can run a headless Xvfb display with the KDE
Connect daemon; xclip then moves text between that display and Monocell.
"""
import os
import re
import subprocess

from ..app import App
from ..util import fit, which
from ..widgets import ListView, button

DEV_RE = re.compile(r'^- (.*?): (\S+?)(?: on (\S+) via (\w+))?(?: \((.*?)\))?$')


def parse_devices(out):
    devs = []
    for line in (out or '').splitlines():
        m = DEV_RE.match(line.strip())
        if not m:
            continue
        name, did, ip, transport, state = m.groups()
        state = state or ''
        devs.append({'name': name.strip(), 'id': did, 'ip': ip or '',
                     'transport': transport or '', 'state': state,
                     'paired': 'paired' in state and 'unpaired' not in state,
                     'reachable': 'reachable' in state})
    return devs


class KDEConnect(App):
    name = 'kdeconnect'
    title = 'KDE Connect'
    icon = '[K]'
    size = (86, 26)
    min_size = (50, 14)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.list = ListView()
        self.devices = []
        self.err = ''
        self.msg = ''
        self.busy = ''
        self.installed = bool(which('kdeconnect-cli'))
        self.has_xclip = bool(which('xclip'))
        self.display = (wm.cfg.get('kdeconnect_display') or ':99').strip() or ':99'
        self.list_y = 2
        self.refresh()
        self.every(12, self.refresh)

    # ------------------------------------------------------------ commands
    def run(self, args, timeout=20):
        try:
            p = subprocess.run(['kdeconnect-cli'] + args, capture_output=True,
                               text=True, timeout=timeout)
        except FileNotFoundError:
            return 127, '', 'kdeconnect-cli not installed'
        except subprocess.TimeoutExpired:
            return 124, '', 'timeout'
        except OSError as e:
            return 1, '', str(e)
        return p.returncode, p.stdout, p.stderr

    def refresh(self):
        if self.busy or not self.installed:
            return

        def work():
            return self.run(['-l'], 8)

        def done(res):
            rc, out, err = res
            self.devices = parse_devices(out)
            self.list.set_items(self.devices, keep=True)
            if rc == 0:
                self.err = ''
            else:
                self.err = (err or out or 'kdeconnect-cli failed').strip().splitlines()[-1][:140]
            self.invalidate()

        def fail(e):
            self.err = str(e)
            self.invalidate()
        self.thread(work, done, fail)

    def device(self):
        return self.list.current()

    def action(self, args, label):
        dev = self.device()
        if not dev or self.busy:
            return
        self.busy = label
        self.msg = label + '...'
        self.invalidate()

        def work():
            return self.run(['-d', dev['id']] + args, 25)

        def done(res):
            rc, out, err = res
            self.busy = ''
            txt = (out or err or '').strip().splitlines()
            self.msg = (txt[-1][:140] if txt else ('%s ok' % label)) if rc == 0 else \
                       ((err or out or 'failed').strip().splitlines()[-1][:140])
            self.refresh()

        def fail(e):
            self.busy = ''
            self.msg = str(e)
            self.invalidate()
        self.thread(work, done, fail)

    def ping(self):
        self.action(['--ping'], 'ping')

    def ring(self):
        self.action(['--ring'], 'ring')

    def pair(self):
        dev = self.device()
        if not dev:
            return
        if dev['paired']:
            self.wm.confirm('Unpair', 'Unpair %s from this computer?' % dev['name'],
                            lambda: self.action(['--unpair'], 'unpair'), yes='Unpair')
        else:
            self.action(['--pair'], 'pair')

    def share_clipboard(self):
        text = (self.wm.clipboard or '').strip()
        if not text:
            self.wm.toast('Clipboard is empty', 'yellow')
            return
        self.action(['--share-text', text[:20000]], 'send clipboard')

    def share_file(self):
        def picked(path):
            if path:
                self.action(['--share', path], 'share file')
        self.wm.pick_file(picked, 'open', title='Share with KDE Connect')

    def xclip(self, *args, text=None, timeout=5):
        if not self.has_xclip:
            return 127, '', 'xclip not installed'
        env = dict(os.environ)
        env['DISPLAY'] = self.display
        try:
            p = subprocess.run(['xclip', '-d', self.display] + list(args),
                               input=text, capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, '', 'timeout'
        except OSError as e:
            return 1, '', str(e)
        return p.returncode, p.stdout, p.stderr

    def pull_clipboard(self):
        rc, out, err = self.xclip('-selection', 'clipboard', '-o')
        if rc != 0:
            self.wm.toast('No X clipboard on %s (%s)' % (self.display, (err or '').strip()[:60]),
                          'yellow')
            return
        out = out.rstrip('\n')
        if not out:
            self.wm.toast('KDE Connect clipboard is empty', 'yellow')
            return
        self.wm.set_clipboard(out)
        self.wm.toast('Pulled %d chars from KDE Connect' % len(out))

    def newest_download(self):
        d = os.path.expanduser('~/Downloads')
        try:
            files = [os.path.join(d, n) for n in os.listdir(d)
                     if os.path.isfile(os.path.join(d, n))]
        except OSError:
            files = []
        if not files:
            self.wm.toast('No received files in ~/Downloads', 'yellow')
            return
        self.import_file(max(files, key=os.path.getmtime))

    def import_file(self, path):
        try:
            if os.path.getsize(path) > 1_000_000:
                raise ValueError('file too large')
            with open(path, 'rb') as f:
                raw = f.read()
            if b'\0' in raw:
                raise ValueError('binary file')
            text = raw.decode('utf-8')
        except (OSError, UnicodeDecodeError, ValueError) as e:
            self.wm.toast('Cannot paste %s: %s' % (os.path.basename(path), e), 'yellow')
            return
        self.wm.set_clipboard(text)
        self.wm.toast('Loaded %s (%d chars)' % (os.path.basename(path), len(text)))

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if not self.installed:
            cv.put(1, 1, 'KDE Connect is not installed.', th.c('bright', 'win'))
            lines = ('Install it on the X61s with:',
                     '  sudo ~/monocell-src/deploy/x61s/install-kdeconnect.sh --user ariedel',
                     '',
                     'That also starts a headless X display (:99) so the phone can',
                     'send its clipboard even without a desktop session.',
                     'The script needs the network once (apt).')
            for i, ln in enumerate(lines):
                cv.put(1, 3 + i, ln, th.c('dim' if ln else 'win', 'win'))
            return
        cv.put(1, 0, 'KDE Connect', th.c('accent', 'win', 1))
        reach = sum(1 for d in self.devices if d['reachable'])
        cv.put(14, 0, '%d devices, %d reachable' % (len(self.devices), reach),
               th.c('dim', 'win'))
        cv.put(max(16, w - 17), 0, 'clipboard ' + self.display,
               th.c('green' if self.has_xclip else 'dim', 'win'))
        y = 2
        self.list_y = y
        if self.devices:
            def row(cv2, rx, ry, rw, d, idx, sel):
                a = th.c('on_accent', 'accent') if sel and focused else \
                    (th.c('bright', 'sel') if sel else th.c('text', 'win'))
                if sel:
                    cv2.fill(rx, ry, rw, 1, a)
                mark = '*' if d['reachable'] else '-'
                cv2.put(rx + 1, ry, mark, a if sel else th.c('green' if d['reachable'] else 'dim', 'win'))
                cv2.put(rx + 3, ry, fit(d['name'], max(8, rw - 28)), a)
                state = 'paired' if d['paired'] else 'unpaired'
                if d['reachable']:
                    state += ' · reachable'
                cv2.put(rx + max(10, rw - 24), ry, fit(state, 23), a)
            self.list.draw(cv, 1, y, w - 2, max(4, h - y - 8), row, th)
        else:
            cv.put(1, y, 'No devices yet.', th.c('bright', 'win'))
            cv.put(1, y + 2, 'Pair once from the KDE Connect app on the phone (Add device),',
                   th.c('dim', 'win'))
            cv.put(1, y + 3, 'then press P here. Both must be in the same network.',
                   th.c('dim', 'win'))
        self.zone(1, y, w - 2, max(4, h - y - 8), 'list')
        by = h - 6
        x = 1
        x += button(self, cv, x, by, 'Pair', 'pair') + 1
        x += button(self, cv, x, by, 'Ping', 'ping') + 1
        x += button(self, cv, x, by, 'Ring', 'ring') + 1
        x += button(self, cv, x, by, 'Send clip', 'send') + 1
        x += button(self, cv, x, by, 'Pull clip', 'pull', primary=True, enabled=self.has_xclip) + 1
        x += button(self, cv, x, by, 'Share file', 'file') + 1
        if x + 10 < w:
            button(self, cv, x, by, 'Newest', 'newest')
        clip = self.wm.clipboard or ''
        first = clip.splitlines()[0][:max(10, w - 24)] if clip else '(empty)'
        cv.put(1, h - 4, 'Clipboard: %d chars' % len(clip), th.c('dim', 'win'))
        cv.put(1, h - 3, fit(first, w - 2), th.c('text', 'win'))
        msg = self.busy or self.msg or self.err
        if msg:
            cv.put(1, h - 1, fit(msg, w - 2), th.c('red' if self.err else 'dim', 'win'))
        else:
            cv.put(1, h - 1, fit('P pair  p ping  s send  v pull  f share  n newest', w - 2),
                   th.c('dim', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'pair':
            self.pair()
        elif key == 'ping':
            self.ping()
        elif key == 'ring':
            self.ring()
        elif key == 'send':
            self.share_clipboard()
        elif key == 'pull':
            self.pull_clipboard()
        elif key == 'file':
            self.share_file()
        elif key == 'newest':
            self.newest_download()
        elif key == 'list':
            self.pressed = 'list'

    def on_mouse(self, ev):
        k = self.track_hover(ev)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k is not None):
            if self.list.mouse(ev, ev.y - self.list_y):
                self.invalidate()
                return True
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k in ('up', 'down', 'pgup', 'pgdn', 'home', 'end'):
            if self.list.key(ev):
                self.invalidate()
                return True
            return False
        if k == 'p':
            self.ping()
        elif k == 'r':
            self.ring()
        elif k in ('a', 'shift+p'):
            self.pair()
        elif k == 's':
            self.share_clipboard()
        elif k == 'v':
            self.pull_clipboard()
        elif k == 'f':
            self.share_file()
        elif k == 'n':
            self.newest_download()
        elif k in ('f5', 'ctrl+r'):
            self.refresh()
        else:
            return False
        self.invalidate()
        return True


APP = KDEConnect
