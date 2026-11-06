"""Games menu: fullscreen launchers for the console and SDL/KMSDRM games."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell.app import REGISTRY, load_app  # noqa: E402
from monocell.apps import game  # noqa: E402


class FakeTerm:
    def __init__(self):
        self.stops = 0
        self.starts = 0
        self.front = 'old'

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1


class FakeWM:
    def __init__(self):
        self.term = FakeTerm()
        self.dirty = False
        self.resized = 0
        self.closed_popups = 0
        self.messages = []
        self.dialogs = []

    def close_popup(self):
        self.closed_popups += 1

    def on_resize(self):
        self.resized += 1

    def message(self, title, text, parent=None):
        self.messages.append((title, text))

    def dialog(self, title, text, buttons=('OK',), on_result=None, **kw):
        self.dialogs.append((title, text, buttons, on_result))


class Proc:
    def __init__(self, rc=0):
        self.rc = rc
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        return self.rc

    def terminate(self):
        pass

    def kill(self):
        pass


class Demo(game.Game):
    title = 'Demo'
    command = (sys.executable, '-c', 'pass')


class DemoGfx(game.Game):
    title = 'Demo Gfx'
    mode = 'gfx'
    command = (sys.executable, '-c', 'pass')


class Missing(game.Game):
    title = 'Missing'
    command = ('/nonexistent/game',)


class TestRegistry(unittest.TestCase):
    def test_every_game_row_has_a_class(self):
        names = [row[0] for row in REGISTRY]
        self.assertEqual(len(names), len(set(names)))
        rows = [row for row in REGISTRY if row[1] == 'game']
        self.assertGreaterEqual(len(rows), 12)
        for row in rows:
            self.assertIn(row[0], game.APPS)
            self.assertIs(load_app(row[0]), game.APPS[row[0]])

    def test_existing_apps_still_load(self):
        self.assertTrue(callable(load_app('files')))
        self.assertTrue(callable(load_app('terminal')))

    def test_doom_has_both_modes(self):
        self.assertEqual(game.DoomText.mode, 'text')
        self.assertEqual(game.DoomGfx.mode, 'gfx')
        self.assertIn('doom-ascii', game.DoomText.command[0])
        self.assertIn('chocolate-doom', game.DoomGfx.command[0])


class TestLaunch(unittest.TestCase):
    def setUp(self):
        self.wm = FakeWM()

    def test_text_game_hands_over_the_console(self):
        proc = Proc(0)
        with mock.patch.object(game.subprocess, 'Popen', return_value=proc) as popen:
            rc = Demo.launch(self.wm)
        self.assertEqual(rc, 0)
        self.assertEqual(self.wm.term.stops, 1)
        self.assertEqual(self.wm.term.starts, 1)
        self.assertIsNone(self.wm.term.front)
        self.assertTrue(self.wm.dirty)
        self.assertEqual(self.wm.resized, 1)
        self.assertEqual(self.wm.messages, [])
        args, kwargs = popen.call_args
        self.assertEqual(list(args[0])[:2], [sys.executable, '-c'])
        self.assertNotIn('SDL_VIDEODRIVER', kwargs['env'])

    def test_graphics_game_uses_kmsdrm(self):
        proc = Proc(0)
        with mock.patch.object(game.subprocess, 'Popen', return_value=proc) as popen:
            DemoGfx.launch(self.wm)
        self.assertEqual(self.wm.term.stops, 1)
        self.assertEqual(self.wm.term.starts, 1)
        args, kwargs = popen.call_args
        self.assertEqual(kwargs['env']['SDL_VIDEODRIVER'], 'kmsdrm')
        self.assertEqual(kwargs['stderr'], game.subprocess.STDOUT)
        self.assertIsNotNone(kwargs['stdout'])

    def test_failure_is_reported_after_restore(self):
        proc = Proc(1)
        with mock.patch.object(game.subprocess, 'Popen', return_value=proc):
            rc = Demo.launch(self.wm)
        self.assertEqual(rc, 1)
        self.assertEqual(self.wm.term.starts, 1)
        self.assertEqual(len(self.wm.messages), 1)
        self.assertIn('status 1', self.wm.messages[0][1])

    def test_signal_exit_is_not_an_error(self):
        with mock.patch.object(game.subprocess, 'Popen', return_value=Proc(-2)):
            rc = Demo.launch(self.wm)
        self.assertEqual(rc, -2)
        self.assertEqual(self.wm.term.starts, 1)
        self.assertEqual(self.wm.messages, [])

    def test_interrupt_restores_without_a_dialog(self):
        class IntProc(Proc):
            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise KeyboardInterrupt
                return -2

        with mock.patch.object(game.subprocess, 'Popen', return_value=IntProc()):
            Demo.launch(self.wm)
        self.assertEqual(self.wm.term.starts, 1)
        self.assertEqual(self.wm.messages, [])

    def test_start_error_still_restores(self):
        with mock.patch.object(game.subprocess, 'Popen', side_effect=OSError('boom')):
            Demo.launch(self.wm)
        self.assertEqual(self.wm.term.starts, 1)
        self.assertEqual(len(self.wm.messages), 1)
        self.assertIn('boom', self.wm.messages[0][1])

    def test_missing_binary_does_not_touch_the_screen(self):
        Missing.launch(self.wm)
        self.assertEqual(self.wm.term.stops, 0)
        self.assertEqual(len(self.wm.messages), 1)
        self.assertIn('not installed', self.wm.messages[0][1])

    def test_dirty_screen_is_rebuilt(self):
        self.wm.term.front = 'old frame'
        with mock.patch.object(game.subprocess, 'Popen', return_value=Proc(0)):
            Demo.launch(self.wm)
        self.assertIsNone(self.wm.term.front)


class TestQuake3(unittest.TestCase):
    def setUp(self):
        self.wm = FakeWM()

    def test_data_check_finds_installed_files(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            old = game.Q3_DATA
            game.Q3_DATA = (f.name,)
            try:
                self.assertEqual(game.q3_data(), [f.name])
            finally:
                game.Q3_DATA = old

    def test_missing_data_offers_openarena(self):
        with mock.patch.object(game, 'q3_data', return_value=[]):
            self.assertFalse(game.Quake3.check(self.wm))
        self.assertEqual(len(self.wm.dialogs), 1)
        title, text, buttons, callback = self.wm.dialogs[0]
        self.assertIn('pak0.pk3', text)
        self.assertEqual(buttons, ('OpenArena', 'Cancel'))
        called = []
        with mock.patch.object(game.APPS['openarena'], 'launch',
                               side_effect=lambda wm: called.append(wm)):
            callback('OpenArena', None)
            callback('Cancel', None)
        self.assertEqual(called, [self.wm])

    def test_argv_sets_the_data_path(self):
        argv = game.Quake3.argv()
        self.assertIn('fs_homepath', argv)
        i = argv.index('fs_homepath')
        self.assertEqual(argv[i + 1], os.path.expanduser('~/.q3a'))
        self.assertIn('r_customwidth', argv)

    def test_openarena_is_the_free_stand_in(self):
        self.assertEqual(game.OpenArena.mode, 'gfx')
        self.assertIn('r_customwidth', game.OpenArena.argv())
        self.assertTrue(game.OpenArena.command[0].endswith('openarena'))

    def test_openarena_uses_quake3e_when_present(self):
        with mock.patch.object(game.os.path, 'exists', return_value=True), \
                mock.patch.object(game.os.path, 'isdir', return_value=True), \
                mock.patch.object(game, 'screen_size', return_value=(1024, 768)):
            argv = game.OpenArena.argv()
        self.assertEqual(argv[0], game.Q3E)
        self.assertIn('fs_basegame', argv)
        self.assertIn('r_renderScale', argv)
        self.assertEqual(argv[argv.index('r_renderScale') + 1], '3')
        self.assertEqual(argv[argv.index('r_renderWidth') + 1], '800')
        self.assertEqual(argv[argv.index('r_customwidth') + 1], '1024')

    def test_graphics_fill_the_native_screen(self):
        with mock.patch.object(game, 'game_size', return_value=(1024, 768)), \
                mock.patch.object(game, 'screen_size', return_value=(1024, 768)):
            arena = game.OpenArena.argv()
            doom = game.DoomGfx.argv()
            q3 = game.Quake3.argv()
        for argv in (arena, q3):
            self.assertEqual(argv[argv.index('r_customwidth') + 1], '1024')
            self.assertEqual(argv[argv.index('r_customheight') + 1], '768')
        self.assertEqual(doom[doom.index('-width') + 1], '1024')
        self.assertEqual(doom[doom.index('-height') + 1], '768')

    def test_game_size_prefers_a_low_mode(self):
        with mock.patch.object(game, 'display_modes',
                               return_value={(1024, 768), (800, 600), (640, 480)}):
            self.assertEqual(game.game_size(), (800, 600))
        with mock.patch.object(game, 'display_modes', return_value={(1024, 768)}), \
                mock.patch.object(game, 'screen_size', return_value=(1024, 768)):
            self.assertEqual(game.game_size(), (1024, 768))
        with mock.patch.object(game, 'display_modes', return_value={(640, 480)}), \
                mock.patch.object(game, 'screen_size', return_value=(1024, 768)):
            self.assertEqual(game.game_size(), (640, 480))

    def test_screen_size_is_plausible(self):
        w, h = game.screen_size()
        self.assertGreaterEqual(w, 320)
        self.assertGreaterEqual(h, 200)


if __name__ == '__main__':
    unittest.main()
