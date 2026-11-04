"""Login screen (/etc/issue) generator."""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import make_issue  # noqa: E402
from monocell.theme import Config, Theme  # noqa: E402


class TestIssue(unittest.TestCase):
    def setUp(self):
        old = Config.path
        Config.path = os.path.join(tempfile.mkdtemp(), 'none.json')
        self.addCleanup(setattr, Config, 'path', old)
        self.th = Theme(Config())

    def test_palette_clear_and_mark(self):
        text = make_issue.build(self.th, 113, 48)
        self.assertEqual(len(re.findall(r'\x1b\]P[0-9A-F][0-9a-f]{6}', text)), 16)
        self.assertIn('\x1b[H\x1b[2J', text)
        # the wordmark is coloured cells: face, lit top and extrusion backgrounds
        for slot in (make_issue.FACE, make_issue.TOP, make_issue.DEEP):
            self.assertIn('[0;%d;%dm' % (30 + make_issue.TEXT, 40 + slot), text)

    def test_agetty_escapes_and_no_stray_backslashes(self):
        text = make_issue.build(self.th, 113, 48)
        for esc in ('\\n', '\\d', '\\t', '\\S{PRETTY_NAME}'):
            self.assertIn(esc, text)
        plain = re.sub(r'\\(n|d|t|S\{PRETTY_NAME\})', '', text)
        self.assertNotIn('\\', plain)

    def test_prompt_starts_at_the_left_margin(self):
        # agetty prints the login prompt right after the issue; login and PAM
        # messages must line up with it, so the issue ends with a newline
        text = make_issue.build(self.th, 113, 48)
        self.assertTrue(text.endswith('\n'))
        plain = re.sub(r'\x1b\[[0-9;]*m', '', text)
        self.assertEqual(plain.rsplit('\n', 2)[1].strip(), '')
        plain = re.sub(r'\x1b\[[0-9;]*m', '', text)
        self.assertIn('Benutzername eingeben, dann Finger scannen', plain)
        self.assertIn('Passwort geht jederzeit als Fallback', plain)

    def test_fits_narrow_console(self):
        text = make_issue.build(self.th, 80, 25)
        rows = [re.sub(r'\x1b\[[0-9;]*m', '', ln) for ln in text.split('\n')]
        self.assertTrue(all(len(r) <= 80 for r in rows[1:]))


if __name__ == '__main__':
    unittest.main()
