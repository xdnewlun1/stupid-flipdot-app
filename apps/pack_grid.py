"""Grid puzzles: Picross (nonograms made from your own pictures), Pipe Mania, Flow and Rush Hour.

Built on pack_puzzle.Puzzle (a cached frame, a title card that alternates with a self-playing demo, best scores).
Picross takes its puzzles from the Pictures (scenes) and drawings you've saved: each is shrunk to 10, 15 or 20
cells wide by 10 high, kept only when it solves line by line and its clues fit the board, and shown full size
when solved. Flow levels are cut from a random Hamiltonian path so the grid always fills. Rush Hour levels are
generated and solved (breadth first) so each one's minimum is known.
"""

import collections
import random

from fonts import NORMAL, SMALL

from .arcade import fill
from .base import P, register
from .pack_puzzle import DEMO, MOVES, Puzzle, path_keys

DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
OPP = {"U": "D", "D": "U", "L": "R", "R": "L"}
KEY_DIR = {"up": "U", "down": "D", "left": "L", "right": "R"}

# 3 x 3 digits for the picross clues (the 3 x 5 font is too tall for a 3-dot cell pitch)
DIG3 = {
    "0": ("###", "#.#", "###"), "1": (".#.", "##.", ".#."), "2": ("##.", ".#.", ".##"), "3": ("###", ".##", "###"),
    "4": ("#.#", "###", "..#"), "5": (".##", ".#.", "##."), "6": ("#..", "###", "###"), "7": ("###", "..#", "..#"),
    "8": ("###", "###", "###"), "9": ("###", "###", "..#"),
}


def dig3_width(s):
    """Width of a clue in 3 x 3 digits: digits 3 wide a dot apart, numbers 3 apart."""
    return sum(4 if ch != " " else 2 for ch in s) - 1


def draw_dig3(c, x, y, s):
    for ch in s:
        if ch == " ":
            x += 2
            continue
        for dy, row in enumerate(DIG3.get(ch, DIG3["0"])):
            for dx, v in enumerate(row):
                if v == "#":
                    c.set(x + dx, y + dy)
        x += 4


def wrap_keys(x, y, tx, ty, w, h):
    """Arrow presses from (x, y) to (tx, ty) on a grid whose cursor wraps round the edges: the short way."""
    dx, dy = (tx - x) % w, (ty - y) % h
    keys = ["right"] * dx if dx <= w - dx else ["left"] * (w - dx)
    keys += ["down"] * dy if dy <= h - dy else ["up"] * (h - dy)
    return keys


# ---- Picross ---------------------------------------------------------------------------------------------------
# Two layouts. "All clues": every row and column clue on the board in 3 x 3 digits, which needs a 4-dot cell
# pitch, so the grids are 10 x 7 and 15 x 7. "Big grid": 15 x 10 and 20 x 10 at a 3-dot pitch, with the clues of
# the cursor's row and column shown large in the margins (ticks mark which row and column they belong to).

PX_SIZES = {"clues": [(15, 7), (10, 7)], "cursor": [(20, 10), (15, 10)]}
PX_CLUE_W = 23   # dots for the row clues at the left (all-clues layout)
PX_CLUE_H = 12   # dots for the column clues above (up to three numbers)
PX_PANEL = 22    # dots for the cursor row's clue at the right (big-grid layout)

BUILTIN = {
    "heart": ["..##...##.", ".####.####", "##########", ".########.", "..######..", "...####...", "....##...."],
    "mushroom": ["...####...", ".########.", "##.####.##", ".########.", "...#..#...", "...#..#...", "...####..."],
    "cat": ["#......#..", "##....##..", "########..", "#.####.#.#", "########.#", ".######.#.", ".#.##.##.."],
    "house": [".......##......", "......####.....", ".....######....", "....########...", "...##########..",
              "...#.##..##.#..", "...#.##..####.."],
    "boat": ["........#......", ".......##......", "......###......", ".....####......", "....#####......",
             "###############", ".#############."],
    "key": ["..####..............", ".##..##.............", "##....##############", "##....##...#.#.#.#.#",
            ".##..##....#.#.#.#.#", "..####..............", "....................", "....................",
            "....................", "...................."],
    "fish": ["......####..........", "....########..####..", "..####.#######.###..", ".######.#####.###...",
             "#############.##....", ".######.#####.###...", "..####.#######.###..", "....########..####..",
             "......####..........", "...................."],
    "cup": ["...............", ".#########.....", ".#########.###.", ".#########.#.#.", ".#########.###.",
            "..#######......", "...#####.......", "...............", ".###########...", "..............."],
}


def px_clues(line):
    runs, n = [], 0
    for v in line:
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs or [0]


def px_line(clue, cells):
    """Line solving: every placement of `clue` that agrees with the known cells (-1 unknown, 0, 1); -> the cells
    that come out the same in all of them (as a new list), or None when nothing fits."""
    n = len(cells)
    runs = [r for r in clue if r]
    certain = [None] * n
    found = [False]

    def place(ri, start, acc):
        if ri == len(runs):
            if any(v == 1 for v in cells[start:]):
                return
            full = acc + [0] * (n - start)
            found[0] = True
            for i, v in enumerate(full):
                certain[i] = v if certain[i] is None or certain[i] == v else -1
            return
        r = runs[ri]
        rest = sum(runs[ri + 1:]) + len(runs[ri + 1:])
        for s in range(start, n - rest - r + 1):
            if any(v == 1 for v in cells[start:s]):
                break
            end = s + r
            if any(v == 0 for v in cells[s:end]):
                continue
            if end < n and cells[end] == 1:
                continue
            nxt = acc + [0] * (s - start) + [1] * r + ([0] if end < n else [])
            place(ri + 1, min(n, end + 1), nxt)

    place(0, 0, [])
    if not found[0]:
        return None
    return [v if v is not None else -1 for v in certain]


def px_solve(w, h, row_clues, col_clues, steps=None):
    """Solve by line deduction only. -> (grid of -1/0/1, solved?). `steps` collects (x, y, v) in deduction order."""
    g = [[-1] * w for _ in range(h)]
    changed = True
    while changed:
        changed = False
        for y in range(h):
            out = px_line(row_clues[y], g[y])
            if out is None:
                return g, False
            for x in range(w):
                if g[y][x] == -1 and out[x] != -1:
                    g[y][x] = out[x]
                    changed = True
                    if steps is not None:
                        steps.append((x, y, out[x]))
        for x in range(w):
            col = [g[y][x] for y in range(h)]
            out = px_line(col_clues[x], col)
            if out is None:
                return g, False
            for y in range(h):
                if g[y][x] == -1 and out[y] != -1:
                    g[y][x] = out[y]
                    changed = True
                    if steps is not None:
                        steps.append((x, y, out[y]))
    return g, all(v != -1 for row in g for v in row)


def px_shrink(rows, w, h):
    """A w x h grid (lists of 0/1) from a picture (rows of '0'/'1' or lists), from its middle, by block average."""
    sh, sw = len(rows), len(rows[0])
    scale = min(sw / w, sh / h)
    bw, bh = w * scale, h * scale
    ox, oy = (sw - bw) / 2, (sh - bh) / 2
    out = []
    for j in range(h):
        line = []
        for i in range(w):
            x0, x1 = int(ox + i * scale), max(int(ox + i * scale) + 1, int(ox + (i + 1) * scale))
            y0, y1 = int(oy + j * scale), max(int(oy + j * scale) + 1, int(oy + (j + 1) * scale))
            n = lit = 0
            for y in range(y0, min(y1, sh)):
                for x in range(x0, min(x1, sw)):
                    n += 1
                    lit += 1 if rows[y][x] in (1, "1") else 0
            line.append(1 if n and lit * 2 >= n else 0)
        out.append(line)
    return out


def px_clue_text(clue):
    return " ".join(map(str, clue))


def px_fits(grid, layout="clues"):
    """A grid makes a puzzle when it solves line by line and its clues fit the layout. -> (rows, cols) or None"""
    w, h = len(grid[0]), len(grid)
    lit = sum(map(sum, grid))
    if not 0.2 * w * h <= lit <= 0.8 * w * h:
        return None
    rows = [px_clues(r) for r in grid]
    cols = [px_clues([grid[y][x] for y in range(h)]) for x in range(w)]
    if layout == "clues":
        if any(len(cl) > 3 or any(n > 9 for n in cl) for cl in cols):
            return None
        if any(dig3_width(px_clue_text(cl)) > PX_CLUE_W for cl in rows):
            return None
    else:
        if any(SMALL.width(px_clue_text(cl)) > PX_PANEL for cl in rows):
            return None
        if any(SMALL.width(px_clue_text(cl)) > w * 3 for cl in cols):
            return None
    _, ok = px_solve(w, h, rows, cols)
    return (rows, cols) if ok else None


def px_pictures(store):
    """(name, rows) for every saved picture and drawing."""
    pics = []
    try:
        for s in store.list_scenes():
            pics.append((s["name"], s["thumb"]))
    except Exception:
        pass
    try:
        for d in store.drawings.values():
            w = d["w"]
            pics.append((d["name"], ["".join(str(v) for v in d["px"][y * w:(y + 1) * w]) for y in range(d["h"])]))
    except Exception:
        pass
    return [(n, r) for n, r in pics if r and r[0]]


def px_candidates(store, layout):
    """(name, grid, picture rows) for every saved picture and drawing that makes a puzzle in this layout, each at
    the biggest size that works."""
    out = []
    for name, rows in px_pictures(store):
        for w, h in PX_SIZES[layout]:
            g = px_shrink(rows, w, h)
            if px_fits(g, layout):
                out.append((name, g, [list(r) for r in rows]))
                break
    return out


def px_builtins(layout):
    out = []
    for name, rows in BUILTIN.items():
        g = [[1 if ch == "#" else 0 for ch in r] for r in rows]
        if (len(g[0]), len(g)) in PX_SIZES[layout] and px_fits(g, layout):
            out.append((name, g, None))
    return out


@register
class Picross(Puzzle):
    id = "picross"
    title = "Picross"
    description = ("Nonograms made from your own Pictures and drawings (or built-in shapes): the numbers say how many "
                   "dots run together in each row and column. A fills a cell, B marks it empty; a wrong fill counts as "
                   "a mistake. Solve it and the picture shows full size.")
    params = [
        P("source", "select", "pictures", "Puzzles from",
          options=[["pictures", "My pictures and drawings (built-in shapes when there are none)"], ["builtin", "Built-in shapes"]]),
        P("layout", "select", "clues", "Layout",
          options=[["clues", "All clues on the board (10 x 7 and 15 x 7 grids)"],
                   ["cursor", "Big grid (15 x 10, 20 x 10), clues for the cursor's row and column"]]),
        DEMO,
    ]
    AI_STEP = 0.2
    lower_better = True

    def score_key(self):
        if not hasattr(self, "w_cells"):
            return "picross"
        return f"picross:{self.w_cells}x{self.h_cells}"

    def pool(self):
        layout = self.p["layout"] if self.p["layout"] in PX_SIZES else "clues"
        cands = []
        if self.p["source"] == "pictures":
            cands = px_candidates(self.ctx.store, layout)
        return cands or px_builtins(layout) or px_builtins("clues")

    def reset(self):
        name, grid, pic = random.choice(self.pool())
        self.name, self.solution, self.picture = name, grid, pic
        self.h_cells, self.w_cells = len(grid), len(grid[0])
        self.layout = "clues" if (self.w_cells, self.h_cells) in PX_SIZES["clues"] else "cursor"
        self.row_clues = [px_clues(r) for r in grid]
        self.col_clues = [px_clues([grid[y][x] for y in range(self.h_cells)]) for x in range(self.w_cells)]
        self.cells = [[-1] * self.w_cells for _ in range(self.h_cells)]  # -1 unknown, 0 marked empty, 1 filled
        self.cx, self.cy = 0, 0
        self.mistakes = 0
        self.started = self.now
        self.solved_at = None
        self.score = 0
        self.ai_plan = None

    def press(self, key, player):
        if self.solved_at:
            return
        d = MOVES.get(key)
        if d:
            self.cx = (self.cx + d[0]) % self.w_cells
            self.cy = (self.cy + d[1]) % self.h_cells
            return
        v = self.cells[self.cy][self.cx]
        if key == "a":
            if v == 1:
                return
            if self.solution[self.cy][self.cx]:
                self.cells[self.cy][self.cx] = 1
            else:
                self.cells[self.cy][self.cx] = 0
                self.mistakes += 1
        elif key == "b" and v != 1:
            self.cells[self.cy][self.cx] = -1 if v == 0 else 0
        if all(self.cells[y][x] == 1 for y in range(self.h_cells) for x in range(self.w_cells) if self.solution[y][x]):
            self.solved_at = self.now
            self.score = max(0, int(self.now - self.started)) + 20 * self.mistakes

    def tick(self, now):
        if self.solved_at and now - self.solved_at > 6:
            key = self.score_key()
            new = self.record(self.score, key, lower=True)
            self.game_over("SOLVED", f"{self.score}s", self.best_line(new, self.score, key, "S"))

    def frame_key(self, now):
        return self.ver, self.blink(now), int(now - self.started) if not self.solved_at else int((now - self.solved_at) * 4)

    def row_done(self, y):
        return px_clues([v == 1 for v in self.cells[y]]) == self.row_clues[y]

    def col_done(self, x):
        return px_clues([self.cells[y][x] == 1 for y in range(self.h_cells)]) == self.col_clues[x]

    def draw_picture(self, c, now):
        """The solved picture, full size (the saved picture itself, or the grid scaled up)."""
        if self.picture is not None and len(self.picture) >= 20:
            ph, pw = len(self.picture), len(self.picture[0])
            ox, oy = (c.w - pw) // 2, (c.h - ph) // 2
            for y in range(ph):
                for x in range(pw):
                    if self.picture[y][x] in (1, "1"):
                        c.set(ox + x, oy + y)
            return
        s = min(84 // self.w_cells, 42 // self.h_cells)
        ox, oy = (c.w - self.w_cells * s) // 2, (c.h - self.h_cells * s) // 2
        for y in range(self.h_cells):
            for x in range(self.w_cells):
                if self.solution[y][x]:
                    fill(c, ox + x * s, oy + y * s, s - 1 if s > 2 else s, s - 1 if s > 2 else s)

    def draw_cells(self, c, x0, y0, pitch):
        box = pitch - 1
        for y in range(self.h_cells):
            for x in range(self.w_cells):
                v = self.cells[y][x]
                px, py = x0 + x * pitch, y0 + y * pitch
                if v == 1:
                    fill(c, px, py, box, box)
                elif v == 0:
                    c.set(px + box // 2, py + box // 2)
        if not self.solved_at and self._cursor_on:
            px, py = x0 + self.cx * pitch - 1, y0 + self.cy * pitch - 1
            for k in range(pitch + 1):
                c.set(px + k, py, 1 - c.get(px + k, py))
                c.set(px + k, py + pitch, 1 - c.get(px + k, py + pitch))
            for k in range(1, pitch):
                c.set(px, py + k, 1 - c.get(px, py + k))
                c.set(px + pitch, py + k, 1 - c.get(px + pitch, py + k))

    def draw(self, c, now):
        if self.solved_at and now - self.solved_at > 1.5:
            self.draw_picture(c, now)
            if int(now * 2) % 2:
                c.text(1, 36, f"{self.score}S" + (f" {self.mistakes} MISS" if self.mistakes else ""), SMALL)
            return
        self._cursor_on = self.blink(now)
        if self.layout == "clues":
            self.draw_all_clues(c, now)
        else:
            self.draw_big(c, now)

    def draw_all_clues(self, c, now):
        pitch = 4
        gw, gh = self.w_cells * pitch, self.h_cells * pitch
        x0 = (84 - (PX_CLUE_W + 1 + gw)) // 2 + PX_CLUE_W + 1
        y0 = PX_CLUE_H + 1
        for y in range(self.h_cells):  # row clues, right-aligned against the grid; a dot once the row is right
            s = px_clue_text(self.row_clues[y])
            if self.row_done(y):
                c.set(x0 - 3, y0 + y * pitch + 1)
            else:
                draw_dig3(c, x0 - 1 - dig3_width(s), y0 + y * pitch, s)
        for x in range(self.w_cells):  # column clues, stacked above, the last one nearest the grid
            if self.col_done(x):
                c.set(x0 + x * pitch + 1, y0 - 3)
                continue
            for k, n in enumerate(reversed(self.col_clues[x])):
                draw_dig3(c, x0 + x * pitch, y0 - 1 - 4 * (k + 1) + 1, str(n))
        self.draw_cells(c, x0, y0, pitch)
        c.text(0, 0, f"{max(0, int(now - self.started))}", SMALL)
        if self.mistakes:
            c.text(0, 6, f"X{self.mistakes}", SMALL)
        if self.demo:
            c.text(0, y0 + gh - 5, "DEMO", SMALL)

    def draw_big(self, c, now):
        pitch = 3
        gw, gh = self.w_cells * pitch, self.h_cells * pitch
        x0, y0 = 1, 7
        self.draw_cells(c, x0, y0, pitch)
        # the cursor's column clue above (a tick over the column), its row clue at the right (a tick by the row)
        col = px_clue_text(self.col_clues[self.cx])
        cxp = x0 + self.cx * pitch
        tx = max(0, min(cxp + 1 - SMALL.width(col) // 2, gw - SMALL.width(col)))
        c.text(tx, 0, col, SMALL)
        c.set(cxp, 5), c.set(cxp + 1, 5)
        if self.col_done(self.cx):
            c.set(tx + SMALL.width(col) + 2, 2)
        px = x0 + gw + 3
        ry = y0 + self.cy * pitch
        c.set(px - 2, ry), c.set(px - 2, ry + 1)
        row = px_clue_text(self.row_clues[self.cy])
        c.text(px, max(y0, min(ry - 1, 42 - 5)), row, SMALL)
        if self.row_done(self.cy):
            c.set(px + SMALL.width(row) + 2, max(y0, min(ry - 1, 42 - 5)) + 2)
        c.text(x0, 37, f"{max(0, int(now - self.started))}", SMALL)  # under the grid: the clock, mistakes, demo
        if self.mistakes:
            c.text(x0 + 20, 37, f"X{self.mistakes}", SMALL)
        if self.demo:
            c.text(x0 + gw - 16, 37, "DEMO", SMALL)

    def demo_begin(self):
        self.ai_plan = None

    def ai(self, now):
        if self.solved_at:
            return []
        if self.ai_plan is None:
            steps = []
            px_solve(self.w_cells, self.h_cells, self.row_clues, self.col_clues, steps)
            self.ai_plan = [(x, y, v) for x, y, v in steps if v == 1]
        while self.ai_plan:
            x, y, v = self.ai_plan.pop(0)
            if self.cells[y][x] != 1:
                return wrap_keys(self.cx, self.cy, x, y, self.w_cells, self.h_cells) + ["a"]
        return []

    def draw_title(self, c, now):
        rows = BUILTIN["heart"]
        x0 = (c.w - 10 * 3) // 2
        for y, r in enumerate(rows):
            for x, ch in enumerate(r):
                if ch == "#":
                    fill(c, x0 + x * 3, 20 + y * 2, 2, 1)

    def describe(self):
        return f"Picross ({self.state}, {getattr(self, 'name', '')})"


# ---- Pipe Mania ------------------------------------------------------------------------------------------------

PIPES = {"h": "LR", "v": "UD", "ur": "UR", "ul": "UL", "dr": "DR", "dl": "DL", "x": "UDLR"}
PIPE_WEIGHTS = {"h": 3, "v": 3, "ur": 2, "ul": 2, "dr": 2, "dl": 2, "x": 1}
PM_COLS, PM_ROWS, PM_CELL = 10, 5, 7
PM_START_S = 12.0


def pipe_exit(kind, entry):
    """The way out of a pipe entered from `entry`, or None."""
    ends = PIPES[kind]
    if entry not in ends:
        return None
    if kind == "x":
        return OPP[entry]
    return ends[0] if ends[1] == entry else ends[1]


def draw_pipe(c, px, py, kind, full, cell=PM_CELL):
    """A pipe in a cell: three-dot arms from the middle; hollow unless the water is in (`full`: the arms that are
    flooded, as a string of directions)."""
    m = cell // 2
    for d in PIPES[kind]:
        dx, dy = DIRS[d]
        n = m + 1
        for k in range(n):
            x, y = px + m + dx * k, py + m + dy * k
            if dx:
                fill(c, x, y - 1, 1, 3)
            else:
                fill(c, x - 1, y, 3, 1)
        if d not in full:  # hollow: take the middle line out again
            for k in range(1, n):
                c.set(px + m + dx * k, py + m + dy * k, 0)
    if not full:
        c.set(px + m, py + m, 0)


@register
class Pipes(Puzzle):
    id = "pipes"
    title = "Pipe Mania"
    description = ("Lay pipe ahead of the water: the next piece in the queue goes where the cursor is (A); pieces "
                   "without water in them can be replaced. The water starts after a countdown (B starts it, or hurries "
                   "it along) and leaks the moment it runs into a gap. Score: how far it flowed.")
    params = [DEMO]
    AI_STEP = 0.08

    def reset(self):
        self.grid = {}
        self.queue = [self.new_piece() for _ in range(5)]
        sx, sy = random.randrange(2, PM_COLS - 2), random.randrange(1, PM_ROWS - 1)
        self.start = (sx, sy)
        self.src_dir = random.choice("UDLR")
        self.head = (sx, sy, self.src_dir)  # cell and the way the water leaves it
        self.cx, self.cy = (sx + 3) % PM_COLS, sy
        self.dist = 0
        self.score = 0
        self.flow_at = self.now + PM_START_S
        self.leak = None
        self.hurry = False

    def new_piece(self):
        kinds = list(PIPE_WEIGHTS)
        return random.choices(kinds, weights=[PIPE_WEIGHTS[k] for k in kinds])[0]

    def step_s(self):
        return 0.45 if self.hurry else max(1.0, 2.4 - 0.05 * self.dist)

    def flooded(self, x, y):
        p = self.grid.get((x, y))
        return bool(p and p["full"])

    def press(self, key, player):
        if self.leak:
            return
        d = MOVES.get(key)
        if d:
            self.cx = (self.cx + d[0]) % PM_COLS
            self.cy = (self.cy + d[1]) % PM_ROWS
            return
        if key == "a":
            if (self.cx, self.cy) == self.start or self.flooded(self.cx, self.cy):
                return
            self.grid[(self.cx, self.cy)] = {"kind": self.queue.pop(0), "full": ""}
            self.queue.append(self.new_piece())
        elif key == "b":
            if self.now < self.flow_at:
                self.flow_at = self.now
            else:
                self.hurry = True

    def advance(self):
        x, y, out = self.head
        dx, dy = DIRS[out]
        nx, ny = x + dx, y + dy
        p = self.grid.get((nx, ny))
        entry = OPP[out]
        if not (0 <= nx < PM_COLS and 0 <= ny < PM_ROWS) or p is None:
            return self.spill(nx, ny)
        exit_ = pipe_exit(p["kind"], entry)
        if exit_ is None or entry in p["full"]:
            return self.spill(nx, ny)
        p["full"] += entry + exit_
        self.dist += 1
        self.score = self.dist
        self.head = (nx, ny, exit_)

    def spill(self, x, y):
        self.leak = (x, y, self.now)

    def tick(self, now):
        if self.leak:
            if now - self.leak[2] > 2.5:
                new = self.record(self.dist)
                self.game_over("LEAK", f"{self.dist}", self.best_line(new, self.dist))
            return
        while now >= self.flow_at and not self.leak:
            self.advance()
            self.flow_at += self.step_s()
            self.hurry = False

    def frame_key(self, now):
        left = max(0.0, self.flow_at - now)
        return self.ver, self.blink(now), self.dist, round(left * 4), self.leak and int(now * 4) % 2

    def draw(self, c, now):
        for (x, y), p in self.grid.items():
            draw_pipe(c, x * PM_CELL, y * PM_CELL, p["kind"], p["full"])
        sx, sy = self.start
        fill(c, sx * PM_CELL + 1, sy * PM_CELL + 1, 5, 5)  # the source, with its spout
        dx, dy = DIRS[self.src_dir]
        for k in range(3, 5):
            fill(c, sx * PM_CELL + 3 + dx * k - (0 if dx else 1), sy * PM_CELL + 3 + dy * k - (0 if dy else 1),
                 1 if dx else 3, 3 if dx else 1)
        c.set(sx * PM_CELL + 3, sy * PM_CELL + 3, 0)
        if self.leak:
            lx, ly = self.leak[:2]
            if int(now * 4) % 2 and 0 <= lx < PM_COLS and 0 <= ly < PM_ROWS:
                fill(c, lx * PM_CELL + 2, ly * PM_CELL + 2, 3, 3)
        elif self.blink(now):
            c.rect(self.cx * PM_CELL, self.cy * PM_CELL, PM_CELL, PM_CELL)
        qx = PM_COLS * PM_CELL + 3
        for i, kind in enumerate(self.queue):  # the queue, next at the top
            draw_pipe(c, qx, i * PM_CELL, kind, "")
        c.rect(qx - 1, -1, PM_CELL + 2, PM_CELL + 2)
        c.text(0, 36, f"DIST {self.dist}", SMALL)
        left = self.flow_at - now
        if left > 0 and self.dist == 0:
            c.text(40, 36, f"{int(left) + 1}", SMALL)
            fill(c, 48, 38, int(22 * left / PM_START_S), 2)
        elif not self.leak:
            c.text(44, 36, "FLOW", SMALL)
        if self.demo:
            c.text(62, 36, "DEMO", SMALL)

    def need(self):
        """Where the water first runs out of pipe: (x, y, entry direction), or None when it would leave the board."""
        x, y, out = self.head
        seen = set()
        while True:
            dx, dy = DIRS[out]
            x, y = x + dx, y + dy
            entry = OPP[out]
            if not (0 <= x < PM_COLS and 0 <= y < PM_ROWS):
                return None
            p = self.grid.get((x, y))
            if p is None or (x, y, entry) in seen:
                return (x, y, entry)
            ex = pipe_exit(p["kind"], entry)
            if ex is None or entry in p["full"]:
                return (x, y, entry) if not p["full"] else None
            seen.add((x, y, entry))
            out = ex

    def ai(self, now):
        if self.leak:
            return []
        need = self.need()
        kind = self.queue[0]
        target = None
        if need is not None:
            nx, ny, entry = need
            if entry in PIPES[kind]:
                ex = pipe_exit(kind, entry)
                ex_dx, ex_dy = DIRS[ex]
                if 0 <= nx + ex_dx < PM_COLS and 0 <= ny + ex_dy < PM_ROWS:
                    target = (nx, ny)
        if target is None:  # the piece doesn't help right now: park it well away from the water
            hx, hy = self.head[:2]
            far = [(x, y) for x in range(PM_COLS) for y in range(PM_ROWS)
                   if (x, y) not in self.grid and (x, y) != self.start and abs(x - hx) + abs(y - hy) > 3
                   and (need is None or abs(x - need[0]) + abs(y - need[1]) > 1)]
            if not far:
                return ["b"]
            target = random.choice(far)
        return wrap_keys(self.cx, self.cy, *target, PM_COLS, PM_ROWS) + ["a"]

    def draw_title(self, c, now):
        x0 = (c.w - 5 * PM_CELL) // 2
        for i, (k, f) in enumerate((("h", "LR"), ("dl", "LD"), ("v", ""), ("ur", ""), ("x", ""))):
            draw_pipe(c, x0 + i * PM_CELL, 22, k, f)

    def describe(self):
        return f"Pipe Mania ({self.state}, dist {self.dist})"


# ---- Flow ------------------------------------------------------------------------------------------------------

FL_PITCH = 6
FL_LEVEL_S = 90
FL_STYLES = 5


def hamiltonian_path(n, rng, shakes=None):
    """A random Hamiltonian path on an n x n grid: a boustrophedon path shaken with backbite moves."""
    path = [(x if y % 2 == 0 else n - 1 - x, y) for y in range(n) for x in range(n)]
    for _ in range(shakes if shakes is not None else 40 * n * n):
        if rng.random() < 0.5:
            path.reverse()
        ex, ey = path[-1]
        opts = [(ex + dx, ey + dy) for dx, dy in DIRS.values()
                if 0 <= ex + dx < n and 0 <= ey + dy < n and (ex + dx, ey + dy) != path[-2]]
        nb = rng.choice(opts)
        i = path.index(nb)
        path = path[:i + 1] + path[i + 1:][::-1]
    return path


def flow_level(n, pairs, rng, min_len=3):
    """Paths that cover an n x n grid, one per pair: a shaken Hamiltonian path cut into `pairs` pieces."""
    for _ in range(200):
        path = hamiltonian_path(n, rng)
        total = n * n
        if pairs * min_len > total:
            pairs = total // min_len
        cuts = sorted(rng.sample(range(min_len, total - min_len + 1), pairs - 1)) if pairs > 1 else []
        ok, prev = True, 0
        for cpos in cuts + [total]:
            if cpos - prev < min_len:
                ok = False
                break
            prev = cpos
        if not ok:
            continue
        out, prev = [], 0
        for cpos in cuts + [total]:
            out.append(path[prev:cpos])
            prev = cpos
        return out
    return [path]


def draw_symbol(c, x, y, k):
    """5 x 5 endpoint marks: one shape per pair."""
    if k == 0:
        fill(c, x, y, 5, 5)
    elif k == 1:
        c.rect(x, y, 5, 5)
    elif k == 2:
        for dy in range(5):
            w = 2 - abs(dy - 2)
            fill(c, x + 2 - w, y + dy, 2 * w + 1, 1)
    elif k == 3:
        for d in range(5):
            c.set(x + d, y + d)
            c.set(x + 4 - d, y + d)
    else:
        fill(c, x + 2, y, 1, 5)
        fill(c, x, y + 2, 5, 1)


def draw_link(c, ax, ay, bx, by, k):
    """The path between two cell centres in the pair's line style."""
    horiz = ay == by
    lo, hi = (min(ax, bx), max(ax, bx)) if horiz else (min(ay, by), max(ay, by))
    for t in range(lo, hi + 1):
        x, y = (t, ay) if horiz else (ax, t)
        if k == 0:
            fill(c, x - (0 if horiz else 1), y - (1 if horiz else 0), 1 if horiz else 3, 3 if horiz else 1)
        elif k == 1:
            if horiz:
                c.set(x, y - 1), c.set(x, y + 1)
            else:
                c.set(x - 1, y), c.set(x + 1, y)
        elif k == 2:
            c.set(x, y)
        elif k == 3:
            if t % 2 == 0:
                c.set(x, y)
        else:
            c.set(x, y)
            if t % 3 == 0:
                if horiz:
                    c.set(x, y - 1), c.set(x, y + 1)
                else:
                    c.set(x - 1, y), c.set(x + 1, y)


@register
class Flow(Puzzle):
    id = "flow"
    title = "Flow"
    description = ("Join each pair of matching marks with a line and fill every cell. A on a mark starts a line, the "
                   "d-pad draws it (through another line cuts that one), A or B stops. Bigger grids as the levels go "
                   "up; the clock runs per level.")
    params = [DEMO]
    AI_STEP = 0.12

    def reset(self):
        self.level = 0
        self.score = 0
        self.next_level()

    def next_level(self):
        self.level += 1
        self.n = 5 if self.level <= 3 else 6 if self.level <= 6 else 7
        pairs = min(FL_STYLES, 3 + (self.level - 1) // 2)
        self.rng = random.Random()
        self.paths = flow_level(self.n, pairs, self.rng)
        self.ends = [(p[0], p[-1]) for p in self.paths]
        self.lines = [[] for _ in self.paths]  # the player's path per pair
        self.drawing = None
        self.cx, self.cy = self.ends[0][0]
        self.level_at = self.now
        self.done_at = None

    def owner(self, cell):
        for k, line in enumerate(self.lines):
            if cell in line:
                return k
        return None

    def endpoint(self, cell):
        for k, (a, b) in enumerate(self.ends):
            if cell in (a, b):
                return k
        return None

    def press(self, key, player):
        if self.done_at:
            return
        d = MOVES.get(key)
        if d:
            nx, ny = self.cx + d[0], self.cy + d[1]
            if not (0 <= nx < self.n and 0 <= ny < self.n):
                return
            if self.drawing is not None:
                k = self.drawing
                line = self.lines[k]
                e = self.endpoint((nx, ny))
                if e is not None and e != k:
                    return  # another pair's mark: can't go through
                if (nx, ny) in line:  # back along the line: cut it there
                    del line[line.index((nx, ny)) + 1:]
                else:
                    o = self.owner((nx, ny))
                    if o is not None:
                        other = self.lines[o]
                        del other[other.index((nx, ny)):]
                    line.append((nx, ny))
                    if e == k:
                        self.drawing = None  # joined up
            self.cx, self.cy = nx, ny
            self.check()
            return
        cell = (self.cx, self.cy)
        if key == "a":
            if self.drawing is not None:
                self.drawing = None
                return
            k = self.endpoint(cell)
            if k is None:
                k = self.owner(cell)
                if k is None:
                    return
                del self.lines[k][self.lines[k].index(cell) + 1:]
            else:
                self.lines[k] = [cell]
            self.drawing = k
        elif key == "b":
            self.drawing = None

    def complete(self, k):
        a, b = self.ends[k]
        line = self.lines[k]
        return len(line) >= 2 and {line[0], line[-1]} == {a, b}

    def check(self):
        if all(self.complete(k) for k in range(len(self.paths))) and sum(map(len, self.lines)) == self.n * self.n:
            self.done_at = self.now
            self.score = self.level
            self.drawing = None

    def tick(self, now):
        if self.done_at:
            if now - self.done_at > 2:
                self.next_level()
            return
        if now - self.level_at > FL_LEVEL_S:
            done = self.level - 1
            new = self.record(done)
            self.game_over("TIME UP", f"{done}", self.best_line(new, done) if done else "LEVELS")

    def frame_key(self, now):
        return self.ver, self.blink(now), int(now - self.level_at), bool(self.done_at) and int(now * 3) % 2

    def centre(self, cell):
        x0 = 1
        y0 = (42 - self.n * FL_PITCH) // 2
        return x0 + cell[0] * FL_PITCH + 2, y0 + cell[1] * FL_PITCH + 2

    def draw(self, c, now):
        for k, line in enumerate(self.lines):
            for a, b in zip(line, line[1:]):
                draw_link(c, *self.centre(a), *self.centre(b), k)
        for k, (a, b) in enumerate(self.ends):
            for cell in (a, b):
                x, y = self.centre(cell)
                fill(c, x - 2, y - 2, 5, 5, 0)
                draw_symbol(c, x - 2, y - 2, k)
        if self.done_at:
            if int(now * 3) % 2:
                c.rect(0, (42 - self.n * FL_PITCH) // 2 - 1, self.n * FL_PITCH + 2, self.n * FL_PITCH + 2)
        elif self.blink(now):
            x, y = self.centre((self.cx, self.cy))
            c.rect(x - 3, y - 3, 7, 7)
            if self.drawing is not None:
                c.set(x, y)
        px = self.n * FL_PITCH + 6
        c.text(px, 1, f"LVL {self.level}", SMALL)
        done = sum(self.complete(k) for k in range(len(self.paths)))
        c.text(px, 8, f"{done}/{len(self.paths)}", SMALL)
        left = max(0, min(FL_LEVEL_S, int(FL_LEVEL_S - (now - self.level_at))))
        c.text(px, 15, f"{left}S", SMALL)
        fill(c, px, 22, int((84 - px - 2) * left / FL_LEVEL_S), 2)
        c.text(px, 36, "DEMO" if self.demo else f"{self.n}X{self.n}", SMALL)

    def ai(self, now):
        if self.done_at:
            return []
        for k, path in enumerate(self.paths):
            if self.complete(k):
                continue
            keys = path_keys(self.cx, self.cy, *path[0]) + ["a"]
            for a, b in zip(path, path[1:]):
                keys.append({(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}[(b[0] - a[0], b[1] - a[1])])
            return keys
        return []

    def draw_title(self, c, now):
        x0 = (c.w - 5 * FL_PITCH) // 2
        for k in range(5):
            draw_symbol(c, x0 + k * FL_PITCH, 22, k)
        draw_link(c, x0 + 2, 31, x0 + 26, 31, 0)

    def describe(self):
        return f"Flow ({self.state}, level {getattr(self, 'level', 0)})"


# ---- Rush Hour -------------------------------------------------------------------------------------------------

RH_N, RH_CELL = 6, 7
RH_EXIT_ROW = 2
_RH_LEVELS = {}


def rh_cells(v):
    x, y, ln, horiz = v
    return [(x + i, y) if horiz else (x, y + i) for i in range(ln)]


def rh_moves(vehicles):
    """(index, step) for every legal one-cell slide. The first vehicle is the red car."""
    taken = {cell for v in vehicles for cell in rh_cells(v)}
    out = []
    for i, (x, y, ln, horiz) in enumerate(vehicles):
        for step in (-1, 1):
            if horiz:
                nx = x - 1 if step < 0 else x + ln
                if 0 <= nx < RH_N and (nx, y) not in taken:
                    out.append((i, step))
            else:
                ny = y - 1 if step < 0 else y + ln
                if 0 <= ny < RH_N and (x, ny) not in taken:
                    out.append((i, step))
    return out


def rh_apply(vehicles, i, step):
    x, y, ln, horiz = vehicles[i]
    v = list(vehicles)
    v[i] = (x + step, y, ln, horiz) if horiz else (x, y + step, ln, horiz)
    return tuple(v)


def rh_solved(vehicles):
    x, y, ln, horiz = vehicles[0]
    return x + ln == RH_N


def rh_solve(vehicles, limit=30000):
    """Fewest one-cell slides to get the red car to the right edge: the list of (index, step), or None."""
    start = tuple(vehicles)
    if rh_solved(start):
        return []
    prev = {start: None}
    todo = collections.deque([start])
    while todo:
        s = todo.popleft()
        for mv in rh_moves(s):
            t = rh_apply(s, *mv)
            if t in prev:
                continue
            prev[t] = (s, mv)
            if rh_solved(t):
                path = []
                while prev[t] is not None:
                    s2, mv2 = prev[t]
                    path.append(mv2)
                    t = s2
                return path[::-1]
            todo.append(t)
        if len(prev) > limit:
            return None
    return None


def rh_random(rng, count):
    """A random layout: the red car on the exit row, `count` others that don't overlap."""
    red = (rng.randrange(0, 3), RH_EXIT_ROW, 2, True)
    vehicles = [red]
    taken = set(rh_cells(red))
    for _ in range(200):
        if len(vehicles) >= count + 1:
            break
        ln = rng.choice((2, 2, 3))
        horiz = rng.random() < 0.5
        if horiz and rng.random() < 0.7:
            continue  # fewer horizontal ones: they rarely block the exit row usefully
        x = rng.randrange(0, RH_N - ln + 1) if horiz else rng.randrange(RH_N)
        y = rng.randrange(RH_N) if horiz else rng.randrange(0, RH_N - ln + 1)
        if horiz and y == RH_EXIT_ROW:
            continue
        v = (x, y, ln, horiz)
        cells = rh_cells(v)
        if any(not (0 <= cx < RH_N and 0 <= cy < RH_N) for cx, cy in cells) or taken & set(cells):
            continue
        vehicles.append(v)
        taken |= set(cells)
    return vehicles


def rh_level(level):
    """(vehicles, solution) for a level: generated from a fixed seed, the hardest of a batch that fits the level's
    band of minimum slides (harder as the levels go up)."""
    if level in _RH_LEVELS:
        return _RH_LEVELS[level]
    rng = random.Random(1000 + level * 7919)
    lo, hi = 2 + level, 8 + 2 * level
    best = None
    for _ in range(80):
        vehicles = rh_random(rng, min(11, 5 + level // 2))
        sol = rh_solve(vehicles)
        if sol is None or len(sol) < 2:
            continue
        if lo <= len(sol) <= hi:
            best = (vehicles, sol)
            break
        if best is None or abs(len(sol) - lo) < abs(len(best[1]) - lo):
            best = (vehicles, sol)
    if best is None:  # can't happen in practice: a trivial board
        vehicles = [(0, RH_EXIT_ROW, 2, True), (3, 0, 3, False)]
        best = (vehicles, rh_solve(vehicles))
    _RH_LEVELS[level] = best
    return best


@register
class RushHour(Puzzle):
    id = "rushhour"
    title = "Rush Hour"
    description = ("Slide the cars and trucks out of the way so the solid car can drive out of the right side. A picks "
                   "the vehicle under the cursor, the d-pad slides it a cell at a time, A or B lets go. Each level's "
                   "fewest slides is shown.")
    params = [P("level", "number", 1, "Start at level", min=1, max=60), DEMO]
    AI_STEP = 0.15
    lower_better = True

    def score_key(self):
        return f"rushhour:{getattr(self, 'level', 1)}"

    def reset(self):
        self.level = max(1, int(self.p["level"] or 1)) - 1
        self.score = 0
        self.next_level()

    def next_level(self):
        self.level += 1
        vehicles, sol = rh_level(self.level)
        self.vehicles = tuple(vehicles)
        self.min_moves = len(sol)
        self.moves = 0
        self.sel = None
        self.cx, self.cy = self.vehicles[0][0], RH_EXIT_ROW
        self.solved_at = None
        self.drive = 0
        self.ai_plan = None

    def vehicle_at(self, cell):
        for i, v in enumerate(self.vehicles):
            if cell in rh_cells(v):
                return i
        return None

    def press(self, key, player):
        if self.solved_at:
            return
        d = MOVES.get(key)
        if d:
            if self.sel is None:
                self.cx = (self.cx + d[0]) % RH_N
                self.cy = (self.cy + d[1]) % RH_N
                return
            x, y, ln, horiz = self.vehicles[self.sel]
            step = d[0] if horiz else d[1]
            if step and (self.sel, step) in rh_moves(self.vehicles):
                self.vehicles = rh_apply(self.vehicles, self.sel, step)
                self.moves += 1
                self.cx += d[0] if horiz else 0
                self.cy += d[1] if not horiz else 0
                if rh_solved(self.vehicles):
                    self.solved_at = self.now
                    self.sel = None
            return
        if key == "a":
            if self.sel is not None:
                self.sel = None
            else:
                self.sel = self.vehicle_at((self.cx, self.cy))
        elif key == "b":
            self.sel = None

    def tick(self, now):
        if self.solved_at:
            t = now - self.solved_at
            self.drive = min(RH_CELL * 3, int(t * 24))
            if t > 0.5 and self.level + 1 not in _RH_LEVELS:
                rh_level(self.level + 1)  # ready before the hold ends
            if t > 3:
                key = self.score_key()
                new = self.record(self.moves, key, lower=True)
                self.score = self.moves
                if self.demo or self.level >= 60:
                    self.game_over("SOLVED", f"{self.moves}", f"MIN {self.min_moves}")
                else:
                    self.next_level()

    def frame_key(self, now):
        return self.ver, self.blink(now), self.drive, self.sel is not None and int(now * 4) % 2

    def draw_vehicle(self, c, v, solid, dx=0):
        x, y, ln, horiz = v
        px, py = x * RH_CELL + dx, y * RH_CELL
        w, h = (ln * RH_CELL - 1, RH_CELL - 1) if horiz else (RH_CELL - 1, ln * RH_CELL - 1)
        if solid:
            fill(c, px, py, w, h)
            for cx, cy in ((px, py), (px + w - 1, py), (px, py + h - 1), (px + w - 1, py + h - 1)):
                c.set(cx, cy, 0)
        else:
            c.rect(px, py, w, h)
            for cx, cy in ((px, py), (px + w - 1, py), (px, py + h - 1), (px + w - 1, py + h - 1)):
                c.set(cx, cy, 0)
            if ln == 3:  # a truck: a cab line
                if horiz:
                    fill(c, px + RH_CELL - 1, py + 1, 1, h - 2)
                else:
                    fill(c, px + 1, py + RH_CELL - 1, w - 2, 1)

    def draw(self, c, now):
        n = RH_N * RH_CELL
        for y in range(RH_N):  # the lot: a dot at each corner
            for x in range(RH_N):
                c.set(x * RH_CELL + RH_CELL - 1, y * RH_CELL + RH_CELL - 1)
        for i, v in enumerate(self.vehicles):
            if i == 0:
                self.draw_vehicle(c, v, True, self.drive)
            elif self.sel == i and int(now * 4) % 2:
                continue
            else:
                self.draw_vehicle(c, v, False)
        ex, ey = n, RH_EXIT_ROW * RH_CELL
        for k in range(3):  # the exit arrow
            c.set(ex + k, ey + 1 + k)
            c.set(ex + k, ey + 5 - k)
        if not self.solved_at and self.sel is None and self.blink(now):
            c.rect(self.cx * RH_CELL - 1, self.cy * RH_CELL - 1, RH_CELL + 1, RH_CELL + 1)
        px = n + 6
        c.text(px, 1, f"LVL {self.level}", SMALL)
        c.text(px, 9, f"MOVES {self.moves}", SMALL)
        c.text(px, 16, f"MIN {self.min_moves}", SMALL)
        if self.solved_at:
            c.text(px, 26, "SOLVED", SMALL)
        elif self.sel is not None:
            c.text(px, 26, "SLIDE", SMALL)
        if self.demo:
            c.text(px, 36, "DEMO", SMALL)

    def ai(self, now):
        if self.solved_at:
            return []
        if self.ai_plan is None:
            self.ai_plan = list(rh_solve(self.vehicles) or [])
        if not self.ai_plan:
            return []
        i, step = self.ai_plan.pop(0)
        x, y, ln, horiz = self.vehicles[i]
        keys = []
        if self.sel != i:
            if self.sel is not None:
                keys.append("b")
            keys += path_keys(self.cx, self.cy, x, y) + ["a"]
        keys.append(("right" if step > 0 else "left") if horiz else ("down" if step > 0 else "up"))
        return keys

    def draw_title(self, c, now):
        x0 = (c.w - 4 * RH_CELL) // 2
        self.draw_vehicle(c, (0, 3, 2, True), True, x0)
        self.draw_vehicle(c, (0, 2, 3, False), False, x0 + 2 * RH_CELL)
        self.draw_vehicle(c, (0, 3, 2, True), False, x0 + 3 * RH_CELL + 2)

    def describe(self):
        return f"Rush Hour ({self.state}, level {getattr(self, 'level', 0)})"
