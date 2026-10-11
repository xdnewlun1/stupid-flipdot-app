"""Playlists and the schedule: shuffle without repeats, item conditions, sunrise/sunset, date ranges, which rule
wins, the old schedule format, and the runner following a sunset rule at a simulated time.
Run: python3 -m pytest -q tests/test_programme.py"""

import datetime as dt
import os
import random
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps  # noqa: E402
import audio_signal  # noqa: E402
import flipdot  # noqa: E402
import playback  # noqa: E402
import refs  # noqa: E402
import runner as runner_mod  # noqa: E402
import sun  # noqa: E402
import timetable as T  # noqa: E402
from canvas import Canvas  # noqa: E402
from store import Store  # noqa: E402
from test_board import config  # noqa: E402


def utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


class SunTest(unittest.TestCase):
    # published sunrise/sunset (USNO / timeanddate), in UTC
    KNOWN = [
        ("New York", 40.7128, -74.0060, dt.date(2024, 12, 21), utc(2024, 12, 21, 12, 16), utc(2024, 12, 21, 21, 32)),
        ("London", 51.5074, -0.1278, dt.date(2024, 6, 21), utc(2024, 6, 21, 3, 43), utc(2024, 6, 21, 20, 21)),
        ("Sydney", -33.8688, 151.2093, dt.date(2024, 12, 21), utc(2024, 12, 20, 18, 41), utc(2024, 12, 21, 9, 5)),
        ("Tokyo", 35.6762, 139.6503, dt.date(2024, 3, 20), utc(2024, 3, 19, 20, 45), utc(2024, 3, 20, 8, 53)),
        ("Reykjavik", 64.1466, -21.9426, dt.date(2024, 6, 21), utc(2024, 6, 21, 2, 55), utc(2024, 6, 22, 0, 3)),
    ]

    def test_known_cities_within_two_minutes(self):
        for name, lat, lon, d, rise, set_ in self.KNOWN:
            r, s = sun.sun_times(d, lat, lon)
            self.assertLessEqual(abs(r - rise), 120, f"{name} sunrise off by {(r - rise) / 60:.1f} min")
            self.assertLessEqual(abs(s - set_), 120, f"{name} sunset off by {(s - set_) / 60:.1f} min")

    def test_polar_day_has_no_sunset(self):
        self.assertEqual(sun.sun_times(dt.date(2024, 6, 21), 69.65, 18.96), (None, None))  # Tromsø
        clock = T.Clock({"lat": 69.65, "lon": 18.96})
        self.assertEqual(clock.sun(dt.date(2024, 6, 21)), T.NO_SUN)  # stand-ins, so rules still have a time

    def test_offsets(self):
        clock = T.Clock({"lat": 51.5074, "lon": -0.1278})
        d = dt.date(2024, 6, 21)
        base = clock.minutes("sunset", d)
        self.assertEqual(clock.minutes("sunset+30", d), base + 30)
        self.assertEqual(clock.minutes("sunrise-15", d), clock.minutes("sunrise", d) - 15)


class OrderTest(unittest.TestCase):
    def test_shuffle_plays_each_once_per_pass_and_never_twice_in_a_row(self):
        for seed in range(30):
            o = playback.Order(5, shuffle=True, rng=random.Random(seed))
            seq = [o.next() for _ in range(50)]
            for k in range(0, 50, 5):
                self.assertEqual(sorted(seq[k:k + 5]), list(range(5)))
            self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])), seq)

    def test_two_items_alternate(self):
        o = playback.Order(2, shuffle=True, rng=random.Random(1))
        seq = [o.next() for _ in range(20)]
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))

    def test_in_order_and_back(self):
        o = playback.Order(4)
        self.assertEqual([o.next() for _ in range(6)], [0, 1, 2, 3, 0, 1])
        o.back()
        self.assertEqual([o.next() for _ in range(3)], [0, 1, 2])
        o = playback.Order(4)
        o.next()
        o.back()
        self.assertEqual(o.next(), 3)  # before the first is the last

    def test_skipped_items_dont_count_as_played(self):
        o = playback.Order(3, shuffle=True, rng=random.Random(4))
        first = o.next()
        nxt = o.next()
        o.skipped()  # couldn't play: the next pass may start with it, but never repeats `first`
        self.assertEqual(o.history[-1], first)
        self.assertNotEqual(nxt, None)


class FakeStore:
    def __init__(self, **settings):
        self.settings = {"quiet_hours": {"enabled": False}, **settings}


class ConditionTest(unittest.TestCase):
    def tearDown(self):
        audio_signal.inject(None)

    def test_music(self):
        st = FakeStore()
        audio_signal.inject(lambda now: {"live": True, "loudness": 0.5, "spectrum": [0.3] * 16})
        self.assertTrue(playback.condition_ok({"type": "music"}, {}, st, time.time()))
        self.assertFalse(playback.condition_ok({"type": "no_music"}, {}, st, time.time()))
        audio_signal.inject(lambda now: None)
        self.assertFalse(playback.condition_ok({"type": "music"}, {}, st, time.time()))

    def test_dark_without_and_with_location(self):
        st = FakeStore()
        at = lambda h: dt.datetime(2024, 6, 21, h, 0).timestamp()
        self.assertTrue(playback.condition_ok({"type": "dark"}, {}, st, at(23)))
        self.assertFalse(playback.condition_ok({"type": "dark"}, {}, st, at(12)))
        self.assertTrue(playback.condition_ok({"type": "daylight"}, {}, st, at(12)))
        # at the weather location: an hour after its sunset (local time on this machine) is dark
        st = FakeStore(weather={"lat": 51.5074, "lon": -0.1278})
        s = sun.local_minutes(dt.date(2024, 6, 21), 51.5074, -0.1278)
        midnight = dt.datetime(2024, 6, 21).timestamp()
        self.assertTrue(playback.condition_ok({"type": "dark"}, {}, st, midnight + (s["sunset"] + 60) * 60))
        self.assertFalse(playback.condition_ok({"type": "dark"}, {}, st, midnight + (s["sunset"] - 60) * 60))

    def test_quiet_hours(self):
        st = FakeStore(quiet_hours={"enabled": True, "start": "22:00", "end": "07:00"})
        night = dt.datetime(2024, 1, 2, 23, 30).timestamp()
        self.assertTrue(playback.condition_ok({"type": "quiet"}, {}, st, night))
        self.assertFalse(playback.condition_ok({"type": "not_quiet"}, {}, st, night))

    def test_presence_follows_the_camera(self):
        import camera
        old = camera._cam

        class Cam:
            presence = type("P", (), {"present": True})()
        try:
            camera._cam = Cam()
            self.assertTrue(playback.condition_ok({"type": "present"}, {}, FakeStore(), time.time()))
            camera._cam = None
            self.assertTrue(playback.condition_ok({"type": "absent"}, {}, FakeStore(), time.time()))
        finally:
            camera._cam = old

    def test_has_data_asks_the_app(self):
        class Widget:
            data = None
            polled = 0

            def poll(self):
                self.polled += 1

            def fetch(self):
                pass

        class Departures:
            def __init__(self, deps):
                self.deps = deps

            def has_data(self, now, minutes):
                return any(d - now <= minutes * 60 for d in self.deps) if minutes else bool(self.deps)

        w = Widget()
        self.assertFalse(playback.app_has_data(w, 0))
        self.assertEqual(w.polled, 1)
        w.data = {"x": 1}
        self.assertTrue(playback.app_has_data(w, 0))
        self.assertTrue(playback.app_has_data(Departures([100, 2000]), 0, minutes=10))
        self.assertFalse(playback.app_has_data(Departures([2000]), 0, minutes=10))
        made = []
        probes = playback.Probes(None, lambda ctx, spec: made.append(spec) or Departures([300]))
        item = {"app": "departures", "params": {"stops": "1"}}
        self.assertTrue(playback.condition_ok({"type": "has_data", "minutes": 10}, item, FakeStore(), 0, probes))
        self.assertTrue(playback.condition_ok({"type": "has_data", "minutes": 10}, item, FakeStore(), 1, probes))
        self.assertEqual(len(made), 1)  # one probe, reused
        probes.sweep(10_000)
        self.assertEqual(probes.items, {})

    def test_clean_condition(self):
        self.assertIsNone(playback.clean_condition({"type": "bogus"}))
        self.assertEqual(playback.clean_condition({"type": "has_data", "minutes": "10"}), {"type": "has_data", "minutes": 10})


class RuleTest(unittest.TestCase):
    clock = T.Clock(None)

    def at(self, sched, y, mo, d, h, mi=0):
        return T.resolve(T.normalize(sched), dt.datetime(y, mo, d, h, mi), self.clock)

    def test_yearly_range_wraps_past_new_year(self):
        self.assertTrue(T.in_range(dt.date(2025, 12, 24), "12-01", "01-01"))
        self.assertTrue(T.in_range(dt.date(2026, 1, 1), "12-01", "01-01"))
        self.assertFalse(T.in_range(dt.date(2026, 1, 2), "12-01", "01-01"))
        self.assertFalse(T.in_range(dt.date(2025, 11, 30), "12-01", "01-01"))
        self.assertTrue(T.in_range(dt.date(2025, 6, 5), "06-01", "06-30"))
        self.assertFalse(T.in_range(dt.date(2026, 6, 5), "2025-06-01", "2025-06-30"))

    def test_date_rule_runs_past_midnight_from_its_last_day(self):
        sched = {"default": "Default", "rules": [
            {"when": "dates", "from": "12-01", "to": "01-01", "start": "17:00", "end": "01:00", "playlist": "Xmas"}]}
        self.assertEqual(self.at(sched, 2026, 1, 1, 18)["playlist"], "Xmas")
        self.assertEqual(self.at(sched, 2026, 1, 2, 0, 30)["playlist"], "Xmas")  # started on Jan 1
        self.assertEqual(self.at(sched, 2026, 1, 2, 18)["playlist"], "Default")
        self.assertEqual(self.at(sched, 2025, 11, 30, 18)["playlist"], "Default")

    def test_first_matching_rule_wins_and_the_others_are_listed(self):
        sched = {"default": "Default", "rules": [
            {"when": "once", "date": "2026-10-17", "start": "18:00", "end": "23:00", "playlist": "Party"},
            {"days": [5, 6], "start": "08:00", "end": "22:00", "playlist": "Weekend"},
            {"start": "00:00", "end": "24:00", "playlist": "All day", "enabled": False},
        ]}
        res = self.at(sched, 2026, 10, 17, 19)  # a Saturday
        self.assertEqual((res["playlist"], res["rule"], res["also"]), ("Party", 0, [1]))
        self.assertEqual(self.at(sched, 2026, 10, 17, 12)["playlist"], "Weekend")
        self.assertEqual(self.at(sched, 2026, 10, 24, 19)["playlist"], "Weekend")  # one-off: only that day
        self.assertEqual(self.at(sched, 2026, 10, 17, 22, 30)["playlist"], "Party")
        self.assertEqual(self.at(sched, 2026, 10, 19, 12)["source"], "default")  # disabled rule ignored

    def test_target_can_be_an_app(self):
        sched = {"rules": [{"start": "00:00", "end": "24:00", "spec": {"app": "layers", "params": {"composition": "X"}}}]}
        res = self.at(sched, 2026, 1, 1, 12)
        self.assertEqual(res["spec"]["params"]["composition"], "X")

    def test_sunset_rule(self):
        clock = T.Clock({"lat": 51.5074, "lon": -0.1278})
        sched = T.normalize({"rules": [{"start": "sunset+30", "end": "23:30", "playlist": "Evening"}]})
        d = dt.date(2024, 6, 21)
        go = clock.minutes("sunset+30", d)
        midnight = dt.datetime.combine(d, dt.time())
        before = midnight + dt.timedelta(minutes=go - 1)
        after = midnight + dt.timedelta(minutes=go + 1)
        if after.hour * 60 + after.minute < 23 * 60 + 30:  # holds unless this machine's zone is far from London's
            self.assertEqual(T.resolve(sched, before, clock)["source"], "default")
            self.assertEqual(T.resolve(sched, after, clock)["playlist"], "Evening")

    def test_preview_segments_cover_the_day(self):
        sched = T.normalize({"default": "Default", "rules": [
            {"start": "22:00", "end": "07:00", "playlist": "Night"},
            {"days": [0, 1, 2, 3, 4], "start": "07:00", "end": "09:00", "playlist": "Morning"}]})
        start = dt.datetime(2026, 10, 12)  # a Monday
        segs = T.preview(sched, start, 24, self.clock)
        self.assertEqual([(s["start"][11:], s["end"][11:], s["playlist"]) for s in segs], [
            ("00:00", "07:00", "Night"), ("07:00", "09:00", "Morning"), ("09:00", "22:00", "Default"),
            ("22:00", "00:00", "Night")])
        week = T.preview(sched, start, 168, self.clock)
        self.assertEqual(week[0]["start"], "2026-10-12T00:00")
        self.assertEqual(week[-1]["end"], "2026-10-19T00:00")
        self.assertEqual(T.next_change(sched, dt.datetime(2026, 10, 12, 8), self.clock), dt.datetime(2026, 10, 12, 9))

    def test_old_schedules_migrate_and_mean_the_same(self):
        old = {"enabled": True, "default": "Default", "rules": [
            {"days": [0, 1, 2, 3, 4], "start": "07:00", "end": "09:00", "playlist": "Morning"},
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:00", "end": "07:00", "playlist": "__off__"},
            {"start": "12:00", "end": "13:00", "playlist": "Lunch"}]}
        new = T.normalize(old)
        self.assertEqual(new["version"], 2)
        self.assertTrue(all(r["when"] == "weekly" and r["enabled"] and r["id"] for r in new["rules"]))
        self.assertEqual(new["rules"][2]["days"], list(range(7)))
        self.assertEqual(len({r["id"] for r in new["rules"]}), 3)
        self.assertEqual(T.normalize(new), new)  # stable
        for day in range(12, 19):
            for hour in range(24):
                t = dt.datetime(2026, 10, day, hour, 15)
                self.assertEqual(T.resolve(old, t, self.clock)["playlist"], T.resolve(new, t, self.clock)["playlist"])

    def test_bad_times_and_dates_are_tidied(self):
        r = T.clean_rule({"start": "25:99", "end": "sunset + 30", "when": "dates", "from": "13-40", "to": "x"})
        self.assertEqual((r["start"], r["end"], r["from"], r["to"]), ("23:59", "sunset+30", "12-31", "12-31"))
        self.assertEqual(T.clean_rule({"start": "bogus"})["start"], "00:00")


class RefsTest(unittest.TestCase):
    def test_rules_that_show_a_layer_set_follow_renames_and_deletes(self):
        data = {"playlists": {}, "compositions": {}, "rules": [], "schedule": {"rules": [
            {"start": "00:00", "end": "24:00", "playlist": "", "spec": {"app": "layers", "params": {"composition": "A"}}}]}}
        self.assertEqual(len(refs.find(data, "composition", "A")), 1)
        refs.rename(data, "composition", "A", "B")
        self.assertEqual(data["schedule"]["rules"][0]["spec"]["params"]["composition"], "B")
        done, changed = refs.remove(data, "composition", "B")
        self.assertIn("schedule", changed)
        self.assertNotIn("spec", data["schedule"]["rules"][0])


def wait_for(fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = flipdot.Board(config(), dry_run=True)
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.runner = runner_mod.Runner(self.board, self.store)

    def tearDown(self):
        self.runner.stop()
        audio_signal.inject(None)
        self.tmp.cleanup()

    def text(self, t, dur=30, **kw):
        return {"app": "text", "params": {"text": t, "style": "static"}, "duration": dur, **kw}

    def test_sunset_rule_fires_at_a_simulated_time(self):
        self.store.settings["weather"] = {"lat": 51.5074, "lon": -0.1278}
        self.store.save_playlists({**self.store.playlists, "Evening": {"items": [self.text("evening")]}})
        self.store.save_schedule({"enabled": True, "default": "Default", "rules": [
            {"start": "sunset", "end": "sunset+120", "playlist": "Evening"}]})
        d = dt.date(2024, 6, 21)
        go = T.Clock({"lat": 51.5074, "lon": -0.1278}).minutes("sunset", d)
        midnight = dt.datetime.combine(d, dt.time()).timestamp()
        fake = [midnight + (go - 2) * 60]
        self.runner.sched_clock = lambda: fake[0]
        self.runner.resume_schedule()
        self.assertTrue(wait_for(lambda: self.runner.playlist == "Default"))
        fake[0] += 5 * 60  # five minutes later: past sunset
        self.runner.wake.set()
        self.assertTrue(wait_for(lambda: self.runner.playlist == "Evening" and self.runner.app is not None
                                 and self.runner.app.p.get("text") == "evening"))
        st = self.runner.status()
        self.assertEqual((st["schedule_target"], st["schedule_rule"]), ("Evening", 0))

    def test_rule_can_show_a_layer_set_until_it_ends(self):
        self.store.save_schedule({"enabled": True, "rules": [
            {"start": "00:00", "end": "24:00", "spec": {"app": "layers", "params": {"composition": "Clock in the rain"}}}]})
        self.runner.resume_schedule()
        self.assertTrue(wait_for(lambda: self.runner.app is not None and self.runner.app.id == "layers"))
        first = self.runner.app
        time.sleep(0.3)
        self.assertIs(self.runner.app, first)  # not restarted every pass
        self.assertEqual(self.runner.status()["schedule_target"], "Layer set: Clock in the rain")

    def test_playlist_shuffle_conditions_and_effects(self):
        audio_signal.inject(lambda now: {"live": True, "loudness": 0.9, "spectrum": [0.9] * 16, "beat_age": 0.0})
        pl = {"shuffle": True, "transition": "none", "fx_chance": 100, "fx_effects": ["invert"], "fx_mode": "whole",
              "react_enabled": True,
              "items": [self.text("a", 1), self.text("b", 1), self.text("c", 1),
                        self.text("never", 1, when={"type": "no_music"})]}
        self.store.save_playlists({"Mix": playback.clean_playlist(pl)})
        seen = []
        self.runner.listeners.append(lambda: None)
        self.runner.play_playlist("Mix")
        end = time.time() + 4.6
        while time.time() < end:
            a = self.runner.app
            t = a.p.get("text") if a is not None else None
            if t and (not seen or seen[-1] != t):
                seen.append(t)
            time.sleep(0.01)
        self.assertGreaterEqual(len(seen), 4, seen)  # a second each (the shortest an item can be)
        self.assertNotIn("never", seen)  # music is playing
        self.assertTrue(all(x != y for x, y in zip(seen, seen[1:])))
        self.assertTrue(self.runner.effects.react_on)
        self.assertIsNotNone(self.runner.effects.fx)

    def test_effects_change_the_frame(self):
        st = Store(os.path.join(self.tmp.name, "d2"))
        fx = playback.Effects(st, random.Random(1))
        pl = {"fx_chance": 100, "fx_effects": ["invert"], "fx_mode": "whole"}
        fx.item_started(pl, 0.0, 30)
        c = Canvas(84, 42)
        out = fx.random_fx(c, 1.0)
        self.assertEqual(sum(out.px), 84 * 42)  # all inverted

    def test_previous_steps_back_through_the_order(self):
        self.store.save_playlists({"P": {"transition": "none", "items": [self.text("a"), self.text("b"), self.text("c")]}})
        self.runner.play_playlist("P")
        self.assertTrue(wait_for(lambda: self.runner.app is not None and self.runner.app.p.get("text") == "a"))
        self.runner.next()
        self.assertTrue(wait_for(lambda: self.runner.app.p.get("text") == "b"))
        self.runner.prev()
        self.assertTrue(wait_for(lambda: self.runner.app.p.get("text") == "a"))

    def test_status_says_how_long_a_notification_stays(self):
        self.runner.notify(spec={"app": "text", "params": {"text": "hi"}}, duration=12, transition="none")
        n = self.runner.status()["notifications"]
        self.assertEqual((n["hold"], n["seconds"]), (False, 12))
        self.runner.clear_notifications()
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        self.runner.notify(spec={"app": "text", "params": {"text": "hold"}}, hold=True, transition="none")
        self.assertTrue(self.runner.status()["notifications"]["hold"])
        self.runner.clear_notifications()


if __name__ == "__main__":
    unittest.main()
