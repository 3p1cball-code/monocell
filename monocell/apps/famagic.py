"""Famagic: shared family todo and shopping lists, synced through Vega."""
import calendar as _calendar
import json
import os
import pwd
import urllib.error
import urllib.request
from datetime import date

from ..app import App
from ..util import fit
from ..widgets import ListView, TextField, button

DEFAULT_SERVER = 'http://192.168.33.24:8791'
CLIENT_CFG = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, '.config', 'famagic-sync', 'client.json')
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
          'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']


def client_config():
    cfg = {'server': DEFAULT_SERVER, 'token': ''}
    try:
        with open(CLIENT_CFG, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for k in ('server', 'token'):
            if data.get(k):
                cfg[k] = str(data[k])
    except (OSError, ValueError):
        pass
    env = os.environ.get('FAMAGIC_SERVER')
    if env:
        cfg['server'] = env
    env = os.environ.get('FAMAGIC_TOKEN')
    if env:
        cfg['token'] = env
    return cfg


def http_json(cfg, path, method='GET', body=None, timeout=10):
    headers = {'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if cfg.get('token'):
        headers['Authorization'] = 'Bearer ' + cfg['token']
    req = urllib.request.Request(
        cfg['server'].rstrip('/') + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            raw = f.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        raise RuntimeError('%d %s' % (e.code, raw[:120]))
    except urllib.error.URLError as e:
        raise RuntimeError(str(e.reason))


def format_due(d):
    return '%02d-%02d-%04d' % (d.day, d.month, d.year)


def parse_due_text(text):
    text = (text or '').strip()
    if not text:
        return None
    parts = text.replace('.', '-').replace('/', '-').split('-')
    if len(parts) != 3:
        raise ValueError('date')
    d, m, y = (int(x) for x in parts)
    return date(y, m, d)


def due_label(task, today=None):
    raw = task.get('due_date')
    if not raw:
        return ''
    today = today or date.today()
    try:
        y, m, dd = (int(x) for x in raw.split('-'))
        due = date(y, m, dd)
    except (ValueError, TypeError):
        return str(raw)
    delta = (due - today).days
    if delta == 0:
        text = 'heute'
    elif delta == 1:
        text = 'morgen'
    elif delta < 0:
        text = '%d T. überfällig' % -delta
    else:
        text = format_due(due)
    if task.get('due_time'):
        text += ' ' + task['due_time'][:5]
    return text


def task_rows(snap, list_id):
    tasks = [t for t in snap.get('tasks', [])
             if t.get('list_id') == list_id and not t.get('parent_task_id')]
    open_t = [t for t in tasks if not t.get('is_completed')]
    done_t = [t for t in tasks if t.get('is_completed')]
    open_t.sort(key=lambda t: (t.get('due_date') or '9999-12-31', t.get('position') or 0))
    done_t.sort(key=lambda t: t.get('completed_at') or '', reverse=True)
    return open_t + done_t


def shopping_rows(snap, list_id):
    sl = next((s for s in snap.get('shopping_lists', []) if s.get('id') == list_id), None)
    items = list(sl.get('items', [])) if sl else []
    open_i = [i for i in items if not i.get('is_checked')]
    done_i = [i for i in items if i.get('is_checked')]
    open_i.sort(key=lambda i: i.get('position') or 0)
    return open_i + done_i


def list_titles(snap):
    return [l for l in snap.get('lists', []) if not l.get('archived_at')]


REPEAT_LABELS = {'none': 'keine', 'daily': 'täglich', 'weekly': 'wöchentlich',
                 'monthly': 'monatlich', 'yearly': 'jährlich'}


def repeat_key(rule):
    if not rule:
        return 'none'
    freq = rule.get('freq')
    try:
        interval = int(rule.get('interval') or 1)
    except (TypeError, ValueError):
        interval = 1
    if freq == 'weekly':
        return 'weekly'
    if freq == 'daily':
        return 'weekly' if interval > 1 and interval % 7 == 0 else 'daily'
    if freq == 'monthly':
        return 'monthly'
    if freq == 'yearly':
        return 'yearly'
    return 'daily'


def repeat_rule(key):
    return {
        'none': None,
        'daily': {'freq': 'daily', 'interval': 1},
        'weekly': {'freq': 'weekly', 'interval': 1},
        'monthly': {'freq': 'monthly', 'interval': 1},
        'yearly': {'freq': 'yearly', 'interval': 1},
    }.get(key)


def member_map(snap):
    out = {}
    for m in snap.get('members', []):
        prof = m.get('profile') or {}
        out[m['id']] = prof.get('first_name') or m.get('first_name') or m.get('role') or '?'
    return out


def calendar_weeks(year, month):
    cal = _calendar.Calendar(firstweekday=0)
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        weeks.append(list(week))
    while len(weeks) < 6:
        weeks.append([0] * 7)
    return weeks


def click_action(x, width):
    if x >= width - 2:
        return 'delete'
    if width - 5 <= x <= width - 3:
        return 'edit'
    if x <= 3:
        return 'toggle'
    return 'select'


class Famagic(App):
    name = 'famagic'
    title = 'Famagic'
    icon = '[F]'
    size = (90, 28)
    min_size = (56, 14)
    single = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        self.cfg = client_config()
        self.snap = None
        self.tab = None
        self.list = ListView()
        self.list.on_activate = self.toggle
        self.status = 'lade ...'
        self.err = ''
        self.busy = False
        self.cloud_offline = False
        self.pending = 0
        self.confirm = None
        self.mode = ''
        self.comp = None
        self.cal = None
        self.newlist = None
        self.pending_select = None
        self.pending_tab = None
        self.done_open = False
        self.filter_member = None
        self.filter_open = False
        self.filter_sel = 0
        self.filter_start = 0
        self.dropdown_open = False
        self.drop_sel = 0
        self.drop_start = 0
        self.load()
        self.every(5, self.load)

    def tabs(self):
        out = []
        if not self.snap:
            return out
        for l in list_titles(self.snap):
            out.append({'kind': 'task', 'id': l['id'], 'title': l.get('title') or '?'})
        for s in self.snap.get('shopping_lists', []):
            out.append({'kind': 'shopping', 'id': s['id'], 'title': s.get('title') or '?'})
        return out

    def current_tab_index(self):
        tabs = self.tabs()
        if not self.tab:
            return None
        for i, t in enumerate(tabs):
            if t['kind'] == self.tab['kind'] and t['id'] == self.tab['id']:
                return i
        return None

    def tab_label(self, t, counts):
        prefix = 'Einkauf - ' if t['kind'] == 'shopping' else 'Aufgabe - '
        n = counts.get(t['id'], 0)
        return '%s%s%s' % (prefix, t['title'], ('  (%d offen)' % n) if n else '  (leer)')

    def open_dropdown(self):
        if not self.tabs():
            return
        idx = self.current_tab_index()
        self.drop_sel = idx if idx is not None else 0
        self.dropdown_open = True
        self.filter_open = False
        self.invalidate()

    def filter_options(self):
        opts = [{'id': None, 'label': 'Alle'}, {'id': 'none', 'label': 'Niemand'}]
        names = member_map(self.snap or {})
        for m in (self.snap or {}).get('members', []):
            opts.append({'id': m['id'], 'label': names.get(m['id'], '?')})
        return opts

    def filter_label(self):
        for o in self.filter_options():
            if o['id'] == self.filter_member:
                return o['label']
        return 'Alle'

    def task_matches_filter(self, t):
        lst = next((l for l in (self.snap or {}).get('lists', [])
                    if l.get('id') == t.get('list_id')), None)
        if lst and lst.get('is_private'):
            return True
        ids = t.get('assignee_member_ids') or []
        if self.filter_member is None:
            return True
        if self.filter_member == 'none':
            return not ids
        return self.filter_member in ids

    def filter_counts(self):
        counts = {}
        if not self.snap or not self.tab or self.tab['kind'] != 'task':
            return counts
        tasks = [t for t in self.snap.get('tasks', [])
                 if t.get('list_id') == self.tab['id'] and not t.get('parent_task_id')
                 and not t.get('is_completed')]
        for o in self.filter_options():
            if o['id'] is None:
                counts[o['id']] = len(tasks)
            elif o['id'] == 'none':
                counts[o['id']] = sum(1 for t in tasks if not (t.get('assignee_member_ids') or []))
            else:
                counts[o['id']] = sum(1 for t in tasks if o['id'] in (t.get('assignee_member_ids') or []))
        return counts

    def open_filter(self):
        if not self.tab or self.tab['kind'] != 'task':
            return
        self.dropdown_open = False
        opts = self.filter_options()
        idx = 0
        for i, o in enumerate(opts):
            if o['id'] == self.filter_member:
                idx = i
                break
        self.filter_sel = idx
        self.filter_open = True
        self.invalidate()

    def select_tab(self, tab):
        self.tab = tab
        self.dropdown_open = False
        self.filter_open = False
        self.done_open = False
        self.rebuild()
        self.invalidate()

    def default_tab(self):
        tabs = self.tabs()
        if not tabs:
            return None
        for l in list_titles(self.snap or {}):
            if l.get('is_default') and not l.get('is_private'):
                for t in tabs:
                    if t['kind'] == 'task' and t['id'] == l['id']:
                        return t
        return tabs[0]

    def load(self):
        if self.busy:
            return
        self.busy = True

        def work():
            return http_json(self.cfg, '/api/snapshot')

        def done(snap):
            self.busy = False
            self.snap = snap
            tabs = self.tabs()
            if self.pending_tab:
                for t in tabs:
                    if t['kind'] == self.pending_tab['kind'] and t['id'] == self.pending_tab['id']:
                        self.tab = t
                        break
                self.pending_tab = None
            if self.tab and not any(t['kind'] == self.tab['kind'] and t['id'] == self.tab['id'] for t in tabs):
                self.tab = None
            if not self.tab:
                self.tab = self.default_tab()
            self.rebuild()
            self.cloud_offline = bool(snap.get('offline'))
            self.pending = int(snap.get('pending') or 0)
            stamp = (snap.get('synced_at') or '')[11:16]
            if self.cloud_offline:
                self.status = 'online · %d Änderungen warten' % self.pending
            elif self.pending:
                self.status = 'online · %d werden gesendet' % self.pending
            else:
                self.status = 'synchronisiert %s' % stamp if stamp else 'bereit'
            self.err = ''
            self.invalidate()

        def bad(e):
            self.busy = False
            self.err = str(e)
            self.status = 'VEGA nicht erreichbar'
            self.invalidate()

        self.thread(work, done, bad)

    def rebuild(self):
        keep = self.current()
        items = []
        done_items = []
        if self.snap and self.tab:
            if self.tab['kind'] == 'task':
                names = member_map(self.snap)
                for t in task_rows(self.snap, self.tab['id']):
                    if not self.task_matches_filter(t):
                        continue
                    entry = {'kind': 'task', 'task': t,
                             'who': [names.get(x, '') for x in t.get('assignee_member_ids', [])]}
                    (done_items if t.get('is_completed') else items).append(entry)
            else:
                for i in shopping_rows(self.snap, self.tab['id']):
                    entry = {'kind': 'shopping', 'item': i}
                    (done_items if i.get('is_checked') else items).append(entry)
        if done_items:
            items.append({'kind': 'toggle', 'count': len(done_items)})
            if self.done_open:
                items.extend(done_items)
        self.list.set_items(items, keep=True)
        if self.pending_select:
            for idx, it in enumerate(items):
                data = it['task'] if it['kind'] == 'task' else it['item']
                if data['id'] == self.pending_select:
                    self.list.select(idx)
                    self.pending_select = None
                    break
        elif keep:
            key = self.item_key(keep)
            for idx, it in enumerate(items):
                if self.item_key(it) == key:
                    self.list.select(idx)
                    break
        self.invalidate()

    def item_key(self, it):
        if not it:
            return None
        if it['kind'] == 'toggle':
            return ('toggle', 'done')
        if it['kind'] == 'task':
            return ('t', it['task']['id'])
        return ('s', it['item']['id'])

    def current(self):
        return self.list.current()

    def tab_counts(self):
        counts = {}
        if not self.snap:
            return counts
        for t in self.snap.get('tasks', []):
            if not t.get('is_completed') and not t.get('parent_task_id'):
                counts[t.get('list_id')] = counts.get(t.get('list_id'), 0) + 1
        for sl in self.snap.get('shopping_lists', []):
            counts[sl['id']] = sum(1 for i in sl.get('items', []) if not i.get('is_checked'))
        return counts

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        cv.put(1, 0, 'Famagic', th.c('accent', 'win', 1))
        fam = ((self.snap or {}).get('family') or {}).get('name') or ''
        cv.put(10, 0, fit(fam, max(8, w - 28)), th.c('dim', 'win'))
        if self.err:
            state, state_attr = 'offline', th.c('red', 'win')
        elif self.cloud_offline:
            state, state_attr = 'online', th.c('yellow', 'win')
        elif self.busy:
            state, state_attr = 'lade ...', th.c('dim', 'win')
        else:
            state, state_attr = 'online', th.c('green', 'win')
        cv.put(w - len(state) - 1, 0, state, state_attr)
        tabs = self.tabs()
        counts = self.tab_counts()
        list_label = ' + Liste '
        dd_w = max(12, w - 3 - len(list_label))
        idx = self.current_tab_index()
        if tabs and idx is not None:
            t = tabs[idx]
            prefix = 'Einkauf - ' if t['kind'] == 'shopping' else ''
            n = counts.get(t['id'], 0)
            label = ' %s%s  -  %s ' % (prefix, t['title'], ('%d offen' % n) if n else 'leer')
        else:
            label = ' keine Liste '
        attr = th.c('on_accent', 'accent') if self.dropdown_open else th.c('text', 'raised')
        cv.put(1, 1, fit(label, dd_w - 2), attr)
        cv.put(dd_w, 1, 'v', attr)
        self.zone(1, 1, dd_w, 1, 'dropdown')
        button(self, cv, w - len(list_label) - 1, 1, '+ Liste', 'newlist')
        if self.tab and self.tab['kind'] == 'task':
            fattr = th.c('on_accent', 'accent') if (self.filter_open or self.filter_member is not None) else th.c('text', 'raised')
            cv.put(1, 2, fit(' Filter: %s ' % self.filter_label(), w - 4), fattr)
            cv.put(w - 2, 2, 'v', fattr)
            self.zone(1, 2, w - 2, 1, 'filter')
        cv.fill(1, 3, w - 2, 1, th.c('dim', 'win'), '-')
        top = 4
        rows_h = max(1, h - 9)
        open_n = sum(1 for it in self.list.items
                     if (it['kind'] == 'task' and not it['task'].get('is_completed'))
                     or (it['kind'] == 'shopping' and not it['item'].get('is_checked')))

        def row(cv2, rx, ry, rw, item, idx, sel):
            if item['kind'] == 'toggle':
                a = th.c('on_accent', 'accent') if sel and focused else \
                    (th.c('bright', 'sel') if sel else th.c('dim', 'win'))
                if sel:
                    cv2.fill(rx, ry, rw, 1, a)
                mark = 'v' if self.done_open else '>'
                cv2.put(rx, ry, ' %s Erledigt (%d)' % (mark, item['count']), a)
                return
            done = item['task'].get('is_completed') if item['kind'] == 'task' else item['item'].get('is_checked')
            a = th.c('on_accent', 'accent') if sel and focused else \
                (th.c('bright', 'sel') if sel else th.c('dim' if done else 'text', 'win'))
            if sel:
                cv2.fill(rx, ry, rw, 1, a)
            mark = '[x]' if done else '[ ]'
            cv2.put(rx, ry, mark, a if sel else th.c('green' if done else 'dim', 'win'))
            if item['kind'] == 'task':
                t = item['task']
                right = ', '.join(n for n in item['who'] if n)
                due = due_label(t)
                if due:
                    right = (right + ' · ' + due) if right else due
                if t.get('priority') == 'high':
                    right = (right + ' · wichtig') if right else 'wichtig'
            else:
                it = item['item']
                right = it.get('quantity_text') or ''
            rw2 = min(len(right) + 1, max(0, rw // 2))
            rx_right = rx + rw - rw2 - 5
            serie = 'Serie' if item['kind'] == 'task' and item['task'].get('recurrence_rule') else ''
            tw = max(4, rx_right - (rx + 4) - 1 - (6 if serie else 0))
            title = item['task'].get('title') if item['kind'] == 'task' else item['item'].get('name')
            cv2.put(rx + 4, ry, fit(title or '', tw), a)
            if serie:
                cv2.put(rx + 4 + tw, ry, ' ' + serie, a if sel else th.c('blue', 'win'))
            if right:
                if sel:
                    ra = a
                elif done:
                    ra = th.c('dim', 'win')
                elif item['kind'] == 'task' and item['task'].get('due_date') \
                        and item['task']['due_date'] < date.today().isoformat():
                    ra = th.c('red', 'win')
                else:
                    ra = th.c('yellow', 'win')
                cv2.put(rx_right, ry, fit(right, rw2, 'right'), ra)
            cv2.put(rx + rw - 3, ry, '!', a if sel else th.c('yellow', 'win'))
            cv2.put(rx + rw - 1, ry, 'X', a if sel else th.c('red', 'win'))

        if self.list.items:
            self.list.draw(cv, 0, top, w, rows_h, row, th)
        else:
            cv.put(2, top, 'Keine Einträge. + Neu legt etwas an.', th.c('dim', 'win'))
        self.zone(0, top, w, rows_h, 'rows')
        btn_y = min(4 + len(self.list.items) + 2, h - 3)
        cv.put((w - 7) // 2, btn_y, ' + Neu ', th.c('text', 'blue'))
        self.zone((w - 7) // 2, btn_y, 7, 1, 'new')
        hint = '%d offen  space abhaken  n neu  e/! ändern  d/X löschen  l Liste  f Filter  r aktualisieren' % open_n
        cv.put(1, h - 2, fit(hint, w - 2), th.c('dim', 'win'))
        msg = self.status
        if self.confirm:
            msg = '„%s“ löschen?' % fit(self.confirm.get('title') or '', w - 30)
        if self.err:
            msg_attr = th.c('red', 'win')
        elif self.cloud_offline or self.pending:
            msg_attr = th.c('yellow', 'win')
        else:
            msg_attr = th.c('dim', 'win')
        cv.put(1, h - 1, fit(msg, w - 12), msg_attr)
        if self.dropdown_open:
            self.draw_dropdown(cv)
        if self.filter_open:
            self.draw_filter(cv)
        if self.mode == 'compose':
            cv.fill(0, 0, w, h, th.c('win', 'win'), ' ')
            if self.cal:
                self.draw_calendar(cv)
            else:
                self.draw_compose(cv)
        if self.mode == 'newlist':
            cv.fill(0, 0, w, h, th.c('win', 'win'), ' ')
            self.draw_newlist(cv)
        if self.confirm:
            self.draw_confirm(cv)

    def draw_dropdown(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        tabs = self.tabs()
        counts = self.tab_counts()
        if not tabs:
            return
        labels = [self.tab_label(t, counts) for t in tabs]
        maxw = w
        vis = min(len(tabs), max(1, h - 6))
        if self.drop_sel < self.drop_start:
            self.drop_start = self.drop_sel
        if self.drop_sel >= self.drop_start + vis:
            self.drop_start = self.drop_sel - vis + 1
        self.drop_start = max(0, min(self.drop_start, max(0, len(tabs) - vis)))
        x0, y0 = 0, 2
        ph = vis + 2
        cv.fill(x0, y0, maxw, ph, th.c('text', 'raised'))
        cv.fill(x0, y0, maxw, 1, th.c('accent', 'win'), '-')
        cv.fill(x0, y0 + ph - 1, maxw, 1, th.c('accent', 'win'), '-')
        for yy in range(y0 + 1, y0 + ph - 1):
            cv.put(x0, yy, '|', th.c('accent', 'win'))
            cv.put(x0 + maxw - 1, yy, '|', th.c('accent', 'win'))
        for i in range(vis):
            idx = self.drop_start + i
            if idx >= len(tabs):
                break
            y = y0 + 1 + i
            sel = idx == self.drop_sel
            attr = th.c('on_accent', 'accent') if sel else th.c('text', 'raised')
            if sel:
                cv.fill(x0 + 1, y, maxw - 2, 1, attr, ' ')
            cv.put(x0 + 2, y, fit(labels[idx], maxw - 4), attr)
            self.zone(x0 + 1, y, maxw - 2, 1, 'drop:%d' % idx)

    def compose_rows(self):
        if not self.comp:
            return []
        rows = []
        if not self.comp['editing']:
            rows.append('kind')
            rows.append('list')
        rows.append('title')
        if self.comp['kind'] == 'task':
            if not self.comp_list_private():
                rows.append('assignee')
            rows.append('due')
            if self.comp.get('show_repeat'):
                rows.append('repeat')
            rows += ['prio', 'notes']
        else:
            rows += ['qty', 'notes']
        return rows

    def comp_lists(self):
        if not self.snap:
            return []
        if self.comp['kind'] == 'task':
            return list_titles(self.snap)
        return self.snap.get('shopping_lists', [])

    def comp_list_title(self):
        lists = self.comp_lists()
        if not lists:
            return '-'
        idx = min(self.comp['list_idx'], len(lists) - 1)
        return lists[idx].get('title') or '?'

    def comp_list_id(self):
        lists = self.comp_lists()
        if not lists:
            return None
        idx = min(self.comp['list_idx'], len(lists) - 1)
        return lists[idx].get('id')

    def comp_list_private(self):
        if not self.snap or not self.comp:
            return False
        if self.comp['editing']:
            entry = self.comp.get('entry')
            lid = entry['task'].get('list_id') if entry and entry.get('kind') == 'task' else None
        else:
            lid = self.comp_list_id()
        lst = next((x for x in list_titles(self.snap) if x.get('id') == lid), None)
        return bool(lst and lst.get('is_private'))

    def comp_fix_field(self):
        if self.comp_list_private():
            self.comp['assignee'] = None
        rows = self.compose_rows()
        if rows:
            self.comp['field'] = min(self.comp['field'], len(rows) - 1)

    def comp_assignee_name(self):
        if not self.comp['assignee']:
            return 'niemand'
        return member_map(self.snap or {}).get(self.comp['assignee'], 'niemand')

    def open_compose(self, entry=None):
        if not self.snap:
            return
        editing = entry is not None
        kind = 'task'
        fields = {
            'title': TextField(placeholder='Was ist zu tun?'),
            'notes': TextField(placeholder='Notiz (optional)'),
            'due': TextField(placeholder='TT-MM-JJJJ'),
            'qty': TextField(placeholder='Menge (optional)'),
        }
        comp = {'kind': kind, 'list_idx': 0, 'assignee': None, 'prio': 'normal',
                'editing': editing, 'field': 0, 'fields': fields, 'entry': entry,
                'repeat': 'none', 'show_repeat': kind == 'task', 'had_recurrence': False}
        if entry:
            if entry['kind'] == 'task':
                t = entry['task']
                comp['had_recurrence'] = bool(t.get('recurrence_rule'))
                comp['repeat'] = repeat_key(t.get('recurrence_rule'))
                comp['kind'] = 'task'
                fields['title'].set(t.get('title') or '')
                fields['notes'].set(t.get('notes') or '')
                if t.get('due_date'):
                    try:
                        y, m, d = (int(x) for x in t['due_date'].split('-'))
                        fields['due'].set(format_due(date(y, m, d)))
                    except (ValueError, TypeError):
                        fields['due'].set(t.get('due_date') or '')
                comp['prio'] = t.get('priority') or 'normal'
                ids = t.get('assignee_member_ids') or []
                comp['assignee'] = ids[0] if ids else None
            else:
                it = entry['item']
                comp['kind'] = 'shopping'
                fields['title'].set(it.get('name') or '')
                fields['notes'].set(it.get('notes') or '')
                fields['qty'].set(it.get('quantity_text') or '')
        else:
            if self.tab:
                comp['kind'] = self.tab['kind']
            lists = list_titles(self.snap) if comp['kind'] == 'task' else self.snap.get('shopping_lists', [])
            ids = [l['id'] for l in lists]
            if self.tab and self.tab['id'] in ids:
                comp['list_idx'] = ids.index(self.tab['id'])
        self.comp = comp
        self.cal = None
        self.mode = 'compose'
        rows = self.compose_rows()
        if 'title' in rows:
            comp['field'] = rows.index('title')
        self.invalidate()

    def close_compose(self):
        self.mode = ''
        self.comp = None
        self.cal = None
        self.invalidate()

    def draw_filter(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        opts = self.filter_options()
        counts = self.filter_counts()
        if not opts:
            return
        labels = ['%s  (%d)' % (o['label'], counts.get(o['id'], 0)) for o in opts]
        maxw = w
        vis = min(len(opts), max(1, h - 7))
        if self.filter_sel < self.filter_start:
            self.filter_start = self.filter_sel
        if self.filter_sel >= self.filter_start + vis:
            self.filter_start = self.filter_sel - vis + 1
        self.filter_start = max(0, min(self.filter_start, max(0, len(opts) - vis)))
        x0, y0 = 0, 3
        ph = vis + 2
        cv.fill(x0, y0, maxw, ph, th.c('text', 'raised'))
        cv.fill(x0, y0, maxw, 1, th.c('accent', 'win'), '-')
        cv.fill(x0, y0 + ph - 1, maxw, 1, th.c('accent', 'win'), '-')
        for yy in range(y0 + 1, y0 + ph - 1):
            cv.put(x0, yy, '|', th.c('accent', 'win'))
            cv.put(x0 + maxw - 1, yy, '|', th.c('accent', 'win'))
        for i in range(vis):
            idx = self.filter_start + i
            if idx >= len(opts):
                break
            y = y0 + 1 + i
            sel = idx == self.filter_sel
            attr = th.c('on_accent', 'accent') if sel else th.c('text', 'raised')
            if sel:
                cv.fill(x0 + 1, y, maxw - 2, 1, attr, ' ')
            cv.put(x0 + 2, y, fit(labels[idx], maxw - 4), attr)
            self.zone(x0 + 1, y, maxw - 2, 1, 'filter:%d' % idx)

    def draw_compose(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        c = self.comp
        rows = self.compose_rows()
        bh = len(rows) + 5
        bw = min(76, w - 8)
        x0 = max(2, (w - bw) // 2)
        y0 = max(2, (h - bh) // 2)
        cv.fill(x0, y0, bw, bh, th.c('win', 'win'))
        cv.fill(x0, y0, bw, 1, th.c('accent', 'win'), '-')
        cv.fill(x0, y0 + bh - 1, bw, 1, th.c('accent', 'win'), '-')
        for yy in range(y0 + 1, y0 + bh - 1):
            cv.put(x0, yy, '|', th.c('accent', 'win'))
            cv.put(x0 + bw - 1, yy, '|', th.c('accent', 'win'))
        title = 'Bearbeiten' if c['editing'] else 'Neu anlegen'
        cv.put(x0 + 2, y0, ' %s ' % title, th.c('on_accent', 'accent'))
        y = y0 + 2
        labels = {
            'kind': 'Art', 'list': 'Liste', 'title': 'Titel', 'assignee': 'Zuweisen',
            'due': 'Fällig', 'repeat': 'Serie', 'prio': 'Priorität', 'notes': 'Notiz', 'qty': 'Menge',
        }
        for i, key in enumerate(rows):
            active = (i == c['field'])
            base = th.c('accent', 'win') if active else th.c('text', 'win')
            cv.put(x0 + 2, y, '>' if active else ' ', th.c('accent', 'win'))
            cv.put(x0 + 4, y, labels[key], th.c('dim', 'win'))
            vx = x0 + 14
            vw = bw - 16
            if key == 'kind':
                cv.put(vx, y, 'Aufgabe' if c['kind'] == 'task' else 'Einkaufsidee', base)
            elif key == 'list':
                cv.put(vx, y, fit(self.comp_list_title(), vw), base)
            elif key == 'assignee':
                cv.put(vx, y, fit(self.comp_assignee_name(), vw), base)
            elif key == 'repeat':
                cv.put(vx, y, REPEAT_LABELS.get(c['repeat'], 'keine'), base)
            elif key == 'prio':
                cv.put(vx, y, 'wichtig' if c['prio'] == 'high' else 'normal', base)
            elif key in ('due', 'notes', 'qty', 'title'):
                c['fields'][key].draw(cv, vx, y, vw, th, active)
            self.zone(x0 + 1, y, bw - 2, 1, 'comp:row:%d' % i)
            y += 1
        y += 1
        x = x0 + 2
        x += button(self, cv, x, y, 'Speichern', 'comp:save', primary=True) + 1
        button(self, cv, x, y, 'Abbrechen', 'comp:cancel')
        if rows[c['field']] == 'due':
            hint = 'Enter öffnet den Kalender'
        elif rows[c['field']] == 'repeat' and c['repeat'] != 'none':
            hint = 'Serie: nächste Anzeige = Fällig-Datum'
        else:
            hint = 'Enter speichert · Esc schließt'
        cv.put(x0 + 2, y0 + bh - 3, hint, th.c('dim', 'win'))

    def draw_calendar(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        c = self.cal
        weeks = calendar_weeks(c['y'], c['m'])
        bw, bh = 34, 13
        x0 = max(1, (w - bw) // 2)
        y0 = max(1, (h - bh) // 2)
        cv.fill(x0, y0, bw, bh, th.c('win', 'win'))
        cv.fill(x0, y0, bw, 1, th.c('accent', 'win'), '-')
        cv.fill(x0, y0 + bh - 1, bw, 1, th.c('accent', 'win'), '-')
        for yy in range(y0 + 1, y0 + bh - 1):
            cv.put(x0, yy, '|', th.c('accent', 'win'))
            cv.put(x0 + bw - 1, yy, '|', th.c('accent', 'win'))
        title = ' %s %d ' % (MONTHS[c['m'] - 1], c['y'])
        cv.put(x0 + 2, y0, title, th.c('on_accent', 'accent'))
        cv.put(x0 + 2, y0 + 2, 'Mo Di Mi Do Fr Sa So', th.c('dim', 'win'))
        today = date.today()
        sel = date(c['y'], c['m'], c['d'])
        for wi, week in enumerate(weeks):
            for wd, day in enumerate(week):
                if not day:
                    continue
                d = date(c['y'], c['m'], day)
                x = x0 + 2 + wd * 3
                y = y0 + 3 + wi
                if d == sel:
                    cv.fill(x, y, 2, 1, th.c('on_accent', 'accent'), ' ')
                    cv.put(x + 1, y, '%2d' % day, th.c('on_accent', 'accent'))
                elif d == today:
                    cv.put(x + 1, y, '%2d' % day, th.c('green', 'win'))
                else:
                    cv.put(x + 1, y, '%2d' % day, th.c('text', 'win'))
                self.zone(x, y, 3, 1, 'cal:day:%04d-%02d-%02d' % (c['y'], c['m'], day))
        cv.put(x0 + 2, y0 + 9, '←→ Tag  ↑↓ Woche  t heute', th.c('dim', 'win'))
        cv.put(x0 + 2, y0 + 10, 'PgUp/PgDn Monat  x löschen', th.c('dim', 'win'))
        x = x0 + 2
        y = y0 + 11
        x += button(self, cv, x, y, 'Übernehmen', 'cal:ok', primary=True) + 1
        button(self, cv, x, y, 'Abbrechen', 'cal:cancel')

    def draw_newlist(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        c = self.newlist
        bh = 9
        bw = min(60, w - 8)
        x0 = max(2, (w - bw) // 2)
        y0 = max(2, (h - bh) // 2)
        cv.fill(x0, y0, bw, bh, th.c('win', 'win'))
        cv.fill(x0, y0, bw, 1, th.c('accent', 'win'), '-')
        cv.fill(x0, y0 + bh - 1, bw, 1, th.c('accent', 'win'), '-')
        for yy in range(y0 + 1, y0 + bh - 1):
            cv.put(x0, yy, '|', th.c('accent', 'win'))
            cv.put(x0 + bw - 1, yy, '|', th.c('accent', 'win'))
        cv.put(x0 + 2, y0, ' Neue Liste ', th.c('on_accent', 'accent'))
        rows = ['kind', 'title', 'private']
        labels = {'kind': 'Art', 'title': 'Name', 'private': 'Privat'}
        y = y0 + 2
        for i, key in enumerate(rows):
            active = i == c['field']
            cv.put(x0 + 2, y, '>' if active else ' ', th.c('accent', 'win'))
            cv.put(x0 + 4, y, labels[key], th.c('dim', 'win'))
            if key == 'kind':
                cv.put(x0 + 14, y, 'Einkaufsliste' if c['kind'] == 'shopping' else 'Aufgabenliste',
                       th.c('accent', 'win') if active else th.c('text', 'win'))
            elif key == 'private':
                cv.put(x0 + 14, y, 'ja' if c['private'] else 'nein',
                       th.c('accent', 'win') if active else th.c('text', 'win'))
            else:
                c['fields']['title'].draw(cv, x0 + 14, y, bw - 16, th, active)
            self.zone(x0 + 1, y, bw - 2, 1, 'nl:row:%d' % i)
            y += 1
        y += 1
        x = x0 + 2
        x += button(self, cv, x, y, 'Anlegen', 'nl:save', primary=True) + 1
        button(self, cv, x, y, 'Abbrechen', 'nl:cancel')
        cv.put(x0 + 2, y0 + bh - 2, 'Enter speichert · Esc schließt', th.c('dim', 'win'))

    def draw_confirm(self, cv):
        th = self.th
        w, h = cv.w, cv.h
        text = self.confirm.get('title') or ''
        bw = min(max(36, len(text) + 12), w - 4)
        bh = 6
        x0 = (w - bw) // 2
        y0 = (h - bh) // 2
        cv.fill(x0, y0, bw, bh, th.c('win', 'win'))
        cv.fill(x0, y0, bw, 1, th.c('red', 'win'), '-')
        cv.fill(x0, y0 + bh - 1, bw, 1, th.c('red', 'win'), '-')
        for yy in range(y0 + 1, y0 + bh - 1):
            cv.put(x0, yy, '|', th.c('red', 'win'))
            cv.put(x0 + bw - 1, yy, '|', th.c('red', 'win'))
        cv.put(x0 + 2, y0, ' Löschen? ', th.c('on_accent', 'red'))
        cv.put(x0 + 2, y0 + 2, fit(text, bw - 4), th.c('text', 'win'))
        x = x0 + 2
        y = y0 + 4
        x += button(self, cv, x, y, 'Löschen', 'confirm:yes', primary=True) + 1
        button(self, cv, x, y, 'Abbrechen', 'confirm:no')
        cv.put(x0 + 2, y0 + 3, 'y = löschen · n = abbrechen', th.c('dim', 'win'))

    def open_compose_from(self, item):
        self.open_compose(item)

    def ask_delete(self, entry):
        data = entry['task'] if entry['kind'] == 'task' else entry['item']
        self.confirm = {'kind': entry['kind'], 'id': data['id'],
                        'title': data.get('title') or data.get('name') or ''}
        self.invalidate()

    def do_delete(self):
        entry = self.confirm
        self.confirm = None
        if not entry:
            return
        if entry.get('kind') == 'task':
            path = '/api/tasks/%s' % entry['id']
        else:
            path = '/api/shopping/items/%s' % entry['id']

        def work():
            return http_json(self.cfg, path, 'DELETE')

        def done(res):
            self.status = 'gelöscht'
            self.later(0.4, self.load)
            self.invalidate()

        self.thread(work, done, self.fail)

    def open_newlist(self):
        self.newlist = {'kind': 'task', 'private': False, 'field': 1,
                        'fields': {'title': TextField(placeholder='Name der Liste')}}
        self.mode = 'newlist'
        self.invalidate()

    def save_newlist(self):
        c = self.newlist
        title = c['fields']['title'].text.strip()
        if not title:
            self.wm.toast('Famagic: Name fehlt', 'red')
            return
        kind = c['kind']
        path = '/api/shopping/lists' if kind == 'shopping' else '/api/lists'
        body = {'title': title, 'is_private': c['private']}

        def work():
            return http_json(self.cfg, path, 'POST', body)

        def done(res):
            if isinstance(res, dict) and res.get('id'):
                self.pending_tab = {'kind': kind, 'id': res['id']}
            self.mode = ''
            self.newlist = None
            self.status = 'Liste angelegt'
            self.later(0.4, self.load)
            self.invalidate()

        self.thread(work, done, self.fail)

    def open_calendar(self):
        text = self.comp['fields']['due'].text if self.comp else ''
        try:
            d = parse_due_text(text) or date.today()
        except ValueError:
            d = date.today()
        self.cal = {'y': d.year, 'm': d.month, 'd': d.day}
        self.invalidate()

    def calendar_pick(self):
        c = self.cal
        d = date(c['y'], c['m'], c['d'])
        self.comp['fields']['due'].set(format_due(d))
        self.cal = None
        self.invalidate()

    def calendar_clear(self):
        self.comp['fields']['due'].set('')
        self.cal = None
        self.invalidate()

    def save_compose(self):
        c = self.comp
        if not c:
            return
        title = c['fields']['title'].text.strip()
        if not title:
            self.wm.toast('Famagic: Titel fehlt', 'red')
            return
        due_text_value = c['fields']['due'].text.strip()
        try:
            due_date = parse_due_text(due_text_value)
        except ValueError:
            self.wm.toast('Famagic: Datum als TT-MM-JJJJ', 'red')
            return
        iso_due = due_date.isoformat() if due_date else None
        if c.get('show_repeat') and c['repeat'] != 'none' and not iso_due:
            self.wm.toast('Famagic: Serie braucht ein Fällig-Datum', 'red')
            return
        if c['editing']:
            entry = c['entry']
            if entry['kind'] == 'task':
                body = {'title': title, 'notes': c['fields']['notes'].text.strip(),
                        'priority': c['prio'], 'due_date': iso_due}
                if c.get('show_repeat') and (c.get('had_recurrence') or c['repeat'] != 'none'):
                    body['recurrence_rule'] = repeat_rule(c['repeat'])
                if c['assignee'] and not self.comp_list_private():
                    body['assignee_member_ids'] = [c['assignee']]
                task_id = entry['task']['id']
                self.close_compose()

                def work():
                    return http_json(self.cfg, '/api/tasks/%s' % task_id, 'PATCH', body)
            else:
                body = {'name': title, 'notes': c['fields']['notes'].text.strip(),
                        'quantity_text': c['fields']['qty'].text.strip()}
                item_id = entry['item']['id']
                self.close_compose()

                def work():
                    return http_json(self.cfg, '/api/shopping/items/%s' % item_id, 'PATCH', body)
        else:
            list_id = self.comp_list_id()
            if not list_id:
                self.wm.toast('Famagic: keine Liste', 'red')
                return
            if c['kind'] == 'task':
                body = {'list_id': list_id, 'title': title, 'priority': c['prio'],
                        'notes': c['fields']['notes'].text.strip()}
                if iso_due:
                    body['due_date'] = iso_due
                if c['repeat'] != 'none':
                    body['recurrence_rule'] = repeat_rule(c['repeat'])
                if c['assignee'] and not self.comp_list_private():
                    body['assignee_member_ids'] = [c['assignee']]
                self.close_compose()

                def work():
                    return http_json(self.cfg, '/api/tasks', 'POST', body)
            else:
                body = {'list_id': list_id, 'name': title,
                        'quantity_text': c['fields']['qty'].text.strip(),
                        'notes': c['fields']['notes'].text.strip()}
                self.close_compose()

                def work():
                    return http_json(self.cfg, '/api/shopping/items', 'POST', body)

        def done(res):
            self.status = 'gespeichert'
            if isinstance(res, dict) and res.get('id'):
                self.pending_select = res.get('id')
            self.later(0.4, self.load)
            self.invalidate()

        def bad(e):
            self.fail(e)

        self.thread(work, done, bad)

    def valid_due(self, text):
        try:
            parse_due_text(text)
            return True
        except ValueError:
            return False

    def on_click(self, key, ev):
        if self.mode == 'compose':
            if self.cal:
                if key == 'cal:ok':
                    self.calendar_pick()
                elif key == 'cal:cancel':
                    self.cal = None
                    self.invalidate()
                elif key and key.startswith('cal:day:'):
                    try:
                        y, m, d = (int(x) for x in key.split(':')[2].split('-'))
                        self.cal['y'], self.cal['m'], self.cal['d'] = y, m, d
                        self.calendar_pick()
                    except (ValueError, IndexError):
                        pass
                return
            if key == 'comp:save':
                self.save_compose()
            elif key == 'comp:cancel':
                self.close_compose()
            elif key and key.startswith('comp:row:'):
                i = int(key.split(':')[2])
                rows = self.compose_rows()
                if 0 <= i < len(rows):
                    self.comp['field'] = i
                self.invalidate()
            return
        if self.mode == 'newlist':
            if key == 'nl:save':
                self.save_newlist()
            elif key == 'nl:cancel':
                self.mode = ''
                self.newlist = None
                self.invalidate()
            elif key and key.startswith('nl:row:'):
                self.newlist['field'] = int(key.split(':')[2])
                self.invalidate()
            return
        if self.confirm:
            if key == 'confirm:yes':
                self.do_delete()
            elif key == 'confirm:no':
                self.confirm = None
                self.invalidate()
            return
        if key == 'filter':
            if self.filter_open:
                self.filter_open = False
                self.invalidate()
            else:
                self.open_filter()
            return
        if key and key.startswith('filter:'):
            i = int(key.split(':', 1)[1])
            opts = self.filter_options()
            if 0 <= i < len(opts):
                self.filter_member = opts[i]['id']
                self.filter_open = False
                self.rebuild()
            return
        if key == 'dropdown':
            self.filter_open = False
            if self.dropdown_open:
                self.dropdown_open = False
                self.invalidate()
            else:
                self.open_dropdown()
            return
        if key == 'newlist':
            self.dropdown_open = False
            self.filter_open = False
            self.open_newlist()
            return
        if key == 'new':
            self.dropdown_open = False
            self.filter_open = False
            self.open_compose()
            return
        if key and key.startswith('drop:'):
            i = int(key.split(':', 1)[1])
            tabs = self.tabs()
            if 0 <= i < len(tabs):
                self.select_tab(tabs[i])
            return
        if self.dropdown_open:
            self.dropdown_open = False
            self.invalidate()
            return
        if self.filter_open:
            self.filter_open = False
            self.invalidate()
            return
        if key == 'rows':
            self.list_click(ev)

    def list_click(self, ev):
        if ev.kind in ('wheelup', 'wheeldown'):
            self.list.mouse(ev, ev.y - 4)
            return
        ry = ev.y - 4
        idx = self.list.top + ry
        items = self.list.items
        if not (0 <= idx < len(items)):
            return
        item = items[idx]
        if item['kind'] == 'toggle':
            self.done_open = not self.done_open
            self.rebuild()
            return
        rows_h = max(1, self.ch - 9)
        sw = 1 if len(items) > rows_h else 0
        width = self.cw - sw
        if ev.x >= width:
            self.list.mouse(ev, ry)
            return
        action = click_action(ev.x, width)
        self.list.select(idx)
        if action == 'delete':
            if ev.clicks <= 1:
                self.ask_delete(item)
            return
        if action == 'edit':
            if ev.clicks <= 1:
                self.open_compose(item)
            return
        if action == 'toggle':
            if ev.clicks <= 1:
                self.toggle(item)
            return
        self.list.mouse(ev, ry)

    def on_key(self, ev):
        if self.mode == 'compose':
            self.key_compose(ev)
            return True
        if self.mode == 'newlist':
            self.key_newlist(ev)
            return True
        if self.confirm:
            if ev.key in ('y', 'j'):
                self.do_delete()
            elif ev.key in ('n', 'esc', 'q'):
                self.confirm = None
                self.invalidate()
            return True
        if self.filter_open:
            opts = self.filter_options()
            if ev.key in ('esc', 'f'):
                self.filter_open = False
            elif ev.key == 'up':
                self.filter_sel = max(0, self.filter_sel - 1)
            elif ev.key == 'down':
                self.filter_sel = min(max(0, len(opts) - 1), self.filter_sel + 1)
            elif ev.key in ('enter', ' '):
                if opts:
                    self.filter_member = opts[min(self.filter_sel, len(opts) - 1)]['id']
                    self.filter_open = False
                    self.rebuild()
                    return True
                self.filter_open = False
            self.invalidate()
            return True
        if self.dropdown_open:
            tabs = self.tabs()
            if ev.key in ('esc', 'l'):
                self.dropdown_open = False
            elif ev.key == 'f':
                self.dropdown_open = False
                self.open_filter()
                return True
            elif ev.key == 'up':
                self.drop_sel = max(0, self.drop_sel - 1)
            elif ev.key == 'down':
                self.drop_sel = min(max(0, len(tabs) - 1), self.drop_sel + 1)
            elif ev.key == 'home':
                self.drop_sel = 0
            elif ev.key == 'end':
                self.drop_sel = max(0, len(tabs) - 1)
            elif ev.key in ('enter', ' '):
                if tabs:
                    self.select_tab(tabs[min(self.drop_sel, len(tabs) - 1)])
                    return True
                self.dropdown_open = False
            self.invalidate()
            return True
        if ev.key == 'tab':
            tabs = self.tabs()
            if tabs:
                idx = 0
                if self.tab in tabs:
                    idx = (tabs.index(self.tab) + 1) % len(tabs)
                self.tab = tabs[idx]
                self.done_open = False
                self.filter_open = False
                self.rebuild()
                self.invalidate()
            return True
        if len(ev.key) == 1 and ev.key.isdigit():
            tabs = self.tabs()
            i = int(ev.key) - 1
            if 0 <= i < len(tabs):
                self.tab = tabs[i]
                self.done_open = False
                self.filter_open = False
                self.rebuild()
                self.invalidate()
                return True
        if ev.key in (' ', 'enter'):
            self.toggle()
            return True
        if ev.key in ('n', 'a', '+'):
            self.open_compose()
            return True
        if ev.key == 'l':
            self.open_dropdown()
            return True
        if ev.key == 'f':
            self.open_filter()
            return True
        if ev.key == 'k':
            self.open_newlist()
            return True
        if ev.key in ('e', 'f2', '!'):
            it = self.current()
            if it and it['kind'] != 'toggle':
                self.open_compose(it)
            return True
        if ev.key in ('d', 'delete'):
            it = self.current()
            if it and it['kind'] != 'toggle':
                self.ask_delete(it)
            return True
        if ev.key == 'r':
            self.status = 'lade ...'
            self.load()
            return True
        if self.list.key(ev):
            self.invalidate()
            return True
        return False

    def key_newlist(self, ev):
        c = self.newlist
        rows = ['kind', 'title', 'private']
        if ev.key == 'esc':
            self.mode = ''
            self.newlist = None
            self.invalidate()
            return
        if ev.key in ('up', 'down', 'tab'):
            step = -1 if ev.key == 'up' else 1
            c['field'] = (c['field'] + step) % len(rows)
            self.invalidate()
            return
        if ev.key == 'enter':
            self.save_newlist()
            return
        key = rows[c['field']]
        if key == 'kind':
            if ev.key in ('left', 'right', ' '):
                c['kind'] = 'shopping' if c['kind'] == 'task' else 'task'
                self.invalidate()
        elif key == 'private':
            if ev.key in ('left', 'right', ' '):
                c['private'] = not c['private']
                self.invalidate()
        else:
            if c['fields']['title'].key(ev):
                self.invalidate()

    def key_compose(self, ev):
        c = self.comp
        if self.cal:
            self.key_calendar(ev)
            return
        rows = self.compose_rows()
        if not rows:
            self.close_compose()
            return
        if ev.key == 'esc':
            self.close_compose()
            return
        if ev.key in ('up', 'down', 'tab'):
            step = -1 if ev.key == 'up' else 1
            c['field'] = (c['field'] + step) % len(rows)
            self.invalidate()
            return
        key = rows[c['field']]
        if ev.key == 'enter':
            if key == 'due':
                self.open_calendar()
            else:
                self.save_compose()
            return
        if key == 'kind':
            if ev.key in ('left', 'right', ' '):
                c['kind'] = 'shopping' if c['kind'] == 'task' else 'task'
                c['list_idx'] = 0
                self.comp_fix_field()
                self.invalidate()
            return
        if key == 'list':
            lists = self.comp_lists()
            if lists and ev.key in ('left', 'right', ' '):
                step = -1 if ev.key == 'left' else 1
                c['list_idx'] = (c['list_idx'] + step) % len(lists)
                self.comp_fix_field()
                self.invalidate()
            return
        if key == 'assignee':
            if ev.key in ('left', 'right', ' '):
                ids = [None] + [m['id'] for m in self.snap.get('members', [])]
                try:
                    i = ids.index(c['assignee'])
                except ValueError:
                    i = 0
                step = -1 if ev.key == 'left' else 1
                c['assignee'] = ids[(i + step) % len(ids)]
                self.invalidate()
            return
        if key == 'repeat':
            if ev.key in ('left', 'right', ' '):
                keys = ['none', 'daily', 'weekly', 'monthly', 'yearly']
                try:
                    i = keys.index(c['repeat'])
                except ValueError:
                    i = 0
                step = -1 if ev.key == 'left' else 1
                c['repeat'] = keys[(i + step) % len(keys)]
                if c['repeat'] != 'none' and not c['fields']['due'].text.strip():
                    c['fields']['due'].set(format_due(date.today()))
                self.invalidate()
            return
        if key == 'prio':
            if ev.key in ('left', 'right', ' '):
                c['prio'] = 'normal' if c['prio'] == 'high' else 'high'
                self.invalidate()
            return
        if key in ('due', 'notes', 'qty', 'title'):
            if c['fields'][key].key(ev):
                self.invalidate()

    def key_calendar(self, ev):
        c = self.cal
        d = date(c['y'], c['m'], c['d'])
        if ev.key == 'esc':
            self.cal = None
            self.invalidate()
            return
        if ev.key == 'enter':
            self.calendar_pick()
            return
        if ev.key == 'x':
            self.calendar_clear()
            return
        if ev.key == 't':
            d = date.today()
        elif ev.key == 'left':
            d = d.fromordinal(d.toordinal() - 1)
        elif ev.key == 'right':
            d = d.fromordinal(d.toordinal() + 1)
        elif ev.key == 'up':
            d = d.fromordinal(d.toordinal() - 7)
        elif ev.key == 'down':
            d = d.fromordinal(d.toordinal() + 7)
        elif ev.key == 'pgup':
            m = c['m'] - 1
            y = c['y']
            if m < 1:
                m = 12
                y -= 1
            last = _calendar.monthrange(y, m)[1]
            d = date(y, m, min(c['d'], last))
        elif ev.key == 'pgdn':
            m = c['m'] + 1
            y = c['y']
            if m > 12:
                m = 1
                y += 1
            last = _calendar.monthrange(y, m)[1]
            d = date(y, m, min(c['d'], last))
        else:
            return
        c['y'], c['m'], c['d'] = d.year, d.month, d.day
        self.invalidate()

    def toggle(self, entry=None):
        it = entry or self.current()
        if not it:
            return
        if it['kind'] == 'toggle':
            self.done_open = not self.done_open
            self.rebuild()
            return
        if it['kind'] == 'task':
            task = it['task']
            target = not task.get('is_completed')

            def work():
                return http_json(self.cfg, '/api/tasks/%s/complete' % task['id'], 'POST', {'completed': target})
        else:
            item = it['item']
            target = not item.get('is_checked')

            def work():
                return http_json(self.cfg, '/api/shopping/items/%s' % item['id'], 'PATCH', {'is_checked': target})

        def done(res):
            outcome = res.get('outcome') if isinstance(res, dict) else None
            if outcome in ('completed_until', 'rolled_over'):
                nxt = res.get('next_due_date') or ''
                self.status = 'Für dieses Mal erledigt · wieder am %s' % nxt
                self.wm.toast('Famagic: erledigt · wieder am %s' % nxt, 'green')
            else:
                self.status = 'abgehakt' if target else 'wieder offen'
            self.later(0.4, self.load)
            self.invalidate()

        self.thread(work, done, self.fail)

    def fail(self, e):
        self.err = str(e)
        self.status = 'Fehler'
        self.wm.toast('Famagic: %s' % e, 'red')
        self.invalidate()


APP = Famagic
