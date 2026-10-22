"""Audio pipeline: ffmpeg decodes to PCM, monocell paces it into a sink (pw-cat / pacat / aplay).

Pacing the writes ourselves keeps pause/seek instant, gives an exact playback clock
(used as master clock by the video player) and the samples for the visualiser.

The clock counts bytes written, not wall-clock time, so it follows the sound card
instead of drifting away from it.  Two known delays sit between "written" and
"heard": the lead we keep queued ahead of the wall clock, and the buffer the sink
itself asks the sound server for.  Both are subtracted in :meth:`AudioPlayer.position`,
which is what keeps picture and sound together.  A sink that buffers for half a
second (aplay's default) therefore no longer drags the audio behind the video.
"""
import json
import math
import os
import subprocess
import time
from array import array

from .util import which

RATE = 44100
CH = 2
BPS = RATE * CH * 2          # bytes per second
LEAD = 0.15                  # seconds written ahead of the wall clock

# Request small device buffers: whatever the sink holds is audible delay.  These
# are the values the clock assumes; a sink may use a little more, which is why
# the video player also offers a manual A/V offset.
SINK_LATENCY = {
    'pw-cat': 0.06,
    'pacat': 0.06,
    'aplay': 0.12,
}

SINKS = {
    # --raw is essential: without it pw-cat treats '-' as a sound file and dies
    'pw-cat': ['pw-cat', '--playback', '--raw', '--format', 's16', '--rate', str(RATE),
               '--channels', str(CH), '--latency=60ms', '-'],
    'pacat': ['pacat', '--raw', '--format=s16le', '--rate=%d' % RATE, '--channels=%d' % CH,
              '--latency-msec=60'],
    'aplay': ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', str(RATE), '-c', str(CH),
              '-B', '120000', '-F', '40000'],
}


def playing_position(written, elapsed, buffered, offset=0.0):
    """Media time of the sample that is leaving the speakers right now.

    ``written``  bytes handed to the sink so far
    ``elapsed``  seconds since playback started (wall clock)
    ``buffered`` seconds of audio still queued in pipe, process and device
    """
    if written <= 0:
        return offset
    return offset + max(0.0, min(written / BPS - buffered, elapsed))


def _available(name):
    if not which(name):
        return False
    rt = '/run/user/%d' % os.getuid()
    if name == 'pw-cat' and not os.path.exists(rt + '/pipewire-0'):
        return False
    if name == 'pacat' and not (os.path.exists(rt + '/pulse/native') or os.path.exists(rt + '/pipewire-0')):
        return False
    return True


def pick_sinks(pref='auto'):
    """Ordered list of usable sinks; the preferred one first.

    pacat comes before pw-cat: where both exist the Pulse/PipeWire compat layer is
    the one that knows the configured default sink, while a bare native client can
    end up with "no target node available".
    """
    if os.environ.get('MONOCELL_NOAUDIO'):
        return []
    rt = '/run/user/%d' % os.getuid()
    order = ['pacat', 'pw-cat', 'aplay'] if os.path.exists(rt + '/pulse/native') else \
            ['pw-cat', 'pacat', 'aplay']
    if pref in order:
        order.remove(pref)
        order.insert(0, pref)
    return [n for n in order if _available(n)]


def pick_sink(pref='auto'):
    sinks = pick_sinks(pref)
    return sinks[0] if sinks else None


def probe(path):
    """Duration + tags + stream info via ffprobe."""
    info = {'duration': 0.0, 'title': '', 'artist': '', 'album': '', 'video': None, 'audio': False}
    if not which('ffprobe'):
        return info
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path],
                             capture_output=True, text=True, timeout=15).stdout
        d = json.loads(out or '{}')
    except (subprocess.SubprocessError, ValueError, OSError):
        return info
    fmt = d.get('format', {})
    try:
        info['duration'] = float(fmt.get('duration') or 0)
    except ValueError:
        pass
    tags = {k.lower(): v for k, v in (fmt.get('tags') or {}).items()}
    for s in d.get('streams', []):
        if s.get('codec_type') == 'video' and not info['video'] and s.get('disposition', {}).get('attached_pic', 0) == 0:
            fr = s.get('avg_frame_rate') or s.get('r_frame_rate') or '0/1'
            try:
                n, m = fr.split('/')
                fps = float(n) / float(m) if float(m) else 0
            except ValueError:
                fps = 0
            sar = s.get('sample_aspect_ratio', '1:1')
            try:
                a, b = sar.split(':')
                sar = float(a) / float(b) if float(b) and float(a) else 1.0
            except ValueError:
                sar = 1.0
            info['video'] = {'w': s.get('width', 0), 'h': s.get('height', 0), 'fps': fps, 'sar': sar,
                             'codec': s.get('codec_name', '')}
        elif s.get('codec_type') == 'audio':
            info['audio'] = True
            for k, v in (s.get('tags') or {}).items():
                tags.setdefault(k.lower(), v)
    info['title'] = tags.get('title', '')
    info['artist'] = tags.get('artist', '') or tags.get('album_artist', '')
    info['album'] = tags.get('album', '')
    return info


class Spectrum:
    """Cheap log-band analyser: one Goertzel-style DFT bin per band (pure Python)."""
    N = 128
    DEC = 4          # 44.1 kHz / 4 -> bins of ~86 Hz

    def __init__(self, bands=24):
        n = self.N
        self.win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
        self.set_bands(bands)

    def set_bands(self, bands):
        n = self.N
        self.bands = bands
        bins = []
        for i in range(bands):
            k = int(round(1.2 * (n / 2 / 1.2) ** (i / max(1, bands - 1))))
            k = max(1, min(n // 2 - 1, k))
            if bins and k <= bins[-1]:
                k = min(n // 2 - 1, bins[-1] + 1)
            bins.append(k)
        self.bins = bins
        self.tw = [([math.cos(2 * math.pi * k * j / n) * self.win[j] for j in range(n)],
                    [math.sin(2 * math.pi * k * j / n) * self.win[j] for j in range(n)]) for k in bins]
        self.peak = [0.0] * bands

    def compute(self, pcm):
        n, d = self.N, self.DEC
        need = n * d * 4
        buf = pcm[-need:]
        if len(buf) < need:
            return [0.0] * self.bands
        a = array('h')
        a.frombytes(buf)
        mono = a[0::2 * d]
        out = []
        norm = n * 32768.0 * 0.5
        for i, (c, s) in enumerate(self.tw):
            re = sum(map(float.__mul__, map(float, mono), c))
            im = sum(map(float.__mul__, map(float, mono), s))
            v = math.sqrt(re * re + im * im) / norm
            lvl = max(0.0, min(1.0, (20 * math.log10(v + 1e-9) + 54) / 50))
            self.peak[i] = max(lvl, self.peak[i] * 0.82)
            out.append(self.peak[i])
        return out


class AudioPlayer:
    def __init__(self, wm, owner, on_end=None, on_error=None):
        self.wm = wm
        self.owner = owner
        self.on_end = on_end
        self.on_error = on_error
        self.candidates = []
        self.sink_try = 0
        self.sink_err = ''
        self.dec_cmd = None
        self.dec = None
        self.sink = None
        self.buf = bytearray()
        self.eof = False
        self.written = 0
        self.start_t = 0.0
        self.offset = 0.0
        self.paused = False
        self.volume = 1.0
        self.timer = None
        self.path = None
        self.last_chunk = b''
        self.error = ''
        self.sink_name = None

    @property
    def playing(self):
        return self.dec is not None and not self.paused

    def play(self, path, start=0.0):
        self.stop()
        self.path = path
        self.offset = max(0.0, start)
        self.candidates = pick_sinks(self.wm.cfg.get('audio_sink', 'auto'))
        if not which('ffmpeg'):
            self.error = 'ffmpeg not installed'
            return False
        if not self.candidates:
            self.error = 'no audio output (pw-cat, pacat or aplay)'
            return False
        self.sink_try = 0
        self.sink_name = self.candidates[0]
        self.dec_cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', '%.3f' % self.offset, '-i', path, '-vn',
                        '-f', 's16le', '-ac', str(CH), '-ar', str(RATE), '-']
        try:
            self.dec = subprocess.Popen(self.dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        stdin=subprocess.DEVNULL)
            self._start_sink(self.sink_name)
        except OSError as e:
            self.error = str(e)
            self.stop()
            return False
        os.set_blocking(self.dec.stdout.fileno(), False)
        self.wm.loop.add_reader(self.dec.stdout.fileno(), self._read)
        self.buf = bytearray()
        self.eof = False
        self.written = 0
        self.start_t = time.monotonic()
        self.paused = False
        self.error = ''
        self.timer = self.wm.loop.every(0.02, self._pump, owner=self.owner)
        return True

    def _start_sink(self, name):
        self.sink = subprocess.Popen(SINKS[name], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        os.set_blocking(self.sink.stdin.fileno(), False)
        if self.sink.stderr:
            os.set_blocking(self.sink.stderr.fileno(), False)

    def _sink_died(self):
        if self.sink is None or self.sink.poll() is None:
            return False
        msg = ''
        try:
            if self.sink.stderr:
                msg = self.sink.stderr.read(2000).decode('utf-8', 'replace').strip()
        except (OSError, ValueError):
            pass
        self.sink_err = (msg.split('\n')[-1] if msg else 'sink exited (%s)' % self.sink.returncode)[:200]
        # first sink failed before it consumed anything -> try the next backend
        if self.written < BPS // 4 and self.sink_try + 1 < len(self.candidates):
            self.sink_try += 1
            self.sink_name = self.candidates[self.sink_try]
            try:
                if self.sink.stdin:
                    self.sink.stdin.close()
            except OSError:
                pass
            try:
                self._start_sink(self.sink_name)
                self.start_t = time.monotonic() - self.written / BPS
                return True
            except OSError as e:
                self.sink_err = str(e)
        return False

    def _read(self, fd):
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self.eof = True
            self.wm.loop.remove_reader(fd)
            return
        self.buf += data
        if len(self.buf) > BPS * 3:      # back-pressure: stop reading until drained
            self.wm.loop.remove_reader(fd)
            self.reader_paused = True

    def _pump(self):
        if not self.dec or self.paused:
            return
        if self._sink_died():
            return
        if self.sink is None or self.sink.poll() is not None:
            self.error = '%s: %s' % (self.sink_name, self.sink_err or 'stopped')
            self.stop()
            if self.on_error:
                self.on_error(self.error)
            return
        elapsed = time.monotonic() - self.start_t
        target = int((elapsed + LEAD) * BPS) // 4 * 4
        need = target - self.written
        if need > 0 and self.buf:
            chunk = bytes(self.buf[:min(need, len(self.buf))])
            if self.volume != 1.0:
                a = array('h')
                a.frombytes(chunk)
                v = self.volume
                a = array('h', [max(-32768, min(32767, int(x * v))) for x in a])
                chunk = a.tobytes()
            try:
                n = os.write(self.sink.stdin.fileno(), chunk)
            except BlockingIOError:
                n = 0
            except OSError:
                self.stop()
                return
            if n:
                del self.buf[:n]
                self.written += n
                self.last_chunk = chunk[:n][-4096:]
        if getattr(self, 'reader_paused', False) and len(self.buf) < BPS:
            self.reader_paused = False
            self.wm.loop.add_reader(self.dec.stdout.fileno(), self._read)
        if self.eof and not self.buf and elapsed > self.written / BPS + self.buffered + 0.05:
            self.stop()
            if self.on_end:
                self.on_end()
        elif need < -BPS:   # sink stalled far behind wall clock: re-anchor
            self.start_t = time.monotonic() - self.written / BPS

    @property
    def buffered(self):
        """Seconds of audio we have handed over but that have not been heard yet."""
        return LEAD + SINK_LATENCY.get(self.sink_name, 0.10)

    @property
    def position(self):
        """Media time leaving the speakers; the video player uses this as master clock."""
        if not self.dec or self.paused:
            return self.offset
        return playing_position(self.written, time.monotonic() - self.start_t,
                                self.buffered, self.offset)

    def pause(self):
        if not self.dec or self.paused:
            return
        self.offset = self.position
        self._halt()
        self.paused = True

    def resume(self):
        if not self.path or not self.paused:
            return
        self.play(self.path, self.offset)

    def toggle(self):
        if self.paused:
            self.resume()
        elif self.dec:
            self.pause()
        elif self.path:
            self.play(self.path, self.offset)

    def seek(self, t):
        if not self.path:
            return
        if self.paused:
            self.offset = max(0.0, t)
        else:
            self.play(self.path, t)

    def levels(self, spec):
        if not self.dec or self.paused or not self.last_chunk:
            return [x * 0.8 for x in spec.peak]
        return spec.compute(self.last_chunk)

    def stop(self):
        pos = self.position
        self._halt()
        self.offset = pos
        self.paused = False

    def _halt(self):
        """Kill decoder and sink; leaves path/offset/paused alone."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.dec:
            try:
                self.wm.loop.remove_reader(self.dec.stdout.fileno())
            except (ValueError, OSError):
                pass
            for p in (self.dec, self.sink):
                if p is None:
                    continue
                try:
                    p.kill()
                except OSError:
                    pass
                try:
                    p.wait(timeout=1)
                except Exception:
                    pass
            for f in (self.dec.stdout, self.sink.stdin if self.sink else None,
                      self.sink.stderr if self.sink else None):
                try:
                    if f:
                        f.close()
                except OSError:
                    pass
        self.dec = None
        self.sink = None
        self.reader_paused = False
