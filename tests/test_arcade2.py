"""Arcade pack 2 (apps/pack_arcade2.py): Dig Dug digs and pumps, Defender's landers abduct and the scanner shows
them, Joust's lance height settles a collision, the marble falls and finishes, the ghosts catch Pac-Man; every
game renders, takes input, and plays itself as a demo without errors.
Run: python3 -m pytest -q tests/test_arcade2.py"""

import json
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
from apps import pack_arcade, pack_arcade2 as a2, scores  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

IDS = ["digdug", "defender", "joust", "marble", "ghosts"]


class Store:
    def __init__(self):
        self.saved = {}
        self.settings = {}

    def _load(self, name, default):
        return json.loads(json.dumps(self.saved.get(name, default)))

    def _save(self, name, data):
        self.saved[name] = json.loads(json.dumps(data))


def make(gid, params=None, store=None):
    return apps.create(Context(84, 42, store or Store()), {"app": gid, "params": params or {}})


def start(gid, params=None, store=None, now=None):
    """A game in play (a button on the title starts it), on a known clock."""
    g = make(gid, params, store)
    g.now = now or time.time()
    g.input("a")
    g.input("a", "up")
    return g


def run(g, now, seconds):
    c = None
    while seconds > 0:
        c = Canvas(84, 42)
        g.render(c, now)
        now += g.interval
        seconds -= g.interval
    return c, now


def steps(g, now, n, keys=()):
    """n physics steps with these keys held (render drives the clock)."""
    g.held = {(1, k) for k in keys}
    for _ in range(n):
        now += pack_arcade.STEP
        g.render(Canvas(84, 42), now)
    return now


class Registry(unittest.TestCase):
    def test_all_five_are_games_with_demos(self):
        for gid in IDS:
            cls = apps.REGISTRY[gid]
            self.assertEqual(cls.category, "Games")
            self.assertTrue(cls.interactive)
            g = make(gid)
            c = Canvas(84, 42)
            g.render(c, time.time())
            self.assertGreater(sum(c.px), 50, gid)  # a title screen

    def test_every_demo_runs_a_minute_and_scores(self):
        for gid in IDS:
            with self.subTest(gid):
                random.seed(gid)
                st = Store()
                g = make(gid, {}, st)
                now = time.time()
                demo_frames, scored = 0, 0
                for _ in range(int((pack_arcade.DEMO_AFTER + 60) / g.interval)):
                    c = Canvas(84, 42)
                    g.render(c, now)
                    if g.demo and g.state == "playing":
                        demo_frames += 1
                        scored = max(scored, g.score)
                    now += g.interval
                self.assertGreater(demo_frames, 600, gid)
                self.assertGreater(scored, 0, f"{gid}: the demo never scored")
                self.assertEqual(scores.table(st, gid), [], gid)  # demo scores are never filed

    def test_a_button_takes_over_and_scores_are_filed(self):
        for gid in ("digdug", "joust"):
            st = Store()
            g = make(gid, {}, st)
            now = time.time()
            _, now = run(g, now, pack_arcade.DEMO_AFTER + 2)
            self.assertTrue(g.demo, gid)
            g.input("left")
            self.assertFalse(g.demo)
            self.assertEqual(g.state, "playing")
            g.score = 1234
            g.game_over()
            g.render(Canvas(84, 42), now)
            self.assertEqual(scores.table(st, gid)[0]["score"], 1234, gid)


class DigDugTest(unittest.TestCase):
    def test_digging_clears_dirt_and_scores(self):
        g = start("digdug")
        now = g.now
        g.safe_until = now + 100  # no enemy gets in the way of this test
        g.move_enemy = lambda e: None
        before = sum(g.dirt)
        now = steps(g, now, 40, ["down"])
        self.assertLess(sum(g.dirt), before)
        self.assertGreater(g.py, 1.0)
        self.assertGreater(g.score, 0)
        self.assertEqual(g.dirt[round(g.py) * a2.DD_COLS + round(g.px)], 0)
        now = steps(g, now, 30, ["right"])
        self.assertGreater(g.px, a2.DD_COLS // 2)

    def test_pumping_pops_an_enemy_deeper_is_worth_more(self):
        g = start("digdug")
        now = g.now
        g.enemies = [{"x": float(round(g.px) + 2), "y": 6.0, "kind": "pooka", "dir": (-1, 0), "mode": "tunnel",
                      "bloat": 0, "bloat_t": 0.0, "ghost_t": 0.0, "fire": 0.0, "pop": 0.0}]
        g.rocks = []
        g.px, g.py, g.face = float(round(g.px)), 6.0, (1, 0)
        for k in range(0, 4):
            g.dirt[6 * a2.DD_COLS + round(g.px) + k] = 0
        g.safe_until = 0
        score = g.score
        g.press("a", 1)
        now = steps(g, now, 8)
        self.assertIsNotNone(g.pump)
        self.assertIs(g.pump["hooked"], g.enemies[0])
        for _ in range(3):
            g.press("a", 1)
            now = steps(g, now, 2)
        self.assertTrue(g.enemies[0]["pop"])
        self.assertEqual(g.score - score, a2.LAYER_POINTS[1])  # layer 2 (rows 4-6)
        now = steps(g, now, 30)
        self.assertTrue(g.clear_t)  # the round is clear once the last one pops
        now = steps(g, now, 100)
        self.assertEqual(g.round, 2)

    def test_a_rock_falls_when_undermined_and_crushes(self):
        g = start("digdug")
        now = g.now
        g.enemies = [{"x": 5.0, "y": 8.0, "kind": "pooka", "dir": (1, 0), "mode": "tunnel", "bloat": 0,
                      "bloat_t": 0.0, "ghost_t": 0.0, "fire": 0.0, "pop": 0.0}]
        g.rocks = [{"x": 5, "y": 4, "state": "set", "t": 0.0}]
        g.px, g.py = 20.0, 0.0
        for y in range(5, 9):
            g.dirt[y * a2.DD_COLS + 5] = 0  # the shaft under the rock, down to the pooka
        g.move_enemy = lambda e: None  # the pooka sits still under the rock
        now = steps(g, now, 30)
        self.assertIn(g.rocks[0]["state"], ("wobble", "falling"))
        now = steps(g, now, 30)  # it lands on the pooka (and that clears the round)
        self.assertTrue(g.enemies == [] or g.enemies[0]["pop"])
        self.assertGreaterEqual(g.score, 1000)
        self.assertTrue(g.clear_t)

    def test_touching_a_pooka_costs_a_life(self):
        g = start("digdug", {"lives": 2})
        now = g.now
        g.safe_until = 0
        g.enemies = [{"x": g.px, "y": g.py, "kind": "pooka", "dir": (1, 0), "mode": "tunnel", "bloat": 0,
                      "bloat_t": 0.0, "ghost_t": 0.0, "fire": 0.0, "pop": 0.0}]
        now = steps(g, now, 2)
        self.assertTrue(g.dead_t)
        now = steps(g, now, 60)
        self.assertEqual(g.lives, 1)


class DefenderTest(unittest.TestCase):
    def test_lander_abducts_a_humanoid_and_shooting_it_drops_him(self):
        g = start("defender")
        now = g.now
        g.enemies = [{"kind": "lander", "wx": 100.0, "y": 16.0, "vx": 0.0, "vy": 0.0, "carry": None, "t": 0.0}]
        g.men = [{"wx": 100.0, "state": "ground", "y": 0.0, "vy": 0.0, "by": None}]
        g.wx, g.y = 300.0, 20.0  # far away: nothing to shoot yet
        now = steps(g, now, 200)
        e = g.enemies[0]
        self.assertIs(e["carry"], g.men[0])
        self.assertEqual(g.men[0]["state"], "carried")
        # the ship arrives level with it and fires
        g.wx, g.y, g.facing = e["wx"] - 20, e["y"], 1
        g.cam = g.wx - 24
        g.press("a", 1)
        now = steps(g, now, 6)
        self.assertEqual(g.wave, 2)  # it was the last one: the next wave came in
        self.assertEqual(g.men[0]["state"], "falling")
        self.assertEqual(g.score, 150)

    def test_scanner_row_shows_every_enemy_across_the_world(self):
        g = start("defender")
        g.enemies = [{"kind": "lander", "wx": wx, "y": 16.0, "vx": 0.0, "vy": 0.0, "carry": None, "t": 0.0}
                     for wx in (0.0, 84.0, 200.0, 335.0)]
        g.wx = 150.0
        c = Canvas(84, 42)
        g.render(c, g.now + 0.001)
        row = c.px[:84]
        for wx in (0, 84, 200, 335):
            x = int(wx * 84 / a2.DF_WORLD)
            self.assertTrue(any(row[(x + k) % 84] for k in (-1, 0, 1)), wx)
        self.assertLessEqual(sum(row), 8)  # just them and the ship marker

    def test_smart_bomb_clears_the_screen_only(self):
        g = start("defender")
        g.enemies = [{"kind": "lander", "wx": g.wx + 20, "y": 16.0, "vx": 0.0, "vy": 0.0, "carry": None, "t": 0.0},
                     {"kind": "lander", "wx": (g.wx + 200) % a2.DF_WORLD, "y": 16.0, "vx": 0.0, "vy": 0.0, "carry": None, "t": 0.0}]
        g.press("b", 1)
        self.assertEqual(len(g.enemies), 1)
        self.assertEqual(g.bombs, 2)

    def test_lost_planet_turns_landers_into_mutants(self):
        g = start("defender")
        g.enemies = [{"kind": "lander", "wx": 50.0, "y": 16.0, "vx": 0.0, "vy": 0.0, "carry": None, "t": 0.0}]
        for m in g.men:
            m["state"] = "dead"
        steps(g, g.now, 1)
        self.assertEqual(g.enemies[0]["kind"], "mutant")


class JoustTest(unittest.TestCase):
    def rider(self, x, y, kind="bounder"):
        return {"x": x, "y": y, "vx": 0.0, "vy": 0.0, "kind": kind, "facing": 1, "mount_t": 0.0, "flap_t": 0.0}

    def test_flapping_lifts_and_gravity_lands_on_a_platform(self):
        g = start("joust")
        now = g.now
        y0 = g.y
        g.press("a", 1)
        now = steps(g, now, 6)
        self.assertLess(g.y, y0)
        now = steps(g, now, 80)
        self.assertEqual(g.y, a2.J_GROUND - 4)  # back on the ground
        self.assertEqual(g.vy, 0.0)
        g.x = 40.0  # under the middle platform (y 23)
        for _ in range(12):
            g.press("a", 1)
            now = steps(g, now, 4)
        self.assertLess(g.y, 28)

    def test_the_higher_lance_wins(self):
        g = start("joust")
        now = g.now
        g.safe_until = 0
        g.spawn_queue = []
        g.spawn_queue, g.spawn_at = ["bounder"], now + 100  # keep the wave going
        g.riders = [self.rider(g.x + 1, g.y + 3)]  # below me: I win, it leaves an egg (which I may catch at once)
        now = steps(g, now, 1)
        self.assertEqual(g.riders, [])
        self.assertGreaterEqual(g.score, a2.J_KINDS["bounder"][0])
        g.eggs = []
        g.riders = [self.rider(g.x + 1, g.y - 3)]  # above me: I lose a life
        now = steps(g, now, 1)
        self.assertTrue(g.dead_t)
        now = steps(g, now, 70)
        self.assertEqual(g.lives, 2)
        g.safe_until = 0
        g.riders = [self.rider(g.x + 2, g.y)]  # level: bounce apart
        vx = g.vx
        now = steps(g, now, 1)
        self.assertFalse(g.dead_t)
        self.assertNotEqual(g.vx, vx)

    def test_eggs_collect_or_hatch_and_the_sides_wrap(self):
        g = start("joust")
        now = g.now
        g.spawn_queue, g.spawn_at, g.riders = ["bounder"], now + 100, []
        g.eggs = [{"x": g.x + 1, "y": g.y, "vy": 0.0, "t": now, "hatch": None}]
        now = steps(g, now, 1)
        self.assertEqual(g.eggs, [])
        self.assertEqual(g.score, 250)
        g.eggs = [{"x": 10.0, "y": 36.0, "vy": 0.0, "t": now - 7, "hatch": None}]
        now = steps(g, now, 100)
        self.assertEqual(g.eggs, [])
        self.assertEqual(len(g.riders), 1)
        g.x, g.vx = 83.0, 30.0
        now = steps(g, now, 4, ["right"])
        self.assertLess(g.x, 10)


class MarbleTest(unittest.TestCase):
    def test_rolls_off_the_edge_and_comes_back_with_a_penalty(self):
        g = start("marble")
        now = g.now
        t = g.time_left
        g.vy = -6.0  # straight up off the top of the course
        now = steps(g, now, 12)
        self.assertTrue(g.fall_t)
        now = steps(g, now, 50)
        self.assertFalse(g.fall_t)
        self.assertEqual((int(g.x), int(g.y)), g.start)
        self.assertLess(g.time_left, t - 3)

    def test_slopes_speed_it_up_and_blocks_bounce(self):
        g = start("marble")
        g.rows = ["S.v.#", "     "]
        g.gh, g.gw = 2, 5
        g.x, g.y, g.vx, g.vy = 2.5, 0.5, 0.0, 0.0
        me = {"x": g.x, "y": g.y, "vx": 0.0, "vy": 0.0}
        g.roll(me)
        self.assertGreater(me["vy"], 0)  # 'v' pushes it down
        me = {"x": 3.95, "y": 0.5, "vx": 3.0, "vy": 0.0}
        g.roll(me)
        self.assertLess(me["vx"], 0)  # bounced off the '#'

    def test_reaching_the_goal_scores_time_and_moves_on(self):
        g = start("marble")
        now = g.now
        gx, gy = g.goal
        g.x, g.y, g.vx, g.vy = gx + 0.5, gy + 0.5, 0.0, 0.0
        now = steps(g, now, 1)
        self.assertTrue(g.done_t)
        self.assertGreaterEqual(g.score, 500 + int(g.time_left) * 10)
        now = steps(g, now, 100)
        self.assertEqual(g.course, 1)
        self.assertEqual((int(g.x), int(g.y)), g.start)

    def test_time_out_ends_the_game(self):
        g = start("marble")
        g.time_left = 0.05
        steps(g, g.now, 5)
        self.assertEqual(g.state, "over")

    def test_demo_finds_its_way_with_steelies_about(self):
        g = start("marble")
        g.course = 2
        g.start_course()
        self.assertEqual(len(g.steelies), 1)
        path = g.path_to_goal()
        self.assertEqual(path[-1], g.goal)
        self.assertGreater(len(path), 10)


class GhostsTest(unittest.TestCase):
    def playing(self, params=None):
        g = start("ghosts", params)
        now = g.now
        now = steps(g, now, 90)  # past READY
        self.assertEqual(g.phase, "play")
        return g, now

    def test_players_drive_the_ghosts_and_catch_the_computer(self):
        g, now = self.playing()
        self.assertFalse(g.pac_human())
        self.assertEqual(g.ghost_player(g.ghosts[0]), 1)
        self.assertIsNone(g.ghost_player(g.ghosts[1]))  # nobody on player 2 yet
        g.input("left", "down", player=2)
        self.assertEqual(g.ghost_player(g.ghosts[1]), 2)
        # put ghost 1 on Pac-Man: he's caught, and the catch is player 1's
        g0 = g.ghosts[0]
        g0.mode, g0.fright = "active", False
        g0.x, g0.y = g.pac.x, g.pac.y
        now = steps(g, now, 1)
        self.assertEqual(g.phase, "dying")
        self.assertEqual(g.caught_by, 1)
        self.assertEqual(g.catches, {1: 1})
        now = steps(g, now, 80)
        self.assertEqual(g.lives, 2)

    def test_a_held_direction_turns_a_player_ghost(self):
        g, now = self.playing()
        g0 = g.ghosts[0]
        g0.mode, g0.x, g0.y, g0.dir = "active", 0.9, 1.0, (1, 0)  # nearly at the top-left corner, heading right
        g.held = {(1, "down")}
        g.last_dir[1] = "down"
        now = steps(g, now, 6, ["down"])
        self.assertEqual(g0.dir, (0, 1))
        self.assertGreater(g0.y, 1.0)

    def test_ghosts_win_after_three_catches_and_pac_wins_by_clearing(self):
        g, now = self.playing()
        g.lives = 1
        g0 = g.ghosts[0]
        g0.mode, g0.fright = "active", False
        g0.x, g0.y = g.pac.x, g.pac.y
        now = steps(g, now, 80)
        self.assertEqual(g.state, "over")
        self.assertEqual(g.result()[0], "GHOSTS WIN")
        g, now = self.playing({"pac": "p1"})
        self.assertTrue(g.pac_human())
        self.assertEqual(g.ghost_player(g.ghosts[0]), None)
        g.input("up", "down", player=2)
        self.assertEqual(g.ghost_player(g.ghosts[0]), 2)
        g.dots, g.power = set(), set()
        now = steps(g, now, 2)
        self.assertEqual(g.state, "over")
        self.assertEqual(g.result()[0], "PAC-MAN WINS")
        self.assertIsNone(g.score_entry())  # versus: nothing goes in the high score table


if __name__ == "__main__":
    unittest.main()
