"""OpenStreetMap for the map app: raster tiles, disk cache, Web-Mercator, place search.

The map is a raster: every picture pixel is one pixel of an OSM tile, composed into
a small canvas (a quarter of the screen, like the video player) and then handed to
the very same graphics pipeline (palette, Bayer dither, scanlines, nearest upscale).

Nothing here talks to the terminal, so the logic can be tested without one. Network
access goes through `http_get`, which tests replace.
"""
import hashlib
import io
import math
import os
import urllib.parse
import urllib.request

from PIL import Image

from .util import CACHE_DIR

TILE = 256
MAX_LAT = 85.05112878
USER_AGENT = 'monocell/1.1 (+https://github.com/3p1cball-code/monocell)'
SEARCH_URL = 'https://nominatim.openstreetmap.org/search'

# key: (name, tile URL, attribution drawn on screen)
SOURCES = {
    'osm': ('OpenStreetMap', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            'OPENSTREETMAP'),
    'osmde': ('OpenStreetMap DE', 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
              'OPENSTREETMAP'),
    'light': ('Carto Light', 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
              'OSM CARTO'),
    'dark': ('Carto Dark', 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
             'OSM CARTO'),
    'topo': ('OpenTopoMap', 'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
             'OSM OPENTOPOMAP'),
}
SOURCE_ORDER = list(SOURCES)
DEFAULT_SOURCE = 'osm'

# Where the OSM default style lives: paper, water, woods, housing, the road classes
# and the label greys. The 256 colours are shades of these, so large flat areas stay
# clean under the dither instead of speckling between two nodes of an RGB cube.
MAP_COLOURS = [
    (242, 239, 233),   # land
    (232, 228, 220),   # land, built up
    (255, 255, 255),   # road fill
    (214, 209, 207),   # road casing
    (170, 211, 223),   # water
    (140, 190, 205),   # water, deep
    (173, 209, 158),   # forest
    (200, 250, 204),   # grass
    (238, 240, 215),   # farmland
    (255, 240, 208),   # sand, beach
    (224, 223, 223),   # residential
    (217, 208, 201),   # building
    (232, 146, 162),   # motorway fill
    (249, 178, 156),   # motorway inner
    (253, 214, 161),   # primary
    (252, 233, 168),   # secondary
    (250, 157, 60),    # orange, amenities
    (208, 80, 120),    # pink, POIs
    (170, 110, 190),   # purple, POIs
    (67, 129, 75),     # green, parks/POIs
    (45, 110, 200),    # blue, water labels
    (160, 120, 100),   # brown, contours/landuse
    (51, 51, 51),      # label
    (110, 110, 110),   # label, dim
    (160, 160, 160),   # label, faint
]
SHADES = (0.62, 0.78, 0.9, 1.0, 1.08, 1.16)


def _clamp(v):
    return 0 if v < 0 else 255 if v > 255 else int(round(v))


def map_palette():
    """256 colours clustered around the map palette.

    Flat map areas are the whole picture, so the palette has to be dense around
    paper, water, green and the road fills: the ordered dither may then only mix
    neighbours that are a hair apart, and large areas stay clean. An even spread
    of greys covers the label antialiasing without pulling the paper to grey.
    """
    cols = []
    seen = set()

    def add(c):
        c = tuple(_clamp(v) for v in c)
        if c not in seen and len(cols) < 256:
            seen.add(c)
            cols.append(c)

    for c in MAP_COLOURS:
        add(c)
    for k in SHADES:
        for c in MAP_COLOURS:
            add((c[0] * k, c[1] * k, c[2] * k))
    for i in range(0, 256, 3):
        add((i, i, i))
    i = 0
    while len(cols) < 256:                    # blends of map colours, light and dark
        a = MAP_COLOURS[i % len(MAP_COLOURS)]
        b = MAP_COLOURS[(i * 7 + 3) % len(MAP_COLOURS)]
        t = ((i // len(MAP_COLOURS)) % 4 + 1) / 5.0
        add((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
             a[2] + (b[2] - a[2]) * t))
        i += 1
    return cols


MAP_VGA = map_palette()


def palette_path():
    """Cached 16x16 PPM of the map palette (fbmode.palette_file writes it on demand)."""
    from . import fbmode
    return fbmode.palette_file(MAP_VGA)


# ------------------------------------------------------------------ mercator
def deg2tile(lat, lon, z):
    """Fractional tile coordinates of a point (OSM/Google scheme, y grows south)."""
    n = float(1 << int(z))
    lon = (lon + 180.0) % 360.0 - 180.0
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, min(max(y, 0.0), n)          # -4e-10 at the pole would floor to -1


def tile2deg(x, y, z):
    """Inverse of deg2tile: tile coordinates -> (lat, lon), longitude wrapped."""
    n = float(1 << int(z))
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    lon = (lon + 180.0) % 360.0 - 180.0
    return lat, lon


def meters_per_pixel(lat, z):
    """Ground metres per tile pixel (one canvas pixel = one tile pixel)."""
    return 156543.03392804097 * math.cos(math.radians(max(-MAX_LAT, min(MAX_LAT, lat)))) \
        / float(1 << int(z))


def zoom_for_bbox(bbox, width=256, height=192, min_z=2, max_z=17):
    """Largest zoom at which a Nominatim bounding box [s, w, n, e] fits the canvas."""
    try:
        s, w, n, e = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if e < w:
        e += 360.0
    for z in range(max_z, min_z - 1, -1):
        x0, y0 = deg2tile(n, w, z)
        x1, y1 = deg2tile(s, e, z)
        if (x1 - x0) * TILE <= width * 0.85 and (y1 - y0) * TILE <= height * 0.85:
            return z
    return min_z


NICE_METRES = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
               20000, 50000, 100000, 200000, 500000]


def scale_bar(mpp, target=64):
    """(metres, length in canvas pixels) for a scale bar about `target` pixels long."""
    mpp = max(1e-6, mpp)
    m = min(NICE_METRES, key=lambda v: abs(v / mpp - target))
    return m, max(1, int(round(m / mpp)))


def fmt_metres(m):
    if m >= 1000:
        v = m / 1000.0
        return ('%dKM' if v == int(v) else '%.1fKM') % v
    return '%dM' % m


def fmt_latlon(lat, lon):
    return '%.4f%s %.4f%s' % (abs(lat), 'N' if lat >= 0 else 'S',
                              abs(lon), 'E' if lon >= 0 else 'W')


# ------------------------------------------------------------------ http
def http_get(url, timeout=15, headers=None):
    h = {'User-Agent': USER_AGENT, 'Accept': '*/*'}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# ------------------------------------------------------------------ tiles
def source_info(key):
    """(name, tile URL, attribution) for a preset key or a raw {z}/{x}/{y} URL."""
    if key in SOURCES:
        return SOURCES[key]
    return ('Custom tiles', key, 'OSM')


def source_url(key):
    return source_info(key)[1]


def tile_url(template, z, x, y):
    return template.format(z=int(z), x=int(x), y=int(y))


class TileStore:
    """Tiles on disk, decoded images in a small LRU. Never touches the UI."""

    def __init__(self, template=None, cache_dir=None, fetch=None, max_images=64,
                 timeout=15):
        self.template = template or source_url(DEFAULT_SOURCE)
        self.fetch = fetch or (lambda url: http_get(url, timeout=timeout)[1])
        self.max_images = max_images
        self.cache_dir = cache_dir or os.path.join(
            CACHE_DIR, 'maps', hashlib.sha1(self.template.encode()).hexdigest()[:10])
        self.images = {}          # (z,x,y) -> PIL image
        self.order = []           # LRU, oldest first
        self.error = ''
        self.fetched = 0

    def set_source(self, key_or_url):
        template = source_url(key_or_url) if key_or_url in SOURCES else key_or_url
        if template == self.template:
            return False
        self.template = template
        self.cache_dir = os.path.join(
            CACHE_DIR, 'maps', hashlib.sha1(template.encode()).hexdigest()[:10])
        self.images.clear()
        self.order = []
        return True

    def path(self, z, x, y):
        return os.path.join(self.cache_dir, '%02d' % int(z), '%d' % int(x), '%d.png' % int(y))

    def _remember(self, key, img):
        self.images[key] = img
        self.order = [k for k in self.order if k != key] + [key]
        while len(self.order) > self.max_images:
            old = self.order.pop(0)
            self.images.pop(old, None)

    def get(self, z, x, y):
        """Decoded tile from memory or disk, or None (a fetch is needed)."""
        key = (int(z), int(x), int(y))
        img = self.images.get(key)
        if img is not None:
            self._remember(key, img)
            return img
        p = self.path(*key)
        try:
            with open(p, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        try:
            img = Image.open(io.BytesIO(data)).convert('RGB')
        except Exception:
            try:
                os.remove(p)                   # broken download: fetch it again
            except OSError:
                pass
            return None
        if img.size != (TILE, TILE):
            img = img.resize((TILE, TILE), Image.NEAREST)
        self._remember(key, img)
        return img

    def fetch_one(self, z, x, y):
        """Download one tile into the cache. True on success."""
        p = self.path(z, x, y)
        try:
            data = self.fetch(tile_url(self.template, z, x, y))
        except Exception as e:                 # network, HTTP, timeout
            self.error = str(e)[:60]
            return False
        if not data or len(data) < 64:
            self.error = 'empty tile'
            return False
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            self.error = 'not an image'
            return False
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, p)
        except OSError as e:
            self.error = str(e)[:60]
            return False
        self.error = ''
        self.fetched += 1
        return True

    def load(self, keys):
        """Fetch a list of (z,x,y) one after the other (the tile policy allows that).
        Returns the number of tiles that arrived."""
        n = 0
        for z, x, y in keys:
            if os.path.exists(self.path(z, x, y)):
                continue
            if self.fetch_one(z, x, y):
                n += 1
            else:
                break                          # offline: no point in 9 more timeouts
        return n


# ------------------------------------------------------------------ the view
class MapView:
    """Where the camera looks: centre, tile zoom and the canvas it is drawn into."""

    def __init__(self, lat=52.5200, lon=13.4050, zoom=12, width=256, height=192):
        self.lat = float(lat)
        self.lon = float(lon)
        self.zoom = max(1, min(19, int(zoom)))
        self.width = max(1, int(width))
        self.height = max(1, int(height))

    def resize(self, width, height):
        self.width, self.height = max(1, int(width)), max(1, int(height))

    def centre_tile(self):
        return deg2tile(self.lat, self.lon, self.zoom)

    def set_centre(self, lat, lon):
        self.lat = max(-MAX_LAT, min(MAX_LAT, float(lat)))
        self.lon = (float(lon) + 180.0) % 360.0 - 180.0

    def pan_pixels(self, dx, dy):
        """Move the camera by dx/dy canvas pixels (east/south positive)."""
        cx, cy = self.centre_tile()
        self.set_centre(*tile2deg(cx + dx / float(TILE), cy + dy / float(TILE), self.zoom))

    def zoom_by(self, delta):
        z = max(1, min(19, self.zoom + int(delta)))
        if z == self.zoom:
            return False
        self.zoom = z
        return True

    def mpp(self):
        return meters_per_pixel(self.lat, self.zoom)

    def visible_tiles(self):
        """[(z, x, y, paste_x, paste_y)] covering the canvas, x wrapped around the world."""
        cx, cy = self.centre_tile()
        px = cx * TILE - self.width / 2.0
        py = cy * TILE - self.height / 2.0
        n = 1 << self.zoom
        x0, x1 = int(math.floor(px / TILE)), int(math.floor((px + self.width - 1) / TILE))
        y0, y1 = int(math.floor(py / TILE)), int(math.floor((py + self.height - 1) / TILE))
        out = []
        for ty in range(y0, y1 + 1):
            if ty < 0 or ty >= n:
                continue                       # above the pole / below the world
            for tx in range(x0, x1 + 1):
                out.append((self.zoom, tx % n, ty, int(round(tx * TILE - px)),
                            int(round(ty * TILE - py))))
        return out

    def bounds(self, pad=2.0):
        """Geographic box of the canvas, grown by `pad`, as Nominatim's viewbox."""
        cx, cy = self.centre_tile()
        half_w = self.width / 2.0 / TILE * pad
        half_h = self.height / 2.0 / TILE * pad
        lat_n, lon_w = tile2deg(cx - half_w, cy - half_h, self.zoom)
        lat_s, lon_e = tile2deg(cx + half_w, cy + half_h, self.zoom)
        return '%.4f,%.4f,%.4f,%.4f' % (lon_w, lat_n, lon_e, lat_s)

    def latlon_at(self, x, y):
        """Geographic position of a canvas pixel (for the HUD / clicks)."""
        cx, cy = self.centre_tile()
        return tile2deg(cx + (x - self.width / 2.0) / TILE,
                        cy + (y - self.height / 2.0) / TILE, self.zoom)


BG = (242, 239, 233)


def compose(view, store, background=BG):
    """The canvas as a PIL image (RGB). Missing tiles stay the background colour."""
    img = Image.new('RGB', (view.width, view.height), background)
    for z, x, y, px, py in view.visible_tiles():
        t = store.get(z, x, y)
        if t is not None:
            img.paste(t, (px, py))
    return img


def missing_tiles(view, store):
    return [(z, x, y) for z, x, y, _, _ in view.visible_tiles() if store.get(z, x, y) is None]


# ------------------------------------------------------------------ place search
def _parse_results(text):
    import json
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return []
    out, seen = [], set()
    for r in rows if isinstance(rows, list) else []:
        try:
            lat, lon = float(r.get('lat')), float(r.get('lon'))
        except (TypeError, ValueError):
            continue
        name = (r.get('name') or '').strip()
        detail = (r.get('display_name') or '').strip()
        if not name:
            name = detail.split(',')[0] if detail else '?'
        if detail in seen:
            continue
        seen.add(detail)
        out.append({'name': name, 'detail': _short_detail(name, detail), 'lat': lat,
                    'lon': lon, 'bbox': r.get('boundingbox') or [],
                    'kind': r.get('type') or ''})
    return out


def _short_detail(name, display):
    """The town/district part of a Nominatim display name (skips house numbers)."""
    for part in (p.strip() for p in display.split(',')[1:]):
        if part and part != name and not part.replace('-', '').isdigit():
            return part
    return ''


def geocode(query, limit=8, url=SEARCH_URL, fetch=None, timeout=20, viewbox=None):
    """Nominatim lookup. Returns a list of {name, detail, lat, lon, bbox, kind}.

    `viewbox` (the map's own area) only *prefers* nearby hits - a search for a street
    name that exists in twenty towns should start with the one on screen."""
    query = (query or '').strip()
    if not query:
        return []
    params = {'q': query, 'format': 'jsonv2', 'limit': int(limit),
              'addressdetails': 0, 'accept-language': 'de'}
    if viewbox:
        params['viewbox'] = viewbox
        params['bounded'] = 0
    params = urllib.parse.urlencode(params)
    fetch = fetch or (lambda u: http_get(u, timeout=timeout)[1])
    data = fetch(url + '?' + params)
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    return _parse_results(data)


def cache_size(cache_dir=None):
    """(tiles, bytes) in the tile cache, for the status line."""
    root = cache_dir or os.path.join(CACHE_DIR, 'maps')
    n = size = 0
    for base, _, files in os.walk(root):
        for f in files:
            if not f.endswith('.png'):
                continue
            try:
                size += os.path.getsize(os.path.join(base, f))
                n += 1
            except OSError:
                pass
    return n, size
