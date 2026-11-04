"""pam_fprintd options: more tries and a longer timeout for the swipe sensor."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import set_pam_fprintd  # noqa: E402

SAMPLE = """# here are the per-package modules (the "Primary" block)
auth\t[success=2 default=ignore]\tpam_fprintd.so max-tries=1 timeout=3 # debug
auth\t[success=1 default=ignore]\tpam_unix.so nullok try_first_pass
auth\t[default=die]\tpam_faildelay.so delay=2000000
"""


class TestPamFprintd(unittest.TestCase):
    def test_rewrites_options_and_keeps_the_rest(self):
        new, found = set_pam_fprintd.rewrite(SAMPLE, 3, 45)
        self.assertTrue(found)
        self.assertIn('pam_fprintd.so max-tries=3 timeout=45\n', new)
        self.assertIn('pam_unix.so nullok try_first_pass', new)
        self.assertNotIn('debug', new)
        self.assertNotIn('timeout=3 ', new)

    def test_is_idempotent(self):
        once, _ = set_pam_fprintd.rewrite(SAMPLE, 3, 45)
        twice, _ = set_pam_fprintd.rewrite(once, 3, 45)
        self.assertEqual(once, twice)

    def test_other_control_fields_survive(self):
        text = 'auth required pam_fprintd.so max-tries=1\n'
        new, found = set_pam_fprintd.rewrite(text, 2, 20)
        self.assertTrue(found)
        self.assertEqual(new, 'auth required pam_fprintd.so max-tries=2 timeout=20\n')

    def test_no_line(self):
        _new, found = set_pam_fprintd.rewrite('auth required pam_unix.so\n', 3, 45)
        self.assertFalse(found)

    def test_cli(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'common-auth')
        with open(p, 'w') as f:
            f.write(SAMPLE)
        self.assertEqual(set_pam_fprintd.main.__module__[:7], 'set_pam')
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'set_pam_fprintd.py'),
                            p, '--tries', '4', '--timeout', '60'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(p) as f:
            self.assertIn('max-tries=4 timeout=60', f.read())


if __name__ == '__main__':
    unittest.main()
