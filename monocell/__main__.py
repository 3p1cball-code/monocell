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
from .theme import Config, Theme, apply_console_font
from .wm import WM


def main(argv=None):
    ap = argparse.ArgumentParser(prog='monocell', description='Text-mode desktop environment')
    ap.add_argument('--open', default='', help='comma separated apps to open at start (e.g. files,terminal)')
    ap.add_argument('--color', default=None, help='auto|truecolor|palette|linux|ansi')
    ap.add_argument('--tile', action='store_true', help='tile the opened windows')
    ap.add_argument('--lowmem', action='store_true', help='low-memory profile: plain desktop, no shadows')
    ap.add_argument('--trd-port', type=int, default=0, help='TRD server port (0 = ephemeral, 127.0.0.1 only)')
    ap.add_argument('--trd-meta', default='', help='TRD meta file (default: ~/.cache/monocell/trd.json)')
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
        # video keeps the configured frame rate: the renderer and the console are
        # fast enough for 24 fps even at 204x109, and the player drops frames by
        # itself when something else is running
        cfg.set_session({
            'shadows': False,
        })
    util.STRICT_ASCII = cfg['strict_ascii']
    theme = Theme(cfg)
    loop = Loop()
    # the console font comes from the theme, not from console-setup: load it before
    # the terminal measures itself (the 3270 font is 9 pixels wide, Fixed is 8)
    apply_console_font(cfg.get('font'))
    term = Terminal(theme, args.color or cfg.get('color_mode', 'auto'))
    wm = WM(term, loop, cfg, theme)
    remote = None
    try:
        from . import remote
        remote.start(loop, wm, term, cfg, port=args.trd_port, meta_path=args.trd_meta or None)
    except Exception:
        util.log('trd unavailable: ' + traceback.format_exc())
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

    restart = [False]

    def on_restart(sig, frame):
        # SIGUSR1: leave the loop and exec monocell again - new code, same tty,
        # no new login (a deploy in another terminal can then restart it)
        restart[0] = True
        loop.call_soon_threadsafe(wm.quit)

    signal.signal(signal.SIGWINCH, on_winch)
    signal.signal(signal.SIGTERM, lambda s, f: loop.call_soon_threadsafe(wm.quit))
    signal.signal(signal.SIGHUP, lambda s, f: loop.call_soon_threadsafe(wm.quit))
    signal.signal(signal.SIGUSR1, on_restart)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    term.start()
    hotkeys = None
    try:
        os.set_blocking(0, False)
        loop.add_reader(0, on_stdin)
        if term.mice:
            loop.add_reader(term.mice.fd, on_mice)
        try:
            from .hotkeys import Hotkeys
            hotkeys = Hotkeys(loop, wm.hotkey)
        except Exception:
            util.log('hotkeys unavailable: ' + traceback.format_exc())
        for name in [n for n in args.open.split(',') if n]:
            wm.open(name)
        for f in args.files:
            from .apps.files import open_path
            open_path(wm, os.path.abspath(f))
        if args.tile:
            wm.tile()
        try:
            from . import startup
            startup.play(cfg)
        except Exception:
            util.log('startup sound unavailable: ' + traceback.format_exc())
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
        if remote:
            try:
                remote.stop()
            except Exception:
                pass
        if hotkeys:
            hotkeys.close_all()
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
    if restart[0]:
        util.log('restart on SIGUSR1')
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.execv(sys.executable, [sys.executable, '-m', 'monocell'] + sys.argv[1:])
        except OSError:
            util.log('restart failed: ' + traceback.format_exc())
    return 0


if __name__ == '__main__':
    sys.exit(main())
