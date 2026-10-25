"""Tests for the two games: pure logic, no window system needed."""
import os
import random
import sys
import unittest
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell.apps.jump import JumpGame, Level, LEVEL_ROWS  # noqa: E402
from monocell.apps.snake import SnakeGame  # noqa: E402


FLAT = [
    '          ',
    '          ',
    '  P    o E',
    '##########',
]
WALL = [
    '          ',
    '          ',
    '  P #  o E',
    '##########',
]
SPIKE = [
    '          ',
    '          ',
    '  P  ^   E',
    '##########',
]
GAP = [
    '          ',
    '          ',
    '  P       ',
    '#### #####',
]


class TestSnake(unittest.TestCase):
    def test_initial_state(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        self.assertEqual(len(g.body), 3)
        self.assertIn(g.food, [(x, y) for y in range(6) for x in range(8)])
        self.assertNotIn(g.food, g.body)

    def test_eat_grows_and_scores(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        g.food = (5, 3)
        self.assertEqual(g.step(), 'eat')
        self.assertEqual(g.score, 1)
        self.assertEqual(len(g.body), 4)
        self.assertNotIn(g.food, g.body)

    def test_plain_move_keeps_length(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        g.food = (7, 0)
        self.assertEqual(g.step(), 'move')
        self.assertEqual(len(g.body), 3)
        self.assertEqual(g.body[0], (5, 3))

    def test_wall_kills(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        g.body = deque([(7, 3), (6, 3), (5, 3)])
        g.dir = (1, 0)
        g.food = (0, 0)
        self.assertEqual(g.step(), 'dead')
        self.assertEqual(g.state, 'dead')

    def test_reverse_turn_ignored(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        self.assertFalse(g.turn(-1, 0))
        self.assertTrue(g.turn(0, 1))
        self.assertEqual(g.step(), 'move')
        self.assertEqual(g.body[0], (4, 4))

    def test_self_collision(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        g.body = deque([(4, 4), (4, 3), (3, 3), (3, 4)])
        g.dir = (0, -1)
        g.food = (7, 0)
        self.assertEqual(g.step(), 'dead')

    def test_tail_cell_is_allowed(self):
        g = SnakeGame(8, 6, rng=random.Random(1))
        g.body = deque([(4, 4), (4, 3), (3, 3), (3, 4)])
        g.dir = (-1, 0)
        g.food = (7, 0)
        self.assertEqual(g.step(), 'move')
        self.assertEqual(g.body[0], (3, 4))

    def test_filling_the_board_wins(self):
        g = SnakeGame(4, 3, rng=random.Random(1))
        g.body = deque([(0, 1), (0, 0), (1, 0), (2, 0), (3, 0),
                        (3, 1), (3, 2), (2, 2), (1, 2), (0, 2)])
        g.dir = (1, 0)
        g.food = (1, 1)
        self.assertEqual(g.step(), 'eat')
        self.assertEqual(g.food, (2, 1))
        self.assertEqual(g.step(), 'won')
        self.assertEqual(g.state, 'won')


class TestLevel(unittest.TestCase):
    def test_parse(self):
        lv = Level(['~        ', '  P    o ', '   #  ^ E', '##########'])
        self.assertEqual(lv.spawn, (2, 1))
        self.assertEqual(lv.exit, (8, 2))
        self.assertIn((7, 1), lv.coins)
        self.assertIn((6, 2), lv.spikes)
        self.assertTrue(lv.is_solid(3, 2))
        self.assertFalse(lv.is_solid(3, 1))
        self.assertTrue(lv.is_solid(-1, 1))
        self.assertFalse(lv.is_solid(1, -1))
        self.assertFalse(lv.is_solid(1, lv.h))
        self.assertEqual(lv.tile(0, 0), '~')
        self.assertNotIn((0, 0), lv.solid)

    def test_real_levels(self):
        self.assertEqual(len(LEVEL_ROWS), 3)
        for rows in LEVEL_ROWS:
            lv = Level(rows)
            self.assertIsNotNone(lv.spawn)
            self.assertIsNotNone(lv.exit)
            self.assertGreater(len(lv.coins), 0)
            self.assertEqual(len({len(r) for r in lv.rows}), 1)


class TestJump(unittest.TestCase):
    def grounded(self, rows=FLAT, frames=12):
        g = JumpGame([rows])
        for _ in range(frames):
            g.update()
        self.assertTrue(g.on_ground)
        return g

    def test_lands_and_steps(self):
        g = self.grounded()
        x = g.x
        self.assertTrue(g.step(1))
        self.assertAlmostEqual(g.x, x + 1)
        g.step_cd = 0
        self.assertTrue(g.step(1))
        self.assertAlmostEqual(g.x, x + 2)

    def test_jump_and_land(self):
        g = self.grounded()
        start = g.y
        self.assertTrue(g.jump())
        low = g.y
        for _ in range(40):
            g.update()
            low = min(low, g.y)
        self.assertLess(low, start - 1.5)
        self.assertTrue(g.on_ground)

    def test_wall_blocks_a_step(self):
        g = self.grounded(WALL)
        g.step_cd = 0
        self.assertTrue(g.step(1))
        g.step_cd = 0
        self.assertFalse(g.step(1))
        self.assertAlmostEqual(g.x, 3.0, places=0)

    def test_coin_is_collected(self):
        g = self.grounded()
        g.x, g.y = 7.0, 2.0
        g.coins = {(7, 2)}
        g.update()
        self.assertEqual(g.collected, 1)
        self.assertNotIn((7, 2), g.coins)

    def test_spike_costs_a_life_and_respawns(self):
        g = self.grounded(SPIKE)
        g.x, g.y = 5.0, 2.0
        ev = g.update()
        self.assertEqual(ev, 'hurt')
        self.assertEqual(g.lives, 2)
        self.assertEqual((g.x, g.y), (2.0, 2.0))

    def test_exit_finishes_the_level(self):
        g = self.grounded()
        g.x, g.y = 9.0, 2.0
        self.assertEqual(g.update(), 'level')
        self.assertTrue(g.finished)

    def test_falling_through_a_gap_hurts(self):
        g = self.grounded(GAP)
        g.x, g.y = 4.0, 2.0
        for _ in range(60):
            ev = g.update()
            if ev == 'hurt':
                break
        self.assertEqual(ev, 'hurt')
        self.assertEqual(g.lives, 2)

    def test_next_level_and_win(self):
        g = JumpGame([FLAT, FLAT])
        self.assertEqual(g.level, 0)
        self.assertEqual(g.next_level(), 'next')
        self.assertEqual(g.level, 1)
        self.assertEqual(g.next_level(), 'won')
        self.assertTrue(g.finished and g.over)

    def test_jump_buffer(self):
        g = self.grounded()
        g.jump()
        for _ in range(20):
            g.update()
        g.jump()          # in the air: buffered
        for _ in range(40):
            g.update()
        self.assertTrue(g.on_ground)


if __name__ == '__main__':
    unittest.main()
