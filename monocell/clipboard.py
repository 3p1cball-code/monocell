"""The Monocell clipboard: a small file shared by the UI, the CLI and external tools."""
import os
import sys

from .util import CACHE_DIR, ensure_dirs


def path():
    return os.path.join(CACHE_DIR, 'clipboard')


def load(limit=1_000_000):
    try:
        with open(path(), encoding='utf-8', errors='replace') as f:
            return f.read(limit)
    except OSError:
        return ''


def save(text):
    try:
        ensure_dirs()
        p = path()
        tmp = p + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('-h', '--help'):
        print('usage: monocell-clip [-p|--path] [-c|--clear] [-s TEXT|--set TEXT]')
        print('       monocell-clip --set < file    # read the new clipboard from stdin')
        print('       monocell-clip                  # print the clipboard')
        return 0
    if argv and argv[0] in ('-p', '--path'):
        print(path())
        return 0
    if argv and argv[0] in ('-c', '--clear'):
        save('')
        return 0
    if argv and argv[0] in ('-s', '--set'):
        text = ' '.join(argv[1:]) if len(argv) > 1 else sys.stdin.read()
        if not save(text):
            print('monocell-clip: cannot write ' + path(), file=sys.stderr)
            return 1
        return 0
    if argv:
        print('monocell-clip: unknown option ' + argv[0], file=sys.stderr)
        return 2
    sys.stdout.write(load())
    return 0


if __name__ == '__main__':
    sys.exit(main())
