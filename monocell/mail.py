"""Mail: IMAP/SMTP account handling, folder listing, header/body parsing, sending.

Everything here is independent of the UI so it can be tested without a network
and reused by other apps.  Accounts live in ``~/.config/monocell/mail.json``
(mode 0600, the passwords are in it — there is no keyring on the VT).

Only the last page of headers is fetched (STRATO mailboxes hold tens of
thousands of messages); paging older mail is done with sequence numbers.
"""
import base64
import datetime as dt
import email
import email.policy
import email.utils
import html as htmlmod
import imaplib
import json
import os
import re
import smtplib
import threading
import time
from email.message import EmailMessage
from email.parser import BytesHeaderParser, BytesParser
from html.parser import HTMLParser

from .util import CONFIG_DIR, CACHE_DIR, ensure_dirs

MAIL_FILE = os.path.join(CONFIG_DIR, 'mail.json')
HEADER_FIELDS = ('DATE FROM TO CC SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES '
                 'CONTENT-TYPE REPLY-TO LIST-ID')

PROVIDERS = {
    'strato': ('imap.strato.de', 993, 'smtp.strato.de', 465),
    'gmail': ('imap.gmail.com', 993, 'smtp.gmail.com', 465),
    'gmx': ('imap.gmx.net', 993, 'mail.gmx.net', 465),
    'web.de': ('imap.web.de', 993, 'smtp.web.de', 465),
    'ionos': ('imap.ionos.de', 993, 'smtp.ionos.de', 465),
    'outlook': ('outlook.office365.com', 993, 'smtp.office365.com', 587),
    'mailbox.org': ('imap.mailbox.org', 993, 'smtp.mailbox.org', 465),
    'posteo': ('posteo.de', 993, 'posteo.de', 465),
    'yahoo': ('imap.mail.yahoo.com', 993, 'smtp.mail.yahoo.com', 465),
    'icloud': ('imap.mail.me.com', 993, 'smtp.mail.me.com', 587),
    'zoho': ('imap.zoho.eu', 993, 'smtp.zoho.eu', 465),
    'fastmail': ('imap.fastmail.com', 993, 'smtp.fastmail.com', 465),
}

ROLE_ORDER = ('inbox', 'sent', 'drafts', 'junk', 'trash', 'archive', 'all', 'other')
ROLE_LABEL = {'inbox': 'Inbox', 'sent': 'Sent', 'drafts': 'Drafts', 'junk': 'Spam',
              'trash': 'Trash', 'archive': 'Archive', 'all': 'All', 'other': ''}
ROLE_ICON = {'inbox': '\u2709', 'sent': '\u2197', 'drafts': '\u270e', 'junk': '!',
             'trash': '\u2717', 'archive': '\u25a4', 'all': '\u2261', 'other': '\u00b7'}

_NAME_ROLES = (
    ('sent', ('sent', 'gesendet', 'ausgang', 'outbox')),
    ('drafts', ('draft', 'entwurf', 'entw')),
    ('trash', ('trash', 'deleted', 'geloescht', 'gelöscht', 'papierkorb')),
    ('junk', ('junk', 'spam', 'bulk')),
    ('archive', ('archive', 'archiv')),
    ('all', ('all mail', 'alle nachrichten')),
)


# ------------------------------------------------------------------ utf-7 (IMAP)
_UTF7_SAFE = set(range(0x20, 0x7f)) - {ord('&')}


def utf7_encode(s):
    """Encode a folder name for IMAP (modified UTF-7, RFC 3501 5.1.3)."""
    out = []
    buf = []

    def flush():
        if buf:
            raw = ''.join(buf).encode('utf-16-be')
            b = base64.b64encode(raw).decode('ascii').rstrip('=').replace('/', ',')
            out.append('&' + b + '-')
            buf.clear()

    for ch in s:
        o = ord(ch)
        if o in _UTF7_SAFE:
            flush()
            out.append(ch)
        elif ch == '&':
            flush()
            out.append('&-')
        else:
            buf.append(ch)
    flush()
    return ''.join(out)


def utf7_decode(s):
    """Decode a folder name from IMAP; undecodable parts are kept verbatim."""
    out = []
    i = 0
    while i < len(s):
        if s[i] != '&':
            out.append(s[i])
            i += 1
            continue
        j = s.find('-', i)
        if j < 0:
            out.append(s[i:])
            break
        chunk = s[i + 1:j]
        if not chunk:
            out.append('&')
        else:
            b = chunk.replace(',', '/')
            b += '=' * (-len(b) % 4)
            try:
                out.append(base64.b64decode(b).decode('utf-16-be'))
            except (ValueError, UnicodeDecodeError):
                out.append(s[i:j + 1])
        i = j + 1
    return ''.join(out)


# ------------------------------------------------------------------ folders
class Folder:
    __slots__ = ('name', 'delim', 'flags', 'role', 'label', 'depth', 'parent',
                 'total', 'unseen')

    def __init__(self, name, delim='.', flags=()):
        self.name = name          # raw IMAP name, used for SELECT
        self.delim = delim or '.'
        self.flags = tuple(flags)
        self.role = role_of(name, self.flags)
        self.label = utf7_decode(name)
        self.depth = 0
        self.parent = ''
        self.total = None
        self.unseen = None

    @property
    def leaf(self):
        parts = self.label.split(self.delim) if self.delim else [self.label]
        return parts[-1] if len(parts) > 1 else self.label

    def __repr__(self):
        return 'Folder(%r, %r)' % (self.name, self.role)


def role_of(name, flags=()):
    low = {f.lower() for f in flags}
    for role, flag in (('sent', '\\sent'), ('drafts', '\\drafts'), ('trash', '\\trash'),
                       ('junk', '\\junk'), ('archive', '\\archive'), ('all', '\\all')):
        if flag in low:
            return role
    if name.upper() == 'INBOX':
        return 'inbox'
    leaf = name.split('.')[-1].lower() if '.' in name else name.lower()
    for role, words in _NAME_ROLES:
        for w in words:
            if leaf == w or leaf.startswith(w):
                return role
    return 'other'


_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|"(?:[^"\\]|\\.)*")\s+(?P<name>.+)$')


def parse_list_line(line):
    """Parse one untagged LIST response into a Folder (or None)."""
    if isinstance(line, bytes):
        line = line.decode('utf-8', 'replace')
    m = _LIST_RE.match(line.strip())
    if not m:
        return None
    flags = [f for f in m.group('flags').split() if f]
    delim = m.group('delim')
    if delim == 'NIL':
        delim = '.'
    else:
        delim = delim[1:-1].replace('\\\\', '\\').replace('\\"', '"')
    name = m.group('name').strip()
    if name.startswith('"') and name.endswith('"') and len(name) > 1:
        name = name[1:-1].replace('\\\\', '\\').replace('\\"', '"')
    if name.startswith('{') or not name:
        return None
    # RFC 3501 servers hand out modified UTF-7 already; a non-compliant one that
    # sends raw UTF-8 is passed through unchanged (that is what its SELECT wants)
    return Folder(name, delim, flags)


def build_tree(folders):
    """Sort folders by role, link children to parents, return a flat display list."""
    folders = [f for f in folders if f is not None]
    seen = {}
    for f in folders:
        seen[f.name] = f
    for f in folders:
        parts = f.label.split(f.delim) if f.delim else [f.label]
        if len(parts) > 1:
            parent_label = f.delim.join(parts[:-1])
            for g in folders:
                if g.label == parent_label:
                    f.parent = g.name
                    f.depth = g.depth + 1
                    break
            else:
                f.depth = 0
        else:
            f.depth = 0
    roots = [f for f in folders if not f.parent]
    kids = {}
    for f in folders:
        if f.parent:
            kids.setdefault(f.parent, []).append(f)

    def walk(f, out, depth):
        if f in out:
            return
        f.depth = depth
        out.append(f)
        for k in sorted(kids.get(f.name, []), key=lambda x: x.label.lower()):
            walk(k, out, depth + 1)

    out = []
    for role in ROLE_ORDER:
        group = [f for f in roots if f.role == role]
        group.sort(key=lambda x: (x.role != 'inbox', x.label.lower()))
        for f in group:
            walk(f, out, 0)
    return out


# ------------------------------------------------------------------ accounts
def blank_account(email_addr='', provider='strato'):
    imap, iport, smtp, sport = PROVIDERS.get(provider, PROVIDERS['strato'])
    user = email_addr
    return {'name': email_addr, 'email': email_addr, 'provider': provider,
            'imap': imap, 'imap_port': iport, 'imap_ssl': True,
            'smtp': smtp, 'smtp_port': sport, 'smtp_ssl': True,
            'user': user, 'password': '', 'smtp_user': '', 'smtp_password': '',
            'sent': '', 'trash': '', 'drafts': ''}


def load_accounts():
    try:
        with open(MAIL_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if isinstance(data, list):
        data = {'accounts': data}
    accs = [a for a in data.get('accounts', []) if isinstance(a, dict) and a.get('email')]
    return {'accounts': accs, 'signature': data.get('signature', '')}


def save_accounts(data):
    ensure_dirs()
    tmp = MAIL_FILE + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, MAIL_FILE)
    try:
        os.chmod(MAIL_FILE, 0o600)
    except OSError:
        pass


def find_account(data, addr):
    for a in data['accounts']:
        if a['email'].lower() == (addr or '').lower():
            return a
    return data['accounts'][0] if data['accounts'] else None


# ------------------------------------------------------------------ flags
FLAG_LETTERS = {'\\seen': 'S', '\\flagged': 'F', '\\answered': 'A', '\\draft': 'D', '\\deleted': 'T'}


def flags_str(flags):
    return ''.join(sorted({FLAG_LETTERS.get(f.lower(), '') for f in flags} - {''}))


def parse_flags_str(s):
    rev = {v: k for k, v in FLAG_LETTERS.items()}
    return ' '.join(rev[c] for c in s or '' if c in rev)


# ------------------------------------------------------------------ session
class MailError(Exception):
    pass


class Session:
    """A logged-in IMAP connection plus SMTP sending for one account."""

    def __init__(self, account, timeout=25):
        self.acc = account
        self.timeout = timeout
        self._lock = threading.RLock()
        self.imap = None
        self.folder = None
        self.uidvalidity = None
        self.exists = 0
        self.capabilities = set()
        self.folders = []
        self.delim = '.'

    # ---------------------------------------------------------- connection
    def connect(self):
        self.close()
        acc = self.acc
        if not acc.get('imap'):
            raise MailError('no IMAP server configured')
        try:
            if acc.get('imap_ssl', True):
                self.imap = imaplib.IMAP4_SSL(acc['imap'], int(acc.get('imap_port') or 993),
                                              timeout=self.timeout)
            else:
                self.imap = imaplib.IMAP4(acc['imap'], int(acc.get('imap_port') or 143),
                                          timeout=self.timeout)
            self.imap.login(acc.get('user') or acc['email'], acc.get('password') or '')
        except imaplib.IMAP4.error as e:
            self.imap = None
            raise MailError(_imap_error(e))
        except OSError as e:
            self.imap = None
            raise MailError('cannot reach %s: %s' % (acc.get('imap'), e))
        self.capabilities = {c.upper() for c in (self.imap.capabilities or ())}
        self.folder = None
        return True

    def close(self):
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:
                pass
        self.imap = None
        self.folder = None

    def _retry(self, fn, *a, **kw):
        # imaplib is not thread safe and the UI runs several jobs at once;
        # one lock per connection keeps their commands from interleaving.
        with self._lock:
            if self.imap is None:
                self.connect()
            try:
                return fn(*a, **kw)
            except imaplib.IMAP4.abort:
                self.connect()
                return fn(*a, **kw)

    def _ok(self, typ, data, what):
        if typ != 'OK':
            msg = data[0] if data else typ
            if isinstance(msg, bytes):
                msg = msg.decode('utf-8', 'replace')
            raise MailError('%s: %s' % (what, msg))

    # ---------------------------------------------------------- folders
    def list_folders(self):
        def work():
            typ, data = self.imap.list()
            self._ok(typ, data, 'LIST')
            out = []
            for line in data or []:
                f = parse_list_line(line)
                if f:
                    out.append(f)
            return out
        folders = self._retry(work)
        if folders:
            self.delim = folders[0].delim
        self.folders = build_tree(folders)
        return self.folders

    def status(self, folder):
        """(messages, unseen) for one folder; cheap with LIST-STATUS / STATUS."""
        name = folder.name if isinstance(folder, Folder) else folder

        def work():
            typ, data = self.imap.status(_quote(name), '(MESSAGES UNSEEN)')
            if typ != 'OK':
                return None
            return _parse_status(data)
        try:
            return self._retry(work)
        except (MailError, imaplib.IMAP4.error, OSError):
            return None

    def select(self, folder, readonly=False):
        name = folder.name if isinstance(folder, Folder) else folder
        if self.folder == name and self.imap is not None:
            return self.exists

        def work():
            typ, data = self.imap.select(_quote(name), readonly)
            self._ok(typ, data, 'SELECT')
            try:
                exists = int(data[0]) if data and data[0] else 0
            except (TypeError, ValueError):
                exists = 0
            uv = None
            try:
                # response() returns (name, [value]); the value is the text
                # behind the [UIDVALIDITY n] code of the untagged OK.
                vals = (self.imap.response('UIDVALIDITY') or (None, []))[1] or []
                vals = [v for v in vals if v]
                if vals:
                    uv = vals[-1].decode('ascii', 'replace') if isinstance(vals[-1], bytes) \
                        else str(vals[-1])
            except (imaplib.IMAP4.error, OSError):
                uv = None
            return exists, uv
        exists, uv = self._retry(work)
        self.folder = name
        self.exists = exists
        self.uidvalidity = uv
        return exists

    # ---------------------------------------------------------- headers
    def fetch_headers(self, folder, count=40, before_seq=None):
        """Newest ``count`` headers (or the page just older than ``before_seq``)."""
        self.select(folder)
        n = self.exists
        if n <= 0:
            return []
        if before_seq:
            if int(before_seq) <= 1:
                return []
            end = min(n, int(before_seq) - 1)
        else:
            end = n
        start = max(1, end - count + 1)
        if end < 1:
            return []
        seq_range = '%d:%d' % (start, end)

        def work():
            typ, data = self.imap.fetch(seq_range, '(UID FLAGS RFC822.SIZE)')
            self._ok(typ, data, 'FETCH')
            seqs = []
            for item in data or []:
                if not isinstance(item, bytes):
                    continue
                sm = re.match(rb'\s*(\d+)\s+\(', item)
                um = re.search(rb'UID (\d+)', item)
                if not (sm and um):
                    continue
                fm = re.search(rb'FLAGS \(([^)]*)\)', item)
                zm = re.search(rb'RFC822\.SIZE (\d+)', item)
                seqs.append((int(sm.group(1)), um.group(1).decode(),
                             fm.group(1).decode() if fm else '',
                             int(zm.group(1)) if zm else 0))
            if not seqs:
                return []
            uids = b','.join(s[1].encode() for s in seqs)
            typ, data = self.imap.uid('FETCH', uids,
                                      '(UID BODY.PEEK[HEADER.FIELDS (%s)])' % HEADER_FIELDS)
            self._ok(typ, data, 'FETCH headers')
            heads = {}
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                m = re.search(rb'UID (\d+)', meta)
                if m:
                    heads[m.group(1).decode()] = raw
            out = []
            for seq, uid, flags, size in seqs:
                h = header_from(raw=heads.get(uid, b''), uid=uid, flags=flags, size=size, seq=seq)
                out.append(h)
            out.sort(key=lambda h: h['seq'])
            return out
        return self._retry(work)

    def search(self, folder, query, count=50):
        self.select(folder)
        q = _quote_search(query)

        def work():
            # A cold full-text search on a big mailbox can take a minute or two;
            # imaplib would abort the connection at the normal socket timeout.
            sock = getattr(self.imap, 'sock', None)
            old = None
            if sock is not None:
                try:
                    old = sock.gettimeout()
                    sock.settimeout(max(240.0, float(old or 0)))
                except (OSError, ValueError):
                    old = None
            try:
                typ, data = self.imap.uid('SEARCH', 'CHARSET', 'UTF-8', 'TEXT', q)
                if typ != 'OK':
                    typ, data = self.imap.uid('SEARCH', 'TEXT', q)
            finally:
                if old is not None:
                    try:
                        sock.settimeout(old)
                    except OSError:
                        pass
            self._ok(typ, data, 'SEARCH')
            uids = (data[0] or b'').split()[-count:]
            if not uids:
                return []
            typ, data = self.imap.uid('FETCH', b','.join(uids),
                                      '(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (%s)])'
                                      % HEADER_FIELDS)
            self._ok(typ, data, 'FETCH')
            heads = []
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                m = re.search(rb'UID (\d+)', meta)
                sz = re.search(rb'RFC822\.SIZE (\d+)', meta)
                fl = re.search(rb'FLAGS \(([^)]*)\)', meta)
                if m:
                    heads.append(header_from(raw, m.group(1).decode(),
                                             (fl.group(1).decode() if fl else ''),
                                             int(sz.group(1)) if sz else 0, 0))
            heads.sort(key=lambda h: h['uid'])
            return heads
        return self._retry(work)

    # ---------------------------------------------------------- bodies
    def fetch_message(self, folder, uid):
        self.select(folder)

        def work():
            typ, data = self.imap.uid('FETCH', str(uid), '(BODY.PEEK[])')
            self._ok(typ, data, 'FETCH body')
            for item in data or []:
                if isinstance(item, tuple):
                    return parse_message(item[1])
            raise MailError('message %s is gone' % uid)
        return self._retry(work)

    def set_flags(self, folder, uids, add=(), remove=()):
        self.select(folder)
        uids = [uids] if isinstance(uids, (str, int)) else list(uids)
        if not uids:
            return
        uset = ','.join(str(u) for u in uids)
        if add:
            work_add = lambda: self.imap.uid('STORE', uset, '+FLAGS', '(%s)' % ' '.join(add))
            self._retry(work_add)
        if remove:
            work_del = lambda: self.imap.uid('STORE', uset, '-FLAGS', '(%s)' % ' '.join(remove))
            self._retry(work_del)

    def move(self, folder, uids, dest):
        self.select(folder)
        uids = [uids] if isinstance(uids, (str, int)) else list(uids)
        uset = ','.join(str(u) for u in uids)
        if 'MOVE' in self.capabilities:
            typ, data = self._retry(lambda: self.imap.uid('MOVE', uset, _quote(dest)))
            self._ok(typ, data, 'MOVE')
            self.folder = None
            return
        self._retry(lambda: self.imap.uid('COPY', uset, _quote(dest)))
        self._retry(lambda: self.imap.uid('STORE', uset, '+FLAGS', '(\\Deleted)'))
        self._retry(lambda: self.imap.expunge())
        self.folder = None

    def delete(self, folder, uids, trash=None):
        uids = [uids] if isinstance(uids, (str, int)) else list(uids)
        if trash and trash != folder:
            self.move(folder, uids, trash)
            return 'trash'
        self.select(folder)
        uset = ','.join(str(u) for u in uids)
        self._retry(lambda: self.imap.uid('STORE', uset, '+FLAGS', '(\\Deleted)'))
        self._retry(lambda: self.imap.expunge())
        self.folder = None
        return 'deleted'

    def append(self, folder, raw, flags='(\\Draft)'):
        name = folder.name if isinstance(folder, Folder) else folder

        raw = crlf(raw)

        def work():
            typ, data = self.imap.append(_quote(name), flags,
                                         time.time(), raw)
            self._ok(typ, data, 'APPEND')
            for item in data or []:
                if isinstance(item, bytes):
                    m = re.search(rb'APPENDUID \d+ (\d+)', item)
                    if m:
                        return m.group(1).decode('ascii', 'replace')
            return None
        return self._retry(work)

    def expunge(self):
        def work():
            typ, data = self.imap.expunge()
            self._ok(typ, data, 'EXPUNGE')
            self.folder = None
        self._retry(work)

    # ---------------------------------------------------------- sending
    def send(self, raw):
        acc = self.acc
        host = acc.get('smtp') or ''
        port = int(acc.get('smtp_port') or 465)
        user = acc.get('smtp_user') or acc.get('user') or acc['email']
        pw = acc.get('smtp_password') or acc.get('password') or ''
        try:
            if not acc.get('smtp_ssl', True):
                s = smtplib.SMTP(host, port, timeout=self.timeout)
            elif port == 587:
                s = smtplib.SMTP(host, port, timeout=self.timeout)
                s.starttls()
            else:
                s = smtplib.SMTP_SSL(host, port, timeout=self.timeout)
            try:
                s.login(user, pw)
                s.sendmail(acc['email'], _rcpt_list(raw), crlf(_strip_bcc(raw)))
            finally:
                try:
                    s.quit()
                except Exception:
                    pass
        except smtplib.SMTPAuthenticationError as e:
            raise MailError('SMTP login failed: %s' % (e.smtp_code,))
        except (smtplib.SMTPException, OSError) as e:
            raise MailError('send failed: %s' % e)


def _imap_error(e):
    s = str(e)
    low = s.lower()
    if 'authenticationfailed' in low or 'invalid credentials' in low or 'login failed' in low:
        return 'login failed - check address and password'
    return 'IMAP error: %s' % s


def _quote(name):
    """Quote an IMAP mailbox name (always, so spaces and dots need no special care)."""
    return '"%s"' % name.replace('\\', '\\\\').replace('"', '\\"')


def _quote_search(q):
    return '"%s"' % q.replace('\\', '\\\\').replace('"', '\\"')


def _parse_status(data):
    for item in data or []:
        if not isinstance(item, bytes):
            continue
        m = re.search(rb'MESSAGES (\d+).*?UNSEEN (\d+)', item, re.S)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def _strip_bcc(raw):
    """Drop the Bcc header before the message goes to SMTP (envelope keeps it)."""
    try:
        msg = BytesParser(policy=email.policy.default).parsebytes(raw)
    except Exception:
        return raw
    if msg.get('Bcc') is None:
        return raw
    del msg['Bcc']
    return msg.as_bytes()


def _rcpt_list(raw):
    msg = BytesParser(policy=email.policy.default).parsebytes(raw)
    addrs = []
    for field in ('To', 'Cc', 'Bcc'):
        addrs.extend(a for _, a in _addr_list(' '.join(str(h) for h in msg.get_all(field) or [])) if a)
    return addrs


# ------------------------------------------------------------------ headers
_HDR = BytesHeaderParser(policy=email.policy.default)


def header_from(raw, uid='', flags='', size=0, seq=0):
    try:
        msg = _HDR.parsebytes(raw or b'')
    except Exception:
        msg = {}
    to = ', '.join(_display(n, a) for n, a in _addr_list(msg.get('To')))
    cc = ', '.join(_display(n, a) for n, a in _addr_list(msg.get('Cc')))
    refs = str(msg.get('References') or '')
    return {
        'uid': str(uid),
        'seq': int(seq or 0),
        'flags': flags_str(flags if isinstance(flags, (list, tuple)) else flags.split()),
        'from': addr_name(msg.get('From')),
        'from_addr': addr_addr(msg.get('From')),
        'to': to,
        'cc': cc,
        'subject': _clean_hdr(msg.get('Subject')) or '(no subject)',
        'date': iso_date(msg.get('Date')),
        'size': int(size or 0),
        'msgid': str(msg.get('Message-ID') or '').strip(),
        'irt': str(msg.get('In-Reply-To') or '').strip(),
        'refs': refs.strip(),
        'ctype': str(msg.get('Content-Type') or ''),
        'reply_to': addr_addr(msg.get('Reply-To')),
        'list': str(msg.get('List-Id') or '').strip(),
    }


def _addr_list(value):
    if not value:
        return []
    try:
        return [(n, a) for n, a in email.utils.getaddresses([str(value)]) if a or n]
    except Exception:
        return []


def _display(name, addr):
    name = _clean_hdr(name).strip('"')
    if name and addr:
        return '%s <%s>' % (name, addr)
    return addr or name


def addr_name(value):
    pairs = _addr_list(value)
    if not pairs:
        return _clean_hdr(value) if value else ''
    return ', '.join(_display(n, a) for n, a in pairs)


def addr_addr(value):
    pairs = _addr_list(value)
    return pairs[0][1] if pairs else ''


def short_name(value):
    """'Max Mustermann <m@x.de>' -> 'Max Mustermann' (falls back to the address)."""
    pairs = _addr_list(value)
    if not pairs:
        return _clean_hdr(value) if value else ''
    name, addr = pairs[0]
    return _clean_hdr(name).strip('"') or addr


def _clean_hdr(v):
    if v is None:
        return ''
    s = str(v)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def iso_date(v):
    if not v:
        return ''
    try:
        if getattr(v, 'datetime', None):
            d = v.datetime
        else:
            d = email.utils.parsedate_to_datetime(str(v))
    except (TypeError, ValueError, IndexError):
        return ''
    if d is None:
        return ''
    if d.tzinfo:
        d = d.astimezone()
    return d.replace(tzinfo=None).isoformat(timespec='seconds')


def parse_dt(s):
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def fmt_date(s, now=None):
    """Compact list date: '14:03' today, '12 Mar' this year, '12.03.2020' before."""
    d = parse_dt(s)
    if not d:
        return ''
    now = now or dt.datetime.now()
    if d.date() == now.date():
        return d.strftime('%H:%M')
    if d.year == now.year:
        return d.strftime('%d %b')
    return d.strftime('%d.%m.%Y')


def fmt_date_long(s):
    d = parse_dt(s)
    if not d:
        return '(unknown date)'
    return d.strftime('%a, %d %b %Y  %H:%M')


def fmt_size(n):
    n = int(n or 0)
    if n >= 1024 * 1024:
        return '%.1f MB' % (n / 1048576.0)
    if n >= 1024:
        return '%.0f KB' % (n / 1024.0)
    return '%d B' % n


# ------------------------------------------------------------------ messages
class _TextExtract(HTMLParser):
    """HTML -> readable plain text: blocks, lists, links, tables, <pre>."""

    BLOCKS = {'p', 'div', 'section', 'article', 'header', 'footer', 'table', 'tr',
              'blockquote', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'form',
              'fieldset', 'figure', 'figcaption', 'dl', 'dd', 'dt', 'address', 'hr'}
    SKIP = {'script', 'style', 'head', 'title', 'noscript', 'template', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.pre = 0
        self.href = None
        self.link_txt = 0
        self.list_depth = 0

    # -- helpers
    def _nl(self, n=1):
        while self.out and self.out[-1] == ' ':
            self.out.pop()
        if not self.out:
            return
        newlines = 0
        for ch in reversed(self.out):
            if ch == '\n':
                newlines += 1
            else:
                break
        while newlines < n:
            self.out.append('\n')
            newlines += 1

    def _text(self):
        return ''.join(self.out)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == 'br':
            self.out.append('\n')
        elif tag == 'pre':
            self.pre += 1
            self._nl(1)
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._nl(2)
        elif tag in self.BLOCKS:
            self._nl(2 if tag in ('p', 'blockquote', 'table', 'hr', 'div') else 1)
        elif tag in ('ul', 'ol'):
            self.list_depth += 1
            self._nl(1)
        elif tag == 'li':
            self._nl(1)
            self.out.append('  ' * self.list_depth + '- ')
        elif tag == 'td' or tag == 'th':
            if self.out and self.out[-1] not in ('\n', ' '):
                self.out.append('  ')
        elif tag == 'a':
            self.href = (a.get('href') or '').strip()
            self.link_txt = len(self.out)
        elif tag == 'img':
            alt = (a.get('alt') or '').strip()
            if alt:
                self.out.append('[%s]' % alt)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'pre':
            self.pre = max(0, self.pre - 1)
            self._nl(1)
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._nl(2)
        elif tag in ('ul', 'ol'):
            self.list_depth = max(0, self.list_depth - 1)
            self._nl(1)
        elif tag in self.BLOCKS:
            self._nl(2 if tag in ('p', 'blockquote', 'table', 'hr', 'div') else 1)
        elif tag == 'a' and self.href:
            seen = ''.join(self.out[self.link_txt:]).strip()
            href = self.href
            if href.lower().startswith(('http://', 'https://', 'mailto:')):
                href_cmp = href[7:] if href.lower().startswith('mailto:') else href
                if not seen or (href_cmp not in seen and seen not in href_cmp):
                    if seen:
                        self.out.append(' ')
                    self.out.append('<%s>' % href)
            self.href = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self.out.append(data)
            return
        text = re.sub(r'[ \t\r\n]+', ' ', data)
        if not text.strip():
            if self.out and self.out[-1] not in (' ', '\n'):
                self.out.append(' ')
            return
        if self.out and self.out[-1] == ' ' and text.startswith(' '):
            text = text.lstrip(' ')
        self.out.append(text)

    def text(self):
        raw = self._text().replace('\xa0', ' ')
        raw = re.sub(r'[ \t]+\n', '\n', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def strip_html(src):
    p = _TextExtract()
    try:
        p.feed(src)
        p.close()
    except Exception:
        return re.sub(r'<[^>]+>', ' ', src)
    return p.text()


def parse_message(raw):
    """Full message -> dict with text, html, attachments and headers."""
    msg = BytesParser(policy=email.policy.default).parsebytes(raw or b'')
    text_parts, html_parts, attachments = [], [], []
    plain_size = html_size = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get('Content-Disposition') or '').lower()
        fname = part.get_filename()
        payload = part.get_payload(decode=True) or b''
        if fname or disp.startswith('attachment'):
            attachments.append({'name': fname or '(unnamed)',
                                'ctype': ctype, 'size': len(payload), 'data': payload})
            continue
        if ctype == 'text/plain':
            text_parts.append(decode_text(payload, part.get_content_charset()))
            plain_size += len(payload)
        elif ctype == 'text/html':
            html_parts.append(decode_text(payload, part.get_content_charset()))
            html_size += len(payload)
        elif ctype.startswith('image/'):
            attachments.append({'name': fname or 'image', 'ctype': ctype,
                                'size': len(payload), 'data': payload})
    html = '\n\n'.join(html_parts)
    text = '\n\n'.join(text_parts).strip()
    if not text and html:
        text = strip_html(html)
    hdr = header_from(raw)
    hdr['to_list'] = [addr for _, addr in _addr_list(msg.get('To'))]
    hdr['text'] = text
    hdr['html'] = html
    hdr['attachments'] = attachments
    return hdr


def decode_text(payload, charset=None):
    if not payload:
        return ''
    enc = (charset or 'utf-8').lower().strip()
    if enc in ('unknown-8bit', 'x-unknown', 'us-ascii', ''):
        enc = 'utf-8'
    for e in (enc, 'utf-8', 'latin-1'):
        try:
            return payload.decode(e, 'strict')
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode('utf-8', 'replace')


# ------------------------------------------------------------------ composing
def reply_subject(subject):
    s = subject or ''
    if re.match(r'^\s*(re|aw|antw)\s*(\[\d+\])?\s*:', s, re.I):
        return s
    return 'Re: ' + s


def forward_subject(subject):
    s = subject or ''
    if re.match(r'^\s*(fwd?|wg)\s*:', s, re.I):
        return s
    return 'Fwd: ' + s


def quote(text, prefix='> '):
    lines = (text or '').rstrip().split('\n')
    return '\n'.join((prefix + ln) if ln.strip() else prefix.rstrip() for ln in lines)


def reply_text(msg, own_addr='', signature=''):
    """Quoted original with an attribution line, ready to be edited."""
    when = fmt_date_long(msg.get('date'))
    who = short_name(msg.get('from'))
    body = quote((msg.get('text') or '').strip())
    head = 'Am %s schrieb %s:' % (when, who)
    parts = []
    if signature:
        parts.append(signature.rstrip())
    parts.append('')
    parts.append(head)
    parts.append(body)
    return '\n'.join(parts)


def build_message(acc, to, subject, body, cc='', bcc='', in_reply_to='', references='',
                  html=None, attach=()):
    msg = EmailMessage()
    msg['From'] = '%s <%s>' % (acc.get('name') or acc['email'], acc['email'])
    msg['To'] = to
    if cc:
        msg['Cc'] = cc
    if bcc:
        msg['Bcc'] = bcc
    msg['Subject'] = subject or '(no subject)'
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain=acc['email'].split('@')[-1] or 'monocell')
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        refs = ('%s %s' % (references, in_reply_to)).strip() if references else in_reply_to
        msg['References'] = refs
    msg.set_content(body or '')
    if html:
        msg.add_alternative(html, subtype='html')
    for name, ctype, data in attach:
        maintype, _, subtype = (ctype or 'application/octet-stream').partition('/')
        msg.add_attachment(data, maintype=maintype or 'application', subtype=subtype or 'octet-stream',
                           filename=name)
    return msg.as_bytes()


def crlf(raw):
    """Line endings for the wire. as_bytes() emits bare LF, and strict servers
    (STRATO does) reject a header block that is not terminated by CRLF."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return re.sub(rb'\r\n|\r|\n', b'\r\n', raw)


def parse_addresses(value):
    """Validate a recipient field; returns (ok, list_of_bad_entries)."""
    bad = []
    for name, addr in email.utils.getaddresses([value or '']):
        if not addr:
            if name.strip():
                bad.append(name.strip())
            continue
        if '@' not in addr or addr.startswith('@') or addr.endswith('@') or ' ' in addr:
            bad.append(addr)
    return (not bad and bool((value or '').strip())), bad


# ------------------------------------------------------------------ cache
def _cache_dir(acc):
    d = os.path.join(CACHE_DIR, 'mail', re.sub(r'[^a-z0-9@._-]+', '_', acc.get('email', '').lower()))
    return d


def _safe(name):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', name)[:80]


def load_cache(acc, folder):
    path = os.path.join(_cache_dir(acc), _safe(folder) + '.json')
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_cache(acc, folder, data):
    d = _cache_dir(acc)
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, _safe(folder) + '.json')
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def clear_cache(acc, folder=None):
    d = _cache_dir(acc)
    try:
        if folder:
            os.unlink(os.path.join(d, _safe(folder) + '.json'))
        else:
            for n in os.listdir(d):
                os.unlink(os.path.join(d, n))
    except OSError:
        pass
