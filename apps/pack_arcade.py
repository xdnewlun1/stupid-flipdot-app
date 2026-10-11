"""Classic arcade pack: Asteroids, Galaxian, Missile Command, Centipede and Lunar Lander.

Built on arcade.Arcade (fixed 40 Hz physics, speeds in dots per second). Each
game plays itself as a demo once the title screen has sat idle for a while;
any button takes over with a fresh game. On the game-over screen A plays again.
"""

import math
import random

from fonts import NORMAL, SMALL

from .arcade import STEP, Arcade, fill, sprite
from .base import P, register

DEMO_AFTER = 20    # seconds on an idle title screen before the demo starts
DEMO_LENGTH = 90   # a demo returns to the title after this long
PACE = {"easy": 0.75, "normal": 1.0, "hard": 1.3}
LIVES = P("lives", "number", 3, "Lives", min=1, max=9)
DIFFICULTY = P("difficulty", "select", "normal", "Difficulty",
               options=[["easy", "Easy"], ["normal", "Normal"], ["hard", "Hard"]])

BOOM = (["X.X", ".X.", "X.X"], ["X...X", ".....", "..X..", ".....", "X...X"])


def _disc(r):
    return [(dy, int(math.sqrt((r + 0.5) ** 2 - dy * dy))) for dy in range(-r, r + 1)]


DISCS = [_disc(r) for r in range(9)]


def disc(c, x, y, r):
    """Filled circle of integer radius r (0-8) centred on dot (x, y)."""
    x, y = int(x), int(y)
    for dy, hw in DISCS[max(0, min(r, 8))]:
        fill(c, x - hw, y + dy, 2 * hw + 1, 1)


def label(c, x, y, s, invert=False):
    """SMALL text on a cleared box so it reads over the playfield. x=None centres it;
    a negative x right-aligns it that many dots from the right edge."""
    tw = SMALL.width(s)
    if x is None:
        x = (c.w - tw) // 2
    elif x < 0:
        x = c.w - tw + x + 1
    fill(c, x - 1, y - 1, tw + 2, 7, 1 if invert else 0)
    c.text(x, y, s, SMALL, v=0 if invert else 1)


def boom(c, x, y, age):
    f = BOOM[0] if age < 0.15 else BOOM[1]
    sprite(c, int(x) - len(f[0]) // 2, int(y) - len(f) // 2, f)


def lives_icons(c, n, icon, y=0):
    """Spare lives as small icons in the top right corner."""
    w = len(icon[0]) + 1
    n = max(0, min(n, 6))
    fill(c, c.w - w * n - 1, y, w * n + 1, len(icon) + 1, 0)
    for i in range(n):
        sprite(c, c.w - w * (i + 1) + 1, y, icon)


class Classic(Arcade):
    """Arcade game with a self-playing demo on an idle title screen and a difficulty setting.
    Subclasses implement autopilot(), which drives the game through hold()/press()."""

    DEMO_X, DEMO_Y = None, 0  # where the DEMO tag goes (x=None: centred)

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.demo = False
        self.demo_at = 0
        self.title_at = self.now
        self.shown = "title"

    @property
    def pace(self):
        return PACE.get(self.p.get("difficulty"), 1.0)

    def input(self, key, action="down", player=1):
        self.title_at = self.now
        if self.demo:
            if action != "down":
                return
            self.demo = False  # a real player takes over with a fresh game
            self.held.clear()
            self.state = "title"
        elif action == "down" and self.state == "over" and key in ("a", "start") and self.now - self.over_at > 0.5:
            self.state = "title"
        super().input(key, action, player)

    def game_over(self):
        if self.demo:
            self.demo = False
            self.state = "title"
            self.title_at = self.now
            return
        self.state = "over"
        self.over_at = self.now

    def hold(self, keys):
        """Demo: the keys the autopilot is holding down this frame."""
        self.held = {(0, k) for k in keys}

    def held_keys(self):
        return {k for _, k in self.held}

    def start_demo(self, now):
        self.demo, self.demo_at = True, now
        self.held.clear()
        self.state = self.shown = "playing"
        self.clock = None
        self.reset()

    def tick(self, now):
        if self.demo:
            best = self.best  # demo scores don't count
            self.autopilot()
            super().tick(now)
            self.best = best
        else:
            super().tick(now)

    def render(self, c, now):
        self.now = now
        if self.state != self.shown:
            self.shown = self.state
            if self.state == "title":
                self.title_at = now
        if self.state == "title":
            if now - self.title_at < DEMO_AFTER:
                self.draw_name(c)
                self.draw_title(c, now)
                self.title_footer(c, now)
                return
            self.start_demo(now)
        if self.demo and now - self.demo_at > DEMO_LENGTH:
            self.demo = False
            self.state = "title"
            return
        super().render(c, now)
        if self.demo and self.state == "playing":
            label(c, self.DEMO_X, self.DEMO_Y, "DEMO")

    def draw_name(self, c):
        t = self.title.upper()
        if NORMAL.width(t) * 2 <= c.w:
            c.text_center(4, t, NORMAL, scale=2)
        elif NORMAL.width(t) <= c.w:
            c.text_center(8, t, NORMAL)
        else:
            for i, word in enumerate(t.split()[:2]):
                c.text_center(2 + i * 9, word, NORMAL)

    def autopilot(self): ...

    def describe(self):
        if self.state == "title":
            return f"{self.title} (title)"
        return f"{self.title} ({'demo' if self.demo else self.state}, score {self.score})"


# ---- Asteroids ------------------------------------------------------------------------

SAUCER = ["..XXX..", "XXXXXXX", ".XXXXX."]
LIFE_SHIP = [".X.", "X.X"]
ROCK_R = (6.0, 3.5, 2.0)
ROCK_SCORE = (20, 50, 100)


def _turned(points, deg):
    """points (x, y) rotated clockwise by deg, rounded to whole dots."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(round(x * ca - y * sa), round(x * sa + y * ca)) for x, y in points]


# The ship points one of 16 ways; vertices are rounded per direction so the outline stays clean.
SHIP_PTS = [_turned([(0, -3.4), (-3, 3), (0, 1), (3, 3)], i * 22.5) for i in range(16)]
SHIP_FLAME = [_turned([(0, 3)], i * 22.5)[0] for i in range(16)]


@register
class Asteroids(Classic):
    id = "asteroids"
    title = "Asteroids"
    description = ("Left/right to turn, up to thrust, A to fire, B for hyperspace. "
                   "Big rocks split in two, then in two again; shoot the saucer for a bonus.")
    params = [LIVES, DIFFICULTY]

    def reset(self):
        self.score, self.wave = 0, 0
        self.lives = int(self.p["lives"])
        self.next_life = 5000
        self.shots, self.rocks, self.bits, self.ufo_shots = [], [], [], []
        self.ufo = None
        self.ufo_at = self.now + random.uniform(12, 20)
        self.fire_at = 0
        self.wave_at = None
        self.spawn_ship()
        self.new_wave()

    def spawn_ship(self):
        self.x, self.y = self.w / 2, self.h / 2
        self.vx = self.vy = 0.0
        self.a = 0.0  # in 16ths of a turn, 0 = up
        self.alive, self.thrust = True, False
        self.safe_until = self.now + 1.5
        self.hyper_until, self.hyper_fail = 0, False

    def new_wave(self):
        self.wave += 1
        for _ in range(min(3 + self.wave, 8)):
            if random.random() < 0.5:
                x, y = random.uniform(0, self.w), random.choice((0.0, self.h - 1.0))
            else:
                x, y = random.choice((0.0, self.w - 1.0)), random.uniform(0, self.h)
            self.add_rock(x, y, 0)
        self.wave_at = None

    def add_rock(self, x, y, size):
        a = random.uniform(0, 2 * math.pi)
        sp = random.uniform(*((5, 9), (9, 14), (13, 19))[size]) * self.pace
        n, r = (9, 7, 5)[size], ROCK_R[size]
        shape = []
        for i in range(n):
            t, f = 2 * math.pi * i / n, random.uniform(0.62, 1.18)
            shape.append((math.cos(t) * r * f, math.sin(t) * r * f))
        self.rocks.append({"x": x, "y": y, "vx": sp * math.cos(a), "vy": sp * math.sin(a), "size": size, "shape": shape})

    def delta(self, x0, y0, x1, y1):
        """Shortest (dx, dy) from point 0 to point 1 on the wrapping playfield."""
        w, h = self.w, self.h
        return (x1 - x0 + w / 2) % w - w / 2, (y1 - y0 + h / 2) % h - h / 2

    def near(self, x0, y0, x1, y1, r):
        dx, dy = self.delta(x0, y0, x1, y1)
        return dx * dx + dy * dy < r * r

    def add(self, points):
        self.score += points
        if self.score >= self.next_life:
            self.next_life += 5000
            self.lives += 1

    def press(self, key, player):
        if key == "a":
            self.fire()
        elif key == "b":
            self.hyperspace()

    def heading(self):
        """Direction index 0-15 and its angle in radians (screen coordinates)."""
        d = round(self.a) % 16
        return d, -math.pi / 2 + d * math.pi / 8

    def fire(self):
        if not self.alive or self.now < self.hyper_until or len(self.shots) >= 4:
            return
        self.fire_at = self.now
        a = self.heading()[1]
        ca, sa = math.cos(a), math.sin(a)
        self.shots.append([self.x + ca * 4, self.y + sa * 4, self.vx + ca * 60, self.vy + sa * 60, self.now + 0.75])

    def hyperspace(self):
        if not self.alive or self.now < self.hyper_until:
            return
        self.x, self.y = random.uniform(6, self.w - 6), random.uniform(8, self.h - 6)
        self.vx = self.vy = 0.0
        self.hyper_until = self.now + 0.5
        self.hyper_fail = random.random() < 0.1  # sometimes the jump goes wrong

    def die(self):
        self.alive = False
        self.burst(self.x, self.y, 10)
        self.lives -= 1
        self.respawn_at = self.now + 2

    def burst(self, x, y, n):
        for _ in range(n):
            a, sp = random.uniform(0, 2 * math.pi), random.uniform(8, 20)
            self.bits.append([x, y, math.cos(a) * sp, math.sin(a) * sp, self.now + random.uniform(0.3, 0.7)])

    def split(self, r):
        self.rocks.remove(r)
        self.burst(r["x"], r["y"], 3 + 2 * (2 - r["size"]))
        if r["size"] < 2:
            for _ in range(2):
                self.add_rock(r["x"], r["y"], r["size"] + 1)

    def step(self):
        now, w, h = self.now, self.w, self.h
        held = self.held_keys()
        if self.alive and now >= self.hyper_until:
            if self.hyper_fail:
                self.hyper_fail = False
                self.die()
            else:
                self.a += (("right" in held) - ("left" in held)) * 11 * STEP  # ~0.7 turns a second
                self.thrust = "up" in held
                if self.thrust:
                    a = self.heading()[1]
                    self.vx += math.cos(a) * 40 * STEP
                    self.vy += math.sin(a) * 40 * STEP
                    sp = math.hypot(self.vx, self.vy)
                    if sp > 36:
                        self.vx, self.vy = self.vx * 36 / sp, self.vy * 36 / sp
                self.vx *= 0.99
                self.vy *= 0.99
                self.x = (self.x + self.vx * STEP) % w
                self.y = (self.y + self.vy * STEP) % h
                if "a" in held and now - self.fire_at > 0.3:
                    self.fire()
        elif not self.alive and now >= self.respawn_at:
            if self.lives <= 0:
                return self.game_over()
            if not any(self.near(r["x"], r["y"], w / 2, h / 2, ROCK_R[r["size"]] + 10) for r in self.rocks):
                self.spawn_ship()

        for s in self.shots + self.ufo_shots:
            s[0] = (s[0] + s[2] * STEP) % w
            s[1] = (s[1] + s[3] * STEP) % h
        self.shots = [s for s in self.shots if s[4] > now]
        self.ufo_shots = [s for s in self.ufo_shots if s[4] > now]
        for r in self.rocks:
            r["x"] = (r["x"] + r["vx"] * STEP) % w
            r["y"] = (r["y"] + r["vy"] * STEP) % h
        for b in self.bits:
            b[0] += b[2] * STEP
            b[1] += b[3] * STEP
        self.bits = [b for b in self.bits if b[4] > now]
        self.step_ufo()

        for s in self.shots[:]:
            for r in self.rocks:
                if self.near(s[0], s[1], r["x"], r["y"], ROCK_R[r["size"]] + 0.8):
                    self.shots.remove(s)
                    self.add(ROCK_SCORE[r["size"]])
                    self.split(r)
                    break
        ship = self.alive and now >= self.hyper_until and now >= self.safe_until
        if ship:
            for r in self.rocks:
                if self.near(self.x, self.y, r["x"], r["y"], ROCK_R[r["size"]] + 2):
                    self.add(ROCK_SCORE[r["size"]])
                    self.split(r)
                    self.die()
                    break
            for s in self.ufo_shots:
                if self.alive and self.near(s[0], s[1], self.x, self.y, 2.5):
                    self.ufo_shots.remove(s)
                    self.die()
                    break
        if self.ufo:
            ux, uy = self.ufo["x"] + 3.5, self.ufo["y"] + 1.5
            hit = None
            for s in self.shots:
                if abs(s[0] - ux) < 4 and abs(s[1] - uy) < 2.5:
                    self.shots.remove(s)
                    self.add(200)
                    hit = True
                    break
            for r in self.rocks:
                if not hit and self.near(ux, uy, r["x"], r["y"], ROCK_R[r["size"]] + 2.5):
                    self.split(r)
                    hit = True
            if not hit and self.alive and now >= self.hyper_until and now >= self.safe_until \
                    and abs(self.x - ux) < 5 and abs(self.y - uy) < 3.5:
                self.die()
                hit = True
            if hit:
                self.burst(ux, uy, 8)
                self.ufo = None
                self.ufo_at = now + random.uniform(15, 25)

        if not self.rocks:
            if self.wave_at is None:
                self.wave_at = now + 2
            elif now >= self.wave_at:
                self.new_wave()

    def step_ufo(self):
        now = self.now
        if self.ufo is None:
            if now >= self.ufo_at and self.alive:
                d = random.choice((-1, 1))
                self.ufo = {"x": -7.0 if d > 0 else float(self.w), "y": random.uniform(8, self.h - 8),
                            "vx": d * 14 * self.pace, "vy": 0, "turn": now + 1, "shoot": now + 1}
            return
        u = self.ufo
        u["x"] += u["vx"] * STEP
        u["y"] = (u["y"] + u["vy"] * STEP) % self.h
        if now >= u["turn"]:
            u["vy"] = random.choice((-8, 0, 8))
            u["turn"] = now + random.uniform(0.8, 1.6)
        if now >= u["shoot"]:
            u["shoot"] = now + 1.4 / self.pace
            if self.alive:
                dx, dy = self.delta(u["x"] + 3.5, u["y"] + 1.5, self.x, self.y)
                a = math.atan2(dy, dx) + random.uniform(-0.4, 0.4)
                self.ufo_shots.append([u["x"] + 3.5, u["y"] + 1.5, math.cos(a) * 35, math.sin(a) * 35, now + 1.2])
        if u["x"] < -8 or u["x"] > self.w + 1:
            self.ufo = None
            self.ufo_at = now + random.uniform(15, 25)

    def wrapped(self, x, y, r):
        """Positions to draw something of radius r at, so it shows on both sides of an edge."""
        xs = [x] + ([x + self.w] if x < r else []) + ([x - self.w] if x > self.w - r else [])
        ys = [y] + ([y + self.h] if y < r else []) + ([y - self.h] if y > self.h - r else [])
        return [(a, b) for a in xs for b in ys]

    def draw(self, c, now):
        for r in self.rocks:
            shape = r["shape"]
            for x, y in self.wrapped(r["x"], r["y"], ROCK_R[r["size"]] + 1):
                px, py = x + shape[-1][0], y + shape[-1][1]
                for dx, dy in shape:
                    c.line(px, py, x + dx, y + dy)
                    px, py = x + dx, y + dy
        if self.alive and now >= self.hyper_until and (now >= self.safe_until or int(now * 8) % 2):
            for x, y in self.wrapped(self.x, self.y, 5):
                self.draw_ship(c, x, y, self.heading()[0], self.thrust and int(now * 12) % 2)
        for s in self.shots + self.ufo_shots:
            c.set(s[0], s[1])
        for b in self.bits:
            c.set(b[0], b[1])
        if self.ufo:
            sprite(c, int(self.ufo["x"]), int(self.ufo["y"]), SAUCER)
        label(c, 0, 0, str(self.score))
        lives_icons(c, self.lives - 1, LIFE_SHIP)

    @staticmethod
    def draw_ship(c, x, y, d, flame=False):
        x, y = round(x), round(y)
        pts = SHIP_PTS[d % 16]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            c.line(x + x0, y + y0, x + x1, y + y1)
        if flame:
            c.set(x + SHIP_FLAME[d % 16][0], y + SHIP_FLAME[d % 16][1])

    def autopilot(self):
        keys = set()
        if self.alive and self.rocks and self.now >= self.hyper_until:
            best = None
            for r in self.rocks:
                dx, dy = self.delta(self.x, self.y, r["x"], r["y"])
                d = math.hypot(dx, dy)
                if best is None or d < best[0]:
                    best = (d, dx, dy, r)
            d, dx, dy, r = best
            t = d / 60  # lead the target by the shot's flight time
            dx += (r["vx"] - self.vx) * t
            dy += (r["vy"] - self.vy) * t
            diff = (math.atan2(dy, dx) - self.heading()[1] + math.pi) % (2 * math.pi) - math.pi
            if diff > 0.2:
                keys.add("right")
            elif diff < -0.2:
                keys.add("left")
            if abs(diff) < 0.3 and d < 40:
                keys.add("a")
            if d < ROCK_R[r["size"]] + 4 and random.random() < 0.1:
                self.press("b", 0)
            elif d > 28 and math.hypot(self.vx, self.vy) < 5 and abs(diff) < 0.3:
                keys.add("up")
        self.hold(keys)

    def draw_title(self, c, now):
        t = now * 0.6
        for i, (cx, cy, r) in enumerate(((16, 26, 5), (66, 24, 4), (54, 30, 2))):
            pts = [(cx + math.cos(t * (1 - i % 2 * 2) + k * 1.1) * r * (0.8 + 0.2 * (k % 2)),
                    cy + math.sin(t * (1 - i % 2 * 2) + k * 1.1) * r * (0.8 + 0.2 * (k % 2))) for k in range(6)]
            for p, q in zip(pts, pts[1:] + pts[:1]):
                c.line(*p, *q)
        self.draw_ship(c, 41, 25, int(now * 3))

    def describe(self):
        if self.state == "title":
            return "Asteroids (title)"
        return f"Asteroids ({'demo' if self.demo else self.state}, wave {self.wave}, score {self.score})"


# ---- Galaxian -----------------------------------------------------------------------------

# 5x4 aliens facing down, two wing frames each: flagship, escort, drone
GX_ALIENS = (
    (["..X..", "XXXXX", "X.X.X", ".X.X."], ["X.X.X", "XXXXX", "..X..", ".X.X."]),
    (["X...X", "XXXXX", ".X.X.", "..X.."], [".X.X.", "XXXXX", "X.X.X", "..X.."]),
    ([".XXX.", "X.X.X", "XXXXX", "X...X"], [".XXX.", "X.X.X", "XXXXX", ".X.X."]),
)
GX_SHIP = ["...X...", "..XXX..", "X.XXX.X", "XXXXXXX"]
GX_ROWS = ((0, (2, 5)), (1, (1, 2, 3, 4, 5, 6)), (2, range(8)), (2, range(8)))
GX_SCORE = ((60, 150), (50, 100), (30, 60))  # (in formation, diving)
GX_LIFE = [".X.", "XXX"]


@register
class Galaxian(Classic):
    id = "galaxian"
    title = "Galaxian"
    description = ("Left/right to move, A or up to fire (hold to keep firing). The formation sways; "
                   "aliens peel off and dive at you, flagships bring escorts. Divers score double.")
    params = [LIVES, DIFFICULTY]
    SHIP_Y = 37

    def reset(self):
        self.score, self.wave = 0, 0
        self.lives = int(self.p["lives"])
        self.px = (self.w - 7) / 2
        self.dead_at = None
        self.booms = []
        self.new_wave()

    def new_wave(self):
        self.wave += 1
        self.home = {(r, col): kind for r, (kind, cols) in enumerate(GX_ROWS) for col in cols}
        self.divers, self.bombs = [], []
        self.shot = None
        self.launch_at = self.now + 2.5
        self.clear_at = None
        self.sway_t = 0.0

    def slot(self, r, col):
        ox = 11 + math.sin(self.sway_t * 0.9) * 10
        return ox + col * 8, 8 + r * 5

    def press(self, key, player):
        if key in ("a", "up", "b"):
            self.fire()

    def fire(self):
        if self.shot is None and self.dead_at is None:
            self.shot = [self.px + 3, self.SHIP_Y - 2.0]

    def launch(self):
        cands = list(self.home)
        if not cands or len(self.divers) >= min(2 + self.wave // 2, 6):
            return
        # aliens at either end of their row go first, like the arcade
        ends = [k for k in cands if (k[0], k[1] - 1) not in self.home or (k[0], k[1] + 1) not in self.home]
        r, col = random.choice(ends or cands)
        side = -1 if col < 4 else 1
        group = [(r, col)]
        if self.home[(r, col)] == 0:  # a flagship takes up to two escorts along
            group += [k for k in ((1, col - 1), (1, col), (1, col + 1)) if k in self.home][:2]
        for k in group:
            x, y = self.slot(*k)
            self.divers.append({"slot": k, "kind": self.home.pop(k), "x": x, "y": y, "phase": "peel",
                                "h": 0.0, "side": side, "vx": 0.0, "aim": random.uniform(-10, 10),
                                "bombs": 1 + min(self.wave, 3) // 2})

    def step(self):
        now, pace = self.now, self.pace
        held = self.held_keys()
        self.sway_t += STEP
        if self.dead_at is None:
            self.px = min(max(self.px + (("right" in held) - ("left" in held)) * 40 * STEP, 0), self.w - 7)
            if held & {"a", "up"}:
                self.fire()
        elif now - self.dead_at > 2:
            if self.lives <= 0:
                return self.game_over()
            if not self.divers:
                self.dead_at = None
                self.px = (self.w - 7) / 2
        self.booms = [b for b in self.booms if now - b[2] < 0.4]

        if self.shot:
            for _ in range(2):
                self.shot[1] -= 50 * STEP
                if self.shot[1] < 5 or self.hit(self.shot[0], self.shot[1]):
                    self.shot = None
                    break

        if now >= self.launch_at and self.dead_at is None:
            self.launch()
            left = len(self.home) + len(self.divers)
            self.launch_at = now + max(0.5, 2.4 - 0.15 * self.wave - (1.2 if left <= 4 else 0)) / pace

        speed = (18 + 1.5 * min(self.wave, 6)) * pace
        for d in self.divers[:]:
            if d["phase"] == "peel":  # loop up and over, then head down
                d["h"] += d["side"] * 5 * STEP
                d["x"] += math.sin(d["h"]) * 16 * STEP
                d["y"] -= math.cos(d["h"]) * 16 * STEP
                if abs(d["h"]) >= math.pi * 0.9:
                    d["phase"], d["vx"] = "dive", math.sin(d["h"]) * 16
            elif d["phase"] == "dive":
                tx = self.px + 1 + d["aim"]
                want = max(-22, min(22, (tx - d["x"]) * 1.5))
                d["vx"] += (want - d["vx"]) * 2.5 * STEP
                d["x"] += d["vx"] * STEP
                d["y"] += speed * STEP
                if d["bombs"] and 10 < d["y"] < 28 and random.random() < 0.05 * pace and self.dead_at is None:
                    d["bombs"] -= 1
                    self.bombs.append([d["x"] + 2, d["y"] + 4, d["vx"] * 0.3])
                if d["y"] > self.h:
                    d["phase"], d["y"] = "return", -5.0
                    d["x"] = min(max(d["x"], 0), self.w - 5)
            else:  # fly back to the formation slot
                sx, sy = self.slot(*d["slot"])
                dx, dy = sx - d["x"], sy - d["y"]
                dist = math.hypot(dx, dy)
                if dist < 24 * STEP + 0.5:
                    self.divers.remove(d)
                    self.home[d["slot"]] = d["kind"]
                    continue
                d["x"] += dx / dist * 24 * STEP
                d["y"] += dy / dist * 24 * STEP
            if self.dead_at is None and d["phase"] == "dive" and self.SHIP_Y - 3 < d["y"] < self.SHIP_Y + 4 \
                    and self.px - 4 < d["x"] < self.px + 7:
                self.divers.remove(d)
                self.score += GX_SCORE[d["kind"]][1]
                self.booms.append([d["x"] + 2, d["y"] + 2, now])
                self.die()

        bomb_speed = 30 * pace * STEP
        for b in self.bombs[:]:
            b[0] += b[2] * STEP
            b[1] += bomb_speed
            if b[1] > self.h:
                self.bombs.remove(b)
            elif self.dead_at is None and self.SHIP_Y <= b[1] + 1 < self.SHIP_Y + 4 and self.px <= b[0] < self.px + 7:
                self.bombs.remove(b)
                self.die()

        if not self.home and not self.divers:
            if self.clear_at is None:
                self.clear_at = now
            elif now - self.clear_at > 1.5:
                self.new_wave()

    def die(self):
        if self.dead_at is not None:
            return
        self.dead_at = self.now
        self.lives -= 1
        self.booms.append([self.px + 3, self.SHIP_Y + 2, self.now])
        self.bombs.clear()
        for d in self.divers:
            if d["phase"] != "return":
                d["phase"] = "return"

    def hit(self, x, y):
        for (r, col), kind in self.home.items():
            ax, ay = self.slot(r, col)
            ax = int(ax)
            if ax <= x < ax + 5 and ay <= y + 2 and y < ay + 4:
                del self.home[(r, col)]
                self.score += GX_SCORE[kind][0]
                self.booms.append([ax + 2, ay + 2, self.now])
                return True
        for d in self.divers:
            if int(d["x"]) <= x < int(d["x"]) + 5 and d["y"] <= y + 2 and y < d["y"] + 4:
                self.divers.remove(d)
                self.score += GX_SCORE[d["kind"]][1]
                self.booms.append([d["x"] + 2, d["y"] + 2, self.now])
                return True
        return False

    def draw(self, c, now):
        f = int(now * 2) % 2
        for (r, col), kind in self.home.items():
            x, y = self.slot(r, col)
            sprite(c, int(x), y, GX_ALIENS[kind][f])
        for d in self.divers:
            sprite(c, int(d["x"]), int(d["y"]), GX_ALIENS[d["kind"]][int(now * 6) % 2])
        for x, y, t in self.booms:
            boom(c, x, y, now - t)
        if self.dead_at is None:
            sprite(c, int(self.px), self.SHIP_Y, GX_SHIP)
        if self.shot:
            fill(c, self.shot[0], self.shot[1], 1, 2)
        for x, y, _ in self.bombs:
            fill(c, x, y, 1, 2)
        fill(c, 0, 0, c.w, 6, 0)
        c.text(0, 0, str(self.score), SMALL)
        if not self.demo:
            c.text_center(0, f"W{self.wave}", SMALL)
        lives_icons(c, self.lives - 1, GX_LIFE, 1)

    def autopilot(self):
        keys = {"a"}
        if self.dead_at is None:
            mid = self.px + 3
            threat = [b for b in self.bombs if b[1] > 18 and abs(b[0] - mid) < 6]
            threat += [(d["x"] + 2, d["y"]) for d in self.divers if d["phase"] == "dive" and d["y"] > 24 and abs(d["x"] + 2 - mid) < 8]
            if threat:
                bx = min(threat, key=lambda b: -b[1])[0]
                keys.add("left" if bx > mid or mid > self.w - 6 else "right")
                if mid < 6:
                    keys.discard("left")
                    keys.add("right")
            else:
                diving = [d for d in self.divers if d["phase"] == "dive" and d["y"] < 26]
                if diving:
                    tx = max(diving, key=lambda d: d["y"])["x"] + 2
                elif self.home:
                    tx = self.slot(*max(self.home, key=lambda k: (k[0], -abs(self.slot(*k)[0] + 2 - mid))))[0] + 2
                else:
                    tx = mid
                if tx > mid + 1:
                    keys.add("right")
                elif tx < mid - 1:
                    keys.add("left")
        self.hold(keys)

    def draw_title(self, c, now):
        f = int(now * 2) % 2
        for i, kind in enumerate((2, 1, 0, 1, 2)):
            sprite(c, 20 + i * 9, 20 + int(math.sin(now * 2 + i) * 2), GX_ALIENS[kind][f])
        sprite(c, 38, 29, GX_SHIP)

    def describe(self):
        if self.state == "title":
            return "Galaxian (title)"
        return f"Galaxian ({'demo' if self.demo else self.state}, wave {self.wave}, score {self.score})"


# ---- Missile Command ----------------------------------------------------------------------

MC_CITY = [".X..X.", "XXX.XX", "XXXXXX"]
MC_RUBBLE = ["X.XX.X"]
MC_BASE = ["..XXX..", ".XXXXX.", "XXXXXXX"]
MC_CROSS = ["..X..", "..X..", "XX.XX", "..X..", "..X.."]
MC_MARK = ["X.X", ".X.", "X.X"]
MC_BASES = (4, 41, 79)                    # centres
MC_CITIES = (12, 20, 28, 49, 57, 65)      # left edges
MC_GROUND = 40
MC_AMMO = 10


@register
class MissileCommand(Classic):
    id = "missile_command"
    title = "Missile Command"
    description = ("Move the crosshair with the arrows, A (or B) fires from the nearest base. "
                   "Explosions knock out incoming missiles; save the six cities.")
    params = [DIFFICULTY]
    TOP = 6

    def reset(self):
        self.score, self.wave = 0, 0
        self.cities = [True] * 6
        self.next_city = 10000
        self.cx, self.cy = self.w / 2, 20.0
        self.ai_fire = 0
        self.new_wave()

    def new_wave(self):
        self.wave += 1
        self.ammo = [MC_AMMO] * 3
        self.incoming, self.counters, self.blasts = [], [], []
        self.to_spawn = min(6 + 2 * self.wave, 22)
        self.spawn_at = self.now + 1.5
        self.phase, self.phase_at = "play", self.now
        self.bonus = 0

    def nearest_base(self, x):
        live = [i for i in range(3) if self.ammo[i] > 0]
        return min(live, key=lambda i: abs(MC_BASES[i] - x)) if live else None

    def press(self, key, player):
        if key in ("a", "b") and self.phase == "play":
            self.fire()

    def fire(self):
        i = self.nearest_base(self.cx)
        if i is None or len(self.counters) >= 8:
            return
        self.ammo[i] -= 1
        bx, by = MC_BASES[i], MC_GROUND - 4
        tx, ty = int(self.cx), int(self.cy)
        d = max(math.hypot(tx - bx, ty - by), 0.1)
        self.counters.append({"sx": bx, "sy": by, "x": float(bx), "y": float(by), "tx": tx, "ty": ty,
                              "vx": (tx - bx) / d * 75, "vy": (ty - by) / d * 75})

    def targets(self):
        t = [(MC_CITIES[i] + 3, MC_GROUND - 1) for i in range(6) if self.cities[i]]
        t += [(MC_BASES[i], MC_GROUND - 2) for i in range(3) if self.ammo[i] > 0]
        return t

    def launch(self, sx, sy):
        tx, ty = random.choice(self.targets() or [(self.w // 2, MC_GROUND - 1)])
        speed = min(3.5 + 0.7 * self.wave, 12) * self.pace
        d = math.hypot(tx - sx, ty - sy)
        split = random.uniform(12, 22) if self.wave >= 2 and random.random() < 0.2 else None
        self.incoming.append({"sx": sx, "sy": sy, "x": sx, "y": sy, "tx": tx, "ty": ty,
                              "vx": (tx - sx) / d * speed, "vy": (ty - sy) / d * speed, "split": split})

    def blast(self, x, y, r, scoring=True):
        self.blasts.append([x, y, self.now, r, scoring])

    @staticmethod
    def radius(b, now):
        a = now - b[2]
        if a < 0.35:
            return round(b[3] * a / 0.35)
        if a < 0.8:
            return b[3]
        return round(b[3] * max(0, 1.2 - a) / 0.4)

    def step(self):
        now = self.now
        held = self.held_keys()
        dx = ("right" in held) - ("left" in held)
        dy = ("down" in held) - ("up" in held)
        self.cx = min(max(self.cx + dx * 45 * STEP, 0), self.w - 1)
        self.cy = min(max(self.cy + dy * 45 * STEP, self.TOP), MC_GROUND - 6)

        if self.phase == "bonus":
            if now - self.phase_at > 3:
                if not any(self.cities):
                    return self.game_over()
                self.new_wave()
            return

        if self.to_spawn and now >= self.spawn_at and len(self.incoming) < min(3 + self.wave // 2, 7):
            for _ in range(min(random.randint(1, 3), self.to_spawn)):
                self.launch(random.uniform(2, self.w - 3), float(self.TOP))
                self.to_spawn -= 1
            self.spawn_at = now + random.uniform(1.5, 3.5) / self.pace

        for m in self.counters[:]:
            m["x"] += m["vx"] * STEP
            m["y"] += m["vy"] * STEP
            if (m["tx"] - m["x"]) * m["vx"] + (m["ty"] - m["y"]) * m["vy"] <= 0:  # went past the target
                self.counters.remove(m)
                self.blast(m["tx"], m["ty"], 5)

        for m in self.incoming[:]:
            m["x"] += m["vx"] * STEP
            m["y"] += m["vy"] * STEP
            if m["split"] is not None and m["y"] >= m["split"]:
                m["split"] = None
                for _ in range(2):
                    self.launch(m["x"], m["y"])
            if m["y"] >= m["ty"]:
                self.incoming.remove(m)
                self.blast(m["tx"], m["ty"] - 1, 3, scoring=False)
                for i in range(6):
                    if self.cities[i] and MC_CITIES[i] <= m["tx"] < MC_CITIES[i] + 6:
                        self.cities[i] = False
                for i in range(3):
                    if abs(MC_BASES[i] - m["tx"]) <= 3:
                        self.ammo[i] = 0

        live = []
        for b in self.blasts:
            r = self.radius(b, now)
            if now - b[2] < 1.2:
                live.append(b)
            if r <= 0:
                continue
            for m in self.incoming[:]:
                if (m["x"] - b[0]) ** 2 + (m["y"] - b[1]) ** 2 <= (r + 0.7) ** 2:
                    self.incoming.remove(m)
                    self.score += 25
                    live.append([m["x"], m["y"], now, 3, True])  # chain reaction
        self.blasts = live

        if not any(self.cities) and not self.blasts:
            self.to_spawn = 0
            self.incoming.clear()
        if not self.to_spawn and not self.incoming and not self.blasts and not self.counters:
            n = sum(self.cities)
            self.bonus = sum(self.ammo) * 5 + n * 100
            self.score += self.bonus
            while self.score >= self.next_city:
                self.next_city += 10000
                if not all(self.cities):
                    self.cities[self.cities.index(False)] = True
            self.phase, self.phase_at = "bonus", now

    def draw(self, c, now):
        g = MC_GROUND
        fill(c, 0, g, c.w, c.h - g)
        for i, bx in enumerate(MC_BASES):
            if self.ammo[i] > 0 or self.phase == "bonus":
                sprite(c, bx - 3, g - 3, MC_BASE)
            for k in range(MC_AMMO - self.ammo[i] if self.phase == "play" else 0, MC_AMMO):
                c.set(bx - 4 + (k % 5) * 2, g + k // 5, 0)  # rounds left show as holes in the ground
        for i, x in enumerate(MC_CITIES):
            if self.cities[i]:
                sprite(c, x, g - 3, MC_CITY)
            else:
                sprite(c, x, g - 1, MC_RUBBLE)
        for m in self.incoming:
            c.line(m["sx"], m["sy"], m["x"], m["y"])
        for m in self.counters:
            c.line(m["sx"], m["sy"], m["x"], m["y"])
            sprite(c, m["tx"] - 1, m["ty"] - 1, MC_MARK)
        for b in self.blasts:
            r = self.radius(b, now)
            if r > 0:
                disc(c, b[0], b[1], r)
        if self.phase == "bonus":
            label(c, None, 14, f"WAVE {self.wave} BONUS")
            label(c, None, 22, str(self.bonus))
        else:
            sprite(c, int(self.cx) - 2, int(self.cy) - 2, MC_CROSS)
        label(c, 0, 0, str(self.score))
        label(c, -1, 0, f"W{self.wave}")

    def autopilot(self):
        keys = set()
        if self.phase == "play":
            best = None
            for m in self.incoming:
                i = self.nearest_base(m["x"])
                if i is None:
                    break
                t = math.hypot(m["x"] - MC_BASES[i], m["y"] - MC_GROUND + 4) / 75 + 0.25
                px, py = m["x"] + m["vx"] * t, m["y"] + m["vy"] * t
                if any((px - k["tx"]) ** 2 + (py - k["ty"]) ** 2 < 16 for k in self.counters) or \
                        any((px - b[0]) ** 2 + (py - b[1]) ** 2 < (b[3] + 1) ** 2 for b in self.blasts):
                    continue  # already taken care of
                if best is None or m["y"] > best[0]:
                    best = (m["y"], px, py)
            if best:
                _, px, py = best
                py = max(py, self.TOP)
                if px > self.cx + 1:
                    keys.add("right")
                elif px < self.cx - 1:
                    keys.add("left")
                if py > self.cy + 1:
                    keys.add("down")
                elif py < self.cy - 1:
                    keys.add("up")
                if abs(px - self.cx) < 2 and abs(py - self.cy) < 2 and self.now - self.ai_fire > 0.3:
                    self.ai_fire = self.now
                    self.press("a", 0)
        self.hold(keys)

    def draw_title(self, c, now):
        g = MC_GROUND
        fill(c, 0, g, c.w, c.h - g)
        for x in MC_CITIES:
            sprite(c, x, g - 3, MC_CITY)
        for bx in MC_BASES:
            sprite(c, bx - 3, g - 3, MC_BASE)
        t = (now * 0.5) % 1
        c.line(70, 20, 70 - 30 * t, 20 + 14 * t)
        if t > 0.7:
            disc(c, 49, 30, int((t - 0.7) * 15))

    def describe(self):
        if self.state == "title":
            return "Missile Command (title)"
        return (f"Missile Command ({'demo' if self.demo else self.state}, wave {self.wave}, "
                f"{sum(self.cities)} cities, score {self.score})")


# ---- Centipede -----------------------------------------------------------------------------

CELL = 3
CP_COLS, CP_ROWS = 28, 14
CP_TOP = 2    # rows 0-1 sit under the HUD
CP_ZONE = 10  # the player keeps to rows 10-13
CP_MUSH = (None, ["X.X", "...", ".X."], ["XXX", "X.X", ".X."], ["XXX", "XXX", ".X."])
CP_HEAD = ["XXX", "X.X", "XXX"]
CP_BODY = [".X.", "XXX", ".X."]
CP_PLAYER = [".X.", "XXX", "X.X"]
CP_SPIDER = (["X...X", ".XXX.", "X.X.X"], [".X.X.", ".XXX.", "X...X"])
CP_FLEA = ["XXX", ".X.", "X.X"]
CP_LIFE = [".X.", "XXX"]


@register
class Centipede(Classic):
    id = "centipede"
    title = "Centipede"
    description = ("Move with the arrows in the bottom area, hold A to fire. Shot segments turn into "
                   "mushrooms and split the centipede; mind the spider and the flea.")
    params = [LIVES, DIFFICULTY]

    def reset(self):
        self.score, self.wave = 0, 1
        self.lives = int(self.p["lives"])
        self.next_life = 12000
        self.mush = {}
        while len(self.mush) < 34:
            cell = (random.randrange(CP_COLS), random.randrange(CP_TOP, CP_ROWS - 2))
            self.mush[cell] = 3
        self.spawn_player()
        self.shot = None
        self.booms = []
        self.start_wave(10)

    def spawn_player(self):
        self.px, self.py = 40.0, float(self.h - 3)
        for cell in [(13, 13), (14, 13), (13, 12), (14, 12)]:
            self.mush.pop(cell, None)
        self.dead_at = None
        self.spider, self.flea = None, None
        self.spider_at = self.now + random.uniform(3, 6)

    def start_wave(self, length):
        col = random.randrange(4, CP_COLS - 4)
        self.pedes = [self.new_pede([(col, CP_TOP - 1 - i) for i in range(length)], random.choice((-1, 1)))]
        for k in range(min(self.wave - 1, 3)):  # later waves add loose heads
            x = random.randrange(CP_COLS)
            self.pedes.append(self.new_pede([(x, CP_TOP - 2 - k * 2)], random.choice((-1, 1))))
        self.speed = min(7 * (1 + 0.08 * (self.wave - 1)), 14) * self.pace

    def new_pede(self, cells, dx):
        k = {"cells": cells, "dx": dx, "vy": 1, "p": 0.0}
        k["nxt"] = [self.head_next(cells[0], k)] + cells[:-1]
        return k

    def head_next(self, cell, k):
        x, y = cell
        if y < CP_TOP:  # still coming in from the top
            return x, y + 1
        nx = x + k["dx"]
        if 0 <= nx < CP_COLS and (nx, y) not in self.mush:
            return nx, y
        k["dx"] = -k["dx"]  # blocked: drop a row (or climb, near the bottom) and turn round
        ny = y + k["vy"]
        if ny >= CP_ROWS:
            k["vy"], ny = -1, y - 1
        elif k["vy"] < 0 and ny < CP_ZONE:
            k["vy"], ny = 1, y + 1
        return x, ny

    def seg_pos(self, k, i):
        (x0, y0), (x1, y1), p = k["cells"][i], k["nxt"][i], k["p"]
        return (x0 + (x1 - x0) * p) * CELL, (y0 + (y1 - y0) * p) * CELL

    def segments(self):
        for k in self.pedes:
            for i in range(len(k["cells"])):
                yield k, i

    def add(self, points):
        self.score += points
        if self.score >= self.next_life:
            self.next_life += 12000
            self.lives += 1

    def press(self, key, player):
        if key in ("a", "b"):
            self.fire()

    def fire(self):
        if self.shot is None and self.dead_at is None:
            self.shot = [int(self.px) + 1, self.py - 1]

    def blocked(self, x, y):
        x, y = int(x), int(y)
        for cx in range(x // CELL, (x + 2) // CELL + 1):
            for cy in range(y // CELL, (y + 2) // CELL + 1):
                if (cx, cy) in self.mush:
                    return True
        return False

    def step(self):
        now = self.now
        held = self.held_keys()
        self.booms = [b for b in self.booms if now - b[2] < 0.4]
        if self.dead_at is not None:
            if now - self.dead_at > 1.5:
                if self.lives <= 0:
                    return self.game_over()
                # mushrooms grow back and the centipede comes in again from the top
                for cell, hp in self.mush.items():
                    if hp < 3 or cell[1] >= CP_ZONE:
                        self.add(5)
                    self.mush[cell] = 3
                n = sum(len(k["cells"]) for k in self.pedes)
                self.spawn_player()
                self.start_wave(max(n, 1))
            return

        dx = ("right" in held) - ("left" in held)
        dy = ("down" in held) - ("up" in held)
        nx = min(max(self.px + dx * 30 * STEP, 0), self.w - 3)
        if not self.blocked(nx, self.py):
            self.px = nx
        ny = min(max(self.py + dy * 30 * STEP, CP_ZONE * CELL), self.h - 3)
        if not self.blocked(self.px, ny):
            self.py = ny
        if "a" in held:
            self.fire()

        if self.shot:
            for _ in range(4):
                self.shot[1] -= 1
                if self.shot[1] < CP_TOP * CELL or self.shoot_at(self.shot[0], int(self.shot[1])):
                    self.shot = None
                    break

        for k in self.pedes:
            k["p"] += self.speed * STEP
            while k["p"] >= 1:
                k["p"] -= 1
                k["cells"] = k["nxt"]
                k["nxt"] = [self.head_next(k["cells"][0], k)] + k["cells"][:-1]

        self.step_spider()
        self.step_flea()

        px, py = self.px, self.py
        for k, i in self.segments():
            sx, sy = self.seg_pos(k, i)
            if abs(sx - px) < 2.5 and abs(sy - py) < 2.5:
                return self.die()
        if self.spider and abs(self.spider[0] + 1 - px) < 3.5 and abs(self.spider[1] - py) < 2.5:
            return self.die()
        if self.flea and abs(self.flea[0] - px) < 2.5 and abs(self.flea[1] - py) < 2.5:
            return self.die()

        if not self.pedes:
            self.wave += 1
            self.start_wave(10)

    def step_spider(self):
        now = self.now
        s = self.spider
        if s is None:
            if now >= self.spider_at:
                d = random.choice((-1, 1))
                self.spider = [-5.0 if d > 0 else float(self.w), random.uniform(28, 36),
                               d * random.uniform(10, 16) * self.pace, random.choice((-1, 1)) * 22 * self.pace]
            return
        s[0] += s[2] * STEP
        s[1] += s[3] * STEP
        if s[1] < (CP_ZONE - 2) * CELL or s[1] > self.h - 3:
            s[3] = -s[3]
            s[1] = min(max(s[1], (CP_ZONE - 2) * CELL), self.h - 3)
        elif random.random() < 0.02:
            s[3] = -s[3]
        if random.random() < 0.03:  # nibbles mushrooms on its way
            self.mush.pop((int(s[0] + 2) // CELL, int(s[1] + 1) // CELL), None)
        if s[0] < -6 or s[0] > self.w + 1:
            self.spider = None
            self.spider_at = now + random.uniform(3, 7)

    def step_flea(self):
        f = self.flea
        if f is None:
            low = sum(1 for _, y in self.mush if y >= CP_ZONE)
            if self.wave >= 2 and low < 5 and random.random() < 0.01:
                self.flea = [random.randrange(CP_COLS) * CELL, float(CP_TOP * CELL), CP_TOP]
            return
        f[1] += 36 * self.pace * STEP
        row = int(f[1]) // CELL
        if row != f[2]:  # drops mushrooms as it falls
            f[2] = row
            if row < CP_ROWS - 1 and random.random() < 0.3:
                self.mush.setdefault((f[0] // CELL, row), 3)
        if f[1] > self.h:
            self.flea = None

    def shoot_at(self, x, y):
        """Hit whatever is at dot (x, y); True if the shot is used up."""
        for k, i in self.segments():
            sx, sy = self.seg_pos(k, i)
            if sx <= x < sx + 3 and sy <= y < sy + 3:
                self.split(k, i)
                return True
        cell = (x // CELL, y // CELL)
        if cell in self.mush:
            self.mush[cell] -= 1
            if not self.mush[cell]:
                del self.mush[cell]
                self.add(1)
            return True
        s = self.spider
        if s and s[0] <= x < s[0] + 5 and s[1] <= y < s[1] + 3:
            d = abs(s[1] - self.py)
            self.add(900 if d < 6 else 600 if d < 12 else 300)
            self.booms.append([s[0] + 2, s[1] + 1, self.now])
            self.spider = None
            self.spider_at = self.now + random.uniform(3, 7)
            return True
        f = self.flea
        if f and f[0] <= x < f[0] + 3 and f[1] <= y < f[1] + 3:
            self.add(200)
            self.booms.append([f[0] + 1, f[1] + 1, self.now])
            self.flea = None
            return True
        return False

    def split(self, k, i):
        sx, sy = self.seg_pos(k, i)
        cell = (round(sx / CELL), round(sy / CELL))
        if cell[1] >= CP_TOP:
            self.mush[cell] = 3
        self.add(100 if i == 0 else 10)
        tail_cells, tail_nxt = k["cells"][i + 1:], k["nxt"][i + 1:]
        k["cells"], k["nxt"] = k["cells"][:i], k["nxt"][:i]
        if not k["cells"]:
            self.pedes.remove(k)
        if tail_cells:
            dx = tail_nxt[0][0] - tail_cells[0][0] or k["dx"]
            self.pedes.append({"cells": tail_cells, "nxt": tail_nxt, "dx": dx, "vy": k["vy"], "p": k["p"]})

    def die(self):
        self.dead_at = self.now
        self.lives -= 1
        self.shot = None
        self.booms.append([self.px + 1, self.py + 1, self.now])

    def draw(self, c, now):
        for (x, y), hp in self.mush.items():
            sprite(c, x * CELL, y * CELL, CP_MUSH[hp])
        for k, i in self.segments():
            sx, sy = self.seg_pos(k, i)
            sprite(c, round(sx), round(sy), CP_HEAD if i == 0 else CP_BODY)
        if self.spider:
            sprite(c, int(self.spider[0]), int(self.spider[1]), CP_SPIDER[int(now * 6) % 2])
        if self.flea:
            sprite(c, self.flea[0], int(self.flea[1]), CP_FLEA)
        if self.dead_at is None:
            sprite(c, int(self.px), int(self.py), CP_PLAYER)
        if self.shot:
            fill(c, self.shot[0], self.shot[1], 1, 2)
        for x, y, t in self.booms:
            boom(c, x, y, now - t)
        fill(c, 0, 0, c.w, CP_TOP * CELL, 0)
        c.text(0, 0, str(self.score), SMALL)
        lives_icons(c, self.lives - 1, CP_LIFE, 1)

    def autopilot(self):
        keys = {"a", "down"}
        if self.dead_at is None:
            mid = self.px + 1
            s = self.spider
            if s and abs(s[0] + 2 - mid) < 14 and abs(s[1] - self.py) < 12:
                keys.add("left" if s[0] + 2 > mid else "right")
                if mid < 4 or mid > self.w - 4:
                    keys ^= {"left", "right"}
            else:
                low = None
                for k, i in self.segments():
                    sx, sy = self.seg_pos(k, i)
                    if low is None or sy > low[1]:
                        low = (sx + 1, sy)
                if low and low[0] > mid + 1:
                    keys.add("right")
                elif low and low[0] < mid - 1:
                    keys.add("left")
        self.hold(keys)

    def draw_title(self, c, now):
        for x, y in ((8, 20), (70, 22), (60, 28), (20, 29)):
            sprite(c, x, y, CP_MUSH[3])
        n = int(now * 8)
        for i in range(8):
            x = 30 + ((n - i * 3) % 30) - 5
            sprite(c, x, 24, CP_HEAD if i == 0 else CP_BODY)

    def describe(self):
        if self.state == "title":
            return "Centipede (title)"
        return f"Centipede ({'demo' if self.demo else self.state}, wave {self.wave}, score {self.score})"


# ---- Lunar Lander ---------------------------------------------------------------------------

LL_STEP = 15          # the lander turns in 15 degree steps, up to 90 either way
LL_FUEL = 800
LL_SAFE_V, LL_SAFE_H = 8, 4
LL_PADS = ((11, 5), (14, 3), (18, 2))  # (width, score multiplier)


# lander lines in its own frame: centre (0, 0), up = -y; feet at y = 3
LANDER_LINES = (((-1, -3), (1, -3)), ((-2, -2), (2, -2)), ((-2, -1), (2, -1)), ((-3, 0), (3, 0)),
                ((-2, 0), (-3, 3)), ((2, 0), (3, 3)), ((0, -4), (0, -4)))


def _lander(deg):
    """Dots of the lander turned clockwise by deg, relative to its centre."""
    from canvas import Canvas
    c = Canvas(13, 13)
    for p, q in LANDER_LINES:
        (x0, y0), (x1, y1) = _turned([p, q], deg)
        c.line(x0 + 6, y0 + 6, x1 + 6, y1 + 6)
    return [(i % 13 - 6, i // 13 - 6) for i, v in enumerate(c.px) if v]


LANDER = {d: _lander(d) for d in range(-90, 91, LL_STEP)}


@register
class LunarLander(Classic):
    id = "lunar_lander"
    title = "Lunar Lander"
    description = (f"Left/right to turn, up or A to fire the engine. Touch down upright on a flat pad, slower than "
                   f"{LL_SAFE_V} down and {LL_SAFE_H} across; smaller pads score more. Fuel refills when you lose a life.")
    params = [LIVES, DIFFICULTY]
    TOP = 6
    DEMO_X, DEMO_Y = 1, 8

    def reset(self):
        self.score = 0
        self.lives = int(self.p["lives"])
        self.fuel = LL_FUEL
        self.landings = 0
        self.new_round()

    def new_round(self):
        self.terrain()
        self.x, self.y = random.uniform(12, self.w - 12), 10.0
        self.vx, self.vy = random.choice((-1, 1)) * random.uniform(3, 7), 0.0
        self.angle = 0.0
        self.firing = False
        self.phase, self.phase_at = "fly", self.now
        self.message = None
        self.bits = []

    def terrain(self):
        w, h = self.w, self.h
        hgt, y = [], random.uniform(26, 36)
        for _ in range(w):
            y = min(max(y + random.uniform(-2.2, 2.2), 16), h - 3)
            hgt.append(y)
        self.pads = []
        spots = list(LL_PADS)
        random.shuffle(spots)
        for i, (pw, mult) in enumerate(spots):  # one pad in each third of the board
            x0 = random.randint(i * 28 + 1, i * 28 + 27 - pw)
            py = int(min(max(sum(hgt[x0:x0 + pw]) / pw, 18), 32))
            for x in range(x0 - 3, x0 + pw + 3):
                if 0 <= x < w:
                    edge = max(x0 - x, x - (x0 + pw - 1), 0)  # blend the slopes either side into the pad
                    hgt[x] = py if edge == 0 else hgt[x] + (py - hgt[x]) * (1 - edge / 4)
            self.pads.append((x0, x0 + pw - 1, py, mult))
        self.ground = [int(v) for v in hgt]
        # the terrain never changes during a round: draw it once and copy it into each frame
        from canvas import Canvas
        bg = Canvas(w, h)
        for x, g in enumerate(self.ground):
            fill(bg, x, g, 1, h - g)
        for x0, x1, py, mult in self.pads:  # a slab with its multiplier cut out underneath
            fill(bg, x0 + 1, py + 1, x1 - x0 - 1, 1, 0)
            s = f"X{mult}"
            bg.text((x0 + x1 + 1 - SMALL.width(s)) // 2, py + 3, s, SMALL, v=0)
        self.bg = bytes(bg.px)

    def shown_angle(self):
        return max(-90, min(90, round(self.angle / LL_STEP) * LL_STEP))

    def points(self):
        """Feet and cabin corners in board coordinates."""
        a = math.radians(self.shown_angle())
        ca, sa = math.cos(a), math.sin(a)
        for lx, ly in ((-3, 3), (3, 3), (-2, -2), (2, -2), (-3, 0), (3, 0), (0, -4)):
            yield self.x + lx * ca - ly * sa, self.y + lx * sa + ly * ca

    def step(self):
        now = self.now
        if self.phase != "fly":
            for b in self.bits:
                b[3] += 10 * STEP
                b[0] += b[2] * STEP
                b[1] += b[3] * STEP
            if now - self.phase_at > 3:
                if self.phase == "crashed":
                    self.lives -= 1
                    if self.lives <= 0:
                        return self.game_over()
                    self.fuel = LL_FUEL
                self.new_round()
            return
        held = self.held_keys()
        self.angle = max(-90, min(90, self.angle + (("right" in held) - ("left" in held)) * 150 * STEP))
        self.firing = bool(held & {"up", "a"}) and self.fuel > 0
        gravity = 5 * self.pace
        if self.firing:
            self.fuel = max(0, self.fuel - 55 * STEP)
            a = math.radians(self.shown_angle())
            self.vx += math.sin(a) * 13 * STEP
            self.vy -= math.cos(a) * 13 * STEP
        self.vy += gravity * STEP
        self.x += self.vx * STEP
        self.y = max(self.y + self.vy * STEP, -8)
        if self.x < 3 or self.x > self.w - 4:
            self.x, self.vx = min(max(self.x, 3), self.w - 4), 0.0
        pts = list(self.points())
        if any(py >= self.ground[min(max(int(px), 0), self.w - 1)] for px, py in pts):
            self.touch_down(pts)

    def touch_down(self, pts):
        lx, rx = round(pts[0][0]), round(pts[1][0])  # the feet, where they're drawn
        pad = next((p for p in self.pads if p[0] <= lx and rx <= p[1]), None)
        upright = abs(self.shown_angle()) <= LL_STEP
        self.phase_at = self.now
        if pad and upright and self.vy <= LL_SAFE_V and abs(self.vx) <= LL_SAFE_H:
            soft = self.vy <= LL_SAFE_V / 2
            gain = (50 if soft else 25) * pad[3]
            self.score += gain
            self.landings += 1
            self.fuel = min(LL_FUEL, self.fuel + (100 if soft else 50))
            self.phase = "landed"
            self.angle = 0
            self.y = pad[2] - 4.0
            self.message = ("PERFECT" if soft else "GOOD LANDING", f"+{gain}")
        else:
            self.phase = "crashed"
            reason = "TOO FAST" if self.vy > LL_SAFE_V or abs(self.vx) > LL_SAFE_H else ("NOT UPRIGHT" if not upright else "NO PAD")
            self.message = ("CRASHED", reason)
            self.bits = [[self.x, self.y, random.uniform(-14, 14), random.uniform(-16, -4)] for _ in range(10)]

    def draw(self, c, now):
        c.px[:] = self.bg
        if self.phase == "crashed":
            for b in self.bits:
                c.set(b[0], b[1])
        else:
            x, y = int(round(self.x)), int(round(self.y))
            for dx, dy in LANDER[self.shown_angle()]:
                c.set(x + dx, y + dy)
            if self.firing and self.phase == "fly":
                a = math.radians(self.shown_angle())
                n = 3 + int(now * 15) % 2
                c.line(self.x - math.sin(a) * 2, self.y + math.cos(a) * 2,
                       self.x - math.sin(a) * n, self.y + math.cos(a) * n)
        # HUD: fuel gauge, speeds (shown inverted when too fast to land), score
        fill(c, 0, 0, c.w, self.TOP, 0)
        c.text(0, 0, "F", SMALL)
        c.rect(4, 0, 22, 5)
        fill(c, 5, 1, round(20 * self.fuel / LL_FUEL), 3)
        v, hz = round(self.vy), round(abs(self.vx))
        label(c, 29, 0, f"V{v}", invert=self.phase == "fly" and v > LL_SAFE_V)
        label(c, 44, 0, f"H{hz}", invert=self.phase == "fly" and hz > LL_SAFE_H)
        s = str(self.score)
        c.text(c.w - SMALL.width(s), 0, s, SMALL)
        if self.message:
            label(c, None, 9, self.message[0])
            label(c, None, 16, self.message[1])

    def autopilot(self):
        keys = set()
        if self.phase == "fly":
            # aim for the pad that pays best for the distance
            pad = max(self.pads, key=lambda p: p[3] * 10 - abs((p[0] + p[1]) / 2 - self.x))
            dx = (pad[0] + pad[1]) / 2 - self.x
            alt = pad[2] - (self.y + 3)
            # lowest clearance over the ground between here and the pad
            x0, x1 = sorted((int(self.x), int(self.x + dx)))
            path = self.ground[max(x0 - 4, 0):min(x1 + 5, self.w)]
            clear = min(path) - (self.y + 3) if abs(dx) > 4 else alt
            want_vx = max(-7, min(7, dx * 0.4))
            err = want_vx - self.vx
            want_a = max(-45, min(45, err * 10)) if alt > 5 else max(-15, min(15, err * 6))
            if abs(dx) < 1 and abs(self.vx) < 1 and alt < 8:
                want_a = 0
            # stay clear of the hills until over the pad, then come down gently
            if abs(dx) > 2:
                want_vy = -3 if clear < 7 else min(1 + (clear - 7) * 0.3, 5)
            else:
                want_vy = min(1.5 + alt * 0.3, 7)
            if self.angle < want_a - 7:
                keys.add("right")
            elif self.angle > want_a + 7:
                keys.add("left")
            if self.vy > want_vy or (abs(err) > 1.5 and abs(self.shown_angle()) >= 30 and self.vy > want_vy - 4):
                keys.add("up")
        self.hold(keys)

    def draw_title(self, c, now):
        fill(c, 0, 31, 30, 3)
        fill(c, 30, 29, 14, 5)
        fill(c, 31, 30, 12, 1, 0)
        fill(c, 44, 31, 40, 3)
        y = 19 + (now * 3) % 6
        for dx, dy in LANDER[0]:
            c.set(37 + dx, int(y) + dy)
        if int(now * 8) % 2:
            c.line(37, int(y) + 4, 37, int(y) + 5)

    def describe(self):
        if self.state == "title":
            return "Lunar Lander (title)"
        return f"Lunar Lander ({'demo' if self.demo else self.state}, score {self.score})"
