"""Jump: a small scrolling platformer in three levels.

Clockwork: the player moves cell-by-cell on the ground and jumps in a
continuous arc.  Levels are plain ASCII maps:

    #  solid ground       =  platform        o  coin
    ^  spikes             E  exit            P  spawn
    ~  cloud (decor)      .  star (decor)

The physics lives in JumpGame/Level and has no terminal dependency, so the unit
tests can simulate it directly.
"""
import math

from ..app import App
from ..util import fit

GRAVITY = 31.0
JUMP_SPEED = 12.0
RUN_SPEED = 6.0
MAX_FALL = 14.0
COYOTE = 0.09
JUMP_BUFFER = 0.12
STEP_COOLDOWN = 0.09
PLAYER_W = 0.8
PLAYER_H = 0.95
DT = 1.0 / 30.0


def fmt_time(seconds):
    s = int(max(0, seconds))
    if s >= 3600:
        return '%d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60)
    return '%d:%02d' % (s // 60, s % 60)


class Level:
    def __init__(self, rows):
        self.rows = [r.rstrip('\n') for r in rows]
        self.w = max(len(r) for r in self.rows)
        self.h = len(self.rows)
        self.rows = [r.ljust(self.w) for r in self.rows]
        self.solid = set()
        self.coins = set()
        self.spikes = set()
        self.exit = None
        self.spawn = None
        for y, row in enumerate(self.rows):
            for x, ch in enumerate(row):
                if ch in '#=':
                    self.solid.add((x, y))
                elif ch == 'o':
                    self.coins.add((x, y))
                elif ch == '^':
                    self.spikes.add((x, y))
                elif ch == 'E':
                    self.exit = (x, y)
                elif ch == 'P':
                    self.spawn = (x, y)
        if self.spawn is None:
            self.spawn = (1, max(0, self.h - 2))
        if self.exit is None:
            self.exit = (max(1, self.w - 2), max(0, self.h - 2))

    def tile(self, x, y):
        if 0 <= y < self.h and 0 <= x < self.w:
            return self.rows[y][x]
        return ' '

    def is_solid(self, x, y):
        if x < 0 or x >= self.w:
            return True
        if y < 0 or y >= self.h:
            return False
        return (x, y) in self.solid


class JumpGame:
    def __init__(self, level_rows, lives=3):
        self.levels = [Level(rows) for rows in level_rows]
        self.start_lives = lives
        self.total_coins = sum(len(lv.coins) for lv in self.levels)
        self.reset()

    def reset(self):
        self.level = 0
        self.lives = self.start_lives
        self.collected = 0
        self.elapsed = 0.0
        self.over = False
        self.finished = False
        self._load(0)

    def _load(self, index):
        self.level = index
        self.lv = self.levels[index]
        self.x, self.y = float(self.lv.spawn[0]), float(self.lv.spawn[1])
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.facing = 1
        self.coins = set(self.lv.coins)
        self.level_collected = 0
        self.finished = False
        self.coyote = 0.0
        self.jump_buffer = 0.0
        self.step_cd = 0.0
        self.cam = 0
        self.on_ground = self._collides(self.x, self.y + 0.1)
        self.coyote = COYOTE if self.on_ground else 0.0

    def next_level(self):
        if self.level + 1 >= len(self.levels):
            self.finished = True
            self.over = True
            return 'won'
        self._load(self.level + 1)
        return 'next'

    # -- controls -------------------------------------------------------
    def jump(self):
        if self.on_ground or self.coyote > 0:
            self.vy = -JUMP_SPEED
            self.vx = self.facing * RUN_SPEED
            self.on_ground = False
            self.coyote = 0.0
            self.jump_buffer = 0.0
            return True
        self.jump_buffer = JUMP_BUFFER
        return False

    def stop(self):
        self.facing = 0
        if not self.on_ground:
            self.vx = 0.0

    def step(self, direction):
        if self.step_cd > 0:
            return False
        self.facing = direction
        if not self.on_ground:
            self.vx = direction * RUN_SPEED
            return True
        target = self.x + direction
        if self._collides(target, self.y):
            return False
        self.x = target
        self.step_cd = STEP_COOLDOWN
        return True

    # -- physics --------------------------------------------------------
    def _cells_at(self, x, y):
        x0, x1 = int(math.floor(x)), int(math.floor(x + PLAYER_W - 1e-6))
        y0, y1 = int(math.floor(y)), int(math.floor(y + PLAYER_H - 1e-6))
        return [(cx, cy) for cy in range(y0, y1 + 1)
                for cx in range(x0, x1 + 1)]

    def _collides(self, x, y):
        for cx, cy in self._cells_at(x, y):
            if self.lv.is_solid(cx, cy):
                return True
        return False

    def _move_x(self, dx):
        if not dx:
            return
        n = max(1, int(abs(dx) / 0.1) + 1)
        part = dx / n
        for _ in range(n):
            nx = self.x + part
            if self._collides(nx, self.y):
                self.vx = 0.0
                return
            self.x = nx

    def _move_y(self, dy):
        if not dy:
            return
        n = max(1, int(abs(dy) / 0.1) + 1)
        part = dy / n
        for _ in range(n):
            ny = self.y + part
            if self._collides(self.x, ny):
                if dy > 0:
                    self.on_ground = True
                self.vy = 0.0
                return
            self.y = ny

    def _hurt(self):
        self.lives -= 1
        if self.lives <= 0:
            self.lives = 0
            self.over = True
            return 'dead'
        self.x, self.y = float(self.lv.spawn[0]), float(self.lv.spawn[1])
        self.vx = self.vy = 0.0
        self.on_ground = self._collides(self.x, self.y + 0.1)
        self.coyote = COYOTE if self.on_ground else 0.0
        self.jump_buffer = 0.0
        self.step_cd = 0.0
        return 'hurt'

    def update(self, dt=DT):
        if self.finished or self.over:
            return None
        self.elapsed += dt
        self.step_cd = max(0.0, self.step_cd - dt)
        self.jump_buffer = max(0.0, self.jump_buffer - dt)
        if self.on_ground:
            self.coyote = COYOTE
        else:
            self.coyote = max(0.0, self.coyote - dt)
        if self.on_ground and self.jump_buffer > 0:
            self.jump()
            self.jump_buffer = 0.0

        if not self.on_ground:
            self._move_x(self.vx * dt)
        self.on_ground = False
        self.vy = min(MAX_FALL, self.vy + GRAVITY * dt)
        self._move_y(self.vy * dt)
        if self.on_ground:
            self.vx = 0.0

        cells = self._cells_at(self.x, self.y)
        for cell in cells:
            if cell in self.coins:
                self.coins.remove(cell)
                self.collected += 1
                self.level_collected += 1
        for cell in cells:
            if cell in self.lv.spikes:
                return self._hurt()
        if self.lv.exit in cells:
            self.finished = True
            return 'level'
        if self.y > self.lv.h + 1:
            return self._hurt()
        return None


# Three levels.  The ground path is always passable (gaps are at most three
# cells); the staircases and platforms are optional coin routes.
LEVEL_ROWS = [
    [
        '                                                                                                ',
        '                                                                                                ',
        '                                                                                                ',
        '    ~~~               ~~~               ~~~               ~~~               ~~~                 ',
        '                                                                                                ',
        '            ~~                        ~~                        ~~                        ~~    ',
        '                                                                                                ',
        '                o             o                   o                   o                   o     ',
        '               ===           ===                 ===                 ===                 ===    ',
        '             o             o                   o                   o                   o        ',
        '            ===           ===                 ===                 ===                 ===       ',
        '           o             o                   o                   o                   o          ',
        '   P  o o ==o  o       o==    ^^ o  o      o== o  ^^ o  o      o== o  ^^ o  o      o== o  o E   ',
        '##################   #################   #################   #################   ###############',
    ],
    [
        '                                                                                                                ',
        '                                                                                                                ',
        '                                                                                                                ',
        '    ~~~               ~~~               ~~~               ~~~               ~~~               ~~~               ',
        '                                                                                                                ',
        '            ~~                        ~~                        ~~                        ~~                    ',
        '                                                                                                                ',
        '              o         o                 o                 o                 o               o               o ',
        '             ===       ===               ===               ===               ===             ===             ===',
        '           o         o                 o                 o                 o               o               o    ',
        '          ===       ===               ===               ===               ===             ===             ===   ',
        '         o         o                 o                 o                 o               o               o      ',
        '   P o  ==o    o  ==o ^^   o        ==  ^^ o  o      o==o ^^   o o     o==o ^^   o o    ==o ^^ o  o     ==  E   ',
        '##############   #############   ###############   ###############   ###############   #############   #########',
    ],
    [
        '                                                                                                                                ',
        '                                                                                                                                ',
        '                                                                                                                                ',
        '    ~~~               ~~~               ~~~               ~~~               ~~~               ~~~               ~~~             ',
        '                                                                                                                                ',
        '            ~~                        ~~                        ~~                        ~~                        ~~          ',
        '                                                                                                                                ',
        '            o         o               o               o               o               o               o               o         ',
        '           ===       ===             ===             ===             ===             ===             ===             ===        ',
        '         o         o               o               o               o               o               o               o            ',
        '        ===       ===             ===             ===             ===             ===             ===             ===           ',
        '       o         o               o               o               o               o               o               o              ',
        '   P o== o   o  ==  ^^ o  o     ==  ^^   o   o  ==  ^^   o   o  ==  ^^   o   o  ==  ^^   o   o  ==  ^^   o   o  ==  ^^   o oE   ',
        '############   #############   #############   #############   #############   #############   #############   #################',
    ],
]


class Jump(App):
    name = 'jump'
    title = 'Jump'
    icon = '[J]'
    size = (64, 18)
    min_size = (40, 16)
    single = True
    preserve_colors = True

    def __init__(self, wm, win, **kw):
        super().__init__(wm, win)
        try:
            self.best = int(wm.cfg.get('jump_best', 0) or 0)
        except (TypeError, ValueError):
            self.best = 0
        self.game = JumpGame(LEVEL_ROWS)
        self.state = 'start'
        self.hurt = 0.0
        self.timer = self.every(DT, self.tick)

    # -- loop -----------------------------------------------------------
    def tick(self):
        self.hurt = max(0.0, self.hurt - DT)
        if self.state != 'running':
            return
        if not self.focused():
            self.state = 'paused'
            self.invalidate()
            return
        ev = self.game.update(DT)
        if ev == 'level':
            self.state = 'clear'
            self.later(1.0, self._advance)
        elif ev == 'hurt':
            self.hurt = 0.25
        elif ev == 'dead':
            self.state = 'over'
        self.invalidate()

    def _advance(self):
        if self.state != 'clear':
            return
        res = self.game.next_level()
        if res == 'won':
            self.state = 'won'
            self._save_best()
        else:
            self.state = 'running'
        self.invalidate()

    def _save_best(self):
        t = int(self.game.elapsed)
        if t > 0 and (not self.best or t < self.best):
            self.best = t
            try:
                self.wm.cfg['jump_best'] = self.best
                self.wm.cfg.save()
            except Exception:
                pass

    def restart(self):
        self.game.reset()
        self.state = 'start'
        self.hurt = 0.0
        self.invalidate()

    def on_key(self, ev):
        k = (ev.key or '').lower()
        if k == 'q':
            self.close()
            return True
        if self.state == 'clear':
            return True
        if self.state in ('over', 'won'):
            if k in ('space', 'enter', 'r'):
                self.restart()
                return True
            return False
        if self.state == 'paused':
            if k in ('p', 'esc', 'space', 'enter'):
                self.state = 'running'
                self.invalidate()
                return True
            if k == 'r':
                self.restart()
                return True
            return False
        if k in ('left', 'a'):
            if self.state == 'start':
                self.state = 'running'
            self.game.step(-1)
            self.invalidate()
            return True
        if k in ('right', 'd'):
            if self.state == 'start':
                self.state = 'running'
            self.game.step(1)
            self.invalidate()
            return True
        if k in ('up', 'space', 'w', 'enter'):
            if self.state == 'start':
                self.state = 'running'
            self.game.jump()
            self.invalidate()
            return True
        if k in ('down', 's'):
            if self.state == 'start':
                self.state = 'running'
            self.game.stop()
            self.invalidate()
            return True
        if k in ('p', 'esc'):
            if self.state == 'running':
                self.state = 'paused'
                self.invalidate()
            return True
        if k == 'r':
            self.restart()
            return True
        return False

    def on_focus(self, focused):
        if not focused and self.state == 'running':
            self.state = 'paused'
            self.invalidate()

    # -- drawing --------------------------------------------------------
    def _overlay(self, cv, oy, h, title, lines):
        w = cv.w
        ow = min(w - 4, 46)
        oh = min(h - 2, len(lines) + 3)
        if ow < 12 or oh < 3:
            return
        ox = (w - ow) // 2
        by = oy + (h - oh) // 2
        cv.fill(ox, by, ow, oh, self.th.c('text', 'win'))
        cv.box(ox, by, ow, oh, self.th.c('accent', 'win'))
        cv.put(ox + 2, by, ' ' + title + ' ', self.th.c('accent', 'win', 1))
        for i, line in enumerate(lines[:oh - 2]):
            cv.put(ox + 1, by + 1 + i, fit(line, ow - 2, 'center'),
                   self.th.c('bright' if i == 0 else 'text', 'win'))

    def _hud(self, cv, w):
        g = self.game
        lv = g.lv
        n = len(g.levels)
        time_s = fmt_time(g.elapsed)
        best = fmt_time(self.best) if self.best else '--:--'
        cv.put(0, 0, 'JUMP', self.th.c('accent', 'win', 1))
        if w >= 74:
            right = 'LEVEL %d/%d   COINS %d/%d   LIVES %d   TIME %s   BEST %s' % (
                g.level + 1, n, g.collected, g.total_coins, g.lives, time_s, best)
        elif w >= 48:
            right = 'LV %d/%d  C %d/%d  L %d  %s' % (
                g.level + 1, n, g.collected, g.total_coins, g.lives, time_s)
        else:
            right = '%d/%d  %d  %s' % (g.level + 1, n, g.lives, time_s)
        cv.put(max(0, w - len(right)), 0, right, self.th.c('dim', 'win'))
        hint = 'ARROWS/WASD MOVE  SPACE JUMP  S STOP  P PAUSE  R RESTART  Q QUIT'
        cv.put(0, 1, fit(hint, w), self.th.c('dim', 'win'))
        return lv

    def draw(self, cv, focused):
        th = self.th
        w, h = cv.w, cv.h
        if w < 30 or h < 16:
            if w > 0 and h > 0:
                cv.put(0, h // 2, fit('JUMP - window too small', w, 'center'),
                       th.c('red', 'win'))
            return
        lv = self._hud(cv, w)
        oy = 2 + max(0, (h - 2 - lv.h) // 2)
        cv.fill(0, oy, w, lv.h, th.c('text', 'field'))
        if lv.w <= w:
            left = (w - lv.w) // 2
            cam = 0
        else:
            left = 0
            cam = max(0, min(lv.w - w, int(self.game.x + 0.4 - w / 2)))
        self.game.cam = cam
        for cy in range(lv.h):
            for sx in range(w):
                cx = cam + (sx - left) if lv.w > w else sx - left
                if cx < 0 or cx >= lv.w:
                    continue
                if (cx, cy) in lv.solid:
                    ch = lv.tile(cx, cy)
                    a = th.c('accent' if ch == '=' else 'dim', 'raised')
                elif (cx, cy) in self.game.coins:
                    ch, a = 'o', th.c('yellow', 'field', 1)
                elif (cx, cy) in lv.spikes:
                    ch, a = '^', th.c('red', 'field', 1)
                elif (cx, cy) == lv.exit:
                    ch, a = 'E', th.c('green', 'field', 1)
                else:
                    ch = lv.tile(cx, cy)
                    if ch in '~.':
                        a = th.c('dim', 'field')
                    else:
                        ch, a = ' ', th.c('text', 'field')
                cv.putc(sx, oy + cy, ch, a)
        px = left + int(round(self.game.x - cam))
        py = oy + int(round(self.game.y))
        if 0 <= px < w and oy <= py < oy + lv.h:
            a = th.c('red' if self.hurt > 0 else 'on_accent', 'accent', 1)
            cv.putc(px, py, '@', a)
        if self.state == 'start':
            self._overlay(cv, oy, lv.h, 'JUMP', [
                'ARROWS / WASD  MOVE',
                'SPACE / UP  JUMP',
                'S / DOWN  STOP',
                '',
                'PRESS SPACE OR A DIRECTION',
                'BEST  %s' % (fmt_time(self.best) if self.best else '--:--'),
            ])
        elif self.state == 'paused':
            self._overlay(cv, oy, lv.h, 'PAUSED', [
                'P / ESC  CONTINUE',
                'R  RESTART',
                'Q  QUIT',
            ])
        elif self.state == 'clear':
            self._overlay(cv, oy, lv.h, 'LEVEL CLEAR', [
                'COINS  %d/%d' % (self.game.level_collected, len(lv.coins)),
                'NEXT LEVEL...',
            ])
        elif self.state == 'over':
            self._overlay(cv, oy, lv.h, 'GAME OVER', [
                'LEVEL  %d/%d' % (self.game.level + 1, len(self.game.levels)),
                'COINS  %d/%d' % (self.game.collected, self.game.total_coins),
                '',
                'SPACE / R  RESTART',
                'Q  QUIT',
            ])
        elif self.state == 'won':
            self._overlay(cv, oy, lv.h, 'ALL LEVELS CLEAR', [
                'COINS  %d/%d' % (self.game.collected, self.game.total_coins),
                'TIME  %s' % fmt_time(self.game.elapsed),
                'BEST  %s' % (fmt_time(self.best) if self.best else '--:--'),
                '',
                'SPACE / R  RESTART',
                'Q  QUIT',
            ])


APP = Jump
