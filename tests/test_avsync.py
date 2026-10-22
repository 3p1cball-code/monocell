"""A/V sync: the audio clock has to report the sample that is actually audible."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import audio  # noqa: E402
from monocell.apps import video  # noqa: E402
from monocell.theme import DEFAULT_CONFIG  # noqa: E402


class TestClock(unittest.TestCase):
    def test_before_any_audio(self):
        self.assertEqual(audio.playing_position(0, 3.0, 0.2, 7.0), 7.0)

    def test_wall_clock_cap(self):
        # nothing written yet -> the clock must not run off on the wall clock
        self.assertEqual(audio.playing_position(0, 0.1, 0.0), 0.0)

    def test_buffer_is_subtracted(self):
        second = audio.BPS
        self.assertAlmostEqual(audio.playing_position(second, 1.0, 0.15), 0.85)
        self.assertAlmostEqual(audio.playing_position(second, 1.0, 0.06), 0.94)

    def test_never_negative(self):
        self.assertEqual(audio.playing_position(1000, 0.5, 0.15), 0.0)

    def test_never_ahead_of_wall_clock(self):
        # sink swallowed a lot at once (pipe buffer): do not jump into the future
        self.assertAlmostEqual(audio.playing_position(audio.BPS * 4, 0.2, 0.06), 0.2)

    def test_offset(self):
        self.assertAlmostEqual(audio.playing_position(audio.BPS, 1.0, 0.0, 30.0), 31.0)

    def test_monotonic(self):
        prev = -1.0
        for i in range(200):
            el = i * 0.02
            pos = audio.playing_position(int((el + audio.LEAD) * audio.BPS), el, audio.LEAD + 0.06)
            self.assertGreaterEqual(pos, prev)
            prev = pos


class TestSinks(unittest.TestCase):
    def test_latency_args(self):
        self.assertIn('--latency=60ms', audio.SINKS['pw-cat'])
        self.assertIn('--latency-msec=60', audio.SINKS['pacat'])
        self.assertIn('-B', audio.SINKS['aplay'])
        self.assertIn('-F', audio.SINKS['aplay'])

    def test_buffered(self):
        for name, lat in audio.SINK_LATENCY.items():
            dummy = type('P', (), {'sink_name': name})()
            self.assertAlmostEqual(audio.AudioPlayer.buffered.fget(dummy), audio.LEAD + lat)
        dummy = type('P', (), {'sink_name': None})()
        self.assertGreater(audio.AudioPlayer.buffered.fget(dummy), 0)

    def test_pacat_first_on_pulse(self):
        rt = '/run/user/%d/pulse/native' % os.getuid()
        if not os.path.exists(rt):
            self.skipTest('no pulse/pipewire socket')
        self.assertEqual(audio.pick_sinks()[0], 'pacat')


class TestVideoClock(unittest.TestCase):
    def test_config_has_offset(self):
        self.assertEqual(DEFAULT_CONFIG['av_offset'], 0)

    def test_media_time_prefers_audio(self):
        class A:
            dec = object()
            position = 12.5

        v = type('V', (), {'paused': False, 'info': {'audio': True}, 'audio': A(),
                           'offset': 0.0, 'clock0': 0.0, 'last_audio': 0.0,
                           'last_audio_at': 0.0})()
        self.assertEqual(video.Video.media_time(v), 12.5)

    def test_media_time_continues_after_audio_end(self):
        import time

        class A:
            dec = None
            position = 0.0

        v = type('V', (), {'paused': False, 'info': {'audio': True}, 'audio': A(),
                           'offset': 0.0, 'clock0': 0.0,
                           'last_audio': 61.0, 'last_audio_at': time.monotonic() - 1.0})()
        self.assertAlmostEqual(video.Video.media_time(v), 62.0, delta=0.05)

    def test_media_time_without_audio_uses_wall_clock(self):
        class A:
            dec = None
            position = 0.0

        v = type('V', (), {'paused': False, 'info': {'audio': False}, 'audio': A(),
                           'offset': 10.0, 'clock0': 0.0,
                           'last_audio': 0.0, 'last_audio_at': 0.0})()
        self.assertGreaterEqual(video.Video.media_time(v), 10.0)

    def test_paused_uses_pause_position(self):
        v = type('V', (), {'paused': True, 'info': {'audio': True}, 'pause_pos': 3.5})()
        self.assertEqual(video.Video.media_time(v), 3.5)


if __name__ == '__main__':
    unittest.main()
