"""TRD server helpers: audio must leave in whole sample frames."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.remote import audio_chunk  # noqa: E402


class TestAudioChunk(unittest.TestCase):
    def test_whole_frames_only(self):
        for have in range(0, 40):
            for allowed in range(0, 40):
                n = audio_chunk(allowed, have)
                self.assertEqual(n % 4, 0)
                self.assertLessEqual(n, min(allowed, have))
                self.assertGreater(min(allowed, have) - n, -1)
                self.assertLess(min(allowed, have) - n, 4)

    def test_remainder_stays_in_the_buffer(self):
        # 4095 bytes (an odd read) -> 4092 sent, 3 wait for the next read
        self.assertEqual(audio_chunk(65536, 4095), 4092)
        self.assertEqual(audio_chunk(2, 4095), 0)


if __name__ == '__main__':
    unittest.main()
