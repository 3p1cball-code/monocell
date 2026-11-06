"""Startup sound catalogue: selection mapping, installed-file filtering, settings page."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import startup, util  # noqa: E402


class TestStartup(unittest.TestCase):
    def setUp(self):
        self.old = util.DATA_DIR
        self.tmp = tempfile.mkdtemp()
        util.DATA_DIR = self.tmp

    def tearDown(self):
        util.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        d = startup.sound_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), 'wb') as f:
            f.write(b'RIFF')

    def test_catalog_ids_and_files_unique(self):
        ids = [c[0] for c in startup.CATALOG]
        files = [c[1] for c in startup.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(files), len(set(files)))
        self.assertIn(startup.DEFAULT, ids)
        for i, f, title, desc in startup.CATALOG:
            self.assertTrue(title and desc)
            self.assertTrue(f.endswith('.wav'))

    def test_available_filters_installed_files(self):
        self.assertEqual(startup.available(), [])
        self._touch('win95.wav')
        self._touch('mac.wav')
        self.assertEqual([a[0] for a in startup.available()], ['win95', 'mac'])
        rows = startup.rows()
        self.assertEqual(rows[0], ('', 'Off', 'No startup sound.'))
        self.assertEqual([r[0] for r in rows[1:]], ['win95', 'mac'])

    def test_current_maps_legacy_true_false(self):
        self.assertEqual(startup.current({'startup_sound': True}), 'win95')
        self.assertEqual(startup.current({'startup_sound': False}), '')
        self.assertEqual(startup.current({'startup_sound': ''}), '')
        self.assertEqual(startup.current({'startup_sound': 'os2warp4'}), 'os2warp4')
        self.assertEqual(startup.current({}), 'win95')
        self.assertEqual(startup.current(None), 'win95')

    def test_path_for(self):
        p = startup.path_for('win95')
        self.assertTrue(p.endswith(os.path.join('sounds', 'startup', 'win95.wav')))
        self.assertIsNone(startup.path_for('nope'))

    def test_play_is_quiet_by_default(self):
        old = os.environ.pop('MONOCELL_NOAUDIO', None)
        try:
            self.assertIsNone(startup.play({'startup_sound': 'win95'}))   # no file
            self._touch('win95.wav')
            self.assertIsNone(startup.play({'startup_sound': ''}))        # off
            os.environ['MONOCELL_NOAUDIO'] = '1'
            self.assertIsNone(startup.play({'startup_sound': 'win95'}, force=True))
        finally:
            os.environ.pop('MONOCELL_NOAUDIO', None)
            if old is not None:
                os.environ['MONOCELL_NOAUDIO'] = old

    def test_enabled(self):
        self.assertFalse(startup.enabled({'startup_sound': 'win95'}))
        self._touch('win95.wav')
        if util.which('ffmpeg'):
            self.assertTrue(startup.enabled({'startup_sound': 'win95'}))
            self.assertFalse(startup.enabled({'startup_sound': ''}))
            self.assertTrue(startup.enabled({'startup_sound': ''}, force=True))


class TestSettingsSoundsPage(unittest.TestCase):
    def test_section_and_option(self):
        from monocell.apps import settings
        keys = [k for k, _label in settings.SECTIONS]
        self.assertIn('sounds', keys)
        self.assertEqual(settings.OPTIONS['sounds'][0][0], 'audio_sink')
        media = [o[0] for o in settings.OPTIONS['media']]
        self.assertNotIn('audio_sink', media)


if __name__ == '__main__':
    unittest.main()
