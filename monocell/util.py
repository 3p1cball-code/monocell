"""Small helpers shared by everything: ASCII sanitising, text wrapping, paths."""
import os
import unicodedata

STRICT_ASCII = False      # set from config: transliterate even Latin-1
UNICODE = True            # single-width symbols/emoji in the UI (config: symbols)
SYMBOLS = 'auto'

# Single-width symbol pairs: (unicode, ascii fallback)
SYM = {
    'files': ('\u25a4', '[/]'), 'pdf': ('\u25a5', '[P]'), 'browser': ('\u25c9', '(@)'), 'terminal': ('>_', '>_'),
    'telegram': ('\u2708', '<">'), 'editor': ('\u270e', '[=]'), 'writer': ('\u00b6', '[W]'),
    'sheet': ('\u25a6', '[#]'), 'calc': ('\u00b1', '[+]'), 'calendar': ('\u25a3', '[31'),
    'viewer': ('\u25a7', '[*]'), 'video': ('\u25b6', '[>]'), 'music': ('\u266a', '(~)'),
    'system': ('\u24d8', '[i]'), 'settings': ('\u2699', '{o}'), 'dialog': ('\u26a0', '!'),
    'menu': ('\u2261', '=='), 'check': ('\u2713', 'v'), 'bullet': ('\u2022', '*'),
    'arrow': ('\u203a', '>'), 'dot': ('\u00b7', '.'),
    'bullet2': ('\u25e6', '-'), 'box_on': ('\u25a3', '[x]'), 'box_off': ('\u25a2', '[ ]'),
    'radio_on': ('\u25c9', '(*)'), 'radio_off': ('\u25cb', '( )'),
    'wifi': ('\u224b', ')))'), 'lan': ('[=]', '[=]'), 'bt': ('\u16d2', 'B'),
    'network': ('\u224b', ')))'), 'bright': ('\u2600', '*'),
    'sound': ('\u266a', '~'), 'bolt': ('\u26a1', '+'), 'opencode': ('\u276f', '[O]'),
    'mail': ('\u2709', '[M]'), 'maps': ('\u26f6', '[m]'),
    'folder': ('\u25b8', '>'), 'sent': ('\u2197', '^'),
    'draft': ('\u270e', '~'), 'trash': ('\u2717', 'x'), 'junk': ('\u26a0', '!'),
    'archive': ('\u25a4', '#'), 'attach': ('\u21a7', '@'), 'star': ('\u2605', '*'),
    'unstar': ('\u2606', '-'), 'reply': ('\u21a9', '<-'),
}


def sym(name, fallback=''):
    u, a = SYM.get(name, (None, None))
    if UNICODE and u:
        return u
    return a if a is not None else fallback

# Box drawing / block elements / typographic marks -> ASCII
_MAP = {}
for chars, rep in (
    ('─━┄┅┈┉╌╍═╼╾⎯', '-'), ('│┃┆┇┊┋╎╏║╽╿', '|'),
    ('┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛├┝┞┟┠┡┢┣┤┥┦┧┨┩┪┫┬┭┮┯┰┱┲┳┴┵┶┷┸┹┺┻┼┽┾┿╀╁╂╃╄╅╆╇╈╉╊╋'
     '╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬╭╮╯╰', '+'),
    ('╱', '/'), ('╲', '\\'), ('╳', 'X'),
    ('█▉▊▋', '#'), ('▓▒▌▐▍▎▏▀▄▆▅▇▃▂▁', '%'), ('░', ':'),
    ('■◼▪●◆♦⬤', '#'), ('□◻▫○◇◯⊙◎◉', 'o'), ('•·∙⋅‧', '*'),
    ('╹╻', '|'), ('✳✻✽✺', '*'), ('⏵', '>'), ('⏸', '='), ('⏺', 'o'), ('⎿', '|'), ('⧉', '='),
    ('‘’‚‛′`´', "'"), ('“”„‟″«»', '"'), ('–—―‒−⁃', '-'),
    ('…', '.'), ('→⟶➔➜▶►▸', '>'), ('←⟵◀◄◂', '<'), ('↑▲▴', '^'), ('↓▼▾', 'v'),
    ('✓✔☑', 'v'), ('✗✘✕✖☒', 'x'), ('★☆✦✧', '*'), ('♪♫♬', '~'),
    ('❯›»⟩\ue0b0\ue0b1\ue0b4\ue0b5\ue0bc', '>'), ('❮‹«⟨\ue0b2\ue0b3\ue0b6\ue0b7\ue0be', '<'),
    ('€', 'E'), ('™', 'T'), ('©', 'c'), ('®', 'R'), ('×', 'x'), ('÷', '/'),
    ('≤', '<'), ('≥', '>'), ('≠', '!'), ('≈', '~'), ('∞', '8'), ('\u00a0', ' '),
    ('\u200b\u200c\u200d\u2060\ufeff\ufe0f\ufe0e', ''),
):
    for c in chars:
        _MAP[c] = rep
_EMOJI = {'😀': ':D', '😃': ':D', '😄': ':D', '😁': ':D', '😆': 'xD', '😂': ":'D",
          '🙂': ':)', '😊': ':)', '😉': ';)', '😍': '<3', '😘': ':*', '😛': ':P',
          '😜': ';P', '😎': 'B)', '😢': ":'(", '😭': ":'(", '😞': ':(', '🙁': ':(',
          '😡': '>:(', '😮': ':O', '😐': ':|', '🤔': ':?', '👍': '(y)', '👎': '(n)',
          '❤': '<3', '💔': '</3', '🔥': '(fire)', '🎉': '\\o/', '👋': 'o/', '🙏': '(pray)'}
# emoji as single-width symbols when the terminal can show them
_EMOJI_U = {'😀': '☺', '😃': '☺', '😄': '☺', '😁': '☺', '😆': '☺', '😂': '☺',
            '🙂': '☺', '😊': '☺', '😉': '☺', '😍': '♥', '😘': '♥', '😛': '☺',
            '😜': '☺', '😎': '☺', '😢': '☹', '😭': '☹', '😞': '☹', '🙁': '☹',
            '😡': '☹', '😮': '☺', '😐': '☺', '🤔': '☺', '👍': '✓', '👎': '✗',
            '❤': '♥', '💔': '♥', '🔥': '♨', '🎉': '★', '👋': '☞', '🙏': '✝',
            '⚠': '⚠', '☀': '☀', '☾': '☾', '☂': '☂', '☕': '☕', '✈': '✈'}
_MAP.update(_EMOJI)
_cache = {}


def char_ascii(c):
    r = _cache.get(c)
    if r is not None:
        return r
    o = ord(c)
    if o < 32 or o == 127:
        r = ' '
    elif o < 127:
        r = c
    elif UNICODE and not STRICT_ASCII and c in _EMOJI_U:
        r = _EMOJI_U[c]
    elif c in _MAP:
        r = _MAP[c]
    elif 0xE000 <= o <= 0xF8FF or o >= 0xF0000:
        r = ' '          # private use (Nerd Font icons)
    elif 160 <= o < 256 and not STRICT_ASCII:
        r = c
    else:
        cat = unicodedata.category(c)
        if cat.startswith('M') or cat == 'Cf':
            r = ''
        else:
            d = unicodedata.normalize('NFKD', c).encode('ascii', 'ignore').decode()
            if not d:
                if c in 'ßẞ':
                    d = 'ss'
                elif c in 'æÆ':
                    d = 'ae'
                elif c in 'øØ':
                    d = 'o'
                else:
                    d = '?'
            r = d
    _cache[c] = r
    return r


# structural graphics always become ASCII; other single-width symbols may stay
_BOX = set('─━┄┅┈┉╌╍═╼╾⎯│┃┆┇┊┋╎╏║╽╿┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛├┝┞┟┠┡┢┣┤┥┦┧┨┩┪┫┬┭┮┯┰┱┲┳┴┵┶┷┸┹┺┻┼┽┾┿'
           '╀╁╂╃╄╅╆╇╈╉╊╋╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬╭╮╯╰╱╲╳'
           '█▉▊▋▓▒▌▐▍▎▏▀▄▆▅▇▃▂▁░')


def ascii_upper(s):
    """Uppercase ASCII for the graphics-mode pixel font (umlauts spelled out)."""
    for a, b in (('ä', 'AE'), ('ö', 'OE'), ('ü', 'UE'), ('ß', 'SS'),
                 ('Ä', 'AE'), ('Ö', 'OE'), ('Ü', 'UE')):
        s = s.replace(a, b)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ''.join(c if 32 <= ord(c) < 127 else '?' for c in s).upper()


def clean(s):
    """Return a string that occupies exactly one cell per character.

    ASCII by default; with UNICODE on, single-width symbols/letters stay as they are
    (the terminal falls back to a font that has them), wide glyphs are folded to ASCII.
    """
    if s.isascii() and s.isprintable():
        return s
    out = []
    for c in s:
        o = ord(c)
        if o < 32 or o == 127:
            out.append(' ')
            continue
        if o < 127:
            out.append(c)
            continue
        if c in _BOX:
            out.append(_MAP[c])
            continue
        if UNICODE and not STRICT_ASCII:
            cat = unicodedata.category(c)
            if (cat[0] in 'LNPS' or cat in ('So', 'Sk', 'Po')) and \
                    unicodedata.east_asian_width(c) in ('Na', 'N', 'A', 'H'):
                out.append(c)
                continue
        out.append(char_ascii(c))
    return ''.join(out)


def clear_cache():
    _cache.clear()


def wrap(text, width):
    """Word wrap a single paragraph; returns list of (start, end) offsets."""
    if width < 1:
        width = 1
    out = []
    n = len(text)
    if n == 0:
        return [(0, 0)]
    i = 0
    while i < n:
        if n - i <= width:
            out.append((i, n))
            break
        j = text.rfind(' ', i, i + width + 1)
        if j <= i:
            out.append((i, i + width))
            i += width
        else:
            out.append((i, j))
            i = j + 1
    return out


def wrap_text(text, width):
    lines = []
    for para in text.split('\n'):
        for a, b in wrap(para, width):
            lines.append(para[a:b].rstrip(' ') if b < len(para) else para[a:b])
    return lines


def fit(s, w, align='left'):
    s = clean(s)
    if len(s) > w:
        if w <= 1:
            return s[:w]
        return s[:w - 1] + '~'
    if align == 'right':
        return s.rjust(w)
    if align == 'center':
        return s.center(w)
    return s.ljust(w)


def human_size(n):
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if n < 1024 or unit == 'T':
            return ('%d%s' % (n, unit)) if unit == 'B' else ('%.1f%s' % (n, unit))
        n /= 1024.0


HOME = os.path.expanduser('~')
CONFIG_DIR = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.join(HOME, '.config')), 'monocell')
DATA_DIR = os.path.join(os.environ.get('XDG_DATA_HOME', os.path.join(HOME, '.local', 'share')), 'monocell')
CACHE_DIR = os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.join(HOME, '.cache')), 'monocell')


def ensure_dirs():
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)


def which(name):
    for p in os.environ.get('PATH', '/usr/bin:/bin').split(':'):
        f = os.path.join(p, name)
        if os.path.isfile(f) and os.access(f, os.X_OK):
            return f
    return None


def shorten_path(p, w):
    p = p.replace(HOME, '~', 1) if p.startswith(HOME) else p
    if len(p) <= w:
        return p
    return '...' + p[-(w - 3):]


def log(*a):
    try:
        ensure_dirs()
        with open(os.path.join(CACHE_DIR, 'monocell.log'), 'a') as f:
            f.write(' '.join(str(x) for x in a) + '\n')
    except OSError:
        pass
