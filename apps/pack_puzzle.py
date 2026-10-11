"""Puzzle games: 2048, Minesweeper, Sokoban, Lights Out, Simon, Columns and Mastermind.

Built on games.Game. These are mostly still pictures, so each game keeps its last
frame and only redraws when something changed; an idle game flips no dots. On the
title screen the name alternates with a self-playing demo. Scores go in the shared
high-score table (apps/scores.py; Minesweeper per difficulty, Sokoban per level).
"""

import itertools
import random
import time

from fonts import NORMAL, SMALL

from .arcade import fill
from .base import P, register
from .games import Game

DEMO = P("demo", "checkbox", True, "Self-playing demo on the title screen")
MOVES = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def bits(*rows):
    """Sprite from rows of '1'/'0' -> list of (dx, dy) for the lit dots."""
    return [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "1"]


def stamp(c, x, y, pts, v=1):
    for dx, dy in pts:
        c.set(x + dx, y + dy, v)


def short(n):
    return str(n) if n < 100000 else f"{n // 1000}K"


def path_keys(x, y, tx, ty):
    """Arrow presses that move a cursor from (x, y) to (tx, ty)."""
    return (["right" if tx > x else "left"] * abs(tx - x)) + (["down" if ty > y else "up"] * abs(ty - y))


class Puzzle(Game):
    """Game with a cached frame, a title card that alternates with a self-playing
    demo, saved best scores and an end screen where A starts a new game."""

    interval = 0.1
    CARD, DEMO = 4, 45  # seconds the title card and the demo show
    AI_STEP = 0.15      # seconds between demo button presses

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.now = self.touched = self.title_at = time.time()
        self.demo = False
        self.demo_over = 0
        self.ai_at = 0
        self.ai_keys = []
        self.ver = 0
        self._key = self._frame = None
        self.result = ("GAME OVER", "0", "")

    # ---- best scores ----------------------------------------------------------------

    def best(self, key=None):
        e = self.hi(key)
        return e["score"] if e else None

    def record(self, value, key=None, lower=False):
        """File value in the high-score table (lower=True: smaller is better). True if it beat the best."""
        if self.demo:
            return False
        key = key or self.score_key()
        old = self.best(key)
        mine = self._eids.get(key)
        self.file_score(key, value, lower)
        if mine and old == value:
            return False
        return old is None or (value < old if lower else value > old)

    def score_entry(self):
        return self._last  # puzzles file their scores through record()

    def best_line(self, new, value, key=None, unit=""):
        b = self.best(key)
        return "NEW BEST!" if new else (f"BEST {b}{unit}" if b is not None else "")

    # ---- flow -------------------------------------------------------------------------

    def input(self, key, action="down", player=1):
        if action != "down":
            self.held.discard((player, key))
            return
        self.held.add((player, key))
        self.now = self.touched = time.time()
        self.ver += 1
        if self.state == "title":
            self.demo = False
            self.state = "playing"
            self.reset()
        elif self.state == "over":
            if key in ("a", "start") and self.now - self.over_at > 0.6:
                self.state = "playing"
                self.reset()
        elif key == "start":
            self.restart()
        else:
            self.press(key, player)

    def restart(self):
        self._eids, self._last = {}, None
        self.reset()

    def game_over(self, headline="GAME OVER", big=None, detail=""):
        if self.demo:
            self.demo_over = self.now + 3  # hold the last demo frame, then back to the card
            return
        self.result = (headline, str(self.score) if big is None else big, detail)
        super().game_over()

    def blink(self, now, rate=2.5):
        """Cursor phase: blinks after a button press, then holds still so an idle board stays quiet."""
        return now - self.touched > 20 or int(now * rate) % 2 == 0

    def frame_key(self, now):
        """Anything that changes the picture; None = redraw every frame."""
        return self.ver

    def paint(self, c, now):
        key = self.frame_key(now)
        if key is not None and key == self._key and self._frame is not None:
            c.px[:] = self._frame
            return
        self.draw(c, now)
        self._key, self._frame = key, bytes(c.px)

    def render(self, c, now):
        self.now = now
        if self.state == "playing":
            self.tick(now)
            if self.state == "playing":
                return self.paint(c, now)
        if self.state == "over":
            self.draw_over(c, now)
            if now - self.over_at > 30:
                self.state, self.title_at = "title", now
            return
        if self.demo:
            if (self.demo_over and now > self.demo_over) or now - self.title_at > self.DEMO:
                self.demo, self.title_at = False, now
            else:
                if not self.demo_over:
                    if now >= self.ai_at:
                        self.ai_at = now + self.AI_STEP
                        if not self.ai_keys:
                            self.ai_keys = list(self.ai(now) or [])
                        if self.ai_keys:
                            self.touched = now
                            self.ver += 1
                            self.press(self.ai_keys.pop(0), 1)
                    self.tick(now)
                return self.paint(c, now)
        elif self.p.get("demo") and now - self.title_at > self.CARD:
            self.demo, self.demo_over, self.ai_keys = True, 0, []
            self.title_at = self.touched = now
            self.ver += 1
            self.reset()
            self.demo_begin()
            return self.paint(c, now)
        self.draw_card(c, now)

    def draw_card(self, c, now):
        t = self.title.upper()
        if NORMAL.width(t) * 2 <= c.w:
            c.text_center(4, t, NORMAL, scale=2)
        else:
            c.text_center(8, t, NORMAL)
        self.draw_title(c, now)
        self.title_footer(c, now)

    def draw_over(self, c, now):
        head, big, detail = self.result
        c.text_center(3, head, NORMAL)
        if NORMAL.width(big) * 2 <= c.w:
            c.text_center(13, big, NORMAL, scale=2)
        else:
            c.text_center(16, big, NORMAL)
        c.text_center(29, detail, SMALL)
        c.text_center(36, "A = PLAY AGAIN", SMALL)

    def demo_begin(self): ...
    def ai(self, now): ...
    def draw_title(self, c, now): ...


# ---- 2048 ---------------------------------------------------------------------------

def slide2048(grid, dx, dy):
    """Slide a 4x4 grid of exponents. Returns (new grid, moves [(r0, c0, r1, c1, e)], points)."""
    new = [[0] * 4 for _ in range(4)]
    moves, pts = [], 0
    for i in range(4):
        # the line's cells, starting at the edge the tiles slide towards
        if dx:
            cells = [(i, 3 - j if dx > 0 else j) for j in range(4)]
        else:
            cells = [(3 - j if dy > 0 else j, i) for j in range(4)]
        k, last = 0, None  # last = (slot, exponent, merged)
        for r, col in cells:
            e = grid[r][col]
            if not e:
                continue
            if last and last[1] == e and not last[2]:
                tr, tc = cells[last[0]]
                new[tr][tc] = e + 1
                pts += 2 ** (e + 1)
                last = (last[0], e + 1, True)
            else:
                tr, tc = cells[k]
                new[tr][tc] = e
                last = (k, e, False)
                k += 1
            moves.append((r, col, tr, tc, e))
    return new, moves, pts


AI_WEIGHT = [[0, 0, 1, 2], [1, 2, 3, 4], [4, 6, 8, 10], [16, 14, 12, 10]]


@register
class Game2048(Puzzle):
    id = "game2048"
    title = "2048"
    description = "Arrows slide every tile; two equal tiles merge into their sum. Make a 2048 tile. Start = new game."
    params = [DEMO]
    interval = 0.05
    TW, TH, GX, GY = 15, 9, 1, 2  # tile size and the gaps between tiles
    SLIDE = 0.12
    AI_STEP = 0.3

    def reset(self):
        self.grid = [[0] * 4 for _ in range(4)]  # exponents: 1 = tile "2", 11 = tile "2048"
        self.score = 0
        self.won = self.banner = False
        self.anim = None  # (start, moves) while tiles slide
        self.over_due = 0
        self.spawn()
        self.spawn()

    def spawn(self):
        free = [(r, col) for r in range(4) for col in range(4) if not self.grid[r][col]]
        if free:
            r, col = random.choice(free)
            self.grid[r][col] = 2 if random.random() < 0.1 else 1

    def stuck(self):
        g = self.grid
        for r in range(4):
            for col in range(4):
                if not g[r][col] or (col < 3 and g[r][col] == g[r][col + 1]) or (r < 3 and g[r][col] == g[r + 1][col]):
                    return False
        return True

    def press(self, key, player):
        if self.banner:
            if key == "a":
                self.banner = False
            elif key == "b":
                self.reset()
            return
        d = MOVES.get(key)
        if not d or self.over_due:
            return
        new, moves, pts = slide2048(self.grid, *d)
        if new == self.grid:
            return
        self.grid = new
        self.score += pts
        self.anim = (self.now, moves)
        self.spawn()
        if not self.won and any(11 in row for row in new):
            self.won = True
            if not self.demo:
                self.banner = True
                self.record(self.score)
        if self.stuck():
            self.over_due = self.now + 1.5

    def tick(self, now):
        if self.anim and now - self.anim[0] >= self.SLIDE:
            self.anim = None
            self.ver += 1
        if self.over_due and now >= self.over_due:
            self.over_due = 0
            new = self.record(self.score)
            self.game_over("NO MOVES", short(self.score), self.best_line(new, self.score))

    def stop(self):
        if self.state == "playing" and self.score:
            self.record(self.score)

    def frame_key(self, now):
        return None if self.anim else self.ver

    def draw_tile(self, c, x, y, e):
        label = str(2 ** e) if e < 10 else f"{2 ** (e - 10)}K"
        c.rect(x, y, self.TW, self.TH)
        if e <= 2:  # 2 and 4 get a dotted frame, so bigger tiles stand out
            for xx in range(x + 1, x + self.TW - 1, 2):
                c.set(xx, y, 0)
                c.set(xx, y + self.TH - 1, 0)
            for yy in range(y + 1, y + self.TH - 1, 2):
                c.set(x, yy, 0)
                c.set(x + self.TW - 1, yy, 0)
        c.text(x + (self.TW - SMALL.width(label) + 1) // 2, y + 2, label, SMALL)

    def draw(self, c, now):
        px, py = self.TW + self.GX, self.TH + self.GY
        if self.anim:
            t = min(1.0, (now - self.anim[0]) / self.SLIDE)
            for r0, c0, r1, c1, e in self.anim[1]:
                self.draw_tile(c, round((c0 + (c1 - c0) * t) * px), round((r0 + (r1 - r0) * t) * py), e)
        else:
            for r in range(4):
                for col in range(4):
                    if self.grid[r][col]:
                        self.draw_tile(c, col * px, r * py, self.grid[r][col])
                    else:
                        c.set(col * px + self.TW // 2, r * py + self.TH // 2)
        hx = 4 * px + 1
        c.text(hx, 1, "SCORE", SMALL)
        c.text(hx, 8, short(self.score), SMALL)
        b = self.best()
        if b:
            c.text(hx, 18, "BEST", SMALL)
            c.text(hx, 25, short(b), SMALL)
        if self.demo:
            c.text(hx, 36, "DEMO", SMALL)
        if self.banner:
            fill(c, 3, 9, 57, 25, 0)
            c.rect(3, 9, 57, 25)
            c.text_center(12, "2048!", NORMAL, w=63)
            c.text_center(21, "A KEEP GOING", SMALL, w=63)
            c.text_center(27, "B NEW GAME", SMALL, w=63)

    def ai(self, now):
        if self.anim or self.over_due:
            return []
        best, choice = None, None
        for key in ("down", "left", "right", "up"):
            new, _, pts = slide2048(self.grid, *MOVES[key])
            if new == self.grid:
                continue
            s = pts + 6 * sum(row.count(0) for row in new) + sum(
                new[r][col] * AI_WEIGHT[r][col] for r in range(4) for col in range(4))
            if key == "up":
                s -= 200
            s += random.random() * 3
            if best is None or s > best:
                best, choice = s, key
        return [choice] if choice else []

    def draw_title(self, c, now):
        for i, e in enumerate((1, 2, 3, 4)):
            self.draw_tile(c, 10 + i * 16, 22, e)

    def describe(self):
        return f"2048 ({self.state}, score {self.score})"


# ---- Minesweeper ------------------------------------------------------------------

MS_COLS, MS_ROWS = 13, 7
MS_PX, MS_PY = 5, 6  # 4x5-dot cells (room for the small font's digits), 1-dot gaps
MS_DIGITS = {n: bits(*(r + "0" for r in SMALL.glyph(str(n)))) for n in range(1, 9)}
MS_FLAG = bits("1110", "1110", "1000", "1000", "1000")
MS_MINE = bits("0110", "1111", "1111", "1111", "0110")
MS_WRONG = bits("1001", "0110", "0110", "0110", "1001")
MS_CLOCK = bits("1111", "0110", "0110", "0110", "1111")


def _ms_neighbours():
    out = []
    for i in range(MS_COLS * MS_ROWS):
        x, y = i % MS_COLS, i // MS_COLS
        out.append([ny * MS_COLS + nx for ny in range(y - 1, y + 2) for nx in range(x - 1, x + 2)
                    if (nx, ny) != (x, y) and 0 <= nx < MS_COLS and 0 <= ny < MS_ROWS])
    return out


MS_NB = _ms_neighbours()


@register
class Minesweeper(Puzzle):
    id = "minesweeper"
    title = "Minesweeper"
    description = ("A opens a square (or, on a number whose mines are all flagged, opens the squares around it); "
                   "B flags. The first square is always safe.")
    LEVELS = {"easy": 10, "normal": 14, "hard": 18}
    params = [
        P("difficulty", "select", "normal", "Difficulty",
          options=[["easy", "Easy (10 mines)"], ["normal", "Normal (14 mines)"], ["hard", "Hard (18 mines)"]]),
        DEMO,
    ]
    AI_STEP = 0.12

    def score_key(self):
        return f"minesweeper:{self.p['difficulty']}"

    def reset(self):
        n = MS_COLS * MS_ROWS
        self.nmines = self.LEVELS.get(self.p["difficulty"], 14)
        self.mines, self.flags = set(), set()
        self.count = [0] * n
        self.open = bytearray(n)
        self.left = n - self.nmines
        self.cx, self.cy = MS_COLS // 2, MS_ROWS // 2
        self.started = self.ended = None
        self.won = False
        self.boom = None
        self.score = 0

    def lay_mines(self, first):
        safe = {first, *MS_NB[first]}
        self.mines = set(random.sample([i for i in range(len(self.open)) if i not in safe], self.nmines))
        self.count = [sum(k in self.mines for k in MS_NB[i]) for i in range(len(self.open))]

    def reveal(self, i):
        if self.open[i] or i in self.flags or self.ended:
            return
        if self.started is None:
            self.lay_mines(i)
            self.started = self.now
        if i in self.mines:
            self.boom = i
            return self.end(False)
        todo = [i]
        while todo:
            j = todo.pop()
            if self.open[j] or j in self.flags:
                continue
            self.open[j] = 1
            self.left -= 1
            if not self.count[j]:
                todo.extend(k for k in MS_NB[j] if not self.open[k])
        if self.left == 0:
            self.end(True)

    def end(self, won):
        self.ended, self.won = self.now, won
        self.score = int(self.ended - self.started)
        if won:
            self.flags = set(self.mines)

    def press(self, key, player):
        if self.ended:
            return
        d = MOVES.get(key)
        if d:
            self.cx = (self.cx + d[0]) % MS_COLS
            self.cy = (self.cy + d[1]) % MS_ROWS
            return
        i = self.cy * MS_COLS + self.cx
        if key == "a":
            if not self.open[i]:
                self.reveal(i)
            elif self.count[i] and sum(k in self.flags for k in MS_NB[i]) == self.count[i]:
                for k in MS_NB[i]:  # chord
                    self.reveal(k)
        elif key == "b" and not self.open[i]:
            self.flags ^= {i}

    def tick(self, now):
        if self.ended and now - self.ended > 3:
            if self.won:
                key = self.score_key()
                new = self.record(self.score, key, lower=True)
                self.game_over("CLEARED", f"{self.score}s", self.best_line(new, self.score, key, "S"))
            else:
                total = len(self.open) - self.nmines
                self.game_over("BOOM", f"{100 * (total - self.left) // total}%", "CLEARED")

    def clock(self, now):
        if self.started is None:
            return 0
        return min(999, int((self.ended or now) - self.started))

    def frame_key(self, now):
        boom = self.ended and not self.won and int(now * 3) % 2
        return self.ver, self.blink(now), self.clock(now), boom

    def draw(self, c, now):
        lost = self.ended and not self.won
        for i in range(len(self.open)):
            x, y = (i % MS_COLS) * MS_PX, (i // MS_COLS) * MS_PY
            if self.open[i]:
                if self.count[i]:
                    stamp(c, x, y, MS_DIGITS[self.count[i]])
            elif i in self.flags:
                stamp(c, x, y, MS_WRONG if lost and i not in self.mines else MS_FLAG)
            elif lost and i in self.mines:
                if i == self.boom and int(now * 3) % 2:
                    fill(c, x, y, 4, 5)
                else:
                    stamp(c, x, y, MS_MINE)
            else:
                fill(c, x, y, 4, 5)
        if not self.ended and self.blink(now):
            c.rect(self.cx * MS_PX - 1, self.cy * MS_PY - 1, 6, 7)  # in the gaps around the cell
        hx = MS_COLS * MS_PX + 1
        stamp(c, hx, 1, MS_MINE)
        c.text(hx + 6, 1, str(self.nmines - len(self.flags)), SMALL)
        stamp(c, hx, 9, MS_CLOCK)
        c.text(hx + 6, 9, str(self.clock(now)), SMALL)
        c.text(hx, 29, str(self.p["difficulty"])[:4].upper(), SMALL)
        if self.demo:
            c.text(hx, 36, "DEMO", SMALL)

    def ai(self, now):
        if self.ended:
            return []
        if self.started is None:
            target, act = random.randrange(len(self.open)), "a"
        else:
            options = []
            for i in range(len(self.open)):
                if not self.open[i] or not self.count[i]:
                    continue
                hidden = [k for k in MS_NB[i] if not self.open[k] and k not in self.flags]
                if not hidden:
                    continue
                flagged = sum(k in self.flags for k in MS_NB[i])
                if flagged == self.count[i]:
                    options.append((i, "a"))  # chord
                elif self.count[i] - flagged == len(hidden):
                    options.extend((k, "b") for k in hidden)
            if not options:  # nothing certain: guess
                options = [(i, "a") for i in range(len(self.open)) if not self.open[i] and i not in self.flags]
                options = [random.choice(options)]
            target, act = min(options, key=lambda o: abs(o[0] % MS_COLS - self.cx) + abs(o[0] // MS_COLS - self.cy))
        return path_keys(self.cx, self.cy, target % MS_COLS, target // MS_COLS) + [act]

    def draw_title(self, c, now):
        x0 = (c.w - 9 * MS_PX) // 2
        for i, n in enumerate((1, 2, 3, 0, 0, 0, 4, 0, 0)):
            if i in (3, 4):
                fill(c, x0 + i * MS_PX, 23, 4, 5)
            elif i == 5:
                stamp(c, x0 + i * MS_PX, 23, MS_FLAG)
            elif n:
                stamp(c, x0 + i * MS_PX, 23, MS_DIGITS[n])

    def describe(self):
        return f"Minesweeper ({self.state}, {self.p['difficulty']})"


# ---- Sokoban ------------------------------------------------------------------------

def _level(s):
    rows = [r for r in s.split("\n") if r.strip()]
    w = max(map(len, rows))
    return [r.ljust(w) for r in rows]


# '#' wall, '.' goal, '$' box, '*' box on goal, '@' player, '+' player on goal. Each fits in 9x7 tiles.
SOKOBAN_LEVELS = [_level(s) for s in (
    """
  ####
###  #
# $ .#
# #  #
# $ .#
#@ ###
####
""", """
#######
#     #
# .$. #
# $@  #
#     #
#######
""", """
########
#  .   #
# #$## #
#  @  .#
## $#  #
 #     #
 #######
""", """
#######
#.  ..#
# $$$ #
#  @  #
## # ##
 #   #
 #####
""", """
  ######
  #    #
###$$$ #
#@ ... #
#  #####
####
""", """
#######
#.@ # #
#$* $ #
#   $ #
# ..  #
#  *  #
#######
""", """
  #####
###   #
#. $# #
#.$ @ #
#.$ ###
#   #
#####
""", """
 ######
 #    #
##$## #
#  .  #
# $.#@#
#   ###
#####
""", """
#########
#   #   #
# $ . $ #
#. ##$# #
#  @    #
###   .##
  #####
""", """
########
#   #  #
# $  $ #
#.##.@ #
#  $ ###
#.   #
######
""", """
 #######
 #  .  #
## #$# #
#  $@$ #
# .# # #
#   .  #
########
""", """
########
#.. #  #
#.  $  #
## $#$ #
 #  @  #
 #######
""", """
 ########
 #  #   #
 # $  $ #
##.##$# #
#  ..   #
# @ #####
#####
""", """
  #######
###  .  #
#  $#$# #
# .  @ .#
#  $#$# #
###  .  #
  #######
""")]

# shortest solutions (found by breadth-first search), replayed by the demo
SOKOBAN_SOLUTIONS = [
    "uRRlluuRR",
    "dlluurRddlU",
    "UdlddrUluRRR",
    "ULdlUruRdRdrU",
    "rrrrruulDulDulD",
    "DDrdrruLruLLDllU",
    "ldLUrrruullDLrurrddlLL",
    "uuulllDurrrddllddlluRluR",
    "luluurrdLulDrrrrurrdLLDDldR",
    "lulLulDDDrRdrUUrruulDrdLuLLLulD",
    "UdddrruuLrddlllluuuurRdDDuLruulldDrrrruuL",
    "rruulLLLddrUdrrruullLdlUddrrrUruLLLuLrddlU",
    "urrrrruuulldDuurrdddlLLrrruuLulDDurrddlLuulLulD",
    "rruullllDurrrrddllUdrrddllUUlLulldRRRRddllUdrrrruuuulLDDDuRllLuuR",
]
SK = 6  # tile size in dots
SK_WALL = bits("111111", "111111", "111111", "111111", "111111", "111111")
SK_BOX = bits("111111", "100001", "100001", "100001", "100001", "111111")
SK_DONE = bits("111111", "100001", "101101", "101101", "100001", "111111")
SK_GOAL = bits("000000", "000000", "001100", "001100", "000000", "000000")
SK_MAN = bits("001100", "001100", "011110", "101101", "001100", "010010")
SK_KEYS = {"u": "up", "d": "down", "l": "left", "r": "right"}


@register
class Sokoban(Puzzle):
    id = "sokoban"
    title = "Sokoban"
    description = f"Push every box onto a target. Arrows move, B undoes, A twice restarts the level. {len(SOKOBAN_LEVELS)} levels."
    params = [P("level", "number", 1, "Start at level", min=1, max=len(SOKOBAN_LEVELS)), DEMO]
    AI_STEP = 0.25

    def reset(self):
        self.score = 0
        self.load(min(max(int(self.p["level"]), 1), len(SOKOBAN_LEVELS)) - 1)

    def load(self, n):
        self.level = n
        rows = SOKOBAN_LEVELS[n]
        self.lw, self.lh = len(rows[0]), len(rows)
        self.walls, self.goals, boxes = set(), set(), set()
        for y, r in enumerate(rows):
            for x, ch in enumerate(r):
                if ch == "#":
                    self.walls.add((x, y))
                if ch in ".*+":
                    self.goals.add((x, y))
                if ch in "$*":
                    boxes.add((x, y))
                if ch in "@+":
                    self.player = (x, y)
        self.boxes = frozenset(boxes)
        self.moves = 0
        self.history = []
        self.clear = False
        self.confirm = 0
        self.ox = (54 - self.lw * SK) // 2
        self.oy = (42 - self.lh * SK) // 2

    def restart(self):
        self.load(self.level)

    def demo_begin(self):
        self.load(random.randrange(len(SOKOBAN_LEVELS)))
        self.ai_keys = [SK_KEYS[ch.lower()] for ch in SOKOBAN_SOLUTIONS[self.level]]

    def blocked(self, x, y):
        return (x, y) in self.walls or not (0 <= x < self.lw and 0 <= y < self.lh)

    def press(self, key, player):
        if self.clear:
            if key in ("a", "b"):
                if self.level + 1 < len(SOKOBAN_LEVELS):
                    self.load(self.level + 1)
                else:
                    self.game_over("ALL SOLVED", str(self.score), f"{len(SOKOBAN_LEVELS)} LEVELS")
            return
        d = MOVES.get(key)
        if d:
            self.confirm = 0
            x, y = self.player[0] + d[0], self.player[1] + d[1]
            if self.blocked(x, y):
                return
            boxes = self.boxes
            if (x, y) in boxes:
                t = (x + d[0], y + d[1])
                if self.blocked(*t) or t in boxes:
                    return
                boxes = (boxes - {(x, y)}) | {t}
            self.history.append((self.player, self.boxes))
            del self.history[:-500]
            self.player, self.boxes = (x, y), boxes
            self.moves += 1
            if boxes == self.goals:
                self.clear = True
                self.score += 1
                self.new_best = self.record(self.moves, f"sokoban:{self.level + 1}", lower=True)
                if self.demo:
                    self.demo_over = self.now + 3
        elif key == "b" and self.history:
            self.player, self.boxes = self.history.pop()
            self.moves -= 1
        elif key == "a":
            if self.now < self.confirm:
                self.restart()
            else:
                self.confirm = self.now + 2

    def tick(self, now):
        if self.confirm and now >= self.confirm:
            self.confirm = 0
            self.ver += 1

    def draw(self, c, now):
        if self.clear and not self.demo:
            c.text_center(3, f"LEVEL {self.level + 1}", NORMAL)
            c.text_center(12, "SOLVED", NORMAL)
            c.text_center(23, f"{self.moves} MOVES", SMALL)
            c.text_center(29, self.best_line(self.new_best, self.moves, f"sokoban:{self.level + 1}"), SMALL)
            c.text_center(36, "A = NEXT LEVEL" if self.level + 1 < len(SOKOBAN_LEVELS) else "A = FINISH", SMALL)
            return
        for (x, y) in self.walls:
            stamp(c, self.ox + x * SK, self.oy + y * SK, SK_WALL)
        for (x, y) in self.goals - self.boxes:
            if (x, y) != self.player:
                stamp(c, self.ox + x * SK, self.oy + y * SK, SK_GOAL)
        for (x, y) in self.boxes:
            stamp(c, self.ox + x * SK, self.oy + y * SK, SK_DONE if (x, y) in self.goals else SK_BOX)
        stamp(c, self.ox + self.player[0] * SK, self.oy + self.player[1] * SK, SK_MAN)
        hx = 57
        c.text(hx, 1, "LEVEL", SMALL)
        c.text(hx, 8, f"{self.level + 1}/{len(SOKOBAN_LEVELS)}", SMALL)
        c.text(hx, 17, "MOVES", SMALL)
        c.text(hx, 24, str(self.moves), SMALL)
        if self.demo:
            c.text(hx, 36, "DEMO", SMALL)
        elif self.confirm:
            c.text(hx, 30, "A =", SMALL)
            c.text(hx, 36, "RESET", SMALL)

    def draw_title(self, c, now):
        x = 24
        stamp(c, x, 23, SK_MAN)
        stamp(c, x + 7, 23, SK_BOX)
        stamp(c, x + 21, 23, SK_GOAL)
        stamp(c, x + 30, 23, SK_DONE)

    def describe(self):
        return f"Sokoban ({self.state}, level {getattr(self, 'level', 0) + 1})"


# ---- Lights Out ---------------------------------------------------------------------

LO_N = 5
LO_TOGGLE = [sum(1 << (ny * LO_N + nx) for nx, ny in ((x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                 if 0 <= nx < LO_N and 0 <= ny < LO_N)
             for y in range(LO_N) for x in range(LO_N)]


def lights_solve(lights):
    """Fewest presses (as a bitmask of cells) that turn every light off, or None."""
    n = LO_N * LO_N
    rows = [LO_TOGGLE[i] | (((lights >> i) & 1) << n) for i in range(n)]  # the toggle matrix is symmetric
    pivot_row = [-1] * n
    r = 0
    for col in range(n):
        sel = next((k for k in range(r, n) if rows[k] >> col & 1), None)
        if sel is None:
            continue
        rows[r], rows[sel] = rows[sel], rows[r]
        for k in range(n):
            if k != r and rows[k] >> col & 1:
                rows[k] ^= rows[r]
        pivot_row[col] = r
        r += 1
    if any(rows[k] >> n & 1 for k in range(r, n)):
        return None
    free = [col for col in range(n) if pivot_row[col] < 0]
    free_mask = sum(1 << col for col in free)
    best = None
    for combo in range(1 << len(free)):
        x = sum(1 << col for j, col in enumerate(free) if combo >> j & 1)
        for col in range(n):
            if pivot_row[col] >= 0:
                row = rows[pivot_row[col]]
                if (row >> n & 1) ^ (bin(row & x & free_mask).count("1") & 1):
                    x |= 1 << col
        if best is None or bin(x).count("1") < bin(best).count("1"):
            best = x
    return best


@register
class LightsOut(Puzzle):
    id = "lights_out"
    title = "Lights Out"
    description = "A toggles a light and its four neighbours. Turn all lights off; each level is harder. B undoes."
    params = [DEMO]
    PITCH = 8
    AI_STEP = 0.2

    def reset(self):
        self.level = 1
        self.score = 0
        self.new_puzzle()

    def new_puzzle(self):
        k = min(2 + self.level, 13)
        lights = 0
        while not lights:
            for i in random.sample(range(LO_N * LO_N), k):
                lights ^= LO_TOGGLE[i]
        self.lights = self.start = lights
        self.par = bin(lights_solve(lights)).count("1")
        self.moves = 0
        self.history = []
        self.solved = False
        self.cx = self.cy = LO_N // 2

    def restart(self):
        self.lights, self.moves, self.history, self.solved = self.start, 0, [], False

    def press(self, key, player):
        if self.solved:
            if key in ("a", "b"):
                self.level += 1
                self.new_puzzle()
            return
        d = MOVES.get(key)
        if d:
            self.cx = (self.cx + d[0]) % LO_N
            self.cy = (self.cy + d[1]) % LO_N
        elif key == "a":
            i = self.cy * LO_N + self.cx
            self.lights ^= LO_TOGGLE[i]
            self.history.append(i)
            self.moves += 1
            if not self.lights:
                self.solved = True
                self.score = self.level
                self.record(self.level)
        elif key == "b" and self.history:
            self.lights ^= LO_TOGGLE[self.history.pop()]
            self.moves -= 1

    def frame_key(self, now):
        return self.ver, self.blink(now)

    def draw(self, c, now):
        p = self.PITCH
        for i in range(LO_N * LO_N):
            x, y = 1 + (i % LO_N) * p, 1 + (i // LO_N) * p
            on = self.lights >> i & 1
            cursor = not self.solved and i == self.cy * LO_N + self.cx and self.blink(now)
            if on:
                fill(c, x, y, p - 1, p - 1)
                if cursor:
                    fill(c, x + 2, y + 2, p - 5, p - 5, 0)
            elif cursor:
                c.rect(x, y, p - 1, p - 1)
            else:
                c.set(x + p // 2 - 1, y + p // 2 - 1)
        hx = LO_N * p + 4
        c.text(hx, 2, f"LEVEL {self.level}", SMALL)
        c.text(hx, 10, f"MOVES {self.moves}", SMALL)
        c.text(hx, 18, f"PAR {self.par}", SMALL)
        if self.solved:
            c.text(hx, 27, "SOLVED", NORMAL)
            c.text(hx, 36, "A = NEXT", SMALL)
        elif self.demo:
            c.text(hx, 36, "DEMO", SMALL)
        elif self.best():
            c.text(hx, 36, f"BEST {self.best()}", SMALL)

    def ai(self, now):
        if self.solved:
            return ["a"]
        sol = lights_solve(self.lights)
        cells = [i for i in range(LO_N * LO_N) if sol >> i & 1]
        if not cells:
            return []
        i = min(cells, key=lambda i: abs(i % LO_N - self.cx) + abs(i // LO_N - self.cy))
        return path_keys(self.cx, self.cy, i % LO_N, i // LO_N) + ["a"]

    def draw_title(self, c, now):
        for i, on in enumerate((1, 0, 1, 1, 0)):
            x = 23 + i * 8
            if on:
                fill(c, x, 21, 7, 7)
            else:
                c.set(x + 3, 24)

    def describe(self):
        return f"Lights Out ({self.state}, level {getattr(self, 'level', 1)})"


# ---- Simon --------------------------------------------------------------------------

SIMON_PADS = ("up", "right", "down", "left")
SIMON_ARROW = ["0001000", "0011100", "0111110", "1111111"]  # pointing up
_simon_masks = {}


def simon_masks(w, h):
    """{pad: (lit dots, idle dots)} as pixel indexes. Four rounded pads around a
    middle box for the score: idle pads are an outline with an arrow, lit pads are
    solid with the arrow cut out."""
    if (w, h) not in _simon_masks:
        mx, mw = (w - 26) // 2, 26
        rects = {"up": (mx, 0, mw, 12), "down": (mx, h - 12, mw, 12),
                 "left": (1, 11, mx - 4, h - 22), "right": (mx + mw + 3, 11, w - mx - mw - 4, h - 22)}
        masks = {}
        for pad, (x0, y0, pw, ph) in rects.items():
            corners = {(0, 0), (pw - 1, 0), (0, ph - 1), (pw - 1, ph - 1)}
            body = {(x, y) for y in range(ph) for x in range(pw) if (x, y) not in corners}
            edge = {(x, y) for x, y in body if x in (0, pw - 1) or y in (0, ph - 1) or
                    (x in (1, pw - 2) and y in (1, ph - 2))}
            ah, aw = len(SIMON_ARROW), len(SIMON_ARROW[0])
            arrow = set()
            for ay, row in enumerate(SIMON_ARROW):
                for ax, ch in enumerate(row):
                    if ch == "1":
                        dx, dy = {"up": (ax, ay), "down": (ax, ah - 1 - ay),
                                  "left": (ay, ax), "right": (ah - 1 - ay, ax)}[pad]
                        vertical = pad in ("up", "down")
                        arrow.add(((pw - (aw if vertical else ah)) // 2 + dx, (ph - (ah if vertical else aw)) // 2 + dy))
            idx = lambda pts: [(y0 + y) * w + x0 + x for x, y in pts]
            masks[pad] = (idx(body - arrow), idx((edge - arrow) | arrow))
        _simon_masks[(w, h)] = masks
    return _simon_masks[(w, h)]


@register
class Simon(Puzzle):
    id = "simon"
    title = "Simon"
    description = "Watch the pads light up, then repeat the sequence with the arrows. It grows by one each round and speeds up."
    params = [DEMO]
    interval = 0.05
    AI_STEP = 0.45

    def reset(self):
        self.seq = []
        self.score = 0
        self.lit, self.lit_until = None, 0
        self.pos = 0
        self.phase, self.next_at = "next", self.now + 1
        self.fail_pad = None
        self.slip_at = random.randint(4, 12)  # demo only: length at which it makes a mistake

    def light(self, pad, secs):
        self.lit, self.lit_until = pad, self.now + secs
        self.ver += 1

    def speed(self):
        on = max(0.18, 0.55 - 0.025 * len(self.seq))
        return on, max(0.08, on * 0.45)

    def tick(self, now):
        if self.lit and now >= self.lit_until:
            self.lit = None
            self.ver += 1
        if now < self.next_at:
            return
        if self.phase == "next":
            self.seq.append(random.choice(SIMON_PADS))
            self.phase, self.pos = "show", 0
        if self.phase == "show":
            on, gap = self.speed()
            if self.pos < len(self.seq):
                self.light(self.seq[self.pos], on)
                self.pos += 1
                self.next_at = now + on + gap
            else:
                self.phase, self.pos = "input", 0
                self.next_at = now + 6  # time allowed for each press
        elif self.phase == "input":
            self.fail()
        elif self.phase == "fail":
            new = self.record(self.score)
            self.game_over("GAME OVER", str(self.score), self.best_line(new, self.score))

    def fail(self):
        self.phase = "fail"
        self.fail_pad = self.seq[self.pos]
        self.next_at = self.now + 2
        self.ver += 1

    def press(self, key, player):
        if self.phase != "input" or key not in SIMON_PADS:
            return
        self.light(key, 0.3)
        if key != self.seq[self.pos]:
            return self.fail()
        self.pos += 1
        self.next_at = self.now + 6
        if self.pos == len(self.seq):
            self.score = len(self.seq)
            self.phase, self.next_at = "next", self.now + 1

    def frame_key(self, now):
        return self.ver, self.phase == "fail" and int(now * 4) % 2

    def draw(self, c, now):
        masks = simon_masks(c.w, c.h)
        px = c.px
        for pad in SIMON_PADS:
            lit, idle = masks[pad]
            on = pad == self.lit or (self.phase == "fail" and pad == self.fail_pad and int(now * 4) % 2)
            for i in (lit if on else idle):
                px[i] = 1
        c.text_center(c.h // 2 - 3, str(self.score), NORMAL)
        if self.demo:
            c.text(0, 0, "DEMO", SMALL)
        elif self.best():
            c.text(0, 0, f"BEST {self.best()}", SMALL)

    def ai(self, now):
        if self.phase != "input":
            return []
        key = self.seq[self.pos]
        if len(self.seq) >= self.slip_at and random.random() < 0.3:
            key = random.choice([p for p in SIMON_PADS if p != key])
        return [key]

    def describe(self):
        return f"Simon ({self.state}, score {self.score})"


# ---- Columns ------------------------------------------------------------------------

JEWELS = [  # 3x3, chosen to stay distinct on 1-bit dots
    bits("111", "111", "111"),
    bits("111", "101", "111"),
    bits("010", "111", "010"),
    bits("101", "010", "101"),
    bits("111", "000", "111"),
    bits("101", "101", "101"),
]
CO_W, CO_H, CO_PITCH = 6, 10, 4
CO_X, CO_Y = 30, 1  # top-left jewel


def co_matches(grid):
    """Cells in lines of three or more equal jewels, across, down or diagonal."""
    hits = set()
    for y in range(CO_H):
        for x in range(CO_W):
            j = grid[y][x]
            if not j:
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                px, py = x - dx, y - dy
                if 0 <= px < CO_W and 0 <= py < CO_H and grid[py][px] == j:
                    continue  # not the start of this run
                run = [(x, y)]
                nx, ny = x + dx, y + dy
                while 0 <= nx < CO_W and 0 <= ny < CO_H and grid[ny][nx] == j:
                    run.append((nx, ny))
                    nx, ny = nx + dx, ny + dy
                if len(run) >= 3:
                    hits.update(run)
    return hits


def co_collapse(grid, hits):
    for x, y in hits:
        grid[y][x] = 0
    for x in range(CO_W):
        col = [grid[y][x] for y in range(CO_H) if grid[y][x]]
        col = [0] * (CO_H - len(col)) + col
        for y in range(CO_H):
            grid[y][x] = col[y]


@register
class Columns(Puzzle):
    id = "columns"
    title = "Columns"
    description = ("Line up three or more equal jewels across, down or diagonally. Left/right move, "
                   "A or up cycles the jewels, down drops faster, B drops at once.")
    params = [P("jewels", "number", 6, "Kinds of jewel", min=4, max=6), DEMO]
    interval = 0.05
    AI_STEP = 0.1

    def reset(self):
        self.kinds = min(max(int(self.p["jewels"]), 4), 6)
        self.grid = [[0] * CO_W for _ in range(CO_H)]
        self.score = self.cleared = 0
        self.level = 1
        self.next = self.new_piece()
        self.spawn()

    def new_piece(self):
        return [random.randint(1, self.kinds) for _ in range(3)]

    def spawn(self):
        self.piece, self.next = self.next, self.new_piece()
        self.px, self.py = CO_W // 2 - 1, -2
        self.phase = "fall"
        self.fall_at = self.now + self.speed()
        self.planned = False
        self.ver += 1
        if not self.fits(self.px, self.py):
            self.game_over_now()

    def game_over_now(self):
        new = self.record(self.score)
        self.game_over("GAME OVER", short(self.score), self.best_line(new, self.score))

    def speed(self):
        return max(0.08, 0.6 - (self.level - 1) * 0.05)

    def fits(self, x, y):
        if not 0 <= x < CO_W:
            return False
        for k in range(3):
            r = y + k
            if r >= CO_H or (r >= 0 and self.grid[r][x]):
                return False
        return True

    def lock(self):
        if self.py < 0:
            return self.game_over_now()
        for k in range(3):
            self.grid[self.py + k][self.px] = self.piece[k]
        self.piece = None
        self.chain = 0
        self.check()

    def check(self):
        self.hits = co_matches(self.grid)
        if self.hits:
            self.chain += 1
            self.cleared += len(self.hits)
            self.score += len(self.hits) * 10 * self.level * self.chain
            self.level = 1 + self.cleared // 30
            self.phase, self.flash_until = "flash", self.now + 0.5
            self.ver += 1
        else:
            self.spawn()

    def press(self, key, player):
        if self.phase != "fall":
            return
        if key in ("left", "right"):
            dx = MOVES[key][0]
            if self.fits(self.px + dx, self.py):
                self.px += dx
        elif key in ("a", "up"):
            self.piece = [self.piece[2], self.piece[0], self.piece[1]]
        elif key == "down":
            if self.fits(self.px, self.py + 1):
                self.py += 1
                self.score += 1
            self.fall_at = self.now + 0.04
        elif key == "b":
            while self.fits(self.px, self.py + 1):
                self.py += 1
                self.score += 2
            self.lock()

    def tick(self, now):
        if self.phase == "fall":
            if now >= self.fall_at:
                soft = any(k == "down" for _, k in self.held)
                self.fall_at = now + (0.04 if soft else self.speed())
                if self.fits(self.px, self.py + 1):
                    self.py += 1
                    self.ver += 1
                else:
                    self.lock()
        elif self.phase == "flash" and now >= self.flash_until:
            co_collapse(self.grid, self.hits)
            self.check()

    def frame_key(self, now):
        return self.ver, self.phase == "flash" and int(now * 10) % 2

    def draw(self, c, now):
        c.line(CO_X - 2, 0, CO_X - 2, c.h - 1)
        c.line(CO_X + CO_W * CO_PITCH, 0, CO_X + CO_W * CO_PITCH, c.h - 1)
        c.line(CO_X - 2, c.h - 1, CO_X + CO_W * CO_PITCH, c.h - 1)
        hide = self.phase == "flash" and int(now * 10) % 2
        for y in range(CO_H):
            for x in range(CO_W):
                j = self.grid[y][x]
                if j and not (hide and (x, y) in self.hits):
                    stamp(c, CO_X + x * CO_PITCH, CO_Y + y * CO_PITCH, JEWELS[j - 1])
        if self.piece:
            for k, j in enumerate(self.piece):
                if self.py + k >= 0:
                    stamp(c, CO_X + self.px * CO_PITCH, CO_Y + (self.py + k) * CO_PITCH, JEWELS[j - 1])
        c.text(0, 1, "SCORE", SMALL)
        c.text(0, 8, short(self.score), SMALL)
        c.text(0, 17, "LEVEL", SMALL)
        c.text(0, 24, str(self.level), SMALL)
        c.text(0, 31, "JEWELS", SMALL)
        c.text(0, 37, str(self.cleared), SMALL)
        rx = CO_X + CO_W * CO_PITCH + 3
        c.text(rx, 1, "NEXT", SMALL)
        for k, j in enumerate(self.next):
            stamp(c, rx + 4, 8 + k * CO_PITCH, JEWELS[j - 1])
        if self.demo:
            c.text(rx, 24, "DEMO", SMALL)
        elif self.best():
            c.text(rx, 31, "BEST", SMALL)
            c.text(rx, 37, short(self.best()), SMALL)

    def ai(self, now):
        if self.phase != "fall" or self.planned:
            return []
        self.planned = True
        best, plan = None, []
        heights = [next((y for y in range(CO_H) if self.grid[y][x]), CO_H) for x in range(CO_W)]
        for x in range(CO_W):
            lo, hi = min(x, self.px), max(x, self.px)
            if heights[x] < 3 or any(h < 3 for h in heights[lo:hi + 1]):
                continue  # too full to land there, or the way there is blocked
            for rot in range(3):
                piece = self.piece[3 - rot:] + self.piece[:3 - rot] if rot else self.piece
                grid = [row[:] for row in self.grid]
                for k in range(3):
                    grid[heights[x] - 3 + k][x] = piece[k]
                gone = 0
                while True:
                    hits = co_matches(grid)
                    if not hits:
                        break
                    gone += len(hits)
                    co_collapse(grid, hits)
                tops = [next((y for y in range(CO_H) if grid[y][xx]), CO_H) for xx in range(CO_W)]
                s = gone * 10 + min(tops) * 2 + sum(tops) * 0.3 + random.random()
                if best is None or s > best:
                    best, plan = s, ["a"] * rot + path_keys(self.px, 0, x, 0) + ["b"]
        return plan

    def draw_title(self, c, now):
        for i, j in enumerate(JEWELS):
            stamp(c, 20 + i * 8, 24, j)

    def describe(self):
        return f"Columns ({self.state}, score {self.score})"


# ---- Mastermind ---------------------------------------------------------------------

MM_SHAPES = [
    bits("11111", "11111", "11111", "11111", "11111"),
    bits("01110", "10001", "10001", "10001", "01110"),
    bits("10001", "01010", "00100", "01010", "10001"),
    bits("00100", "00100", "11111", "00100", "00100"),
    bits("00100", "00100", "01110", "01110", "11111"),
    bits("00100", "01110", "11111", "01110", "00100"),
]
MM_EMPTY = bits("00000", "00000", "00100", "00000", "00000")
MM_PEGS, MM_TRIES = 4, 10


def mm_score(code, guess):
    """(right shape in the right place, right shape in the wrong place)"""
    exact = sum(a == b for a, b in zip(code, guess))
    common = sum(min(code.count(k), guess.count(k)) for k in set(guess))
    return exact, common - exact


@register
class Mastermind(Puzzle):
    id = "mastermind"
    title = "Mastermind"
    description = ("Find the hidden code of 4 shapes in 10 tries. Left/right pick a slot, up/down change its shape, "
                   "A checks the guess. A block = right shape and place, a dot = right shape, wrong place.")
    params = [P("repeats", "checkbox", True, "Shapes can repeat in the code"), DEMO]
    AI_STEP = 0.15

    def reset(self):
        kinds = range(len(MM_SHAPES))
        if self.p["repeats"]:
            self.code = [random.choice(kinds) for _ in range(MM_PEGS)]
        else:
            self.code = random.sample(kinds, MM_PEGS)
        self.guesses = []  # (guess, exact, near)
        self.cur = [None] * MM_PEGS
        self.slot = 0
        self.score = 0
        self.done_at = 0
        self.cands = None  # demo: codes still possible

    def press(self, key, player):
        if self.done_at:
            return
        n = len(MM_SHAPES)
        if key in ("left", "right"):
            self.slot = (self.slot + MOVES[key][0]) % MM_PEGS
        elif key in ("up", "down"):
            v = self.cur[self.slot]
            step = 1 if key == "up" else -1
            self.cur[self.slot] = (0 if step > 0 else n - 1) if v is None else (v + step) % n
        elif key == "b":
            self.cur[self.slot] = None
        elif key == "a":
            if None in self.cur:
                self.slot = self.cur.index(None)
                return
            guess = list(self.cur)
            exact, near = mm_score(self.code, guess)
            self.guesses.append((guess, exact, near))
            self.score = len(self.guesses)
            self.slot = 0
            if exact == MM_PEGS or len(self.guesses) == MM_TRIES:
                self.done_at = self.now

    def tick(self, now):
        if self.done_at and now - self.done_at > 2:
            n = len(self.guesses)
            if self.guesses[-1][1] == MM_PEGS:
                new = self.record(n, lower=True)
                self.game_over("CRACKED", str(n), f"IN {n} {'TRY' if n == 1 else 'TRIES'}  " + self.best_line(new, n))
            else:
                self.game_over("OUT OF TRIES", "", "THAT WAS THE CODE")

    def draw_code(self, c, x, y, code, scale=1, pitch=6):
        for j, k in enumerate(code):
            for dx, dy in (MM_EMPTY if k is None else MM_SHAPES[k]):
                if scale == 1:
                    c.set(x + j * pitch + dx, y + dy)
                else:
                    fill(c, x + j * pitch + dx * scale, y + dy * scale, scale, scale)

    def draw_pegs(self, c, x, y, exact, near):
        for k in range(exact + near):
            px, py = x + (k % 2) * 3, y + (k // 2) * 3
            if k < exact:
                fill(c, px, py, 2, 2)
            else:
                c.set(px, py)

    def frame_key(self, now):
        return self.ver, self.blink(now)

    def draw(self, c, now):
        for i, (g, exact, near) in enumerate(self.guesses):
            x, y = (i // 5) * 32, (i % 5) * 6
            self.draw_code(c, x, y, g)
            self.draw_pegs(c, x + 25, y, exact, near)
        c.line(0, 31, 61, 31)
        if self.done_at:
            self.draw_code(c, 1, 34, self.code, pitch=8)
            c.text(36, 34, "CODE", SMALL)
        else:
            self.draw_code(c, 1, 34, self.cur, pitch=8)
            if self.blink(now):
                c.line(1 + self.slot * 8, 40, 5 + self.slot * 8, 40)
            if None not in self.cur:
                c.text(36, 34, "A = GO", SMALL)
        hx = 65
        c.text(hx, 1, "TRY", SMALL)
        c.text(hx, 8, f"{min(len(self.guesses) + 1, MM_TRIES)}/{MM_TRIES}", SMALL)
        if self.demo:
            c.text(hx, 36, "DEMO", SMALL)
        elif self.best():
            c.text(hx, 17, "BEST", SMALL)
            c.text(hx, 24, str(self.best()), SMALL)

    def draw_over(self, c, now):
        head, big, detail = self.result
        c.text_center(2, head, NORMAL)
        self.draw_code(c, 15, 12, self.code, scale=2, pitch=15)
        c.text_center(26, detail, SMALL)
        c.text_center(36, "A = PLAY AGAIN", SMALL)

    def ai(self, now):
        if self.done_at:
            return []
        if self.cands is None:
            kinds = range(len(MM_SHAPES))
            self.cands = [list(t) for t in (itertools.product(kinds, repeat=MM_PEGS) if self.p["repeats"]
                                            else itertools.permutations(kinds, MM_PEGS))]
        for g, exact, near in self.guesses[len(self.guesses) - 1:] if self.guesses else ():
            self.cands = [t for t in self.cands if mm_score(t, g) == (exact, near)]
        if not self.cands:
            return []
        target = random.choice(self.cands)
        keys, slot = [], self.slot
        n = len(MM_SHAPES)
        for j, t in enumerate(target):
            v = self.cur[j]
            if v == t:
                continue
            keys += path_keys(slot, 0, j, 0)
            slot = j
            up = t + 1 if v is None else (t - v) % n
            down = n - t if v is None else (v - t) % n
            keys += ["up"] * up if up <= down else ["down"] * down
        return keys + ["a"]

    def draw_title(self, c, now):
        self.draw_code(c, 8, 22, list(range(len(MM_SHAPES))), pitch=12)

    def describe(self):
        return f"Mastermind ({self.state}, try {len(getattr(self, 'guesses', [])) + 1})"
