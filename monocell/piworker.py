"""Pi benchmark worker: pi to N decimal digits, integer arithmetic only.

Runs as its own process (`python3 -m monocell.piworker DIGITS`) so that every
core computes independently. Python's big integers hold the GIL, so threads
would not use more than one core.

Machin's formula: pi = 16*atan(1/5) - 4*atan(1/239). Integer fixed point, no
floating point anywhere, therefore the same input always gives the same digits
on every machine -- the result is comparable and verifiable.
"""
import json
import sys
import time

try:                                  # pi needs far more than the default 4300
    sys.set_int_max_str_digits(0)
except AttributeError:                # Python < 3.11 has no limit
    pass


def arctan_inv(x, one):
    """arctan(1/x) scaled by `one` (integer fixed point, truncated)."""
    term = one // x
    total = term
    x2 = x * x
    n = 1
    sign = 1
    while term:
        term //= x2
        n += 2
        sign = -sign
        total += sign * (term // n)
    return total


def pi_digits(digits):
    """The first `digits` decimal digits of pi, e.g. '3141592653...'."""
    if digits < 1:
        raise ValueError('digits must be >= 1')
    guard = 20                       # truncation head room for both series
    one = 10 ** (digits + guard)
    pi = 16 * arctan_inv(5, one) - 4 * arctan_inv(239, one)
    return str(pi)[:digits]


def checksum(text):
    """FNV-1a 32 bit: a wrong number cannot pass as a fast one."""
    h = 0x811c9dc5
    for ch in text.encode('ascii'):
        h = ((h ^ ch) * 0x01000193) & 0xffffffff
    return '%08x' % h


def main(argv):
    if len(argv) != 1:
        print('usage: python3 -m monocell.piworker DIGITS', file=sys.stderr)
        return 2
    digits = int(argv[0])
    t0 = time.perf_counter()
    text = pi_digits(digits)
    secs = time.perf_counter() - t0
    print(json.dumps({'digits': digits, 'secs': round(secs, 4),
                      'head': text[:20], 'tail': text[-10:],
                      'checksum': checksum(text)}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
