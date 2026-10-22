"""Mail: IMAP/SMTP client with folder tree, message list and reader pane."""
import email.utils
import mimetypes
import os
import time

from .. import mail
from ..app import App
from ..util import clean, fit, sym, wrap_text
from ..widgets import ListView, MenuItem, SBar, TextField, button, sb_draw, sb_geom, scroll_from_click
from ..textarea import TextArea

ROLE_SYM = {'inbox': 'mail', 'sent': 'sent', 'drafts': 'draft', 'junk': 'junk',
            'trash': 'trash', 'archive': 'archive', 'all': 'menu', 'other': 'folder'}
PAGE = 40


class Mail(App):
    name = 'mail'
    title = 'Mail'
    icon = '[M]'
    size = (128, 34)
    min_size = (52, 14)
    single = True

    def __init__(self, wm, win, account=None, folder=None, **kw):
        super().__init__(wm, win)
        self.data = mail.load_accounts()
        self.acc = mail.find_account(self.data, account) if self.data['accounts'] else None
        self.session = None
        self.folders = []
        self.folder = None
        self.headers = []
        self.sel = 0
        self.ltop = 0
        self.body = None
        self.body_uid = None
        self.bodies = {}
        self.read_top = 0
        self.read_lines = []
        self.read_w = 1
        self.pane = 'list'
        self.status = ''
        self.busy = 0
        self.gen = 0
        self.q = ''
        self.help = False
        self.fac = ListView()
        self.fac.on_select = self._fac_select
        self.fac.on_activate = self._fac_open
        self._pending_folder = None
        self._pending_timer = None
        self._sel_timer = None
        self.connected = False
        if self.acc:
            self.open_account(self.acc, folder)
        self.every(60, self._tick)

    # ------------------------------------------------------------ accounts
    def open_account(self, acc, folder=None):
        self.close_session()
        self.acc = acc
        self.session = mail.Session(acc)
        self.folders = []
        self.folder = None
        self.headers = []
        self.fac.set_items([])
        self.body = None
        self.bodies = {}
        self.connected = False
        self.status = 'connecting to %s ...' % acc.get('imap', '')
        self.set_title('%s - %s' % (acc['email'], 'Mail'))
        self.load_folders(folder)

    def close_session(self):
        if self.session is not None:
            s = self.session
            self.session = None
            self.thread(s.close, None, None)

    def account_menu(self):
        items = []
        for a in self.data['accounts']:
            items.append(MenuItem(a['email'], lambda a=a: self.open_account(a), '',
                                  sym(ROLE_SYM['inbox']), checked=(self.acc is not None and
                                                                   a['email'] == self.acc['email'])))
        items.append(None)
        items.append(MenuItem('Add account...', self.add_account, '',
                              sym('mail')))
        if self.acc:
            items.append(MenuItem('Edit account...', lambda: self.edit_account(self.acc),
                                  '', sym('draft')))
        self.wm.menu(items, self.win.x + 2, self.win.y + 3)

    def add_account(self):
        self.wm.open_cls(AccountForm, modal_for=self.win, account=None,
                         on_save=self._account_saved, start=self.acc)

    def edit_account(self, acc):
        self.wm.open_cls(AccountForm, modal_for=self.win, account=dict(acc),
                         on_save=self._account_saved, start=self.acc)

    def _account_saved(self, acc, remove=False):
        accs = [a for a in self.data['accounts'] if a['email'].lower() != acc['email'].lower()]
        if not remove:
            accs.append(acc)
        self.data['accounts'] = accs
        mail.save_accounts(self.data)
        if remove and self.acc and self.acc['email'].lower() == acc['email'].lower():
            self.acc = None
            self.close_session()
            self.fac.set_items([])
            self.headers = []
            self.folder = None
            self.body = None
            if self.data['accounts']:
                self.open_account(self.data['accounts'][0])
        elif not remove:
            self.open_account(acc)
        self.invalidate()

    # ------------------------------------------------------------ folders
    def load_folders(self, want=None):
        if not self.session:
            return
        acc = self.acc
        sess = self.session

        def work():
            folders = sess.list_folders()
            for f in folders:
                st = sess.status(f)
                if st:
                    f.total, f.unseen = st
            return folders

        def done(folders):
            if self.session is not sess:
                return
            self.connected = True
            self.folders = folders
            self.fac.set_items(folders, keep=True)
            self.fac.sel = 0
            target = None
            if want:
                target = next((f for f in folders if f.name == want), None)
            if target is None:
                target = self._role_folder('inbox') or (folders[0] if folders else None)
            self.status = '%d folders' % len(folders)
            if target:
                self.open_folder(target)
        self.job(work, done, self._conn_error)

    def _conn_error(self, e):
        self.connected = False
        self.status = str(e)
        self.wm.toast('Mail: %s' % e, 'red')

    def _role_folder(self, role):
        for f in self.folders:
            if f.role == role:
                return f
        return None

    def _fac_select(self, f):
        if f is self.folder:
            return
        if self._pending_timer is not None:
            self._pending_timer.cancel()
        self._pending_folder = f
        self._pending_timer = self.later(0.12, self._open_pending)

    def _fac_open(self, f):
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None
        if f is not None:
            self.open_folder(f)

    def _open_pending(self):
        self._pending_timer = None
        f = self._pending_folder
        if f is not None and f is not self.folder:
            self.open_folder(f)

    def open_folder(self, f):
        self.folder = f
        self.q = ''
        self.gen += 1
        gen = self.gen
        self.headers = []
        self.sel = 0
        self.ltop = 0
        self.body = None
        self.body_uid = None
        self.read_top = 0
        cached = mail.load_cache(self.acc, f.name) if self.acc else None
        if cached and cached.get('headers'):
            self.headers = cached['headers']
            self.folder.total = cached.get('total', self.folder.total)
            self.status = 'cached - refreshing ...'
        if self.session is None:
            return
        sess = self.session

        def work():
            hs = sess.fetch_headers(f, PAGE)
            hs.reverse()
            return hs, sess.exists, sess.uidvalidity

        def done(res):
            if gen != self.gen:
                return
            hs, total, uv = res
            old = {h['uid']: h for h in self.headers}
            for h in hs:
                o = old.get(h['uid'])
                if o and o.get('flags') != h['flags']:
                    h['flags'] = o['flags']
            self.headers = hs
            self.folder.total = total
            self.status = '%d messages' % total
            self.sel = min(self.sel, max(0, len(hs) - 1))
            mail.save_cache(self.acc, f.name, {'uidvalidity': uv, 'headers': hs, 'total': total})
            if self.pane == 'folders':
                self.pane = 'list'
            self.invalidate()
            self.select_message(self.sel, open_body=False)
        self.job(work, done, lambda e: self._folder_error(e, gen))

    def _folder_error(self, e, gen):
        if gen == self.gen:
            self.status = str(e)
            self.wm.toast('Mail: %s' % e, 'red')

    def refresh(self):
        if not self.folder:
            self.load_folders()
            return
        f = self.folder
        gen = self.gen
        if not self.session:
            return
        sess = self.session

        def work():
            hs = sess.fetch_headers(f, PAGE)
            hs.reverse()
            return hs, sess.exists, sess.uidvalidity

        def done(res):
            if gen != self.gen:
                return
            hs, total, uv = res
            self.headers = hs
            self.folder.total = total
            self.status = '%d messages' % total
            self.sel = min(self.sel, max(0, len(hs) - 1))
            mail.save_cache(self.acc, f.name, {'uidvalidity': uv, 'headers': hs, 'total': total})
            self.invalidate()
            self.select_message(self.sel)
        self.job(work, done, self._conn_error)

    def refresh_counts(self):
        if not self.session or not self.folders:
            return
        sess, folders = self.session, self.folders
        gen = self.gen

        def work():
            out = []
            for f in folders:
                try:
                    out.append((f, sess.status(f)))
                except (mail.MailError, OSError):
                    out.append((f, None))
            return out

        def done(res):
            if gen != self.gen:
                return
            for f, st in res:
                if st:
                    f.total, f.unseen = st
            self.invalidate()
        self.job(work, done, lambda e: None)

    def older(self):
        if not self.headers or not self.folder or not self.session:
            return
        before = min(h['seq'] for h in self.headers if h.get('seq'))
        if not before or before <= 1:
            self.wm.toast('No older mail')
            return
        f = self.folder
        gen = self.gen
        sess = self.session

        def work():
            hs = sess.fetch_headers(f, PAGE, before_seq=before)
            hs.reverse()
            return hs

        def done(hs):
            if gen != self.gen or not hs:
                return
            have = {h['uid'] for h in self.headers}
            self.headers += [h for h in hs if h['uid'] not in have]
            self.status = '%d loaded' % len(self.headers)
            self.invalidate()
        self.job(work, done, self._conn_error)

    # ------------------------------------------------------------ messages
    def select_message(self, i, open_body=False):
        n = len(self.headers)
        if not n:
            self.body = None
            self.body_uid = None
            return
        self.sel = max(0, min(i, n - 1))
        h = self.headers[self.sel]
        self.read_top = 0
        uid = h['uid']
        if self.body_uid != uid:
            self.body = self.bodies.get(uid) or None
            self.body_uid = uid
        if open_body and self.body is None:
            self.fetch_body(h)
        if open_body and 'S' not in h.get('flags', ''):
            h['flags'] = ''.join(sorted(set(h.get('flags', '') + 'S')))
            self.mark_seen_async(h['uid'])
        self.invalidate()

    def fetch_body(self, h):
        if not self.session or not self.folder:
            return
        f = self.folder
        uid = h['uid']
        gen = self.gen
        sess = self.session

        def work():
            return sess.fetch_message(f, uid)

        def done(m):
            self.bodies[uid] = m
            if len(self.bodies) > 30:
                for k in list(self.bodies)[:10]:
                    if k != uid:
                        self.bodies.pop(k, None)
            if uid == self.body_uid:
                self.body = m
                self.read_top = 0
            self.invalidate()
        self.job(work, done, lambda e: self._body_error(e))

    def _body_error(self, e):
        self.body = None
        self.status = str(e)
        self.wm.toast('Mail: %s' % e, 'red')

    def mark_seen_async(self, uid):
        if not self.session or not self.folder:
            return
        f = self.folder
        sess = self.session
        self.job(lambda: sess.set_flags(f, uid, add=['\\Seen']), None, lambda e: None)

    def toggle_unread(self):
        h = self.current()
        if not h or not self.session:
            return
        seen = 'S' in h.get('flags', '')
        add, rem = (['\\Seen'], []) if not seen else ([], ['\\Seen'])
        h['flags'] = (''.join(c for c in h.get('flags', '') if c != 'S')) + ('S' if not seen else '')
        f = self.folder
        sess = self.session
        self.job(lambda: sess.set_flags(f, h['uid'], add=add, remove=rem), None,
                 lambda e: self.wm.toast(str(e), 'red'))
        self.invalidate()

    def toggle_flag(self):
        h = self.current()
        if not h or not self.session:
            return
        on = 'F' in h.get('flags', '')
        add, rem = ([], ['\\Flagged']) if on else (['\\Flagged'], [])
        h['flags'] = (''.join(c for c in h.get('flags', '') if c != 'F')) + ('' if on else 'F')
        f = self.folder
        sess = self.session
        self.job(lambda: sess.set_flags(f, h['uid'], add=add, remove=rem), None,
                 lambda e: self.wm.toast(str(e), 'red'))
        self.invalidate()

    def delete(self):
        h = self.current()
        if not h or not self.session or not self.folder:
            return
        trash = self._role_folder('trash')
        in_trash = self.folder.role == 'trash'

        def go():
            f, sess = self.folder, self.session
            self.job(lambda: sess.delete(f, [h['uid']], None if in_trash else
                                         (trash.name if trash else None)),
                     lambda r: self.after_remove([h['uid']]),
                     lambda e: self.wm.toast(str(e), 'red'))
        if in_trash:
            self.wm.confirm('Delete permanently', 'Delete this message for good?', go,
                            yes='Delete', parent=self.win)
        else:
            go()

    def after_remove(self, uids):
        drop = set(uids)
        self.headers = [h for h in self.headers if h['uid'] not in drop]
        for u in drop:
            self.bodies.pop(u, None)
        if self.body_uid in drop:
            self.body = None
            self.body_uid = None
        self.sel = min(self.sel, max(0, len(self.headers) - 1))
        trash = self._role_folder('trash')
        self.status = 'moved to %s' % trash.leaf if (trash and self.folder.role != 'trash') \
            else 'deleted'
        self.invalidate()
        self.select_message(self.sel)
        self.refresh_counts()

    def move_menu(self):
        if not self.current() or not self.folder:
            return
        items = []
        for f in self.folders:
            if f is self.folder:
                continue
            items.append(MenuItem(f.label, lambda f=f: self.do_move(f), '',
                                  sym(ROLE_SYM.get(f.role, 'folder')),
                                  enabled=f.role not in ('all',)))
        if not items:
            self.wm.toast('No other folders')
            return
        self.wm.menu(items, self.win.x + 20, self.win.y + 3, title='Move to')

    def do_move(self, dest):
        h = self.current()
        if not h or not self.session:
            return
        f, sess = self.folder, self.session
        self.job(lambda: sess.move(f, [h['uid']], dest.name),
                 lambda r: self.after_remove([h['uid']]),
                 lambda e: self.wm.toast(str(e), 'red'))

    def current(self):
        return self.headers[self.sel] if 0 <= self.sel < len(self.headers) else None

    # ------------------------------------------------------------ search
    def search(self):
        if not self.folder:
            return
        self.wm.prompt('Search', 'Text in %s:' % self.folder.leaf, self.q,
                       self.do_search, parent=self.win)

    def do_search(self, q):
        q = (q or '').strip()
        if not q or not self.session:
            self.q = ''
            self.refresh()
            return
        f, sess = self.folder, self.session
        gen = self.gen
        self.status = 'searching all mail in %s ...' % f.leaf
        self.invalidate()

        def work():
            return sess.search(f, q, 60)

        def done(hs):
            if gen != self.gen:
                return
            hs.reverse()
            self.headers = hs
            self.q = q
            self.sel = 0
            self.ltop = 0
            self.body = None
            self.body_uid = None
            self.status = '%d hits for "%s"' % (len(hs), q)
            self.invalidate()
            self.select_message(0)
        self.job(work, done, self._conn_error)

    # ------------------------------------------------------------ composing
    def compose(self, mode='new'):
        if not self.acc:
            self.add_account()
            return
        h = self.current()
        if mode in ('reply', 'forward', 'draft') and h:
            self._with_body(h, lambda b: self._open_compose(mode, h, b))
            return
        self._open_compose(mode, None, None)

    def _with_body(self, h, cb):
        uid = h['uid']
        b = self.bodies.get(uid)
        if b is not None:
            cb(b)
            return
        if not self.session or not self.folder:
            self.wm.toast('Not connected')
            return
        sess, f = self.session, self.folder
        prev = self.status
        self.status = 'loading message ...'
        self.invalidate()

        def work():
            return sess.fetch_message(f, uid)

        def done(m):
            self.bodies[uid] = m
            if self.body_uid == uid:
                self.body = m
            if self.status == 'loading message ...':
                self.status = prev
            self.invalidate()
            cb(m)
        self.job(work, done, lambda e: self._body_error(e))

    def _open_compose(self, mode, h, b):
        kwargs = {'session': self.session, 'account': self.acc, 'on_sent': self.after_send,
                  'signature': self.data.get('signature', '')}
        if mode == 'reply' and h:
            kwargs.update(to=h.get('reply_to') or h.get('from_addr') or h['from'],
                          subject=mail.reply_subject(h['subject']),
                          body=mail.reply_text(b, self.acc['email'],
                                               self.data.get('signature', '')),
                          in_reply_to=h.get('msgid', ''), references=h.get('refs', ''))
        elif mode == 'forward' and h:
            head = ('---------- Forwarded message ----------\n'
                    'From: %s\nDate: %s\nSubject: %s\nTo: %s\n\n' %
                    (h['from'], mail.fmt_date_long(h.get('date')), h['subject'], h.get('to', '')))
            kwargs.update(subject=mail.forward_subject(h['subject']),
                          body=head + (b.get('text') or ''))
        elif mode == 'draft' and h:
            kwargs.update(to=h.get('to', ''), cc=h.get('cc', ''), subject=h['subject'],
                          body=(b or {}).get('text', ''),
                          draft_uid=h['uid'], draft_folder=self.folder.name,
                          attachments=(b or {}).get('attachments', []))
        self.wm.open_cls(Compose, size=(min(96, self.wm.w - 6), min(30, self.wm.desk_h - 6)),
                         title='New message', **kwargs)

    def after_send(self, info):
        self.status = 'sent'
        if self.folder:
            self.refresh()
            self.refresh_counts()

    # ------------------------------------------------------------ drawing
    def layout(self):
        w, h = self.cw, self.ch
        if w < 68:
            return ('single', 0, w, 2, h - 3)
        need = 16
        for f in self.folders:
            need = max(need, 4 + f.depth * 2 + len(f.leaf) + 2)
        fac = max(18, min(26, need))
        lst = 40 if w >= 118 else max(26, min(36, w // 3))
        rd = w - fac - lst - 2
        return ('wide', fac, lst, rd, 2, h - 3)

    def toolbar(self, cv):
        th = self.th
        w = cv.w
        groups = (('New', ('new',)), ('Reply', ('reply',)), ('Fwd', ('fwd',)),
                  ('Delete', ('delete',)), ('Move', ('move',)), ('Reload', ('reload',)),
                  ('Search', ('search',)), ('Unread', ('unread',)))
        title = self.acc['email'] if self.acc else 'No account'
        if self.folder:
            title = self.folder.leaf if w < 90 else '%s  %s' % (self.acc['email'], self.folder.leaf)
        need = sum(len(label) + 3 for label, _ in groups) if self.acc else 14
        x = max(12, w - need)
        tw = x - 1
        a = th.c('bright', 'win', 1)
        cv.put(0, 0, fit(title, tw), a)
        self.zone(0, 0, tw, 1, ('account',))
        if not self.acc:
            button(self, cv, x, 0, 'Add account', ('add',), primary=True)
            return
        for label, key in groups:
            if x + len(label) + 2 > w:
                break
            x += button(self, cv, x, 0, label, key) + 1
        if self.busy:
            cv.put(w - 3, 0, sym('dot', '*'), th.c('accent', 'win', 1))
        cv.hline(0, 1, w, th.c('raised', 'win'), '-')

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.zones = []
        self.toolbar(cv)
        lay = self.layout()
        if lay[0] == 'single':
            self.draw_single(cv, lay)
        else:
            _mode, fac, lst, rd, y0, y1 = lay
            hh = y1 - y0 + 1
            self.draw_folders(cv, 0, y0, fac, hh)
            cv.vline(fac, y0, hh, th.c('accent' if self.pane == 'folders' else 'raised', 'win'), '|')
            self.draw_list(cv, fac + 1, y0, lst, hh)
            cv.vline(fac + 1 + lst, y0, hh, th.c('accent' if self.pane == 'list' else 'raised', 'win'), '|')
            self.draw_reader(cv, fac + 2 + lst, y0, rd, hh)
        self.draw_status(cv, h - 1, w)
        if self.help:
            self.draw_help(cv, w, h)

    def draw_status(self, cv, y, w):
        th = self.th
        left = self.status
        if self.q:
            left = 'search: %s  (%s)' % (self.q, left)
        if self.busy:
            left = ('.. ' + left) if left else 'working ...'
        cv.fill(0, y, w, 1, th.c('dim', 'raised'))
        cv.put(0, y, fit(left, w - 24), th.c('text', 'raised'))
        hint = '? keys' if w >= 70 else ''
        cv.put(w - len(hint) - 1, y, hint, th.c('dim', 'raised'))

    def draw_help(self, cv, w, h):
        th = self.th
        lines = ['Mail - keys',
                 '',
                 'Tab / Shift+Tab   switch pane (folders, list, message)',
                 'Up/Down, j/k      move',
                 'Enter             open message',
                 'Space, PgUp/PgDn  scroll / next message',
                 'Esc               back, clear search',
                 '',
                 'n  new            r  reply           f  forward',
                 'd  delete         u  unread          s  star',
                 'm  move           /  search          g  reload',
                 'G  load older     a  save attach.    w  open HTML',
                 'c  accounts       F1 or ?  this help']
        bw = min(w - 4, max(len(s) for s in lines) + 6)
        bh = len(lines) + 2
        x = (w - bw) // 2
        y = max(0, (h - bh) // 2)
        cv.fill(x, y, bw, bh, th.c('text', 'menu'))
        cv.hline(x, y, bw, th.c('raised', 'menu'), '-')
        cv.hline(x, y + bh - 1, bw, th.c('raised', 'menu'), '-')
        for yy in range(y + 1, y + bh - 1):
            cv.putc(x, yy, '|', th.c('raised', 'menu'))
            cv.putc(x + bw - 1, yy, '|', th.c('raised', 'menu'))
        for i, ln in enumerate(lines):
            a = th.c('bright', 'menu', 1) if i == 0 else th.c('text', 'menu')
            cv.put(x + 2, y + 1 + i, fit(ln, bw - 4), a)
        self.zone(x, y, bw, bh, ('help',))

    # ------------------------------------------------------------ folders pane
    def draw_folders(self, cv, x, y, w, h):
        th = self.th

        def row(cv, rx, ry, rw, f, i, on):
            bg = 'sel' if on else 'win'
            unread = f.unseen or 0
            cnt = ('%d' % unread) if unread else ''
            icon = sym(ROLE_SYM.get(f.role, 'folder'), '-')
            name_x = rx + 2 + f.depth * 2
            label = f.leaf
            avail = rw - (name_x - rx) - len(cnt) - (1 if cnt else 0)
            a = th.c('bright', bg, 1) if (on or f.role == 'inbox') else th.c('text', bg)
            if on:
                cv.fill(rx, ry, rw, 1, th.c('bright', 'sel'))
            cv.put(rx + 1 + f.depth * 2, ry, fit(icon + ' ' + label, avail),
                   a if unread else th.c('dim' if on else 'text', bg))
            if cnt:
                cv.put(rx + rw - len(cnt), ry, cnt, th.c('accent' if not on else 'on_accent', bg, 1))
            self.zone(rx, ry, rw, 1, ('f', i))
        self.fac.draw(cv, x, y, w, h, row, th)

    # ------------------------------------------------------------ message list
    def list_geometry(self, x, y, w, h):
        per = max(1, h // 2)
        sb = sb_geom(x + w - 1, y, h, len(self.headers), per, self.ltop)
        return per, sb

    def draw_list(self, cv, x, y, w, h):
        th = self.th
        per, sb = self.list_geometry(x, y, w, h)
        if self.sel < self.ltop:
            self.ltop = self.sel
        elif self.sel >= self.ltop + per:
            self.ltop = self.sel - per + 1
        self.ltop = max(0, min(self.ltop, max(0, len(self.headers) - per)))
        lw = w - (1 if sb else 0)
        if not self.headers:
            msg = 'Searching...' if self.busy else ('No messages' if self.folder else '')
            cv.put(x + 2, y + 1, fit(msg, lw - 3), th.c('dim', 'win'))
            return
        for slot in range(per):
            i = self.ltop + slot
            if i >= len(self.headers):
                break
            hdr = self.headers[i]
            on = i == self.sel
            ry = y + slot * 2
            bg = 'sel' if on else 'win'
            if on:
                cv.fill(x, ry, lw, 2, th.c('bright', 'sel'))
            flags = hdr.get('flags', '')
            seen = 'S' in flags
            fg = th.c('bright', bg, 0 if seen else 1) if not on else th.c('bright', 'sel', 1)
            who = hdr.get('from') or hdr.get('from_addr') or '(unknown)'
            who = mail.short_name(who) if '<' in who else who
            date = mail.fmt_date(hdr.get('date'))
            who_w = lw - len(date) - 4
            cv.put(x + 1, ry, fit(who, who_w), fg)
            if date:
                cv.put(x + lw - len(date) - 1, ry, date,
                       th.c('on_accent', 'sel') if on else th.c('dim', 'win'))
            subj_a = th.c('bright', 'sel') if on else (
                th.c('bright', 'win') if not seen else th.c('text', 'win'))
            subj = hdr.get('subject') or ''
            if 'F' in flags:
                subj = sym('star', '*') + ' ' + subj
            mark = ' ' if 'S' in flags else sym('dot', '*')
            cv.put(x + 1, ry + 1, fit(mark + ' ' + subj, lw - 2), subj_a)
            self.zone(x, ry, lw, 2, ('m', i))
        if sb:
            sb_draw(cv, sb, th, 'win')

    # ------------------------------------------------------------ reader
    def reader_geometry(self, x, y, w, h):
        return w - 1, h

    def draw_reader(self, cv, x, y, w, h):
        th = self.th
        hdr = self.current()
        b = self.body if self.body_uid == (hdr or {}).get('uid') else None
        rw = w - 1
        if not hdr:
            cv.put(x + 1, y, 'No message selected', th.c('dim', 'win'))
            return
        yy = y
        cv.put(x, yy, fit(hdr.get('subject') or '', rw), th.c('bright', 'win', 1))
        yy += 1
        lines = [('From', hdr.get('from', '')),
                 ('To', hdr.get('to', '') or '-'),
                 ('Date', '%s  %s' % (mail.fmt_date_long(hdr.get('date')), mail.fmt_size(hdr.get('size'))))]
        if hdr.get('cc'):
            lines.insert(2, ('Cc', hdr['cc']))
        for label, val in lines:
            if yy >= y + 4:
                break
            cv.put(x, yy, fit('%-5s ' % label, 6), th.c('dim', 'win'))
            cv.put(x + 6, yy, fit(val, rw - 6), th.c('text', 'win'))
            yy += 1
        cv.hline(x, yy, rw, th.c('raised', 'win'), '-')
        yy += 1
        tw = rw
        if b is None:
            self.read_lines = ['Loading message ...' if self.busy else 'Press Enter to load']
        else:
            if self.read_w != tw or getattr(self, '_read_uid', None) != hdr['uid']:
                body = (b.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
                self.read_lines = wrap_text(body, max(8, tw - 1))
                self.read_w = tw
                self._read_uid = hdr['uid']
        avail = h - (yy - y)
        if avail > 0:
            if self.read_top > max(0, len(self.read_lines) - 1):
                self.read_top = max(0, len(self.read_lines) - 1)
            for i in range(avail):
                li = self.read_top + i
                if li >= len(self.read_lines):
                    break
                cv.put(x, yy + i, fit(self.read_lines[li], tw), th.c('text', 'win'))
            if self.read_lines and len(self.read_lines) > avail:
                frac = self.read_top / max(1, len(self.read_lines) - avail)
                cv.put(x + rw - 1, y, sym('arrow', '^') if frac > 0 else ' ', th.c('dim', 'win'))
        if b and b.get('attachments'):
            ay = y + h - 1
            names = ', '.join('%s (%s)' % (a['name'], mail.fmt_size(a['size']))
                              for a in b['attachments'][:3])
            more = '' if len(b['attachments']) <= 3 else ' +%d' % (len(b['attachments']) - 3)
            cv.put(x, ay, fit(sym('attach', '@') + ' ' + names + more, rw),
                   th.c('accent', 'win', 1))
            self.zone(x, ay, rw, 1, ('attach',))

    def draw_single(self, cv, lay):
        th = self.th
        _, x, w, y0, y1 = lay
        hh = y1 - y0 + 1
        stage = getattr(self, 'stage', 'list')
        if stage == 'folders' or not self.folder:
            self.draw_folders(cv, 0, y0, w, hh)
        elif stage == 'reader' and self.current():
            self.draw_reader(cv, 0, y0, w, hh)
        else:
            self.draw_list(cv, 0, y0, w, hh)

    # ------------------------------------------------------------ events
    def scroll(self, d):
        if self.pane == 'reader':
            maxv = max(0, len(self.read_lines) - max(1, self.ch - 8))
            self.read_top = max(0, min(self.read_top + d, maxv))
        elif self.headers:
            self.select_message(self.sel + d)
            self.ltop = max(0, min(self.ltop, max(0, len(self.headers) - 1)))
        self.invalidate()

    def on_click(self, key, ev):
        if key[0] == 'f':
            self.pane = 'folders'
            self.fac.select(key[1])
        elif key[0] == 'm':
            self.pane = 'list'
            self.select_message(key[1], open_body=(ev.clicks >= 2))
            if ev.clicks >= 2:
                self.pane = 'reader'
        elif key[0] == 'reader':
            self.pane = 'reader'
        elif key[0] == 'attach':
            self.save_attachments()
        elif key == ('account',):
            self.account_menu()
        elif key == ('add',):
            self.add_account()
        elif key == ('new',):
            self.compose('new')
        elif key == ('reply',):
            self.compose('reply')
        elif key == ('fwd',):
            self.compose('forward')
        elif key == ('delete',):
            self.delete()
        elif key == ('move',):
            self.move_menu()
        elif key == ('reload',):
            self.refresh()
        elif key == ('search',):
            self.search()
        elif key == ('unread',):
            self.toggle_unread()
        elif key == ('help',):
            self.help = False
        self.invalidate()

    def on_mouse(self, ev):
        k = self.track_hover(ev)
        if ev.kind in ('wheelup', 'wheeldown'):
            d = -1 if ev.kind == 'wheelup' else 1
            if k and k[0] == 'm':
                self.select_message(self.sel + d * 2)
            elif k and k[0] == 'reader':
                self.pane = 'reader'
                self.scroll(d * 3)
            elif k and k[0] == 'f':
                self.fac.select(max(0, self.fac.sel + d))
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if self.help:
            if k in ('esc', '?', 'f1', 'enter', 'q'):
                self.help = False
                self.invalidate()
                return True
        if k == 'f1' or (k == '?'):
            self.help = True
            self.invalidate()
            return True
        if k in ('ctrl+n',):
            self.compose('new')
            return True
        if k in ('j', 'down'):
            if self.pane == 'folders':
                self.fac.select(min(len(self.folders) - 1, self.fac.sel + 1))
            elif self.pane == 'reader':
                self.scroll(1)
            else:
                self.select_message(self.sel + 1)
            return True
        if k in ('k', 'up'):
            if self.pane == 'folders':
                self.fac.select(max(0, self.fac.sel - 1))
            elif self.pane == 'reader':
                self.scroll(-1)
            else:
                self.select_message(self.sel - 1)
            return True
        if k in ('pgdn', ' '):
            if self.pane == 'reader' and self.current():
                self.scroll(max(1, self.ch - 9))
            else:
                self.select_message(self.sel + max(1, (self.ch - 4) // 2))
            return True
        if k == 'pgup':
            if self.pane == 'reader':
                self.scroll(-max(1, self.ch - 9))
            else:
                self.select_message(self.sel - max(1, (self.ch - 4) // 2))
            return True
        if k == 'enter':
            if self.pane == 'folders' and self.fac.current():
                self.open_folder(self.fac.current())
            else:
                h = self.current()
                if h and self.folder and self.folder.role == 'drafts':
                    self.compose('draft')
                else:
                    self.pane = 'reader'
                    self.select_message(self.sel, open_body=True)
            self.invalidate()
            return True
        if k == 'tab':
            order = ['folders', 'list', 'reader'] if self.cw >= 68 else ['list', 'reader']
            i = (order.index(self.pane) + 1) % len(order) if self.pane in order else 0
            self.pane = order[i]
            self.invalidate()
            return True
        if k == 'shift+tab':
            order = ['folders', 'list', 'reader'] if self.cw >= 68 else ['list', 'reader']
            i = (order.index(self.pane) - 1) % len(order) if self.pane in order else 0
            self.pane = order[i]
            self.invalidate()
            return True
        if k == 'esc':
            if self.q:
                self.q = ''
                self.refresh()
            elif self.cw < 68 and self.pane == 'reader':
                self.pane = 'list'
            self.invalidate()
            return True
        if k == 'n':
            self.compose('new')
            return True
        if k == 'c':
            self.account_menu()
            return True
        if k == 'r':
            self.compose('reply')
            return True
        if k == 'f':
            self.compose('forward')
            return True
        if k == 'd':
            self.delete()
            return True
        if k == 'u':
            self.toggle_unread()
            return True
        if k in ('s', '!'):
            self.toggle_flag()
            return True
        if k == 'm':
            self.move_menu()
            return True
        if k == '/':
            self.search()
            return True
        if k in ('g', 'f5'):
            self.refresh()
            return True
        if k == 'G':
            self.older()
            return True
        if k == 'a':
            self.save_attachments()
            return True
        if k == 'w':
            self.open_html()
            return True
        if k == 'A':
            self.account_menu()
            return True
        if k in ('home', 'end'):
            if self.headers:
                self.select_message(0 if k == 'home' else len(self.headers) - 1)
            return True
        return False

    def on_close(self):
        self.close_session()
        return True

    def _tick(self):
        if not self.connected and self.acc and not self.busy and not self.session:
            self.open_account(self.acc, self.folder.name if self.folder else None)

    # ------------------------------------------------------------ extras
    def save_attachments(self):
        if not self.body or not self.body.get('attachments'):
            return
        atts = self.body['attachments']
        if len(atts) == 1:
            a = atts[0]

            def save(path):
                try:
                    with open(path, 'wb') as f:
                        f.write(a['data'])
                    self.wm.toast('Saved %s' % os.path.basename(path))
                except OSError as e:
                    self.wm.toast(str(e), 'red')
            self.wm.pick_file(save, mode='save', name=a['name'],
                              start=self.wm.cfg.get('attach_dir') or os.path.expanduser('~/Downloads'),
                              parent=self.win)
            return
        start = self.wm.cfg.get('attach_dir') or os.path.expanduser('~/Downloads')
        for a in atts:
            path = os.path.join(start, a['name'])
            n = 1
            while os.path.exists(path):
                base, ext = os.path.splitext(a['name'])
                path = os.path.join(start, '%s-%d%s' % (base, n, ext))
                n += 1
            try:
                with open(path, 'wb') as f:
                    f.write(a['data'])
            except OSError as e:
                self.wm.toast(str(e), 'red')
                return
        self.wm.toast('Saved %d attachments to %s' % (len(atts), start))

    def open_html(self):
        if not self.body or not self.body.get('html'):
            return
        import tempfile
        path = os.path.join(tempfile.gettempdir(), 'monocell-mail-%s.html' %
                            (self.body.get('uid') or str(time.time())))
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.body['html'])
        except OSError as e:
            self.wm.toast(str(e), 'red')
            return
        self.wm.open('browser', path=path)

    # ------------------------------------------------------------ jobs
    def job(self, fn, done=None, err=None):
        self.busy += 1
        self.invalidate()

        def ok(res):
            self.busy = max(0, self.busy - 1)
            if done:
                try:
                    done(res)
                except Exception as e:      # a bug in the callback must not wedge the UI
                    self.wm.on_error(e)
            self.invalidate()

        def bad(e):
            self.busy = max(0, self.busy - 1)
            if err:
                err(e)
            else:
                self.status = str(e)
                self.wm.toast('Mail: %s' % e, 'red')
            self.invalidate()
        self.thread(fn, ok, bad)


class Compose(App):
    name = 'compose'
    title = 'New message'
    icon = '[M]'
    size = (84, 26)
    min_size = (46, 14)
    shift_select = True

    def __init__(self, wm, win, session=None, account=None, to='', cc='', subject='', body='',
                 in_reply_to='', references='', on_sent=None, draft_uid=None, draft_folder=None,
                 attachments=(), signature='', **kw):
        super().__init__(wm, win)
        self.session = session or (mail.Session(account) if account else None)
        self.acc = account
        self.on_sent = on_sent
        self.draft_uid = draft_uid
        self.draft_folder = draft_folder
        self.in_reply_to = in_reply_to or ''
        self.references = references or ''
        self.attachments = list(attachments or [])
        if signature and not body:
            body = '\n\n-- \n' + signature.strip('\n')
        self.to = TextField(to or '', 'recipient@example.com')
        self.cc = TextField(cc, 'copy (empty = none)')
        self.subject = TextField(subject or '', 'subject')
        self.ta = TextArea(body, wrap=True)
        self.ta.scrollbar = True
        self.ta.height = max(4, self.ch - 9)
        self.field = 0
        self.busy = False
        self.status = ''
        self.show_cc = bool(cc)
        if to:
            self.field = len(self.fields()) - 1
        self.set_title(('Draft - ' if draft_uid else 'New message - ') +
                       (self.acc['email'] if self.acc else ''))

    def fields(self):
        f = [self.to]
        if self.show_cc:
            f.append(self.cc)
        f.append(self.subject)
        f.append(self.ta)
        return f

    def current(self):
        f = self.fields()
        return f[min(self.field, len(f) - 1)]

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        self.zones = []
        y = 0
        rows = [('To', self.to)]
        if self.show_cc:
            rows.append(('Cc', self.cc))
        rows.append(('Subject', self.subject))
        for i, (label, fld) in enumerate(rows):
            on = self.current() is fld
            cv.put(0, y, fit(label, 8), th.c('accent' if on else 'dim', 'win', 1 if on else 0))
            fld.draw(cv, 8, y, w - 8, th, on)
            self.zone(8, y, w - 8, 1, ('field', i))
            y += 1
        self.subject_y = 1
        cv.hline(0, y, w, th.c('raised', 'win'), '-')
        y += 1
        body_h = h - y - 2
        self.ta.height = max(1, body_h)
        on_body = self.current() is self.ta
        self.ta.draw(cv, 0, y, w, body_h, th, on_body)
        self.zone(0, y, w, body_h, ('field', len(rows)))
        by = h - 1
        x = 0
        if not self.show_cc:
            x += button(self, cv, x, by, 'Cc', ('showcc',)) + 1
        x += button(self, cv, x, by, 'Attach', ('attach',)) + 1
        x += button(self, cv, x, by, 'Send', ('send',), primary=True) + 1
        x += button(self, cv, x, by, 'Draft', ('draft',)) + 1
        x += button(self, cv, x, by, 'Discard', ('discard',)) + 1
        info = self.status or ('sending ...' if self.busy else '')
        if self.attachments:
            names = ', '.join(a[0] for a in self.attachments[:2])
            if len(self.attachments) > 2:
                names += ' +%d' % (len(self.attachments) - 2)
            info = (info + '  ' if info else '') + sym('attach', '@') + ' ' + names
        if info:
            cv.put(min(x + 1, w - 10), by, fit(info, max(0, w - x - 1)), th.c('dim', 'raised'))

    def on_click(self, key, ev):
        if key[0] == 'field':
            self.focus_field(key[1])
        elif key == ('showcc',):
            self.show_cc = True
            self.field = 1
        elif key == ('attach',):
            self.attach()
        elif key == ('send',):
            self.send()
        elif key == ('draft',):
            self.save_draft()
        elif key == ('discard',):
            self.discard()
        self.invalidate()

    def focus_field(self, i):
        self.field = i
        f = self.current()
        if isinstance(f, TextField):
            f.sel_all = True
        self.invalidate()

    def on_key(self, ev):
        k = ev.key
        if k == 'ctrl+enter' or k == 'f5':
            self.send()
            return True
        if k == 'ctrl+s':
            self.save_draft()
            return True
        if k == 'tab':
            self.field = (self.field + 1) % len(self.fields())
            self.invalidate()
            return True
        if k == 'shift+tab':
            self.field = (self.field - 1) % len(self.fields())
            self.invalidate()
            return True
        if k in ('up', 'down') and not isinstance(self.current(), TextArea):
            self.field = max(0, min(len(self.fields()) - 1, self.field + (-1 if k == 'up' else 1)))
            self.invalidate()
            return True
        f = self.current()
        if isinstance(f, TextField):
            if k == 'enter' and f is not self.subject:
                self.field = min(len(self.fields()) - 1, self.field + 1)
                self.invalidate()
                return True
            if k == 'enter' and f is self.subject:
                self.field = len(self.fields()) - 1
                self.invalidate()
                return True
            return f.key(ev)
        return f.key(ev)

    def on_paste(self, text):
        f = self.current()
        if isinstance(f, TextArea):
            f.insert(text)
        else:
            f.insert(text.replace('\n', ' '))

    def on_close(self):
        if self.ta.modified or self.to.text or self.subject.text:
            self.wm.confirm('Close', 'Discard this message?', lambda: self.wm.close(self.win, True),
                            yes='Discard', parent=self.win)
            return False
        return True

    def attach(self):
        def picked(path):
            if not path:
                return
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except OSError as e:
                self.wm.toast(str(e), 'red')
                return
            if len(data) > 15 * 1024 * 1024:
                self.wm.toast('Attachment too large (15 MB)', 'red')
                return
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            self.attachments.append((os.path.basename(path), ctype, data))
            self.wm.cfg['attach_dir'] = os.path.dirname(path)
            self.invalidate()
        self.wm.pick_file(picked, mode='open', parent=self.win)

    def _addresses(self):
        to = self.to.text.strip()
        ok, bad = mail.parse_addresses(to)
        if not ok:
            self.status = 'Check To: %s' % ', '.join(bad or ['empty'])
            return None
        cc = self.cc.text.strip() if self.show_cc else ''
        if cc:
            ok, bad = mail.parse_addresses(cc)
            if not ok:
                self.status = 'Check Cc: %s' % ', '.join(bad)
                return None
        return to, cc

    def _raw(self, to, cc):
        return mail.build_message(self.acc, to, self.subject.text.strip(), self.ta.text(),
                                  cc=cc, in_reply_to=self.in_reply_to,
                                  references=self.references, attach=self.attachments)

    def send(self):
        if self.busy:
            return
        if not self.acc or not self.session:
            self.wm.toast('No account configured', 'red')
            return
        ad = self._addresses()
        if not ad:
            return
        to, cc = ad
        raw = self._raw(to, cc)
        self.busy = True
        self.status = 'sending ...'
        self.invalidate()
        sent = self._sent_folder()

        def work():
            self.session.send(raw)
            copied = True
            if sent:
                try:
                    self.session.append(sent, raw, '(\\Seen)')
                except mail.MailError:
                    copied = False
            if self.draft_uid and self.draft_folder:
                try:
                    self.session.delete(self.draft_folder, [self.draft_uid], None)
                except mail.MailError:
                    pass
            return sent, copied

        def done(res):
            self.busy = False
            self.status = 'sent'
            if self.on_sent:
                self.on_sent(self)
            sent_folder, copied = res
            if not copied:
                self.wm.toast('Sent, but the copy in %s failed' % sent_folder, 'red')
            self.wm.close(self.win, True)

        def bad(e):
            self.busy = False
            self.status = str(e)
            self.wm.toast('Send failed: %s' % e, 'red')
            self.invalidate()
        self.thread(work, done, bad)

    def _sent_folder(self):
        name = self.acc.get('sent') if self.acc else ''
        if name:
            return name
        try:
            for f in self.session.list_folders():
                if f.role == 'sent':
                    return f.name
        except (mail.MailError, OSError):
            return ''
        return ''

    def save_draft(self):
        if not self.acc or not self.session:
            return
        raw = self._raw(self.to.text.strip() or '(none)', '')
        name = (self.acc.get('drafts') or '')
        self.busy = True

        def done(res):
            self.busy = False
            self.wm.toast('Draft saved')
            self.wm.close(self.win, True)

        def bad(e):
            self.busy = False
            self.status = str(e)
            self.invalidate()

        def work():
            folder = name
            if not folder:
                for f in self.session.list_folders():
                    if f.role == 'drafts':
                        folder = f.name
                        break
            if not folder:
                raise mail.MailError('no drafts folder on this account')
            self.session.append(folder, raw, '(\\Draft)')
            return True
        self.thread(work, done, bad)

    def discard(self):
        if self.ta.modified:
            self.wm.confirm('Discard', 'Throw this message away?', lambda: self.wm.close(self.win, True),
                            yes='Discard', parent=self.win)
        else:
            self.wm.close(self.win, True)


class AccountForm(App):
    name = 'mailaccount'
    title = 'Mail account'
    icon = '[M]'
    size = (66, 19)
    min_size = (54, 17)
    single = True

    ROWS = ('provider', 'email', 'password', 'name', 'imap', 'imap_port', 'tls', 'smtp',
            'smtp_port', 'user', 'sent', 'drafts')
    PLAIN = ('provider', 'tls')

    def __init__(self, wm, win, account=None, on_save=None, start=None, **kw):
        super().__init__(wm, win)
        self.on_save = on_save
        acc = dict(account) if account else mail.blank_account(getattr(start, 'email', '') if start else '')
        self.acc = acc
        self.provider = acc.get('provider') or 'strato'
        self.acc['provider'] = self.provider
        self.fields = {}
        self.tls = bool(acc.get('imap_ssl', True))
        for name in self.ROWS:
            if name in self.PLAIN:
                continue
            fld = TextField(str(acc.get(name, '')), password=(name == 'password'))
            self.fields[name] = fld
        self.row = 0
        self.status = ''
        self.busy = False

    def rows(self):
        return [(n, None if n in self.PLAIN else self.fields[n]) for n in self.ROWS]

    def label(self, name):
        return {'provider': 'Provider', 'email': 'E-mail', 'password': 'Password', 'name': 'Your name',
                'imap': 'IMAP host', 'imap_port': 'IMAP port', 'smtp': 'SMTP host',
                'smtp_port': 'SMTP port', 'user': 'IMAP user', 'sent': 'Sent',
                'drafts': 'Drafts', 'tls': 'TLS'}[name]

    def current_field(self):
        rows = self.rows()
        return rows[min(self.row, len(rows) - 1)][1]

    def draw(self, cv, focused):
        th = self.th
        w = cv.w
        cv.put(0, 0, 'Account', th.c('bright', 'win', 1))
        y = 1
        for i, (name, fld) in enumerate(self.rows()):
            on = i == self.row
            cv.put(0, y, fit(self.label(name), 12), th.c('accent' if on else 'dim', 'win', 1 if on else 0))
            if name in self.PLAIN:
                val = self.provider if name == 'provider' else ('yes' if self.tls else 'no')
                cv.fill(12, y, w - 12, 1, th.c('text', 'field'))
                cv.put(13, y, fit('%s <  >' % val, w - 14),
                       th.c('on_accent', 'accent', 1) if on else th.c('text', 'field'))
                self.zone(12, y, w - 12, 1, ('row', i))
            else:
                fld.draw(cv, 12, y, w - 12, th, on)
                self.zone(12, y, w - 12, 1, ('row', i))
            y += 1
        y += 1
        cv.hline(0, y - 1, w, th.c('raised', 'win'), '-')
        x = 0
        x += button(self, cv, x, y, 'Test', ('test',)) + 1
        x += button(self, cv, x, y, 'Save', ('save',), primary=True) + 1
        if self.acc.get('email'):
            x += button(self, cv, x, y, 'Remove', ('remove',)) + 1
        hint = self.status or ('testing ...' if self.busy else 'Tab next  Enter save  Ctrl+T test')
        cv.put(x + 1, y, fit(hint, max(0, w - x - 1)), th.c('dim', 'raised'))

    def on_click(self, key, ev):
        if key[0] == 'row':
            self.row = key[1]
            if self.rows()[self.row][0] == 'tls':
                self.toggle_tls()
            self.invalidate()
        elif key == ('test',):
            self.test()
        elif key == ('save',):
            self.save()
        elif key == ('remove',):
            self.wm.confirm('Remove account', 'Remove %s from monocell?' % self.acc.get('email', ''),
                            self.remove, yes='Remove', parent=self.win)

    def collect(self):
        a = dict(self.acc)
        for name, fld in self.fields.items():
            a[name] = fld.text.strip()
        a['provider'] = self.provider
        a['imap_ssl'] = a['smtp_ssl'] = self.tls
        for k in ('imap_port', 'smtp_port'):
            try:
                a[k] = int(a.get(k) or 0)
            except ValueError:
                a[k] = 993 if k == 'imap_port' else 465
        if not a.get('user'):
            a['user'] = a.get('email', '')
        if not a.get('name'):
            a['name'] = a.get('email', '')
        return a

    def toggle_tls(self):
        self.tls = not self.tls
        self.invalidate()

    def set_provider(self, d):
        names = list(mail.PROVIDERS)
        self.provider = names[(names.index(self.provider) + d) % len(names)] \
            if self.provider in names else names[0]
        imap, ip, smtp, sp = mail.PROVIDERS[self.provider]
        self.fields['imap'].set(imap)
        self.fields['imap_port'].set(str(ip))
        self.fields['smtp'].set(smtp)
        self.fields['smtp_port'].set(str(sp))
        email_addr = self.fields['email'].text or self.acc.get('email', '')
        if email_addr and not self.fields['user'].text:
            self.fields['user'].set(email_addr)
        if self.provider == 'gmail':
            self.status = 'Gmail: create an app password (2-step verification)'
        else:
            self.status = ''
        self.invalidate()

    def on_key(self, ev):
        k = ev.key
        if k in ('ctrl+t', 'f8'):
            self.test()
            return True
        if k in ('ctrl+s',):
            self.save()
            return True
        if k == 'tab':
            self.row = (self.row + 1) % len(self.rows())
            self.invalidate()
            return True
        if k == 'shift+tab':
            self.row = (self.row - 1) % len(self.rows())
            self.invalidate()
            return True
        if k in ('up', 'down'):
            d = -1 if k == 'up' else 1
            if self.row == 0:
                self.set_provider(d)
            elif self.rows()[self.row][0] == 'tls':
                self.toggle_tls()
            else:
                self.row = max(0, min(len(self.rows()) - 1, self.row + d))
            self.invalidate()
            return True
        if self.row == 0 and k in ('left', 'right'):
            self.set_provider(-1 if k == 'left' else 1)
            return True
        if self.rows()[self.row][0] == 'tls' and k in ('left', 'right', 'space'):
            self.toggle_tls()
            return True
        if k == 'enter':
            if self.row == 0:
                self.set_provider(1)
            else:
                self.row = min(len(self.rows()) - 1, self.row + 1)
            self.invalidate()
            return True
        fld = self.current_field()
        return fld.key(ev) if fld else False

    def test(self):
        a = self.collect()
        self.busy = True
        self.status = 'connecting ...'
        self.invalidate()

        def work():
            s = mail.Session(a)
            s.connect()
            folders = s.list_folders()
            s.close()
            return folders

        def done(folders):
            self.busy = False
            self.status = 'OK - %d folders' % len(folders)
            self.invalidate()

        def bad(e):
            self.busy = False
            self.status = str(e)
            self.invalidate()
        self.thread(work, done, bad)

    def save(self):
        a = self.collect()
        if not a.get('email') or '@' not in a['email']:
            self.status = 'E-mail address missing'
            self.invalidate()
            return
        if not a.get('password'):
            self.status = 'Password missing'
            self.invalidate()
            return
        if self.on_save:
            self.on_save(a)
        self.wm.close(self.win, True)

    def remove(self):
        a = self.collect()
        if self.on_save:
            self.on_save(a, True)
        self.wm.close(self.win, True)


APP = Mail
