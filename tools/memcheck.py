"""Measure monocell's RSS in a pty (idle) for normal and --lowmem sessions."""
import os, pty, select, sys, time, fcntl, struct, termios

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
HOME = os.path.expanduser('~')

def run(args, secs=4):
    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ)
        testhome = os.path.join(ROOT, '.memhome')
        env.update({'TERM': 'xterm-256color', 'PYTHONPATH': ROOT,
                    'XDG_CONFIG_HOME': os.path.join(testhome, 'config'),
                    'XDG_DATA_HOME': os.path.join(testhome, 'data'),
                    'XDG_CACHE_HOME': os.path.join(testhome, 'cache')})
        os.execvpe(sys.executable, [sys.executable, '-m', 'monocell'] + args, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 128, 0, 0))
    t = time.time()
    while time.time() - t < secs:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                os.read(fd, 65536)
            except OSError:
                break
    rss = 0
    try:
        with open('/proc/%d/status' % pid) as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    rss = int(line.split()[1])
    except OSError:
        pass
    try:
        os.kill(pid, 15)
        os.waitpid(pid, 0)
    except OSError:
        pass
    return rss

if __name__ == '__main__':
    print('normal: %d MB' % (run([]) // 1024))
    print('lowmem: %d MB' % (run(['--lowmem']) // 1024))
