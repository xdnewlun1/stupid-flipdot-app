"""More clocks (apps/pack_clocks2.py): the word clock's wording and grid, the binary clock's digits, the marble
clock's mechanism (counts after N minutes of drops equal the time) and its animation, and the sun path clock's
positions and setup. Run: python3 -m pytest -q tests/test_clocks2.py"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
from apps import pack_clocks2 as C  # noqa: E402
from apps import react  # noqa: E402
from canvas import Canvas  # noqa: E402

react.SAMPLE_HZ = 0


class Store:
    def __init__(self, quiet=False, location=True):
        self.settings = {"visualizer": {"source": "off"}}
        if location:
            self.settings["weather"] = {"lat": 51.5, "lon": -0.1, "name": "London"}
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}


class Ctx:
    def __init__(self, store):
        self.width, self.height, self.store = 84, 42, store


def local(h, m, s=0, day=11, month=10):
    return dt.datetime(2026, month, day, h, m, s).timestamp()


def frame(app, now):
    c = Canvas(84, 42)
    app.render(c, now)
    return c


def lit(c):
    return sum(c.px)


class WordClockTest(unittest.TestCase):
    def test_wording(self):
        cases = {(7, 0): "IT IS SEVEN", (7, 5): "IT IS FIVE PAST SEVEN", (7, 10): "IT IS TEN PAST SEVEN",
                 (7, 15): "IT IS QUARTER PAST SEVEN", (7, 20): "IT IS TWENTY PAST SEVEN",
                 (7, 25): "IT IS TWENTY FIVE PAST SEVEN", (7, 30): "IT IS HALF PAST SEVEN",
                 (7, 35): "IT IS TWENTY FIVE TO EIGHT", (7, 40): "IT IS TWENTY TO EIGHT",
                 (7, 45): "IT IS QUARTER TO EIGHT", (7, 50): "IT IS TEN TO EIGHT", (7, 55): "IT IS FIVE TO EIGHT",
                 (11, 58): "IT IS TWELVE", (23, 57): "IT IS FIVE TO TWELVE", (23, 58): "IT IS TWELVE", (0, 2): "IT IS TWELVE", (0, 3): "IT IS FIVE PAST TWELVE", (12, 32): "IT IS HALF PAST TWELVE", (12, 33): "IT IS TWENTY FIVE TO ONE",
                 (13, 2): "IT IS ONE", (7, 3): "IT IS FIVE PAST SEVEN", (7, 2): "IT IS SEVEN"}
        for (h, m), want in cases.items():
            self.assertEqual(C.sentence(h, m), want, (h, m))
        self.assertEqual(C.sentence(7, 3, nearest=False), "IT IS SEVEN")  # floored, with minute dots
        self.assertEqual(C.sentence(7, 59, nearest=False), "IT IS FIVE TO EIGHT")
        self.assertEqual(C.sentence(7, 0, "oclock"), "SEVEN OCLOCK")
        self.assertEqual(C.sentence(7, 25, "oclock"), "TWENTY FIVE PAST SEVEN")
        self.assertEqual(C.sentence(12, 0, "oclock"), "TWELVE OCLOCK")

    def test_grids_are_sound(self):
        for name, lay in C.LAYOUTS.items():
            self.assertEqual(len(lay["rows"]), 7, name)
            self.assertTrue(all(len(r) == 14 for r in lay["rows"]), name)
            for word, (row, col, n) in lay["words"].items():
                self.assertEqual(lay["rows"][row][col:col + n], word.lstrip("M"), (name, word))
            # every word the clock can say is in the grid, and in reading order
            for h in range(24):
                for m in range(0, 60, 5):
                    words = C.words_for(h, m, name)
                    spots = [lay["words"][w][:2] for w in words]
                    self.assertEqual(spots, sorted(spots), (name, h, m))
        for g in C.GLYPHS.values():
            self.assertEqual([len(r) for r in g], [5] * 5)

    def test_renders_the_lit_words_only(self):
        a = C.WordClock(Ctx(Store()), dots=True)
        c = frame(a, local(7, 0))
        # SEVEN sits in row 4 at columns 8..12: those cells are lit, the cell before (FIVE's E) is dark
        self.assertTrue(any(c.get(8 * 6 + x, 4 * 6 + y) for x in range(5) for y in range(5)))
        self.assertFalse(any(c.get(7 * 6 + x, 4 * 6 + y) for x in range(5) for y in range(5)))
        self.assertEqual(sum(c.px[41 * 84:]), 0)  # no minute dots at :00
        c3 = frame(a, local(7, 3))
        self.assertEqual(sum(c3.px[41 * 84:]), 6)  # three dots (two wide) for :03
        self.assertGreater(lit(frame(C.WordClock(Ctx(Store()), faint=True), local(7, 0))), lit(c))
        self.assertTrue(a.describe().startswith("It is"))


class BinaryClockTest(unittest.TestCase):
    def test_bcd_columns(self):
        a = C.BinaryClock(Ctx(Store()), style="bcd", labels=False, seconds=True, hour24=True)
        c = frame(a, local(17, 38, 29))
        # 6 columns at a 12-dot pitch from x = 12; bits 8 4 2 1 at y = 8, 17, 26, 35; filled = 1, hollow = 0
        digits = [1, 7, 3, 8, 2, 9]
        for i, d in enumerate(digits):
            x = 12 + i * 12
            for bit in range(4):
                y = 8 + bit * 9
                self.assertEqual(c.get(x, y), d >> (3 - bit) & 1, (i, bit))  # the centre dot
                self.assertEqual(c.get(x, y - 3), 1)  # the ring is always there
        a = C.BinaryClock(Ctx(Store()), style="bcd", hour24=False, seconds=False)
        self.assertEqual(a.digits(dt.datetime(2026, 10, 11, 17, 38)), [5, 38])

    def test_binary_rows(self):
        a = C.BinaryClock(Ctx(Store()), style="rows", labels=True, seconds=True)
        c = frame(a, local(17, 38, 29))
        self.assertGreater(lit(c), 100)
        # minutes row (the middle one) = 38 = 100110 over six 11-dot pitches
        y = (42 - 2 * 13) // 2 + 13
        x0 = (84 - 11 * 5) // 2 + 4
        self.assertEqual([c.get(x0 + b * 11, y) for b in range(6)], [1, 0, 0, 1, 1, 0])


class MarbleClockTest(unittest.TestCase):
    def test_counts_follow_the_time_through_the_mechanism(self):
        t = dt.datetime(2026, 10, 11, 7, 23)
        counts = C.counts_for(t)
        self.assertEqual(counts, {"hours": 6, "fives": 4, "ones": 3})  # 6 balls + the fixed one: 7 o'clock
        for k in range(1, 24 * 60 + 1):
            counts, tips = C.advance(counts)
            self.assertEqual(counts, C.counts_for(t + dt.timedelta(minutes=k)), k)
            if (t + dt.timedelta(minutes=k)).minute == 0:
                self.assertIn("fives", tips)
        self.assertEqual(C.advance({"hours": 11, "fives": 11, "ones": 4})[1], ["ones", "fives", "hours"])
        self.assertEqual(C.counts_for(dt.datetime(2026, 1, 1, 12, 0)), {"hours": 11, "fives": 0, "ones": 0})
        self.assertEqual(C.counts_for(dt.datetime(2026, 1, 1, 1, 0)), {"hours": 0, "fives": 0, "ones": 0})

    def test_a_minute_is_animated_and_ends_on_the_new_counts(self):
        a = C.MarbleClock(Ctx(Store()))
        frame(a, local(7, 59, 58))
        self.assertEqual(a.shown, {"hours": 6, "fives": 11, "ones": 4})
        self.assertGreater(a.interval, 1.0)  # resting until the minute
        t0 = local(8, 0, 0)
        pics = set()
        for k in range(140):  # 7 s: the lift, then three rails tipping one after another
            c = frame(a, t0 + k * 0.05)
            pics.add(bytes(c.px))
            if k == 2:
                self.assertEqual(a.interval, 0.05)  # animating
                self.assertEqual(a.shown["ones"], 4)  # the ball is still on its way up
        self.assertGreater(len(pics), 20)
        self.assertEqual(a.shown, {"hours": 7, "fives": 0, "ones": 0})
        self.assertFalse(a.moves or a.changes)
        plain = frame(a, t0 + 30)
        self.assertEqual(sum(1 for x in range(30, 82) if plain.get(x, C.HOURS_Y)), 8 * 3)  # 7 balls + the fixed one

    def test_quiet_hours_skip_the_show_and_a_gap_catches_up(self):
        a = C.MarbleClock(Ctx(Store(quiet=True)))
        react._quiet[:] = [0.0, False]
        try:
            frame(a, local(7, 59, 58))
            frame(a, local(8, 0, 1))
            self.assertFalse(a.moves)
            self.assertEqual(a.shown, {"hours": 7, "fives": 0, "ones": 0})
        finally:
            react._quiet[:] = [0.0, False]
        a = C.MarbleClock(Ctx(Store()))
        frame(a, local(7, 23, 0))
        frame(a, local(9, 41, 0))  # the board was showing something else for two hours
        self.assertFalse(a.moves)
        self.assertEqual(a.shown, C.counts_for(dt.datetime(2026, 10, 11, 9, 41)))

    def test_plan_moves_every_ball_home_on_a_tip(self):
        moves, changes, new, end = C.plan_minute({"hours": 2, "fives": 11, "ones": 4}, 100.0)
        self.assertEqual(new, {"hours": 3, "fives": 0, "ones": 0})
        self.assertEqual(len(moves), 1 + 5 + 12)  # the lift, the ones rail's five, the fives rail's twelve
        self.assertGreater(end, 102.2)
        rails = [r for _, r, _ in changes]
        self.assertEqual(rails, ["ones", "fives", "hours"])
        self.assertEqual(changes[-1][2], 3)
        for m in moves:
            self.assertEqual(m.at(m.t0 - 1), m.points[0][:2])
            self.assertEqual(m.at(m.t1 + 1), m.points[-1][:2])


class SunClockTest(unittest.TestCase):
    def test_needs_a_location(self):
        self.assertIsNotNone(C.SunClock.needs_setup(Store(location=False), {}))
        self.assertIsNone(C.SunClock.needs_setup(Store(), {}))
        self.assertIsNotNone(apps.base.setup_needed(Store(location=False), {"app": "sunclock", "params": {}}))

    def test_sunrise_and_sunset_and_the_sun_on_its_arc(self):
        a = C.SunClock(Ctx(Store()))
        t = dt.datetime(2026, 6, 21, 12, 0)
        rise, set_, mrise, mset = a.day_times(t.replace(tzinfo=dt.timezone.utc).timestamp())
        self.assertIsNotNone(rise)
        self.assertIsNotNone(set_)
        length = (set_ - rise) / 3600
        self.assertTrue(16.3 < length < 16.9, length)  # midsummer in London: about 16 h 38 min
        noon_utc = dt.datetime(2026, 6, 21, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertLess(abs((rise + set_) / 2 - noon_utc), 15 * 60)  # solar noon close to 12:00 UTC
        # at midday the sun sits on top of the arc in the middle; before dawn there's no sun above the horizon
        c = frame(a, (rise + set_) / 2)
        top = min(y for y in range(27) for x in range(84) if c.get(x, y))
        self.assertLessEqual(top, 4)
        xs = [x for x in range(84) if c.get(x, 7)]
        self.assertTrue(xs and 35 <= sum(xs) / len(xs) <= 49, xs)
        night = frame(a, rise - 3 * 3600)
        self.assertLess(sum(night.px[:26 * 84]), sum(c.px[:26 * 84]))

    def test_a_full_moon_night_shows_the_moon(self):
        a = C.SunClock(Ctx(Store()))
        t = dt.datetime(2026, 10, 26, 23, 0, tzinfo=dt.timezone.utc).timestamp()  # full moon, high at night in London
        c = frame(a, t)
        above = sum(c.px[:26 * 84])
        self.assertGreater(above, 60)  # the arc dots plus a moon of radius 3
        self.assertIn("--:--", C.SunClock(Ctx(Store())).fmt(None))

    def test_polar_day_has_no_crossings(self):
        a = C.SunClock(Ctx(Store()))
        a.ctx.store.settings["weather"] = {"lat": 78.2, "lon": 15.6}  # Svalbard in June
        rise, set_, _, _ = a.day_times(dt.datetime(2026, 6, 21, 12, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertIsNone(rise)
        self.assertIsNone(set_)
        frame(a, dt.datetime(2026, 6, 21, 12, 0).timestamp())  # still renders


if __name__ == "__main__":
    unittest.main()
