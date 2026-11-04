"""IBM Proprinter (PPDS) text output for documents.

The printer is a 24-pin, 80-column line printer: 10 cpi, 6 lpi, 66 lines per
form. Text goes through the printer's own font; emphasis uses ESC E / ESC F.
Images are already ASCII/block art in the rendered rows, so a monochrome
dot-matrix printer reproduces them exactly like the screen preview. A real
24-pin bit-image backend can be added later without changing the Writer.

Paper: 11 inch (66 lines at 6 lpi). Set the printer to the same form length or
change `page_lines` when calling encode.
"""
import subprocess

from .screen import BOLD


def _cp437(text):
    return text.encode('cp437', 'replace')


def _bold_runs(chars, attrs):
    runs = []
    cur_bold = None
    cur = []
    for i, ch in enumerate(chars):
        bold = bool(((attrs[i] if i < len(attrs) else 0) >> 8) & BOLD)
        if bold != cur_bold:
            if cur:
                runs.append((cur_bold, ''.join(cur)))
                cur = []
            cur_bold = bold
        cur.append(ch)
    if cur:
        runs.append((cur_bold, ''.join(cur)))
    return runs


def encode(rows, width=80, page_lines=66, init=True):
    """Rendered document rows -> PPDS bytes.

    rows: list of (chars, attrs) as produced by writer_doc.Renderer.
    """
    out = bytearray()
    if init:
        out += b'\x1b@'          # initialise
        out += b'\x12'           # 10 characters per inch
        out += b'\x1bA\x0c'      # line spacing 12/72 inch
        out += b'\x1bC\x42'      # form length 66 lines (11 inch)
        out += b'\x1b2'          # use the spacing set by ESC A
    lines = 0
    for row in rows:
        if isinstance(row, str):
            chars, attrs = row[:width], []
        else:
            chars, attrs = row[0][:width], row[1]
        for bold, part in _bold_runs(chars, attrs):
            if bold:
                out += b'\x1bE'
            out += _cp437(part)
            if bold:
                out += b'\x1bF'
        out += b'\r\n'
        lines += 1
        if lines % page_lines == 0:
            out += b'\f'
    if lines % page_lines:
        out += b'\f'
    return bytes(out)


def send(data, target):
    """Write to a device path or hand the raw data to a CUPS queue."""
    target = (target or '').strip()
    if not target:
        raise ValueError('no printer configured')
    if target.startswith('/'):
        with open(target, 'wb') as f:
            f.write(data)
        return
    try:
        p = subprocess.run(['lp', '-d', target, '-o', 'raw'], input=data,
                           capture_output=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError('lp is not installed')
    except subprocess.TimeoutExpired:
        raise RuntimeError('lp timed out')
    if p.returncode:
        msg = (p.stderr or p.stdout or b'lp failed').decode('utf-8', 'replace').strip()
        raise RuntimeError(msg or 'lp failed')
