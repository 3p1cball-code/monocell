"""Copy/move with progress: byte counts, cancel, and the cross-device fallback."""
import errno
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import wm  # noqa: E402
from monocell.transfer import SYNC_EVERY, Transfer, sync_interval  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.src = os.path.join(self.root, 'src')
        self.dst = os.path.join(self.root, 'dst')
        os.makedirs(self.src)
        os.makedirs(self.dst)

    def read(self, path):
        with open(path, 'rb') as f:
            return f.read()

    def write(self, name, size=10, base=None):
        path = os.path.join(base or self.src, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(bytes((i % 251) for i in range(size)))
        return path

    def go(self, sources, move=False):
        t = Transfer(sources, self.dst, move=move)
        t.plan()
        t.run()
        return t


class TestCopy(Base):
    def test_one_file(self):
        p = self.write('a.txt', 1000)
        t = self.go([p])
        self.assertEqual(t.state, 'done')
        self.assertEqual((t.total, t.done, t.files_total, t.files_done), (1000, 1000, 1, 1))
        self.assertEqual(self.read(os.path.join(self.dst, 'a.txt')), self.read(p))
        self.assertAlmostEqual(t.fraction, 1.0)

    def test_a_tree(self):
        self.write('a.txt', 500)
        self.write('sub/b.txt', 700)
        self.write('sub/deep/c.txt', 300)
        t = self.go([self.src])
        self.assertEqual(t.state, 'done')
        self.assertEqual(t.total, 1500)
        self.assertEqual(t.files_total, 3)
        self.assertEqual(t.done, 1500)
        self.assertEqual(t.files_done, 3)
        for rel in ('src/a.txt', 'src/sub/b.txt', 'src/sub/deep/c.txt'):
            src = os.path.join(self.root, rel)
            copy = os.path.join(self.dst, rel)
            self.assertTrue(os.path.exists(copy), rel)
            self.assertEqual(self.read(src), self.read(copy), rel)

    def test_same_folder_gets_a_copy_name(self):
        p = self.write('note.txt', 10)
        t = Transfer([p], self.src)
        t.plan()
        t.run()
        self.assertEqual(t.state, 'done')
        self.assertEqual(sorted(os.listdir(self.src)), ['note copy.txt', 'note.txt'])

    def test_an_existing_target_is_not_merged(self):
        self.write('hash.txt', 5)
        p = self.write('file.txt', 5)
        os.makedirs(os.path.join(self.dst, 'file.txt'))     # a folder with that name
        t = self.go([p])
        self.assertEqual(t.state, 'error')
        self.assertTrue(t.error)

    def test_symlink_is_recreated(self):
        target = self.write('real.txt', 20)
        link = os.path.join(self.src, 'link.txt')
        os.symlink(target, link)
        t = self.go([link])
        self.assertEqual(t.state, 'done')
        self.assertTrue(os.path.islink(os.path.join(self.dst, 'link.txt')))

    def test_empty_file(self):
        p = self.write('empty', 0)
        t = self.go([p])
        self.assertEqual(t.state, 'done')
        self.assertTrue(os.path.exists(os.path.join(self.dst, 'empty')))


class TestMove(Base):
    def test_rename_on_the_same_filesystem(self):
        p = self.write('a.txt', 400)
        t = self.go([p], move=True)
        self.assertEqual(t.state, 'done')
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.exists(os.path.join(self.dst, 'a.txt')))
        self.assertEqual((t.done, t.files_done), (400, 1))

    def test_cross_device_falls_back_to_copy_and_delete(self):
        p = self.write('a.txt', 1200)
        t = Transfer([p], self.dst, move=True)
        t.plan()

        def no_rename(src, dst):
            raise OSError(errno.EXDEV, 'Invalid cross-device link')
        t.rename = no_rename
        t.run()
        self.assertEqual(t.state, 'done')
        self.assertFalse(os.path.exists(p))
        self.assertEqual(len(self.read(os.path.join(self.dst, 'a.txt'))), 1200)
        self.assertEqual(t.done, 1200)

    def test_cross_device_tree(self):
        self.write('sub/x.txt', 100)
        self.write('y.txt', 50)
        t = Transfer([self.src], self.dst, move=True)
        t.plan()
        t.rename = lambda s, d: (_ for _ in ()).throw(OSError(errno.EXDEV, 'xdev'))
        t.run()
        self.assertEqual(t.state, 'done')
        self.assertFalse(os.path.exists(self.src))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, 'src', 'sub', 'x.txt')))
        self.assertEqual(t.done, 150)


class TestCancel(Base):
    def cancel_after_first_block(self, t):
        t.block_hook = lambda tr: tr.cancel()

    def test_cancel_a_big_file(self):
        p = self.write('big.bin', 3 * (1 << 20))
        t = Transfer([p], self.dst)
        t.plan()
        self.cancel_after_first_block(t)
        t.run()
        self.assertEqual(t.state, 'cancelled')
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'big.bin')))
        self.assertEqual(t.done, 0)             # the partial block is taken back
        self.assertEqual(t.files_done, 0)

    def test_cancel_removes_a_half_copied_tree(self):
        self.write('one.bin', 2 << 20)
        self.write('two.bin', 100)
        t = Transfer([self.src], self.dst)
        t.plan()
        self.cancel_after_first_block(t)
        t.run()
        self.assertEqual(t.state, 'cancelled')
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'src')))

    def test_cancel_before_start(self):
        p = self.write('a.txt', 10)
        t = Transfer([p], self.dst)
        t.plan()
        t.cancel()
        t.run()
        self.assertEqual(t.state, 'cancelled')
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'a.txt')))

    def test_cancel_leaves_finished_files(self):
        self.write('a.bin', 100)                # smaller than one block: completes
        self.write('b.bin', 3 << 20)
        t = Transfer([self.src], self.dst)
        t.plan()
        t.block_hook = lambda tr: (tr.cancel() if tr.files_done >= 1 else None)
        t.run()
        self.assertEqual(t.state, 'cancelled')
        # the first file was finished, then the tree is removed as a whole
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'src')))


MOUNTS = '''/dev/sda1 / ext4 rw,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid 0 0
/dev/sdb1 /media/u/MICRODRIVE vfat rw,flush,errors=remount-ro 0 0
/dev/sdb2 /media/u/NOFLUSH exfat rw 0 0
//server/share /mnt/share cifs rw,vers=3.1.1 0 0
/dev/sda9 /mnt/disk ext4 rw,flush 0 0
'''


class TestSyncPolicy(unittest.TestCase):
    def interval(self, path):
        return sync_interval(path, mounts=MOUNTS)

    def test_ext4_and_tmpfs_do_not_sync_per_block(self):
        self.assertEqual(self.interval('/home/u/file'), 0)
        self.assertEqual(self.interval('/tmp/x'), 0)

    def test_vfat_syncs_before_close_gets_expensive(self):
        self.assertEqual(self.interval('/media/u/MICRODRIVE/a.avi'), SYNC_EVERY)
        self.assertEqual(self.interval('/media/u/MICRODRIVE'), SYNC_EVERY)

    def test_any_filesystem_mounted_with_flush(self):
        self.assertEqual(self.interval('/mnt/disk/x'), SYNC_EVERY)

    def test_exfat_syncs_even_without_the_flush_option(self):
        self.assertEqual(self.interval('/media/u/NOFLUSH/x'), SYNC_EVERY)

    def test_network_shares_are_left_fast(self):
        self.assertEqual(self.interval('/mnt/share/x'), 0)

    def test_the_longest_mountpoint_wins(self):
        self.assertEqual(self.interval('/media/u/MICRODRIVE/sub/x'), SYNC_EVERY)

    def test_unknown_destination_is_treated_as_slow(self):
        self.assertEqual(sync_interval('/nowhere', mounts=''), SYNC_EVERY)


class TestSlowMedia(Base):
    def test_big_file_copies_completely(self):
        size = 9 * (1 << 20)                   # crosses the 8 MB window on slow media
        p = self.write('big.bin', size)
        dest = os.path.join(self.root, 'media')
        os.makedirs(dest)
        t = Transfer([p], dest)
        t.sync_every = SYNC_EVERY              # pretend the destination is a card
        t.plan()
        t.run()
        self.assertEqual(t.state, 'done')
        self.assertEqual(len(self.read(os.path.join(dest, 'big.bin'))), size)
        self.assertEqual(t.done, size)


class TestSpace(Base):
    def test_fits(self):
        p = self.write('a.txt', 1000)
        t = Transfer([p], self.dst)
        t.plan()
        self.assertIsNone(t.space_problem())

    def test_a_full_target_is_refused_before_copying(self):
        p = self.write('a.txt', 1000)
        t = Transfer([p], self.dst)
        t.plan()
        fake = type('U', (), {'free': 500, 'total': 10 ** 9, 'used': 10 ** 9})()
        with mock.patch.object(shutil, 'disk_usage', return_value=fake):
            problem = t.space_problem()
        self.assertIn('needed', problem)
        self.assertIn('free', problem)

    def test_nothing_to_do(self):
        t = Transfer([], self.dst)
        t.plan()
        self.assertIsNone(t.space_problem())


class TestExitGuard(unittest.TestCase):
    def transfer(self, state='running', total=1000, done=470):
        t = Transfer(['/x'], '/y')
        t.state, t.total, t.done = state, total, done
        return t

    def test_note_carries_progress(self):
        note = wm.WM.busy_note(None, self.transfer())
        self.assertIn('47%', note)
        self.assertIn('stop it', note.lower())

    def test_only_running_transfers_count(self):
        import types
        t = self.transfer()
        app = types.SimpleNamespace(transfer=t)
        fake = types.SimpleNamespace(windows=[types.SimpleNamespace(app=app),
                                              types.SimpleNamespace(app=None)])
        self.assertEqual(wm.WM.busy_transfers(fake), [t])
        t.state = 'done'
        self.assertEqual(wm.WM.busy_transfers(fake), [])


class TestProgress(Base):
    def test_counters_move_while_copying(self):
        seen = []
        p = self.write('big.bin', 2 * (1 << 20))
        t = Transfer([p], self.dst)
        t.plan()
        t.block_hook = lambda tr: seen.append((tr.done, tr.fraction))
        t.run()
        self.assertEqual(len(seen), 2)          # 1 MB blocks
        self.assertLess(seen[0][1], 1.0)
        self.assertEqual(t.fraction, 1.0)

    def test_plan_does_not_touch_the_disk(self):
        p = self.write('a.txt', 10)
        t = Transfer([p], self.dst)
        t.plan()
        self.assertEqual(t.state, 'planning')
        self.assertEqual(t.total, 10)
        self.assertEqual(os.listdir(self.dst), [])


if __name__ == '__main__':
    unittest.main()
