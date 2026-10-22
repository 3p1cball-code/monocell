"""Wire-level test of monocell.mail.Session against tests/fakeimap.py.

This is the closest thing to a real mail server we can run in CI: every
command Session sends is answered by a plain IMAP4rev1 socket server, so
sequence sets, literals, flags, MOVE and paging are exercised for real.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from monocell import mail  # noqa: E402
from fakeimap import FakeIMAP  # noqa: E402

PLAIN = b"""From: Max Mustermann <max@example.org>
To: Alex <test@example.org>
Subject: Erste Mail
Date: Tue, 21 Oct 2025 18:07:00 +0200
Message-ID: <one@example.org>
Content-Type: text/plain; charset=utf-8

Hallo Welt.
"""

HTML = """From: Shop <shop@example.org>
To: test@example.org
Subject: =?utf-8?q?Angebot_f=C3=BCr_dich?=
Date: Wed, 22 Oct 2025 09:00:00 +0200
Message-ID: <two@example.org>
Content-Type: text/html; charset=utf-8

<html><body><p>Nur <b>HTML</b> hier.</p><p>Zweiter Absatz.</p></body></html>
""".encode()

MIXED = b"""From: Buchhaltung <bill@example.org>
To: test@example.org
Subject: Rechnung
Date: Thu, 23 Oct 2025 12:00:00 +0200
Message-ID: <three@example.org>
In-Reply-To: <one@example.org>
Content-Type: multipart/mixed; boundary="B"

--B
Content-Type: text/plain; charset=utf-8

Anbei die Rechnung.
--B
Content-Type: application/pdf; name="r.pdf"
Content-Disposition: attachment; filename="r.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--B--
"""

SUB = b"""From: Sub <sub@example.org>
To: test@example.org
Subject: Unterordner
Date: Fri, 24 Oct 2025 08:00:00 +0200

Tief drin.
"""

SENT = b"""From: test@example.org
To: wer@example.org
Subject: Gesendet
Date: Sat, 25 Oct 2025 08:00:00 +0200

Schon raus.
"""


def make_server():
    srv = FakeIMAP({'INBOX': [PLAIN, HTML, MIXED],
                    'INBOX.Sub': [SUB],
                    'Sent Items': [SENT],
                    'Drafts': [],
                    'Trash': [],
                    'Spam': []})
    return srv


class TestSession(unittest.TestCase):
    def setUp(self):
        self.srv = make_server()
        self.port = self.srv.start()
        self.acc = {'email': 'test@example.org', 'user': 'test@example.org',
                    'password': 'secret', 'imap': '127.0.0.1', 'imap_port': self.port,
                    'imap_ssl': False, 'smtp': '127.0.0.1', 'smtp_port': 1,
                    'smtp_ssl': False}
        self.s = mail.Session(self.acc)
        self.s.connect()
        self.folders = self.s.list_folders()

    def tearDown(self):
        self.s.close()
        self.srv.stop()

    # ------------------------------------------------------------ folders
    def test_login_and_folders(self):
        names = [f.name for f in self.folders]
        self.assertIn('INBOX', names)
        self.assertIn('INBOX.Sub', names)
        self.assertIn('Sent Items', names)
        inbox = [f for f in self.folders if f.name == 'INBOX'][0]
        self.assertEqual(inbox.role, 'inbox')
        self.assertEqual(inbox.depth, 0)
        sub = [f for f in self.folders if f.name == 'INBOX.Sub'][0]
        self.assertEqual(sub.depth, 1)
        self.assertEqual(sub.leaf, 'Sub')
        roles = {f.name: f.role for f in self.folders}
        self.assertEqual(roles['Sent Items'], 'sent')
        self.assertEqual(roles['Drafts'], 'drafts')
        self.assertEqual(roles['Trash'], 'trash')
        self.assertEqual(roles['Spam'], 'junk')

    def test_bad_login(self):
        bad = dict(self.acc, password='nope')
        s = mail.Session(bad)
        with self.assertRaises(mail.MailError) as cm:
            s.connect()
        self.assertIn('login failed', str(cm.exception))

    def test_status(self):
        self.assertEqual(self.s.status('INBOX'), (3, 3))
        self.assertEqual(self.s.status(mail.Folder('INBOX.Sub')), (1, 1))

    # ------------------------------------------------------------ headers
    def test_select_and_uidvalidity(self):
        n = self.s.select('INBOX')
        self.assertEqual(n, 3)
        self.assertEqual(self.s.uidvalidity, '7')

    def test_fetch_headers(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        self.assertEqual(len(hs), 3)
        self.assertEqual([h['seq'] for h in hs], [1, 2, 3])
        self.assertEqual(hs[0]['subject'], 'Erste Mail')
        self.assertEqual(hs[1]['subject'], 'Angebot für dich')
        self.assertIn('Max Mustermann', hs[0]['from'])
        self.assertEqual(mail.short_name(hs[0]['from']), 'Max Mustermann')
        self.assertEqual(hs[0]['from_addr'], 'max@example.org')
        self.assertEqual(hs[0]['date'][:10], '2025-10-21')
        self.assertGreater(hs[0]['size'], 100)
        self.assertEqual(len({h['uid'] for h in hs}), 3)

    def test_header_paging(self):
        first = self.s.fetch_headers('INBOX', count=2)
        self.assertEqual([h['subject'] for h in first], ['Angebot für dich', 'Rechnung'])
        older = self.s.fetch_headers('INBOX', count=2, before_seq=first[0]['seq'])
        self.assertEqual([h['subject'] for h in older], ['Erste Mail'])
        again = self.s.fetch_headers('INBOX', count=2, before_seq=1)
        self.assertEqual(again, [])

    def test_fetch_headers_empty_folder(self):
        self.assertEqual(self.s.fetch_headers('Drafts'), [])

    # ------------------------------------------------------------ bodies
    def test_fetch_message_plain(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        msg = self.s.fetch_message('INBOX', hs[0]['uid'])
        self.assertEqual(msg['subject'], 'Erste Mail')
        self.assertIn('Hallo Welt', msg['text'])
        self.assertEqual(msg['attachments'], [])

    def test_fetch_message_html_only(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        msg = self.s.fetch_message('INBOX', hs[1]['uid'])
        self.assertIn('Nur HTML hier.', msg['text'])
        self.assertIn('Zweiter Absatz.', msg['text'])
        self.assertNotIn('<p>', msg['text'])

    def test_fetch_message_attachment(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        msg = self.s.fetch_message('INBOX', hs[2]['uid'])
        self.assertIn('Anbei die Rechnung', msg['text'])
        self.assertEqual([a['name'] for a in msg['attachments']], ['r.pdf'])
        self.assertEqual(msg['attachments'][0]['data'][:4], b'%PDF')

    def test_body_fetch_does_not_set_seen(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        self.assertNotIn('S', hs[0]['flags'])
        self.s.fetch_message('INBOX', hs[0]['uid'])
        hs = self.s.fetch_headers('INBOX', count=10)
        self.assertNotIn('S', hs[0]['flags'])

    # ------------------------------------------------------------ flags
    def test_set_flags_roundtrip(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        uid = hs[0]['uid']
        self.s.set_flags('INBOX', uid, add=['\\Seen'])
        self.assertIn('S', self.s.fetch_headers('INBOX', count=10)[0]['flags'])
        self.s.set_flags('INBOX', uid, remove=['\\Seen'])
        self.assertNotIn('S', self.s.fetch_headers('INBOX', count=10)[0]['flags'])

    def test_flag_multiple_uids(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        self.s.set_flags('INBOX', [h['uid'] for h in hs], add=['\\Flagged'])
        hs = self.s.fetch_headers('INBOX', count=10)
        self.assertTrue(all('F' in h['flags'] for h in hs))
        self.s.set_flags('INBOX', [h['uid'] for h in hs], remove=['\\Flagged'])

    # ------------------------------------------------------------ move
    def test_move(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        uid = hs[2]['uid']
        subj = hs[2]['subject']
        self.s.move('INBOX', uid, 'Trash')
        left = self.s.fetch_headers('INBOX', count=10)
        self.assertEqual(len(left), 2)
        self.assertNotIn(subj, [h['subject'] for h in left])
        trashed = self.s.fetch_headers('Trash', count=10)
        self.assertEqual([h['subject'] for h in trashed], [subj])
        self.assertNotEqual(trashed[0]['uid'], uid)
        # put it back for other tests
        self.s.move('Trash', trashed[0]['uid'], 'INBOX')

    def test_delete_with_trash_folder(self):
        hs = self.s.fetch_headers('INBOX', count=10)
        uid = hs[0]['uid']
        self.assertEqual(self.s.delete('INBOX', uid, trash='Trash'), 'trash')
        self.assertEqual(len(self.s.fetch_headers('INBOX', count=10)), 2)
        back = self.s.fetch_headers('Trash', count=10)
        self.s.move('Trash', back[0]['uid'], 'INBOX')

    def test_delete_permanent(self):
        self.s.append('Drafts', SENT)
        hs = self.s.fetch_headers('Drafts', count=10)
        self.assertEqual(len(hs), 1)
        self.assertEqual(self.s.delete('Drafts', hs[0]['uid']), 'deleted')
        self.assertEqual(self.s.fetch_headers('Drafts', count=10), [])

    def test_folder_names_with_spaces(self):
        self.assertEqual(self.s.status('Sent Items'), (1, 1))
        self.assertEqual(len(self.s.fetch_headers('Sent Items', count=10)), 1)
        uid = self.s.fetch_headers('INBOX', count=10)[0]['uid']
        self.s.move('INBOX', uid, 'Sent Items')
        self.assertEqual(self.s.status('Sent Items'), (2, 2))

    # ------------------------------------------------------------ append
    def test_append(self):
        uid = self.s.append('Drafts', PLAIN, '(\\Draft)')
        self.assertTrue(uid)
        hs = self.s.fetch_headers('Drafts', count=10)
        self.assertEqual(len(hs), 1)
        self.assertIn('D', hs[0]['flags'])
        self.assertEqual(hs[0]['subject'], 'Erste Mail')
        self.s.delete('Drafts', hs[0]['uid'])

    # ------------------------------------------------------------ search
    def test_search(self):
        hs = self.s.search('INBOX', 'Welt')
        self.assertEqual([h['subject'] for h in hs], ['Erste Mail'])
        # search returns headers, so the caller can show them in place
        self.assertTrue(hs[0]['from_addr'])


class TestSessionReconnect(unittest.TestCase):
    def test_retry_after_close(self):
        srv = make_server()
        port = srv.start()
        try:
            acc = {'email': 'test@example.org', 'password': 'secret', 'imap': '127.0.0.1',
                   'imap_port': port, 'imap_ssl': False}
            s = mail.Session(acc)
            s.connect()
            self.assertEqual(s.select('INBOX'), 3)
            s.imap.logout()          # simulate a dropped connection
            s.imap = None
            self.assertEqual(s.select('INBOX'), 3)
            s.close()
        finally:
            srv.stop()


if __name__ == '__main__':
    unittest.main()
