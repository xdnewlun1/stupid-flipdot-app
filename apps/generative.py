"""Generative animations."""

import math
import random

from .base import App, P, register
from .react import reactive
from .restart import RESTART_PARAMS, RestartFx

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


@register
@reactive
class Life(App):
    id = "life"
    title = "Game of Life"
    category = "Generative"
    description = "Conway's Game of Life; reseeds when it settles down."
    params = [
        P("density", "number", 0.3, "Starting density", min=0.05, max=0.9, step=0.05),
        P("fps", "number", 8, "Generations per second", min=1, max=20),
        P("max_seconds", "number", 120, "Restart after at most (s)", min=10, max=3600),
        *RESTART_PARAMS,
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.gen_time = 1 / float(self.p["fps"])
        self.interval = self.gen_time
        self.next_gen = 0
        self.fx = RestartFx(self)
        self.seed()

    def seed(self):
        d = float(self.p["density"])
        self.cells = bytearray(1 if random.random() < d else 0 for _ in range(self.w * self.h))
        self.history = []
        self.pops = []
        self.seeded_at = None

    def step(self):
        w, h, cur = self.w, self.h, self.cells
        nxt = bytearray(w * h)
        for y in range(h):
            up, down = ((y - 1) % h) * w, ((y + 1) % h) * w
            row = y * w
            for x in range(w):
                l, r = (x - 1) % w, (x + 1) % w
                n = (cur[up + l] + cur[up + x] + cur[up + r] + cur[row + l] + cur[row + r]
                     + cur[down + l] + cur[down + x] + cur[down + r])
                nxt[row + x] = 1 if n == 3 or (n == 2 and cur[row + x]) else 0
        self.cells = nxt

    def burst(self, size):
        """Music: a random blob of live cells somewhere (a new beat to grow from)."""
        w, h, rnd = self.w, self.h, random.random
        r = max(1, int(size))
        cx, cy = int(rnd() * w), int(rnd() * h)
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r * 2, cx + r * 2 + 1):  # wider than tall, like the board
                if rnd() < 0.5:
                    self.cells[(y % h) * w + x % w] = 1

    def render(self, c, now):
        m = self.music
        if m.enabled:  # generations on the music's clock: faster with the tempo, landing on the beat
            gens = m.ticks("gen", now, float(self.p["fps"]), per_beat=float(self.p["fps"]) / 2)
            if m.beat or m.hit("bass"):  # seed bursts on beats and bass notes, bigger when it's loud
                self.burst(1 + 3 * min(1.5, m.level("bass") + 0.5 * m.amount))
        else:
            gens = 1 if now >= self.next_gen else 0
        if gens:  # generations keep their own pace, even while a transition draws faster
            self.next_gen = max(self.next_gen + self.gen_time, now - self.gen_time)
            for _ in range(gens):
                self.step()
            if self.seeded_at is None:
                self.seeded_at = now
            key = hash(bytes(self.cells))
            pop = sum(self.cells)
            self.pops = (self.pops + [pop])[-80:]
            frozen = key in self.history or pop == 0           # still or repeating exactly
            boring = len(self.pops) == 80 and max(self.pops) - min(self.pops) <= 6  # e.g. only gliders left
            too_long = now - self.seeded_at > float(self.p["max_seconds"])
            if frozen or boring or too_long:
                self.seed()
                self.fx.restarted(now)
            self.history = (self.history + [key])[-12:]
        c.px[:] = self.cells
        self.fx.finish(c, now)
        # reacting: come back often enough to catch each beat and the clock's next generation
        self.interval = self.fx.interval(0.05 if m.enabled else max(0.01, self.next_gen - now))


@register
@reactive
class Rain(App):
    id = "rain"
    title = "Matrix rain"
    category = "Generative"
    description = "Falling streaks of dots."
    params = [
        P("density", "number", 0.5, min=0.1, max=1, step=0.1),
        P("speed", "number", 1, min=0.2, max=3, step=0.1),
    ]
    interval = 0.06

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.drops = [self.new_drop(x, initial=True) for x in range(self.w)]

    def new_drop(self, x, initial=False, dense=1.0):
        return {"x": x, "y": random.uniform(-self.h, self.h if initial else 0),
                "v": random.uniform(0.3, 1.0) * float(self.p["speed"]), "len": random.randint(3, 14),
                "active": random.random() < float(self.p["density"]) * dense}

    def render(self, c, now):
        m = self.music
        go, dense = 1.0, 1.0
        if m.enabled:  # the highs speed the streams up; hats start new ones, so busy cymbals fill the board
            go = m.speed * (1 - 0.3 * m.presence + 0.6 * m.level("high") + 1.2 * m.hits("high"))
            dense = 1 - 0.4 * m.presence + 0.9 * m.level("high")
            if m.hit("high"):
                idle = [d for d in self.drops if not d["active"] and d["y"] - d["len"] < 0]
                for d in random.sample(idle, min(len(idle), round(1 + 3 * min(1.5, m.hits("high"))))):
                    d["active"] = True  # still above the top: it streams in rather than appearing
        for i, d in enumerate(self.drops):
            d["y"] += d["v"] * go
            if d["y"] - d["len"] > self.h:
                self.drops[i] = d = self.new_drop(d["x"], dense=dense)
            if not d["active"]:
                continue
            head = int(d["y"])
            for k in range(d["len"]):
                # gappy tail looks more "digital"
                if k == 0 or (k + head) % 3:
                    c.set(d["x"], head - k)


@register
@reactive
class Plasma(App):
    id = "plasma"
    title = "Plasma"
    category = "Generative"
    description = "Flowing interference patterns."
    params = [
        P("style", "select", "bands", options=[["bands", "Contour bands"], ["dither", "Dithered shading"]]),
        P("speed", "number", 1, min=0.1, max=4, step=0.1),
        P("scale", "number", 1, "Zoom", min=0.3, max=3, step=0.1),
    ]
    interval = 0.08

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, float(self.p["speed"]))
        k = 1 / float(self.p["scale"])
        shift = warp = 0.0
        if m.enabled:
            # bass warps the rings from the centre outwards; the mids roll the palette (the bands flow)
            warp = 1.1 * min(1.5, m.both("bass"))
            self.shift = getattr(self, "shift", 0.0) + m.dt * (0.5 * m.level("mid") + 1.5 * m.hits("mid"))
            shift = self.shift
        cx, cy = self.w / 2 + 20 * math.sin(t * 0.3), self.h / 2 + 10 * math.cos(t * 0.4)
        bands = self.p["style"] == "bands"
        for y in range(self.h):
            for x in range(self.w):
                v = (math.sin(x * k / 7 + t) + math.sin(y * k / 5 + t * 1.3)
                     + math.sin((x + y) * k / 9 + t * 0.7) + math.sin(math.hypot(x - cx, y - cy) * k / 5 - t - warp))
                if bands:
                    on = int((v + 4) * 2 + shift) % 2
                else:
                    on = (v + 4 + 2 * math.sin(shift * math.pi)) / 8 * 16 > BAYER4[y % 4][x % 4] + 0.5
                c.px[y * self.w + x] = 1 if on else 0


@register
@reactive
class Bounce(App):
    id = "bounce"
    title = "Bouncing balls"
    category = "Generative"
    description = "Balls with gravity bouncing around the board."
    params = [
        P("balls", "number", 4, min=1, max=20),
        P("gravity", "checkbox", True),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.balls = [{
            "x": random.uniform(5, self.w - 5), "y": random.uniform(5, self.h / 2),
            "vx": random.uniform(-1.5, 1.5) or 1, "vy": random.uniform(-1, 1), "r": random.choice([1.5, 2, 3, 4]),
        } for _ in range(int(self.p["balls"]))]

    def render(self, c, now):
        m = self.music
        go, grow = 1.0, 0.0
        if m.enabled:
            go = m.speed
            grow = 0.1 * min(1.5, m.level("bass")) + 0.5 * min(1.5, m.hits("bass"))
        if m.on:  # bass notes kick every ball upwards (sideways too, a little); loud bass swells them
            if m.hit("bass") or m.beat:
                kick = 1.5 + 2.5 * min(1.5, max(m.hits("bass"), m.pulse))
                for b in self.balls:
                    b["vy"] = min(b["vy"], 0.0) - kick
                    b["vx"] += random.uniform(-0.6, 0.6) * min(1.0, m.amount)
                    b["vx"] = max(-3.0, min(3.0, b["vx"]))
        for b in self.balls:
            if self.p["gravity"]:
                b["vy"] += 0.15 * go
            b["x"] += b["vx"] * go
            b["y"] += b["vy"] * go
            r = b["r"]
            if b["x"] < r or b["x"] > self.w - 1 - r:
                b["vx"] = -b["vx"]
                b["x"] = min(max(b["x"], r), self.w - 1 - r)
            if b["y"] < r:
                b["vy"] = abs(b["vy"])
                b["y"] = r
            if b["y"] > self.h - 1 - r:
                b["y"] = self.h - 1 - r
                b["vy"] = -abs(b["vy"]) * (0.95 if self.p["gravity"] else 1)
                if self.p["gravity"] and abs(b["vy"]) < 1.5:
                    b["vy"] = -random.uniform(3, 4.5)  # keep them bouncing forever
            c.circle(b["x"], b["y"], r * (1 + grow) if grow else r, fill=True)


@register
@reactive
class Starfield(App):
    id = "starfield"
    title = "Starfield"
    category = "Generative"
    description = "Flying through space."
    params = [
        P("stars", "number", 60, min=10, max=300),
        P("speed", "number", 1, min=0.2, max=4, step=0.1),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.stars = [self.new_star(random.uniform(0.1, 1)) for _ in range(int(self.p["stars"]))]

    def new_star(self, z=1.0):
        return [random.uniform(-1, 1), random.uniform(-1, 1), z]

    def project(self, s):
        return self.w / 2 + s[0] / s[2] * self.w / 2, self.h / 2 + s[1] / s[2] * self.h / 2

    def render(self, c, now):
        m = self.music
        go, streak = 1.0, 0.4
        if m.enabled:  # warp speed on the kick: the bass surges you forward, loud passages fly faster
            kick = min(1.5, m.hits("bass"))
            go = m.speed * (1 - 0.4 * m.presence + 0.5 * m.level("level") + 0.3 * m.level("bass") + 2.5 * kick)
            streak = 0.4 + 0.25 * min(1.0, kick)
        for i, s in enumerate(self.stars):
            x0, y0 = self.project(s)
            s[2] -= 0.02 * float(self.p["speed"]) * go
            if s[2] <= 0.02:
                self.stars[i] = self.new_star()
                continue
            x1, y1 = self.project(s)
            if not (0 <= x1 < self.w and 0 <= y1 < self.h):
                self.stars[i] = self.new_star()
                continue
            if s[2] < streak:
                c.line(x0, y0, x1, y1)  # streak when close
            else:
                c.set(x1, y1)
