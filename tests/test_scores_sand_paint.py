"""High scores and demos for the classic games (apps/scores.py, games.Attract), the sand hourglass and
sandbox (apps/sand.py) and the slow painting (apps/slowpaint.py).
Run: python3 -m pytest -q tests/test_scores_sand_paint.py"""

import datetime as dt
import json
import os
import random
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
from apps import base, blackjack, games, sand, scores, slowpaint  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402
from store import Store as RealStore  # noqa: E402

CLASSICS = ["snake", "tetris", "pong", "breakout", "invaders", "tron", "flappy", "platformer", "blackjack"]


class Store:
    def __init__(self, files=None):
        self.saved = dict(files or {})
        self.writes = 0

    def _load(self, name, default):
        return json.loads(json.dumps(self.saved.get(name, default)))

    def _save(self, name, data):
        self.writes += 1
        self.saved[name] = json.loads(json.dumps(data))


def make(app_id, params=None, store=None):
    return apps.create(Context(84, 42, store if store is not None else Store()), {"app": app_id, "params": params or {}})


def run(app, now, frames):
    c = None
    for _ in range(frames):
        c = Canvas(84, 42)
        app.render(c, now)
        now += app.interval
    return c, now


def offline(url):
    raise OSError("no network in tests")


class ScoreTable(unittest.TestCase):
    def test_top5_order_and_persistence(self):
        st = Store()
        ranks = [scores.submit(st, "snake", v)[1] for v in (5, 9, 1, 7, 3, 8)]
        self.assertEqual(ranks, [0, 0, 2, 1, 3, 1])
        self.assertEqual([e["score"] for e in scores.table(st, "snake")], [9, 8, 7, 5, 3])
        self.assertEqual(scores.submit(st, "snake", 2)[1], None)  # doesn't make the table
        # a fresh process reads the same file
        scores._cache.clear()
        self.assertEqual(scores.best(st, "snake")["score"], 9)
        self.assertIn("scores.json", st.saved)

    def test_lower_is_better_and_update_by_id(self):
        st = Store()
        eid, rank = scores.submit(st, "minesweeper:easy", 40, lower=True)
        scores.submit(st, "minesweeper:easy", 30, lower=True)
        self.assertEqual(scores.best(st, "minesweeper:easy")["score"], 30)
        self.assertEqual(scores.submit(st, "minesweeper:easy", 25, lower=True, eid=eid), (eid, 0))
        self.assertEqual(len(scores.table(st, "minesweeper:easy")), 2)  # updated, not added
        scores.submit(st, "minesweeper:easy", 99, lower=False)  # the table keeps its direction
        self.assertEqual(scores.table(st, "minesweeper:easy")[-1]["score"], 99)

    def test_old_puzzle_bests_are_imported(self):
        st = Store({"puzzle_scores.json": {"game2048": 2048, "sokoban:3": 40}})
        self.assertEqual(scores.best(st, "game2048")["score"], 2048)
        self.assertTrue(scores.everything(st)["sokoban:3"]["lower"])

    def test_names(self):
        st = Store()
        eid, _ = scores.submit(st, "tetris", 100)
        scores.name(st, "tetris", eid, "x.d!n")
        self.assertEqual(scores.best(st, "tetris")["name"], "XDN")
        self.assertEqual(scores.last_initials(st), "XDN")


class InitialsEntry(unittest.TestCase):
    def finish_snake(self, st, score, now):
        g = make("snake", {"demo": False}, st)
        g.input("a")
        g.score = score
        g.game_over()
        c, now = run(g, now, 1)  # the game over frame files the score
        return g, now

    def test_entry_names_the_score_and_shows_the_table(self):
        st = Store()
        now = time.time()
        g, now = self.finish_snake(st, 12, now)
        self.assertIsNotNone(g.scoreboard)
        self.assertEqual(scores.best(st, "snake")["score"], 12)  # saved before any initials
        self.assertEqual(scores.best(st, "snake")["name"], "")
        # A -> B (up), next, 9 (down twice from A, past the space), next, keep A, done
        for key in ("up", "right", "down", "down", "a", "a"):
            g.input(key)
            g.input(key, "up")
        self.assertEqual(scores.best(st, "snake")["name"], "B9A")
        self.assertEqual(g.scoreboard.phase, "table")
        c, now = run(g, now, 2)
        self.assertIn("1", "".join(c.rows()))
        c, now = run(g, now + games.TABLE_S + 1, 2)
        self.assertIsNone(g.scoreboard)
        self.assertEqual(g.state, "title")
        self.assertEqual(g.hi_text(), "HI 12 B9A")
        # the title alternates PRESS ANY BUTTON and the best score
        seen = set()
        for k in range(8):
            c = Canvas(84, 42)
            g.render(c, int(now) + k * 0.5)
            seen.add(c.rows()[30:36] == Canvas(84, 42).rows()[30:36])
        self.assertEqual(seen, {True, False})

    def test_default_initials_and_timeout(self):
        st = Store()
        now = time.time()
        g, now = self.finish_snake(st, 5, now)
        g.input("start")  # keeps the remembered initials (AAA the first time)
        self.assertEqual(scores.best(st, "snake")["name"], "AAA")
        g2, now = self.finish_snake(st, 3, now + 30)
        run(g2, now + games.ENTRY_TIMEOUT + 1, 1)  # nobody typed: the score stays, unnamed
        self.assertEqual(g2.scoreboard.phase, "table")
        self.assertEqual([e["name"] for e in scores.table(st, "snake")], ["AAA", ""])

    def test_low_score_with_full_table_asks_nothing(self):
        st = Store()
        for v in (50, 40, 30, 20, 10):
            scores.submit(st, "snake", v)
        g, _ = self.finish_snake(st, 2, time.time())
        self.assertIsNone(g.scoreboard)

    def test_demo_scores_are_never_filed(self):
        st = Store()
        g = make("snake", {}, st)
        now = time.time()
        _, now = run(g, now, int((games.DEMO_AFTER + 1) / g.interval))
        self.assertTrue(g.demo)
        g.score = 99
        g.game_over()
        run(g, now, 1)
        self.assertEqual(scores.table(st, "snake"), [])
        self.assertEqual(g.state, "title")

    def test_stopping_mid_game_still_files(self):
        st = Store()
        g = make("tetris", {"demo": False}, st)
        g.input("a")
        g.score = 300
        g.stop()
        self.assertEqual(scores.best(st, "tetris")["score"], 300)

    def test_runner_pack_and_arcade_use_the_table(self):
        st = Store()
        scores.submit(st, "dino", 777)
        scores.submit(st, "asteroids", 1234)
        self.assertEqual(make("dino", {}, st).best, 777)
        self.assertEqual(make("asteroids", {}, st).best, 1234)

    def test_puzzle_record_goes_to_the_shared_table(self):
        st = Store()
        g = make("game2048", {"demo": False}, st)
        g.input("a")
        self.assertTrue(g.record(512))
        self.assertTrue(g.record(1024))
        self.assertEqual(len(scores.table(st, "game2048")), 1)  # one entry per game, kept up to date
        g.game_over("NO MOVES")
        run(g, time.time(), 1)
        self.assertIsNotNone(g.scoreboard)
        self.assertEqual(scores.best(st, "game2048")["score"], 1024)

    def test_two_player_games_are_not_ranked(self):
        st = Store()
        g = make("pong", {"demo": False}, st)
        g.input("a")
        g.input("up", "down", player=2)
        g.score = 40
        g.game_over()
        run(g, time.time(), 1)
        self.assertEqual(scores.table(st, "pong"), [])


@mock.patch.object(blackjack, "_get", offline)
class Demos(unittest.TestCase):
    def test_every_classic_plays_a_demo(self):
        for gid in CLASSICS:
            with self.subTest(gid):
                random.seed(gid)
                st = Store()
                g = make(gid, {}, st)
                now = time.time()
                demo_frames, scored = 0, 0
                for _ in range(2000 + int(games.DEMO_AFTER / g.interval)):
                    c = Canvas(84, 42)
                    g.render(c, now)
                    if g.demo and g.state == "playing":
                        demo_frames += 1
                        scored = max(scored, g.score)
                    now += g.interval
                self.assertGreater(demo_frames, 1000, gid)
                self.assertGreater(scored, 0, f"{gid}: the demo never scored")
                self.assertEqual(scores.table(st, gid), [], gid)

    def test_a_button_takes_over_from_the_demo(self):
        g = make("breakout")
        now = time.time()
        _, now = run(g, now, int((games.DEMO_AFTER + 2) / g.interval))
        self.assertTrue(g.demo)
        g.input("left")
        self.assertFalse(g.demo)
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.lives, 3)

    def test_demo_off(self):
        g = make("snake", {"demo": False})
        run(g, time.time(), int((games.DEMO_AFTER + 2) / g.interval))
        self.assertFalse(g.demo)
        self.assertEqual(g.state, "title")


class Hourglass(unittest.TestCase):
    def test_grains_are_conserved_through_tilt_and_turning_over(self):
        h = make("hourglass", {"minutes": 0.5})
        n = h.sand.count()
        self.assertEqual(n, h.total)
        now = 1000.0
        for i in range(900):
            run(h, now, 1)
            now += 0.05
            if i == 100:
                h.input("left")
            if i == 200:
                h.input("left", "up")
                h.input("right")
            if i == 300:
                h.input("right", "up")
            if i == 400:
                h.input("a")
            self.assertEqual(h.sand.count(), n)

    def test_runs_out_on_time(self):
        h = make("hourglass", {"minutes": 0.5})
        now = 1000.0
        ran_out = None
        while now < 1040:
            run(h, now, 1)
            if h.done and ran_out is None:
                ran_out = now - 1000
            now += 0.05
        self.assertIsNotNone(ran_out)
        self.assertAlmostEqual(ran_out, 30, delta=1.5)
        self.assertEqual(h.top_count(), 0)

    def test_tilt_moves_sand_to_one_side(self):
        h = make("hourglass", {"minutes": 60, "fill": 40})
        w, cx = h.w, h.cx

        def sides():
            top = h.sand.grid[:h.neck * w]
            return (sum(1 for i, v in enumerate(top) if v == sand.SAND and i % w < cx),
                    sum(1 for i, v in enumerate(top) if v == sand.SAND and i % w > cx))

        left, right = sides()
        self.assertLessEqual(abs(left - right), 1)  # level to begin with
        h.input("left")
        self.assertEqual(h.sand.tilt, -1)
        run(h, 1000.0, 300)
        left, right = sides()
        self.assertGreater(left, 2 * right)  # leaning against the low wall
        h.input("left", "up")
        run(h, 1100.0, 100)
        self.assertGreater(sides()[0], 2 * sides()[1])  # level again it stays where it slid

    def test_turning_over_restarts_with_what_fell(self):
        h = make("hourglass", {"minutes": 1})
        run(h, 1000.0, 400)  # 20 s: a third has fallen
        top_before, bottom_before = h.top_count(), h.total - h.top_count()
        h.input("a")
        self.assertEqual(h.top_count(), bottom_before - sum(
            1 for y in (h.neck, h.neck + 1) if h.sand.grid[y * h.w + h.cx] == sand.SAND))
        self.assertLess(h.remaining(), 60 * bottom_before / h.total + 1)
        self.assertGreater(top_before, 0)

    def test_notification_when_it_runs_out(self):
        calls = []
        with mock.patch.object(base, "notify", lambda **kw: calls.append(kw)):
            h = make("hourglass", {"minutes": 0.25, "notify": True, "label": "Tea"})
            run(h, 1000.0, int(20 / 0.05))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["spec"]["params"]["text"], "Tea")

    def test_cheap_frames(self):
        h = make("hourglass", {"minutes": 0.25, "fill": 95})
        now, worst = 1000.0, 0
        for _ in range(300):
            c = Canvas(84, 42)
            t = time.perf_counter()
            h.render(c, now)
            worst = max(worst, time.perf_counter() - t)
            now += 0.05
        self.assertLess(worst, 0.02)


class Sandbox(unittest.TestCase):
    def test_pour_wall_erase_and_cap(self):
        s = make("sandbox", {"max_grains": 100, "demo": False})
        now = 1000.0
        s.input("a")
        _, now = run(s, now, 200)
        self.assertEqual(s.sand.count(), 100)  # the cap holds
        s.input("a", "up")
        s.input("b")  # wall
        s.input("down")
        s.input("a")
        _, now = run(s, now, 3)
        self.assertIn(sand.WALL, s.sand.grid)
        n = s.sand.count()
        _, now = run(s, now, 100)
        self.assertEqual(s.sand.count(), n)  # moving sand neither appears nor vanishes
        s.input("start")
        self.assertEqual(s.sand.count(), 0)

    def test_idle_pours_by_itself(self):
        s = make("sandbox")
        run(s, 1000.0, int((s.IDLE + 10) / s.interval))
        self.assertGreater(s.sand.count(), 50)


class SlowPaint(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = RealStore(self.dir)
        a = Canvas(84, 42)
        a.rect(10, 5, 30, 30, fill=True)
        a.text(50, 10, "HI")
        b = Canvas(84, 42)
        b.line(0, 41, 83, 0)
        b.rect(44, 4, 30, 20, fill=True)
        self.store.save_scene("A", [{"rows": a.rows(), "delay": 100}], "a")
        self.store.save_scene("B", [{"rows": b.rows(), "delay": 100}], "b")
        self.a = bytes(a.px)

    def test_never_exceeds_the_budget_and_converges(self):
        p = make("slowpaint", {"budget": 600, "per": "hour", "linger": 0}, self.store)
        now = dt.datetime(2026, 10, 10, 9, 0, 0).timestamp()
        prev, per_hour = None, {}
        for _ in range(3 * 3600):
            c = Canvas(84, 42)
            p.render(c, now)
            if prev is not None:
                hour = dt.datetime.fromtimestamp(now).hour
                per_hour[hour] = per_hour.get(hour, 0) + sum(x != y for x, y in zip(prev, c.px))
            prev = bytes(c.px)
            if p.target_id == "a" and bytes(c.px) == self.a:
                break
            now += 1
        self.assertTrue(all(n <= 600 for n in per_hour.values()), per_hour)
        self.assertEqual(prev, self.a)  # got there
        self.assertIn("100% of A", p.describe())

    def test_flips_are_spread_over_the_window(self):
        p = make("slowpaint", {"budget": 240, "per": "day"}, self.store)
        start = dt.datetime(2026, 10, 10, 0, 0, 0).timestamp()
        for h in range(0, 6):
            p.render(Canvas(84, 42), start + h * 3600)
        self.assertLessEqual(p.used, 240 * 5 / 24 + 1)
        self.assertGreater(p.used, 0)

    def test_state_survives_a_restart(self):
        p = make("slowpaint", {"budget": 600, "per": "hour"}, self.store)
        now = dt.datetime(2026, 10, 10, 9, 0, 0).timestamp()
        run(p, now, 600)
        p.stop()
        q = make("slowpaint", {"budget": 600, "per": "hour"}, self.store)
        self.assertEqual(q.cur, p.cur)
        self.assertEqual(q.used, p.used)
        c = Canvas(84, 42)
        q.render(c, now + 600)
        self.assertLessEqual(q.used, slowpaint.allowance(600, "hour", now + 600))

    def test_choose_prefers_error_dense_areas_and_less_worn_discs(self):
        w, h = 84, 42
        cur, target = bytearray(w * h), bytearray(w * h)
        for y in range(10, 20):
            for x in range(10, 20):
                target[y * w + x] = 1
        target[5 * w + 70] = 1  # a stray dot
        first = slowpaint.choose(cur, target, w, h, 1, rng=random.Random(1))[0]
        self.assertTrue(10 <= first % w < 20)
        # two equally wrong dots far apart: the less worn one goes first
        cur, target = bytearray(w * h), bytearray(w * h)
        target[5 * w + 5] = target[30 * w + 60] = 1
        wear = [0] * (w * h)
        wear[5 * w + 5] = 500
        wear[30 * w + 60] = 10
        self.assertEqual(slowpaint.choose(cur, target, w, h, 1, wear=wear, rng=random.Random(1)), [30 * w + 60])

    def test_batch_picks_keep_apart(self):
        w, h = 84, 42
        cur, target = bytearray(w * h), bytearray([1]) * (w * h)
        picks = slowpaint.choose(cur, target, w, h, 20, rng=random.Random(2))
        close = sum(1 for i in picks for j in picks if i < j and abs(i % w - j % w) <= 3 and abs(i // w - j // w) <= 3)
        self.assertEqual(close, 0)

    def test_no_pictures(self):
        p = make("slowpaint", {}, RealStore(tempfile.mkdtemp()))
        p.render(Canvas(84, 42), time.time())
        self.assertIn("No pictures", p.describe())


if __name__ == "__main__":
    unittest.main()
