#!/usr/bin/env python3
"""Measure text-mode video playback: run monocell in a pty of a given size with
the Linux-console colour mode, play a video fullscreen, profile the process.

    tools/vidbench.py VIDEO [--size 204x109] [--secs 20] [--mode ascii] [--fps 10]
                      [--profile OUT.prof] [--dump OUT.bin]

Prints frames shown, the average render time the player reports and the bytes
the terminal output cost per frame (what the kernel VT has to parse and draw).
The user's config is copied to a temporary XDG dir, so nothing is written back;
sound is off (the player then runs on the wall clock).
"""
import argparse
import json
import os
import pty
import select
import shutil
import signal
import struct
import fcntl
import sys
import tempfile
import termios
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def cpu(pid):
    """user+system seconds of a process (from /proc)."""
    try:
        f = open('/proc/%d/stat' % pid).read().rsplit(')', 1)[1].split()
    except OSError:
        return 0.0
    return (int(f[11]) + int(f[12])) / os.sysconf('SC_CLK_TCK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--size', default='204x109')
    ap.add_argument('--secs', type=float, default=20)
    ap.add_argument('--mode', default='')
    ap.add_argument('--fps', type=int, default=0)
    ap.add_argument('--profile', default='')
    ap.add_argument('--dump', default='', help='save the terminal output stream')
    ap.add_argument('--color', default='linux')
    a = ap.parse_args()
    cols, rows = (int(v) for v in a.size.split('x'))
    tmp = tempfile.mkdtemp(prefix='vidbench-')
    cfgdir = os.path.join(tmp, 'config', 'monocell')
    os.makedirs(cfgdir)
    src = os.path.expanduser('~/.config/monocell/config.json')
    cfg = json.load(open(src)) if os.path.exists(src) else {}
    if a.mode:
        cfg['video_mode'] = a.mode
    if a.fps:
        cfg['video_fps'] = a.fps
    cfg['startup_sound'] = ''
    json.dump(cfg, open(os.path.join(cfgdir, 'config.json'), 'w'))
    stats = os.path.join(tmp, 'stats.json')
    child_code = r'''
import cProfile, json, os, signal, sys, time
sys.path.insert(0, %(root)r)
from monocell import __main__ as m
from monocell.apps import video as V
prof = cProfile.Profile() if %(prof)r else None
orig_tick = V.Video.tick
def stop(*_):
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
try:
    if prof: prof.enable()
    m.main(['--color', %(color)r, '--trd-meta', %(meta)r, %(video)r])
except SystemExit:
    pass
finally:
    if prof:
        prof.disable(); prof.dump_stats(%(prof)r)
    import gc
    vids = [o for o in gc.get_objects() if isinstance(o, V.Video)]
    st = {'frames': vids[0].stats[0], 'ms': vids[0].stats[1], 'dims': vids[0].dims} if vids else {}
    json.dump(st, open(%(stats)r, 'w'))
''' % {'root': ROOT, 'prof': a.profile, 'color': a.color, 'meta': os.path.join(tmp, 'trd.json'),
       'video': a.video, 'stats': stats}
    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ, TERM='linux', MONOCELL_NOAUDIO='1',
                   XDG_CONFIG_HOME=os.path.join(tmp, 'config'),
                   XDG_CACHE_HOME=os.path.join(tmp, 'cache'), XDG_DATA_HOME=os.path.join(tmp, 'data'))
        os.execvpe(sys.executable, [sys.executable, '-c', child_code], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, cols * 5, rows * 7))
    dump = open(a.dump, 'wb') if a.dump else None
    total = 0
    t_start = time.monotonic()
    sent = False
    measuring_from = None
    bytes_at = 0
    while time.monotonic() - t_start < a.secs + 4:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 1 << 20)
            except OSError:
                break
            total += len(d)
            if dump and measuring_from:
                dump.write(d)
        el = time.monotonic() - t_start
        if not sent and el > 2.5:
            os.write(fd, b'f')                 # fullscreen
            sent = True
        if measuring_from is None and el > 4:
            measuring_from = time.monotonic()
            bytes_at = total
            cpu_at = cpu(pid)
    span = time.monotonic() - measuring_from
    out_bytes = total - bytes_at
    cpu_used = cpu(pid) - cpu_at
    os.kill(pid, signal.SIGTERM)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                if not os.read(fd, 1 << 20):
                    break
            except OSError:
                break
        if os.waitpid(pid, os.WNOHANG)[0]:
            break
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    st = json.load(open(stats)) if os.path.exists(stats) else {}
    print('dims %s  frames %s  render %.1f ms (player avg)  output %.0f KB/s  monocell CPU %.0f %%'
          '  (%.1f s measured)' % (st.get('dims'), st.get('frames'), st.get('ms', 0),
                                   out_bytes / span / 1024, cpu_used / span * 100, span))
    if a.profile:
        import pstats
        p = pstats.Stats(a.profile)
        p.sort_stats('tottime').print_stats(18)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
