"""Removable media: what is plugged in, and mounting it without a desktop session.

udisks2 does the actual work: it creates the mountpoint under /media/<user>, sets
the owner of vfat/ntfs filesystems and respects fstab. This module only answers
"which removable volumes exist" (from lsblk) and calls udisksctl; the two system
calls are injectable so the logic can be tested without a device.

Everything here is blocking; the window manager runs the scan in a worker thread
and mounts in one too (a CF microdrive takes a moment to spin up).
"""
import json
import os
import re
import shutil
import subprocess
import time

from .util import human_size

COLUMNS = ('NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINT,RM,HOTPLUG,TRAN,MODEL,PKNAME')
SKIP_FS = {'swap', 'crypto_LUKS', 'LVM2_member', 'linux_raid_member', 'zfs_member'}
REMOVABLE_TRANSPORT = {'usb', 'mmc', 'pcmcia', 'ieee1394'}


def _run(cmd, runner=None):
    if runner is not None:
        return runner(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def _first_error(p):
    text = (p.stderr or p.stdout or '').strip().splitlines()
    for line in reversed(text):
        line = line.strip()
        if line:
            return re.sub(r'^Error\s+\S+:\s*', '', line)[:120]
    return 'failed'


def lsblk(runner=None):
    p = _run(['lsblk', '-J', '-b', '-o', COLUMNS], runner)
    if p.returncode != 0:
        raise OSError(_first_error(p))
    return json.loads(p.stdout or '{}')


def root_source(runner=None):
    """The device the running system is on, e.g. /dev/sda1."""
    p = _run(['findmnt', '-n', '-o', 'SOURCE', '/'], runner)
    return (p.stdout or '').strip() if p.returncode == 0 else ''


def _entries(data):
    """Every disk and partition in the tree, with its disk as 'disk'."""
    out = []
    for disk in data.get('blockdevices') or []:
        out.append((disk, disk))
        for child in disk.get('children') or []:
            out.append((child, disk))
    return out


def volumes(data=None, root=None, runner=None):
    """Removable volumes that carry a filesystem, internal disks left out.

    A device counts as removable when the kernel says so (RM), when the port is
    hot pluggable, or when it hangs off a card/PC-card/USB bus - an SD card in a
    reader often reports rm=0 on the card. Anything on the system disk and the
    root filesystem itself is skipped, so nothing here can unmount the machine.
    """
    if data is None:
        try:
            data = lsblk(runner)
        except (OSError, ValueError):
            return []
    if root is None:
        root = root_source(runner)
    bypath = {e.get('path'): e for e, _ in _entries(data) if e.get('path')}
    root_parent = (bypath.get(root) or {}).get('pkname') or ''
    out = []
    for entry, disk in _entries(data):
        path = entry.get('path') or ''
        fstype = (entry.get('fstype') or '').strip()
        if not path or not fstype or fstype in SKIP_FS:
            continue
        if path == root or bypath.get(root) is entry:
            continue
        if root_parent and entry.get('pkname') == root_parent:
            continue
        removable = (disk.get('rm') or entry.get('rm') or disk.get('hotplug')
                     or entry.get('hotplug')
                     or (disk.get('tran') or '').strip() in REMOVABLE_TRANSPORT)
        if not removable:
            continue
        label = (entry.get('label') or '').strip()
        model = (disk.get('model') or entry.get('model') or '').strip()
        size = int(entry.get('size') or disk.get('size') or 0)
        out.append({
            'device': path,
            'name': label or model or os.path.basename(path),
            'label': label,
            'model': model,
            'size': size,
            'size_text': human_size(size) if size else '',
            'fstype': fstype,
            'mount': (entry.get('mountpoint') or '').strip(),
            'tran': (disk.get('tran') or '').strip(),
        })
    out.sort(key=lambda v: v['device'])
    return out


# ------------------------------------------------------------------ mounting
def have_udisks():
    return bool(shutil.which('udisksctl'))


def mount(device, runner=None):
    """Mount with udisks2. Returns (mountpoint, '') or ('', error)."""
    if runner is None and not have_udisks():
        return '', 'udisksctl not installed'
    p = _run(['udisksctl', 'mount', '-b', device, '--no-user-interaction'], runner)
    invalidate()
    if p.returncode != 0:
        return '', _first_error(p)
    m = re.search(r'\bat\s+(\S+?)\.?\s*$', (p.stdout or '').strip())
    if m:
        return m.group(1), ''
    for v in volumes(runner=runner):           # no message to parse: ask lsblk
        if v['device'] == device and v['mount']:
            return v['mount'], ''
    return '', 'mounted, but no mountpoint found'


def unmount(device, runner=None):
    """Returns (True, '') or (False, error)."""
    if runner is None and not have_udisks():
        return False, 'udisksctl not installed'
    p = _run(['udisksctl', 'unmount', '-b', device, '--no-user-interaction'], runner)
    invalidate()
    if p.returncode != 0:
        return False, _first_error(p)
    return True, ''


# ------------------------------------------------------------------ cache
_cache = {'at': -1.0, 'volumes': []}


def invalidate():
    _cache['at'] = -1.0


def snapshot(max_age=3.0, scanner=None):
    """The last scan if it is recent - the WM polls, Files just reads."""
    now = time.monotonic()
    if scanner is None and _cache['at'] >= 0 and now - _cache['at'] <= max_age:
        return _cache['volumes']
    vols = (scanner or volumes)()
    _cache['at'] = time.monotonic()
    _cache['volumes'] = vols
    return vols


def inside(path, mountpoint):
    if not mountpoint:
        return False
    path, mountpoint = os.path.abspath(path), os.path.abspath(mountpoint)
    return path == mountpoint or path.startswith(mountpoint.rstrip('/') + os.sep)
