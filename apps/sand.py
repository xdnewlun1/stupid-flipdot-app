"""Falling sand: an hourglass timer and a free-play sandbox.

One cellular sand engine (Sand) for both. Each grain falls straight down, or slides off
diagonally, so piles keep a natural slope. Tilted, gravity points diagonally (in the
glass's own frame) and grains on the surface also creep sideways to it now and then,
so the sand settles level with the world, leaning against the low wall. Only grains that moved last step
(and the neighbours they uncovered) are looked at; settled sand costs nothing, and at most
MAX_ACTIVE grains move per step, so a Pi 5 stays well under a millisecond a frame.

Hourglass: the neck lets grains through at total/duration per second, so the top bulb
empties exactly when the time is up. Left/right (gamepad stick, phone D-pad) tilts the
glass; A, B or up turns it over. When the sand runs out the board clatters (an avalanche
of flips) and, if asked, raises a notification.
"""

import math
import random

from fonts import NORMAL, SMALL

from . import base
from .base import App, P, register

EMPTY, SAND, WALL, OUT = 0, 1, 2, 3
SHOW = bytes([0, 1, 1, 0]) + bytes(252)  # cell -> dot (the outside of the glass stays dark)
CREEP = 0.3  # tilted: chance a grain moves across the slope in a step (lets the surface level out)
MAX_ACTIVE = 900   # grains moved per step at most; the rest wait their turn
ALARM_S = 3.5      # seconds of avalanche when the time is up


class Sand:
    """Sand in a w x h grid of cells (EMPTY/SAND/WALL/OUT). `gate` is a cell that a grain may
    only enter while gate_credit >= 1 (the hourglass neck); None = no gate."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.grid = bytearray(w * h)
        self.active = set()
        self.tilt = 0  # -1 = tilted left, 1 = right
        self.gate = None
        self.gate_credit = 0.0
        self.passed = 0  # grains through the gate
        self.flip_scan = 1
        self.rng = random.Random()

    def count(self):
        return self.grid.count(SAND)

    def add(self, i):
        if self.grid[i] == EMPTY:
            self.grid[i] = SAND
            self.active.add(i)
            return True
        return False

    def remove(self, i):
        if self.grid[i] == SAND:
            self.grid[i] = EMPTY
            self.wake(i)

    def wake(self, i):
        """Mark the sand around cell i as able to move again."""
        w, grid, act = self.w, self.grid, self.active
        x, y = i % w, i // w
        for dy in (-1, 0, 1):
            yy = y + dy
            if 0 <= yy < self.h:
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if 0 <= xx < w and grid[yy * w + xx] == SAND:
                        act.add(yy * w + xx)

    def wake_all(self):
        self.active = {i for i, v in enumerate(self.grid) if v == SAND}

    def step(self):
        """Move every active grain one cell. Returns how many moved."""
        if not self.active:
            return 0
        w, h, grid, rng = self.w, self.h, self.grid, self.rng
        t = self.tilt
        # furthest along gravity first, so a falling column moves as one; within a row the scan
        # direction alternates, or grains on one side would always get the free cells first
        self.flip_scan = sx = -self.flip_scan
        order = sorted(self.active, key=lambda i: (-((i % w) * t + i // w), sx * (i % w)))
        later = set(order[MAX_ACTIVE:])
        self.active = later
        moved = 0
        arrived = set()
        for i in order[:MAX_ACTIVE]:
            if grid[i] != SAND or i in arrived:
                continue
            x, y = i % w, i // w
            if t:  # gravity (t, 1): straight along it, either 45-degree side, then across it
                a, b = ((t, 0, 1), (0, 1, 1)) if rng.random() < 0.5 else ((0, 1, 1), (t, 0, 1))
                c, d = ((-1, -t, CREEP), (1, t, CREEP)) if rng.random() < 0.5 else ((1, t, CREEP), (-1, -t, CREEP))
                moves = ((t, 1, 1), a, b, c, d)
            elif rng.random() < 0.5:
                moves = ((0, 1, 1), (-1, 1, 1), (1, 1, 1))
            else:
                moves = ((0, 1, 1), (1, 1, 1), (-1, 1, 1))
            for dx, dy, chance in moves:
                xx, yy = x + dx, y + dy
                if not (0 <= xx < w and 0 <= yy < h):
                    continue
                j = yy * w + xx
                if grid[j] != EMPTY:
                    continue
                if chance < 1 and rng.random() >= chance:
                    self.active.add(i)  # could move, didn't this time: stays awake
                    continue
                if j == self.gate:
                    if self.gate_credit < 1:
                        self.active.add(i)  # waiting at the neck: try again next step
                        break
                    self.gate_credit -= 1
                    self.passed += 1
                grid[i], grid[j] = EMPTY, SAND
                arrived.add(j)
                self.active.add(j)
                self.wake(i)
                moved += 1
                break
        return moved

    def mirror(self):
        """Turn the grid upside down (an hourglass turned over)."""
        w = self.w
        rows = [self.grid[y * w:(y + 1) * w] for y in range(self.h)]
        self.grid = bytearray(b"".join(reversed(rows)))
        self.wake_all()


# ---- hourglass ----------------------------------------------------------------------

def glass_shape(w, h):
    """Half-width of the inside of the glass per row (None = not inside), the neck row and centre x.
    Bulbs are rounded at the ends and taper to a one-cell neck in the middle."""
    cx = w // 2
    neck = h // 2 - 1  # rows neck and neck+1 are the narrow middle (mirror images)
    bulb = neck - 1    # rows 1..bulb are the top bulb
    widest = min(13, (w - 4) // 2 - 1)
    half = [None] * h
    for y in range(1, h - 1):
        d = y if y <= neck else h - 1 - y   # distance from the nearest end: 1 at the cap, `neck` at the middle
        t = (d - 1) / max(bulb - 1, 1)      # 0 at the cap, 1 at the neck
        if t >= 1:
            half[y] = 0
            continue
        body = math.cos(t * math.pi / 2) ** 0.7       # full in the bulb, narrowing to the neck
        shoulder = min(1.0, 0.55 + 0.45 * math.sqrt(min(1, (d - 1) / 3)))  # rounded ends
        half[y] = max(0, round(widest * body * shoulder))
    # towards the neck the glass may narrow by at most a cell a row, or sand would rest on a ledge
    for y in range(neck - 1, 0, -1):
        half[y] = min(half[y], half[y + 1] + 1)
        half[h - 1 - y] = half[y]
    return half, neck, cx


@register
class Hourglass(App):
    id = "hourglass"
    title = "Sand hourglass"
    category = "Clocks"
    description = ("A falling-sand hourglass timer that runs out at the set time. Left/right tilts it, "
                   "A, B or up turns it over. When the sand runs out the board clatters.")
    interactive = True
    interval = 0.05
    params = [
        P("minutes", "number", 5, "Duration (minutes)", min=0.25, max=180, step=0.25),
        P("fill", "number", 70, "Sand (% of a bulb)", min=20, max=95),
        P("label", "text", "", "Name (shown when it runs out)"),
        P("show_time", "checkbox", True, "Show the time left beside it"),
        P("on_end", "select", "stay", "When the sand has run out",
          options=[["stay", "Stay empty"], ["turn", "Turn over and start again"]]),
        P("notify", "checkbox", False, "Also raise a notification when it runs out"),
        P("clatter", "checkbox", True, "Clatter when it runs out"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.half, self.neck, self.cx = glass_shape(self.w, self.h)
        self.sand = Sand(self.w, self.h)
        self.build_glass()
        self.total = self.fill_top()
        self.duration = max(1.0, float(self.p["minutes"]) * 60)
        self.rate = self.total / self.duration
        self.sand.gate = self.neck * self.w + self.cx
        self.last = None
        self.tilt = 0
        self.held = set()
        self.alarm_at = None
        self.done = False
        self.flips = 0

    def inside(self, x, y):
        hw = self.half[y] if 0 <= y < self.h else None
        return hw is not None and abs(x - self.cx) <= hw

    def build_glass(self):
        g, w = self.sand.grid, self.w
        for y in range(self.h):
            for x in range(w):
                if self.inside(x, y):
                    continue
                near = any(self.inside(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                g[y * w + x] = WALL if near else OUT

    def fill_top(self):
        """Sand into the top bulb, packed down from the neck. Returns the grain count."""
        cells = [y * self.w + x for y in range(1, self.neck) for x in range(self.w) if self.inside(x, y)]
        cells.sort(key=lambda i: (-(i // self.w), abs(i % self.w - self.cx)))  # a part row fills from the middle
        n = int(len(cells) * float(self.p["fill"]) / 100)
        for i in cells[:n]:
            self.sand.grid[i] = SAND
        self.sand.wake_all()
        return n

    def top_count(self):
        """Grains above the neck."""
        return self.sand.grid[:self.neck * self.w].count(SAND)

    def remaining(self):
        """Seconds until the top bulb is empty."""
        return max(0.0, (self.top_count() - self.sand.gate_credit) / self.rate)

    # ---- input --------------------------------------------------------------------

    def input(self, key, action="down", player=1):
        if action != "down":
            self.held.discard(key)
        else:
            self.held.add(key)
            if key in ("a", "b", "up", "start"):
                self.turn_over()
        tilt = ("right" in self.held) - ("left" in self.held)
        if tilt != self.tilt:
            self.tilt = tilt
            self.sand.tilt = tilt
            self.sand.wake_all()

    def turn_over(self):
        self.sand.mirror()
        self.sand.gate_credit = 0
        self.alarm_at = None
        self.done = False

    # ---- frames -------------------------------------------------------------------

    def render(self, c, now):
        dt = 0.0 if self.last is None else min(0.5, max(0.0, now - self.last))
        self.last = now
        s = self.sand
        if self.alarm_at is None:
            s.gate_credit = min(s.gate_credit + dt * self.rate, 3.0)
        s.step()
        if not self.done and self.top_count() == 0:
            self.done, self.alarm_at = True, now
            self.finished()
        c.px[:] = s.grid.translate(SHOW)
        if self.p["show_time"]:
            self.draw_time(c, now)
        if self.alarm_at is not None and self.p["clatter"]:
            self.avalanche(c, now - self.alarm_at)
        if self.alarm_at is not None and now - self.alarm_at > ALARM_S and self.p["on_end"] == "turn" and not s.active:
            self.turn_over()

    def draw_time(self, c, now):
        left = self.remaining()
        if self.done:
            text = "TIME"
        else:
            secs = math.ceil(left)
            text = f"{secs // 3600}:{secs // 60 % 60:02d}" if secs >= 3600 else f"{secs // 60}:{secs % 60:02d}"
        room = self.cx - (max(h for h in self.half if h is not None) + 2)
        font = NORMAL if NORMAL.width(text) <= room - 2 else SMALL
        x = max(0, (room - font.width(text)) // 2)
        c.text(x, (c.h - font.height) // 2, text, font)
        if self.p["label"]:
            lab = str(self.p["label"]).upper()[:6]
            c.text(self.w - room + max(0, (room - SMALL.width(lab)) // 2), (c.h - 5) // 2, lab, SMALL)

    def avalanche(self, c, t):
        """Three bands of sand pouring down the board: loud on purpose."""
        if t > ALARM_S:
            return
        band, speed = 8, c.h * 3 / ALARM_S
        front = t * speed
        for k in range(3):
            y0 = int(front - k * c.h) - band
            for y in range(max(0, y0), min(c.h, y0 + band)):
                row = y * c.w
                for x in range((y + k) % 2, c.w, 2):
                    c.px[row + x] ^= 1

    def finished(self):
        if not self.p["notify"] or base.notify is None:
            return
        text = (str(self.p["label"]).strip() or "Time is up")[:40]
        spec = {"app": "text", "params": {"text": text, "style": "static", "fit": True}}
        try:
            try:  # high: the hourglass takes input, so a normal notification would wait until it's closed
                base.notify(spec=spec, duration=8, transition="dissolve", priority="high", key="hourglass", text=text)
            except TypeError:  # a runner without the notification queue
                base.notify(spec=spec, duration=8, transition="dissolve")
        except Exception as e:
            base.log.warning("hourglass: notification failed: %s", e)

    def describe(self):
        if self.done:
            return f"Sand hourglass (run out{', ' + self.p['label'] if self.p['label'] else ''})"
        secs = math.ceil(self.remaining())
        return f"Sand hourglass ({secs // 60}:{secs % 60:02d} left of {float(self.p['minutes']):g} min)"


# ---- sandbox ----------------------------------------------------------------------

TOOLS = ("sand", "wall", "erase")


@register
class Sandbox(App):
    id = "sandbox"
    title = "Sandbox"
    category = "Games"
    description = ("Falling sand to play with. Arrows move the cursor, hold A to use the tool, B switches "
                   "tool (sand, wall, eraser), Start clears. Left alone it pours by itself.")
    interactive = True
    interval = 0.05
    params = [
        P("max_grains", "number", 1500, "Most grains at once", min=100, max=3000, step=100),
        P("demo", "checkbox", True, "Pour by itself when nobody is playing"),
    ]
    IDLE = 20  # seconds before the self-pouring starts

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.sand = Sand(self.w, self.h)
        self.cx, self.cy = self.w // 2, 4
        self.tool = 0
        self.held = set()
        self.moved_at = 0
        self.touched = None
        self.tool_at = -10
        self.now = 0
        self.spouts = []

    def input(self, key, action="down", player=1):
        self.touched = self.now
        if action != "down":
            self.held.discard(key)
            return
        self.held.add(key)
        if key == "b":
            self.tool = (self.tool + 1) % len(TOOLS)
            self.tool_at = self.now
        elif key == "start":
            self.sand = Sand(self.w, self.h)
        elif key in ("up", "down", "left", "right"):
            self.move(key)
            self.moved_at = self.now + 0.25  # first repeat after a short pause

    def move(self, key):
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[key]
        self.cx = min(max(self.cx + dx, 0), self.w - 1)
        self.cy = min(max(self.cy + dy, 0), self.h - 1)

    def brush(self):
        return [(self.cx + dx, self.cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if 0 <= self.cx + dx < self.w and 0 <= self.cy + dy < self.h]

    def use_tool(self):
        s, w = self.sand, self.w
        tool = TOOLS[self.tool]
        if tool == "sand":
            room = int(self.p["max_grains"]) - s.count()
            spots = [(x, y) for x, y in self.brush() if s.grid[y * w + x] == EMPTY]
            for x, y in random.sample(spots, min(len(spots), 2, max(room, 0))):
                s.add(y * w + x)
        elif tool == "wall":
            for x, y in self.brush():
                if s.grid[y * w + x] == EMPTY:
                    s.grid[y * w + x] = WALL
        else:
            for x, y in self.brush():
                i = y * w + x
                if s.grid[i] != EMPTY:
                    s.grid[i] = EMPTY
                    s.wake(i)

    def self_pour(self, now):
        """Idle: a few spouts pour from the top until the board is a third full, then it drains."""
        s, w = self.sand, self.w
        if not self.spouts or random.random() < 0.004:
            self.spouts = [random.randrange(4, w - 4) for _ in range(random.randint(1, 3))]
        if s.count() < min(int(self.p["max_grains"]), w * self.h // 3):
            for x in self.spouts:
                s.add(x + random.choice((-1, 0, 1)))
        elif random.random() < 0.5:
            # open the floor a little: the oldest pile drains away
            bottom = (self.h - 1) * w
            for x in random.sample(range(w), 6):
                s.remove(bottom + x)

    def render(self, c, now):
        self.now = now
        if self.touched is None:
            self.touched = now - self.IDLE * 0.75
        idle = now - self.touched > self.IDLE
        for key in ("up", "down", "left", "right"):
            if key in self.held and now >= self.moved_at:
                self.move(key)
                self.moved_at = now + 0.06
        if "a" in self.held:
            self.use_tool()
        if idle and self.p["demo"]:
            self.self_pour(now)
        self.sand.step()
        c.px[:] = self.sand.grid.translate(SHOW)
        if not idle and int(now * 3) % 2:
            c.px[self.cy * self.w + self.cx] ^= 1
        if now - self.tool_at < 1.5:
            name = TOOLS[self.tool].upper()
            c.rect(0, 0, SMALL.width(name) + 2, 7, v=0, fill=True)
            c.text(1, 1, name, SMALL)

    def describe(self):
        return f"Sandbox ({self.sand.count()} grains, tool: {TOOLS[self.tool]})"
