"""Arcade pack 2: Dig Dug, Defender, Joust, Marble Madness and Ghosts vs Pac-Man.

Built on the classic arcade base (apps/pack_arcade.py Classic: fixed 40 Hz physics, a self-playing demo once the
title has sat idle, A plays again on the game-over screen, high scores with initials). Ghosts vs Pac-Man is the
Pac-Man from apps/pack_maze.py turned round: the players drive the ghosts.
"""

import math
import random
from collections import deque

from fonts import NORMAL, SMALL

from .arcade import STEP, fill, sprite
from .base import P, register
from .pack_arcade import DIFFICULTY, LIVES, Classic, boom, disc, label, lives_icons
from .pack_maze import (DIRS, DOOR, EYES, GHOST, PCOLS, POWER, PT, PY, SCARED, PacMan, banner, facings, put, shape,
                        travel)


def box_hit(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# ---- Dig Dug ---------------------------------------------------------------------------------------------------

DD_TOP = 6                     # the HUD sits above the dirt
DD_CELL = 3                    # dots per cell
DD_COLS, DD_ROWS = 28, 12      # 84 x 36 dots of ground
DIGGER = facings([".XX", "XXX", "X.X"])
POOKA = (shape(["XXX", "X.X", "XXX"]), shape(["XXX", "XXX", "X.X"]))
FYGAR = facings(["XX.", "XXX", "X.X"])
ROCK = shape(["XXX", "XXX", "XXX"])
ROCK_CRACKED = shape(["X.X", ".X.", "X.X"])
BLOAT = [shape(["XXX", "XXX", "XXX"]),
         shape([".XXX.", "X...X", "X...X", "X...X", ".XXX."]),
         shape([".XXX.", "XXXXX", "XXXXX", "XXXXX", ".XXX."])]
LAYER_POINTS = (200, 300, 400, 500)
DD_LIFE = ["XX.", "XXX", "X.X"]


def _dd_dither(w, h):
    """The ground: a checker of dots that reads as packed dirt."""
    bg = bytearray(w * h)
    for y in range(DD_TOP, h):
        for x in range(w):
            if (x + y) % 2 == 0:
                bg[y * w + x] = 1
    return bytes(bg)


@register
class DigDug(Classic):
    id = "digdug"
    title = "Dig Dug"
    description = ("Dig tunnels through the dirt, pump the Pookas and Fygars up until they pop (A, again and again), "
                   "and dig under rocks to drop them on whatever is below. Deeper pops are worth more; a Fygar "
                   "breathes fire along its tunnel.")
    params = [LIVES, DIFFICULTY]
    DEMO_X, DEMO_Y = -1, 0

    def reset(self):
        self.score, self.round = 0, 1
        self.lives = int(self.p["lives"])
        self.new_round()

    def new_round(self):
        self.dirt = bytearray(b"\x01" * (DD_COLS * DD_ROWS))
        for x in range(DD_COLS):
            self.dirt[x] = 0  # the sky
        self.rocks, self.enemies = [], []
        n = min(3 + self.round, 7)
        spots = [(x, y) for y in range(2, DD_ROWS - 1, 3) for x in range(2, DD_COLS - 2, 6)]
        random.shuffle(spots)
        for i, (x, y) in enumerate(spots[:n]):
            kind = "fygar" if i % 3 == 2 or (self.round > 2 and i % 2) else "pooka"
            for k in range(-1, 2):  # a short tunnel to start in
                self.dirt[y * DD_COLS + x + k] = 0
            self.enemies.append({"x": float(x), "y": float(y), "kind": kind, "dir": random.choice([(-1, 0), (1, 0)]),
                                 "mode": "tunnel", "bloat": 0, "bloat_t": 0.0, "ghost_t": 0.0, "fire": 0.0, "pop": 0.0})
        rocks = 0
        for x, y in spots[n:]:
            if rocks < 2 + self.round // 2 and y >= 3 and not any(abs(e["x"] - x) < 3 and abs(e["y"] - y) < 2 for e in self.enemies):
                self.rocks.append({"x": x, "y": y, "state": "set", "t": 0.0})
                rocks += 1
        self.new_life()
        self.clear_t = 0.0

    def new_life(self):
        self.px, self.py = float(DD_COLS // 2), 0.0
        self.pdir, self.face = (0, 1), (1, 0)
        self.pump = None  # {"dir", "len", "hooked": enemy or None, "t"}
        self.dead_t = 0.0
        self.safe_until = self.now + 1.0
        for k in range(1, 4):  # the first shaft down
            self.dig(int(self.px), k)
        self.py = 0.0

    @property
    def speed(self):
        return 5.0 * self.pace

    def dig(self, cx, cy):
        if 0 <= cx < DD_COLS and 0 <= cy < DD_ROWS and self.dirt[cy * DD_COLS + cx]:
            self.dirt[cy * DD_COLS + cx] = 0
            if cy > 0:
                self.score += 10
            return True
        return False

    def is_open(self, cx, cy):
        return 0 <= cx < DD_COLS and 0 <= cy < DD_ROWS and not self.dirt[cy * DD_COLS + cx]

    def rock_at(self, cx, cy):
        for r in self.rocks:
            if r["x"] == cx and r["y"] == cy and r["state"] != "gone":
                return r
        return None

    def press(self, key, player):
        if key in ("a", "b") and not self.dead_t and self.state == "playing":
            self.do_pump()

    def do_pump(self):
        if self.pump is None:
            self.pump = {"dir": self.face, "len": 0.0, "hooked": None, "t": self.now}
            return
        self.pump["t"] = self.now
        e = self.pump["hooked"]
        if e is not None:
            e["bloat"] += 1
            e["bloat_t"] = self.now
            if e["bloat"] >= 4:
                e["pop"] = self.now
                layer = min(3, int((e["y"] - 1) // 3))
                pts = LAYER_POINTS[layer] * (2 if e["kind"] == "fygar" and self.face[1] == 0 else 1)
                self.score += pts
                self.pump = None

    # ---- movement ----

    def move_player(self):
        held = self.held_keys()
        want = None
        for k in ("up", "down", "left", "right"):
            if k in held:
                want = DIRS[k]
        at_cell = abs(self.px - round(self.px)) < 1e-6 and abs(self.py - round(self.py)) < 1e-6
        if want is None:
            if self.pump is None:
                return
        if want is not None and self.pump is not None and want != self.face:
            self.pump = None  # moving lets go of the pump
        if want is None:
            return
        if at_cell:
            cx, cy = round(self.px), round(self.py)
            nx, ny = cx + want[0], cy + want[1]
            if not (0 <= nx < DD_COLS and 0 <= ny < DD_ROWS) or self.rock_at(nx, ny):
                self.face = want
                return
            self.pdir, self.face = want, want
            self.dig(nx, ny)
        elif want != self.pdir and want != (-self.pdir[0], -self.pdir[1]):
            pass  # a turn: carry on to the next cell centre first, then turn there
        else:
            self.pdir = want
            self.face = want
        d = self.speed * STEP
        nx, ny = self.px + self.pdir[0] * d, self.py + self.pdir[1] * d
        # don't overshoot the next cell centre, and dig the cell being entered
        tx, ty = round(self.px + self.pdir[0] * 0.5001), round(self.py + self.pdir[1] * 0.5001)
        if self.pdir[0] and (nx - tx) * self.pdir[0] > 0:
            nx = float(tx)
        if self.pdir[1] and (ny - ty) * self.pdir[1] > 0:
            ny = float(ty)
        self.px, self.py = max(0.0, min(DD_COLS - 1.0, nx)), max(0.0, min(DD_ROWS - 1.0, ny))
        self.dig(round(self.px), round(self.py))

    def path_dir(self, e):
        """BFS through the tunnels from the enemy's cell towards the player: the first step, or None."""
        start = (round(e["x"]), round(e["y"]))
        goal = (round(self.px), round(self.py))
        if start == goal:
            return None
        seen = {start}
        todo = deque()
        for d in ((0, -1), (-1, 0), (0, 1), (1, 0)):
            n = (start[0] + d[0], start[1] + d[1])
            if self.is_open(*n) and not self.rock_at(*n):
                seen.add(n)
                todo.append((n, d))
        steps = 0
        while todo and steps < 400:
            steps += 1
            (x, y), first = todo.popleft()
            if (x, y) == goal:
                return first
            for d in ((0, -1), (-1, 0), (0, 1), (1, 0)):
                n = (x + d[0], y + d[1])
                if n not in seen and self.is_open(*n) and not self.rock_at(*n):
                    seen.add(n)
                    todo.append((n, first))
        return None

    def move_enemy(self, e):
        if e["pop"] or (self.pump and self.pump["hooked"] is e):
            return
        if e["bloat"] and self.now - e["bloat_t"] > 0.6:
            e["bloat"] -= 1
            e["bloat_t"] = self.now
            return
        if e["fire"]:
            if self.now - e["fire"] > 0.7:
                e["fire"] = 0.0
            return
        speed = (2.2 + 0.25 * min(self.round, 6)) * self.pace
        at_cell = abs(e["x"] - round(e["x"])) < 1e-6 and abs(e["y"] - round(e["y"])) < 1e-6
        if e["mode"] == "ghost":
            speed = 1.4
            dx, dy = self.px - e["x"], self.py - e["y"]
            dist = math.hypot(dx, dy) or 1
            e["x"] += dx / dist * speed * STEP
            e["y"] += dy / dist * speed * STEP
            e["dir"] = (1, 0) if dx > 0 else (-1, 0)
            cx, cy = round(e["x"]), round(e["y"])
            if self.now - e["ghost_t"] > 2 and self.is_open(cx, cy) and not self.rock_at(cx, cy):
                e["x"], e["y"], e["mode"] = float(cx), float(cy), "tunnel"
            return
        if at_cell:
            d = self.path_dir(e)
            if d is None:
                if random.random() < 0.02 * self.pace:
                    e["mode"], e["ghost_t"] = "ghost", self.now
                    return
                opts = [d for d in ((0, -1), (-1, 0), (0, 1), (1, 0))
                        if self.is_open(round(e["x"]) + d[0], round(e["y"]) + d[1]) and not self.rock_at(round(e["x"]) + d[0], round(e["y"]) + d[1])]
                if not opts:
                    return
                d = e["dir"] if e["dir"] in opts and random.random() < 0.8 else random.choice(opts)
            elif random.random() < 0.004 and self.round > 1:
                e["mode"], e["ghost_t"] = "ghost", self.now
                return
            e["dir"] = d
            if e["kind"] == "fygar" and round(e["y"]) == round(self.py) and 0 < (self.px - e["x"]) * d[0] <= 3.5 and d[1] == 0 \
                    and random.random() < 0.25:
                e["fire"] = self.now
                return
        d = e["dir"]
        step = speed * STEP
        tx, ty = round(e["x"] + d[0] * 0.5001), round(e["y"] + d[1] * 0.5001)
        nx, ny = e["x"] + d[0] * step, e["y"] + d[1] * step
        if d[0] and (nx - tx) * d[0] > 0:
            nx = float(tx)
        if d[1] and (ny - ty) * d[1] > 0:
            ny = float(ty)
        e["x"], e["y"] = nx, ny

    def move_rocks(self):
        for r in self.rocks:
            if r["state"] == "gone":
                continue
            if r["state"] == "set":
                below = (r["x"], r["y"] + 1)
                if r["y"] + 1 < DD_ROWS and self.is_open(*below) and not self.rock_at(*below) \
                        and not (round(self.px) == r["x"] and round(self.py) == r["y"] + 1):
                    r["state"], r["t"] = "wobble", self.now
            elif r["state"] == "wobble":
                if self.now - r["t"] > 0.6:
                    r["state"], r["fy"] = "falling", float(r["y"])
            elif r["state"] == "falling":
                r["fy"] += 7 * STEP
                r["y"] = int(r["fy"] + 0.5)
                for e in self.enemies:  # anything under a falling rock is crushed
                    if not e["pop"] and abs(e["x"] - r["x"]) < 0.6 and abs(e["y"] - r["fy"]) < 0.8:
                        e["pop"] = self.now
                        self.score += 1000
                if abs(self.px - r["x"]) < 0.6 and abs(self.py - r["fy"]) < 0.8 and not self.dead_t:
                    self.die()
                nxt = (r["x"], r["y"] + 1)
                if r["y"] + 1 >= DD_ROWS or not self.is_open(*nxt) or self.rock_at(*nxt):
                    r["state"], r["t"] = "cracked", self.now
            elif r["state"] == "cracked" and self.now - r["t"] > 0.5:
                r["state"] = "gone"

    def die(self):
        if self.now < self.safe_until or self.demo:  # the demo digger shrugs it off so the demo keeps going
            return
        self.dead_t = self.now
        self.pump = None

    def step(self):
        if self.clear_t:
            if self.now - self.clear_t > 1.8:
                self.round += 1
                self.new_round()
            return
        if self.dead_t:
            if self.now - self.dead_t > 1.2:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.new_life()
            return
        self.move_player()
        if self.pump is not None:
            pm = self.pump
            pm["len"] = min(3.0, pm["len"] + 20 * STEP)
            if pm["hooked"] is None:
                for k in range(1, int(pm["len"]) + 1):
                    cx, cy = round(self.px) + pm["dir"][0] * k, round(self.py) + pm["dir"][1] * k
                    if not self.is_open(cx, cy) or self.rock_at(cx, cy):
                        pm["len"] = min(pm["len"], k - 0.01)
                        break
                    for e in self.enemies:
                        if not e["pop"] and e["mode"] == "tunnel" and abs(e["x"] - cx) < 0.6 and abs(e["y"] - cy) < 0.6:
                            pm["hooked"] = e
                            e["bloat"] = max(e["bloat"], 1)
                            e["bloat_t"] = self.now
                            break
                    if pm["hooked"]:
                        break
            if self.now - pm["t"] > 1.0:
                self.pump = None
        for e in self.enemies:
            self.move_enemy(e)
            if e["pop"]:
                continue
            if abs(e["x"] - self.px) < 0.7 and abs(e["y"] - self.py) < 0.7 and not (self.pump and self.pump["hooked"] is e):
                self.die()
            if e["fire"] and round(e["y"]) == round(self.py):
                span = min(3, int((self.now - e["fire"]) * 6) + 1)
                if 0 < (self.px - e["x"]) * e["dir"][0] <= span + 0.4:
                    self.die()
        self.enemies = [e for e in self.enemies if not e["pop"] or self.now - e["pop"] < 0.4]
        self.move_rocks()
        if not any(not e["pop"] for e in self.enemies):
            self.clear_t = self.now

    # ---- demo ----

    def autopilot(self):
        if self.dead_t or self.clear_t:
            return self.hold([])
        live = [e for e in self.enemies if not e["pop"]]
        if not live:
            return self.hold([])
        if self.pump and self.pump["hooked"] is not None:
            self.hold([])
            if self.now - self.pump["t"] > 0.12:
                self.press("a", 1)
            return
        px, py = self.px, self.py
        e = min(live, key=lambda e: abs(e["x"] - px) + abs(e["y"] - py))
        dx, dy = e["x"] - px, e["y"] - py
        if abs(dy) < 0.5 and 0 < abs(dx) <= 3.2:
            face = (1, 0) if dx > 0 else (-1, 0)
            if self.face != face:
                return self.hold(["right" if dx > 0 else "left"])
            self.hold([])
            if self.pump is None or self.now - self.pump["t"] > 0.2:
                self.press("a", 1)
            return
        if abs(dx) < 0.5 and 0 < abs(dy) <= 3.2:
            face = (0, 1) if dy > 0 else (0, -1)
            if self.face != face:
                return self.hold(["down" if dy > 0 else "up"])
            self.hold([])
            if self.pump is None or self.now - self.pump["t"] > 0.2:
                self.press("a", 1)
            return
        if abs(dx) + abs(dy) < 1.5:  # too close to pump: back off along the tunnel
            return self.hold(["left" if dx > 0 else "right"] if abs(dx) > abs(dy) else ["up" if dy > 0 else "down"])
        if abs(dy) > abs(dx) or (abs(dx) < 0.5):
            return self.hold(["down" if dy > 0 else "up"])
        self.hold(["right" if dx > 0 else "left"])

    # ---- drawing ----

    def draw(self, c, now):
        bg = getattr(self, "_bg", None)
        if bg is None:
            bg = self._bg = _dd_dither(c.w, c.h)
        c.px[:] = bg
        W = c.w
        for cy in range(DD_ROWS):
            for cx in range(DD_COLS):
                if not self.dirt[cy * DD_COLS + cx]:
                    fill(c, cx * DD_CELL, DD_TOP + cy * DD_CELL, DD_CELL, DD_CELL, 0)
        for r in self.rocks:
            if r["state"] == "gone":
                continue
            x, y = r["x"] * DD_CELL, DD_TOP + r["y"] * DD_CELL
            if r["state"] == "wobble":
                x += 1 if int(now * 12) % 2 else -1
            fill(c, x - 1, y - 1, DD_CELL + 2, DD_CELL + 2, 0)
            put(c, x, y, ROCK_CRACKED if r["state"] == "cracked" else ROCK)
        if self.pump is not None:
            pm = self.pump
            for k in range(1, int(pm["len"]) + 1):
                cx, cy = round(self.px) + pm["dir"][0] * k, round(self.py) + pm["dir"][1] * k
                c.set(cx * DD_CELL + 1, DD_TOP + cy * DD_CELL + 1)
        for e in self.enemies:
            x, y = round(e["x"] * DD_CELL), DD_TOP + round(e["y"] * DD_CELL)
            if e["pop"]:
                boom(c, x + 1, y + 1, now - e["pop"])
                continue
            if e["bloat"]:
                k = min(e["bloat"], 3) - 1
                off = 0 if k == 0 else -1
                fill(c, x + off - 1, y + off - 1, (3 if k == 0 else 5) + 2, (3 if k == 0 else 5) + 2, 0)
                put(c, x + off, y + off, BLOAT[k])
                continue
            fill(c, x - 1, y - 1, DD_CELL + 2, DD_CELL + 2, 0)
            if e["mode"] == "ghost" and int(now * 8) % 2:
                continue
            if e["kind"] == "pooka":
                put(c, x, y, POOKA[int(now * 4) % 2])
            else:
                put(c, x, y, FYGAR[e["dir"]])
                if e["fire"]:
                    span = min(3, int((now - e["fire"]) * 6) + 1)
                    for k in range(1, span + 1):
                        fx = x + e["dir"][0] * k * DD_CELL
                        fill(c, fx, y, DD_CELL, DD_CELL, 0)
                        c.set(fx + 1, y + 1)
                        c.set(fx + (1 + k) % 3, y + (k % 2) * 2)
        x, y = round(self.px * DD_CELL), DD_TOP + round(self.py * DD_CELL)
        fill(c, x - 1, y - 1, DD_CELL + 2, DD_CELL + 2, 0)
        if self.dead_t:
            if int((now - self.dead_t) * 8) % 2:
                put(c, x, y, DIGGER[self.face])
        elif now >= self.safe_until or int(now * 8) % 2:
            put(c, x, y, DIGGER[self.face])
        fill(c, 0, 0, W, DD_TOP, 0)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, f"R{self.round}", SMALL)
        lives_icons(c, self.lives - 1, DD_LIFE)
        if self.clear_t:
            banner(c, 20, "ROUND CLEAR")

    def draw_title(self, c, now):
        y = 26
        for x in range(0, c.w, 2):
            if (x // 2 + int(now * 6)) % 7 not in (3, 4):
                c.set(x, y)
                c.set(x + 1, y + 2)
        put(c, 40, 23, DIGGER[(1, 0)])
        put(c, 58, 23, POOKA[int(now * 4) % 2])

    def describe(self):
        if self.state == "title":
            return "Dig Dug (title)"
        return f"Dig Dug ({'demo' if self.demo else self.state}, round {getattr(self, 'round', 1)}, score {self.score})"


# ---- Defender --------------------------------------------------------------------------------------------------

DF_WORLD = 84 * 4   # the planet wraps round after four screens
DF_TOP = 8          # minimap (row 0), a gap, then the HUD; the sky starts here
DF_SHIP = {1: ["XX...", "XXXXX", "..XX."], -1: ["...XX", "XXXXX", ".XX.."]}
DF_LANDER = (["X.X.X", ".XXX.", "X...X"], ["X.X.X", "XXXXX", ".X.X."])
DF_MUTANT = (["X...X", ".XXX.", "XX.XX"], [".X.X.", "XXXXX", "X...X"])
DF_BOMBER = (["XXXXX", "X.X.X", "XXXXX"], ["XXXXX", ".X.X.", "XXXXX"])
DF_MAN = ["X", "X", "XX"]
DF_LIFE = ["XX..", "XXXX"]


@register
class Defender(Classic):
    id = "defender"
    title = "Defender"
    description = ("Fly left and right over a planet four screens wide (it wraps). A fires, B drops a smart bomb "
                   "(everything on screen). Landers pick up the humanoids and carry them to the top: shoot the lander "
                   "and catch the humanoid before it falls too far. The top row is the long-range scanner.")
    params = [LIVES, DIFFICULTY]
    DEMO_X, DEMO_Y = None, DF_TOP

    def reset(self):
        self.score, self.wave = 0, 1
        self.lives = int(self.p["lives"])
        self.bombs = 3
        self.booms = []
        self.flash_t = 0.0
        self.terrain = [0] * DF_WORLD
        h = 6
        for x in range(DF_WORLD):
            if x % 12 == 0:
                h = max(3, min(11, h + random.choice([-4, -2, 0, 2, 4])))
            self.terrain[x] = h
        self.ground_y = 41
        self.men = [{"wx": float(i * DF_WORLD / 8 + random.uniform(0, 20)), "state": "ground", "y": 0.0, "vy": 0.0, "by": None}
                    for i in range(8)]
        self.new_wave()
        self.new_life()

    def new_wave(self):
        self.enemies = []
        n = min(4 + self.wave * 2, 14)
        for i in range(n):
            kind = "bomber" if self.wave >= 2 and i % 4 == 3 else "lander"
            self.enemies.append({"kind": kind, "wx": random.uniform(0, DF_WORLD), "y": random.uniform(DF_TOP + 2, 24),
                                 "vx": random.choice([-1, 1]) * 8.0, "vy": 0.0, "carry": None, "t": 0.0})
        self.shots, self.bullets, self.mines = [], [], []
        self.wave_t = self.now

    def new_life(self):
        self.wx, self.y = DF_WORLD / 4.0, 24.0
        self.vx, self.vy = 0.0, 0.0
        self.facing = 1
        self.cam = self.wx - 24
        self.dead_t = 0.0
        self.safe_until = self.now + 2.0
        self.fire_at = 0.0
        self.bullets = []

    def press(self, key, player):
        if self.dead_t:
            return
        if key == "a":
            self.fire()
        elif key == "b":
            self.smart_bomb()

    def fire(self):
        if self.now - self.fire_at < 0.12:
            return
        self.fire_at = self.now
        self.shots.append({"wx": self.wx + (5 if self.facing > 0 else -1), "y": self.y + 1, "dir": self.facing, "len": 2.0,
                           "t": self.now})

    def smart_bomb(self):
        if self.bombs <= 0:
            return
        self.bombs -= 1
        for e in self.enemies:
            if self.on_screen(e["wx"]):
                self.kill(e)
        self.mines = [m for m in self.mines if not self.on_screen(m[0])]
        self.flash_t = self.now

    def sx(self, wx):
        """Screen x of a world x, wrapping."""
        return (wx - self.cam + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2

    def on_screen(self, wx):
        return -6 <= self.sx(wx) < 90

    def kill(self, e):
        if e in self.enemies:
            self.enemies.remove(e)
        self.score += {"lander": 150, "mutant": 150, "bomber": 250}[e["kind"]]
        if e["carry"] is not None:
            m = e["carry"]
            m["state"], m["vy"], e["carry"] = "falling", 0.0, None
        self.booms.append((e["wx"], e["y"] + 1, self.now))

    def die(self):
        if self.now < self.safe_until or self.dead_t:
            return
        self.dead_t = self.now
        self.booms.append((self.wx + 2, self.y + 1, self.now))

    def step(self):
        self.booms = [b for b in self.booms if self.now - b[2] < 0.4]
        if self.dead_t:
            if self.now - self.dead_t > 1.5:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.new_life()
            return
        held = self.held_keys()
        if "left" in held:
            self.facing = -1
            self.vx -= 90 * STEP
        if "right" in held:
            self.facing = 1
            self.vx += 90 * STEP
        if "left" not in held and "right" not in held:
            self.vx *= 1 - 1.2 * STEP
        self.vx = max(-60.0, min(60.0, self.vx))
        if "up" in held:
            self.y -= 30 * STEP
        if "down" in held:
            self.y += 30 * STEP
        self.y = max(DF_TOP + 1.0, min(self.ground_y - 4.0, self.y))
        self.wx = (self.wx + self.vx * STEP) % DF_WORLD
        want = self.wx - (24 if self.facing > 0 else 55)
        d = (want - self.cam + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
        self.cam = (self.cam + d * min(1.0, 3.0 * STEP)) % DF_WORLD
        if "a" in held:
            self.fire()
        pace = self.pace
        # enemies
        for e in self.enemies[:]:
            e["t"] += STEP
            if e["kind"] == "lander":
                self.move_lander(e, pace)
            elif e["kind"] == "mutant":
                dx = (self.wx - e["wx"] + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
                e["vx"] += (18 if dx > 0 else -18) * STEP * pace + random.uniform(-6, 6) * STEP
                e["vx"] = max(-40 * pace, min(40 * pace, e["vx"]))
                e["y"] += (self.y - e["y"]) * 1.5 * STEP + random.uniform(-0.6, 0.6)
                e["wx"] = (e["wx"] + e["vx"] * STEP) % DF_WORLD
            else:  # bomber: drifts along laying mines
                e["wx"] = (e["wx"] + e["vx"] * STEP * pace) % DF_WORLD
                e["y"] += math.sin(e["t"] * 2) * 0.1
                if random.random() < 0.02 and self.on_screen(e["wx"]):
                    self.mines.append([e["wx"], e["y"] + 2, self.now])
            e["y"] = max(DF_TOP + 1.0, min(self.ground_y - 6.0, e["y"]))
            if self.on_screen(e["wx"]) and random.random() < 0.0025 * pace and e["kind"] != "bomber":
                dx = (self.wx - e["wx"] + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
                dist = math.hypot(dx, self.y - e["y"]) or 1
                self.bullets.append([e["wx"], e["y"] + 1, dx / dist * 24, (self.y - e["y"]) / dist * 24])
            if self.on_screen(e["wx"]) and box_hit(self.sx(e["wx"]), e["y"], 5, 3, self.sx(self.wx), self.y, 5, 3):
                self.die()
        self.mines = [m for m in self.mines if self.now - m[2] < 20]
        for m in self.mines:
            if self.on_screen(m[0]) and box_hit(self.sx(m[0]), m[1], 1, 1, self.sx(self.wx), self.y, 5, 3):
                self.die()
        for b in self.bullets[:]:
            b[0] = (b[0] + b[2] * STEP) % DF_WORLD
            b[1] += b[3] * STEP
            if not (DF_TOP <= b[1] < self.ground_y) or not self.on_screen(b[0]):
                self.bullets.remove(b)
            elif box_hit(self.sx(b[0]), b[1], 1, 1, self.sx(self.wx), self.y, 5, 3):
                self.bullets.remove(b)
                self.die()
        # shots
        for s in self.shots[:]:
            s["len"] = min(30.0, s["len"] + 160 * STEP)
            s["wx"] = (s["wx"] + s["dir"] * 60 * STEP) % DF_WORLD
            if self.now - s["t"] > 0.35:
                self.shots.remove(s)
                continue
            x0 = s["wx"] if s["dir"] > 0 else s["wx"] - s["len"]
            for e in self.enemies[:]:
                dx = (e["wx"] - x0 + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
                if -5 < dx < s["len"] and e["y"] <= s["y"] < e["y"] + 3:
                    self.kill(e)
                    if s in self.shots:
                        self.shots.remove(s)
                    break
            for m in self.mines[:]:
                dx = (m[0] - x0 + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
                if -1 < dx < s["len"] and abs(m[1] - s["y"]) < 1.5:
                    self.mines.remove(m)
        # humanoids
        for m in self.men:
            if m["state"] == "falling":
                m["vy"] += 40 * STEP
                m["y"] += m["vy"] * STEP
                gy = self.ground_y - self.terrain[int(m["wx"]) % DF_WORLD] - 3
                if self.on_screen(m["wx"]) and box_hit(self.sx(m["wx"]), m["y"], 2, 3, self.sx(self.wx) - 1, self.y - 1, 7, 5):
                    m["state"], m["by"] = "caught", None
                    self.score += 500
                elif m["y"] >= gy:
                    m["y"] = gy
                    if m["vy"] > 28:
                        m["state"] = "dead"
                    else:
                        m["state"] = "ground"
                        self.score += 250
            elif m["state"] == "caught":
                m["wx"], m["y"] = self.wx + 2, self.y + 3
                gy = self.ground_y - self.terrain[int(m["wx"]) % DF_WORLD] - 3
                if m["y"] >= gy - 2:
                    m["state"], m["y"] = "ground", gy
                    self.score += 500
            elif m["state"] == "ground":
                m["y"] = self.ground_y - self.terrain[int(m["wx"]) % DF_WORLD] - 3
        if not any(m["state"] in ("ground", "falling", "caught", "carried") for m in self.men):
            for e in self.enemies:  # the planet is lost: everything turns mutant
                if e["kind"] == "lander":
                    e["kind"], e["carry"] = "mutant", None
        if not self.enemies:
            self.wave += 1
            self.bombs = min(self.bombs + 1, 5)
            for m in self.men:
                if m["state"] == "dead" and random.random() < 0.5:
                    m["state"] = "ground"
            self.new_wave()

    def move_lander(self, e, pace):
        speed = 10 * pace
        if e["carry"] is not None:
            e["y"] -= 5 * pace * STEP
            e["carry"]["wx"], e["carry"]["y"] = e["wx"] + 2, e["y"] + 3
            if e["y"] <= DF_TOP + 1:
                e["carry"]["state"], e["carry"] = "dead", None
                e["kind"] = "mutant"
            return
        target = e.get("target")
        if target is None or target["state"] != "ground":
            free = [m for m in self.men if m["state"] == "ground"]
            e["target"] = target = min(free, key=lambda m: abs((m["wx"] - e["wx"] + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2)) if free else None
        if target is None:  # nothing to take: patrol
            e["wx"] = (e["wx"] + e["vx"] * STEP) % DF_WORLD
            e["y"] += math.sin(e["t"]) * 0.15
            return
        dx = (target["wx"] - 2 - e["wx"] + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
        if abs(dx) > 1.5:
            e["wx"] = (e["wx"] + (speed if dx > 0 else -speed) * STEP) % DF_WORLD
            e["y"] += (16 - e["y"]) * 0.5 * STEP
        else:
            gy = target["y"] - 3
            if e["y"] < gy:
                e["y"] = min(gy, e["y"] + 8 * pace * STEP)
            else:
                e["carry"] = target
                target["state"] = "carried"

    # ---- demo ----

    def autopilot(self):
        keys = []
        if self.dead_t:
            return self.hold(keys)
        on = [e for e in self.enemies if self.on_screen(e["wx"])]
        threats = [m for m in self.men if m["state"] == "falling"]
        if threats:
            m = threats[0]
            dx = (m["wx"] - self.wx + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
            keys.append("right" if dx > 2 else "left" if dx < -2 else ("right" if self.vx < 0 else "left"))
            if m["y"] > self.y + 2:
                keys.append("down")
            return self.hold(keys)
        if not self.enemies:
            return self.hold([])
        e = min(self.enemies, key=lambda e: abs((e["wx"] - self.wx + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2) + (0 if e["carry"] is None else -40))
        dx = (e["wx"] - self.wx + DF_WORLD / 2) % DF_WORLD - DF_WORLD / 2
        if len(on) >= 5 and self.bombs > 0 and random.random() < 0.05:
            self.press("b", 1)
        if dx > 0 and self.facing < 0 or dx < 0 and self.facing > 0:
            keys.append("right" if dx > 0 else "left")
        elif abs(dx) > 30:
            keys.append("right" if dx > 0 else "left")
        elif abs(dx) < 10:
            keys.append("left" if dx > 0 else "right")  # keep a little distance
        if e["y"] < self.y - 1:
            keys.append("up")
        elif e["y"] > self.y + 1:
            keys.append("down")
        if abs(e["y"] - self.y) < 2.5 and abs(dx) < 45 and random.random() < 0.6:
            keys.append("a")
        for b in self.bullets:
            if abs(self.sx(b[0]) - self.sx(self.wx)) < 8 and abs(b[1] - self.y) < 4:
                keys = [k for k in keys if k not in ("up", "down")] + ["up" if b[3] > 0 else "down"]
        self.hold(keys)

    # ---- drawing ----

    def draw(self, c, now):
        W = c.w
        flash = now - self.flash_t < 0.15
        if flash:
            fill(c, 0, DF_TOP, W, c.h - DF_TOP, 1)
        # terrain
        cam = int(self.cam)
        prev = None
        for x in range(W):
            h = self.terrain[(cam + x) % DF_WORLD]
            y = self.ground_y - h
            if prev is not None and abs(prev - y) > 1:
                c.line(x - 1, prev, x, y, 0 if flash else 1)
            else:
                c.set(x, y, 0 if flash else 1)
            prev = y
        v = 0 if flash else 1
        for m in self.men:
            if m["state"] in ("dead",) or not self.on_screen(m["wx"]):
                continue
            sprite(c, int(self.sx(m["wx"])), int(m["y"]), DF_MAN) if v else fill(c, int(self.sx(m["wx"])), int(m["y"]), 2, 3, 0)
        for mx, my, _ in self.mines:
            if self.on_screen(mx) and int(now * 6) % 2:
                c.set(int(self.sx(mx)), int(my), v)
        for e in self.enemies:
            if not self.on_screen(e["wx"]):
                continue
            x, y = int(self.sx(e["wx"])), int(e["y"])
            f = int(now * 5) % 2
            rows = {"lander": DF_LANDER, "mutant": DF_MUTANT, "bomber": DF_BOMBER}[e["kind"]][f]
            for dy, r in enumerate(rows):
                for dx, ch in enumerate(r):
                    if ch == "X":
                        c.set(x + dx, y + dy, v)
        for s in self.shots:
            x0 = self.sx(s["wx"] if s["dir"] > 0 else s["wx"] - s["len"])
            fill(c, int(x0), int(s["y"]), int(s["len"]), 1, v)
        for b in self.bullets:
            c.set(int(self.sx(b[0])), int(b[1]), v)
        for bx, by, t in self.booms:
            if self.on_screen(bx):
                boom(c, self.sx(bx), by, now - t)
        if not self.dead_t and (now >= self.safe_until or int(now * 8) % 2):
            x = int(self.sx(self.wx))
            rows = DF_SHIP[self.facing]
            for dy, r in enumerate(rows):
                for dx, ch in enumerate(r):
                    if ch == "X":
                        c.set(x + dx, int(self.y) + dy, v)
            if abs(self.vx) > 20 and int(now * 20) % 2:
                c.set(x - 1 if self.facing > 0 else x + 5, int(self.y) + 1, v)
        # scanner and HUD
        fill(c, 0, 0, W, DF_TOP, 0)
        for e in self.enemies:
            c.set(int(e["wx"] * W / DF_WORLD) % W, 0)
        for m in self.men:
            if m["state"] == "ground" and int(now * 2) % 2 == 0:
                pass
        if int(now * 4) % 2:
            sx = int(self.wx * W / DF_WORLD) % W
            for k in (-1, 0, 1):
                c.set((sx + k) % W, 0, 1 if k == 0 else 1)
        c.text(0, 2, str(self.score), SMALL)
        men = sum(1 for m in self.men if m["state"] != "dead")
        c.text_center(2, f"W{self.wave} M{men}", SMALL)
        for i in range(self.bombs):
            c.set(W - 20 + i * 2, 6)
        lives_icons(c, self.lives - 1, DF_LIFE, y=2)

    def draw_title(self, c, now):
        x = 20 + int(now * 30) % 60
        sprite(c, x, 24, DF_SHIP[1])
        fill(c, x - 10, 25, 8, 1)
        sprite(c, 60, 20 + int(math.sin(now * 3) * 2), DF_LANDER[int(now * 4) % 2])
        for i in range(0, c.w, 3):
            c.set(i, 33 - (i // 9) % 3)

    def describe(self):
        if self.state == "title":
            return "Defender (title)"
        return f"Defender ({'demo' if self.demo else self.state}, wave {getattr(self, 'wave', 1)}, score {self.score})"


# ---- Joust -----------------------------------------------------------------------------------------------------

J_GROUND = 40
J_PLATFORMS = [(0, 22, 30), (62, 84, 30), (28, 56, 23), (8, 32, 13), (52, 76, 13)]
J_PADS = [(14, 13), (64, 13), (42, 23), (42, J_GROUND)]
J_RIDER = {1: [".XX..", "XXXX.", ".XXXX", "X..X."], -1: ["..XX.", ".XXXX", "XXXX.", ".X..X"]}
J_BUZZARD = {1: ["..X..", "XX.XX", ".XXX.", "X...X"], -1: ["..X..", "XX.XX", ".XXX.", "X...X"]}
J_HUNTER = {1: [".X.X.", "XXXX.", ".XXXX", "X..X."], -1: [".X.X.", ".XXXX", "XXXX.", ".X..X"]}
J_EGG = [".X.", "XXX", ".X."]
J_LIFE = ["XX.", "XXX"]
J_KINDS = {"bounder": (500, 3.0, 18.0), "hunter": (750, 4.5, 26.0), "shadow": (1000, 6.0, 34.0)}  # points, flaps/s, speed


@register
class Joust(Classic):
    id = "joust"
    title = "Joust"
    description = ("Flap (A or up) to fly, left and right to run and bank. Meet a buzzard rider and the higher lance "
                   "wins: knock them off and collect the egg before it hatches. The sides wrap round. Waves get "
                   "faster riders.")
    params = [LIVES, DIFFICULTY]
    DEMO_X, DEMO_Y = -1, 0

    def reset(self):
        self.score, self.wave = 0, 1
        self.lives = int(self.p["lives"])
        self.new_wave()
        self.new_life()

    def new_wave(self):
        self.riders, self.eggs = [], []
        n = min(2 + self.wave, 7)
        kinds = ["bounder"] * 3 + (["hunter"] * 3 if self.wave >= 2 else []) + (["shadow"] * 2 if self.wave >= 4 else [])
        self.spawn_queue = [random.choice(kinds) for _ in range(n)]
        self.spawn_at = self.now + 1.0
        self.wave_t = self.now

    def new_life(self):
        self.x, self.y = 40.0, float(J_GROUND - 4)
        self.vx, self.vy = 0.0, 0.0
        self.facing = 1
        self.dead_t = 0.0
        self.safe_until = self.now + 2.0
        self.flap_t = 0.0
        self.chain = 0

    def spawn_rider(self, kind):
        x, y = random.choice(J_PADS)
        self.riders.append({"x": float(x), "y": float(y - 4), "vx": 0.0, "vy": 0.0, "kind": kind, "facing": 1,
                            "mount_t": self.now + 1.2, "flap_t": 0.0})

    def press(self, key, player):
        if key in ("a", "up", "b") and not self.dead_t:
            self.flap()

    def flap(self):
        if self.now - self.flap_t < 0.08:
            return
        self.flap_t = self.now
        self.vy = min(self.vy, 0.0) - 24.0

    def stand_on(self, x, y, vy, oy):
        """The platform top the sprite's feet (y + 4) land on this step, or None."""
        if vy < 0:
            return None
        for x0, x1, py in J_PLATFORMS + [(0, 84, J_GROUND)]:
            if oy + 4 <= py < y + 4 + 1e-6 and x + 4 > x0 and x < x1:
                return py
        return None

    def physics(self, o, keys):
        """Gravity, flapping and running for a rider dict (o has x, y, vx, vy, facing)."""
        if "left" in keys:
            o["vx"] -= 70 * STEP
            o["facing"] = -1
        if "right" in keys:
            o["vx"] += 70 * STEP
            o["facing"] = 1
        o["vx"] = max(-40.0, min(40.0, o["vx"]))
        o["vy"] = min(o["vy"] + 55 * STEP, 50.0)
        oy = o["y"]
        o["y"] += o["vy"] * STEP
        o["x"] = (o["x"] + o["vx"] * STEP) % 84
        if o["y"] < 2:
            o["y"], o["vy"] = 2.0, max(o["vy"], 0.0)
        py = self.stand_on(o["x"], o["y"], o["vy"], oy)
        if py is not None:
            o["y"], o["vy"] = float(py - 4), 0.0
            if "left" not in keys and "right" not in keys:
                o["vx"] *= 1 - 3 * STEP
        for x0, x1, p in J_PLATFORMS:  # bump the head on a platform from below
            if o["vy"] < 0 and p < oy and p + 1 > o["y"] and o["x"] + 4 > x0 and o["x"] < x1 and p + 1 > o["y"] >= p - 3:
                o["y"], o["vy"] = float(p + 1), 1.0

    def step(self):
        if self.dead_t:
            if self.now - self.dead_t > 1.5:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.new_life()
            return
        held = self.held_keys()
        me = {"x": self.x, "y": self.y, "vx": self.vx, "vy": self.vy, "facing": self.facing}
        self.physics(me, held)
        self.x, self.y, self.vx, self.vy, self.facing = me["x"], me["y"], me["vx"], me["vy"], me["facing"]
        if self.spawn_queue and self.now >= self.spawn_at:
            self.spawn_rider(self.spawn_queue.pop(0))
            self.spawn_at = self.now + 1.5
        pace = self.pace
        for r in self.riders[:]:
            pts, flaps, speed = J_KINDS[r["kind"]]
            keys = set()
            if self.now >= r["mount_t"]:
                dx = (self.x - r["x"] + 42) % 84 - 42
                if r["kind"] != "bounder" or random.random() < 0.5:
                    keys.add("right" if dx > 0 else "left")
                want_y = self.y - 6 if r["kind"] != "bounder" else self.y - 2
                if r["y"] > want_y and self.now - r["flap_t"] > 1 / (flaps * pace) and random.random() < 0.7:
                    r["flap_t"] = self.now
                    r["vy"] = min(r["vy"], 0.0) - 22.0
                elif random.random() < 0.02:
                    r["vy"] = min(r["vy"], 0.0) - 18.0
            self.physics(r, keys)
            r["vx"] = max(-speed * pace, min(speed * pace, r["vx"]))
            # the joust
            close = abs((self.x - r["x"] + 42) % 84 - 42) < 5 and abs(self.y - r["y"]) < 4
            if self.now >= r["mount_t"] and close:
                dy = self.y - r["y"]
                if dy < -1.0:  # my lance is higher
                    self.riders.remove(r)
                    self.score += pts
                    self.eggs.append({"x": r["x"], "y": r["y"], "vy": 0.0, "t": self.now, "hatch": None})
                    self.vy = -10.0
                    continue
                if dy > 1.0:
                    if self.now >= self.safe_until:
                        self.dead_t = self.now
                        return
                else:  # level: bounce apart
                    push = 30.0 if self.x < r["x"] else -30.0
                    self.vx, r["vx"] = -push, push
        for e in self.eggs[:]:
            if e["hatch"] is None:
                e["vy"] = min(e["vy"] + 55 * STEP, 50.0)
                oy = e["y"]
                e["y"] += e["vy"] * STEP
                py = self.stand_on(e["x"], e["y"], e["vy"], oy)
                if py is not None:
                    e["y"], e["vy"] = float(py - 3), 0.0
                if box_hit(self.x, self.y, 5, 4, e["x"], e["y"], 3, 3):
                    self.eggs.remove(e)
                    self.chain = min(self.chain + 1, 4)
                    self.score += 250 * self.chain
                    continue
                if self.now - e["t"] > 6.0:
                    e["hatch"] = self.now
            elif self.now - e["hatch"] > 2.0:
                self.eggs.remove(e)
                self.riders.append({"x": e["x"], "y": e["y"] - 1, "vx": 0.0, "vy": 0.0, "kind": "hunter" if self.wave >= 2 else "bounder",
                                    "facing": 1, "mount_t": self.now + 1.0, "flap_t": 0.0})
        if not self.riders and not self.eggs and not self.spawn_queue:
            self.wave += 1
            self.score += 1000
            self.chain = 0
            self.new_wave()

    def autopilot(self):
        if self.dead_t:
            return self.hold([])
        keys = []
        live = [r for r in self.riders if self.now >= r["mount_t"] - 0.5]
        if live:
            r = min(live, key=lambda r: abs((r["x"] - self.x + 42) % 84 - 42) + abs(r["y"] - self.y))
            dx = (r["x"] - self.x + 42) % 84 - 42
            if r["y"] < self.y - 1 and abs(dx) < 24:  # they're higher: get away and climb
                keys.append("left" if dx > 0 else "right")
                if self.vy > -4:
                    self.press("a", 1)
            else:
                keys.append("right" if dx > 2 else "left" if dx < -2 else ("right" if self.facing > 0 else "left"))
                if self.y > r["y"] - 5 and self.vy > -6 and random.random() < 0.8:
                    self.press("a", 1)
        elif self.eggs:
            e = min(self.eggs, key=lambda e: abs((e["x"] - self.x + 42) % 84 - 42))
            dx = (e["x"] - self.x + 42) % 84 - 42
            keys.append("right" if dx > 1 else "left")
            if e["y"] < self.y - 2 and self.vy > 0:
                self.press("a", 1)
        else:
            if self.vy > 5:
                self.press("a", 1)
        self.hold(keys)

    def draw(self, c, now):
        for x0, x1, y in J_PLATFORMS:
            fill(c, x0, y, x1 - x0, 1)
            for x in range(x0 + 1, x1 - 1, 3):
                c.set(x, y + 1)
        fill(c, 0, J_GROUND, c.w, 1)
        for x in range(0, c.w, 2):
            c.set(x, J_GROUND + 1)
        for e in self.eggs:
            if e["hatch"] is None or int(now * 10) % 2:
                sprite(c, int(e["x"]), int(e["y"]), J_EGG)
        for r in self.riders:
            if now < r["mount_t"] and int(now * 6) % 2:
                continue
            rows = (J_BUZZARD if r["kind"] == "bounder" else J_HUNTER)[r["facing"]]
            sprite(c, int(r["x"]), int(r["y"]), rows)
            if r["x"] > 79:
                sprite(c, int(r["x"]) - 84, int(r["y"]), rows)
        if not self.dead_t or int((now - self.dead_t) * 8) % 2:
            if now >= self.safe_until or int(now * 8) % 2:
                rows = J_RIDER[self.facing]
                if now - self.flap_t < 0.12:
                    rows = [rows[1], rows[0], rows[2], rows[3]]
                sprite(c, int(self.x), int(self.y), rows)
                if self.x > 79:
                    sprite(c, int(self.x) - 84, int(self.y), rows)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, f"WAVE {self.wave}", SMALL)
        lives_icons(c, self.lives - 1, J_LIFE)

    def draw_title(self, c, now):
        y = 24 + int(math.sin(now * 4) * 2)
        sprite(c, 30, y, J_RIDER[1])
        sprite(c, 48, 26 - int(math.sin(now * 4) * 2), J_HUNTER[-1])
        fill(c, 20, 34, 44, 1)

    def describe(self):
        if self.state == "title":
            return "Joust (title)"
        return f"Joust ({'demo' if self.demo else self.state}, wave {getattr(self, 'wave', 1)}, score {self.score})"


# ---- Marble Madness --------------------------------------------------------------------------------------------
# A course is a grid of 4-dot cells drawn in an oblique projection (each row sits 2 dots further right and 3 dots
# lower than the one above), so a flat platform reads as a tilted slab. '.' flat, 'v' downhill (towards the
# bottom), '^' uphill, '>' '<' slope right/left, '#' raised block, 'o' hole, 'S' start, 'G' goal, ' ' nothing.

M_CELL, M_ROW, M_SKEW, M_TOP = 4, 3, 2, 8
M_COURSES = [
    ["S.....        ",
     ".....v.       ",
     "  ....v..     ",
     "    ..vv...   ",
     "     .......  ",
     "       #..... ",
     "        ..>>..",
     "         .....",
     "          ...G",
     "              "],
    ["  S....       ",
     "  .o...v      ",
     "  .....v.     ",
     "   ##..vv..   ",
     "     ....o..  ",
     "      ....... ",
     "       .o..>>.",
     "        ......",
     "         ...G.",
     "              "],
    ["S......       ",
     "...o...v.     ",
     ".#.....v..    ",
     "..vv...vv..   ",
     "...........   ",
     "  ....#...o.  ",
     "   ...>>>.... ",
     "    ....o.....",
     "     ..#...G..",
     "      ....... "],
    ["   S....      ",
     "   .o..v..    ",
     "   ....v..o.  ",
     "  ..v..vv.... ",
     " ...v........ ",
     "....o..#..o...",
     "...>>>........",
     " .........o...",
     "  ..o...#..G..",
     "   ..........."],
]
M_SLOPE = {"v": (0, 1), "^": (0, -1), ">": (1, 0), "<": (-1, 0)}
M_MARBLE = shape([".X.", "XXX", ".X."])
M_STEELY = shape(["XXX", "X.X", "XXX"])
M_GOAL = (shape([".X.", "XXX", ".X."]), shape(["X.X", ".X.", "X.X"]))


@register
class Marble(Classic):
    id = "marble"
    title = "Marble Madness"
    description = ("Roll the marble down a tilted course to the goal before the clock runs out: it keeps its "
                   "momentum, slopes speed it up, and off the edge it falls (back to where it was, minus some time). "
                   "Later courses have holes, blocks and a steely that barges you about.")
    params = [DIFFICULTY]
    DEMO_X, DEMO_Y = -1, 0
    TIME = 45.0

    def reset(self):
        self.score, self.course = 0, 0
        self.start_course()

    def start_course(self):
        rows = M_COURSES[self.course % len(M_COURSES)]
        self.rows = rows
        self.gh, self.gw = len(rows), len(rows[0])
        self.start = self.goal = None
        for gy, row in enumerate(rows):
            for gx, ch in enumerate(row):
                if ch == "S":
                    self.start = (gx, gy)
                elif ch == "G":
                    self.goal = (gx, gy)
        self.time_left = self.TIME + 8 * min(self.course, 3)
        self.steelies = []
        if self.course >= 2:
            cells = [(gx, gy) for gy, row in enumerate(rows) for gx, ch in enumerate(row) if ch == "." and abs(gx - self.start[0]) + abs(gy - self.start[1]) > 6]
            for _ in range(1 + (self.course >= 4)):
                sx, sy = random.choice(cells)
                self.steelies.append({"x": sx + 0.5, "y": sy + 0.5, "vx": 0.0, "vy": 0.0})
        self.respawn()
        self.course_t = self.now
        self.done_t = 0.0

    def respawn(self):
        self.x, self.y = self.start[0] + 0.5, self.start[1] + 0.5
        self.vx = self.vy = 0.0
        self.safe = (self.start[0], self.start[1])
        self.fall_t = 0.0
        self.safe_t = self.now

    def cell(self, gx, gy):
        if 0 <= gy < self.gh and 0 <= gx < self.gw:
            return self.rows[gy][gx]
        return " "

    def passable(self, ch):
        return ch not in " #"

    def project(self, x, y):
        return x * M_CELL + y * M_SKEW, M_TOP + y * M_ROW

    def offset(self):
        """Centre the course on the board."""
        w = self.gw * M_CELL + self.gh * M_SKEW
        return (84 - w) // 2

    def press(self, key, player):
        pass

    def steer(self, o, keys, accel):
        if "left" in keys:
            o["vx"] -= accel * STEP
        if "right" in keys:
            o["vx"] += accel * STEP
        if "up" in keys:
            o["vy"] -= accel * STEP
        if "down" in keys:
            o["vy"] += accel * STEP

    def roll(self, o, bounce_void=False):
        """Slopes, friction and movement for a marble dict; True if it rolled off the edge."""
        ch = self.cell(math.floor(o["x"]), math.floor(o["y"]))
        if ch in M_SLOPE:
            sx, sy = M_SLOPE[ch]
            o["vx"] += sx * 7 * STEP
            o["vy"] += sy * 7 * STEP
        f = 1 - 1.6 * STEP
        o["vx"] *= f
        o["vy"] *= f
        speed = math.hypot(o["vx"], o["vy"])
        if speed > 7:
            o["vx"], o["vy"] = o["vx"] / speed * 7, o["vy"] / speed * 7
        for axis, v in (("x", "vx"), ("y", "vy")):
            nxt = o[axis] + o[v] * STEP
            gx, gy = (math.floor(nxt), math.floor(o["y"])) if axis == "x" else (math.floor(o["x"]), math.floor(nxt))
            ch = self.cell(gx, gy)
            if ch == "#" or (ch == " " and bounce_void):
                o[v] = -o[v] * 0.5
            elif ch == " ":
                o[axis] = nxt
                return True
            else:
                o[axis] = nxt
        return False

    def step(self):
        if self.done_t:
            if self.now - self.done_t > 2.0:
                self.course += 1
                self.start_course()
            return
        self.time_left -= STEP
        if self.time_left <= 0:
            self.time_left = 0.0
            return self.game_over()
        if self.fall_t:
            if self.now - self.fall_t > 0.9:
                sx, sy = self.safe
                self.x, self.y, self.vx, self.vy = sx + 0.5, sy + 0.5, 0.0, 0.0
                self.fall_t = 0.0
                self.time_left = max(0.1, self.time_left - 3)
            return
        me = {"x": self.x, "y": self.y, "vx": self.vx, "vy": self.vy}
        self.steer(me, self.held_keys(), 9.0)
        fell = self.roll(me)
        self.x, self.y, self.vx, self.vy = me["x"], me["y"], me["vx"], me["vy"]
        gx, gy = math.floor(self.x), math.floor(self.y)
        ch = self.cell(gx, gy)
        if fell or ch == "o":
            self.fall_t = self.now
            return
        if ch in ".S" and self.now - self.safe_t > 0.4:
            self.safe, self.safe_t = (gx, gy), self.now
        if (gx, gy) == self.goal:
            bonus = int(self.time_left) * 10
            self.score += 500 + bonus
            self.done_t = self.now
            return
        for s in self.steelies:
            dx, dy = self.x - s["x"], self.y - s["y"]
            dist = math.hypot(dx, dy) or 0.01
            s["vx"] += (-dx / dist) * 4 * STEP * self.pace
            s["vy"] += (-dy / dist) * 4 * STEP * self.pace
            self.roll(s, bounce_void=True)
            if dist < 0.9:
                self.vx += dx / dist * 5
                self.vy += dy / dist * 5
                s["vx"], s["vy"] = -dx / dist * 2, -dy / dist * 2

    def path_to_goal(self):
        start = (int(self.x), int(self.y))
        seen = {start}
        todo = deque([(start, None)])
        parent = {}
        while todo:
            (x, y), _ = todo.popleft()
            if (x, y) == self.goal:
                path = [(x, y)]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                return path[::-1]
            for d in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                n = (x + d[0], y + d[1])
                if n not in seen and self.cell(*n) not in " #o":
                    seen.add(n)
                    parent[n] = (x, y)
                    todo.append((n, d))
        return [start]

    def autopilot(self):
        if self.fall_t or self.done_t:
            return self.hold([])
        path = self.path_to_goal()
        nxt = path[1] if len(path) > 1 else path[0]
        tx, ty = nxt[0] + 0.5, nxt[1] + 0.5
        want_vx = max(-3.5, min(3.5, (tx - self.x) * 4))
        want_vy = max(-3.5, min(3.5, (ty - self.y) * 4))
        keys = []
        if self.vx < want_vx - 0.3:
            keys.append("right")
        elif self.vx > want_vx + 0.3:
            keys.append("left")
        if self.vy < want_vy - 0.3:
            keys.append("down")
        elif self.vy > want_vy + 0.3:
            keys.append("up")
        self.hold(keys)

    def draw(self, c, now):
        off = self.offset()
        for gy in range(self.gh):
            for gx in range(self.gw):
                ch = self.cell(gx, gy)
                if ch == " ":
                    continue
                x, y = self.project(gx, gy)
                x += off
                if ch == "#":
                    fill(c, x, y - 2, M_CELL, M_ROW + 2, 1)
                    c.set(x, y - 2, 0)
                    continue
                if ch in M_SLOPE:
                    for yy in range(y, y + M_ROW):
                        for xx in range(x, x + M_CELL):
                            if (xx + yy) % 2 == 0:
                                c.set(xx, yy)
                if ch == "o":
                    fill(c, x, y, M_CELL, M_ROW, 1)
                    fill(c, x + 1, y + 1, M_CELL - 2, M_ROW - 2, 0)
                # edges where the slab ends
                if self.cell(gx, gy - 1) == " ":
                    fill(c, x, y - 1, M_CELL + M_SKEW, 1, 1)
                if self.cell(gx, gy + 1) == " ":
                    fill(c, x - M_SKEW, y + M_ROW - 1, M_CELL + M_SKEW, 1, 1)
                    fill(c, x - M_SKEW, y + M_ROW, M_CELL + M_SKEW, 1, 1)  # the slab's thickness
                if self.cell(gx - 1, gy) == " ":
                    c.line(x, y - 1, x - M_SKEW + 1, y + M_ROW - 1)
                if self.cell(gx + 1, gy) == " ":
                    c.line(x + M_CELL - 1, y - 1, x + M_CELL - M_SKEW, y + M_ROW - 1)
        gx, gy = self.goal
        x, y = self.project(gx + 0.5, gy + 0.5)
        put(c, int(x) + off - 1, int(y) - 1, M_GOAL[int(now * 3) % 2])
        for s in self.steelies:
            x, y = self.project(s["x"], s["y"])
            put(c, int(x) + off - 1, int(y) - 1, M_STEELY)
        x, y = self.project(self.x, self.y)
        x, y = int(x) + off, int(y)
        if self.fall_t:
            k = int((now - self.fall_t) * 6)
            if k < 2:
                put(c, x - 1, y - 1 + k, M_MARBLE)
            elif k < 4:
                c.set(x, y + k - 1)
        else:
            fill(c, x - 2, y - 2, 5, 5, 0)
            put(c, x - 1, y - 1, M_MARBLE)
        fill(c, 0, 0, c.w, 7, 0)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, f"C{self.course + 1}", SMALL)
        t = f"{int(self.time_left):02d}"
        c.text(c.w - SMALL.width(t) - (0 if not self.demo else 20), 0, t, SMALL)
        if self.done_t:
            banner(c, 18, "GOAL!")

    def draw_title(self, c, now):
        x0 = 20
        for i in range(6):
            fill(c, x0 + i * 7 + 4, 22 + i, 6, 1)
        disc(c, x0 + 4 + int(now * 12) % 42, 20 + int(now * 12) % 42 // 7, 1)

    def describe(self):
        if self.state == "title":
            return "Marble Madness (title)"
        return f"Marble Madness ({'demo' if self.demo else self.state}, course {getattr(self, 'course', 0) + 1}, score {self.score})"


# ---- Ghosts vs Pac-Man ------------------------------------------------------------------------------------------

GHOST_LOOKS = [GHOST, (shape(["XXX", "XXX", "X.X"]), shape(["XXX", "XXX", "XXX"])),
               (shape([".X.", "XXX", "XXX"]), shape([".X.", "XXX", "X.X"])),
               (shape(["X.X", "XXX", "X.X"]), shape(["X.X", "XXX", "XXX"]))]
RELEASE_FAST = (0, 0.8, 1.6, 2.4)
PAC_SHAPE = shape(["XXX", "X..", "XXX"])
PAC_SHUT_SHAPE = shape(["XXX", "XXX", "XXX"])


@register
class Ghosts(PacMan):
    id = "ghosts"
    title = "Ghosts vs Pac"
    description = ("Pac-Man turned round: up to four players drive the ghosts (each a different shape) and hunt a "
                   "computer Pac-Man, or player 1 is Pac-Man and the others are the ghosts. Ghosts win when they "
                   "catch him three times; Pac-Man wins by clearing the dots. A power pellet still sends you home.")
    params = [P("pac", "select", "cpu", "Who is Pac-Man", options=[["cpu", "The computer (everyone is a ghost)"],
                                                                     ["p1", "Player 1 (players 2-4 are ghosts)"]]),
              P("difficulty", "select", "normal", "Pac-Man's speed",
                options=[["easy", "Slow"], ["normal", "Normal"], ["hard", "Fast"]])]

    def reset(self):
        self.catches = {}
        self.caught_by = None
        self.score, self.level = 0, 1
        self.lives = 3
        self.new_level()

    def new_level(self):
        super().new_level()
        mult = {"easy": 0.85, "hard": 1.15}.get(self.p["difficulty"], 1.0)
        self.pac_speed = 7.5 * mult
        self.ghost_speed = 7.4

    def new_life(self):
        super().new_life()
        for g, rel in zip(self.ghosts, RELEASE_FAST):
            g.release = rel

    def pac_human(self):
        return self.p["pac"] == "p1" and not self.demo

    def ghost_player(self, g):
        """The player number driving ghost g, or None (the computer)."""
        n = g.kind + (2 if self.p["pac"] == "p1" else 1)
        return n if not self.demo and (n == 1 or self.human(n)) else None

    def score_entry(self):
        return None

    def result(self):
        if self.state != "over":
            return None
        if self.lives <= 0:
            best = max(self.catches.items(), key=lambda kv: kv[1])[0] if self.catches else None
            return ("GHOSTS WIN", f"P{best} x{self.catches[best]}" if best else "CAUGHT")
        return ("PAC-MAN WINS", str(self.score))

    def button(self, key, player):
        d = DIRS.get(key)
        if not d:
            return
        if player == 1 and self.pac_human():
            self.pac.want = d
            if d == (-self.pac.dir[0], -self.pac.dir[1]):
                self.pac.dir = self.pac.face = d
            return
        for g in self.ghosts:
            if self.ghost_player(g) == player:
                g.want = d
                if g.mode == "active" and d == (-g.dir[0], -g.dir[1]):
                    g.dir = d

    def pac_turn(self, a):
        self.wrap(a)
        if not self.pac_human():
            self.autopilot(a)
        tx, ty = round(a.x), round(a.y)
        if a.want and self.open(tx + a.want[0], ty + a.want[1]):
            a.dir = a.face = a.want
        elif not self.open(tx + a.dir[0], ty + a.dir[1]):
            a.dir = (0, 0)

    def ghost_turn(self, g):
        player = self.ghost_player(g)
        if player is None or g.mode == "eyes":
            return super().ghost_turn(g)
        self.wrap(g)
        tx, ty = round(g.x), round(g.y)
        want = getattr(g, "want", None)
        held = self.held_dir(player)
        for d in (held, want):
            if d and self.open(tx + d[0], ty + d[1]):
                g.dir = d
                return
        if not self.open(tx + g.dir[0], ty + g.dir[1]):
            opts = [d for d in DIRS.values() if self.open(tx + d[0], ty + d[1])]
            g.dir = random.choice(opts) if opts else (0, 0)

    def step(self):
        phase = self.phase
        super().step()
        if self.phase == "dying" and phase != "dying":
            p = self.pac
            near = min(self.ghosts, key=lambda g: abs(g.x - p.x) + abs(g.y - p.y))
            who = self.ghost_player(near)
            self.caught_by = who
            if who:
                self.catches[who] = self.catches.get(who, 0) + 1
        if self.phase == "clear":
            self.game_over()

    def game_over(self):
        if self.demo:
            return super().game_over()
        self.state = "over"
        self.over_at = self.now

    def draw(self, c, now):
        super().draw(c, now)
        # each ghost its own look, and who drives it
        if self.phase != "dying" or self.t - self.phase_t < 0.5:
            legs = int(self.t * 5) % 2
            left = self.fright_until - self.t
            for g in self.ghosts:
                gx, gy = round(g.x * PT), PY + round(g.y * PT)
                if g.mode in ("eyes", "enter") or (g.fright and (left > 2 or int(self.t * 6) % 2)):
                    continue
                fill(c, gx, gy, 3, 3, 0)
                put(c, gx, gy, GHOST_LOOKS[g.kind][legs])
        fill(c, 0, 0, c.w, PY + 1, 0)
        c.text(0, 0, str(self.score), SMALL)
        tags = []
        for g in self.ghosts:
            pl = self.ghost_player(g)
            if pl:
                tags.append((pl, g.kind))
        x = c.w - 1
        for pl, kind in reversed(tags):
            x -= 4
            put(c, x, 1, GHOST_LOOKS[kind][0])
            x -= SMALL.width(str(pl)) + 1
            c.text(x, 0, str(pl), SMALL)
            x -= 2
        mid = "DEMO" if self.demo else (f"P1 PAC" if self.pac_human() else f"LIVES {self.lives}")
        c.text_center(0, mid, SMALL)
        if self.phase == "dying" and self.caught_by:
            banner(c, PY + 7 * PT - 1, f"P{self.caught_by} GOT HIM")
        elif self.phase == "ready":
            banner(c, PY + 7 * PT - 1, "HUNT!" if not self.pac_human() else "READY")

    def draw_title(self, c, now):
        n = int(now * 8) % 64
        x = 12 + n
        put(c, x, 25, PAC_SHAPE if int(now * 6) % 2 else PAC_SHUT_SHAPE)
        for i in range(4):
            put(c, x - 8 - i * 5, 25, GHOST_LOOKS[i][int(now * 5) % 2])

    def describe(self):
        if self.state == "title":
            return "Ghosts vs Pac (title)"
        return f"Ghosts vs Pac ({'demo' if self.demo else self.state}, lives {getattr(self, 'lives', 3)}, score {self.score})"
