"""Writer Markdown dialect: inline parser, blocks, page renderer, DOCX export."""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import writer_doc  # noqa: E402
from monocell.theme import DEFAULT_ON, PRESETS, ROLE_DEFAULTS, Theme  # noqa: E402


class FakeWM:
    def __init__(self, mode='palette'):
        self.theme = Theme({'palette': PRESETS['Ember'], 'roles': dict(ROLE_DEFAULTS),
                           'layout': [], 'on': dict(DEFAULT_ON), 'preset': 'Ember'})
        self.cfg = {'image_mode': 'ascii', 'dither': 'bayer4', 'ascii_charset': 'full',
                    'cell_aspect': 0}
        self.term = types.SimpleNamespace(allowed_bg=list(range(8)), cell_aspect=2.0,
                                          mode=mode)


class TestInline(unittest.TestCase):
    def test_underlines_and_size(self):
        spans = writer_doc.parse_inline('a <u>b</u> <u2>c</u2> <size3>D</size3> **e**')
        self.assertEqual(writer_doc.plain_text(spans), 'a b c D e')
        self.assertEqual([s.ul for s in spans], [0, 1, 0, 2, 0, 0, 0, 0])
        self.assertEqual([s.size for s in spans if s.text.strip() == 'D'][0], 3)
        self.assertTrue(spans[-1].bold)

    def test_markdown_styles(self):
        spans = writer_doc.parse_inline('**fett** *kursiv* `code` [Link](https://example.org)')
        self.assertTrue(any(s.bold for s in spans))
        self.assertTrue(any(s.italic for s in spans))
        self.assertTrue(any(s.code for s in spans))
        link = [s for s in spans if s.link][0]
        self.assertEqual(link.url, 'https://example.org')

    def test_underscore_inside_word_is_not_italic(self):
        spans = writer_doc.parse_inline('datei_name_bleibt')
        self.assertFalse(any(s.italic for s in spans))

    def test_backslash_escapes(self):
        spans = writer_doc.parse_inline(r'2\*3\*4 \<u> C:\Users')
        self.assertEqual(writer_doc.plain_text(spans), r'2*3*4 <u> C:\Users')
        self.assertFalse(any(s.italic or s.ul for s in spans))


class TestBlocks(unittest.TestCase):
    def test_kinds(self):
        text = ('# Titel\n\nText\n- Punkt\n\n![Alt](bild.png)\n\n'
                '```\ncode\n```\n---\n> Zitat\n')
        kinds = [b['kind'] for b in writer_doc.parse_blocks(text)]
        for kind in ('heading', 'para', 'list', 'image', 'code', 'rule', 'quote'):
            self.assertIn(kind, kinds)

    def test_image_with_float_attributes(self):
        m = writer_doc.IMAGE_RE.match('![Sonne](a b.png){x=3 w=20 wrap=square}')
        self.assertEqual(m.group(2), 'a b.png')
        self.assertEqual(m.group(3), 'x=3 w=20 wrap=square')

    def test_nested_inline(self):
        b = writer_doc.parse_blocks('# <u>Titel</u>\n')[0]
        self.assertEqual(b['level'], 1)
        self.assertEqual(writer_doc.plain_text(b['spans']), 'Titel')


class TestRenderer(unittest.TestCase):
    def setUp(self):
        self.wm = FakeWM()
        self.r = writer_doc.Renderer(self.wm, 40)

    def test_underline_rows_use_dash_and_equals(self):
        rows = self.r.render('Hallo <u>Welt</u> und <u2>doppelt</u2>.\nDanach\n')
        text = [chars for chars, _ in rows]
        i = next(i for i, line in enumerate(text) if line.startswith('Hallo Welt'))
        self.assertEqual(text[i + 1].rstrip(), '      ----     =======')
        self.assertTrue(text[i + 2].startswith('Danach'))   # next line moved down

    def test_big_heading_is_figlet_ascii(self):
        rows = self.r.render('# AB\n')
        self.assertGreaterEqual(len(rows), 6)
        self.assertTrue(all(ord(c) < 128 for chars, _ in rows for c in chars))
        self.assertTrue(any('/' in chars or '|' in chars for chars, _ in rows))

    def test_empty_document(self):
        self.assertEqual(self.r.render('\n'), [])

    def test_image(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.png')
            Image.new('RGB', (8, 4), (255, 0, 0)).save(p)
            r = writer_doc.Renderer(self.wm, 40, d)
            rows = r.render('![ein Bild](x.png)\n')
            self.assertGreater(len(rows), 3)
            self.assertFalse(any('missing' in chars for chars, _ in rows))

    def test_missing_image(self):
        rows = self.r.render('![weg](missing.png)\n')
        self.assertTrue(any('missing' in chars for chars, _ in rows))


class TestDocxExport(unittest.TestCase):
    def test_tags_and_image_placeholder(self):
        import zipfile
        from monocell.apps.writer import write_docx
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.docx')
            write_docx(p, '# Titel\n\nHallo <u>Welt</u> <u2>zwei</u2> '
                          '<size3>gross</size3>\n![Alt](bild.png){x=2 w=20 wrap=square}\n')
            with zipfile.ZipFile(p) as z:
                doc = z.read('word/document.xml').decode('utf-8')
        self.assertIn('<w:u w:val="single"/>', doc)
        self.assertIn('<w:u w:val="double"/>', doc)
        self.assertIn('<w:sz w:val="64"/>', doc)
        self.assertIn('[Bild: Alt]', doc)
        self.assertNotIn('<u>', doc)
        self.assertNotIn('wrap=', doc)


if __name__ == '__main__':
    unittest.main()
