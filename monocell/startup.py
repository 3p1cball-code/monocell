"""Login sound: one short wave file once the desktop is up.

The samples themselves are not part of the repository.  They live next to the
user data in ``~/.local/share/monocell/sounds/startup/``; ``CATALOG`` lists what
the settings page offers, and only files that are actually present show up.
Without a file (or with ``startup_sound`` set to off, or ``MONOCELL_NOAUDIO=1``)
nothing happens.  Playback runs in a daemon thread through the same sinks as the
music app, so it never delays the desktop.
"""
import os
import subprocess
import threading
import time

from . import util
from .audio import SINKS, pick_sinks

DIR = 'startup'
DEFAULT = 'win95'
DELAY = 1.4          # let terminal and PipeWire settle before the first sample

# id, file, title, description - order is the order in the settings list
CATALOG = [
    ('win95', 'win95.wav', 'Windows 95',
     "Brian Eno's \"The Microsoft Sound\" (1995) - the chime that started it all."),
    ('win98', 'win98.wav', 'Windows 98',
     'The same "Microsoft Sound" as 95 (1998); the last of the classic boot chimes.'),
    ('win2000', 'win2000.wav', 'Windows 2000',
     "Microsoft's deeper, calmer professional startup (2000)."),
    ('winxp', 'winxp.wav', 'Windows XP',
     'The bright, optimistic XP startup (2001).'),
    ('os2warp4', 'os2warp4.wav', 'OS/2 Warp 4',
     "IBM's soft synth chord (1996) - the sound of Big Blue."),
    ('os2warp3', 'os2warp3.wav', 'OS/2 Warp 3',
     "IBM's earlier, brighter startup (1994)."),
    ('ibmpc', 'ibmpc.wav', 'IBM PC (POST)',
     'The single 1 kHz self-test beep of the IBM 5150 (1981).'),
    ('mac', 'mac.wav', 'Macintosh',
     "Jim Reekes' \"bong\" chime (Quadra 840AV, 1993)."),
    ('sgi', 'sgi.wav', 'SGI Indy',
     'The jazzy IRIX boot jingle of Silicon Graphics (1993).'),
    ('ps1', 'ps1.wav', 'PlayStation',
     "Sony Computer Entertainment's boot chime (1994)."),
    ('ps2', 'ps2.wav', 'PlayStation 2',
     'The eerie synth sweep of the PS2 boot (2000).'),
    ('dreamcast', 'dreamcast.wav', 'Dreamcast',
     "Sega's floating \"Dreamcast\" boot sound (1998)."),
    ('gamecube', 'gamecube.wav', 'GameCube',
     "Nintendo's short, soft \"ding\" (2001)."),
    ('xbox', 'xbox.wav', 'Xbox',
     "Microsoft's deep green \"whum\" (2001)."),
    ('genesis', 'genesis.wav', 'Sega Genesis',
     'The \"SEGA\" chant (1988).'),
    ('gameboy', 'gameboy.wav', 'Game Boy',
     "The two-note \"ding\" with the Nintendo logo (1989)."),
]


def sound_dir():
    return os.path.join(util.DATA_DIR, 'sounds', DIR)


def path_for(sid):
    """File path for a catalogue id (whether it exists or not)."""
    for i, fname, _title, _desc in CATALOG:
        if i == sid:
            return os.path.join(sound_dir(), fname)
    return None


def available():
    """[(id, title, description, path)] for samples that are on disk, in order."""
    d = sound_dir()
    out = []
    for i, fname, title, desc in CATALOG:
        path = os.path.join(d, fname)
        if os.path.exists(path):
            out.append((i, title, desc, path))
    return out


def rows():
    """List rows for the settings page: Off first, then the installed samples."""
    return [('', 'Off', 'No startup sound.')] + [(i, t, d) for i, t, d, _p in available()]


def current(cfg=None):
    """Selected id.  Older configs stored True/False; map them."""
    if cfg is None:
        return DEFAULT
    v = cfg.get('startup_sound', DEFAULT)
    if v is True:
        return DEFAULT
    if v is False or v is None:
        return ''
    return str(v)


def enabled(cfg=None, force=False):
    if os.environ.get('MONOCELL_NOAUDIO'):
        return False
    if not util.which('ffmpeg') or not available():
        return False
    return force or bool(current(cfg))


def play(cfg=None, delay=DELAY, sound=None, force=False):
    """Start a login sound in the background; returns the thread or None.

    ``sound`` overrides the configured id (used for the preview); ``force``
    plays even when the startup sound is switched off.
    """
    if os.environ.get('MONOCELL_NOAUDIO') or not util.which('ffmpeg'):
        return None
    sid = sound if sound is not None else current(cfg)
    if not sid or (not force and not current(cfg)):
        return None
    path = path_for(sid)
    if not path or not os.path.exists(path):
        return None
    pref = cfg.get('audio_sink', 'auto') if cfg is not None else 'auto'
    sinks = pick_sinks(pref)
    if not sinks:
        return None
    sink = sinks[0]

    def run():
        if delay:
            time.sleep(delay)
        dec = None
        try:
            dec = subprocess.Popen(
                ['ffmpeg', '-v', 'error', '-i', path, '-f', 's16le',
                 '-ac', '2', '-ar', '44100', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            subprocess.run(SINKS[sink], stdin=dec.stdout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=25)
        except Exception as e:
            util.log('startup sound failed: %s' % e)
        finally:
            if dec is not None:
                try:
                    dec.stdout.close()
                except Exception:
                    pass
                try:
                    dec.kill()
                except Exception:
                    pass
                dec.wait()

    t = threading.Thread(target=run, name='startup-sound', daemon=True)
    t.start()
    return t
