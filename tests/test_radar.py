"""Rain radar: time range, geometry of a request, decoding the DWD classes,
the disk cache and the map app's keys, without a screen or the network."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image  # noqa: E402

from monocell import maps, radar  # noqa: E402
from monocell.apps.maps import Maps  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402
from monocell.term import Key  # noqa: E402

CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities><Capability><Layer><Title>DWD</Title>
  <Layer queryable="1" opaque="0">
    <Name>Radar_other_thing</Name>
    <Dimension name="time" default="current" units="ISO8601">2026-01-01T00:00:00.000Z/2026-01-02T00:00:00.000Z/PT5M</Dimension>
    <Dimension name="REFERENCE_TIME" units="ISO8601">%s</Dimension>
  </Layer>
  <Layer queryable="1" opaque="0">
    <Name>Radar_rv_product_1x1km_ger</Name>
    <Title>Deutsches Radarkomposit</Title>
    <CRS>EPSG:4326</CRS>
    <BoundingBox CRS="EPSG:4326" minx="45.684605781367544" miny="1.4633015102566664" maxx="56.21940322990386" maxy="18.731616454665566"/>
    <BoundingBox CRS="EPSG:3857" minx="162893.97" miny="5729950.32" maxx="2085194.00" maxy="7602217.19"/>
    <Dimension name="time" default="current" units="ISO8601">2026-11-16T00:00:00.000Z/2026-11-19T10:40:00.000Z/PT5M</Dimension>
    <Dimension name="REFERENCE_TIME" default="2026-11-19T08:35:00.000Z" units="ISO8601">2026-11-19T08:30:00.000Z,2026-11-19T08:35:00.000Z</Dimension>
  </Layer>
</Layer></Capability></WMS_Capabilities>
""" % ','.join('2026-11-19T08:%02d:00.000Z' % i for i in range(60))


def png(rows, alpha=255, size=None):
    """A PNG of `rows` (each a list of RGB), RGBA with one alpha for all."""
    h, w = len(rows), len(rows[0])
    img = Image.new('RGBA', (w, h))
    img.putdata([tuple(c) + (alpha,) for row in rows for c in row])
    if size:
        img = img.resize(size, Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def class_image(width=8, height=6, alpha=255):
    """The same as a PIL image, for compose (one stripe per class, top to bottom)."""
    n = len(radar.RAMP)
    img = Image.new('RGBA', (width, height))
    img.putdata([tuple(radar.RAMP[min(n - 1, y * n // height)]) + (alpha,)
                 for y in range(height) for _ in range(width)])
    return img


def class_png(width=8, height=6, alpha=255):
    """One horizontal stripe per class, in the legend's own colours."""
    rows = []
    for i, c in enumerate(radar.RAMP):
        rows += [[c] * width] * max(1, height // len(radar.RAMP))
    return png(rows, alpha)


class TestTime(unittest.TestCase):
    def test_iso_roundtrip(self):
        for when in (0, 1795077300, 1795084800):
            self.assertEqual(radar._parse_iso(radar.iso(when)), when)
        self.assertEqual(radar.iso(1795077300), '2026-11-19T08:35:00.000Z')

    def test_parse_period(self):
        self.assertEqual(radar._parse_period('PT5M'), 300)
        self.assertEqual(radar._parse_period('PT1H'), 3600)
        self.assertEqual(radar._parse_period('PT1H30M'), 5400)
        self.assertRaises(ValueError, radar._parse_period, 'P1D')

    def test_parse_range_ignores_the_neighbour_layer(self):
        rng = radar.parse_range(CAPS)
        self.assertEqual(rng, (radar._parse_iso('2026-11-16T00:00:00.000Z'),
                               radar._parse_iso('2026-11-19T10:40:00.000Z'), 300))
        self.assertIsNone(radar.parse_range('<html>'))

    def test_parse_coverage_is_latitude_first(self):
        south, west, north, east = radar.parse_coverage(CAPS)
        self.assertAlmostEqual(south, 45.6846, places=3)
        self.assertAlmostEqual(west, 1.4633, places=3)
        self.assertAlmostEqual(north, 56.2194, places=3)
        self.assertAlmostEqual(east, 18.7316, places=3)

    def test_frame_time_snaps_down_and_clamps(self):
        start, end, step = radar._parse_iso('2026-11-16T00:00:00.000Z'), \
            radar._parse_iso('2026-11-19T10:40:00.000Z'), 300
        rng = (start, end, step)
        now = start + 12 * step + 120          # two minutes into a frame
        self.assertEqual(radar.frame_time(rng, now, 0), start + 12 * step)
        self.assertEqual(radar.frame_time(rng, now, 10), start + 14 * step)
        self.assertEqual(radar.frame_time(rng, now, -30), start + 6 * step)
        self.assertEqual(radar.frame_time(rng, end + 9999, 0), end)      # no future
        self.assertEqual(radar.frame_time(rng, start - 9999, -120), start)
        self.assertIsNone(radar.frame_time(None, now, 0))

    def test_labels(self):
        self.assertEqual(radar.offset_label(0), 'JETZT')
        self.assertEqual(radar.offset_label(-1), 'JETZT')
        self.assertEqual(radar.offset_label(-5), 'JETZT')
        self.assertEqual(radar.offset_label(-6), '-0:06')
        self.assertEqual(radar.offset_label(-70), '-1:10')
        self.assertEqual(radar.offset_label(40), '+0:40')
        self.assertEqual(radar.offset_label(-120), '-2:00')
        self.assertEqual(radar.clock(0), time.strftime('%H:%M', time.localtime(0)))


class TestGeometry(unittest.TestCase):
    def test_padded_box_is_twice_the_canvas(self):
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        (x0, y0, x1, y1), crop = radar.view_boxes(v)
        self.assertLess(x0, x1)
        self.assertLess(y0, y1)
        # mercator width of the whole world at z12: 2 tiles of 256 px
        world = 2 * 3.141592653589793 * 6378137.0 / (1 << 12)
        self.assertAlmostEqual(x1 - x0, 2 * world, places=0)
        self.assertAlmostEqual(y1 - y0, 2 * world * 192 / 256.0, places=0)

    def test_crop_stays_inside_the_picture(self):
        for lat, lon, z in ((52.52, 13.405, 12), (48.1, 11.6, 15), (54.0, 9.0, 3)):
            v = maps.MapView(lat, lon, z, 256, 192)
            (x0, y0, x1, y1), (left, top) = radar.view_boxes(v)
            self.assertTrue(0 <= left <= 256, (lat, z, left))
            self.assertTrue(0 <= top <= 192, (lat, z, top))

    def test_panning_a_little_reuses_the_same_box(self):
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        box0 = radar.view_boxes(v)[0]
        v.pan_pixels(32, 0)                                # one arrow press
        self.assertEqual(radar.view_boxes(v)[0], box0)
        v.pan_pixels(160, 0)                               # far enough to move on
        self.assertNotEqual(radar.view_boxes(v)[0], box0)

    def test_near_the_pole_there_is_nothing(self):
        self.assertIsNone(radar.view_boxes(maps.MapView(85.0, 0.0, 6, 256, 192)))

    def test_getmap_url(self):
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        box, crop = radar.view_boxes(v)
        url = radar.getmap_url(box, (512, 384), 1795077300)
        self.assertIn('request=GetMap', url)
        self.assertIn('layers=dwd:Radar_rv_product_1x1km_ger', url)
        self.assertIn('crs=EPSG:3857', url)
        self.assertIn('width=512&height=384', url)
        self.assertIn('transparent=TRUE', url)
        self.assertIn('time=2026-11-19T08:35:00.000Z', url)
        self.assertIn('bbox=%.1f,' % box[0], url)

    def test_request_name_is_stable(self):
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        req = radar.Request(radar.view_boxes(v)[0], (512, 384), 1795077300,
                            radar.view_boxes(v)[1])
        self.assertEqual(req.name.count('|'), 2)
        self.assertIn('1795077300', req.name)


class TestDecode(unittest.TestCase):
    def test_classes_get_their_own_alpha(self):
        img = radar.decode(class_png(8, 30), (8, 30))       # two rows per class
        rows = [i * 2 + 1 for i in range(len(radar.RAMP))]
        for i, y in enumerate(rows):
            self.assertEqual(img.getpixel((3, y))[3], int(round(255 * radar.ALPHA[i])),
                             'class %d' % i)
        self.assertEqual(img.getpixel((3, rows[0]))[:3], radar.RAMP[0])

    def test_no_data_is_transparent(self):
        grey = (126, 126, 126)
        data = png([[grey] * 8 for _ in range(8)], alpha=77)
        img = radar.decode(data, (8, 8))
        self.assertEqual(img.getpixel((4, 4))[3], 0)

    def test_a_service_exception_becomes_a_message(self):
        xml = (b'<?xml version="1.0"?><ServiceExceptionReport>'
               b'<ServiceException code="InvalidDimensionValue">'
               b"Could not find a match for 'time'</ServiceException></ServiceExceptionReport>")
        with self.assertRaises(ValueError) as cm:
            radar.decode(xml, (8, 8))
        self.assertIn('KEIN BILD ZU DIESER ZEIT', str(cm.exception))

    def test_a_picture_of_the_wrong_size_is_scaled(self):
        img = radar.decode(class_png(8, 30), (64, 48))
        self.assertEqual(img.size, (64, 48))


class FakeFetch:
    def __init__(self, caps=CAPS, image=None, fail=None):
        self.caps, self.image, self.fail = caps, image, fail
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError(self.fail)
        if 'GetCapabilities' in url:
            return self.caps.encode()
        return self.image if self.image is not None else class_png()


class TestLayer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def layer(self, **kw):
        return radar.RadarLayer(cache_dir=self.dir, **kw)

    def test_nothing_before_the_range_is_known(self):
        L = self.layer(fetch=FakeFetch())
        self.assertTrue(L.range_stale())
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        self.assertIsNone(L.request(v, 0))
        self.assertIsNone(L.image(None))

    def test_load_range_and_request(self):
        f = FakeFetch()
        L = self.layer(fetch=f)
        rng = L.load_range()
        self.assertFalse(L.range_stale())
        self.assertIn('GetCapabilities', f.urls[0])
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        req = L.request(v, 0, now=rng[1] - 3600)
        self.assertEqual(req.size, (512, 384))
        self.assertEqual(req.when % 300, 0)
        self.assertIsNone(L.image(req))                     # not fetched yet

    def test_out_of_coverage_asks_for_nothing(self):
        L = self.layer(fetch=FakeFetch())
        L.load_range()
        self.assertIsNone(L.request(maps.MapView(35.0, 139.0, 10, 256, 192), 0))
        self.assertTrue(L.covers(maps.MapView(52.52, 13.405, 12, 256, 192)))
        self.assertFalse(L.covers(maps.MapView(40.4, -3.7, 6, 256, 192)))    # Madrid

    def test_load_writes_the_disk_cache_and_reads_it_back(self):
        f = FakeFetch()
        L = self.layer(fetch=f)
        L.load_range()
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        req = L.request(v, 0)
        L.load(req)
        self.assertEqual(L.fetched, 1)
        self.assertEqual(len(L.images), 1)
        self.assertEqual(L.image(req).size, (256, 192))
        self.assertIn('time=', f.urls[-1])
        self.assertTrue(os.path.exists(L.path(req.name)))
        again = self.layer(fetch=FakeFetch(fail='offline'))      # a fresh start
        self.assertIsNotNone(again.image(req))
        self.assertEqual(again.fetched, 0)

    def test_a_broken_answer_raises_and_keeps_nothing(self):
        L = self.layer(fetch=FakeFetch(image=b'<html>nope</html>'))
        L.load_range()
        req = L.request(maps.MapView(52.52, 13.405, 12, 256, 192), 0)
        self.assertRaises(ValueError, L.load, req)
        self.assertIsNone(L.image(req))

    def test_the_range_survives_a_restart(self):
        L = self.layer(fetch=FakeFetch())
        L.load_range()
        again = self.layer(fetch=FakeFetch(fail='offline'))
        self.assertEqual(again.range, L.range)
        self.assertFalse(again.range_stale())

    def test_the_nowcast_end_moves_with_the_clock(self):
        L = self.layer(fetch=FakeFetch())
        L.load_range(now=1795077300)                     # 08:35 UTC
        end = L.range[1]
        self.assertEqual(L.span(now=1795077300)[1], end)
        later = 1795077300 + 900                         # 15 minutes on
        self.assertEqual(L.span(now=later)[1], end + 900)
        far = 1795077300 + 6 * 3600                      # no further than NOWCAST
        self.assertEqual(L.span(now=far)[1], end + radar.NOWCAST * 60)
        self.assertFalse(L.range_stale(now=1795077300 + 60))
        self.assertTrue(L.range_stale(now=1795077300 + radar.RANGE_TTL + 60))

    def test_a_frame_in_the_future_asks_for_the_times_again(self):
        L = self.layer(fetch=FakeFetch())                # the answer says "no such time"
        L.load_range(now=1795077300)
        req = radar.Request((0, 0, 1, 1), (2, 2), 1795077300, (0, 0))
        L.fetch = lambda url: (b'<?xml version="1.0"?><ServiceExceptionReport>'
                               b'<ServiceException code="InvalidDimensionValue">time'
                               b'</ServiceException></ServiceExceptionReport>')
        self.assertRaises(ValueError, L.load, req)
        self.assertTrue(L.range_stale())

    def test_range_error_is_remembered(self):
        L = self.layer(fetch=FakeFetch(fail='kein netz'))
        self.assertRaises(OSError, L.load_range)
        self.assertIn('RADAR', L.range_error)
        self.assertIsNone(L.range)

    def test_prune_keeps_the_newest(self):
        L = self.layer(fetch=FakeFetch())
        L.load_range()
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        for i in range(8):
            req = L.request(v, i * 10)
            L.load(req)
        frames = [f for f in os.listdir(self.dir) if f.endswith('.png')]
        self.assertEqual(len(frames), 8)
        self.assertEqual(L.prune(keep=3), 5)
        self.assertEqual(len([f for f in os.listdir(self.dir) if f.endswith('.png')]), 3)


class TestPalette(unittest.TestCase):
    def test_radar_classes_survive_the_palette(self):
        plain = maps.map_palette()
        wet = maps.map_palette(radar.RAMP)
        self.assertEqual(len(wet), 256)
        self.assertEqual(len(set(wet)), 256)
        self.assertNotEqual(plain, wet)
        for c in radar.RAMP:
            self.assertIn(c, wet)

    def test_compose_lays_the_layer_on_top(self):
        class Store:
            def get(self, z, x, y):
                return Image.new('RGB', (maps.TILE, maps.TILE), (255, 255, 255))

        v = maps.MapView(52.52, 13.405, 12, 64, 48)
        out = maps.compose(v, Store(), radar=class_image(64, 48))
        self.assertEqual(out.size, (64, 48))
        self.assertEqual(out.mode, 'RGB')
        self.assertNotEqual(out.getpixel((10, 30)), (255, 255, 255))

    def test_without_a_layer_the_map_is_untouched(self):
        class Store:
            def get(self, z, x, y):
                return Image.new('RGB', (maps.TILE, maps.TILE), (10, 20, 30))

        v = maps.MapView(52.52, 13.405, 12, 64, 48)
        self.assertEqual(maps.compose(v, Store()).getpixel((5, 5)), (10, 20, 30))
        self.assertEqual(maps.compose(v, Store(), radar=None).getpixel((5, 5)), (10, 20, 30))


# ---------------------------------------------------------------- the app
class FakeCfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def save(self):
        pass


class FakeLoop:
    def __init__(self):
        self.later = []

    def call_later(self, delay, cb, owner=None):
        self.later.append(cb)

    def every(self, *a, **kw):
        return None

    def run_in_thread(self, fn, done=None, error=None):
        self.later.append(fn)


class FakeWin:
    closed = False
    borderless = False
    cw, ch = 72, 20
    x = y = 0
    title = ''


class FakeTerm:
    w, h = 113, 48
    fd_out = -1


class FakeWM:
    def __init__(self, **cfg):
        self.cfg = FakeCfg(**cfg)
        self.theme = Theme(Config())
        self.loop = FakeLoop()
        self.term = FakeTerm()
        self.dirty = False
        self.toasts = []

    def toast(self, text, color=None, secs=None):
        self.toasts.append(text)

    def invalidate(self):
        self.dirty = True


class FakeRadar:
    """Stands in for the network: a range, a picture, nothing else."""

    def __init__(self, image=None, covers=True, when=1795077300):
        self.range = (1794787200, 1795084800, 300)
        self.range_at = time.time()
        self.range_error = ''
        self.error = ''
        self.coverage = None
        self.image_ = image
        self._covers = covers
        self.when = when
        self.asked = []

    def range_stale(self, now=None):
        return False

    def covers(self, view):
        return self._covers

    def request(self, view, offset_min=0, now=None):
        return radar.Request(radar.view_boxes(view)[0], (512, 384), self.when,
                             radar.view_boxes(view)[1]) if self._covers else None

    def image(self, req):
        self.asked.append(req)
        return self.image_


def app(**cfg):
    wm = FakeWM(**cfg)
    a = Maps(wm, FakeWin())
    a.radar = FakeRadar(image=class_image(256, 192))
    return a


class TestApp(unittest.TestCase):
    def test_the_window_shows_the_state(self):
        a = app()
        self.assertFalse(a.radar_on)
        self.assertEqual(a.radar_state(), 'off')
        a.toggle_radar()
        self.assertTrue(a.radar_on)
        self.assertTrue(a.wm.cfg['map_radar'])
        self.assertIn('on', a.radar_state())
        self.assertIn('DWD', a.radar_state())

    def test_the_setting_is_read_at_start(self):
        self.assertTrue(app(map_radar=True).radar_on)

    def test_time_steps_clamp(self):
        a = app()
        a.step_radar_time(10)                      # radar is off: nothing happens
        self.assertEqual(a.radar_offset, 0)
        a.toggle_radar()
        a.step_radar_time(10)
        a.step_radar_time(10)
        self.assertEqual(a.radar_offset, 20)
        a.step_radar_time(-radar.STEP_UI * 100)
        self.assertEqual(a.radar_offset, -radar.SPAN)
        a.radar_now()
        self.assertEqual(a.radar_offset, 0)

    def test_the_layer_is_asked_for_and_laid_down(self):
        a = app(map_radar=True)
        pic = a.radar_picture()
        self.assertIsNotNone(pic)
        self.assertEqual(pic.size, (256, 192))
        self.assertEqual(a.radar_when, 1795077300)
        self.assertEqual(a.radar_note, '')

    def test_the_last_picture_stays_while_the_next_one_flies(self):
        a = app(map_radar=True)
        first = a.radar_picture()
        self.assertIsNotNone(first)
        a.radar.image_ = None                        # the next frame is in flight
        a.radar.when += radar.STEP
        self.assertIs(a.radar_picture(), first)      # the old picture keeps the view
        self.assertEqual(a.radar_note, 'LAEDT')

    def test_out_of_coverage_says_so(self):
        a = app(map_radar=True)
        a.radar = FakeRadar(image=None, covers=False)
        self.assertIsNone(a.radar_picture())
        self.assertIn('DEUTSCHLAND', a.radar_note)

    def test_off_means_no_picture_and_no_request(self):
        a = app()
        self.assertIsNone(a.radar_picture())
        self.assertEqual(a.radar.asked, [])

    def test_the_keys(self):
        a = app(map_radar=True)

        def press(k):
            return a.gfx_key(Key(k, k))

        self.assertTrue(press('.'))
        self.assertEqual(a.radar_offset, radar.STEP_UI)
        self.assertTrue(press(','))
        self.assertEqual(a.radar_offset, 0)
        self.assertTrue(press('w'))
        self.assertFalse(a.radar_on)


if __name__ == '__main__':
    unittest.main()
