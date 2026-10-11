"""Mushroom garden (apps/mushrooms.py): every species draws its own shape, the life cycle runs grow -> hold ->
wither -> gone and the garden refills, the species choice holds, puffballs puff and gills drop spores, overlaps
stay readable, music speeds it up and bursts puffballs, and a frame is cheap.
Run: python3 -m pytest -q tests/test_mushrooms.py"""

import hashlib
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
import audio_signal  # noqa: E402
from apps import mushrooms as M  # noqa: E402
from apps import effects, react  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

react.SAMPLE_HZ = 0  # the tests drive the clock


class Store:
    def __init__(self, quiet=False):
        self.settings = {"visualizer": {"source": "off"}}
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}


def make(params=None, store=None, seed=1):
    random.seed(seed)
    a = apps.create(Context(84, 42, store or Store()), {"app": "mushrooms", "params": params or {}})
    a.started = 0.0
    return a


def frame(a, now):
    c = Canvas(84, 42)
    a.render(c, now)
    return c


def lit(c):
    return sum(c.px)


def _pump(now):
    k, hat = now % 0.5 < 0.08, 0.25 <= now % 0.5 < 0.29
    return {"live": True, "loudness": 0.6,
            "spectrum": [0.9 if k else 0.4] * 4 + [0.4] * 8 + [0.8 if hat else 0.3] * 4}


def one(species, size=24, seed=5, **kw):
    """A single grown mushroom of that species, drawn on its own."""
    a = make({"crowd": 1, "species": species, "ground": "none"})
    sh = M.Shroom(species, 42, a.ground_y, size, 0.0, 30.0, seed)
    sh.grow_s, sh.hold_s, sh.fade_s = 10.0, 20.0, 10.0
    for k, v in kw.items():
        setattr(sh, k, v)
    return a, sh


def draw(a, sh, t):
    c = Canvas(84, 42)
    sh.draw(c, t, a.music, 0.0)
    return c


class SpeciesTest(unittest.TestCase):
    def test_every_species_draws_a_shape_of_its_own(self):
        pics = {}
        for sp in M.SPECIES:
            a, sh = one(sp)
            c = draw(a, sh, 13.0)  # fully grown, 3 s into its prime
            self.assertGreater(lit(c), 20, sp)
            self.assertLess(lit(c), 84 * 42 // 3, sp)
            top = min(y for y in range(42) for x in range(84) if c.px[y * 84 + x])
            self.assertLess(top, a.ground_y - 6, sp)  # it stands up from the ground
            pics[sp] = hashlib.sha1(bytes(c.px)).hexdigest()
        self.assertEqual(len(set(pics.values())), len(M.SPECIES))  # no two alike
        self.assertEqual(len(M.SPECIES), 12)
        self.assertNotIn("button", M.ODD)

    def test_it_grows_then_withers(self):
        a, sh = one("toadstool")
        sizes = [lit(draw(a, sh, t)) for t in (1.0, 4.0, 7.0, 10.5)]
        self.assertTrue(all(b > x for x, b in zip(sizes, sizes[1:])), sizes)  # bigger and bigger
        full = lit(draw(a, sh, 20.0))
        self.assertEqual(full, sizes[-1])  # holds still when grown (the details don't jitter)
        self.assertEqual(sh.phase(20.0)[0], "hold")
        self.assertEqual(sh.phase(32.0)[0], "fade")
        self.assertLess(lit(draw(a, sh, 35.0)), full * 0.7)  # dissolving
        self.assertEqual(sh.phase(41.0)[0], "gone")
        self.assertFalse(sh.draw(Canvas(84, 42), 41.0, a.music, 0.0))

    def test_holes_only_land_inside_the_silhouette(self):
        b = M.Brush(10, 10)
        b.hole(3, 3)
        self.assertEqual(lit(b.mask), 0)
        b.ellipse(5, 5, 3, 3)
        b.hole(5, 5)
        self.assertEqual(b.mask.get(5, 5), 1)
        self.assertEqual(b.ink.get(5, 5), 0)

    def test_overlapping_mushrooms_are_separated_by_a_dark_outline(self):
        c = Canvas(84, 42, fill=1)  # everything behind is lit
        b = M.Brush(84, 42)
        b.ellipse(42, 20, 6, 6)
        b.stamp(c)
        self.assertEqual(c.get(42, 20), 1)
        self.assertEqual(c.get(42, 13), 0)  # one dark dot all round the silhouette...
        self.assertEqual(c.get(35, 20), 0)
        self.assertEqual(c.get(42, 11), 1)  # ...and no more than that
        self.assertEqual(c.get(0, 0), 1)

    def test_the_ink_cap_melts_and_the_puffball_puffs(self):
        a, sh = one("inkcap")
        fresh, old = lit(draw(a, sh, 11.0)), lit(draw(a, sh, 28.0))
        self.assertLess(old, fresh * 0.8)  # the cap has deliquesced
        a, sh = one("puffball")
        draw(a, sh, 10.5)
        self.assertFalse(sh.puffing)
        draw(a, sh, 10.0 + sh.next_puff + 0.2)
        self.assertTrue(sh.puffing)
        self.assertEqual(sh.want[0], "puff")
        a.emit(sh, 15.0, a.music)
        self.assertGreater(len([p for p in a.parts if p.kind == "puff"]), 10)
        self.assertEqual(sh.puffs, 1)
        self.assertFalse(sh.puffing)


class GardenTest(unittest.TestCase):
    def test_the_garden_keeps_itself_full_and_turns_over(self):
        a = make({"crowd": 5, "pace": 10})
        self.assertEqual(len(a.shrooms), 5)
        self.assertTrue(all(s.born <= 0 for s in a.shrooms))  # a garden already in progress
        seen = set()
        now = 0.0
        while now < 120:
            frame(a, now)
            self.assertLessEqual(len(a.shrooms), 5)
            seen |= {s.species for s in a.shrooms}
            now += 0.1
        self.assertEqual(len(a.shrooms), 5)  # refilled after the first ones went
        self.assertTrue(all(s.born > 0 for s in a.shrooms))  # all of the first lot have gone
        self.assertGreaterEqual(len(seen), 6)  # a mix of kinds came up

    def test_species_choice(self):
        a = make({"crowd": 6, "pace": 5, "species": "odd"})
        kinds = set()
        for i in range(600):
            frame(a, i * 0.1)
            kinds |= {s.species for s in a.shrooms}
        self.assertNotIn("button", kinds)
        self.assertGreater(len(kinds), 4)
        a = make({"crowd": 4, "pace": 5, "species": "morel"})
        for i in range(300):
            frame(a, i * 0.1)
        self.assertEqual({s.species for s in a.shrooms}, {"morel"})

    def test_spores_drift_from_gilled_mushrooms_only_when_on(self):
        for on, want in ((True, True), (False, False)):
            a = make({"crowd": 4, "pace": 6, "species": "toadstool", "spores": on})
            spores = 0
            for i in range(400):
                frame(a, i * 0.1)
                spores += sum(1 for p in a.parts if p.kind == "spore")
            self.assertEqual(spores > 0, want, on)
        a = make({"crowd": 3, "pace": 6, "species": "puffball", "spores": True})
        for i in range(400):
            frame(a, i * 0.1)
        self.assertFalse([p for p in a.parts if p.kind == "spore"])  # puffballs puff instead

    def test_ground_and_mycelium(self):
        a = make({"crowd": 4, "pace": 8, "ground": "mycelium"})
        for i in range(300):
            c = frame(a, i * 0.1)
        self.assertEqual(sum(c.px[(a.ground_y + 1) * 84:(a.ground_y + 2) * 84]), 84)  # the ground line
        self.assertGreater(len(a.soil), 30)  # threads have spread under it
        self.assertTrue(all(y > a.ground_y + 1 for _, y in a.soil))
        a = make({"crowd": 4, "ground": "none"})
        c = frame(a, 0.0)
        self.assertEqual(a.ground_y, 41)
        self.assertLess(sum(c.px[41 * 84:]), 84)

    def test_shelves_face_the_middle(self):
        a = make({"crowd": 8, "pace": 5, "species": "bracket"})
        for i in range(200):
            frame(a, i * 0.1)
        for s in a.shrooms:
            self.assertEqual(s.side, -1 if s.x > 42 else 1)

    def test_a_frame_is_cheap(self):
        a = make({"crowd": 12, "pace": 8, "sizes": "big"})
        t0 = time.perf_counter()
        for i in range(200):
            frame(a, i * 0.1)
        per = (time.perf_counter() - t0) / 200
        self.assertLess(per, 0.02, per)

    def test_quiet_hours_slow_it_down(self):
        a = make({"crowd": 4}, store=Store(quiet=True))
        react._quiet[:] = [0.0, False]
        try:
            frame(a, 1.0)
            self.assertEqual(a.interval, 0.25)
        finally:
            react._quiet[:] = [0.0, False]

    def test_in_the_random_effects_pool_and_the_option_list(self):
        self.assertIn("mushrooms", effects.POOL)
        p = effects._rand_params("mushrooms", random.Random(1))
        self.assertIn(p["species"], ("mix", "odd"))
        names = [p["name"] for p in apps.REGISTRY["mushrooms"].params]
        self.assertTrue(set(react.REACT_NAMES) <= set(names))


class MusicTest(unittest.TestCase):
    def run_with(self, params, signal, seconds=12.0):
        clock = [0.0]
        audio_signal.inject(signal, clock=lambda: clock[0])
        react._quiet[:] = [0.0, False]
        try:
            a = make(params, seed=3)
            puffs, now, frames = 0, 0.0, []
            while now < seconds:
                clock[0] = now
                frames.append(bytes(frame(a, now).px))
                puffs += sum(1 for p in a.parts if p.kind == "puff" and p.t0 == now)
                now = round(now + 0.1, 3)
            return a, frames, puffs
        finally:
            audio_signal.inject(None)

    def test_music_speeds_the_garden_and_bursts_the_puffballs(self):
        p = {"crowd": 4, "pace": 20, "species": "puffball", "react": True, "react_mode": "motion"}
        still, plain, _ = self.run_with(p, lambda now: None)
        self.assertFalse(still.music.on)
        loud, moved, puffs = self.run_with(p, _pump)
        self.assertTrue(loud.music.on)
        self.assertNotEqual(plain, moved)
        self.assertGreater(puffs, 0)

    def test_off_is_the_same_garden_with_or_without_a_beat(self):
        p = {"crowd": 4, "pace": 20, "react": False}
        _, a, _ = self.run_with(p, lambda now: None)
        _, b, _ = self.run_with(p, _pump)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
