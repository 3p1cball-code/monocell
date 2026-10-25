"""Core tests: text sanitation, formulas, VT emulation, input decoding, canvas, imaging."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import imaging, util  # noqa: E402
from monocell.formula import FormulaError, col_name, evaluate, fmt_number  # noqa: E402
from monocell.screen import A, Buffer, Canvas, bg_of  # noqa: E402
from monocell.term import InputParser, Key, Mouse, Paste, Terminal  # noqa: E402
from monocell import theme  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402
from monocell.vt import VTScreen, xterm256_to16  # noqa: E402


class TestUtil(unittest.TestCase):
    def test_box_and_blocks(self):
        self.assertEqual(util.clean('┌─┐│█▓░'), '+-+|#%:')
        self.assertEqual(util.clean('“hi” – ok…'), '“hi” – ok…')

    def test_ascii_folding(self):
        util.UNICODE = False
        util.clear_cache()
        try:
            self.assertEqual(util.clean('“hi” – ok… € ∞ ≤'), '"hi" - ok. E 8 <')
        finally:
            util.UNICODE = True
            util.clear_cache()

    def test_umlauts_kept_by_default(self):
        self.assertEqual(util.clean('Größe'), 'Größe')

    def test_emoji_and_pua(self):
        self.assertEqual(util.clean('😀'), '☺')
        self.assertEqual(util.clean('\ue0b0'), '>')
        self.assertEqual(util.clean('\uf8ff'), ' ')
        util.UNICODE = False
        util.clear_cache()
        try:
            self.assertEqual(util.clean('😀'), ':D')
            self.assertEqual(util.sym('files'), '[/]')
        finally:
            util.UNICODE = True
            util.clear_cache()

    def test_symbols(self):
        from monocell.wm import WM
        self.assertEqual(len(util.sym('files')), 1)
        self.assertEqual(len(util.sym('settings')), 1)
        self.assertEqual(util.sym('lan'), '[=]')
        util.UNICODE = False
        util.clear_cache()
        try:
            self.assertEqual(util.sym('wifi'), ')))')
        finally:
            util.UNICODE = True
            util.clear_cache()
        b = type('B', (), {'battery': (87, 'Discharging')})()
        self.assertIn('87%', WM.battery_text(b))
        self.assertIn('[###-]', WM.battery_text(b))
        b.battery = (75, 'Discharging')
        self.assertTrue(WM.battery_text(b).startswith('[###-]'))
        b.battery = (40, 'Charging')
        self.assertIn('+', WM.battery_text(b))
        self.assertTrue(WM.battery_text(b).startswith('[##--]'))
        b.battery = (87, 'Discharging')
        b.battery_time = (222, 'discharging')
        self.assertTrue(WM.battery_text(b).endswith('3h42'))

    def test_battery_time_estimate(self):
        import tempfile
        from monocell import hardware
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        bat = os.path.join(root, 'BAT0')
        os.makedirs(bat)

        def put(key, value):
            with open(os.path.join(bat, key), 'w') as f:
                f.write(str(value))

        put('type', 'Battery')
        put('status', 'Discharging')
        put('energy_now', 50000000)
        put('energy_full', 60000000)
        put('power_now', 10000000)
        self.assertEqual(hardware.battery_time(root), (300, 'discharging'))
        put('status', 'Charging')
        put('energy_now', 30000000)
        self.assertEqual(hardware.battery_time(root), (180, 'charging'))
        put('status', 'Full')
        self.assertIsNone(hardware.battery_time(root))
        for key in ('energy_now', 'energy_full', 'power_now'):
            os.unlink(os.path.join(bat, key))
        put('status', 'Discharging')
        put('charge_now', 2000000)
        put('charge_full', 4000000)
        put('current_now', 1000000)
        put('voltage_now', 12000000)
        self.assertEqual(hardware.battery_time(root), (120, 'discharging'))
        self.assertEqual(hardware.format_minutes(300), '5h00')
        self.assertEqual(hardware.format_minutes(125), '2h05')
        self.assertEqual(hardware.format_minutes(59), '59m')
        self.assertEqual(hardware.format_minutes(0), '')

    def test_signal_bars(self):
        from monocell.hardware import signal_bars
        self.assertEqual(signal_bars(100), ')))')
        self.assertEqual(signal_bars(67), ')))')
        self.assertEqual(signal_bars(66), '))-')
        self.assertEqual(signal_bars(34), '))-')
        self.assertEqual(signal_bars(33), ')--')
        self.assertEqual(signal_bars(1), ')--')
        self.assertEqual(signal_bars(0), '---')
        self.assertEqual(signal_bars(None), '')

    def test_clock_faces(self):
        import time
        from monocell import clock
        th = Theme(Config())
        buf = Buffer(40, 16)
        cv = Canvas(buf)
        clock.draw_analog(cv, 0, 0, th, time.localtime())
        text = ''.join(''.join(row) for row in buf.ch)
        self.assertTrue(any(c in text for c in '#o.+|'))
        self.assertEqual(clock.face_size(118, 42), (51, 25))
        self.assertEqual(clock.face_size(30, 20), (27, 13))
        big = Buffer(60, 30)
        cv = Canvas(big)
        # a fixed 3:30:40, so no hand covers the 12 (at :00 one would)
        clock.draw_analog(cv, 0, 0, th, time.struct_time((2026, 1, 1, 3, 30, 40, 3, 1, 0)), 51, 25)
        text = ''.join(''.join(row) for row in big.ch)
        self.assertIn('12', text)
        self.assertIn('#', text)
        big = Buffer(32, 8)
        cv = Canvas(big)
        self.assertEqual(clock.draw_digital(cv, 0, 0, th, '12:34'), 29)
        accent = th.r['accent']
        self.assertTrue(any(bg_of(a) == accent for row in big.at for a in row))

    def test_nmcli_parsing(self):
        from monocell.apps.network import parse_networks, portal_address, terse_fields
        self.assertEqual(terse_fields('*:My Net:77:WPA2'), ['*', 'My Net', '77', 'WPA2'])
        self.assertEqual(terse_fields(r':a\:b:5:'), ['', 'a:b', '5', ''])
        text = ('*:Home:80:WPA2\n :Home:40:WPA2\n :Guest:55:\n :Weak:20:WPA2\n')
        nets = parse_networks(text)
        self.assertEqual([n['ssid'] for n in nets], ['Home', 'Guest', 'Weak'])
        self.assertEqual(nets[0]['signal'], 80)
        self.assertTrue(nets[0]['in_use'])
        self.assertEqual(nets[1]['security'], '')
        self.assertEqual(portal_address('WIFIonICE'), 'http://wifi.bahn.de/')
        self.assertEqual(portal_address('Hotel', '10.0.0.1'), 'http://10.0.0.1/')
        self.assertEqual(portal_address('Hotel'), 'http://neverssl.com/')

    def test_rain(self):
        from monocell.wm import Rain
        r = Rain(40, 12)
        for _ in range(8):
            r.step()
        self.assertTrue(all(d['x'] < 40 for d in r.drops))
        self.assertGreater(len(r.drops), 0)

    def test_wrap(self):
        self.assertEqual(util.wrap('hello world', 5), [(0, 5), (6, 11)])

    def test_fit_shortens(self):
        self.assertEqual(util.fit('abcdef', 4), 'abc~')


class TestFormula(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(evaluate('2+3*4'), 14)
        self.assertEqual(evaluate('(1+2)*3'), 9)
        self.assertEqual(evaluate('2^10'), 1024)
        self.assertEqual(evaluate('-2^2'), -4)
        self.assertEqual(evaluate('7%'), 0.07)

    def test_functions(self):
        self.assertEqual(evaluate('sqrt(16)'), 4)
        self.assertEqual(evaluate('SUM(1,2,3)'), 6)
        self.assertEqual(evaluate('ROUND(3.14159,2)'), 3.14)
        self.assertEqual(evaluate('10!'), 3628800)
        self.assertEqual(evaluate('IF(1<2,10,20)'), 10)
        self.assertEqual(evaluate('CONCAT("a","b")'), 'ab')

    def test_errors(self):
        with self.assertRaises(FormulaError):
            evaluate('1/0')
        with self.assertRaises(FormulaError):
            evaluate('nope(1)')
        with self.assertRaises(FormulaError):
            evaluate('1+')

    def test_ranges(self):
        cells = {(0, 0): '1', (1, 0): '2', (2, 0): '3'}

        def ref(rc):
            return float(cells.get(rc, 0))

        def rng(a, b):
            return [float(cells.get((r, c), 0)) for r in range(a[0], b[0] + 1) for c in range(a[1], b[1] + 1)]
        self.assertEqual(evaluate('SUM(A1:A3)+1', ref=ref, rng=rng), 7)

    def test_col_names(self):
        self.assertEqual(col_name(0), 'A')
        self.assertEqual(col_name(25), 'Z')
        self.assertEqual(col_name(26), 'AA')

    def test_format(self):
        self.assertEqual(fmt_number(3.0), '3')
        self.assertEqual(fmt_number(0.5), '0.5')


class TestVT(unittest.TestCase):
    def test_text_and_wrap(self):
        v = VTScreen(5, 3)
        v.feed('abcdefgh')
        self.assertEqual(''.join(v.lines[0][0]), 'abcde')
        self.assertEqual(''.join(v.lines[1][0]), 'fgh  ')
        self.assertEqual((v.cx, v.cy), (3, 1))

    def test_cursor_and_erase(self):
        v = VTScreen(10, 4)
        v.feed('hello\x1b[2;3HX\x1b[2;1H')
        self.assertEqual(''.join(v.lines[1][0]), '  X       ')
        v.feed('\x1b[2K')
        self.assertEqual(''.join(v.lines[1][0]), ' ' * 10)

    def test_colors(self):
        v = VTScreen(10, 2)
        v.feed('\x1b[31mA\x1b[0mB')
        self.assertEqual(v.lines[0][1][0] & 31, 1)
        self.assertEqual(v.lines[0][1][1] & 31, 16)

    def test_scrollback(self):
        v = VTScreen(5, 2)
        v.feed('a\r\nb\r\nc')
        self.assertEqual(len(v.history), 1)
        self.assertEqual(''.join(v.history[0][0]), 'a    ')

    def test_alt_screen(self):
        v = VTScreen(5, 2)
        v.feed('main\x1b[?1049hALT\x1b[?1049l')
        self.assertEqual(''.join(v.lines[0][0]), 'main ')

    def test_osc_palette(self):
        v = VTScreen(4, 2)
        v.feed('\x1b]4;1;rgb:ff/00/11\x1b\\')
        self.assertEqual(v.palette[1], (255, 0, 17))

    def test_linux_palette(self):
        v = VTScreen(4, 2)
        v.feed('\x1b]P2ff8a1c')
        self.assertEqual(v.palette[2], (255, 138, 28))

    def test_key_encoding(self):
        v = VTScreen(10, 2)
        self.assertEqual(v.encode_key('up'), '\x1b[A')
        self.assertEqual(v.encode_key('shift+up'), '\x1b[1;2A')
        self.assertEqual(v.encode_key('ctrl+c'), '\x03')
        self.assertEqual(v.encode_key('enter'), '\r')
        self.assertEqual(v.encode_key('f6'), '\x1b[17~')
        self.assertEqual(v.encode_key(None, 'x'), 'x')
        v.feed('\x1b[?1h')
        self.assertEqual(v.encode_key('up'), '\x1bOA')

    def test_resize(self):
        v = VTScreen(10, 3)
        v.feed('abc')
        v.resize(5, 2)
        self.assertEqual(''.join(v.lines[0][0]), 'abc  ')
        self.assertEqual(len(v.lines), 2)

    def test_256_and_rgb(self):
        self.assertEqual(xterm256_to16(196), 9)
        v = VTScreen(4, 2)
        v.feed('\x1b[38;5;196mX')
        self.assertEqual(v.lines[0][1][0] & 31, 9)

    def test_dec_graphics(self):
        v = VTScreen(4, 2)
        v.feed('\x1b(0lqk\x1b(B')
        self.assertEqual(''.join(v.lines[0][0][:3]), '+-+')


class TestInput(unittest.TestCase):
    def feed(self, s):
        p = InputParser()
        return p.feed(s)

    def test_chars(self):
        ev = self.feed('a')
        self.assertIsInstance(ev[0], Key)
        self.assertEqual((ev[0].key, ev[0].char), ('a', 'a'))

    def test_special_keys(self):
        self.assertEqual(self.feed('\x1b[A')[0].key, 'up')
        self.assertEqual(self.feed('\x1b[1;5D')[0].key, 'ctrl+left')
        self.assertEqual(self.feed('\x1b[3~')[0].key, 'delete')
        self.assertEqual(self.feed('\x7f')[0].key, 'backspace')
        self.assertEqual(self.feed('\x1bOP')[0].key, 'f1')
        self.assertEqual(self.feed('\x01')[0].key, 'ctrl+a')

    def test_alt(self):
        self.assertEqual(self.feed('\x1bx')[0].key, 'alt+x')
        self.assertEqual(self.feed('\x1b\x7f')[0].key, 'alt+backspace')

    def test_mouse_sgr(self):
        ev = self.feed('\x1b[<0;10;5M')[0]
        self.assertIsInstance(ev, Mouse)
        self.assertEqual((ev.kind, ev.x, ev.y), ('down', 9, 4))
        ev = self.feed('\x1b[<64;3;3M')[0]
        self.assertEqual(ev.kind, 'wheelup')

    def test_paste(self):
        ev = self.feed('\x1b[200~hello\r\nworld\x1b[201~')[0]
        self.assertIsInstance(ev, Paste)
        self.assertEqual(ev.text, 'hello\nworld')

    def test_partial_sequence(self):
        p = InputParser()
        self.assertEqual(p.feed('\x1b['), [])
        self.assertEqual([e.key for e in p.feed('A')], ['up'])

    def test_lone_escape(self):
        p = InputParser()
        p.feed('\x1b')
        self.assertTrue(p.pending_escape())
        self.assertEqual([e.key for e in p.flush()], ['esc'])


class TestScreen(unittest.TestCase):
    def test_canvas_clipping(self):
        b = Buffer(10, 3)
        cv = Canvas(b, 0, 0, 4, 2)
        cv.put(-2, 0, 'abcdef', A(1, 0))
        self.assertEqual(''.join(b.ch[0]), 'cdef      ')
        cv.put(3, 1, 'XYZ', A(2, 0))
        self.assertEqual(''.join(b.ch[1]), '   X      ')

    def test_fill_and_attr(self):
        b = Buffer(5, 2)
        cv = Canvas(b)
        cv.fill(1, 0, 3, 2, A(3, 4))
        self.assertEqual(b.at[1][1], 3 | 4 << 4)

    def test_sub_clip(self):
        b = Buffer(10, 5)
        cv = Canvas(b, 2, 2, 4, 2)
        cv.sub(1, 1, 10, 10).put(0, 0, 'ab', A(1, 1))
        self.assertEqual(''.join(b.ch[3]), '   ab     ')


class TestThemeAndImaging(unittest.TestCase):
    def test_palette_sizes(self):
        cfg = Config()
        self.assertEqual(len(cfg['palette']), 16)
        th = Theme(cfg)
        self.assertEqual(len(th.rgb), 16)

    def test_session_overrides(self):
        cfg = Config()
        cfg.user_set.clear()          # independent of the config of whoever runs the tests
        cfg.set_session({'background': 'matrix', 'image_mode': 'pixel'})
        self.assertEqual(cfg['background'], 'matrix')
        self.assertEqual(cfg.get('image_mode'), 'pixel')
        cfg['background'] = 'logo'
        self.assertEqual(cfg['background'], 'logo')
        self.assertNotIn('background', cfg.overrides)
        self.assertEqual(cfg.get('image_mode'), 'pixel')

    def test_nearest(self):
        th = Theme(Config())
        self.assertEqual(th.nearest(th.rgb[14]), 14)

    def test_fit_size_keeps_aspect(self):
        w, h = imaging.fit_size(1000, 500, 100, 50, 2.0)
        self.assertLessEqual(w, 100)
        self.assertLessEqual(h, 50)
        self.assertAlmostEqual(w / (h * 2.0), 1000 / 500, delta=0.1)

    def test_grey_render_only_grey_slots(self):
        cfg = Config()
        th = Theme(cfg)
        wm = type('W', (), {'theme': th, 'cfg': cfg,
                            'term': type('T', (), {'allowed_bg': list(range(16)), 'cell_aspect': 2.0})()})()
        rgb = bytes([200, 60, 40] * 64)
        rows = imaging.render(wm, rgb, 8, 8, 'grey', 'bayer4')
        for cs, ats in rows:
            for a in ats:
                self.assertIn(a & 15, imaging.GREYS)
                self.assertIn((a >> 4) & 15, imaging.GREYS)

    def test_palette_bg_restriction(self):
        cfg = Config()
        th = Theme(cfg)
        wm = type('W', (), {'theme': th, 'cfg': cfg,
                            'term': type('T', (), {'allowed_bg': [0, 8, 9], 'cell_aspect': 2.0})()})()
        rgb = bytes([10, 200, 100] * 100)
        rows = imaging.render(wm, rgb, 10, 10, 'blend', 'bayer4')
        for cs, ats in rows:
            for a in ats:
                self.assertIn((a >> 4) & 15, [0, 8, 9])


class TestLogo(unittest.TestCase):
    def test_measure(self):
        from monocell import logo
        self.assertEqual(logo.measure(), (96, 8))
        self.assertEqual(logo.measure_for(128)[:2], (96, 11))
        self.assertEqual(logo.measure_for(80)[0], 56)
        self.assertEqual(logo.measure_for(30)[2], 0)

    def test_draw_uses_solid_cells(self):
        from monocell import logo
        b = Buffer(128, 20)
        cv = Canvas(b)
        th = Theme(Config())
        w, h = logo.draw(cv, 0, 0, th)
        self.assertEqual(b.ch[0][0], ' ')
        self.assertIn(bg_of(b.at[0][0]), (th.idx('accent'), th.idx('accent2')))
        self.assertEqual((w, h), (96, 11))


class TestTextShot(unittest.TestCase):
    def make(self):
        from monocell import shot
        b = Buffer(6, 3)
        b.ch[0] = list('ab  cd')
        b.at[0] = [A(1, 8)] * 2 + [A(14, 0)] * 2 + [A(9, 0)] * 2
        b.ch[1] = list('<&>x  ')
        b.at[1] = [A(12, 9)] * 6
        b.ch[2] = list('      ')
        b.at[2] = [A(3, 3)] + [A(0, 8)] * 5
        pal = ['%02x%02x%02x' % (i * 16, i * 8, i * 4) for i in range(16)]
        return shot, b, pal

    def test_plain_markdown(self):
        import datetime
        shot, b, pal = self.make()
        rows = [''.join(r) for r in b.ch]
        md = shot.markdown_plain(rows, 'Ember', 'x61s', '1.0.0',
                                 datetime.datetime(2026, 10, 22, 1, 2, 3), 'x-colour.html')
        self.assertIn('```text', md)
        self.assertIn('ab  cd', md)
        self.assertIn('[x-colour.html](x-colour.html)', md)
        self.assertNotIn('\x1b', md)
        self.assertNotIn('<span', md)

    def test_html_colours(self):
        import datetime
        shot, b, pal = self.make()
        html = shot.html_document([''.join(r) for r in b.ch], [list(a) for a in b.at], pal,
                                  'Ember', 'x61s', '1.0.0', datetime.datetime(2026, 10, 22, 1, 2, 3))
        self.assertIn('<span style="color:#100804;background-color:#804020">ab</span>', html)
        self.assertIn('<span style="color:#904824;background-color:#000000">cd</span>', html)
        self.assertIn('&lt;&amp;&gt;x', html)
        self.assertNotIn('\x1b', html)
        self.assertIn('<pre', html)
        self.assertIn('15 #f0783c', html)

    def test_save_pair(self):
        import tempfile, os, datetime
        shot, b, pal = self.make()
        with tempfile.TemporaryDirectory() as d:
            md, colour, msize, hsize = shot.save(
                b, pal, 'Ember', 'x61s', '1.0.0', d,
                when=datetime.datetime(2026, 10, 22, 1, 2, 3))
            self.assertTrue(md.endswith('monocell-20261022-010203.md'))
            self.assertTrue(colour.endswith('monocell-20261022-010203-colour.html'))
            self.assertTrue(os.path.isfile(md) and os.path.isfile(colour))
            self.assertEqual(os.stat(md).st_mode & 0o777, 0o600)
            self.assertIn('[monocell-20261022-010203-colour.html]', open(md, encoding='utf-8').read())
            self.assertIn('background-color:#', open(colour, encoding='utf-8').read())
            self.assertGreater(msize, 50)
            self.assertGreater(hsize, 100)


class TestVTModifiers(unittest.TestCase):
    def test_state_bits(self):
        from monocell.term import vt_state_mods
        self.assertEqual(vt_state_mods(0), set())
        self.assertEqual(vt_state_mods(1 | 4), {'shift', 'ctrl'})
        self.assertEqual(vt_state_mods(16 | 128), {'shift', 'ctrl'})
        self.assertEqual(vt_state_mods(8), {'alt'})

    def test_augment(self):
        from monocell.term import augment_key
        self.assertEqual(augment_key('ctrl+v', {'ctrl', 'shift'}), 'ctrl+shift+v')
        self.assertEqual(augment_key('ctrl+c', {'ctrl'}), 'ctrl+c')
        self.assertEqual(augment_key('insert', {'shift'}), 'shift+insert')
        self.assertEqual(augment_key('insert', {'ctrl'}), 'ctrl+insert')
        self.assertEqual(augment_key('up', {'shift', 'ctrl'}), 'ctrl+shift+up')
        self.assertEqual(augment_key('alt+up', {'shift'}), 'alt+shift+up')
        self.assertEqual(augment_key('enter', {'shift'}), 'enter')
        self.assertEqual(augment_key('f5', {'ctrl'}), 'f5')


class TestKDEConnect(unittest.TestCase):
    def test_parse_devices(self):
        from monocell.apps.kdeconnect import parse_devices
        out = ("- Pixel 9: abc_123 on 192.168.1.8 via LAN (paired and reachable)\n"
               "- laptop: def456 (paired)\n"
               "- unknown: xyz (unpaired)\n"
               "4 devices found\n")
        d = parse_devices(out)
        self.assertEqual(len(d), 3)
        self.assertEqual(d[0]['name'], 'Pixel 9')
        self.assertEqual(d[0]['id'], 'abc_123')
        self.assertTrue(d[0]['reachable'])
        self.assertTrue(d[1]['paired'])
        self.assertFalse(d[1]['reachable'])
        self.assertFalse(d[2]['paired'])


class TestWebParse(unittest.TestCase):
    def test_layout_table_rows(self):
        from monocell import web
        html = ('<table><tr><td class="title"><span>1.</span></td>'
                '<td><a href="/x">Story</a></td></tr>'
                '<tr><td></td><td>12 points</td></tr></table>')
        doc = web.parse_html(html, 'https://example.org/', reader=False)
        texts = [''.join(s.get('x', '') for s in b['s']) for b in doc['blocks'] if b['t'] == 'p']
        self.assertTrue(any('1.' in t and 'Story' in t for t in texts))
        self.assertTrue(any('12 points' in t for t in texts))

    def test_data_table(self):
        from monocell import web
        html = ('<table class="wikitable"><tr><th>Name</th><th>Value</th></tr>'
                '<tr><td>Alpha</td><td>1</td></tr></table>')
        doc = web.parse_html(html, 'https://example.org/', reader=False)
        tables = [b for b in doc['blocks'] if b['t'] == 'table']
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]['rows'][0], ['Name', 'Value'])


class TestICal(unittest.TestCase):
    def test_weekly_and_all_day(self):
        from monocell import gcal
        ics = ('BEGIN:VCALENDAR\n'
               'BEGIN:VEVENT\nUID:1\nSUMMARY:Weekly Sync\n'
               'DTSTART;TZID=Europe/Berlin:20261021T180000\n'
               'DTEND;TZID=Europe/Berlin:20261021T190000\n'
               'RRULE:FREQ=WEEKLY;COUNT=3\nEND:VEVENT\n'
               'BEGIN:VEVENT\nUID:2\nSUMMARY:Workshop\n'
               'DTSTART;VALUE=DATE:20261024\nDTEND;VALUE=DATE:20261025\nEND:VEVENT\n'
               'END:VCALENDAR')
        import datetime as dt
        ev = gcal.parse_ics(ics, dt.datetime(2026, 1, 1), dt.datetime(2027, 1, 1))
        weekly = [e for e in ev if e['summary'] == 'Weekly Sync']
        self.assertEqual(len(weekly), 3)
        self.assertEqual(weekly[0]['time'], '18:00')
        day = [e for e in ev if e['summary'] == 'Workshop'][0]
        self.assertTrue(day['all_day'])
        self.assertEqual(day['date'], '2026-10-24')

    def test_utc_conversion(self):
        from monocell import gcal
        ics = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:3\nSUMMARY:Zulu\n'
               'DTSTART:20261019T080000Z\nDTEND:20261019T090000Z\nEND:VEVENT\nEND:VCALENDAR')
        import datetime as dt
        ev = gcal.parse_ics(ics, dt.datetime(2026, 1, 1), dt.datetime(2027, 1, 1))
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]['start'].startswith('2026-10-19T'))


class TestTailscale(unittest.TestCase):
    def test_parse_status(self):
        from monocell.apps.tailscale import parse_status, first_ip
        data = {
            'BackendState': 'Running',
            'Self': {'HostName': 'x61s', 'Online': True,
                     'TailscaleIPs': ['100.100.218.41', 'fd7a:115c::1']},
            'Peer': {
                'a': {'HostName': 'raspberrypi', 'Online': True, 'OS': 'linux', 'ExitNode': True,
                      'TailscaleIPs': ['100.65.133.100']},
                'b': {'HostName': 'phone', 'Online': False, 'OS': 'android',
                      'TailscaleIPs': ['100.125.27.43']},
            },
            'CurrentTailnet': {'Name': 'alex@'},
            'ExitNodeStatus': {'ID': 'a'},
        }
        st = parse_status(data)
        self.assertEqual(st['state'], 'Running')
        self.assertEqual(st['tailnet'], 'alex@')
        self.assertEqual(st['exit'], 'a')
        self.assertEqual([p['HostName'] for p in st['peers']], ['raspberrypi', 'phone'])
        self.assertEqual(first_ip(st['ips'], True), '100.100.218.41')
        self.assertEqual(first_ip(st['ips'], False), 'fd7a:115c::1')


class TestScrollbars(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from monocell.theme import Config, Theme
        self.th = Theme(Config())

    def test_list_scrollbar_click_and_drag(self):
        from monocell.screen import Buffer, Canvas
        from monocell.term import Mouse
        from monocell.widgets import ListView
        lv = ListView(list(range(100)))
        cv = Canvas(Buffer(40, 12))
        lv.draw(cv, 0, 0, 20, 10, lambda *a: None, self.th)
        self.assertIsNotNone(lv.sb)
        self.assertEqual(lv.sb.x, 19)
        lv.mouse(Mouse('down', 19, 8), 8)
        self.assertGreater(lv.top, 0)
        top_before = lv.top
        lv.mouse(Mouse('down', 19, lv.sb.y + lv.sb.ty), 0)
        lv.mouse(Mouse('drag', 19, 9), 9)
        self.assertGreater(lv.top, top_before)
        lv.mouse(Mouse('up', 19, 9), 9)

    def test_textarea_scrollbar_drag(self):
        from monocell.screen import Buffer, Canvas
        from monocell.term import Mouse
        from monocell.textarea import TextArea
        ta = TextArea('\n'.join('line %d' % i for i in range(300)))
        ta.scrollbar = True
        cv = Canvas(Buffer(40, 20))
        ta.draw(cv, 0, 0, 40, 20, self.th, True)
        self.assertIsNotNone(ta.sb)
        g = ta.sb
        ta.mouse(Mouse('down', g.x, g.y + g.ty), g.x, g.y + g.ty)
        ta.mouse(Mouse('drag', g.x, 19), g.x, 19)
        self.assertGreater(ta.top, 100)
        ta.mouse(Mouse('up', g.x, 19), g.x, 19)
        ta.mouse(Mouse('down', g.x, 1), g.x, 1)
        self.assertLess(ta.top, 300)


class TestTermColors(unittest.TestCase):
    def make(self, mode):
        r, w = os.pipe()
        t = Terminal(Theme(Config()), mode, fd_in=r, fd_out=w)
        return t

    def test_truecolor_sgr(self):
        t = self.make('truecolor')
        s = t.sgr(A(14, 9, 1))
        self.assertIn('38;2;', s)
        self.assertIn('48;2;', s)
        self.assertIn(';1', s)

    def test_ansi_mapping(self):
        t = self.make('ansi')
        self.assertIn('91', t.sgr(A(9, 0)))
        self.assertIn('100', t.sgr(A(15, 8)))

    def test_linux_backgrounds_limited(self):
        t = self.make('linux')
        t.apply_palette()
        allowed = t.allowed_bg
        self.assertLessEqual(len(allowed), 8)
        for bg in range(16):
            self.assertIn(t.bgmap[bg], allowed)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestThemeLayout(unittest.TestCase):
    """layout: which palette entries may be a background (the first 8) and which are text."""

    def legacy_mapping(self, roles):
        """The mapping term.apply_palette used before layout existed."""
        want = []
        for role in ('desk', 'win', 'title', 'sel', 'accent', 'shadow', 'field', 'bar',
                     'menu', 'raised'):
            i = roles[role]
            if i not in want:
                want.append(i)
        for i in (0, 7, 12, 4, 1, 2, 3, 6, 5, 13, 15, 8, 9, 10, 11, 14):
            if len(want) >= 8:
                break
            if i not in want:
                want.append(i)
        want = want[:8]
        return want + [i for i in range(16) if i not in want]

    def test_presets_look_the_same_as_before(self):
        for name in theme.PRESETS:
            lay = theme.derive_layout(theme.ROLE_DEFAULTS)
            self.assertEqual(lay, self.legacy_mapping(theme.LEGACY_DEFAULTS), name)

    def test_layout_is_a_permutation(self):
        for roles in (theme.ROLE_DEFAULTS,
                      dict(theme.ROLE_DEFAULTS, win=0, title=0, sel=1),
                      dict(theme.ROLE_DEFAULTS, **{r: 5 for r in theme.SURFACE_ROLES}),
                      {}):
            lay = theme.derive_layout(roles)
            self.assertTrue(theme.valid_layout(lay), lay)
            self.assertEqual(sorted(lay), list(range(16)))

    def test_normal_text_is_not_bold_on_a_vt(self):
        # a VT renders fg >= 8 as bold, so text and dim must land in the low half
        lay = theme.derive_layout(theme.ROLE_DEFAULTS)
        self.assertLess(lay.index(theme.ROLE_DEFAULTS['text']), 8)
        self.assertLess(lay.index(theme.ROLE_DEFAULTS['dim']), 8)

    def test_config_derives_layout_when_missing(self):
        cfg = Config()
        self.assertTrue(theme.valid_layout(cfg['layout']))
        self.assertEqual(cfg['layout'], theme.derive_layout(cfg['roles']))

    def test_text_colour_is_keyed_by_element(self):
        roles = dict(theme.ROLE_DEFAULTS)
        self.assertEqual(theme.clean_on({'win': '12'}, roles), {'win': 12})
        # version 2 keyed the surface slot instead; that is migrated to the role
        self.assertEqual(theme.clean_on({'9': 13}, roles), {'win': 13})
        self.assertEqual(theme.clean_on({'bogus': 12, 'win': 99}, roles), {})

    def test_term_maps_backgrounds_with_layout(self):
        r, w = os.pipe()
        cfg = Config()
        th = Theme(cfg)
        t = Terminal(th, 'linux', fd_in=r, fd_out=w)
        t.apply_palette()
        self.assertEqual(t.allowed_bg, th.layout[:8])
        for logical in range(16):
            self.assertEqual(th.layout[t.perm[logical]], logical)
        for bg in range(16):
            self.assertIn(t.bgmap[bg], t.allowed_bg)

    def test_custom_layout_reorders_the_vt_backgrounds(self):
        r, w = os.pipe()
        cfg = Config()
        cfg['layout'] = [3, 2, 1, 0, 7, 6, 5, 4, 15, 14, 13, 12, 11, 10, 9, 8]
        th = Theme(cfg)
        t = Terminal(th, 'linux', fd_in=r, fd_out=w)
        t.apply_palette()
        self.assertEqual(t.allowed_bg, [3, 2, 1, 0, 7, 6, 5, 4])
        self.assertEqual(t.perm[3], 0)

    def test_text_colour_per_element(self):
        cfg = Config()
        cfg['on'] = {'win': cfg['roles']['dim']}
        th = Theme(cfg)
        self.assertEqual(th.c('text', 'win'), th.c('dim', 'win'))
        self.assertNotEqual(th.c('text', 'menu'), th.c('dim', 'menu'))
        # an element without its own text colour keeps the global one
        self.assertEqual(th.c('text', 'menu'), A(th.r['text'], th.r['menu']))
        # the element colour also replaces bright text on it (title bars, selections)
        self.assertEqual(th.c('bright', 'win'), th.c('dim', 'win'))
        self.assertEqual(th.c('accent', 'win'), A(th.r['accent'], th.r['win']))


class TestThemeFiles(unittest.TestCase):
    """Saved themes are plain JSON files: palette + role map."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = theme.THEME_DIR
        theme.THEME_DIR = self.dir

    def tearDown(self):
        theme.THEME_DIR = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_dir_is_empty(self):
        self.assertEqual(theme.saved_themes(), {})

    def test_save_load_delete(self):
        pal = list(theme.PRESETS['Ember'])
        roles = dict(theme.ROLE_DEFAULTS, accent=3)
        self.assertEqual(theme.save_theme('My Theme', pal, roles), 'My Theme')
        saved = theme.saved_themes()
        self.assertEqual(list(saved), ['My Theme'])
        self.assertEqual(saved['My Theme']['palette'], pal)
        self.assertEqual(saved['My Theme']['roles']['accent'], 3)
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'my-theme.json')))
        self.assertTrue(theme.delete_theme('My Theme'))
        self.assertEqual(theme.saved_themes(), {})
        self.assertFalse(theme.delete_theme('My Theme'))

    def test_v3_roundtrip_keeps_layout_and_text_colours(self):
        lay = theme.derive_layout(theme.ROLE_DEFAULTS)
        on = {'win': 12, 'sel': 7}
        theme.save_theme('With layout', theme.PRESETS['Ember'], theme.ROLE_DEFAULTS,
                         layout=lay, on=on)
        data = theme.saved_themes()['With layout']
        self.assertEqual(data['layout'], lay)
        self.assertEqual(data['on'], on)

    def test_v1_file_gets_a_layout(self):
        with open(os.path.join(self.dir, 'old.json'), 'w') as f:
            json.dump({'version': 1, 'name': 'Old', 'palette': list(theme.PRESETS['Ember']),
                       'roles': {'accent': 3}}, f)
        data = theme.saved_themes()['Old']
        self.assertEqual(data['on'], {'title_off': 7})      # the old dim inactive title
        self.assertTrue(theme.valid_layout(data['layout']))
        self.assertEqual(data['layout'], theme.derive_layout(
            dict(theme.ROLE_DEFAULTS, accent=3)))

    def test_layout_in_a_file_is_honoured(self):
        lay = [3, 2, 1, 0, 7, 6, 5, 4, 15, 14, 13, 12, 11, 10, 9, 8]
        with open(os.path.join(self.dir, 'flip.json'), 'w') as f:
            json.dump({'version': 2, 'name': 'Flip', 'palette': list(theme.PRESETS['Ember']),
                       'layout': lay, 'roles': {}, 'on': {'8': '12'}}, f)
        data = theme.saved_themes()['Flip']
        self.assertEqual(data['layout'], lay)
        self.assertEqual(data['on'], {'desk': 12, 'title_off': 7})   # v2 slot key -> element

    def test_broken_layout_falls_back(self):
        with open(os.path.join(self.dir, 'bad.json'), 'w') as f:
            json.dump({'name': 'Bad', 'palette': list(theme.PRESETS['Ember']),
                       'layout': [1, 1, 2], 'roles': {}}, f)
        self.assertTrue(theme.valid_layout(theme.saved_themes()['Bad']['layout']))

    def test_overwrite_same_name(self):
        theme.save_theme('T', theme.PRESETS['Ember'], theme.ROLE_DEFAULTS)
        theme.save_theme('T', theme.PRESETS['Nord'], theme.ROLE_DEFAULTS)
        self.assertEqual(theme.saved_themes()['T']['palette'], theme.PRESETS['Nord'])

    def test_colours_normalised(self):
        pal = ['FFFFFF'] * 16
        theme.save_theme('caps', pal, {})
        self.assertEqual(theme.saved_themes()['caps']['palette'], ['ffffff'] * 16)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            theme.save_theme('  ', theme.PRESETS['Ember'], {})
        with self.assertRaises(ValueError):
            theme.save_theme('half', theme.PRESETS['Ember'][:8], {})
        with self.assertRaises(ValueError):
            theme.save_theme('bad hex', ['zzzzzz'] * 16, {})

    def test_slug_keeps_dots_out_of_paths(self):
        self.assertEqual(theme.theme_slug('../../etc/passwd'), 'etc-passwd')
        self.assertEqual(theme.theme_slug('  Ember  '), 'ember')
        self.assertEqual(theme.theme_slug('Müller & Söhne!'), 'm-ller-s-hne')
        self.assertEqual(theme.theme_slug('...'), 'theme')

    def test_broken_files_are_skipped(self):
        for fn, text in (('broken.json', '{nope'),
                         ('short.json', '{"palette": ["000000"]}'),
                         ('weird.json', '{"palette": [], "roles": {"win": "x"}}'),
                         ('notjson.txt', 'hello')):
            with open(os.path.join(self.dir, fn), 'w') as f:
                f.write(text)
        self.assertEqual(theme.saved_themes(), {})

    def test_unknown_roles_ignored(self):
        theme.save_theme('T', theme.PRESETS['Ember'], {'win': 3, 'nonsense': 9, 'desk': 99})
        roles = theme.saved_themes()['T']['roles']
        self.assertEqual(roles['win'], 3)
        self.assertNotIn('nonsense', roles)
        self.assertEqual(roles['desk'], theme.ROLE_DEFAULTS['desk'])


class TestAppearanceRoles(unittest.TestCase):
    """Version 4: the focused window has named colours instead of a slot shift."""

    @staticmethod
    def old_lift(roles):
        return {roles['win']: roles['title'], roles['title']: roles['sel']}

    def check_same_look(self, legacy):
        roles, on = theme.migrate_roles(legacy, {})
        full = dict(theme.LEGACY_DEFAULTS, **legacy)
        old = self.old_lift(full)
        cfg = Config()
        cfg['roles'], cfg['on'] = roles, on
        th = Theme(cfg)
        new = th.lift()
        # body and buttons follow the focus; other surfaces (a menu drawn inside a
        # window) used to be dragged along by slot and now keep their own colour
        for role in ('win', 'raised', 'field', 'sel'):
            bg = full[role]
            self.assertEqual(new.get(bg, bg), old.get(bg, bg), (legacy, role))
        # title bars: active was 'title' shifted once more, inactive was the window
        self.assertEqual(roles['title_on'], old.get(full['title'], full['title']))
        self.assertEqual(roles['title_off'], full['win'])
        self.assertEqual(on['title_off'], full['dim'])

    def test_migration_keeps_the_look(self):
        self.check_same_look({})
        self.check_same_look({'raised': 9})
        self.check_same_look({'raised': 11, 'sel': 12, 'title': 3})
        self.check_same_look({'win': 2, 'title': 2})

    def test_v4_roles_are_kept(self):
        roles, on = theme.migrate_roles(dict(theme.ROLE_DEFAULTS, win_on=4, title_off=5),
                                        {'win': 13})
        self.assertEqual((roles['win_on'], roles['title_off']), (4, 5))
        self.assertEqual(on, {'win': 13})          # no migration defaults added

    def test_v3_title_text_moves_to_the_active_title(self):
        _, on = theme.migrate_roles({}, {'title': 13})
        self.assertEqual(on['title_on'], 13)

    def test_assign_surface_inside_the_background_row(self):
        lay = theme.derive_layout(theme.ROLE_DEFAULTS)
        roles, lay2 = theme.assign_surface(theme.ROLE_DEFAULTS, lay, 'desk', 0)
        self.assertEqual(roles['desk'], 0)
        self.assertEqual(lay2, lay)

    def test_assign_surface_swaps_a_text_colour_in(self):
        lay = theme.derive_layout(theme.ROLE_DEFAULTS)
        self.assertNotIn(1, lay[:8])
        roles, lay2 = theme.assign_surface(theme.ROLE_DEFAULTS, lay, 'menu', 1)
        self.assertIn(1, lay2[:8])
        self.assertTrue(theme.valid_layout(lay2))
        # every surface still has a VT background, and nothing but the swap moved
        for r in theme.SURFACE_ROLES:
            self.assertIn(roles[r], lay2[:8], r)
        self.assertEqual(sum(a != b for a, b in zip(lay, lay2)), 2)

    def test_assign_surface_refuses_a_ninth_background(self):
        roles = dict(theme.ROLE_DEFAULTS)
        for role, slot in zip(('desk', 'win', 'win_on', 'title_on', 'raised', 'sel',
                               'accent', 'shadow'), (8, 9, 10, 11, 7, 12, 14, 0)):
            roles[role] = slot
        for role in ('title_off', 'raised_on', 'field', 'bar', 'menu'):
            roles[role] = 8
        lay = [8, 9, 10, 11, 7, 12, 14, 0] + [i for i in range(16) if i not in
                                              (8, 9, 10, 11, 7, 12, 14, 0)]
        with self.assertRaises(ValueError):
            theme.assign_surface(roles, lay, 'menu', 1)
        # ... unless the element frees its own background by moving away
        r3 = dict(roles, title_off=9, raised_on=9, field=9, menu=9, bar=9)
        roles2, lay2 = theme.assign_surface(r3, lay, 'desk', 1)
        self.assertIn(1, lay2[:8])
        self.assertNotIn(8, lay2[:8])

    def test_identical_focus_colours_mean_no_lift(self):
        cfg = Config()
        roles = dict(cfg['roles'])
        roles.update(win_on=roles['win'], raised_on=roles['raised'])
        cfg['roles'] = roles
        self.assertIsNone(Theme(cfg).lift_fn())


class TestAppearanceEditor(unittest.TestCase):
    """The Appearance page: items, preview zones, assigning colours."""

    def make(self):
        from monocell.apps import appearance

        class FakeWM:
            def __init__(self):
                self.cfg = Config()
                # independent of whoever runs the tests: start from the Ember defaults
                self.cfg['roles'] = dict(theme.ROLE_DEFAULTS)
                self.cfg['on'] = dict(theme.DEFAULT_ON)
                self.cfg['palette'] = list(theme.PRESETS['Ember'])
                self.cfg['layout'] = theme.derive_layout(theme.ROLE_DEFAULTS)
                self.cfg['preset'] = 'Custom'
                self.cfg.save = lambda: None
                self.theme = Theme(self.cfg)
                self.toasts = []
                self.menus = []

            def toast(self, msg, col=''):
                self.toasts.append(msg)

            def menu(self, items, x, y, **kw):
                self.menus.append(dict(title=kw.get('title'), items=items))

            def apply_theme(self):
                self.theme.reload()

            def load_wallpaper(self):
                pass

        class Ed(appearance.AppearanceMixin):
            def __init__(self, wm):
                self.wm, self.cfg, self.th = wm, wm.cfg, wm.theme
                self.saved, self.edit, self.zones = {}, None, []

                class Win:
                    x = y = 0
                self.win = Win()
                self.body_x = 0
                self.init_appearance()

            def apply(self):
                self.wm.apply_theme()
        return Ed(FakeWM())

    def test_every_item_has_a_colour(self):
        from monocell.apps.appearance import ITEMS
        ed = self.make()
        for it in ITEMS:
            if it:
                ed.select_item(it[0])
                self.assertIsNotNone(ed.pot, it[0])

    def test_text_target_writes_the_element_colour(self):
        ed = self.make()
        ed.select_item('menu', 'fg')
        ed.assign(13)
        self.assertEqual(ed.th.on['menu'], 13)
        ed.assign(None)                     # back to the global text colour
        self.assertNotIn('menu', ed.th.on)
        self.assertEqual(ed.item_text('menu'), (ed.th.r['text'], True))

    def test_background_target_and_users(self):
        ed = self.make()
        ed.select_item('desk')
        self.assertEqual(ed.target, 'bg')
        ed.assign(ed.th.r['shadow'])
        self.assertEqual(ed.th.r['desk'], ed.th.r['shadow'])
        users = ed.pot_users(ed.pot)
        self.assertIn('Desktop', users)
        self.assertIn('Shadow', users)

    def test_new_colour_uses_a_free_pot(self):
        ed = self.make()
        # merge the logo highlight into the highlight, which frees its entry
        roles = dict(ed.cfg['roles'])
        roles['accent2'] = roles['accent']
        ed.cfg['roles'] = roles
        ed.apply()
        self.assertIn(15, ed.free_pots())
        ed.select_item('title_on', 'bg')
        shared = ed.th.r['title_on']
        others = {r for r, v in ed.th.r.items() if v == shared and r != 'title_on'}
        ed.separate()
        self.assertNotEqual(ed.th.r['title_on'], shared)
        for r in others:                       # the old colour stays with them
            self.assertEqual(ed.th.r[r], shared, r)
        self.assertEqual(ed.cfg['palette'][ed.th.r['title_on']],
                         ed.cfg['palette'][shared])      # same look, own entry
        self.assertIn(ed.th.r['title_on'], ed.th.layout[:8])

    def test_new_colour_offers_a_pot_when_none_is_free(self):
        ed = self.make()
        self.assertEqual(ed.free_pots(), [])
        ed.select_item('title_on', 'bg')
        ed.separate()                          # opens the "free a colour" menu
        self.assertTrue(ed.wm.menus)
        items = ed.wm.menus[-1]['items']
        # a background can only take one of the 8 background entries, minus its own
        self.assertEqual(len(items), 7)
        # whose loss shows least comes first: the shadow, body text last
        self.assertIn('Shadow', items[0].label)
        self.assertTrue(items[-1].label.endswith('Text') or 'Dim text' in items[-1].label)
        # take the entry of the logo highlight over
        before = ed.th.r['title_on']
        taken = ed.th.r['accent2']
        ed.take_pot(taken, before)
        self.assertEqual(ed.th.r['title_on'], taken)
        self.assertEqual(ed.cfg['palette'][taken], ed.cfg['palette'][before])
        self.assertNotEqual(ed.th.r['accent2'], taken)
        for r in theme.SURFACE_ROLES:          # every surface still has a VT background
            self.assertIn(ed.th.r[r], ed.th.layout[:8], r)
        # nothing but the title bar paints that entry any more
        self.assertEqual({r for r, v in ed.th.r.items() if v == taken}, {'title_on'})

    def test_rgb_edits_the_pot_of_the_target(self):
        ed = self.make()
        ed.select_item('title_on')
        slot = ed.th.r['title_on']
        ed.set_hex('#123456')
        self.assertEqual(ed.cfg['palette'][slot], '123456')

    def test_preview_covers_all_items(self):
        from monocell.apps.appearance import ITEM_KEYS
        from monocell.wm import WM
        ed = self.make()
        ed.cfg['shadows'] = True              # the shadow is only drawn when enabled
        for name in ('draw_window', 'win_buttons', 'shade'):
            setattr(type(ed.wm), name, getattr(WM, name))
        ed.wm.hover_part = None
        ed.wm.w, ed.wm.h = 200, 60
        ed.section, ed.body_x, ed.pv_rect = 'colors', 0, (0, 0, 72, 16)
        ed.zone_off = (0, 0)

        def zone(x, y, w, h, key):
            ox, oy = ed.zone_off
            ed.zones.append((x + ox, y + oy, w, h, key))
        ed.zone = zone
        cv = Canvas(Buffer(80, 20))
        ed.post_draw(cv, True)
        seen = {k[1] for *_, k in ed.zones if k[0] == 'pv'}
        self.assertEqual(set(ITEM_KEYS) - seen, set())
