"""Pi benchmark: one worker process per core, with temperature and fan graphs.

Pi is computed with Machin's formula in integer arithmetic (see
`monocell/piworker.py`): identical work on every core, identical digits on
every machine, so the numbers are comparable between runs.

Deliberately not a "score": the app shows the work (digits), the time, the
achieved rate and what the cooling does while it runs.
"""
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import deque

from .. import hardware
from ..app import App
from ..util import fit

RUNGS = [1000, 2000, 5000, 10000, 20000, 50000, 100000]
DEFAULT_DIGITS = 50000  # ~15 s per core on an L7700: long enough for the fan
HISTORY = 1200          # samples kept, two per second
KNOWN_HEAD = '31415926535897932384626433832795028841971693993751'


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ''


def cpu_label():
    for line in read_text('/proc/cpuinfo').splitlines():
        if line.startswith('model name'):
            name = ' '.join(line.split(':', 1)[1].split())
            m = re.search(r'([LTUEP]\d{4})', name)
            return m.group(1) if m else name[:24]
    return 'CPU'


def human(n):
    return '{:,}'.format(n)


class Bench(App):
    name = 'bench'
    title = 'Pi Benchmark'
    icon = '[%]'
    size = (72, 26)
    min_size = (44, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.cores = os.cpu_count() or 1
        self.cpu = cpu_label()
        self.digits = DEFAULT_DIGITS
        self.workers = []
        self.running = False
        self.t0 = 0.0
        self.elapsed = 0.0
        self.result = ''
        self.error = ''
        self.runs = []                      # (digits, cores, secs)
        self.cores_t = []
        self.hist_t = deque(maxlen=HISTORY)
        self.hist_f = deque(maxlen=HISTORY)
        self.temp = None
        self.fan = None
        self.every(0.5, self.sample)
        self.every(0.2, self.poll)
        self.later(0.05, self.sample)

    # ---------------------------------------------------------------- sensors
    def sample(self):
        self.cores_t = hardware.cpu_cores()
        self.temp = hardware.cpu_temp()
        self.fan = hardware.fan_rpm()
        if self.temp is not None:
            self.hist_t.append(self.temp)
        if self.fan is not None:
            self.hist_f.append(self.fan)
        self.invalidate()

    def reset(self):
        self.hist_t.clear()
        self.hist_f.clear()
        self.runs = []
        self.result = ''
        self.error = ''
        self.elapsed = 0.0
        self.sample()

    # ---------------------------------------------------------------- workers
    def start(self):
        self.stop(quiet=True)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = dict(os.environ)
        env['PYTHONPATH'] = root + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        self.workers = []
        for _ in range(self.cores):
            try:
                proc = subprocess.Popen(
                    [sys.executable, '-m', 'monocell.piworker', str(self.digits)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            except OSError as e:
                self.error = str(e)
                self.stop(quiet=True)
                return
            self.workers.append({'proc': proc, 'secs': None, 'sum': None, 'head': None})
        self.running = True
        self.error = ''
        self.result = ''
        self.elapsed = 0.0
        self.t0 = time.monotonic()
        self.sample()
        self.invalidate()

    def stop(self, quiet=False):
        for wk in self.workers:
            proc = wk.get('proc')
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                    proc.communicate(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self.workers = []
        self.running = False
        if not quiet:
            self.invalidate()

    def poll(self):
        if not self.running:
            return
        done = 0
        for wk in self.workers:
            if wk['secs'] is not None:
                done += 1
                continue
            proc = wk['proc']
            if proc.poll() is None:
                continue
            out, err = proc.communicate()
            data = None
            for line in reversed((out or '').strip().splitlines()):
                try:
                    data = json.loads(line)
                    break
                except ValueError:
                    continue
            if not data:
                lines = (err or '').strip().splitlines()
                self.error = 'worker failed: %s' % (lines[-1] if lines else 'no output')
                wk['secs'] = 0.0
                continue
            wk.update(secs=data['secs'], sum=data['checksum'], head=data['head'])
            done += 1
        if self.workers and done == len(self.workers):
            self.finish()
        self.invalidate()

    def finish(self):
        self.running = False
        self.elapsed = max(wk['secs'] or 0.0 for wk in self.workers)
        sums = {wk['sum'] for wk in self.workers}
        heads = [wk['head'] or '' for wk in self.workers]
        if len(sums) != 1 or None in sums:
            self.error = 'workers disagree -- result not trustworthy'
        elif not all(KNOWN_HEAD.startswith(h) for h in heads if h):
            self.error = 'digits do not match pi -- result not trustworthy'
        else:
            self.result = list(sums)[0]
            self.error = ''
            self.runs.append((self.digits, self.cores, self.elapsed))
            self.runs = self.runs[-8:]
        self.stop(quiet=True)
        self.invalidate()

    # ---------------------------------------------------------------- helpers
    def rate(self):
        secs = self.elapsed
        if self.running:
            secs = time.monotonic() - self.t0
        if secs <= 0:
            return 0.0
        return self.digits / secs

    def total_rate(self):
        return self.rate() * self.cores

    def step(self, direction):
        if self.running:
            return
        i = RUNGS.index(self.digits) if self.digits in RUNGS else len(RUNGS) - 2
        self.digits = RUNGS[max(0, min(len(RUNGS) - 1, i + direction))]
        self.invalidate()

    # ---------------------------------------------------------------- drawing
    def kv(self, cv, y, w, label, value, role='bright'):
        cv.put(0, y, fit(label, 11), self.th.c('dim', 'win'))
        cv.put(11, y, fit(value, max(1, w - 11)), self.th.c(role, 'win'))

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if w < 30 or h < 10:
            cv.put(0, 0, fit('Pi Benchmark: window too small', w), th.c('dim', 'win'))
            return
        y = 0
        cv.put(0, y, fit('%s benchmark' % self.cpu, w), th.c('accent', 'win', 1))
        cv.put(max(0, w - len('%d cores' % self.cores)), y, '%d cores' % self.cores, th.c('dim', 'win'))
        y += 1

        self.kv(cv, y, w, 'work', '%d x %s digits' % (self.cores, human(self.digits)))
        y += 1
        if self.running:
            elapsed = time.monotonic() - self.t0
            refs = [r[2] for r in self.runs if r[0] == self.digits and r[1] == self.cores]
            state = 'running %.1f s' % elapsed
            if refs:
                state += '   ~%d%%' % min(99, int(100 * elapsed / max(refs)))
            self.kv(cv, y, w, 'state', state, 'accent')
        else:
            state = 'idle, press s to start' if not self.elapsed else 'done in %.2f s' % self.elapsed
            self.kv(cv, y, w, 'state', state, 'dim')
        y += 1
        if self.running:
            speed = 'measuring...'
        else:
            speed = '%.2f kdigit/s total   %.2f per core' % (self.total_rate() / 1000.0,
                                                             self.rate() / 1000.0)
        refs = [r[2] for r in self.runs if r[0] == self.digits and r[1] == self.cores]
        if refs:
            speed += '   best %.2f s' % min(refs)
        self.kv(cv, y, w, 'speed', speed)
        y += 1
        if self.error:
            self.kv(cv, y, w, 'result', self.error, 'red')
        elif self.result:
            note = 'head %s...  checksum %s  ok' % (KNOWN_HEAD[:10], self.result)
            self.kv(cv, y, w, 'result', note, 'green')
        else:
            self.kv(cv, y, w, 'result', 'no run yet -- results are checked against pi', 'dim')
        y += 1
        parts = []
        if self.cores_t:
            if len(self.cores_t) <= 3:
                parts.append('  '.join('%s %.0f C' % (n, v) for n, v in self.cores_t))
            else:
                parts.append('max %.0f C (%d cores)' % (self.temp or 0, len(self.cores_t)))
        elif self.temp is not None:
            parts.append('%.0f C' % self.temp)
        if self.fan is not None:
            parts.append('%d rpm' % self.fan)
        self.kv(cv, y, w, 'sensors', '   '.join(parts) if parts else 'no sensors found',
                'text' if parts else 'dim')
        y += 1

        avail = h - y - 1
        ph = min(14, max(0, (avail - 2) // 2))
        if ph >= 4:
            y = self.panel(cv, y, w, ph, 'TEMPERATURE (hottest core)', self.hist_t, 'C', 'accent', 6)
            y = self.panel(cv, y, w, ph, 'FAN', self.hist_f, 'rpm', 'accent2', 300)
        self.hints(cv, h - 1, w)

    def panel(self, cv, y, w, ph, label, hist, unit, role, min_span):
        th = self.th
        lo, hi = self.scale(hist, min_span)
        vals = list(hist)[-300:]
        head = label
        if vals:
            head += '   now %d %s   min %d   max %d   %d samples' % (
                vals[-1], unit, min(vals), max(vals), len(hist))
        else:
            head += '   no readings'
        cv.put(0, y, fit(head, w), th.c('dim', 'win'))
        lw = 6
        gx, gw = lw, w - lw
        cv.put(0, y + 1, fit('%g' % hi, lw - 1, 'right'), th.c('dim', 'win'))
        cv.put(0, y + ph, fit('%g' % lo, lw - 1, 'right'), th.c('dim', 'win'))
        cv.box(gx, y + 1, gw, ph, th.c('sel', 'win'))
        inner_h, inner_w = ph - 2, gw - 2
        if inner_h >= 1 and inner_w >= 2 and vals:
            mid = y + 2 + (inner_h - 1) // 2
            cv.hline(gx + 1, mid, inner_w, th.c('sel', 'win'), '.')
            pts = vals[-inner_w:]
            off = inner_w - len(pts)
            prev = None
            for i, v in enumerate(pts):
                frac = 0.0 if hi <= lo else (v - lo) / float(hi - lo)
                r = max(0, min(inner_h - 1, int(round(frac * (inner_h - 1)))))
                col = gx + 1 + off + i
                if prev is not None and prev != r:
                    for mid_r in range(min(prev, r) + 1, max(prev, r)):
                        cv.putc(col, y + ph - 1 - mid_r, '|', th.c(role, 'win'))
                cv.putc(col, y + ph - 1 - r, '#', th.c(role, 'win'))
                prev = r
        return y + ph + 1

    @staticmethod
    def nice_step(span):
        target = max(1.0, span / 4.0)
        mag = 10.0 ** int(math.floor(math.log10(target)))
        for m in (1, 2, 5, 10):
            if target <= m * mag:
                return m * mag
        return 10 * mag

    def scale(self, hist, min_span):
        vals = list(hist)[-300:]
        if not vals:
            return 0, min_span
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, min_span)
        pad = span * 0.25
        lo = max(0.0, lo - pad)
        hi = hi + pad
        step = self.nice_step(hi - lo)
        lo = int(lo // step) * step
        hi = int(math.ceil(hi / step)) * step
        if hi <= lo:
            hi = lo + step
        return lo, hi

    def hints(self, cv, y, w):
        th = self.th
        items = [('[s]', 'start'), ('[x]', 'stop'), ('[-/+]', 'digits'), ('[r]', 'reset')]
        x = 0
        for key, label in items:
            self.zone(x, y, len(key), 1, key)
            cv.put(x, y, key, th.c('accent', 'win') if self.hover == key else th.c('dim', 'win'))
            x += len(key)
            cv.put(x, y, ' ' + label + '  ', th.c('dim', 'win'))
            x += len(label) + 3

    def on_click(self, key, ev):
        if key == '[s]':
            if not self.running:
                self.start()
        elif key == '[x]':
            if self.running:
                self.stop()
        elif key == '[-/+]':
            self.step(1)
        elif key == '[r]':
            self.reset()

    def on_key(self, ev):
        k = ev.key
        if k in ('s', 'enter'):
            if not self.running:
                self.start()
        elif k in ('x', 'space'):
            if self.running:
                self.stop()
        elif k in ('+', '=', 'right'):
            self.step(1)
        elif k in ('-', '_', 'left'):
            self.step(-1)
        elif k == 'r':
            self.reset()
        else:
            return False
        self.invalidate()
        return True

    def on_close(self):
        self.stop(quiet=True)
        return True


APP = Bench
