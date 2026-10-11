"""Games, controlled from the /play page (or the keyboard on the main page).

Input keys: up, down, left, right, a (rotate/action), b (drop). Each press
arrives as action "down" and its release as "up".

Every Game files its score in the shared high-score table (apps/scores.py) when
a game ends. A score that makes the top 5 asks for three initials (up/down picks
a letter, A or right moves on, B or left goes back), then the table shows. The
title screen alternates "PRESS ANY BUTTON" with the best score. The hooks wrap
render() and input() of every subclass, so each game family's own flow (title,
demo, game over) stays as it is.
"""

import functools
import random
import time

from canvas import Canvas
from fonts import NORMAL, SMALL

from . import scores
from .base import REGISTRY, App, P, register

DEMO_AFTER = 8     # idle seconds on a title screen before the classic games play a demo
DEMO_LENGTH = 60   # a demo goes back to the title after this long at most
DEMO = P("demo", "checkbox", True, "Play a demo when nobody is playing")
ENTRY_TIMEOUT = 40  # seconds without a button before initials entry gives up (the score stays, unnamed)
TABLE_S = 8         # seconds the top 5 shows after initials
BADGE_S = 3.0       # seconds each achievement card shows after a game


def tag(c, x, y, s):
    """SMALL text on a cleared box so it reads over a playfield; x=None centres it."""
    tw = SMALL.width(s)
    x = (c.w - tw) // 2 if x is None else (c.w - tw + x + 1 if x < 0 else x)
    for yy in range(max(y - 1, 0), min(y + 6, c.h)):
        i = yy * c.w
        c.px[i + max(x - 1, 0):i + min(x + tw + 1, c.w)] = bytes(min(x + tw + 1, c.w) - max(x - 1, 0))
    c.text(x, y, s, SMALL)


def _render_hook(fn):
    @functools.wraps(fn)
    def render(self, c, now):
        if self.scoreboard is not None:
            return self.scoreboard.render(c, now)
        if self.badges:
            return self.draw_badges(c, now)
        out = fn(self, c, now)
        if self._ended:
            self._ended = False
            self.game_ended(now)
        return out
    render._score_hook = True
    return render


def _input_hook(fn):
    @functools.wraps(fn)
    def input(self, key, action="down", player=1):
        if self.scoreboard is not None:
            if action == "down":
                self.scoreboard.press(key)
            return None
        if self.badges:
            if action == "down":
                self.badges = []  # any button skips the cards
            return None
        return fn(self, key, action, player)
    input._score_hook = True
    return input


def _describe_hook(fn):
    @functools.wraps(fn)
    def describe(self):
        if self.scoreboard is not None:
            return f"{self.title} (high score: {self.scoreboard.describe()})"
        return fn(self)
    describe._score_hook = True
    return describe


class Game(App):
    category = "Games"
    interactive = True
    interval = 0.05
    lower_better = False  # smaller scores are better (times, tries)

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        for name, hook in (("render", _render_hook), ("input", _input_hook), ("describe", _describe_hook)):
            fn = cls.__dict__.get(name)
            if fn is not None and not getattr(fn, "_score_hook", False):
                setattr(cls, name, hook(fn))
        # React to music (effects on top) on every game; the Music itself is attached by apps.create()
        from .react import GAME_REACT_PARAMS, REACT_NAMES
        cls.params = [*[p for p in cls.params if p["name"] not in REACT_NAMES], *GAME_REACT_PARAMS]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self._ended = False  # a game ended since the last frame: file its score
        self.held = set()
        self.state = "title"  # title -> playing -> over
        self.over_at = 0
        self.score = 0
        self.scoreboard = None  # initials entry / top 5 over the game
        self._eids = {}  # score key -> this game's entry in the table
        self._last = None  # (key, value, lower) filed during this game
        self._hi = {}
        self.badges = []  # [(id, title)] achievement cards still to show, with when the first went up
        self._badge_at = None

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value == "over" and getattr(self, "_state", None) != "over" and not getattr(self, "demo", False):
            self._ended = True
        self._state = value

    # ---- high scores ------------------------------------------------------------------

    def score_key(self):
        return self.id

    def score_entry(self):
        """(key, value, lower) to file when a game ends, or None (nothing worth keeping)."""
        if self._last:
            return self._last
        return (self.score_key(), self.score, self.lower_better) if self.score > 0 else None

    def file_score(self, key, value, lower=False):
        """Save a score now (no initials yet). Returns its rank in the table or None."""
        if getattr(self, "demo", False):
            return None
        eid, rank = scores.submit(self.ctx.store, key, value, lower, self._eids.get(key))
        self._eids[key] = eid
        self._last = (key, value, lower)
        self._hi.pop(key, None)
        return rank

    def hi(self, key=None):
        """The best entry for this game (cached for a few seconds), or None."""
        key = key or self.score_key()
        hit = self._hi.get(key)
        if hit is None or time.time() - hit[0] > 5:
            try:
                hit = self._hi[key] = (time.time(), scores.best(self.ctx.store, key))
            except Exception:
                hit = (time.time(), None)
        return hit[1]

    def hi_text(self, key=None):
        e = self.hi(key)
        if not e:
            return ""
        return f"HI {scores.short(e['score'])}" + (f" {e['name'].strip()}" if e["name"].strip() else "")

    def game_ended(self, now):
        entry = self.score_entry()
        had = bool(scores.table(self.ctx.store, entry[0])) if entry else False
        rank = self.file_score(*entry) if entry else None
        eid = self._eids.get(entry[0]) if entry else None
        self._eids, self._last = {}, None
        if entry:
            try:
                self.badges += scores.award(self.ctx.store, entry[0], rank, had, now)
            except Exception:
                pass
        if rank is not None and eid:
            self.scoreboard = Scoreboard(self, entry[0], eid, entry[1], rank, now)

    def draw_badges(self, c, now):
        """The achievement cards, one after another; any button skips them."""
        if self._badge_at is None:
            self._badge_at = now
        i = int((now - self._badge_at) / BADGE_S)
        if i >= len(self.badges):
            self.badges, self._badge_at = [], None
            return self.render(c, now)
        aid, title = self.badges[i]
        on = int((now - self._badge_at) / BADGE_S * 8) % 8 < 7  # a quick blink as each card lands
        c.rect(1, 1, c.w - 2, c.h - 2)
        c.rect(3, 3, c.w - 6, c.h - 6)
        if on:
            c.text_center(7, "ACHIEVEMENT", SMALL)
            c.text_center(16, title, NORMAL)
            how = next((h for a, _, h in scores.ACHIEVEMENTS if a == aid), "")
            words, line, lines = how.split(), "", []
            for w in words:
                if SMALL.width((line + " " + w).strip()) > c.w - 10:
                    lines.append(line)
                    line = w
                else:
                    line = (line + " " + w).strip()
            lines.append(line)
            for k, ln in enumerate(lines[:2]):
                c.text_center(27 + k * 7, ln, SMALL)


    def scoreboard_done(self, now):
        """Back to the title once the top 5 has shown."""
        self.scoreboard = None
        self.held.clear()
        self.state = "title"
        for name in ("title_at", "touched"):
            if hasattr(self, name):
                setattr(self, name, now)
        for name in ("idle_at", "title_since"):
            if hasattr(self, name):
                setattr(self, name, None)

    def title_footer(self, c, now, y=36):
        """Blinking "PRESS ANY BUTTON", every other blink the best score instead."""
        phase = int(now * 2) % 4
        if phase % 2:
            hi = self.hi_text() if phase == 3 else ""
            c.text_center(y, hi or "PRESS ANY BUTTON", SMALL)

    def stop(self):
        # a game cut short (playlist moved on, app switched) still counts
        if self.state == "playing" and not getattr(self, "demo", False):
            entry = self.score_entry()
            if entry:
                self.file_score(*entry)

    # ---- flow -------------------------------------------------------------------------

    def input(self, key, action="down", player=1):
        if action == "down":
            self.held.add((player, key))
            if self.state == "title":
                self.state = "playing"
                self.reset()
            elif self.state == "playing":
                self.press(key, player)
        else:
            self.held.discard((player, key))

    def game_over(self):
        self.state = "over"
        self.over_at = time.time()

    def render(self, c, now):
        if self.state == "title":
            return self.draw_title_screen(c, now)
        if self.state == "over":
            c.text_center(6, "GAME OVER", NORMAL)
            c.text_center(18, str(self.score), NORMAL, scale=2)
            if now - self.over_at > 4:
                self.state = "title"
            return
        self.tick(now)
        self.draw(c, now)

    def draw_title_screen(self, c, now):
        c.text_center(8, self.title.upper(), NORMAL, scale=2)
        self.title_footer(c, now, 30)

    def describe(self):
        return f"{self.title} ({'demo' if getattr(self, 'demo', False) else self.state}, score {self.score})"

    def reset(self): ...
    def press(self, key, player): ...
    def tick(self, now): ...
    def draw(self, c, now): ...


Game.render = _render_hook(Game.render)
Game.input = _input_hook(Game.input)
Game.describe = _describe_hook(Game.describe)


class Scoreboard:
    """Arcade-style initials entry for a score that made the top 5, then the table."""

    def __init__(self, game, key, eid, value, rank, now):
        self.game, self.key, self.eid, self.value, self.rank = game, key, eid, value, rank
        self.letters = list(scores.last_initials(game.ctx.store))
        self.pos = 0
        self.phase = "enter"
        self.touched = now
        self.now = now

    def press(self, key):
        self.touched = self.now
        if self.phase == "table":
            if self.now - self.shown_at > 1:
                self.game.scoreboard_done(self.now)
            return
        L = scores.LETTERS
        i = L.index(self.letters[self.pos]) if self.letters[self.pos] in L else 0
        if key == "up":
            self.letters[self.pos] = L[(i + 1) % len(L)]
        elif key == "down":
            self.letters[self.pos] = L[(i - 1) % len(L)]
        elif key in ("a", "right"):
            if self.pos == 2 and key == "a":
                return self.save()
            self.pos = min(self.pos + 1, 2)
        elif key in ("b", "left"):
            self.pos = max(self.pos - 1, 0)
        elif key == "start":
            self.save()

    def save(self):
        scores.name(self.game.ctx.store, self.key, self.eid, "".join(self.letters))
        self.game._hi.pop(self.key, None)
        if "".join(self.letters).strip():
            try:
                self.game.badges += scores.award_named(self.game.ctx.store, self.key)
            except Exception:
                pass
        self.show_table()

    def show_table(self):
        self.phase, self.shown_at = "table", self.now

    def render(self, c, now):
        self.now = now
        if self.phase == "enter":
            if now - self.touched > ENTRY_TIMEOUT:
                self.show_table()
            else:
                return self.draw_entry(c, now)
        if now - self.shown_at > TABLE_S:
            self.game.scoreboard_done(now)
            return self.game.render(c, now)
        self.draw_table(c, now)

    def draw_entry(self, c, now):
        c.text_center(0, "NEW BEST!" if self.rank == 0 else "HIGH SCORE", SMALL)
        c.text_center(7, scores.short(self.value), NORMAL)
        x0 = (c.w - 3 * 10 - 2 * 6) // 2
        for i, ch in enumerate(self.letters):
            x = x0 + i * 16
            c.text(x, 17, ch if ch != " " else "_" if i == self.pos else " ", NORMAL, scale=2)
            if i == self.pos and (int(now * 3) % 2 or now - self.touched < 0.5):
                c.rect(x, 32, 10, 1, fill=True)
        c.text_center(36, "UP/DN  A=OK", SMALL)

    def draw_table(self, c, now):
        head = self.game.title.upper()
        if ":" in self.key:
            head += " " + self.key.split(":", 1)[1].upper()
        c.text_center(0, head[:20], SMALL)
        for i, e in enumerate(scores.table(self.game.ctx.store, self.key)):
            y = 8 + i * 7
            mine = e["id"] == self.eid
            v = 1
            if mine and int(now * 2) % 2 == 0:
                c.rect(0, y - 1, c.w, 7, fill=True)
                v = 0
            c.text(4, y, str(i + 1), SMALL, v=v)
            c.text(14, y, (e["name"].strip() or "---").ljust(3), SMALL, v=v)
            s = scores.short(e["score"])
            c.text(c.w - 4 - SMALL.width(s), y, s, SMALL, v=v)

    def describe(self):
        if self.phase == "enter":
            return f"entering initials for {scores.short(self.value)}"
        return "top 5"


class Attract:
    """Mixin for the classic games: after DEMO_AFTER idle seconds on the title the computer
    plays (autoplay(now) is called once a frame and holds keys / calls press()); any button
    takes over with a fresh game. Demo scores are never filed."""

    DEMO_POS = (None, 0)  # where the DEMO tag goes (x=None: centred, negative: from the right)

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.demo = False
        self.demo_at = 0
        self.idle_at = None

    def input(self, key, action="down", player=1):
        if self.demo:
            if action != "down":
                return
            self.end_demo()  # a real player takes over with a fresh game
        super().input(key, action, player)

    def end_demo(self):
        self.demo = False
        self.held = set()
        self.state = "title"
        self.idle_at = None

    def game_over(self):
        if self.demo:
            return self.end_demo()
        super().game_over()

    def hold(self, *keys):
        self.held = {(1, k) for k in keys}

    def autoplay(self, now): ...

    def render(self, c, now):
        if self.state == "title" and not self.demo:
            if self.idle_at is None:
                self.idle_at = now
            elif self.p.get("demo", True) and now - self.idle_at > DEMO_AFTER:
                self.demo, self.demo_at = True, now
                self.held = set()
                self.state = "playing"
                self.now = now
                self.reset()
        elif self.state != "title":
            self.idle_at = None
        if self.demo:
            if now - self.demo_at > DEMO_LENGTH:
                self.end_demo()
            elif self.state == "playing":
                self.autoplay(now)
        out = super().render(c, now)
        if self.demo and self.state == "playing":
            tag(c, *self.DEMO_POS, "DEMO")
        return out


@register
class Snake(Attract, Game):
    id = "snake"
    title = "Snake"
    description = "Eat the dots, don't hit yourself."
    params = [P("walls", "checkbox", False, "Walls kill (otherwise wrap around)"), DEMO]
    CELL = 2
    DEMO_POS = (-2, 1)

    def reset(self):
        # with walls, the playing area sits inside a 1-dot border
        self.margin = 1 if self.p["walls"] else 0
        self.gw = (self.w - 2 * self.margin) // self.CELL
        self.gh = (self.h - 2 * self.margin) // self.CELL
        self.body = [(self.gw // 2 - i, self.gh // 2) for i in range(4)]
        self.dir = self.next_dir = (1, 0)
        self.score = 0
        self.last_step = 0
        self.ai_step = None
        self.place_food()

    def place_food(self):
        free = [(x, y) for x in range(self.gw) for y in range(self.gh) if (x, y) not in self.body]
        self.food = random.choice(free)

    def press(self, key, player):
        d = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.get(key)
        if d and (d[0] != -self.dir[0] or d[1] != -self.dir[1]):
            self.next_dir = d

    def cell(self, x, y):
        """Grid cell after a move (wrapped without walls), or None off a walled board."""
        if self.p["walls"]:
            return (x, y) if 0 <= x < self.gw and 0 <= y < self.gh else None
        return x % self.gw, y % self.gh

    def room(self, start, body, cap):
        """Free cells reachable from start (counting stops at cap)."""
        seen, todo = {start}, [start]
        while todo and len(seen) < cap:
            x, y = todo.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = self.cell(x + dx, y + dy)
                if n is not None and n not in seen and n not in body:
                    seen.add(n)
                    todo.append(n)
        return len(seen)

    def autoplay(self, now):
        # once per step: head for the food, but never into a pocket smaller than the snake
        if self.ai_step == self.last_step:
            return
        self.ai_step = self.last_step
        hx, hy = self.body[0]
        body = set(self.body[:-1])
        fx, fy = self.food
        best = None
        for key, (dx, dy) in (("up", (0, -1)), ("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0))):
            if (dx, dy) == (-self.dir[0], -self.dir[1]):
                continue
            n = self.cell(hx + dx, hy + dy)
            if n is None or n in body:
                continue
            room = self.room(n, body, len(self.body) + 4)
            ddx, ddy = abs(n[0] - fx), abs(n[1] - fy)
            if not self.p["walls"]:
                ddx, ddy = min(ddx, self.gw - ddx), min(ddy, self.gh - ddy)
            rank = (room > len(self.body), -(ddx + ddy), random.random())
            if best is None or rank > best[0]:
                best = (rank, key)
        if best:
            self.press(best[1], 1)

    def tick(self, now):
        if now - self.last_step < max(0.06, 0.16 - self.score * 0.004):
            return
        self.last_step = now
        self.dir = self.next_dir
        hx, hy = self.body[0][0] + self.dir[0], self.body[0][1] + self.dir[1]
        if self.p["walls"] and not (0 <= hx < self.gw and 0 <= hy < self.gh):
            return self.game_over()
        head = (hx % self.gw, hy % self.gh)
        if head in self.body[:-1]:
            return self.game_over()
        self.body.insert(0, head)
        if head == self.food:
            self.score += 1
            self.place_food()
        else:
            self.body.pop()

    def draw(self, c, now):
        s, m = self.CELL, self.margin
        for x, y in self.body:
            c.rect(m + x * s, m + y * s, s, s, fill=True)
        if int(now * 4) % 2:
            c.rect(m + self.food[0] * s, m + self.food[1] * s, s, s, fill=True)
        if m:
            c.rect(0, 0, self.gw * s + 2, self.gh * s + 2)


PIECES = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)], "O": [(1, 0), (2, 0), (1, 1), (2, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)], "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)], "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}


@register
class Tetris(Attract, Game):
    id = "tetris"
    title = "Tetris"
    description = "Left/right to move, up or A to rotate, down to drop faster, B to hard-drop."
    params = [DEMO]
    DEMO_POS = (1, 34)
    AI_STEP = 0.12  # seconds between the demo's button presses
    COLS, ROWS, CELL = 10, 20, 2

    def reset(self):
        self.grid = [[0] * self.COLS for _ in range(self.ROWS)]
        self.score = self.lines = 0
        self.next = random.choice(list(PIECES))
        self.spawn()
        self.last_fall = time.time()

    def spawn(self):
        self.kind, self.next = self.next, random.choice(list(PIECES))
        self.cells = list(PIECES[self.kind])
        self.px, self.py = 3, 0
        self.plan = None
        if self.collides(self.cells, self.px, self.py):
            self.game_over()

    def collides(self, cells, px, py):
        for x, y in cells:
            gx, gy = px + x, py + y
            if gx < 0 or gx >= self.COLS or gy >= self.ROWS or (gy >= 0 and self.grid[gy][gx]):
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
        for x, y in self.cells:
            if self.py + y >= 0:
                self.grid[self.py + y][self.px + x] = 1
        full = [r for r in range(self.ROWS) if all(self.grid[r])]
        for r in full:
            del self.grid[r]
            self.grid.insert(0, [0] * self.COLS)
        self.lines += len(full)
        self.score += [0, 100, 300, 500, 800][len(full)]
        self.spawn()

    def press(self, key, player):
        if key == "left":
            self.move(-1, 0)
        elif key == "right":
            self.move(1, 0)
        elif key in ("up", "a"):
            self.rotate()
        elif key == "down":
            if not self.move(0, 1):
                self.lock()
            self.last_fall = time.time()
        elif key == "b":
            while self.move(0, 1):
                self.score += 2
            self.lock()

    def tick(self, now):
        speed = max(0.1, 0.8 - (self.lines // 10) * 0.07)
        if any(k == "down" for _, k in self.held):
            speed = 0.05
        if now - self.last_fall >= speed:
            self.last_fall = now
            if not self.move(0, 1):
                self.lock()

    def placement_value(self, cells, px, py):
        """How good locking cells at (px, py) is: lines, holes, height and bumpiness (the
        usual hand-tuned weights)."""
        grid = [row[:] for row in self.grid]
        for x, y in cells:
            if py + y < 0:
                return -1e9
            grid[py + y][px + x] = 1
        grid = [r for r in grid if not all(r)]
        lines = self.ROWS - len(grid)
        grid = [[0] * self.COLS] * lines + grid
        heights, holes = [], 0
        for x in range(self.COLS):
            top = next((y for y in range(self.ROWS) if grid[y][x]), self.ROWS)
            heights.append(self.ROWS - top)
            holes += sum(1 for y in range(top, self.ROWS) if not grid[y][x])
        bump = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
        return 0.76 * lines - 0.51 * sum(heights) - 0.36 * holes - 0.18 * bump

    def make_plan(self):
        size = 4 if self.kind == "I" else 3
        cells, best = list(self.cells), None
        for turns in range(1 if self.kind == "O" else 4):
            for px in range(-3, self.COLS):
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

    def autoplay(self, now):
        if now < getattr(self, "ai_at", 0):
            return
        self.ai_at = now + self.AI_STEP
        if self.plan is None:
            self.plan = self.make_plan()
        plan = self.plan
        if plan["turns"]:
            plan["turns"] -= 1
            self.press("a", 1)
        elif self.px != plan["x"] and self.move(1 if plan["x"] > self.px else -1, 0):
            pass
        else:
            self.press("b", 1)

    def draw(self, c, now):
        s = self.CELL
        ox, oy = (c.w - self.COLS * s) // 2, c.h - self.ROWS * s - 1
        c.line(ox - 1, 0, ox - 1, c.h - 1)
        c.line(ox + self.COLS * s, 0, ox + self.COLS * s, c.h - 1)
        c.line(ox - 1, c.h - 1, ox + self.COLS * s, c.h - 1)
        for y, row in enumerate(self.grid):
            for x, v in enumerate(row):
                if v:
                    c.rect(ox + x * s, oy + y * s, s, s, fill=True)
        for x, y in self.cells:
            if self.py + y >= 0:
                c.rect(ox + (self.px + x) * s, oy + (self.py + y) * s, s, s, fill=True)
        c.text(1, 1, "SCORE", SMALL)
        c.text(1, 8, str(self.score), SMALL)
        c.text(1, 18, "LINES", SMALL)
        c.text(1, 25, str(self.lines), SMALL)
        nx = ox + self.COLS * s + 4
        c.text(nx, 1, "NEXT", SMALL)
        for x, y in PIECES[self.next]:
            c.rect(nx + x * s, 9 + y * s, s, s, fill=True)


@register
class Pong(Attract, Game):
    id = "pong"
    title = "Pong"
    description = ("Player 1 = left paddle. Right paddle is the computer unless a player 2 joins. "
                   "Against the computer the score is 10 a point plus 1 a return.")
    params = [P("to", "number", 7, "Points to win", min=1, max=21), DEMO]
    PADDLE = 9
    DEMO_POS = (None, 35)

    def reset(self):
        self.p1 = self.p2 = (self.h - self.PADDLE) / 2
        self.s1 = self.s2 = 0
        self.score = 0
        self.p2_human_at = 0
        self.aim = 0.0
        self.serve(1)

    def score_entry(self):
        if time.time() - self.p2_human_at < 15:
            return None  # two players: nothing to rank
        return super().score_entry()

    def autoplay(self, now):
        # follows the ball while it comes this way, aiming a little off-centre; drifts home otherwise
        if self.vx < 0:
            target = self.by + 1 - self.PADDLE / 2 + self.aim
        else:
            target = (self.h - self.PADDLE) / 2
        d = target - self.p1
        if abs(d) > 1:
            self.hold("down" if d > 0 else "up")
        else:
            self.hold()

    def input(self, key, action="down", player=1):
        if player == 2:
            self.p2_human_at = time.time()
        super().input(key, action, player)

    def serve(self, direction):
        self.bx, self.by = self.w / 2, self.h / 2
        self.vx = 0.9 * direction
        self.vy = random.uniform(-0.6, 0.6)
        self.pause_until = time.time() + 1

    def paddle_move(self, pos, player):
        if (player, "up") in self.held:
            pos -= 1.2
        if (player, "down") in self.held:
            pos += 1.2
        return min(max(pos, 0), self.h - self.PADDLE)

    def tick(self, now):
        self.p1 = self.paddle_move(self.p1, 1)
        if now - self.p2_human_at < 15:
            self.p2 = self.paddle_move(self.p2, 2)
        else:  # computer: follows the ball, a bit slower than it
            target = self.by - self.PADDLE / 2
            self.p2 += max(-0.75, min(0.75, target - self.p2))
            self.p2 = min(max(self.p2, 0), self.h - self.PADDLE)
        if now < self.pause_until:
            return
        self.bx += self.vx
        self.by += self.vy
        if self.by < 0 or self.by > self.h - 2:
            self.vy = -self.vy
            self.by = min(max(self.by, 0), self.h - 2)
        for px, pos, sign in ((2, self.p1, 1), (self.w - 4, self.p2, -1)):
            if (sign > 0 and self.vx < 0 and px <= self.bx <= px + 1.5) or (sign < 0 and self.vx > 0 and px - 1.5 <= self.bx <= px):
                if pos - 1.5 <= self.by <= pos + self.PADDLE:
                    hit = (self.by + 1 - (pos + self.PADDLE / 2)) / (self.PADDLE / 2)
                    self.vx = sign * min(abs(self.vx) * 1.06, 2.2)
                    self.vy = hit * 1.1
                    if sign > 0:
                        self.score += 1
                        self.aim = random.uniform(-5.5, 5.5)  # the demo misses now and then
        if self.bx < -2:
            self.s2 += 1
            self.serve(1)
        elif self.bx > self.w:
            self.s1 += 1
            self.score += 10
            self.serve(-1)
        if max(self.s1, self.s2) >= int(self.p["to"]):
            self.game_over()

    def draw(self, c, now):
        for y in range(0, c.h, 4):
            c.line(c.w // 2, y, c.w // 2, y + 1)
        c.rect(2, int(self.p1), 2, self.PADDLE, fill=True)
        c.rect(c.w - 4, int(self.p2), 2, self.PADDLE, fill=True)
        c.rect(int(self.bx), int(self.by), 2, 2, fill=True)
        c.text(c.w // 2 - 3 - SMALL.width(str(self.s1)), 1, str(self.s1), SMALL)
        c.text(c.w // 2 + 3, 1, str(self.s2), SMALL)

    def render(self, c, now):
        if self.state == "over":
            c.text_center(6, "P1 WINS" if self.s1 > self.s2 else ("CPU WINS" if now - self.p2_human_at > 15 else "P2 WINS"), NORMAL)
            c.text_center(18, f"{self.s1}-{self.s2}", NORMAL, scale=2)
            if now - self.over_at > 4:
                self.state = "title"
            return
        super().render(c, now)


# ---- Game of the day ----------------------------------------------------------------------------------------

FEATURED_POOL = [g for ids in scores.GROUPS.values() for g in ids]  # the scored single-player games


def featured_game(day=None):
    """Today's featured game id: the same all day, a different one tomorrow."""
    import datetime as dt
    day = day or dt.date.today()
    pool = [g for g in FEATURED_POOL if g in REGISTRY]
    return random.Random(day.toordinal() * 7919).choice(pool) if pool else None


@register
class GameOfTheDay(App):
    id = "gameoftheday"
    title = "Game of the day"
    category = "Games"
    interactive = True
    interval = 0.05
    description = ("A different game is featured each day: its name, the best score and a QR code to the phone "
                   "controller, then the game itself with its demo until someone plays.")
    params = [
        P("card_seconds", "number", 10, "Show the card for (s)", min=3, max=120),
        P("every", "number", 90, "Show the card again every (s)", min=20, max=3600),
        P("url", "text", "", "Link in the QR code (empty = this board's /play page)"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.gid = featured_game()
        self.game = REGISTRY[self.gid](ctx) if self.gid else None
        self.card_until = None
        self.card_at = None
        self.qr = None

    def _card(self):
        if self.qr is not None:
            return self.qr
        import guestwall
        import qr
        from .wifiqr import fit_qr
        c = Canvas(self.w, self.h)
        url = str(self.p["url"] or "").strip() or guestwall.default_url().replace("/guest", "/play")
        try:
            code, quiet, inverted = fit_qr(url, self.h - 2)
            side = code.size + 2 * quiet
            qr.draw(c, code, self.w - side - 1, (self.h - side) // 2, quiet=quiet, invert=inverted)
            right = self.w - side - 3
        except ValueError:
            right = self.w
        c.text(1, 1, "GAME OF THE DAY", SMALL)
        name = (self.game.title if self.game else "").upper()
        c.text(1, 9, name if NORMAL.width(name) <= right - 2 else name[:12], NORMAL)
        hi = scores.best(self.ctx.store, self.gid) if self.gid else None
        c.text(1, 20, (f"HI {scores.short(hi['score'])} {hi['name'].strip()}".strip() if hi else "NO SCORE YET"), SMALL)
        c.text(1, 28, "SCAN TO PLAY", SMALL)
        self.qr = c
        return c

    def input(self, key, action="down", player=1):
        if self.game is None:
            return
        self.card_until = 0  # a button goes straight to the game
        self.game.input(key, action, player)

    def render(self, c, now):
        if self.game is None:
            c.text_center(17, "NO GAMES", NORMAL)
            return
        if self.card_until is None or (now >= self.card_at + float(self.p["every"]) and self.game.state == "title"
                                       and not getattr(self.game, "demo", False)):
            self.card_at = now
            self.card_until = now + float(self.p["card_seconds"])
            self.qr = None
        if now < self.card_until:
            c.px[:] = self._card().px
            self.interval = 0.5
            return
        self.interval = self.game.interval
        return self.game.render(c, now)

    def stop(self):
        if self.game is not None:
            self.game.stop()

    def describe(self):
        return f"Game of the day: {self.game.describe() if self.game else 'none'}"
