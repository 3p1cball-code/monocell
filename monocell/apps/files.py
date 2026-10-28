"""File manager + file dialogs + 'open with' associations."""
import datetime
import os
import shutil
import stat
import subprocess
import time
import urllib.parse

from .. import media, transfer
from ..app import App
from ..util import HOME, fit, human_size, shorten_path, wrap_text
from ..widgets import ListView, MenuItem, TextField, button, hbar

AUDIO = ('.mp3', '.flac', '.ogg', '.oga', '.opus', '.wav', '.m4a', '.aac', '.wma', '.aiff', '.mka')
VIDEO = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.m4v', '.wmv', '.flv', '.mpg', '.mpeg', '.ts', '.ogv', '.3gp')
SHEET = ('.csv', '.tsv', '.xlsx', '.ods', '.tws')
DOC = ('.md', '.markdown', '.docx', '.odt', '.rtf')
WEB = ('.html', '.htm', '.xhtml')


def kind_of(path):
    from ..imaging import IMAGE_EXT
    if os.path.isdir(path):
        return 'dir'
    p = path.lower()
    if p.endswith(IMAGE_EXT):
        return 'image'
    if p.endswith(AUDIO):
        return 'audio'
    if p.endswith(VIDEO) or p.endswith('.gif') and False:
        return 'video'
    if p.endswith(SHEET):
        return 'sheet'
    if p.endswith(DOC):
        return 'doc'
    if p.endswith(WEB):
        return 'web'
    if p.endswith('.pdf'):
        return 'pdf'
    return 'text'


ICONS = {'dir': '/', 'image': '*', 'audio': '~', 'video': '>', 'sheet': '#', 'doc': 'W', 'web': '@',
         'text': ' ', 'pdf': 'P', 'exec': '!', 'link': '@'}
KIND_COLOR = {'dir': 'accent', 'image': 'magenta', 'audio': 'cyan', 'video': 'blue', 'sheet': 'green',
              'doc': 'yellow', 'web': 'blue', 'pdf': 'red', 'exec': 'green'}


def looks_binary(path):
    try:
        with open(path, 'rb') as f:
            chunk = f.read(4096)
        return b'\0' in chunk
    except OSError:
        return False


def open_path(wm, path, with_app=None):
    k = kind_of(path)
    app = with_app or {'dir': 'files', 'image': 'viewer', 'audio': 'music', 'video': 'video', 'sheet': 'sheet',
                       'doc': 'writer', 'web': 'browser', 'text': 'editor', 'pdf': 'pdf'}[k]
    if app == 'editor' and k == 'text' and looks_binary(path) and not with_app:
        wm.toast('Binary file - not opened', 'red')
        return
    if app == 'browser':
        return wm.open('browser', url='file://' + urllib.parse.quote(path))
    if app == 'files':
        return wm.open('files', path=path)
    return wm.open(app, path=path)


class Entry:
    __slots__ = ('name', 'path', 'isdir', 'size', 'mtime', 'kind', 'link', 'mode')


def list_dir(path, hidden=False, sort='name'):
    out = []
    try:
        names = os.listdir(path)
    except OSError as e:
        raise e
    for n in names:
        if not hidden and n.startswith('.'):
            continue
        p = os.path.join(path, n)
        e = Entry()
        e.name = n
        e.path = p
        try:
            st = os.stat(p)
            lst = os.lstat(p)
            e.link = stat.S_ISLNK(lst.st_mode)
            e.isdir = stat.S_ISDIR(st.st_mode)
            e.size = st.st_size
            e.mtime = st.st_mtime
            e.mode = st.st_mode
        except OSError:
            e.isdir, e.size, e.mtime, e.link, e.mode = False, 0, 0, True, 0
        if e.isdir:
            e.kind = 'dir'
        else:
            e.kind = kind_of(p)
            if e.kind == 'text' and e.mode & 0o111:
                e.kind = 'exec'
        out.append(e)
    key = {'name': lambda e: (not e.isdir, e.name.lower()),
           'size': lambda e: (not e.isdir, -e.size),
           'date': lambda e: (not e.isdir, -e.mtime)}[sort]
    out.sort(key=key)
    return out


def places():
    """Sidebar: home folders + mounted shares, without duplicate entries."""
    cand = [('Home', HOME)]
    for name in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos',
                 'Schreibtisch', 'Dokumente', 'Bilder', 'Musik'):
        p = os.path.join(HOME, name)
        if os.path.isdir(p):
            cand.append((name, p))
    cand.append(('Root', '/'))
    mounts = []
    try:
        with open('/proc/mounts') as f:
            for line in f:
                mp = line.split()[1].replace('\\040', ' ')
                if mp.startswith(('/mnt/', '/media/', '/run/media/')) and mp.count('/') <= 4:
                    mounts.append((os.path.basename(mp.rstrip('/')) or mp, mp))
    except OSError:
        pass
    out, seen, labels = [], set(), {}
    for label, p in cand:
        try:
            rp = os.path.realpath(p)
        except OSError:
            rp = p
        if rp in seen or not os.path.isdir(p):
            continue
        seen.add(rp)
        out.append((label, p))
        labels.setdefault(label, 0)
        labels[label] += 1
    for label, p in mounts:
        try:
            rp = os.path.realpath(p)
        except OSError:
            rp = p
        if rp in seen:
            continue
        seen.add(rp)
        if labels.get(label):
            parent = os.path.basename(os.path.dirname(p.rstrip('/'))) or ''
            label = '%s (%s)' % (label, parent) if parent else label
        labels[label] = labels.get(label, 0) + 1
        out.append((label, p))
    return out[:18]


def trash(path):
    """freedesktop.org trash."""
    tdir = os.path.join(HOME, '.local', 'share', 'Trash')
    os.makedirs(os.path.join(tdir, 'files'), exist_ok=True)
    os.makedirs(os.path.join(tdir, 'info'), exist_ok=True)
    base = os.path.basename(path.rstrip('/'))
    name = base
    i = 1
    while os.path.exists(os.path.join(tdir, 'files', name)):
        i += 1
        name = '%s.%d' % (base, i)
    with open(os.path.join(tdir, 'info', name + '.trashinfo'), 'w') as f:
        f.write('[Trash Info]\nPath=%s\nDeletionDate=%s\n' % (urllib.parse.quote(path),
                                                               time.strftime('%Y-%m-%dT%H:%M:%S')))
    shutil.move(path, os.path.join(tdir, 'files', name))


class FileList:
    """Directory listing widget shared by Files and the file dialogs."""

    def __init__(self, app, path):
        self.app = app
        self.th = app.th
        self.path = os.path.abspath(path)
        self.hidden = False
        self.sort = 'name'
        self.list = ListView()
        self.entries = []
        self.marked = set()
        self.error = ''
        self.typed = ''
        self.typed_t = 0
        self.filter = None
        self.refresh()

    def refresh(self, keep=None):
        try:
            ents = list_dir(self.path, self.hidden, self.sort)
            if self.filter:
                ents = [e for e in ents if self.filter(e)]
            self.entries = ents
            self.error = ''
        except OSError as e:
            self.entries = []
            self.error = e.strerror or str(e)
        self.marked &= {e.path for e in self.entries}
        self.list.set_items(self.entries, keep=keep is not None)
        if keep:
            for i, e in enumerate(self.entries):
                if e.name == keep:
                    self.list.sel = i
                    self.list.ensure()
                    break

    def chdir(self, path, select=None):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return False
        old = self.path
        self.path = path
        self.marked = set()
        self.refresh()
        if select is None and old.startswith(path) and old != path:
            select = os.path.relpath(old, path).split(os.sep)[0]
        if select:
            for i, e in enumerate(self.entries):
                if e.name == select:
                    self.list.sel = i
                    self.list.ensure()
        remember = getattr(self.app, 'remember_dir', None)
        if remember:
            remember(self.path)
        return True

    def up(self):
        parent = os.path.dirname(self.path.rstrip('/')) or '/'
        self.chdir(parent)

    def current(self):
        return self.list.current()

    def selection(self):
        if self.marked:
            return [e for e in self.entries if e.path in self.marked]
        c = self.current()
        return [c] if c else []

    def draw(self, cv, x, y, w, h, focused, details=True):
        th = self.th
        app = self.app
        w = max(1, w - 1)          # last column belongs to the scrollbar
        sizew = 8 if details and w > 40 else 0
        datew = 17 if details and w > 60 else 0
        namew = w - sizew - datew - 1
        hdr = th.c('dim', 'raised')
        cv.fill(x, y, w, 1, hdr)
        for label, key, cx, cw in (('Name', 'name', x + 1, namew), ('Size', 'size', x + namew, sizew),
                                   ('Modified', 'date', x + namew + sizew + 1, datew)):
            if cw:
                a = th.c('accent', 'raised') if self.sort == key else hdr
                cv.put(cx, y, label + (' v' if self.sort == key else ''), a)
                app.zone(cx, y, cw, 1, ('sort', key))
        if self.error:
            cv.put(x + 1, y + 2, fit(self.error, w - 2), th.c('red', 'win'))
            return
        if not self.entries:
            cv.put(x + 1, y + 2, '(empty folder)', th.c('dim', 'win'))

        def row(cv2, rx, ry, rw, e, idx, sel):
            marked = e.path in self.marked
            if sel and focused:
                a = th.c('on_accent', 'accent')
                ia = a
            elif sel:
                a = th.c('bright', 'sel')
                ia = th.c(KIND_COLOR.get(e.kind, 'dim'), 'sel')
            elif marked:
                a = th.c('accent', 'raised')
                ia = a
            else:
                a = th.c('bright' if e.isdir else 'text', 'win')
                ia = th.c(KIND_COLOR.get(e.kind, 'dim'), 'win')
            cv2.fill(rx, ry, rw, 1, a)
            cv2.put(rx, ry, '+' if marked else ' ', a)
            cv2.put(rx + 1, ry, ICONS.get(e.kind, ' '), ia)
            nm = e.name + ('/' if e.isdir else '') + (' ->' if e.link else '')
            cv2.put(rx + 3, ry, fit(nm, namew - 4), a)
            if sizew:
                s = '' if e.isdir else human_size(e.size)
                cv2.put(rx + namew - 1, ry, s.rjust(sizew - 1), a if sel else th.c('dim', 'win') if not marked else a)
            if datew:
                d = datetime.datetime.fromtimestamp(e.mtime).strftime('%Y-%m-%d %H:%M') if e.mtime else ''
                cv2.put(rx + namew + sizew, ry, d, a if sel or marked else th.c('dim', 'win'))
        self.list.draw(cv, x, y + 1, w, h - 1, row, th)
        app.zone(x, y + 1, w, h - 1, 'list')

    def key(self, ev):
        k = ev.key
        if self.list.key(ev):
            return True
        if k == 'backspace' or k == 'alt+up':
            self.up()
        elif k == ' ':
            c = self.current()
            if c:
                self.marked ^= {c.path}
                self.list.select(min(len(self.entries) - 1, self.list.sel + 1))
        elif k == 'ctrl+a':
            self.marked = {e.path for e in self.entries}
        elif k == '.':
            self.hidden = not self.hidden
            self.refresh(keep=self.current().name if self.current() else None)
        elif ev.char and ev.char.isprintable():
            now = time.monotonic()
            self.typed = (self.typed if now - self.typed_t < 1.0 else '') + ev.char.lower()
            self.typed_t = now
            for i, e in enumerate(self.entries):
                if e.name.lower().startswith(self.typed):
                    self.list.select(i)
                    break
        else:
            return False
        return True


class Files(App):
    name = 'files'
    preserve_colors = True
    title = 'Files'
    icon = '[/]'
    size = (88, 26)
    min_size = (40, 10)

    def __init__(self, wm, win, path=None, **kw):
        super().__init__(wm, win)
        start = path or wm.cfg.get('files_dir') or HOME
        if not (os.path.isdir(start) or os.path.isfile(start)):
            start = HOME                       # the remembered folder is gone
        sel = None
        if os.path.isfile(start):
            start, sel = os.path.split(start)
        self.fl = FileList(self, start)
        self.rem_timer = None
        self.rem_dir = None
        self.remember_dir(self.fl.path)
        if sel:
            self.fl.chdir(start, sel)
        self.fl.list.on_activate = self.activate
        self.drives = []
        self.drives_key = None
        self.transfer = None
        self.refresh_drives()
        self.back_stack = []
        self.fwd_stack = []
        self.pathfield = None
        # mounted media live in DRIVES (mount/unmount), not twice in PLACES
        self.places = [(n, p) for n, p in places()
                       if not any(v['mount'] and media.inside(p, v['mount'])
                                  for v in self.drives)]
        self.preview_key = None
        self.preview = None
        self.update_title()
        self.every(3.0, self.poll)
        self.every(0.1, self.transfer_tick)
        self.mtime = self.dir_mtime()

    def remember_dir(self, path):
        """Keep the folder for the next start (debounced: arrow keys through the tree)."""
        self.rem_dir = os.path.abspath(path)
        if self.wm.cfg.get('files_dir') == self.rem_dir:
            return
        if self.rem_timer:
            self.rem_timer.cancel()
        self.rem_timer = self.later(0.5, self.save_dir)

    def save_dir(self):
        self.rem_timer = None
        if self.rem_dir and self.wm.cfg.get('files_dir') != self.rem_dir:
            self.wm.cfg['files_dir'] = self.rem_dir
            self.wm.cfg.save()

    def on_close(self):
        self.save_dir()
        return True

    def dir_mtime(self):
        try:
            return os.stat(self.fl.path).st_mtime
        except OSError:
            return 0

    def poll(self):
        self.refresh_drives()
        m = self.dir_mtime()
        if m != self.mtime:
            self.mtime = m
            c = self.fl.current()
            self.fl.refresh(keep=c.name if c else None)
            self.invalidate()

    # ------------------------------------------------------------ drives
    def refresh_drives(self):
        vols = media.snapshot(5.0)
        key = tuple((v['device'], v['mount']) for v in vols)
        if key != self.drives_key:
            self.drives_key = key
            self.drives = vols
            self.invalidate()

    def open_drive(self, i):
        if not 0 <= i < len(self.drives):
            return
        v = self.drives[i]
        if v['mount']:
            self.go(v['mount'])
        else:
            self.mount_drive(v)

    def mount_drive(self, v):
        self.wm.toast('Mounting %s...' % v['name'], 'accent', 3)

        def done(res):
            path, err = res
            self.refresh_drives()
            if path:
                self.go(path)
                self.wm.toast('Mounted %s at %s' % (v['name'], path), 'accent', 5)
            else:
                media.invalidate()
                self.wm.toast('Mount %s: %s' % (v['name'], err or 'failed'), 'red', 8)

        self.thread(lambda: media.mount(v['device']), done)

    def unmount_drive(self, v):
        def done(res):
            ok, err = res
            self.refresh_drives()
            if ok:
                if media.inside(self.fl.path, v['mount']):
                    self.go(HOME)
                self.wm.toast('Unmounted %s - safe to remove' % v['name'], 'accent', 5)
            else:
                self.wm.toast('Unmount %s: %s' % (v['name'], err or 'busy'), 'red', 8)

        self.thread(lambda: media.unmount(v['device']), done)

    def drive_menu(self, i, ev):
        if not 0 <= i < len(self.drives):
            return
        v = self.drives[i]
        items = []
        if v['mount']:
            items.append(MenuItem('Open', lambda: self.go(v['mount'])))
            items.append(MenuItem('Unmount', lambda: self.unmount_drive(v)))
        else:
            items.append(MenuItem('Mount', lambda: self.mount_drive(v)))
        self.wm.menu(items, self.win.x + 1 + ev.x, self.win.y + 1 + ev.y)
        self.invalidate()

    def update_title(self):
        self.set_title(os.path.basename(self.fl.path.rstrip('/')) or '/')

    def go(self, path, record=True, select=None):
        old = self.fl.path
        if self.fl.chdir(path, select):
            if record and old != self.fl.path:
                self.back_stack.append(old)
                self.fwd_stack = []
            self.mtime = self.dir_mtime()
            self.update_title()
        else:
            self.wm.toast('Not a folder: ' + path, 'red')

    def activate(self, e):
        if e is None:
            return
        if e.isdir:
            self.go(e.path)
        else:
            open_path(self.wm, e.path)

    # ------------------------------------------------------------ drawing
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        # toolbar
        x = 0
        for label, key in (('<', 'back'), ('>', 'fwd'), ('^', 'up')):
            x += button(self, cv, x, 0, label, key, enabled=(key != 'back' or bool(self.back_stack)) and
                        (key != 'fwd' or bool(self.fwd_stack))) + 1
        pw = w - x - 9
        if self.pathfield:
            self.pathfield.draw(cv, x, 0, pw, th, focused)
        else:
            a = th.c('text', 'field')
            cv.fill(x, 0, pw, 1, a)
            cv.put(x + 1, 0, shorten_path(self.fl.path, pw - 2), a)
        self.zone(x, 0, pw, 1, 'path')
        button(self, cv, w - 7, 0, '...', 'menu')
        side = 16 if w >= 70 else 0
        prev = w >= 110
        listw = w - side - (36 if prev else 0)
        if side:
            cv.fill(0, 2, side - 1, h - 3, th.c('text', 'win'))
            cv.put(1, 2, 'PLACES', th.c('dim', 'win'))
            for i, (name, p) in enumerate(self.places):
                yy = 3 + i
                if yy >= h - 1:
                    break
                on = self.fl.path == p
                a = th.c('accent', 'raised') if on else (th.c('bright', 'sel') if self.hover == ('place', i) else th.c('text', 'win'))
                cv.fill(0, yy, side - 1, 1, a)
                cv.put(1, yy, fit(name, side - 2), a)
                self.zone(0, yy, side - 1, 1, ('place', i))
            row = 3 + len(self.places)
            if self.drives and row + 1 < h - 1:
                row += 1
                cv.put(1, row, 'DRIVES', th.c('dim', 'win'))
                row += 1
                for i, v in enumerate(self.drives):
                    if row >= h - 1:
                        break
                    here = v['mount'] and media.inside(self.fl.path, v['mount'])
                    if here:
                        a = th.c('accent', 'raised')
                    elif self.hover == ('drive', i):
                        a = th.c('bright', 'sel')
                    else:
                        a = th.c('text' if v['mount'] else 'dim', 'win')
                    cv.fill(0, row, side - 1, 1, a)
                    cv.put(1, row, fit(v['name'], side - 2), a)
                    self.zone(0, row, side - 1, 1, ('drive', i))
                    row += 1
        self.fl.draw(cv, side, 2, listw, h - 3, focused)
        if prev:
            self.draw_preview(cv, side + listw + 1, 2, 35, h - 3)
        # status bar
        if self.transfer and self.transfer.active:
            self.draw_transfer(cv, w, h - 1)
            return
        n = len(self.fl.entries)
        sel = len(self.fl.marked)
        try:
            st = os.statvfs(self.fl.path)
            free = human_size(st.f_bavail * st.f_frsize) + ' free'
        except OSError:
            free = ''
        s = '%d items%s' % (n, ', %d marked' % sel if sel else '')
        cv.put(0, h - 1, s, th.c('dim', 'win'))
        cv.put(w - len(free), h - 1, free, th.c('dim', 'win'))

    def draw_transfer(self, cv, w, y):
        """Status line while a copy runs: counts, bar, current name and a Stop button."""
        th = self.th
        t = self.transfer
        cv.fill(0, y, w, 1, th.c('text', 'win'))
        stop = 6                                      # ' Stop '
        verb = 'Moving' if t.move else 'Copying'
        if t.state == 'planning':
            cv.put(0, y, fit('%s - preparing...' % verb, w - stop), th.c('text', 'win'))
            button(self, cv, w - stop, y, 'Stop', 'stop')
            return
        pct = int(t.fraction * 100 + 0.5)
        left = '%s %d/%d' % (verb, t.files_done, t.files_total)
        size = '%s/%s' % (human_size(t.done), human_size(t.total))
        right = '%d%%  %s' % (pct, size)
        middle = w - len(left) - len(right) - stop - 3
        cv.put(0, y, left, th.c('text', 'win'))
        if middle >= 8:
            hbar(cv, len(left) + 1, y, middle, t.fraction, th, bg='win')
        if middle >= 8:
            cv.put(w - stop - len(right) - 1, y, right, th.c('dim', 'win'))
        else:
            cv.put(len(left) + 1, y, fit('%d%%' % pct, w - len(left) - stop - 1),
                   th.c('dim', 'win'))
        button(self, cv, w - stop, y, 'Stop', 'stop')

    def draw_preview(self, cv, x, y, w, h):
        th = self.th
        e = self.fl.current()
        cv.fill(x, y, w, h, th.c('text', 'win'))
        if not e:
            return
        cv.put(x, y, fit(e.name, w), th.c('bright', 'win', 1))
        info = 'folder' if e.isdir else '%s  %s' % (human_size(e.size), e.kind)
        cv.put(x, y + 1, fit(info, w), th.c('dim', 'win'))
        key = (e.path, e.mtime, w, h, tuple(th.rgb))
        if key != self.preview_key:
            self.preview_key = key
            self.preview = None
            self.load_preview(e, w, h - 3, key)
        p = self.preview
        if p is None:
            return
        if p[0] == 'img':
            cells, cw, chh = p[1]
            cv.sub(x, y + 3, w, h - 3).blit_cells(0, 0, cells)
        else:
            for i, ln in enumerate(p[1][:h - 3]):
                cv.put(x, y + 3 + i, fit(ln, w), th.c('text', 'win'))

    def load_preview(self, e, w, h, key):
        from .. import imaging
        if e.isdir:
            try:
                names = sorted(os.listdir(e.path))[:h]
                self.preview = ('txt', [n for n in names if not n.startswith('.')])
            except OSError:
                pass
            return
        if e.kind == 'image':
            def work():
                img = imaging.load(e.path, 800)
                return img

            def done(img):
                if self.preview_key == key:
                    self.preview = ('img', imaging.render_fit(self.wm, img, w, h))
                    self.invalidate()
            self.thread(work, done, lambda err: None)
        elif e.kind in ('text', 'exec', 'web', 'sheet') and e.size < 5_000_000 and not looks_binary(e.path):
            try:
                with open(e.path, errors='replace') as f:
                    txt = f.read(4000)
                lines = []
                for ln in txt.replace('\t', '    ').split('\n'):
                    lines.extend(wrap_text(ln, w) or [''])
                self.preview = ('txt', lines)
            except OSError:
                pass

    # ------------------------------------------------------------ actions
    def menu_items(self, e):
        sel = self.fl.selection()
        has = bool(sel)
        items = []
        if e:
            items.append(MenuItem('Open', lambda: self.activate(e), 'Enter'))
            if not e.isdir:
                items.append(MenuItem('Open with...', lambda: self.open_with(e)))
            items.append(None)
        items += [
            MenuItem('Copy', lambda: self.clip('copy'), 'Ctrl+C', enabled=has),
            MenuItem('Cut', lambda: self.clip('cut'), 'Ctrl+X', enabled=has),
            MenuItem('Paste', self.paste, 'Ctrl+V', enabled=bool(getattr(self.wm, 'file_clip', None))),
            MenuItem('Rename', self.rename, 'F2', enabled=bool(e)),
            MenuItem('Move to trash', self.delete, 'Del', enabled=has),
            None,
            MenuItem('New folder', self.new_folder, 'F7'),
            MenuItem('New file', self.new_file),
            MenuItem('Terminal here', lambda: self.wm.open('terminal', cwd=self.fl.path)),
            MenuItem('Show hidden', self.toggle_hidden, '.', checked=self.fl.hidden),
            MenuItem('Properties', lambda: self.properties(e), enabled=bool(e)),
        ]
        return items

    def open_with(self, e):
        apps = [('Editor', 'editor'), ('Writer', 'writer'), ('Sheets', 'sheet'), ('PDF', 'pdf'),
                ('Pictures', 'viewer'), ('Music', 'music'), ('Video', 'video'), ('Browser', 'browser')]
        items = [MenuItem(label, (lambda a=a: open_path(self.wm, e.path, a))) for label, a in apps]
        items.append(MenuItem('Run in terminal', lambda: self.wm.open('terminal', cwd=self.fl.path,
                                                                       cmd=[e.path] if e.mode & 0o111 else ['xdg-open', e.path])))
        mx, my = self.wm.mouse_pos
        self.wm.menu(items, mx, my)

    def toggle_hidden(self):
        self.fl.hidden = not self.fl.hidden
        self.fl.refresh()

    def clip(self, mode):
        sel = self.fl.selection()
        self.wm.file_clip = (mode, [e.path for e in sel])
        self.wm.set_clipboard('\n'.join(e.path for e in sel))
        self.wm.toast('%s %d item(s)' % ('Copied' if mode == 'copy' else 'Cut', len(sel)))

    def paste(self):
        clip = getattr(self.wm, 'file_clip', None)
        if not clip:
            return
        if self.transfer and self.transfer.active:
            self.wm.toast('A transfer is still running', 'red')
            return
        mode, paths = clip
        t = transfer.Transfer(paths, self.fl.path, move=(mode == 'cut'))
        self.transfer = t
        self.invalidate()

        def work():
            t.plan()
            problem = t.space_problem()
            if problem:                        # do not waste minutes on a full card
                t.error = 'not enough space: %s' % problem
                t.state = 'error'
                return
            t.run()

        def done(_):
            self.transfer_done(t, mode)
        self.thread(work, done)

    def cancel_transfer(self):
        if self.transfer and self.transfer.active:
            self.transfer.cancel()
            self.wm.toast('Stopping...', 'accent', 2)

    def transfer_tick(self):
        if self.transfer and self.transfer.active:
            self.invalidate()

    def transfer_done(self, t, mode):
        if t.state == 'error':
            self.wm.toast('Paste failed: %s' % t.error, 'red', 8)
        elif t.state == 'cancelled':
            self.wm.toast('Cancelled - %s' % t.summary(), 'accent', 6)
        else:
            verb = 'Moved' if mode == 'cut' else 'Copied'
            extra = '' if not t.skipped else ' (%d skipped)' % t.skipped
            self.wm.toast('%s %s%s' % (verb, t.summary(), extra), 'green', 6)
            if mode == 'cut':
                self.wm.file_clip = None
        self.fl.refresh()
        self.invalidate()

    def rename(self):
        e = self.fl.current()
        if not e:
            return

        def ok(name):
            if name and name != e.name:
                try:
                    os.rename(e.path, os.path.join(self.fl.path, name))
                    self.fl.refresh(keep=name)
                except OSError as err:
                    self.wm.toast(str(err), 'red')
        self.wm.prompt('Rename', 'New name for "%s":' % e.name, e.name, ok, parent=self.win)

    def delete(self):
        sel = self.fl.selection()
        if not sel:
            return
        names = ', '.join(e.name for e in sel[:3]) + ('...' if len(sel) > 3 else '')

        def yes():
            def work():
                for e in sel:
                    trash(e.path)
                return len(sel)

            def ok(n):
                self.wm.toast('Moved %d item(s) to trash' % n)
                self.fl.refresh(keep=None)
                self.invalidate()
            self.thread(work, ok)
        self.wm.confirm('Move to trash', 'Move %d item(s) to the trash?\n%s' % (len(sel), names), yes,
                        yes='Trash', parent=self.win)

    def new_folder(self):
        def ok(name):
            if name:
                try:
                    os.mkdir(os.path.join(self.fl.path, name))
                    self.fl.refresh(keep=name)
                except OSError as err:
                    self.wm.toast(str(err), 'red')
        self.wm.prompt('New folder', 'Folder name:', 'New folder', ok, parent=self.win)

    def new_file(self):
        def ok(name):
            if name:
                p = os.path.join(self.fl.path, name)
                try:
                    open(p, 'a').close()
                    self.fl.refresh(keep=name)
                except OSError as err:
                    self.wm.toast(str(err), 'red')
        self.wm.prompt('New file', 'File name:', 'untitled.txt', ok, parent=self.win)

    def properties(self, e):
        if not e:
            return
        try:
            st = os.stat(e.path)
        except OSError as err:
            self.wm.toast(str(err), 'red')
            return
        size = human_size(st.st_size)
        if e.isdir:
            try:
                out = subprocess.run(['du', '-sh', e.path], capture_output=True, text=True, timeout=5).stdout
                size = out.split()[0] if out else size
            except Exception:
                pass
        import pwd
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        txt = ('Path: %s\nType: %s\nSize: %s\nModified: %s\nPermissions: %s\nOwner: %s' %
               (e.path, e.kind, size, datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                stat.filemode(st.st_mode), owner))
        self.wm.message('Properties', txt, parent=self.win)

    # ------------------------------------------------------------ input
    def on_click(self, key, ev):
        if key == 'back' and self.back_stack:
            self.fwd_stack.append(self.fl.path)
            self.go(self.back_stack.pop(), record=False)
        elif key == 'fwd' and self.fwd_stack:
            self.back_stack.append(self.fl.path)
            self.go(self.fwd_stack.pop(), record=False)
        elif key == 'up':
            self.go(os.path.dirname(self.fl.path.rstrip('/')) or '/')
        elif key == 'path':
            if not self.pathfield:
                self.pathfield = TextField(self.fl.path.replace(HOME, '~', 1) if self.fl.path.startswith(HOME) else self.fl.path,
                                           on_enter=self.path_enter)
        elif key == 'menu':
            mx, my = self.wm.mouse_pos
            self.wm.menu(self.menu_items(self.fl.current()), mx - 20, my + 1)
        elif key[0] == 'place':
            self.go(self.places[key[1]][1])
        elif key[0] == 'drive':
            self.open_drive(key[1])
        elif key == 'stop':
            self.cancel_transfer()
        elif key[0] == 'sort':
            self.fl.sort = key[1]
            self.fl.refresh()

    def path_enter(self, t):
        self.pathfield = None
        p = os.path.expanduser(t.strip())
        if os.path.isdir(p):
            self.go(p)
        elif os.path.isfile(p):
            self.go(os.path.dirname(p), select=os.path.basename(p))
        else:
            self.wm.toast('No such folder: ' + t, 'red')

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.fl.list.sb_grab is not None:
            self.fl.list.mouse(ev, 0)
            self.invalidate()
            return True
        if ev.kind == 'down' and self.zone_at(ev.x, ev.y) != 'path':
            self.pathfield = None
        k = self.zone_at(ev.x, ev.y)
        if (isinstance(k, tuple) and k[0] == 'drive' and ev.kind == 'down'
                and ev.button == 2):
            self.drive_menu(k[1], ev)
            return True
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown') and k is not None and k != 'path'):
            side = 16 if self.cw >= 70 else 0
            ry = ev.y - 3
            if ev.kind == 'down' and ev.button == 2:
                idx = self.fl.list.top + ry
                if 0 <= idx < len(self.fl.entries):
                    self.fl.list.select(idx)
                    e = self.fl.current()
                else:
                    e = None
                self.wm.menu(self.menu_items(e), self.win.x + 1 + ev.x, self.win.y + 1 + ev.y)
                self.invalidate()
                return True
            if ev.kind == 'down' and 'ctrl' in ev.mods:
                idx = self.fl.list.top + ry
                if 0 <= idx < len(self.fl.entries):
                    self.fl.marked ^= {self.fl.entries[idx].path}
                self.invalidate()
                return True
            if self.fl.list.mouse(ev, ry):
                self.invalidate()
                return True
        if ev.kind in ('wheelup', 'wheeldown'):
            self.fl.list.mouse(ev, 0)
            self.invalidate()
            return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if self.pathfield:
            if k == 'esc':
                self.pathfield = None
            else:
                self.pathfield.key(ev)
            self.invalidate()
            return True
        if k in ('enter', 'right') and self.fl.current():
            e = self.fl.current()
            if k == 'enter' or e.isdir:
                self.activate(e)
        elif k in ('left', 'backspace', 'alt+up'):
            self.fl.up()
            self.update_title()
        elif k == 'alt+left':
            self.on_click('back', None)
        elif k == 'alt+right':
            self.on_click('fwd', None)
        elif k == 'ctrl+l':
            self.on_click('path', None)
        elif k == 'f2':
            self.rename()
        elif k == 'delete':
            self.delete()
        elif k == 'f7':
            self.new_folder()
        elif k == 'ctrl+c':
            self.clip('copy')
        elif k == 'ctrl+x':
            self.clip('cut')
        elif k == 'ctrl+v':
            self.paste()
        elif k == 'f5' or k == 'ctrl+r':
            self.fl.refresh(keep=self.fl.current().name if self.fl.current() else None)
        elif k == 'menu' or k == 'f10':
            self.on_click('menu', None)
        elif self.fl.key(ev):
            pass
        else:
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        if self.pathfield:
            self.pathfield.insert(text.strip())
            self.invalidate()

    def reopen(self, path=None, **kw):
        if path:
            self.go(path)


class FileDialog(App):
    name = 'filedialog'
    title = 'Open'
    icon = '[/]'
    size = (70, 20)
    min_size = (40, 10)

    def __init__(self, wm, win, mode='open', start=None, on_ok=None, name='', dirs=False, exts=None, **kw):
        super().__init__(wm, win)
        self.mode = mode
        self.on_ok = on_ok
        self.dirs = dirs
        start = start or getattr(wm, 'last_dir', None) or HOME
        if os.path.isfile(start):
            name = name or os.path.basename(start)
            start = os.path.dirname(start)
        self.fl = FileList(self, start)
        self.fl.list.on_activate = self.activate
        self.field = TextField(name, placeholder='file name', on_enter=lambda t: self.ok())
        self.field.sel_all = bool(name)
        self.focus_field = mode == 'save'
        self.done = False

    def activate(self, e):
        if e.isdir:
            self.fl.chdir(e.path)
        else:
            self.field.set(e.name)
            self.ok()

    def ok(self):
        name = self.field.text.strip()
        if self.dirs and not name:
            path = self.fl.path
        elif not name:
            e = self.fl.current()
            if e and e.isdir:
                self.fl.chdir(e.path)
                return
            if not e:
                return
            path = e.path
        elif name.startswith(('/', '~')):
            path = os.path.expanduser(name)
        else:
            path = os.path.join(self.fl.path, os.path.expanduser(name))
        if os.path.isdir(path) and not self.dirs:
            self.fl.chdir(path)
            self.field.set('')
            return
        if self.mode == 'open' and not os.path.exists(path):
            self.wm.toast('File not found', 'red')
            return
        if self.mode == 'save' and os.path.exists(path) and not getattr(self, '_confirmed', False):
            def yes():
                self._confirmed = True
                self.ok()
            self.wm.confirm('Overwrite', '"%s" exists. Replace it?' % os.path.basename(path), yes,
                            yes='Replace', parent=self.win)
            return
        self.wm.last_dir = os.path.dirname(path)
        self.done = True
        self.close()
        if self.on_ok:
            self.on_ok(path)

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        button(self, cv, 0, 0, '^', 'up')
        cv.put(4, 0, fit(shorten_path(self.fl.path, w - 4), w - 4), th.c('text', 'win'))
        self.fl.draw(cv, 0, 2, w, h - 5, focused and not self.focus_field, details=w > 50)
        cv.put(0, h - 2, 'Name', th.c('dim', 'win'))
        self.field.draw(cv, 5, h - 2, w - 5, th, focused and self.focus_field)
        self.zone(5, h - 2, w - 5, 1, 'field')
        x = w - 1
        for label, key, prim in (('Cancel', 'cancel', False), ('Open' if self.mode == 'open' else 'Save', 'ok', True)):
            x -= len(label) + 3
            button(self, cv, x, h - 1, label, key, primary=prim)
        if self.dirs:
            button(self, cv, 0, h - 1, 'Use this folder', 'usedir')

    def on_click(self, key, ev):
        if key == 'up':
            self.fl.up()
        elif key == 'field':
            self.focus_field = True
            self.field.click(ev.x - 5)
        elif key == 'ok':
            self.ok()
        elif key == 'usedir':
            self.field.set('')
            self.ok()
        elif key == 'cancel':
            self.close()
        elif key[0] == 'sort':
            self.fl.sort = key[1]
            self.fl.refresh()

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.fl.list.sb_grab is not None:
            self.fl.list.mouse(ev, 0)
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if k == 'list' or (ev.kind in ('wheelup', 'wheeldown')):
            if ev.kind == 'down':
                self.focus_field = False
            if self.fl.list.mouse(ev, ev.y - 3):
                c = self.fl.current()
                if c and not c.isdir and self.mode == 'save' and ev.kind == 'down':
                    self.field.set(c.name)
                self.invalidate()
                return True
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if ev.char and ev.char in ('/', '~') and not self.focus_field:
            self.focus_field = True
            self.field.set('')
            self.field.insert(ev.char)
            self.invalidate()
            return True
        if k == 'tab':
            self.focus_field = not self.focus_field
        elif k == 'esc':
            self.close()
        elif self.focus_field:
            if k in ('up', 'down'):
                self.focus_field = False
                self.fl.key(ev)
            else:
                self.field.key(ev)
        elif k == 'enter':
            e = self.fl.current()
            if e:
                self.activate(e)
        elif k in ('backspace', 'left'):
            self.fl.up()
        elif k == 'right':
            e = self.fl.current()
            if e and e.isdir:
                self.fl.chdir(e.path)
        elif not self.fl.key(ev):
            return False
        self.invalidate()
        return True

    def on_paste(self, text):
        self.field.insert(text.strip())
        self.focus_field = True
        self.invalidate()


APP = Files
