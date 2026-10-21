"""Text editor with line numbers, find/replace, undo and light syntax highlighting."""
import os
import re
import subprocess

from ..app import App
from ..textarea import TextArea
from ..util import fit, shorten_path
from ..widgets import TextField, button

KEYWORDS = {
    'py': r'\b(def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|'
          r'raise|pass|break|continue|lambda|yield|None|True|False|self|is|global|nonlocal|async|await|del)\b',
    'c': r'\b(int|char|void|float|double|long|short|unsigned|signed|struct|union|enum|typedef|static|const|'
         r'return|if|else|for|while|do|switch|case|break|continue|default|sizeof|include|define|function|var|'
         r'let|const|class|new|this|null|true|false|import|export|fn|pub|impl|use|mut|match|struct)\b',
    'sh': r'\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|in|return|export|local|echo|exit)\b',
}
EXT_LANG = {'.py': 'py', '.c': 'c', '.h': 'c', '.cpp': 'c', '.js': 'c', '.ts': 'c', '.rs': 'c', '.go': 'c',
            '.java': 'c', '.sh': 'sh', '.bash': 'sh', '.zsh': 'sh', '.json': 'c', '.gd': 'py', '.lua': 'c',
            '.md': 'md', '.toml': 'conf', '.ini': 'conf', '.conf': 'conf', '.cfg': 'conf', '.yml': 'conf',
            '.yaml': 'conf', '.desktop': 'conf', '.service': 'conf'}
STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
NUM_RE = re.compile(r'\b\d+(?:\.\d+)?\b')


class Editor(App):
    name = 'editor'
    title = 'Editor'
    icon = '[=]'
    size = (84, 26)
    min_size = (30, 6)
    shift_select = True

    def __init__(self, wm, win, path=None, text=None, pdf=False, **kw):
        super().__init__(wm, win)
        self.ta = TextArea(gutter=True)
        self.ta.scrollbar = True
        self.path = None
        self.lang = None
        self.findbar = None      # TextField when open
        self.replbar = None
        self.find_focus = 0
        self.status = ''
        if path:
            self.load(path, pdf)
        elif text:
            self.ta.set_text(text)
        self.update_title()

    def update_title(self):
        name = os.path.basename(self.path) if self.path else 'untitled'
        self.set_title(('* ' if self.ta.modified else '') + name)

    def load(self, path, pdf=False):
        try:
            if pdf:
                txt = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True,
                                     timeout=30).stdout
                self.ta.read_only = True
            else:
                with open(path, 'rb') as f:
                    data = f.read()
                try:
                    txt = data.decode('utf-8')
                except UnicodeDecodeError:
                    txt = data.decode('latin-1')
            txt = txt.replace('\r\n', '\n')
            self.ta.set_text(txt)
            self.path = path
            ext = os.path.splitext(path)[1].lower()
            self.lang = EXT_LANG.get(ext)
            if not self.lang and txt.startswith('#!'):
                self.lang = 'py' if 'python' in txt.split('\n')[0] else 'sh'
            self.ta.style = self.make_style()
            self.status = '%d lines' % len(self.ta.lines) + ('  (read only)' if pdf else '')
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def make_style(self):
        th = self.th
        lang = self.lang
        if not lang:
            return None
        kw = re.compile(KEYWORDS[lang]) if lang in KEYWORDS else None
        a_kw = th.c('accent', 'win')
        a_str = th.c('green', 'win')
        a_com = th.c('dim', 'win')
        a_num = th.c('magenta', 'win')
        a_head = th.c('accent', 'win', 1)
        a_key = th.c('cyan', 'win')
        comment = '#' if lang in ('py', 'sh', 'conf') else '//'

        def style(i, line):
            out = []
            if lang == 'md':
                if line.startswith('#'):
                    return [(0, len(line), a_head)]
                for m in re.finditer(r'\*\*[^*]+\*\*|`[^`]+`', line):
                    out.append((m.start(), m.end(), a_key))
                return out
            if lang == 'conf':
                s = line.lstrip()
                if s.startswith(('#', ';')):
                    return [(0, len(line), a_com)]
                if s.startswith('['):
                    return [(0, len(line), a_head)]
                m = re.match(r'\s*([\w.\-]+)\s*[=:]', line)
                if m:
                    out.append((m.start(1), m.end(1), a_key))
                return out
            if kw:
                for m in kw.finditer(line):
                    out.append((m.start(), m.end(), a_kw))
            for m in NUM_RE.finditer(line):
                out.append((m.start(), m.end(), a_num))
            strs = [(m.start(), m.end()) for m in STR_RE.finditer(line)]
            for s0, s1 in strs:
                out.append((s0, s1, a_str))
            ci = line.find(comment)
            while ci >= 0 and any(s0 < ci < s1 for s0, s1 in strs):
                ci = line.find(comment, ci + 1)
            if ci >= 0:
                out.append((ci, len(line), a_com))
            return out
        return style

    def on_theme(self):
        self.ta.style = self.make_style()

    # ------------------------------------------------------------ file ops
    def save(self, path=None, done=None):
        path = path or self.path
        if not path:
            return self.save_as(done)
        try:
            tmp = path + '.tw-tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(self.ta.text())
            if os.path.exists(path):
                os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            os.replace(tmp, path)
            self.path = path
            self.ta.modified = False
            self.lang = EXT_LANG.get(os.path.splitext(path)[1].lower(), self.lang)
            self.ta.style = self.make_style()
            self.wm.toast('Saved ' + shorten_path(path, 50), 'green')
            self.update_title()
            if done:
                done()
        except OSError as e:
            self.wm.toast(str(e), 'red')

    def save_as(self, done=None):
        self.wm.pick_file(lambda p: self.save(p, done), 'save', start=self.path, name='' if self.path else 'untitled.txt',
                          parent=self.win)

    def open_file(self):
        def ok(p):
            if self.ta.modified or self.path:
                self.wm.open('editor', path=p)
            else:
                self.load(p)
                self.update_title()
        self.wm.pick_file(ok, 'open', start=self.path, parent=self.win)

    def on_close(self):
        if not self.ta.modified:
            return True

        def res(r, v):
            if r == 'Save':
                self.save(done=lambda: self.wm.close(self.win, force=True))
            elif r == 'Discard':
                self.wm.close(self.win, force=True)
        self.wm.dialog('Unsaved changes', 'Save changes to %s?' % (os.path.basename(self.path) if self.path else 'untitled'),
                       ('Save', 'Discard', 'Cancel'), res, parent=self.win)
        return False

    # ------------------------------------------------------------ draw
    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        x = 0
        for label, key in (('New', 'new'), ('Open', 'open'), ('Save', 'save'), ('Save as', 'saveas'),
                           ('Find', 'find'), ('Undo', 'undo')):
            if x + len(label) + 3 > w:
                break
            x += button(self, cv, x, 0, label, key) + 1
        top = 2
        bottom = h - 1
        if self.findbar:
            fy = h - 2
            bottom = fy
            cv.fill(0, fy, w, 1, th.c('text', 'raised'))
            cv.put(1, fy, 'Find', th.c('dim', 'raised'))
            fw = (w - 30) // 2
            self.findbar.draw(cv, 6, fy, fw, th, focused and self.find_focus == 0)
            self.zone(6, fy, fw, 1, ('ff', 0))
            cv.put(7 + fw, fy, 'Replace', th.c('dim', 'raised'))
            self.replbar.draw(cv, 15 + fw, fy, fw, th, focused and self.find_focus == 1)
            self.zone(15 + fw, fy, fw, 1, ('ff', 1))
            bx = 16 + 2 * fw
            bx += button(self, cv, bx, fy, 'Next', 'fnext') + 1
            button(self, cv, bx, fy, 'All', 'frepl')
        self.ta.draw(cv, 0, top, w, bottom - top, th, focused and not self.findbar)
        self.zone(0, top, w, bottom - top, 'text')
        # status
        st = 'Ln %d, Col %d' % (self.ta.cy + 1, self.ta.cx + 1)
        sel = self.ta.selected_text()
        if sel:
            st += '  (%d selected)' % len(sel)
        left = shorten_path(self.path, max(10, w - len(st) - 20)) if self.path else 'untitled'
        cv.put(0, h - 1, fit(left + ('  ' + self.status if self.status else ''), w - len(st) - 2), th.c('dim', 'win'))
        cv.put(w - len(st), h - 1, st, th.c('text', 'win'))

    # ------------------------------------------------------------ input
    def toggle_find(self):
        if self.findbar:
            self.findbar = None
        else:
            sel = self.ta.selected_text()
            self.findbar = TextField(sel if '\n' not in sel else '', placeholder='search',
                                     on_enter=lambda t: self.find_next())
            self.replbar = TextField(placeholder='replace with', on_enter=lambda t: self.replace_all())
            self.find_focus = 0

    def find_next(self, forward=True):
        t = self.findbar.text if self.findbar else self.ta.find_text
        if t and not self.ta.find(t, forward):
            self.wm.toast('Not found: ' + t)
        self.ta.ensure_visible()

    def replace_all(self):
        n = self.ta.replace_all(self.findbar.text, self.replbar.text)
        self.wm.toast('Replaced %d occurrence(s)' % n)
        self.update_title()

    def on_click(self, key, ev):
        if key == 'new':
            self.wm.open('editor')
        elif key == 'open':
            self.open_file()
        elif key == 'save':
            self.save()
        elif key == 'saveas':
            self.save_as()
        elif key == 'find':
            self.toggle_find()
        elif key == 'undo':
            self.ta.undo()
            self.update_title()
        elif key == 'fnext':
            self.find_next()
        elif key == 'frepl':
            self.replace_all()
        elif key[0] == 'ff':
            self.find_focus = key[1]

    def on_mouse(self, ev):
        if ev.kind == 'up' and self.ta.sb_grab is not None:
            self.ta.mouse(ev, ev.x, ev.y - 2)
            self.invalidate()
            return True
        k = self.zone_at(ev.x, ev.y)
        if k == 'text' or (ev.kind == 'drag' and self.pressed == 'text') or ev.kind in ('wheelup', 'wheeldown'):
            if ev.kind == 'down':
                self.pressed = 'text'
                self.find_focus = 2 if self.findbar else 0
            if self.ta.mouse(ev, ev.x, ev.y - 2):
                if ev.kind != 'drag':
                    pass
                self.invalidate()
                if ev.kind in ('down', 'drag'):
                    self.ta.ensure_visible() if ev.kind == 'drag' else None
                return True
        if ev.kind == 'up':
            self.pressed = None
        return super().on_mouse(ev)

    def on_key(self, ev):
        k = ev.key
        if k == 'ctrl+s':
            self.save()
        elif k == 'ctrl+shift+s':
            self.save_as()
        elif k == 'ctrl+o':
            self.open_file()
        elif k == 'ctrl+n':
            self.wm.open('editor')
        elif k == 'ctrl+f':
            self.toggle_find()
        elif k == 'f3':
            self.find_next()
        elif k == 'shift+f3':
            self.find_next(False)
        elif k == 'ctrl+g':
            def go(t):
                try:
                    self.ta.cy = max(0, min(len(self.ta.lines) - 1, int(t) - 1))
                    self.ta.cx = 0
                    self.ta.anchor = None
                    self.ta.ensure_visible()
                except ValueError:
                    pass
            self.wm.prompt('Go to line', 'Line number:', '', go, parent=self.win)
        elif k == 'ctrl+c':
            t = self.ta.selected_text()
            if t:
                self.wm.set_clipboard(t)
        elif k == 'ctrl+x':
            t = self.ta.selected_text()
            if t:
                self.wm.set_clipboard(t)
                self.ta.snapshot('other')
                self.ta.delete_selection()
        elif k == 'ctrl+v':
            if self.wm.clipboard:
                self.ta.insert(self.wm.clipboard, 'other')
        elif self.findbar and self.find_focus < 2:
            if k == 'esc':
                self.findbar = None
            elif k == 'tab':
                self.find_focus = 1 - self.find_focus
            else:
                (self.findbar if self.find_focus == 0 else self.replbar).key(ev)
        elif k == 'esc' and self.findbar:
            self.findbar = None
        elif self.ta.key(ev):
            self.ta.ensure_visible()
        else:
            return False
        self.update_title()
        self.invalidate()
        return True

    def selection_text(self):
        return self.ta.selected_text() or ''

    def on_paste(self, text):
        if self.findbar and self.find_focus < 2:
            (self.findbar if self.find_focus == 0 else self.replbar).insert(text)
        else:
            self.ta.insert(text, 'other')
            self.ta.ensure_visible()
        self.update_title()
        self.invalidate()


APP = Editor
