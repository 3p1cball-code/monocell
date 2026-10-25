"""Snake: classic grid snake with a persistent high score.

The game logic is independent of the window system so the tests can drive it
without a terminal.  The app is a thin renderer/input wrapper around it.
"""
import random
from collections import deque

from ..app import App
from ..util import fit

INITIAL_INTERVAL = 0.12
MIN_INTERVAL = 0.055
SPEEDUP = 0.006


class SnakeGame:
    def __init__(self, w, h, rng=None):
        self.w = max(3, int(w))
        self.h = max(2, int(h))
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        cx, cy = self.w // 2, self.h // 2
        self.body = deque([(cx, cy), (cx - 1, cy), (cx - 2, cy)])
        self.dir = (1, 0)
        self.pending = None
        self.score = 0
        self.state = 'alive'
        self.food = None
        self.spawn_food()

    def spawn_food(self):
        if len(self.body) >= self.w * self.h:
            self.food = None
            self.state = 'won'
            return
        free = [(x, y) for y in range(self.h) for x in range(self.w)
                if (x, y) not in self.body]
        if not free:
            self.food = None
            self.state = 'won'
            return
        self.food = self.rng.choice(free)

    def turn(self, dx, dy):
        if self.state != 'alive':
            return False
        if (dx, dy) == self.dir or (dx, dy) == (-self.dir[0], -self.dir[1]):
            return False
        self.pending = (dx, dy)
        return True

    def step(self):
        if self.state != 'alive':
            return self.state
        if self.pending:
            self.dir, self.pending = self.pending, None
        hx, hy = self.body[0]
        nx, ny = hx + self.dir[0], hy + self.dir[1]
        if nx < 0 or nx >= self.w or ny < 0 or ny >= self.h:
            self.state = 'dead'
            return 'dead'
        grow = (nx, ny) == self.food
        if (nx, ny) in self.body:
            tail = self.body[-1]
            if grow or (nx, ny) != tail:
                self.state = 'dead'
                return 'dead'
        self.body.appendleft((nx, ny))
        if grow:
            self.score += 1
            self.spawn_food()
            return 'won' if self.state == 'won' else 'eat'
        self.body.pop()
        return 'move'


class Snake(App):
    name = 'snake'
    title = 'Snake'
    icon = '[S]'
    size = (58, 22)
    min_size = (24, 12)
    single = True
    preserve_colors = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        try:
            self.best = int(wm.cfg.get('snake_best', 0) or 0)
        except (TypeError, ValueError):
            self.best = 0
        self.timer = None
        self.board = None
        self.board_size = None
        self.game = None
        self.state = 'start'
        self.interval = INITIAL_INTERVAL
        self._init_game(self.cw, self.ch)

    # -- layout ---------------------------------------------------------
    def _geom(self, w, h):
        if w < 24 or h < 12:
            return None
        bw = max(16, min(60, w - 2))
        bh = max(8, min(24, h - 4))
        if bw > w - 2 or bh > h - 4:
            return None
        bx = (w - bw) // 2
        return bw, bh, bx, 3

    def _init_game(self, w, h):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        g = self._geom(w, h)
        self.board = None
        self.board_size = None
        self.game = None
        self.state = 'start'
        self.interval = INITIAL_INTERVAL
        if not g:
            return
        bw, bh, bx, by = g
        self.board = (bw, bh, bx, by)
        self.board_size = (bw, bh)
        self.game = SnakeGame(bw, bh)
        self._reset_timer()

    def _reset_timer(self):
        if self.timer:
            self.timer.cancel()
        self.timer = self.every(self.interval, self.tick)

    def on_resize(self, w, h):
        g = self._geom(w, h)
        size = (g[0], g[1]) if g else None
        if size != self.board_size:
            self._init_game(w, h)

    # -- game loop ------------------------------------------------------
    def tick(self):
        if not self.game or self.state != 'running':
            return
        if not self.focused():
            self.state = 'paused'
            self.invalidate()
            return
        ev = self.game.step()
        if ev in ('dead', 'won'):
            self.state = 'over' if ev == 'dead' else 'won'
            if self.game.score > self.best:
                self.best = self.game.score
                try:
                    self.wm.cfg['snake_best'] = self.best
                    self.wm.cfg.save()
                except Exception:
                    pass
        elif ev == 'eat':
            nxt = max(MIN_INTERVAL, INITIAL_INTERVAL - SPEEDUP * self.game.score)
            if nxt < self.interval - 1e-9:
                self.interval = nxt
                self._reset_timer()
        self.invalidate()

    def _turn(self, dx, dy):
        if not self.game:
            return False
        if self.state in ('over', 'won'):
            return True
        if self.state in ('start', 'paused'):
            self.state = 'running'
        self.game.turn(dx, dy)
        self.invalidate()
        return True

    def _toggle(self, start=False):
        if not self.game:
            return
        if self.state in ('over', 'won'):
            self.restart()
        elif self.state == 'running':
            self.state = 'paused'
        elif self.state == 'paused':
            self.state = 'running'
        elif self.state == 'start' and start:
            self.state = 'running'
        self.invalidate()

    def restart(self):
        if not self.game:
            return
        self.game.reset()
        self.state = 'start'
        self.interval = INITIAL_INTERVAL
        self._reset_timer()
        self.invalidate()

    def on_key(self, ev):
        k = (ev.key or '').lower()
        if k in ('up', 'w'):
            return self._turn(0, -1)
        if k in ('down', 's'):
            return self._turn(0, 1)
        if k in ('left', 'a'):
            return self._turn(-1, 0)
        if k in ('right', 'd'):
            return self._turn(1, 0)
        if k in ('space', 'enter'):
            self._toggle(start=True)
            return True
        if k in ('p', 'esc'):
            self._toggle(start=False)
            return True
        if k == 'r':
            self.restart()
            return True
        if k == 'q':
            self.close()
            return True
        return False

    def on_focus(self, focused):
        if not focused and self.state == 'running':
            self.state = 'paused'
            self.invalidate()

    # -- drawing --------------------------------------------------------
    def _overlay(self, cv, bx, by, bw, bh, title, lines):
        ow = min(bw - 2, 42)
        oh = min(bh - 2, len(lines) + 3)
        if ow < 10 or oh < 3:
            return
        ox = bx + (bw - ow) // 2
        oy = by + (bh - oh) // 2
        cv.fill(ox, oy, ow, oh, self.th.c('text', 'win'))
        cv.box(ox, oy, ow, oh, self.th.c('accent', 'win'))
        cv.put(ox + 2, oy, ' ' + title + ' ', self.th.c('accent', 'win', 1))
        for i, line in enumerate(lines[:oh - 2]):
            cv.put(ox + 1, oy + 1 + i, fit(line, ow - 2, 'center'),
                   self.th.c('bright' if i == 0 else 'text', 'win'))

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if not self.board or not self.game:
            if w > 0 and h > 0:
                cv.put(0, h // 2, fit('SNAKE - window too small', w, 'center'),
                       th.c('red', 'win'))
            return
        bw, bh, bx, by = self.board
        score = self.game.score
        length = len(self.game.body)
        cv.put(0, 0, 'SNAKE', th.c('accent', 'win', 1))
        if w >= 46:
            right = 'SCORE %d   BEST %d   LEN %d' % (score, self.best, length)
        elif w >= 32:
            right = 'S %d  B %d  L %d' % (score, self.best, length)
        else:
            right = '%d/%d' % (score, self.best)
        cv.put(max(0, w - len(right)), 0, right, th.c('dim', 'win'))
        hint = 'ARROWS/WASD  SPACE PAUSE  R RESTART  Q QUIT'
        cv.put(0, 1, fit(hint, w), th.c('dim', 'win'))

        cv.box(bx - 1, 2, bw + 2, bh + 2, th.c('dim', 'win'))
        cv.fill(bx, by, bw, bh, th.c('text', 'field'))
        for i, (x, y) in enumerate(self.game.body):
            if 0 <= x < bw and 0 <= y < bh:
                ch = '@' if i == 0 else '#'
                a = (th.c('on_accent', 'accent', 1) if i == 0
                     else th.c('green', 'field'))
                cv.putc(bx + x, by + y, ch, a)
        if self.game.food:
            fx, fy = self.game.food
            if 0 <= fx < bw and 0 <= fy < bh:
                cv.putc(bx + fx, by + fy, '*', th.c('red', 'field', 1))

        if self.state == 'start':
            self._overlay(cv, bx, by, bw, bh, 'SNAKE', [
                'ARROWS / WASD  TURN',
                'SPACE / ENTER  START',
                'R  RESTART    Q  QUIT',
                '',
                'BEST  %d' % self.best,
            ])
        elif self.state == 'paused':
            self._overlay(cv, bx, by, bw, bh, 'PAUSED', [
                'SPACE / P  CONTINUE',
                'R  RESTART',
                'Q  QUIT',
            ])
        elif self.state == 'over':
            self._overlay(cv, bx, by, bw, bh, 'GAME OVER', [
                'SCORE  %d' % score,
                'BEST  %d' % self.best,
                '',
                'SPACE / R  RESTART',
                'Q  QUIT',
            ])
        elif self.state == 'won':
            self._overlay(cv, bx, by, bw, bh, 'YOU WIN', [
                'PERFECT SNAKE',
                'SCORE  %d' % score,
                'BEST  %d' % self.best,
                '',
                'SPACE / R  RESTART',
            ])


APP = Snake
