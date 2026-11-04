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


class TestTempRoles(unittest.TestCase):
    def test_thresholds(self):
        self.assertIsNone(hardware.temp_role(None))
        self.assertEqual(hardware.temp_role(40), 'green')
        self.assertEqual(hardware.temp_role(79), 'green')
        self.assertEqual(hardware.temp_role(80), 'yellow')
        self.assertEqual(hardware.temp_role(89), 'yellow')
        self.assertEqual(hardware.temp_role(90), 'red')
        self.assertEqual(hardware.temp_role(99), 'red')

    def test_uses_rounded_value(self):
        self.assertEqual(hardware.temp_role(79.6), 'yellow')
        self.assertEqual(hardware.temp_role(89.6), 'red')


class TestLongTest(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(bench.LONG_TESTS, sorted(set(bench.LONG_TESTS)))
        self.assertIn(bench.DEFAULT_LONG, bench.LONG_TESTS)
        self.assertEqual(bench.next_duration(300), 600)
        self.assertEqual(bench.next_duration(3600), 300)
        self.assertEqual(bench.next_duration(999), 300)

    def test_duration_format(self):
        self.assertEqual(bench.fmt_duration(0), '0:00')
        self.assertEqual(bench.fmt_duration(65), '1:05')
        self.assertEqual(bench.fmt_duration(600), '10:00')

    def test_stats(self):
        st = bench.TempStats(recent=2)
        st.add(50, 4000)
        st.add(60, 4100)
        st.add(None, None)
        st.add(70, 4200)
        self.assertEqual(st.temp_n, 3)
        self.assertAlmostEqual(st.temp_avg(), 60.0)
        self.assertEqual((st.temp_min, st.temp_max), (50, 70))
        self.assertAlmostEqual(st.recent_temp_avg(), 65.0)
        self.assertEqual(st.fan_n, 3)
        self.assertAlmostEqual(st.fan_avg(), 4100.0)
        self.assertEqual((st.fan_min, st.fan_max), (4000, 4200))

    def test_summary(self):
        st = bench.TempStats()
        for temp, fan in ((50, 4000), (60, 4100), (70, 4200)):
            st.add(temp, fan)
        reason, line1, line2 = bench.long_summary(st, [10.0, 9.8], 600)
        self.assertEqual(reason, 'done')
        self.assertIn('long test done 10:00', line1)
        self.assertIn('2 runs', line1)
        self.assertIn('temp avg 60.0', line1)
        self.assertIn('max 70 C', line1)
        self.assertIn('last min avg 60.0 C', line2)
        self.assertIn('fan avg 4100  max 4200 rpm', line2)
        self.assertIn('run 10.00 -> 9.80 s', line2)

    def test_summary_single_run(self):
        st = bench.TempStats()
        st.add(50, 4000)
        reason, line1, line2 = bench.long_summary(st, [9.7], 60)
        self.assertIn('1 run', line1)
        self.assertNotIn('1 runs', line1)
        self.assertNotIn('->', line2)

    def test_summary_stopped_before_any_sample(self):
        reason, line1, line2 = bench.long_summary(bench.TempStats(), [], 12, 'stopped')
        self.assertEqual(reason, 'stopped')
        self.assertIn('long test stopped 0:12', line1)
        self.assertIn('0 runs', line1)
        self.assertEqual(line2, '')


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
