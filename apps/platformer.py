"""Platformer: a side-scroller with tile levels, stompable walkers, coins and a flag.

Built on arcade.Arcade (fixed 40 Hz physics). Levels are 3x3-dot tiles drawn
below a 6-row HUD. Speeds are in dots per second. An idle title screen gives way
to a demo run (games.Attract).
"""

import math
import random

from fonts import SMALL

from .arcade import STEP, Arcade, fill, sprite
from .base import P, register
from .games import DEMO, Attract

T = 3            # tile size in dots
ROWS = 12        # tile rows per level (36 dots)
TOP = 6          # screen row where the level starts; the HUD sits above
WALK, RUN = 26, 36
ACCEL_GROUND, ACCEL_AIR = 400, 260
GRAVITY, FALL_GRAVITY, MAX_FALL = 180, 250, 90
JUMP = 70        # ~13 dots: clears 4 tiles with the button held
JUMP_CUT = 0.55  # rising speed kept when the button is let go early
COYOTE, BUFFER = 0.1, 0.15
ENEMY_SPEED = 12

# '#' solid, '=' one-way platform (jump up through it), 'o' coin, 'e' walker,
# 'P' start, 'F' flag. Rows 10-11 are the ground; gaps in them are pits.
LEVELS = [
    [  # level 1
        "....................................................................................................",
        "....................................................................................................",
        "....................................................................................................",
        "....................................................................................................",
        ".............................................................ooo....................................",
        "...............ooo..........................................=====...................................",
        "..............=====....................................................................##...........",
        "..............................oo........................o.............................###...........",
        "........................................##...........................................####...........",
        "..P.....o.o.o...........e...............##......e.....................e.....e.......#####......F....",
        "##############################..#######################...##########################################",
        "##############################..#######################...##########################################",
    ],
    [  # level 2
        "..............................................................................................................",
        "..............................................................................................................",
        "..............................................................................................................",
        "..............................................................................................................",
        ".......................................................................oooo...................................",
        "................................oo....................................======..................................",
        "..........ooo..................====...........................................................................",
        "..........===...........................................##......o................===..........................",
        "....................................oo........##........##...oo...o...............................##..........",
        "..P.......................e...................##....e...##..............e.....e...........e...e...##....F.....",
        "#################...###########....####....##################...##..#############....#########################",
        "#################...###########....####....##################...##..#############....#########################",
    ],
    [  # level 3
        "........................................................................................................................",
        "........................................................................................................................",
        "........................................................................................................................",
        "........................................................................................................................",
        "........................................................................................................................",
        "...............................oo..........................................ooo..........................................",
        "..............................====............................o.....o.....=====.........................................",
        "..................o.........................o.o.o...................................................#...................",
        ".........................====.......====....................................................#.......#...................",
        "..P.......e.......................................e....e...................e....e...........#...e...#...e.....e....F....",
        "###############....####.....................##############...###...###...############....###############################",
        "###############....####.....................##############...###...###...############....###############################",
    ],
]

# 5x6 hero facing right: standing, two walk frames, jumping
HERO = {
    "stand": [".XXX.", ".XXXX", "..XX.", ".XXXX", ".XXX.", ".X.X."],
    "walk1": [".XXX.", ".XXXX", "..XX.", "XXXX.", ".XXX.", "XX.XX"],
    "walk2": [".XXX.", ".XXXX", "..XX.", ".XXXX", ".XXX.", "..XX."],
    "jump": [".XXX.", ".XXXX", "X.XX.", "XXXXX", ".XXX.", "XX.XX"],
}
HERO_LEFT = {k: [r[::-1] for r in v] for k, v in HERO.items()}
WALKER = ([".XXX.", "XX.XX", "XXXXX", "X...X"],
          [".XXX.", "XX.XX", "XXXXX", ".X.X."])
SQUASHED = ["XXXXX", "XXXXX"]
COIN = ["XX", "XX"]  # sits low in its tile so rows of coins stay apart
FLAG = ["XXXXX", "XXXX.", "XXX..", "XX..."]


class Body:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = float(x), float(y), w, h
        self.vx = self.vy = 0.0

    def hits(self, o):
        return self.x < o.x + o.w and o.x < self.x + self.w and self.y < o.y + o.h and o.y < self.y + self.h


@register
class Platformer(Attract, Arcade):
    id = "platformer"
    title = "Platformer"
    description = "Left/right to run (hold B to run faster), A or up to jump; hold it longer to jump higher. Stomp walkers, grab coins, reach the flag."
    params = [P("lives", "number", 3, "Lives", min=1, max=9), DEMO]
    DEMO_POS = (1, 7)

    def reset(self):
        self.score, self.number = 0, 0
        self.lives = int(self.p["lives"])
        self.load()

    # ---- level ----------------------------------------------------------------------

    def load(self):
        """(Re)start the current level; called on a new level and after losing a life."""
        rows = LEVELS[self.number % len(LEVELS)]
        self.round = self.number // len(LEVELS) + 1
        self.name = f"{self.round}-{self.number % len(LEVELS) + 1}"
        self.cols = len(rows[0])
        self.grid = rows
        self.coins = set()
        self.enemies = []
        self.flag_x = self.cols * T
        start = (2, 9)
        for ty, row in enumerate(rows):
            for tx, ch in enumerate(row):
                if ch == "o":
                    self.coins.add((tx, ty))
                elif ch == "e":
                    e = Body(tx * T - 1, (ty + 1) * T - 4, 5, 4)
                    e.dir, e.dead_at = -1, None
                    self.enemies.append(e)
                elif ch == "P":
                    start = (tx, ty)
                elif ch == "F":
                    self.flag_x, self.flag_ty = tx * T, ty
        self.hero = Body(start[0] * T, (start[1] + 1) * T - 6, 5, 6)
        self.facing, self.walk = 1, 0.0
        self.grounded, self.jumping = False, False
        self.coyote = self.buffer = self.drop = 0.0
        self.phase, self.phase_t = "play", 0.0  # play | dying | clear
        self.enemy_speed = min(ENEMY_SPEED * (1 + 0.35 * (self.round - 1)), 30)
        self.cam = 0
        self.follow()

    def solid(self, tx, ty):
        if tx < 0 or tx >= self.cols or ty < 0:
            return True  # side walls and a ceiling under the HUD
        return ty < ROWS and self.grid[ty][tx] == "#"

    def ledge(self, tx, ty):
        return 0 <= tx < self.cols and 0 <= ty < ROWS and self.grid[ty][tx] == "="

    def move_x(self, b, dx):
        """Move along x; True if a wall stopped the body."""
        b.x += dx
        y0, y1 = math.floor(b.y / T), math.floor((b.y + b.h - 0.001) / T)
        if dx > 0:
            tx = math.floor((b.x + b.w - 0.001) / T)
            if any(self.solid(tx, ty) for ty in range(y0, y1 + 1)):
                b.x = tx * T - b.w
                return True
        elif dx < 0:
            tx = math.floor(b.x / T)
            if any(self.solid(tx, ty) for ty in range(y0, y1 + 1)):
                b.x = (tx + 1) * T
                return True
        return False

    def move_y(self, b, dy, drop=False):
        """Move along y; returns "floor", "ceiling" or None."""
        bottom = b.y + b.h
        b.y += dy
        x0, x1 = math.floor(b.x / T), math.floor((b.x + b.w - 0.001) / T)
        if dy > 0:
            ty = math.floor((b.y + b.h - 0.001) / T)
            for tx in range(x0, x1 + 1):
                # one-way platforms only catch bodies that were above them
                if self.solid(tx, ty) or (not drop and self.ledge(tx, ty) and bottom <= ty * T + 0.001):
                    b.y = ty * T - b.h
                    return "floor"
        elif dy < 0:
            ty = math.floor(b.y / T)
            if any(self.solid(tx, ty) for tx in range(x0, x1 + 1)):
                b.y = (ty + 1) * T
                return "ceiling"
        return None

    def follow(self):
        """Keep the hero about a third of the way in from the left."""
        self.cam = int(min(max(self.hero.x + 2 - self.w // 3, 0), self.cols * T - self.w))

    # ---- input and physics ------------------------------------------------------------

    def press(self, key, player):
        if key in ("a", "up"):
            self.buffer = BUFFER
        elif key == "down" and self.grounded:
            self.drop = 0.25  # fall through a one-way platform

    def autoplay(self, now):
        """Runs right and jumps over walls, pits and walkers; holds the button for a full jump."""
        if self.phase != "play":
            return self.hold()
        h = self.hero
        keys = ["right"]
        if random.random() < 0.002:
            self.dawdle = now + random.uniform(0.3, 0.8)  # stops to look around now and then
        if now < getattr(self, "dawdle", 0):
            keys = []
        front = h.x + h.w
        ahead = math.floor((front + 2) / T)
        feet = math.floor((h.y + h.h + 0.5) / T)  # the tile row under the hero
        wall = any(self.solid(ahead, ty) for ty in range(feet - 2, feet))
        pit = not any(self.solid(tx, feet) or self.ledge(tx, feet) for tx in (ahead, ahead + 1))
        walker = any(e.dead_at is None and -2 < e.x - front < 14 and abs(e.y + e.h - h.y - h.h) < 7
                     for e in self.enemies)
        if self.grounded and (wall or pit or walker) and keys:
            self.press("a", 1)
            keys.append("a")
        elif not self.grounded and h.vy < 0:
            keys.append("a")  # keep rising
        self.hold(*keys)

    def die(self):
        self.phase, self.phase_t = "dying", 0.0

    def step(self):
        self.phase_t += STEP
        if self.phase == "dying":
            if self.phase_t > 1.2:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.load()
            return
        if self.phase == "clear":
            if self.phase_t > 2:
                self.number += 1
                self.load()
            return
        held = {k for _, k in self.held}
        h = self.hero
        d = ("right" in held) - ("left" in held)
        if d:
            self.facing = d
        target = d * (RUN if "b" in held else WALK)
        a = (ACCEL_GROUND if self.grounded else ACCEL_AIR) * STEP
        h.vx = min(h.vx + a, target) if h.vx < target else max(h.vx - a, target)

        self.buffer -= STEP
        self.coyote = COYOTE if self.grounded else self.coyote - STEP
        self.drop -= STEP
        jump_held = bool(held & {"a", "up"})
        if self.buffer > 0 and self.coyote > 0:
            h.vy, self.jumping = -JUMP, True
            self.buffer = self.coyote = 0
        if self.jumping and h.vy < 0 and not jump_held:
            h.vy *= JUMP_CUT  # let go early for a short hop
            self.jumping = False
        h.vy = min(h.vy + (GRAVITY if h.vy < 0 else FALL_GRAVITY) * STEP, MAX_FALL)

        if self.move_x(h, h.vx * STEP):
            h.vx = 0
        bottom = h.y + h.h
        hit = self.move_y(h, h.vy * STEP, drop=self.drop > 0)
        self.grounded = hit == "floor"
        if hit:
            h.vy = 0
        if self.grounded:
            self.jumping = False
            self.walk += abs(h.vx) * STEP

        # coins under any part of the hero
        for tx in range(math.floor(h.x / T), math.floor((h.x + h.w - 0.001) / T) + 1):
            for ty in range(math.floor(h.y / T), math.floor((h.y + h.h - 0.001) / T) + 1):
                if (tx, ty) in self.coins:
                    self.coins.discard((tx, ty))
                    self.score += 10

        for e in self.enemies:
            if e.dead_at is not None:
                continue
            if e.x < self.cam + self.w + 30 and e.x > self.cam - 30:  # only nearby walkers move
                e.vy = min(e.vy + FALL_GRAVITY * STEP, MAX_FALL)
                on = self.move_y(e, e.vy * STEP) == "floor"
                if on:
                    e.vy = 0
                if self.move_x(e, e.dir * self.enemy_speed * STEP):
                    e.dir = -e.dir
                elif on:  # turn at ledges
                    fx = math.floor((e.x + e.w + 0.5 if e.dir > 0 else e.x - 0.5) / T)
                    fy = math.floor((e.y + e.h + 0.5) / T)
                    if not (self.solid(fx, fy) or self.ledge(fx, fy)):
                        e.dir = -e.dir
                if e.y > ROWS * T:
                    e.dead_at = -1.0
            if h.hits(e):
                if h.vy > 0 and bottom <= e.y + 2:
                    e.dead_at = self.phase_t
                    self.score += 50
                    h.vy = -JUMP * (1 if jump_held else 0.6)
                    self.jumping = jump_held
                else:
                    return self.die()
        self.enemies = [e for e in self.enemies if e.dead_at is None or self.phase_t - e.dead_at < 0.4]

        if h.y > ROWS * T + 2:
            return self.die()
        if h.x + h.w >= self.flag_x + 1:
            self.score += 100
            self.phase, self.phase_t = "clear", 0.0
        self.follow()

    # ---- drawing ----------------------------------------------------------------------

    def draw(self, c, now):
        cam = self.cam
        t0, t1 = max(cam // T, 0), min((cam + c.w) // T + 1, self.cols)
        for ty, row in enumerate(self.grid):
            y = TOP + ty * T
            tx = t0
            while tx < t1:
                ch = row[tx]
                if ch in "#=":
                    run = tx
                    while run < t1 and row[run] == ch:
                        run += 1
                    fill(c, tx * T - cam, y, (run - tx) * T, T if ch == "#" else 2)
                    tx = run
                else:
                    tx += 1
        for tx, ty in self.coins:
            if t0 - 1 <= tx < t1:
                fill(c, tx * T - cam, TOP + ty * T + 1, 2, 2)
        fx = self.flag_x - cam
        if -6 < fx < c.w:
            base = TOP + (self.flag_ty + 1) * T
            fill(c, fx, base - 24, 1, 24)
            sprite(c, fx + 1, base - 24, FLAG)
        for e in self.enemies:
            ex = int(e.x) - cam
            if -5 < ex < c.w:
                if e.dead_at is not None:
                    sprite(c, ex, int(e.y) + 2 + TOP, SQUASHED)
                else:
                    sprite(c, ex, int(e.y) + TOP, WALKER[int(self.phase_t * 4) % 2])
        h = self.hero
        if self.phase != "dying" or int(now * 8) % 2:
            frames = HERO if self.facing > 0 else HERO_LEFT
            if not self.grounded:
                f = frames["jump"]
            elif abs(h.vx) > 1:
                f = frames["walk1" if int(self.walk / 4) % 2 else "walk2"]
            else:
                f = frames["stand"]
            sprite(c, int(h.x) - cam, int(h.y) + TOP, f)
        # HUD
        fill(c, 0, 0, c.w, TOP, 0)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, self.name, SMALL)
        for i in range(min(self.lives, 6)):
            fill(c, c.w - 3 - i * 5, 1, 3, 3)
        if self.phase == "clear":
            msg = "LEVEL CLEAR"
            tw = SMALL.width(msg)
            fill(c, (c.w - tw) // 2 - 2, 13, tw + 4, 9, 0)
            c.text_center(15, msg, SMALL)

    def draw_title(self, c, now):
        f = HERO["walk1" if int(now * 6) % 2 else "walk2"]
        sprite(c, 24, 24, f)
        sprite(c, 50, 26, WALKER[int(now * 4) % 2])
        if int(now * 2) % 2:
            sprite(c, 37, 20, COIN)
        fill(c, 8, 30, 68, 3)

    def describe(self):
        if self.state == "title":
            return "Platformer (title)"
        return f"Platformer ({'demo' if self.demo else self.state}, level {getattr(self, 'name', '1-1')}, score {self.score})"
