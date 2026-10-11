"""Grid puzzles (apps/pack_grid.py): Picross from pictures (line solver, clues, layouts), Pipe Mania (flow and
leaks), Flow (levels that always fill) and Rush Hour (moves, solver, win). Every game renders, takes input and
plays its own demo. Run: python3 -m pytest -q tests/test_grid_puzzles.py"""

import json
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
from apps import pack_grid as G  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

GAMES = ["picross", "pipes", "flow", "rushhour"]


def blob_rows():
    return ["".join("1" if ((x - 42) ** 2 / 400 + (y - 21) ** 2 / 100 < 1) or (y > 30 and 20 < x < 64) else "0"
                    for x in range(84)) for y in range(42)]


class Store:
    def __init__(self, scenes=True, drawings=True):
        self.saved, self.settings = {}, {}
        self.scenes = [{"id": "s1", "name": "Blob", "frames": 1, "thumb": blob_rows()}] if scenes else []
        self.drawings = {}
        if drawings:
            px = bytearray(84 * 42)
            for y in range(42):
                for x in range(84):
                    px[y * 84 + x] = 1 if (x // 6 + y // 6) % 2 == 0 and 10 < x < 74 else 0
            self.drawings["d1"] = {"name": "Checks", "w": 84, "h": 42, "px": px, "version": 0}

    def _load(self, n, d):
        return json.loads(json.dumps(self.saved.get(n, d)))

    def _save(self, n, d):
        self.saved[n] = json.loads(json.dumps(d))

    def list_scenes(self):
        return self.scenes


def make(app_id, params=None, store=None):
    return apps.create(Context(84, 42, store if store is not None else Store()), {"app": app_id, "params": params or {}})


def press(app, *keys):
    for k in keys:
        app.input(k, "down", 1)
        app.input(k, "up", 1)


def frame(app, now):
    c = Canvas(84, 42)
    app.render(c, now)
    return c


def lit(c):
    return sum(c.px)


def start(app, now=None):
    now = now or time.time()
    frame(app, now)
    press(app, "a")  # off the title card
    app.now = now
    return now


class PicrossTest(unittest.TestCase):
    def test_clues_and_line_solver(self):
        self.assertEqual(G.px_clues([1, 1, 0, 1, 0, 0, 1, 1, 1]), [2, 1, 3])
        self.assertEqual(G.px_clues([0, 0, 0]), [0])
        # a full line is certain at once; an overlapping run fixes its middle; an impossible clue is None
        self.assertEqual(G.px_line([5], [-1] * 5), [1, 1, 1, 1, 1])
        self.assertEqual(G.px_line([4], [-1] * 6), [-1, -1, 1, 1, -1, -1])
        self.assertEqual(G.px_line([0], [-1] * 3), [0, 0, 0])
        self.assertIsNone(G.px_line([3], [0, -1, 0, -1, 0]))
        self.assertEqual(G.px_line([1, 1], [1, -1, -1]), [1, 0, 1])

    def test_solver_solves_a_simple_grid_and_shrinking_keeps_the_shape(self):
        grid = [[1 if ch == "#" else 0 for ch in r] for r in G.BUILTIN["heart"]]
        rows = [G.px_clues(r) for r in grid]
        cols = [G.px_clues([grid[y][x] for y in range(len(grid))]) for x in range(len(grid[0]))]
        solved, ok = G.px_solve(len(grid[0]), len(grid), rows, cols)
        self.assertTrue(ok)
        self.assertEqual(solved, grid)
        small = G.px_shrink(blob_rows(), 20, 10)
        self.assertEqual((len(small), len(small[0])), (10, 20))
        self.assertEqual(small[5][10], 1)  # the middle of the blob
        self.assertEqual(small[0][0], 0)

    def test_puzzles_come_from_your_pictures_and_fit_their_layout(self):
        for layout in ("clues", "cursor"):
            cands = G.px_candidates(Store(), layout)
            self.assertTrue(cands, layout)
            for name, grid, pic in cands:
                self.assertIn((len(grid[0]), len(grid)), G.PX_SIZES[layout])
                self.assertIsNotNone(G.px_fits(grid, layout))
                self.assertEqual(len(pic), 42)
        self.assertEqual(G.px_candidates(Store(scenes=False, drawings=False), "clues"), [])
        for layout in ("clues", "cursor"):
            self.assertTrue(G.px_builtins(layout), layout)

    def test_a_game_fills_marks_counts_mistakes_and_reveals(self):
        for layout in ("clues", "cursor"):
            app = make("picross", {"layout": layout})
            now = start(app)
            sol = app.solution
            # a right fill, a wrong fill (a mistake, marked empty), a mark toggled
            yes = next((x, y) for y in range(app.h_cells) for x in range(app.w_cells) if sol[y][x])
            no = next((x, y) for y in range(app.h_cells) for x in range(app.w_cells) if not sol[y][x])
            press(app, *G.wrap_keys(0, 0, *yes, app.w_cells, app.h_cells), "a")
            self.assertEqual(app.cells[yes[1]][yes[0]], 1)
            press(app, *G.wrap_keys(*yes, *no, app.w_cells, app.h_cells), "a")
            self.assertEqual(app.cells[no[1]][no[0]], 0)
            self.assertEqual(app.mistakes, 1)
            press(app, "b")
            self.assertEqual(app.cells[no[1]][no[0]], -1)
            c = frame(app, now + 1)
            self.assertGreater(lit(c), 20)
            # fill the rest: solved, then the picture shows full size
            for y in range(app.h_cells):
                for x in range(app.w_cells):
                    if sol[y][x] and app.cells[y][x] != 1:
                        app.cx, app.cy = x, y
                        press(app, "a")
            self.assertIsNotNone(app.solved_at)
            self.assertGreaterEqual(app.score, 20)
            reveal = frame(app, app.solved_at + 3)
            self.assertGreater(lit(reveal), 400)  # the whole picture, not a 15 x 7 grid
            frame(app, app.solved_at + 7)
            self.assertEqual(app.state, "over")

    def test_demo_solves_it(self):
        app = make("picross", {"layout": "clues", "source": "builtin"})
        now = start(app)
        for _ in range(400):
            keys = app.ai(now)
            if not keys:
                break
            for k in keys:
                app.press(k, 1)
        self.assertIsNotNone(app.solved_at)
        self.assertEqual(app.mistakes, 0)


class PipesTest(unittest.TestCase):
    def test_pipe_ends_and_exits(self):
        self.assertEqual(G.pipe_exit("h", "L"), "R")
        self.assertEqual(G.pipe_exit("ur", "U"), "R")
        self.assertEqual(G.pipe_exit("ur", "R"), "U")
        self.assertIsNone(G.pipe_exit("ur", "D"))
        self.assertEqual(G.pipe_exit("x", "U"), "D")

    def test_water_flows_along_laid_pipe_and_leaks_at_a_gap(self):
        random.seed(1)
        app = make("pipes")
        now = start(app)
        app.start, app.src_dir, app.head = (2, 2), "R", (2, 2, "R")
        app.grid = {(3, 2): {"kind": "h", "full": ""}, (4, 2): {"kind": "dl", "full": ""}, (4, 3): {"kind": "x", "full": ""},
                    (4, 4): {"kind": "ur", "full": ""}, (5, 4): {"kind": "h", "full": ""}}
        app.flow_at = now
        app.advance()
        self.assertEqual(app.head, (3, 2, "R"))
        self.assertEqual(app.grid[(3, 2)]["full"], "LR")
        app.advance()
        self.assertEqual(app.head, (4, 2, "D"))
        app.advance()
        self.assertEqual(app.head, (4, 3, "D"))
        self.assertEqual(app.grid[(4, 3)]["full"], "UD")
        app.advance()
        self.assertEqual(app.head, (4, 4, "R"))
        app.advance()
        self.assertEqual(app.dist, 5)
        self.assertIsNone(app.leak)
        app.advance()  # (6, 4) is empty
        self.assertEqual(app.leak[:2], (6, 4))
        # a cross can carry water the other way too, but not twice the same way
        app2 = make("pipes")
        start(app2)
        app2.grid = {(3, 2): {"kind": "x", "full": "UD"}}
        app2.head, app2.leak = (2, 2, "R"), None
        app2.advance()
        self.assertEqual(app2.grid[(3, 2)]["full"], "UDLR")
        app2.head, app2.leak = (2, 2, "R"), None
        app2.advance()
        self.assertIsNotNone(app2.leak)

    def test_placing_and_the_countdown(self):
        random.seed(2)
        app = make("pipes")
        now = start(app)
        self.assertEqual(len(app.queue), 5)
        head = app.queue[0]
        app.cx, app.cy = (app.start[0] + 1) % G.PM_COLS, (app.start[1] + 1) % G.PM_ROWS
        press(app, "a")
        self.assertEqual(app.grid[(app.cx, app.cy)]["kind"], head)
        self.assertEqual(len(app.queue), 5)
        app.cx, app.cy = app.start
        n = len(app.grid)
        press(app, "a")
        self.assertEqual(len(app.grid), n)  # not on the source
        self.assertGreater(app.flow_at, now)
        press(app, "b")  # start the water now
        self.assertLessEqual(app.flow_at, app.now)
        frame(app, app.now + 0.01)
        self.assertTrue(app.leak or app.dist >= 1)

    def test_demo_lays_pipe_ahead_of_the_water(self):
        random.seed(5)
        app = make("pipes")
        now = start(app)
        app.flow_at = now + 60
        for _ in range(6):
            for k in app.ai(now):
                app.press(k, 1)
        need = app.need()
        self.assertTrue(need is None or need[:2] != app.head[:2])
        self.assertGreaterEqual(len(app.grid), 3)


class FlowTest(unittest.TestCase):
    def test_hamiltonian_paths_cover_the_grid(self):
        rng = random.Random(3)
        for n in (5, 6, 7):
            path = G.hamiltonian_path(n, rng)
            self.assertEqual(len(set(path)), n * n)
            for a, b in zip(path, path[1:]):
                self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)

    def test_levels_always_fill_and_every_path_is_long_enough(self):
        rng = random.Random(9)
        for n, pairs in ((5, 3), (6, 4), (7, 5)):
            for _ in range(5):
                paths = G.flow_level(n, pairs, rng)
                self.assertEqual(len(paths), pairs)
                self.assertEqual(sum(map(len, paths)), n * n)
                self.assertEqual(len({c for p in paths for c in p}), n * n)
                self.assertTrue(all(len(p) >= 3 for p in paths))

    def test_drawing_cutting_and_finishing_a_level(self):
        random.seed(4)
        app = make("flow")
        now = start(app)
        self.assertEqual(app.level, 1)
        # draw the first pair along the solution
        p0 = app.paths[0]
        press(app, *G.path_keys(app.cx, app.cy, *p0[0]), "a")
        self.assertEqual(app.drawing, 0)
        for a, b in zip(p0, p0[1:]):
            press(app, {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}[(b[0] - a[0], b[1] - a[1])])
        self.assertIsNone(app.drawing)  # joined up
        self.assertTrue(app.complete(0))
        # drawing another pair through it cuts it
        p1 = app.paths[1]
        press(app, *G.path_keys(app.cx, app.cy, *p1[0]), "a")
        cut = p0[len(p0) // 2]
        press(app, *G.path_keys(*p1[0], *cut))  # straight at a cell of the first line (may stop at a mark)
        if cut in app.lines[1]:
            self.assertNotIn(cut, app.lines[0])
            self.assertFalse(app.complete(0))
        press(app, "b")
        self.assertIsNone(app.drawing)
        # the demo finishes the level
        for _ in range(50):
            ks = app.ai(now)
            if not ks:
                break
            for k in ks:
                app.press(k, 1)
        self.assertIsNotNone(app.done_at)
        self.assertEqual(app.score, 1)
        frame(app, app.done_at + 2.5)
        self.assertEqual(app.level, 2)
        app.level_at = now - G.FL_LEVEL_S - 1
        frame(app, now)
        self.assertEqual(app.state, "over")


class RushHourTest(unittest.TestCase):
    def test_moves_apply_and_the_solver_finds_the_minimum(self):
        v = ((1, 2, 2, True), (3, 1, 3, False), (0, 0, 2, True))
        moves = G.rh_moves(v)
        self.assertIn((0, -1), moves)
        self.assertNotIn((0, 1), moves)  # the truck blocks the car
        self.assertIn((1, 1), moves)
        self.assertNotIn((2, -1), moves)
        t = G.rh_apply(v, 1, 1)
        self.assertEqual(t[1], (3, 2, 3, False))
        sol = G.rh_solve(v)
        self.assertEqual(len(sol), 2 + 3)  # truck down two (clear of the exit row), car right three
        s = v
        for mv in sol:
            s = G.rh_apply(s, *mv)
        self.assertTrue(G.rh_solved(s))
        self.assertEqual(G.rh_solve(((4, 2, 2, True),)), [])

    def test_levels_are_fixed_and_get_harder(self):
        G._RH_LEVELS.clear()
        a, sa = G.rh_level(3)
        b, sb = G.rh_level(3)
        self.assertEqual(a, b)
        self.assertGreaterEqual(len(sa), 2)
        _, s10 = G.rh_level(10)
        self.assertGreater(len(s10), len(sa))
        cells = [c for v in a for c in G.rh_cells(v)]
        self.assertEqual(len(cells), len(set(cells)))  # no overlaps
        self.assertEqual(a[0][1], G.RH_EXIT_ROW)

    def test_select_slide_and_win(self):
        app = make("rushhour", {"level": 1})
        now = start(app)
        self.assertEqual(app.level, 1)
        sol = G.rh_solve(app.vehicles)
        for i, step in sol:
            x, y, ln, horiz = app.vehicles[i]
            app.cx, app.cy, app.sel = x, y, None
            press(app, "a")
            self.assertEqual(app.sel, i)
            press(app, ("right" if step > 0 else "left") if horiz else ("down" if step > 0 else "up"))
            press(app, "b")
        self.assertEqual(app.moves, len(sol))
        self.assertIsNotNone(app.solved_at)
        frame(app, app.solved_at + 1)
        self.assertGreater(app.drive, 0)
        frame(app, app.solved_at + 3.5)
        self.assertEqual(app.level, 2)
        self.assertEqual(app.moves, 0)
        # a blocked slide doesn't count
        app.sel = 0
        before = app.vehicles
        if (0, 1) not in G.rh_moves(app.vehicles):
            press(app, "right")
            self.assertEqual(app.vehicles, before)
            self.assertEqual(app.moves, 0)


class EveryGameTest(unittest.TestCase):
    def test_renders_takes_input_and_demos(self):
        for gid in GAMES:
            app = make(gid)
            self.assertEqual(app.state, "title")
            now = time.time()
            card = frame(app, now)
            self.assertGreater(lit(card), 50, gid)
            press(app, "a")
            self.assertEqual(app.state, "playing")
            for k in ("right", "down", "a", "b", "left", "up"):
                press(app, k)
                frame(app, now + 0.1)
            # the demo: back on the title, the computer plays after the card
            app.state, app.title_at, app.demo = "title", now, False
            app.ver += 1
            frame(app, now + app.CARD + 1)
            self.assertTrue(app.demo, gid)
            for i in range(60):
                frame(app, now + app.CARD + 1 + i * 0.2)
            self.assertIn("(", app.describe())

    def test_in_the_catalog_with_music_effects(self):
        for gid in GAMES:
            cls = apps.REGISTRY[gid]
            self.assertEqual(cls.category, "Games")
            self.assertIn("react_fx", {p["name"] for p in cls.params}, gid)


if __name__ == "__main__":
    unittest.main()
