"""Game pack: Dino Run, Road Racer, Cave Copter, Stacker, Sky Jump and Pitfall Run.

Built on arcade.Arcade (fixed 40 Hz physics, speeds in dots per second). After a
few idle seconds on the title screen each game plays itself; any button takes
over. Scenery is kept sparse because every changed dot is a physical flip.
"""

import math
import random

from fonts import NORMAL, SMALL

from .arcade import STEP, Arcade, fill
from .base import P, register

DEMO_AFTER = 5  # idle seconds on the title screen before the demo starts
DEMO_LENGTH = 90  # a demo game ends after this many seconds at most
DEMO = P("demo", "checkbox", True, "Play a demo when nobody is playing")
ONES = b"\x01" * 256
ZEROS = bytes(256)


def spr(rows):
    """Compile a sprite: 'X' = dot on, 'o' = dot off (hides what's behind), else transparent."""
    on = [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "X"]
    off = [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "o"]
    return on, off, len(rows[0]), len(rows)


def mirror(rows):
    return [r[::-1] for r in rows]


def blit(c, x, y, s, clip=None):
    """Draw a compiled sprite; rows at or below `clip` are skipped."""
    on, off, _, _ = s
    x, y = int(x), int(y)
    w, px = c.w, c.px
    h = c.h if clip is None else min(c.h, clip)
    for dx, dy in off:
        xx, yy = x + dx, y + dy
        if 0 <= xx < w and 0 <= yy < h:
            px[yy * w + xx] = 0
    for dx, dy in on:
        xx, yy = x + dx, y + dy
        if 0 <= xx < w and 0 <= yy < h:
            px[yy * w + xx] = 1


def overlap(a, ax, ay, b, bx, by):
    """Dot-exact collision between two compiled sprites."""
    ax, ay, bx, by = int(ax), int(ay), int(bx), int(by)
    if ax >= bx + b[2] or bx >= ax + a[2] or ay >= by + b[3] or by >= ay + a[3]:
        return False
    dots = {(ax + dx, ay + dy) for dx, dy in a[0]}
    return any((bx + dx, by + dy) in dots for dx, dy in b[0])


def hspan(c, x0, x1, y, v=1):
    """Dots x0..x1 (inclusive) on row y, clipped."""
    if not 0 <= y < c.h:
        return
    x0, x1 = max(int(x0), 0), min(int(x1), c.w - 1)
    if x1 >= x0:
        i = y * c.w
        c.px[i + x0:i + x1 + 1] = (ONES if v else ZEROS)[:x1 - x0 + 1]


def label(c, x, y, s, align="left"):
    """SMALL text in a cleared box so it reads over scenery."""
    tw = SMALL.width(s)
    if align == "center":
        x = (c.w - tw) // 2
    elif align == "right":
        x = x - tw + 1
    fill(c, x - 1, y - 1, tw + 2, 7, 0)
    c.text(x, y, s, SMALL)


class PackGame(Arcade):
    """Arcade game with an attract-mode demo, restart on A and a best score from the
    shared high-score table. Subclasses read buttons through keys() and set
    demo_keys (and call press()) from autoplay()."""

    over_text = "GAME OVER"

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.demo = False
        self.demo_keys = set()
        self.idle_at = None

    def keys(self):
        if self.demo:
            return self.demo_keys
        return {k for _, k in self.held}

    def input(self, key, action="down", player=1):
        if action == "down":
            if self.demo:  # a real player takes over
                self.demo = False
                self.state = "title"
            elif self.state == "over" and key in ("a", "start") and self.now - self.over_at > 0.8:
                self.state = "title"
        super().input(key, action, player)

    def reset(self):
        self.over_text = type(self).over_text

    def game_over(self):
        if self.demo:
            self.demo = False
            self.state = "title"
            self.idle_at = self.now
            return
        super().game_over()
        self.over_at = self.now  # the simulated clock, not wall time

    def tick(self, now):
        self.now = now
        if self.clock is None or now - self.clock > 0.5:
            self.clock = now - STEP
        while now - self.clock >= STEP and self.state == "playing":
            self.clock += STEP
            if self.demo:
                if now > self.demo_until:
                    return self.game_over()
                self.autoplay()
            self.step()
        if not self.demo and self.score > self.best:
            self.best = self.score

    def autoplay(self): ...

    def render(self, c, now):
        self.now = now
        if self.state == "title":
            if self.idle_at is None:
                self.idle_at = now
            if self.p.get("demo") and now - self.idle_at > DEMO_AFTER:
                self.demo, self.state, self.idle_at = True, "playing", None
                self.demo_until = now + DEMO_LENGTH
                self.demo_keys = set()
                self.reset()
        else:
            self.idle_at = None
        if self.state == "playing" and self.demo:
            self.tick(now)
            self.draw(c, now)
            return
        if self.state == "over":
            c.text_center(4, self.over_text, NORMAL)
            c.text_center(14, str(self.score), NORMAL, scale=2)
            c.text_center(34, f"BEST {self.best}" if int(now / 2) % 2 == 0 else "A: PLAY AGAIN", SMALL)
            if now - self.over_at > 8:
                self.state = "title"
            return
        super().render(c, now)

    def demo_label(self, c, x, y, align="left"):
        if self.demo:
            label(c, x, y, "DEMO", align)
            return True
        return False


# ---- Dino Run -----------------------------------------------------------------------

DINO_HEAD = [
    "......XXXXX.",
    ".....XX.XXXX",
    ".....XXXXXXX",
    ".....XXXXXXX",
    ".....XXXX...",
    ".....XXXXXX.",
    "X...XXXX....",
    "X..XXXXXXX..",
    "XXXXXXXXX.X.",
    ".XXXXXXXX...",
    "..XXXXXX....",
]
DINO = {
    "stand": spr(DINO_HEAD + ["...X..X.....", "...XX.XX...."]),
    "run1": spr(DINO_HEAD + ["...X..XX....", "...XX......."]),
    "run2": spr(DINO_HEAD + ["..XX..X.....", "......XX...."]),
    "duck1": spr(["X.........XXXXX.", "XXX...XXXXX.XXXX", ".XXXXXXXXXXXXXXX", "..XXXXXXXXXXX...",
                  "...XXXXXXX.XX...", "...X..XX........", "...XX..........."]),
    "duck2": spr(["X.........XXXXX.", "XXX...XXXXX.XXXX", ".XXXXXXXXXXXXXXX", "..XXXXXXXXXXX...",
                  "...XXXXXXX.XX...", "..XX...X........", ".......XX......."]),
    "dead": spr(["......XXXXX.", ".....X.X.XXX", ".....XX.XXXX", ".....X.X.XXX"] + DINO_HEAD[4:]
                + ["...X..X.....", "...XX.XX...."]),
}
CACTUS_SMALL = ["..X..", "..X..", "X.X..", "X.X.X", "XXX.X", "..XXX", "..X..", "..X.."]
CACTUS_BIG = ["...XX...", "...XX...", "X..XX...", "X..XX..X", "X..XX..X", "XXXXX..X",
              "...XXXXX", "...XX...", "...XX...", "...XX...", "...XX..."]
PTERO = (spr(["....X.....", "....XX....", "..X.XXX...", ".XX.XXXX..", "XXXXXXXXXX", "....XXXXX.", ".........."]),
         spr(["..........", "..........", "XX........", ".XXXXXXXXX", "....XXXXX.", "....XXX...", "....X....."]))
CLOUD = spr(["...XXX...", ".XX...XX.", "XXXXXXXXX"])
_groups = {}


def cactus_group(big, n):
    """n cacti side by side as one sprite, bottoms aligned."""
    key = (big, n)
    if key not in _groups:
        base = CACTUS_BIG if big else CACTUS_SMALL
        _groups[key] = spr([".".join([r] * n) for r in base])
    return _groups[key]


class Thing:
    """A moving object in a scrolling game."""

    def __init__(self, x, y, kind, sprite=None, **extra):
        self.x, self.y, self.kind, self.sprite = float(x), float(y), kind, sprite
        self.__dict__.update(extra)


@register
class Dino(PackGame):
    id = "dino"
    title = "Dino Run"
    description = "A or up to jump (hold it for a higher jump), down to duck or drop faster. Jump the cacti, duck the low birds."
    params = [P("speed", "number", 36, "Starting speed (dots per second)", min=20, max=60),
              P("night", "checkbox", True, "Night now and then (inverts the board)"),
              DEMO]
    GROUND = 37
    X = 4
    JUMP, GRAVITY = 100, 220

    def reset(self):
        super().reset()
        self.score, self.dist = 0, 0.0
        self.h, self.vy = 0.0, 0.0  # height above the ground, up is positive
        self.ducking = False
        self.obs = []
        self.gap = 50.0
        self.pebbles = [[random.uniform(0, self.w), random.choice((39, 40, 41)), random.choice((1, 1, 2))] for _ in range(7)]
        self.cloud = [60.0, 9]
        self.dying_at = 0
        self.speed = float(self.p["speed"])
        self.legs = 0.0

    def dino(self):
        if self.dying_at:
            return DINO["dead"]
        if self.ducking:
            return DINO["duck1" if int(self.legs / 5) % 2 else "duck2"]
        if self.h > 0:
            return DINO["stand"]
        return DINO["run1" if int(self.legs / 5) % 2 else "run2"]

    def spawn(self):
        start = float(self.p["speed"])
        fast = self.speed > start * 1.35
        if self.score > 250 and random.random() < 0.25:
            level = random.choice(("low", "mid", "high"))
            bottom = {"low": self.GROUND - 1, "mid": self.GROUND - 10, "high": self.GROUND - 16}[level]
            self.obs.append(Thing(self.w + 2, bottom - 6, level, PTERO[0], err=random.gauss(0, 1.5)))
        else:
            big = random.random() < 0.4
            n = random.randint(1, (2 if fast else 1) if big else (3 if fast else 2))
            s = cactus_group(big, n)
            self.obs.append(Thing(self.w + 2, self.GROUND - s[3], "cactus", s, err=random.gauss(0, 1.5)))
        if random.random() < 0.04:  # the demo misjudges a jump now and then
            self.obs[-1].err = random.choice((-16, 14))
        self.gap = self.speed * random.uniform(0.75, 1.5) + self.obs[-1].sprite[2]

    def press(self, key, player):
        if key in ("a", "up") and self.h <= 0 and not self.dying_at:
            self.vy = self.JUMP

    def step(self):
        if self.dying_at:
            if self.now - self.dying_at > 1:
                self.game_over()
            return
        keys = self.keys()
        start = float(self.p["speed"])
        self.speed = min(start * 1.9, start + self.dist * 0.008)
        dx = self.speed * STEP
        self.dist += dx
        self.legs += dx
        self.score = int(self.dist / 4)
        jump = bool(keys & {"a", "up"})
        if self.h <= 0 and jump and self.vy <= 0:
            self.vy = self.JUMP  # holding the button keeps jumping
        if self.h > 0 or self.vy > 0:
            if self.vy > 45 and not jump:
                self.vy = 45  # let go early for a short hop
            self.vy -= self.GRAVITY * (3 if "down" in keys else 1) * STEP
            self.h += self.vy * STEP
            if self.h <= 0:
                self.h, self.vy = 0.0, 0.0
        self.ducking = self.h <= 0 and "down" in keys
        for o in self.obs:
            o.x -= dx + (4 * STEP if o.kind != "cactus" else 0)
            if o.kind != "cactus":
                o.sprite = PTERO[int(self.now * 5) % 2]
        self.obs = [o for o in self.obs if o.x > -20]
        if not self.obs or self.obs[-1].x < self.w - self.gap:
            self.spawn()
        for p in self.pebbles:
            p[0] -= dx
            if p[0] < -2:
                p[0] += self.w + random.uniform(2, 14)
                p[1] = random.choice((39, 40, 41))
        self.cloud[0] -= dx * 0.12
        if self.cloud[0] < -10:
            self.cloud = [self.w + random.uniform(0, 30), random.randint(6, 14)]
        d = self.dino()
        dy = self.GROUND - d[3] - int(self.h)
        for o in self.obs:
            if overlap(d, self.X, dy, o.sprite, o.x, o.y):
                self.dying_at = self.now
                return

    def autoplay(self):
        keys = set()
        if any(o.kind == "mid" and o.x < self.X + 24 and o.x + o.sprite[2] > self.X - 1 for o in self.obs):
            keys.add("down")
        ahead = [o for o in self.obs if o.x + o.sprite[2] / 2 > self.X + 5 and o.kind in ("cactus", "low")]
        if ahead and not keys:
            o = ahead[0]
            centre = o.x + o.sprite[2] / 2 - (self.X + 6)
            if centre + o.err <= self.speed * self.JUMP / self.GRAVITY:
                keys.add("a")
        if self.vy > 0:
            keys.add("a")  # full-height jumps
        self.demo_keys = keys

    def draw(self, c, now):
        fill(c, 0, self.GROUND, c.w, 1)
        for x, y, n in self.pebbles:
            fill(c, x, y, n, 1)
        blit(c, self.cloud[0], self.cloud[1], CLOUD)
        for o in self.obs:
            blit(c, o.x, o.y, o.sprite)
        d = self.dino()
        if not self.dying_at or int(now * 6) % 2:
            blit(c, self.X, self.GROUND - d[3] - int(self.h), d)
        c.text(c.w - SMALL.width(f"{self.score:05d}"), 0, f"{self.score:05d}", SMALL)
        if not self.demo_label(c, 1, 0):
            c.text(1, 0, f"HI {self.best:05d}", SMALL)
        if self.p["night"] and self.score % 700 >= 600 and self.score > 600:
            c.invert()

    def draw_title(self, c, now):
        fill(c, 4, 33, 76, 1)
        blit(c, 20, 33 - 13, DINO["run1" if int(now * 6) % 2 else "run2"])
        blit(c, 52, 33 - 11, cactus_group(True, 1))
        blit(c, 64, 33 - 8, cactus_group(False, 1))


# ---- Road Racer -----------------------------------------------------------------------

def ease_in(a, b, t):
    return a + (b - a) * t * t


def ease_in_out(a, b, t):
    return a + (b - a) * (-math.cos(t * math.pi) / 2 + 0.5)


def build_track(seed=7, length=2400):
    """A looping road: per-segment curvature and height, built from eased sections."""
    rng = random.Random(seed)
    curves, ys = [], []
    y = 0.0

    def section(enter, hold, leave, curve, hill):
        nonlocal y
        start, total = y, enter + hold + leave
        end = start + hill
        for n in range(total):
            if n < enter:
                curves.append(ease_in(0, curve, n / enter))
            elif n < enter + hold:
                curves.append(curve)
            else:
                curves.append(ease_in_out(curve, 0, (n - enter - hold) / leave))
            ys.append(ease_in_out(start, end, n / total))
        y = end

    section(10, 50, 10, 0, 0)
    while len(curves) < length - 200:
        curve = rng.choice((0, 0.004, 0.008, 0.012)) * rng.choice((-1, 1))
        hill = rng.choice((0, 0, 2, 3, 4)) * rng.choice((-1, 1))
        if abs(y + hill) > 6:
            hill = -hill
        section(rng.randint(10, 25), rng.randint(15, 60), rng.randint(10, 25), curve, hill)
    rest = length - len(curves)
    section(rest // 4, rest - 2 * (rest // 4), rest // 4, 0, -y)  # flat run back to the start
    ys.append(ys[0])
    return curves, ys


TRACK = build_track()

# rear views of cars, largest to smallest: lit bodies with dark windows and lights,
# ringed with dark dots ('o') so they stand out on the lit road as well as the grass
PLAYER_ROWS = ["....oooooooooooo....", "...oXXXXXXXXXXXXo...", "..oXXooooooooooXXo..", "..oXooooooooooooXo..",
               "oooXXXXXXXXXXXXXXooo", "oXXXXXXXXXXXXXXXXXXo", "oXooXXXXXXXXXXXXooXo", "oXXXXXXXXXXXXXXXXXXo",
               "oooooXXXXXXXXXXooooo", "oooooooooooooooooooo"]
PLAYER = {
    0: spr(PLAYER_ROWS),
    -1: spr([r[1:] + "." for r in PLAYER_ROWS[:4]] + PLAYER_ROWS[4:]),  # leaning into a turn
    1: spr(["." + r[:-1] for r in PLAYER_ROWS[:4]] + PLAYER_ROWS[4:]),
}
CARS = [spr(r) for r in (
    PLAYER_ROWS[:9],
    ["..oooooooooo..", ".oXXXXXXXXXXo.", ".oXooooooooXo.", "ooXXXXXXXXXXoo", "oXoXXXXXXXXoXo",
     "oXXXXXXXXXXXXo", "ooooXXXXXXoooo"],
    [".oooooooo.", ".oXXXXXXo.", "ooXooooXoo", "oXXXXXXXXo", "oXoXXXXoXo", "oooXXXXooo"],
    ["ooooooo", "oXXXXXo", "oXoooXo", "oXXXXXo", "ooXXXoo"],
    ["ooooo", "oXXXo", "oXoXo", "ooooo"],
    ["ooo", "ooo"],
)]
MOUNTAINS = [int(3 + 2.2 * math.sin(i * math.tau / 56) + 1.6 * math.sin(i * math.tau / 21 + 1)
                 + 0.8 * math.sin(i * math.tau / 12)) for i in range(168)]


@register
class Racer(PackGame):
    id = "racer"
    title = "Road Racer"
    description = ("A or up to accelerate, B or down to brake, left/right to steer. Pass cars and "
                   "reach each checkpoint before the clock runs out. Off the road you slow down.")
    params = [P("time", "number", 40, "Starting time (seconds)", min=15, max=120),
              P("traffic", "number", 20, "Rival cars", min=0, max=40),
              P("top", "number", 40, "Top speed (road segments per second)", min=25, max=60),
              DEMO]
    over_text = "TIME UP"
    HOR = 13           # horizon row on a flat road
    XS, YS = 52.0, 58.0  # half road width and camera height in dots, at one segment away
    CAM = 2.0          # the player's car sits this many segments ahead of the camera
    DRAW = 70          # segments drawn
    RUMBLE = 4         # segments per rumble stripe
    CHECK = 800        # segments between checkpoints
    CENTRIFUGAL = 55
    POSTS = 8          # segments between roadside posts

    def reset(self):
        super().reset()
        self.curves, self.ys = TRACK
        self.n = len(self.curves)
        self.score, self.dist, self.passes = 0, 0.0, 0
        self.pos, self.x, self.speed = 0.0, 0.0, 0.0
        self.time = float(self.p["time"])
        self.extend = 30.0
        self.next_check = self.CHECK
        self.msg, self.msg_until = "", 0
        self.go_at = self.now + 1.5
        self.bump = 0.0
        self.bg = 0.0
        self.lane = 0.5
        self.cars = []
        for i in range(int(self.p["traffic"])):
            z = 25 + i * (self.n - 40) / max(1, int(self.p["traffic"]))
            self.cars.append(Thing(random.choice((-0.5, 0.5)), 0, "car", z=z, speed=random.uniform(16, 28)))
        for car in self.cars:
            car.ahead = self.rel(car.z) > -0.2
        self.steer = 0

    def rel(self, z):
        """Distance from the player's car to track position z, wrapped to -n/2..n/2."""
        return (z - (self.pos + self.CAM) + self.n / 2) % self.n - self.n / 2

    def height(self, z):
        i = int(z) % self.n
        t = z - math.floor(z)
        return self.ys[i] + (self.ys[i + 1] - self.ys[i]) * t

    def step(self):
        keys = self.keys()
        top = float(self.p["top"])
        if self.now < self.go_at:
            return
        self.time -= STEP
        if self.time <= 0:
            self.time = 0
            self.speed = max(0.0, self.speed - 30 * STEP)
            if self.speed == 0:
                return self.game_over()
        elif self.bump > 0:
            self.bump -= STEP
        elif keys & {"a", "up"}:
            self.speed += (12 - 6 * self.speed / top) * STEP
        elif keys & {"b", "down"}:
            self.speed -= 35 * STEP
        else:
            self.speed -= 5 * STEP
        if abs(self.x) > 1.05 and self.speed > 12:
            self.speed -= 40 * STEP  # grass
        pz = self.pos + self.CAM
        if 1.0 < abs(self.x) < 1.6 and int(pz) % self.POSTS == 0 and self.bump <= 0 and self.speed > 6:
            self.speed, self.bump = 3.0, 0.6  # hit a roadside post
        self.speed = min(max(self.speed, 0.0), top)
        pct = self.speed / top
        seg = int(self.pos + self.CAM) % self.n
        self.steer = ("right" in keys) - ("left" in keys)
        self.x += self.steer * 2.2 * STEP * min(1.0, self.speed / 12)
        self.x -= STEP * 2 * pct * pct * self.curves[seg] * self.CENTRIFUGAL
        self.x = min(max(self.x, -1.8), 1.8)
        self.pos = (self.pos + self.speed * STEP) % self.n
        self.dist += self.speed * STEP
        self.bg += self.curves[seg] * self.speed * STEP * 60
        for car in self.cars:
            car.z = (car.z + car.speed * STEP) % self.n
            if random.random() < 0.002:
                car.x = -car.x
            r = self.rel(car.z)
            if -0.3 < r < 0.9 and abs(car.x - self.x) < 0.6 and self.speed > car.speed:
                self.speed = car.speed * 0.5  # rear-ended it
                self.bump = 0.4
                self.pos = (car.z - self.CAM - 0.95) % self.n
                r = 0.05
            if car.ahead and r < -0.2:
                self.passes += 1
            elif not car.ahead and r > 0.5:
                self.passes -= 1  # overtaken again: the pass doesn't count
            car.ahead = r > -0.2
        if self.dist >= self.next_check:
            self.next_check += self.CHECK
            self.time += self.extend
            self.msg, self.msg_until = f"CHECKPOINT +{int(self.extend)}", self.now + 2
            self.extend = max(15.0, self.extend - 2)
        self.score = int(self.dist / 2) + max(self.passes, 0) * 50

    def autoplay(self):
        keys = {"a"}
        lanes = (self.lane, -self.lane)
        blocked = {ln: min((self.rel(car.z) for car in self.cars
                           if abs(car.x - ln) < 0.3 and 0 < self.rel(car.z) < 14), default=99) for ln in lanes}
        if blocked[self.lane] < 10 and blocked[-self.lane] > blocked[self.lane]:
            self.lane = -self.lane
        elif blocked[self.lane] < 3:
            keys = {"b"}
        seg = int(self.pos + self.CAM) % self.n
        err = self.lane - self.x + self.curves[seg] * 8
        if err > 0.08:
            keys.add("right")
        elif err < -0.08:
            keys.add("left")
        self.demo_keys = keys

    def draw(self, c, now):
        n, curves, ys = self.n, self.curves, self.ys
        base = int(self.pos)
        frac = self.pos - base
        cam_y = self.height(self.pos + self.CAM) + 1.0
        cx0 = c.w / 2
        x, dx = 0.0, -curves[base % n] * frac
        maxy = c.h
        proj = [None] * self.DRAW
        px, W = c.px, c.w
        for k in range(self.DRAW):
            i = (base + k) % n
            z1, z2 = k - frac, k + 1 - frac
            curve = curves[i]
            if z2 <= 0.4:
                x += dx
                dx += curve
                continue
            s1, s2 = 1 / max(z1, 0.4), 1 / z2
            X1 = cx0 + (x - self.x) * self.XS * s1
            Y1 = self.HOR + (cam_y - ys[i]) * self.YS * s1
            X2 = cx0 + (x + dx - self.x) * self.XS * s2
            Y2 = self.HOR + (cam_y - ys[i + 1]) * self.YS * s2
            proj[k] = (X1, Y1, self.XS * s1, maxy, z1)
            x += dx
            dx += curve
            if Y2 >= Y1 or Y2 >= maxy:
                continue
            ytop = max(math.ceil(Y2), 0)
            ybot = min(math.ceil(Y1), maxy) - 1
            stripe = (i // self.RUMBLE) % 2
            detail = (Y1 - Y2) * self.RUMBLE >= 2  # stripes only where they're big enough not to shimmer
            W1, W2 = self.XS * s1, self.XS * s2
            for y in range(ybot, ytop - 1, -1):
                t = (y - Y2) / (Y1 - Y2)
                cx = X2 + (X1 - X2) * t
                w = W2 + (W1 - W2) * t
                lft, rgt = int(round(cx - w)), int(round(cx + w))
                hspan(c, lft, rgt, y)
                if detail and stripe:
                    rw = max(1, int(w * 0.12))
                    hspan(c, lft + 1, lft + rw, y, 0)
                    hspan(c, rgt - rw, rgt - 1, y, 0)
                    lw = max(1, int(w * 0.06))
                    hspan(c, int(cx) - lw // 2, int(cx) - lw // 2 + lw - 1, y, 0)
            maxy = ytop
        horizon = maxy
        # mountains on the horizon, sliding sideways through the curves
        off = int(self.bg) % len(MOUNTAINS)
        base_y = self.HOR - 1
        last = None
        for sx in range(W):
            my = base_y - MOUNTAINS[(sx + off) % len(MOUNTAINS)]
            lo, hi = (my, my) if last is None else (min(my, last), max(my, last))
            for yy in range(lo, hi + 1):
                if yy < horizon and (yy == my or yy != last):
                    px[yy * W + sx] = 1
            last = my
        # roadside posts, checkpoint gantries and cars, far to near
        cars = {}
        for car in self.cars:
            r = (car.z - self.pos) % n
            if 1 < r < self.DRAW - 1:
                cars.setdefault(int(r + frac), []).append((r, car))
        for k in range(self.DRAW - 2, 0, -1):
            pr = proj[k]
            if pr is None:
                continue
            X, Y, w, clip, z = pr
            i = (base + k) % n
            if i % self.POSTS == 0:
                hgt = 0.6 * self.YS / max(z, 0.4)
                if hgt >= 2:
                    pw = 2 if w > 14 else 1
                    for side in (-1.3, 1.3):
                        sx = int(X + side * w)
                        for yy in range(max(int(Y - hgt), 0), min(int(Y), clip)):
                            hspan(c, sx, sx + pw - 1, yy)
            if i % self.CHECK == 0:
                self.draw_gantry(c, X, Y, w, clip, z)
            nxt = proj[k + 1]
            for r, car in cars.get(k, ()):
                t = r + frac - k
                if nxt:
                    cxp = X + (nxt[0] - X) * t
                    cyp = Y + (nxt[1] - Y) * t
                    cw = w + (nxt[2] - w) * t
                else:
                    cxp, cyp, cw = X, Y, w
                size = 0.7 * cw
                s = next((s for s in CARS if s[2] <= size + 1.5), None)
                if s is not None and size >= 1.5:
                    blit(c, cxp + car.x * cw - s[2] / 2, cyp - s[3], s, clip)
        # the player's car
        jig = 1 if (abs(self.x) > 1.05 and self.speed > 3 and int(now * 15) % 2) or self.bump > 0.2 else 0
        blit(c, c.w // 2 - 10, c.h - 9 - jig, PLAYER[self.steer if self.speed > 2 else 0])
        # HUD
        fill(c, 0, 0, c.w, 6, 0)
        if not self.demo_label(c, 1, 0):
            c.text(0, 0, f"TIME {int(math.ceil(self.time))}", SMALL)
        c.text_center(0, str(self.score), SMALL)
        kmh = f"{int(self.speed * 6)}KMH"
        c.text(c.w - SMALL.width(kmh), 0, kmh, SMALL)
        if now < self.go_at:
            label(c, 0, 18, "READY" if now < self.go_at - 0.5 else "GO!", "center")
        elif now < self.msg_until and int(now * 4) % 4:
            label(c, 0, 18, self.msg, "center")
        elif self.time <= 0:
            label(c, 0, 18, "TIME UP", "center")
        elif self.time < 6 and int(now * 2) % 2:
            label(c, 0, 18, str(int(math.ceil(self.time))), "center")

    def draw_gantry(self, c, X, Y, w, clip, z):
        hgt = 1.2 * self.YS / max(z, 0.4)
        if hgt < 3:
            return
        lft, rgt = int(X - 1.15 * w), int(X + 1.15 * w)
        top = int(Y - hgt)
        bar = max(1, int(0.25 * self.YS / max(z, 0.4)))
        for yy in range(max(top, 0), min(int(Y), clip)):
            hspan(c, lft, lft, yy)
            hspan(c, rgt, rgt, yy)
        for yy in range(max(top, 0), min(top + bar, clip)):
            hspan(c, lft, rgt, yy)
        if bar >= 6 and rgt - lft > 26:
            c.text_center(top + (bar - 5) // 2, "CHECK", SMALL, v=0, x0=lft, w=rgt - lft + 1)

    def draw_title(self, c, now):
        for y in range(18, 34):
            t = (y - 18) / 16
            lft, rgt = int(42 - 4 - 30 * t), int(42 + 4 + 30 * t)
            c.set(lft, y)
            c.set(rgt, y)
            if (y + int(now * 8)) % 6 < 3:
                c.set(42, y)
        blit(c, 42 - 10 + int(math.sin(now * 2) * 6), 25, PLAYER[0])


# ---- Cave Copter ----------------------------------------------------------------------

COPTER_BODY = ["......X....", "X....XXXX..", "XXXXXXX.XX.", "X....XXXXX.", ".....X..X..", "....XXXXXX."]
COPTER = (spr(["XXXXXXXXXXX"] + COPTER_BODY), spr(["....XXX...."] + COPTER_BODY))


@register
class Copter(PackGame):
    id = "copter"
    title = "Cave Copter"
    description = "Hold A or up to climb, let go to sink. Fly as far as you can; the cave keeps narrowing."
    params = [P("speed", "number", 28, "Starting speed (dots per second)", min=15, max=50), DEMO]
    X = 12

    def reset(self):
        super().reset()
        self.score, self.dist = 0, 0
        self.y, self.vy = 17.0, 0.0
        self.mid, self.target, self.gap = 21.0, 21.0, 34.0
        self.cols = [(4, 38)] * 30
        while len(self.cols) <= self.w:
            self.cols.append(self.new_col())
        self.blocks = []  # [x, y, h]
        self.next_block = 70
        self.scroll = 0.0
        self.dying_at = 0

    def new_col(self):
        self.gap += (max(13.0, 34 - self.dist / 110) - self.gap) * 0.05
        lo, hi = self.gap / 2 + 1, self.h - self.gap / 2 - 1
        if abs(self.mid - self.target) < 0.5:
            self.target = random.uniform(lo, hi)
        self.mid += max(-0.45, min(0.45, self.target - self.mid))
        self.mid = min(max(self.mid, lo), hi)
        return int(self.mid - self.gap / 2), int(self.mid + self.gap / 2)

    def speed(self):
        return float(self.p["speed"]) * (1 + min(self.dist / 3000, 0.6))

    def step(self):
        if self.dying_at:
            if self.now - self.dying_at > 0.9:
                self.game_over()
            return
        up = bool(self.keys() & {"a", "up"})
        self.vy = min(max(self.vy + (-140 if up else 110) * STEP, -42), 46)
        self.y += self.vy * STEP
        self.scroll += self.speed() * STEP
        while self.scroll >= 1:
            self.scroll -= 1
            self.dist += 1
            self.cols.pop(0)
            top, bottom = self.new_col()
            self.cols.append((top, bottom))
            for b in self.blocks:
                b[0] -= 1
            self.blocks = [b for b in self.blocks if b[0] > -4]
            if self.dist >= self.next_block:
                self.next_block = self.dist + random.randint(45, 90) - min(self.dist // 200, 20)
                bh = int(min(8, self.gap * 0.25))
                # leave at least one side wide enough to fly through
                by = random.choice((random.randint(top + 2, top + 5), random.randint(bottom - 5 - bh, bottom - 2 - bh)))
                if bottom - top - bh >= 14:
                    by = random.randint(top + 2, bottom - 2 - bh)
                if max(by - top, bottom - by - bh) >= 11:
                    self.blocks.append([self.w, by, bh])
        self.score = self.dist // 2
        s = COPTER[0]
        y = int(self.y)
        for dx, dy in s[0]:
            x, yy = self.X + dx, y + dy
            top, bottom = self.cols[x]
            if yy < top or yy >= bottom or any(b[0] <= x < b[0] + 3 and b[1] <= yy < b[1] + b[2] for b in self.blocks):
                self.dying_at = self.now
                return

    def autoplay(self):
        span = self.cols[self.X:self.X + 24]
        top, bottom = max(t for t, _ in span), min(b for _, b in span)
        target = (top + bottom) / 2
        for bx, by, bh in self.blocks:
            if self.X - 3 < bx < self.X + 34:
                above, below = by - top, bottom - (by + bh)
                target = (top + by) / 2 if above > below else (by + bh + bottom) / 2
                break
        self.demo_keys = {"a"} if self.y + 3.5 + self.vy * 0.3 > target else set()

    def draw(self, c, now):
        W, px = c.w, c.px
        for x in range(W):
            top, bottom = self.cols[x]
            if top > 0:
                px[x:x + top * W:W] = ONES[:top]
            if bottom < c.h:
                px[bottom * W + x::W] = ONES[:c.h - bottom]
        for bx, by, bh in self.blocks:
            fill(c, bx, by, 3, bh)
        if not self.dying_at or int(now * 8) % 2:
            blit(c, self.X, self.y, COPTER[int(now * 10) % 2])
        label(c, 1, 1, str(self.score))
        self.demo_label(c, c.w - 2, 1, "right")

    def draw_title(self, c, now):
        for x in range(0, c.w, 1):
            t = 19 + int(2 * math.sin((x + now * 20) / 9))
            c.set(x, t)
            c.set(x, t + 14)
        blit(c, 30, 22 + int(math.sin(now * 3) * 2), COPTER[int(now * 10) % 2])


# ---- Stacker --------------------------------------------------------------------------

@register
class Stacker(PackGame):
    id = "stacker"
    title = "Stacker"
    description = ("A stops the moving row; blocks hanging past the row below fall off. "
                   "At the minor prize line, A keeps going for the major prize and B takes the minor one.")
    params = [P("speed", "number", 6, "Starting speed (steps per second)", min=2, max=15), DEMO]
    COLS, ROWS = 7, 14
    MINOR = 10
    X0 = 6

    def reset(self):
        super().reset()
        self.score, self.row, self.width = 0, 0, 3
        self.stack = []
        self.falling = []  # [col, row (float), speed]
        self.start_row()

    def start_row(self):
        self.width = min(self.width, 3 if self.row < 4 else 2 if self.row < 9 else 1)
        self.dir = random.choice((1, -1))
        self.col = 0 if self.dir > 0 else self.COLS - self.width
        self.move_t = 0.0
        self.phase, self.phase_t = "move", 0.0
        good = self.good_cols()
        aim = random.choice(good)
        if random.random() < 0.03 + 0.025 * self.row:  # the demo slips now and then
            aim = min(max(aim + random.choice((-1, 1)), 0), self.COLS - self.width)
        self.aim = aim

    def good_cols(self):
        if not self.stack:
            return list(range(1, self.COLS - self.width))
        below = self.stack[-1]
        return [x for x in range(self.COLS - self.width + 1) if all(x + i in below for i in range(self.width))] or [min(below)]

    def press(self, key, player):
        if self.phase == "move" and key in ("a", "up", "b"):
            self.place()
        elif self.phase == "prize" and key in ("a", "up"):
            self.start_row()
        elif self.phase == "prize" and key == "b":
            self.score += 250
            self.over_text = "MINOR PRIZE"
            self.phase, self.phase_t = "done", 0.0

    def place(self):
        cells = set(range(self.col, self.col + self.width))
        kept = cells & self.stack[-1] if self.stack else cells
        for x in cells - kept:
            self.falling.append([x, float(self.row), 0.0])
        self.phase_t = 0.0
        if not kept:
            self.phase = "lost"
            return
        self.stack.append(kept)
        self.width = len(kept)
        self.row += 1
        self.score = self.row * 10
        if self.row == self.ROWS:
            self.score += 1000
            self.over_text = "MAJOR PRIZE!"
            self.phase = "done"
        elif self.row == self.MINOR:
            self.phase = "prize"
        else:
            self.phase = "settle"

    def step(self):
        self.phase_t += STEP
        for f in self.falling:
            f[2] += 25 * STEP
            f[1] -= f[2] * STEP
        self.falling = [f for f in self.falling if f[1] > -2]
        if self.phase == "move":
            self.move_t += STEP
            rate = float(self.p["speed"]) * (1 + 0.14 * self.row)
            if self.move_t >= 1 / rate:
                self.move_t -= 1 / rate
                self.col += self.dir
                if self.col < 0 or self.col + self.width > self.COLS:
                    self.dir = -self.dir
                    self.col += 2 * self.dir
        elif self.phase == "settle" and self.phase_t > 0.35:
            self.start_row()
        elif self.phase == "lost" and self.phase_t > 1.5:
            self.game_over()
        elif self.phase == "done" and self.phase_t > 2.5:
            self.game_over()

    def autoplay(self):
        if self.phase == "move" and self.phase_t > 0.3 and self.col == self.aim and self.move_t < 0.05:
            self.press("a", 1)
        elif self.phase == "prize" and self.phase_t > 1.5:
            self.press("a", 1)

    def cell(self, c, col, row):
        fill(c, self.X0 + col * 5, c.h - 3 * (int(round(row)) + 1), 4, 2)

    def draw(self, c, now):
        right = self.X0 + self.COLS * 5
        fill(c, self.X0 - 2, 0, 1, c.h)
        fill(c, right, 0, 1, c.h)
        my = c.h - 3 * self.MINOR - 1
        for x in range(self.X0, right - 1, 2):
            c.set(x, my)
        for r, cells in enumerate(self.stack):
            for col in cells:
                self.cell(c, col, r)
        if self.phase == "move":
            for i in range(self.width):
                self.cell(c, self.col + i, self.row)
        for col, row, _ in self.falling:
            if int(now * 10) % 2:
                self.cell(c, col, row)
        px = right + 4
        c.text(px, 0, "< MAJOR", SMALL)
        c.text(px, my - 2, "< MINOR", SMALL)
        if self.phase == "prize":
            if int(now * 2) % 2:
                c.text(px, 18, "MINOR!", SMALL)
            c.text(px, 26, "A: GO ON", SMALL)
            c.text(px, 33, "B: TAKE IT", SMALL)
        else:
            if not self.demo_label(c, px, 16):
                c.text(px, 16, f"BEST {self.best}", SMALL)
            c.text(px, 26, "SCORE", SMALL)
            c.text(px, 33, str(self.score), SMALL)

    def draw_title(self, c, now):
        n = int(now * 6) % 12
        x = n if n < 6 else 12 - n
        for i in range(3):
            fill(c, 20 + (x + i) * 5, 22, 4, 2)
        for i in range(3):
            fill(c, 30 + i * 5, 25, 4, 2)
        for i in range(4):
            fill(c, 25 + i * 5, 28, 4, 2)


# ---- Sky Jump -------------------------------------------------------------------------

JUMPER = spr(["..XXX..", ".XXXXX.", "XX.X.XX", "XXXXXXX", ".XXXXX.", ".X...X.", "XX...XX"])
JUMPER_UP = spr(["..XXX..", ".XXXXX.", "XX.X.XX", "XXXXXXX", ".XXXXX.", ".X...X.", ".X...X."])
PLATFORMS = {
    "n": spr(["XXXXXXXXX", ".XXXXXXX."]),
    "m": spr(["XXXXXXXXX", "X.X.X.X.X"]),
    "b": spr(["XXX.X.XXX", ".X.X.X.X."]),
    "s": spr(["XXXXXXXXX", ".XXXXXXX."]),
}
SPRING = (spr(["XXX", ".X.", "XXX"]), spr(["XXX", ".X.", ".X.", ".X.", "XXX"]))
HALF = spr(["XXXX", ".X.X"])


@register
class SkyJump(PackGame):
    id = "skyjump"
    title = "Sky Jump"
    description = ("Left/right to steer; the sides wrap around. Bounce ever higher: some platforms move, "
                   "cracked ones break, springs launch you. Don't fall off the bottom.")
    params = [P("difficulty", "select", "normal", "Difficulty", options=[["easy", "Easy"], ["normal", "Normal"], ["hard", "Hard"]]),
              DEMO]
    GRAVITY, BOUNCE, SPRING_V = 150, 82, 128
    FOLLOW = 13  # the camera keeps the jumper at least this far below the top

    def reset(self):
        super().reset()
        self.score = 0
        self.x, self.y = 38.0, 29.0
        self.vx, self.vy = 0.0, -self.BOUNCE
        self.facing = 1
        self.cam = 0.0
        self.start = self.y
        self.plats = [Thing(36, 36, "n")]
        self.top = 36.0
        self.dying_at = 0
        self.sprung = {}
        self.slip = False
        self.fill_platforms()

    def hard(self):
        return {"easy": 0.75, "normal": 1.0, "hard": 1.3}.get(self.p["difficulty"], 1.0)

    def add(self, y, kind):
        p = Thing(random.uniform(0, self.w - 9), y, kind)
        p.vx = random.choice((-1, 1)) * random.uniform(10, 18 + 4 * self.hard()) if kind == "m" else 0
        self.plats.append(p)

    def fill_platforms(self):
        while self.top > self.cam - 10:
            height = (self.start - self.top) * self.hard()
            gap = random.uniform(5, min(18, 8 + height / 120))
            self.top -= gap
            r = random.random()
            moving = min(0.35, height / 2500)
            kind = "m" if r < moving else "s" if r < moving + 0.06 else "n"
            self.add(self.top, kind)
            if random.random() < min(0.3, 0.05 + height / 3000):
                self.add(self.top + gap / 2, "b")  # decoy: breaks under you

    def step(self):
        if self.dying_at:
            self.vy += self.GRAVITY * STEP
            self.y += self.vy * STEP
            if self.now - self.dying_at > 0.8:
                self.game_over()
            return
        keys = self.keys()
        d = ("right" in keys) - ("left" in keys)
        if d:
            self.facing = d
            self.vx = max(-44.0, min(44.0, self.vx + d * 280 * STEP))
        else:
            self.vx -= math.copysign(min(abs(self.vx), 200 * STEP), self.vx)
        self.x = (self.x + self.vx * STEP) % self.w
        feet = self.y + 7
        self.vy += self.GRAVITY * STEP
        self.y += self.vy * STEP
        if self.vy > 0:
            for p in self.plats:
                if p.kind == "x" or not (feet <= p.y + 0.5 and self.y + 7 >= p.y):
                    continue
                dx = (self.x + 3.5 - (p.x + 4.5) + self.w / 2) % self.w - self.w / 2
                if abs(dx) < 6.5:
                    if p.kind == "b":
                        p.kind, p.vx = "x", 0.0
                        p.vy = 0.0
                        continue
                    self.y = p.y - 7
                    if p.kind == "s" and abs(self.x + 3.5 - (p.x + 4.5)) < 4.5:
                        self.vy = -self.SPRING_V
                        self.sprung[id(p)] = self.now
                    else:
                        self.vy = -self.BOUNCE
                    break
        for p in self.plats:
            if p.kind == "m":
                p.x += p.vx * STEP
                if p.x < 0 or p.x > self.w - 9:
                    p.vx = -p.vx
                    p.x = min(max(p.x, 0), self.w - 9)
            elif p.kind == "x":
                p.vy += self.GRAVITY * STEP
                p.y += p.vy * STEP
        if self.y - self.cam < self.FOLLOW:
            self.cam = self.y - self.FOLLOW
        self.plats = [p for p in self.plats if p.y < self.cam + self.h + 4]
        self.fill_platforms()
        self.score = max(self.score, int((self.start - self.y) / 2))
        if self.y > self.cam + self.h:
            self.dying_at = self.now

    def autoplay(self):
        feet, g = self.y + 7, self.GRAVITY
        best = None
        for p in self.plats:
            if p.kind in "bx":
                continue
            disc = self.vy * self.vy + 2 * g * (p.y - feet)
            if disc < 0:
                continue  # above the top of this jump
            t = (-self.vy + math.sqrt(disc)) / g  # until the feet come down to its top
            if t < 0.05:
                continue
            dx = ((p.x + 4.5) - (self.x + 3.5) + self.w / 2) % self.w - self.w / 2
            if abs(dx) < 40 * t + 2 and (best is None or p.y < best[0].y):
                best = (p, dx, t)  # the highest platform it can still land on
        if self.vy < -self.BOUNCE + 4:  # just bounced: now and then the demo misjudges a jump
            self.slip = random.random() < 0.03
        keys = set()
        if self.slip:
            keys.add("left")
        elif best is not None:
            p, dx, t = best
            want = max(-44.0, min(44.0, dx / max(t, 0.1)))
            if want - self.vx > 4:
                keys.add("right")
            elif want - self.vx < -4:
                keys.add("left")
        self.demo_keys = keys

    def draw(self, c, now):
        cam = int(self.cam)
        for p in self.plats:
            y = int(p.y) - cam
            if -5 < y < c.h:
                if p.kind == "x":
                    t = max(0.0, p.vy) * 0.08
                    blit(c, p.x - t, y, HALF)
                    blit(c, p.x + 5 + t, y, HALF)
                    continue
                blit(c, p.x, y, PLATFORMS[p.kind])
                if p.kind == "s":
                    s = SPRING[1 if self.now - self.sprung.get(id(p), -9) < 0.3 else 0]
                    blit(c, p.x + 3, y - s[3], s)
        s = JUMPER_UP if self.vy < -20 else JUMPER
        y = int(self.y) - cam
        for x in (self.x, self.x - c.w):
            blit(c, x, y, s)
        label(c, 1, 1, str(self.score))
        self.demo_label(c, c.w - 2, 1, "right")

    def draw_title(self, c, now):
        blit(c, 18, 30, PLATFORMS["n"])
        blit(c, 56, 24, PLATFORMS["m"])
        t = (now * 1.2) % 1
        blit(c, 19 + 38 * t, 23 - 4 * math.sin(t * math.pi) - 6 * t, JUMPER)


# ---- Pitfall Run ----------------------------------------------------------------------

HARRY = {
    "stand": [".XX..", ".XX..", "..X..", ".XXX.", "X.X.X", "..X..", ".X.X.", ".X.X."],
    "run1": [".XX..", ".XX..", "..X..", ".XXXX", "X.X..", "..XX.", ".X..X", "X...."],
    "run2": [".XX..", ".XX..", "..X..", ".XXX.", "..XX.", "..X..", "..X..", "..XX."],
    "jump": [".XX..", ".XX..", "X.X.X", ".XXX.", "..X..", ".XXX.", "X...X", "....."],
    "hang": ["X...X", "XXXX.", ".XX..", "..X..", ".XXX.", "..X..", ".X.X.", "X...X"],
}
HARRY_R = {k: spr(v) for k, v in HARRY.items()}
HARRY_L = {k: spr(mirror(v)) for k, v in HARRY.items()}
LOG = (spr([".XX.", "X..X", "X..X", ".XX."]), spr([".XX.", "XX.X", "X.XX", ".XX."]))
TREASURE = {
    "money": (spr(["..X..", ".XXX.", "XX.XX", "XXXXX", ".XXX."]), 2000),
    "gold": (spr([".XXXX.", "XXXXXX", "XXXXXX"]), 3000),
    "ring": (spr(["..X..", ".X.X.", "..X..", ".XXX.", "X...X", ".XXX."]), 5000),
}


@register
class Pitfall(PackGame):
    id = "pitfall"
    title = "Pitfall Run"
    description = ("Left/right to run, A or up to jump. Jump onto a swinging vine to grab it and press A to "
                   "let go. Clear pits and logs and grab treasures before the clock runs out.")
    params = [P("minutes", "number", 5, "Time limit (minutes)", min=1, max=20),
              P("lives", "number", 3, "Lives", min=1, max=9),
              P("seed", "number", 1982, "Jungle seed (same seed, same rooms)", min=0, max=99999),
              DEMO]
    GROUND = 32  # surface row; Harry stands on it
    VINE_TOP, VINE_LEN, SWING, PERIOD = 7, 18, 1.0, 2.6
    RUN, JUMP, GRAVITY = 26, 58, 220

    def reset(self):
        super().reset()
        self.score = 2000
        self.lives = int(self.p["lives"])
        self.time = float(self.p["minutes"]) * 60
        self.collected = set()
        self.t = 0.0
        self.number = 0
        self.room = self.make_room(0)
        self.spawn()

    def spawn(self):
        self.x, self.feet, self.vy, self.jvx = 4.0, float(self.GROUND), 0.0, 0.0
        self.mode = "ground"  # ground | air | vine | dying
        self.facing = 1
        self.legs = 0.0
        self.hit_until = self.stumble_until = self.let_go_at = 0.0
        self.mode_t = 0.0

    def make_room(self, n):
        rng = random.Random(int(self.p["seed"]) * 7919 + n)
        kind = "logs" if n == 0 else rng.choice(("logs", "hole", "holes", "swamp", "swamp", "logs"))
        room = {"kind": kind, "holes": [], "swamp": None, "logs": [], "treasure": None}
        if kind == "hole":
            room["holes"] = [(36, 46)]
        elif kind == "holes":
            room["holes"] = [(20, 27), (39, 46), (58, 65)]
        elif kind == "swamp":
            room["swamp"] = (26, 58)
        if kind == "logs" or (kind == "hole" and rng.random() < 0.5):
            rolling = n != 0 and rng.random() < 0.6
            for i in range(rng.randint(1, 3) if kind == "logs" else 1):
                room["logs"].append([60.0 - i * 14 if kind == "logs" else 70.0, rolling])
        if n != 0 and n not in self.collected and rng.random() < 0.4:
            room["treasure"] = (74, rng.choice(list(TREASURE)))
        pits = room["holes"] + ([room["swamp"]] if room["swamp"] else [])
        spots = [x for x in range(4, 78, 6) if not any(a - 4 <= x <= b + 1 for a, b in pits)]
        room["trees"] = sorted(rng.sample(spots, min(3, len(spots))))
        room["canopy"] = [rng.randint(0, 3) for _ in range(15)]
        return room

    def vine_end(self, t=None):
        a = self.SWING * math.sin((self.t if t is None else t) * math.tau / self.PERIOD)
        pivot = (self.room["swamp"][0] + self.room["swamp"][1]) / 2
        return pivot + self.VINE_LEN * math.sin(a), self.VINE_TOP + self.VINE_LEN * math.cos(a)

    def vine_speed(self, t=None):
        t = self.t if t is None else t
        a = self.SWING * math.sin(t * math.tau / self.PERIOD)
        da = self.SWING * math.tau / self.PERIOD * math.cos(t * math.tau / self.PERIOD)
        return self.VINE_LEN * math.cos(a) * da

    def grabs(self, x, feet, t):
        ex, ey = self.vine_end(t)
        return abs(ex - (x + 2.5)) < 4 and abs(ey - (feet - 8)) < 5

    def over_pit(self, x):
        cx = x + 2.5
        sw = self.room["swamp"]
        return any(a <= cx < b for a, b in self.room["holes"]) or (sw and sw[0] <= cx < sw[1])

    def press(self, key, player):
        if key in ("a", "up"):
            if self.mode == "ground" and self.now >= self.stumble_until:
                self.mode, self.vy = "air", -self.JUMP
                self.jvx = self.facing * self.RUN if self.keys() & {"left", "right"} else 0.0
            elif self.mode == "vine":
                self.mode, self.vy = "air", -25.0
                self.jvx = max(-40.0, min(40.0, self.vine_speed()))
                self.let_go_at = self.now

    def die(self):
        self.mode, self.mode_t = "dying", 0.0

    def step(self):
        self.t += STEP
        self.mode_t += STEP
        self.time -= STEP
        if self.time <= 0:
            self.time = 0
            self.over_text = "TIME UP"
            return self.game_over()
        for log in self.room["logs"]:
            if log[1]:
                log[0] -= 14 * STEP
                if log[0] < -4:
                    log[0] += self.w + 4
        if self.mode == "dying":
            if self.mode_t > 1.2:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.spawn()
            return
        keys = self.keys()
        d = ("right" in keys) - ("left" in keys)
        if self.mode == "ground":
            if d:
                self.facing = d
            if self.now >= self.stumble_until:
                self.x += d * self.RUN * STEP
                self.legs += abs(d) * self.RUN * STEP
            if self.over_pit(self.x):
                self.mode, self.vy, self.jvx = "air", 0.0, 0.0
        elif self.mode == "air":
            self.vy += self.GRAVITY * STEP
            self.feet += self.vy * STEP
            self.x += self.jvx * STEP
            if self.room["swamp"] and self.now - self.let_go_at > 0.4:
                if self.grabs(self.x, self.feet, self.t):
                    self.mode = "vine"
            if self.mode == "air" and self.feet >= self.GROUND and self.feet - self.vy * STEP <= self.GROUND + 0.5 \
                    and not self.over_pit(self.x):
                self.feet, self.mode = float(self.GROUND), "ground"
            elif self.feet > self.h + 8:
                return self.die()
        if self.mode == "vine":
            ex, ey = self.vine_end()
            self.x, self.feet = ex - 2.5, ey + 8
            self.facing = 1 if self.vine_speed() > 0 else -1
        if self.mode == "air" and self.feet > self.GROUND + 2:
            self.x = min(max(self.x, -2), self.w - 3)  # no flipping screens while falling into a pit
        if self.x < -3:
            self.flip(-1)
        elif self.x > self.w - 2:
            self.flip(1)
        harry = (self.x, self.feet - 8)
        for log in self.room["logs"]:
            if log[0] < harry[0] + 4 and harry[0] + 1 < log[0] + 4 and self.feet > self.GROUND - 4:
                if self.now >= self.hit_until:
                    self.score = max(0, self.score - 100)
                    self.hit_until = self.now + 1
                    self.stumble_until = self.now + 0.3
        tr = self.room["treasure"]
        if tr and tr[0] - 4 < self.x < tr[0] + 5 and self.feet > self.GROUND - 8:
            self.score += TREASURE[tr[1]][1]
            self.collected.add(self.number)
            self.room["treasure"] = None

    def flip(self, d):
        self.number += d
        self.room = self.make_room(self.number)
        self.x = -2.0 if d > 0 else self.w - 3.0
        if self.mode == "vine":
            self.mode = "air"

    def jump_grabs(self, jvx):
        """Would a jump right now catch the vine?"""
        for k in range(1, 26):
            tau = k * STEP
            feet = self.GROUND - self.JUMP * tau + self.GRAVITY * tau * tau / 2
            if feet >= self.GROUND:
                return False
            if self.grabs(self.x + jvx * tau, feet, self.t + tau):
                return True
        return False

    def release_lands(self):
        """Would letting go of the vine now land past the far bank?"""
        vx = max(-40.0, min(40.0, self.vine_speed()))
        for k in range(1, 40):
            tau = k * STEP
            feet = self.feet - 25 * tau + self.GRAVITY * tau * tau / 2
            if feet >= self.GROUND:
                cx = self.x + vx * tau + 2.5
                return self.room["swamp"][1] + 1 < cx < self.w - 4
        return False

    def autoplay(self):
        r = self.room
        keys = {"right"}
        cx = self.x + 2.5
        if self.mode == "ground":
            jump = False
            for a, b in r["holes"]:
                if a - 3.5 <= cx < a:
                    jump = True
            for log in r["logs"]:
                gap = log[0] - (self.x + 5)
                if 0 <= gap < (6 if log[1] else 2):
                    jump = True
            sw = r["swamp"]
            if sw and cx < sw[0] and cx >= sw[0] - 4:
                keys = set()  # wait at the edge for the vine
                if self.jump_grabs(self.RUN):
                    keys, jump = {"right"}, True
                elif self.jump_grabs(0):
                    jump = True
            self.demo_keys = keys
            if jump and random.random() > 0.01:
                self.press("a", 1)
        else:
            self.demo_keys = set()
            if self.mode == "vine" and self.release_lands():
                self.press("a", 1)

    def draw(self, c, now):
        r = self.room
        g = self.GROUND
        # canopy and trunks
        for x in range(c.w):
            c.set(x, 6 + r["canopy"][(x // 6) % 15] * (1 if x % 6 in (2, 3) else 0) + (1 if x % 6 in (1, 4) else 0))
        for t in r["trees"]:  # dotted bark, so Harry stays readable in front of a trunk
            for y in range(9, g):
                c.set(t + (y % 2) * 2, y)
        # ground with pits
        pits = list(r["holes"]) + ([r["swamp"]] if r["swamp"] else [])
        fill(c, 0, g, c.w, 1)
        for x in range(1, c.w, 5):
            c.set(x, g + 3)
        fill(c, 0, c.h - 1, c.w, 1)
        for a, b in pits:
            fill(c, a, g, b - a, 4, 0)
            fill(c, a - 1, g, 1, c.h - g)
            fill(c, b, g, 1, c.h - g)
            fill(c, a, c.h - 1, b - a, 1, 0)
            fill(c, a, c.h - 2, b - a, 1)
        if r["swamp"]:
            a, b = r["swamp"]
            off = int(now * 3) % 4
            for x in range(a, b):
                c.set(x, g + 4 + (1 if (x + off) % 4 == 0 else 0))
            ex, ey = self.vine_end()
            c.line((a + b) / 2, self.VINE_TOP, ex, ey)
        for log in r["logs"]:
            blit(c, log[0], g - 4, LOG[int(log[0] / 3) % 2 if log[1] else 0])
        if r["treasure"]:
            s = TREASURE[r["treasure"][1]][0]
            blit(c, r["treasure"][0], g - s[3], s)
        frames = HARRY_R if self.facing > 0 else HARRY_L
        if self.mode == "vine":
            f = frames["hang"]
        elif self.mode == "air":
            f = frames["jump"]
        elif self.keys() & {"left", "right"}:
            f = frames["run1" if int(self.legs / 4) % 2 else "run2"]
        else:
            f = frames["stand"]
        if self.mode != "dying" or int(now * 8) % 2:
            if self.now >= self.hit_until or int(now * 10) % 2:
                blit(c, self.x, self.feet - 8, f)
        # HUD
        fill(c, 0, 0, c.w, 6, 0)
        c.text(0, 0, str(self.score), SMALL)
        if not self.demo_label(c, 0, 0, "center"):
            for i in range(min(self.lives, 9)):
                fill(c, 36 + i * 3, 1, 1, 4)
        m, s = divmod(int(math.ceil(self.time)), 60)
        tm = f"{m}:{s:02d}"
        c.text(c.w - SMALL.width(tm), 0, tm, SMALL)

    def draw_title(self, c, now):
        fill(c, 4, 33, 28, 1)
        fill(c, 56, 33, 24, 1)
        a = self.SWING * math.sin(now * math.tau / self.PERIOD)
        ex, ey = 44 + 10 * math.sin(a), 16 + 9 * math.cos(a)
        c.line(44, 16, ex, ey)
        blit(c, ex - 2.5, ey, HARRY_R["hang"])
