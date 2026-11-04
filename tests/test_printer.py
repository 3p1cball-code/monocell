"""IBM Proprinter output: PPDS text, emphasis, code page and form feeds."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import printer  # noqa: E402
from monocell.screen import A, BOLD  # noqa: E402


class TestEncode(unittest.TestCase):
    def test_header_and_line_end(self):
        data = printer.encode([('AB', [0, 0])])
        self.assertTrue(data.startswith(b'\x1b@'))
        self.assertIn(b'\x12', data)
        self.assertIn(b'\x1bA\x0c', data)
        self.assertIn(b'\x1bC\x42', data)
        self.assertIn(b'AB\r\n', data)
        self.assertTrue(data.endswith(b'\f'))

    def test_emphasis(self):
        attr = A(0, 0, BOLD)
        data = printer.encode([('AB', [attr, attr])])
        self.assertIn(b'\x1bEAB\x1bF', data)

    def test_cp437_umlauts(self):
        data = printer.encode([('äöüß', [0, 0, 0, 0])])
        self.assertIn(b'\x84\x94\x81\xe1', data)

    @staticmethod
    def body(data):
        return data.split(b'\x1b2', 1)[1]

    def test_page_breaks(self):
        one = self.body(printer.encode([('x', [0])] * 66, page_lines=66))
        self.assertEqual(one.count(b'\f'), 1)
        two = self.body(printer.encode([('x', [0])] * 67, page_lines=66))
        self.assertEqual(two.count(b'\f'), 2)

    def test_trailing_spaces_are_kept_out(self):
        data = printer.encode([('AB   ', [0, 0, 0, 0, 0])])
        self.assertIn(b'AB   \r\n', data)


class TestSend(unittest.TestCase):
    def test_device_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'print.prn')
            printer.send(b'hello', p)
            with open(p, 'rb') as f:
                self.assertEqual(f.read(), b'hello')

    def test_empty_target(self):
        with self.assertRaises(ValueError):
            printer.send(b'x', '')


if __name__ == '__main__':
    unittest.main()
