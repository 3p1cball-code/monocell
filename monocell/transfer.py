"""Copying and moving files with progress and a stop button (the file manager's paste).

`Transfer` does the work in whatever thread calls `run()`; the counters are plain
attributes the UI can read whenever it redraws. A cancel stops between blocks of
1 MB: the half-written file and a half-copied tree are removed again, files that
were already finished stay. A move on the same filesystem is a rename and costs
nothing at all (the sizes are still counted, so the bar does not lie afterwards).
"""
import errno
import os
import shutil

from .util import human_size

BLOCK = 1 << 20
SYNC_EVERY = 8 << 20          # fsync window on filesystems that flush on close
# Where close() waits for the writeback, a big file otherwise keeps the process in
# disk sleep after a quit (FAT with the 'flush' mount option did exactly that). On
# ext4/tmpfs close returns at once, and an fsync every 8 MB made a copy five times
# slower on the X61s (5.6 s instead of 0.9 s for 300 MB) - so sync only there.
FLUSH_FS = {'vfat', 'msdos', 'exfat', 'ntfs', 'ntfs3', 'fuseblk', 'iso9660', 'udf'}


def sync_interval(dest, mounts=None):
    """Bytes between fsyncs for this destination: SYNC_EVERY only where needed."""
    if mounts is None:
        try:
            with open('/proc/mounts') as f:
                mounts = f.read()
        except OSError:
            return SYNC_EVERY               # unknown: be careful
    path = os.path.abspath(dest)
    best, fstype, opts = '', '', ''
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        point = parts[1].replace('\\040', ' ')
        if (path == point or path.startswith(point.rstrip('/') + os.sep)) \
                and len(point) >= len(best):
            best, fstype, opts = point, parts[2], parts[3]
    if not best:
        return SYNC_EVERY
    if fstype in FLUSH_FS or 'flush' in opts.split(','):
        return SYNC_EVERY
    return 0


class Transfer:
    def __init__(self, sources, dest, move=False):
        self.sources = list(sources)
        self.dest = dest
        self.move = move
        self.state = 'idle'        # idle | planning | running | done | cancelled | error
        self.done = 0              # bytes written
        self.total = 0             # bytes planned
        self.files_done = 0
        self.files_total = 0
        self.current = ''
        self.error = ''
        self.skipped = 0           # fifos, sockets, devices
        self.cancelled = False
        self.per_source = {}       # path -> (bytes, files), for renames
        self.block_hook = None     # called after every block (tests, throttling)
        self.rename = os.rename    # replaceable: the cross-device path is testable
        self.sync_every = sync_interval(dest)

    # ---------------------------------------------------------- planning
    def plan(self):
        """Sum the sizes up front so the bar has a real denominator."""
        self.state = 'planning'
        total = files = 0
        for path in self.sources:
            size, n = self._measure(path)
            self.per_source[path] = (size, n)
            total += size
            files += n
        self.total, self.files_total = total, files
        return total, files

    def _measure(self, path):
        try:
            st = os.lstat(path)
        except OSError:
            return 0, 0
        if os.path.isdir(path) and not os.path.islink(path):
            size = files = 0
            for base, dirs, names in os.walk(path):
                for name in names:
                    try:
                        s = os.lstat(os.path.join(base, name))
                    except OSError:
                        continue
                    if os.path.isfile(os.path.join(base, name)):
                        size += s.st_size
                    files += 1
            return size, files
        return (st.st_size if os.path.isfile(path) else 0), 1

    def space_problem(self, reserve=1 << 20):
        """None when the sources fit, else a short reason - checked before the first byte."""
        if not self.total:
            return None
        try:
            free = shutil.disk_usage(self.dest).free
        except OSError:
            return None
        if self.total + reserve > free:
            return '%s needed, %s free' % (human_size(self.total), human_size(free))
        return None

    # ---------------------------------------------------------- doing it
    def cancel(self):
        if self.state in ('planning', 'running'):
            self.cancelled = True

    def run(self):
        self.state = 'running'
        for path in self.sources:
            if self.cancelled:
                break
            try:
                self._one(path)
            except OSError as e:
                self.error = str(e)
                self.state = 'error'
                return
        self.state = 'cancelled' if self.cancelled else 'done'

    def _one(self, path):
        name = os.path.basename(path.rstrip('/')) or path
        target = os.path.join(self.dest, name)
        if os.path.abspath(path) == os.path.abspath(target):
            base, ext = os.path.splitext(name)
            target = os.path.join(self.dest, '%s copy%s' % (base, ext))
            n = 2
            while os.path.lexists(target):
                target = os.path.join(self.dest, '%s copy %d%s' % (base, n, ext))
                n += 1
        existed = os.path.lexists(target)
        if self.move:
            self._move(path, target, existed)
        else:
            self._copy(path, target)
        if self.cancelled and not existed:
            self._remove(target)

    def _move(self, path, target, existed):
        try:
            self.rename(path, target)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            self._copy(path, target)          # another filesystem: real bytes
            if self.cancelled:
                return
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return
        size, files = self.per_source.get(path, (0, 1))
        self.done += size                      # a rename moves everything at once
        self.files_done += files
        self.current = os.path.basename(target)

    def _copy(self, src, dst):
        if self.cancelled:
            return
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
            self.files_done += 1
            return
        if os.path.isdir(src):
            os.makedirs(dst)                   # does not merge into an existing folder
            for name in sorted(os.listdir(src)):
                if self.cancelled:
                    return
                self._copy(os.path.join(src, name), os.path.join(dst, name))
            if not self.cancelled:
                try:
                    shutil.copystat(src, dst)
                except OSError:
                    pass
            return
        if not os.path.isfile(src):
            self.skipped += 1
            return
        self._copy_file(src, dst)

    def _copy_file(self, src, dst):
        self.current = os.path.basename(src)
        created = not os.path.lexists(dst)
        written = 0
        synced = 0
        try:
            # raw (unbuffered) on both sides: the kernel copy and the Python
            # fallback then share one file offset
            with open(src, 'rb', buffering=0) as fi, open(dst, 'wb', buffering=0) as fo:
                fast = hasattr(os, 'sendfile')
                while not self.cancelled:
                    if fast:
                        try:
                            n = os.sendfile(fo.fileno(), fi.fileno(), None, BLOCK)
                        except OSError as e:
                            if e.errno in (errno.EINVAL, errno.ENOSYS, errno.EXDEV,
                                           errno.EOPNOTSUPP, errno.EBADF):
                                fast = False       # e.g. two different filesystems
                                continue
                            raise
                        if not n:
                            break
                    else:
                        buf = fi.read(BLOCK)
                        if not buf:
                            break
                        fo.write(buf)
                        n = len(buf)
                    written += n
                    self.done += n
                    if self.sync_every and written - synced >= self.sync_every:
                        # FAT flushes every dirty page of the filesystem on close (mount
                        # option 'flush'); on a CF card in PIO mode that took minutes and
                        # kept the exited process alive. Sync while copying instead.
                        os.fsync(fo.fileno())
                        synced = written
                    if self.block_hook:
                        self.block_hook(self)
        except OSError:
            if written:
                self.done -= written
            try:
                os.remove(dst)
            except OSError:
                pass
            raise
        if self.cancelled:
            self.done -= written
            if created:
                self._remove(dst)
            return
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
        self.files_done += 1

    def _remove(self, path):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass

    # ---------------------------------------------------------- for the UI
    @property
    def active(self):
        return self.state in ('planning', 'running')

    @property
    def fraction(self):
        if not self.total:
            return 0.0
        return max(0.0, min(1.0, self.done / float(self.total)))

    def summary(self):
        return '%d file(s), %s' % (self.files_done, human_size(self.done))
