"""Four-player games for the GameCube adapter: Curve fever, four-sided Pong, Tetris battle and co-op Breakout.

Player 1 is the first controller; the others join by pressing a button on theirs and the computer plays any seat
nobody has taken (a seat goes back to the computer after HUMAN_FOR seconds without a press). While nobody plays,
the title alternates with a computer-only demo, as the maze pack's games do.
"""

import math
import random

from fonts import NORMAL, SMALL

from .arcade import STEP, fill
from .base import P, register
from .games import PIECES, tag
from .pack_maze import DIRS, PackGame

SEATS = P("players", "number", 4, "Players (incl. computer)", min=2, max=4)
NAMES = ("P1", "P2", "P3", "P4")


class MultiGame(PackGame):
    """A seat per player (1..players); seat i is the computer unless controller i has pressed lately."""

    def seats(self):
        return max(2, min(4, int(self.p.get("players") or 4)))

    def name(self, i):
        return NAMES[i] if self.human(i + 1) else f"CPU{i + 1}"

    def short(self, i):
        """P1 / C2: a name that fits a margin."""
        return ("P" if self.human(i + 1) else "C") + str(i + 1)

    def cpu(self, i):
        return not self.human(i + 1)

    def holding(self, i, key):
        return (i + 1, key) in self.held


# ---- Curve fever ---------------------------------------------------------------------------------------------
# Snakes that only turn, leaving trails with gaps now and then; the last one moving wins the round.

TEXTURES = ("solid", "dotted", "dashed", "double")
CURVE_TOP = 6                      # the HUD row above the arena
TURN = 3.2                         # radians a second
GAP_EVERY = (1.6, 3.4)             # seconds between gaps (random in this range)
GAP_LEN = 0.28                     # seconds a gap lasts


class Snake:
    def __init__(self, i, x, y, a, texture):
        self.i, self.x, self.y, self.a, self.texture = i, x, y, a, texture
        self.alive = True
        self.steps = 0
        self.gap_at = random.uniform(*GAP_EVERY) * 0.8 + 0.6
        self.gap_until = 0.0
        self.turn = 0  # -1 left, 0 straight, 1 right
        self.cell = None


@register
class Curve(MultiGame):
    id = "curve"
    title = "Curve fever"
    description = ("Your snake never stops: left and right steer it round the others' trails, which have gaps now and "
                   "then you can slip through. Last one moving wins the round. Each player's trail has its own "
                   "texture: solid, dotted, dashed, double.")
    params = [SEATS, P("wins", "number", 3, "Round wins needed", min=1, max=9),
              P("speed", "select", "normal", "Speed", options=[["slow", "Slow"], ["normal", "Normal"], ["fast", "Fast"]])]

    def reset(self):
        self.n = self.seats()
        self.wins = [0] * self.n
        self.score = 0
        self.round = 0
        self.t = 0.0
        self.new_round()

    def new_round(self):
        self.round += 1
        self.occ = bytearray(self.w * self.h)  # trail cells (collisions)
        self.vis = bytearray(self.w * self.h)  # trail dots as drawn (textures)
        self.snakes = []
        starts = [(14, 14, 0.0), (self.w - 15, self.h - 9, math.pi), (self.w - 15, 14, math.pi / 2), (14, self.h - 9, -math.pi / 2)]
        for i in range(self.n):
            x, y, a = starts[i]
            self.snakes.append(Snake(i, x + random.uniform(-3, 3), y + random.uniform(-2, 2),
                                     a + random.uniform(-0.5, 0.5), TEXTURES[i]))
        self.round_t = self.t
        self.start_at = self.t + 1.2  # a moment to see where everyone is
        self.round_winner = None
        self.round_end = None

    def speed(self):
        return {"slow": 9.0, "fast": 16.0}.get(self.p["speed"], 12.0)

    def free(self, x, y):
        xi, yi = int(x), int(y)
        return 0 <= xi < self.w and CURVE_TOP <= yi < self.h and not self.occ[yi * self.w + xi]

    def look(self, s, turn, steps=26):
        """How many steps the snake can go on, turning `turn`, before hitting something."""
        x, y, a = s.x, s.y, s.a
        v = self.speed() * STEP
        for k in range(steps):
            a += turn * TURN * STEP
            x += math.cos(a) * v
            y += math.sin(a) * v
            if not self.free(x, y) and (int(x), int(y)) != s.cell:
                return k
        return steps

    def cpu_turn(self, s):
        if s.steps % 3:
            return
        ahead = self.look(s, 0)
        if ahead >= 26:  # clear ahead: carry on, with the odd wander so the trails curve
            s.turn = random.choice((-1, 1)) if random.random() < 0.06 else (s.turn if random.random() < 0.5 else 0)
            return
        best = max((self.look(s, t), -abs(t), t) for t in (0, -1, 1))
        s.turn = best[2]

    def human_turn(self, s):
        i = s.i
        s.turn = (-1 if self.holding(i, "left") or self.holding(i, "b") else 0) + (1 if self.holding(i, "right") or self.holding(i, "a") else 0)

    def step(self):
        self.t += STEP
        if self.t < self.start_at:
            return
        if self.round_end is not None:
            if self.t >= self.round_end:
                if max(self.wins) >= int(self.p["wins"]):
                    self.game_over()
                else:
                    self.new_round()
            return
        v = self.speed() * STEP
        alive = [s for s in self.snakes if s.alive]
        for s in alive:
            (self.cpu_turn if self.cpu(s.i) else self.human_turn)(s)
            s.a += s.turn * TURN * STEP
            s.x += math.cos(s.a) * v
            s.y += math.sin(s.a) * v
            s.steps += 1
            cell = (int(s.x), int(s.y))
            if cell == s.cell:
                continue
            if not self.free(s.x, s.y):
                s.alive = False
                continue
            age = self.t - self.round_t
            if age >= s.gap_at:
                s.gap_until = age + GAP_LEN
                s.gap_at = age + GAP_LEN + random.uniform(*GAP_EVERY)
            s.cell = cell
            if age < s.gap_until:
                continue
            i = cell[1] * self.w + cell[0]
            self.occ[i] = 1
            draw = {"solid": True, "dotted": s.steps % 2 == 0, "dashed": s.steps % 7 < 5, "double": True}[s.texture]
            if draw:
                self.vis[i] = 1
            if s.texture == "double":  # a second dot beside the first, across the way it's going
                nx, ny = (cell[0], cell[1] + 1) if abs(math.cos(s.a)) > abs(math.sin(s.a)) else (cell[0] + 1, cell[1])
                if 0 <= nx < self.w and CURVE_TOP <= ny < self.h:
                    self.vis[ny * self.w + nx] = 1
        alive = [s for s in self.snakes if s.alive]
        if len(alive) <= 1:
            if alive:
                self.wins[alive[0].i] += 1
                self.round_winner = alive[0].i
                self.score = self.wins[0]
            self.round_end = self.t + 2.2

    def button(self, key, player):
        pass

    def result(self):
        best = max(range(self.n), key=lambda i: self.wins[i])
        return f"{self.name(best)} WINS", " ".join(str(w) for w in self.wins)

    def draw(self, c, now):
        w = c.w
        for i in range(len(self.vis)):
            if self.vis[i]:
                c.px[i] = 1
        for s in self.snakes:  # heads: a 2x2 block that blinks while the snake is alive
            if s.alive and (int(self.t * 6) % 2 or self.t < self.start_at):
                fill(c, int(s.x) - (1 if s.x % 1 < 0.5 else 0), int(s.y) - (1 if s.y % 1 < 0.5 else 0), 2, 2)
            elif not s.alive:
                fill(c, int(s.x) - 1, int(s.y) - 1, 3, 3, 0)
        fill(c, 0, 0, w, CURVE_TOP, 0)
        c.line(0, CURVE_TOP - 1, w - 1, CURVE_TOP - 1)
        for i, s in enumerate(self.snakes):
            x = 1 + i * 21
            c.text(x, 0, f"{self.short(i)} {self.wins[i]}", SMALL)
            if s.texture != "solid":  # a sample of the trail beside the name
                for k in range(5):
                    if {"dotted": k % 2 == 0, "dashed": k < 4, "double": True}[s.texture]:
                        c.set(x + 15 + k, 2)
                        if s.texture == "double":
                            c.set(x + 15 + k, 3)
            else:
                c.line(x + 15, 2, x + 19, 2)
        if self.t < self.start_at:
            tag(c, None, 18, f"ROUND {self.round}")
        elif self.round_end is not None:
            tag(c, None, 18, f"{self.name(self.round_winner)} WINS ROUND" if self.round_winner is not None else "NOBODY WINS")

    def describe(self):
        return f"{self.title} ({', '.join(f'{self.name(i)} {w}' for i, w in enumerate(getattr(self, 'wins', [])))})"


# ---- Four-player Pong ----------------------------------------------------------------------------------------

PADDLE_V = 9   # left/right paddles, dots tall
PADDLE_H = 16  # top/bottom paddles, dots wide
SIDES = ("left", "right", "top", "bottom")


@register
class Pong4(MultiGame):
    id = "pong4"
    title = "Pong 4"
    description = ("A paddle on each side of the board: P1 left, P2 right, P3 top, P4 bottom. Left and right paddles "
                   "move with up/down, top and bottom with left/right. The ball past your side costs a life; out of "
                   "lives, your side becomes a wall. Last one in wins. It speeds up with every return.")
    params = [SEATS, P("lives", "number", 3, "Lives each", min=1, max=9)]

    def reset(self):
        self.n = self.seats()
        self.lives = [int(self.p["lives"])] * self.n
        self.score = 0
        self.t = 0.0
        self.pos = [(self.h - PADDLE_V) / 2, (self.h - PADDLE_V) / 2, (self.w - PADDLE_H) / 2, (self.w - PADDLE_H) / 2]
        self.aim = [0.0] * 4
        self.returns = 0
        self.winner = None
        self.serve()

    def serve(self):
        self.bx, self.by = self.w / 2, self.h / 2
        live = [i for i in range(self.n) if self.lives[i] > 0]
        toward = random.choice(live)
        a = {0: math.pi, 1: 0.0, 2: -math.pi / 2, 3: math.pi / 2}[toward] + random.uniform(-0.7, 0.7)
        v = 22.0 + 2.5 * self.returns
        self.vx, self.vy = math.cos(a) * v, math.sin(a) * v
        self.pause_until = self.t + 1.0

    def active(self, i):
        return i < self.n and self.lives[i] > 0

    def move_paddle(self, i):
        vertical = i < 2
        span = (self.h - PADDLE_V) if vertical else (self.w - PADDLE_H)
        if self.cpu(i):
            # follows the ball once it's heading its way on its half of the board, less surely as the rally goes
            # on (the ball gets faster and the computer's aim looser), drifts back to the middle otherwise
            coming = ((self.vx < 0 and self.bx < self.w / 2), (self.vx > 0 and self.bx > self.w / 2),
                      (self.vy < 0 and self.by < self.h / 2), (self.vy > 0 and self.by > self.h / 2))[i]
            if coming:
                target = (self.by - PADDLE_V / 2 if vertical else self.bx - PADDLE_H / 2) + self.aim[i]
                rate = (0.9 if vertical else 1.5) / (1 + self.returns / 40)
            else:
                target = span / 2
                rate = 0.35
            self.pos[i] += max(-rate, min(rate, target - self.pos[i]))
        else:
            minus, plus = ("up", "down") if vertical else ("left", "right")
            d = (1.3 if self.holding(i, plus) else 0) - (1.3 if self.holding(i, minus) else 0)
            if not vertical:  # top/bottom players may use up/down too
                d += (1.3 if self.holding(i, "down") else 0) - (1.3 if self.holding(i, "up") else 0)
            self.pos[i] += d
        self.pos[i] = max(0.0, min(span, self.pos[i]))

    def step(self):
        self.t += STEP
        if self.winner is not None:
            if self.t > self.won_at + 2.5:
                self.game_over()
            return
        for i in range(self.n):
            if self.active(i):
                self.move_paddle(i)
        if self.t < self.pause_until:
            return
        self.bx += self.vx * STEP
        self.by += self.vy * STEP
        # the four sides: a paddle (or a wall once that player is out) on each
        lost = None
        if self.bx <= 2:
            if self.active(0) and self.pos[0] - 1.5 <= self.by <= self.pos[0] + PADDLE_V:
                self.bounce(0, (self.by + 1 - (self.pos[0] + PADDLE_V / 2)) / (PADDLE_V / 2))
            elif self.active(0):
                lost = 0
            else:
                self.vx, self.bx = abs(self.vx), 2
        elif self.bx >= self.w - 4:
            if self.active(1) and self.pos[1] - 1.5 <= self.by <= self.pos[1] + PADDLE_V:
                self.bounce(1, (self.by + 1 - (self.pos[1] + PADDLE_V / 2)) / (PADDLE_V / 2))
            elif self.active(1):
                lost = 1
            else:
                self.vx, self.bx = -abs(self.vx), self.w - 4
        if self.by <= 2:
            if self.active(2) and self.pos[2] - 1.5 <= self.bx <= self.pos[2] + PADDLE_H:
                self.bounce(2, (self.bx + 1 - (self.pos[2] + PADDLE_H / 2)) / (PADDLE_H / 2))
            elif self.active(2):
                lost = 2
            else:
                self.vy, self.by = abs(self.vy), 2
        elif self.by >= self.h - 4:
            if self.active(3) and self.pos[3] - 1.5 <= self.bx <= self.pos[3] + PADDLE_H:
                self.bounce(3, (self.bx + 1 - (self.pos[3] + PADDLE_H / 2)) / (PADDLE_H / 2))
            elif self.active(3):
                lost = 3
            else:
                self.vy, self.by = -abs(self.vy), self.h - 4
        if lost is not None and (self.bx < -2 or self.bx > self.w or self.by < -2 or self.by > self.h):
            self.lives[lost] -= 1
            live = [i for i in range(self.n) if self.lives[i] > 0]
            if len(live) == 1:
                self.winner = live[0]
                self.won_at = self.t
                self.score = self.returns if self.winner == 0 else 0
                return
            self.serve()

    def bounce(self, i, hit):
        """The ball off paddle i; `hit` is where along the paddle (-1..1), which sets the angle."""
        speed = min(math.hypot(self.vx, self.vy) * 1.05, 72.0)
        self.returns += 1
        if i == 0:
            self.vx, self.vy, self.bx = abs(self.vx), hit * speed * 0.8, 2
        elif i == 1:
            self.vx, self.vy, self.bx = -abs(self.vx), hit * speed * 0.8, self.w - 4
        elif i == 2:
            self.vy, self.vx, self.by = abs(self.vy), hit * speed * 0.8, 2
        else:
            self.vy, self.vx, self.by = -abs(self.vy), hit * speed * 0.8, self.h - 4
        d = max(1e-6, math.hypot(self.vx, self.vy))
        self.vx, self.vy = self.vx / d * speed, self.vy / d * speed
        if abs(self.vx) < speed * 0.35 and i >= 2:  # keep the ball moving across, not just up and down
            self.vx = math.copysign(speed * 0.35, self.vx or 1)
        if abs(self.vy) < speed * 0.2 and i < 2:
            self.vy = math.copysign(speed * 0.2, self.vy or 1)
        self.aim[i] = random.uniform(-3, 3) * (1 + self.returns / 12)

    def button(self, key, player):
        pass

    def result(self):
        if self.winner is None:
            return None
        return f"{self.name(self.winner)} WINS", f"{self.returns} HITS"

    def draw(self, c, now):
        w, h = c.w, c.h
        for i in range(4):
            if i >= self.n or not self.active(i):  # a wall: a dotted line along that side
                if i == 0:
                    for y in range(0, h, 2):
                        c.set(0, y)
                elif i == 1:
                    for y in range(0, h, 2):
                        c.set(w - 1, y)
                elif i == 2:
                    for x in range(0, w, 2):
                        c.set(x, 0)
                else:
                    for x in range(0, w, 2):
                        c.set(x, h - 1)
                continue
            if i == 0:
                fill(c, 1, int(self.pos[0]), 2, PADDLE_V)
            elif i == 1:
                fill(c, w - 3, int(self.pos[1]), 2, PADDLE_V)
            elif i == 2:
                fill(c, int(self.pos[2]), 1, PADDLE_H, 2)
            else:
                fill(c, int(self.pos[3]), h - 3, PADDLE_H, 2)
        fill(c, int(self.bx), int(self.by), 2, 2)
        # lives: a dot per life in the corner nearest each player
        for i in range(self.n):
            for k in range(self.lives[i]):
                x, y = ((5, 5 + k * 2), (w - 6, 5 + k * 2), (w // 2 - 4 + k * 2, 5), (w // 2 - 4 + k * 2, h - 6))[i]
                c.set(x, y)
        if self.t < self.pause_until and self.winner is None:
            tag(c, None, 16, "SERVE")
        elif self.winner is not None:
            tag(c, None, 16, f"{self.name(self.winner)} WINS")

    def describe(self):
        return f"{self.title} ({', '.join(f'{self.name(i)} {l}' for i, l in enumerate(getattr(self, 'lives', [])))})"


# ---- Tetris battle -------------------------------------------------------------------------------------------

COLS, ROWS, CELL = 10, 20, 2
ATTACK = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4}  # garbage rows sent for lines cleared at once


class Well:
    """One player's Tetris: the usual rules, plus garbage rows from the other well."""

    AI_STEP = 0.14

    def __init__(self, i, t):
        self.i = i
        self.grid = [[0] * COLS for _ in range(ROWS)]
        self.lines = self.sent = 0
        self.pending = 0
        self.dead = False
        self.next = random.choice(list(PIECES))
        self.last_fall = t
        self.ai_at = 0.0
        self.plan = None
        self.spawn()

    def spawn(self):
        self.kind, self.next = self.next, random.choice(list(PIECES))
        self.cells = list(PIECES[self.kind])
        self.px, self.py = 3, 0
        self.plan = None
        if self.collides(self.cells, self.px, self.py):
            self.dead = True

    def collides(self, cells, px, py):
        for x, y in cells:
            gx, gy = px + x, py + y
            if gx < 0 or gx >= COLS or gy >= ROWS or (gy >= 0 and self.grid[gy][gx]):
                return True
        return False

    def rotate(self):
        if self.kind == "O":
            return
        size = 4 if self.kind == "I" else 3
        rotated = [(size - 1 - y, x) for x, y in self.cells]
        for kick in (0, -1, 1, -2, 2):
            if not self.collides(rotated, self.px + kick, self.py):
                self.cells, self.px = rotated, self.px + kick
                return

    def move(self, dx, dy):
        if not self.collides(self.cells, self.px + dx, self.py + dy):
            self.px += dx
            self.py += dy
            return True
        return False

    def lock(self):
        """Lock the piece; -> rows of garbage to send the other way."""
        for x, y in self.cells:
            if self.py + y >= 0:
                self.grid[self.py + y][self.px + x] = 1
        full = [r for r in range(ROWS) if all(self.grid[r])]
        for r in full:
            del self.grid[r]
            self.grid.insert(0, [0] * COLS)
        self.lines += len(full)
        attack = ATTACK.get(len(full), 4)
        if self.pending:  # garbage waiting comes up now (an attack cancels some of it)
            cancel = min(self.pending, attack)
            self.pending -= cancel
            attack -= cancel
            self.take_garbage(self.pending)
            self.pending = 0
        self.sent += attack
        self.spawn()
        return attack

    def take_garbage(self, rows):
        for _ in range(rows):
            hole = random.randrange(COLS)
            if any(self.grid[0]):
                self.dead = True
            del self.grid[0]
            self.grid.append([0 if x == hole else 1 for x in range(COLS)])

    def press(self, key):
        if key == "left":
            self.move(-1, 0)
        elif key == "right":
            self.move(1, 0)
        elif key in ("up", "a"):
            self.rotate()
        elif key == "down":
            return None if self.move(0, 1) else self.lock()
        elif key == "b":
            while self.move(0, 1):
                pass
            return self.lock()
        return None

    def fall(self, t, soft):
        speed = 0.05 if soft else max(0.12, 0.7 - (self.lines // 8) * 0.06)
        if t - self.last_fall >= speed:
            self.last_fall = t
            if not self.move(0, 1):
                return self.lock()
        return None

    # ---- the computer ----

    def placement_value(self, cells, px, py):
        grid = [row[:] for row in self.grid]
        for x, y in cells:
            if py + y < 0:
                return -1e9
            grid[py + y][px + x] = 1
        grid = [r for r in grid if not all(r)]
        lines = ROWS - len(grid)
        grid = [[0] * COLS] * lines + grid
        heights, holes = [], 0
        for x in range(COLS):
            top = next((y for y in range(ROWS) if grid[y][x]), ROWS)
            heights.append(ROWS - top)
            holes += sum(1 for y in range(top, ROWS) if not grid[y][x])
        bump = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
        return 0.76 * lines + (0.6 if lines >= 2 else 0) - 0.51 * sum(heights) - 0.36 * holes - 0.18 * bump

    def make_plan(self):
        size = 4 if self.kind == "I" else 3
        cells, best = list(self.cells), None
        for turns in range(1 if self.kind == "O" else 4):
            for px in range(-3, COLS):
                if self.collides(cells, px, self.py):
                    continue
                py = self.py
                while not self.collides(cells, px, py + 1):
                    py += 1
                v = self.placement_value(cells, px, py) + random.uniform(0, 0.05)
                if best is None or v > best[0]:
                    best = (v, turns, px)
            cells = [(size - 1 - y, x) for x, y in cells]
        return {"turns": best[1], "x": best[2]} if best else {"turns": 0, "x": self.px}

    def autoplay(self, t, skill=1.0):
        if t < self.ai_at:
            return None
        self.ai_at = t + self.AI_STEP / skill
        if self.plan is None:
            self.plan = self.make_plan()
        plan = self.plan
        if plan["turns"]:
            plan["turns"] -= 1
            self.rotate()
        elif self.px != plan["x"] and self.move(1 if plan["x"] > self.px else -1, 0):
            pass
        else:
            return self.press("b")
        return None


@register
class TetrisBattle(MultiGame):
    id = "tetris_battle"
    title = "Tetris battle"
    description = ("Two wells side by side. Left/right to move, up or A to rotate, down to drop faster, B to hard-drop. "
                   "Clearing two or more lines at once sends garbage rows to the other well (2 lines: 1 row, 3: 2, "
                   "a Tetris: 4). Top out and you lose the round. Player 2 joins on their controller; otherwise "
                   "it's the computer.")
    params = [P("wins", "number", 2, "Round wins needed", min=1, max=9),
              P("difficulty", "select", "normal", "Computer",
                options=[["easy", "Easy"], ["normal", "Normal"], ["hard", "Hard"]])]
    WELL_X = (11, 53)  # left edge of each well's cells

    def seats(self):
        return 2

    def reset(self):
        self.n = 2
        self.wins = [0, 0]
        self.score = 0
        self.round = 0
        self.t = 0.0
        self.new_round()

    def new_round(self):
        self.round += 1
        self.wells = [Well(0, self.t), Well(1, self.t)]
        self.round_winner = None
        self.round_end = None
        self.start_at = self.t + 1.0

    def skill(self):
        return {"easy": 0.55, "hard": 1.8}.get(self.p["difficulty"], 1.0)

    def button(self, key, player):
        if self.state != "playing" or self.round_end is not None or self.t < self.start_at:
            return
        i = player - 1
        if 0 <= i < 2 and not self.cpu(i) and not self.wells[i].dead:
            sent = self.wells[i].press(key)
            if sent:
                self.wells[1 - i].pending += sent

    def step(self):
        self.t += STEP
        if self.t < self.start_at:
            return
        if self.round_end is not None:
            if self.t >= self.round_end:
                if max(self.wins) >= int(self.p["wins"]):
                    self.game_over()
                else:
                    self.new_round()
            return
        for i, well in enumerate(self.wells):
            if well.dead:
                continue
            sent = well.autoplay(self.t, self.skill()) if self.cpu(i) else None
            if sent is None:
                sent = well.fall(self.t, self.holding(i, "down") and not self.cpu(i))
            if sent:
                self.wells[1 - i].pending += sent
        dead = [w.dead for w in self.wells]
        if any(dead):
            if not all(dead):
                self.round_winner = dead.index(False)
                self.wins[self.round_winner] += 1
                self.score = self.wins[0]
            self.round_end = self.t + 2.5

    def result(self):
        best = 0 if self.wins[0] >= self.wins[1] else 1
        return f"{self.name(best)} WINS", f"{self.wins[0]}-{self.wins[1]}"

    def draw(self, c, now):
        s = CELL
        oy = c.h - ROWS * s - 1
        for i, well in enumerate(self.wells):
            ox = self.WELL_X[i]
            c.line(ox - 1, 0, ox - 1, c.h - 1)
            c.line(ox + COLS * s, 0, ox + COLS * s, c.h - 1)
            c.line(ox - 1, c.h - 1, ox + COLS * s, c.h - 1)
            for y, row in enumerate(well.grid):
                for x, v in enumerate(row):
                    if v:
                        fill(c, ox + x * s, oy + y * s, s, s)
            if not well.dead:
                for x, y in well.cells:
                    if well.py + y >= 0:
                        fill(c, ox + (well.px + x) * s, oy + (well.py + y) * s, s, s)
            # the margin: name, next piece, lines, garbage on its way
            mx = 1 if i == 0 else c.w - 9
            c.text(mx, 0, self.short(i), SMALL)
            for x, y in PIECES[well.next]:
                fill(c, mx + x * s, 8 + y * s, s, s)
            c.text(mx, 18, str(well.lines), SMALL)
            for k in range(min(well.pending, 8)):  # garbage waiting: a stack of dashes
                c.line(mx + 1, c.h - 2 - k * 2, mx + 5, c.h - 2 - k * 2)
        c.text_center(1, f"{self.wins[0]}-{self.wins[1]}", SMALL, x0=32, w=20)
        if self.t < self.start_at:
            tag(c, None, 18, f"ROUND {self.round}")
        elif self.round_end is not None:
            tag(c, None, 18, f"{self.name(self.round_winner)} WINS" if self.round_winner is not None else "DRAW")

    def describe(self):
        return f"{self.title} ({self.name(0)} {self.wins[0]} - {self.wins[1]} {self.name(1)})" if hasattr(self, "wins") else self.title


# ---- Co-op Breakout ------------------------------------------------------------------------------------------

B_TOP = 7         # ceiling (the HUD sits above it)
BRICKS_Y = 10
BRICK_W, BRICK_H = 6, 2
B_COLS = 12
PADDLE_Y = 39
PADDLE_W = 10


@register
class BreakoutCoop(MultiGame):
    id = "breakout_coop"
    title = "Breakout co-op"
    description = ("Two to four paddles share the bottom, each in its own lane (left/right to move, A to launch). "
                   "One ball to start; bricks with a hole drop a capsule, and a paddle that catches it adds a ball. "
                   "The team shares lives and the score.")
    params = [P("players", "number", 2, "Players (incl. computer)", min=2, max=4),
              P("lives", "number", 3, "Lives", min=1, max=9)]

    def reset(self):
        self.n = self.seats()
        self.score, self.level = 0, 1
        self.lives = int(self.p["lives"])
        self.t = 0.0
        lane = self.w / self.n
        self.px = [lane * i + (lane - PADDLE_W) / 2 for i in range(self.n)]
        self.capsules = []  # falling "+ball" capsules [x, y]
        self.new_level()

    def lane(self, i):
        lane = self.w / self.n
        return lane * i, lane * (i + 1) - PADDLE_W

    def new_level(self):
        self.rows = min(3 + self.level, 6)
        self.bricks = [[1] * B_COLS for _ in range(self.rows)]
        self.bonus = {(r, c) for r in range(self.rows) for c in range(B_COLS) if (r * 5 + c * 3 + self.level) % 9 == 0}
        self.left = self.rows * B_COLS
        self.balls = []
        self.serve()

    def serve(self):
        self.stuck = True
        self.serve_at = self.t
        self.speed = 26 + 4 * self.level
        self.balls = [[self.px[0] + PADDLE_W / 2 - 1, PADDLE_Y - 2, 0.0, 0.0]]
        self.capsules = []

    def launch(self):
        self.stuck = False
        a = random.uniform(-0.5, 0.5)
        self.balls[0][2], self.balls[0][3] = self.speed * math.sin(a), -self.speed * math.cos(a)

    def button(self, key, player):
        if key in ("a", "up", "b") and self.stuck and self.state == "playing":
            self.launch()

    def brick_at(self, x, y):
        x, y = int(x), int(y) - BRICKS_Y
        if y < 0 or x < 0 or x % 7 >= BRICK_W or y % 3 >= BRICK_H:
            return None
        r, col = y // 3, x // 7
        if r < self.rows and col < B_COLS and self.bricks[r][col]:
            return r, col
        return None

    def move_paddles(self):
        lo_hi = [self.lane(i) for i in range(self.n)]
        for i in range(self.n):
            lo, hi = lo_hi[i]
            if self.cpu(i):
                mine = [b for b in self.balls if b[3] > 0 and lo <= b[0] <= hi + PADDLE_W] or [b for b in self.balls if lo <= b[0] <= hi + PADDLE_W]
                target = (mine[0][0] + 1 if mine else (lo + hi + PADDLE_W) / 2) - PADDLE_W / 2
                if self.capsules and not mine:
                    cx = self.capsules[0][0]
                    if lo <= cx <= hi + PADDLE_W:
                        target = cx - PADDLE_W / 2
                self.px[i] += max(-1.1, min(1.1, target - self.px[i]))
                if self.stuck and i == 0 and self.t - self.serve_at > 1:
                    self.launch()
            else:
                self.px[i] += (1.4 if self.holding(i, "right") else 0) - (1.4 if self.holding(i, "left") else 0)
            self.px[i] = max(lo, min(hi, self.px[i]))

    def step(self):
        self.t += STEP
        self.move_paddles()
        if self.stuck:
            self.balls[0][0] = self.px[0] + PADDLE_W / 2 - 1
            return
        keep = []
        for b in self.balls:
            b[0] += b[2] * STEP
            b[1] += b[3] * STEP
            if b[0] < 0 or b[0] > self.w - 2:
                b[2] = -b[2]
                b[0] = max(0, min(self.w - 2, b[0]))
            if b[1] < B_TOP:
                b[3] = abs(b[3])
                b[1] = B_TOP
            hit = self.brick_at(b[0] + 1, b[1] + 1) or self.brick_at(b[0] + 1, b[1] + (1 if b[3] < 0 else 0))
            if hit:
                r, col = hit
                self.bricks[r][col] = 0
                self.left -= 1
                self.score += 10 * self.level
                b[3] = -b[3]
                if (r, col) in self.bonus:
                    self.capsules.append([col * 7 + 2, BRICKS_Y + r * 3 + 2])
            if b[3] > 0 and PADDLE_Y - 2 <= b[1] <= PADDLE_Y:
                for i in range(self.n):
                    if self.px[i] - 1 <= b[0] + 1 <= self.px[i] + PADDLE_W + 1:
                        off = (b[0] + 1 - (self.px[i] + PADDLE_W / 2)) / (PADDLE_W / 2)
                        sp = min(math.hypot(b[2], b[3]) * 1.02, 64)
                        a = max(-1.1, min(1.1, off * 1.2))
                        b[2], b[3] = sp * math.sin(a), -sp * math.cos(a)
                        b[1] = PADDLE_Y - 2
                        break
            if b[1] < self.h:
                keep.append(b)
        self.balls = keep
        for cap in self.capsules:
            cap[1] += 14 * STEP
        caught = []
        for cap in self.capsules:
            if PADDLE_Y - 2 <= cap[1] <= PADDLE_Y + 1:
                for i in range(self.n):
                    if self.px[i] <= cap[0] <= self.px[i] + PADDLE_W:
                        caught.append(cap)
                        a = random.uniform(-0.6, 0.6)
                        self.balls.append([cap[0], PADDLE_Y - 3, self.speed * math.sin(a), -self.speed * math.cos(a)])
                        self.score += 25
                        break
        self.capsules = [cap for cap in self.capsules if cap[1] < self.h and cap not in caught]
        if not self.balls:
            self.lives -= 1
            if self.lives <= 0:
                self.game_over()
                return
            self.serve()
        elif self.left <= 0:
            self.level += 1
            self.new_level()

    def draw(self, c, now):
        c.line(0, B_TOP - 1, c.w - 1, B_TOP - 1)
        for r, row in enumerate(self.bricks):
            for col, v in enumerate(row):
                if v:
                    fill(c, col * 7, BRICKS_Y + r * 3, BRICK_W, BRICK_H)
                    if (r, col) in self.bonus:
                        c.set(col * 7 + 2, BRICKS_Y + r * 3)
                        c.set(col * 7 + 3, BRICKS_Y + r * 3 + 1)
        for i in range(self.n):  # lane marks on the floor, a paddle each (the computer's are hollow in the middle)
            lo, _ = self.lane(i)
            if i:
                c.set(int(lo), c.h - 1)
            fill(c, int(self.px[i]), PADDLE_Y, PADDLE_W, 2)
            if self.cpu(i):
                c.set(int(self.px[i]) + PADDLE_W // 2 - 1, PADDLE_Y + 1, 0)
                c.set(int(self.px[i]) + PADDLE_W // 2, PADDLE_Y + 1, 0)
        for b in self.balls:
            fill(c, int(b[0]), int(b[1]), 2, 2)
        for cap in self.capsules:
            fill(c, int(cap[0]) - 1, int(cap[1]) - 1, 3, 3)
            c.set(int(cap[0]), int(cap[1]), 0)
        c.text(1, 0, str(self.score), SMALL)
        for k in range(self.lives):
            c.line(c.w - 3 - k * 4, 1, c.w - 2 - k * 4, 1)
            c.line(c.w - 3 - k * 4, 2, c.w - 2 - k * 4, 2)
        c.text_center(0, f"L{self.level}", SMALL)
        if self.stuck and not self.cpu(0) and int(now * 2) % 2:
            tag(c, None, 24, "A: LAUNCH")

    def describe(self):
        return f"{self.title} (score {self.score}, level {getattr(self, 'level', 1)})"
