"""Nature and automata (apps/pack_nature.py): an aquarium you can feed, an ant colony, flocking boids, migrating
sand dunes, reaction-diffusion (Turing) patterns, Wolfram's one-dimensional automata, Langton's ants and Voronoi
cells growing until they meet. All generative, all cheap, all with React to music (apps/react.py), all slower in
quiet hours."""

import math
import random

from canvas import Canvas

from .base import App, P, register
from .react import quiet_now, reactive

BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def _quiet(app):
    try:
        return quiet_now(app.ctx.store)
    except Exception:
        return False


def _bits(rows):
    """A small sprite from strings ('#' = dot) -> (w, h, set of (x, y))."""
    dots = {(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "#"}
    return len(rows[0]), len(rows), dots


def _blit(c, sprite, x, y, flip=False, v=1):
    w, h, dots = sprite
    for dx, dy in dots:
        c.set(x + (w - 1 - dx if flip else dx), y + dy, v)


def _clear(c, sprite, x, y, flip=False):
    """A one-dot black halo around a sprite so it stays readable over what's behind it."""
    w, h, dots = sprite
    for dx, dy in dots:
        px = x + (w - 1 - dx if flip else dx)
        for ex in (-1, 0, 1):
            for ey in (-1, 0, 1):
                c.set(px + ex, y + dy + ey, 0)


# ---- 1. Aquarium ------------------------------------------------------------------------------------------------

FISH = {  # sprite facing right, speed, lives on the bottom
    "neon": (_bits(["  ####", "######", "  ####"]), 1.7, 0.0),
    "guppy": (_bits(["    #####  ", "#  ########", "## #########", "#  ######## ", "    #####   "]), 1.1, 0.0),
    "angel": (_bits(["      ##  ", "   ###### ", "  ########", "##########", "##########", "  ########", "   ###### ", "      ##  "]), 0.7, 0.0),
    "puffer": (_bits(["  # ### #  ", "  #######  ", "# ####### #", "########## ", "# ####### #", "  #######  ", "  # ### #  "]), 0.5, 0.0),
    "catfish": (_bits(["#   #########  ", " #  ###########", "  ###########  ", " #  ##  ##  ## "]), 0.6, 1.0),
}
CASTLE = _bits(["#  #  #  #", "##########", " ######## ", " ##    ## ", " ##  # ## ", " ## ## ## ", " ######## "])


@register
@reactive
class Aquarium(App):
    id = "aquarium"
    title = "Aquarium"
    category = "Generative"
    description = ("Fish of several kinds swim about, turn, and go for the food you drop (any controller's A); "
                   "bubbles rise, the plants sway, a castle sits on the sand.")
    interactive = True
    params = [
        P("fish", "number", 8, "Fish", min=2, max=20),
        P("plants", "number", 4, "Plants", min=0, max=8),
        P("bubbles", "checkbox", True, "Bubbles"),
        P("castle", "checkbox", True, "A castle"),
        P("feed_every", "number", 0, "Feed by itself every (s; 0 = only when you press A)", min=0, max=600),
    ]
    interval = 0.08

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.floor = self.h - 2
        self.fish = [self.new_fish(i) for i in range(int(self.p["fish"]))]
        self.plants = [(self.rng.uniform(4, self.w - 12), self.rng.randint(8, 20), self.rng.uniform(0, 6.3))
                       for _ in range(int(self.p["plants"]))]
        self.bubbles = []
        self.food = []
        self.last_t = None
        self.fed_at = 0.0
        self.eaten = 0
        self.castle_x = self.w - 14

    def new_fish(self, i):
        kind = self.rng.choice(["neon", "neon", "guppy", "guppy", "angel", "puffer", "catfish"])
        sprite, speed, bottom = FISH[kind]
        y = self.floor - sprite[1] if bottom else self.rng.uniform(2, self.floor - sprite[1] - 4)
        return {"kind": kind, "x": self.rng.uniform(0, self.w - sprite[0]), "y": y, "dir": self.rng.choice([-1, 1]),
                "speed": speed * self.rng.uniform(0.8, 1.2), "ty": y, "turn": 0, "phase": self.rng.uniform(0, 6.3),
                "bottom": bottom, "dash": 0.0}

    def input(self, key, action="down", player=1):
        if action == "down" and key in ("a", "start"):
            self.drop_food()

    def drop_food(self):
        for _ in range(self.rng.randint(4, 7)):
            self.food.append([self.rng.uniform(4, self.w - 4), 0.0, self.rng.uniform(1.5, 3.0)])

    def step(self, dt, t, m):
        rng = self.rng
        for f in self.food:
            f[1] += f[2] * dt
            f[0] += 0.4 * math.sin(t * 2 + f[0]) * dt
        self.food = [f for f in self.food if f[1] < self.floor - 1]
        for f in self.fish:
            sprite, base_speed, bottom = FISH[f["kind"]]
            w, h, _ = sprite
            speed = f["speed"] * (1 + 2 * f["dash"])
            f["dash"] = max(0.0, f["dash"] - dt)
            # the nearest flake ahead or behind: go for it
            target = None
            if self.food and not bottom:
                target = min(self.food, key=lambda fl: abs(fl[0] - f["x"]) + abs(fl[1] - f["y"]))
                if abs(target[0] - f["x"]) + abs(target[1] - f["y"]) > 40:
                    target = None
            if target is not None:
                want = 1 if target[0] > f["x"] + w / 2 else -1
                if want != f["dir"] and f["turn"] == 0:
                    f["dir"], f["turn"] = want, 4
                f["ty"] = min(self.floor - h - 1, max(1, target[1] - h / 2))
                speed *= 1.6
                if abs(target[0] - f["x"] - w / 2) < 3 and abs(target[1] - f["y"] - h / 2) < 3:
                    self.food.remove(target)
                    self.eaten += 1
                    f["dash"] = 0.3
            elif rng.random() < 0.01 * dt * 10:  # wander: a new depth or a turn now and then
                if not bottom:
                    f["ty"] = rng.uniform(1, self.floor - h - 3)
                elif rng.random() < 0.5:
                    f["dir"], f["turn"] = -f["dir"], 4
            if f["turn"]:
                f["turn"] -= 1
                continue  # turning on the spot: a narrow frame or two
            f["x"] += f["dir"] * speed * 10 * dt
            f["y"] += (f["ty"] - f["y"]) * min(1.0, 1.5 * dt) + (0 if bottom else 0.15 * math.sin(t * 2 + f["phase"]) * dt * 10)
            if f["x"] < -1 and f["dir"] < 0 or f["x"] > self.w - w + 1 and f["dir"] > 0:
                f["dir"], f["turn"] = -f["dir"], 4
        for b in self.bubbles:
            b[1] -= b[2] * dt
            b[0] += 0.6 * math.sin(t * 3 + b[1]) * dt
        self.bubbles = [b for b in self.bubbles if b[1] > 0]

    def draw(self, c, t, m):
        # sand, plants, castle at the back
        for x in range(self.w):
            if (x + (self.floor + 1)) % 2 == 0:
                c.set(x, self.floor + 1)
            c.set(x, self.floor + 2 if self.floor + 2 < self.h else self.floor + 1)
        sway = 1.0 + (0.6 * m.level("mid") if m.on else 0.0)
        for x0, hgt, ph in self.plants:
            for k in range(hgt):
                y = self.floor - k
                off = sway * 2.0 * (k / hgt) ** 2 * math.sin(t * 1.2 + ph + k * 0.15)
                c.set(x0 + off, y)
                if k % 3 == 1 and k < hgt - 1:  # fronds, alternating sides, longer lower down
                    side = 1 if (k // 3) % 2 else -1
                    for j in range(1, 2 + (hgt - k) // 6):
                        c.set(x0 + off + side * j, y + (1 if j > 1 else 0))
        if self.p["castle"]:
            _clear(c, CASTLE, self.castle_x, self.floor - CASTLE[1] + 1)
            _blit(c, CASTLE, self.castle_x, self.floor - CASTLE[1] + 1)
        for f in self.fish:
            sprite = FISH[f["kind"]][0]
            w, h, dots = sprite
            x, y = int(round(f["x"])), int(round(f["y"]))
            if f["turn"]:  # seen from the front: a thin slice
                for dy in range(h):
                    c.set(x + w // 2, y + dy, 0)
                for dy in range(1, h - 1):
                    c.set(x + w // 2, y + dy)
                continue
            _clear(c, sprite, x, y, flip=f["dir"] < 0)
            _blit(c, sprite, x, y, flip=f["dir"] < 0)
            if f["kind"] != "neon":  # an eye
                c.set(x + (w - 3 if f["dir"] > 0 else 2), y + (h // 2 - 1 if h > 4 else 1), 0)
        for fl in self.food:
            c.set(fl[0], fl[1])
        for b in self.bubbles:
            c.set(b[0], b[1])
            if b[2] < 5:  # big slow bubbles are rings
                c.set(b[0] - 1, b[1], 0)

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, 1.0)
        dt = 0.0 if self.last_t is None else max(0.0, min(0.3, t - self.last_t))
        self.last_t = t
        if m.on and m.beat:
            for f in self.fish:
                f["dash"] = max(f["dash"], 0.25)
        every = float(self.p["feed_every"] or 0)
        if every and t - self.fed_at >= every:
            self.fed_at = t
            self.drop_food()
        if self.p["bubbles"]:
            rate = 0.8 + (3 * m.level("bass") if m.on else 0)
            if self.rng.random() < rate * dt:
                src = self.castle_x + 5 if self.p["castle"] else self.rng.uniform(2, self.w - 2)
                self.bubbles.append([src + self.rng.uniform(-2, 2), self.floor - 1.0, self.rng.uniform(3, 9)])
        self.step(dt, t, m)
        self.draw(c, t, m)
        self.interval = 0.16 if _quiet(self) else 0.08

    def describe(self):
        return f"Aquarium: {len(self.fish)} fish"


# ---- 2. Ant colony ------------------------------------------------------------------------------------------------

@register
@reactive
class Ants(App):
    id = "ants"
    title = "Ant colony"
    category = "Generative"
    description = ("Ants leave the nest, find food and carry it home, laying scent trails that others follow and "
                   "that fade when a pile runs out. New piles appear elsewhere.")
    params = [
        P("ants", "number", 30, "Ants", min=5, max=80),
        P("piles", "number", 2, "Food piles at a time", min=1, max=4),
        P("speed", "number", 1, "Speed", min=0.3, max=3, step=0.1),
        P("trails", "checkbox", True, "Show the scent trails"),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.nest = (self.rng.uniform(12, self.w - 12), self.rng.uniform(10, self.h - 10))
        self.scent = [0.0] * (self.w * self.h)  # food scent laid by ants carrying food home
        self.piles = []
        for _ in range(int(self.p["piles"])):
            self.new_pile()
        self.ants = [self.new_ant() for _ in range(int(self.p["ants"]))]
        self.carried = 0
        self.last_t = None
        self.acc = 0.0

    def new_pile(self):
        for _ in range(30):
            x, y = self.rng.uniform(4, self.w - 5), self.rng.uniform(4, self.h - 5)
            if math.hypot(x - self.nest[0], y - self.nest[1]) > 18 and all(
                    math.hypot(x - p["x"], y - p["y"]) > 14 for p in self.piles):
                break
        self.piles.append({"x": x, "y": y, "food": self.rng.randint(40, 90), "r": 3.0})

    def new_ant(self):
        return {"x": self.nest[0], "y": self.nest[1], "a": self.rng.uniform(0, 2 * math.pi), "food": False,
                "age": 0}

    def sense(self, ant, side):
        """Scent a couple of dots ahead, to the left (side -1), straight on (0) or right (+1)."""
        a = ant["a"] + side * 0.8
        x, y = int(ant["x"] + 3 * math.cos(a)), int(ant["y"] + 3 * math.sin(a))
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.scent[y * self.w + x]
        return -1.0

    def step(self, speed):
        rng, w, h = self.rng, self.w, self.h
        nx, ny = self.nest
        for ant in self.ants:
            ant["age"] += 1
            if ant["food"]:  # home with it, laying scent
                i = int(ant["y"]) * w + int(ant["x"])
                if 0 <= i < w * h:
                    self.scent[i] = min(1.0, self.scent[i] + 0.12)
                want = math.atan2(ny - ant["y"], nx - ant["x"])
                ant["a"] += ((want - ant["a"] + math.pi) % (2 * math.pi) - math.pi) * 0.3 + rng.uniform(-0.3, 0.3)
                if math.hypot(nx - ant["x"], ny - ant["y"]) < 3:
                    ant["food"] = False
                    ant["a"] += math.pi + rng.uniform(-0.8, 0.8)
                    self.carried += 1
            else:
                l, s, r = self.sense(ant, -1), self.sense(ant, 0), self.sense(ant, 1)
                if max(l, s, r) > 0.02 and rng.random() < 0.9:
                    ant["a"] += -0.5 if l > s and l >= r else (0.5 if r > s and r > l else 0.0)
                else:
                    ant["a"] += rng.uniform(-0.5, 0.5)
                for p in self.piles:
                    if p["food"] > 0 and math.hypot(p["x"] - ant["x"], p["y"] - ant["y"]) < p["r"] + 0.5:
                        p["food"] -= 1
                        p["r"] = max(1.0, 1 + 2.5 * p["food"] / 90)
                        ant["food"] = True
                        ant["a"] = math.atan2(ny - ant["y"], nx - ant["x"])
                        break
            ant["x"] += speed * math.cos(ant["a"])
            ant["y"] += speed * math.sin(ant["a"])
            if not 0 <= ant["x"] < w or not 0 <= ant["y"] < h:  # the edge of the world: turn back
                ant["x"], ant["y"] = min(w - 1, max(0, ant["x"])), min(h - 1, max(0, ant["y"]))
                ant["a"] += math.pi
        self.piles = [p for p in self.piles if p["food"] > 0]
        while len(self.piles) < int(self.p["piles"]):
            self.new_pile()

    def evaporate(self):
        self.scent = [v * 0.975 if v > 0.004 else 0.0 for v in self.scent]

    def render(self, c, now):
        m = self.music
        speed = float(self.p["speed"] or 1)
        steps = m.ticks("step", now, 8.0 * speed, per_beat=4.0 * speed)
        if not m.motion:
            steps = 1 if not _quiet(self) or int(now * 10) % 2 == 0 else 0
        for _ in range(min(3, steps)):
            self.step(0.9)
            self.evaporate()
        w = self.w
        if self.p["trails"]:
            for i, v in enumerate(self.scent):
                if v > 0.06 and BAYER[(i // w) & 3][i & 3] < v * 14:
                    c.px[i] = 1
        nx, ny = int(self.nest[0]), int(self.nest[1])
        c.circle(nx, ny, 3, fill=True)
        c.set(nx, ny, 0)
        for p in self.piles:
            r = p["r"]
            for y in range(int(p["y"] - r), int(p["y"] + r) + 1):
                for x in range(int(p["x"] - r), int(p["x"] + r) + 1):
                    if math.hypot(x - p["x"], y - p["y"]) <= r and (x + y) % 2 == 0:
                        c.set(x, y)
        for ant in self.ants:
            x, y = int(ant["x"]), int(ant["y"])
            c.set(x, y)
            if ant["food"]:
                c.set(x + int(round(math.cos(ant["a"]))), y + int(round(math.sin(ant["a"]))))
        self.interval = 0.2 if _quiet(self) else 0.1

    def describe(self):
        return f"Ants: {self.carried} carried home"


# ---- 3. Boids ---------------------------------------------------------------------------------------------------

@register
@reactive
class Boids(App):
    id = "boids"
    title = "Flock"
    category = "Generative"
    description = ("A flock of boids: they keep together, line up and keep their distance, and scatter from the "
                   "hawks that chase them. React to music: the beat scatters the flock.")
    params = [
        P("boids", "number", 40, "Boids", min=5, max=120),
        P("hawks", "number", 1, "Hawks", min=0, max=4),
        P("edges", "select", "wrap", "At the edges", options=[["wrap", "Wrap round"], ["bounce", "Turn back"]]),
        P("tails", "checkbox", True, "Tails"),
    ]
    interval = 0.06

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.boids = [[self.rng.uniform(0, self.w), self.rng.uniform(0, self.h), self.rng.uniform(-1, 1),
                       self.rng.uniform(-1, 1)] for _ in range(int(self.p["boids"]))]
        self.hawks = [[self.rng.uniform(0, self.w), self.rng.uniform(0, self.h), 0.0, 0.0]
                      for _ in range(int(self.p["hawks"]))]
        self.last_t = None

    def step(self, dt, scatter):
        w, h, wrap = self.w, self.h, self.p["edges"] == "wrap"
        boids = self.boids
        n = len(boids)
        R2, SEP2 = 12 ** 2, 4.0 ** 2
        acc = []
        for i, (x, y, vx, vy) in enumerate(boids):
            cx = cy = ax = ay = sx = sy = 0.0
            count = 0
            for j in range(n):
                if j == i:
                    continue
                ox, oy, ovx, ovy = boids[j]
                dx, dy = ox - x, oy - y
                if wrap:
                    dx -= w * round(dx / w)
                    dy -= h * round(dy / h)
                d2 = dx * dx + dy * dy
                if d2 < R2:
                    count += 1
                    cx += dx
                    cy += dy
                    ax += ovx
                    ay += ovy
                    if d2 < SEP2 and d2 > 0:
                        sx -= dx / d2
                        sy -= dy / d2
            fx = fy = 0.0
            if count:
                fx += 0.008 * cx / count + 0.08 * (ax / count - vx) + 0.5 * sx
                fy += 0.008 * cy / count + 0.08 * (ay / count - vy) + 0.5 * sy
            for hx, hy, _, _ in self.hawks:
                dx, dy = x - hx, y - hy
                d2 = dx * dx + dy * dy
                if d2 < 15 ** 2 and d2 > 0:
                    fx += 2.0 * dx / d2
                    fy += 2.0 * dy / d2
            if scatter:
                fx += self.rng.uniform(-1.5, 1.5)
                fy += self.rng.uniform(-1.5, 1.5)
            if not wrap:  # a soft wall
                if x < 6:
                    fx += 0.15
                if x > w - 7:
                    fx -= 0.15
                if y < 4:
                    fy += 0.15
                if y > h - 5:
                    fy -= 0.15
            acc.append((fx, fy))
        for b, (fx, fy) in zip(boids, acc):
            b[2] += fx
            b[3] += fy
            s = math.hypot(b[2], b[3])
            top, floor = 1.4, 0.6
            if s > top:
                b[2], b[3] = b[2] / s * top, b[3] / s * top
            elif s < floor and s > 0:
                b[2], b[3] = b[2] / s * floor, b[3] / s * floor
            b[0] += b[2] * dt * 12
            b[1] += b[3] * dt * 12
            if wrap:
                b[0] %= w
                b[1] %= h
            else:
                b[0], b[1] = min(w - 1, max(0, b[0])), min(h - 1, max(0, b[1]))
        for hk in self.hawks:  # a hawk chases the nearest boid, slower but single-minded
            if boids:
                tx, ty = min(boids, key=lambda b: (b[0] - hk[0]) ** 2 + (b[1] - hk[1]) ** 2)[:2]
                dx, dy = tx - hk[0], ty - hk[1]
                d = math.hypot(dx, dy) or 1
                hk[2] += 0.1 * dx / d
                hk[3] += 0.1 * dy / d
                s = math.hypot(hk[2], hk[3])
                if s > 1.0:
                    hk[2], hk[3] = hk[2] / s, hk[3] / s
                hk[0] += hk[2] * dt * 11
                hk[1] += hk[3] * dt * 11
                hk[0] %= w
                hk[1] %= h

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, 1.0)
        dt = 0.0 if self.last_t is None else max(0.0, min(0.2, t - self.last_t))
        self.last_t = t
        self.step(dt, m.on and m.beat)
        tails = self.p["tails"]
        for x, y, vx, vy in self.boids:
            c.set(x, y)
            if tails:
                c.set(x - vx * 1.2, y - vy * 1.2)
        for x, y, vx, vy in self.hawks:
            c.set(x, y)
            c.set(x - 1, y)
            c.set(x + 1, y)
            c.set(x, y - 1)
            c.set(x - vx * 2, y - vy * 2)
        self.interval = 0.12 if _quiet(self) else 0.06

    def describe(self):
        return f"Flock of {len(self.boids)}"


# ---- 4. Sand dunes -----------------------------------------------------------------------------------------------

@register
@reactive
class Dunes(App):
    id = "dunes"
    title = "Sand dunes"
    category = "Generative"
    description = ("A dune field in the wind: three ranges of dunes, the far ones smaller, creep downwind with "
                   "ripples running over them; the slip faces in the lee lie in shadow. A few grains blow past.")
    params = [
        P("wind", "number", 1, "Wind", min=0.2, max=3, step=0.1),
        P("direction", "select", "right", "Blows to the", options=[["right", "Right"], ["left", "Left"]]),
        P("grains", "checkbox", True, "Blowing grains"),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        # far to near: base row, height, and the waves that make the profile (wavelength, share, phase)
        self.lanes = []
        for base, amp, lam in ((self.h - 19, 4.0, 26), (self.h - 10, 6.0, 34), (self.h - 1, 9.0, 44)):
            waves = [(lam * self.rng.uniform(0.9, 1.1), 1.0, self.rng.uniform(0, 6.3)),
                     (lam * self.rng.uniform(0.45, 0.6), 0.45, self.rng.uniform(0, 6.3)),
                     (lam * 0.18, 0.12, self.rng.uniform(0, 6.3))]  # ripples
            self.lanes.append((base, amp, waves))
        self.grains = []

    def profile(self, lane, x, t, d):
        base, amp, waves = lane
        h = 0.0
        for k, (lam, share, ph) in enumerate(waves):
            # a dune is not a sine: the windward side is long and gentle, the lee short and steep
            speed = (0.6 if k < 2 else 2.5) * (0.8 + 0.2 * k)
            u = (d * x / lam - t * speed / lam * 3 + ph) % 1.0
            shape = u / 0.75 if u < 0.75 else (1 - u) / 0.25
            h += amp * share * shape
        return h

    def render(self, c, now):
        m = self.music
        wind = float(self.p["wind"] or 1)
        t = m.clock("t", now, wind)
        d = 1 if self.p["direction"] == "right" else -1
        w = self.w
        for lane in self.lanes:
            base = lane[0]
            hs = [self.profile(lane, x, t, d) for x in range(w)]
            for x in range(w):
                top = base - int(round(hs[x]))
                lee = hs[x] < hs[(x - d) % w] - 0.15  # dropping away downwind: the slip face, in shadow
                for y in range(max(0, top - 2), base + 1):  # sky behind the crest, over the range behind
                    c.set(x, y, 0)
                for y in range(top, base + 1):
                    if not lee or y == top or ((x + y) % 2 == 0 and y < top + 4):
                        c.set(x, y)
        if self.p["grains"]:
            if self.rng.random() < 0.5 * wind:
                self.grains.append([0.0 if d > 0 else float(w - 1), self.rng.uniform(1, self.h - 22),
                                    self.rng.uniform(1.5, 3)])
            for g in self.grains:
                g[0] += d * g[2] * wind
                g[1] += self.rng.uniform(-0.3, 0.4)
                c.set(g[0], g[1])
            self.grains = [g for g in self.grains if 0 <= g[0] < w and g[1] < self.h][-20:]
        self.interval = 0.3 if _quiet(self) else 0.1

    def describe(self):
        return self.title


# ---- 5. Reaction-diffusion ------------------------------------------------------------------------------------------

TURING = {  # Gray-Scott feed / kill rates
    "spots": (0.035, 0.065), "stripes": (0.030, 0.060), "coral": (0.055, 0.062), "mitosis": (0.028, 0.062),
    "worms": (0.046, 0.063),
}


@register
@reactive
class Turing(App):
    id = "turing"
    title = "Turing patterns"
    category = "Generative"
    description = ("Reaction-diffusion (Gray-Scott): two chemicals feed on and eat each other and spread, and out of "
                   "noise grow spots, stripes, coral or dividing cells. Starts again when it settles.")
    params = [
        P("pattern", "select", "random", options=[["random", "A different one each time"],
                                                  *[[k, k.capitalize()] for k in TURING]]),
        P("speed", "number", 1, "Speed", min=0.3, max=3, step=0.1),
        P("max_seconds", "number", 180, "Start again after at most (s)", min=20, max=3600),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.gw, self.gh = self.w // 2, self.h // 2  # a 2 x 2 dot cell: fast enough for a Pi
        self.seed(0.0)

    def seed(self, t):
        gw, gh = self.gw, self.gh
        n = gw * gh
        self.u = [1.0] * n
        self.v = [0.0] * n
        for _ in range(self.rng.randint(3, 7)):  # a few blots of the second chemical
            cx, cy = self.rng.randrange(gw), self.rng.randrange(gh)
            for y in range(cy - 2, cy + 3):
                for x in range(cx - 2, cx + 3):
                    i = (y % gh) * gw + (x % gw)
                    self.u[i], self.v[i] = 0.5, 0.25
        for i in range(n):
            if self.rng.random() < 0.02:
                self.v[i] = 0.2
        kind = self.p["pattern"]
        self.kind = self.rng.choice(list(TURING)) if kind not in TURING else kind
        self.f, self.k = TURING[self.kind]
        self.seeded_at = t
        self.history = []

    @staticmethod
    def laplacian(g, gw):
        """The usual weighted 3 x 3 Laplacian (0.2 to the sides, 0.05 to the corners), wrapping round."""
        l, r = g[1:] + g[:1], g[-1:] + g[:-1]
        up, dn = g[gw:] + g[:gw], g[-gw:] + g[:-gw]
        ul, ur = up[1:] + up[:1], up[-1:] + up[:-1]
        dl, dr = dn[1:] + dn[:1], dn[-1:] + dn[:-1]
        return [0.2 * (a + b + c + d) + 0.05 * (e + f + h + i) - j
                for a, b, c, d, e, f, h, i, j in zip(l, r, up, dn, ul, ur, dl, dr, g)]

    def iterate(self, n):
        gw, u, v = self.gw, self.u, self.v
        f, k = self.f, self.k
        du, dv = 0.16, 0.08
        for _ in range(n):
            lu, lv = self.laplacian(u, gw), self.laplacian(v, gw)
            uvv = [a * b * b for a, b in zip(u, v)]
            u = [min(1.0, max(0.0, a + du * la - r + f * (1 - a))) for a, la, r in zip(u, lu, uvv)]
            v = [min(1.0, max(0.0, b + dv * lb + r - (f + k) * b)) for b, lb, r in zip(v, lv, uvv)]
        self.u, self.v = u, v

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, 1.0)
        speed = float(self.p["speed"] or 1)
        n = m.ticks("it", now, 100 * speed, cap=20) if m.motion else int(10 * speed)
        if _quiet(self):
            n = max(1, n // 2)
        self.iterate(max(1, min(20, n)))
        if m.on and m.hit("bass"):  # a drop of the second chemical where the bass lands
            cx, cy = self.rng.randrange(self.gw), self.rng.randrange(self.gh)
            for y in range(cy - 1, cy + 2):
                for x in range(cx - 1, cx + 2):
                    self.v[(y % self.gh) * self.gw + (x % self.gw)] = 0.5
        gw, gh, w = self.gw, self.gh, self.w
        bits = bytes(1 if x > 0.12 else 0 for x in self.v)
        px = c.px
        for gy in range(gh):
            row = bytes(b for b in bits[gy * gw:(gy + 1) * gw] for _ in (0, 1))
            px[(2 * gy) * w:(2 * gy) * w + len(row)] = row
            px[(2 * gy + 1) * w:(2 * gy + 1) * w + len(row)] = row
        key = hash(bits)
        self.history = (self.history + [key])[-30:]
        lit = sum(bits)
        settled = len(self.history) == 30 and len(set(self.history)) == 1
        dead = lit == 0 and t - self.seeded_at > 5
        full = lit > 0.95 * len(bits)
        if settled or dead or full or t - self.seeded_at > float(self.p["max_seconds"]):
            self.seed(t)
        self.interval = 0.2 if _quiet(self) else 0.1

    def describe(self):
        return f"Turing: {self.kind}"


# ---- 6. Wolfram automata --------------------------------------------------------------------------------------------

@register
@reactive
class Wolfram(App):
    id = "wolfram"
    title = "Wolfram automaton"
    category = "Generative"
    description = ("A one-dimensional cellular automaton, one row a step, scrolling up: rule 30's chaos, 90's "
                   "Sierpinski triangles, 110's gliders, 184's traffic, or a random rule.")
    params = [
        P("rule", "select", "30", "Rule", options=[["30", "30 (chaos)"], ["90", "90 (Sierpinski)"],
                                                  ["110", "110 (gliders)"], ["184", "184 (traffic)"], ["54", "54"],
                                                  ["150", "150"], ["random", "A random rule each run"]]),
        P("seed", "select", "single", "Start from", options=[["single", "One dot"], ["random", "Random dots"]]),
        P("rows_per_second", "number", 10, "Rows a second", min=1, max=30),
        P("restart", "number", 60, "Start again after (rows past the board; 0 = never)", min=0, max=2000),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.reset()

    def reset(self):
        rule = self.p["rule"]
        self.rule = self.rng.randrange(256) if rule == "random" else int(rule)
        if self.p["seed"] == "random":
            row = [1 if self.rng.random() < 0.5 else 0 for _ in range(self.w)]
        else:
            row = [0] * self.w
            row[self.w // 2] = 1
        self.rows = [row]
        self.count = 0

    def step(self):
        row, w, rule = self.rows[-1], self.w, self.rule
        nxt = [(rule >> ((row[(x - 1) % w] << 2) | (row[x] << 1) | row[(x + 1) % w])) & 1 for x in range(w)]
        self.rows = (self.rows + [nxt])[-self.h:]
        self.count += 1

    def render(self, c, now):
        m = self.music
        rate = float(self.p["rows_per_second"] or 10)
        if _quiet(self):
            rate = min(rate, 3.0)
        n = m.ticks("rows", now, rate, per_beat=rate / 2)
        for _ in range(min(5, n)):
            self.step()
            if self.p["rule"] == "random" and self.count == 3 * self.h:  # a boring random rule: try another
                if len({tuple(r) for r in self.rows[-5:]}) == 1:
                    self.reset()
        past = self.count - self.h
        restart = int(self.p["restart"] or 0)
        if restart and past >= restart:
            self.reset()
        w = self.w
        px = c.px
        for y, row in enumerate(self.rows):
            px[y * w:(y + 1) * w] = bytes(row)
        self.interval = 0.2 if _quiet(self) else min(0.1, 1 / rate)

    def describe(self):
        return f"Rule {self.rule}"


# ---- 7. Langton's ant --------------------------------------------------------------------------------------------

LANGTON_RULES = {"RL": "Classic (the highway)", "LLRR": "LLRR (symmetric)", "LRRRRRLLR": "LRRRRRLLR (fills a square)",
                 "RRLLLRLLLRRR": "RRLLLRLLLRRR (a triangle)", "LLRRRLRLRLLR": "LLRRRLRLRLLR (spirals)"}


@register
@reactive
class Langton(App):
    id = "langton"
    title = "Langton's ant"
    category = "Generative"
    description = ("An ant on a grid: on a black dot it turns right, flips the dot and steps; on a white one it turns "
                   "left. Chaos for ten thousand steps, then a highway. Multi-colour rules draw other shapes, the "
                   "colours as shades of dither. Starts again when the board fills.")
    params = [
        P("rule", "select", "RL", "Rule", options=[[k, v] for k, v in LANGTON_RULES.items()]),
        P("ants", "number", 1, "Ants", min=1, max=4),
        P("steps_per_second", "number", 200, "Steps a second", min=10, max=2000),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.reset()

    def reset(self):
        self.rule = self.p["rule"] if self.p["rule"] in LANGTON_RULES else "RL"
        self.k = len(self.rule)
        self.grid = bytearray(self.w * self.h)
        self.ants = [[self.w // 2 + self.rng.randint(-8, 8), self.h // 2 + self.rng.randint(-4, 4),
                      self.rng.randrange(4)] for _ in range(int(self.p["ants"]))]
        self.steps = 0

    def step(self, n):
        w, h, g, rule, k = self.w, self.h, self.grid, self.rule, self.k
        for _ in range(n):
            for ant in self.ants:
                x, y, d = ant
                i = y * w + x
                s = g[i]
                d = (d + (1 if rule[s] == "R" else -1)) % 4
                g[i] = (s + 1) % k
                if d == 0:
                    y -= 1
                elif d == 1:
                    x += 1
                elif d == 2:
                    y += 1
                else:
                    x -= 1
                ant[0], ant[1], ant[2] = x % w, y % h, d
            self.steps += 1

    def render(self, c, now):
        m = self.music
        rate = float(self.p["steps_per_second"] or 200)
        if _quiet(self):
            rate = min(rate, 40.0)
        n = m.ticks("steps", now, rate, cap=200)
        self.step(min(100, n))
        w, k, g = self.w, self.k, self.grid
        if k == 2:
            c.px[:] = g
        else:
            px = c.px
            for i, s in enumerate(g):
                if s:
                    px[i] = 1 if s == k - 1 or BAYER[(i // w) & 3][i & 3] < 16 * s / (k - 1) else 0
        for x, y, d in self.ants:
            c.set(x, y, 1 - c.get(x, y))
        if sum(1 for s in g if s) > 0.7 * len(g) or self.steps > 400000:
            self.reset()
        self.interval = 0.1 if _quiet(self) else 0.05

    def describe(self):
        return f"Langton {self.rule}: {self.steps} steps"


# ---- 8. Voronoi growth -----------------------------------------------------------------------------------------------

@register
@reactive
class Voronoi(App):
    id = "voronoi"
    title = "Voronoi cells"
    category = "Generative"
    description = ("Seeds appear and their cells grow outwards at the same speed until they meet, drawing the "
                   "boundaries between them; when the board is full it starts again. React to music: new seeds "
                   "on the hits.")
    params = [
        P("seeds", "number", 14, "Seeds", min=3, max=40),
        P("growth", "number", 1, "Growth speed", min=0.2, max=4, step=0.1),
        P("look", "select", "lines", "Draw", options=[["lines", "Boundaries"], ["fill", "Dithered cells"],
                                                      ["both", "Both"]]),
        P("hold", "number", 3, "Hold the finished picture (s)", min=0, max=60),
    ]
    interval = 0.08

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())
        self.reset(0.0)

    def reset(self, t):
        self.owner = [-1] * (self.w * self.h)
        self.frontier = []  # per seed: list of cells reached last step
        self.seeds = []
        self.full_at = None
        self.next_seed = t
        self.started_at = t

    def add_seed(self, x=None, y=None):
        """A seed on free ground (a few tries at random spots); False when there's none left."""
        if x is None:
            for _ in range(20):
                x, y = self.rng.randrange(self.w), self.rng.randrange(self.h)
                if self.owner[y * self.w + x] == -1:
                    break
        i = y * self.w + x
        if self.owner[i] != -1:
            return False
        k = len(self.seeds)
        self.seeds.append((x, y))
        self.owner[i] = k
        self.frontier.append([i])
        return True

    def grow(self, parity):
        w, h, owner = self.w, self.h, self.owner
        for k, cells in enumerate(self.frontier):
            nxt = []
            for i in cells:
                x, y = i % w, i // w
                nbs = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                if parity:  # every other step the diagonals too: round cells, not diamonds
                    nbs += ((x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1))
                for nx, ny in nbs:
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if owner[j] == -1:
                            owner[j] = k
                            nxt.append(j)
            self.frontier[k] = nxt

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, 1.0)
        want = int(self.p["seeds"])
        growth = float(self.p["growth"] or 1)
        if len(self.seeds) < want and t >= self.next_seed:
            self.add_seed()
            self.next_seed = t + self.rng.uniform(0.1, 0.4) / growth
        if m.on and (m.hit("bass") or m.hit("high")) and len(self.seeds) < want * 2:
            self.add_seed()
        steps = m.ticks("grow", now, 5 * growth, cap=3)
        if _quiet(self):
            steps = min(steps, 1)
        for _ in range(steps):
            self.grow(self.rng.random() < 0.5)
        w, h, owner = self.w, self.h, self.owner
        look = self.p["look"]
        px = c.px
        if look in ("fill", "both"):
            for i, k in enumerate(owner):
                if k >= 0 and (k % 3 == 0 or (k % 3 == 1 and (i % w + i // w) % 2 == 0)):
                    px[i] = 1
        if look in ("lines", "both"):
            for i, k in enumerate(owner):
                if k < 0:
                    continue
                x = i % w  # the edge of a cell: against another cell, or the ground it's still growing into
                if (x + 1 < w and owner[i + 1] != k) or (x > 0 and owner[i - 1] != k) or \
                        (i + w < len(owner) and owner[i + w] != k) or (i >= w and owner[i - w] != k):
                    px[i] = 1
            for x, y in self.seeds:
                c.set(x, y, 1)
        claimed = sum(1 for k in owner if k >= 0)
        if claimed == len(owner):
            if self.full_at is None:
                self.full_at = t
            elif t - self.full_at >= float(self.p["hold"] or 0):
                self.reset(t)
        self.interval = 0.16 if _quiet(self) else 0.08

    def describe(self):
        return f"Voronoi: {len(self.seeds)} cells"
