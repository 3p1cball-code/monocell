"""Render an image through monocell's imaging pipeline to a PNG (for tuning)."""
import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from monocell import imaging
from monocell.theme import Config, Theme, PRESETS
from PIL import Image, ImageDraw, ImageFont
FONT = '/usr/share/fonts/nerd-fonts/Terminus/TerminessNerdFontMono-Regular.ttf'

def run(path, out, w=100, h=40, mode='blend', dither='bayer4', preset='Ember'):
    cfg = Config(); cfg['palette'] = PRESETS[preset]; cfg['image_mode'] = mode; cfg['dither'] = dither
    th = Theme(cfg)
    wm = types.SimpleNamespace(theme=th, cfg=cfg, term=types.SimpleNamespace(allowed_bg=list(range(16)), cell_aspect=2.0))
    imaging._renderer = None
    img = imaging.load(path)
    rows, cw, chh = imaging.render_fit(wm, img, w, h)
    font = ImageFont.truetype(FONT, 16); cwpx = 8; chpx = 17
    im = Image.new('RGB', (cw * cwpx, chh * chpx)); d = ImageDraw.Draw(im)
    for y, (cs, ats) in enumerate(rows):
        for x, (c, a) in enumerate(zip(cs, ats)):
            fg, bg = th.rgb[a & 15], th.rgb[(a >> 4) & 15]
            d.rectangle([x*cwpx, y*chpx, x*cwpx+cwpx-1, y*chpx+chpx-1], fill=bg)
            if c != ' ': d.text((x*cwpx, y*chpx), c, font=font, fill=fg)
    im.save(out)

if __name__ == '__main__':
    a = sys.argv
    run(a[1], a[2], int(a[3]), int(a[4]), a[5], a[6], a[7] if len(a) > 7 else 'Ember')
