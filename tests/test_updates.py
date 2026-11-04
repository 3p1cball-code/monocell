"""Updates app: apt output parsing, progress, and the root helper's contract."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.apps import updates  # noqa: E402

APT_LIST = """Listing...
bind9-host/stable-security 1:9.20.30-1~deb13u1 amd64 [upgradable from: 1:9.20.26-1~deb13u1]
brave-browser/stable 1.97.59 amd64 [upgradable from: 1.97.57]
linux-image-amd64/stable-updates 6.12.60-1 amd64 [upgradable from: 6.12.57-1]
"""


class TestParse(unittest.TestCase):
    def test_upgradable(self):
        pkgs = updates.parse_upgradable(APT_LIST)
        self.assertEqual([p['name'] for p in pkgs],
                         ['bind9-host', 'brave-browser', 'linux-image-amd64'])
        b = pkgs[0]
        self.assertTrue(b['security'])
        self.assertEqual((b['old'], b['new']), ('1:9.20.26-1~deb13u1', '1:9.20.30-1~deb13u1'))
        self.assertFalse(pkgs[1]['security'])

    def test_security_first(self):
        text = 'a/stable 2 amd64 [upgradable from: 1]\nz/stable-security 2 amd64 [upgradable from: 1]\n'
        self.assertEqual([p['name'] for p in updates.parse_upgradable(text)], ['z', 'a'])

    def test_garbage_is_ignored(self):
        self.assertEqual(updates.parse_upgradable('Listing...\nWARNING: apt does not have a '
                                                  'stable CLI interface.\n'), [])

    def test_progress(self):
        log = ['Get:1 http://deb x', 'Unpacking a (2) over (1) ...', 'Unpacking b (2) over (1) ...',
               'Setting up a (2) ...']
        frac, label = updates.progress_of(log, 2)
        self.assertAlmostEqual(frac, 0.75)
        self.assertTrue(label.startswith('Setting up a'))
        self.assertEqual(updates.progress_of([], 3)[0], 0.0)

    def test_ago(self):
        self.assertEqual(updates.ago(None), 'never')
        self.assertEqual(updates.ago(1000, now=1030), 'just now')
        self.assertEqual(updates.ago(100, now=7300), '2 h ago')


class TestInstallFlow(unittest.TestCase):
    def test_lists_are_refreshed_before_installing(self):
        app = updates.Updates.__new__(updates.Updates)
        app.sudo_cmd = lambda a: ['sudo', '-n', updates.HELPER, a]
        cmds = app.install_commands()
        self.assertEqual([c[-1] for c in cmds], ['update', 'upgrade'])


class TestHelper(unittest.TestCase):
    HELPER = os.path.join(ROOT, 'deploy', 'x61s', 'monocell-apt')

    def test_rejects_anything_else(self):
        for args in ([], ['install', 'foo'], ['upgrade; rm -rf /'], ['remove']):
            r = subprocess.run(['sh', self.HELPER] + args, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn('usage', r.stderr)

    def test_never_removes_packages(self):
        text = open(self.HELPER).read()
        self.assertIn('--with-new-pkgs upgrade', text)
        self.assertNotIn('full-upgrade', text.split('set -eu', 1)[1])
        self.assertNotIn('dist-upgrade', text)
        self.assertIn('--force-confold', text)


if __name__ == '__main__':
    unittest.main()
