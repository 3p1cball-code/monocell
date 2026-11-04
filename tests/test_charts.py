"""Sheets charts: series extraction, ASCII bars and line plots."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from monocell import charts  # noqa: E402
from monocell.apps.sheet import SheetModel  # noqa: E402


def demo_model():
    m = SheetModel()
    data = [['Item', 'Qty', 'Price', 'Total'],
            ['Coffee', '2', '3.20', '=B2*C2'],
            ['Bread', '1', '2.80', '=B3*C3'],
            ['Cheese', '3', '1.95', '=B4*C4'],
            ['', '', 'Sum', '=SUM(D2:D4)']]
    for r, row in enumerate(data):
        for c, v in enumerate(row):
            if v:
                m.set(r, c, v)
    return m


class TestExtract(unittest.TestCase):
    def test_totals_with_labels(self):
        m = demo_model()
        d = charts.extract(m, 0, 0, 4, 3, 3)
        self.assertIsNotNone(d)
        self.assertEqual(d.title, 'Total')
        self.assertEqual(d.labels, ['Coffee', 'Bread', 'Cheese', 'Sum'])
        self.assertEqual([round(v, 2) for v in d.values], [6.4, 2.8, 5.85, 15.05])

    def test_qty_column(self):
        m = demo_model()
        d = charts.extract(m, 0, 0, 4, 3, 1)
        self.assertEqual(d.title, 'Qty')
        self.assertEqual(d.values, [2, 1, 3])
        self.assertEqual(d.labels[:3], ['Coffee', 'Bread', 'Cheese'])

    def test_single_value_column_selection(self):
        m = demo_model()
        d = charts.extract(m, 1, 3, 3, 3, 3)
        self.assertEqual(d.title, 'D')
        self.assertEqual(d.labels, ['2', '3', '4'])
        self.assertEqual(d.values, [6.4, 2.8, 5.85])

    def test_text_active_column_falls_back_to_numbers(self):
        m = demo_model()
        d = charts.extract(m, 0, 0, 4, 3, 0)
        self.assertEqual(d.title, 'Qty')

    def test_empty(self):
        self.assertIsNone(charts.extract(SheetModel(), 0, 0, 0, 0, 0))

    def test_no_numbers(self):
        m = SheetModel()
        m.set(0, 0, 'a')
        m.set(1, 0, 'b')
        self.assertIsNone(charts.extract(m, 0, 0, 1, 0, 0))


class TestBars(unittest.TestCase):
    def test_rows_and_length(self):
        data = charts.ChartData('T', ['A', 'B'], [1, 2], 1, 0, 1, False)
        rows = charts.bars_chart(data, 40, 4)
        text = [r[0] for r in rows]
        self.assertEqual(len(rows), 4)
        self.assertTrue(text[0].startswith('A'))
        self.assertTrue(text[1].startswith('B'))
        self.assertIn('#', text[0])
        self.assertIn('#', text[1])
        self.assertGreater(text[1].count('#'), text[0].count('#'))
        roles = rows[1][1]
        self.assertIn('accent', roles)
        self.assertEqual(len(rows[1][0]), 40)
        self.assertEqual(len(rows[1][0]), len(roles))

    def test_negative(self):
        data = charts.ChartData('T', ['A', 'B'], [-2, 1], 1, 0, 1, False)
        rows = charts.bars_chart(data, 40, 4)
        self.assertIn('red', rows[0][1])
        self.assertIn('accent', rows[1][1])

    def test_truncation_note(self):
        data = charts.ChartData('T', list('ABC'), [1, 2, 3], 1, 0, 2, False)
        rows = charts.bars_chart(data, 40, 2)
        self.assertTrue(any('more' in r[0] for r in rows))


class TestLine(unittest.TestCase):
    def test_points_and_axes(self):
        data = charts.ChartData('T', ['A', 'B', 'C'], [1, 5, 2], 1, 0, 2, False)
        rows = charts.line_chart(data, 40, 10)
        text = '\n'.join(r[0] for r in rows)
        self.assertEqual(len(rows), 10)
        self.assertIn('*', text)
        self.assertIn('+', text)
        self.assertIn('-', text)
        self.assertIn('A', text)
        self.assertIn('C', text)
        self.assertTrue(any('accent2' in r[1] for r in rows))
        self.assertTrue(any('accent' in r[1] for r in rows))

    def test_zero_line(self):
        data = charts.ChartData('T', ['A', 'B'], [-1, 1], 1, 0, 1, False)
        text = '\n'.join(r[0] for r in charts.line_chart(data, 40, 8))
        self.assertIn('.', text)

    def test_single_value(self):
        data = charts.ChartData('T', ['A'], [5], 1, 0, 0, False)
        rows = charts.line_chart(data, 30, 8)
        self.assertIn('*', '\n'.join(r[0] for r in rows))


if __name__ == '__main__':
    unittest.main()
