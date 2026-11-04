"""Updates: look for Debian package updates and install them.

Reading the list needs no root (`apt list --upgradable`). Refreshing the package
lists and installing go through /usr/local/sbin/monocell-apt with `sudo -n`; a
sudoers rule allows exactly `monocell-apt update` and `monocell-apt upgrade`
(deploy/x61s/install-extras.sh). On the console there is no password agent, so
the app never asks for a password itself.
"""
import os
import re
import subprocess
import threading
import time

from ..app import App
from ..util import fit, which
from ..widgets import ListView, button, hbar

HELPER = '/usr/local/sbin/monocell-apt'
LISTS = '/var/lib/apt/lists'
REBOOT_FLAG = '/run/reboot-required'
LINE_RE = re.compile(r'^(?P<name>[^/\s]+)/(?P<suite>\S+)\s+(?P<new>\S+)\s+(?P<arch>\S+)'
                     r'(?:\s+\[upgradable from:\s*(?P<old>[^\]]+)\])?')
INSTALLER = '~/.local/share/monocell/deploy/x61s/install-extras.sh'


def parse_upgradable(text):
    """`apt list --upgradable` -> [{'name', 'suite', 'new', 'old', 'security'}]."""
    out = []
    for line in (text or '').splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        suite = m.group('suite')
        out.append({'name': m.group('name'), 'suite': suite, 'new': m.group('new'),
                    'old': (m.group('old') or '').strip(), 'arch': m.group('arch'),
                    'security': 'security' in suite})
    out.sort(key=lambda p: (not p['security'], p['name']))
    return out


def progress_of(lines, total):
    """(done, label) from apt-get output: unpacked + configured packages."""
    unpack = setup = 0
    label = ''
    for ln in lines:
        if ln.startswith('Unpacking '):
            unpack += 1
            label = ln
        elif ln.startswith('Setting up '):
            setup += 1
            label = ln
        elif ln.startswith(('Get:', 'Fetched ', 'Preparing to unpack', 'Processing triggers')):
            label = ln
    steps = max(1, 2 * max(total, unpack, setup))
    return min(1.0, (unpack + setup) / steps), label


def last_check():
    try:
        return os.stat(LISTS).st_mtime
    except OSError:
        return None


def ago(t, now=None):
    if t is None:
        return 'never'
    d = max(0, int((now or time.time()) - t))
    if d < 90:
        return 'just now'
    if d < 3600:
        return '%d min ago' % (d // 60)
    if d < 2 * 86400:
        return '%d h ago' % (d // 3600)
    return '%d days ago' % (d // 86400)


class Updates(App):
    name = 'updates'
    title = 'Updates'
    icon = '[u]'
    size = (84, 26)
    min_size = (50, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.list = ListView()
        self.pkgs = []
        self.busy = ''
        self.err = ''
        self.msg = ''
        self.log = []
        self.show_log = False
        self.total = 0
        self.lock = threading.Lock()
        self.helper_ok = None
        self.ticker = None
        self.read_list()
        self.check_helper()

    # ------------------------------------------------------------ plumbing
    def sudo_cmd(self, action):
        return ['sudo', '-n', HELPER, action]

    def check_helper(self):
        def work():
            if not os.path.exists(HELPER) or not which('sudo'):
                return False
            try:
                r = subprocess.run(['sudo', '-n', '-l', HELPER, 'update'],
                                   capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                return False
            return r.returncode == 0

        def done(ok):
            self.helper_ok = ok
            self.invalidate()
        self.thread(work, done, lambda e: done(False))

    def read_list(self, after=None):
        def work():
            p = subprocess.run(['apt', 'list', '--upgradable'], capture_output=True, text=True,
                               timeout=60, env=dict(os.environ, LC_ALL='C.UTF-8'))
            return parse_upgradable(p.stdout)

        def done(pkgs):
            self.pkgs = pkgs
            self.list.set_items(pkgs, keep=True)
            if after:
                after()
            self.invalidate()

        def fail(e):
            self.err = 'apt list: %s' % e
            self.invalidate()
        self.thread(work, done, fail)

    def install_commands(self):
        """Fresh lists first: stale ones are what makes apt hit 404s in the pool."""
        return [self.sudo_cmd('update'), self.sudo_cmd('upgrade')]

    def stream(self, cmds, label, done_msg, after=None):
        """Run one or more commands off the UI thread and collect their output."""
        if cmds and isinstance(cmds[0], str):
            cmds = [cmds]
        if self.busy:
            return
        if not self.helper_ok:
            self.err = 'Not set up yet - run once: sudo %s' % INSTALLER
            self.invalidate()
            return
        self.busy = label
        self.err = ''
        self.msg = ''
        with self.lock:
            self.log = []
        self.ticker = self.every(0.4, self.invalidate)

        def work():
            for cmd in cmds:
                with self.lock:
                    self.log.append('$ ' + ' '.join(cmd[2:]))
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                     env=dict(os.environ, LC_ALL='C.UTF-8'))
                for line in p.stdout:
                    with self.lock:
                        self.log.append(line.rstrip('\n'))
                        if len(self.log) > 4000:
                            del self.log[:1000]
                rc = p.wait()
                if rc:
                    return rc
            return 0

        def finish(rc):
            self.busy = ''
            if self.ticker:
                self.ticker.cancel()
                self.ticker = None
            if rc == 0:
                self.msg = done_msg
            else:
                with self.lock:
                    tail = [ln for ln in self.log if ln.strip()][-1:] or ['failed']
                self.err = 'exit %d: %s' % (rc, tail[0][:160])
                self.show_log = True
            self.read_list(after=after if rc == 0 else None)
            self.invalidate()

        def fail(e):
            self.busy = ''
            if self.ticker:
                self.ticker.cancel()
                self.ticker = None
            self.err = str(e)
            self.invalidate()
        self.thread(work, finish, fail)

    # ------------------------------------------------------------ actions
    def check(self):
        self.stream(self.sudo_cmd('update'), 'looking for updates...', 'package lists refreshed')

    def install(self):
        if not self.pkgs:
            self.wm.toast('Everything is up to date')
            return
        self.total = len(self.pkgs)

        def go():
            self.show_log = True
            self.stream(self.install_commands(), 'installing %d updates...' % self.total,
                        'updates installed', after=self.after_install)
        self.wm.confirm('Install updates', 'Install %d package updates now?\n'
                        'Do not switch off the computer while it runs.' % len(self.pkgs),
                        go, yes='Install', parent=self.win)

    def after_install(self):
        if os.path.exists(REBOOT_FLAG):
            self.wm.toast('Updates installed - a reboot is needed', 'yellow', 6)

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.put(1, 0, 'Debian updates', th.c('accent', 'win', 1))
        sec = sum(1 for p in self.pkgs if p['security'])
        n = len(self.pkgs)
        chip = ('%d updates' % n if n else 'up to date')
        col = 'red' if sec else ('accent' if n else 'green')
        cv.put(17, 0, ' %s ' % chip, th.c('on_accent', col))
        if sec:
            cv.put(19 + len(chip), 0, '%d security' % sec, th.c('red', 'win'))
        info = 'last check: %s' % ago(last_check())
        cv.put(max(1, w - len(info) - 1), 0, info, th.c('dim', 'win'))
        x = 1
        busy = bool(self.busy)
        x += button(self, cv, x, 2, 'Check', 'check', enabled=not busy) + 1
        x += button(self, cv, x, 2, 'Install all', 'install', primary=bool(n),
                    enabled=not busy and bool(n)) + 1
        x += button(self, cv, x, 2, 'Packages' if self.show_log else 'Log', 'log') + 1
        reboot = os.path.exists(REBOOT_FLAG)
        if reboot:
            x += button(self, cv, x, 2, 'Reboot', 'reboot', primary=True) + 1
            cv.put(x + 1, 2, 'reboot needed', th.c('yellow', 'win'))
        y = 4
        body_h = max(1, h - y - 2)
        if self.helper_ok is False:
            cv.put(1, 3, fit('Read-only: to check and install, run once in a terminal:', w - 2),
                   th.c('yellow', 'win'))
            cv.put(3, 4, fit('sudo ' + INSTALLER, w - 4), th.c('bright', 'win'))
            y = 6
            body_h = max(1, h - y - 2)
        if self.busy and self.total and self.busy.startswith('installing'):
            with self.lock:
                frac, label = progress_of(self.log, self.total)
            hbar(cv, 1, y, w - 2, frac, th)
            cv.put(1, y + 1, fit(label, w - 2), th.c('dim', 'win'))
            y += 2
            body_h = max(1, h - y - 2)
        if self.show_log:
            with self.lock:
                lines = list(self.log[-body_h:])
            for i, ln in enumerate(lines):
                cv.put(1, y + i, fit(ln, w - 2), th.c('text', 'win'))
        else:
            self.list_y = y

            def row(cv2, rx, ry, rw, p, idx, sel):
                a = th.c('on_accent', 'accent') if sel and focused else \
                    (th.c('bright', 'sel') if sel else th.c('text', 'win'))
                if sel:
                    cv2.fill(rx, ry, rw, 1, a)
                mark = '!' if p['security'] else ' '
                cv2.put(rx + 1, ry, mark, a if sel else th.c('red', 'win'))
                namew = max(10, min(32, rw // 3))
                cv2.put(rx + 3, ry, fit(p['name'], namew), a)
                ver = '%s -> %s' % (p['old'] or '?', p['new'])
                vw = max(8, rw - namew - 6 - 16)
                cv2.put(rx + 4 + namew, ry, fit(ver, vw), a if sel else th.c('dim', 'win'))
                suite = p['suite'].replace('stable-', '').replace('-security', ' sec')
                cv2.put(rx + rw - 15, ry, fit(suite, 14),
                        a if sel else th.c('red' if p['security'] else 'dim', 'win'))
            if self.pkgs:
                self.list.draw(cv, 0, y, w, body_h, row, th)
                self.zone(0, y, w, body_h, 'list')
            else:
                cv.put(2, y + 1, 'No updates known. "Check" refreshes the package lists.',
                       th.c('dim', 'win'))
        msg = self.busy or self.err or self.msg
        cv.put(1, h - 1, fit(msg or 'c check  i install all  l log  enter details', w - 2),
               th.c('red' if self.err else ('accent' if self.busy else 'dim'), 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'check':
            self.check()
        elif key == 'install':
            self.install()
        elif key == 'log':
            self.show_log = not self.show_log
        elif key == 'reboot':
            self.wm.confirm_reboot()
        elif key == 'list':
            self.pressed = 'list'

    def on_mouse(self, ev):
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k == 'list'):
            if self.list.mouse(ev, ev.y - getattr(self, 'list_y', 4)):
                self.invalidate()
            return True
        return super().on_mouse(ev)

    def details(self):
        p = self.list.current()
        if p:
            self.wm.toast('%s %s -> %s (%s)' % (p['name'], p['old'], p['new'], p['suite']), 'accent', 6)

    def on_key(self, ev):
        k = ev.key
        if k == 'c':
            self.check()
        elif k == 'i':
            self.install()
        elif k == 'l':
            self.show_log = not self.show_log
        elif k == 'enter':
            self.details()
        elif not self.show_log and self.list.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True

    def on_close(self):
        if self.busy and self.busy.startswith('installing'):
            self.wm.toast('Updates are still being installed', 'yellow')
            return False
        return True


APP = Updates
