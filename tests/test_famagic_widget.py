"""Famagic desktop widget: range/person filtering and labels."""
import unittest
from datetime import date

from monocell import famagic_widget as fw


def task(tid, due=None, done=False, members=None, list_id='L1', parent=None, title='t'):
    return {'id': tid, 'list_id': list_id, 'title': title, 'is_completed': done,
            'due_date': due, 'assignee_member_ids': members or [], 'parent_task_id': parent}


SNAP = {
    'lists': [
        {'id': 'L1', 'title': 'A', 'archived_at': None},
        {'id': 'L2', 'title': 'B', 'archived_at': '2026-01-01'},
    ],
    'members': [
        {'id': 'M1', 'first_name': None, 'role': 'Vater', 'profile': {'first_name': 'Alexander'}},
        {'id': 'M2', 'first_name': 'Klemens', 'role': 'Kind'},
    ],
    'tasks': [
        task('today', '2026-11-09', members=['M1']),
        task('week', '2026-11-13', members=['M2']),
        task('later', '2026-11-20', members=['M1']),
        task('overdue', '2026-11-01'),
        task('done', '2026-11-10', done=True),
        task('sub', '2026-11-10', parent='today'),
        task('archived', '2026-11-10', list_id='L2'),
        task('nodue', None, members=['M2']),
    ],
}


class TestFamagicWidget(unittest.TestCase):
    def test_range_end(self):
        today = date(2026, 11, 9)  # Monday
        self.assertEqual(fw.range_end('today', today), today)
        self.assertEqual(fw.range_end('week', today), date(2026, 11, 15))
        self.assertEqual(fw.range_end('7', today), date(2026, 11, 15))
        self.assertEqual(fw.range_end('14', today), date(2026, 11, 22))
        self.assertEqual(fw.range_end('30', today), date(2026, 12, 8))
        self.assertIsNone(fw.range_end('all', today))

    def test_week_includes_overdue(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'week', '*', None, date(2026, 11, 9))]
        self.assertEqual(ids, ['overdue', 'today', 'week'])

    def test_today(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'today', '*', None, date(2026, 11, 9))]
        self.assertEqual(ids, ['overdue', 'today'])

    def test_member(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'week', 'M2', None, date(2026, 11, 9))]
        self.assertEqual(ids, ['week'])
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'week', 'none', None, date(2026, 11, 9))]
        self.assertEqual(ids, ['overdue'])

    def test_all_includes_nodue(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'all', '*', None, date(2026, 11, 9))]
        self.assertEqual(ids, ['overdue', 'today', 'week', 'later', 'nodue'])

    def test_excludes_done_sub_archived(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP, 'all', '*', None, date(2026, 11, 9))]
        self.assertNotIn('done', ids)
        self.assertNotIn('sub', ids)
        self.assertNotIn('archived', ids)

    def test_limit(self):
        self.assertEqual(len(fw.filter_tasks(SNAP, 'all', '*', 2, date(2026, 11, 9))), 2)

    def test_due_text(self):
        today = date(2026, 11, 9)
        self.assertEqual(fw.due_text(task('a', '2026-11-09'), today), 'heute')
        self.assertEqual(fw.due_text(task('a', '2026-11-10'), today), 'morgen')
        self.assertEqual(fw.due_text(task('a', '2026-11-01'), today), '8 T. überfällig')
        self.assertEqual(fw.due_text(task('a', '2026-12-01'), today), '01-12-2026')
        self.assertEqual(fw.due_text(task('a'), today), '')

    def test_status(self):
        self.assertEqual(fw.status(None, 'x'), 'offline')
        self.assertEqual(fw.status({'offline': True}, None), 'online-offline')
        self.assertEqual(fw.status({'offline': False}, None), 'online')
        self.assertEqual(fw.status(None, None), 'loading')

    def test_member_options(self):
        opts = fw.member_options(SNAP)
        self.assertEqual(opts[:2], [('*', 'all'), ('none', 'nobody')])
        self.assertIn(('M1', 'Alexander'), opts)
        self.assertIn(('M2', 'Klemens'), opts)


SNAP2 = {
    'lists': [
        {'id': 'L1', 'title': 'Familie', 'archived_at': None},
        {'id': 'L2', 'title': 'Archiv', 'archived_at': '2026-01-01'},
        {'id': 'L3', 'title': 'Arbeit', 'archived_at': None},
    ],
    'members': [],
    'tasks': [
        task('a', '2026-11-10', list_id='L1'),
        task('b', '2026-11-10', list_id='L3'),
    ],
}


class TestListSelection(unittest.TestCase):
    def test_all(self):
        self.assertEqual(fw.selected_list_ids(SNAP2, None), {'L1', 'L3'})
        self.assertEqual(fw.selected_list_ids(SNAP2, []), set())
        self.assertEqual(fw.selected_list_ids(SNAP2, '*'), {'L1', 'L3'})

    def test_explicit(self):
        self.assertEqual(fw.selected_list_ids(SNAP2, ['L1']), {'L1'})
        self.assertEqual(fw.selected_list_ids(SNAP2, 'L1,L2'), {'L1'})

    def test_filter_tasks_list_ids(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP2, 'all', '*', None, date(2026, 11, 9), list_ids=['L1'])]
        self.assertEqual(ids, ['a'])
        ids = [t['id'] for t in fw.filter_tasks(SNAP2, 'all', '*', None, date(2026, 11, 9), list_ids=[])]
        self.assertEqual(ids, [])


SNAP_PRIV = {
    'lists': [
        {'id': 'FAM', 'title': 'Familie', 'archived_at': None, 'is_private': False},
        {'id': 'PRIV', 'title': 'Persönlich', 'archived_at': None, 'is_private': True},
    ],
    'members': [],
    'tasks': [
        task('fam_unassigned', '2026-11-10', list_id='FAM'),
        task('fam_alex', '2026-11-10', list_id='FAM', members=['M1']),
        task('priv_unassigned', '2026-11-10', list_id='PRIV'),
        task('priv_assigned', '2026-11-10', list_id='PRIV', members=['M2']),
    ],
}


class TestPrivateListFilter(unittest.TestCase):
    def test_person_filter_ignores_private_lists(self):
        ids = [t['id'] for t in fw.filter_tasks(SNAP_PRIV, 'all', 'M1', None, date(2026, 11, 9))]
        self.assertIn('priv_unassigned', ids)
        self.assertIn('priv_assigned', ids)
        self.assertIn('fam_alex', ids)
        self.assertNotIn('fam_unassigned', ids)
        ids = [t['id'] for t in fw.filter_tasks(SNAP_PRIV, 'all', 'none', None, date(2026, 11, 9))]
        self.assertIn('priv_unassigned', ids)
        self.assertIn('priv_assigned', ids)
        self.assertIn('fam_unassigned', ids)
        self.assertNotIn('fam_alex', ids)
