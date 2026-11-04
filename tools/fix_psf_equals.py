#!/usr/bin/env python3
"""Repair the '=' glyph of a PSF console font (the 3270 font collapses it).

The IBM 3270 font draws '=' as two bars that hinting merges into one line at
16 px, so the converted PSF showed a single dash even for the equals sign.
This rewrites the glyph with two bars around the dash row. Works for PSF1 and
PSF2, plain and gzip-compressed.

usage: fix_psf_equals.py FONT.psf[.gz] [--check] [--rows 5,9] [--bits 0x7f]

--bits is the 8-pixel pattern of the first byte of a row (left aligned); the
first version of this script wrote it right aligned, into the padding of
9-pixel fonts, and the '=' became invisible.
"""
import argparse
import gzip
import struct
import sys


def read(path):
    raw = open(path, 'rb').read()
    data = gzip.decompress(raw) if path.endswith('.gz') else raw
    if data[:2] == b'\x36\x04':
        mode, height = data[2], data[3]
        count = 256 if mode & 1 else 512
        return ('psf1', data, 4, height, 8, count, height)
    magic, version, hdr, flags, length, charsize, height, width = struct.unpack('<IIIIIIII', data[:32])
    if magic != 0x864AB572:
        raise ValueError('not a PSF font: %s' % path)
    return ('psf2', data, hdr, height, width, length, charsize)


def glyph_rows(info, ch):
    kind, data, off, height, width, count, charsize = info
    nb = (width + 7) // 8
    g = data[off + ord(ch) * charsize: off + (ord(ch) + 1) * charsize]
    rows = []
    for r in range(height):
        row = 0
        for b in g[r * nb:(r + 1) * nb]:
            row = (row << 8) | b
        if row:
            rows.append((r, row))
    return rows


def visible(info, row):
    """Pixels of a row that are inside the glyph width (the rest is padding)."""
    width = info[4]
    nb = (width + 7) // 8
    mask = ((1 << width) - 1) << (nb * 8 - width)
    return row & mask


def patch(info, rows, bits):
    """bits: an 8-pixel pattern, left aligned like the first byte of a row."""
    kind, data, off, height, width, count, charsize = info
    nb = (width + 7) // 8
    bits = bits << (nb * 8 - 8)
    start = off + ord('=') * charsize
    glyph = bytearray(data[start:start + charsize])
    for r in range(height):
        val = bits if r in rows else 0
        chunk = val.to_bytes(nb, 'big')
        glyph[r * nb:(r + 1) * nb] = chunk
    out = bytearray(data)
    out[start:start + charsize] = glyph
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('font')
    ap.add_argument('--check', action='store_true', help='only report the glyph')
    ap.add_argument('--rows', default='5,9')
    ap.add_argument('--bits', default='0x7f')
    a = ap.parse_args()
    info = read(a.font)
    rows = glyph_rows(info, '=')
    print('%s: %s %dx%d  = at rows %s' % (a.font, info[0], info[4], info[3],
                                          [r for r, _ in rows]))
    if a.check:
        return 0 if len([r for r, v in rows if visible(info, v)]) >= 2 else 1
    want = sorted(int(v) for v in a.rows.split(','))
    bits = int(a.bits, 0)
    data = patch(info, want, bits)
    raw = gzip.compress(data, 9) if a.font.endswith('.gz') else data
    with open(a.font, 'wb') as f:
        f.write(raw)
    print('patched rows %s with %#x' % (want, bits))
    return 0


if __name__ == '__main__':
    sys.exit(main())
