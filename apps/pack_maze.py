"""Maze and arena games: Pac-Man, Frogger, Qix, Bomberman and Tank Combat.

Built on arcade.Arcade (fixed 40 Hz physics). After a while on the title screen
each game plays itself as a demo; any button ends the demo and starts a game.
On the game over screen A starts again straight away.
"""

import math
import random
from collections import deque

from fonts import NORMAL, SMALL

from .arcade import STEP, Arcade, fill
from .base import P, register

DEMO_AFTER = 12   # seconds on the title screen before the game plays itself
DEMO_LENGTH = 60  # longest a demo runs before the title comes back
HUMAN_FOR = 30    # a player counts as human this long after their last button
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def shape(rows):
    """Sprite rows ("X" = dot) -> list of (dx, dy)."""
    return [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "X"]


def turn(rows):
    """Rotate sprite rows a quarter turn clockwise."""
    return ["".join(rows[len(rows) - 1 - x][y] for x in range(len(rows))) for y in range(len(rows[0]))]


def facings(rows):
    """{direction: points} for a sprite drawn facing right."""
    down = turn(rows)
    left = turn(down)
    up = turn(left)
    return {(1, 0): shape(rows), (0, 1): shape(down), (-1, 0): shape(left), (0, -1): shape(up)}


def put(c, x, y, pts, v=1):
    w, h, px = c.w, c.h, c.px
    x, y = int(x), int(y)
    for dx, dy in pts:
        xx, yy = x + dx, y + dy
        if 0 <= xx < w and 0 <= yy < h:
            px[yy * w + xx] = v


def banner(c, y, text):
    """A line of small text on a cleared strip, for messages over the playfield."""
    tw = SMALL.width(text)
    fill(c, (c.w - tw) // 2 - 2, y - 2, tw + 4, 9, 0)
    c.text_center(y, text, SMALL)


def travel(a, dist, decide):
    """Move `a` (x, y in tiles, dir) along its direction; decide(a) runs at every tile centre
    it reaches and may change a.dir (or stop it with (0, 0))."""
    while dist > 1e-6 and (a.dir[0] or a.dir[1]):
        dx, dy = a.dir
        pos = a.x if dx else a.y
        s = dx or dy
        nxt = math.floor(pos) + 1 if s > 0 else math.ceil(pos) - 1
        gap = abs(nxt - pos)
        if gap > dist:
            if dx:
                a.x += dx * dist
            else:
                a.y += dy * dist
            return
        if dx:
            a.x = float(nxt)
        else:
            a.y = float(nxt)
        dist -= gap
        decide(a)


class Mover:
    def __init__(self, x, y, d=(0, 0), **kw):
        self.x, self.y, self.dir = float(x), float(y), d
        self.__dict__.update(kw)


class PackGame(Arcade):
    """Arcade game with an attract-mode demo and A-to-restart on the game over screen.
    Subclasses check self.demo to let the computer play player 1."""

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.demo = False
        self.demo_until = 0
        self.title_since = None
        self.pressed_at = {}  # player -> time of their last button
        self.last_dir = {}    # player -> the direction they pressed last

    def input(self, key, action="down", player=1):
        if action == "down":
            self.pressed_at[player] = self.now
            if self.demo:  # any button ends the demo and starts a real game
                self.demo = False
                self.state = "title"
            elif self.state == "over" and key in ("a", "start") and self.now - self.over_at > 1:
                self.state = "title"
        super().input(key, action, player)

    def human(self, player):
        if self.demo:
            return False
        if player == 1:
            return True
        return self.now - self.pressed_at.get(player, -1e9) < HUMAN_FOR

    def game_over(self):
        if self.demo:
            self.demo = False
            self.state = "title"
            self.title_since = self.now
            return
        super().game_over()
        self.over_at = self.now

    def held_dir(self, player):
        """The direction player is holding (the most recently pressed one wins)."""
        last = self.last_dir.get(player)
        if last and (player, last) in self.held:
            return DIRS[last]
        for k, d in DIRS.items():
            if (player, k) in self.held:
                return d
        return None

    def press(self, key, player):
        if key in DIRS:
            self.last_dir[player] = key
        self.button(key, player)

    def button(self, key, player): ...

    def render(self, c, now):
        self.now = now
        if self.state == "title":
            if self.title_since is None:
                self.title_since = now
            elif now - self.title_since > DEMO_AFTER:
                self.title_since = None
                self.demo, self.demo_until = True, now + DEMO_LENGTH
                self.state = "playing"
                self.reset()
        else:
            self.title_since = None
            if self.demo and now > self.demo_until:
                self.demo = False
                self.state = "title"
                self.title_since = now
        result = self.result() if self.state == "over" else None
        if result:  # versus games show who won instead of a score
            c.text_center(4, result[0], NORMAL)
            c.text_center(14, result[1], NORMAL, scale=2)
            c.text_center(34, "A: PLAY AGAIN", SMALL)
            if now - self.over_at > 6:
                self.state = "title"
            return
        super().render(c, now)

    def result(self):
        """(headline, score line) for the game over screen of a versus game."""
        return None

    def score_entry(self):
        return None if self.result() else super().score_entry()


# ---- Pac-Man ------------------------------------------------------------------------

# Left half of the maze (mirrored for the right). '#' wall, '.' dot, 'o' power pellet,
# ' ' corridor without a dot, '-' ghost house door, 'G' inside the ghost house.
PAC_HALF = [
    "##############",
    "#o...........#",
    "#.####.#####.#",
    "#.............",
    "#.####.#####.#",
    "#.####........",
    "#.####.##.###-",
    " ......##.#GGG",
    "#.####.##.####",
    "#.####........",
    "#.####.#####.#",
    "#o............",
    "##############",
]
PAC_MAZE = [r + r[::-1] for r in PAC_HALF]
PCOLS, PROWS = len(PAC_MAZE[0]), len(PAC_MAZE)
PT = 3          # dots per tile
PY = 4          # screen row of the maze's top tile (only its bottom edge shows)
TUNNEL = 7
PAC_START = (13.5, 9)
DOOR = ((13, 5), (14, 5))  # corridor tiles just above the door
ORDER = [(0, -1), (-1, 0), (0, 1), (1, 0)]  # ghosts break ties up, left, down, right

PAC = facings(["XXX", "X..", "XXX"])
PAC_HALFOPEN = facings(["XXX", "XX.", "XXX"])
PAC_SHUT = shape(["XXX", "XXX", "XXX"])
PAC_DYING = [shape(r) for r in (["X.X", "X.X", "XXX"], ["...", "X.X", "XXX"], ["...", "...", "XXX"], ["...", "...", ".X."])]
GHOST = (shape([".X.", "XXX", "X.X"]), shape([".X.", "XXX", "XXX"]))
SCARED = (shape(["X.X", ".X.", "X.X"]), shape(["X.X", "...", "X.X"]))
EYES = shape(["X.X"])
POWER = shape([".X.", "XXX", ".X."])
LIFE = shape(["XXX", "X..", "XXX"])
# (scatter, chase) seconds; after the list it's chase for good
WAVES = [(7, 20), (7, 20), (5, 20), (5, 1e9)]
WALL_CACHE = {}


@register
class PacMan(PackGame):
    id = "pacman"
    title = "Pac-Man"
    description = ("Eat every dot. Press a direction early and Pac-Man turns at the next opening. "
                   "Power pellets make the ghosts edible for a few seconds.")
    params = [P("lives", "number", 3, "Lives", min=1, max=9),
              P("difficulty", "select", "normal", "Ghosts",
                options=[["easy", "Slow"], ["normal", "Normal"], ["hard", "Fast"]])]

    def reset(self):
        self.score, self.level = 0, 1
        self.lives = int(self.p["lives"])
        self.new_level()

    def new_level(self):
        self.dots, self.power = set(), set()
        for y, row in enumerate(PAC_MAZE):
            for x, ch in enumerate(row):
                if ch == ".":
                    self.dots.add((x, y))
                elif ch == "o":
                    self.power.add((x, y))
        self.bg = bytearray(self.walls())
        lv = min(self.level - 1, 5)
        mult = {"easy": 0.85, "hard": 1.12}.get(self.p["difficulty"], 1.0)
        self.pac_speed = 7.5 * (1 + 0.03 * lv)
        self.ghost_speed = 7.0 * mult * (1 + 0.05 * lv)
        self.fright_time = max(1.5, 7 - self.level) * (1.2 if mult < 1 else 1)
        self.new_life()

    def new_life(self):
        self.t = 0.0
        self.pac = Mover(*PAC_START, (-1, 0), want=None, face=(-1, 0), walked=0.0)
        self.ghosts = [
            Mover(13.5, 5, (-1, 0), kind=0, mode="active", corner=(PCOLS - 3, -3), home=(13.5, 7), release=0),
            Mover(13.5, 7, (0, -1), kind=1, mode="house", corner=(2, -3), home=(13.5, 7), release=1),
            Mover(11.5, 7, (0, -1), kind=2, mode="house", corner=(PCOLS - 1, PROWS + 1), home=(11.5, 7), release=5),
            Mover(15.5, 7, (0, -1), kind=3, mode="house", corner=(0, PROWS + 1), home=(15.5, 7), release=9),
        ]
        for g in self.ghosts:
            g.fright = False
        self.fright_until = 0
        self.chain = 0
        self.wave_t = 0.0
        self.scatter = True
        self.freeze_until = 0
        self.popups = []
        self.phase, self.phase_t = "ready", 0.0

    def walls(self):
        """Background with the walls drawn as single lines through the wall tiles, so there's a
        dot of space between them and anything in a corridor."""
        W, H = self.w, self.h
        if (W, H) in WALL_CACHE:
            return WALL_CACHE[(W, H)]
        bg = bytearray(W * H)

        def wall(tx, ty):
            if not (0 <= tx < PCOLS and 0 <= ty < PROWS):
                return True
            return PAC_MAZE[ty][tx] == "#"

        def centre(tx, ty):
            return tx * PT + 1, PY + ty * PT + 1

        def edge(*tiles):  # a wall line runs between two wall tiles if it borders open space
            return any(not wall(*t) for t in tiles)

        for ty in range(PROWS):
            for tx in range(PCOLS):
                if not wall(tx, ty) or not edge(*[(tx + dx, ty + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]):
                    continue
                x, y = centre(tx, ty)
                if 0 <= y < H:
                    bg[y * W + x] = 1
                if wall(tx + 1, ty) and tx + 1 < PCOLS and edge((tx, ty - 1), (tx + 1, ty - 1), (tx, ty + 1), (tx + 1, ty + 1)):
                    for k in range(1, PT + 1):
                        bg[y * W + x + k] = 1
                if wall(tx, ty + 1) and ty + 1 < PROWS and edge((tx - 1, ty), (tx - 1, ty + 1), (tx + 1, ty), (tx + 1, ty + 1)):
                    for k in range(1, PT + 1):
                        if y + k < H:
                            bg[(y + k) * W + x] = 1
        y = PY + 6 * PT + 1  # the ghost house door: a dashed line
        for x in (39, 41, 42, 44):
            bg[y * W + x] = 1
        WALL_CACHE[(W, H)] = bytes(bg)
        return WALL_CACHE[(W, H)]

    def open(self, tx, ty):
        if not 0 <= ty < PROWS:
            return False
        if not 0 <= tx < PCOLS:
            return ty == TUNNEL
        return PAC_MAZE[ty][tx] in ".o "

    def wrap(self, a):
        if a.x <= -1:
            a.x = float(PCOLS)
        elif a.x >= PCOLS:
            a.x = -1.0

    # ---- Pac-Man ----

    def button(self, key, player):
        d = DIRS.get(key)
        if d and player == 1 and not self.demo:
            self.pac.want = d
            if d == (-self.pac.dir[0], -self.pac.dir[1]):
                self.pac.dir = self.pac.face = d  # reversing works anywhere

    def pac_turn(self, a):
        self.wrap(a)
        if self.demo:
            self.autopilot(a)
        tx, ty = round(a.x), round(a.y)
        if a.want and self.open(tx + a.want[0], ty + a.want[1]):
            a.dir = a.face = a.want
        elif not self.open(tx + a.dir[0], ty + a.dir[1]):
            a.dir = (0, 0)

    def autopilot(self, a):
        """Demo: head for the nearest dot (or a blue ghost), keeping clear of the others."""
        start = (round(a.x), round(a.y))
        bad = set()
        for g in self.ghosts:
            if g.mode == "active" and not g.fright:
                gx, gy = round(g.x), round(g.y)
                for dx in range(-2, 3):
                    for dy in range(-2 + abs(dx), 3 - abs(dx)):
                        bad.add((gx + dx, gy + dy))
        prey = {(round(g.x), round(g.y)) for g in self.ghosts
                if g.mode == "active" and g.fright and self.fright_until - self.t > 2}
        seen = {start}
        todo = deque()
        for d in ORDER:
            n = ((start[0] + d[0]) % PCOLS, start[1] + d[1])
            if self.open(*n) and n not in bad:
                seen.add(n)
                todo.append((n, d))
        while todo:
            (x, y), first = todo.popleft()
            if (x, y) in prey or (x, y) in self.dots or (x, y) in self.power:
                a.want = first
                return
            for d in ORDER:
                n = ((x + d[0]) % PCOLS, y + d[1])
                if n not in seen and self.open(*n) and n not in bad:
                    seen.add(n)
                    todo.append((n, first))
        # boxed in: step to the open neighbour furthest from the nearest ghost
        threats = [(g.x, g.y) for g in self.ghosts if g.mode == "active" and not g.fright] or [(0, 0)]
        best = None
        for d in ORDER:
            n = (start[0] + d[0], start[1] + d[1])
            if self.open(*n):
                score = min(abs(n[0] - gx) + abs(n[1] - gy) for gx, gy in threats)
                if best is None or score > best[0]:
                    best = (score, d)
        if best:
            a.want = best[1]

    # ---- ghosts ----

    def target(self, g):
        if g.mode == "eyes":
            return DOOR[0]
        if self.scatter:
            return g.corner
        p = self.pac
        px, py = round(p.x), round(p.y)
        fx, fy = p.face
        if g.kind == 0:  # Blinky chases Pac-Man directly
            return px, py
        if g.kind == 1:  # Pinky aims four tiles ahead of him
            return px + 4 * fx, py + 4 * fy
        if g.kind == 2:  # Inky: two tiles ahead, then double the vector from Blinky
            b = self.ghosts[0]
            ax, ay = px + 2 * fx, py + 2 * fy
            return 2 * ax - round(b.x), 2 * ay - round(b.y)
        # Clyde chases from afar but wanders off to his corner when close
        if (px - g.x) ** 2 + (py - g.y) ** 2 > 36:
            return px, py
        return g.corner

    def ghost_turn(self, g):
        self.wrap(g)
        tx, ty = round(g.x), round(g.y)
        if g.mode == "eyes" and (tx, ty) in DOOR:
            g.mode, g.dir = "enter", (0, 0)
            return
        back = (-g.dir[0], -g.dir[1])
        opts = [d for d in ORDER if d != back and self.open(tx + d[0], ty + d[1])] or [back]
        if g.fright:
            g.dir = random.choice(opts)
            return
        gx, gy = self.target(g)
        g.dir = min(opts, key=lambda d: (tx + d[0] - gx) ** 2 + (ty + d[1] - gy) ** 2)

    def glide(self, g, tx, ty, dist):
        """Straight moves in and out of the house (x first, then y); True on arrival."""
        for axis, goal in (("x", tx), ("y", ty)):
            cur = getattr(g, axis)
            if cur != goal:
                s = min(dist, abs(goal - cur))
                setattr(g, axis, cur + (s if goal > cur else -s))
                dist -= s
                g.dir = ((1 if goal > cur else -1), 0) if axis == "x" else (0, (1 if goal > cur else -1))
            if dist <= 0:
                break
        return g.x == tx and g.y == ty

    def move_ghost(self, g):
        if g.mode == "house":
            if self.t >= g.release:
                g.mode = "leave"
            return
        if g.mode == "eyes":
            speed = 14
        elif g.mode in ("leave", "enter"):
            speed = 8 if g.mode == "enter" else 3
        elif g.fright:
            speed = 4
        elif round(g.y) == TUNNEL and (g.x < 6 or g.x > PCOLS - 7):
            speed = 3.5
        else:
            speed = self.ghost_speed
        dist = speed * STEP
        if g.mode == "leave":
            if self.glide(g, 13.5, 5, dist):
                g.mode, g.dir = "active", random.choice(((-1, 0), (1, 0)))
        elif g.mode == "enter":
            if self.glide(g, 13.5, 7, dist):
                g.mode, g.fright = "leave", False
        else:
            travel(g, dist, self.ghost_turn)

    # ---- the clock ----

    def step(self):
        self.t += STEP
        t = self.t
        if self.phase != "play":
            if self.phase == "ready" and t > 2:
                self.phase = "play"
                self.t = 0.0
            elif self.phase == "dying" and t - self.phase_t > 1.6:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.new_life()
            elif self.phase == "clear" and t - self.phase_t > 2:
                self.level += 1
                self.new_level()
            return
        self.popups = [p for p in self.popups if p[3] > t]
        if t < self.freeze_until:
            return
        if t >= self.fright_until:
            for g in self.ghosts:
                g.fright = False
            # scatter/chase waves; ghosts turn around whenever the wave changes
            self.wave_t += STEP
            acc, scatter = 0, False
            for s, ch in WAVES:
                if self.wave_t < acc + s:
                    scatter = True
                    break
                acc += s + ch
                if self.wave_t < acc:
                    break
            if scatter != self.scatter:
                self.scatter = scatter
                for g in self.ghosts:
                    if g.mode == "active":
                        g.dir = (-g.dir[0], -g.dir[1])
        p = self.pac
        if p.dir == (0, 0):
            self.pac_turn(p)
        before = (p.x, p.y)
        travel(p, self.pac_speed * STEP, self.pac_turn)
        p.walked += abs(p.x - before[0]) + abs(p.y - before[1])
        tile = (round(p.x), round(p.y))
        if tile in self.dots:
            self.dots.discard(tile)
            self.score += 10
        elif tile in self.power:
            self.power.discard(tile)
            self.score += 50
            self.fright_until = t + self.fright_time
            self.chain = 0
            for g in self.ghosts:
                if g.mode in ("active", "house", "leave"):
                    if g.mode == "active" and not g.fright:
                        g.dir = (-g.dir[0], -g.dir[1])
                    g.fright = True
        for g in self.ghosts:
            self.move_ghost(g)
        for g in self.ghosts:
            if g.mode == "active" and abs(g.x - p.x) + abs(g.y - p.y) < 0.8:
                if g.fright:
                    pts = 200 << min(self.chain, 3)
                    self.chain += 1
                    self.score += pts
                    g.mode, g.fright = "eyes", False
                    self.popups.append((round(g.x * PT) - 4, PY + round(g.y * PT) - 1, str(pts), t + 1))
                    self.freeze_until = t + 0.4
                else:
                    self.phase, self.phase_t = "dying", t
                    return
        if not self.dots and not self.power:
            self.phase, self.phase_t = "clear", t

    # ---- drawing ----

    def draw(self, c, now):
        c.px[:] = self.bg
        px, W = c.px, c.w
        for x, y in self.dots:
            px[(PY + y * PT + 1) * W + x * PT + 1] = 1
        if self.phase != "play" or int(self.t * 3) % 2:
            for x, y in self.power:
                put(c, x * PT, PY + y * PT, POWER)
        p = self.pac
        ppx, ppy = round(p.x * PT), PY + round(p.y * PT)
        if self.phase == "dying":
            k = int((self.t - self.phase_t - 0.5) * 4)
            if k < 0:
                put(c, ppx, ppy, PAC[p.face])
            elif k < len(PAC_DYING):
                put(c, ppx, ppy, PAC_DYING[k])
        else:
            frame = int(p.walked * 2.5) % 4
            put(c, ppx, ppy, (PAC[p.face], PAC_HALFOPEN[p.face], PAC_SHUT, PAC_HALFOPEN[p.face])[frame])
        if self.phase != "dying" or self.t - self.phase_t < 0.5:
            legs = int(self.t * 5) % 2
            left = self.fright_until - self.t
            for g in self.ghosts:
                gx, gy = round(g.x * PT), PY + round(g.y * PT)
                if g.mode in ("eyes", "enter"):
                    put(c, gx, gy, EYES)
                elif g.fright and (left > 2 or int(self.t * 6) % 2):
                    put(c, gx, gy, SCARED[legs])
                else:
                    put(c, gx, gy, GHOST[legs])
        for x, y, text, _ in self.popups:
            fill(c, x - 1, y - 1, SMALL.width(text) + 2, 7, 0)
            c.text(x, y, text, SMALL)
        # HUD
        fill(c, 0, 0, W, PY + 1, 0)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, "DEMO" if self.demo else f"LEVEL {self.level}", SMALL)
        for i in range(min(self.lives - 1, 5)):
            put(c, W - 3 - i * 5, 1, LIFE)
        if self.phase == "ready":
            banner(c, PY + 7 * PT - 1, "READY")
        elif self.phase == "clear":
            banner(c, PY + 7 * PT - 1, "LEVEL CLEAR")

    def draw_title(self, c, now):
        n = int(now * 8) % 64
        x = 72 - n
        put(c, x, 25, PAC[(-1, 0)] if int(now * 6) % 2 else PAC_SHUT)
        for i in range(4):
            put(c, x + 8 + i * 5, 25, GHOST[int(now * 5) % 2])
        for d in range(4, 84, 4):
            if d < x:
                c.set(d, 26)

    def describe(self):
        if self.state == "title":
            return "Pac-Man (title)"
        return f"Pac-Man ({'demo' if self.demo else self.state}, level {getattr(self, 'level', 1)}, score {self.score})"


# ---- Frogger -------------------------------------------------------------------------

LANE = 4        # lane pitch; sprites are 3 tall so neighbouring lanes stay apart
FTOP = 5        # top of the home row (the HUD sits above)
RIVER, MEDIAN, ROAD, START = (1, 2, 3), 4, (5, 6, 7), 8
SLOTS = [6, 24, 42, 60, 78]  # home bay centres
LOOP = 96  # lane objects wrap round a loop a little wider than the board
FROG = shape(["X.X", "XXX", "X.X"])
SPLAT = (shape(["X.X", ".X.", "X.X"]), shape([".X.", "X.X", ".X."]))
TURTLE = shape([".X.", "XXX", ".X."])
TURTLE_SINKING = shape(["...", ".X.", "..."])
CAR = {1: shape(["XXXX", "X.XX", "XXXX"]), -1: shape(["XXXX", "XX.X", "XXXX"])}
RACER = {1: shape(["XX..", "XXXX", "XX.."]), -1: shape(["..XX", "XXXX", "..XX"])}
TRUCK = {1: shape(["XXXXX.XX", "XXXXXXXX", "XXXXX.XX"]), -1: shape(["XX.XXXXX", "XXXXXXXX", "XX.XXXXX"])}
# lane: (kind, speed in dots/s, [(start x, size)]) - size is turtles per group, or width
FROG_LANES = {
    1: ("log", 8, [(0, 18), (34, 16), (66, 14)]),
    2: ("turtles", -9, [(0, 3), (24, 2), (44, 3), (72, 3)]),
    3: ("log", 12, [(0, 24), (50, 22)]),
    5: ("truck", -8, [(0, 8), (44, 8)]),
    6: ("racer", 16, [(0, 4), (52, 4)]),
    7: ("car", -10, [(0, 4), (30, 4), (62, 4)]),
}
DIVERS = (44,)  # turtle groups (by start x) that dive


@register
class Frogger(PackGame):
    id = "frogger"
    title = "Frogger"
    description = ("Hop across the road and the river into the five bays at the top. "
                   "Ride logs and turtles; some turtles dive. Each frog has a time limit.")
    params = [P("lives", "number", 3, "Lives", min=1, max=9),
              P("time", "number", 30, "Seconds per frog", min=10, max=99),
              P("difficulty", "select", "normal", "Traffic",
                options=[["easy", "Slow"], ["normal", "Normal"], ["hard", "Fast"]])]

    def reset(self):
        self.score, self.level = 0, 1
        self.lives = int(self.p["lives"])
        self.t = 0.0
        self.homes = [False] * 5
        self.set_speed()
        self.new_frog()
        self.ai_at = 0

    def set_speed(self):
        base = {"easy": 0.75, "hard": 1.3}.get(self.p["difficulty"], 1.0)
        self.speed = base * min(1 + 0.15 * (self.level - 1), 2.2)

    def new_frog(self):
        self.fx, self.fl = 41.0, START
        self.best = START
        self.frog_t = self.t
        self.phase, self.phase_t = "play", self.t

    def lane_y(self, i):
        return FTOP + i * LANE + 1

    # ---- lanes ----

    def objects(self, lane, t):
        """(screen x, width, group start) of each object in a lane at time t."""
        kind, v, objs = FROG_LANES[lane]
        off = v * self.speed * t
        for x0, size in objs:
            w = size * 4 - 1 if kind == "turtles" else size
            sx = (x0 + off) % LOOP - 6
            yield sx, w, x0
            if sx + w > LOOP - 6:
                yield sx - LOOP, w, x0

    def dive(self, x0, t):
        """Turtle group state: 0 up, 1 sinking or rising, 2 under."""
        if x0 not in DIVERS:
            return 0
        ph = t % 8
        if ph < 5.2:
            return 0
        if ph < 6:
            return 1
        return 2 if ph < 7.2 else 1

    def supported(self, lane, cx, t, margin=0.0):
        kind = FROG_LANES[lane][0]
        for sx, w, x0 in self.objects(lane, t):
            if sx + margin <= cx < sx + w - margin and (kind != "turtles" or self.dive(x0, t) < 2):
                return True
        return False

    def run_over(self, lane, fx, t, margin=0.0):
        for sx, w, _ in self.objects(lane, t):
            if fx + 2.5 + margin > sx and fx + 0.5 - margin < sx + w:
                return True
        return False

    # ---- frog ----

    def button(self, key, player):
        if self.phase == "play" and not self.demo and player == 1:
            self.hop(key)

    def hop(self, key):
        if key == "up":
            self.fl -= 1
            if self.fl == 0:
                return self.reach_home()
            if self.fl < self.best:
                self.best = self.fl
                self.score += 10
        elif key == "down":
            self.fl = min(self.fl + 1, START)
        elif key == "left":
            self.fx = max(self.fx - 3, 0)
        elif key == "right":
            self.fx = min(self.fx + 3, self.w - 3)

    def reach_home(self):
        cx = self.fx + 1.5
        i = min(range(5), key=lambda k: abs(SLOTS[k] - cx))
        if abs(SLOTS[i] - cx) > 2.5 or self.homes[i]:
            return self.die()
        self.homes[i] = True
        left = max(0, int(self.p["time"]) - (self.t - self.frog_t))
        self.score += 50 + 10 * int(left)
        if all(self.homes):
            self.score += 1000
            self.level += 1
            self.homes = [False] * 5
            self.set_speed()
            self.new_frog()
            self.phase, self.phase_t = "level", self.t
        else:
            self.new_frog()

    def die(self):
        self.phase, self.phase_t = "dying", self.t

    def step(self):
        self.t += STEP
        t = self.t
        if self.phase == "dying":
            if t - self.phase_t > 1.2:
                self.lives -= 1
                if self.lives <= 0:
                    return self.game_over()
                self.new_frog()
            return
        if self.phase == "level":
            if t - self.phase_t > 1.5:
                self.new_frog()
            return
        if self.demo and t >= self.ai_at:
            self.ai_at = t + 0.2
            self.autopilot()
            if self.phase != "play":
                return
        lane = self.fl
        if lane in RIVER:
            self.fx += FROG_LANES[lane][1] * self.speed * STEP
            if not self.supported(lane, self.fx + 1.5, t):
                return self.die()
        elif lane in ROAD and self.run_over(lane, self.fx, t):
            return self.die()
        if self.fx < -1 or self.fx > self.w - 2:
            return self.die()
        if t - self.frog_t > int(self.p["time"]):
            return self.die()

    def safe(self, lane, fx, ahead=0.35):
        t = self.t
        if lane == 0:
            cx = fx + 1.5
            return any(abs(s - cx) <= 2 and not h for s, h in zip(SLOTS, self.homes))
        if lane in RIVER:
            v = FROG_LANES[lane][1] * self.speed
            for dt in (0, ahead / 2, ahead):
                x = fx + v * dt
                if x < 0 or x > self.w - 3 or not self.supported(lane, x + 1.5, t + dt, 0.5):
                    return False
            return True
        if lane in ROAD:
            return not any(self.run_over(lane, fx, t + dt, 1) for dt in (0, ahead / 3, 2 * ahead / 3, ahead))
        return True

    def autopilot(self):
        """Demo: hop up when the next lane is safe, line up with a free bay, dodge otherwise."""
        free = [s for s, h in zip(SLOTS, self.homes) if not h] or SLOTS
        cx = self.fx + 1.5
        goal = min(free, key=lambda s: abs(s - cx))
        toward = "left" if goal < cx else "right"
        away = "right" if toward == "left" else "left"
        moves = {"up": (self.fl - 1, self.fx), "stay": (self.fl, self.fx),
                 "left": (self.fl, max(self.fx - 3, 0)), "right": (self.fl, min(self.fx + 3, self.w - 3)),
                 "down": (min(self.fl + 1, START), self.fx)}
        if self.fl == 1 and abs(goal - cx) > 2:
            order = [toward, "stay", away, "down"]
        else:
            order = ["up", "stay", toward, away, "down"]
        for k in order:
            if self.safe(*moves[k]):
                if k != "stay":
                    self.hop(k)
                return

    # ---- drawing ----

    def draw(self, c, now):
        t = self.t
        W = c.w
        y0 = self.lane_y(0)
        for x in range(W):  # the hedge, with a bay for each frog
            if all(abs(x - s) > 2 for s in SLOTS):
                c.set(x, y0)
                c.set(x, y0 + 2)
                if x % 2:
                    c.set(x, y0 + 1)
        for s, h in zip(SLOTS, self.homes):
            if h:
                put(c, s - 1, y0, FROG)
        for x in range(0, W, 3):  # the bank between road and river
            c.set(x, self.lane_y(MEDIAN) + 1)
        for lane, (kind, v, _) in FROG_LANES.items():
            y = self.lane_y(lane)
            d = 1 if v > 0 else -1
            for sx, w, x0 in self.objects(lane, t):
                sx = int(math.floor(sx))
                if kind == "log":
                    fill(c, sx, y, w, 1)
                    fill(c, sx, y + 2, w, 1)
                    fill(c, sx, y + 1, 1, 1)
                    fill(c, sx + w - 1, y + 1, 1, 1)
                    for k in range(sx + 4, sx + w - 3, 6):
                        c.set(k, y + 1)
                elif kind == "turtles":
                    state = self.dive(x0, t)
                    if state < 2:
                        for i in range(0, w, 4):
                            put(c, sx + i, y, TURTLE if state == 0 else TURTLE_SINKING)
                else:
                    put(c, sx, y, {"car": CAR, "racer": RACER, "truck": TRUCK}[kind][d])
        fy = self.lane_y(self.fl)
        if self.phase == "dying":
            fill(c, int(self.fx), fy, 3, 3, 0)
            put(c, int(self.fx), fy, SPLAT[int((t - self.phase_t) * 6) % 2])
        elif self.phase == "play":
            # clear the frog's square so it reads on top of a log or turtle
            fill(c, int(self.fx), fy, 3, 3, 0)
            put(c, int(self.fx), fy, FROG)
        left = max(0.0, 1 - (t - self.frog_t) / int(self.p["time"])) if self.phase == "play" else 1
        fill(c, 0, c.h - 1, int(W * left), 1)
        # HUD
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, "DEMO" if self.demo else f"LEVEL {self.level}", SMALL)
        for i in range(min(self.lives - 1, 5)):
            put(c, W - 3 - i * 5, 1, FROG)
        if self.phase == "level":
            banner(c, self.lane_y(MEDIAN) - 1, f"LEVEL {self.level}")

    def draw_title(self, c, now):
        put(c, int(now * 10) % 100 - 10, 24, TRUCK[1])
        put(c, 80 - (int(now * 14) % 90), 29, RACER[-1])
        put(c, 40, 20 + (int(now * 2) % 2), FROG)

    def describe(self):
        if self.state == "title":
            return "Frogger (title)"
        return f"Frogger ({'demo' if self.demo else self.state}, level {getattr(self, 'level', 1)}, score {self.score})"


# ---- Qix ----------------------------------------------------------------------------

QY = 6                  # field top (the HUD sits above)
QW, QH = 84, 36         # field size in dots
OPEN, EDGE, FAST, TRAIL, DEAD, SLOW = 0, 1, 2, 3, 4, 5  # field cell values
MARKER = shape(["XXX", "X.X", "XXX"])
SPARK = (shape(["X.X", ".X.", "X.X"]), shape([".X.", "X.X", ".X."]))


@register
class Qix(PackGame):
    id = "qix"
    title = "Qix"
    description = ("Move along the edges; hold A and move to draw into the open (B draws slowly "
                   "for double points). Close a shape to claim it. Don't let the Qix touch your line "
                   "or a spark catch you.")
    params = [P("lives", "number", 3, "Lives", min=1, max=9),
              P("target", "number", 75, "Percent to claim", min=50, max=90),
              P("difficulty", "select", "normal", "Enemies",
                options=[["easy", "Slow"], ["normal", "Normal"], ["hard", "Fast"]])]

    def reset(self):
        self.score, self.level = 0, 1
        self.lives = int(self.p["lives"])
        self.t = 0.0
        self.new_level()

    def new_level(self):
        g = self.g = bytearray(QW * QH)
        for x in range(QW):
            g[x] = g[(QH - 1) * QW + x] = EDGE
        for y in range(QH):
            g[y * QW] = g[y * QW + QW - 1] = EDGE
        self.total = (QW - 2) * (QH - 2)
        self.claimed = 0
        self.bg = bytearray(self.w * self.h)
        self.repaint()
        self.x, self.y = QW // 2, QH - 1
        self.trail = []
        self.slow = False
        mult = {"easy": 0.75, "hard": 1.3}.get(self.p["difficulty"], 1.0)
        self.qix_speed = 16 * mult * (1 + 0.12 * min(self.level - 1, 5))
        self.spark_speed = 9 * mult * (1 + 0.1 * min(self.level - 1, 5))
        a = [QW / 2 - 6, QH / 2, 0, 0]
        b = [QW / 2 + 6, QH / 2, 0, 0]
        self.qix = [a, b]
        for e in self.qix:
            ang = random.uniform(0, 2 * math.pi)
            e[2], e[3] = math.cos(ang) * self.qix_speed, math.sin(ang) * self.qix_speed
        self.qix_trail = deque(maxlen=4)
        self.qix_frame = 0
        self.plan = []
        self.wander = (1, 0)
        self.ai_draw = False
        self.new_life()

    def new_life(self):
        n = 2 if self.level < 3 else 3
        # sparks start on the edge across the field from the player
        sx, sy = self.nearest_edge(QW - 1 - self.x, 0 if self.y > QH // 2 else QH - 1)
        self.sparks = [Mover(sx, sy, (1 - 2 * (i % 2), 0), pref=1 - 2 * (i % 2), acc=0.0) for i in range(n)]
        self.spark_wait = self.t + 1.5  # sparks hold back a moment at the start of each life
        self.acc = 0.0
        self.stall = 0.0
        self.fuse = None
        self.phase, self.phase_t = "play", self.t

    def cell(self, x, y):
        if 0 <= x < QW and 0 <= y < QH:
            return self.g[y * QW + x]
        return -1

    def paint(self, x, y):
        v = self.g[y * QW + x]
        if v in (EDGE, TRAIL, DEAD):
            on = 1
        elif v == FAST:
            on = (x % 2 == 0 and y % 2 == 0)
        elif v == SLOW:
            on = (x + y) % 2 == 0
        else:
            on = 0
        self.bg[(y + QY) * self.w + x] = on

    def repaint(self):
        for y in range(QH):
            for x in range(QW):
                self.paint(x, y)

    # ---- the player ----

    def drawing_keys(self, player=1):
        if self.demo:
            return self.ai_draw
        return bool({(player, "a"), (player, "b")} & self.held)

    def try_move(self, d):
        """One dot of movement; returns True if the player moved."""
        nx, ny = self.x + d[0], self.y + d[1]
        v = self.cell(nx, ny)
        drawing = self.drawing_keys()
        if self.trail:
            if not drawing:
                return False
            if v == EDGE:
                if len(self.trail) == 1 and (nx, ny) == self.trail_from:
                    return False  # straight back where it started
                self.x, self.y = nx, ny
                self.close()
                return True
            if v != OPEN or self.touches_trail(nx, ny):
                return False
        elif v == EDGE:
            self.x, self.y = nx, ny
            return True
        elif v == OPEN and drawing:
            self.trail_from = (self.x, self.y)
            self.slow = self.demo is False and (1, "b") in self.held and (1, "a") not in self.held
        else:
            return False
        self.g[ny * QW + nx] = TRAIL
        self.trail.append((nx, ny))
        self.paint(nx, ny)
        self.x, self.y = nx, ny
        return True

    def touches_trail(self, nx, ny):
        """The line can't run alongside itself (except its own last dot)."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if self.cell(nx + dx, ny + dy) == TRAIL and (nx + dx, ny + dy) != (self.x, self.y):
                return True
        return False

    def close(self):
        """The line reached an edge: claim everything not on the Qix's side."""
        g = self.g
        keep = bytearray(QW * QH)
        start = None
        for e in self.qix:  # an open cell at (or right by) either end of the Qix
            for dy in (0, -1, 1, -2, 2):
                for dx in (0, -1, 1, -2, 2):
                    if start is None and self.cell(int(e[0]) + dx, int(e[1]) + dy) == OPEN:
                        start = (int(e[1]) + dy) * QW + int(e[0]) + dx
        if start is None:
            for i in range(QW * QH):  # nowhere to tell sides apart: claim just the line
                keep[i] = 1
        else:
            keep[start] = 1
            todo = [start]
            while todo:
                i = todo.pop()
                for j in (i - 1, i + 1, i - QW, i + QW):
                    if not keep[j] and g[j] == OPEN:
                        keep[j] = 1
                        todo.append(j)
        fill_v = SLOW if self.slow else FAST
        got = 0
        for i in range(QW * QH):
            v = g[i]
            if v == OPEN and not keep[i]:
                g[i] = fill_v
                got += 1
            elif v == TRAIL:
                g[i] = EDGE
        # edges with no open area beside them are retired (still drawn, no longer walkable)
        for y in range(QH):
            for x in range(QW):
                i = y * QW + x
                if g[i] == EDGE and not any(
                        0 <= x + dx < QW and 0 <= y + dy < QH and g[i + dy * QW + dx] == OPEN
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    g[i] = DEAD
        self.claimed += got + len(self.trail)
        self.score += (got + len(self.trail)) * (2 if self.slow else 1)
        self.trail = []
        self.plan = []
        self.fuse = None
        self.repaint()
        for e in self.qix:  # an end left outside the open area jumps back in
            if self.cell(int(e[0]), int(e[1])) != OPEN:
                e[0], e[1] = self.qix[0][0], self.qix[0][1]
        if self.cell(self.x, self.y) != EDGE:
            self.x, self.y = self.nearest_edge(self.x, self.y)
        for s in self.sparks:
            if self.cell(int(s.x), int(s.y)) != EDGE:
                s.x, s.y = self.nearest_edge(int(s.x), int(s.y))
        if self.percent() >= int(self.p["target"]):
            self.score += 1000 + 100 * (self.percent() - int(self.p["target"]))
            self.phase, self.phase_t = "clear", self.t

    def nearest_edge(self, x, y):
        best, where = None, (x, y)
        for i, v in enumerate(self.g):
            if v == EDGE:
                ex, ey = i % QW, i // QW
                d = abs(ex - x) + abs(ey - y)
                if best is None or d < best:
                    best, where = d, (ex, ey)
        return where

    def percent(self):
        return self.claimed * 100 // self.total

    def die(self):
        self.phase, self.phase_t = "dying", self.t

    def lose_life(self):
        for x, y in self.trail:
            self.g[y * QW + x] = OPEN
            self.paint(x, y)
        if self.trail:
            self.x, self.y = self.trail_from
        self.trail = []
        self.plan = []
        self.lives -= 1
        if self.lives <= 0:
            return self.game_over()
        self.new_life()

    # ---- enemies ----

    def move_qix(self):
        s = STEP
        for e in self.qix:
            if random.random() < 0.04:  # wander: turn a little now and then
                ang = math.atan2(e[3], e[2]) + random.uniform(-1, 1)
                e[2], e[3] = math.cos(ang) * self.qix_speed, math.sin(ang) * self.qix_speed
            nx, ny = e[0] + e[2] * s, e[1] + e[3] * s
            if self.cell(int(nx), int(e[1])) != OPEN:
                e[2] = -e[2]
                nx = e[0]
            if self.cell(int(nx), int(ny)) != OPEN:
                e[3] = -e[3]
                ny = e[1]
            e[0], e[1] = nx, ny
        a, b = self.qix
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx * dx + dy * dy > 14 ** 2:  # keep the two ends close: steer b back toward a
            n = math.hypot(dx, dy)
            b[2], b[3] = -dx / n * self.qix_speed, -dy / n * self.qix_speed
        self.qix_frame += 1
        if self.qix_frame % 3 == 0:
            self.qix_trail.append((a[0], a[1], b[0], b[1]))

    def qix_hits_line(self):
        if not self.trail:
            return False
        a, b = self.qix
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
        for i in range(n + 1):
            x = int(a[0] + (b[0] - a[0]) * i / n)
            y = int(a[1] + (b[1] - a[1]) * i / n)
            if self.cell(x, y) == TRAIL:
                return True
        return False

    def move_spark(self, s):
        x, y = int(s.x), int(s.y)
        dx, dy = s.dir
        left, right = (dy, -dx), (-dy, dx)
        for d in ((left, s.dir, right) if s.pref > 0 else (right, s.dir, left)):
            if self.cell(x + d[0], y + d[1]) == EDGE:
                s.dir = d
                s.x, s.y = x + d[0], y + d[1]
                return
        s.dir = (-dx, -dy)
        if self.cell(x - dx, y - dy) == EDGE:
            s.x, s.y = x - dx, y - dy

    # ---- demo ----

    def autopilot(self):
        """Demo: walk the edge, then cut out small boxes when the Qix is far away."""
        self.ai_draw = bool(self.trail)
        if self.plan:
            d, n = self.plan[0]
            self.ai_draw = True
            return d
        if self.trail:  # plan ran out mid-line: step onto an edge if one is next to us, else keep going
            ok = []
            for d in DIRS.values():
                v = self.cell(self.x + d[0], self.y + d[1])
                if v == EDGE and (len(self.trail) > 1 or (self.x + d[0], self.y + d[1]) != self.trail_from):
                    return d
                if v == OPEN and not self.touches_trail(self.x + d[0], self.y + d[1]):
                    ok.append(d)
            if not ok:
                return None
            if self.wander not in ok:
                self.wander = random.choice(ok)
            return self.wander
        qx, qy = self.qix[0][0], self.qix[0][1]
        qd = abs(qx - self.x) + abs(qy - self.y)
        spark = min((abs(s.x - self.x) + abs(s.y - self.y) for s in self.sparks), default=99)
        if (qd > 22 and random.random() < 0.05) or (spark < 10 and qd > 10):  # sparks can't follow a line
            ins = [d for d in DIRS.values() if self.cell(self.x + d[0], self.y + d[1]) == OPEN]
            if ins:
                d = random.choice(ins)
                side = random.choice(((d[1], -d[0]), (-d[1], d[0])))
                self.plan = [[d, random.randint(3, 8)], [side, random.randint(4, 14)], [(-d[0], -d[1]), 99]]
                self.ai_draw = True
                return d
        opts = [d for d in DIRS.values() if self.cell(self.x + d[0], self.y + d[1]) == EDGE]
        if spark < 8 and opts:  # run from the nearest spark
            s = min(self.sparks, key=lambda s: abs(s.x - self.x) + abs(s.y - self.y))
            self.wander = max(opts, key=lambda d: abs(s.x - self.x - d[0]) + abs(s.y - self.y - d[1]))
        elif self.wander not in opts or random.random() < 0.01:
            back = (-self.wander[0], -self.wander[1])
            self.wander = random.choice([d for d in opts if d != back] or opts or [self.wander])
        return self.wander

    def step(self):
        self.t += STEP
        t = self.t
        if self.phase == "dying":
            if t - self.phase_t > 1.2:
                self.lose_life()
            return
        if self.phase == "clear":
            if t - self.phase_t > 2.5:
                self.level += 1
                self.new_level()
            return
        if self.demo:
            d = self.autopilot()
        else:
            d = self.held_dir(1)
        moved = False
        if d:
            speed = 14 if (self.trail and self.slow) else 24
            self.acc += speed * STEP
            while self.acc >= 1:
                self.acc -= 1
                if self.try_move(d):
                    moved = True
                    if self.demo and self.plan:
                        self.plan[0][1] -= 1
                        if self.plan[0][1] <= 0:
                            self.plan.pop(0)
                else:
                    if self.demo and self.plan:
                        self.plan.pop(0)
                    self.acc = 0
                    break
                if self.phase != "play":
                    return
        else:
            self.acc = 0
        if not self.trail:
            for s in self.sparks:
                if abs(s.x - self.x) + abs(s.y - self.y) <= 1:
                    return self.die()
        # a line left standing still starts to burn from its start
        if self.trail and not moved:
            self.stall += STEP
            if self.stall > 0.6:
                self.fuse = (self.fuse or 0) + 12 * STEP
                if self.fuse >= len(self.trail):
                    return self.die()
        elif moved:
            self.stall, self.fuse = 0.0, None
        self.move_qix()
        if self.qix_hits_line():
            return self.die()
        if t >= self.spark_wait:
            for s in self.sparks:
                s.acc += self.spark_speed * STEP
                while s.acc >= 1:
                    s.acc -= 1
                    self.move_spark(s)
                    if not self.trail and abs(s.x - self.x) + abs(s.y - self.y) <= 1:
                        return self.die()

    # ---- drawing ----

    def draw(self, c, now):
        c.px[:] = self.bg
        for i, (ax, ay, bx, by) in enumerate(self.qix_trail):
            if i % 2 == len(self.qix_trail) % 2 or i == len(self.qix_trail) - 1:
                c.line(int(ax), int(ay) + QY, int(bx), int(by) + QY)
        a, b = self.qix
        c.line(int(a[0]), int(a[1]) + QY, int(b[0]), int(b[1]) + QY)
        # markers get a cleared 3x3 so they stand out from the line they're on
        if self.fuse is not None and self.trail:
            fx, fy = self.trail[min(int(self.fuse), len(self.trail) - 1)]
            fill(c, fx - 1, fy - 1 + QY, 3, 3, 0)
            put(c, fx - 1, fy - 1 + QY, SPARK[0])
        if self.t >= self.spark_wait or int(now * 4) % 2:
            for s in self.sparks:
                fill(c, int(s.x) - 1, int(s.y) - 1 + QY, 3, 3, 0)
                put(c, int(s.x) - 1, int(s.y) - 1 + QY, SPARK[0])
        if self.phase != "dying" or int(now * 8) % 2:
            fill(c, self.x - 1, self.y - 1 + QY, 3, 3, 0)
            put(c, self.x - 1, self.y - 1 + QY, MARKER)
        fill(c, 0, 0, c.w, QY - 1, 0)
        c.text(0, 0, str(self.score), SMALL)
        c.text_center(0, "DEMO" if self.demo else f"{self.percent()}%/{int(self.p['target'])}%", SMALL)
        for i in range(min(self.lives - 1, 5)):
            put(c, c.w - 3 - i * 5, 0, MARKER)
        if self.phase == "clear":
            banner(c, QY + QH // 2 - 2, f"{self.percent()}% CLAIMED")

    def draw_title(self, c, now):
        c.rect(14, 20, 56, 13)
        k = now * 3
        c.line(42 + int(math.cos(k) * 8), 26 + int(math.sin(k) * 3),
               42 - int(math.cos(k * 1.3) * 8), 26 - int(math.sin(k * 1.7) * 3))
        for x in range(15, 30):
            for y in range(21, 32):
                if x % 2 == 0 and y % 2 == 0:
                    c.set(x, y)

    def describe(self):
        if self.state == "title":
            return "Qix (title)"
        return f"Qix ({'demo' if self.demo else self.state}, level {getattr(self, 'level', 1)}, score {self.score})"


# ---- Bomberman ------------------------------------------------------------------------

BT = 5          # tile pitch: 4x4 dots of content and a dot of space
BX, BY = 4, 5   # arena frame's top left corner (the HUD sits above)
BW, BH = 15, 7  # arena in tiles
HARD, SOFT = 1, 2
FUSE = 2.5
FLAME_TIME = 0.5
BOMBERS = [  # one head per player so they can be told apart; two leg frames
    [".XX.", ".XX."], ["X..X", ".XX."], ["XXXX", "X..X"], [".XX.", "X..X"],
]
BOMBER_PTS = [(shape(head + ["XXXX", "X..X"]), shape(head + ["XXXX", ".XX."])) for head in BOMBERS]
HARD_PTS = shape(["XXXX", "XXXX", "XXXX", "XXXX"])
SOFT_PTS = shape(["XXXX", "X..X", "X..X", "XXXX"])
BOMB_PTS = (shape([".XX.", "XXXX", "XXXX", ".XX."]), shape(["....", ".XX.", ".XX.", "...."]))
POWERUPS = {"bomb": shape([".XX.", "X..X", "X..X", ".XX."]), "fire": shape(["X..X", ".XX.", ".XX.", "X..X"])}
CORNERS = [(0, 0), (BW - 1, BH - 1), (BW - 1, 0), (0, BH - 1)]
HURRY = 45  # seconds into a round before the walls start closing in


def spiral(w, h):
    """Tiles from the outside in, ring by ring, clockwise."""
    out, x0, y0, x1, y1 = [], 0, 0, w - 1, h - 1
    while x0 <= x1 and y0 <= y1:
        out += [(x, y0) for x in range(x0, x1 + 1)]
        out += [(x1, y) for y in range(y0 + 1, y1 + 1)]
        if y1 > y0:
            out += [(x, y1) for x in range(x1 - 1, x0 - 1, -1)]
        if x1 > x0:
            out += [(x0, y) for y in range(y1 - 1, y0, -1)]
        x0, y0, x1, y1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
    return out


SPIRAL = spiral(BW, BH)


@register
class Bomberman(PackGame):
    id = "bomberman"
    title = "Bomberman"
    description = ("Arrows to walk, A or B drops a bomb. Blast the soft blocks for extra bombs and "
                   "longer flames; last bomber standing wins the round. Other players join from their "
                   "own controller; empty places are played by the computer.")
    params = [P("bombers", "number", 4, "Bombers (incl. computer)", min=2, max=4),
              P("wins", "number", 3, "Round wins needed", min=1, max=9),
              P("blocks", "select", "normal", "Soft blocks",
                options=[["few", "Few"], ["normal", "Normal"], ["many", "Many"]]),
              P("difficulty", "select", "normal", "Computer",
                options=[["easy", "Easy"], ["normal", "Normal"], ["hard", "Hard"]])]

    def reset(self):
        n = int(self.p["bombers"])
        self.wins = [0] * n
        self.score = 0
        self.round = 0
        self.t = 0.0
        self.new_round()

    def new_round(self):
        self.round += 1
        self.round_t = self.t
        density = {"few": 0.3, "many": 0.75}.get(self.p["blocks"], 0.5)
        g = self.grid = [[0] * BW for _ in range(BH)]
        keep = set()
        for cx, cy in CORNERS:  # room to escape the first bomb
            for dx, dy in ((0, 0), (1, 0), (2, 0), (0, 1), (0, 2), (-1, 0), (-2, 0), (0, -1), (0, -2)):
                keep.add((cx + dx, cy + dy))
        for y in range(BH):
            for x in range(BW):
                if x % 2 and y % 2:
                    g[y][x] = HARD
                elif (x, y) not in keep and random.random() < density:
                    g[y][x] = SOFT
        self.bombs = {}    # (x, y) -> [owner index, range, explode time]
        self.flames = {}   # (x, y) -> until
        self.items = {}    # (x, y) -> "bomb" | "fire"
        self.bombers = []
        for i in range(len(self.wins)):
            x, y = CORNERS[i]
            self.bombers.append(Mover(x, y, (0, 0), i=i, alive=True, died=0, cap=1, range=2, out=0,
                                      walked=0.0, think=0, face=(0, 1)))
        self.phase, self.phase_t = "play", self.t
        self.ending = None
        self.closing = 0  # tiles of SPIRAL filled in so far

    # ---- arena ----

    def free(self, x, y):
        return 0 <= x < BW and 0 <= y < BH and not self.grid[y][x] and (x, y) not in self.bombs

    def blast(self, x, y, r):
        tiles = [(x, y)]
        for dx, dy in DIRS.values():
            for k in range(1, r + 1):
                nx, ny = x + dx * k, y + dy * k
                if not (0 <= nx < BW and 0 <= ny < BH) or self.grid[ny][nx] == HARD:
                    break
                tiles.append((nx, ny))
                if self.grid[ny][nx] == SOFT or (nx, ny) in self.bombs:
                    break
        return tiles

    def explode(self, pos):
        owner, r, _ = self.bombs.pop(pos)
        if owner < len(self.bombers):
            self.bombers[owner].out = max(0, self.bombers[owner].out - 1)
        until = self.t + FLAME_TIME
        for x, y in self.blast(pos[0], pos[1], r):
            self.flames[(x, y)] = until
            if self.grid[y][x] == SOFT:
                self.grid[y][x] = 0
                if random.random() < 0.3:
                    self.items[(x, y)] = random.choice(("bomb", "fire"))
                    self.flames[(x, y)] = self.t  # don't burn the item just revealed
            elif (x, y) in self.items and self.flames[(x, y)] > self.t:
                del self.items[(x, y)]
            if (x, y) in self.bombs:
                self.bombs[(x, y)][2] = self.t  # chain reaction

    def tile(self, b):
        return round(b.x), round(b.y)

    def drop(self, b):
        pos = self.tile(b)
        if b.alive and b.out < b.cap and pos not in self.bombs:
            self.bombs[pos] = [b.i, b.range, self.t + FUSE]
            b.out = b.out + 1
            return True
        return False

    # ---- players ----

    def button(self, key, player):
        if self.phase == "play" and player <= len(self.bombers) and self.human(player) and key in ("a", "b"):
            self.drop(self.bombers[player - 1])

    def human_turn(self, b):
        d = self.held_dir(b.i + 1)
        x, y = self.tile(b)
        if d and self.free(x + d[0], y + d[1]):
            b.dir = b.face = d
        else:
            b.dir = (0, 0)
            if d:
                b.face = d

    # ---- computer players ----

    def danger(self, extra=None, careless=False):
        bad = set(k for k, until in self.flames.items() if until > self.t)
        for (x, y), (_, r, at) in self.bombs.items():
            if not careless or at - self.t < 1.2:
                bad.update(self.blast(x, y, r))
        if extra:
            bad.update(self.blast(extra[0], extra[1], extra[2]))
        if self.t - self.round_t > HURRY - 3:
            bad.update(SPIRAL[self.closing:self.closing + 3])
        return bad

    def bfs(self, start, goal, avoid, limit=99):
        """First step and distance of the shortest walk to a tile where goal() holds."""
        seen = {start}
        todo = deque([(start, None, 0)])
        while todo:
            (x, y), first, n = todo.popleft()
            if first and goal(x, y):
                return first, n
            if n >= limit:
                continue
            for d in DIRS.values():
                nt = (x + d[0], y + d[1])
                if nt not in seen and self.free(*nt) and nt not in avoid:
                    seen.add(nt)
                    todo.append((nt, first or d, n + 1))
        return None, None

    def cpu_turn(self, b):
        x, y = self.tile(b)
        b.dir = (0, 0)
        burning = {k for k, until in self.flames.items() if until > self.t}
        # now and then a computer player overlooks a bomb that isn't about to go off
        slip = {"easy": 0.3, "hard": 0.03}.get(self.p["difficulty"], 0.12)
        bad = self.danger(careless=random.random() < slip)
        if (x, y) in bad:  # get out of the way of a bomb
            d, _ = self.bfs((x, y), lambda tx, ty: (tx, ty) not in bad, burning)
            if d:
                b.dir = b.face = d
            return
        enemies = [self.tile(o) for o in self.bombers if o.alive and o is not b]
        # bomb something worthwhile if there's a way out afterwards
        target = any(0 <= x + dx < BW and 0 <= y + dy < BH and self.grid[y + dy][x + dx] == SOFT
                     for dx, dy in DIRS.values())
        reach = set(self.blast(x, y, b.range))
        target = target or any(e in reach for e in enemies)
        if target and b.out < b.cap and (random.random() < 0.7 or any(e in reach for e in enemies)):
            after = self.danger((x, y, b.range))
            d, n = self.bfs((x, y), lambda tx, ty: (tx, ty) not in after, bad | burning, limit=6)
            if d and n <= 5:
                self.bombs[(x, y)] = [b.i, b.range, self.t + FUSE]
                b.out = b.out + 1
                b.dir = b.face = d
                return
        en = set(enemies)
        # an enemy close by: get somewhere a bomb would reach them
        d, _ = self.bfs((x, y), lambda tx, ty: (tx, ty) not in en and any(e in en for e in self.blast(tx, ty, b.range)),
                        bad | burning, limit=7)
        if d and (x + d[0], y + d[1]) not in bad:
            b.dir = b.face = d
            return
        # otherwise walk toward a power-up, the nearest enemy or the nearest soft block

        def goal(tx, ty):
            if (tx, ty) in self.items or (tx, ty) in en:
                return True
            return any(0 <= tx + dx < BW and 0 <= ty + dy < BH and self.grid[ty + dy][tx + dx] == SOFT
                       for dx, dy in DIRS.values())

        d, _ = self.bfs((x, y), goal, bad | burning)
        if d is None and en:  # no soft blocks left: just walk at somebody
            d, _ = self.bfs((x, y), lambda tx, ty: any(abs(tx - ex) + abs(ty - ey) <= 1 for ex, ey in en), bad | burning)
        if d and (x + d[0], y + d[1]) not in bad:
            b.dir = b.face = d

    def turn_for(self, b):
        if self.human(b.i + 1):
            return self.human_turn
        return self.cpu_turn

    # ---- the clock ----

    def step(self):
        self.t += STEP
        t = self.t
        if self.phase == "end":
            if t - self.phase_t > 2.5:
                need = int(self.p["wins"])
                if max(self.wins) >= need:
                    return self.game_over()
                self.new_round()
            return
        while True:  # a blast sets off any bomb it reaches in the same step
            due = next((p for p, bomb in self.bombs.items() if bomb[2] <= t), None)
            if due is None:
                break
            self.explode(due)
        self.flames = {k: v for k, v in self.flames.items() if v >= t}
        for b in self.bombers:
            if not b.alive:
                continue
            decide = self.turn_for(b)
            if b.dir == (0, 0):
                if decide == self.cpu_turn and t < b.think:
                    continue
                decide(b)
                if b.dir == (0, 0):
                    b.think = t + 0.2
            elif decide == self.human_turn:
                d = self.held_dir(b.i + 1)
                if d == (-b.dir[0], -b.dir[1]):
                    b.dir = b.face = d  # turn back mid-tile
            before = (b.x, b.y)
            travel(b, 4.5 * STEP, decide)
            b.walked += abs(b.x - before[0]) + abs(b.y - before[1])
            pos = self.tile(b)
            if pos in self.items:
                if self.items.pop(pos) == "bomb":
                    b.cap = min(b.cap + 1, 6)
                else:
                    b.range = min(b.range + 1, 8)
            if self.flames.get(pos, -1) > t:
                b.alive, b.died = False, t
        # hurry up: hard blocks drop in from the outside, crushing whatever is there
        due = int((t - self.round_t - HURRY) / 0.4) + 1 if t - self.round_t > HURRY else 0
        while self.closing < min(due, len(SPIRAL)):
            x, y = SPIRAL[self.closing]
            self.closing += 1
            self.grid[y][x] = HARD
            self.items.pop((x, y), None)
            if (x, y) in self.bombs:
                owner = self.bombs.pop((x, y))[0]
                self.bombers[owner].out -= 1
            for b in self.bombers:
                if b.alive and self.tile(b) == (x, y):
                    b.alive, b.died = False, t
        alive = [b for b in self.bombers if b.alive]
        timeout = t - self.round_t > HURRY + 0.4 * len(SPIRAL) + 5
        if len(alive) <= 1 or timeout:
            # wait a moment so bombers caught by the same blast all count
            if self.ending is None:
                self.ending = t
            if t - self.ending > 0.2 or timeout:
                alive = [b for b in self.bombers if b.alive]
                self.winner = alive[0].i if len(alive) == 1 and not timeout else None
                if self.winner is not None:
                    self.wins[self.winner] += 1
                self.score = self.wins[0]
                self.phase, self.phase_t = "end", t
                self.ending = None
        else:
            self.ending = None

    def name(self, i):
        return f"P{i + 1}" if self.human(i + 1) else f"CPU{i + 1}"

    # ---- drawing ----

    def draw(self, c, now):
        t = self.t
        c.rect(BX, BY, BW * BT + 1, BH * BT + 1)
        ox, oy = BX + 1, BY + 1
        for y, row in enumerate(self.grid):
            for x, v in enumerate(row):
                if v:
                    put(c, ox + x * BT, oy + y * BT, HARD_PTS if v == HARD else SOFT_PTS)
        for (x, y), kind in self.items.items():
            put(c, ox + x * BT, oy + y * BT, POWERUPS[kind])
        for (x, y), (_, _, at) in self.bombs.items():
            put(c, ox + x * BT, oy + y * BT, BOMB_PTS[1 if (at - t < 0.8 and int(t * 8) % 2) else 0])
        for (x, y), until in self.flames.items():
            if until <= t:
                continue
            X, Y = ox + x * BT, oy + y * BT
            horiz = (x - 1, y) in self.flames or (x + 1, y) in self.flames
            vert = (x, y - 1) in self.flames or (x, y + 1) in self.flames
            if horiz or not vert:
                fill(c, X - 1 if (x - 1, y) in self.flames else X, Y + 1, BT + 1 if horiz else BT - 1, 2)
            if vert or not horiz:
                fill(c, X + 1, Y - 1 if (x, y - 1) in self.flames else Y, 2, BT + 1 if vert else BT - 1)
        for b in self.bombers:
            if not b.alive and (t - b.died > 1 or int(t * 8) % 2):
                continue
            frame = int(b.walked * 3) % 2 if b.dir != (0, 0) else 0
            put(c, ox + round(b.x * BT), oy + round(b.y * BT), BOMBER_PTS[b.i][frame])
        # HUD: every bomber's look and round wins
        for b in self.bombers:
            x = b.i * 18
            if b.alive or int(t * 2) % 2:
                put(c, x, 0, BOMBER_PTS[b.i][0])
            c.text(x + 6, 0, str(self.wins[b.i]), SMALL)
        if self.demo:
            c.text(c.w - SMALL.width("DEMO"), 0, "DEMO", SMALL)
        if self.phase == "end":
            msg = "DRAW" if self.winner is None else f"{self.name(self.winner)} WINS ROUND"
            banner(c, BY + 16, msg)

    def result(self):
        best = max(range(len(self.wins)), key=lambda i: self.wins[i])
        return f"{self.name(best)} WINS", "-".join(map(str, self.wins))

    def draw_title(self, c, now):
        for i in range(4):
            put(c, 20 + i * 12, 22, BOMBER_PTS[i][int(now * 3 + i) % 2])
        put(c, 40, 30, BOMB_PTS[int(now * 4) % 2])

    def describe(self):
        if self.state == "title":
            return "Bomberman (title)"
        wins = "-".join(map(str, getattr(self, "wins", [])))
        return f"Bomberman ({'demo' if self.demo else self.state}, round {getattr(self, 'round', 1)}, wins {wins})"


# ---- Tank Combat ------------------------------------------------------------------------

TY = 6                      # field top
DIR8 = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
# Layouts as rectangles (x, y, w, h) in field dots (84 x 36, border included; it's added
# anyway). Passages are at least 9 dots wide: tanks are 7.
TANK_LAYOUTS = {
    "open": [(26, 10, 3, 16), (55, 10, 3, 16)],
    "maze": [(16, 0, 3, 13), (16, 23, 3, 13), (65, 0, 3, 13), (65, 23, 3, 13), (32, 16, 20, 3),
             (41, 0, 2, 5), (41, 31, 2, 5)],
    "pillars": [(20, 10, 5, 4), (59, 10, 5, 4), (20, 22, 5, 4), (59, 22, 5, 4), (39, 15, 6, 6)],
}


def tank_sprite(k):
    """Tank drawn by sampling its shape rotated to direction k (of 8), so diagonals look right."""
    a = k * math.pi / 4
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for py in range(-4, 5):
        for px in range(-4, 5):
            # rotate the dot back into the tank's own frame (barrel along -y)
            x = px * ca + py * sa
            y = -px * sa + py * ca
            tread = 2.4 <= abs(x) <= 3.5 and -2.6 <= y <= 3.5
            body = abs(x) <= 1.5 and -1.5 <= y <= 2.5
            barrel = abs(x) <= 0.5 and -4.5 <= y <= -1.5
            if tread or body or barrel:
                pts.append((px, py))
    return pts


TANKS = [tank_sprite(k) for k in range(8)]
SPIN, SAFE = 1.0, 1.0  # a hit tank spins, then can't be hit again for a moment


@register
class Tanks(PackGame):
    id = "tanks"
    title = "Tank Combat"
    description = ("Up drives forward, down backs up, left/right turn, A or B fires. First to the "
                   "set number of hits wins. Player 2 is the computer unless someone presses buttons "
                   "as player 2.")
    params = [P("to", "number", 5, "Hits to win", min=1, max=20),
              P("layout", "select", "maze", "Walls",
                options=[["open", "Open field"], ["maze", "Maze"], ["pillars", "Pillars"]]),
              P("bounce", "checkbox", True, "Shells bounce off walls"),
              P("difficulty", "select", "normal", "Computer",
                options=[["easy", "Easy"], ["normal", "Normal"], ["hard", "Hard"]])]

    def reset(self):
        self.score = 0
        self.hits = [0, 0]
        self.t = 0.0
        W, H = self.w, self.h
        wall = self.wall = bytearray(W * H)
        for x in range(W):
            for y in range(TY):
                wall[y * W + x] = 1
            wall[TY * W + x] = wall[(H - 1) * W + x] = 1
        for y in range(TY, H):
            wall[y * W] = wall[y * W + W - 1] = 1
        for x0, y0, w, h in TANK_LAYOUTS.get(self.p["layout"], TANK_LAYOUTS["maze"]):
            for y in range(TY + y0, min(TY + y0 + h, H)):
                for x in range(x0, min(x0 + w, W)):
                    wall[y * W + x] = 1
        self.bg = bytearray(wall)
        self.bg[:TY * W] = bytes(TY * W)
        # coarse map for the computer's route finding: 3x3-dot cells a tank fits in
        self.cw, self.ch = W // 3, (H - TY) // 3
        self.room = bytearray(self.cw * self.ch)
        for cy in range(self.ch):
            for cx in range(self.cw):
                self.room[cy * self.cw + cx] = self.fits(cx * 3 + 1, TY + cy * 3 + 1)
        mid = TY + (H - TY) / 2
        self.tanks = [Mover(7, mid, a=2, shell=None, spin=0, turn_at=0, i=0, route=[], route_at=0, stuck=0),
                      Mover(W - 8, mid, a=6, shell=None, spin=0, turn_at=0, i=1, route=[], route_at=0, stuck=0)]
        self.phase, self.phase_t = "play", 0.0

    def cpu(self, i):
        return not self.human(i + 1)

    def fits(self, x, y):
        W, wall = self.w, self.wall
        x0, y0 = int(round(x)) - 3, int(round(y)) - 3
        if x0 < 0 or y0 < 0 or x0 + 7 > W or y0 + 7 > self.h:
            return False
        for yy in range(y0, y0 + 7):
            row = yy * W
            if any(wall[row + x0:row + x0 + 7]):
                return False
        return True

    def blocked_by_tank(self, me, x, y):
        o = self.tanks[1 - me.i]
        return abs(o.x - x) < 7 and abs(o.y - y) < 7

    def drive(self, tk, sign):
        dx, dy = DIR8[tk.a]
        n = 0.7071 if dx and dy else 1
        sp = (14 if sign > 0 else 8) * STEP * n * sign
        moved = False
        for ax, ay in ((dx * sp, 0), (0, dy * sp)):  # per axis so tanks slide along walls
            nx, ny = tk.x + ax, tk.y + ay
            if (ax or ay) and self.fits(nx, ny) and not self.blocked_by_tank(tk, nx, ny):
                tk.x, tk.y = nx, ny
                moved = True
        return moved

    def fire(self, tk):
        if tk.shell is None and tk.spin <= self.t:
            dx, dy = DIR8[tk.a]
            n = 0.7071 if dx and dy else 1
            tk.shell = Mover(tk.x + dx * 4, tk.y + dy * 4, (dx * n * 50, dy * n * 50), born=self.t, bounces=0)

    def button(self, key, player):
        if self.phase == "play" and player in (1, 2) and self.human(player) and key in ("a", "b"):
            self.fire(self.tanks[player - 1])

    def control(self, tk):
        held = {k for p, k in self.held if p == tk.i + 1}
        turn = ("right" in held) - ("left" in held)
        if turn and self.t >= tk.turn_at:
            tk.a = (tk.a + turn) % 8
            tk.turn_at = self.t + 0.16
        elif not turn:
            tk.turn_at = 0
        if "up" in held:
            self.drive(tk, 1)
        elif "down" in held:
            self.drive(tk, -1)

    # ---- computer ----

    def clear_shot(self, x0, y0, x1, y1):
        n = int(max(abs(x1 - x0), abs(y1 - y0)) / 2) + 1
        W, wall = self.w, self.wall
        for k in range(1, n):
            x = int(x0 + (x1 - x0) * k / n)
            y = int(y0 + (y1 - y0) * k / n)
            if wall[y * W + x]:
                return False
        return True

    def route(self, tk, goal):
        """Breadth-first search over the coarse map; returns cell centres to drive through."""
        cw, ch, room = self.cw, self.ch, self.room

        def cell(x, y):
            return min(max(int(x // 3), 0), cw - 1), min(max(int((y - TY) // 3), 0), ch - 1)

        start, end = cell(tk.x, tk.y), cell(*goal)
        prev = {start: None}
        todo = deque([start])
        while todo:
            cur = todo.popleft()
            if cur == end:
                break
            x, y = cur
            for dx, dy in DIR8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < cw and 0 <= ny < ch and (nx, ny) not in prev and room[ny * cw + nx] \
                        and (not (dx and dy) or (room[y * cw + nx] and room[ny * cw + x])):
                    prev[(nx, ny)] = cur
                    todo.append((nx, ny))
        if end not in prev:
            return []
        path = []
        while end and end != start:
            path.append((end[0] * 3 + 1, TY + end[1] * 3 + 1))
            end = prev[end]
        return path[::-1]

    def aim(self, tk, x, y):
        ang = math.atan2(x - tk.x, -(y - tk.y))  # 0 = up, clockwise
        return round(ang / (math.pi / 4)) % 8

    def cpu_control(self, tk):
        t = self.t
        o = self.tanks[1 - tk.i]
        level = {"easy": 0, "hard": 2}.get(self.p["difficulty"], 1)
        turn_every = (0.3, 0.2, 0.13)[level]
        if t >= tk.route_at:
            tk.route_at = t + 0.5
            tk.route = self.route(tk, (o.x, o.y))
        while tk.route and abs(tk.route[0][0] - tk.x) + abs(tk.route[0][1] - tk.y) < 2.5:
            tk.route.pop(0)
        lined_up = False
        if self.clear_shot(tk.x, tk.y, o.x, o.y):
            want = self.aim(tk, o.x, o.y)
            dx, dy = DIR8[tk.a]
            rx, ry = o.x - tk.x, o.y - tk.y
            n = math.hypot(dx, dy)
            # lined up when the shell would pass within ~3 dots of the other tank
            lined_up = (rx * dx + ry * dy) / n > 0 and abs(rx * dy - ry * dx) / n < 3.5
            if lined_up and random.random() < (0.02, 0.05, 0.12)[level]:
                self.fire(tk)
        else:
            goal = (o.x, o.y)
            if tk.route:
                ahead = tk.route[min(len(tk.route) - 1, 2)]
                goal = ahead if self.clear_shot(tk.x, tk.y, *ahead) else tk.route[0]
            want = self.aim(tk, *goal)
        if want != tk.a and t >= tk.turn_at:
            tk.a = (tk.a + (1 if (want - tk.a) % 8 < 4 else -1)) % 8
            tk.turn_at = t + turn_every
        # drive while roughly facing the way it wants to go; hold still once lined up close by
        if (tk.a - want) % 8 in (0, 1, 7) and not (lined_up and math.hypot(o.x - tk.x, o.y - tk.y) < 30):
            if self.drive(tk, 1):
                tk.stuck = 0
            else:
                tk.stuck += STEP
        if tk.stuck > 0.6:  # wedged on a corner or against the other tank: back up and turn
            self.drive(tk, -1)
            if t >= tk.turn_at:
                tk.a = (tk.a + random.choice((1, -1))) % 8
                tk.turn_at = t + 0.2
            if tk.stuck > 1.2:
                tk.stuck = 0

    # ---- the clock ----

    def move_shell(self, tk):
        s = tk.shell
        W, wall = self.w, self.wall
        for _ in range(2):  # two half steps so shells don't skip through thin walls
            vx, vy = s.dir
            nx, ny = s.x + vx * STEP / 2, s.y + vy * STEP / 2
            if not (0 <= nx < W and 0 <= ny < self.h):
                tk.shell = None
                return
            if wall[int(ny) * W + int(nx)]:
                if not self.p["bounce"] or s.bounces >= 3:
                    tk.shell = None
                    return
                s.bounces += 1
                if wall[int(s.y) * W + int(nx)]:
                    vx = -vx
                if wall[int(ny) * W + int(s.x)]:
                    vy = -vy
                if vx == s.dir[0] and vy == s.dir[1]:
                    vx, vy = -vx, -vy
                s.dir = (vx, vy)
                continue
            s.x, s.y = nx, ny
            o = self.tanks[1 - tk.i]
            if abs(o.x - s.x) < 3.5 and abs(o.y - s.y) < 3.5 and o.spin + SAFE <= self.t:
                tk.shell = None
                self.hits[tk.i] += 1
                o.spin = self.t + SPIN
                o.shell = None
                self.score = self.hits[0]
                if self.hits[tk.i] >= int(self.p["to"]):
                    self.phase, self.phase_t = "end", self.t
                return
        if self.t - s.born > 2.5:
            tk.shell = None

    def step(self):
        self.t += STEP
        t = self.t
        if self.phase == "end":
            if t - self.phase_t > 1.5:
                self.game_over()
            return
        for tk in self.tanks:
            if tk.spin > t:  # hit: spins on the spot for a second
                if int(t * 40) % 3 == 0:
                    tk.a = (tk.a + 1) % 8
                continue
            if self.cpu(tk.i):
                self.cpu_control(tk)
            else:
                self.control(tk)
        for tk in self.tanks:
            if tk.shell:
                self.move_shell(tk)

    # ---- drawing ----

    def draw(self, c, now):
        c.px[:] = self.bg
        for tk in self.tanks:
            if tk.shell:
                fill(c, int(tk.shell.x), int(tk.shell.y), 2, 2)
            if not (tk.spin <= self.t < tk.spin + SAFE and int(now * 8) % 2):  # blinks while safe
                put(c, round(tk.x), round(tk.y), TANKS[tk.a])
        names = ("P1", "CPU" if self.cpu(1) else "P2")
        c.text(0, 0, f"{names[0]} {self.hits[0]}", SMALL)
        right = f"{self.hits[1]} {names[1]}"
        c.text(c.w - SMALL.width(right), 0, right, SMALL)
        c.text_center(0, "DEMO" if self.demo else f"TO {int(self.p['to'])}", SMALL)

    def result(self):
        p2 = "CPU" if self.cpu(1) else "P2"
        return ("P1 WINS" if self.hits[0] > self.hits[1] else f"{p2} WINS"), f"{self.hits[0]}-{self.hits[1]}"

    def draw_title(self, c, now):
        k = int(now * 2) % 8
        put(c, 30, 27, TANKS[k])
        put(c, 54, 27, TANKS[(k + 4) % 8])

    def describe(self):
        if self.state == "title":
            return "Tank Combat (title)"
        p2 = "CPU" if self.cpu(1) else "P2"
        return f"Tank Combat ({'demo' if self.demo else self.state}, P1 {self.hits[0]} - {self.hits[1]} {p2})"
