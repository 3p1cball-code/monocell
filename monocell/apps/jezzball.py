"""JezzBall: cut the field in two while the balls keep bouncing.

The rules live in JezzBallGame and know nothing about the terminal, so the
unit tests drive them directly.

A wall is a run of cells: the aim cell and everything left and right (or up
and down) from there until the next solid cell.  It does not appear at once -
it grows outward from the aim cell, and a ball that touches any of its cells
while it grows destroys the whole wall and costs a life.  Once a wall is
finished the field is split: every area that no ball can reach is filled and
counts as captured.  Capture 75% of the field to clear the level.
"""
import math
import random
import time

from ..app import App
from ..util import fit

GOAL = 0.75
LIVES = 3
RADIUS = 0.34
BUILD_SPEED = 60.0        # cells per second, scaled with the field below
BUILD_SECONDS = 1.0       # time a wall needs for the whole field, edge to edge
BASE_SPEED = 20.0         # ball speed in cells per second (level 1)
LEVEL_SPEED = 1.5
MAX_SPEED = 30.0
TICK = 1.0 / 30.0

DIRS = {'h': ((-1, 0), (1, 0)), 'v': ((0, -1), (0, 1))}


class Ball:
    __slots__ = ('x', 'y', 'vx', 'vy')

    def __init__(self, x, y, vx, vy):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy


class JezzBallGame:
    def __init__(self, w, h, rng=None, level=1):
        self.w = max(8, int(w))
        self.h = max(6, int(h))
        self.rng = rng or random.Random()
        self.level = max(1, int(level))
        self.reset()

    # -- field ---------------------------------------------------------
    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def solid(self, x, y):
        return (not self.inside(x, y)) or (x, y) in self.walls or (x, y) in self.captured

    def free(self, x, y):
        return not self.solid(x, y)

    def open_cell(self, x, y):
        """Free and not already part of the wall that is being built."""
        return self.free(x, y) and (x, y) not in self.building

    def progress(self):
        """Share of the field the player owns: sealed areas plus the walls."""
        return (len(self.captured) + len(self.walls)) / float(self.w * self.h)

    def ball_count(self):
        base = max(2, min(4, int(round(self.w * self.h / 1400.0))))
        return min(8, base + self.level - 1)

    # -- setup ---------------------------------------------------------
    def reset(self):
        self.walls = set()
        self.captured = set()
        self.building = set()
        self.tips = []
        self.axis = None
        self.build_acc = 0.0
        self.lives = LIVES
        self.state = 'running'
        self.flash = 0.0          # seconds left of the "wall destroyed" notice
        self.gain = 0.0           # seconds left of the "area captured" notice
        self.last_gain = 0
        self.speed = min(MAX_SPEED, BASE_SPEED + LEVEL_SPEED * (self.level - 1))
        # A wall always needs the same time for the whole field, however wide
        # the window is - otherwise a big field is unplayable.
        self.build_speed = max(BUILD_SPEED, max(self.w, self.h) / BUILD_SECONDS)
        self.balls = []
        self._spawn_balls()

    def _spawn_balls(self):
        n = self.ball_count()
        s = self.speed / math.sqrt(2.0)
        mx = max(1.0, min(3.0, (self.w - 1) / 4.0))
        my = max(1.0, min(3.0, (self.h - 1) / 4.0))
        tries = 0
        while len(self.balls) < n and tries < 4000:
            tries += 1
            x = self.rng.uniform(mx, self.w - mx)
            y = self.rng.uniform(my, self.h - my)
            if any(math.hypot(b.x - x, b.y - y) < 4.0 for b in self.balls):
                continue
            vx = s if self.rng.random() < 0.5 else -s
            vy = s if self.rng.random() < 0.5 else -s
            self.balls.append(Ball(x, y, vx, vy))

    # -- wall ----------------------------------------------------------
    def wall_cells(self, cx, cy, axis):
        """Cells a wall started at (cx, cy) would fill, aim cell first."""
        cells = [(cx, cy)]
        for dx, dy in DIRS[axis]:
            x, y = cx + dx, cy + dy
            while self.open_cell(x, y):
                cells.append((x, y))
                x += dx
                y += dy
        return cells

    def start_wall(self, cx, cy, axis):
        """Begin a wall through (cx, cy).  True if it was placed at all."""
        if axis not in DIRS or self.state != 'running' or self.building:
            return False
        if not self.open_cell(cx, cy):
            return False
        if len(self.wall_cells(cx, cy, axis)) < 2:
            return False           # nothing to split - a lone cell is not a wall
        self.axis = axis
        self.building = {(cx, cy)}
        self.tips = []
        for dx, dy in DIRS[axis]:
            nx, ny = cx + dx, cy + dy
            if self.open_cell(nx, ny):
                self.tips.append([nx, ny, dx, dy])
        self.build_acc = 0.0
        if self._building_hit():
            self._fail_wall()
        return True

    def _grow(self, dt):
        self.build_acc += dt * self.build_speed
        while self.build_acc >= 1.0 and self.tips:
            self.build_acc -= 1.0
            keep = []
            for tip in self.tips:
                x, y, dx, dy = tip
                if not self.open_cell(x, y):
                    continue       # this direction reached a solid cell
                self.building.add((x, y))
                tip[0], tip[1] = x + dx, y + dy
                keep.append(tip)
            self.tips = keep
        if not self.tips:
            self._finish_wall()

    def _finish_wall(self):
        self.walls.update(self.building)
        self.building.clear()
        self.tips = []
        self.axis = None
        self.last_gain = self._capture()
        if self.last_gain:
            self.gain = 1.0
        if self.progress() >= GOAL:
            self.state = 'won'

    def _fail_wall(self):
        self.building.clear()
        self.tips = []
        self.axis = None
        self.last_gain = 0
        self.flash = 1.0
        self.lives -= 1
        if self.lives <= 0:
            self.state = 'over'

    # -- areas ---------------------------------------------------------
    def _flood(self, sx, sy, seen):
        stack = [(sx, sy)]
        seen.add((sx, sy))
        cells = []
        while stack:
            x, y = stack.pop()
            cells.append((x, y))
            for dx, dy in DIRS['h'] + DIRS['v']:
                nx, ny = x + dx, y + dy
                if (nx, ny) in seen or self.solid(nx, ny):
                    continue
                seen.add((nx, ny))
                stack.append((nx, ny))
        return cells

    def _capture(self):
        """Fill every area without a ball.  Returns the number of new cells."""
        alive = set()
        for b in self.balls:
            alive.update(self._cells(b.x, b.y))
        seen = set()
        gain = 0
        for y in range(self.h):
            for x in range(self.w):
                if (x, y) in seen or self.solid(x, y):
                    continue
                cells = self._flood(x, y, seen)
                if cells and not any(c in alive for c in cells):
                    self.captured.update(cells)
                    gain += len(cells)
        return gain

    # -- balls ---------------------------------------------------------
    def _cells(self, bx, by):
        """Field cells the ball's box overlaps (one, or up to four on a seam)."""
        r = RADIUS
        out = []
        for y in range(int(math.floor(by - r)), int(math.floor(by + r)) + 1):
            for x in range(int(math.floor(bx - r)), int(math.floor(bx + r)) + 1):
                if bx + r > x and x + 1 > bx - r and by + r > y and y + 1 > by - r:
                    out.append((x, y))
        return out

    def blocked(self, bx, by):
        return any(self.solid(x, y) for x, y in self._cells(bx, by))

    def _building_hit(self):
        if not self.building:
            return False
        for b in self.balls:
            for c in self._cells(b.x, b.y):
                if c in self.building:
                    return True
        return False

    def _unstick(self, b):
        """Put a ball back on open ground after the field changed under it."""
        start = (int(math.floor(b.x)), int(math.floor(b.y)))
        seen = {start}
        queue = [start]
        while queue:
            x, y = queue.pop(0)
            if self.free(x, y):
                b.x, b.y = x + 0.5, y + 0.5
                return
            for dx, dy in DIRS['h'] + DIRS['v']:
                n = (x + dx, y + dy)
                if n not in seen and self.inside(*n):
                    seen.add(n)
                    queue.append(n)

    def _move_ball(self, b, dt):
        if self.blocked(b.x, b.y):
            self._unstick(b)
        dist = math.hypot(b.vx, b.vy) * dt
        steps = max(1, int(math.ceil(dist / 0.1)))
        sdt = dt / steps
        for _ in range(steps):
            nx = b.x + b.vx * sdt
            if self.blocked(nx, b.y):
                b.vx = -b.vx
            else:
                b.x = nx
            ny = b.y + b.vy * sdt
            if self.blocked(b.x, ny):
                b.vy = -b.vy
            else:
                b.y = ny

    # -- main loop -----------------------------------------------------
    def tick(self, dt):
        if self.state != 'running':
            return None
        self.flash = max(0.0, self.flash - dt)
        self.gain = max(0.0, self.gain - dt)
        if self.building:
            self._grow(dt)
        for b in self.balls:
            self._move_ball(b, dt)
        if self.building and self._building_hit():
            self._fail_wall()
            return 'hit'
        return None


class JezzBall(App):
    name = 'jezzball'
    title = 'JezzBall'
    icon = '[o]'
    size = (100, 43)
    min_size = (34, 16)
    single = True
    preserve_colors = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        try:
            self.best = int(wm.cfg.get('jezzball_best', 0) or 0)
        except (TypeError, ValueError):
            self.best = 0
        self.game = None
        self.field = None
        self.aim = [0, 0]
        self.state = 'start'
        self.timer = None
        self._last = None
        self._init(self.cw, self.ch)

    # -- layout ---------------------------------------------------------
    def _geom(self, w, h):
        if w < 34 or h < 15:
            return None
        fw = min(96, w - 2)
        fh = min(44, h - 5)
        if fw < 24 or fh < 8:
            return None
        return fw, fh, (w - fw) // 2, 4

    def _init(self, w, h, level=1):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.field = None
        self.game = None
        self.state = 'start'
        g = self._geom(w, h)
        if not g:
            return
        fw, fh, bx, by = g
        self.field = (fw, fh, bx, by)
        self.game = JezzBallGame(fw, fh, level=level)
        self.aim = [fw // 2, fh // 2]
        self._last = None
        self.timer = self.every(TICK, self.tick)

    def on_resize(self, w, h):
        g = self._geom(w, h)
        size = (g[0], g[1]) if g else None
        cur = (self.field[0], self.field[1]) if self.field else None
        if size != cur:
            level = self.game.level if self.game else 1
            self._init(w, h, level=level)

    # -- loop -----------------------------------------------------------
    def tick(self):
        g = self.game
        now = time.monotonic()
        dt = TICK if self._last is None else now - self._last
        self._last = now
        if not g or self.state != 'running':
            return
        if not self.focused():
            self.state = 'paused'
            self.invalidate()
            return
        # real time, so a slow frame does not stretch the wall build
        g.tick(max(0.001, min(0.1, dt)))
        if g.state == 'over':
            self.state = 'over'
        elif g.state == 'won':
            self.state = 'clear'
            if g.level > self.best:
                self.best = g.level
                try:
                    self.wm.cfg['jezzball_best'] = self.best
                    self.wm.cfg.save()
                except Exception:
                    pass
            self.later(1.8, self._next_level)
        self.invalidate()

    def _next_level(self):
        g = self.game
        if self.state != 'clear' or not g:
            return
        level = g.level + 1
        self._init(self.cw, self.ch, level=level)
        self.state = 'running'
        self.invalidate()

    def _restart(self):
        self._init(self.cw, self.ch)
        self.invalidate()

    # -- input ----------------------------------------------------------
    def _aim(self, dx, dy):
        if not self.game:
            return
        if self.state == 'start':
            self.state = 'running'
        self.aim[0] = max(0, min(self.game.w - 1, self.aim[0] + dx))
        self.aim[1] = max(0, min(self.game.h - 1, self.aim[1] + dy))
        self.invalidate()

    def _build(self, axis):
        if not self.game:
            return
        if self.state == 'start':
            self.state = 'running'      # the first press only clears the overlay
            self.invalidate()
            return
        if self.state != 'running':
            return
        if self.game.start_wall(self.aim[0], self.aim[1], axis):
            self.invalidate()

    def _point(self, ev):
        if not self.field:
            return False
        fw, fh, bx, by = self.field
        cx, cy = ev.x - bx, ev.y - by
        if not (0 <= cx < fw and 0 <= cy < fh):
            return False
        if self.aim != [cx, cy]:
            self.aim[0], self.aim[1] = cx, cy
            self.invalidate()
        return True

    def on_key(self, ev):
        k = (ev.key or '').lower()
        if k == 'q':
            self.close()
            return True
        if not self.game:
            return False
        if self.state == 'clear':
            return True
        if self.state in ('over', 'won'):
            if k in (' ', 'space', 'enter', 'r'):
                self._restart()
                return True
            return False
        if self.state == 'paused':
            if k in ('p', 'esc', ' ', 'space', 'enter'):
                self.state = 'running'
                self.invalidate()
                return True
            if k == 'r':
                self._restart()
                return True
            return False
        if k in ('up', 'w'):
            self._aim(0, -1)
            return True
        if k in ('down', 's'):
            self._aim(0, 1)
            return True
        if k in ('left', 'a'):
            self._aim(-1, 0)
            return True
        if k in ('right', 'd'):
            self._aim(1, 0)
            return True
        if k in (' ', 'space', 'h'):
            self._build('h')
            return True
        if k in ('enter', 'v'):
            self._build('v')
            return True
        if k in ('p', 'esc'):
            if self.state == 'running':
                self.state = 'paused'
                self.invalidate()
            return True
        if k == 'r':
            self._restart()
            return True
        return False

    def on_mouse(self, ev):
        if not self.game or not self.field:
            return False
        if ev.kind in ('move', 'drag'):
            return self._point(ev)
        if ev.kind == 'down' and ev.button in (0, 2):
            # never the window's edit menu in the middle of a game
            if self.state == 'start':
                self.state = 'running'
                self._point(ev)
                self.invalidate()
                return True
            if self.state != 'running':
                return True
            if self._point(ev):
                self._build('h' if ev.button == 0 else 'v')
            return True
        return False

    def on_focus(self, focused):
        if not focused and self.state == 'running':
            self.state = 'paused'
            self.invalidate()

    # -- drawing --------------------------------------------------------
    def _overlay(self, cv, bx, by, bw, bh, title, lines):
        ow = min(bw - 2, 52)
        oh = min(bh - 2, len(lines) + 3)
        if ow < 12 or oh < 3:
            return
        ox = bx + (bw - ow) // 2
        oy = by + (bh - oh) // 2
        cv.fill(ox, oy, ow, oh, self.th.c('text', 'win'))
        cv.box(ox, oy, ow, oh, self.th.c('accent', 'win'))
        cv.put(ox + 2, oy, ' ' + title + ' ', self.th.c('accent', 'win', 1))
        for i, line in enumerate(lines[:oh - 2]):
            cv.put(ox + 1, oy + 1 + i, fit(line, ow - 2, 'center'),
                   self.th.c('bright' if i == 0 else 'text', 'win'))

    def _bar(self, cv, x, y, w, frac):
        """Capture bar with the goal marked by '|'."""
        th = self.th
        goal_at = min(w - 1, int(GOAL * w))
        cv.putc(x, y, '[', th.c('dim', 'win'))
        for i in range(w):
            if i == goal_at:
                cv.putc(x + 1 + i, y, '|', th.c('yellow', 'win', 1))
            elif (i + 0.5) / w <= frac:
                cv.putc(x + 1 + i, y, '=', th.c('green', 'win', 1))
            else:
                cv.putc(x + 1 + i, y, '.', th.c('dim', 'win'))
        cv.putc(x + 1 + w, y, ']', th.c('dim', 'win'))
        return w + 2

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if not self.game or not self.field:
            if w > 0 and h > 0:
                cv.put(0, h // 2, fit('JEZJBALL - window too small', w, 'center'),
                       th.c('red', 'win'))
            return
        g = self.game
        fw, fh, bx, by = self.field

        # --- header ---
        cv.put(0, 0, 'JEZJBALL', th.c('accent', 'win', 1))
        if w >= 52:
            right = 'LEVEL %d   LIVES %s   BALLS %d   BEST %d' % (
                g.level, '*' * g.lives if g.lives else '-', len(g.balls), self.best)
        elif w >= 34:
            right = 'L%d  LIFE %d  B%d' % (g.level, g.lives, len(g.balls))
        else:
            right = '%d/%d' % (g.level, g.lives)
        cv.put(max(0, w - len(right)), 0, right, th.c('dim', 'win'))

        # --- capture bar ---
        frac = g.progress()
        pct = int(round(frac * 100))
        if w >= 46:
            label = 'CAPTURED '
            cv.put(0, 1, label, th.c('dim', 'win'))
            used = self._bar(cv, len(label), 1, min(40, w - len(label) - 16), frac)
            rest = '%3d%%  GOAL %d%%' % (pct, int(GOAL * 100))
            cv.put(len(label) + used + 1, 1, rest, th.c('dim', 'win'))
        else:
            cv.put(0, 1, '%d%% / %d%%' % (pct, int(GOAL * 100)), th.c('dim', 'win'))

        # --- hint line ---
        if g.flash:
            hint, ha = 'WALL DESTROYED - a ball touched it', th.c('red', 'win', 1)
        elif g.gain:
            hint, ha = 'AREA CAPTURED +%d' % g.last_gain, th.c('green', 'win', 1)
        elif g.building:
            hint, ha = 'BUILDING - keep the balls away', th.c('yellow', 'win', 1)
        else:
            hint, ha = ('ARROWS AIM   SPACE HORIZONTAL   ENTER VERTICAL   '
                        'P PAUSE   R RESTART   Q QUIT'), th.c('dim', 'win')
        cv.put(0, 2, fit(hint, w), ha)

        # --- field ---
        bg = th.c('text', 'field')
        wall_a = th.c('accent', 'field', 1)
        cap_a = th.c('dim', 'field')
        build_a = th.c('yellow', 'field', 1)
        ball_a = th.c('red', 'field', 1)
        cross_a = th.c('dim', 'field')
        frame_a = th.c('accent', 'win')

        cv.put(bx - 1, by - 1, '+' + '-' * fw + '+', frame_a)
        cv.put(bx - 1, by + fh, '+' + '-' * fw + '+', frame_a)
        for y in range(fh):
            cv.putc(bx - 1, by + y, '|', frame_a)
            cv.putc(bx + fw, by + y, '|', frame_a)

        blank = ' ' * fw
        for y in range(fh):
            cv.put(bx, by + y, blank, bg)

        ax, ay = self.aim
        # crosshair: show what a horizontal and a vertical wall would reach
        if not g.building and g.state == 'running' and g.open_cell(ax, ay):
            hc = g.wall_cells(ax, ay, 'h')
            vc = g.wall_cells(ax, ay, 'v')
            for x, y in hc[1:]:
                cv.putc(bx + x, by + y, '-', cross_a)
            for x, y in vc[1:]:
                cv.putc(bx + x, by + y, '|', cross_a)

        for x, y in g.walls:
            cv.putc(bx + x, by + y, '#', wall_a)
        for x, y in g.captured:
            cv.putc(bx + x, by + y, ':', cap_a)
        for x, y in g.building:
            cv.putc(bx + x, by + y, '*', build_a)

        for b in g.balls:
            cx, cy = int(math.floor(b.x)), int(math.floor(b.y))
            if 0 <= cx < fw and 0 <= cy < fh:
                cv.putc(bx + cx, by + cy, '@', ball_a)

        aim_a = (th.c('on_accent', 'accent', 1) if g.open_cell(ax, ay)
                 else th.c('bright', 'field', 1))
        cv.putc(bx + ax, by + ay, '+', aim_a)

        # --- overlays ---
        if self.state == 'start':
            self._overlay(cv, bx, by, fw, fh, 'JEZJBALL', [
                'SPLIT THE FIELD, TRAP THE BALLS',
                '',
                'ARROWS / WASD  AIM',
                'SPACE / CLICK  HORIZONTAL WALL',
                'ENTER / RIGHT CLICK  VERTICAL WALL',
                '',
                'CAPTURE %d%% TO CLEAR A LEVEL' % int(GOAL * 100),
                'A BALL TOUCHING A GROWING WALL COSTS A LIFE',
                '',
                'PRESS SPACE OR MOVE TO START',
            ])
        elif self.state == 'paused':
            self._overlay(cv, bx, by, fw, fh, 'PAUSED', [
                'SPACE / P  CONTINUE',
                'R  RESTART',
                'Q  QUIT',
            ])
        elif self.state == 'clear':
            self._overlay(cv, bx, by, fw, fh, 'LEVEL CLEARED', [
                'LEVEL %d  -  %d%% CAPTURED' % (g.level, pct),
                'NEXT LEVEL...',
            ])
        elif self.state == 'over':
            self._overlay(cv, bx, by, fw, fh, 'GAME OVER', [
                'LEVEL %d  -  %d%% CAPTURED' % (g.level, pct),
                'BEST LEVEL %d' % self.best,
                '',
                'SPACE / R  RESTART',
                'Q  QUIT',
            ])


APP = JezzBall
