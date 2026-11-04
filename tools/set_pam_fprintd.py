#!/usr/bin/env python3
"""Set the pam_fprintd retry count and timeout in a PAM config.

Debian ships `pam_fprintd.so max-tries=1 timeout=3` in some setups: a single
slightly diagonal swipe fails and the login falls through to the password.
This rewrites the options of an existing pam_fprintd line and keeps everything
else (leading whitespace, the [success=.. default=..] control field) untouched.

usage: set_pam_fprintd.py /etc/pam.d/common-auth [--tries 3] [--timeout 45]
exit: 0 changed or already set, 2 no pam_fprintd line
"""
import argparse
import re
import sys

LINE = re.compile(r'^(\s*auth\s+(?:\[[^\]]*\]|\w+)\s+pam_fprintd\.so)\b.*$', re.M)


def rewrite(text, tries, timeout):
    """(new_text, found); options on the pam_fprintd line are replaced."""
    def repl(m):
        return '%s max-tries=%d timeout=%d' % (m.group(1), tries, timeout)
    new, n = LINE.subn(repl, text)
    return new, bool(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pam_file')
    ap.add_argument('--tries', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=45)
    a = ap.parse_args()
    if a.tries < 1 or a.timeout < 1:
        print('tries and timeout must be positive', file=sys.stderr)
        return 2
    with open(a.pam_file) as f:
        text = f.read()
    new, found = rewrite(text, a.tries, a.timeout)
    if not found:
        print('no pam_fprintd line in %s' % a.pam_file, file=sys.stderr)
        return 2
    if new == text:
        print('pam_fprintd already set to max-tries=%d timeout=%d' % (a.tries, a.timeout))
        return 0
    with open(a.pam_file, 'w') as f:
        f.write(new)
    print('pam_fprintd: max-tries=%d timeout=%d s' % (a.tries, a.timeout))
    return 0


if __name__ == '__main__':
    sys.exit(main())
