"""Famagic desktop widget: cached snapshot, range/person filter and labels."""
import json
import os
import pwd
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

DEFAULT_SERVER = 'http://192.168.33.24:8791'
CLIENT_CFG = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, '.config', 'famagic-sync', 'client.json')

RANGE_LABELS = {
    'week': 'this week',
    'today': 'today',
    '7': '7 days',
    '14': '14 days',
    '30': '30 days',
    'all': 'all',
}

_lock = threading.Lock()
_state = {'snap': None, 'error': None, 'at': 0.0}


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


def fetch(cfg=None):
    cfg = cfg or client_config()
    headers = {'Accept': 'application/json'}
    if cfg.get('token'):
        headers['Authorization'] = 'Bearer ' + cfg['token']
    req = urllib.request.Request(cfg['server'].rstrip('/') + '/api/snapshot', headers=headers)
    with urllib.request.urlopen(req, timeout=8) as f:
        return json.loads(f.read().decode('utf-8'))


def set_snapshot(snap, at=None):
    with _lock:
        _state['snap'] = snap
        _state['error'] = None
        _state['at'] = at if at is not None else time.monotonic()


def set_error(err):
    with _lock:
        _state['error'] = str(err)


def snapshot():
    with _lock:
        return _state['snap']


def error():
    with _lock:
        return _state['error']


def fetched_at():
    with _lock:
        return _state['at']


def member_options(snap=None):
    snap = snap if snap is not None else snapshot()
    out = [('*', 'all'), ('none', 'nobody')]
    for m in (snap or {}).get('members', []):
        prof = m.get('profile') or {}
        name = prof.get('first_name') or m.get('first_name') or m.get('role') or '?'
        out.append((m.get('id'), name))
    return out


def member_name(member_id, snap=None):
    for key, name in member_options(snap):
        if key == member_id:
            return name
    return member_id or '?'


def range_end(key, today=None):
    today = today or date.today()
    if key == 'today':
        return today
    if key == 'week':
        return today + timedelta(days=6 - today.weekday())
    if key == '7':
        return today + timedelta(days=6)
    if key == '14':
        return today + timedelta(days=13)
    if key == '30':
        return today + timedelta(days=29)
    return None


def due_text(task, today=None):
    raw = task.get('due_date')
    if not raw:
        return ''
    today = today or date.today()
    try:
        y, m, d = (int(x) for x in raw.split('-'))
        due = date(y, m, d)
    except (ValueError, TypeError):
        return str(raw)
    delta = (due - today).days
    if delta == 0:
        return 'heute'
    if delta == 1:
        return 'morgen'
    if delta < 0:
        return '%d T. überfällig' % -delta
    return '%02d-%02d-%04d' % (d, m, y)


def selected_list_ids(snap, value):
    if not snap:
        return set()
    active = {l.get('id') for l in snap.get('lists', []) if not l.get('archived_at')}
    if value is None:
        return active
    if isinstance(value, str):
        value = value.strip()
        if value in ('', '*'):
            return active
        if value == '-':
            return set()
        value = [x for x in value.split(',') if x]
    try:
        ids = set(value)
    except TypeError:
        return active
    if '*' in ids:
        return active
    return active & ids


def filter_tasks(snap, range_key='week', member='*', limit=5, today=None, list_ids=None):
    if not snap:
        return []
    today = today or date.today()
    end = range_end(range_key, today)
    active = selected_list_ids(snap, list_ids)
    private = {l.get('id') for l in snap.get('lists', []) if l.get('is_private')}
    out = []
    for t in snap.get('tasks', []):
        if t.get('is_completed') or t.get('parent_task_id'):
            continue
        if t.get('list_id') not in active:
            continue
        ids = t.get('assignee_member_ids') or []
        if t.get('list_id') not in private:
            if member == 'none' and ids:
                continue
            if member not in (None, '*', 'none') and member not in ids:
                continue
        due = t.get('due_date')
        if due:
            if end is not None and due > end.isoformat():
                continue
        elif range_key != 'all':
            continue
        out.append(t)
    out.sort(key=lambda t: (t.get('due_date') or '9999-12-31', (t.get('title') or '').lower()))
    return out[:limit] if limit else out


def status(snap, err):
    if err:
        return 'offline'
    if snap and snap.get('offline'):
        return 'online-offline'
    if snap:
        return 'online'
    return 'loading'
