"""Sending mail: build_message + Session.send against a fake SMTP server."""
import email
import email.policy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from monocell import mail  # noqa: E402

from fakesmtp import FakeSMTP  # noqa: E402


ACC = {'name': 'Alexander Riedel', 'email': 'alexander.riedel@eyedea3d.com',
       'user': 'alexander.riedel@eyedea3d.com', 'password': 'secret',
       'smtp': '127.0.0.1', 'smtp_port': 0, 'smtp_ssl': False}


def _parse(entry):
    return email.message_from_bytes(entry['data'], policy=email.policy.default)


class SendTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeSMTP()
        self.port = self.srv.start()
        self.acc = dict(ACC, smtp_port=self.port)

    def tearDown(self):
        self.srv.stop()

    def send(self, **kw):
        raw = mail.build_message(self.acc, **kw)
        mail.Session(self.acc).send(raw)
        self.assertEqual(len(self.srv.mail), 1)
        return self.srv.mail[0]

    def test_plain(self):
        entry = self.send(to='kunde@example.org', subject='Hallo', body='Zeile eins\nZeile zwei')
        self.assertEqual(entry['from'], ACC['email'])
        self.assertEqual(entry['to'], ['kunde@example.org'])
        msg = _parse(entry)
        self.assertEqual(msg['Subject'], 'Hallo')
        self.assertEqual(msg['To'], 'kunde@example.org')
        self.assertEqual(msg['From'], 'Alexander Riedel <alexander.riedel@eyedea3d.com>')
        self.assertIn('Zeile zwei', msg.get_content())
        self.assertTrue(msg['Message-ID'].endswith('@eyedea3d.com>'))
        self.assertTrue(msg['Date'])

    def test_cc_and_bcc_envelope(self):
        entry = self.send(to='a@example.org', cc='b@example.org', bcc='c@example.org',
                          subject='Runde', body='text')
        self.assertEqual(entry['to'], ['a@example.org', 'b@example.org', 'c@example.org'])
        msg = _parse(entry)
        self.assertEqual(msg['Cc'], 'b@example.org')
        self.assertIsNone(msg['Bcc'], 'Bcc must not reach the recipients')

    def test_reply_headers(self):
        entry = self.send(to='a@example.org', subject='Re: Frage', body='antwort',
                          in_reply_to='<abc@example.org>', references='<root@example.org>')
        msg = _parse(entry)
        self.assertEqual(msg['In-Reply-To'], '<abc@example.org>')
        self.assertEqual(msg['References'], '<root@example.org> <abc@example.org>')

    def test_wire_line_endings(self):
        # build_message emits bare LF; send() has to put CRLF on the wire or a
        # strict server answers 554 ("header line must be terminated by CRLF").
        self.assertIn(b'\n', mail.build_message(self.acc, 'a@example.org', 's', 'b'))
        entry = self.send(to='a@example.org', subject='Zeilen', body='eins\nzwei')
        self.assertNotIn(b'\n', entry['data'].replace(b'\r\n', b''))
        body = _parse(entry).get_content().strip().replace('\r\n', '\n')
        self.assertEqual(body, 'eins\nzwei')

    def test_attachment(self):
        entry = self.send(to='a@example.org', subject='Datei', body='siehe Anhang',
                          attach=[('notiz.txt', 'text/plain', b'inhalt')])
        msg = _parse(entry)
        self.assertTrue(msg.is_multipart())
        names = [p.get_filename() for p in msg.walk() if p.get_filename()]
        self.assertEqual(names, ['notiz.txt'])

    def test_html_alternative(self):
        entry = self.send(to='a@example.org', subject='Html', body='nur text',
                          html='<p>nur <b>html</b></p>')
        msg = _parse(entry)
        types = [p.get_content_type() for p in msg.walk()]
        self.assertIn('text/plain', types)
        self.assertIn('text/html', types)

    def test_unreachable_server(self):
        self.acc = dict(self.acc, smtp_port=1)
        raw = mail.build_message(self.acc, to='a@example.org', subject='x', body='y')
        with self.assertRaises(mail.MailError) as cm:
            mail.Session(self.acc).send(raw)
        self.assertIn('send failed', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
