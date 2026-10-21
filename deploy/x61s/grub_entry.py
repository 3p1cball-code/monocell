#!/usr/bin/env python3
"""Clone the running kernel's normal GRUB entry and append systemd.unit=monocell.target."""
import argparse
import re
import sys


def blocks(lines):
    cur = None
    for line in lines:
        if line.startswith('menuentry '):
            cur = [line]
        elif cur is not None:
            cur.append(line)
            if line.strip() == '}':
                yield cur
                cur = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cfg', default='/boot/grub/grub.cfg')
    ap.add_argument('--kernel', default='')
    ap.add_argument('--name', default='MONOCELL - TUI')
    a = ap.parse_args()
    krel = a.kernel
    try:
        lines = open(a.cfg, encoding='utf-8', errors='replace').read().splitlines()
    except OSError as e:
        print('cannot read %s: %s' % (a.cfg, e), file=sys.stderr)
        return 2
    pick = None
    fallback = None
    for b in blocks(lines):
        text = '\n'.join(b)
        if 'recovery mode' in text.lower() or 'monocell' in text.lower():
            continue
        linux = [l for l in b if re.match(r'\s*linux\s', l)]
        if not linux:
            continue
        if fallback is None:
            fallback = b
        if krel and ('vmlinuz-' + krel) in linux[0]:
            pick = b
            break
    b = pick or fallback
    if b is None:
        print('no suitable GRUB entry found', file=sys.stderr)
        return 1
    out = []
    for i, line in enumerate(b):
        if i == 0:
            out.append('menuentry %r --class debian --class gnu-linux --class gnu --class os {' % a.name)
        elif re.match(r'\s*linux\s', line):
            if 'systemd.unit=' not in line:
                line = line.rstrip() + ' systemd.unit=monocell.target'
            out.append(line)
        else:
            out.append(line)
    print('\n'.join(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
