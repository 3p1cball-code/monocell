"""Google Calendar (and any iCalendar feed) as a read-only subscription.

Google exposes a secret iCal address (Settings > calendar > 'Secret address in iCal format').
We fetch it, parse VEVENTs (with a small RRULE expander) and cache the occurrences so the
calendar works offline. No OAuth, nothing is written back.
"""
import calendar as pycal
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .util import CACHE_DIR, DATA_DIR, ensure_dirs

CACHE = os.path.join(DATA_DIR, 'gcal.json')
HORIZON_BACK = 120
HORIZON_FWD = 400
MAX_PER_EVENT = 600


def unfold(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for line in text.split('\n'):
        if line[:1] in (' ', '\t') and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split(line):
    """'DTSTART;TZID=Europe/Berlin:20261021T140000' -> (name, params, value)."""
    m = re.match(r'([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$', line)
    if not m:
        return None
    name, par, val = m.group(1).upper(), m.group(2), m.group(3)
    params = {}
    for p in par.split(';'):
        if '=' in p:
            k, v = p.split('=', 1)
            params[k.upper()] = v.strip('"')
    return name, params, val.replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')


def _dt(val, params):
    val = val.strip()
    tzid = params.get('TZID')
    try:
        if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', val):
            d = dt.datetime.strptime(val[:8], '%Y%m%d').date()
            return dt.datetime.combine(d, dt.time(0, 0)), True
        utc = val.endswith('Z')
        core = val.rstrip('Z')
        fmt = '%Y%m%dT%H%M%S' if 'T' in core else '%Y%m%d'
        d = dt.datetime.strptime(core, fmt)
        if utc:
            d = d.replace(tzinfo=dt.timezone.utc)
        elif tzid:
            try:
                d = d.replace(tzinfo=ZoneInfo(tzid))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        return d, False
    except ValueError:
        return None, False


def _local(d):
    if d.tzinfo is None:
        return d
    return d.astimezone()


def _parse_rrule(val):
    r = {}
    for part in val.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            r[k.upper()] = v
    return r


WEEKDAYS = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}


def _occurrences(start, rule, exdates, lo, hi):
    """Expand a (simple) RRULE between lo and hi (datetimes)."""
    if start.tzinfo:
        if lo.tzinfo is None:
            lo = lo.replace(tzinfo=start.tzinfo)
        if hi.tzinfo is None:
            hi = hi.replace(tzinfo=start.tzinfo)
        exdates = {d.astimezone(start.tzinfo) if d.tzinfo else d.replace(tzinfo=start.tzinfo)
                   for d in exdates}
    else:
        lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
        exdates = {d.replace(tzinfo=None) for d in exdates}
    if not rule:
        return [start] if lo <= start <= hi else []
    freq = rule.get('FREQ', '').upper()
    try:
        interval = max(1, int(rule.get('INTERVAL', '1')))
    except ValueError:
        interval = 1
    count = int(rule['COUNT']) if rule.get('COUNT', '').isdigit() else None
    until = None
    if rule.get('UNTIL'):
        until, _ = _dt(rule['UNTIL'], {})
    byday = [WEEKDAYS[d[-2:]] for d in rule.get('BYDAY', '').split(',') if d[-2:] in WEEKDAYS]
    bymonthday = [int(x) for x in rule.get('BYMONTHDAY', '').split(',') if re.fullmatch(r'-?\d+', x)]
    out = []
    cur = start
    n = 0
    guard = 0
    while guard < 20000 and len(out) < MAX_PER_EVENT:
        guard += 1
        if until and cur.replace(tzinfo=None) > until.replace(tzinfo=None):
            break
        if cur > hi:
            break
        keep = False
        if freq == 'DAILY':
            keep = True
        elif freq == 'WEEKLY':
            if byday:
                keep = cur.weekday() in byday
            else:
                keep = cur.weekday() == start.weekday()
        elif freq == 'MONTHLY':
            if bymonthday:
                keep = cur.day in bymonthday or (cur.day == pycal.monthrange(cur.year, cur.month)[1]
                                                 and -1 in bymonthday)
            else:
                keep = cur.day == start.day
        elif freq == 'YEARLY':
            keep = cur.month == start.month and cur.day == start.day
        if keep:
            n += 1
            if count and n > count:
                break
            if cur not in exdates and lo <= cur <= hi:
                out.append(cur)
        # advance
        if freq == 'DAILY':
            cur += dt.timedelta(days=interval)
        elif freq == 'WEEKLY':
            cur += dt.timedelta(days=1)
            # step weeks at week boundaries when BYDAY is used
            if byday and cur.weekday() == 0 and interval > 1:
                cur += dt.timedelta(weeks=interval - 1)
            elif not byday and cur.weekday() == start.weekday():
                cur += dt.timedelta(weeks=interval - 1)
        elif freq == 'MONTHLY':
            m = cur.month - 1 + interval
            y = cur.year + m // 12
            m = m % 12 + 1
            day = min(start.day, pycal.monthrange(y, m)[1])
            cur = cur.replace(year=y, month=m, day=day)
        elif freq == 'YEARLY':
            try:
                cur = cur.replace(year=cur.year + interval)
            except ValueError:
                cur = cur.replace(year=cur.year + interval, day=28)
        else:
            break
    return out


def parse_ics(text, lo=None, hi=None):
    """Return a list of occurrence dicts in local time."""
    today = dt.date.today()
    lo = lo or (dt.datetime.combine(today - dt.timedelta(days=HORIZON_BACK), dt.time(0, 0)))
    hi = hi or (dt.datetime.combine(today + dt.timedelta(days=HORIZON_FWD), dt.time(23, 59)))
    lines = unfold(text)
    events = []
    cur = None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            cur = {}
            continue
        if line == 'END:VEVENT':
            if cur:
                events.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        sp = _split(line)
        if not sp:
            continue
        name, params, val = sp
        if name in ('DTSTART', 'DTEND', 'EXDATE'):
            if name == 'EXDATE':
                cur.setdefault('EXDATE', []).append((val, params))
            else:
                cur[name] = (val, params)
        elif name in ('SUMMARY', 'LOCATION', 'DESCRIPTION', 'UID', 'RRULE', 'RECURRENCE-ID'):
            cur[name] = val
    occ = []
    for ev in events:
        if 'DTSTART' not in ev:
            continue
        start, all_day = _dt(*ev['DTSTART'])
        if start is None:
            continue
        end = None
        if 'DTEND' in ev:
            end, _ = _dt(*ev['DTEND'])
        if end is None:
            end = start + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
        exdates = set()
        for val, params in ev.get('EXDATE', []):
            for part in val.split(','):
                d, _ = _dt(part, params)
                if d:
                    exdates.add(d)
        rule = _parse_rrule(ev['RRULE']) if ev.get('RRULE') else None
        # recurrence overrides are added as their own event
        occs = _occurrences(start, rule, exdates, lo, hi)
        if ev.get('RECURRENCE-ID'):
            occs = _occurrences(start, None, set(), lo, hi)
        for o in occs:
            s = _local(o)
            e = _local(o + (end - start)) if not all_day else s + dt.timedelta(days=1)
            occ.append({
                'start': s.strftime('%Y-%m-%dT%H:%M'),
                'end': e.strftime('%Y-%m-%dT%H:%M'),
                'date': s.date().isoformat(),
                'time': '' if all_day else s.strftime('%H:%M'),
                'all_day': all_day,
                'summary': ev.get('SUMMARY', '(no title)').strip(),
                'location': ev.get('LOCATION', '').strip(),
                'desc': re.sub(r'\s+', ' ', ev.get('DESCRIPTION', ''))[:600],
                'uid': ev.get('UID', ''),
                'src': 'gcal',
            })
    occ.sort(key=lambda x: x['start'])
    return occ


def fetch_ics(url, timeout=25):
    from . import web
    u = url.strip()
    if u.startswith('webcal://'):
        u = 'https://' + u[len('webcal://'):]
    if u.startswith(('http://', 'https://')):
        _, ct, body = web.fetch(u, timeout=timeout, max_bytes=8_000_000,
                                headers={'Accept': 'text/calendar,*/*'})
        return body.decode('utf-8', 'replace')
    if u.startswith('file://'):
        u = urllib.parse.unquote(u[7:])
    with open(os.path.expanduser(u), encoding='utf-8', errors='replace') as f:
        return f.read()


def load_cache():
    try:
        with open(CACHE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data):
    ensure_dirs()
    try:
        tmp = CACHE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def sync(url):
    """Fetch + parse + cache. Returns (events, cached_dict, error_or_None)."""
    url = (url or '').strip()
    if not url:
        return [], {}, None
    try:
        text = fetch_ics(url)
        events = parse_ics(text)
        stamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        data = {'url': hashlib.sha1(url.encode()).hexdigest()[:12], 'fetched': stamp,
                'events': events, 'count': len(events)}
        save_cache(data)
        return events, data, None
    except Exception as e:
        cached = load_cache()
        err = str(e)[:140]
        return cached.get('events', []), cached, err


def cached_events(url):
    d = load_cache()
    if not url:
        return [], {}
    if d.get('url') != hashlib.sha1(url.strip().encode()).hexdigest()[:12]:
        return [], {}
    return d.get('events', []), d
