"""Removable media: lsblk filtering, udisksctl mount/unmount, the shared snapshot."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import media  # noqa: E402


class P:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def part(name, fstype, label=None, mount=None, rm=False, size=10 ** 9, pkname=None):
    return {'name': name, 'path': '/dev/' + name, 'type': 'part', 'size': size,
            'fstype': fstype, 'label': label, 'mountpoint': mount, 'rm': rm,
            'hotplug': False, 'pkname': pkname or name.rstrip('0123456789')}


DATA = {'blockdevices': [
    {'name': 'sda', 'path': '/dev/sda', 'type': 'disk', 'size': 128 * 10 ** 9,
     'rm': False, 'hotplug': False, 'tran': 'sata', 'model': 'SAMSUNG SSD 830',
     'children': [
         part('sda1', 'ext4', mount='/', pkname='sda'),
         part('sda3', 'ext4', label='DATEN', mount='/mnt/daten', pkname='sda'),
         part('sda5', 'swap', mount='[SWAP]', pkname='sda'),
     ]},
    {'name': 'sdb', 'path': '/dev/sdb', 'type': 'disk', 'size': 1073741824,
     'rm': True, 'hotplug': False, 'tran': 'ata', 'model': 'IBM-DSCM-11000',
     'children': [part('sdb1', 'vfat', label='MICRODRIVE', rm=True,
                       size=1073741824, pkname='sdb')]},
    {'name': 'mmcblk0', 'path': '/dev/mmcblk0', 'type': 'disk', 'size': 32 * 10 ** 9,
     'rm': False, 'hotplug': False, 'tran': 'mmc', 'model': 'SD Card',
     'children': [part('mmcblk0p1', 'exfat', label='FOTOS',
                       mount='/media/ariedel/FOTOS', pkname='mmcblk0')]},
    {'name': 'sdc', 'path': '/dev/sdc', 'type': 'disk', 'size': 8 * 10 ** 9,
     'rm': False, 'hotplug': True, 'tran': 'usb', 'model': 'USB DISK',
     'children': []},
    {'name': 'sr0', 'path': '/dev/sr0', 'type': 'rom', 'size': 700 * 10 ** 6,
     'rm': True, 'hotplug': False, 'tran': 'ata', 'model': 'DVD'},
]}


class TestVolumes(unittest.TestCase):
    def vols(self, data=DATA, root='/dev/sda1'):
        return media.volumes(data=data, root=root)

    def test_only_removable_filesystems(self):
        vols = self.vols()
        self.assertEqual([v['device'] for v in vols], ['/dev/mmcblk0p1', '/dev/sdb1'])

    def test_microdrive_details(self):
        v = [v for v in self.vols() if v['device'] == '/dev/sdb1'][0]
        self.assertEqual(v['name'], 'MICRODRIVE')
        self.assertEqual(v['fstype'], 'vfat')
        self.assertEqual(v['mount'], '')
        self.assertEqual(v['model'], 'IBM-DSCM-11000')
        self.assertEqual(v['size'], 1073741824)
        self.assertTrue(v['size_text'].endswith('G') or v['size_text'].endswith('M'))

    def test_sd_card_is_kept_and_mountpoint_shown(self):
        v = [v for v in self.vols() if v['device'] == '/dev/mmcblk0p1'][0]
        self.assertEqual(v['name'], 'FOTOS')
        self.assertEqual(v['mount'], '/media/ariedel/FOTOS')

    def test_swap_and_cd_without_filesystem_are_left_out(self):
        devs = {v['device'] for v in self.vols()}
        self.assertNotIn('/dev/sda5', devs)          # swap
        self.assertNotIn('/dev/sr0', devs)           # no filesystem

    def test_system_disk_is_never_offered(self):
        devs = {v['device'] for v in self.vols()}
        self.assertNotIn('/dev/sda1', devs)
        self.assertNotIn('/dev/sda3', devs)          # a data partition on the system disk

    def test_without_findmnt_the_internal_disk_stays_out_anyway(self):
        self.assertEqual([v['device'] for v in self.vols(root='')],
                         ['/dev/mmcblk0p1', '/dev/sdb1'])

    def test_usb_disk_without_partitions(self):
        data = {'blockdevices': [
            {'name': 'sda', 'path': '/dev/sda', 'rm': False, 'tran': 'sata',
             'children': [part('sda1', 'ext4', mount='/')]},
            {'name': 'sdb', 'path': '/dev/sdb', 'type': 'disk', 'rm': False,
             'hotplug': True, 'tran': 'usb', 'fstype': 'vfat', 'label': 'STICK',
             'size': 4 * 10 ** 9}]}
        vols = media.volumes(data=data, root='/dev/sda1')
        self.assertEqual([v['device'] for v in vols], ['/dev/sdb'])
        self.assertEqual(vols[0]['name'], 'STICK')

    def test_broken_lsblk_output_is_empty_not_a_crash(self):
        def runner(cmd):
            return P(1, '', 'lsblk: not found')
        self.assertEqual(media.volumes(runner=runner), [])

    def test_name_falls_back_to_the_model(self):
        data = {'blockdevices': [
            {'name': 'sdb', 'path': '/dev/sdb', 'rm': True, 'tran': 'ata',
             'fstype': 'vfat', 'model': 'GENERIC USB', 'size': 10 ** 9}]}
        self.assertEqual(media.volumes(data=data)[0]['name'], 'GENERIC USB')


class TestMount(unittest.TestCase):
    def test_mount_returns_the_mountpoint(self):
        asked = []
        def runner(cmd):
            asked.append(cmd)
            return P(0, 'Mounted /dev/sdb1 at /media/ariedel/MICRODRIVE.\n')
        path, err = media.mount('/dev/sdb1', runner=runner)
        self.assertEqual((path, err), ('/media/ariedel/MICRODRIVE', ''))
        self.assertEqual(asked[0][:3], ['udisksctl', 'mount', '-b'])
        self.assertIn('--no-user-interaction', asked[0])

    def test_mount_error_is_reported(self):
        def runner(cmd):
            return P(1, '', 'Error mounting /dev/sdb1: GDBus.Error: Not authorized\n')
        path, err = media.mount('/dev/sdb1', runner=runner)
        self.assertEqual(path, '')
        self.assertIn('Not authorized', err)

    def test_mount_without_a_message_asks_lsblk(self):
        def runner(cmd):
            if cmd[0] == 'lsblk':
                return P(0, '{"blockdevices": []}')
            return P(0, 'mounted (quietly)')
        path, err = media.mount('/dev/sdb1', runner=runner)
        self.assertEqual(path, '')
        self.assertIn('mountpoint', err)

    def test_unmount(self):
        calls = []
        def ok(cmd):
            calls.append(cmd)
            return P(0, 'Unmounted /dev/sdb1.\n')
        self.assertEqual(media.unmount('/dev/sdb1', runner=ok), (True, ''))
        self.assertEqual(calls[0][:3], ['udisksctl', 'unmount', '-b'])

        def busy(cmd):
            return P(1, '', 'Error unmounting /dev/sdb1: target is busy\n')
        self.assertEqual(media.unmount('/dev/sdb1', runner=busy)[0], False)
        self.assertIn('busy', media.unmount('/dev/sdb1', runner=busy)[1])

    def test_first_error_line_is_short(self):
        p = P(1, '', '\nError mounting /dev/sdb1: ' + 'x' * 400 + '\n')
        self.assertLessEqual(len(media._first_error(p)), 120)


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        media.invalidate()

    def tearDown(self):
        media.invalidate()

    def test_snapshot_is_cached(self):
        calls = []
        def scan():
            calls.append(1)
            return ['a']
        self.assertEqual(media.snapshot(10.0, scanner=scan), ['a'])
        self.assertEqual(media.snapshot(10.0, scanner=scan), ['a'])
        self.assertEqual(len(calls), 2)                     # a scanner is always called
        self.assertEqual(media.snapshot(max_age=10.0), ['a'])
        self.assertEqual(media.snapshot(max_age=10.0), ['a'])
        self.assertEqual(len(calls), 2)

    def test_invalidate_forces_a_new_scan(self):
        self.assertEqual(media.snapshot(max_age=10.0, scanner=lambda: []), [])
        media.invalidate()
        self.assertEqual(media.snapshot(max_age=10.0, scanner=lambda: [1]), [1])


class TestInside(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(media.inside('/media/u/CD', '/media/u/CD'))
        self.assertTrue(media.inside('/media/u/CD/sub', '/media/u/CD/'))
        self.assertFalse(media.inside('/media/u/CD2', '/media/u/CD'))
        self.assertFalse(media.inside('/home/u', ''))


if __name__ == '__main__':
    unittest.main()
