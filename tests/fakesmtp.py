"""A tiny in-process SMTP server for tests.

Plain SMTP only (no TLS); AUTH PLAIN and LOGIN are accepted with any
credentials.  It records the envelope and the message source so the tests
can check what :meth:`monocell.mail.Session.send` actually put on the wire.
"""
import base64
import socket
import threading


class FakeSMTP:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.mail = []
        self.conns = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self.port

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass
        for c in list(self.conns):
            try:
                c.close()
            except OSError:
                pass

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        f = conn.makefile('rb')

        def say(line):
            try:
                conn.sendall(line.encode() + b'\r\n')
            except OSError:
                pass

        say('220 fake ESMTP ready')
        env = {'from': '', 'rcpt': []}
        data = None
        hdr = {'done': False, 'bare_lf': False}
        auth = None
        while True:
            raw_line = f.readline()
            if not raw_line:
                return
            line = raw_line.rstrip(b'\r\n')
            up = line.upper()
            if data is not None:
                if line == b'.':
                    if hdr['bare_lf']:
                        # strict like a real MTA: bare LF inside the header block
                        say('554 5.6.0 SMTP protocol violation: '
                            'A header line must be terminated by CRLF')
                    else:
                        self.mail.append({'from': env['from'], 'to': list(env['rcpt']),
                                          'data': b'\r\n'.join(data) + b'\r\n'})
                        say('250 2.0.0 Ok: queued as 1')
                    env = {'from': '', 'rcpt': []}
                    data = None
                    hdr = {'done': False, 'bare_lf': False}
                else:
                    if not line:
                        hdr['done'] = True
                    elif not hdr['done'] and not raw_line.endswith(b'\r\n'):
                        hdr['bare_lf'] = True
                    data.append(line[1:] if line.startswith(b'..') else line)
                continue
            if auth is not None:
                if auth == 'login-user':
                    auth = 'login-pass'
                    say('334 UGFzc3dvcmQ6')
                else:
                    auth = None
                    say('235 2.7.0 Authentication successful')
                continue
            if up.startswith(b'EHLO'):
                conn.sendall(b'250-fake\r\n250-AUTH PLAIN LOGIN\r\n'
                             b'250-8BITMIME\r\n250 SIZE 33554432\r\n')
            elif up.startswith(b'HELO'):
                say('250 fake')
            elif up.startswith(b'AUTH PLAIN'):
                rest = line.split(None, 2)
                if len(rest) > 2:
                    say('235 2.7.0 Authentication successful')
                else:
                    auth = 'plain'
                    say('334 ')
            elif up.startswith(b'AUTH LOGIN'):
                auth = 'login-user'
                say('334 VXNlcm5hbWU6')
            elif up.startswith(b'MAIL FROM'):
                env['from'] = _addr(line)
                say('250 2.1.0 Ok')
            elif up.startswith(b'RCPT TO'):
                env['rcpt'].append(_addr(line))
                say('250 2.1.5 Ok')
            elif up == b'DATA':
                data = []
                say('354 End data with <CR><LF>.<CR><LF>')
            elif up in (b'RSET', b'NOOP'):
                env = {'from': '', 'rcpt': []}
                say('250 Ok')
            elif up.startswith(b'QUIT'):
                say('221 2.0.0 Bye')
                return
            else:
                say('250 Ok')


def _addr(line):
    s = line.decode('utf-8', 'replace')
    i, j = s.find('<'), s.rfind('>')
    return s[i + 1:j] if 0 <= i < j else s.split(':', 1)[-1].strip()


def auth_blob(user, pw):
    return base64.b64encode(b'\0' + user.encode() + b'\0' + pw.encode()).decode()
