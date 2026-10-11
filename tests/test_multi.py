"""Four-player games (apps/pack_multi.py): Curve fever, Pong 4, Tetris battle, Breakout co-op. Seats for players
1-4 with the computer in the empty ones, rounds and lives, garbage and lanes, and demos that run on their own.
Run: python3 -m pytest -q tests/test_multi.py"""

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
from apps import pack_multi  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402


class Store:
    def __init__(self):
        self.settings = {}
        self.saved = {}

    def _load(self, name, default):
        return self.saved.get(name, default)

    def _save(self, name, data):
        self.saved[name] = data


def make(app_id, **params):
    random.seed(5)
    a = apps.create(Context(84, 42, Store()), {"app": app_id, "params": params})
    a.render(Canvas(84, 42), 0.0)
    return a


def start(a, t=1.0, players=(1,)):
    """Buttons from these controllers: a real game with them as humans."""
    for p in players:
        a.input("a", "down", p)
        a.input("a", "up", p)
    a.render(Canvas(84, 42), t)
    return a


def run(a, t, seconds, dt=0.05):
    c = None
    end = t + seconds
    while t < end:
        c = Canvas(84, 42)
        a.render(c, t)
        t += dt
    return t, c


def demo(a):
    a.demo, a.demo_until, a.state = True, 1e9, "playing"
    a.reset()
    return a


def lit(c):
    return sum(c.px)


class Seats(unittest.TestCase):
    def test_every_game_takes_input_from_four_controllers_and_fills_the_rest_with_the_computer(self):
        for app_id in ("curve", "pong4", "breakout_coop"):
            a = make(app_id, players=4)
            start(a, players=(1, 3))
            self.assertEqual(a.state, "playing")
            self.assertEqual([a.human(p) for p in (1, 2, 3, 4)], [True, False, True, False], app_id)
            self.assertEqual([a.cpu(i) for i in range(4)], [False, True, False, True], app_id)
            self.assertEqual(a.name(1), "CPU2")
            self.assertEqual(a.short(2), "P3")
            for p in (1, 2, 3, 4):
                a.input("left", "down", p)
                self.assertTrue(a.holding(p - 1, "left"))
                a.input("left", "up", p)
        a = make("tetris_battle")
        start(a, players=(1, 2))
        self.assertEqual([a.cpu(0), a.cpu(1)], [False, False])
        self.assertEqual(a.seats(), 2)

    def test_demos_run_a_minute_without_errors(self):
        for app_id in ("curve", "pong4", "tetris_battle", "breakout_coop"):
            a = make(app_id)
            a.title_since = 0.0
            t, c = run(a, 13.0, 60.0)  # after DEMO_AFTER on the title the computer plays
            self.assertTrue(lit(c) > 10, app_id)
            self.assertIn(a.state, ("playing", "title", "over"), app_id)
            if a.state == "playing":
                self.assertTrue(a.demo, app_id)
            a.input("a", "down", 2)  # any button ends the demo
            self.assertFalse(a.demo, app_id)


class CurveTest(unittest.TestCase):
    def test_turning_leaves_a_trail_and_crossing_one_kills(self):
        a = start(make("curve", players=2, wins=3))
        t, _ = run(a, 1.0, 1.5)  # past the "round 1" moment
        s = a.snakes[0]
        s.x, s.y, s.a, s.cell = 30.0, 20.0, 0.0, (30, 20)
        a.input("right", "down", 1)
        heading = s.a
        t, _ = run(a, t, 0.5)
        self.assertGreater(s.a, heading + 1.0)  # a clockwise turn (right)
        a.input("right", "up", 1)
        self.assertTrue(s.alive)
        self.assertGreater(sum(a.occ), 15)  # the trail behind it
        # a wall of trail straight ahead: the snake dies on it
        s.a = 0.0
        for y in range(pack_multi.CURVE_TOP, a.h):
            a.occ[y * a.w + int(s.x) + 6] = 1
        t, _ = run(a, t, 0.8)
        self.assertFalse(s.alive)
        self.assertEqual(a.wins, [0, 1])  # the other one wins the round

    def test_gaps_are_passable_and_textures_differ(self):
        a = start(make("curve", players=4))
        t, _ = run(a, 1.0, 10.0)
        occ_total, vis_total = sum(a.occ), sum(a.vis)
        self.assertGreater(occ_total, 40)
        self.assertLess(vis_total, occ_total + 40)  # dotted and dashed trails draw fewer dots than they block
        self.assertEqual([s.texture for s in a.snakes], ["solid", "dotted", "dashed", "double"])
        # a gap: the snake moves on without marking cells
        s = a.snakes[0]
        s.alive, s.x, s.y, s.a, s.cell = True, 40.0, 20.0, math.pi / 2, (40, 20)
        for sn in a.snakes:  # everyone in a gap: nobody marks a cell, though they all move on
            sn.gap_at, sn.gap_until = -1.0, a.t - a.round_t + 1.0
        before, x0 = sum(a.occ), s.x
        a.held.clear()
        for _ in range(8):
            a.step()
        self.assertEqual(sum(a.occ), before)
        self.assertNotEqual((s.x, s.y), (x0, 20.0))

    def test_rounds_to_a_match(self):
        a = demo(make("curve", players=3, wins=2))
        t, ended = 1.0, False
        while t < 200.0 and not ended:
            a.render(Canvas(84, 42), t)
            t += 0.04
            ended = a.state == "title"  # the demo's match ended with a result
        self.assertTrue(ended)
        self.assertGreaterEqual(max(a.wins), 2)
        self.assertEqual(len(a.wins), 3)
        self.assertEqual(a.speed(), 12.0)


class Pong4Test(unittest.TestCase):
    def test_sides_paddles_and_lives(self):
        a = start(make("pong4", players=4, lives=2), players=(1, 2, 3, 4))
        self.assertEqual(a.lives, [2, 2, 2, 2])
        a.input("down", "down", 1)
        a.input("left", "down", 3)
        p1, p3 = a.pos[0], a.pos[2]
        run(a, 1.0, 0.5)
        self.assertGreater(a.pos[0], p1)   # left paddle moved down
        self.assertLess(a.pos[2], p3)      # top paddle moved left
        a.held.clear()
        # the ball out past the top costs player 3 a life and serves again
        a.bx, a.by, a.vx, a.vy = 60.0, 1.5, 0.0, -40.0
        a.pos[2] = 0.0
        a.pause_until = 0.0
        for _ in range(10):
            a.step()
        self.assertEqual(a.lives, [2, 2, 1, 2])
        self.assertEqual((a.bx, a.by), (42.0, 21.0))  # served from the middle
        # two players only: the top and bottom are walls
        b = start(make("pong4", players=2))
        b.bx, b.by, b.vx, b.vy = 40.0, 1.5, 0.0, -40.0
        b.pause_until = 0.0
        b.step()
        self.assertGreater(b.vy, 0)
        self.assertEqual(b.lives, [3, 3])

    def test_last_one_in_wins_and_the_rally_speeds_up(self):
        a = start(make("pong4", players=3, lives=1))
        v0 = math.hypot(a.vx, a.vy)
        a.bx, a.by, a.vx, a.vy = 3.0, a.pos[0] + 4, -30.0, 0.0
        a.pause_until = 0.0
        for _ in range(4):
            a.step()
        self.assertGreater(a.vx, 0)
        self.assertGreater(math.hypot(a.vx, a.vy), v0 - 1e-9)
        self.assertEqual(a.returns, 1)
        a.lives = [1, 0, 1]
        a.bx, a.by, a.vx, a.vy = 60.0, 1.5, 0.0, -40.0
        a.pos[2] = 0.0  # the top paddle is nowhere near
        for _ in range(10):
            a.step()
        self.assertEqual(a.winner, 0)
        self.assertEqual(a.result()[0], "P1 WINS")
        run(a, 10.0, 3.0)
        self.assertEqual(a.state, "over")


class TetrisBattleTest(unittest.TestCase):
    def test_two_or_more_lines_send_garbage_and_topping_out_loses(self):
        w = pack_multi.Well(0, 0.0)
        for r in range(18, 20):  # two rows missing one cell each
            w.grid[r] = [1] * 9 + [0]
        w.kind, w.cells, w.px, w.py = "I", [(0, 0), (0, 1), (0, 2), (0, 3)], 9, 16
        sent = w.lock()
        self.assertEqual((w.lines, sent, w.sent), (2, 1, 1))
        self.assertEqual(pack_multi.ATTACK[4], 4)
        other = pack_multi.Well(1, 0.0)
        other.pending = 2
        other.kind, other.cells, other.px, other.py = "O", list(pack_multi.PIECES["O"]), 3, 17
        other.lock()
        self.assertEqual(other.pending, 0)
        self.assertEqual([sum(r) for r in other.grid[-2:]], [9, 9])  # two garbage rows, a hole in each
        other.grid[0][4] = 1
        other.take_garbage(1)
        self.assertTrue(other.dead)

    def test_a_match_of_rounds_against_the_computer(self):
        a = start(make("tetris_battle", wins=1, difficulty="hard"))
        self.assertTrue(a.cpu(1) and not a.cpu(0))
        t, _ = run(a, 1.0, 1.2)
        a.wells[0].grid = [[0] * 10] + [[1] * 9 + [0] for _ in range(19)]  # player 1 about to top out
        a.input("b", "down", 1)
        t, _ = run(a, t, 0.5)
        self.assertEqual(a.round_winner, 1)
        self.assertEqual(a.wins, [0, 1])
        t, _ = run(a, t, 3.0)
        self.assertEqual(a.state, "over")
        self.assertEqual(a.result(), ("CPU2 WINS", "0-1"))

    def test_keys_move_the_piece(self):
        a = start(make("tetris_battle"))
        run(a, 1.0, 1.2)
        px = a.wells[0].px
        a.input("left", "down", 1)
        self.assertEqual(a.wells[0].px, px - 1)
        a.input("b", "down", 1)  # hard drop locks
        self.assertTrue(any(any(r) for r in a.wells[0].grid))


class BreakoutCoopTest(unittest.TestCase):
    def test_lanes_and_bricks(self):
        a = start(make("breakout_coop", players=3), players=(1, 2))
        lo, hi = a.lane(1)
        self.assertEqual((round(lo), round(hi)), (28, 46))
        a.input("left", "down", 2)
        for _ in range(60):
            a.step()
        self.assertEqual(a.px[1], lo)  # stays in its own lane
        a.input("left", "up", 2)
        a.input("a", "down", 1)
        self.assertFalse(a.stuck)
        bricks, score = a.left, a.score
        a.balls = [[30.0, pack_multi.BRICKS_Y + a.rows * 3 + 1, 0.0, -30.0]]
        for _ in range(6):
            a.step()
        self.assertEqual(a.left, bricks - 1)
        self.assertEqual(a.score, score + 10)
        self.assertGreater(a.balls[0][3], 0)  # bounced back down

    def test_capsules_add_balls_and_losing_them_all_costs_a_life(self):
        a = start(make("breakout_coop", players=2, lives=2))
        a.input("a", "down", 1)
        a.capsules = [[a.px[0] + 5, pack_multi.PADDLE_Y - 1.0]]
        a.step()
        self.assertEqual(len(a.balls), 2)
        a.balls = []
        a.step()
        self.assertEqual(a.lives, 1)
        self.assertTrue(a.stuck)
        a.input("a", "down", 1)
        a.balls = [[40.0, 60.0, 0.0, 30.0]]
        a.step()
        self.assertEqual(a.state, "over")

    def test_computer_paddles_launch_and_play(self):
        a = demo(make("breakout_coop", players=4))
        t, c = run(a, 1.0, 20.0)
        self.assertFalse(a.stuck)
        self.assertGreater(a.score, 0)


if __name__ == "__main__":
    unittest.main()
