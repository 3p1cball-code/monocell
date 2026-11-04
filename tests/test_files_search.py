"""File manager: recursive search by name."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from monocell.apps.files import Files, find_entries  # noqa: E402
from monocell.term import Key  # noqa: E402
from test_files import FakeWin, FakeWM  # noqa: E402


def tree():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'b', 'deep'))
    os.makedirs(os.path.join(d, '.hidden'))
    for rel in ('alpha.txt', 'b/beta.txt', 'b/deep/alpha2.txt', '.hidden/alpha3.txt'):
        with open(os.path.join(d, rel), 'w') as f:
            f.write('x')
    return d


class TestFindEntries(unittest.TestCase):
    def setUp(self):
        self.root = tree()
        self.addCleanup(shutil.rmtree, self.root)

    def test_names_relative_and_sorted(self):
        hits = find_entries(self.root, 'alpha')
        self.assertEqual([e.name for e in hits], ['alpha.txt', 'b/deep/alpha2.txt'])
        self.assertTrue(all(os.path.isabs(e.path) for e in hits))

    def test_hidden_directories_are_skipped(self):
        self.assertFalse(any('.hidden' in e.name for e in find_entries(self.root, 'alpha')))

    def test_limit(self):
        self.assertEqual(len(find_entries(self.root, 'alpha', limit=1)), 1)

    def test_case_insensitive(self):
        self.assertEqual([e.name for e in find_entries(self.root, 'ALPHA2')], ['b/deep/alpha2.txt'])


class TestSearchInFiles(unittest.TestCase):
    def setUp(self):
        self.root = tree()
        self.addCleanup(shutil.rmtree, self.root)
        self.wm = FakeWM()
        self.wm.prompt = lambda title, text, default, cb, parent=None: cb('alpha2')
        self.app = Files(self.wm, FakeWin(), path=self.root)
        self.addCleanup(self.app.on_close)

    def test_search_shows_results_and_esc_restores(self):
        self.app.start_search()
        self.assertEqual(self.app.fl.search, (self.root, 'alpha2'))
        self.assertEqual([e.name for e in self.app.fl.entries], ['b/deep/alpha2.txt'])
        self.assertEqual(self.app.win.title, 'Search: alpha2')
        self.app.on_key(Key('esc'))
        self.assertIsNone(self.app.fl.search)
        self.assertEqual(sorted(e.name for e in self.app.fl.entries),
                         ['alpha.txt', 'b'])

    def test_ctrl_f_starts_the_search(self):
        self.app.on_key(Key('ctrl+f'))
        self.assertEqual(self.app.fl.search[1], 'alpha2')


if __name__ == '__main__':
    unittest.main()
