"""IBM Proprinter X24E output: PPDS setup, text, bit-image bands and the test page."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import printer, richdoc  # noqa: E402
from monocell.screen import BOLD  # noqa: E402


class TestSetup(unittest.TestCase):
    def test_setup_selects_charset_pitch_and_quality(self):
        data = printer.setup(66, cpi=10, lpi=6, quality='draft')
        self.assertTrue(data.startswith(b'\x1b6'))       # character set 2 (umlauts)
        self.assertIn(b'\x12', data)                     # 10 cpi
        self.assertIn(b'\x1bA\x0c\x1b2', data)           # 12/72 inch = 6 lpi
        self.assertIn(b'\x1bCB', data)                   # form length 66 lines
        self.assertIn(b'\x1bI\x00', data)                # draft
        data = printer.setup(72, cpi=12, lpi=8, quality='letter')
        self.assertIn(b'\x1b:', data)                    # 12 cpi
        self.assertIn(b'\x1b0', data)                    # 8 lpi
        self.assertIn(b'\x1bCH', data)

    def test_text_line_bolds_and_umlauts(self):
        attrs = [0] * 3 + [(BOLD << 8)] * 4
        line = printer.text_line('ab \u00c4\u00d6\u00dc\u00df', attrs)
        self.assertIn(b'ab ', line)                      # outside the run
        self.assertIn(b'\x1bE', line)                    # emphasized
        self.assertIn(b'\x1bF', line)
        head, rest = line.split(b'\x1bE', 1)
        self.assertIn('\u00c4\u00d6\u00dc\u00df'.encode('cp437'), rest)

    def test_trailing_spaces_are_not_sent(self):
        self.assertEqual(printer.text_line('a   ', []), b'a')

    def test_band_is_24_wire_180_dpi(self):
        rows = [bytes([0xFF] * 4) for _ in range(24)]
        data = printer.band(rows, 4)
        self.assertTrue(data.startswith(b'\x1b[g'))
        n = data[3] | data[4] << 8
        self.assertEqual(n, 4 * 3 + 1)                   # three bytes per column
        self.assertEqual(data[5], 11)                    # mode 11: 24 pins, 180 dpi
        self.assertEqual(data[6:], b'\xff' * 12)             # all 24 pins

    def test_band_can_be_empty_and_trims_blank_columns(self):
        self.assertEqual(printer.band([bytes(8) for _ in range(24)], 8), b'')
        rows = [bytes(8) for _ in range(24)]
        rows[0] = bytes([0, 1, 0, 0])
        data = printer.band(rows, 8)
        n = data[3] | data[4] << 8
        self.assertEqual(n, 2 * 3 + 1)                   # cut after the last dot

    def test_vunits_and_feed(self):
        self.assertEqual(printer.vunits(180), b'\x1b[\\\x04\x00\x00\x00\x00\xb4')
        self.assertEqual(printer.feed(30), b'\x1bJ\x1e')
        self.assertEqual(printer.feed(300), b'\x1bJ\xff' + b'\x1bJ\x2d')


class TestSheets(unittest.TestCase):
    def page(self, **kw):
        return richdoc.Page(dict({'paper': 'a4', 'cpi': '10', 'lpi': '6'}, **kw))

    def sheet(self, text, **kw):
        pg = self.page(**kw)
        doc = richdoc.parse(text)
        lay = richdoc.Layout(_wm(), pg.text_w, sync=True, mono=True, page=pg.rows).run(doc)
        return pg, richdoc.sheets(lay, pg, 'Titel', '07.11.2026')

    def test_text_and_page_numbers(self):
        pg, sheets = self.sheet('Hallo\n')
        data = printer.encode_sheets(sheets, pg)
        self.assertIn(b'Hallo', data)
        self.assertIn(b'Seite 1 von 1', data)
        body = data[len(printer.setup()):]               # init holds a \x0c of its own
        self.assertEqual(body.count(b'\x0c'), 1)
        self.assertTrue(data.endswith(b'\x0c'))

    def test_graphics_block_uses_bands(self):
        class Raster:
            def __init__(self):
                self.calls = []

            def dot_rows(self, sheet, r0, r1):
                self.calls.append((r0, r1))
                return [b'\xff\xff'] * int(round((r1 - r0) * 30))
        pg = self.page()
        sh = printer.Sheet(1, [(' ' * pg.cols, []) for _ in range(pg.lines)])
        sh.rows[5] = ('x' * 10, [])
        sh.gfx = [(0, 5, 2, 2, None)]
        ras = Raster()
        data = printer.encode_sheets([sh], pg, raster=ras.dot_rows)
        self.assertEqual(ras.calls, [(5, 7)])
        self.assertIn(b'\x1b[\\\x04\x00\x00\x00\x00\xb4', data)   # 1/180 inch units
        self.assertIn(b'\x1b[g', data)
        self.assertIn(b'\x1bJ\x18', data)                         # 24 dots = one band
        self.assertIn(b'\x1b[\\\x04\x00\x00\x00\x00\xd8', data)   # back to 1/216

    def test_copies_repeat_the_sheets(self):
        pg, sheets = self.sheet('Hallo\n')
        data = printer.encode_sheets(sheets, pg, copies=3)
        self.assertEqual(data.count(b'Hallo'), 3)

    def test_test_page_has_frames(self):
        pg = self.page()
        sh = printer.test_sheet(pg)
        self.assertEqual(len(sh.rows), pg.lines)
        self.assertEqual(len(sh.gfx), 2)
        text = '\n'.join(r[0] for r in sh.rows)
        self.assertIn('MONOCELL WRITER', text)
        self.assertIn('+' + '-' * 38 + '+', text)

    def test_send_to_a_device_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'lpt')
            printer.send(b'abc', path)
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), b'abc')


class _wm:
    class theme:
        rgb = [(0, 0, 0)] * 16

        @staticmethod
        def c(fg, bg, fl=0):
            return fl

    cfg = {'image_mode': 'ascii', 'dither': 'bayer4', 'ascii_charset': 'full',
           'cell_aspect': 0}
    term = None

    def __init__(self):
        import types
        self.term = types.SimpleNamespace(allowed_bg=list(range(8)), cell_aspect=1.67,
                                          mode='linux')


if __name__ == '__main__':
    unittest.main()
