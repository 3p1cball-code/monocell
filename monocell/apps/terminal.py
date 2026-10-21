"""Terminal emulator window: a pty running your shell, rendered through monocell's VT parser."""
import fcntl
import os
import pty
import signal
import struct
import termios

from ..app import App
from ..util import which
from ..vt import DEF, F_BOLD, F_DIM, F_INV, F_REV, F_UL, VTScreen

ANSI_ROLES = ['shadow', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'text',
              'dim', 'red', 'green', 'accent2', 'blue', 'magenta', 'cyan', 'bright']


class Terminal(App):
    name = 'terminal'
    title = 'Terminal'
    icon = '>_ '
    size = (84, 24)
    min_size = (20, 4)
    shift_select = True

    def __init__(self, wm, win, cmd=None, cwd=None, title=None, close_on_exit=True, env=None, **kw):
        super().__init__(wm, win)
        cols, rows = max(10, win.cw), max(3, win.ch)
        self.vt = VTScreen(cols, rows, scrollback=3000, reply=self.write)
        self.scroll = 0
        self.sel = None           # ((line, col), (line, col)) in absolute coords
        self.selecting = False
        self.exited = None
        self.close_on_exit = close_on_exit
        self.fixed_title = title
        self.attr_cache = {}
        shell = cmd or [os.environ.get('SHELL') or which('bash') or '/bin/sh']
        self.cmd = shell
        pid, fd = pty.fork()
        if pid == 0:
            try:
                if cwd:
                    os.chdir(cwd)
                e = dict(os.environ)
                e.update({'TERM': 'xterm-256color', 'COLUMNS': str(cols), 'LINES': str(rows),
                          'MONOCELL': '1'})
                e.pop('COLORTERM', None)
                if env:
                    e.update(env)
                os.execvpe(shell[0], shell, e)
            except Exception as ex:
                os.write(2, ('monocell: cannot run %s: %s\n' % (shell[0], ex)).encode())
            os._exit(127)
        self.pid, self.fd = pid, fd
        os.set_blocking(fd, False)
        self.set_winsize(cols, rows)
        wm.loop.add_reader(fd, self.on_output)
        self.decoder = __import__('codecs').getincrementaldecoder('utf-8')('replace')
        if title:
            self.set_title(title)

    def proc_info(self):
        return 'pid %d %s' % (self.pid, ' '.join(self.cmd)[:30]) if not self.exited else 'exited'

    def set_winsize(self, cols, rows):
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass

    def write(self, s):
        if self.exited is not None:
            return
        data = s.encode('utf-8')
        while data:
            try:
                n = os.write(self.fd, data)
                data = data[n:]
            except BlockingIOError:
                import time
                time.sleep(0.005)
            except OSError:
                break

    def on_output(self, fd):
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self.child_exit()
            return
        self.vt.feed(self.decoder.decode(data))
        if self.vt.title and not self.fixed_title:
            self.win.title = self.vt.title[:60]
        if self.vt.bell:
            self.vt.bell = False
            if self.wm.focus is not self.win:
                self.win.urgent = True
        self.invalidate()

    def child_exit(self):
        self.wm.loop.remove_reader(self.fd)
        status = 0
        try:
            _, st = os.waitpid(self.pid, 0)
            status = os.waitstatus_to_exitcode(st)
        except ChildProcessError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.exited = status
        if self.close_on_exit and status == 0:
            self.wm.close(self.win, force=True)
        else:
            self.vt.feed('\r\n\x1b[0;2m[process exited with code %d - press any key to close]\x1b[0m' % status)
            self.invalidate()

    def on_close(self):
        if self.exited is None:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except OSError:
                    pass
            self.wm.loop.remove_reader(self.fd)
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                pass
        return True

    def on_resize(self, w, h):
        w, h = max(2, w), max(1, h)
        if (w, h) != (self.vt.cols, self.vt.rows):
            self.vt.resize(w, h)
            if self.exited is None:
                self.set_winsize(w, h)
                try:
                    os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
                except OSError:
                    pass

    def on_theme(self):
        self.attr_cache = {}

    def attr(self, a):
        r = self.attr_cache.get(a)
        if r is not None:
            return r
        th = self.th
        fg, bg, fl = a & 31, (a >> 5) & 31, a >> 10
        f = th.r['text'] if fg == DEF else th.r[ANSI_ROLES[fg]]
        b = th.r['win'] if bg == DEF else th.r[ANSI_ROLES[bg]]
        if fl & F_BOLD and fg == DEF:
            f = th.r['bright']
        if fl & F_DIM:
            f = th.r['dim']
        if fl & F_REV:
            f, b = b, f
        if fl & F_INV:
            f = b
        out = f | (b << 4) | ((1 if fl & F_BOLD else 0) | (2 if fl & F_UL else 0)) << 8
        self.attr_cache[a] = out
        return out

    def all_lines(self):
        return self.vt.history + self.vt.lines

    def draw(self, cv, focused):
        vt = self.vt
        hist = vt.history if vt.alt_lines is None else []
        total = len(hist) + vt.rows
        self.scroll = max(0, min(self.scroll, len(hist)))
        start = total - vt.rows - self.scroll
        attr = self.attr
        sel = self.norm_sel()
        sel_a = self.th.c('bright', 'sel')
        for i in range(min(cv.h, vt.rows)):
            li = start + i
            ln = hist[li] if li < len(hist) else vt.lines[li - len(hist)]
            chars, attrs = ln
            n = min(cv.w, len(chars))
            # run-length draw
            row_attrs = [attr(a) for a in attrs[:n]]
            cv.blit_cells(0, i, [(''.join(chars[:n]), row_attrs)])
            if sel:
                (l0, c0), (l1, c1) = sel
                if l0 <= li <= l1:
                    s0 = c0 if li == l0 else 0
                    s1 = c1 if li == l1 else n
                    if s1 > s0:
                        cv.set_attr(s0, i, s1 - s0, 1, sel_a)
        if self.scroll:
            tag = ' scrollback %d/%d ' % (self.scroll, len(hist))
            cv.put(cv.w - len(tag), 0, tag, self.th.c('on_accent', 'accent'))
        elif focused and vt.cursor_visible and self.exited is None:
            cx, cy = vt.cx, vt.cy
            if 0 <= cx < cv.w and 0 <= cy < cv.h:
                ch = vt.lines[cy][0][cx]
                cv.put(cx, cy, ch, self.th.c('on_accent', 'accent'))

    def norm_sel(self):
        if not self.sel:
            return None
        a, b = self.sel
        return (a, b) if a <= b else (b, a)

    def abs_pos(self, x, y):
        hist = self.vt.history if self.vt.alt_lines is None else []
        total = len(hist) + self.vt.rows
        return (total - self.vt.rows - self.scroll + y, max(0, x))

    def selected_text(self):
        s = self.norm_sel()
        if not s:
            return ''
        (l0, c0), (l1, c1) = s
        lines = self.all_lines() if self.vt.alt_lines is None else self.vt.lines
        out = []
        for li in range(l0, l1 + 1):
            if 0 <= li < len(lines):
                t = ''.join(lines[li][0])
                a = c0 if li == l0 else 0
                b = c1 if li == l1 else len(t)
                out.append(t[a:b].rstrip())
        return '\n'.join(out)

    def selection_text(self):
        return self.selected_text() if self.sel else ''

    def on_key(self, ev):
        if self.exited is not None:
            self.wm.close(self.win, force=True)
            return True
        k = ev.key
        if k == 'shift+pgup':
            self.scroll += self.vt.rows // 2
        elif k == 'shift+pgdn':
            self.scroll = max(0, self.scroll - self.vt.rows // 2)
        elif k in ('ctrl+shift+c', 'ctrl+insert') or (k == 'ctrl+c' and self.sel):
            t = self.selected_text()
            if t:
                self.wm.set_clipboard(t)
                self.wm.toast('Copied')
            self.sel = None
        elif k in ('ctrl+shift+v', 'shift+insert', 'ctrl+v'):
            self.on_paste(self.wm.clipboard)
        else:
            s = self.vt.encode_key(k, ev.char)
            if s:
                self.scroll = 0
                self.sel = None
                self.write(s)
        self.invalidate()
        return True

    def on_paste(self, text):
        if not text or self.exited is not None:
            return
        self.scroll = 0
        text = text.replace('\n', '\r')
        if self.vt.bracketed_paste:
            text = '\x1b[200~' + text + '\x1b[201~'
        self.write(text)

    def on_mouse(self, ev):
        vt = self.vt
        if vt.mouse_mode and 'shift' not in ev.mods and self.exited is None and not self.scroll:
            if 0 <= ev.x < vt.cols and 0 <= ev.y < vt.rows or ev.kind in ('up', 'drag'):
                s = vt.encode_mouse(ev.kind, max(0, min(vt.cols - 1, ev.x)), max(0, min(vt.rows - 1, ev.y)), ev.button)
                if s:
                    self.write(s)
            return True
        if ev.kind == 'wheelup':
            if vt.alt_lines is not None:
                self.write(vt.encode_key('up') * 3)
            else:
                self.scroll += 3
        elif ev.kind == 'wheeldown':
            if vt.alt_lines is not None:
                self.write(vt.encode_key('down') * 3)
            else:
                self.scroll = max(0, self.scroll - 3)
        elif ev.kind == 'down' and ev.button == 0:
            p = self.abs_pos(ev.x, ev.y)
            if ev.clicks >= 2:
                lines = self.all_lines() if vt.alt_lines is None else vt.lines
                if 0 <= p[0] < len(lines):
                    t = ''.join(lines[p[0]][0])
                    a = b = min(p[1], len(t) - 1)
                    while a > 0 and not t[a - 1].isspace():
                        a -= 1
                    while b < len(t) and not t[b].isspace():
                        b += 1
                    self.sel = ((p[0], a), (p[0], b))
                    self.wm.set_clipboard(self.selected_text())
            else:
                self.sel = (p, p)
                self.selecting = True
        elif ev.kind == 'drag' and self.selecting:
            if ev.y < 0:
                self.scroll += 1
            elif ev.y >= vt.rows:
                self.scroll = max(0, self.scroll - 1)
            self.sel = (self.sel[0], self.abs_pos(min(vt.cols, ev.x + 1), max(0, min(vt.rows - 1, ev.y))))
        elif ev.kind == 'up' and ev.button == 0 and self.selecting:
            self.selecting = False
            if self.sel and self.sel[0] != self.sel[1]:
                t = self.selected_text()
                if t:
                    self.wm.set_clipboard(t)
            else:
                self.sel = None
        elif ev.kind == 'down' and ev.button == 1:
            self.on_paste(self.wm.clipboard)
        else:
            return False
        self.invalidate()
        return True


APP = Terminal
