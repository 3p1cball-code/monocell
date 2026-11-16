"""YouTube: sign-in arguments, entry parsing and the Shorts/mix filter."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell.apps import youtube  # noqa: E402


def entry(vid, title='Video', duration=600, url=None, views=1):
    return {'id': vid, 'title': title, 'duration': duration,
            'url': url or 'https://www.youtube.com/watch?v=' + vid,
            'uploader': 'Channel', 'view_count': views}


class TestAuth(unittest.TestCase):
    def test_off_needs_no_cookies(self):
        self.assertEqual(youtube.auth_args({'youtube_signin': 'off'}), ([], ''))

    def test_browser_uses_cookies_from_browser(self):
        for browser in youtube.BROWSERS:
            args, err = youtube.auth_args({'youtube_signin': browser})
            self.assertEqual(args, ['--cookies-from-browser', browser])
            self.assertEqual(err, '')

    def test_cookies_file(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            f.write('# Netscape HTTP Cookie File\n')
            path = f.name
        try:
            args, err = youtube.auth_args(
                {'youtube_signin': 'cookies.txt', 'youtube_cookies': path})
            self.assertEqual(args, ['--cookies', path])
            self.assertEqual(err, '')
        finally:
            os.unlink(path)

    def test_missing_cookies_file_is_reported(self):
        args, err = youtube.auth_args(
            {'youtube_signin': 'cookies.txt', 'youtube_cookies': '/no/such/file'})
        self.assertEqual(args, [])
        self.assertIn('not found', err)

    def test_unknown_mode(self):
        args, err = youtube.auth_args({'youtube_signin': 'password'})
        self.assertEqual(args, [])
        self.assertIn('Unknown', err)


class TestEntries(unittest.TestCase):
    def test_flat_entry_url_falls_back_to_url_field(self):
        data = {'entries': [entry('abc')]}
        self.assertEqual(youtube.parse_entries(data)[0]['url'],
                         'https://www.youtube.com/watch?v=abc')

    def test_recommendations_skip_shorts_and_mixes(self):
        data = {'entries': [
            entry('normal', 'A normal video'),
            entry('shortdur', 'A 30 second clip', duration=30),
            entry('shorturl', 'A vertical clip', duration=400,
                  url='https://www.youtube.com/shorts/shorturl'),
            entry('mix', 'Mix - Some music', duration=None,
                  url='https://www.youtube.com/playlist?list=RDmix'),
            entry('hashtag', 'Funny #shorts', duration=200),
            entry('unknown', 'Unknown length', duration=None),
        ]}
        got = youtube.recommendations(data)
        self.assertEqual([e['id'] for e in got], ['normal', 'unknown'])

    def test_recommendations_respect_the_limit(self):
        data = {'entries': [entry('v%d' % i) for i in range(10)]}
        self.assertEqual(len(youtube.recommendations(data, limit=4)), 4)

    def test_title_without_id_is_skipped(self):
        data = {'entries': [{'title': 'broken'}, entry('ok')]}
        self.assertEqual([e['id'] for e in youtube.parse_entries(data)], ['ok'])


class TestFetch(unittest.TestCase):
    def test_fetch_uses_the_signed_in_feed(self):
        payload = '{"entries": [%s]}' % (
            '{"id": "abc", "title": "A video", "duration": 600, '
            '"url": "https://www.youtube.com/watch?v=abc"}')
        proc = mock.Mock(returncode=0, stdout=payload, stderr='')
        cfg = {'youtube_signin': 'firefox'}
        with mock.patch.object(youtube, 'which', return_value='/usr/bin/yt-dlp'), \
                mock.patch.object(youtube.subprocess, 'run', return_value=proc) as run:
            out = youtube.fetch_recommendations(cfg)
        self.assertEqual([e['id'] for e in out], ['abc'])
        cmd = run.call_args.args[0]
        self.assertIn('--cookies-from-browser', cmd)
        self.assertIn('https://www.youtube.com/feed/recommended', cmd)

    def test_decryption_error_gets_a_clear_message(self):
        proc = mock.Mock(returncode=1, stdout='',
                         stderr='WARNING: cannot decrypt v11 cookies: no key found')
        cfg = {'youtube_signin': 'brave'}
        with mock.patch.object(youtube, 'which', return_value='/usr/bin/yt-dlp'), \
                mock.patch.object(youtube.subprocess, 'run', return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                youtube.fetch_recommendations(cfg)
        self.assertIn('cookies.txt', str(cm.exception))

    def test_exported_cookies_are_the_fallback_when_the_keyring_is_locked(self):
        payload = '{"entries": [%s]}' % (
            '{"id": "abc", "title": "A video", "duration": 600, '
            '"url": "https://www.youtube.com/watch?v=abc"}')
        failed = mock.Mock(returncode=1, stdout='',
                           stderr='WARNING: cannot decrypt v11 cookies: no key found')
        ok = mock.Mock(returncode=0, stdout=payload, stderr='')
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            f.write('# Netscape HTTP Cookie File\n')
            path = f.name
        try:
            cfg = {'youtube_signin': 'brave'}
            with mock.patch.object(youtube, 'which', return_value='/usr/bin/yt-dlp'), \
                    mock.patch.object(youtube, 'DEFAULT_COOKIES', path), \
                    mock.patch.object(youtube.subprocess, 'run',
                                      side_effect=[failed, ok]) as run:
                out = youtube.fetch_recommendations(cfg)
            self.assertEqual([e['id'] for e in out], ['abc'])
            cmd = run.call_args_list[1].args[0]
            self.assertIn('--cookies', cmd)
            self.assertIn(path, cmd)
        finally:
            os.unlink(path)

    def test_not_signed_in_is_reported(self):
        with mock.patch.object(youtube, 'which', return_value='/usr/bin/yt-dlp'):
            with self.assertRaises(RuntimeError) as cm:
                youtube.fetch_recommendations({'youtube_signin': 'off'})
        self.assertIn('Not signed in', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
