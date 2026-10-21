#!/usr/bin/env python3
"""Headless test harness: run monocell in a pty of a given size, drive it with a
script of keys/mouse actions and save PNG screenshots rendered from the
emulated screen (monocell's own VT parser, palette taken from the OSC 4 stream).

usage: snap.py COLSxROWS "script" [--args "--open files"] [--out DIR] [--font PATH] [--px 16]

script: actions separated by ';'
  wait S | key NAME | type TEXT | click X Y | rclick X Y | dclick X Y
  drag X1 Y1 X2 Y2 | move X Y | wheel up|down X Y | shot NAME | paste TEXT
"""
import argparse
import os
import pty
import select
import shlex
import signal
import struct
import fcntl
import sys
import termios
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from monocell.vt import VTScreen, DEF, F_UL, F_REV  # noqa: E402
from monocell.theme import PRESETS, hex_rgb  # noqa: E402

FONT = '/usr/share/fonts/nerd-fonts/Terminus/TerminessNerdFontMono-Regular.ttf'


class Runner:
    def __init__(self, cols, rows, args, env_extra=None):
        self.cols, self.rows = cols, rows
        self.vt = VTScreen(cols, rows, reply=self.reply)
        self.vt.rgb_palette = [hex_rgb(h) for h in PRESETS['Ember']]
        self.vt.keep_unicode = True
        self.lock = threading.Lock()
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ)
            env.update({'TERM': 'xterm-256color', 'MONOCELL_COLOR': os.environ.get('SNAP_COLOR', 'palette'),
                        'COLORTERM': 'truecolor' if os.environ.get('SNAP_COLOR') == 'truecolor' else '', 'MONOCELL_NOAUDIO': '' if os.environ.get('SNAP_AUDIO') else '1',
                        'PYTHONPATH': os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))})
            env.update(env_extra or {})
            os.chdir(os.path.expanduser('~'))
            os.execvpe(sys.executable, [sys.executable, '-m', 'monocell'] + args, env)
        self.pid, self.fd = pid, fd
        self.set_size(cols, rows)
        self.alive = True
        threading.Thread(target=self.pump, daemon=True).start()

    def set_size(self, cols, rows):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, cols * 8, rows * 16))
        with self.lock:
            self.vt.resize(cols, rows)
        self.cols, self.rows = cols, rows
        try:
            os.kill(self.pid, signal.SIGWINCH)
        except OSError:
            pass

    def reply(self, s):
        pass

    def pump(self):
        while self.alive:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    self.alive = False
                    break
                if not data:
                    self.alive = False
                    break
                with self.lock:
                    self.vt.feed(data.decode('utf-8', 'replace'))

    def send(self, s):
        os.write(self.fd, s.encode())

    def mouse(self, kind, x, y, b=0):
        if kind == 'down':
            self.send('\x1b[<%d;%d;%dM' % (b, x + 1, y + 1))
        elif kind == 'up':
            self.send('\x1b[<%d;%d;%dm' % (b, x + 1, y + 1))
        elif kind == 'drag':
            self.send('\x1b[<%d;%d;%dM' % (32 + b, x + 1, y + 1))
        elif kind == 'move':
            self.send('\x1b[<35;%d;%dM' % (x + 1, y + 1))
        elif kind == 'wheelup':
            self.send('\x1b[<64;%d;%dM' % (x + 1, y + 1))
        elif kind == 'wheeldown':
            self.send('\x1b[<65;%d;%dM' % (x + 1, y + 1))

    def stop(self):
        self.alive = False
        try:
            os.kill(self.pid, signal.SIGTERM)
            time.sleep(0.3)
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass


FALLBACKS = ['/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
             '/usr/share/fonts/google-noto-vf/NotoSansSymbols[wght].ttf',
             '/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc',
             '/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-Regular.ttf']


def render(vt, path, font_path=FONT, px=16):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(font_path, px)
    fonts = [(font, px)]
    try:
        nd = bytes(font.getmask('\uffff'))
        for fp in FALLBACKS:
            try:
                f2 = ImageFont.truetype(fp, px)
            except OSError:
                continue
            if bytes(f2.getmask('\uffff')) != nd or True:
                fonts.append((f2, px))
        notdef = nd
    except Exception:
        notdef = None
    glyph_cache = {}

    def pick(ch):
        r = glyph_cache.get(ch)
        if r is not None:
            return r
        if notdef is None or ch.isascii():
            res = fonts[0]
        else:
            res = fonts[0]
            for f2, sz in fonts[1:]:
                try:
                    m = bytes(f2.getmask(ch))
                except Exception:
                    continue
                if m != bytes(f2.getmask('\uffff')):
                    res = (f2, sz)
                    break
        glyph_cache[ch] = res
        return res
    bbox = font.getbbox('M')
    cw = int(round(font.getlength('M')))
    chh = int(px * 1.0) + 1
    pal = [hex_rgb(h) for h in PRESETS['Ember']]
    for i, c in vt.palette.items():
        if 0 <= i < 16:
            pal[i] = c
    img = Image.new('RGB', (vt.cols * cw, vt.rows * chh), pal[0])
    d = ImageDraw.Draw(img)
    for y, (chars, attrs) in enumerate(vt.lines):
        for x in range(vt.cols):
            a = attrs[x]
            fg, bg, fl = a & 31, (a >> 5) & 31, a >> 10
            fg = 12 if fg == DEF else fg
            bg = 0 if bg == DEF else bg
            if fl & F_REV:
                fg, bg = bg, fg
            x0, y0 = x * cw, y * chh
            d.rectangle([x0, y0, x0 + cw - 1, y0 + chh - 1], fill=pal[bg])
            c = chars[x]
            if c != ' ':
                if c.isascii():
                    d.text((x0, y0), c, font=font, fill=pal[fg])
                else:
                    f2, sz = pick(c)
                    if f2 is font:
                        d.text((x0, y0), c, font=font, fill=pal[fg])
                    else:
                        try:
                            scaled = ImageFont.truetype(f2.path, max(8, int(sz * cw / max(1, f2.getlength('M')))))
                        except Exception:
                            scaled = f2
                        bb = scaled.getbbox(c)
                        dx = x0 + max(0, (cw - scaled.getlength(c)) // 2)
                        d.text((dx, y0 + (chh - (bb[3] - bb[1])) // 2 - bb[1]), c, font=scaled, fill=pal[fg])
            if fl & F_UL:
                d.line([x0, y0 + chh - 2, x0 + cw - 1, y0 + chh - 2], fill=pal[fg])
    img.save(path)
    return path


def text_dump(vt):
    return '\n'.join(''.join(ch) for ch, _ in vt.lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('size')
    ap.add_argument('script')
    ap.add_argument('--args', default='')
    ap.add_argument('--out', default='/tmp/opencode/shots')
    ap.add_argument('--prefix', default='')
    ap.add_argument('--font', default=FONT)
    ap.add_argument('--px', type=int, default=16)
    ap.add_argument('--text', action='store_true', help='also print text dump of each shot')
    ap.add_argument('--home', default=None, help='isolated XDG dirs root')
    a = ap.parse_args()
    cols, rows = (int(v) for v in a.size.lower().split('x'))
    os.makedirs(a.out, exist_ok=True)
    env = {}
    if a.home:
        env = {'XDG_CONFIG_HOME': os.path.join(a.home, 'config'), 'XDG_DATA_HOME': os.path.join(a.home, 'data'),
               'XDG_CACHE_HOME': os.path.join(a.home, 'cache')}
    r = Runner(cols, rows, shlex.split(a.args), env)
    time.sleep(1.0)
    try:
        for step in [s.strip() for s in a.script.split(';') if s.strip()]:
            parts = step.split(' ', 1)
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else ''
            nums = [int(v) for v in arg.split()] if cmd in ('click', 'rclick', 'dclick', 'drag', 'move', 'down', 'up') else []
            if cmd == 'wait':
                time.sleep(float(arg))
            elif cmd == 'key':
                for k in arg.split():
                    r.send(r.vt.encode_key(k.replace('SPACE', ' ') if k != 'space' else ' ',
                                           ' ' if k == 'space' else None))
                    time.sleep(0.08)
            elif cmd == 'type':
                r.send(arg)
            elif cmd == 'paste':
                r.send('\x1b[200~' + arg + '\x1b[201~')
            elif cmd == 'raw':
                r.send(arg.encode().decode('unicode_escape'))
            elif cmd == 'click':
                r.mouse('down', nums[0], nums[1])
                time.sleep(0.03)
                r.mouse('up', nums[0], nums[1])
            elif cmd == 'dclick':
                for _ in range(2):
                    r.mouse('down', nums[0], nums[1])
                    time.sleep(0.02)
                    r.mouse('up', nums[0], nums[1])
                    time.sleep(0.05)
            elif cmd == 'rclick':
                r.mouse('down', nums[0], nums[1], 2)
                time.sleep(0.03)
                r.mouse('up', nums[0], nums[1], 2)
            elif cmd == 'down':
                r.mouse('down', nums[0], nums[1])
            elif cmd == 'up':
                r.mouse('up', nums[0], nums[1])
            elif cmd == 'move':
                r.mouse('move', nums[0], nums[1])
            elif cmd == 'drag':
                x1, y1, x2, y2 = nums
                r.mouse('down', x1, y1)
                steps = max(abs(x2 - x1), abs(y2 - y1), 1)
                for i in range(1, steps + 1):
                    time.sleep(0.01)
                    r.mouse('drag', x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps)
                time.sleep(0.05)
                r.mouse('up', x2, y2)
            elif cmd == 'wheel':
                d, x, y = arg.split()
                r.mouse('wheel' + d, int(x), int(y))
            elif cmd == 'resize':
                c, rr = (int(v) for v in arg.lower().split('x'))
                r.set_size(c, rr)
            elif cmd == 'shot':
                time.sleep(0.25)
                with r.lock:
                    p = render(r.vt, os.path.join(a.out, a.prefix + arg + '.png'), a.font, a.px)
                    if a.text:
                        print('==== %s ====' % arg)
                        print(text_dump(r.vt))
                print(p)
            time.sleep(0.05)
    finally:
        r.stop()


if __name__ == '__main__':
    main()
