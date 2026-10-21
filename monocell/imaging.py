"""Image decoding and text-cell rendering (pure ASCII, 16 colours).

Decoders, in order: Pillow (if installed) -> ffmpeg (any format) -> built-in PNG/PPM/BMP.

Render modes (per cell):
  blend : bg colour + fg colour + a density character from ' .:+*%#@' -> mixes two palette
          colours; best tonal reconstruction with ASCII only
  pixel : one dithered "pixel" per cell drawn as a coloured space (8-bit look)
  ascii : classic ASCII art, brightness -> glyph, nearest hue as fg
  mono  : ASCII art in the accent colour
"""
import io
import struct
import subprocess
import zlib

from .util import which

try:
    from PIL import Image as PILImage  # optional
except Exception:  # pragma: no cover
    PILImage = None

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
BAYER8 = [[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
          [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
          [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]
DENSITY = [(' ', 0.0), ('.', 0.10), (':', 0.18), ('+', 0.28), ('*', 0.36), ('%', 0.45), ('#', 0.52), ('@', 0.60)]
TEXTURE = 0.35
ASCII_RAMP = " .,:;-~=+*o%#&@"
# classic density ordering of printable ASCII (every glyph used)
ASCII_FULL = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
ASCII_SHADES = ' ░▒▓█'


class Image:
    """Decoded image. Either a PIL image or raw RGB bytes."""

    def __init__(self, w, h, data=None, pil=None, path=None):
        self.w, self.h = w, h
        self.data = data
        self.pil = pil
        self.path = path
        self._cache = None
        self.frames = None    # for animated images (list of Image) - optional

    def scaled(self, w, h, nearest=False):
        w, h = max(1, w), max(1, h)
        key = (w, h, nearest)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        if self.pil is not None:
            filt = PILImage.NEAREST if nearest else (PILImage.BOX if (w < self.w // 2) else PILImage.BILINEAR)
            im = self.pil.resize((w, h), filt)
            out = im.tobytes()
        elif nearest:
            out = resample_nearest(self.data, self.w, self.h, w, h)
        else:
            out = resample(self.data, self.w, self.h, w, h)
        self._cache = (key, out)
        return out


def resample_nearest(data, sw, sh, w, h):
    out = bytearray(w * h * 3)
    fx, fy = sw / w, sh / h
    o = 0
    for y in range(h):
        row = min(sh - 1, int(y * fy)) * sw * 3
        for x in range(w):
            p = row + min(sw - 1, int(x * fx)) * 3
            out[o] = data[p]
            out[o + 1] = data[p + 1]
            out[o + 2] = data[p + 2]
            o += 3
    return bytes(out)


def resample(data, sw, sh, w, h):
    """Pure-python downscale with 2x2 supersampling (good enough, fast)."""
    out = bytearray(w * h * 3)
    fx, fy = sw / w, sh / h
    o = 0
    for y in range(h):
        y0 = min(sh - 1, int((y + 0.25) * fy))
        y1 = min(sh - 1, int((y + 0.75) * fy))
        r0, r1 = y0 * sw * 3, y1 * sw * 3
        for x in range(w):
            x0 = min(sw - 1, int((x + 0.25) * fx)) * 3
            x1 = min(sw - 1, int((x + 0.75) * fx)) * 3
            a, b, c, d = r0 + x0, r0 + x1, r1 + x0, r1 + x1
            out[o] = (data[a] + data[b] + data[c] + data[d]) >> 2
            out[o + 1] = (data[a + 1] + data[b + 1] + data[c + 1] + data[d + 1]) >> 2
            out[o + 2] = (data[a + 2] + data[b + 2] + data[c + 2] + data[d + 2]) >> 2
            o += 3
    return bytes(out)


# ------------------------------------------------------------------ decoding
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.ppm', '.pgm', '.pnm', '.tif', '.tiff', '.ico',
             '.avif', '.heic', '.tga', '.jxl', '.svg')


def load(path, max_side=1600):
    errs = []
    if PILImage is not None:
        try:
            im = PILImage.open(path)
            im.draft('RGB', (max_side, max_side))
            im = _pil_rgb(im)
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side))
            return Image(im.size[0], im.size[1], pil=im, path=path)
        except Exception as e:
            errs.append('pil: %s' % e)
    if which('ffmpeg'):
        try:
            return _ffmpeg_decode(['-i', path], max_side, path)
        except Exception as e:
            errs.append('ffmpeg: %s' % e)
    try:
        with open(path, 'rb') as f:
            return from_bytes(f.read(), builtin_only=True)
    except Exception as e:
        errs.append(str(e))
    raise ValueError('cannot decode image (%s)' % '; '.join(errs))


def _pil_rgb(im):
    if im.mode in ('RGBA', 'LA', 'P', 'PA'):
        im = im.convert('RGBA')
        bg = PILImage.new('RGBA', im.size, (24, 24, 24, 255))
        bg.alpha_composite(im)
        return bg.convert('RGB')
    if im.mode != 'RGB':
        return im.convert('RGB')
    return im


def from_bytes(data, builtin_only=False, max_side=1600):
    if not builtin_only and PILImage is not None:
        try:
            im = PILImage.open(io.BytesIO(data))
            im.draft('RGB', (max_side, max_side))
            im = _pil_rgb(im)
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side))
            return Image(im.size[0], im.size[1], pil=im)
        except Exception:
            pass
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return decode_png(data)
    if data[:2] in (b'P6', b'P5', b'P3'):
        return decode_pnm(data)
    if data[:2] == b'BM':
        return decode_bmp(data)
    if not builtin_only and which('ffmpeg'):
        return _ffmpeg_decode(['-i', 'pipe:0'], max_side, None, data)
    raise ValueError('unsupported image format')


def _ffmpeg_decode(inargs, max_side, path, stdin=None):
    vf = "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % (max_side, max_side)
    cmd = ['ffmpeg', '-v', 'error', '-nostdin'] + inargs + ['-frames:v', '1', '-vf', vf,
                                                          '-f', 'image2pipe', '-vcodec', 'ppm', '-']
    if stdin is not None:
        cmd.remove('-nostdin')
    p = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if p.returncode or not p.stdout:
        raise ValueError(p.stderr.decode(errors='replace').strip().split('\n')[-1][:120] or 'ffmpeg failed')
    img = decode_pnm(p.stdout)
    img.path = path
    return img


def decode_pnm(data):
    parts = []
    i = 2
    n = len(data)
    while len(parts) < (2 if data[:2] == b'P4' else 3):
        while i < n and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b'#':
            while i < n and data[i:i + 1] != b'\n':
                i += 1
            continue
        j = i
        while j < n and not data[j:j + 1].isspace():
            j += 1
        parts.append(int(data[i:j]))
        i = j
    i += 1
    w, h, mx = parts
    raw = data[i:]
    if data[:2] == b'P6':
        if mx > 255:
            raw = bytes(raw[k] for k in range(0, len(raw), 2))
        return Image(w, h, bytes(raw[:w * h * 3]))
    if data[:2] == b'P5':
        g = raw[:w * h]
        return Image(w, h, bytes(v for px in g for v in (px, px, px)))
    vals = [int(v) for v in raw.split()]
    return Image(w, h, bytes(min(255, v * 255 // mx) for v in vals[:w * h * 3]))


def decode_bmp(data):
    off = struct.unpack_from('<I', data, 10)[0]
    w, h, planes, bpp, comp = struct.unpack_from('<iiHHI', data, 18)
    if comp not in (0, 3) or bpp not in (24, 32):
        raise ValueError('unsupported BMP')
    flip = h > 0
    h = abs(h)
    stride = ((w * bpp // 8) + 3) & ~3
    out = bytearray(w * h * 3)
    step = bpp // 8
    for y in range(h):
        row = off + (h - 1 - y if flip else y) * stride
        o = y * w * 3
        for x in range(w):
            p = row + x * step
            out[o] = data[p + 2]
            out[o + 1] = data[p + 1]
            out[o + 2] = data[p]
            o += 3
    return Image(w, h, bytes(out))


def decode_png(data):
    pos = 8
    idat = []
    plte = None
    trns = None
    while pos < len(data):
        ln, typ = struct.unpack_from('>I4s', data, pos)
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b'IHDR':
            w, h, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', body)
        elif typ == b'PLTE':
            plte = body
        elif typ == b'tRNS':
            trns = body
        elif typ == b'IDAT':
            idat.append(body)
        elif typ == b'IEND':
            break
    if inter:
        raise ValueError('interlaced PNG needs Pillow or ffmpeg')
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp_bits = chans * depth
    bpp = max(1, bpp_bits // 8)
    stride = (w * bpp_bits + 7) // 8
    raw = zlib.decompress(b''.join(idat))
    prev = bytearray(stride)
    rows = []
    i = 0
    for y in range(h):
        ft = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ft == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        rows.append(line)
        prev = line
    out = bytearray(w * h * 3)
    o = 0
    bg = 24
    for line in rows:
        if depth == 16:
            line = line[0::2]
        if depth < 8:
            vals = []
            mask = (1 << depth) - 1
            for byte in line:
                for s in range(8 - depth, -1, -depth):
                    vals.append((byte >> s) & mask)
            vals = vals[:w]
            if ctype == 0:
                scale = 255 // mask
                for v in vals:
                    out[o:o + 3] = bytes((v * scale,) * 3)
                    o += 3
            else:
                for v in vals:
                    out[o:o + 3] = plte[v * 3:v * 3 + 3]
                    o += 3
            continue
        if ctype == 2:
            out[o:o + w * 3] = line[:w * 3]
            o += w * 3
        elif ctype == 6:
            for x in range(w):
                r, g, b, a = line[x * 4:x * 4 + 4]
                out[o] = (r * a + bg * (255 - a)) // 255
                out[o + 1] = (g * a + bg * (255 - a)) // 255
                out[o + 2] = (b * a + bg * (255 - a)) // 255
                o += 3
        elif ctype == 0:
            for v in line[:w]:
                out[o] = out[o + 1] = out[o + 2] = v
                o += 3
        elif ctype == 4:
            for x in range(w):
                v, a = line[x * 2], line[x * 2 + 1]
                out[o] = out[o + 1] = out[o + 2] = (v * a + bg * (255 - a)) // 255
                o += 3
        elif ctype == 3:
            for v in line[:w]:
                out[o:o + 3] = plte[v * 3:v * 3 + 3]
                o += 3
    return Image(w, h, bytes(out))


def test_card(w, h):
    """Colourful gradient test image for previews."""
    out = bytearray(w * h * 3)
    o = 0
    for y in range(h):
        for x in range(w):
            if y < h * 2 // 3:
                hue = x / w
                light = 1.0 - y / (h * 2 / 3)
                r, g, b = _hsl(hue, 0.85, 0.15 + 0.7 * light)
            else:
                v = int(255 * x / (w - 1))
                r = g = b = v
            out[o], out[o + 1], out[o + 2] = int(r), int(g), int(b)
            o += 3
    return Image(w, h, bytes(out))


def _hsl(h, s, l):
    def f(n):
        k = (n + h * 12) % 12
        a = s * min(l, 1 - l)
        return 255 * (l - a * max(-1, min(k - 3, 9 - k, 1)))
    return f(0), f(8), f(4)


# ------------------------------------------------------------------ rendering
class Renderer:
    """Holds palette-dependent lookup tables; rebuilt when the palette changes."""

    def __init__(self, rgb, allowed_bg, accent, dark, strict=False):
        self.rgb = rgb
        self.strict = strict
        self.allowed_bg = list(allowed_bg)
        self.accent = accent
        self.dark = dark
        self.near = {}
        self.near_bg = {}
        self.blend = {}
        self.pairs = []
        self.key = (tuple(rgb), tuple(allowed_bg), accent, dark)

    def nearest(self, r, g, b, allowed=None):
        k = (r >> 2, g >> 2, b >> 2, allowed is not None)
        v = self.near.get(k)
        if v is None:
            best, bd = 0, 1 << 30
            for i in (allowed if allowed is not None else range(16)):
                pr, pg, pb = self.rgb[i]
                d = (r - pr) ** 2 * 3 + (g - pg) ** 2 * 4 + (b - pb) ** 2 * 2
                if d < bd:
                    best, bd = i, d
            v = best
            self.near[k] = v
        return v

    def blend_cell(self, q):
        """q: quantised colour key (r>>4, g>>4, b>>4) -> (bg, fg, char)."""
        v = self.blend.get(q)
        if v is not None:
            return v
        r, g, b = (q[0] << 4) + 8, (q[1] << 4) + 8, (q[2] << 4) + 8
        rgb = self.rgb
        dist = sorted(range(16), key=lambda i: (r - rgb[i][0]) ** 2 * 3 + (g - rgb[i][1]) ** 2 * 4 + (b - rgb[i][2]) ** 2 * 2)
        allowed = self.allowed_bg
        cand = ([i for i in dist if not self.strict or i in allowed] if self.strict else dist)[:6]
        best = None
        bd = 1 << 40
        levels = DENSITY
        for ai in range(len(cand)):
            i = cand[ai]
            Pi = rgb[i]
            for bi in range(ai, len(cand)):
                j = cand[bi]
                Pj = rgb[j]
                dx, dy, dz = Pj[0] - Pi[0], Pj[1] - Pi[1], Pj[2] - Pi[2]
                dd = dx * dx * 3 + dy * dy * 4 + dz * dz * 2
                if dd:
                    t = ((r - Pi[0]) * dx * 3 + (g - Pi[1]) * dy * 4 + (b - Pi[2]) * dz * 2) / dd
                else:
                    t = 0.0
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                for bgc, fgc, f in ((i, j, t), (j, i, 1 - t)):
                    if bgc not in allowed:
                        continue
                    # nearest density level
                    ch, cov = ' ', 0.0
                    bdist = 9.0
                    for c, cv in levels:
                        dlt = abs(cv - f)
                        if dlt < bdist:
                            bdist, ch, cov = dlt, c, cv
                    Pb, Pf = rgb[bgc], rgb[fgc]
                    mr = Pb[0] + (Pf[0] - Pb[0]) * cov
                    mg = Pb[1] + (Pf[1] - Pb[1]) * cov
                    mb = Pb[2] + (Pf[2] - Pb[2]) * cov
                    err = (r - mr) ** 2 * 3 + (g - mg) ** 2 * 4 + (b - mb) ** 2 * 2
                    err += TEXTURE * cov * (1 - cov) * dd       # texture/noise penalty
                    if err < bd:
                        bd = err
                        best = (bgc, fgc if ch != ' ' else bgc, ch)
                    if not dd:
                        break
        if best is None:
            best = (allowed[0], allowed[0], ' ')
        self.blend[q] = best
        return best


_renderer = None


def renderer(wm):
    global _renderer
    th = wm.theme
    allowed = wm.term.allowed_bg
    key = (tuple(th.rgb), tuple(allowed), th.r['accent'], th.r['shadow'])
    if _renderer is None or _renderer.key != key:
        _renderer = Renderer(list(th.rgb), allowed, th.r['accent'], th.r['shadow'])
    return _renderer


def cell_aspect(wm):
    a = wm.cfg.get('cell_aspect') or 0
    return a if a > 0 else (wm.term.cell_aspect or 2.0)


def fit_size(iw, ih, max_w, max_h, aspect):
    """Cells (w,h) that show an iw x ih image undistorted within max_w x max_h."""
    if iw <= 0 or ih <= 0:
        return max_w, max_h
    w = max_w
    h = int(round(w * ih / (iw * aspect)))
    if h > max_h:
        h = max_h
        w = int(round(h * aspect * iw / ih))
    return max(1, min(w, max_w)), max(1, min(h, max_h))


def render_fit(wm, img, max_w, max_h, mode=None, zoom=1.0):
    w, h = fit_size(img.w, img.h, max_w, max_h, cell_aspect(wm))
    if zoom != 1.0:
        w, h = max(1, int(w * zoom)), max(1, int(h * zoom))
    return render(wm, img.scaled(w, h), w, h, mode), w, h


def render_cover(wm, img, w, h, mode=None, dim=0.62, sat=1.0, dither=None, grey=False):
    """Scale to cover w x h and centre-crop (desktop wallpaper)."""
    w, h = max(1, w), max(1, h)
    aspect = cell_aspect(wm)
    # required source cell grid so that the image covers the area
    tw = max(w, int(round(h * aspect * img.w / max(1, img.h))))
    th = max(h, int(round(w * img.h / (aspect * max(1, img.w)))))
    rgb = img.scaled(tw, th)
    ox = max(0, (tw - w) // 2)
    oy = max(0, (th - h) // 2)
    if (ox, oy, tw, th) != (0, 0, w, h):
        cropped = bytearray(w * h * 3)
        for y in range(h):
            s0 = ((y + oy) * tw + ox) * 3
            cropped[y * w * 3:(y + 1) * w * 3] = rgb[s0:s0 + w * 3]
        rgb = bytes(cropped)
    if sat != 1.0:
        out = bytearray(len(rgb))
        for o in range(0, len(rgb), 3):
            r, g, b = rgb[o], rgb[o + 1], rgb[o + 2]
            l = (r * 77 + g * 151 + b * 28) >> 8
            out[o] = max(0, min(255, int(l + (r - l) * sat)))
            out[o + 1] = max(0, min(255, int(l + (g - l) * sat)))
            out[o + 2] = max(0, min(255, int(l + (b - l) * sat)))
        rgb = bytes(out)
    return render(wm, rgb, w, h, mode, dither, dim=dim, grey=grey)


GREYS = [0, 7, 8, 9, 10, 11, 12, 13]
_grey = None


def grey_renderer(wm):
    """Palette reduced to its grey ramp - wallpapers and the 'grey' image mode."""
    global _grey
    th = wm.theme
    allowed = [i for i in wm.term.allowed_bg if i in GREYS] or [0]
    key = (tuple(th.rgb), tuple(wm.term.allowed_bg))
    if _grey is not None and _grey[0] == key:
        return _grey[1]
    rgb = list(th.rgb)
    for i in range(16):
        if i not in GREYS:
            rgb[i] = rgb[th.nearest(th.rgb[i], GREYS)]
    R = Renderer(rgb, allowed, th.r['accent'], th.r['shadow'], strict=True)
    _grey = (key, R)
    return R


def render(wm, rgb, w, h, mode=None, dither=None, dim=1.0, grey=False):
    """rgb: bytes of w*h*3 -> list of (chars:str, attrs:list)."""
    mode = mode or wm.cfg.get('image_mode', 'blend')
    dither = dither or wm.cfg.get('dither', 'bayer4')
    if mode == 'grey':
        mode, grey = 'blend', True
    R = grey_renderer(wm) if grey else renderer(wm)
    if dim != 1.0:
        rgb = bytes(int(v * dim) for v in rgb)
    if mode == 'pixel':
        return _render_pixel(R, rgb, w, h, dither)
    if mode in ('ascii', 'mono'):
        return _render_ascii(R, rgb, w, h, mode, wm.theme, wm.cfg.get('ascii_charset', 'full'))
    return _render_blend(R, rgb, w, h, dither)


def _bayer(dither):
    if dither == 'bayer8':
        return [[(v + 0.5) / 64 - 0.5 for v in row] for row in BAYER8], 8
    if dither == 'none' or dither == 'fs':
        return [[0.0]], 1
    return [[(v + 0.5) / 16 - 0.5 for v in row] for row in BAYER4], 4


def _render_blend(R, rgb, w, h, dither):
    mat, n = _bayer(dither)
    spread = 18
    rows = []
    blend = R.blend_cell
    cache = R.blend
    o = 0
    for y in range(h):
        mrow = mat[y % n]
        cs = []
        ats = []
        for x in range(w):
            t = mrow[x % n] * spread
            r = rgb[o] + t
            g = rgb[o + 1] + t
            b = rgb[o + 2] + t
            o += 3
            q = (0 if r < 0 else 15 if r > 255 else int(r) >> 4,
                 0 if g < 0 else 15 if g > 255 else int(g) >> 4,
                 0 if b < 0 else 15 if b > 255 else int(b) >> 4)
            v = cache.get(q) or blend(q)
            cs.append(v[2])
            ats.append(v[1] | (v[0] << 4))
        rows.append((''.join(cs), ats))
    return rows


def _render_pixel(R, rgb, w, h, dither):
    allowed = R.allowed_bg
    pal = R.rgb
    rows = []
    if dither == 'fs':
        buf = [float(v) for v in rgb]
        for y in range(h):
            ats = []
            for x in range(w):
                o = (y * w + x) * 3
                r, g, b = buf[o], buf[o + 1], buf[o + 2]
                i = R.nearest(max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b))), allowed)
                ats.append(i | (i << 4))
                pr, pg, pb = pal[i]
                er, eg, eb = r - pr, g - pg, b - pb
                for dx, dy, f in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and yy < h:
                        k = (yy * w + xx) * 3
                        buf[k] += er * f
                        buf[k + 1] += eg * f
                        buf[k + 2] += eb * f
            rows.append((' ' * w, ats))
        return rows
    mat, n = _bayer(dither)
    spread = 56
    o = 0
    for y in range(h):
        mrow = mat[y % n]
        ats = []
        for x in range(w):
            t = mrow[x % n] * spread
            r = int(rgb[o] + t)
            g = int(rgb[o + 1] + t)
            b = int(rgb[o + 2] + t)
            o += 3
            i = R.nearest(0 if r < 0 else 255 if r > 255 else r, 0 if g < 0 else 255 if g > 255 else g,
                          0 if b < 0 else 255 if b > 255 else b, allowed)
            ats.append(i | (i << 4))
        rows.append((' ' * w, ats))
    return rows


def _render_ascii(R, rgb, w, h, mode, th, charset='full'):
    bg = th.r['shadow']
    if charset == 'simple':
        ramp = ASCII_RAMP
    elif charset == 'shades':
        ramp = ASCII_SHADES if __import__('monocell.util', fromlist=['UNICODE']).UNICODE else ASCII_FULL
    else:
        ramp = ASCII_FULL
    nr = len(ramp) - 1
    rows = []
    o = 0
    acc = th.r['accent']
    for y in range(h):
        cs = []
        ats = []
        for x in range(w):
            r, g, b = rgb[o], rgb[o + 1], rgb[o + 2]
            o += 3
            lum = (r * 299 + g * 587 + b * 114) // 1000
            cs.append(ramp[lum * nr // 255])
            if mode == 'mono':
                fg = acc
            else:
                m = max(r, g, b, 1)
                fg = R.nearest(r * 255 // m, g * 255 // m, b * 255 // m,
                               R.allowed_bg if R.strict else None)
            ats.append(fg | (bg << 4))
        rows.append((''.join(cs), ats))
    return rows


def is_image(path):
    return path.lower().endswith(IMAGE_EXT)
