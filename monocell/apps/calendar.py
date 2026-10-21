"""Calendar: month view with ISO weeks, local events and a Google Calendar ICS subscription."""
import calendar as pycal
import datetime as dt
import json
import os
import re

from .. import gcal
from ..app import App
from ..util import DATA_DIR, ensure_dirs, fit, sym, wrap_text
from ..widgets import button

STORE = os.path.join(DATA_DIR, 'calendar.json')
TIME_RE = re.compile(r'^\s*(\d{1,2}:\d{2})\s*')


def load_events():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_events(ev):
    ensure_dirs()
    tmp = STORE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(ev, f, indent=1, sort_keys=True)
    os.replace(tmp, STORE)


class Calendar(App):
    name = 'calendar'
    title = 'Calendar'
    icon = '[31]'
    size = (70, 22)
    min_size = (34, 12)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.today = dt.date.today()
        self.sel = self.today
        self.events = load_events()
        self.evsel = 0
        self.g_url = (wm.cfg.get('gcal_url') or '').strip()
        self.g_events = []
        self.g_index = {}
        self.g_status = ''
        self.g_busy = False
        self.g_fetched = ''
        self.every(30, self._tick)
        self.reload_gcal()
        if self.g_url:
            self.later(0.4, self.sync_gcal)
            self.every(1200, self.sync_gcal)

    # ------------------------------------------------------------ google
    def reload_gcal(self):
        if not self.g_url:
            return
        events, data = gcal.cached_events(self.g_url)
        self.set_gcal(events, data, '')

    def set_gcal(self, events, data, err):
        self.g_events = events or []
        self.g_index = {}
        for o in self.g_events:
            self.g_index.setdefault(o['date'], []).append(o)
        self.g_fetched = (data or {}).get('fetched', '')
        self.g_status = err or ''
        if err:
            self.g_status = err
        self.invalidate()

    def sync_gcal(self):
        if not self.g_url or self.g_busy:
            return
        self.g_busy = True
        url = self.g_url

        def work():
            return gcal.sync(url)

        def done(res):
            self.g_busy = False
            self.set_gcal(res[0], res[1], res[2])
        self.thread(work, done, lambda e: self.set_gcal(self.g_events, {}, str(e)))

    # ------------------------------------------------------------ dates/events
    def _tick(self):
        t = dt.date.today()
        if t != self.today:
            self.today = t
            self.invalidate()
        if self.g_url and not self.g_busy:
            self.sync_gcal()

    def move(self, days=0, months=0):
        d = self.sel
        if months:
            m = d.month - 1 + months
            y = d.year + m // 12
            m = m % 12 + 1
            day = min(d.day, pycal.monthrange(y, m)[1])
            d = dt.date(y, m, day)
        d += dt.timedelta(days=days)
        self.sel = d
        self.evsel = 0
        self.invalidate()

    def day_events(self, d=None):
        return self.events.get((d or self.sel).isoformat(), [])

    def has_events(self, d):
        return d.isoformat() in self.events or d.isoformat() in self.g_index

    def day_items(self, d=None):
        d = d or self.sel
        items = []
        for s in self.day_events(d):
            m = TIME_RE.match(s)
            items.append({'summary': s[m.end():] if m else s, 'raw': s, 'src': 'local',
                          'time': m.group(1) if m else '', 'location': '', 'desc': '',
                          'date': d.isoformat()})
        items.extend(self.g_index.get(d.isoformat(), []))
        items.sort(key=lambda o: (o.get('time') or '99:99', o.get('summary', '').lower()))
        return items

    def add_event(self):
        def ok(text):
            if text and text.strip():
                self.events.setdefault(self.sel.isoformat(), []).append(text.strip())
                self.events[self.sel.isoformat()].sort()
                save_events(self.events)
                self.invalidate()
        self.wm.prompt('New event', 'Event on %s (tip: start with a time, e.g. "14:00 Dentist")'
                       % self.sel.strftime('%a %d %b %Y'), '', ok, parent=self.win)

    def del_event(self):
        items = self.day_items()
        if not items:
            return
        i = min(self.evsel, len(items) - 1)
        it = items[i]
        if it['src'] != 'local':
            self.wm.toast('Google events are read-only in monocell', 'accent')
            return
        local = self.day_events()
        name = it.get('raw', it['summary'])
        if name not in local:
            return
        j = local.index(name)

        def yes():
            local.pop(j)
            if not local:
                self.events.pop(self.sel.isoformat(), None)
            save_events(self.events)
            self.evsel = 0
            self.invalidate()
        self.wm.confirm('Delete event', 'Delete "%s"?' % name, yes, yes='Delete', parent=self.win)

    def show_details(self):
        items = self.day_items()
        if not items:
            return
        self.evsel = min(self.evsel, len(items) - 1)
        it = items[self.evsel]
        txt = '%s\n%s%s' % (it.get('summary', ''), it.get('date', ''),
                            ('  ' + it['time']) if it.get('time') else '')
        if it.get('location'):
            txt += '\nLocation: ' + it['location']
        if it.get('desc'):
            txt += '\n\n' + it['desc']
        self.wm.message('Google Calendar' if it['src'] != 'local' else 'Event', txt, parent=self.win)

    # ------------------------------------------------------------ drawing
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        side = w >= 60
        gw = min(39, w - 28) if side else w
        title = self.sel.strftime('%B %Y')
        bw = sum(len(l) + 3 for l in ('<', 'Today', '>')) + (len('Sync') + 3 if self.g_url else 0)
        x = gw - bw
        cv.put(1, 0, fit(title, max(4, x - 2)), th.c('bright', 'win', 1))
        for label, key in (('<', 'prev'), ('Today', 'today'), ('>', 'next')):
            x += button(self, cv, x, 0, label, key) + 1
        if self.g_url:
            x += button(self, cv, x, 0, 'Sync', 'sync', active=self.g_busy) + 1
        cell = max(3, min(5, (gw - 4) // 7))
        gx = 4
        cv.put(0, 2, 'wk', th.c('dim', 'win'))
        for i, name in enumerate(['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']):
            cv.put(gx + i * cell + cell - 3, 2, name, th.c('accent' if i >= 5 else 'dim', 'win'))
        weeks = pycal.Calendar(0).monthdatescalendar(self.sel.year, self.sel.month)
        rh = max(1, min(2, (h - 4) // len(weeks)))
        for r, week in enumerate(weeks):
            y = 3 + r * rh
            if y >= h:
                break
            cv.put(0, y, '%2d' % week[0].isocalendar()[1], th.c('dim', 'win'))
            for c, d in enumerate(week):
                cx = gx + c * cell
                inmonth = d.month == self.sel.month
                key = ('d', d)
                if d == self.sel:
                    a = th.c('on_accent', 'accent', 1)
                elif d == self.today:
                    a = th.c('accent', 'raised', 1)
                elif self.hover == key:
                    a = th.c('bright', 'sel')
                elif not inmonth:
                    a = th.c('sel', 'win')
                elif c >= 5:
                    a = th.c('text', 'win')
                else:
                    a = th.c('bright', 'win')
                cv.fill(cx, y, cell - 1, 1, a)
                cv.put(cx + cell - 4, y, '%2d' % d.day, a)
                if inmonth and self.has_events(d):
                    ext = d.isoformat() in self.g_index
                    cv.put(cx + cell - 2, y, sym('bullet', '.'), a if d == self.sel
                           else th.c('blue' if ext else 'accent', 'win'))
                self.zone(cx, y, cell - 1, rh, key)
        if side:
            px = gw + 2
            pw = w - px
            cv.vline(gw, 0, h, th.c('raised', 'win'), '|')
            cv.put(px, 0, fit(self.sel.strftime('%A, %d %B'), pw), th.c('bright', 'win', 1))
            items = self.day_items()
            y = 2
            if not items:
                cv.put(px, y, 'No events', th.c('dim', 'win'))
            for i, it in enumerate(items):
                lines = wrap_text(it.get('summary', ''), pw - 9)
                if it.get('location'):
                    lines += wrap_text('@ ' + it['location'], pw - 10)
                on = i == min(self.evsel, len(items) - 1)
                marker = util_dot(it['src'])
                for j, ln in enumerate(lines):
                    if y >= h - 3:
                        break
                    a = th.c('bright', 'sel') if on else th.c('text', 'win')
                    cv.fill(px, y, pw, 1, a)
                    if j == 0:
                        if it.get('time'):
                            cv.put(px, y, it['time'], a)
                        cv.put(px + 6, y, marker, a if on else th.c('blue' if it['src'] != 'local' else 'accent', 'win'))
                    cv.put(px + 8, y, ln, a)
                    self.zone(px, y, pw, 1, ('e', i))
                    y += 1
            bx = px
            bx += button(self, cv, bx, h - 1, '+ Add', 'add', primary=True) + 1
            if items:
                button(self, cv, bx, h - 1, 'Delete', 'del')
            # google status
            if self.g_url and h > 6:
                st = self.g_status or ('Google: %d events  %s' % (len(self.g_events), self.g_fetched))
                col = 'red' if self.g_status else 'dim'
                cv.put(px, h - 2, fit(st, pw), th.c(col, 'win'))
            # upcoming
            if y < h - 4:
                y += 1
                up = sorted(set(k for k in self.events if k > self.sel.isoformat()) |
                            set(k for k in self.g_index if k > self.sel.isoformat()))[:max(0, h - y - 5)]
                if up:
                    cv.put(px, y, 'UPCOMING', th.c('dim', 'win'))
                    y += 1
                    for k in up:
                        d = dt.date.fromisoformat(k)
                        first = self.day_items(d)
                        txt = first[0].get('summary', '') if first else ''
                        cv.put(px, y, fit('%s  %s' % (d.strftime('%d %b'), txt), pw), th.c('dim', 'win'))
                        self.zone(px, y, pw, 1, ('d', d))
                        y += 1
        else:
            items = self.day_items()
            if items and 3 + len(weeks) * rh < h:
                cv.put(1, h - 1, fit('* ' + items[0].get('summary', '') +
                                    (' (+%d)' % (len(items) - 1) if len(items) > 1 else ''), w - 2),
                       th.c('accent', 'win'))

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'next':
            self.move(months=1)
        elif key == 'prev':
            self.move(months=-1)
        elif key == 'today':
            self.sel = self.today
        elif key == 'add':
            self.add_event()
        elif key == 'del':
            self.del_event()
        elif key == 'sync':
            self.sync_gcal()
        elif key[0] == 'd':
            self.sel = key[1]
            self.evsel = 0
            if ev.clicks >= 2:
                self.add_event()
        elif key[0] == 'e':
            self.evsel = key[1]
            if ev.clicks >= 2:
                self.show_details()

    def on_mouse(self, ev):
        if ev.kind == 'wheelup':
            self.move(months=-1)
            return True
        if ev.kind == 'wheeldown':
            self.move(months=1)
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        moves = {'left': -1, 'right': 1, 'up': -7, 'down': 7}
        if k in moves:
            self.move(days=moves[k])
        elif k == 'pgup':
            self.move(months=-1)
        elif k == 'pgdn':
            self.move(months=1)
        elif k in ('t', 'home'):
            self.sel = self.today
        elif k in ('n', 'a', '+'):
            self.add_event()
        elif k in ('delete', 'd'):
            self.del_event()
        elif k == 'enter' and self.day_items():
            self.show_details()
        elif k in ('r', 'f5'):
            self.sync_gcal()
        elif k == 'tab':
            n = len(self.day_items())
            self.evsel = (self.evsel + 1) % n if n else 0
        else:
            return False
        self.invalidate()
        return True


def util_dot(src):
    return sym('dot', '.') if src == 'local' else sym('arrow', '>')


APP = Calendar
