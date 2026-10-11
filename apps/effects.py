"""More generative effects, plus "Random effects", which keeps switching between all of them
with random settings and random transitions. Everything here is cheap enough for a Pi."""

import math
import random

from canvas import Canvas
from transitions import NAMES as TRANSITIONS, Transition

from .base import REGISTRY, App, P, create, register
from .react import react_params, reactive
from .restart import RESTART_PARAMS, RestartFx

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def ring(c, cx, cy, r, toggle=False):
    """Circle outline by walking the circumference; toggle=True XORs dots (interference)."""
    steps = max(8, int(r * 6.3))
    seen = set()
    for k in range(steps):
        a = k * 6.2832 / steps
        x, y = int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))
        if 0 <= x < c.w and 0 <= y < c.h and (x, y) not in seen:
            seen.add((x, y))
            i = y * c.w + x
            c.px[i] = (c.px[i] ^ 1) if toggle else 1


@register
@reactive
class Fireworks(App):
    id = "fireworks"
    title = "Fireworks"
    category = "Generative"
    description = "Rockets that burst into falling sparks."
    params = [P("rate", "number", 1.0, "Rockets per second", min=0.2, max=5, step=0.1)]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random()
        self.rockets, self.sparks = [], []

    def render(self, c, now):
        m = self.music
        rate, size = 1.0, 1.0
        if m.motion:  # rockets go up on the beat and on bass notes; louder music, bigger bursts
            rate = 1 - 0.7 * m.presence  # a few random ones still, so a quiet passage isn't empty
            size = 0.7 + 0.6 * min(1.5, m.level("level"))
            if m.beat:  # rockets near the top burst on the beat
                for r in self.rockets:
                    if r[3] > -1.0:
                        r[3] = -0.2
            # launches on the beat (on bass notes too while no beat has come for a while), two in loud parts
            if m.beat or (m.hit("bass") and now - getattr(self, "launched", 0) > 0.6):
                self.launched = now
                for _ in range(2 if m.level("level") > 0.85 else 1):
                    self.rockets.append([self.rng.uniform(10, c.w - 10), c.h - 1, self.rng.uniform(-0.3, 0.3),
                                         -self.rng.uniform(1.6, 2.3) * (0.85 + 0.25 * min(1.0, m.level("bass"))), size])
        if self.rng.random() < float(self.p["rate"]) * self.interval * rate:
            self.rockets.append([self.rng.uniform(10, c.w - 10), c.h - 1, self.rng.uniform(-0.3, 0.3),
                                 -self.rng.uniform(1.6, 2.3)])
        alive = []
        for r in self.rockets:
            r[0] += r[2]; r[1] += r[3]; r[3] += 0.06
            if r[3] >= -0.2:  # apex: burst
                big = r[4] if len(r) > 4 else 1.0
                n = self.rng.randint(18, 34) if big == 1.0 else int(self.rng.randint(18, 34) * big)
                for k in range(n):
                    a, sp = k * 6.2832 / n, self.rng.uniform(0.6, 1.4) * big
                    self.sparks.append([r[0], r[1], math.cos(a) * sp, math.sin(a) * sp * 0.8, self.rng.randint(12, 26)])
            else:
                alive.append(r)
                c.set(r[0], r[1]); c.set(r[0], r[1] + 1)
        self.rockets = alive
        keep = []
        for s in self.sparks:
            s[0] += s[2]; s[1] += s[3]; s[3] += 0.05; s[2] *= 0.97; s[4] -= 1
            if s[4] > 0 and (s[4] > 5 or self.rng.random() < 0.5):  # flicker out at the end
                c.set(s[0], s[1])
            if s[4] > 0:
                keep.append(s)
        self.sparks = keep[-600:]


@register
@reactive
class Ripples(App):
    id = "ripples"
    title = "Ripples"
    category = "Generative"
    description = "Raindrops on a pond; overlapping rings interfere."
    params = [P("rate", "number", 1.5, "Drops per second", min=0.2, max=6, step=0.1),
              P("rings", "number", 3, "Rings per drop", min=1, max=6)]
    interval = 0.06

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random()
        self.drops = []

    def render(self, c, now):
        m = self.music
        if m.motion:
            return self.render_music(c, now, m)
        if self.rng.random() < float(self.p["rate"]) * self.interval:
            self.drops.append((self.rng.uniform(0, c.w), self.rng.uniform(0, c.h), now))
        keep = []
        for x, y, t0 in self.drops:
            r0 = (now - t0) * 18
            if r0 < 70:
                keep.append((x, y, t0))
                for k in range(int(self.p["rings"])):
                    r = r0 - k * 4
                    if r > 0:
                        ring(c, x, y, r, toggle=True)
        self.drops = keep[-14:]

    def render_music(self, c, now, m):
        """Drops fall on the hits: a bass note is a big drop with extra rings, a snare a smaller one, a
        hi-hat a tiny one. Rings spread on the music's clock (faster with the tempo or the loudness)."""
        T = m.clock("t", now, 1.0)
        rings = int(self.p["rings"])
        if self.rng.random() < float(self.p["rate"]) * self.interval * (1 - 0.75 * m.presence):
            self.drops.append((self.rng.uniform(0, c.w), self.rng.uniform(0, c.h), T, rings, 70))
        for band, extra, reach in (("bass", 2, 80), ("mid", 0, 45), ("high", -1, 18)):
            if m.hit(band):
                n = max(1, rings + round(extra * min(1.5, m.amount)))
                self.drops.append((self.rng.uniform(0, c.w), self.rng.uniform(0, c.h), T, n, reach))
        keep = []
        for d in self.drops:
            x, y, t0, n, reach = d if len(d) == 5 else (*d, rings, 70)
            r0 = (T - t0) * 18
            if r0 < reach:
                keep.append((x, y, t0, n, reach))
                for k in range(n):
                    r = r0 - k * 4
                    if r > 0:
                        ring(c, x, y, r, toggle=True)
        self.drops = keep[-14:]


@register
@reactive
class Metaballs(App):
    id = "metaballs"
    title = "Metaballs"
    category = "Generative"
    description = "Blobs that drift, merge and split."
    params = [P("balls", "number", 5, min=2, max=9),
              P("outline", "checkbox", False, "Outlines only")]
    interval = 0.08

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        rng = random.Random()
        self.balls = [[rng.uniform(10, self.w - 10), rng.uniform(5, self.h - 5), rng.uniform(-0.9, 0.9),
                       rng.uniform(-0.5, 0.5), rng.uniform(6, 11)] for _ in range(int(self.p["balls"]))]

    def render(self, c, now):
        m = self.music
        go, grow, edge = 1.0, 1.0, 1.0
        if m.motion:  # the bass swells the blobs; the mids stir them faster and thin the outline band
            go = m.speed * (1 + 1.2 * min(1.5, m.level("mid"))) * m.surge(0.5)
            grow = 1 + 0.25 * min(1.5, m.level("bass")) + 0.5 * min(1.5, m.hits("bass"))
            edge = 1 + 0.4 * min(1.0, m.level("mid"))
        for b in self.balls:
            b[0] += b[2] * go; b[1] += b[3] * go
            if not 0 < b[0] < c.w: b[2] = -b[2]
            if not 0 < b[1] < c.h: b[3] = -b[3]
        w, h, px = c.w, c.h, c.px
        outline = self.p["outline"]
        hi = 1.35 / edge + (1 - 1 / edge) if outline else 1.35  # the outline band: 1.35 at rest, narrower with the mids
        # per ball: its squared x distance to every column once; then each row adds one ball at a time to the
        # row's field with a list comprehension (C-speed iteration), instead of a Python loop per dot per ball
        cols = [[(x - bx) ** 2 for x in range(w)] for bx, _, _, _, _ in self.balls]
        rr = [r * r * grow for _, _, _, _, r in self.balls]
        out = bytearray()
        for y in range(h):
            dys = [((y - by) * 1.6) ** 2 + 0.5 for _, by, _, _, _ in self.balls]
            if not outline and sum(r2 / dy for r2, dy in zip(rr, dys)) <= 1.0:
                out += bytes(w)  # even straight above/below every ball the field stays under the threshold
                continue
            r2, dxs, dy = rr[0], cols[0], dys[0]
            f = [r2 / (dx + dy) for dx in dxs]
            for r2, dxs, dy in zip(rr[1:], cols[1:], dys[1:]):
                f = [v + r2 / (dx + dy) for v, dx in zip(f, dxs)]
            out += bytes((1.0 < v < hi) if outline else (v > 1.0) for v in f)
        px[:] = out


@register
@reactive
class Tunnel(App):
    id = "tunnel"
    title = "Tunnel"
    category = "Generative"
    description = "Flying down a winding tunnel."
    params = [P("shape", "select", "circle", options=[["circle", "Round"], ["square", "Square"]]),
              P("speed", "number", 1.0, min=0.2, max=4, step=0.1)]
    interval = 0.05

    def render(self, c, now):
        m = self.music
        # following the tempo, one ring passes per beat (landing on it); else the bass and loudness push
        t = m.clock("t", now, float(self.p["speed"]), per_beat=7 / 12)
        swell = 1.0
        if m.motion:
            swell = 1 + 0.25 * min(1.5, m.both("bass"))  # the rings breathe out on the kick
            if m.follow != "tempo":
                self.push = getattr(self, "push", 0.0) + m.dt * 1.2 * min(1.5, m.both("bass"))
        t += getattr(self, "push", 0.0)
        cx = c.w / 2 + math.sin(t * 0.7) * 14
        cy = c.h / 2 + math.cos(t * 0.9) * 6
        gap = 7
        for k in range(12):
            r = ((t * 12 + k * gap) % (gap * 12)) * 0.75 * swell
            if self.p["shape"] == "square":
                c.rect(cx - r, cy - r / 2, r * 2, r)
            else:
                ring(c, cx, cy, r)


@register
@reactive
class Spiral(App):
    id = "spiral"
    title = "Spiral"
    category = "Generative"
    description = "A hypnotic rotating spiral."
    params = [P("arms", "number", 3, min=1, max=8),
              P("twist", "number", 0.35, min=0.05, max=1, step=0.05),
              P("speed", "number", 2.0, min=0.2, max=8, step=0.1)]
    interval = 0.07

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        w, h = self.w, self.h
        # precompute angle and distance of every dot from the centre
        self.geo = [(math.atan2(y - h / 2, (x - w / 2) * 0.6), math.hypot((x - w / 2) * 0.6, y - h / 2))
                    for y in range(h) for x in range(w)]

    def render(self, c, now):
        m = self.music
        # following the tempo: an arm moves round to the next one every 4 beats, in step with the beat
        t = m.clock("t", now, float(self.p["speed"]), per_beat=math.pi / 2)
        arms, twist = int(self.p["arms"]), float(self.p["twist"])
        if m.motion:  # a bass note kicks it round a little further; the mids widen the arms
            self.kick = getattr(self, "kick", 0.0) + m.dt * 5 * min(1.5, m.hits("bass"))
            t += self.kick
            thr = 0.15 - 0.3 * min(1.0, m.level("mid")) - 0.3 * min(1.0, m.hits("mid"))
            c.px[:] = bytearray(1 if math.sin(a * arms + d * twist * 3 - t) > thr else 0 for a, d in self.geo)
            return
        c.px[:] = bytearray(1 if math.sin(a * arms + d * twist * 3 - t) > 0.15 else 0 for a, d in self.geo)


@register
@reactive
class Maze(App):
    id = "maze"
    title = "Maze"
    category = "Generative"
    description = "Carves a random maze, shows it off, then starts again."
    params = [P("speed", "number", 30, "Cells carved per second", min=5, max=300),
              P("hold", "number", 4, "Seconds to show the finished maze", min=0, max=60),
              *RESTART_PARAMS]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random()
        self.fx = RestartFx(self)
        self.reset()

    def reset(self):
        self.cw, self.ch = (self.w - 1) // 2, (self.h - 1) // 2
        self.grid = Canvas(self.w, self.h, fill=1)
        start = (0, 0)
        self.stack, self.seen = [start], {start}
        self._open(*start)
        self.done_at = None
        self.budget = 0.0

    def _open(self, cx, cy):
        self.grid.set(1 + cx * 2, 1 + cy * 2, 0)

    def render(self, c, now):
        m = self.music
        if self.done_at is None:
            if m.on:  # carves faster with the tempo / loudness, and a spurt on every beat
                self.budget += float(self.p["speed"]) * self.interval * m.speed
                if m.beat:
                    self.budget += float(self.p["speed"]) * 0.15 * min(1.5, m.amount)
            else:
                self.budget += float(self.p["speed"]) * self.interval
            while self.budget >= 1 and self.stack:
                self.budget -= 1
                x, y = self.stack[-1]
                nbrs = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                        if 0 <= x + dx < self.cw and 0 <= y + dy < self.ch and (x + dx, y + dy) not in self.seen]
                if nbrs:
                    nx, ny = self.rng.choice(nbrs)
                    self.grid.set(1 + x + nx, 1 + y + ny, 0)  # knock down the wall between
                    self._open(nx, ny)
                    self.seen.add((nx, ny))
                    self.stack.append((nx, ny))
                else:
                    self.stack.pop()
            if not self.stack:
                self.done_at = now
        elif now - self.done_at > float(self.p["hold"]):
            self.reset()
            self.fx.restarted(now)
        c.px[:] = self.grid.px
        if self.stack:  # the carving head blinks (on the beat, with music)
            x, y = self.stack[-1]
            blink = (m.phase < 0.5) if m.on and m.amount and m.phase is not None else int(now * 8) % 2
            c.set(1 + x * 2, 1 + y * 2, blink)
        self.fx.finish(c, now)
        self.interval = self.fx.interval(0.05)


@register
@reactive
class Lissajous(App):
    id = "lissajous"
    title = "Lissajous"
    category = "Generative"
    description = "Oscilloscope-style curves that slowly change shape."
    params = [P("a", "number", 3, "X frequency", min=1, max=9),
              P("b", "number", 2, "Y frequency", min=1, max=9),
              P("speed", "number", 1.0, min=0.1, max=4, step=0.1)]
    interval = 0.05

    def render(self, c, now):
        m = self.music
        # following the tempo: a quarter turn per beat, in step with it
        t = m.clock("t", now, float(self.p["speed"]), per_beat=math.pi / 2)
        a, b = int(self.p["a"]), int(self.p["b"])
        if m.motion:
            return self.render_music(c, t, a, b, m)
        prev = None
        for k in range(361):
            s = k * 6.2832 / 360
            x = c.w / 2 + (c.w / 2 - 2) * math.sin(a * s + t)
            y = c.h / 2 + (c.h / 2 - 2) * math.sin(b * s)
            if prev:
                c.line(*prev, x, y)
            prev = (x, y)

    def render_music(self, c, t, a, b, m):
        """The mids morph the Y ratio towards the next one up (b -> b+1, still a closed curve); the highs
        do the same for X; the kick squashes it like a speaker cone."""
        wy = min(1.0, 0.6 * m.level("mid") + 0.5 * m.hits("mid"))
        wx = min(1.0, 0.3 * m.level("high") + 0.4 * m.hits("high"))
        ry = (c.h / 2 - 2) * (1 - 0.35 * min(1.0, m.both("bass")))
        rx = c.w / 2 - 2
        prev = None
        for k in range(361):
            s = k * 6.2832 / 360
            x = c.w / 2 + rx * ((1 - wx) * math.sin(a * s + t) + wx * math.sin((a + 1) * s + t))
            y = c.h / 2 + ry * ((1 - wy) * math.sin(b * s) + wy * math.sin((b + 1) * s))
            if prev:
                c.line(*prev, x, y)
            prev = (x, y)


@register
@reactive
class Fire(App):
    id = "fire"
    title = "Fire"
    category = "Generative"
    description = "Flickering flames rising from the bottom."
    params = [P("height", "number", 1.0, "Flame height", min=0.4, max=2, step=0.1)]
    interval = 0.06

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random()
        self.heat = [0] * (self.w * self.h)

    def render(self, c, now):
        w, h, heat, rnd = c.w, c.h, self.heat, self.rng.random
        m = self.music
        tall, flare = 1.0, 0
        if m.motion:  # the bass makes the flames leap (a bass note flares them up); quiet passages burn low
            tall = 1 - 0.45 * m.presence + 0.2 * min(1.5, m.level("bass"))
            flare = int(14 * min(1.5, m.hits("bass")))
            self.interval = max(0.04, 0.06 / max(0.6, min(1.5, m.speed)))
        heat[(h - 1) * w:] = [36 if rnd() < 0.8 else 20 for _ in range(w)]  # 80 % hot
        decay = 1.4 / (float(self.p["height"]) * tall)
        # each dot takes the heat of one of the three dots below it (as they were last frame: the rows are done top
        # down), less a random bit: a row at a time, drawing the random numbers in the same order as ever (so a
        # seeded fire still burns the same way)
        last, xs = w - 1, range(w)
        for y in range(h - 1):
            below = heat[(y + 1) * w:(y + 2) * w]
            heat[y * w:(y + 1) * w] = [max(0, below[min(last, max(0, x + int(rnd() * 3) - 1))] - int(rnd() * decay * 2.2))
                                       for x in xs]
        # heat (0..36) to dots through a 256-entry table per Bayer column phase, on the four interleaved columns
        px = c.px
        # a bass note: every flame flares up at once (shown hotter; the fire itself doesn't change): tables per flare
        luts_for = self._luts if not flare else self._flares.get(flare) or self._flares.setdefault(
            flare, [[bytes(1 if v and (v + flare) * 16 / 36 > BAYER4[r][k] + 0.5 else 0 for v in range(256))
                     for k in range(4)] for r in range(4)])
        for y in range(h):
            luts = luts_for[y % 4]
            row = bytes(heat[y * w:(y + 1) * w])
            px[y * w:(y + 1) * w] = row.translate(luts[0])
            for k in (1, 2, 3):
                px[y * w + k:(y + 1) * w:4] = row[k::4].translate(luts[k])

    _luts = [[bytes(1 if v * 16 / 36 > BAYER4[r][k] + 0.5 else 0 for v in range(256)) for k in range(4)] for r in range(4)]
    _flares = {}  # flare -> the same tables, hotter


# ---- random effects -----------------------------------------------------------------

def _rand_params(app_id, r):
    """Random but good-looking settings for each effect."""
    return {
        "life": lambda: {"density": round(r.uniform(0.15, 0.45), 2), "fps": r.choice([6, 8, 10, 12]),
                         "restart_transition": "random"},
        "rain": lambda: {"density": round(r.uniform(0.3, 1), 1), "speed": round(r.uniform(0.6, 2), 1)},
        "plasma": lambda: {"style": r.choice(["bands", "dither"]), "speed": round(r.uniform(0.5, 2), 1),
                           "scale": round(r.uniform(0.6, 1.8), 1)},
        "bounce": lambda: {"balls": r.randint(2, 8), "gravity": r.random() < 0.7},
        "starfield": lambda: {"stars": r.randint(40, 140), "speed": round(r.uniform(0.6, 2.5), 1)},
        "fireworks": lambda: {"rate": round(r.uniform(0.5, 2.5), 1)},
        "ripples": lambda: {"rate": round(r.uniform(0.8, 3), 1), "rings": r.randint(1, 4)},
        "metaballs": lambda: {"balls": r.randint(3, 7), "outline": r.random() < 0.3},
        "tunnel": lambda: {"shape": r.choice(["circle", "square"]), "speed": round(r.uniform(0.6, 2), 1)},
        "spiral": lambda: {"arms": r.randint(1, 6), "twist": round(r.uniform(0.15, 0.6), 2),
                           "speed": round(r.uniform(1, 4), 1)},
        "maze": lambda: {"speed": r.choice([40, 80, 150]), "hold": r.choice([2, 3, 4]), "restart_transition": "random"},
        "lissajous": lambda: {"a": r.randint(1, 5), "b": r.randint(1, 5), "speed": round(r.uniform(0.4, 1.6), 1)},
        "fire": lambda: {"height": round(r.uniform(0.7, 1.5), 1)},
        "mushrooms": lambda: {"species": r.choice(["mix", "mix", "odd"]), "crowd": r.randint(4, 9),
                              "pace": r.choice([10, 15, 20]), "sizes": r.choice(["mixed", "mixed", "big"])},
        "visualizer": lambda: {"style": r.choice(["rain", "bars", "rings", "wave", "warp", "sparkle"]),
                               "source": "beat", "bpm": r.choice([96, 110, 124, 132]), "song_change": "none"},
    }.get(app_id, dict)()


POOL = ["life", "rain", "plasma", "bounce", "starfield", "fireworks", "ripples", "metaballs",
        "tunnel", "spiral", "maze", "lissajous", "fire", "mushrooms"]
NICE_TRANSITIONS = ["morph", "migrate", "scatter", "life", "dissolve", "fade", "iris-open", "iris-close", "clock",
                    "spiral", "tiles", "drip", "diagonal", "wipe-ragged", "push-left", "push-up", "zoom-in", "vortex",
                    "melt", "pixelate", "glitch", "blinds", "checker"]


class _Child:
    """Runs one app on its own schedule into its own canvas."""

    def __init__(self, app, w, h):
        self.app, self.w, self.h = app, w, h
        self.canvas = Canvas(w, h)
        self.due = 0

    def frame(self, now):
        if now >= self.due:
            c = Canvas(self.w, self.h)
            try:
                self.app.render(c, now)
            except Exception:
                pass
            self.canvas = c
            self.due = now + max(0.02, self.app.interval)
        return self.canvas


@register
@reactive
class RandomEffects(App):
    id = "random_effects"
    title = "Random effects"
    category = "Generative"
    description = "Keeps switching between effects with random settings and random transitions."
    params = [
        P("seconds", "number", 20, "Seconds per effect", min=3, max=600),
        P("transition", "select", "random", "Transition", options=[["random", "Random (a good mix)"], ["none", "None"]]
          + [[t, t] for t in TRANSITIONS if t not in ("none", "random")]),
        P("transition_time", "number", 1.5, "Transition time (s)", min=0.2, max=6, step=0.1, show_if={"transition": {"not": "none"}}),
        P("visualizer", "checkbox", False, "Include music-visualizer styles"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random()
        self.cur = self.prev = self.trans = None
        self.next_at = 0
        self.name = ""
        self.fps = max(5, min(30, float(ctx.store.settings.get("display", {}).get("transition_fps", 15))))

    def _pick(self, now, w, h):
        pool = POOL + (["visualizer"] if self.p["visualizer"] else [])
        pool = [a for a in pool if a in REGISTRY and (self.cur is None or a != self.cur.app.id)]
        app_id = self.rng.choice(pool)
        params = _rand_params(app_id, self.rng)
        if self.p.get("react") and app_id != "visualizer":  # the effect reacts itself; the effects on top stay here
            # the picked app does the motion (if any); the effects on top stay here, on the whole picture
            mode = self.music.mode
            params.update(react_params(self.p), react_mode="motion")
            if mode == "effects":
                params["react"] = False
        app = create(type(self.ctx)(w, h, self.ctx.store), {"app": app_id, "params": params})
        self.name = app.title
        return _Child(app, w, h)

    def render(self, c, now):
        if self.cur is None or (self.trans is None and now >= self.next_at):
            nxt = self._pick(now, c.w, c.h)
            kind = self.p["transition"]
            if kind == "random":
                kind = self.rng.choice(NICE_TRANSITIONS)
            if self.cur is not None and kind != "none":
                self.prev = self.cur
                self.trans = Transition(kind, self.prev.frame(now), c.w, c.h, float(self.p["transition_time"]))
                self.trans.start = now
            elif self.cur is not None:
                self.cur.app.stop()
            self.cur = nxt
            secs = float(self.p["seconds"])
            self.next_at = now + secs * self.rng.uniform(0.8, 1.2)
        frame = self.cur.frame(now)
        if self.trans is not None:
            frame = self.trans.mix(frame, now, self.prev.frame(now))  # both effects keep moving
            if self.trans.done(now):
                self.prev.app.stop()
                self.trans = self.prev = None
        c.px[:] = frame.px
        if self.trans is not None:
            self.interval = 1 / self.fps
        else:
            self.interval = max(0.02, min(self.cur.due - now, self.next_at - now, 0.5))

    def stop(self):
        for ch in (self.cur, self.prev):
            if ch:
                ch.app.stop()

    def describe(self):
        return f"Random effects: {self.name}"
