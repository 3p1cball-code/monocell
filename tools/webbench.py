#!/usr/bin/env python3
"""Time the browser graphics mode per phase: start, navigate, first frame, scroll.

usage: webbench.py URL [URL ...]      (uses the normal monocell cache/profile)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from monocell import web  # noqa: E402


class Bench:
    def __init__(self):
        self.frames = 0
        self.first = None
        self.t0 = time.time()
        self.page = web.Page(on_event=self.on_event)

    def on_event(self, name, p):
        if name == 'Page.screencastFrame':
            self.frames += 1
            if self.first is None:
                self.first = time.time() - self.t0
            self.page.ack(p['sessionId'])

    def wait_first(self, timeout=20):
        t = time.time()
        while self.first is None and time.time() - t < timeout:
            time.sleep(0.02)
        return self.first


def main(urls):
    t = time.time()
    b = Bench()
    print('page start     %6.2fs' % (time.time() - t))
    b.page.start_screencast()
    for url in urls:
        b.first = None
        b.frames = 0
        b.t0 = time.time()
        b.page.navigate(url)
        first = b.wait_first()
        time.sleep(1.5)
        print('%-45s first frame %5.2fs  frames %3d  %s'
              % (url[:45], first or -1, b.frames, b.page.state()[1][:40]))
        b.page.wheel(500, 400, 600)
        time.sleep(1.0)
        print('%-45s scroll       frames %3d in 1 s' % ('', b.frames))
    b.page.close()
    web.shutdown_all()


if __name__ == '__main__':
    main(sys.argv[1:] or ['https://news.ycombinator.com/', 'https://en.wikipedia.org/wiki/Linux',
                          'https://www.tagesschau.de/', 'https://github.com/3p1cball-code/monocell'])
