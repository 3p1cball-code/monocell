"""JezzBall: the rules, driven without a terminal."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monocell.apps.jezzball import (  # noqa: E402
    APP, GOAL, LIVES, TICK, Ball, JezzBall, JezzBallGame)
from monocell.app import app_info  # noqa: E402


def settle(g, ticks=200):
    """Run the wall build to its end (or the game to its end)."""
    for _ in range(ticks):
        g.tick(TICK)
        if not g.building:
            return
    raise AssertionError('wall did not finish')


class TestField(unittest.TestCase):
    def test_initial_state(self):
        g = JezzBallGame(12, 8, rng=random.Random(3))
        self.assertEqual(g.lives, LIVES)
        self.assertEqual(g.progress(), 0.0)
        self.assertEqual(g.state, 'running')
        self.assertEqual(len(g.balls), 2)
        self.assertGreaterEqual(len(g.balls), 2)
        for b in g.balls:
            self.assertTrue(g.free(*[int(b.x), int(b.y)]))

    def test_ball_count_scales_with_level(self):
        a = JezzBallGame(40, 20, level=1).ball_count()
        b = JezzBallGame(40, 20, level=4).ball_count()
        self.assertEqual(b, a + 3)
        self.assertLessEqual(JezzBallGame(90, 44, level=9).ball_count(), 8)

    def test_speed_scales_with_level(self):
        self.assertLess(JezzBallGame(20, 10, level=1).speed,
                        JezzBallGame(20, 10, level=3).speed)
        self.assertLessEqual(JezzBallGame(20, 10, level=50).speed, 34.0)

    def test_balls_keep_their_diagonal(self):
        g = JezzBallGame(24, 14, rng=random.Random(7))
        for _ in range(300):
            g.tick(TICK)
        for b in g.balls:
            self.assertAlmostEqual(abs(b.vx), abs(b.vy), places=6)

    def test_no_ball_ever_ends_up_inside_a_wall(self):
        g = JezzBallGame(16, 10, rng=random.Random(5))
        for _ in range(600):
            g.tick(TICK)
            for b in g.balls:
                self.assertFalse(g.blocked(b.x, b.y))


class TestWalls(unittest.TestCase):
    def still(self, w=12, h=8):
        """A field whose only ball never moves, so builds are deterministic."""
        g = JezzBallGame(w, h, rng=random.Random(1))
        g.balls = [Ball(1.5, h - 1.5, 0.0, 0.0)]
        return g

    def test_horizontal_wall_covers_the_row(self):
        g = self.still()
        g.start_wall(6, 1, 'h')
        self.assertEqual(g.building, {(6, 1)})
        self.assertEqual(sorted(g.wall_cells(6, 1, 'h')), [(x, 1) for x in range(12)])

    def test_vertical_wall_covers_the_column(self):
        g = self.still()
        g.start_wall(3, 4, 'v')
        self.assertEqual(sorted(g.wall_cells(3, 4, 'v')), [(3, y) for y in range(8)])

    def test_wall_grows_and_becomes_solid(self):
        g = self.still()
        g.start_wall(6, 1, 'h')
        self.assertEqual(len(g.building), 1)
        settle(g)
        self.assertEqual(g.walls, {(x, 1) for x in range(12)})
        self.assertEqual(g.building, set())
        for x in range(12):
            self.assertFalse(g.free(x, 1))

    def test_empty_area_is_captured(self):
        g = self.still()
        g.start_wall(6, 1, 'h')
        settle(g)
        self.assertEqual(g.last_gain, 12)          # row 0, no ball in it
        self.assertIn((3, 0), g.captured)
        self.assertNotIn((3, 6), g.captured)       # the ball still lives there
        self.assertAlmostEqual(g.progress(), 24 / 96.0)   # sealed row + the wall
        self.assertEqual(g.state, 'running')

    def test_area_with_a_ball_stays_open(self):
        g = self.still()
        g.balls = [Ball(6.5, 0.5, 0.0, 0.0)]
        g.start_wall(6, 4, 'h')
        settle(g)
        self.assertEqual(g.last_gain, 36)          # rows 5..7
        self.assertIn((0, 7), g.captured)
        self.assertFalse(any(y <= 3 for _, y in g.captured))
        self.assertEqual(g.state, 'running')

    def test_vertical_split_captures_the_empty_side(self):
        g = self.still()
        g.start_wall(3, 4, 'v')
        settle(g)
        self.assertEqual(sorted(g.walls), [(3, y) for y in range(8)])
        self.assertEqual(g.last_gain, 64)          # columns 4..11
        self.assertIn((11, 0), g.captured)
        self.assertNotIn((1, 0), g.captured)

    def test_lone_cell_is_not_a_wall(self):
        g = self.still()
        g.walls.update({(5, 4), (7, 4)})
        self.assertFalse(g.start_wall(6, 4, 'h'))
        self.assertEqual(g.building, set())
        self.assertEqual(g.walls, {(5, 4), (7, 4)})

    def test_cannot_build_on_a_wall(self):
        g = self.still()
        g.walls.add((2, 2))
        self.assertFalse(g.start_wall(2, 2, 'h'))

    def test_only_one_wall_at_a_time(self):
        g = self.still()
        self.assertTrue(g.start_wall(6, 1, 'h'))
        self.assertFalse(g.start_wall(6, 5, 'v'))
        self.assertFalse(g.start_wall(8, 1, 'h'))

    def test_placing_on_a_ball_destroys_the_wall_at_once(self):
        g = self.still()
        g.balls = [Ball(6.5, 1.5, 0.0, 0.0)]
        self.assertTrue(g.start_wall(6, 1, 'h'))
        self.assertEqual(g.building, set())
        self.assertEqual(g.lives, LIVES - 1)
        self.assertEqual(g.walls, set())
        self.assertGreater(g.flash, 0.0)

    def test_ball_touching_a_growing_wall_destroys_it(self):
        g = self.still()
        g.start_wall(6, 1, 'h')
        g.tick(TICK)
        self.assertIn((7, 1), g.building)
        g.balls = [Ball(7.5, 1.5, 0.0, 0.0)]       # now standing on it
        self.assertEqual(g.tick(TICK), 'hit')
        self.assertEqual(g.building, set())
        self.assertEqual(g.walls, set())
        self.assertEqual(g.lives, LIVES - 1)

    def test_three_lost_lives_end_the_game(self):
        g = self.still()
        for _ in range(LIVES):
            g.balls = [Ball(6.5, 1.5, 0.0, 0.0)]
            g.start_wall(6, 1, 'h')
        self.assertEqual(g.lives, 0)
        self.assertEqual(g.state, 'over')
        self.assertFalse(g.start_wall(6, 5, 'h'))

    def test_no_tick_after_the_game_is_over(self):
        g = self.still()
        g.state = 'over'
        self.assertIsNone(g.tick(TICK))


class TestCapture(unittest.TestCase):
    def test_clearing_the_goal_wins_the_level(self):
        g = JezzBallGame(10, 8, rng=random.Random(2))
        g.balls = []                               # nothing left to trap
        g.start_wall(5, 4, 'h')
        settle(g)
        self.assertEqual(g.progress(), 1.0)
        self.assertEqual(g.state, 'won')
        self.assertGreaterEqual(g.progress(), GOAL)

    def test_goal_is_reached_exactly_at_seventyfive_percent(self):
        g = JezzBallGame(8, 8, rng=random.Random(2))
        g.balls = []
        g.captured = {(x, y) for y in range(6) for x in range(8)}   # 48/64
        self.assertAlmostEqual(g.progress(), 0.75)
        g.start_wall(4, 7, 'h')                    # fills the rest
        settle(g)
        self.assertEqual(g.state, 'won')

    def test_captured_cells_block_the_balls(self):
        g = JezzBallGame(20, 10, rng=random.Random(0))
        g.balls = [Ball(4.0, 5.0, 8.0, 0.0)]
        for x in range(10, 20):
            for y in range(10):
                g.captured.add((x, y))
        left = right = False
        for _ in range(120):
            g.tick(TICK)
            self.assertLess(g.balls[0].x, 10.0)
            if g.balls[0].vx < 0:
                left = True
            if g.balls[0].vx > 0:
                right = True
        self.assertTrue(left and right)
        self.assertIn((15, 3), g.captured)

    def test_unstick_puts_a_ball_back_on_open_ground(self):
        g = JezzBallGame(10, 8)
        g.balls = [Ball(5.5, 4.5, 1.0, 1.0)]
        g.walls.add((5, 4))
        g.tick(TICK)
        b = g.balls[0]
        self.assertFalse(g.blocked(b.x, b.y))


class TestBorder(unittest.TestCase):
    def test_ball_bounces_off_the_border(self):
        g = JezzBallGame(10, 8, rng=random.Random(0))
        g.balls = [Ball(1.0, 4.0, -3.0, 0.0)]
        for _ in range(60):
            g.tick(TICK)
        self.assertGreater(g.balls[0].vx, 0)

    def test_ball_cannot_tunnel_through_a_thin_wall(self):
        g = JezzBallGame(20, 10, rng=random.Random(0))
        for y in range(10):
            g.walls.add((10, y))
        g.balls = [Ball(4.0, 5.0, 20.0, 0.0)]
        for _ in range(400):
            g.tick(TICK)
            self.assertLess(g.balls[0].x, 10.0)


class TestRegistry(unittest.TestCase):
    def test_registered_under_games(self):
        row = app_info('jezzball')
        self.assertIsNotNone(row)
        self.assertEqual(row[2], 'JezzBall')
        self.assertEqual(row[5], 'Games')
        self.assertIs(APP, JezzBall)


if __name__ == '__main__':
    unittest.main()


class _Loop:
    def every(self, interval, cb, owner=None):
        return type('T', (), {'cancel': lambda s: None})()

    def call_later(self, delay, cb, owner=None):
        return None


class _WM:
    def __init__(self):
        from monocell import theme as T
        self.cfg = _Cfg()
        self.theme = T.Theme({'palette': T.PRESETS['Ember'],
                              'roles': dict(T.ROLE_DEFAULTS),
                              'layout': [], 'on': dict(T.DEFAULT_ON)})
        self.loop = _Loop()
        self.dirty = False

    def close(self, win):
        win.closed = True


class _Cfg(dict):
    def save(self):
        pass


class _Win:
    def __init__(self, cw=60, ch=30):
        self.cw, self.ch = cw, ch
        self.closed = False
        self.title = ''


class _Key:
    def __init__(self, key, char=None):
        self.key, self.char = key, char


def make_app(cw=60, ch=30):
    app = JezzBall.__new__(JezzBall)
    app.wm, app.win = _WM(), _Win(cw, ch)
    app.th = app.wm.theme
    app.zones, app.hover, app.pressed = [], None, None
    app.best, app.game, app.field = 0, None, None
    app.aim, app.state, app.timer, app._last = [0, 0], 'start', None, None
    app._init(cw, ch)
    return app


class TestKeyboard(unittest.TestCase):
    """The console sends a plain space as ' ', not as 'space'."""

    def test_space_starts_and_then_builds(self):
        app = make_app()
        self.assertEqual(app.state, 'start')
        self.assertTrue(app.on_key(_Key(' ', ' ')))
        self.assertEqual(app.state, 'running')
        self.assertEqual(app.game.building, set())
        app.on_key(_Key(' ', ' '))
        self.assertTrue(app.game.building)

    def test_named_space_still_works(self):
        app = make_app()
        app.on_key(_Key('space'))
        app.on_key(_Key('space'))
        self.assertTrue(app.game.building)

    def test_enter_builds_a_vertical_wall(self):
        app = make_app()
        app.on_key(_Key('enter'))
        app.on_key(_Key('enter'))
        self.assertEqual(app.game.axis, 'v')

    def test_arrows_move_the_aim(self):
        app = make_app()
        ax, ay = app.aim
        app.on_key(_Key('up'))
        app.on_key(_Key('left'))
        self.assertEqual(app.aim, [ax - 1, ay - 1])

    def test_first_space_only_dismisses_the_start_screen(self):
        app = make_app()
        app.on_key(_Key(' '))
        self.assertEqual(app.state, 'running')
        self.assertEqual(app.game.walls, set())

    def test_q_closes(self):
        app = make_app()
        app.on_key(_Key('q'))
        self.assertTrue(app.win.closed)

    def test_no_field_means_no_crash(self):
        app = make_app(20, 10)          # too small for a board
        self.assertIsNone(app.field)
        self.assertFalse(app.on_key(_Key(' ')))
