"""External games: fullscreen on the terminal Monocell runs on.

Text games take over the tty directly (the same hand-over the lock screen uses:
stop the terminal, run the game, start it again).  Graphics games use SDL's
KMSDRM backend, so they also draw to the display themselves - no X, no window.
Afterwards the desktop is restored and repainted.
"""
import fcntl
import glob
import os
import subprocess
import tempfile

from ..util import which

LOG = os.path.join(tempfile.gettempdir(), 'monocell-game.log')

# Cheap settings for the GMA X3100: the panel only offers 1024x768 and KMSDRM
# does not scale, so the resolution stays native and the load is reduced here.
PERF = ('+set', 'com_maxfps', '30',
        '+set', 'r_picmip', '2',
        '+set', 'r_subdivisions', '32',
        '+set', 'r_lodbias', '2',
        '+set', 'r_vertexLight', '1',
        '+set', 'r_dynamiclight', '0',
        '+set', 'r_flares', '0',
        '+set', 'r_detailtextures', '0',
        '+set', 'r_fastSky', '1',
        '+set', 'r_swapInterval', '0',
        '+set', 'cg_shadows', '0',
        '+set', 'cg_simpleItems', '1',
        '+set', 'cg_marks', '0',
        '+set', 'cg_gibs', '0')


GAME_SIZES = ((800, 600), (640, 480))


def display_modes():
    modes = set()
    for path in sorted(glob.glob('/sys/class/drm/card*-*/modes')):
        status = os.path.join(os.path.dirname(path), 'status')
        try:
            with open(status) as f:
                if f.read().strip() != 'connected':
                    continue
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if 'x' in line:
                        w, h = line.split('x')[:2]
                        modes.add((int(w), int(h)))
        except (OSError, ValueError):
            continue
    return modes


def game_size():
    """Prefer a low mode when the connector offers one (panel fitter scales it)."""
    modes = display_modes()
    for size in GAME_SIZES:
        if size in modes:
            return size
    return screen_size()


def screen_size():
    """Native display size: fill the screen instead of drawing a small corner."""
    try:
        with open('/sys/class/graphics/fb0/virtual_size') as f:
            w, h = f.read().strip().split(',')
            if int(w) > 0 and int(h) > 0:
                return int(w), int(h)
    except (OSError, ValueError):
        pass
    for path in sorted(glob.glob('/sys/class/drm/card*-*/modes')):
        try:
            with open(path) as f:
                line = f.readline().strip()
            if line:
                w, h = line.split('x')
                return int(w), int(h)
        except (OSError, ValueError):
            continue
    return 1024, 768


def read_tail(lines=8):
    try:
        with open(LOG, errors='replace') as f:
            rows = [line.rstrip() for line in f if line.strip()]
    except OSError:
        return ''
    return '\n'.join(rows[-lines:])


def run(wm, argv, title, gfx=False, env=None, cwd=None):
    """Hand the console over to `argv`, then restore Monocell."""
    wm.close_popup()
    rc = 0
    err = ''
    logf = None
    saved_flags = None
    interrupted = False
    try:
        saved_flags = fcntl.fcntl(0, fcntl.F_GETFL)
        fcntl.fcntl(0, fcntl.F_SETFL, saved_flags & ~os.O_NONBLOCK)
    except OSError:
        saved_flags = None
    wm.term.stop()
    try:
        e = dict(os.environ)
        if gfx:
            e.setdefault('SDL_VIDEODRIVER', 'kmsdrm')
        e.update(env or {})
        try:
            logf = open(LOG, 'w')
        except OSError:
            logf = None
        if gfx:
            proc = subprocess.Popen(argv, env=e, cwd=cwd, stdout=logf,
                                    stderr=subprocess.STDOUT)
        else:
            # Text games keep stderr: doom-ascii reads its keys from fd 2 (its UI
            # normally *is* the terminal), and a log file there fails with EBADF.
            proc = subprocess.Popen(argv, env=e, cwd=cwd)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            interrupted = True
            proc.terminate()
            try:
                rc = proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait()
    except OSError as ex:
        err = str(ex)
    finally:
        if logf is not None:
            try:
                logf.close()
            except OSError:
                pass
        if saved_flags is not None:
            try:
                fcntl.fcntl(0, fcntl.F_SETFL, saved_flags)
            except OSError:
                pass
        wm.term.start()
        wm.term.front = None
        wm.dirty = True
        wm.on_resize()
    if err:
        wm.message(title, 'Could not start %s:\n%s' % (argv[0], err))
    elif rc > 0 and not interrupted:
        tail = read_tail()
        wm.message(title, '%s exited with status %d.%s'
                   % (title, rc, ('\n\n' + tail) if tail else ''))
    return rc


class Game:
    title = 'Game'
    mode = 'text'
    command = ()
    args = ()
    env = {}
    cwd = None          # working directory, for engines that look for their data there

    @classmethod
    def argv(cls):
        return list(cls.command) + list(cls.args)

    @classmethod
    def check(cls, wm):
        return True

    @classmethod
    def launch(cls, wm):
        if not cls.check(wm):
            return None
        argv = cls.argv()
        if not argv:
            return None
        exe = argv[0]
        if not os.path.isabs(exe):
            found = which(exe)
            if not found:
                wm.message(cls.title, '%s is not installed.' % exe)
                return None
            argv[0] = found
        elif not os.path.exists(exe):
            wm.message(cls.title, '%s is not installed.' % exe)
            return None
        return run(wm, argv, cls.title, cls.mode == 'gfx', cls.env, cls.cwd)


# ---------------------------------------------------------------- terminal games
class Crawl(Game):
    title = 'Dungeon Crawl'
    command = ('/usr/games/crawl',)


class Cataclysm(Game):
    title = 'Cataclysm'
    command = ('/usr/games/cataclysm',)


class Brogue(Game):
    title = 'Brogue'
    command = ('/usr/local/games/brogue',)


class NetHack(Game):
    title = 'NetHack'
    command = ('/usr/games/nethack',)


class DoomText(Game):
    title = 'Doom (Text)'
    command = ('/usr/local/games/doom-ascii',)


class Vitetris(Game):
    title = 'vitetris'
    command = ('/usr/games/vitetris',)


class Nudoku(Game):
    title = 'Nudoku'
    command = ('/usr/games/nudoku',)


class FreeSweep(Game):
    title = 'FreeSweep'
    command = ('/usr/games/freesweep',)


class Solitaire(Game):
    title = 'Solitaire'
    command = ('/usr/games/ttysolitaire',)


class Pacman(Game):
    title = 'Pac-Man'
    command = ('/usr/games/pacman4console',)


class NInvaders(Game):
    title = 'nInvaders'
    command = ('/usr/games/ninvaders',)


class MoonBuggy(Game):
    title = 'Moon Buggy'
    command = ('/usr/games/moon-buggy',)


# ---------------------------------------------------------------- graphics games
class DoomGfx(Game):
    title = 'Doom (Grafik)'
    mode = 'gfx'
    command = ('/usr/games/chocolate-doom', '-iwad',
               '/usr/share/games/doom/freedoom1.wad')

    @classmethod
    def argv(cls):
        w, h = game_size()
        return list(cls.command) + ['-width', str(w), '-height', str(h)]


Q3E = '/usr/local/games/quake3e/quake3e.x64'
Q3E_DATA = '/usr/local/share/openarena-0.8.8'


class OpenArena(Game):
    title = 'OpenArena'
    mode = 'gfx'
    command = ('/usr/games/openarena',)

    @classmethod
    def argv(cls):
        w, h = screen_size()
        if os.path.exists(Q3E) and os.path.isdir(os.path.join(Q3E_DATA, 'baseoa')):
            return [
                Q3E,
                '+set', 'fs_basepath', Q3E_DATA,
                '+set', 'fs_basegame', 'baseoa',
                '+set', 'fs_homepath', os.path.expanduser('~/.quake3e-openarena'),
                '+set', 'com_legacyprotocol', '71',
                '+set', 'com_protocol', '71',
                '+set', 'cl_motd', '0',
                '+set', 'sv_master1', 'dpmaster.deathmask.net',
                '+set', 'r_mode', '-1',
                '+set', 'r_customwidth', str(w),
                '+set', 'r_customheight', str(h),
                '+set', 'r_fullscreen', '1',
                '+set', 'r_fbo', '1',
                '+set', 'r_renderWidth', '800',
                '+set', 'r_renderHeight', '600',
                '+set', 'r_renderScale', '3',
            ] + list(PERF)
        return list(cls.command) + [
            '+set', 'r_mode', '-1', '+set', 'r_customwidth', str(w),
            '+set', 'r_customheight', str(h), '+set', 'r_fullscreen', '1',
            ] + list(PERF)


Q3_DATA = ('~/.q3a/baseq3/pak0.pk3',
           '/usr/lib/ioquake3/baseq3/pak0.pk3',
           '/usr/lib/quake3/baseq3/pak0.pk3',
           '/usr/lib/quake3/base/baseq3/pak0.pk3',
           '/usr/share/games/quake3/baseq3/pak0.pk3')


def q3_data():
    return [p for p in Q3_DATA if os.path.exists(os.path.expanduser(p))]


class Quake3(Game):
    title = 'Quake 3 Arena'
    mode = 'gfx'
    command = ('/usr/lib/ioquake3/ioquake3',)

    @classmethod
    def argv(cls):
        w, h = screen_size()
        if os.path.exists(Q3E):
            return [
                Q3E,
                '+set', 'fs_homepath', os.path.expanduser('~/.q3a'),
                '+set', 'r_mode', '-1', '+set', 'r_customwidth', str(w),
                '+set', 'r_customheight', str(h), '+set', 'r_fullscreen', '1',
                '+set', 'r_fbo', '1',
                '+set', 'r_renderWidth', '800',
                '+set', 'r_renderHeight', '600',
                '+set', 'r_renderScale', '3',
            ] + list(PERF)
        return list(cls.command) + [
            '+set', 'fs_homepath', os.path.expanduser('~/.q3a'),
            '+set', 'r_mode', '-1', '+set', 'r_customwidth', str(w),
            '+set', 'r_customheight', str(h), '+set', 'r_fullscreen', '1',
            ] + list(PERF)

    @classmethod
    def check(cls, wm):
        if q3_data():
            return True
        wm.dialog(
            cls.title,
            'Quake-3-Arena-Daten nicht gefunden.\n\n'
            'Kopiere pak0.pk3 (und pak1-8) nach ~/.q3a/baseq3/ - zum Beispiel aus '
            'einer Steam- oder CD-Installation. Danach hier erneut starten.\n\n'
            'OpenArena ist frei und sofort spielbar.',
            ('OpenArena', 'Cancel'),
            lambda result, value: (APPS['openarena'].launch(wm)
                                   if result == 'OpenArena' else None))
        return False


SDL32 = '/usr/lib/i386-linux-gnu/libSDL2-2.0.so.0'
EGL32 = '/usr/lib/i386-linux-gnu/libEGL.so.1'


class HalfLife(Game):
    title = 'Half-Life'
    mode = 'gfx'
    # xash3d (not xash): it prints why SDL fails instead of falling back silently
    command = ('/usr/local/games/xash3d-i386/xash3d',)
    # The AppImage build ignores -basedir: it reads XASH3D_BASEDIR (and its AppRun
    # uses $PWD), so both are set here - otherwise it looks for valve/ in ~.
    # Its bundled SDL2 was built without the KMSDRM driver ("kmsdrm not available",
    # then a silent headless server), so the 32-bit SDL/EGL from Debian are used.
    env = {'LD_PRELOAD': '/usr/lib/i386-linux-gnu/libstdc++.so.6',
           'XASH3D_BASEDIR': '/usr/local/share/half-life',
           'LD_LIBRARY_PATH': '/usr/lib/i386-linux-gnu'}
    cwd = '/usr/local/share/half-life'

    @classmethod
    def argv(cls):
        w, h = screen_size()
        return list(cls.command) + [
            '-basedir', '/usr/local/share/half-life',
            '-game', 'valve',
            '-fullscreen',
            '-width', str(w),
            '-height', str(h),
            '-ref', 'gl',
        ]

    @classmethod
    def check(cls, wm):
        if not os.path.exists('/usr/local/share/half-life/valve/liblist.gam'):
            wm.message(cls.title,
                       'Half-Life data not found under /usr/local/share/half-life/valve.')
            return False
        if not os.path.exists(cls.command[0]):
            wm.message(cls.title, 'Xash3D not found under %s.'
                       % os.path.dirname(cls.command[0]))
            return False
        if not (os.path.exists(SDL32) and os.path.exists(EGL32)):
            wm.message(cls.title,
                       'Xash3D needs the 32-bit SDL2 and EGL (the SDL bundled with the '
                       'AppImage has no KMSDRM):\n\n'
                       'sudo apt install libsdl2-2.0-0:i386 libegl1:i386')
            return False
        return True


APPS = {
    'crawl': Crawl,
    'cataclysm': Cataclysm,
    'brogue': Brogue,
    'nethack': NetHack,
    'doom': DoomText,
    'doom-gfx': DoomGfx,
    'quake3': Quake3,
    'openarena': OpenArena,
    'vitetris': Vitetris,
    'nudoku': Nudoku,
    'freesweep': FreeSweep,
    'solitaire': Solitaire,
    'pacman': Pacman,
    'ninvaders': NInvaders,
    'moonbuggy': MoonBuggy,
    'halflife': HalfLife,
}
