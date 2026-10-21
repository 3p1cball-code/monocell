"""monocell entry point:  python3 -m monocell [--open app[,app]] [--color MODE]"""
import argparse
import os
import signal
import sys
import time
import traceback

from . import util
from .loop import Loop
from .term import Terminal
from .theme import Config, Theme
from .wm import WM


def main(argv=None):
    ap = argparse.ArgumentParser(prog='monocell', description='Text-mode desktop environment')
    ap.add_argument('--open', default='', help='comma separated apps to open at start (e.g. files,terminal)')
    ap.add_argument('--color', default=None, help='auto|truecolor|palette|linux|ansi')
    ap.add_argument('--tile', action='store_true', help='tile the opened windows')
    ap.add_argument('--lowmem', action='store_true', help='low-memory profile: plain desktop, no shadows, pixel video, lite web')
    ap.add_argument('files', nargs='*', help='files to open')
    args = ap.parse_args(argv)

    if not os.isatty(0) or not os.isatty(1):
        print('monocell needs a terminal', file=sys.stderr)
        return 1
    util.ensure_dirs()
    cfg = Config()
    if args.lowmem:
        # session overrides, not written back to the config
        # only performance knobs; look, image mode and browser engine stay user choices
        cfg.set_session({
            'shadows': False,
            'video_fps': 10,
        })
    util.STRICT_ASCII = cfg['strict_ascii']
    theme = Theme(cfg)
    loop = Loop()
    term = Terminal(theme, args.color or cfg.get('color_mode', 'auto'))
    wm = WM(term, loop, cfg, theme)
    last_input = [0.0]

    def on_stdin(fd):
        evs = term.read_events()
        if evs is None:
            loop.running = False
            return
        last_input[0] = time.monotonic()
        for ev in evs:
            wm.handle(ev)

    def on_mice(fd):
        evs = term.mice.read()
        mods = term.vt_mods() if evs else frozenset()
        for ev in evs:
            if mods:
                ev.mods = mods
            wm.handle(ev)

    def idle():
        t = wm.idle()
        if term.parser.buf:
            dt = time.monotonic() - last_input[0]
            if dt >= 0.03:
                for ev in term.parser.flush():
                    wm.handle(ev)
                t = wm.idle()
            else:
                t = 0.03 - dt if t is None else min(t, 0.03 - dt)
        return t

    def on_winch(sig, frame):
        loop.call_soon_threadsafe(wm.on_resize)

    signal.signal(signal.SIGWINCH, on_winch)
    signal.signal(signal.SIGTERM, lambda s, f: loop.call_soon_threadsafe(wm.quit))
    signal.signal(signal.SIGHUP, lambda s, f: loop.call_soon_threadsafe(wm.quit))
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    term.start()
    try:
        os.set_blocking(0, False)
        loop.add_reader(0, on_stdin)
        if term.mice:
            loop.add_reader(term.mice.fd, on_mice)
        for name in [n for n in args.open.split(',') if n]:
            wm.open(name)
        for f in args.files:
            from .apps.files import open_path
            open_path(wm, os.path.abspath(f))
        if args.tile:
            wm.tile()
        loop.run(idle)
    except KeyboardInterrupt:
        pass
    except Exception:
        tb = traceback.format_exc()
        util.log(tb)
        term.stop()
        os.set_blocking(0, True)
        print(tb, file=sys.stderr)
        return 1
    finally:
        for win in list(wm.windows):
            try:
                wm.close(win, force=True)
            except Exception:
                pass
    try:
        from . import web
        web.shutdown_all()
    except Exception:
        pass
    term.stop()
    os.set_blocking(0, True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
