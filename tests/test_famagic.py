"""Famagic app: due labels, task ordering and client config."""
import json
import os
import tempfile
import unittest
from datetime import date

from monocell.apps import famagic


class TestDueLabel(unittest.TestCase):
    def test_no_due(self):
        self.assertEqual(famagic.due_label({'due_date': None}, date(2026, 11, 9)), '')
        self.assertEqual(famagic.due_label({}, date(2026, 11, 9)), '')

    def test_today_and_tomorrow(self):
        self.assertEqual(famagic.due_label({'due_date': '2026-11-09'}, date(2026, 11, 9)), 'heute')
        self.assertEqual(famagic.due_label({'due_date': '2026-11-10'}, date(2026, 11, 9)), 'morgen')

    def test_overdue(self):
        self.assertEqual(famagic.due_label({'due_date': '2026-10-29'}, date(2026, 11, 9)), '11 T. überfällig')
        self.assertEqual(famagic.due_label({'due_date': '2026-11-08'}, date(2026, 11, 9)), '1 T. überfällig')

    def test_future_uses_dd_mm_yyyy(self):
        self.assertEqual(famagic.due_label({'due_date': '2026-11-12'}, date(2026, 11, 9)), '12-11-2026')
        self.assertEqual(
            famagic.due_label({'due_date': '2026-11-12', 'due_time': '09:00:00'}, date(2026, 11, 9)),
            '12-11-2026 09:00',
        )

    def test_far_future(self):
        self.assertEqual(famagic.due_label({'due_date': '2027-03-31'}, date(2026, 11, 9)), '31-03-2027')

    def test_broken_date_is_returned(self):
        self.assertEqual(famagic.due_label({'due_date': 'kaputt'}, date(2026, 11, 9)), 'kaputt')


def task(tid, list_id='L1', title='t', done=False, due=None, pos=0, parent=None, completed_at=None):
    return {
        'id': tid, 'list_id': list_id, 'title': title, 'is_completed': done,
        'due_date': due, 'position': pos, 'parent_task_id': parent, 'completed_at': completed_at,
    }


class TestRows(unittest.TestCase):
    def test_filters_other_lists_and_subtasks(self):
        snap = {'tasks': [
            task('a'),
            task('b', list_id='L2'),
            task('c', parent='a'),
        ]}
        self.assertEqual([t['id'] for t in famagic.task_rows(snap, 'L1')], ['a'])

    def test_open_before_done_and_sorted(self):
        snap = {'tasks': [
            task('done1', done=True, completed_at='2026-11-08T10:00:00+00:00'),
            task('done2', done=True, completed_at='2026-11-09T10:00:00+00:00'),
            task('late', due='2026-12-01', pos=5),
            task('early', due='2026-11-10', pos=9),
            task('none', due=None, pos=1),
        ]}
        ids = [t['id'] for t in famagic.task_rows(snap, 'L1')]
        self.assertEqual(ids, ['early', 'late', 'none', 'done2', 'done1'])

    def test_same_due_uses_position(self):
        snap = {'tasks': [
            task('b', due='2026-11-10', pos=2),
            task('a', due='2026-11-10', pos=1),
        ]}
        self.assertEqual([t['id'] for t in famagic.task_rows(snap, 'L1')], ['a', 'b'])

    def test_archived_lists_hidden(self):
        snap = {'lists': [
            {'id': 'L1', 'title': 'A', 'archived_at': None},
            {'id': 'L2', 'title': 'B', 'archived_at': '2026-01-01'},
        ]}
        self.assertEqual([l['id'] for l in famagic.list_titles(snap)], ['L1'])


class TestShoppingRows(unittest.TestCase):
    def test_open_first_sorted_by_position(self):
        snap = {'shopping_lists': [{'id': 'S1', 'items': [
            {'id': 'b', 'is_checked': False, 'position': 2, 'name': 'Brot'},
            {'id': 'a', 'is_checked': False, 'position': 1, 'name': 'Milch'},
            {'id': 'z', 'is_checked': True, 'position': 3, 'name': 'Eier'},
        ]}]}
        self.assertEqual([i['id'] for i in famagic.shopping_rows(snap, 'S1')], ['a', 'b', 'z'])

    def test_missing_list(self):
        self.assertEqual(famagic.shopping_rows({'shopping_lists': []}, 'S1'), [])


class TestMemberMap(unittest.TestCase):
    def test_profile_name_wins(self):
        snap = {'members': [
            {'id': 'm1', 'first_name': None, 'role': 'Vater', 'profile': {'first_name': 'Alexander'}},
            {'id': 'm2', 'first_name': 'Klemens', 'role': 'Kind'},
            {'id': 'm3', 'role': 'Kind'},
        ]}
        names = famagic.member_map(snap)
        self.assertEqual(names['m1'], 'Alexander')
        self.assertEqual(names['m2'], 'Klemens')
        self.assertEqual(names['m3'], 'Kind')


class TestClientConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        self.tmp.close()
        self.old_path = famagic.CLIENT_CFG
        self.old_env = {k: os.environ.get(k) for k in ('FAMAGIC_SERVER', 'FAMAGIC_TOKEN')}
        for k in self.old_env:
            os.environ.pop(k, None)
        famagic.CLIENT_CFG = self.tmp.name

    def tearDown(self):
        famagic.CLIENT_CFG = self.old_path
        os.unlink(self.tmp.name)
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default(self):
        cfg = famagic.client_config()
        self.assertEqual(cfg['server'], famagic.DEFAULT_SERVER)
        self.assertEqual(cfg['token'], '')

    def test_file(self):
        with open(self.tmp.name, 'w', encoding='utf-8') as f:
            json.dump({'server': 'http://192.168.33.24:8791', 'token': 'abc'}, f)
        cfg = famagic.client_config()
        self.assertEqual(cfg['server'], 'http://192.168.33.24:8791')
        self.assertEqual(cfg['token'], 'abc')

    def test_env_wins(self):
        with open(self.tmp.name, 'w', encoding='utf-8') as f:
            json.dump({'server': 'http://datei:1', 'token': 'datei'}, f)
        os.environ['FAMAGIC_SERVER'] = 'http://env:2'
        os.environ['FAMAGIC_TOKEN'] = 'env'
        cfg = famagic.client_config()
        self.assertEqual(cfg['server'], 'http://env:2')
        self.assertEqual(cfg['token'], 'env')

    def test_broken_file_falls_back(self):
        with open(self.tmp.name, 'w', encoding='utf-8') as f:
            f.write('{kaputt')
        cfg = famagic.client_config()
        self.assertEqual(cfg['server'], famagic.DEFAULT_SERVER)


if __name__ == '__main__':
    unittest.main()


class FakeWM:
    def __init__(self):
        self.toasts = []

    def toast(self, msg, color=None):
        self.toasts.append((msg, color))


class FakeWin:
    closed = False


class FakeList:
    def __init__(self):
        self.items = []
        self.sel = 0
        self.on_activate = None
        self.on_select = None

    def set_items(self, items, keep=False):
        self.items = list(items)

    def select(self, i):
        self.sel = i

    def current(self):
        return self.items[self.sel] if self.items and 0 <= self.sel < len(self.items) else None


SNAP = {
    'lists': [
        {'id': 'L1', 'title': 'Familienliste', 'is_default': True, 'is_private': False, 'archived_at': None},
        {'id': 'L2', 'title': 'Persönliche Liste', 'is_default': True, 'is_private': True, 'archived_at': None},
    ],
    'shopping_lists': [
        {'id': 'S1', 'title': 'Montag', 'is_private': False, 'items': []},
    ],
    'tasks': [
        {'id': 'T1', 'list_id': 'L1', 'title': 'Klemens Impftermin', 'is_completed': False,
         'due_date': '2026-11-01', 'priority': 'normal', 'assignee_member_ids': ['M1'],
         'position': 1, 'parent_task_id': None, 'notes': ''},
        {'id': 'T2', 'list_id': 'L1', 'title': 'Zahnarzt', 'is_completed': True,
         'due_date': None, 'priority': 'high', 'assignee_member_ids': [],
         'position': 2, 'parent_task_id': None, 'notes': 'Notiz'},
    ],
    'members': [
        {'id': 'M1', 'first_name': None, 'role': 'Vater', 'profile': {'first_name': 'Alexander'}},
        {'id': 'M2', 'first_name': 'Klemens', 'role': 'Kind'},
    ],
    'family': {'id': 'F1', 'name': 'Sturm Riedel'},
}


class AppCase(unittest.TestCase):
    def make_app(self, calls, tab=None):
        app = famagic.Famagic.__new__(famagic.Famagic)
        app.cfg = {'server': 'http://x', 'token': ''}
        app.snap = json.loads(json.dumps(SNAP))
        app.tab = tab or {'kind': 'task', 'id': 'L1', 'title': 'Familienliste'}
        app.list = FakeList()
        app.pending_select = None
        app.done_open = False
        app.filter_member = None
        app.filter_open = False
        app.filter_sel = 0
        app.filter_start = 0
        app.dropdown_open = False
        app.drop_sel = 0
        app.drop_start = 0
        app.mode = ''
        app.comp = None
        app.confirm = None
        app.status = ''
        app.err = ''
        app.busy = False
        app.wm = FakeWM()
        app.win = FakeWin()
        app.invalidate = lambda: None
        app.later = lambda delay, cb: None

        def thread(fn, done=None, bad=None):
            try:
                res = fn()
            except Exception as e:
                if bad:
                    bad(e)
                return
            if done:
                done(res)
        app.thread = thread

        def fake_http(cfg, path, method='GET', body=None, timeout=10):
            calls.append((method, path, body))
            if method == 'POST' and path == '/api/tasks':
                return {'id': 'NEW-T', 'success': True}
            if method == 'POST' and path == '/api/shopping/items':
                return {'id': 'NEW-S', 'success': True}
            if method == 'POST' and path in ('/api/lists', '/api/shopping/lists'):
                return {'id': 'NEW-L', 'success': True}
            return {'success': True}
        self._http = unittest.mock.patch.object(famagic, 'http_json', side_effect=fake_http)
        self._http.start()
        self.addCleanup(self._http.stop)
        return app


from unittest import mock  # noqa: E402


class TestToggleCalls(AppCase):
    def test_toggle_accepts_entry_from_listview(self):
        calls = []
        app = self.make_app(calls)
        entry = {'kind': 'task', 'task': SNAP['tasks'][0], 'who': []}
        app.toggle(entry)
        self.assertEqual(calls[-1], ('POST', '/api/tasks/T1/complete', {'completed': True}))

    def test_toggle_current_when_no_argument(self):
        calls = []
        app = self.make_app(calls)
        app.list.items = [{'kind': 'task', 'task': SNAP['tasks'][0], 'who': []}]
        app.toggle()
        self.assertEqual(calls[-1][0:2], ('POST', '/api/tasks/T1/complete'))

    def test_check_and_uncheck(self):
        calls = []
        app = self.make_app(calls)
        done = {'kind': 'task', 'task': dict(SNAP['tasks'][1]), 'who': []}
        app.toggle(done)
        self.assertEqual(calls[-1][2], {'completed': False})
        todo = {'kind': 'task', 'task': dict(SNAP['tasks'][0]), 'who': []}
        app.toggle(todo)
        self.assertEqual(calls[-1][2], {'completed': True})

    def test_shopping_toggle(self):
        calls = []
        app = self.make_app(calls)
        entry = {'kind': 'shopping', 'item': {'id': 'I1', 'name': 'Milch', 'is_checked': True}}
        app.toggle(entry)
        self.assertEqual(calls[-1], ('PATCH', '/api/shopping/items/I1', {'is_checked': False}))


class TestCompose(AppCase):
    def test_defaults_and_task_payload(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        self.assertEqual(app.mode, 'compose')
        self.assertEqual(app.comp['kind'], 'task')
        self.assertEqual(app.comp_list_id(), 'L1')
        self.assertEqual(app.compose_rows()[app.comp['field']], 'title')
        app.comp['fields']['title'].set('Blumen giessen')
        app.comp['fields']['due'].set('01-12-2026')
        app.comp['prio'] = 'high'
        app.comp['assignee'] = 'M1'
        app.save_compose()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('POST', '/api/tasks'))
        self.assertEqual(body['title'], 'Blumen giessen')
        self.assertEqual(body['list_id'], 'L1')
        self.assertEqual(body['due_date'], '2026-12-01')
        self.assertEqual(body['priority'], 'high')
        self.assertEqual(body['assignee_member_ids'], ['M1'])

    def test_shopping_payload(self):
        calls = []
        tab = {'kind': 'shopping', 'id': 'S1', 'title': 'Montag'}
        app = self.make_app(calls, tab=tab)
        app.open_compose()
        self.assertEqual(app.comp['kind'], 'shopping')
        self.assertEqual(app.comp_list_id(), 'S1')
        app.comp['fields']['title'].set('Eier')
        app.comp['fields']['qty'].set('4 x')
        app.save_compose()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('POST', '/api/shopping/items'))
        self.assertEqual(body['name'], 'Eier')
        self.assertEqual(body['quantity_text'], '4 x')

    def test_edit_task_payload(self):
        calls = []
        app = self.make_app(calls)
        entry = {'kind': 'task', 'task': SNAP['tasks'][1], 'who': []}
        app.open_compose(entry)
        self.assertEqual(app.comp['editing'], True)
        self.assertEqual(app.comp['fields']['title'].text, 'Zahnarzt')
        self.assertEqual(app.comp['prio'], 'high')
        app.comp['fields']['title'].set('Zahnarzt buchen')
        app.save_compose()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('PATCH', '/api/tasks/T2'))
        self.assertEqual(body['title'], 'Zahnarzt buchen')
        self.assertIn('due_date', body)

    def test_invalid_due_date_blocks_save(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.comp['fields']['title'].set('Test')
        app.comp['fields']['due'].set('32.13.2026')
        app.save_compose()
        self.assertEqual(calls, [])
        self.assertTrue(app.wm.toasts)
        self.assertEqual(app.mode, 'compose')

    def test_empty_title_blocks_save(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.comp['fields']['title'].set('   ')
        app.save_compose()
        self.assertEqual(calls, [])


class TestNavigation(AppCase):
    def test_dropdown_selects_shopping_list(self):
        calls = []
        app = self.make_app(calls)
        tabs = app.tabs()
        self.assertEqual([t['kind'] for t in tabs], ['task', 'task', 'shopping'])
        app.open_dropdown()
        self.assertTrue(app.dropdown_open)
        app.select_tab(tabs[2])
        self.assertEqual(app.tab['id'], 'S1')
        self.assertFalse(app.dropdown_open)

    def test_current_tab_index(self):
        calls = []
        app = self.make_app(calls)
        self.assertEqual(app.current_tab_index(), 0)
        app.tab = {'kind': 'shopping', 'id': 'S1', 'title': 'Montag'}
        self.assertEqual(app.current_tab_index(), 2)

    def test_dropdown_initial_selection(self):
        calls = []
        app = self.make_app(calls)
        app.tab = {'kind': 'shopping', 'id': 'S1', 'title': 'Montag'}
        app.open_dropdown()
        self.assertEqual(app.drop_sel, 2)

    def test_labels_are_full_names(self):
        calls = []
        app = self.make_app(calls)
        counts = {'L1': 1, 'S1': 3}
        tabs = app.tabs()
        self.assertIn('Familienliste', app.tab_label(tabs[0], counts))
        self.assertIn('Einkauf - Montag', app.tab_label(tabs[2], counts))
        self.assertIn('3 offen', app.tab_label(tabs[2], counts))


class Ev:
    def __init__(self, key='', char=None):
        self.key = key
        self.char = char


class TestKeysAndClicks(AppCase):
    def test_n_and_plus_open_compose(self):
        for key in ('n', 'a', '+'):
            calls = []
            app = self.make_app(calls)
            app.on_key(Ev(key))
            self.assertEqual(app.mode, 'compose')
            self.assertEqual(app.compose_rows()[app.comp['field']], 'title')

    def test_l_opens_dropdown(self):
        calls = []
        app = self.make_app(calls)
        app.on_key(Ev('l'))
        self.assertTrue(app.dropdown_open)
        self.assertEqual(app.drop_sel, 0)

    def test_dropdown_keyboard_navigation(self):
        calls = []
        app = self.make_app(calls)
        app.on_key(Ev('l'))
        app.on_key(Ev('down'))
        app.on_key(Ev('down'))
        app.on_key(Ev('enter'))
        self.assertFalse(app.dropdown_open)
        self.assertEqual(app.tab['id'], 'S1')

    def test_dropdown_escape(self):
        calls = []
        app = self.make_app(calls)
        app.on_key(Ev('l'))
        app.on_key(Ev('esc'))
        self.assertFalse(app.dropdown_open)

    def test_space_toggles_selected(self):
        calls = []
        app = self.make_app(calls)
        app.list.items = [{'kind': 'task', 'task': SNAP['tasks'][0], 'who': []}]
        app.on_key(Ev(' '))
        self.assertEqual(calls[-1][0:2], ('POST', '/api/tasks/T1/complete'))

    def test_r_reloads(self):
        calls = []
        app = self.make_app(calls)
        loaded = []
        app.load = lambda: loaded.append(True)
        app.on_key(Ev('r'))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(app.status, 'lade ...')

    def test_click_new_and_dropdown(self):
        calls = []
        app = self.make_app(calls)
        app.on_click('new', None)
        self.assertEqual(app.mode, 'compose')
        app.mode = ''
        app.on_click('dropdown', None)
        self.assertTrue(app.dropdown_open)
        app.on_click('drop:2', None)
        self.assertEqual(app.tab['id'], 'S1')
        self.assertFalse(app.dropdown_open)


class TestDates(unittest.TestCase):
    def test_format(self):
        self.assertEqual(famagic.format_due(date(2026, 11, 9)), '09-11-2026')

    def test_parse(self):
        self.assertEqual(famagic.parse_due_text('09-11-2026'), date(2026, 11, 9))
        self.assertEqual(famagic.parse_due_text('9.11.2026'), date(2026, 11, 9))
        self.assertEqual(famagic.parse_due_text(''), None)
        with self.assertRaises(ValueError):
            famagic.parse_due_text('2026-11-09')
        with self.assertRaises(ValueError):
            famagic.parse_due_text('32-13-2026')

    def test_calendar_weeks(self):
        weeks = famagic.calendar_weeks(2026, 11)
        self.assertEqual(len(weeks), 6)
        self.assertEqual(len(weeks[0]), 7)
        self.assertEqual(weeks[0][6], 1)
        days = [d for w in weeks for d in w if d]
        self.assertEqual(len(days), 30)
        self.assertEqual(days[0], 1)
        self.assertEqual(days[-1], 30)


class TestClickAction(unittest.TestCase):
    def test_boundaries(self):
        w = 90
        self.assertEqual(famagic.click_action(w - 1, w), 'delete')
        self.assertEqual(famagic.click_action(w - 2, w), 'delete')
        self.assertEqual(famagic.click_action(w - 3, w), 'edit')
        self.assertEqual(famagic.click_action(w - 5, w), 'edit')
        self.assertEqual(famagic.click_action(w - 6, w), 'select')
        self.assertEqual(famagic.click_action(0, w), 'toggle')
        self.assertEqual(famagic.click_action(3, w), 'toggle')
        self.assertEqual(famagic.click_action(4, w), 'select')


class TestCalendar(AppCase):
    def test_open_pick_and_clear(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.comp['fields']['due'].set('01-12-2026')
        app.open_calendar()
        self.assertEqual((app.cal['y'], app.cal['m'], app.cal['d']), (2026, 12, 1))
        app.key_calendar(Ev('left'))
        self.assertEqual(app.cal['d'], 30)
        app.key_calendar(Ev('enter'))
        self.assertIsNone(app.cal)
        self.assertEqual(app.comp['fields']['due'].text, '30-11-2026')
        app.open_calendar()
        app.key_calendar(Ev('x'))
        self.assertEqual(app.comp['fields']['due'].text, '')

    def test_due_enter_opens_calendar_instead_of_saving(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.comp['field'] = app.compose_rows().index('due')
        app.on_key(Ev('enter'))
        self.assertIsNotNone(app.cal)
        self.assertEqual(calls, [])

    def test_day_click_picks(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.open_calendar()
        app.on_click('cal:day:2026-12-24', None)
        self.assertEqual(app.comp['fields']['due'].text, '24-12-2026')


class TestNewList(AppCase):
    def test_create_task_list(self):
        calls = []
        app = self.make_app(calls)
        app.on_click('newlist', None)
        self.assertEqual(app.mode, 'newlist')
        app.newlist['fields']['title'].set('Einkauf Samstag')
        app.newlist['private'] = True
        app.save_newlist()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('POST', '/api/lists'))
        self.assertEqual(body, {'title': 'Einkauf Samstag', 'is_private': True})
        self.assertEqual(app.pending_tab, {'kind': 'task', 'id': 'NEW-L'})
        self.assertEqual(app.mode, '')

    def test_create_shopping_list(self):
        calls = []
        app = self.make_app(calls)
        app.on_click('newlist', None)
        app.newlist['kind'] = 'shopping'
        app.newlist['fields']['title'].set('Wochenende')
        app.save_newlist()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('POST', '/api/shopping/lists'))

    def test_empty_name_blocks(self):
        calls = []
        app = self.make_app(calls)
        app.on_click('newlist', None)
        app.save_newlist()
        self.assertEqual(calls, [])
        self.assertEqual(app.mode, 'newlist')


class TestDeleteConfirm(AppCase):
    def test_ask_delete_and_confirm(self):
        calls = []
        app = self.make_app(calls)
        entry = {'kind': 'task', 'task': SNAP['tasks'][0], 'who': []}
        app.ask_delete(entry)
        self.assertIsNotNone(app.confirm)
        self.assertEqual(app.confirm['kind'], 'task')
        app.on_key(Ev('y'))
        self.assertIsNone(app.confirm)
        self.assertEqual(calls[-1][0:2], ('DELETE', '/api/tasks/T1'))

    def test_cancel(self):
        calls = []
        app = self.make_app(calls)
        app.ask_delete({'kind': 'shopping', 'item': {'id': 'I1', 'name': 'Milch'}})
        app.on_key(Ev('n'))
        self.assertIsNone(app.confirm)
        self.assertEqual(calls, [])


class TestRollover(AppCase):
    def test_serie_status_and_toast(self):
        calls = []
        app = self.make_app(calls)
        res = {'success': True, 'outcome': 'rolled_over', 'next_due_date': '2027-01-01', 'is_completed': False}
        with mock.patch.object(famagic, 'http_json', return_value=res):
            entry = {'kind': 'task', 'task': SNAP['tasks'][0], 'who': []}
            app.toggle(entry)
        self.assertIn('2027-01-01', app.status)
        self.assertTrue(app.wm.toasts)


class TestPrivateLists(AppCase):
    def test_private_list_hides_assignee(self):
        calls = []
        tab = {'kind': 'task', 'id': 'L2', 'title': 'Persönliche Liste'}
        app = self.make_app(calls, tab=tab)
        app.open_compose()
        self.assertNotIn('assignee', app.compose_rows())
        app.comp['fields']['title'].set('Privater Eintrag')
        app.comp['assignee'] = 'M1'
        app.save_compose()
        method, path, body = calls[-1]
        self.assertEqual((method, path), ('POST', '/api/tasks'))
        self.assertNotIn('assignee_member_ids', body)

    def test_switching_to_private_clears_assignee(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        self.assertIn('assignee', app.compose_rows())
        app.comp['assignee'] = 'M1'
        app.comp['field'] = app.compose_rows().index('list')
        app.on_key(Ev('right'))
        self.assertNotIn('assignee', app.compose_rows())
        self.assertIsNone(app.comp['assignee'])
        app.comp['fields']['title'].set('Wechsel')
        app.save_compose()
        self.assertNotIn('assignee_member_ids', calls[-1][2])

    def test_editing_private_task_hides_assignee(self):
        calls = []
        app = self.make_app(calls)
        task = {'id': 'T9', 'list_id': 'L2', 'title': 'Privat', 'is_completed': False,
                'due_date': None, 'priority': 'normal', 'assignee_member_ids': [],
                'position': 1, 'parent_task_id': None, 'notes': ''}
        app.snap['tasks'].append(task)
        app.open_compose({'kind': 'task', 'task': task, 'who': []})
        self.assertNotIn('assignee', app.compose_rows())


class TestDoneSection(AppCase):
    def test_toggle_expands_and_collapses(self):
        calls = []
        app = self.make_app(calls)
        app.rebuild()
        kinds = [it['kind'] for it in app.list.items]
        self.assertIn('toggle', kinds)
        self.assertFalse(any(it['kind'] == 'task' and it['task']['is_completed'] for it in app.list.items))
        toggle = next(it for it in app.list.items if it['kind'] == 'toggle')
        self.assertEqual(toggle['count'], 1)
        app.toggle(toggle)
        self.assertTrue(app.done_open)
        self.assertTrue(any(it['kind'] == 'task' and it['task']['is_completed'] for it in app.list.items))
        toggle = next(it for it in app.list.items if it['kind'] == 'toggle')
        app.toggle(toggle)
        self.assertFalse(app.done_open)
        self.assertFalse(any(it['kind'] == 'task' and it['task']['is_completed'] for it in app.list.items))

    def test_no_toggle_without_done(self):
        calls = []
        app = self.make_app(calls)
        app.snap['tasks'] = [t for t in app.snap['tasks'] if not t['is_completed']]
        app.rebuild()
        self.assertFalse(any(it['kind'] == 'toggle' for it in app.list.items))

    def test_switching_list_resets_done(self):
        calls = []
        app = self.make_app(calls)
        app.rebuild()
        app.done_open = True
        tabs = app.tabs()
        app.select_tab(tabs[1])
        self.assertFalse(app.done_open)


class TestFilter(AppCase):
    def test_filter_by_member(self):
        calls = []
        app = self.make_app(calls)
        app.filter_member = 'M1'
        app.rebuild()
        tasks = [it['task'] for it in app.list.items if it['kind'] == 'task']
        self.assertEqual([t['id'] for t in tasks], ['T1'])
        self.assertFalse(any(it['kind'] == 'toggle' for it in app.list.items))

    def test_filter_unassigned(self):
        calls = []
        app = self.make_app(calls)
        app.filter_member = 'none'
        app.rebuild()
        tasks = [it['task'] for it in app.list.items if it['kind'] == 'task']
        self.assertEqual(tasks, [])
        toggle = next(it for it in app.list.items if it['kind'] == 'toggle')
        self.assertEqual(toggle['count'], 1)
        app.done_open = True
        app.rebuild()
        done = [it['task']['id'] for it in app.list.items if it['kind'] == 'task']
        self.assertEqual(done, ['T2'])

    def test_filter_all(self):
        calls = []
        app = self.make_app(calls)
        app.rebuild()
        ids = [it['task']['id'] for it in app.list.items if it['kind'] == 'task']
        self.assertEqual(ids, ['T1'])
        toggle = next(it for it in app.list.items if it['kind'] == 'toggle')
        self.assertEqual(toggle['count'], 1)

    def test_filter_menu_selects_member(self):
        calls = []
        app = self.make_app(calls)
        app.on_key(Ev('f'))
        self.assertTrue(app.filter_open)
        app.on_key(Ev('down'))
        app.on_key(Ev('down'))
        app.on_key(Ev('enter'))
        self.assertFalse(app.filter_open)
        self.assertEqual(app.filter_member, 'M1')
        ids = [it['task']['id'] for it in app.list.items if it['kind'] == 'task']
        self.assertEqual(ids, ['T1'])

    def test_filter_labels_and_counts(self):
        calls = []
        app = self.make_app(calls)
        labels = [o['label'] for o in app.filter_options()]
        self.assertEqual(labels[0], 'Alle')
        self.assertEqual(labels[1], 'Niemand')
        self.assertIn('Alexander', labels)
        counts = app.filter_counts()
        self.assertEqual(counts[None], 1)
        self.assertEqual(counts['M1'], 1)
        self.assertEqual(counts['none'], 0)


class TestRecurrence(AppCase):
    def test_create_weekly(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        self.assertIn('repeat', app.compose_rows())
        app.comp['field'] = app.compose_rows().index('repeat')
        app.on_key(Ev('right'))
        app.on_key(Ev('right'))
        self.assertEqual(app.comp['repeat'], 'weekly')
        self.assertTrue(app.comp['fields']['due'].text)
        app.comp['fields']['title'].set('Serie')
        app.save_compose()
        body = calls[-1][2]
        self.assertEqual(body['recurrence_rule'], {'freq': 'weekly', 'interval': 1})

    def test_edit_recurring_changes_rule(self):
        calls = []
        app = self.make_app(calls)
        task = {'id': 'T3', 'list_id': 'L1', 'title': 'Jährlich', 'is_completed': False,
                'due_date': '2027-01-01', 'priority': 'normal', 'assignee_member_ids': [],
                'position': 1, 'parent_task_id': None, 'notes': '',
                'recurrence_rule': {'freq': 'yearly', 'interval': 1}}
        app.snap['tasks'].append(task)
        app.open_compose({'kind': 'task', 'task': task, 'who': []})
        self.assertTrue(app.comp['show_repeat'])
        self.assertEqual(app.comp['repeat'], 'yearly')
        self.assertIn('repeat', app.compose_rows())
        app.comp['repeat'] = 'monthly'
        app.comp['fields']['title'].set('Jährlich')
        app.save_compose()
        self.assertEqual(calls[-1][2]['recurrence_rule'], {'freq': 'monthly', 'interval': 1})

    def test_edit_clears_recurrence(self):
        calls = []
        app = self.make_app(calls)
        task = {'id': 'T3', 'list_id': 'L1', 'title': 'Serie', 'is_completed': False,
                'due_date': '2027-01-01', 'priority': 'normal', 'assignee_member_ids': [],
                'position': 1, 'parent_task_id': None, 'notes': '',
                'recurrence_rule': {'freq': 'daily', 'interval': 7}}
        app.snap['tasks'].append(task)
        app.open_compose({'kind': 'task', 'task': task, 'who': []})
        self.assertEqual(app.comp['repeat'], 'weekly')
        app.comp['repeat'] = 'none'
        app.comp['fields']['title'].set('Serie')
        app.save_compose()
        self.assertIsNone(calls[-1][2]['recurrence_rule'])

    def test_edit_non_recurring_shows_repeat_but_keeps_none(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose({'kind': 'task', 'task': SNAP['tasks'][1], 'who': []})
        self.assertIn('repeat', app.compose_rows())
        app.comp['fields']['title'].set('Zahnarzt')
        app.save_compose()
        self.assertNotIn('recurrence_rule', calls[-1][2])

    def test_add_recurrence_to_existing_task(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose({'kind': 'task', 'task': SNAP['tasks'][1], 'who': []})
        app.comp['repeat'] = 'monthly'
        app.comp['fields']['due'].set('01-01-2027')
        app.comp['fields']['title'].set('Zahnarzt')
        app.save_compose()
        self.assertEqual(calls[-1][2]['recurrence_rule'], {'freq': 'monthly', 'interval': 1})

    def test_repeat_requires_due_date(self):
        calls = []
        app = self.make_app(calls)
        app.open_compose()
        app.comp['repeat'] = 'yearly'
        app.comp['fields']['title'].set('Serie')
        app.comp['fields']['due'].set('')
        app.save_compose()
        self.assertEqual(calls, [])
        self.assertTrue(app.wm.toasts)


class TestPrivateListFilterInApp(AppCase):
    def test_person_filter_ignores_private_tasks(self):
        calls = []
        app = self.make_app(calls)
        app.snap['lists'].append({'id': 'P1', 'title': 'Privat', 'is_private': True, 'archived_at': None})
        app.snap['tasks'].append({'id': 'PT', 'list_id': 'P1', 'title': 'Privat', 'is_completed': False,
                                  'due_date': '2026-11-10', 'priority': 'normal', 'assignee_member_ids': [],
                                  'position': 1, 'parent_task_id': None, 'notes': ''})
        app.filter_member = 'M1'
        self.assertTrue(app.task_matches_filter(app.snap['tasks'][-1]))
        app.filter_member = 'none'
        self.assertTrue(app.task_matches_filter(app.snap['tasks'][-1]))
