"""Mail core: IMAP folder names, LIST parsing, MIME parsing, HTML, composing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell import mail  # noqa: E402

EML = b"""From: Max Mustermann <max@example.org>
To: Alex <alex@example.com>, "Other" <o@example.com>
Cc: cc@example.com
Subject: =?utf-8?q?Gr=C3=BC=C3=9Fe_aus_Berlin?=
Date: Tue, 21 Oct 2025 18:07:00 +0200
Message-ID: <abc@example.org>
In-Reply-To: <parent@example.org>
References: <root@example.org> <parent@example.org>
Content-Type: multipart/mixed; boundary="BOUND"

--BOUND
Content-Type: multipart/alternative; boundary="ALT"

--ALT
Content-Type: text/plain; charset=utf-8

Hallo Alex,
das ist der Text.
--ALT
Content-Type: text/html; charset=utf-8

<html><head><style>p{color:red}</style></head><body>
<p>Hallo <b>Alex</b>,</p><p>das ist der <a href="https://example.org/x">Text</a>.</p>
<script>bad()</script></body></html>
--ALT--
--BOUND
Content-Type: application/pdf; name="rechnung.pdf"
Content-Disposition: attachment; filename="rechnung.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--BOUND--
"""

HTML_ONLY = b"""From: Shop <shop@example.org>
To: alex@example.com
Subject: Angebot
Date: Mon, 20 Oct 2025 09:00:00 +0000
Content-Type: text/html; charset=utf-8

<div>Guten Tag,<br>hier die <b>Links</b>:</div>
<ul><li><a href="https://a.example/1">Angebot 1</a></li>
<li><a href="https://a.example/1">https://a.example/1</a></li></ul>
<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>
<p>&nbsp;</p><p>Gru&szlig;<br>Shop</p>
"""


class TestUtf7(unittest.TestCase):
    def test_roundtrip(self):
        for s in ('INBOX', 'Entwürfe', 'Gelöschte Elemente', 'Ärger & Freude',
                  '日本語', 'INBOX.VEGA'):
            self.assertEqual(mail.utf7_decode(mail.utf7_encode(s)), s)

    def test_known_values(self):
        self.assertEqual(mail.utf7_encode('Entwürfe'), 'Entw&APw-rfe')
        self.assertEqual(mail.utf7_decode('Entw&APw-rfe'), 'Entwürfe')
        self.assertEqual(mail.utf7_encode('A&B'), 'A&-B')
        self.assertEqual(mail.utf7_decode('A&-B'), 'A&B')

    def test_broken_input_kept(self):
        self.assertEqual(mail.utf7_decode('abc&XYZ'), 'abc&XYZ')


class TestListen(unittest.TestCase):
    def test_flags_and_quoted_name(self):
        f = mail.parse_list_line(b'(\\HasNoChildren \\Sent) "." "Sent Items"')
        self.assertEqual(f.name, 'Sent Items')
        self.assertEqual(f.role, 'sent')
        f = mail.parse_list_line(b'(\\HasChildren) "." INBOX')
        self.assertEqual((f.name, f.role, f.delim), ('INBOX', 'inbox', '.'))

    def test_utf7_literal_name(self):
        f = mail.parse_list_line(b'(\\HasNoChildren) "/" "Entw&APw-rfe"')
        self.assertEqual(f.name, 'Entw&APw-rfe')
        self.assertEqual(f.label, 'Entwürfe')
        self.assertEqual(f.role, 'drafts')

    def test_role_guessing(self):
        self.assertEqual(mail.role_of('INBOX'), 'inbox')
        self.assertEqual(mail.role_of('INBOX.Spam'), 'junk')
        self.assertEqual(mail.role_of('Gelöscht'), 'trash')
        self.assertEqual(mail.role_of('Projekte'), 'other')
        self.assertEqual(mail.role_of('X', ['\\Archive']), 'archive')

    def test_tree(self):
        raw = [b'(\\HasChildren) "." INBOX',
               b'(\\HasNoChildren) "." INBOX.VEGA',
               b'(\\HasNoChildren) "." INBOX.Augenrahmen info',
               b'(\\Sent) "." "Sent Items"',
               b'(\\Trash) "." Trash',
               b'(\\Junk) "." Spam',
               b'(\\Drafts) "." Drafts',
               b'(\\HasNoChildren) "." Projekte']
        tree = mail.build_tree([mail.parse_list_line(x) for x in raw])
        labels = [f.label for f in tree]
        self.assertEqual(labels[0], 'INBOX')
        self.assertIn('INBOX.VEGA', labels[:4])
        self.assertLess(labels.index('Sent Items'), labels.index('Trash'))
        self.assertEqual(tree[labels.index('INBOX.VEGA')].depth, 1)
        self.assertEqual(tree[-1].label, 'Projekte')
        self.assertEqual(len(tree), 8)


class TestParsing(unittest.TestCase):
    def test_full_message(self):
        m = mail.parse_message(EML)
        self.assertEqual(m['subject'], 'Grüße aus Berlin')
        self.assertEqual(m['from'], 'Max Mustermann <max@example.org>')
        self.assertIn('alex@example.com', m['to'])
        self.assertIn('cc@example.com', m['cc'])
        self.assertIn('das ist der Text.', m['text'])
        self.assertEqual(len(m['attachments']), 1)
        att = m['attachments'][0]
        self.assertEqual(att['name'], 'rechnung.pdf')
        self.assertTrue(att['data'].startswith(b'%PDF'))
        self.assertEqual(m['msgid'], '<abc@example.org>')
        self.assertEqual(m['irt'], '<parent@example.org>')
        self.assertTrue(m['date'].startswith('2025-10-21T18:07'))

    def test_html_only_falls_back_to_text(self):
        m = mail.parse_message(HTML_ONLY)
        self.assertEqual(m['text'].strip().split('\n')[0], 'Guten Tag,')
        self.assertIn('Angebot 1 <https://a.example/1>', m['text'])
        self.assertEqual(m['text'].count('<https://a.example/1>'), 1)
        self.assertIn('A  B', m['text'])
        self.assertIn('Gruß\nShop', m['text'])

    def test_strip_html_blocks(self):
        t = mail.strip_html('<p>eins</p><p>zwei</p>')
        self.assertEqual(t, 'eins\n\nzwei')
        self.assertEqual(mail.strip_html('a<br>b'), 'a\nb')
        self.assertEqual(mail.strip_html('<ul><li>x</li><li>y</li></ul>'), '- x\n- y')
        self.assertNotIn('color', mail.strip_html('<style>p{color:red}</style>x'))
        self.assertEqual(mail.strip_html('<p>a&nbsp;&amp;&nbsp;b</p>'), 'a & b')

    def test_header_only(self):
        head = EML.split(b'\n\n--BOUND')[0] + b'\n\n'
        h = mail.header_from(head, uid='7', flags=['\\Seen', '\\Flagged'], size=1234, seq=3)
        self.assertEqual(h['uid'], '7')
        self.assertEqual(h['flags'], 'FS')
        self.assertEqual(h['size'], 1234)
        self.assertEqual(h['subject'], 'Grüße aus Berlin')
        self.assertEqual(h['from_addr'], 'max@example.org')
        self.assertEqual(mail.parse_flags_str('FS'), '\\flagged \\seen')

    def test_characterset_fallback(self):
        self.assertEqual(mail.decode_text('Grüße'.encode('latin-1'), 'iso-8859-1'), 'Grüße')
        self.assertEqual(mail.decode_text(b'\xff\xfe', 'bogus-charset'), '\xff\xfe'.encode('latin-1').decode('latin-1'))

    def test_bad_dates(self):
        self.assertEqual(mail.iso_date('not a date'), '')
        self.assertEqual(mail.iso_date(''), '')


class TestCompose(unittest.TestCase):
    acc = {'email': 'alex@example.com', 'name': 'Alexander Riedel'}

    def test_build_and_send_fields(self):
        raw = mail.build_message(self.acc, 'to@example.org', 'Test', 'Hallo',
                                 cc='cc@example.org', in_reply_to='<x@y>',
                                 references='<r@y>')
        m = mail.parse_message(raw)
        self.assertEqual(m['subject'], 'Test')
        self.assertEqual(m['from'], 'Alexander Riedel <alex@example.com>')
        self.assertEqual(m['to'], 'to@example.org')
        self.assertEqual(m['irt'], '<x@y>')
        self.assertIn('<r@y> <x@y>', m['refs'])
        self.assertEqual(m['text'].strip(), 'Hallo')
        self.assertTrue(m['msgid'].startswith('<'))

    def test_attachment(self):
        raw = mail.build_message(self.acc, 'to@example.org', 'T', 'B',
                                 attach=[('a.txt', 'text/plain', b'hello')])
        m = mail.parse_message(raw)
        self.assertEqual(m['attachments'][0]['name'], 'a.txt')
        self.assertEqual(m['attachments'][0]['data'], b'hello')
        self.assertEqual(mail._rcpt_list(raw), ['to@example.org'])

    def test_reply_subject(self):
        self.assertEqual(mail.reply_subject('Hallo'), 'Re: Hallo')
        self.assertEqual(mail.reply_subject('Re: Hallo'), 'Re: Hallo')
        self.assertEqual(mail.reply_subject('AW: Hallo'), 'AW: Hallo')
        self.assertEqual(mail.forward_subject('Hallo'), 'Fwd: Hallo')

    def test_quote(self):
        self.assertEqual(mail.quote('a\n\nb'), '> a\n>\n> b')

    def test_reply_text(self):
        m = mail.parse_message(EML)
        t = mail.reply_text(m, signature='-- \nAlex')
        self.assertTrue(t.startswith('-- \nAlex'))
        self.assertIn('schrieb Max Mustermann', t)
        self.assertIn('> Hallo Alex,', t)

    def test_addresses(self):
        ok, bad = mail.parse_addresses('a@example.org, B <b@example.org>')
        self.assertTrue(ok)
        self.assertEqual(bad, [])
        ok, bad = mail.parse_addresses('kein-at, c@example.org')
        self.assertFalse(ok)
        self.assertEqual(bad, ['kein-at'])
        self.assertFalse(mail.parse_addresses('')[0])

    def test_names(self):
        self.assertEqual(mail.short_name('"Mustermann, Max" <max@x.de>'), 'Mustermann, Max')
        self.assertEqual(mail.short_name('max@x.de'), 'max@x.de')
        self.assertEqual(mail.addr_addr('Max <max@x.de>'), 'max@x.de')


class TestFormat(unittest.TestCase):
    def test_size(self):
        self.assertEqual(mail.fmt_size(900), '900 B')
        self.assertEqual(mail.fmt_size(2048), '2 KB')
        self.assertEqual(mail.fmt_size(5 * 1048576), '5.0 MB')

    def test_dates(self):
        import datetime as dt
        now = dt.datetime(2026, 10, 22, 15, 0)
        self.assertEqual(mail.fmt_date('2026-10-22T09:31:00', now), '09:31')
        self.assertEqual(mail.fmt_date('2026-03-04T09:31:00', now), '04 Mar')
        self.assertEqual(mail.fmt_date('2019-03-04T09:31:00', now), '04.03.2019')
        self.assertEqual(mail.fmt_date(''), '')


class TestAccounts(unittest.TestCase):
    def test_providers(self):
        a = mail.blank_account('x@y.de', 'strato')
        self.assertEqual((a['imap'], a['imap_port'], a['smtp'], a['smtp_port']),
                         ('imap.strato.de', 993, 'smtp.strato.de', 465))
        self.assertEqual(a['user'], 'x@y.de')

    def test_find(self):
        data = {'accounts': [{'email': 'a@b.c'}, {'email': 'd@e.f'}]}
        self.assertEqual(mail.find_account(data, 'D@E.F')['email'], 'd@e.f')
        self.assertEqual(mail.find_account(data, 'zz')['email'], 'a@b.c')


if __name__ == '__main__':
    unittest.main()
