import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from monocell import imaging
from monocell.theme import Config, Theme
from PIL import Image, ImageDraw, ImageFont
FONT = '/usr/share/fonts/nerd-fonts/Terminus/TerminessNerdFontMono-Regular.ttf'

def run(path, out, w, h, mode, dim, sat):
    cfg = Config(); th = Theme(cfg)
    wm = types.SimpleNamespace(theme=th, cfg=cfg, term=types.SimpleNamespace(allowed_bg=list(range(16)), cell_aspect=2.0))
    imaging._renderer = None
    img = imaging.load(path)
    rows = imaging.render_cover(wm, img, w, h, mode, dim, sat)
    font = ImageFont.truetype(FONT, 16)
    im = Image.new('RGB', (w*8, h*17)); d = ImageDraw.Draw(im)
    for y, (cs, ats) in enumerate(rows):
        for x, (c, a) in enumerate(zip(cs, ats)):
            d.rectangle([x*8, y*17, x*8+7, y*17+16], fill=th.rgb[(a>>4)&15])
            if c != ' ': d.text((x*8, y*17), c, font=font, fill=th.rgb[a&15])
    im.save(out)

if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], float(sys.argv[6]), float(sys.argv[7]))
