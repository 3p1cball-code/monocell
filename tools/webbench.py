#!/usr/bin/env python3
"""Time the Chromium (CDP) engine per phase: navigate -> interactive -> complete -> extract.

usage: webbench.py URL [URL ...]      (uses the normal monocell cache/profile)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from monocell import web  # noqa: E402


def state(e):
    try:
        return e.evaluate('document.readyState', 5)
    except RuntimeError:
        return None


def main(urls):
    t = time.time()
    e = web.CDPEngine()
    print('engine start   %6.2fs' % (time.time() - t))
    for url in urls:
        t0 = time.time()
        doc = e.load(url)
        print('%-45s load() %5.2fs  blocks %4d' % (url[:45], time.time() - t0, len(doc['blocks'])))
    for url in urls:
        t0 = time.time()
        e.mark()
        e.call('Page.navigate', {'url': url})
        ti = tc = None
        while time.time() - t0 < 40:
            s = state(e)
            if s in ('interactive', 'complete') and ti is None:
                ti = time.time() - t0
            if s == 'complete':
                tc = time.time() - t0
                break
            time.sleep(0.05)
        t1 = time.time()
        doc = e.extract()
        tx = time.time() - t1
        size = len(json.dumps(doc))
        print('%-45s interactive %5.2fs  complete %5s  extract %5.2fs  blocks %4d  %6d B'
              % (url[:45], ti or -1, '%.2fs' % tc if tc else '>40s', tx, len(doc['blocks']), size))
    e.close()


if __name__ == '__main__':
    main(sys.argv[1:] or ['https://news.ycombinator.com/', 'https://en.wikipedia.org/wiki/Linux',
                          'https://www.tagesschau.de/', 'https://github.com/3p1cball-code/monocell'])
