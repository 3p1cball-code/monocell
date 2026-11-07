"""TRD server: mirror Monocell's cell buffer, input and sound over a loopback socket."""
import gzip
import hmac
import io
import json
import os
import queue
import re
import secrets
import select
import socket
import struct
import subprocess
import threading
import time

from . import util
from .term import Key, Mouse, Paste
from .theme import console_font_path

VERSION = 1
F_HELLO, F_FONT, F_META, F_CELLS, F_CURSOR, F_AUDIO, F_STATUS, F_ERROR = range(1, 9)
F_TEXT, F_GRAPHICS, F_IMAGE = 9, 10, 11
C_HELLO, C_KEY, C_MOUSE, C_PASTE, C_AUDIO, C_FULL, C_PING = range(10, 17)
C_SNAPSHOT = 18

AUDIO_RATE = 44100
AUDIO_CHANNELS = 2
GFX_FPS = 12
GFX_FPS_ORIGINAL = 6
FB_DEV = '/dev/fb0'

SERVER = None


def _psf_parse(data):
    if data[:2] == b'\x1f\x8b':
        try:
            data = gzip.decompress(data)
        except OSError:
            return None
    if data[:2] == b'\x36\x04':
        mode, charsize = data[2], data[3]
        glyphs = 512 if mode & 0x01 else 256
        start, width = 4, 8
        height = charsize
        table = data[4 + glyphs * charsize:] if mode & 0x02 else b''
        return glyphs, width, height, table, True
    if data[:4] == b'\x72\xb5\x4a\x86' and len(data) >= 32:
        _m, _v, headersize, flags, glyphs, charsize, height, width = \
            struct.unpack('<IIIIIIII', data[:32])
        if not charsize or not height or not width:
            return None
        table = data[headersize + glyphs * charsize:] if flags & 1 else b''
        return glyphs, width, height, table, False
    return None


def psf_table(table, glyphs, wide=False):
    out = {}
    i = 0
    glyph = 0
    if wide:
        while i + 1 < len(table) and glyph < glyphs:
            value = table[i] | (table[i + 1] << 8)
            i += 2
            if value == 0xFFFF:
                glyph += 1
                continue
            out.setdefault(value, glyph)
        return out
    while i < len(table) and glyph < glyphs:
        if table[i] == 0xFF:
            glyph += 1
            i += 1
            continue
        size = 1
        b = table[i]
        if b >> 5 == 6:
            size = 2
        elif b >> 4 == 14:
            size = 3
        elif b >> 3 == 30:
            size = 4
        try:
            ch = table[i:i + size].decode('utf-8')
        except (UnicodeDecodeError, ValueError):
            size = 1
        else:
            for c in ch:
                out.setdefault(ord(c), glyph)
        i += size
    return out


def _load_font(name):
    path = console_font_path(name)
    if not path:
        return None
    try:
        raw = open(path, 'rb').read()
    except OSError:
        return None
    parsed = _psf_parse(raw)
    if not parsed:
        return None
    glyphs, width, height, table, wide = parsed
    return {
        'path': path, 'name': os.path.basename(path), 'bytes': raw,
        'glyphs': glyphs, 'cell_w': width, 'cell_h': height,
        'table': psf_table(table, glyphs, wide),
    }


def _unimap_from_tty(tty):
    if not tty:
        return {}
    path = os.path.join(util.CACHE_DIR, 'trd-unimap-%d' % os.getpid())
    try:
        r = subprocess.run(['setfont', '-C', tty, '-ou', path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)
        if r.returncode != 0:
            return {}
        out = {}
        with open(path, 'r', errors='replace') as f:
            for line in f:
                parts = line.split()
                if len(parts) != 2 or not parts[0].startswith('0x') or not parts[1].startswith('U+'):
                    continue
                try:
                    pos = int(parts[0], 16)
                    cp = int(parts[1][2:], 16)
                except ValueError:
                    continue
                out[cp] = pos
        return out
    except (OSError, subprocess.SubprocessError):
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _unimap_for_font(font, tty):
    out = _unimap_from_tty(tty)
    if out:
        return out
    if font and font['table']:
        return font['table']
    fallback = _load_font('Lat15-3270.psf.gz')
    if fallback and fallback['table']:
        return fallback['table']
    return {}


def audio_chunk(allowed, have):
    """Bytes to take out of the capture buffer for this tick.

    Only whole s16le stereo frames: `os.read` returns whatever length the pipe
    has, and an odd byte count would shift every following sample by one byte -
    the player then turns the whole stream into static."""
    return max(0, min(allowed, have)) & ~3


def _monitor_source():
    try:
        sink = subprocess.run(['pactl', 'get-default-sink'], capture_output=True,
                              text=True, timeout=4).stdout.strip()
        r = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True,
                           text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    fallback = None
    for line in r.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        if sink and parts[1] == sink + '.monitor':
            return parts[0]
        if parts[1].endswith('.monitor') and fallback is None:
            fallback = parts[0]
    return fallback



def _fb_geometry():
    try:
        with open('/sys/class/graphics/fb0/virtual_size') as f:
            w, h = [int(v) for v in f.read().strip().split(',')]
        with open('/sys/class/graphics/fb0/bits_per_pixel') as f:
            bpp = int(f.read().strip())
        try:
            with open('/sys/class/graphics/fb0/stride') as f:
                stride = int(f.read().strip())
        except OSError:
            stride = w * bpp // 8
        if w <= 0 or h <= 0 or bpp not in (16, 24, 32):
            return None
        return w, h, bpp, stride
    except (OSError, ValueError):
        return None


def _read_fb():
    geo = _fb_geometry()
    if not geo:
        return None
    w, h, bpp, stride = geo
    try:
        with open(FB_DEV, 'rb', buffering=0) as f:
            data = f.read(stride * h)
    except OSError:
        return None
    if len(data) < stride * h:
        return None
    return data, w, h, bpp, stride


def _fb_image(data, w, h, bpp, stride):
    try:
        from PIL import Image
    except ImportError:
        return None
    raw = {32: 'BGRX', 24: 'BGR'}.get(bpp)
    if not raw:
        return None
    try:
        return Image.frombytes('RGB', (w, h), data, 'raw', raw, stride, 1)
    except Exception:
        return None


def _fb_jpeg(data, w, h, bpp, stride):
    img = _fb_image(data, w, h, bpp, stride)
    if img is None:
        return None
    try:
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=90)
        return buf.getvalue()
    except Exception:
        return None


def _fb_png(data, w, h, bpp, stride):
    img = _fb_image(data, w, h, bpp, stride)
    if img is None:
        return None
    try:
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()
    except Exception:
        return None


def _jpeg_image(img, quality=90):
    try:
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=quality)
        return buf.getvalue()
    except Exception:
        return None


def _uniform_blocks(img, n, cw, ch, x0, y0):
    try:
        pix = img.load()
    except Exception:
        return False
    step_x = max(1, cw // 16)
    step_y = max(1, ch // 12)
    for j in range(0, ch, step_y):
        for i in range(0, cw, step_x):
            bx = x0 + i * n
            by = y0 + j * n
            base = pix[bx, by]
            for dy in range(1, n):
                for dx in range(1, n):
                    if pix[bx + dx, by + dy] != base:
                        return False
    return True


def _osd_blob(runs):
    out = bytearray(struct.pack('<I', len(runs)))
    for off, data in runs:
        out += struct.pack('<II', off, len(data))
        out += data
    return bytes(out)


class Trd:
    def __init__(self, loop, wm, term, cfg, port=0, meta_path=None):
        self.loop = loop
        self.wm = wm
        self.term = term
        self.cfg = cfg
        self.theme = wm.theme
        self.port = int(port or 0)
        self.meta_path = os.path.abspath(os.path.expanduser(
            meta_path or os.path.join(util.CACHE_DIR, 'trd.json')))
        self.stopped = False
        os.makedirs(util.CACHE_DIR, exist_ok=True)
        self.token = secrets.token_hex(24)
        self.listener = None
        self.conn = None
        self.sendq = None
        self.writer = None
        self.reader = None
        self.accept = None
        self.timer = None
        self.audio_thread = None
        self.audio_proc = None
        self.audio_stop = None
        self.audio_on = False
        self.gfx_thread = None
        self.gfx_stop = None
        self.gfx_snap = None
        self.gfx_cache = None
        self.gfx_fb_time = 0.0
        self.snapshot_requested = False
        self.force_full = True
        self.last_ch = None
        self.last_at = None
        self.last_wm_frame = None
        self.sent_cursor = 'init'
        self.sent_gfx = None
        self.glyph_cache = {}
        self.lock = threading.Lock()
        try:
            tty = os.ttyname(0)
        except OSError:
            tty = ''
        self.tty = tty if re.match(r'^/dev/tty\d+$', tty or '') else None
        self.font = _load_font(cfg.get('font'))
        self.unimap = _unimap_for_font(self.font, self.tty)
        self.sig = None
        self.meta = None
        self._refresh_meta(initial=True)

    def log(self, msg):
        util.log('trd: ' + msg)

    def _palette(self):
        rgb = self.theme.rgb
        bgmap = getattr(self.term, 'bgmap', list(range(16)))
        fg = [list(rgb[i]) for i in range(16)]
        bg = [list(rgb[bgmap[i]]) for i in range(16)]
        return fg, bg

    def _signature(self):
        return (self.term.w, self.term.h, self.cfg.get('font'), self.term.mode,
                tuple(tuple(c) for c in self.theme.rgb),
                tuple(getattr(self.term, 'bgmap', ())))

    def _refresh_meta(self, initial=False):
        sig = self._signature()
        font_name = str(self.cfg.get('font') or '')
        if not self.font or self.font['name'] != os.path.basename(font_name):
            self.font = _load_font(font_name)
            self.unimap = _unimap_for_font(self.font, self.tty)
            self.glyph_cache = {}
        if not self.font:
            self.font = _load_font('Lat15-3270.psf.gz')
            self.unimap = _unimap_for_font(self.font, self.tty)
        cw = self.font['cell_w'] if self.font else 8
        ch = self.font['cell_h'] if self.font else 16
        fg, bg = self._palette()
        cursor = self.term.cursor
        self.meta = {
            'version': VERSION,
            'app': 'monocell',
            'title': socket.gethostname().split('.')[0],
            'tty': self.tty,
            'cols': self.term.w,
            'rows': self.term.h,
            'cell_w': cw,
            'cell_h': ch,
            'glyphs': self.font['glyphs'] if self.font else 256,
            'font': self.font['name'] if self.font else '',
            'mode': self.term.mode,
            'fg': fg,
            'bg': bg,
            'audio': {'rate': AUDIO_RATE, 'channels': AUDIO_CHANNELS, 'format': 's16le'},
            'gfx': bool(self.wm.gfx),
            'cursor': list(cursor) if cursor else None,
        }
        changed = not initial and sig != self.sig
        self.sig = sig
        if changed and self.conn:
            self._send(F_META, json.dumps(self.meta).encode())
            if self.font:
                self._send(F_FONT, self.font['bytes'])
            self._reset_frames()

    def _reset_frames(self):
        self.last_ch = None
        self.last_at = None
        self.force_full = True

    def _write_meta(self):
        data = {'port': self.port, 'token': self.token, 'pid': os.getpid(),
                'tty': self.tty, 'font': self.meta.get('font') if self.meta else ''}
        directory = os.path.dirname(self.meta_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.meta_path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(data).encode())
        finally:
            os.close(fd)
        os.replace(tmp, self.meta_path)

    def _remove_meta(self):
        try:
            with open(self.meta_path, 'r') as f:
                data = json.load(f)
            if data.get('pid') == os.getpid() and data.get('token') == self.token:
                os.unlink(self.meta_path)
        except (OSError, ValueError):
            pass

    def start(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(('127.0.0.1', self.port))
            srv.listen(4)
            srv.settimeout(1.0)
        except OSError as e:
            self.log('listen failed: %s' % e)
            return False
        self.listener = srv
        self.port = srv.getsockname()[1]
        try:
            self._write_meta()
        except OSError as e:
            self.log('meta file failed: %s' % e)
        self.accept = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept.start()
        self.timer = self.loop.every(1 / 24.0, self._tick)
        self.log('listening on 127.0.0.1:%d' % self.port)
        return True

    def stop(self):
        self.stopped = True
        if self.timer:
            self.timer.cancel()
        self._drop_conn()
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        self._remove_meta()
        self.audio_on = False
        self._stop_audio()

    def _accept_loop(self):
        while not self.stopped:
            try:
                conn, _addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                busy = self.conn is not None
            if busy:
                try:
                    conn.sendall(struct.pack('<BI', F_ERROR, 4) + b'busy')
                except OSError:
                    pass
                conn.close()
                continue
            if not self._handshake(conn):
                conn.close()
                continue
            self._start_conn(conn)

    def _recv_exact(self, conn, n):
        data = b''
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _recv_frame(self, conn):
        head = self._recv_exact(conn, 5)
        if head is None:
            return None, None
        kind, length = struct.unpack('<BI', head)
        payload = self._recv_exact(conn, length) if length else b''
        if payload is None:
            return None, None
        return kind, payload

    def _handshake(self, conn):
        conn.settimeout(6.0)
        try:
            kind, payload = self._recv_frame(conn)
            if kind != C_HELLO:
                return False
            token = payload.decode('utf-8', 'replace').strip()
            if not hmac.compare_digest(token, self.token):
                try:
                    msg = b'auth'
                    conn.sendall(struct.pack('<BI', F_ERROR, len(msg)) + msg)
                except OSError:
                    pass
                return False
        except OSError:
            return False
        conn.settimeout(None)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return True

    def _start_conn(self, conn):
        with self.lock:
            self.conn = conn
        self.sendq = queue.Queue(maxsize=256)
        self.writer = threading.Thread(target=self._writer_loop, args=(conn,), daemon=True)
        self.writer.start()
        self.reader = threading.Thread(target=self._reader_loop, args=(conn,), daemon=True)
        self.reader.start()
        self.force_full = True
        self.sent_cursor = 'init'
        self.sent_gfx = None
        self.snapshot_requested = False
        self._send(F_HELLO, json.dumps(self.meta).encode())
        if self.font:
            self._send(F_FONT, self.font['bytes'])
        self.log('client connected')

    def _drop_conn(self):
        with self.lock:
            conn, self.conn = self.conn, None
            q, self.sendq = self.sendq, None
        if q:
            try:
                q.put_nowait(None)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
        if conn:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            self.log('client disconnected')
        self.audio_on = False
        self._stop_audio()
        self._stop_gfx()

    def _send_audio(self, payload):
        # MONOCELL_TRD_AUDIO_DUMP writes what goes to the client (debugging):
        # compare with MONOCELL_TRD_RAW_DUMP (what parec delivered)
        dump = os.environ.get('MONOCELL_TRD_AUDIO_DUMP')
        if dump:
            try:
                with open(dump, 'ab') as f:
                    f.write(payload)
            except OSError:
                pass
        self._send(F_AUDIO, payload)

    def _send(self, kind, payload):
        q = self.sendq
        if q is None:
            return
        try:
            q.put_nowait((kind, payload))
        except queue.Full:
            if kind == F_AUDIO:
                return
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait((kind, payload))
            except queue.Full:
                pass

    def _writer_loop(self, conn):
        while True:
            item = self.sendq.get()
            if item is None:
                return
            kind, payload = item
            try:
                conn.sendall(struct.pack('<BI', kind, len(payload)) + payload)
            except OSError:
                break
        self._drop_conn()

    def _reader_loop(self, conn):
        try:
            while self.conn is conn and not self.stopped:
                kind, payload = self._recv_frame(conn)
                if kind is None:
                    break
                self._handle_client(kind, payload)
        except OSError:
            pass
        finally:
            if self.conn is conn:
                self._drop_conn()

    def _handle_client(self, kind, payload):
        if kind == C_KEY:
            text = payload.decode('utf-8', 'replace')
            self.loop.call_soon_threadsafe(lambda: self._inject_key(text))
        elif kind == C_MOUSE:
            if len(payload) < 12:
                return
            k, x, y, button, mods, clicks = struct.unpack('<BffBBH', payload[:14])
            self.loop.call_soon_threadsafe(lambda: self._inject_mouse(k, x, y, button, mods, clicks))
        elif kind == C_PASTE:
            text = payload.decode('utf-8', 'replace')
            self.loop.call_soon_threadsafe(lambda: self._inject_paste(text))
        elif kind == C_AUDIO:
            on = bool(payload and payload[0])
            self.audio_on = on
            if on:
                self._start_audio()
            else:
                self._stop_audio()
        elif kind == C_FULL:
            self.force_full = True
        elif kind == C_SNAPSHOT:
            self.snapshot_requested = True
        elif kind == C_PING:
            pass

    def _inject_key(self, text):
        if not text:
            return
        ev = Key(text, text) if len(text) == 1 else Key(text)
        self.wm.handle(ev)

    def _inject_mouse(self, kind, x, y, button, mods, clicks):
        names = ('down', 'up', 'drag', 'move', 'wheelup', 'wheeldown')
        name = names[kind] if 0 <= kind < len(names) else 'move'
        m = frozenset(n for bit, n in ((1, 'shift'), (2, 'alt'), (4, 'ctrl')) if mods & bit)
        if not self.wm.gfx:
            x, y = int(round(x)), int(round(y))
        self.wm.handle(Mouse(name, x, y, button, m, max(1, clicks)))

    def _inject_paste(self, text):
        if text:
            self.wm.handle(Paste(text))

    def _glyph(self, ch):
        pos = self.glyph_cache.get(ch)
        if pos is not None:
            return pos
        cp = ord(ch[0]) if ch else 32
        limit = self.font['glyphs'] if self.font else 256
        found = self.unimap.get(cp)
        if found is None or found >= limit:
            found = self.unimap.get(63)
        if found is None or found >= limit:
            found = cp if cp < limit else (63 if limit > 63 else 0)
        self.glyph_cache[ch] = found
        return found

    def _tick(self):
        if self.stopped:
            return
        self._refresh_meta()
        if not self.conn:
            return
        if self.force_full:
            self.last_ch = None
            self.last_at = None
        gfx = bool(self.wm.gfx)
        if gfx:
            self._snapshot_gfx_state()
        else:
            self.gfx_snap = None
        if self.snapshot_requested:
            self.snapshot_requested = False
            if gfx:
                threading.Thread(target=self._snapshot_gfx, daemon=True).start()
            else:
                self._snapshot_text()
        if gfx != self.sent_gfx:
            self.sent_gfx = gfx
            self._send(F_STATUS, struct.pack('<B', 1 if gfx else 0))
            if gfx:
                self._start_gfx()
            else:
                self._stop_gfx()
        cursor = None if gfx else self.term.cursor
        if cursor != self.sent_cursor:
            self.sent_cursor = cursor
            if cursor:
                self._send(F_CURSOR, struct.pack('<hh', int(cursor[0]), int(cursor[1])))
            else:
                self._send(F_CURSOR, struct.pack('<hh', -1, -1))
        if gfx:
            return
        frame = self.wm.last_frame
        if not self.force_full and frame == self.last_wm_frame:
            return
        self.last_wm_frame = frame
        self._scan()

    def _snapshot_text(self):
        rows = [''.join(row) for row in self.term.back.ch]
        data = {'cols': self.term.w, 'rows': self.term.h, 'lines': rows}
        self._send(F_TEXT, json.dumps(data).encode())

    def _snapshot_gfx(self):
        fb = _read_fb()
        if not fb:
            self._send(F_ERROR, b'screenshot: kein Framebuffer')
            return
        data, w, h, bpp, stride = fb
        png = _fb_png(data, w, h, bpp, stride)
        if not png:
            self._send(F_ERROR, b'screenshot: kein Bild')
            return
        self._send(F_IMAGE, struct.pack('<HH', w, h) + png)

    def _gfx_session(self):
        obj = self.wm.gfx
        for _ in range(4):
            if obj is None:
                return None
            if (hasattr(obj, 'osd_runs') and hasattr(obj, 'screen')
                    and hasattr(obj, 'frame') and hasattr(obj, 'fb')):
                return obj
            obj = getattr(obj, 'gfx', None) or getattr(obj, 'sess', None) or \
                getattr(obj, 'session', None)
        return None

    def _snapshot_gfx_state(self):
        sess = self._gfx_session()
        if sess is not None and getattr(sess, 'frame', None) is not None:
            try:
                osd = sess.osd_runs()
            except Exception:
                osd = []
            fb = getattr(sess, 'fb', None)
            screen = tuple(getattr(sess, 'screen', (0, 0)))
            bpp = int(getattr(fb, 'bpp', 4) or 4)
            stride = int(getattr(fb, 'stride', 0) or 0)
            if screen[0] and screen[1] and stride:
                self.gfx_snap = (sess.frame, screen, bpp, stride, osd)
                return
        now = time.monotonic()
        if now - self.gfx_fb_time < 0.1:
            return
        self.gfx_fb_time = now
        fb = _read_fb()
        if fb:
            data, w, h, bpp, stride = fb
            self.gfx_snap = (data, (w, h), bpp, stride, [])
        else:
            self.gfx_snap = None

    def _gfx_payload(self, frame, screen, bpp, stride, osd):
        w, h = screen
        img = _fb_image(frame, w, h, bpp, stride)
        if img is None:
            return None
        for n in (4, 3, 2):
            cw, ch = max(1, w // n), max(1, h // n)
            uw, uh = cw * n, ch * n
            if uw > w or uh > h:
                continue
            if _uniform_blocks(img, n, cw, ch, (w - uw) // 2, (h - uh) // 2):
                x0, y0 = (w - uw) // 2, (h - uh) // 2
                try:
                    from PIL import Image
                    small = img.crop((x0, y0, x0 + uw, y0 + uh)).resize(
                        (cw, ch), Image.NEAREST)
                except Exception:
                    return None
                jpeg = _jpeg_image(small, 92)
                if jpeg:
                    return jpeg, cw, ch, x0, y0, n
                return None
        jpeg = _jpeg_image(img, 85)
        if jpeg:
            return jpeg, w, h, 0, 0, 1
        return None

    def _start_gfx(self):
        if self.gfx_thread and self.gfx_thread.is_alive():
            return
        self.gfx_stop = threading.Event()
        self.gfx_thread = threading.Thread(target=self._gfx_loop, daemon=True)
        self.gfx_thread.start()

    def _stop_gfx(self):
        if self.gfx_stop:
            self.gfx_stop.set()
        self.gfx_thread = None

    def _gfx_loop(self):
        stop = self.gfx_stop
        if not stop:
            return
        self.log('graphics stream started')
        fails = 0
        last_frame = object()
        last_osd = object()
        geometry = None
        jpeg = None
        small = None
        while not stop.is_set() and self.conn:
            t0 = time.monotonic()
            snap = self.gfx_snap
            if snap is None:
                stop.wait(0.1)
                continue
            frame, screen, bpp, stride, osd = snap
            if frame is last_frame and osd == last_osd and small is not None:
                stop.wait(1.0 / GFX_FPS)
                continue
            if frame is last_frame and small is not None and geometry == (screen, bpp, stride):
                jpeg, cw, ch, x0, y0, n = small
            else:
                out = self._gfx_payload(frame, screen, bpp, stride, osd)
                if out is None:
                    fails += 1
                    if fails >= 3:
                        self.log('graphics stream unavailable')
                        break
                    stop.wait(0.2)
                    continue
                fails = 0
                jpeg, cw, ch, x0, y0, n = out
                small = (jpeg, cw, ch, x0, y0, n)
                geometry = (screen, bpp, stride)
                last_frame = frame
            w, h = screen
            osd_data = _osd_blob(osd)
            header = struct.pack('<HHHHHHBBI', w, h, cw, ch, x0, y0, n,
                                 max(1, bpp // 8), stride)
            header += struct.pack('<II', len(jpeg), len(osd_data))
            self._send(F_GRAPHICS, header + jpeg + osd_data)
            last_osd = list(osd)
            interval = 1.0 / GFX_FPS if n > 1 else 1.0 / GFX_FPS_ORIGINAL
            dt = time.monotonic() - t0
            if dt < interval:
                stop.wait(interval - dt)
        self.log('graphics stream stopped')

    def _scan(self):
        back = self.term.back
        w, h = self.term.w, self.term.h
        chs, ats = back.ch, back.at
        if self.last_ch is None or self.last_at is None or \
                len(self.last_ch) != h or (h and len(self.last_ch[0]) != w):
            self.last_ch = None
            self.last_at = None
        full = self.last_ch is None
        out = bytearray(4)
        count = 0
        for y in range(h):
            rowc = chs[y]
            rowa = ats[y]
            if full:
                lc = la = None
            else:
                lc = self.last_ch[y]
                la = self.last_at[y]
            for x in range(w):
                c = rowc[x]
                a = rowa[x]
                if not full and c == lc[x] and a == la[x]:
                    continue
                g = self._glyph(c)
                out += struct.pack('<HHHBB', x, y, g, a & 15, (a >> 4) & 15)
                count += 1
        if count:
            struct.pack_into('<I', out, 0, count)
            self._send(F_CELLS, bytes(out))
        if count or full:
            self.last_ch = [row[:] for row in chs]
            self.last_at = [row[:] for row in ats]
        self.force_full = False

    def _start_audio(self):
        if self.audio_thread and self.audio_thread.is_alive() and not self.audio_stop.is_set():
            return
        if not self.audio_on:
            return
        self.audio_stop = threading.Event()
        self.audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.audio_thread.start()

    def _stop_audio(self):
        if self.audio_stop:
            self.audio_stop.set()
        self.audio_thread = None
        proc, self.audio_proc = self.audio_proc, None
        if proc:
            try:
                proc.terminate()
            except OSError:
                pass

    def _audio_loop(self):
        stop = self.audio_stop
        rate = AUDIO_RATE * AUDIO_CHANNELS * 2
        silence_step = max(1, rate // 50)
        start = time.monotonic()
        sent = 0
        buf = bytearray()
        last_data = time.monotonic()
        proc = None
        retry = 0.0
        while not stop.is_set() and self.conn:
            now = time.monotonic()
            if proc is None and now >= retry:
                idx = _monitor_source()
                if idx is not None:
                    cmd = ['parec', '--raw', '--format=s16le', '--rate=%d' % AUDIO_RATE,
                           '--channels=%d' % AUDIO_CHANNELS, '--latency-msec=80', '-d', idx]
                    try:
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                stdin=subprocess.DEVNULL, bufsize=0)
                        self.audio_proc = proc
                        self.log('audio capture started: -d %s' % idx)
                    except OSError:
                        proc = None
                retry = now + 2.0
            if proc is not None:
                try:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.0)
                except (OSError, ValueError):
                    ready = []
                if ready:
                    try:
                        data = os.read(proc.stdout.fileno(), 65536)
                    except OSError:
                        data = b''
                    if data:
                        last_data = now
                        _dump = os.environ.get('MONOCELL_TRD_RAW_DUMP')
                        if _dump:
                            try:
                                with open(_dump, 'ab') as _f:
                                    _f.write(data)
                            except OSError:
                                pass
                        buf.extend(data)
                        cap = rate * 2
                        if len(buf) > cap:
                            drop = len(buf) - cap
                            drop -= drop % 4          # never cut a sample in half
                            del buf[:drop]
                    else:
                        if self.audio_proc is proc:
                            self.audio_proc = None
                        try:
                            proc.terminate()
                            proc.wait(timeout=2)
                        except (OSError, subprocess.SubprocessError):
                            pass
                        proc = None
                        self.log('audio capture stopped')
                        retry = time.monotonic() + 2.0
            now = time.monotonic()
            allowed = int((now - start) * rate) - sent
            if allowed > 0:
                if buf:
                    # whole frames only: the client plays raw s16le stereo, an odd
                    # byte count would shift every following sample by one byte and
                    # the sound turns into static (os.read returns any length)
                    n = audio_chunk(allowed, len(buf))
                    if n:
                        self._send_audio(bytes(buf[:n]))
                        del buf[:n]
                        sent += n
                        allowed -= n
                if allowed > 0 and not buf and now - last_data > 0.4:
                    # the capture is really quiet/stalled: keep the clock with
                    # silence. While it is only *late* (parec delivers in bursts of
                    # ~80 ms) we let the stream lag instead - padding here would cut
                    # holes into the audio every few milliseconds.
                    while allowed > 0:
                        n = min(allowed, silence_step)
                        self._send_audio(b'\0' * n)
                        sent += n
                        allowed -= n
            stop.wait(0.01)
        if proc is not None:
            if self.audio_proc is proc:
                self.audio_proc = None
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        self.log('audio capture stopped')


def start(loop, wm, term, cfg, port=0, meta_path=None):
    global SERVER
    trd = Trd(loop, wm, term, cfg, port=port, meta_path=meta_path)
    trd.start()
    SERVER = trd
    return trd


def stop():
    global SERVER
    if SERVER:
        SERVER.stop()
    SERVER = None
