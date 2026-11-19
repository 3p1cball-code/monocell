"""Rain radar for the map app: the DWD composite "RV" as a transparent layer.

The German weather service (Deutscher Wetterdienst) publishes its radar
composite on a public GeoServer: one kilometre, all of Germany plus a ring
around it, three days of analysis and a nowcast two hours ahead, one frame
every five minutes. A single GetMap draws the whole map canvas in the DWD's
own fifteen class colours, so the map app just lays that picture over the
tiles - the ordinary graphics pipeline dithers both together, the radar is
not a second renderer.

Nothing here talks to the terminal; network access goes through `fetch`,
which the tests replace. Times are UTC seconds since the epoch.
"""
import hashlib
import io
import math
import os
import re
import time
from collections import namedtuple

from PIL import Image, ImageChops

from . import maps
from .util import CACHE_DIR

WMS = 'https://maps.dwd.de/geoserver/dwd/wms'
LAYER = 'dwd:Radar_rv_product_1x1km_ger'
NAME = 'DWD RADAR'          # in the HUD; the README names the source in full
TILE = maps.TILE

STEP = 300               # seconds per frame (the product's PT5M)
SPAN = 120               # minutes either side of now the app offers
STEP_UI = 10             # minutes one key press moves
RANGE_TTL = 1800         # seconds before the server's time range is asked again
NOWCAST = 150            # minutes the range may be carried on without asking
MAX_IMAGES = 6           # padded pictures in memory (~4.7 MB)
MAX_FILES = 240          # frames on disk (~30 kB each: a few days of panning)

# The fifteen classes of the DWD legend, light to heavy, with the lower bound
# of each class in mm/h (legend: 0.1-0.2, 0.2-0.4, 0.4-1.0 ... >=150).
RAMP = ((51, 255, 255), (26, 204, 154), (1, 153, 52), (77, 179, 27), (153, 204, 1),
        (204, 230, 1), (255, 255, 1), (255, 196, 1), (255, 137, 1), (255, 69, 1),
        (254, 0, 0), (229, 0, 76), (204, 0, 152), (102, 0, 203), (0, 0, 254))
RAMP_MM = ('0.1', '0.2', '0.4', '1', '2', '3', '5', '7.5', '10', '15',
           '30', '45', '75', '100', '150')
TICKS = (0, 6, 14)                        # classes that get an mm/h label

# How much of the map a class hides. Drizzle is a breath, a downpour is solid;
# the curve is deliberately steep, because at a quarter resolution even
# 0.2 mm/h washes the whole city green (measured against Hamburg and Mainz).
ALPHA = (0.04, 0.08, 0.14, 0.20, 0.29, 0.39, 0.50, 0.60, 0.70, 0.80,
         0.90, 0.96, 1.00, 1.00, 1.00)

# "Keine Daten" is drawn as a translucent grey around the radar range; the
# layer must not paint it. Everything below this alpha is dropped.
ALPHA_CUT = 200

Request = namedtuple('Request', 'box size when crop')
Request.name = property(lambda r: '%s|%dX%d|%s' % (
    ','.join('%.0f' % v for v in r.box), r.size[0], r.size[1], int(r.when)))


# ------------------------------------------------------------------ projection
def mercator(x, y, z):
    """Web-Mercator metres (EPSG:3857) of fractional tile coordinates."""
    n = float(1 << int(z))
    r = 6378137.0 * 2.0 * math.pi
    return (x / n - 0.5) * r, (0.5 - y / n) * r


def view_boxes(view):
    """(request box in metres, crop offset) for a view, or None.

    The picture is fetched for twice the canvas around a centre snapped to half
    a tile: panning by a quarter canvas then cuts a new picture out of the same
    download instead of asking the DWD again for every key press. Returns
    (minx, miny, maxx, maxy) and the (left, top) origin of the canvas inside
    that padded picture."""
    if abs(view.lat) > 84.0:
        return None
    w, h = view.width, view.height
    cx, cy = view.centre_tile()
    sx, sy = round(cx * 2.0) / 2.0, round(cy * 2.0) / 2.0
    dx, dy = (cx - sx) * TILE, (cy - sy) * TILE         # at most a quarter canvas
    left = int(round(w / 2.0 + dx))
    top = int(round(h / 2.0 + dy))
    half_w, half_h = w / float(TILE), h / float(TILE)   # in tiles
    x0, y0 = mercator(sx - half_w, sy - half_h, view.zoom)   # north-west corner
    x1, y1 = mercator(sx + half_w, sy + half_h, view.zoom)   # south-east corner
    return (x0, y1, x1, y0), (left, top)


def getmap_url(box, size, when):
    """The GetMap request for a padded box; `when` is UTC seconds."""
    return ('%s?service=WMS&version=1.3.0&request=GetMap&layers=%s&styles='
            '&crs=EPSG:3857&bbox=%.1f,%.1f,%.1f,%.1f&width=%d&height=%d'
            '&format=image/png&transparent=TRUE&time=%s'
            % (WMS, LAYER, box[0], box[1], box[2], box[3], size[0], size[1],
               iso(when)))


CAPABILITIES = '%s?service=WMS&version=1.3.0&request=GetCapabilities' % WMS


# ------------------------------------------------------------------ time
def iso(when):
    """UTC seconds -> '2026-11-19T08:35:00.000Z' (the WMS wants exactly that)."""
    return time.strftime('%Y-%m-%dT%H:%M:00.000Z', time.gmtime(when))


def _parse_iso(text):
    """'2026-11-19T08:35:00.000Z' -> UTC seconds (no timezone guessing)."""
    text = text.strip().replace('Z', '')
    if '.' in text:
        text = text.split('.')[0]
    t = time.strptime(text, '%Y-%m-%dT%H:%M:%S')
    import calendar
    return calendar.timegm(t)


def _parse_period(text):
    """'PT5M' / 'PT1H' / 'PT1H30M' -> seconds."""
    m = re.match(r'^P(?:[^T]*T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', text.strip())
    if not m:
        raise ValueError('period')
    h, mi, s = (int(v) if v else 0 for v in m.groups())
    total = h * 3600 + mi * 60 + s
    if total <= 0:
        raise ValueError('period')
    return total


_DIM = re.compile(r'<Dimension[^>]*name="time"[^>]*>([^<]+)</Dimension>')
_BOX4326 = re.compile(r'<BoundingBox CRS="EPSG:4326"[^>]*minx="([-\d.]+)"[^>]*'
                      r'miny="([-\d.]+)"[^>]*maxx="([-\d.]+)"[^>]*maxy="([-\d.]+)"')


def layer_block(xml, layer=LAYER):
    """The capabilities' description of one layer (bounded, so the huge
    REFERENCE_TIME value list of a neighbour does not get dragged in)."""
    key = '<Name>%s</Name>' % layer.split(':')[-1]
    i = xml.find(key)
    if i < 0:
        return ''
    j = xml.find('</Layer>', i)
    return xml[i:j] if j > i else xml[i:i + 60000]


def parse_range(xml, layer=LAYER):
    """(start, end, step) of the layer's time dimension, or None."""
    m = _DIM.search(layer_block(xml, layer))
    if not m:
        return None
    parts = m.group(1).strip().split('/')
    if len(parts) < 3:
        return None
    try:
        start, end, step = _parse_iso(parts[0]), _parse_iso(parts[1]), \
            _parse_period(parts[2])
    except (ValueError, IndexError):
        return None
    if end <= start:
        return None
    return start, end, step


def parse_coverage(xml, layer=LAYER):
    """(south, west, north, east) of the layer in degrees, or None.

    EPSG:4326 in WMS 1.3.0 is latitude first, so the box reads minx=south."""
    m = _BOX4326.search(layer_block(xml, layer))
    if not m:
        return None
    try:
        south, west, north, east = (float(v) for v in m.groups())
    except ValueError:
        return None
    if north <= south or east <= west:
        return None
    return south, west, north, east


def frame_time(rng, now, offset_min=0):
    """The frame at `now + offset`, clamped into the product's range and
    snapped down to its grid (never show a picture from the future)."""
    if not rng:
        return None
    start, end, step = rng
    t = now + offset_min * 60.0
    t = min(max(t, start), end)
    return int(start + int((t - start) // step) * step)


def offset_minutes(when, now):
    """Whole minutes between a frame and now, rounded to the nearer minute."""
    return int(round((when - now) / 60.0))


def offset_label(minutes):
    """'JETZT', '-1:10', '+0:40' for the HUD.

    The product's newest frame is a few minutes old, so anything within one
    step of the clock is simply "now" - a '-0:03' would only puzzle."""
    if abs(minutes) < 6:
        return 'JETZT'
    m = abs(int(minutes))
    return '%s%d:%02d' % ('-' if m and minutes < 0 else '+', m // 60, m % 60)


def clock(when, local=True):
    """'08:40' of a frame in local time (the console's timezone)."""
    return time.strftime('%H:%M', time.localtime(when) if local else time.gmtime(when))


# ------------------------------------------------------------------ the layer
def exception_text(data):
    """The message of a WMS ServiceException, for the status line."""
    try:
        text = data.decode('utf-8', 'replace')
    except Exception:
        return ''
    m = re.search(r'<ServiceException[^>]*>(.*?)</ServiceException>', text, re.S)
    msg = re.sub(r'\s+', ' ', (m.group(1) if m else text)).strip()
    if 'InvalidDimensionValue' in text:
        return 'RADAR: KEIN BILD ZU DIESER ZEIT'
    return msg[:70] or 'RADAR: UNGUELTIGE ANTWORT'


def decode(data, size):
    """A GetMap answer as RGBA of `size`.

    The DWD paints "no data" as a translucent grey; that is dropped. Each rain
    class then gets its own opacity (ALPHA), so drizzle tints the map instead of
    covering it and a downpour hides it. The class is found by quantising the
    picture against the legend's fifteen colours - one pass in C, no Python loop."""
    if not data or data[:4] != b'\x89PNG':
        raise ValueError(exception_text(data))
    src = Image.open(io.BytesIO(data)).convert('RGBA')
    if src.size != tuple(size):
        src = src.resize(tuple(size), Image.NEAREST)
    pal = Image.new('P', (1, 1))
    pal.putpalette([c for rgb in RAMP for c in rgb] +
                   [0, 0, 0] * (256 - len(RAMP)))
    classes = src.convert('RGB').quantize(palette=pal, dither=Image.Dither.NONE)
    alpha = Image.frombytes('L', classes.size, classes.tobytes()).point(
        [int(round(255 * a)) for a in ALPHA] + [0] * (256 - len(ALPHA)))
    data_mask = src.getchannel('A').point(lambda v: 255 if v >= ALPHA_CUT else 0)
    alpha = ImageChops.multiply(alpha, data_mask)
    out = src.copy()
    out.putalpha(alpha)
    return out


class RadarLayer:
    """The radar picture for one view and time: asked for on demand, kept on disk.

    `fetch(url)` returns the body of an HTTP answer (bytes) or raises.

    The DWD's GeoServer is slow and spiky (measured 0.2 to 20 s for one picture),
    so the time range is carried on locally instead of being asked for every
    quarter of an hour: the nowcast end simply moves with the clock. The range is
    written next to the frames, so even a restart starts without a request."""

    def __init__(self, fetch=None, cache_dir=None, ttl=RANGE_TTL):
        self.fetch = fetch or (lambda url: maps.http_get(url, timeout=60)[1])
        self.cache_dir = cache_dir or os.path.join(CACHE_DIR, 'radar')
        self.ttl = ttl
        self.range = None            # (start, end, step) in UTC seconds
        self.coverage = None         # (south, west, north, east)
        self.range_at = 0.0          # when the server told us (wall clock)
        self.range_error = ''
        self.error = ''
        self.images = {}             # request name -> padded RGBA
        self.order = []
        self.fetched = 0
        self.hits = 0
        self._load_range_file()

    # -- the product's time range ---------------------------------------
    def range_stale(self, now=None):
        now = time.time() if now is None else now
        return self.range is None or now - self.range_at > self.ttl

    def span(self, now=None):
        """(start, end, step) for now: the nowcast end moves with the clock.

        The server's range ends two hours ahead; without asking again we carry
        that end along, but never by more than NOWCAST minutes past the last
        answer - after that the product may have changed (or be gone)."""
        if not self.range:
            return None
        now = time.time() if now is None else now
        start, end, step = self.range
        moved = end + max(0.0, min(now - self.range_at, NOWCAST * 60.0))
        end = min(moved, now + NOWCAST * 60.0)
        return start, int(end // step) * step, step

    def load_range(self, now=None):
        """One request (~1 MB) for the valid times and the covered area."""
        now = now or time.time()
        try:
            data = self.fetch(CAPABILITIES)
        except Exception as e:                      # network, timeout, HTTP
            self.range_error = 'RADAR: %s' % str(e)[:60]
            raise
        text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else str(data)
        rng = parse_range(text)
        if not rng:
            self.range_error = 'RADAR: ZEITBEREICH FEHLT'
            raise ValueError(self.range_error)
        self.range = rng
        self.coverage = parse_coverage(text)
        self.range_at = now
        self.range_error = ''
        self._save_range_file()
        return rng

    def covers(self, view):
        """Whether the DWD composite reaches this view at all."""
        if not self.coverage:
            return True
        south, west, north, east = self.coverage
        cx, cy = view.centre_tile()
        hw, hh = view.width / 2.0 / TILE, view.height / 2.0 / TILE
        (lat_n, lon_w) = maps.tile2deg(cx - hw, cy - hh, view.zoom)
        (lat_s, lon_e) = maps.tile2deg(cx + hw, cy + hh, view.zoom)
        return not (lat_s > north or lat_n < south or lon_e < west or lon_w > east)

    # -- requests -------------------------------------------------------
    def request(self, view, offset_min=0, now=None):
        """What to ask for (a Request), or None when there is nothing to show."""
        now = time.time() if now is None else now
        when = frame_time(self.span(now), now, offset_min)
        box = view_boxes(view)
        if not when or not box or not self.covers(view):
            return None
        bbox, crop = box
        return Request(box=bbox, size=(2 * view.width, 2 * view.height),
                       when=when, crop=crop)

    def image(self, req):
        """The canvas-sized RGBA for a request, or None (nothing fetched yet)."""
        if req is None:
            return None
        name = req.name
        img = self.images.get(name)
        if img is None:
            img = self._from_disk(name)
            if img is None:
                return None
            self._remember(name, img)
        left, top = req.crop
        w, h = req.size[0] // 2, req.size[1] // 2
        return img.crop((left, top, left + w, top + h))

    def load(self, req):
        """Fetch and remember one request (network). Returns its name."""
        data = self.fetch(getmap_url(req.box, req.size, req.when))
        try:
            img = decode(data, req.size)
        except ValueError as e:
            if 'KEIN BILD ZU DIESER ZEIT' in str(e):
                self.range_at = 0.0        # the product moved on: ask for the times
            self.error = str(e)[:60]
            raise
        self._remember(req.name, img)
        self._save(req.name, data)
        self.fetched += 1
        self.error = ''
        return req.name

    # -- cache ----------------------------------------------------------
    def path(self, name):
        return os.path.join(self.cache_dir,
                            hashlib.sha1(name.encode()).hexdigest()[:16] + '.png')

    def _from_disk(self, name):
        try:
            with open(self.path(name), 'rb') as f:
                data = f.read()
            return decode(data, tuple(int(v) for v in name.split('|')[1].split('X')))
        except (OSError, ValueError):
            return None

    def _save(self, name, data):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            p = self.path(name)
            tmp = p + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, p)
        except OSError:
            pass

    def _remember(self, name, img):
        self.images[name] = img
        self.order = [k for k in self.order if k != name] + [name]
        while len(self.order) > MAX_IMAGES:
            self.images.pop(self.order.pop(0), None)

    # -- the time range on disk (a restart should not wait for the server)
    def range_file(self):
        return os.path.join(self.cache_dir, 'range.json')

    def _load_range_file(self):
        try:
            import json
            with open(self.range_file()) as f:
                d = json.load(f)
            self.range = (int(d['start']), int(d['end']), int(d['step']))
            self.range_at = float(d['at'])
            cov = d.get('coverage')
            self.coverage = tuple(float(v) for v in cov) if cov else None
        except (OSError, ValueError, KeyError, TypeError):
            self.range, self.range_at = None, 0.0

    def _save_range_file(self):
        try:
            import json
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.range_file() + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'start': self.range[0], 'end': self.range[1],
                           'step': self.range[2], 'at': self.range_at,
                           'coverage': self.coverage}, f)
            os.replace(tmp, self.range_file())
        except OSError:
            pass

    def prune(self, keep=MAX_FILES):
        """Drop the oldest frames (panning produces a new picture every few keys)."""
        try:
            files = [(os.stat(os.path.join(self.cache_dir, f)).st_mtime,
                      os.path.join(self.cache_dir, f))
                     for f in os.listdir(self.cache_dir) if f.endswith('.png')]
        except OSError:
            return 0
        if len(files) <= keep:
            return 0
        files.sort()
        n = 0
        for _, p in files[:len(files) - keep]:
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
        return n
