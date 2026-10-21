"""Single-threaded event loop: fd readers, timers, thread hand-off.

Apps run concurrently: every window may own child processes (shell, ffmpeg,
headless browser) and timers; blocking work goes to worker threads whose
results are posted back into the loop.
"""
import heapq
import itertools
import os
import select
import threading
import time
import traceback

from .util import log


class Timer:
    __slots__ = ('when', 'cb', 'interval', 'cancelled', 'owner')

    def __init__(self, when, cb, interval=None, owner=None):
        self.when = when
        self.cb = cb
        self.interval = interval
        self.cancelled = False
        self.owner = owner

    def cancel(self):
        self.cancelled = True


class Loop:
    def __init__(self):
        self.readers = {}
        self.timers = []
        self.seq = itertools.count()
        self.running = True
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)
        os.set_blocking(self._w, False)
        self._calls = []
        self._lock = threading.Lock()
        self.on_error = None
        self.threads = 0

    def add_reader(self, fd, cb):
        self.readers[fd] = cb

    def remove_reader(self, fd):
        self.readers.pop(fd, None)

    def call_later(self, delay, cb, owner=None):
        t = Timer(time.monotonic() + delay, cb, None, owner)
        heapq.heappush(self.timers, (t.when, next(self.seq), t))
        return t

    def every(self, interval, cb, owner=None):
        t = Timer(time.monotonic() + interval, cb, interval, owner)
        heapq.heappush(self.timers, (t.when, next(self.seq), t))
        return t

    def cancel_owner(self, owner):
        for _, _, t in self.timers:
            if t.owner is owner:
                t.cancelled = True

    def call_soon_threadsafe(self, cb):
        with self._lock:
            self._calls.append(cb)
        try:
            os.write(self._w, b'.')
        except (BlockingIOError, OSError):
            pass

    def run_in_thread(self, fn, done=None, error=None):
        def worker():
            try:
                res = fn()
            except Exception as e:
                tb = traceback.format_exc()
                log('thread error', tb)
                if error:
                    self.call_soon_threadsafe(lambda e=e: error(e))
            else:
                if done:
                    self.call_soon_threadsafe(lambda: done(res))
            finally:
                self.threads -= 1
        self.threads += 1
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        return th

    def _safe(self, cb, *a):
        try:
            cb(*a)
        except Exception as e:
            tb = traceback.format_exc()
            log('callback error', tb)
            if self.on_error:
                self.on_error(e, tb)

    def run(self, idle):
        while self.running:
            now = time.monotonic()
            while self.timers and self.timers[0][0] <= now:
                _, _, t = heapq.heappop(self.timers)
                if t.cancelled:
                    continue
                if t.interval:
                    t.when = max(t.when + t.interval, now)
                    heapq.heappush(self.timers, (t.when, next(self.seq), t))
                self._safe(t.cb)
            timeout = idle()
            if self.timers:
                nt = max(0.0, self.timers[0][0] - time.monotonic())
                timeout = nt if timeout is None else min(timeout, nt)
            if timeout is None:
                timeout = 1.0
            fds = list(self.readers) + [self._r]
            try:
                r, _, _ = select.select(fds, [], [], timeout)
            except InterruptedError:
                continue
            except (OSError, ValueError):
                # a reader fd was closed behind our back: drop bad ones
                for fd in list(self.readers):
                    try:
                        os.fstat(fd)
                    except OSError:
                        self.readers.pop(fd, None)
                continue
            for fd in r:
                if fd == self._r:
                    try:
                        os.read(self._r, 4096)
                    except BlockingIOError:
                        pass
                    with self._lock:
                        calls, self._calls = self._calls, []
                    for cb in calls:
                        self._safe(cb)
                else:
                    cb = self.readers.get(fd)
                    if cb:
                        self._safe(cb, fd)
