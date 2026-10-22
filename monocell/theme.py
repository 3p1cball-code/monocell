"""16-colour palette, semantic roles, presets and persistent config."""
import json
import os

from .screen import A
from .util import CONFIG_DIR, ensure_dirs, log

SLOT_NAMES = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'grey',
              'dark 1', 'dark 2', 'dark 3', 'dark 4', 'light', 'white', 'accent', 'accent 2']

# Slot semantics are fixed so that roles keep working across presets:
#  0 deepest dark (shadow)   1-6 hues   7 dim text
#  8 desktop   9 window   10 raised/title   11 selection/thumb
# 12 text   13 bright text   14 accent   15 accent light
PRESETS = {
    'Ember': ['0c0c0c', 'e0574a', '8db86b', 'e8c26a', '6b9bd1', 'c08cc0', '6db8b4', '7c7c7c',
              '161616', '202020', '2c2c2c', '424242', 'c4c4c4', 'f2f2f2', 'ff8a1c', 'ffb766'],
    'Graphite Blue': ['0b0c0e', 'e06c75', '98c379', 'e5c07b', '61afef', 'c678dd', '56b6c2', '7a808a',
                      '15171a', '1e2125', '2a2e34', '3e434c', 'c3c8d0', 'f2f4f7', '4aa3ff', '9ccdff'],
    'Amber CRT': ['080500', 'ff7a1a', 'ffb000', 'ffcc33', 'cc7a00', 'e09030', 'ffc266', '8a5a10',
                  '120b02', '1c1204', '2a1b06', '3f2a0a', 'ffb000', 'ffd480', 'ffcc00', 'ffe099'],
    'Green Phosphor': ['020803', '4cff6a', '33ff66', 'a8ff60', '22cc55', '66ff99', '99ffcc', '1f7a35',
                       '041006', '061a0a', '0a2610', '12401c', '33ff66', 'b0ffc4', '7dff3a', 'ccff99'],
    'CGA': ['000000', 'aa0000', '00aa00', 'aa5500', '0000aa', 'aa00aa', '00aaaa', '555555',
            '000000', '0000aa', '00aaaa', '555555', 'aaaaaa', 'ffffff', 'ffff55', 'ff55ff'],
    'Gruvbox': ['141617', 'fb4934', 'b8bb26', 'fabd2f', '83a598', 'd3869b', '8ec07c', '928374',
                '1d2021', '282828', '32302f', '45403d', 'ebdbb2', 'fbf1c7', 'fe8019', 'fabd2f'],
    'Nord': ['1b1f26', 'bf616a', 'a3be8c', 'ebcb8b', '81a1c1', 'b48ead', '88c0d0', '6c7a91',
             '242933', '2e3440', '3b4252', '4c566a', 'd8dee9', 'eceff4', 'd08770', 'ebcb8b'],
    'Matrix': ['000000', '33ff66', '7dff3a', 'ccff99', '22cc55', '66ff99', '00ffcc', '1f7a35',
               '020803', '04120a', '07200f', '113a1e', 'a8ffbe', 'e8fff0', '33ff66', 'b4ffcc'],
    'Paper': ['9a9a9a', 'b83a2e', '3f7a2a', '9a6b00', '2f5f9e', '8a3f8a', '237878', '7a7a7a',
              'd6d3cc', 'f2efe8', 'e4e0d6', 'c9c4b8', '2b2b2b', '000000', 'd9600b', 'b04a00'],
}

ROLE_DEFAULTS = {
    'desk': 8, 'desk_fg': 11, 'shadow': 0, 'win': 9, 'title': 10, 'raised': 10,
    'sel': 11, 'field': 8, 'bar': 10, 'menu': 10,
    'text': 12, 'dim': 7, 'bright': 13, 'accent': 14, 'accent2': 15, 'on_accent': 0,
    'cursor': 6,
    'red': 1, 'green': 2, 'yellow': 3, 'blue': 4, 'magenta': 5, 'cyan': 6,
}
ROLE_HELP = {
    'desk': 'Desktop background', 'desk_fg': 'Desktop logo', 'shadow': 'Window shadow',
    'win': 'Window background', 'title': 'Active title bar', 'raised': 'Buttons / headers',
    'sel': 'Selection', 'field': 'Input fields', 'bar': 'Taskbar', 'menu': 'Menus',
    'text': 'Text', 'dim': 'Dim text', 'bright': 'Bright text', 'accent': 'Highlight',
    'accent2': 'Highlight 2', 'on_accent': 'Text on highlight',
    'cursor': 'Mouse cursor',
}

# a cursor colour per preset, distinct from accent and selection
PRESET_CURSOR = {
    'Ember': 6, 'Graphite Blue': 5, 'Amber CRT': 13, 'Green Phosphor': 13,
    'CGA': 5, 'Gruvbox': 6, 'Nord': 4, 'Paper': 5, 'Matrix': 13,
}

DEFAULT_CONFIG = {
    'preset': 'Ember',
    'palette': PRESETS['Ember'],
    'roles': dict(ROLE_DEFAULTS),
    'taskbar': 'bottom',          # bottom | top
    'frame': 'flat',              # flat | lines
    'shadows': True,
    'clock_seconds': False,
    'clock_24h': True,
    'desktop_logo': False,
    'wallpaper': '',
    'background': 'auto',         # auto | logo | plain | image | matrix
    'rain_density': 1.0,
    'rain_speed': 1.0,
    'rain_random': 0.6,
    'symbols': 'auto',            # auto | on | off (single-width unicode symbols)
    'ascii_charset': 'full',      # full | simple | shades
    'strict_ascii': False,
    'image_mode': 'blend',        # blend | pixel | ascii | mono
    'dither': 'bayer4',           # bayer4 | bayer8 | fs | none
    'video_mode': 'pixel',
    'cell_aspect': 0.0,           # 0 = auto (h/w ratio of a cell, usually ~2)
    'video_fps': 15,
    'av_offset': 0,               # ms; negative shows the picture earlier than the sound
    'audio_sink': 'auto',         # auto | pw-cat | pacat | aplay
    'web_engine': 'auto',         # auto | lite | chromium
    'web_images': True,
    'web_home': 'about:home',
    'search_url': 'https://html.duckduckgo.com/html/?q=%s',
    'chromium': '',
    'music_dir': '',
    'gcal_url': '',
    'opencode_cmd': 'opencode',
    'opencode_host': '',
    'opencode_workdir': '',
    'kdeconnect_display': ':99',
    'shot_dir': '~/monocell-shots',
    'color_mode': 'auto',         # auto | truecolor | palette | linux | ansi
    'bookmarks': [['Wikipedia', 'https://en.wikipedia.org'],
                  ['Hacker News', 'https://news.ycombinator.com'],
                  ['tagesschau', 'https://www.tagesschau.de'],
                  ['DuckDuckGo', 'https://html.duckduckgo.com/html/']],
}


def hex_rgb(h):
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_hex(c):
    return '%02x%02x%02x' % tuple(max(0, min(255, int(v))) for v in c)


class Config(dict):
    path = os.path.join(CONFIG_DIR, 'config.json')
    # a 0.x install kept its config next to this one; read it once, then save to the new path
    legacy = os.path.join(os.path.dirname(CONFIG_DIR), 'textwin', 'config.json')

    def __init__(self):
        super().__init__(json.loads(json.dumps(DEFAULT_CONFIG)))
        self.overrides = {}
        self.load()

    # session overrides (e.g. --lowmem): read-only defaults that are not saved
    def __getitem__(self, key):
        if key in self.overrides:
            return self.overrides[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in self.overrides:
            return self.overrides[key]
        return super().get(key, default)

    def __setitem__(self, key, value):
        if self.overrides:
            self.overrides.pop(key, None)   # an explicit edit wins over the session profile
        super().__setitem__(key, value)

    def set_session(self, values):
        self.overrides.update(values)

    def load(self):
        try:
            src = self.path
            if not os.path.exists(src) and os.path.exists(self.legacy):
                src = self.legacy
            with open(src) as f:
                data = json.load(f)
            roles = dict(ROLE_DEFAULTS)
            if 'cursor' not in (data.get('roles') or {}):
                roles['cursor'] = PRESET_CURSOR.get(data.get('preset', 'Ember'), roles['cursor'])
            roles.update(data.get('roles', {}))
            self.update(data)
            self.pop('image_boost', None)
            self['roles'] = roles
            if len(self['palette']) != 16:
                self['palette'] = list(PRESETS['Ember'])
        except FileNotFoundError:
            pass
        except Exception as e:  # corrupt config: keep defaults
            log('config load failed', e)

    def save(self):
        try:
            ensure_dirs()
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(dict(self), f, indent=1)
            os.chmod(tmp, 0o600)   # may contain the calendar secret
            os.replace(tmp, self.path)
        except OSError as e:
            log('config save failed', e)


class Theme:
    """Resolved palette (RGB tuples) + role indices + attr cache."""

    def __init__(self, config):
        self.cfg = config
        self.reload()

    def reload(self):
        self.rgb = [hex_rgb(h) for h in self.cfg['palette']]
        self.r = dict(ROLE_DEFAULTS)
        self.r.update(self.cfg['roles'])
        self._cache = {}

    def c(self, fg, bg, fl=0):
        """Attr from role names (or raw palette ints)."""
        k = (fg, bg, fl)
        a = self._cache.get(k)
        if a is None:
            f = self.r[fg] if isinstance(fg, str) else fg
            b = self.r[bg] if isinstance(bg, str) else bg
            a = A(f, b, fl)
            self._cache[k] = a
        return a

    def idx(self, role):
        return self.r[role]

    def contrast(self, slot):
        """A readable foreground role for a coloured block (e.g. the mouse cursor)."""
        r, g, b = self.rgb[slot]
        return self.r['shadow'] if (r * 299 + g * 587 + b * 114) // 1000 > 120 else self.r['bright']

    def nearest(self, rgb, allowed=None):
        r, g, b = rgb
        best, bd = 0, 1 << 30
        for i in (allowed if allowed is not None else range(16)):
            pr, pg, pb = self.rgb[i]
            d = (r - pr) ** 2 * 3 + (g - pg) ** 2 * 4 + (b - pb) ** 2 * 2
            if d < bd:
                best, bd = i, d
        return best
