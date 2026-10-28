"""Files: remembering the last folder between starts."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import media, transfer  # noqa: E402
from monocell.apps.files import Files  # noqa: E402
from monocell.screen import Buffer, Canvas  # noqa: E402
from monocell.util import HOME  # noqa: E402


class FakeCfg(dict):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.saves = 0

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def save(self):
        self.saves += 1


class FakeTimer:
    def __init__(self, cb):
        self.cb = cb
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeLoop:
    def __init__(self):
        self.timers = []

    def every(self, *a, **kw):
        return None

    def call_later(self, delay, cb, owner=None):
        t = FakeTimer(cb)
        self.timers.append(t)
        return t

    def run_in_thread(self, fn, done=None, error=None):
        try:
            res = fn()
        except Exception as e:
            if error:
                error(e)
        else:
            if done:
                done(res)


class FakeTheme:
    r = {'accent': 1, 'dim': 2, 'shadow': 0, 'bright': 15, 'field': 3}
    rgb = [(0, 0, 0)] * 16

    def c(self, fg, bg, fl=0):
        return 0


class FakeWin:
    title = 'Files'
    cw = 88
    ch = 26
    closed = False


class FakeWM:
    def __init__(self, **cfg):
        self.cfg = FakeCfg(cfg)
        self.loop = FakeLoop()
        self.theme = FakeTheme()
        self.dirty = False
        self.toasts = []

    def toast(self, text, kind='info', secs=3.5):
        self.toasts.append(text)


class TestRemember(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.a = os.path.join(self.root, 'a')
        self.b = os.path.join(self.a, 'b')
        os.makedirs(self.b)
        self.file = os.path.join(self.a, 'note.txt')
        with open(self.file, 'w') as f:
            f.write('x\n')

    def open(self, path=None, **cfg):
        wm = FakeWM(**cfg)
        app = Files(wm, FakeWin(), path=path)
        self.addCleanup(app.on_close)
        return app, wm

    def test_starts_in_the_remembered_folder(self):
        app, wm = self.open(files_dir=self.b)
        self.assertEqual(app.fl.path, self.b)

    def test_missing_folder_falls_back_to_home(self):
        app, wm = self.open(files_dir=os.path.join(self.root, 'gone'))
        self.assertEqual(app.fl.path, HOME)

    def test_explicit_path_is_used_and_remembered(self):
        app, wm = self.open(path=self.b)
        self.assertEqual(app.fl.path, self.b)
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.b)

    def test_opening_a_file_remembers_its_folder(self):
        app, wm = self.open(path=self.file)
        self.assertEqual(app.fl.path, self.a)
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.a)

    def test_navigation_is_saved_after_the_debounce(self):
        app, wm = self.open(files_dir=self.a)
        app.go(self.b)
        self.assertEqual(app.fl.path, self.b)
        self.assertEqual(wm.cfg.get('files_dir'), self.a)      # not yet written
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.b)

    def test_arrow_through_folders_writes_once(self):
        app, wm = self.open(files_dir=self.a)
        app.fl.chdir(self.b)                       # what Up/Backspace ends up doing
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.b)
        saves = wm.cfg.saves
        app.remember_dir(self.b)                   # same folder again: no write
        app.save_dir()
        self.assertEqual(wm.cfg.saves, saves)

    def test_going_up_remembers_the_parent(self):
        app, wm = self.open(files_dir=self.b)
        app.fl.up()
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.a)

    def test_closing_writes_immediately(self):
        app, wm = self.open(files_dir=self.a)
        app.go(self.b)
        app.on_close()
        self.assertEqual(wm.cfg['files_dir'], self.b)

    def test_failed_navigation_keeps_the_folder(self):
        app, wm = self.open(files_dir=self.a)
        app.go(os.path.join(self.root, 'nope'))
        self.assertEqual(app.fl.path, self.a)
        app.save_dir()
        self.assertEqual(wm.cfg['files_dir'], self.a)


class TestDrives(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.mp = os.path.join(self.root, 'MICRODRIVE')
        os.makedirs(self.mp)
        with open(os.path.join(self.mp, 'a.txt'), 'w') as f:
            f.write('x')
        self.stick = {'device': '/dev/sdb1', 'name': 'MICRODRIVE', 'label': 'MICRODRIVE',
                      'model': 'IBM', 'size': 10 ** 9, 'size_text': '1.0G',
                      'fstype': 'vfat', 'mount': '', 'tran': 'ata'}
        self.card = dict(self.stick, device='/dev/mmcblk0p1', name='FOTOS',
                         mount=self.mp)

    def open(self, vols, **cfg):
        p = mock.patch.object(media, 'snapshot', return_value=vols)
        p.start()
        self.addCleanup(p.stop)
        wm = FakeWM(**cfg)
        app = Files(wm, FakeWin())
        self.addCleanup(app.on_close)
        return app, wm

    def test_drives_are_listed(self):
        app, _ = self.open([self.stick, self.card])
        self.assertEqual([v['name'] for v in app.drives], ['MICRODRIVE', 'FOTOS'])

    def test_open_a_mounted_drive(self):
        app, _ = self.open([self.card])
        app.open_drive(0)
        self.assertEqual(app.fl.path, self.mp)

    def test_open_an_unmounted_drive_mounts_it_first(self):
        app, wm = self.open([self.stick])
        with mock.patch.object(media, 'mount', return_value=(self.mp, '')) as m:
            app.open_drive(0)
        m.assert_called_once_with('/dev/sdb1')
        self.assertEqual(app.fl.path, self.mp)

    def test_failed_mount_reports_and_stays(self):
        app, wm = self.open([self.stick], files_dir=self.root)
        with mock.patch.object(media, 'mount', return_value=('', 'Not authorized')):
            app.open_drive(0)
        self.assertEqual(app.fl.path, self.root)
        self.assertTrue(any('Not authorized' in t for t in wm.toasts), wm.toasts)

    def test_unmount_leaves_the_mountpoint(self):
        app, wm = self.open([self.card], files_dir=self.mp)
        self.assertEqual(app.fl.path, self.mp)
        with mock.patch.object(media, 'unmount', return_value=(True, '')) as u:
            app.unmount_drive(self.card)
        u.assert_called_once_with('/dev/mmcblk0p1')
        self.assertNotEqual(app.fl.path, self.mp)
        self.assertTrue(any('Unmounted' in t for t in wm.toasts), wm.toasts)

    def test_busy_unmount_keeps_the_folder(self):
        app, wm = self.open([self.card], files_dir=self.mp)
        with mock.patch.object(media, 'unmount', return_value=(False, 'target is busy')):
            app.unmount_drive(self.card)
        self.assertEqual(app.fl.path, self.mp)
        self.assertTrue(any('busy' in t for t in wm.toasts), wm.toasts)

    def test_drive_click_zone(self):
        app, _ = self.open([self.card])
        app.on_click(('drive', 0), None)
        self.assertEqual(app.fl.path, self.mp)


class TestTransferBar(unittest.TestCase):
    def app(self, state='running', total=1000, done=470, files=2, files_total=4, width=80):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with mock.patch.object(media, 'snapshot', return_value=[]):
            wm = FakeWM(files_dir=root)
            a = Files(wm, FakeWin())
        self.addCleanup(a.on_close)
        t = transfer.Transfer(['/x'], root)
        t.state = state
        t.total, t.done = total, done
        t.files_done, t.files_total = files, files_total
        t.current = 'big.bin'
        a.transfer = t
        buf = Buffer(width, 2)
        a.draw_transfer(Canvas(buf), width, 1)
        return a, ''.join(buf.ch[1])

    def test_running_shows_counts_percent_and_stop(self):
        a, row = self.app()
        self.assertIn('Copying 2/4', row)
        self.assertIn('47%', row)
        self.assertIn('470B/1000B', row)
        self.assertIn('Stop', row)
        self.assertIn('stop', [z[4] for z in a.zones])

    def test_move_says_moving(self):
        a, row = self.app()
        a.transfer.move = True
        buf = Buffer(80, 2)
        a.draw_transfer(Canvas(buf), 80, 1)
        self.assertIn('Moving', ''.join(buf.ch[1]))

    def test_preparing_before_the_size_is_known(self):
        a, row = self.app(state='planning', total=0, done=0)
        self.assertIn('preparing', row)
        self.assertIn('stop', [z[4] for z in a.zones])

    def test_narrow_window_still_shows_the_percentage(self):
        a, row = self.app(width=30)
        self.assertIn('47%', row)
        self.assertIn('Stop', row)

    def test_knowing_nothing_does_not_divide_by_zero(self):
        a, row = self.app(total=0, done=0)
        self.assertIn('0%', row)


if __name__ == '__main__':
    unittest.main()
