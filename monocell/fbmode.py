"""Real graphics on the Linux console, the way a DOS game left the prompt.

The VT is switched to KD_GRAPHICS - the console stops drawing text, like a DOS
game switching to mode 13h - and finished frames go straight into the
framebuffer (/dev/fb0). Only the video player uses it, always full screen.

ffmpeg does all the pixel work in C: fit the video into a small canvas (a
quarter of the screen resolution: 256x192 on a 1024x768 panel), reduce it to the colours of a classic PC
graphics adapter with ordered (Bayer) dithering, scale it up with nearest
neighbour - hard square pixels, no filtering - and convert it to the
framebuffer's pixel format. Python only copies frames and paints a small
on-screen display in a 3x5 pixel font.

    python3 -m monocell.fbmode VIDEO OUT.png [--look vga] [--at 10]
renders one frame through exactly this pipeline (no framebuffer needed).
"""
import atexit
import fcntl
import hashlib
import os
import re
import subprocess
import time

from .util import CACHE_DIR

# Tests and screenshots: MONOCELL_FB=<file> uses a plain file as the framebuffer
# (MONOCELL_FB_GEOMETRY=1024x768x32) and leaves the console mode alone.
FAKE = os.environ.get('MONOCELL_FB', '')
FAKE_GEOMETRY = os.environ.get('MONOCELL_FB_GEOMETRY', '1024x768x32')

KDSETMODE = 0x4B3A
KD_TEXT, KD_GRAPHICS = 0, 1

EGA = ['000000', '0000aa', '00aa00', '00aaaa', 'aa0000', 'aa00aa', 'aa5500', 'aaaaaa',
       '555555', '5555ff', '55ff55', '55ffff', 'ff5555', 'ff55ff', 'ffff55', 'ffffff']
CGA = ['000000', '55ffff', 'ff55ff', 'ffffff']          # mode 4, palette 1, high intensity

# key: (name on screen, colours, explanation)
LOOKS = {
    'vga': ('VGA 256', 256, '256 colours, one palette tuned to the video (like a game palette)'),
    'ega': ('EGA 16', 16, 'the 16 colours of the EGA palette'),
    'theme': ('THEME 16', 16, 'the 16 colours of the desktop theme'),
    'cga': ('CGA 4', 4, 'black, cyan, magenta and white'),
    'amber': ('AMBER', 4, 'amber monochrome monitor, highlights burn to white'),
    'green': ('GREEN', 4, 'green phosphor monitor, highlights burn to white'),
}
LOOK_ORDER = ['vga', 'ega', 'theme', 'cga', 'amber', 'green']
OLD_LOOKS = {'herc': 'amber'}                       # names of earlier versions
# monochrome monitors, darkest to brightest: off, glow, phosphor, burnt-out highlight
PHOSPHOR = {
    'amber': [(0, 0, 0), (122, 72, 0), (255, 170, 0), (255, 240, 205)],
    'green': [(0, 0, 0), (12, 104, 36), (51, 255, 102), (226, 255, 230)],
}
# Two and four colours work on brightness, as monochrome and CGA conversions did:
# the picture becomes grey, is dithered to evenly spaced grey levels and every
# level is then coloured (darkest to brightest). Nearest-RGB matching would turn
# a dark blue night into solid orange or magenta.
LUMA_LOOKS = ('cga', 'amber', 'green')
# the monitors get more contrast, so that bright parts really reach the white
TONE = {'cga': 'eq=gamma=1.25:contrast=1.15',
        'amber': 'eq=gamma=1.1:contrast=1.3', 'green': 'eq=gamma=1.1:contrast=1.3'}
# a smaller bayer_scale is a stronger ordered dither: few colours need it to carry shades
BAYER_SCALE = {'vga': 2, 'theme': 2, 'ega': 1, 'cga': 1}
SCALE = 4                              # screen pixels per picture pixel: a quarter resolution
DITHERS = ('bayer', 'none')
PIX_FMT = {32: 'bgr0', 24: 'bgr24', 16: 'rgb565le'}


def hex_rgb(h):
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ------------------------------------------------------------------ console mode
_entered = set()


def set_mode(fd, mode):
    fcntl.ioctl(fd, KDSETMODE, mode)


def enter(fd):
    """Switch the VT on `fd` to graphics: the console stops drawing text."""
    set_mode(fd, KD_GRAPHICS)
    _entered.add(fd)


def leave(fd):
    _entered.discard(fd)
    try:
        set_mode(fd, KD_TEXT)
    except OSError:
        pass


def restore():
    """Back to text mode on every VT we switched (exit, crash, signal)."""
    for fd in list(_entered):
        leave(fd)


atexit.register(restore)


def ensure_text(fd):
    """A previous run that died in graphics mode leaves a dark console: undo that."""
    try:
        set_mode(fd, KD_TEXT)
    except OSError:
        pass


def tty_name(fd):
    try:
        return os.path.basename(os.ttyname(fd))
    except OSError:
        return ''


def vt_active(name, path='/sys/class/tty/tty0/active'):
    """True when VT `name` (e.g. 'tty1') is the one on screen."""
    try:
        with open(path) as f:
            return f.read().strip() == name
    except OSError:
        return True


# ------------------------------------------------------------------ framebuffer
class Framebuffer:
    """/dev/fbN with its visible size, pixel format and line stride."""

    def __init__(self, dev='/dev/fb0', sysfs=None, geometry=None):
        self.dev = dev
        if geometry:
            self.w, self.h, self.bpp, self.stride = geometry
        else:
            self.w, self.h, self.bpp, self.stride = read_geometry(
                sysfs or os.path.join('/sys/class/graphics', os.path.basename(dev)))
        if self.bpp not in PIX_FMT:
            raise OSError('unsupported framebuffer depth: %d bit' % self.bpp)
        self.bytes_pp = self.bpp // 8
        self.size = self.stride * self.h
        self.fd = os.open(dev, os.O_RDWR | os.O_CREAT if geometry else os.O_RDWR)

    @property
    def pix_fmt(self):
        return PIX_FMT[self.bpp]

    @property
    def line_px(self):
        """Pixels per line including padding (what ffmpeg has to deliver)."""
        return self.stride // self.bytes_pp

    def pixel(self, rgb):
        r, g, b = rgb
        if self.bpp == 32:
            return bytes((b, g, r, 0))
        if self.bpp == 24:
            return bytes((b, g, r))
        v = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3
        return bytes((v & 255, v >> 8))

    def write(self, data, offset=0):
        os.pwrite(self.fd, data, offset)

    def clear(self):
        self.write(bytes(self.size))

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def read_geometry(sysfs):
    def rd(name):
        with open(os.path.join(sysfs, name)) as f:
            return f.read().strip()
    bpp = int(rd('bits_per_pixel'))
    stride = int(rd('stride'))
    try:                                   # visible mode, e.g. 'U:1024x768p-0'
        m = re.search(r'(\d+)x(\d+)', rd('modes'))
        w, h = int(m.group(1)), int(m.group(2))
    except (OSError, AttributeError):
        w, h = (int(v) for v in rd('virtual_size').split(','))
    return w, h, bpp, stride


def open_framebuffer():
    if FAKE:
        w, h, bpp = (int(v) for v in FAKE_GEOMETRY.split('x'))
        return Framebuffer(FAKE, geometry=(w, h, bpp, w * bpp // 8))
    return Framebuffer()


def available(term, dev='/dev/fb0'):
    """(True, '') when graphics mode can run here, else (False, reason)."""
    if FAKE:
        return True, ''
    if getattr(term, 'mode', '') != 'linux':
        return False, 'graphics mode needs the Linux console (not a terminal window)'
    if not tty_name(term.fd_out).startswith('tty'):
        return False, 'graphics mode needs a virtual console'
    if not os.path.exists(dev):
        return False, 'no framebuffer (%s)' % dev
    if not os.access(dev, os.R_OK | os.W_OK):
        return False, 'no access to %s (add the user to the video group)' % dev
    return True, ''


# ------------------------------------------------------------------ pipeline
def canvas(screen, scale):
    """Size of the picture canvas: the screen divided by the pixel size."""
    W, H = screen
    return max(1, W // scale), max(1, H // scale)


def fit(vw, vh, sar, cw, ch):
    """Video size inside the canvas, square pixels, aspect kept."""
    dw = max(1.0, vw * (sar or 1.0))
    dh = max(1.0, float(vh))
    k = min(cw / dw, ch / dh)
    return max(1, int(dw * k + 0.5)), max(1, int(dh * k + 0.5))


def uniform_256():
    """Fallback VGA palette: a 6x7x6 colour cube plus four greys."""
    out = [(round(r * 255 / 5), round(g * 255 / 6), round(b * 255 / 5))
           for r in range(6) for g in range(7) for b in range(6)]
    return out + [(32, 32, 32), (96, 96, 96), (160, 160, 160), (224, 224, 224)]


def video_palette_path(path):
    """Cache file of the tuned palette of one video (keyed by path, size and mtime)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(('%s|%d|%d' % (os.path.abspath(path), st.st_size,
                                       int(st.st_mtime))).encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, 'gfxvga-%s.png' % key)


def make_video_palette(path, timeout=180, sample=90.0):
    """One 256-colour palette for the video, from the key frames of its first
    `sample` seconds.

    Only key frames are decoded, and only for a sample of the film: reading a
    whole 188 MB episode over a network share competes with playback itself (the
    picture stalled at 16 instead of 25 frames a second). Returns the cached PNG
    path or None.
    """
    out = video_palette_path(path)
    if not out:
        return None
    if os.path.exists(out):
        return out
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = out[:-4] + '.tmp.png'
    # nice: this must never starve playback (it runs while the film is playing)
    # -t BEFORE -i stops reading the file after the sample; as an output option it
    # still reads all of it (31 s instead of 2 s for a 22-minute episode)
    cmd = ['nice', '-n', '15', 'ffmpeg', '-v', 'error', '-nostdin', '-y',
           '-skip_frame', 'nokey', '-t', '%.0f' % sample, '-i', path,
           '-an', '-sn', '-vf', 'scale=256:-2:flags=area,'
           'palettegen=max_colors=256:stats_mode=full:reserve_transparent=0',
           '-frames:v', '1', '-update', '1', tmp]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout, check=True)
        os.replace(tmp, out)
        return out
    except (OSError, subprocess.SubprocessError):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None


def palette_colours(look, theme_rgb=None, accent=None):
    if look == 'vga':
        return uniform_256()
    if look == 'ega':
        return [hex_rgb(h) for h in EGA]
    if look == 'cga':                     # by brightness: black, magenta, cyan, white
        return [hex_rgb(h) for h in (CGA[0], CGA[2], CGA[1], CGA[3])]
    if look == 'theme':
        return list(theme_rgb or [hex_rgb(h) for h in EGA])
    if look in PHOSPHOR:
        return list(PHOSPHOR[look])
    raise ValueError('unknown look: %s' % look)


def grey_levels(n):
    return [round(i * 255 / (n - 1)) for i in range(n)]


def colourize(colours):
    """lutrgb mapping the grey levels 0..n-1 (evenly spaced) to `colours`."""
    n = len(colours)
    lv = grey_levels(n)
    cuts = [(lv[i] + lv[i + 1]) // 2 for i in range(n - 1)]
    exprs = []
    for ch in range(3):
        e = str(colours[-1][ch])
        for i in range(n - 2, -1, -1):
            e = 'if(lt(val,%d),%d,%s)' % (cuts[i], colours[i][ch], e)
        exprs.append(e)
    return "lutrgb=r='%s':g='%s':b='%s'" % tuple(exprs)


def write_cached(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if not os.path.exists(path):
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def palette_file(colours):
    """A 16x16 PPM for ffmpeg's paletteuse (256 entries; repeats are harmless)."""
    pix = (list(colours) * 256)[:256]
    data = b'P6\n16 16\n255\n' + bytes(c for rgb in pix for c in rgb)
    return write_cached('gfxpal-%s.ppm' % hashlib.sha1(data).hexdigest()[:12], data)


BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def bayer_file(w, h, step):
    """Grey threshold pattern (classic 4x4 Bayer) scaled to one grey-level step."""
    rows = []
    for y in range(h):
        m = BAYER4[y % 4]
        line = bytes(int((m[x % 4] + 0.5) * step / 16) for x in range(4))
        rows.append((line * (w // 4 + 1))[:w])
    data = b'P5\n%d %d\n255\n' % (w, h) + b''.join(rows)
    return write_cached('gfxbayer-%dx%d-%d.pgm' % (w, h, step), data)


def scanline_file(w, h, scale):
    """Grey mask: the last row of every picture pixel is darker, the rest untouched."""
    dark = bytes([110]) * w
    full = bytes([255]) * w
    rows = b''.join(dark if y % scale == scale - 1 else full for y in range(h))
    data = b'P5\n%d %d\n255\n' % (w, h) + rows
    return write_cached('gfxscan-%dx%d-%d.pgm' % (w, h, scale), data)


def build(video, screen, line_px, pix_fmt, look='vga', scale=SCALE, dither='bayer',
          scanlines=False, fps=25, palette=None, mask=None, colours=None, pattern=None,
          down='area', bayer_scale=None):
    """(extra inputs, filter graph) for one look. `video`: probe info of the stream.

    `fps=None` keeps the input rate untouched (still images, where the source is
    already exactly one frame per picture)."""
    cw, ch = canvas(screen, scale)
    fw, fh = fit(video.get('w', 16), video.get('h', 9), video.get('sar', 1.0), cw, ch)
    W, H = screen
    uw, uh = cw * scale, ch * scale
    x0, y0 = (W - uw) // 2, (H - uh) // 2
    bs = bayer_scale or BAYER_SCALE.get(look, 2)
    d = 'bayer:bayer_scale=%d' % bs if dither == 'bayer' else 'none'
    luma = look in LUMA_LOOKS
    pre = '' if fps is None else 'fps=%d,' % fps
    inputs = []
    if luma:
        # ordered dither by hand: add a Bayer threshold, round down to the grey levels
        n = len(colours)
        step = 255 // (n - 1)
        g = ['[0:v]%sscale=%d:%d:flags=%s,setsar=1,format=gray,%s,'
             'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black[g]' % (pre, fw, fh, down, TONE[look], cw, ch)]
        if dither == 'bayer':
            inputs.append(pattern)
            g.append('[g][1:v]blend=all_mode=addition[gd]')
            src, bias = 'gd', 0
        else:
            src, bias = 'g', step // 2
        g.append("[%s]lut=c0='min(floor((val+%d)/%d),%d)*%d',format=rgb24,%s[q]"
                 % (src, bias, step, n - 1, step, colourize(colours)))
    else:
        g = ['[0:v]%sscale=%d:%d:flags=%s,setsar=1,format=rgb24[s]' % (pre, fw, fh, down)]
        inputs.append(palette)
        g.append('[s][1:v]paletteuse=dither=%s[q]' % d)
    g.append('[q]format=rgb24,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,'
             'scale=%d:%d:flags=neighbor[u]' % (cw, ch, uw, uh))
    last = 'u'
    if scanlines and mask:
        inputs.append(mask)
        g.append('[u]format=gbrp[u1];[%d:v]format=gbrp[m];'
                 '[u1][m]blend=all_mode=multiply[u2]' % len(inputs))
        last = 'u2'
    g.append('[%s]pad=%d:%d:%d:%d:black,format=%s[out]' % (last, line_px, H, x0, y0, pix_fmt))
    return inputs, ';'.join(g), (cw, ch)


def command(path, offset, video, screen, line_px, pix_fmt, theme_rgb=None, accent=None,
            look='vga', scale=SCALE, dither='bayer', scanlines=False, fps=25, vga_palette=None,
            down='area'):
    """The ffmpeg command line producing raw framebuffer frames on stdout.

    `vga_palette`: the tuned palette of this video (make_video_palette); without
    it VGA uses an even colour cube."""
    pal = pattern = None
    colours = palette_colours(look, theme_rgb, accent)
    if look in LUMA_LOOKS:                 # dither in grey, colour afterwards
        cw, ch = canvas(screen, scale)
        pattern = bayer_file(cw, ch, 255 // (len(colours) - 1))
    elif look == 'vga' and vga_palette:
        pal = vga_palette
    else:
        pal = palette_file(colours)
    mask = None
    if scanlines:
        cw, ch = canvas(screen, scale)
        mask = scanline_file(cw * scale, ch * scale, scale)
    inputs, graph, cv = build(video, screen, line_px, pix_fmt, look, scale, dither,
                              scanlines, fps, pal, mask, colours, pattern, down)
    # the picture ends up a few hundred pixels wide: a sloppier, faster h264 decode
    # is invisible at that size and matters on a Core 2 Duo
    cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2',
           '-skip_loop_filter', 'all', '-flags2', '+fast',
           '-ss', '%.3f' % offset, '-i', path]
    for p in inputs:
        cmd += ['-i', p]
    cmd += ['-an', '-sn', '-filter_complex', graph, '-map', '[out]',
            '-f', 'rawvideo', '-pix_fmt', pix_fmt, '-']
    return cmd, cv


def image_command(size, screen, line_px, pix_fmt, theme_rgb=None, accent=None, look='vga',
                  scale=SCALE, dither='bayer', scanlines=False, vga_palette=None,
                  down='area', bayer_scale=None, out='raw'):
    """The same look for still pictures (the map app): the input comes on stdin as one
    raw rgb24 frame of `size` canvas pixels, each frame in gives one framebuffer frame.

    Everything after the source is shared with `command()`: identical palettes, Bayer
    pattern, scanline mask, nearest-neighbour upscale. `out='ppm'` is for the long
    running StillPipeline: the rawvideo muxer buffers a frame until the next one
    arrives, the image2pipe muxer flushes every picture."""
    colours = palette_colours(look, theme_rgb, accent)
    pal = pattern = mask = None
    if look in LUMA_LOOKS:
        cw, ch = canvas(screen, scale)
        pattern = bayer_file(cw, ch, 255 // (len(colours) - 1))
    elif look == 'vga' and vga_palette:
        pal = vga_palette
    else:
        pal = palette_file(colours)
    if scanlines:
        cw, ch = canvas(screen, scale)
        mask = scanline_file(cw * scale, ch * scale, scale)
    video = {'w': size[0], 'h': size[1], 'sar': 1.0}
    inputs, graph, cv = build(video, screen, line_px, pix_fmt, look, scale, dither,
                              scanlines, None, pal, mask, colours, pattern, down,
                              bayer_scale)
    cmd = ['ffmpeg', '-v', 'error', '-nostdin',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '%dx%d' % (size[0], size[1]),
           '-i', '-']
    for p in inputs:
        cmd += ['-i', p]
    cmd += ['-an', '-sn', '-filter_complex', graph, '-map', '[out]']
    if out == 'ppm':
        cmd += ['-f', 'image2pipe', '-c:v', 'ppm', '-']
    else:
        cmd += ['-f', 'rawvideo', '-pix_fmt', pix_fmt, '-']
    return cmd, cv


def image_frame(raw, size, screen, line_px, pix_fmt, **kw):
    """One still through the graphics pipeline: (framebuffer bytes, canvas size)."""
    cmd, cv = image_command(size, screen, line_px, pix_fmt, **kw)
    out = subprocess.run(cmd, input=raw, capture_output=True)
    return out.stdout, cv


class StillPipeline:
    """A long running filter chain for still pictures: canvas frames in, framebuffer
    frames out - the video player's looks, for something that is redrawn by hand.

    A new ffmpeg per picture costs ~190 ms just to start up, so the process stays
    alive. Its output must not be rawvideo: that muxer holds a picture back until the
    next one arrives (one frame in, nothing out). The image2pipe muxer flushes every
    PPM, so the picture comes back as P6 and the framebuffer's pixel format is made
    here (PIL, in C) - about 3 ms for 1024x768.
    """

    PPM_TARGET = {32: 'BGRX', 24: 'BGR', 16: 'BGR;16'}

    def __init__(self, size, fb, **kw):
        self.fb = fb
        self.size = (int(size[0]), int(size[1]))
        if fb.bpp not in self.PPM_TARGET:
            raise OSError('unsupported framebuffer depth: %d bit' % fb.bpp)
        self.target = self.PPM_TARGET[fb.bpp]
        cmd, self.canvas = image_command(self.size, (fb.w, fb.h), fb.w, 'rgb24',
                                         out='ppm', **kw)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)
        for f in (self.proc.stdin, self.proc.stdout):
            try:                                  # 64 kB means many tiny reads
                fcntl.fcntl(f.fileno(), 1031, 1 << 20)          # F_SETPIPE_SZ
            except OSError:
                pass

    def _read(self, n):
        out = bytearray(n)
        got = 0
        while got < n:
            k = self.proc.stdout.readinto(memoryview(out)[got:])
            if not k:
                raise OSError('graphics pipeline stopped')
            got += k
        return out

    def frame(self, rgb):
        """One canvas frame (rgb24, size) -> bytes for the framebuffer."""
        try:
            self.proc.stdin.write(rgb)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise OSError('graphics pipeline: %s' % e)
        hdr = b''
        while hdr.count(b'\n') < 3:               # 'P6\nW H\n255\n'
            c = self.proc.stdout.read(1)
            if not c:
                raise OSError('graphics pipeline stopped')
            hdr += c
        if not hdr.startswith(b'P6'):
            raise OSError('graphics pipeline: unexpected output')
        w, h = (int(v) for v in hdr.split(b'\n')[1].split())
        data = bytes(self._read(w * h * 3))
        from PIL import Image
        img = Image.frombytes('RGB', (w, h), data)
        if self.fb.w > w:                         # line padding
            wide = Image.new('RGB', (self.fb.line_px, h), (0, 0, 0))
            wide.paste(img, (0, 0))
            img = wide
        return img.tobytes('raw', self.target)

    def close(self):
        p, self.proc = self.proc, None
        if not p:
            return
        for step in (lambda: p.stdin.close(), lambda: p.kill(), lambda: p.wait(timeout=1),
                     lambda: p.stdout.close()):
            try:
                step()
            except Exception:
                pass


# ------------------------------------------------------------------ 3x5 pixel font
FONT = {
    '0': ['###', '# #', '# #', '# #', '###'], '1': [' # ', '## ', ' # ', ' # ', '###'],
    '2': ['###', '  #', '###', '#  ', '###'], '3': ['###', '  #', ' ##', '  #', '###'],
    '4': ['# #', '# #', '###', '  #', '  #'], '5': ['###', '#  ', '###', '  #', '###'],
    '6': ['###', '#  ', '###', '# #', '###'], '7': ['###', '  #', '  #', ' # ', ' # '],
    '8': ['###', '# #', '###', '# #', '###'], '9': ['###', '# #', '###', '  #', '###'],
    'A': [' # ', '# #', '###', '# #', '# #'], 'B': ['## ', '# #', '## ', '# #', '## '],
    'C': [' ##', '#  ', '#  ', '#  ', ' ##'], 'D': ['## ', '# #', '# #', '# #', '## '],
    'E': ['###', '#  ', '## ', '#  ', '###'], 'F': ['###', '#  ', '## ', '#  ', '#  '],
    'G': [' ##', '#  ', '# #', '# #', ' ##'], 'H': ['# #', '# #', '###', '# #', '# #'],
    'I': ['###', ' # ', ' # ', ' # ', '###'], 'J': ['  #', '  #', '  #', '# #', ' # '],
    'K': ['# #', '# #', '## ', '# #', '# #'], 'L': ['#  ', '#  ', '#  ', '#  ', '###'],
    'M': ['# #', '###', '# #', '# #', '# #'], 'N': ['## ', '# #', '# #', '# #', '# #'],
    'O': [' # ', '# #', '# #', '# #', ' # '], 'P': ['## ', '# #', '## ', '#  ', '#  '],
    'Q': [' # ', '# #', '# #', '## ', ' ##'], 'R': ['## ', '# #', '## ', '# #', '# #'],
    'S': [' ##', '#  ', ' # ', '  #', '## '], 'T': ['###', ' # ', ' # ', ' # ', ' # '],
    'U': ['# #', '# #', '# #', '# #', '###'], 'V': ['# #', '# #', '# #', '# #', ' # '],
    'W': ['# #', '# #', '# #', '###', '# #'], 'X': ['# #', '# #', ' # ', '# #', '# #'],
    'Y': ['# #', '# #', ' # ', ' # ', ' # '], 'Z': ['###', '  #', ' # ', '#  ', '###'],
    ':': ['   ', ' # ', '   ', ' # ', '   '], '/': ['  #', '  #', ' # ', '#  ', '#  '],
    '.': ['   ', '   ', '   ', '   ', ' # '], '-': ['   ', '   ', '###', '   ', '   '],
    '+': ['   ', ' # ', '###', ' # ', '   '], '%': ['# #', '  #', ' # ', '#  ', '# #'],
    '=': ['   ', '###', '   ', '###', '   '], '_': ['   ', '   ', '   ', '   ', '###'],
    ' ': ['   '] * 5,
    '(': [' # ', '#  ', '#  ', '#  ', ' # '], ')': [' # ', '  #', '  #', '  #', ' # '],
    ',': ['   ', '   ', '   ', ' # ', '#  '],
}


def text_width(text, px):
    return len(text) * 4 * px - px if text else 0


# ------------------------------------------------------------------ the session
class Session:
    """One stay in graphics mode: framebuffer, console mode and the OSD."""

    def __init__(self, tty_fd, fb=None, colours=None, active_path='/sys/class/tty/tty0/active'):
        self.tty_fd = tty_fd
        self.tty = tty_name(tty_fd)
        self.active_path = active_path
        self.fb = fb or Framebuffer()
        self.screen = (self.fb.w, self.fb.h)
        c = colours or {}
        self.c_bg = c.get('bg', (12, 12, 12))
        self.c_fg = c.get('fg', (242, 242, 242))
        self.c_dim = c.get('dim', (124, 124, 124))
        self.c_acc = c.get('accent', (255, 138, 28))
        self.c_field = c.get('field', (44, 44, 44))
        self.frame = None
        self.scale = SCALE
        self.was_active = False
        self.info_until = 0.0         # top label
        self.info_lines = []
        self.bar_until = 0.0          # bottom bar
        self.bar_state = None         # what the bar showed last (to repaint on change)
        self.osd_shown = False
        self.osd_key = None           # what the cached OSD pieces show
        self.osd_cache = []
        self.scratch = None           # frame + OSD, written in one go
        self.entered = False

    @property
    def frame_size(self):
        return self.fb.size

    def start(self):
        if not FAKE:
            enter(self.tty_fd)
            self.entered = True
        self.fb.clear()
        self.was_active = True

    def close(self):
        if self.entered:
            leave(self.tty_fd)
            self.entered = False
        self.fb.close()

    def active(self):
        return vt_active(self.tty, self.active_path) if self.tty.startswith('tty') else True

    # ---------------------------------------------------------- output
    def show(self, frame):
        self.frame = frame
        self.paint()

    def paint(self):
        """Frame plus OSD as ONE write (skipped while another VT is shown).

        Writing the frame and then the OSD on top would let the display scan out
        the frame without it in between - the overlay would flicker. So the OSD is
        put into a copy of the frame in memory first."""
        if not self.active():
            self.was_active = False
            return
        self.was_active = True
        runs = self.osd_runs()
        if not runs:
            if self.frame is not None:
                self.fb.write(self.frame)
            else:
                self.fb.clear()
            return
        if self.scratch is None:
            self.scratch = bytearray(self.fb.size)
        buf = self.scratch
        if self.frame is not None:
            buf[:] = self.frame
        else:
            buf[:] = bytes(self.fb.size)
        for off, data in runs:
            buf[off:off + len(data)] = data
        self.fb.write(buf)

    def tick(self, now, pos, dur, paused):
        """Called often: repaint when the OSD appears, changes or disappears, or
        when our VT came back to the front (the console does not redraw it)."""
        act = self.active()
        if act and not self.was_active:
            self.paint()
            return
        if not act:
            self.was_active = False
            return
        want_bar = paused or now < self.bar_until
        want_info = now < self.info_until
        state = (want_bar, want_info, int(pos), paused) if (want_bar or want_info) else None
        self.bar = (pos, dur, paused)
        if state != self.bar_state:
            self.bar_state = state
            self.osd_shown = state is not None
            self.paint()

    def note(self, lines, secs=3.0):
        self.info_lines = [l.upper() for l in lines]
        self.info_until = time.monotonic() + secs
        self.bar_state = 'changed'

    def poke(self, secs=2.5):
        self.bar_until = time.monotonic() + secs
        self.bar_state = 'changed'

    # ---------------------------------------------------------- OSD
    def osd_runs(self):
        """(byte offset, bytes) pieces of the OSD, only its opaque parts.

        Drawn once per change (at most once a second for the clock) into blank
        buffers, so a playing video only costs a few slice copies per frame."""
        if not self.osd_shown:
            return []
        now = time.monotonic()
        pos, dur, paused = getattr(self, 'bar', (0, 0, False))
        want_bar = paused or now < self.bar_until
        want_info = now < self.info_until and bool(self.info_lines)
        key = (want_bar, want_info, int(pos), int(dur), paused, tuple(self.info_lines))
        if key != self.osd_key:
            self.osd_key = key
            px = max(2, self.scale)
            runs = []
            if want_bar:
                runs += self.bottom_bar(px, pos, dur, paused)
            if want_info:
                runs += self.info_box(px)
            self.osd_cache = runs
        return self.osd_cache

    def region(self, y0, y1):
        return bytearray((y1 - y0) * self.fb.stride)

    def rect(self, buf, y0, x, y, w, h, rgb):
        W = self.fb.w
        x, w = max(0, x), min(w, W - max(0, x))
        if w <= 0:
            return
        bp, st = self.fb.bytes_pp, self.fb.stride
        row = self.fb.pixel(rgb) * w
        for yy in range(y, y + h):
            o = (yy - y0) * st + x * bp
            if 0 <= o and o + len(row) <= len(buf):
                buf[o:o + len(row)] = row

    def text(self, buf, y0, x, y, s, px, rgb):
        for ch in s:
            g = FONT.get(ch, FONT[' '])
            for gy, line in enumerate(g):
                for gx, c in enumerate(line):
                    if c == '#':
                        self.rect(buf, y0, x + gx * px, y + gy * px, px, px, rgb)
            x += 4 * px
        return x

    def bottom_bar(self, px, pos, dur, paused):
        W, H = self.screen
        bh = 9 * px
        y0 = H - bh
        buf = self.region(y0, H)
        self.rect(buf, y0, 0, y0, W, bh, self.c_bg)
        x = 2 * px
        ty = y0 + 2 * px
        if paused:                              # two bars
            self.rect(buf, y0, x, ty, px, 5 * px, self.c_acc)
            self.rect(buf, y0, x + 2 * px, ty, px, 5 * px, self.c_acc)
        else:                                   # a play triangle
            for i in range(3):
                self.rect(buf, y0, x + i * px, ty + i * px, px, (5 - 2 * i) * px, self.c_acc)
        x += 5 * px
        t = '%s / %s' % (clock(pos), clock(dur)) if dur else clock(pos)
        x = self.text(buf, y0, x, ty, t, px, self.c_fg) + 2 * px
        bx1 = W - 2 * px
        if dur and bx1 - x > 8 * px:
            bw = bx1 - x
            self.rect(buf, y0, x, ty + px, bw, 3 * px, self.c_field)
            fill = int(bw * max(0.0, min(1.0, pos / dur)))
            self.rect(buf, y0, x, ty + px, fill, 3 * px, self.c_acc)
        return [(y0 * self.fb.stride, bytes(buf))]              # full rows: one piece

    def info_box(self, px):
        W, H = self.screen
        lines = self.info_lines[:3]
        bw = max(text_width(l, px) for l in lines) + 4 * px
        bh = len(lines) * 7 * px + 3 * px
        y0, y1 = 2 * px, 2 * px + bh
        buf = self.region(y0, y1)
        self.rect(buf, y0, 2 * px, y0, min(bw, W - 4 * px), bh, self.c_bg)
        self.rect(buf, y0, 2 * px, y0, px, bh, self.c_acc)
        for i, l in enumerate(lines):
            self.text(buf, y0, 4 * px, y0 + 2 * px + i * 7 * px, l, px,
                      self.c_fg if i == 0 else self.c_dim)
        # only the box itself, row by row: the video around it stays live
        st, bp = self.fb.stride, self.fb.bytes_pp
        x0, n = 2 * px * bp, min(bw, W - 4 * px) * bp
        return [((y0 + r) * st + x0, bytes(buf[r * st + x0:r * st + x0 + n])) for r in range(bh)]


def clock(t):
    t = max(0, int(t))
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


# ------------------------------------------------------------------ dev tool
def main(argv=None):
    """Render one frame of a video through the graphics pipeline into a PNG."""
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('video')
    ap.add_argument('out')
    ap.add_argument('--look', default='vga', choices=LOOK_ORDER)
    ap.add_argument('--dither', default='bayer', choices=DITHERS)
    ap.add_argument('--scanlines', action='store_true')
    ap.add_argument('--at', type=float, default=5.0)
    ap.add_argument('--screen', default='1024x768')
    ap.add_argument('--osd', action='store_true', help='draw the on-screen display too')
    a = ap.parse_args(argv)
    W, H = (int(v) for v in a.screen.split('x'))
    p = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,sample_aspect_ratio', '-of', 'json', a.video],
                       capture_output=True, text=True)
    s = json.loads(p.stdout)['streams'][0]
    sar = 1.0
    if s.get('sample_aspect_ratio', '1:1') not in ('0:1', 'N/A'):
        n, d = s.get('sample_aspect_ratio', '1:1').split(':')
        sar = float(n) / float(d) if float(d) else 1.0
    video = {'w': s['width'], 'h': s['height'], 'sar': sar}
    from .theme import PRESETS
    theme = [hex_rgb(h) for h in PRESETS['Ember']]
    vpal = make_video_palette(a.video) if a.look == 'vga' else None
    cmd, cv = command(a.video, a.at, video, (W, H), W, 'bgr0', theme, theme[14], a.look,
                      SCALE, a.dither, a.scanlines, 25, vpal)
    t0 = time.monotonic()
    raw = subprocess.run(cmd[:-1] + ['-frames:v', '1', '-'], capture_output=True).stdout
    ms = (time.monotonic() - t0) * 1000
    if len(raw) < W * H * 4:
        raise SystemExit('ffmpeg produced no frame: ' + ' '.join(cmd))
    frame = raw[:W * H * 4]
    if a.osd:
        fbfile = a.out + '.fb'
        fb = Framebuffer(fbfile, geometry=(W, H, 32, W * 4))
        se = Session(-1, fb)
        se.tty = ''
        se.frame = frame
        se.note(['%s  %dx%d  %s' % (LOOKS[a.look][0], cv[0], cv[1], a.dither),
                 'G EXIT  C LOOK  D DITHER  S SCANLINES'])
        se.bar = (a.at, 222.0, False)
        se.poke()
        se.osd_shown = True
        buf = bytearray(frame)
        for off, data in se.osd_runs():
            buf[off:off + len(data)] = data
        frame = bytes(buf)
        fb.close()
        os.remove(fbfile)
    from PIL import Image
    Image.frombytes('RGB', (W, H), frame, 'raw', 'BGRX').save(a.out)
    print('%s  canvas %dx%d  %.0f ms' % (a.out, cv[0], cv[1], ms))


if __name__ == '__main__':
    main()
