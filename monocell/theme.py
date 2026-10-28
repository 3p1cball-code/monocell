"""16-colour palette, semantic roles, presets and persistent config."""
import json
import os
import re

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
    # neutral like Ember, only the accent is green: the tint belongs to the rain,
    # not to the whole desktop ("no global filter")
    'Matrix': ['0c0c0c', 'e0574a', '8db86b', 'e8c26a', '6b9bd1', 'c08cc0', '6db8b4', '7c7c7c',
               '161616', '202020', '2c2c2c', '424242', 'c4c4c4', 'f2f2f2', '8db86b', 'b8e0a0'],
    'Paper': ['9a9a9a', 'b83a2e', '3f7a2a', '9a6b00', '2f5f9e', '8a3f8a', '237878', '7a7a7a',
              'd6d3cc', 'f2efe8', 'e4e0d6', 'c9c4b8', '2b2b2b', '000000', 'd9600b', 'b04a00'],
}

ROLE_DEFAULTS = {
    'desk': 8, 'desk_fg': 11, 'shadow': 0,
    'win': 9, 'win_on': 10,                 # window body: inactive / focused
    'title_on': 11, 'title_off': 9,         # title bar: focused / inactive
    'raised': 10, 'raised_on': 11,          # buttons: elsewhere / inside the focused window
    'sel': 11, 'field': 8, 'bar': 10, 'menu': 10,
    'text': 12, 'dim': 7, 'bright': 13, 'accent': 14, 'accent2': 15, 'on_accent': 0,
    'cursor': 6,
    'unread': 14,                           # new mail in the mail client
    'red': 1, 'green': 2, 'yellow': 3, 'blue': 4, 'magenta': 5, 'cyan': 6,
}
ROLE_HELP = {
    'desk': 'Desktop background', 'desk_fg': 'Desktop text / logo', 'shadow': 'Window shadow',
    'win': 'Window background', 'win_on': 'Focused window background',
    'title_on': 'Active title bar', 'title_off': 'Inactive title bar',
    'raised': 'Buttons / headers', 'raised_on': 'Buttons in the focused window',
    'sel': 'Selection', 'field': 'Input fields', 'bar': 'Taskbar', 'menu': 'Menus',
    'text': 'Text', 'dim': 'Dim text', 'bright': 'Bright text', 'accent': 'Highlight',
    'accent2': 'Highlight 2', 'on_accent': 'Text on highlight',
    'cursor': 'Mouse cursor', 'unread': 'New mail',
}
# element text colours that are part of the default look (the inactive title is dim)
DEFAULT_ON = {'title_off': ROLE_DEFAULTS['dim']}

# Roles that paint a surface (a background colour). A kernel VT can only show 8
# distinct backgrounds, so `layout` puts these slots into its 8 background slots.
# The order decides which entry gets which VT slot when a layout is derived; the
# focused-window roles sit where the old 'title' and 'sel' were, so every derived
# layout is the same as before they existed.
SURFACE_ROLES = ('desk', 'win', 'win_on', 'title_on', 'title_off', 'raised_on', 'sel',
                 'accent', 'shadow', 'field', 'bar', 'menu', 'raised')
# version 1-3 themes: the old roles before the focused window got its own ones
LEGACY_DEFAULTS = dict(ROLE_DEFAULTS, title=10)


def migrate_roles(raw, on=None, preset=None):
    """Role map and element text colours from any theme version -> (roles, on).

    Up to version 3 the focused window was derived from slot numbers: its body
    took the 'title' slot, its title bar and buttons moved one step further to
    'sel'. Version 4 names those colours, so they can be edited one by one; the
    migration reproduces the exact old look.
    """
    raw = {k: v for k, v in (raw or {}).items() if isinstance(v, int) and 0 <= v < 16}
    on = dict(on or {})
    if 'cursor' not in raw:
        raw['cursor'] = PRESET_CURSOR.get(preset, ROLE_DEFAULTS['cursor'])
    if 'win_on' not in raw:
        base = dict(LEGACY_DEFAULTS, **raw)
        win, title, sel = base['win'], base['title'], base['sel']
        lift = {win: title, title: sel}         # (a shared slot went the whole way)
        raw.update(win_on=lift[win], title_on=sel, title_off=win,
                   raised_on=lift.get(base['raised'], base['raised']))
        if 'title' in on:                      # v3 keyed the old title bar text 'title'
            on['title_on'] = on.pop('title')
        on.setdefault('title_off', base['dim'])
    roles = dict(ROLE_DEFAULTS)
    roles.update({k: v for k, v in raw.items() if k in ROLE_DEFAULTS})
    return roles, clean_on(on, roles)


def surface_slots(roles, skip=None):
    """Palette entries that some surface paints as a background."""
    return {roles[r] for r in SURFACE_ROLES if r != skip and r in roles}


def assign_surface(roles, layout, role, slot):
    """Give `role` the background `slot` -> (roles, layout).

    The first 8 layout entries are the only ones a VT can paint as a background.
    A text-only entry swaps places with a background entry that no surface uses
    (preferably one nothing uses, then one without the body text colours). When
    all 8 already carry a surface the theme cannot take a 9th: ValueError.
    """
    roles = dict(roles)
    lay = list(layout) if valid_layout(layout) else derive_layout(roles)
    roles[role] = slot
    if slot in lay[:8]:
        return roles, lay
    used = surface_slots(roles)
    free = [p for p in lay[:8] if p not in used]
    if not free:
        raise ValueError('all 8 background colours are in use')

    def cost(p):
        users = {r for r, v in roles.items() if v == p}
        return (0 if not users else 2 if users & {'text', 'dim'} else 1, lay.index(p))
    p = min(free, key=cost)
    i, j = lay.index(p), lay.index(slot)
    lay[i], lay[j] = lay[j], lay[i]
    return roles, lay


def derive_layout(roles):
    """Physical palette order: [0:8] may be painted as background, [8:] is text only.

    Surface roles own the low half, then 'dim' and 'text' so ordinary text stays
    below slot 8 -- a VT renders fg >= 8 as bold, which must be opt-in.
    """
    lay = []
    for role in SURFACE_ROLES + ('dim', 'text'):
        i = roles.get(role)
        if isinstance(i, int) and 0 <= i < 16 and i not in lay and len(lay) < 8:
            lay.append(i)
    return lay + [i for i in range(16) if i not in lay]


def valid_layout(lay):
    return isinstance(lay, list) and len(lay) == 16 and sorted(lay) == list(range(16))


def clean_on(on, roles=None):
    """{element role: text slot} -- the text colour that belongs to a surface.

    Version 2 stored the surface *slot* as the key; pass `roles` to convert those.
    """
    out = {}
    for k, v in (on or {}).items():
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if not 0 <= v < 16:
            continue
        if isinstance(k, str) and k in SURFACE_ROLES:
            out[k] = v
        elif roles is not None:
            try:
                slot = int(k)
            except (TypeError, ValueError):
                continue
            for role in SURFACE_ROLES:
                if roles.get(role) == slot:
                    out[role] = v
                    break
    return out


# a cursor colour per preset, distinct from accent and selection
PRESET_CURSOR = {
    'Ember': 6, 'Graphite Blue': 5, 'Amber CRT': 13, 'Green Phosphor': 13,
    'CGA': 5, 'Gruvbox': 6, 'Nord': 4, 'Paper': 5, 'Matrix': 13,
}

DEFAULT_CONFIG = {
    'preset': 'Ember',
    'palette': PRESETS['Ember'],
    'roles': dict(ROLE_DEFAULTS),
    'layout': [],                 # physical palette order; empty = derived from the roles
    'on': dict(DEFAULT_ON),       # text colour per element {surface role: slot}
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
    'gfx_look': 'vga',            # graphics mode (Linux console): vga|ega|theme|cga|amber|green
    'gfx_dither': 'bayer',        # bayer | none
    'gfx_scanlines': False,
    'gfx_fps': 0,                 # 0 = auto (25; never above the video's own rate)
    'map_tiles': 'osm',           # osm|osmde|light|dark|topo or an own {z}/{x}/{y} URL
    'map_lat': 52.5200,           # last map position
    'map_lon': 13.4050,
    'map_zoom': 12,
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


# ---------------------------------------------------------------- theme files
# A theme is a palette plus a full role map, stored as plain JSON so it can be
# copied, edited or shared: {version, name, palette: [16 hex], roles: {role: slot}}
THEME_DIR = os.path.join(CONFIG_DIR, 'themes')


def theme_slug(name):
    """File stem for a theme name: no path separators, stable, readable."""
    s = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower()).strip('-._')
    return (s or 'theme')[:48]


def saved_themes():
    """Saved themes as {name: {'palette', 'roles', 'path'}}; broken files are skipped."""
    out = {}
    try:
        files = sorted(os.listdir(THEME_DIR))
    except OSError:
        return out
    for fn in files:
        if not fn.endswith('.json'):
            continue
        path = os.path.join(THEME_DIR, fn)
        try:
            with open(path) as f:
                data = json.load(f)
            pal = [rgb_hex(hex_rgb(h)) for h in data['palette']]
            if len(pal) != 16:
                raise ValueError('needs 16 colours')
            roles, on = migrate_roles(data.get('roles'), data.get('on'))
            lay = data.get('layout')
            if not valid_layout(lay):
                lay = derive_layout(roles)
        except (OSError, ValueError, KeyError, TypeError) as e:
            log('theme %s ignored: %s' % (fn, e))
            continue
        out[str(data.get('name') or fn[:-5])] = {'palette': pal, 'roles': roles,
                                                 'layout': lay, 'on': on, 'path': path}
    return out


def save_theme(name, palette, roles, layout=None, on=None):
    """Write palette, layout, role map and element text colours (version 4)."""
    name = ' '.join(str(name).split())
    if not name:
        raise ValueError('empty name')
    pal = [rgb_hex(hex_rgb(h)) for h in palette]
    if len(pal) != 16:
        raise ValueError('a theme needs 16 colours')
    roles = {k: int(v) for k, v in roles.items() if k in ROLE_DEFAULTS}
    lay = list(layout or [])
    if not valid_layout(lay):
        lay = derive_layout(dict(ROLE_DEFAULTS, **roles))
    ensure_dirs()
    os.makedirs(THEME_DIR, exist_ok=True)
    path = os.path.join(THEME_DIR, theme_slug(name) + '.json')
    data = {'version': 4, 'name': name, 'palette': pal, 'layout': lay,
            'roles': roles,
            'on': {k: int(v) for k, v in clean_on(on, roles).items()}}
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    return name


def delete_theme(name):
    """Remove a saved theme by display name; True when a file was removed."""
    t = saved_themes().get(name)
    if not t:
        return False
    try:
        os.remove(t['path'])
        return True
    except OSError as e:
        log('theme delete failed: %s' % e)
        return False


class Config(dict):
    path = os.path.join(CONFIG_DIR, 'config.json')
    # a 0.x install kept its config next to this one; read it once, then save to the new path
    legacy = os.path.join(os.path.dirname(CONFIG_DIR), 'textwin', 'config.json')

    def __init__(self):
        super().__init__(json.loads(json.dumps(DEFAULT_CONFIG)))
        self.overrides = {}
        self.user_set = set()     # keys the user edited and saved; session profiles skip them
        self._loading = False
        self.load()
        # a fresh or inherited config has no layout yet: derive it from the roles
        if not valid_layout(self['layout']):
            self['layout'] = derive_layout(self['roles'])

    # session overrides (e.g. --lowmem): defaults for values the user never set explicitly
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
        if not self._loading:
            self.user_set.add(key)
        super().__setitem__(key, value)

    def set_session(self, values):
        for key, value in values.items():
            if key not in self.user_set:    # the user's own settings always win
                self.overrides[key] = value

    def load(self):
        try:
            src = self.path
            if not os.path.exists(src) and os.path.exists(self.legacy):
                src = self.legacy
            with open(src) as f:
                data = json.load(f)
            explicit = data.pop('user_set', None)
            if explicit is None:
                # older config: everything that differs from the default was set by hand
                explicit = [k for k, v in data.items()
                            if k in DEFAULT_CONFIG and v != DEFAULT_CONFIG[k]]
            self.user_set = set(explicit)
            roles, on = migrate_roles(data.get('roles'), data.get('on'),
                                      data.get('preset', 'Ember'))
            self._loading = True
            try:
                self.update(data)
                self.pop('image_boost', None)
                self['roles'] = roles
                if len(self['palette']) != 16:
                    self['palette'] = list(PRESETS['Ember'])
                self['on'] = on
                # a saved theme file is the source of truth for its own name, so
                # hand-editing ~/.config/monocell/themes/<name>.json takes effect
                if self['preset'] not in PRESETS:
                    th = saved_themes().get(self['preset'])
                    if th:
                        if len(th['palette']) == 16:
                            self['palette'] = list(th['palette'])
                        self['roles'] = dict(th['roles'])
                        self['layout'] = th['layout']
                        self['on'] = dict(th['on'])
                if not valid_layout(self['layout']):
                    self['layout'] = derive_layout(self['roles'])
            finally:
                self._loading = False
        except FileNotFoundError:
            pass
        except Exception as e:  # corrupt config: keep defaults
            self._loading = False
            log('config load failed', e)

    def save(self):
        try:
            ensure_dirs()
            tmp = self.path + '.tmp'
            data = dict(self)
            data['user_set'] = sorted(self.user_set)
            data['on'] = {k: int(v) for k, v in clean_on(self['on']).items()}
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=1)
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
        lay = list(self.cfg.get('layout') or [])
        if not valid_layout(lay):
            lay = derive_layout(self.r)
            self.cfg['layout'] = lay
        self.layout = lay
        self.low = lay[:8]              # entries a kernel VT can paint as a background
        self.on = clean_on(self.cfg.get('on'), self.r)
        self._cache = {}

    def c(self, fg, bg, fl=0):
        """Attr from role names (or raw palette ints)."""
        k = (fg, bg, fl)
        a = self._cache.get(k)
        if a is None:
            f = self.r[fg] if isinstance(fg, str) else fg
            b = self.r[bg] if isinstance(bg, str) else bg
            if fg in ('text', 'bright') and isinstance(bg, str) and bg in self.on:
                f = self.on[bg]                    # text colour of that element
            a = A(f, b, fl)
            self._cache[k] = a
        return a

    def idx(self, role):
        return self.r[role]

    def lift(self):
        """Background slot -> slot inside the focused window (body and buttons)."""
        m = {}
        for base, up in (('raised', 'raised_on'), ('win', 'win_on')):   # body wins a tie
            if self.r[base] != self.r[up]:
                m[self.r[base]] = self.r[up]
        return m

    def lift_fn(self):
        m = self.lift()

        def up(a):
            bg = (a >> 4) & 15
            return (a & ~0xf0) | (m.get(bg, bg) << 4)
        return up if m else None

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
