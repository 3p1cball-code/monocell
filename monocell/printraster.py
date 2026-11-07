"""The printed page as dots: 180 dpi, one bit per dot.

Used twice, so the preview cannot lie about the paper:

* printing - rows of a sheet that contain a picture printed as graphics are
  rasterised and sent as 24-dot bands (printer.gfx_block);
* the graphics preview of the Writer (F6) - the whole sheet, scaled down for
  the framebuffer.

Text is drawn with the console font (the theme's PSF), stretched to the
printer cell (10 cpi = 18 dots, 12 cpi = 15 dots; 6 lpi = 30 dots per line).
Bold is emphasized print: the glyph a second time, one dot to the right.
Pictures are dithered (Floyd-Steinberg) after a gamma lift, because ink dots
spread on paper.
"""
from . import printer

DPI = printer.GFX_DPI
GAMMA = 0.75            # < 1 lightens: compensates the dot gain of the ribbon


def _font(name=None):
    from . import fbmode
    return fbmode.console_font(name)


class Raster:
    def __init__(self, page, font=None, font_name=None):
        from PIL import Image
        self.Image = Image
        self.page = page
        self.cw = int(round(DPI / float(page.cpi)))
        self.lh = DPI / float(page.lpi)
        self.width = page.cols * self.cw
        self.height = int(round(page.lines * self.lh))
        self.font = font or _font(font_name)
        self.gh = max(8, int(round(self.lh * 0.9)))
        self._glyphs = {}

    def y_of(self, r):
        return int(round(r * self.lh))

    def glyph(self, ch):
        g = self._glyphs.get(ch)
        if g is None:
            src = self.font.glyph(ch) if self.font else None
            if src is None:
                g = self.Image.new('1', (self.cw, self.gh), 0)
            else:
                g = src.resize((self.cw, self.gh), self.Image.NEAREST)
            self._glyphs[ch] = g
        return g

    def picture(self, img, w, h):
        """A picture (imaging.Image or 'frame'/'ramp' test patterns) as ink mask."""
        Image = self.Image
        if img == 'frame':
            m = Image.new('L', (w, h), 0)
            from PIL import ImageDraw
            d = ImageDraw.Draw(m)
            d.rectangle([2, 2, w - 3, h - 3], outline=255, width=3)
            d.line([2, 2, w - 3, h - 3], fill=255, width=2)
            d.line([2, h - 3, w - 3, 2], fill=255, width=2)
            return m.convert('1')
        if img == 'ramp':
            g = Image.linear_gradient('L').rotate(90).resize((w, h))
            return self.dither(g)
        if img is None:
            return None
        iw, ih = img.w, img.h
        k = min(w / float(iw), h / float(ih))
        sw, sh = max(1, int(iw * k)), max(1, int(ih * k))
        if getattr(img, 'pil', None) is not None:
            src = img.pil.convert('L').resize((sw, sh), Image.LANCZOS)
        else:
            src = Image.frombytes('RGB', (sw, sh), img.scaled(sw, sh)).convert('L')
        m = Image.new('1', (w, h), 0)
        m.paste(self.dither(src), ((w - sw) // 2, 0))
        return m

    def dither(self, grey):
        """Grey (L, 255 = paper) -> '1' with 1 = ink."""
        lut = [int(255 * (v / 255.0) ** GAMMA) for v in range(256)]
        g = grey.point(lut)
        g = g.point(lambda v: 255 - v)                  # ink amount
        return g.convert('1')                           # Floyd-Steinberg

    def render(self, sheet, r0=0, r1=None, ink=255):
        """Rows r0..r1 of the sheet -> PIL 'L' image, ink = 255, paper = 0."""
        Image = self.Image
        r1 = len(sheet.rows) if r1 is None else r1
        y0 = self.y_of(r0)
        img = Image.new('L', (self.width, max(1, self.y_of(r1) - y0)), 0)
        top = int(round((self.lh - self.gh) / 2.0))
        for r in range(r0, min(r1, len(sheet.rows))):
            chars, attrs = sheet.rows[r]
            skip = sheet.masked(r)
            y = self.y_of(r) - y0 + top
            for x, ch in enumerate(chars[:self.page.cols]):
                if ch == ' ' or x in skip:
                    continue
                g = self.glyph(ch)
                img.paste(ink, (x * self.cw, y), g)
                a = attrs[x] if x < len(attrs) else 0
                if (a >> 8) & printer.BOLD:
                    img.paste(ink, (x * self.cw + 1, y), g)
        for x, y, w, h, pic in sheet.gfx:
            if y + h <= r0 or y >= r1:
                continue
            pw, ph = w * self.cw, self.y_of(y + h) - self.y_of(y)
            m = self.picture(pic, pw, ph)
            if m is not None:
                img.paste(ink, (x * self.cw, self.y_of(y) - y0), m)
        return img

    def dot_rows(self, sheet, r0, r1):
        """For printer.encode_sheets: one bytes object per dot row (non-zero = ink)."""
        img = self.render(sheet, r0, r1)
        w = img.size[0]
        data = img.tobytes()
        return [data[i * w:(i + 1) * w] for i in range(img.size[1])]
