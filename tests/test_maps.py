"""Maps: projection, tiles, cache, composition, place search and the still pipeline."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import fbmode, maps  # noqa: E402
from monocell.apps.maps import ascii_upper  # noqa: E402
from monocell.util import which  # noqa: E402

from PIL import Image  # noqa: E402

W, H = 320, 240
_CACHE = {}


def png(colour):
    buf = io.BytesIO()
    Image.new('RGB', (maps.TILE, maps.TILE), colour).save(buf, 'PNG')
    return buf.getvalue()


def setUpModule():
    _CACHE['old'] = maps.CACHE_DIR
    maps.CACHE_DIR = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(maps.CACHE_DIR, ignore_errors=True)
    maps.CACHE_DIR = _CACHE['old']


class TestProjection(unittest.TestCase):
    def test_roundtrip(self):
        for z in (2, 10, 14, 19):
            x, y = maps.deg2tile(52.52, 13.405, z)
            lat, lon = maps.tile2deg(x, y, z)
            self.assertAlmostEqual(lat, 52.52, places=6)
            self.assertAlmostEqual(lon, 13.405, places=6)

    def test_null_point(self):
        x, y = maps.deg2tile(0, 0, 4)
        self.assertEqual((x, y), (8.0, 8.0))

    def test_longitude_wraps(self):
        x, y = maps.deg2tile(52.0, -170.0, 5)
        lat, lon = maps.tile2deg(x + 32, y, 5)     # 32 tiles = the whole world at z5
        self.assertAlmostEqual(lon, -170.0, places=6)

    def test_latitude_is_clamped(self):
        x, y = maps.deg2tile(89.9, 0, 6)
        self.assertTrue(0 <= y <= 64)

    def test_metres_per_pixel_halves_per_zoom(self):
        a = maps.meters_per_pixel(52.5, 10)
        b = maps.meters_per_pixel(52.5, 11)
        self.assertAlmostEqual(a / b, 2.0, places=6)
        self.assertGreater(a, 20)

    def test_scale_bar(self):
        m, px = maps.scale_bar(maps.meters_per_pixel(52.5, 14))     # ~7.5 m/px
        self.assertIn(m, maps.NICE_METRES)
        self.assertTrue(40 <= px <= 100, (m, px))
        self.assertEqual(maps.fmt_metres(500), '500M')
        self.assertEqual(maps.fmt_metres(2000), '2KM')

    def test_zoom_for_bbox(self):
        big = maps.zoom_for_bbox([52.3, 13.2, 52.7, 13.6], 256, 192)
        small = maps.zoom_for_bbox([52.5201, 13.4130, 52.5202, 13.4131], 256, 192)
        self.assertLess(big, small)
        self.assertIsNone(maps.zoom_for_bbox(None))

    def test_fmt_latlon(self):
        self.assertEqual(maps.fmt_latlon(52.52, 13.405), '52.5200N 13.4050E')
        self.assertEqual(maps.fmt_latlon(-33.9, -70.6), '33.9000S 70.6000W')


class TestView(unittest.TestCase):
    def test_visible_tiles_cover_the_canvas(self):
        v = maps.MapView(52.52, 13.405, 13, 256, 192)
        tiles = v.visible_tiles()
        self.assertTrue(2 <= len(tiles) <= 12)
        for z, x, y, px, py in tiles:
            self.assertEqual(z, 13)
            self.assertTrue(-maps.TILE < px < 256 and -maps.TILE < py < 192)

    def test_pan_moves_the_centre_east(self):
        v = maps.MapView(52.52, 13.405, 14, 256, 192)
        lon0 = v.lon
        v.pan_pixels(64, 0)
        self.assertGreater(v.lon, lon0)
        v.pan_pixels(-64, 0)
        self.assertAlmostEqual(v.lon, lon0, places=5)

    def test_pan_south_grows_y(self):
        v = maps.MapView(52.52, 13.405, 14, 256, 192)
        y0 = v.centre_tile()[1]
        v.pan_pixels(0, -32)                       # camera up -> smaller tile y
        self.assertLess(v.centre_tile()[1], y0)

    def test_zoom_limits_and_centre(self):
        v = maps.MapView(52.52, 13.405, 18, 256, 192)
        self.assertTrue(v.zoom_by(1))
        self.assertEqual(v.zoom, 19)
        self.assertFalse(v.zoom_by(1))
        lat, lon = v.lat, v.lon
        self.assertTrue(v.zoom_by(-5))
        self.assertEqual((v.lat, v.lon), (lat, lon))

    def test_world_wraps_at_the_antimeridian(self):
        v = maps.MapView(0.0, 179.99, 3, 512, 384)
        for z, x, y, px, py in v.visible_tiles():
            self.assertTrue(0 <= x < 8)
        self.assertGreater(len(v.visible_tiles()), 1)

    def test_bounds_is_nominatim_order(self):
        v = maps.MapView(52.52, 13.405, 12, 256, 192)
        w, n, e, s = (float(p) for p in v.bounds().split(','))
        self.assertLess(w, e)
        self.assertLess(s, n)
        self.assertLess(w, 13.405)
        self.assertGreater(e, 13.405)


class TestPalette(unittest.TestCase):
    def test_256_unique_and_map_like(self):
        pal = maps.map_palette()
        self.assertEqual(len(pal), 256)
        self.assertEqual(len(set(pal)), 256)
        for c in ((242, 239, 233), (170, 211, 223), (51, 51, 51)):
            self.assertIn(c, pal)

    def test_palette_file_is_a_ppm(self):
        path = maps.palette_path()
        with open(path, 'rb') as f:
            data = f.read()
        self.assertTrue(data.startswith(b'P6\n16 16\n255\n'))
        self.assertEqual(len(data), len(b'P6\n16 16\n255\n') + 256 * 3)


class TestTileStore(unittest.TestCase):
    def store(self, data=None, template='https://tiles.test/{z}/{x}/{y}.png'):
        asked = []
        def fetch(url):
            asked.append(url)
            if isinstance(data, Exception):
                raise data
            return data if data is not None else png((10, 20, 30))
        st = maps.TileStore(template, cache_dir=os.path.join(maps.CACHE_DIR, 't'),
                            fetch=fetch)
        return st, asked

    def test_path_and_fetch_roundtrip(self):
        st, asked = self.store()
        self.assertTrue(st.fetch_one(14, 8800, 5372))
        self.assertEqual(asked, ['https://tiles.test/14/8800/5372.png'])
        self.assertTrue(os.path.exists(st.path(14, 8800, 5372)))
        img = st.get(14, 8800, 5372)
        self.assertEqual(img.size, (maps.TILE, maps.TILE))
        self.assertEqual(img.getpixel((0, 0)), (10, 20, 30))

    def test_get_without_file_is_none(self):
        st, _ = self.store()
        self.assertIsNone(st.get(1, 0, 0))

    def test_broken_download_is_rejected_and_removed(self):
        st, _ = self.store(data=b'<html>nope</html>')
        self.assertFalse(st.fetch_one(3, 1, 1))
        self.assertFalse(os.path.exists(st.path(3, 1, 1)))
        self.assertTrue(st.error)

    def test_network_error_is_reported(self):
        st, _ = self.store(data=OSError('offline'))
        self.assertFalse(st.fetch_one(3, 1, 1))
        self.assertIn('offline', st.error)

    def test_load_stops_after_the_first_failure(self):
        st, asked = self.store(data=OSError('offline'))
        self.assertEqual(st.load([(3, 1, 1), (3, 1, 2), (3, 1, 3)]), 0)
        self.assertEqual(len(asked), 1)

    def test_load_skips_files_already_on_disk(self):
        st, asked = self.store()
        self.assertTrue(st.fetch_one(3, 1, 1))
        st.load([(3, 1, 1), (3, 1, 2)])
        self.assertEqual(asked[-1].rsplit('/', 1)[-1], '2.png')
        self.assertEqual(len(asked), 2)

    def test_source_switch_uses_its_own_folder(self):
        st, _ = self.store()
        first = st.cache_dir
        self.assertTrue(st.set_source('light'))
        self.assertNotEqual(st.cache_dir, first)
        self.assertIn('cartocdn', st.template)
        self.assertFalse(st.set_source('light'))

    def test_lru_drops_the_oldest_images(self):
        st, _ = self.store()
        st.max_images = 2
        for x in range(3):
            st.fetch_one(5, x, 1)
            st.get(5, x, 1)
        self.assertEqual(len(st.images), 2)
        self.assertNotIn((5, 0, 1), st.images)

    def test_custom_url_template(self):
        st, asked = self.store(template='http://localhost:9000/osm/{z}/{x}/{y}.png')
        st.fetch_one(9, 2, 3)
        self.assertEqual(asked, ['http://localhost:9000/osm/9/2/3.png'])

    def test_source_info(self):
        self.assertEqual(maps.source_info('osm')[1], maps.SOURCES['osm'][1])
        name, url, attr = maps.source_info('https://x/{z}/{x}/{y}.png')
        self.assertEqual(name, 'Custom tiles')
        self.assertEqual(attr, 'OSM')


class FakeStore:
    def __init__(self, colour):
        self.colour = colour

    def get(self, z, x, y):
        return Image.new('RGB', (maps.TILE, maps.TILE), self.colour)


class TestCompose(unittest.TestCase):
    def test_tiles_are_pasted_at_their_offsets(self):
        v = maps.MapView(52.52, 13.405, 13, 256, 192)
        img = maps.compose(v, FakeStore((1, 2, 3)))
        self.assertEqual(img.size, (256, 192))
        covered = 0
        for z, x, y, px, py in v.visible_tiles():
            covered += max(0, min(256, px + 256) - max(0, px)) * \
                max(0, min(192, py + 256) - max(0, py))
        self.assertEqual(covered, 256 * 192)
        self.assertEqual(img.getpixel((100, 100)), (1, 2, 3))

    def test_missing_tiles_stay_background(self):
        class Empty:
            def get(self, z, x, y):
                return None
        v = maps.MapView(0.0, 0.0, 2, 256, 192)
        img = maps.compose(v, Empty())
        self.assertEqual(img.getpixel((50, 50)), maps.BG)
        self.assertEqual(maps.missing_tiles(v, Empty()), v.visible_tiles()[0][:3] and
                         [(z, x, y) for z, x, y, _, _ in v.visible_tiles()])


class TestSearch(unittest.TestCase):
    def test_parse_results(self):
        rows = [{'name': 'Alexanderplatz', 'display_name': 'Alexanderplatz, Mitte, Berlin',
                 'lat': '52.5219', 'lon': '13.4136',
                 'boundingbox': ['52.52', '13.41', '52.53', '13.42'], 'type': 'square'},
                {'display_name': 'Alexanderplatz, Mitte, Berlin', 'lat': '1', 'lon': '2'},
                {'name': 'kaputt', 'lat': 'x', 'lon': 'y'}]
        out = maps._parse_results(json.dumps(rows))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'Alexanderplatz')
        self.assertAlmostEqual(out[0]['lat'], 52.5219)
        self.assertEqual(out[0]['bbox'][0], '52.52')

    def test_parse_garbage(self):
        self.assertEqual(maps._parse_results('<html>'), [])
        self.assertEqual(maps._parse_results('{"a": 1}'), [])

    def test_geocode_builds_the_query(self):
        asked = []
        def fetch(url):
            asked.append(url)
            return json.dumps([{'name': 'X', 'display_name': 'X, Y', 'lat': '1', 'lon': '2'}])
        res = maps.geocode('Potsdamer Platz', fetch=fetch,
                           viewbox='13.0,52.6,13.8,52.4')
        self.assertEqual(len(res), 1)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(asked[0]).query)
        self.assertEqual(q['q'], ['Potsdamer Platz'])
        self.assertEqual(q['format'], ['jsonv2'])
        self.assertEqual(q['bounded'], ['0'])
        self.assertEqual(q['viewbox'], ['13.0,52.6,13.8,52.4'])

    def test_empty_query_does_not_call_out(self):
        def fetch(url):
            raise AssertionError('must not fetch')
        self.assertEqual(maps.geocode('   ', fetch=fetch), [])


class TestHudText(unittest.TestCase):
    def test_umlauts_are_spelled_out(self):
        self.assertEqual(ascii_upper('Grüße'), 'GRUESSE')
        self.assertEqual(ascii_upper('Königsallee'), 'KOENIGSALLEE')
        self.assertEqual(ascii_upper('Café'), 'CAFE')

    def test_font_only_glyphs(self):
        text = ascii_upper('Straße 5 · «x»')
        self.assertTrue(all(32 <= ord(c) < 127 for c in text), text)
        self.assertIn('STRASSE 5', text)


@unittest.skipUnless(which('ffmpeg'), 'needs ffmpeg')
class TestStillPipeline(unittest.TestCase):
    """The real filter chain, driven like the map app drives it."""

    def make(self, **kw):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        fb = fbmode.Framebuffer(os.path.join(d, 'fb'), geometry=(W, H, 32, W * 4))
        self.addCleanup(fb.close)
        p = fbmode.StillPipeline((W // 4, H // 4), fb, **kw)
        self.addCleanup(p.close)
        return p, fb

    def canvas(self, colour):
        return bytes(colour) * ((W // 4) * (H // 4))

    def test_frame_comes_without_waiting_for_another(self):
        # the rawvideo muxer held a frame back until the next one; PPM flushes
        p, fb = self.make(look='ega', scale=4, dither='none')
        out = p.frame(self.canvas((255, 255, 255)))
        self.assertEqual(len(out), fb.size)
        self.assertEqual(len(set(out[i:i + 4] for i in range(0, len(out), 4))), 1)

    def test_second_frame_differs(self):
        p, fb = self.make(look='ega', scale=4, dither='none')
        a = p.frame(self.canvas((0, 0, 0)))
        b = p.frame(self.canvas((255, 255, 255)))
        self.assertNotEqual(a, b)

    def test_only_palette_colours(self):
        p, fb = self.make(look='cga', scale=4, dither='bayer')
        out = p.frame(self.canvas((200, 30, 90)))
        allowed = set(fbmode.palette_colours('cga'))
        got = {(out[i + 2], out[i + 1], out[i]) for i in range(0, len(out), 4)}
        self.assertTrue(got <= allowed, got - allowed)
        self.assertGreater(len(got), 1)

    def test_map_palette_reaches_the_image(self):
        p, fb = self.make(look='vga', scale=4, dither='none', vga_palette=maps.palette_path())
        out = p.frame(self.canvas((242, 239, 233)))
        got = {(out[i + 2], out[i + 1], out[i]) for i in range(0, len(out), 4)}
        self.assertEqual(got, {(242, 239, 233)})

    def test_scanlines_darken_every_fourth_row(self):
        p, fb = self.make(look='ega', scale=4, dither='none', scanlines=True)
        out = p.frame(self.canvas((255, 255, 255)))
        rows = [out[y * fb.stride:y * fb.stride + 8] for y in range(8)]
        self.assertLess(rows[3][0], rows[0][0])

    def test_image_frame_matches_the_pipeline(self):
        p, fb = self.make(look='ega', scale=4, dither='none')
        raw = self.canvas((90, 90, 90))
        one = p.frame(raw)
        direct, cv = fbmode.image_frame(raw, (W // 4, H // 4), (W, H), W, 'bgr0',
                                        look='ega', scale=4, dither='none')
        rgb = lambda b: [b[i:i + 3] for i in range(0, len(b), 4)]   # alpha differs
        self.assertEqual(rgb(one), rgb(direct))
        self.assertEqual(cv, (W // 4, H // 4))


if __name__ == '__main__':
    unittest.main()
