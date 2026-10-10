"""Arcade games: Breakout, Space Invaders, Tron light cycles and Flappy.

Built on games.Game. Physics runs on a fixed 40 Hz clock (step()) so speeds
don't depend on the frame rate; speeds below are in dots per second.
"""

import math
import random
import time

from fonts import NORMAL, SMALL

from .base import P, register
from .games import Game

STEP = 1 / 40


def fill(c, x, y, w, h, v=1):
    """Filled rectangle, clipped, written straight into the pixel buffer."""
    x0, y0 = max(int(x), 0), max(int(y), 0)
    x1, y1 = min(int(x) + w, c.w), min(int(y) + h, c.h)
    if x1 <= x0:
        return
    row = bytes([v]) * (x1 - x0)
    for yy in range(y0, y1):
        i = yy * c.w
        c.px[i + x0:i + x1] = row


def sprite(c, x, y, rows):
    for dy, r in enumerate(rows):
        for dx, ch in enumerate(r):
            if ch == "X":
                c.set(x + dx, y + dy)


class Arcade(Game):
    """Game with a fixed-step clock, a title that fits the board and a best score."""

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.now = time.time()
        self.clock = None
        self.best = 0

    def tick(self, now):
        self.now = now
        if self.clock is None or now - self.clock > 0.5:  # first frame or after a stall
            self.clock = now - STEP
        while now - self.clock >= STEP and self.state == "playing":
            self.clock += STEP
            self.step()
        self.best = max(self.best, self.score)

    def step(self): ...
    def draw_title(self, c, now): ...

    def render(self, c, now):
        self.now = now  # reset() runs on a button press; timers start from here
        if self.state == "title":
            t = self.title.upper()
            if NORMAL.width(t) * 2 <= c.w:
                c.text_center(4, t, NORMAL, scale=2)
            else:
                c.text_center(8, t, NORMAL)
            self.draw_title(c, now)
            if int(now * 2) % 2:
                c.text_center(36, "PRESS ANY BUTTON", SMALL)
            return
        if self.state == "over":
            c.text_center(4, "GAME OVER", NORMAL)
            c.text_center(14, str(self.score), NORMAL, scale=2)
            c.text_center(34, f"BEST {self.best}", SMALL)
            if now - self.over_at > 4:
                self.state = "title"
            return
        self.tick(now)
        self.draw(c, now)


# ---- Breakout -----------------------------------------------------------------

@register
class Breakout(Arcade):
    id = "breakout"
    title = "Breakout"
    description = "Left/right moves the paddle, A or up launches the ball. Where the ball hits the paddle sets its angle."
    params = [P("lives", "number", 3, "Lives", min=1, max=9)]
    TOP = 7          # ceiling (the HUD sits above it)
    BRICKS_Y = 10
    BW, BH = 6, 2    # brick size; bricks sit on a 7x3 grid
    COLS = 12
    PADDLE_Y = 39

    def reset(self):
        self.score, self.level = 0, 1
        self.lives = int(self.p["lives"])
        self.px = (self.w - 12) / 2
        self.new_level()

    def new_level(self):
        self.rows = min(3 + self.level, 6)
        self.bricks = [[1] * self.COLS for _ in range(self.rows)]
        self.left = self.rows * self.COLS
        self.pw = max(8, 14 - 2 * self.level)
        self.serve()

    def serve(self):
        self.stuck = True
        self.serve_at = self.now
        self.speed = 28 + 4 * self.level
        self.bx, self.by = self.px + self.pw / 2 - 1, self.PADDLE_Y - 2
        self.vx = self.vy = 0

    def launch(self):
        self.stuck = False
        a = random.uniform(-0.5, 0.5)
        self.vx, self.vy = self.speed * math.sin(a), -self.speed * math.cos(a)

    def press(self, key, player):
        if key in ("a", "up", "b") and self.stuck:
            self.launch()

    def brick_at(self, x, y):
        """(row, col) of the brick covering dot (x, y), or None."""
        x, y = int(x), int(y) - self.BRICKS_Y
        if y < 0 or x < 0 or x % 7 >= self.BW or y % 3 >= self.BH:
            return None
        r, col = y // 3, x // 7
        if r < self.rows and col < self.COLS and self.bricks[r][col]:
            return r, col
        return None

    def hit_bricks(self):
        hits = {self.brick_at(self.bx + dx, self.by + dy) for dx in (0, 1.99) for dy in (0, 1.99)} - {None}
        for r, col in hits:
            self.bricks[r][col] = 0
            self.left -= 1
            self.score += self.rows - r
        return bool(hits)

    def step(self):
        held = {k for _, k in self.held}
        dx = (("right" in held) - ("left" in held)) * 60 * STEP
        self.px = min(max(self.px + dx, 0), self.w - self.pw)
        if self.stuck:
            self.bx, self.by = self.px + self.pw / 2 - 1, self.PADDLE_Y - 2
            if self.now - self.serve_at > 3:
                self.launch()
            return
        n = math.ceil(self.speed * STEP / 0.5)
        for _ in range(n):
            # move one axis at a time so we know which way to bounce
            ox = self.bx
            self.bx += self.vx * STEP / n
            if self.bx < 0 or self.bx > self.w - 2:
                self.vx = -self.vx
                self.bx = min(max(self.bx, 0), self.w - 2)
            elif self.hit_bricks():
                self.vx, self.bx = -self.vx, ox
            oy = self.by
            self.by += self.vy * STEP / n
            if self.by < self.TOP:
                self.vy, self.by = abs(self.vy), self.TOP
            elif self.hit_bricks():
                self.vy, self.by = -self.vy, oy
            if self.vy > 0 and oy + 2 <= self.PADDLE_Y < self.by + 2 and self.px - 1.5 < self.bx < self.px + self.pw - 0.5:
                off = (self.bx + 1 - (self.px + self.pw / 2)) / (self.pw / 2 + 1)
                a = max(-1, min(1, off)) * 1.05  # up to 60 degrees off vertical
                self.speed = min(self.speed + 0.6, 55)
                self.vx, self.vy = self.speed * math.sin(a), -self.speed * math.cos(a)
                self.by = self.PADDLE_Y - 2
        if self.left == 0:
            self.level += 1
            return self.new_level()
        if self.by > self.h:
            self.lives -= 1
            if self.lives <= 0:
                return self.game_over()
            self.serve()

    def draw(self, c, now):
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, f"L{self.level}", SMALL)
        for i in range(self.lives - 1):
            fill(c, c.w - 4 - i * 5, 2, 4, 2)
        c.line(0, self.TOP - 1, c.w - 1, self.TOP - 1)
        for r, row in enumerate(self.bricks):
            for col, alive in enumerate(row):
                if alive:
                    fill(c, col * 7, self.BRICKS_Y + r * 3, self.BW, self.BH)
        fill(c, self.px, self.PADDLE_Y, self.pw, 2)
        if not self.stuck or int(now * 4) % 2:
            fill(c, self.bx, self.by, 2, 2)

    def draw_title(self, c, now):
        for col in range(self.COLS):
            if (col + int(now * 3)) % 4:
                fill(c, col * 7, 20, self.BW, self.BH)
        fill(c, 36, 30, 12, 2)

    def describe(self):
        return f"Breakout ({self.state}, level {getattr(self, 'level', 1)}, score {self.score})"


# ---- Space Invaders ---------------------------------------------------------------

ALIENS = [
    (["..XXX..", ".XXXXX.", "XX.X.XX", "XXXXXXX", ".X...X."],
     ["..XXX..", ".XXXXX.", "XX.X.XX", "XXXXXXX", "X.....X"]),
    (["X.....X", ".XXXXX.", "XX.X.XX", ".XXXXX.", ".X...X."],
     [".X...X.", ".XXXXX.", "XX.X.XX", ".XXXXX.", "X.....X"]),
    ([".XXXXX.", "XXXXXXX", "X..X..X", "XXXXXXX", ".X.X.X."],
     [".XXXXX.", "XXXXXXX", "X..X..X", "XXXXXXX", "X.X.X.X"]),
]
SHIP = ["...X...", ".XXXXX.", "XXXXXXX"]
SHIELD = [".XXXX.", "XXXXXX", "XX..XX"]  # in 2x2 blocks


@register
class Invaders(Arcade):
    id = "invaders"
    title = "Space Invaders"
    description = "Left/right to move, A or up to fire. Shields wear away; each wave marches faster."
    params = [P("lives", "number", 3, "Lives", min=1, max=9)]
    ROWS, COLS = 3, 6
    SHIP_Y = 38
    SHIELD_Y = 29

    def reset(self):
        self.score, self.wave = 0, 1
        self.lives = int(self.p["lives"])
        self.sx = (self.w - 7) / 2
        self.shields = {(x0 + bx * 2, self.SHIELD_Y + by * 2)
                        for x0 in (8, 36, 64) for by, r in enumerate(SHIELD) for bx, ch in enumerate(r) if ch == "X"}
        self.new_wave()

    def new_wave(self):
        self.alive = [[1] * self.COLS for _ in range(self.ROWS)]
        self.count = self.ROWS * self.COLS
        self.ox, self.oy = 2, 7 + 2 * min(self.wave - 1, 4)
        self.dir, self.frame = 1, 0
        self.march_at = self.now + 1
        self.shot = None
        self.bombs = []
        self.hit_until = 0

    def press(self, key, player):
        if key in ("a", "up", "b"):
            self.fire()

    def fire(self):
        if self.shot is None and self.now >= self.hit_until:
            self.shot = [self.sx + 3, self.SHIP_Y - 3.0]

    def aliens(self):
        for r in range(self.ROWS):
            for col in range(self.COLS):
                if self.alive[r][col]:
                    yield r, col, self.ox + col * 10, self.oy + r * 7

    def march(self):
        xs = [x for _, _, x, _ in self.aliens()]
        if min(xs) + 2 * self.dir < 0 or max(xs) + 7 + 2 * self.dir > self.w:
            self.dir = -self.dir
            self.oy += 2
        else:
            self.ox += 2 * self.dir
        self.frame ^= 1
        bottom = max(y for _, _, _, y in self.aliens()) + 5
        if bottom > self.SHIELD_Y:  # aliens low enough eat through the shields
            for _, _, x, y in self.aliens():
                self.shields -= {b for b in self.shields if x - 1 <= b[0] < x + 7 and y - 1 <= b[1] < y + 5}
        if bottom >= self.SHIP_Y:
            self.lives = 0
            self.game_over()

    def hit_shield(self, x, y):
        b = (8 + (int(x) - 8) // 2 * 2, self.SHIELD_Y + (int(y) - self.SHIELD_Y) // 2 * 2)
        if b in self.shields:
            self.shields.discard(b)
            return True
        return False

    def step(self):
        now = self.now
        held = {k for _, k in self.held}
        if now >= self.hit_until:
            self.sx = min(max(self.sx + (("right" in held) - ("left" in held)) * 40 * STEP, 0), self.w - 7)
            if held & {"a", "up"}:
                self.fire()  # hold to keep firing
        # the formation speeds up as it thins out and with each wave
        if now >= self.march_at:
            self.march_at = now + max(0.04, 0.06 + 0.55 * self.count / (self.ROWS * self.COLS) - 0.04 * (self.wave - 1))
            self.march()
            if self.state != "playing":
                return
        if self.shot:
            for _ in range(2):  # 80 dots/s in two half-steps so nothing is skipped
                self.shot[1] -= 40 * STEP
                x, y = self.shot
                if y < 6:
                    self.shot = None
                elif self.hit_shield(x, y) or self.hit_alien(x, y):
                    self.shot = None
                if not self.shot:
                    break
        speed = (32 + 4 * self.wave) * STEP
        for b in self.bombs[:]:
            b[1] += speed
            if b[1] > self.h or self.hit_shield(b[0], b[1] + 2):
                self.bombs.remove(b)
            elif self.SHIP_Y <= b[1] + 2 and b[1] < self.SHIP_Y + 3 and self.sx <= b[0] < self.sx + 7 and now >= self.hit_until:
                self.bombs.clear()
                self.lives -= 1
                self.hit_until = now + 1.2
                if self.lives <= 0:
                    return self.game_over()
                break
        if len(self.bombs) < 1 + min(self.wave, 4) // 2 and random.random() < 0.03 + 0.01 * self.wave:
            cols = {}
            for _, col, x, y in self.aliens():
                cols[col] = (x + 3, y + 5)
            self.bombs.append(list(random.choice(list(cols.values()))))
        if self.count == 0:
            self.wave += 1
            self.new_wave()

    def hit_alien(self, x, y):
        for r, col, ax, ay in self.aliens():
            if ax <= x < ax + 7 and ay <= y + 2 and y < ay + 5:
                self.alive[r][col] = 0
                self.count -= 1
                self.score += (3 - r) * 10
                return True
        return False

    def draw(self, c, now):
        c.text(0, 0, str(self.score), SMALL)
        for i in range(self.lives - 1):
            fill(c, c.w - 5 - i * 6, 2, 5, 2)
            c.set(c.w - 3 - i * 6, 1)
        for r, col, x, y in self.aliens():
            sprite(c, x, y, ALIENS[r][self.frame])
        for bx, by in self.shields:
            fill(c, bx, by, 2, 2)
        if now >= self.hit_until or int(now * 8) % 2:
            sprite(c, int(self.sx), self.SHIP_Y, SHIP)
        if self.shot:
            fill(c, self.shot[0], self.shot[1], 1, 3)
        for bx, by in self.bombs:
            fill(c, bx, by, 1, 3)
        c.line(0, c.h - 1, c.w - 1, c.h - 1)

    def draw_title(self, c, now):
        f = int(now * 2) % 2
        for i, r in enumerate((0, 1, 2)):
            sprite(c, 22 + i * 15, 20, ALIENS[r][f])

    def describe(self):
        return f"Space Invaders ({self.state}, wave {getattr(self, 'wave', 1)}, score {self.score})"


# ---- Tron light cycles ----------------------------------------------------------

DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


@register
class Tron(Arcade):
    id = "tron"
    title = "Tron"
    description = ("Light cycles: steer with the arrows, don't hit any trail or the edge. "
                   "Player 2 is the computer unless someone presses buttons as player 2.")
    params = [P("rounds", "number", 5, "Best of (rounds)", min=1, max=15),
              P("speed", "number", 14, "Speed (cells per second)", min=6, max=30)]
    CELL = 2

    def reset(self):
        self.gw, self.gh = self.w // self.CELL, self.h // self.CELL
        self.wins = [0, 0]
        self.score = 0
        self.round = 0
        self.p2_human_at = getattr(self, "p2_human_at", 0)
        self.new_round()

    def input(self, key, action="down", player=1):
        if player == 2 and action == "down":
            self.p2_human_at = self.now
        super().input(key, action, player)

    def cpu(self):
        return self.now - self.p2_human_at > 15

    def new_round(self):
        self.round += 1
        self.grid = bytearray(self.gw * self.gh)
        my = self.gh // 2
        self.pos = [(6, my), (self.gw - 7, my)]
        self.dir = [(1, 0), (-1, 0)]
        self.queue = [[], []]
        self.trail = [[self.pos[0]], [self.pos[1]]]
        for i, (x, y) in enumerate(self.pos):
            self.grid[y * self.gw + x] = i + 1
        self.go_at = self.now + 1.5
        self.next_move = self.go_at
        self.crashed = None  # set of crashed player indexes once the round ends
        self.end_at = 0

    def press(self, key, player):
        d = DIRS.get(key)
        if not d or player not in (1, 2):
            return
        q = self.queue[player - 1]
        last = q[-1] if q else self.dir[player - 1]
        if d != last and d != (-last[0], -last[1]) and len(q) < 2:
            q.append(d)

    def free(self, x, y):
        return 0 <= x < self.gw and 0 <= y < self.gh and not self.grid[y * self.gw + x]

    def area(self, start, blocked, cap=300):
        """Free cells reachable from start (stops counting at cap)."""
        gw, gh, grid = self.gw, self.gh, self.grid
        seen = set(blocked)
        seen.add(start)
        todo = [start]
        n = 0
        while todo and n < cap:
            x, y = todo.pop()
            n += 1
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < gw and 0 <= ny < gh and not grid[ny * gw + nx] and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    todo.append((nx, ny))
        return n

    def cpu_turn(self):
        (x, y), d = self.pos[1], self.dir[1]
        ox, oy = self.pos[0]
        danger = {(ox + dx, oy + dy) for dx, dy in DIRS.values()}  # where player 1 could be next
        best, choice = -1, d
        for nd in (d, (d[1], -d[0]), (-d[1], d[0])):
            nx, ny = x + nd[0], y + nd[1]
            if not self.free(nx, ny):
                continue
            score = self.area((nx, ny), danger)
            if (nx, ny) in danger:
                score -= 200
            # look one more cell ahead: prefer room to keep going straight
            if self.free(nx + nd[0], ny + nd[1]):
                score += 3
            score += random.random() * 2 + (2 if nd == d else 0)
            # now and then drift toward player 1 to cut them off
            if random.random() < 0.3:
                score += 2 * ((abs(ox - x) + abs(oy - y)) - (abs(ox - nx) + abs(oy - ny)))
            if score > best:
                best, choice = score, nd
        self.dir[1] = choice

    def step(self):
        now = self.now
        if self.crashed is not None:
            if now >= self.end_at:
                need = int(self.p["rounds"]) // 2 + 1
                if max(self.wins) >= need or self.round >= int(self.p["rounds"]) * 2:
                    self.game_over()
                else:
                    self.new_round()
            return
        if now < self.next_move:
            return
        self.next_move += 1 / float(self.p["speed"])
        for i in (0, 1):
            if self.queue[i]:
                self.dir[i] = self.queue[i].pop(0)
        if self.cpu():
            self.queue[1].clear()
            self.cpu_turn()
        new = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(self.pos, self.dir)]
        crashed = {i for i in (0, 1) if not self.free(*new[i])}
        if new[0] == new[1]:
            crashed = {0, 1}
        for i in (0, 1):
            if i not in crashed:
                x, y = new[i]
                self.grid[y * self.gw + x] = i + 1
                self.trail[i].append(new[i])
                self.pos[i] = new[i]
        if crashed:
            self.crashed = crashed
            self.end_at = now + 2
            if len(crashed) == 1:
                self.wins[1 - next(iter(crashed))] += 1
            self.score = self.wins[0]

    def draw(self, c, now):
        s = self.CELL
        for x, y in self.trail[0]:
            fill(c, x * s, y * s, s, s)
        # player 2's trail is a thin line so the two read apart without colour
        t = self.trail[1]
        for (x0, y0), (x1, y1) in zip(t, t[1:]):
            c.line(x0 * s, y0 * s, x1 * s, y1 * s)
        for i, (x, y) in enumerate(self.pos):
            blink = int(now * 6) % 2
            if (self.crashed and i in self.crashed) or now < self.go_at:
                if blink:
                    fill(c, x * s - 1, y * s - 1, s + 2, s + 2)
            else:
                fill(c, x * s, y * s, s, s)
        if now < self.go_at or self.crashed:
            names = ("P1", "CPU" if self.cpu() else "P2")
            text = f"{names[0]} {self.wins[0]}-{self.wins[1]} {names[1]}"
            tw = SMALL.width(text)
            fill(c, (c.w - tw) // 2 - 2, 2, tw + 4, 9, 0)
            c.text_center(4, text, SMALL)

    def render(self, c, now):
        if self.state == "over":
            p2 = "CPU" if self.cpu() else "P2"
            won = self.wins[0] > self.wins[1]
            c.text_center(6, "P1 WINS" if won else (f"{p2} WINS" if self.wins[0] < self.wins[1] else "DRAW"), NORMAL)
            c.text_center(18, f"{self.wins[0]}-{self.wins[1]}", NORMAL, scale=2)
            if now - self.over_at > 4:
                self.state = "title"
            return
        super().render(c, now)

    def draw_title(self, c, now):
        n = int(now * 10) % 30
        fill(c, 10, 26, 2 + n, 2)
        c.line(73 - n, 27, 73, 27)

    def describe(self):
        if self.state == "title":
            return "Tron (title)"
        p2 = "CPU" if self.cpu() else "P2"
        return f"Tron ({self.state}, P1 {self.wins[0]} - {self.wins[1]} {p2})"


# ---- Flappy -------------------------------------------------------------------------

BIRD = ([".XXX..", "XXX.X.", "XXXXXX", ".XXX.."],
        ["......", "XXX.X.", "XXXXXX", "XXXX.."])
# The bird tilted, hand-drawn (a 6x4 sprite doesn't survive real rotation): (wings up, wings down)
BIRD_TILT = {
    "up": (["...XX.", ".XX.XX", "XXXXX.", ".XX..."],    # rising: head above the tail
           ["...XX.", "XXX.XX", "XXXXX.", "XX...."]),
    "level": BIRD,
    "down": ([".XX...", "XXXXX.", ".XX.XX", "...XX."],  # falling: head dips below the tail
             ["......", "XXXXX.", "XXX.XX", "X..XX."]),
}


@register
class Flappy(Arcade):
    id = "flappy"
    title = "Flappy"
    description = "Tap A or up to flap through the gaps."
    params = []
    BIRD_X = 18
    PIPE_W = 6
    SPACING = 32

    def reset(self):
        self.score = 0
        self.y, self.vy = self.h / 2 - 2, 0.0
        self.ready = True  # bird hovers until the first flap
        self.pipes = []  # [x, gap_top, gap_size, scored]
        self.dying_at = 0
        self.flap_at = 0
        self.scroll = 0.0

    def press(self, key, player):
        if key in ("a", "up", "b") and not self.dying_at:
            self.ready = False
            self.vy = -36
            self.flap_at = self.now

    def add_pipe(self, x):
        gap = max(11, 16 - self.score // 4)
        prev = self.pipes[-1][1] + self.pipes[-1][2] / 2 if self.pipes else self.h / 2
        lo, hi = gap / 2 + 5, self.h - gap / 2 - 6  # keep a few rows of pipe at each end
        mid = min(max(prev + random.uniform(-14, 14), lo), hi)
        self.pipes.append([x, int(mid - gap / 2), gap, False])

    def step(self):
        if self.dying_at:
            self.vy = min(self.vy + 110 * STEP, 60)
            self.y = min(self.y + self.vy * STEP, self.h - 5)
            if self.now - self.dying_at > 0.8:
                self.game_over()
            return
        if self.ready:
            self.y = self.h / 2 - 2 + math.sin(self.now * 4) * 2
            return
        self.vy = min(self.vy + 110 * STEP, 60)
        self.y = max(self.y + self.vy * STEP, 0)
        speed = min(22 + self.score * 0.4, 32) * STEP
        self.scroll += speed
        for p in self.pipes:
            p[0] -= speed
        self.pipes = [p for p in self.pipes if p[0] > -10]
        if not self.pipes or self.pipes[-1][0] < self.w - self.SPACING:
            self.add_pipe(self.pipes[-1][0] + self.SPACING if self.pipes else self.w + 4)
        bx, by = self.BIRD_X, int(self.y)
        for p in self.pipes:
            if not p[3] and p[0] + self.PIPE_W < bx:
                p[3] = True
                self.score += 1
            # bird box 6x4 (trimmed by a dot each side) against the pipe incl. lip
            if p[0] - 1 < bx + 5 and bx + 1 < p[0] + self.PIPE_W + 1:
                if by + 1 < p[1] or by + 3 > p[1] + p[2]:
                    self.dying_at = self.now
        if by + 4 >= self.h - 1:
            self.y = self.h - 5
            self.dying_at = self.now

    def draw(self, c, now):
        for x, top, gap, _ in self.pipes:
            x = int(x)
            fill(c, x, 0, self.PIPE_W, top)
            fill(c, x - 1, top - 2, self.PIPE_W + 2, 2)
            fill(c, x, top + gap, self.PIPE_W, c.h - top - gap)
            fill(c, x - 1, top + gap, self.PIPE_W + 2, 2)
        # dashed ground scrolls with the pipes
        off = int(self.scroll) % 4
        for x in range(-off, c.w, 4):
            fill(c, x, c.h - 1, 2, 1)
        flapping = now - self.flap_at < 0.15 or (self.ready and int(now * 4) % 2)
        if not self.dying_at or int(now * 8) % 2:
            sprite(c, self.BIRD_X, int(self.y), BIRD_TILT[self.tilt()][1 if flapping else 0])
        if not self.ready:
            s = str(self.score)
            tw = SMALL.width(s)
            fill(c, (c.w - tw) // 2 - 1, 0, tw + 2, 7, 0)
            c.text_center(1, s, SMALL)

    def tilt(self):
        """Nose up while rising, level near the top of the arc, nose down once it's falling."""
        if self.ready or -12 < self.vy < 18:
            return "level"
        return "up" if self.vy < 0 else "down"

    def draw_title(self, c, now):
        sprite(c, 39, 24 + int(math.sin(now * 4) * 2), BIRD[int(now * 4) % 2])
