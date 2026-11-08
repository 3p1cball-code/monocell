#!/usr/bin/env python3
"""Per-stage cost of one text-mode video frame: render (image -> cells), blit
into the screen buffer, terminal diff/flush (Linux-console colour mode, written
to /dev/null). Uses the user's theme and two real frames of a video.

    tools/stagebench.py VIDEO [--size 204x109] [--at 30] [--n 20]
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from monocell import imaging  # noqa: E402
from monocell.screen import Buffer, Canvas  # noqa: E402
from monocell.term import Terminal  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402


class FakeWM:
    pass


def frames(path, w, h, at, n=2):
    cmd = ['ffmpeg', '-v', 'error', '-ss', str(at), '-i', path, '-an', '-vf',
           'fps=10,scale=%d:%d:flags=area' % (w, h), '-frames:v', str(n), '-f', 'rawvideo',
           '-pix_fmt', 'rgb24', '-']
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
    fs = w * h * 3
    return [raw[i * fs:(i + 1) * fs] for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--size', default='204x109')
    ap.add_argument('--at', type=float, default=30)
    ap.add_argument('--n', type=int, default=20)
    ap.add_argument('--modes', default='ascii,blend,pixel,mono,grey')
    ap.add_argument('--tty', default='', help='write to this VT instead of /dev/null: '
                    'the flush time then includes the kernel drawing the cells')
    a = ap.parse_args()
    W, H = (int(v) for v in a.size.split('x'))
    cfg = Config()
    theme = Theme(cfg)
    null = os.open(a.tty or '/dev/null', os.O_WRONLY)
    term = Terminal(theme, 'linux', fd_in=null, fd_out=null)
    term.w, term.h = W, H
    term.back = Buffer(W, H)
    term.apply_palette()
    wm = FakeWM()
    wm.theme, wm.term, wm.cfg = theme, term, cfg
    fr = frames(a.video, W, H, a.at, 8)
    print('%dx%d cells, %d frames' % (W, H, len(fr)))
    for mode in a.modes.split(','):
        imaging.render(wm, fr[0], W, H, mode)            # warm the caches
        t0 = time.perf_counter()
        cells = []
        for i in range(a.n):
            cells.append(imaging.render(wm, fr[i % len(fr)], W, H, mode))
        t_render = (time.perf_counter() - t0) / a.n * 1000
        cv = Canvas(term.back)
        term.front = None
        term.flush()
        t_blit = t_flush = 0.0
        nbytes = 0
        for i in range(a.n):
            t0 = time.perf_counter()
            cv.blit_cells(0, 0, cells[i])
            t1 = time.perf_counter()
            b0 = term.bytes_out
            term.flush()
            t2 = time.perf_counter()
            t_blit += t1 - t0
            t_flush += t2 - t1
            nbytes += term.bytes_out - b0
        print('%-6s render %6.1f ms   blit %5.1f ms   flush %6.1f ms   %6.1f KB/frame' % (
            mode, t_render, t_blit / a.n * 1000, t_flush / a.n * 1000, nbytes / a.n / 1024))


if __name__ == '__main__':
    main()
