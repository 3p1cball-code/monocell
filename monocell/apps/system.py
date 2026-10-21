"""System monitor: host info, CPU/memory/disk/battery, network, process list (task manager)."""
import os
import platform
import signal
import socket
import time

from ..app import App
from ..util import fit, human_size
from ..widgets import ListView, button, hbar


def read(path, default=''):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def cpu_times():
    out = []
    for line in read('/proc/stat').splitlines():
        if line.startswith('cpu'):
            p = line.split()
            vals = [int(x) for x in p[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            out.append((sum(vals), idle))
    return out


def meminfo():
    m = {}
    for line in read('/proc/meminfo').splitlines():
        k, _, v = line.partition(':')
        try:
            m[k] = int(v.split()[0]) * 1024
        except (ValueError, IndexError):
            pass
    return m


def os_name():
    for line in read('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='):
            return line.split('=', 1)[1].strip('"')
    return platform.system()


def cpu_model():
    for line in read('/proc/cpuinfo').splitlines():
        if line.startswith('model name'):
            return ' '.join(line.split(':', 1)[1].split())
    return platform.machine()


def uptime():
    try:
        s = float(read('/proc/uptime').split()[0])
    except (ValueError, IndexError):
        return '?'
    d, s = divmod(int(s), 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return ('%dd ' % d if d else '') + '%dh %02dm' % (h, m)


def ip_addrs():
    out = []
    try:
        import fcntl
        import struct
        for idx, name in socket.if_nameindex():
            if name == 'lo':
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', name[:15].encode()))[20:24])
                out.append((name, ip))
            except OSError:
                pass
            finally:
                s.close()
    except (OSError, AttributeError):
        pass
    return out


def disks():
    seen = set()
    out = []
    for line in read('/proc/mounts').splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        dev, mnt, fs = p[0], p[1].replace('\\040', ' '), p[2]
        if fs in ('proc', 'sysfs', 'tmpfs', 'devtmpfs', 'devpts', 'cgroup', 'cgroup2', 'securityfs', 'pstore',
                  'debugfs', 'tracefs', 'configfs', 'fusectl', 'mqueue', 'hugetlbfs', 'bpf', 'autofs',
                  'binfmt_misc', 'efivarfs', 'overlay', 'squashfs', 'nsfs', 'ramfs', 'rpc_pipefs', 'fuse.portal',
                  'fuse.gvfsd-fuse', 'selinuxfs'):
            continue
        if mnt.startswith(('/snap', '/var/lib/docker', '/run/user')) or dev in seen:
            continue
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total < 64 * 1024 * 1024:
            continue
        seen.add(dev)
        out.append((mnt, total, total - st.f_bavail * st.f_frsize))
    return out[:8]


def battery():
    base = '/sys/class/power_supply'
    out = []
    try:
        for n in sorted(os.listdir(base)):
            p = os.path.join(base, n)
            if read(os.path.join(p, 'type')).strip() != 'Battery':
                continue
            cap = read(os.path.join(p, 'capacity')).strip()
            st = read(os.path.join(p, 'status')).strip()
            ef = read(os.path.join(p, 'energy_full')).strip() or read(os.path.join(p, 'charge_full')).strip()
            ed = read(os.path.join(p, 'energy_full_design')).strip() or read(os.path.join(p, 'charge_full_design')).strip()
            health = ''
            if ef and ed and int(ed):
                health = '%d%%' % (100 * int(ef) // int(ed))
            out.append((n, int(cap or 0), st, health))
    except (OSError, ValueError):
        pass
    return out


def temperature():
    best = None
    base = '/sys/class/thermal'
    try:
        for n in os.listdir(base):
            if n.startswith('thermal_zone'):
                t = read(os.path.join(base, n, 'temp')).strip()
                if t:
                    v = int(t) / 1000.0
                    if best is None or v > best:
                        best = v
    except (OSError, ValueError):
        pass
    return best


class Proc:
    __slots__ = ('pid', 'name', 'user', 'cpu', 'mem', 'ticks', 'state')


def processes(prev, dt_ticks):
    out = []
    pagesize = os.sysconf('SC_PAGE_SIZE')
    uids = {}
    try:
        import pwd
        for p in pwd.getpwall():
            uids[p.pw_uid] = p.pw_name
    except Exception:
        pass
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % d) as f:
                st = f.read()
            name = st[st.index('(') + 1:st.rindex(')')]
            rest = st[st.rindex(')') + 2:].split()
            ticks = int(rest[11]) + int(rest[12])
            rss = int(rest[21]) * pagesize
            uid = os.stat('/proc/%s' % d).st_uid
        except (OSError, ValueError, IndexError):
            continue
        p = Proc()
        p.pid = int(d)
        p.name = name
        p.user = uids.get(uid, str(uid))
        p.mem = rss
        p.ticks = ticks
        p.state = rest[0]
        old = prev.get(p.pid)
        p.cpu = 100.0 * (ticks - old) / dt_ticks if old is not None and dt_ticks > 0 else 0.0
        out.append(p)
    return out


class System(App):
    name = 'system'
    title = 'System'
    icon = '[i]'
    size = (78, 26)
    min_size = (40, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.tab = 'overview'
        self.prev_cpu = cpu_times()
        self.cpu = [0.0] * len(self.prev_cpu)
        self.hist = []
        self.prev_ticks = {}
        self.prev_t = time.monotonic()
        self.procs = []
        self.sort = 'cpu'
        self.list = ListView()
        self.static = {
            'host': socket.gethostname(), 'os': os_name(), 'kernel': platform.release(),
            'cpu': cpu_model(), 'arch': platform.machine(), 'cores': os.cpu_count() or 1,
            'py': platform.python_version(),
        }
        self.slow = {}
        self.refresh_slow()
        self.update()
        self.every(1.5, self.update)
        self.every(10, self.refresh_slow)

    def refresh_slow(self):
        self.slow = {'ips': ip_addrs(), 'disks': disks(), 'bat': battery()}

    def update(self):
        cur = cpu_times()
        self.cpu = []
        for (t1, i1), (t0, i0) in zip(cur, self.prev_cpu):
            dt = t1 - t0
            self.cpu.append(0.0 if dt <= 0 else 100.0 * (1 - (i1 - i0) / dt))
        self.prev_cpu = cur
        if self.cpu:
            self.hist.append(self.cpu[0])
            self.hist = self.hist[-200:]
        if self.tab == 'procs':
            now = time.monotonic()
            hz = os.sysconf('SC_CLK_TCK')
            dt_ticks = (now - self.prev_t) * hz
            sel = self.list.current()
            sel_pid = sel.pid if sel else None
            self.procs = processes(self.prev_ticks, dt_ticks)
            self.prev_ticks = {p.pid: p.ticks for p in self.procs}
            self.prev_t = now
            key = {'cpu': lambda p: -p.cpu, 'mem': lambda p: -p.mem, 'pid': lambda p: p.pid,
                   'name': lambda p: p.name.lower()}[self.sort]
            self.procs.sort(key=key)
            self.list.set_items(self.procs, keep=True)
            if sel_pid is not None:
                for i, p in enumerate(self.procs):
                    if p.pid == sel_pid:
                        self.list.sel = i
                        break
        self.invalidate()

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.zone_off = (0, 0)
        x = 0
        for label, key in (('Overview', 'overview'), ('Processes', 'procs'), ('monocell', 'tw')):
            bw = button(self, cv, x, 0, label, ('tab', key), active=self.tab == key)
            # the empty row under the tab still belongs to it (easier to hit)
            self.zone(x, 1, bw, 1, ('tab', key))
            x += bw + 1
        body = cv.sub(0, 2, w, h - 2)
        if self.tab == 'overview':
            self.draw_overview(body)
        elif self.tab == 'procs':
            self.draw_procs(body, focused)
        else:
            self.draw_tw(body)

    def kv(self, cv, x, y, k, v, w):
        th = self.th
        cv.put(x, y, k, th.c('dim', 'win'))
        cv.put(x + 10, y, fit(v, max(1, w - 10)), th.c('bright', 'win'))

    def draw_overview(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        s = self.static
        two = w >= 70
        lw = w // 2 - 1 if two else w
        y = 0
        rows = [('Host', s['host']), ('OS', s['os']), ('Kernel', '%s (%s)' % (s['kernel'], s['arch'])),
                ('CPU', '%s x%d' % (s['cpu'], s['cores'])), ('Uptime', uptime()),
                ('Terminal', '%dx%d  %s' % (self.wm.w, self.wm.h, self.wm.term.mode))]
        t = temperature()
        if t is not None:
            rows.append(('Temp', '%.0f C' % t))
        for k, v in rows:
            self.kv(cv, 0, y, k, v, lw)
            y += 1
        for name, ip in self.slow.get('ips', [])[:3]:
            self.kv(cv, 0, y, name[:9], ip, lw)
            y += 1
        for name, cap, st, health in self.slow.get('bat', []):
            self.kv(cv, 0, y, 'Battery', '%d%% %s%s' % (cap, st, ('  health ' + health) if health else ''), lw)
            y += 1
        # right column (or below): gauges
        if two:
            gx, gy, gw = w // 2 + 1, 0, w - w // 2 - 1
        else:
            gx, gy, gw = 0, y + 1, w
        bw = gw - 16
        cv.put(gx, gy, 'CPU', th.c('dim', 'win'))
        tot = self.cpu[0] if self.cpu else 0
        hbar(cv, gx + 10, gy, bw, tot / 100, th)
        cv.put(gx + 11 + bw, gy, '%3.0f%%' % tot, th.c('bright', 'win'))
        gy += 1
        per = self.cpu[1:]
        for i, v in enumerate(per[:8]):
            cv.put(gx, gy, ' core%d' % i, th.c('dim', 'win'))
            hbar(cv, gx + 10, gy, bw, v / 100, th, fg='accent2')
            cv.put(gx + 11 + bw, gy, '%3.0f%%' % v, th.c('text', 'win'))
            gy += 1
        m = meminfo()
        tot_m = m.get('MemTotal', 1)
        used = tot_m - m.get('MemAvailable', 0)
        cv.put(gx, gy, 'Memory', th.c('dim', 'win'))
        hbar(cv, gx + 10, gy, bw, used / tot_m, th)
        cv.put(gx + 11 + bw, gy, '%3.0f%%' % (100 * used / tot_m), th.c('bright', 'win'))
        gy += 1
        cv.put(gx + 10, gy, fit('%s of %s' % (human_size(used), human_size(tot_m)), gw - 10), th.c('dim', 'win'))
        gy += 1
        sw = m.get('SwapTotal', 0)
        if sw:
            su = sw - m.get('SwapFree', 0)
            cv.put(gx, gy, 'Swap', th.c('dim', 'win'))
            hbar(cv, gx + 10, gy, bw, su / sw, th, fg='accent2')
            cv.put(gx + 11 + bw, gy, '%3.0f%%' % (100 * su / sw), th.c('text', 'win'))
            gy += 1
        gy += 1
        for mnt, total, usedd in self.slow.get('disks', []):
            if gy >= h - 3:
                break
            label = mnt if len(mnt) <= 9 else ('...' + mnt[-6:])
            cv.put(gx, gy, fit(label, 9), th.c('dim', 'win'))
            hbar(cv, gx + 10, gy, bw, usedd / total if total else 0, th, fg='accent2')
            cv.put(gx + 11 + bw, gy, '%3.0f%%' % (100 * usedd / total), th.c('text', 'win'))
            gy += 1
        # cpu history graph (ASCII)
        top = max(gy, y) + 1
        gh = h - top
        if gh >= 3:
            cv.put(0, top - 0, 'CPU history', th.c('dim', 'win'))
            if len(self.hist) < 3:
                cv.put(11, top, ' collecting samples...', th.c('dim', 'win'))
            self.graph(cv, 0, top + 1, w, gh - 1, self.hist)

    def graph(self, cv, x, y, w, h, data):
        th = self.th
        # faint grid at 25/50/75 %
        for frac in (0.25, 0.5, 0.75):
            cv.hline(x, y + h - 1 - int(h * frac), w, th.c('sel', 'win'), '.')
        data = data[-w:]
        off = w - len(data)
        for i, v in enumerate(data):
            lv = v / 100.0 * h
            full = int(lv)
            frac = lv - full
            for r in range(h):
                ry = y + h - 1 - r
                if r < full:
                    cv.putc(x + off + i, ry, '|', th.c('accent', 'win'))
                elif r == full and frac > 0.33:
                    cv.putc(x + off + i, ry, '.', th.c('accent', 'win'))
        cv.hline(x, y + h - 1, off, th.c('sel', 'win'), '_')

    def draw_procs(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.zone_off = (0, 2)
        w = max(1, w - 1)          # reserve the scrollbar column
        cols = [('pid', 'PID', 7), ('name', 'NAME', 0), ('user', 'USER', 10), ('cpu', 'CPU%', 6), ('mem', 'MEM', 8)]
        fixed = sum(c[2] for c in cols)
        namew = max(8, w - fixed - 2)
        x = 0
        cv.fill(0, 0, w, 1, th.c('dim', 'raised'))
        for key, label, cw in cols:
            cw = cw or namew
            a = th.c('accent', 'raised', 1) if self.sort == key else th.c('dim', 'raised')
            cv.put(x + 1, 0, label, a)
            if key != 'user':
                self.zone(x, 0, cw, 1, ('sort', key))
            x += cw

        def row(cv2, rx, ry, rw, p, idx, sel):
            a = th.c('on_accent', 'accent') if sel and focused else (th.c('bright', 'sel') if sel else th.c('text', 'win'))
            cv2.fill(rx, ry, rw, 1, a)
            xx = rx + 1
            cv2.put(xx, ry, str(p.pid).rjust(5), a)
            xx += 7
            cv2.put(xx, ry, fit(p.name, namew - 1), a)
            xx += namew
            cv2.put(xx, ry, fit(p.user, 9), a)
            xx += 10
            cv2.put(xx, ry, ('%5.1f' % p.cpu), a)
            xx += 6
            cv2.put(xx, ry, human_size(p.mem).rjust(7), a)
        self.list.draw(cv, 0, 1, w, h - 3, row, th)
        self.zone(0, 1, w, h - 3, 'list')
        tot = len(self.procs)
        cv.put(0, h - 1, '%d processes' % tot, th.c('dim', 'win'))
        bx = w - 30
        bx += button(self, cv, bx, h - 1, 'Terminate', 'term') + 1
        button(self, cv, bx, h - 1, 'Kill', 'kill')
        self.zone_off = (0, 0)

    def draw_tw(self, cv):
        th = self.th
        y = 0
        cv.put(0, y, 'Windows and their background work', th.c('dim', 'win'))
        y += 2
        for win in self.wm.windows:
            app = win.app
            timers = sum(1 for _, _, t in self.wm.loop.timers if t.owner is app and not t.cancelled)
            extra = getattr(app, 'proc_info', lambda: '')()
            cv.put(0, y, fit('%-3s %-24s timers:%d  %s' % (win.icon, win.title, timers, extra), cv.w), th.c('text', 'win'))
            y += 1
        y += 1
        loop = self.wm.loop
        cv.put(0, y, 'event loop: %d fds watched, %d timers, %d worker threads' % (
            len(loop.readers), len([t for t in loop.timers if not t[2].cancelled]), loop.threads), th.c('dim', 'win'))
        y += 1
        cv.put(0, y, 'output: %s written to the terminal' % human_size(self.wm.term.bytes_out), th.c('dim', 'win'))

    def signal(self, sig):
        p = self.list.current()
        if not p:
            return
        try:
            os.kill(p.pid, sig)
            self.wm.toast('Sent %s to %s (%d)' % ('SIGKILL' if sig == signal.SIGKILL else 'SIGTERM', p.name, p.pid))
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def on_click(self, key, ev):
        if key[0] == 'tab':
            self.tab = key[1]
            self.update()
        elif key[0] == 'sort':
            self.sort = key[1]
            self.update()
        elif key == 'term':
            self.signal(signal.SIGTERM)
        elif key == 'kill':
            p = self.list.current()
            if p:
                self.wm.confirm('Kill process', 'Kill %s (%d)?' % (p.name, p.pid),
                                lambda: self.signal(signal.SIGKILL), yes='Kill', parent=self.win)

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.list.sb_grab is not None:
            self.list.mouse(ev, 0)
            self.invalidate()
            return True
        if self.tab == 'procs' and 1 + 2 <= ev.y < self.ch - 2 + 0:
            if self.list.mouse(ev, ev.y - 3):
                self.invalidate()
                if ev.kind == 'down':
                    return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 'tab':
            tabs = ['overview', 'procs', 'tw']
            self.tab = tabs[(tabs.index(self.tab) + 1) % 3]
            self.update()
            return True
        if self.tab == 'procs':
            if self.list.key(ev):
                self.invalidate()
                return True
            if k == 'delete':
                self.signal(signal.SIGTERM)
                return True
        return False


APP = System
