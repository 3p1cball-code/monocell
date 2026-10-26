"""Pi benchmark tests: digits, checksum, the worker process, sensor parsing."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import hardware, piworker          # noqa: E402
from monocell.app import REGISTRY, load_app      # noqa: E402
from monocell.apps import bench                  # noqa: E402

PI_100 = ('31415926535897932384626433832795028841971693993751'
          '05820974944592307816406286208998628034825342117067')


class TestDigits(unittest.TestCase):
    def test_first_digits(self):
        self.assertEqual(piworker.pi_digits(1), '3')
        self.assertEqual(piworker.pi_digits(10), '3141592653')
        self.assertEqual(piworker.pi_digits(100), PI_100)

    def test_matches_known_head(self):
        head = piworker.pi_digits(50)
        self.assertEqual(head, bench.KNOWN_HEAD[:50])

    def test_deterministic(self):
        self.assertEqual(piworker.pi_digits(300), piworker.pi_digits(300))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            piworker.pi_digits(0)

    def test_checksum(self):
        a = piworker.checksum(piworker.pi_digits(200))
        b = piworker.checksum(piworker.pi_digits(200))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)
        self.assertNotEqual(a, piworker.checksum(piworker.pi_digits(201)))


class TestWorkerProcess(unittest.TestCase):
    def test_cli(self):
        env = dict(os.environ, PYTHONPATH=os.path.abspath(ROOT))
        out = subprocess.check_output(
            [sys.executable, '-m', 'monocell.piworker', '200'], env=env, text=True, timeout=120)
        data = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(data['digits'], 200)
        self.assertEqual(data['head'], PI_100[:20])
        self.assertGreater(data['secs'], 0)
        self.assertEqual(data['checksum'], piworker.checksum(piworker.pi_digits(200)))


class TestSensors(unittest.TestCase):
    def test_temp_sane(self):
        t = hardware.cpu_temp()
        if t is not None:
            self.assertGreater(t, 0)
            self.assertLess(t, 150)

    def test_fan_sane(self):
        rpm = hardware.fan_rpm()
        if rpm is not None:
            self.assertGreater(rpm, 0)
            self.assertLess(rpm, 20000)

    def test_cores_are_plausible(self):
        cores = hardware.cpu_cores()
        self.assertIsInstance(cores, list)
        for name, value in cores:
            self.assertTrue(name)
            self.assertGreater(value, 0)
            self.assertLess(value, 150)

    def test_hottest_core_matches_cores(self):
        cores = hardware.cpu_cores()
        if cores:
            self.assertEqual(hardware.cpu_temp(), max(v for _, v in cores))

    def test_cpu_label(self):
        self.assertTrue(bench.cpu_label())


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        names = [row[0] for row in REGISTRY]
        self.assertIn('bench', names)

    def test_loads(self):
        self.assertIs(load_app('bench'), bench.Bench)

    def test_rungs_sorted(self):
        self.assertEqual(bench.RUNGS, sorted(set(bench.RUNGS)))
        self.assertIn(bench.DEFAULT_DIGITS, bench.RUNGS)


if __name__ == '__main__':
    unittest.main()
