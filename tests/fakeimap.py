"""A tiny in-process IMAP4rev1 server for tests.

Only what :class:`monocell.mail.Session` sends is implemented - no TLS, no
partial fetch, no ACLs.  It exists so the wire protocol (sequence and UID
sets, literals, quoted mailbox names, MOVE/COPYUID, flag updates, paging)
can be exercised without a real mail account.
"""
import re
import socket
import threading


class Msg:
    def __init__(self, raw, flags=(), uid=None):
        if not isinstance(raw, bytes):
            raw = raw.encode()
        # fixtures are usually written with plain \n, the wire uses CRLF
        self.raw = raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.flags = set(flags)
        self.uid = uid

    def header(self, fields=None):
        head = self.raw.partition(b'\r\n\r\n')[0]
        if not fields:
            return head + b'\r\n\r\n'
        want = {f.encode().upper() if isinstance(f, str) else f.upper() for f in fields}
        out = []
        for line in re.split(rb'\r\n(?![ \t])', head):
            name = line.split(b':', 1)[0].strip().upper()
            if name in want:
                out.append(line)
        return b'\r\n'.join(out) + b'\r\n\r\n'


# ------------------------------------------------------------------ parsing
def _tokens(s):
    """Split IMAP arguments, honouring quoted strings. Quotes are dropped."""
    out, cur, quote, i = [], '', False, 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == '\\' and i + 1 < len(s):
                cur += s[i + 1]
                i += 2
                continue
            if c == '"':
                quote = False
                out.append(cur)
                cur = ''
            else:
                cur += c
        elif c == '"':
            quote = True
        elif c == ' ':
            if cur:
                out.append(cur)
                cur = ''
        else:
            cur += c
        i += 1
    if cur:
        out.append(cur)
    return out


def _take(s):
    """First argument (quote aware) and the rest of the line, stripped."""
    s = s.lstrip()
    if s.startswith('"'):
        i, buf = 1, ''
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                buf += s[i + 1]
                i += 2
            elif s[i] == '"':
                return buf, s[i + 1:].strip()
            else:
                buf += s[i]
                i += 1
        return buf, ''
    i = s.find(' ')
    if i < 0:
        return s, ''
    return s[:i], s[i + 1:].strip()


def _seqset(spec, n):
    out = []
    for part in spec.split(','):
        if ':' in part:
            a, b = part.split(':', 1)
            a = n if a == '*' else int(a)
            b = n if b == '*' else int(b)
            if a > b:
                a, b = b, a
            out.extend(range(a, min(b, n) + 1))
        elif part == '*':
            out.append(n)
        elif part.strip():
            out.append(int(part))
    return out


def _items(spec):
    """'X (A B (C D))' -> ['A', 'B', 'C D']."""
    spec = spec.strip()
    if spec.startswith('(') and spec.endswith(')'):
        spec = spec[1:-1]
    out, depth, cur = [], 0, ''
    for ch in spec:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        if ch == ' ' and depth == 0:
            if cur:
                out.append(cur)
            cur = ''
        else:
            cur += ch
    if cur:
        out.append(cur)
    return [i.upper() for i in out]


class FakeIMAP:
    def __init__(self, boxes=None, user='test@example.org', password='secret'):
        self.boxes = {}
        self.uidnext = 0
        for name, msgs in (boxes or {}).items():
            self.boxes[name] = []
            for m in msgs:
                m = m if isinstance(m, Msg) else Msg(m)
                if not m.uid:
                    m.uid = self._new_uid()
                self.boxes[name].append(m)
        self.uidnext += 100
        self.flags = {'\\Seen', '\\Answered', '\\Flagged', '\\Deleted', '\\Draft'}
        self.user = user
        self.password = password
        self.uidvalidity = 7
        self.log = []
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._accept, daemon=True)

    # ---------------------------------------------------------------- life
    def start(self):
        self.thread.start()
        return self.port

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()

    def append(self, name, raw, flags=()):
        """Server-side helper: put a message into a mailbox."""
        m = Msg(raw, set(flags), self._new_uid())
        self.boxes.setdefault(name, []).append(m)
        return m.uid

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _new_uid(self):
        self.uidnext += 1
        return self.uidnext

    def _children(self, name):
        pre = name + '.'
        return [b for b in self.boxes if b.startswith(pre) and '.' not in b[len(pre):]]

    # ---------------------------------------------------------------- conn
    def _serve(self, conn):
        rf = conn.makefile('rb')
        st = {'box': None, 'readonly': False}

        def send(b):
            if isinstance(b, str):
                b = b.encode()
            conn.sendall(b)

        send('* OK fakeimap ready\r\n')
        while True:
            line = rf.readline()
            if not line:
                break
            text = line.decode('utf-8', 'replace').rstrip('\r\n')
            lit = b''
            while True:
                m = re.search(r'\{(\d+)\}$', text)
                if not m:
                    break
                send('+ go ahead\r\n')
                lit = rf.read(int(m.group(1)))
                rest = rf.readline().decode('utf-8', 'replace').rstrip('\r\n')
                text = text[:m.start()] + ' ' + rest
            if not text.strip():
                continue
            with self.lock:
                self.log.append(text)
            tag, rest = _take(text)
            cmd, arg = _take(rest)
            cmd = cmd.upper()
            try:
                r = self._cmd(cmd, arg, st, lit, send)
            except Exception as e:                       # noqa: BLE001
                send('%s NO %s\r\n' % (tag, str(e).replace('\n', ' ')))
                continue
            if r == 'bye':
                send('%s OK LOGOUT completed\r\n' % tag)
                break
            if not r.startswith('OK') and not r.startswith('NO') and not r.startswith('BAD'):
                r = 'OK %s completed' % cmd
            send('%s %s\r\n' % (tag, r))
        try:
            conn.close()
        except OSError:
            pass

    # ---------------------------------------------------------------- cmds
    def _cmd(self, cmd, arg, st, lit, send):
        if cmd == 'CAPABILITY':
            return 'OK [CAPABILITY IMAP4rev1 UIDPLUS MOVE SPECIAL-USE ' \
                   'LIST-STATUS SORT SEARCH] done'
        if cmd == 'NOOP':
            return 'OK done'
        if cmd in ('CHECK', 'UNSELECT'):
            return 'OK done'
        if cmd == 'LOGOUT':
            send('* BYE bye\r\n')
            return 'bye'
        if cmd == 'LOGIN':
            user, pw = _tokens(arg)
            if user != self.user or pw != self.password:
                return 'NO [AUTHENTICATIONFAILED] invalid credentials'
            return 'OK LOGIN completed'
        if cmd == 'LIST':
            args = _tokens(arg)
            pat = args[1] if len(args) > 1 else '*'
            rx = re.escape(pat).replace(r'\*', '.*').replace(r'%', '[^.]*')
            for name in sorted(self.boxes):
                if not re.fullmatch(rx, name):
                    continue
                flags = '\\HasChildren' if self._children(name) else '\\HasNoChildren'
                low = name.lower()
                if low == 'inbox':
                    flags += ' \\Inbox'
                elif 'sent' in low:
                    flags += ' \\Sent'
                elif 'draft' in low:
                    flags += ' \\Drafts'
                elif 'trash' in low:
                    flags += ' \\Trash'
                elif 'spam' in low or 'junk' in low:
                    flags += ' \\Junk'
                send('* LIST (%s) "." "%s"\r\n' % (flags, name.replace('"', '\\"')))
            return 'OK LIST completed'
        if cmd == 'STATUS':
            name = _take(arg)[0]
            if name not in self.boxes:
                return 'NO [NONEXISTENT] no such mailbox'
            items = self.boxes[name]
            unseen = sum(1 for m in items if '\\Seen' not in m.flags)
            send('* STATUS "%s" (MESSAGES %d UNSEEN %d RECENT 0 UIDNEXT %d)\r\n'
                 % (name, len(items), unseen, self.uidnext))
            return 'OK STATUS completed'
        if cmd in ('SELECT', 'EXAMINE'):
            name = _take(arg)[0]
            if name not in self.boxes:
                return 'NO [NONEXISTENT] no such mailbox'
            st['box'] = name
            st['readonly'] = cmd == 'EXAMINE'
            box = self.boxes[name]
            send('* FLAGS (%s)\r\n' % ' '.join(sorted(self.flags)))
            send('* %d EXISTS\r\n' % len(box))
            send('* 0 RECENT\r\n')
            send('* OK [UIDVALIDITY %d] UIDs valid\r\n' % self.uidvalidity)
            send('* OK [UIDNEXT %d] next uid\r\n' % self.uidnext)
            send('* OK [PERMANENTFLAGS (%s \\*)] flags\r\n' % ' '.join(sorted(self.flags)))
            return 'OK [%s] done' % ('READ-ONLY' if st['readonly'] else 'READ-WRITE')
        if cmd == 'CLOSE':
            if st['box'] and not st['readonly']:
                box = self.boxes[st['box']]
                box[:] = [m for m in box if '\\Deleted' not in m.flags]
            st['box'] = None
            return 'OK CLOSE completed'
        if cmd == 'EXPUNGE':
            box = self._boxed(st)
            for i in range(len(box) - 1, -1, -1):
                if '\\Deleted' in box[i].flags:
                    del box[i]
                    send('* %d EXPUNGE\r\n' % (i + 1))
            send('* %d EXISTS\r\n' % len(box))
            return 'OK EXPUNGE completed'
        if cmd == 'APPEND':
            name = _take(arg)[0]
            if name not in self.boxes:
                return 'NO [TRYCREATE] no such mailbox'
            flags = set()
            fm = re.search(r'\(([^)]*)\)', arg)
            if fm:
                flags = {f for f in fm.group(1).split() if f}
            m = Msg(lit, flags, self._new_uid())
            self.boxes[name].append(m)
            send('* OK [APPENDUID %d %d] appended\r\n' % (self.uidvalidity, m.uid))
            return 'OK [APPENDUID %d %d] done' % (self.uidvalidity, m.uid)
        if cmd == 'UID':
            sub, rest = _take(arg)
            return self._uid(sub.upper(), rest, st, send, True)
        if cmd in ('FETCH', 'STORE', 'SEARCH', 'COPY', 'MOVE'):
            return self._uid(cmd, arg, st, send, False)
        return 'BAD unknown command %s' % cmd

    def _boxed(self, st):
        if not st['box']:
            raise ValueError('no mailbox selected')
        return self.boxes[st['box']]

    def _uid(self, sub, arg, st, send, uid):
        if sub == 'SEARCH':
            return self._search(arg, st, send, uid)
        spec, rest = _take(arg)
        if sub == 'FETCH':
            return self._fetch(uid, spec, rest, st, send)
        if sub == 'STORE':
            return self._store(uid, spec, rest, st, send)
        if sub in ('COPY', 'MOVE'):
            return self._copy(sub == 'MOVE', uid, spec, _take(rest)[0], st, send)
        return 'BAD unknown command %s' % sub

    def _targets(self, uid, spec, box):
        """[(seq, msg)] addressed by a UID or sequence set."""
        if uid:
            want = set(_seqset(spec, 10 ** 9))
            return [(i + 1, m) for i, m in enumerate(box) if m.uid in want]
        return [(i, box[i - 1]) for i in _seqset(spec, len(box)) if i <= len(box)]

    def _fetch(self, uid, spec, rest, st, send):
        items = _items(rest)
        box = self._boxed(st)
        for seq, m in self._targets(uid, spec, box):
            parts, literals = [], []
            for it in items:
                if it == 'UID':
                    parts.append('UID %d' % m.uid)
                elif it == 'FLAGS':
                    parts.append('FLAGS (%s)' % ' '.join(sorted(m.flags)))
                elif it == 'RFC822.SIZE':
                    parts.append('RFC822.SIZE %d' % len(m.raw))
                elif it == 'INTERNALDATE':
                    parts.append('INTERNALDATE "01-Jan-2026 10:00:00 +0000"')
                elif it.startswith('BODY[') or it.startswith('BODY.PEEK['):
                    inner = it[it.index('[') + 1:it.rindex(']')]
                    if it.startswith('BODY[') and not st['readonly']:
                        m.flags.add('\\Seen')
                    if inner.startswith('HEADER.FIELDS'):
                        names = inner[inner.index('(') + 1:inner.rindex(')')].split()
                        data = m.header(names)
                        inner = inner.replace('HEADER.FIELDS', 'HEADER.FIELDS')
                    else:
                        data = m.raw
                    parts.append('BODY[%s]' % inner)
                    literals.append((len(parts) - 1, data))
                else:
                    parts.append(it)
            buf = '* %d FETCH (' % seq
            for i, part in enumerate(parts):
                lit_data = [d for (pi, d) in literals if pi == i]
                if lit_data:
                    send(buf + part + ' {%d}\r\n' % len(lit_data[0]))
                    send(lit_data[0])
                    buf = ''
                else:
                    buf += part + (' ' if i < len(parts) - 1 else '')
            send(buf + ')\r\n')
        return 'OK FETCH completed'

    def _store(self, uid, spec, rest, st, send):
        mode, flags = _take(rest)
        mode = mode.upper()
        flags = flags.strip()
        if flags.startswith('(') and flags.endswith(')'):
            flags = flags[1:-1]
        flags = set(flags.split())
        box = self._boxed(st)
        if st['readonly']:
            return 'NO mailbox is read-only'
        for seq, m in self._targets(uid, spec, box):
            if mode == '+FLAGS':
                m.flags |= flags
            elif mode == '-FLAGS':
                m.flags -= flags
            elif mode == 'FLAGS':
                m.flags = set(flags)
            send('* %d FETCH (UID %d FLAGS (%s))\r\n'
                 % (seq, m.uid, ' '.join(sorted(m.flags))))
        return 'OK STORE completed'

    def _copy(self, move, uid, spec, dest, st, send):
        if dest not in self.boxes:
            return 'NO [TRYCREATE] no such mailbox'
        box = self._boxed(st)
        dst = self.boxes[dest]
        moved, mapping = [], []
        for seq, m in self._targets(uid, spec, box):
            new = Msg(m.raw, m.flags, self._new_uid())
            dst.append(new)
            mapping.append((m.uid, new.uid))
            if move:
                moved.append(seq)
        if mapping:
            send('* OK [COPYUID %d %s %s] copied\r\n'
                 % (self.uidvalidity,
                    ','.join(str(a) for a, _ in mapping),
                    ','.join(str(b) for _, b in mapping)))
        if move:
            for seq in sorted(moved, reverse=True):
                del box[seq - 1]
                send('* %d EXPUNGE\r\n' % seq)
            send('* %d EXISTS\r\n' % len(box))
        return 'OK %s completed' % ('MOVE' if move else 'COPY')

    def _search(self, arg, st, send, uid):
        args = _tokens(arg)
        box = self._boxed(st)
        if args and args[0].upper() == 'CHARSET':
            args = args[2:]
        key = args[0].upper() if args else 'ALL'
        val = args[1] if len(args) > 1 else ''
        hits = []
        for i, m in enumerate(box):
            if key == 'ALL':
                ok = True
            elif key == 'UNSEEN':
                ok = '\\Seen' not in m.flags
            elif key == 'SEEN':
                ok = '\\Seen' in m.flags
            elif key in ('TEXT', 'BODY'):
                ok = val.lower().encode() in m.raw.lower()
            elif key == 'SUBJECT':
                ok = val.lower().encode() in m.header(['Subject']).lower()
            elif key == 'FROM':
                ok = val.lower().encode() in m.header(['From']).lower()
            else:
                ok = False
            if ok:
                hits.append(m.uid if uid else i + 1)
        send('* SEARCH%s\r\n' % (''.join(' %d' % h for h in hits)))
        return 'OK SEARCH completed'
