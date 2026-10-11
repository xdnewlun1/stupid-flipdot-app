"""The ambient pack (apps/pack_ambient.py): percussion, split-flap (app and character transition), mechanical
clock, rain, pendulum wave, timers (engine, commands, alarm), on air, and the calendar automation trigger.
Run: python3 -m pytest -q tests/test_ambient.py"""

import datetime as dt
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
import audio_signal  # noqa: E402
import splitflap  # noqa: E402
import timers  # noqa: E402
from apps import pack_ambient, react  # noqa: E402
from canvas import Canvas  # noqa: E402
from charfx import CharFx  # noqa: E402
from runner import Context  # noqa: E402

react.SAMPLE_HZ = 0  # the tests drive the clock


class Store:
    def __init__(self, quiet=False, **settings):
        self.settings = {"visualizer": {"source": "off"}, **settings}
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59", "max_flips": 60}
        self.dir = tempfile.mkdtemp()
        self.saved = {}
        self.compositions = {}

    def _load(self, name, default):
        return self.saved.get(name, default)

    def _save(self, name, data):
        import json
        self.saved[name] = json.loads(json.dumps(data))


def make(app_id, params=None, store=None, started=None):
    a = apps.create(Context(84, 42, store or Store()), {"app": app_id, "params": params or {}})
    if started is not None:
        a.started = started
    return a


def frame(app, now):
    c = Canvas(84, 42)
    out = app.render(c, now)
    return c, out


def at(h, m, s, frac=0.0):
    return dt.datetime(2026, 10, 10, h, m, s).timestamp() + frac


def setUpModule():
    react._quiet[:] = [0.0, False]


class SplitFlapTest(unittest.TestCase):
    def test_sequence_goes_forward_round_the_drum(self):
        self.assertEqual(splitflap.sequence("A", "D"), ["B", "C", "D"])
        self.assertEqual(splitflap.sequence(" ", "A"), ["A"])
        self.assertEqual(splitflap.sequence("9", "0", seg=True), ["0"])  # a digit drum wraps without a blank
        self.assertEqual(splitflap.sequence("5", "0", seg=True), ["6", "7", "8", "9", "0"])
        self.assertEqual(splitflap.sequence("A", "Z", limit=3), ["X", "Y", "Z"])
        self.assertEqual(splitflap.sequence("A", "é"), ["é"])  # not on the drum: one flap

    def test_app_flaps_then_lands_on_the_text(self):
        t0 = at(12, 0, 0)
        a = make("splitflap", {"text": "HELLO\nWORLD", "stagger": 40}, started=t0)
        seen = set()
        c = None
        for i in range(150):  # 4.5 s
            c, _ = frame(a, t0 + i * 0.03)
            seen.add(bytes(c.px))
        self.assertGreater(len(seen), 20)  # it went through many in-between pictures
        want = Canvas(84, 42)
        want.text(a.x0, a.y0, "HELLO", a.font)
        want.text(a.x0, a.y0 + a.ch, "WORLD", a.font)
        self.assertEqual(c.rows(), want.rows())
        self.assertEqual(a.interval, 0.25)  # settled: slow redraws

    def test_quiet_hours_land_in_one_flap(self):
        t0 = at(12, 0, 0)
        a = make("splitflap", {"text": "ZZZ", "stagger": 0}, store=Store(quiet=True), started=t0)
        react._quiet[:] = [0.0, False]
        frame(a, t0)
        self.assertEqual([len(r.steps) for r in a.runs[:3]], [1, 1, 1])
        react._quiet[:] = [0.0, False]

    def test_pages_and_time(self):
        t0 = at(18, 42, 5)
        a = make("splitflap", {"text": "{time}\n\nNEXT", "hold": 5}, started=t0)
        frame(a, t0)
        self.assertEqual("".join(a.target[:5]), "18:42")
        frame(a, t0 + 5.1)
        self.assertEqual("".join(a.target[:4]), "NEXT")

    def test_whole_board_transition(self):
        import transitions
        old, new = Canvas(84, 42), Canvas(84, 42)
        old.text(2, 2, "OLD")
        new.text(2, 20, "NEW PICTURE")
        t = transitions.Transition("split-flap", old, 84, 42, 1.0)
        mids = {bytes(t.mix(new, t.start + k * 0.05).px) for k in range(1, 20)}
        self.assertGreater(len(mids), 8)
        self.assertEqual(t.mix(new, t.start + 1.0).px, new.px)

    def test_char_transition_flaps_clock_digits(self):
        fx = CharFx("split-flap", 1.0)

        def draw(s, now):
            c, boxes = Canvas(84, 42), []
            c.seg_text(10, 5, s, boxes=boxes)
            fx.apply(c, boxes, now)
            return c

        draw("2:59", 0.0)
        mids = [draw("3:00", 1.0 + k * 0.03) for k in range(40)]
        final = Canvas(84, 42)
        final.seg_text(10, 5, "3:00")
        self.assertTrue(fx.active is False)
        self.assertEqual(mids[-1].rows(), final.rows())
        # the "5" went 6, 7, 8, 9 before 0: a picture of a 7 was on the board on the way
        seven = Canvas(84, 42)
        seven.seg_text(10, 5, "3:70")
        box = lambda c: [r[27:41] for r in c.rows()[5:32]]  # the second digit's box
        self.assertIn(box(seven), [box(m) for m in mids])


class MechClockTest(unittest.TestCase):
    def test_one_disc_moves_each_second(self):
        a = make("mechclock")
        prev, _ = frame(a, at(10, 20, 5, 0.1))
        for s in range(6, 20):
            c, _ = frame(a, at(10, 20, s, 0.1))
            diff = sum(x != y for x, y in zip(prev.px, c.px))
            self.assertEqual(diff, 2)  # the bead left one place and arrived at the next: two flips, a tick
            prev = c
        self.assertLess(a.interval, 1.0)

    def test_strikes_the_hour(self):
        a = make("mechclock", {"chime_gap": 3})
        inverted = []
        for k in range(int(12 / 0.05)):
            c, _ = frame(a, at(15, 0, 0, k * 0.05))
            inverted.append(c.px[0] == 1 and c.px[84 * 41 + 83] == 1 and sum(c.px) > 2000)
        changes = [i for i in range(1, len(inverted)) if inverted[i] != inverted[i - 1]]
        # 3 o'clock: strikes at 0, 3 and 6 s (invert, back, invert), then the damper just after the last one
        self.assertTrue(inverted[0])
        self.assertEqual(len(changes), 3)
        self.assertAlmostEqual(changes[0] * 0.05, 3.0, delta=0.06)
        self.assertAlmostEqual(changes[1] * 0.05, 6.0, delta=0.06)
        self.assertAlmostEqual(changes[2] * 0.05, 6.6, delta=0.06)
        self.assertFalse(inverted[-1])

    def test_quiet_hours_mute_the_chime(self):
        react._quiet[:] = [0.0, False]
        a = make("mechclock", store=Store(quiet=True))
        c, _ = frame(a, at(15, 0, 0, 0.1))
        react._quiet[:] = [0.0, False]
        self.assertLess(sum(c.px), 1000)


class RainTest(unittest.TestCase):
    def test_rate_from_the_weather(self):
        self.assertEqual(pack_ambient.rain_rate(0, 3, 10), 0)       # overcast: dry
        self.assertGreater(pack_ambient.rain_rate(0, 61, 10), 0)    # rain reported, nothing measured yet: a little
        light, heavy = pack_ambient.rain_rate(0.5, 61, 10), pack_ambient.rain_rate(8, 65, 10)
        self.assertLess(light, heavy)
        self.assertAlmostEqual(heavy, 10)

    def test_drops_at_the_volume(self):
        a = make("asmrrain", {"volume": 5, "linger": 1})
        t0 = 1000.0
        for i in range(400):  # 20 s
            c, _ = frame(a, t0 + i * 0.05)
        self.assertAlmostEqual(a.drop_count / 20, 5, delta=1.5)
        self.assertLess(sum(c.px), 30)  # sparse: the sound is the point

    def test_quiet_hours_cap(self):
        react._quiet[:] = [0.0, False]
        a = make("asmrrain", {"volume": 20, "quiet_volume": 1}, store=Store(quiet=True))
        self.assertEqual(a.rate(), 1)
        react._quiet[:] = [0.0, False]

    def test_weather_mode_follows_the_fixture(self):
        fixture = {"current": {"time": "2026-10-10T12:00", "interval": 900, "precipitation": 2.4, "rain": 2.4,
                               "showers": 0.0, "weather_code": 63}}
        store = Store(weather={"lat": 51.5, "lon": -0.12})
        a = make("asmrrain", {"mode": "weather", "volume": 10}, store=store)
        with mock.patch.object(pack_ambient, "http_get", return_value=fixture) as get:
            a.data = a.fetch()
        self.assertIn("latitude=51.5", get.call_args[0][0])
        self.assertGreater(a.rate(), 5)
        a.data = {"mm": 0.0, "code": 2}
        a._last_fetch = time.time()
        frame(a, 1000.0)
        self.assertTrue(a.hidden)  # dry: as a layer it shows what's below

    def test_weather_mode_needs_a_location(self):
        self.assertIsNotNone(apps.base.setup_needed(Store(), {"app": "asmrrain", "params": {"mode": "weather"}}))
        self.assertIsNone(apps.base.setup_needed(Store(), {"app": "asmrrain", "params": {}}))


class PendulumTest(unittest.TestCase):
    def test_lined_up_at_rest_and_again_after_a_cycle(self):
        a = make("pendulums", {"cycle": 60, "rest": 4}, started=0.0)
        ys = lambda c: [next((y for y in range(42) if c.px[y * 84 + x]), None) for x in range(84)]
        c0, _ = frame(a, 1.0)
        self.assertEqual(len(set(ys(c0))), 1)  # resting, lined up
        self.assertEqual(a.interval, 0.5)  # and still
        c1, _ = frame(a, 4 + 17.3)
        self.assertGreater(len(set(ys(c1))), 10)  # drifting through a pattern
        c2, _ = frame(a, 64 + 1.0)
        self.assertEqual(ys(c2), ys(c0))

    def test_reacts_to_music_without_jumping(self):
        a = make("pendulums", {"react": True, "react_fallback": "beat"}, started=0.0)
        for i in range(50):
            frame(a, 10 + i * 0.066)
        self.assertTrue(a.music.on)


class TimerEngineTest(unittest.TestCase):
    def test_durations(self):
        for text, secs in [("8", 480), ("8m", 480), ("8 minutes", 480), ("1h 30m", 5400), ("8:00", 480),
                           ("1:30:00", 5400), ("90s", 90), (300, 300)]:
            self.assertEqual(timers.parse_duration(text), secs, text)
        with self.assertRaises(ValueError):
            timers.parse_duration("soon")

    def test_commands(self):
        self.assertEqual(timers.split_command("pasta 8m"), ("start", "pasta", 480))
        self.assertEqual(timers.split_command("8 minutes for the pasta"), ("start", "the pasta", 480))
        self.assertEqual(timers.split_command("Tea 3:30"), ("start", "Tea", 210))
        self.assertEqual(timers.split_command("start laundry 1 hour 20 minutes"), ("start", "laundry", 4800))
        self.assertEqual(timers.split_command("10 min"), ("start", "Timer", 600))
        self.assertEqual(timers.split_command("pause pasta"), ("pause", "pasta"))
        self.assertEqual(timers.split_command("cancel all"), ("cancel", None))
        self.assertEqual(timers.split_command("pomodoro"), ("pomodoro", True))

    def test_start_pause_resume_cancel_and_the_limit(self):
        t = timers.Timers(Store())
        t.start("Pasta", "8m", now=0)
        t.start("Tea", "30m", now=0)
        self.assertEqual(t.pause("pasta", now=60), 1)
        self.assertEqual(t.snapshot(200)["timers"][-1]["left"], 420)  # paused: still 7 minutes
        t.resume("Pasta", now=200)
        self.assertAlmostEqual(t.left(t._find("Pasta")[0], 260), 360)
        t.start("A", 60, now=0)
        t.start("B", 60, now=0)
        with self.assertRaises(ValueError):
            t.start("C", 60, now=0)
        t.cancel("tea")
        t.start("C", 60, now=0)
        self.assertEqual(t.cancel(), 4)

    def test_rings_then_clears(self):
        rang = []
        t = timers.Timers(Store())
        t.on_ring = rang.append
        t.start("Eggs", 420, now=0)
        t.tick(419)
        self.assertEqual(rang, [])
        t.tick(420.2)
        time.sleep(0.05)  # on_ring runs on its own thread
        self.assertEqual([r["name"] for r in rang], ["Eggs"])
        self.assertEqual(t.ringing(421)[0]["name"], "Eggs")
        t.tick(420.2 + timers.RING_S)
        self.assertEqual(t.snapshot(441)["timers"], [])

    def test_pomodoro_alternates_and_counts(self):
        t = timers.Timers(Store())
        day = at(9, 0, 0)
        t.set_pomodoro(True, work=25, rest=5, now=day)
        names = []
        now = day
        for _ in range(9):
            nxt = min(x["ends"] for x in t.items if x["state"] == "running")
            now = nxt + 0.1
            t.tick(now)
            names.append([x["name"] for x in t.items if x["state"] == "running"][0])
        self.assertEqual(names, ["Break", "Work", "Break", "Work", "Break", "Work", "Long break", "Work", "Break"])
        self.assertEqual(t.pomodoro["sessions"], 5)
        t.set_pomodoro(False, now=now)
        self.assertEqual([x for x in t.items if x["state"] == "running"], [])

    def test_saved_and_restored(self):
        store = Store()
        t = timers.Timers(store)
        t.start("Bread", "45m", now=time.time())
        again = timers.Timers(store)
        self.assertEqual([x["name"] for x in again.items], ["Bread"])
        self.assertEqual(again.start("Tea", 60)["id"], "t2")

    def test_command_api_shapes(self):
        t = timers.Timers(Store())
        t.command('{"action": "start", "name": "Pizza", "duration": "12:00"}')
        t.command("pause")
        t.command({"command": "resume pizza"})
        self.assertEqual(t.snapshot()["timers"][0]["state"], "running")
        self.assertIn("Pizza until", t.summary())
        with self.assertRaises(ValueError):
            t.command({"action": "explode"})


class TimersAppTest(unittest.TestCase):
    def test_alarm_clatters_then_flashes_the_name(self):
        store = Store()
        box = timers.for_store(store)
        box.start("Pasta", 60, now=0)
        a = make("timers", store=store)
        whites = []
        for k in range(int(5 / 0.03)):
            c, _ = frame(a, 60 + k * 0.03)
            whites.append(sum(c.px))
        full = [False] + [w == 84 * 42 for w in whites[:int(3.0 / 0.03)]]
        bursts = sum(1 for i in range(1, len(full)) if full[i] and not full[i - 1])
        self.assertEqual(bursts, 6)  # 12 flips: six times to white and back
        self.assertTrue(0 < whites[-1] < 84 * 42)  # then the name

    def test_alarm_only_ends_when_nothing_rings(self):
        store = Store()
        a = make("timers", {"alarm_only": True}, store=store)
        _, out = frame(a, 0)
        self.assertIs(out, False)

    def test_layouts(self):
        store = Store()
        box = timers.for_store(store)
        for i, name in enumerate(["Pasta", "Tea", "Laundry", "Bread"]):
            box.start(name, 60 * (i + 2), now=0)
            c, _ = frame(make("timers", store=store), 10)
            self.assertGreater(sum(c.px), 40)


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a1
DTSTART:20261010T130000Z
DTEND:20261010T140000Z
SUMMARY:Team meeting
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART:20261010T150000Z
DTEND:20261010T153000Z
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261011
SUMMARY:Meeting day (all day)
END:VEVENT
END:VCALENDAR
"""


def utc(h, m):
    return dt.datetime(2026, 10, 10, h, m, tzinfo=dt.timezone.utc)


class OnAirTest(unittest.TestCase):
    def setUp(self):
        from apps.ical import parse_ics
        self.events = parse_ics(ICS)

    def test_meetings_now(self):
        self.assertEqual([e[2] for e in pack_ambient.meetings_now(self.events, utc(13, 30))], ["Team meeting"])
        self.assertEqual(pack_ambient.meetings_now(self.events, utc(13, 30), "dentist"), [])
        self.assertEqual([e[2] for e in pack_ambient.meetings_now(self.events, utc(15, 10), "meeting, dentist")], ["Dentist"])
        self.assertEqual(pack_ambient.meetings_now(self.events, utc(14, 0)), [])  # ended
        self.assertEqual(pack_ambient.next_meeting(self.events, utc(14, 5))[2], "Dentist")

    def test_sign_and_hiding(self):
        a = make("onair", {"urls": "https://example.invalid/cal.ics", "keyword": "meeting"})
        a.data, a._last_fetch = self.events, time.time()
        c, _ = frame(a, utc(13, 30).timestamp())
        self.assertFalse(a.hidden)
        self.assertGreater(sum(c.px[:84 * 14]), 84 * 14 // 2)  # the lit ON AIR panel
        c, _ = frame(a, utc(14, 30).timestamp())
        self.assertTrue(a.hidden)
        self.assertEqual(sum(c.px), 0)

    def test_needs_a_calendar(self):
        self.assertIsNotNone(apps.base.setup_needed(Store(), {"app": "onair", "params": {}}))


class CalendarTriggerTest(unittest.TestCase):
    def test_fires_once_per_event_and_lasts_until_it_ends(self):
        import rules
        runner = mock.Mock()
        board = mock.Mock(version=0)
        store = Store()
        with mock.patch.object(rules.threading, "Thread"):
            auto = rules.Automations(runner, board, store, notify_fn=mock.Mock())
        auto.rules = rules.validate([{"id": "r1", "trigger": {"type": "calendar", "urls": "https://example.invalid/c.ics",
                                                              "keyword": "meeting", "until_end": True},
                                      "action": {"type": "app", "app": "onair", "params": {"idle": "next"}}}])
        with mock.patch.object(rules, "http_get", return_value=ICS.encode()):
            auto._check_calendar(utc(12, 0).timestamp())
            runner.notify.assert_not_called()
            with mock.patch.object(rules.time, "time", return_value=utc(13, 1).timestamp()):
                auto._check_calendar(utc(13, 1).timestamp())
            self.assertEqual(runner.notify.call_count, 1)
            kw = runner.notify.call_args.kwargs
            self.assertEqual(kw["spec"]["app"], "onair")
            self.assertAlmostEqual(kw["duration"], 59 * 60, delta=2)
            auto._check_calendar(utc(13, 30).timestamp())  # still the same meeting: no second takeover
            self.assertEqual(runner.notify.call_count, 1)

    def test_needs_a_link(self):
        import rules
        with self.assertRaises(ValueError):
            rules.validate([{"trigger": {"type": "calendar"}, "action": {"type": "black"}}])


class PercussionTest(unittest.TestCase):
    def run_app(self, params, store=None, seconds=8):
        audio_signal.inject(lambda now: {**audio_signal.generated(now), "live": True})
        try:
            a = make("percussion", params, store=store)
            pics = []
            for i in range(int(seconds * 25)):
                c, _ = frame(a, 5000 + i / 25)
                pics.append(bytes(c.px))
            return a, pics
        finally:
            audio_signal.inject(None)

    def test_plays_along_on_the_border(self):
        a, pics = self.run_app({"where": "border", "thickness": 2})
        changes = sum(1 for i in range(1, len(pics)) if pics[i] != pics[i - 1])
        self.assertGreater(a.beats, 10)  # 120 BPM for 8 s
        self.assertGreater(changes, 15)
        inside = {y * 84 + x for y in range(2, 40) for x in range(2, 82)}
        self.assertTrue(all(not p[i] for p in pics for i in inside))  # only the frame plays

    def test_whole_board_and_corners(self):
        for where in ("board", "corners"):
            a, pics = self.run_app({"where": where})
            self.assertGreater(len(set(pics)), 10, where)

    def test_quiet_hours_hold_still(self):
        react._quiet[:] = [0.0, False]
        a, pics = self.run_app({}, store=Store(quiet=True), seconds=3)
        react._quiet[:] = [0.0, False]
        self.assertEqual(len(set(pics)), 1)

    def test_layer_stack_keeps_steady_timing(self):
        stack = make("layers", {"layers": [{"app": "clock"}, {"app": "percussion", "blend": "xor"}]})
        self.assertTrue(stack.steady_timing)
        self.assertFalse(make("layers", {"layers": [{"app": "clock"}]}).steady_timing)


class MqttTimerTest(unittest.TestCase):
    def test_discovery_has_the_timer_entities(self):
        import mqtt
        cfgs = dict(mqtt.discovery_configs("flipdot", "homeassistant", ["Nothing"], ["Nothing"]))
        keys = {c["unique_id"] for c in cfgs.values()}
        self.assertTrue({"flipdot_timer", "flipdot_timers", "flipdot_timers_cancel", "flipdot_pomodoro"} <= keys)
        timer = next(c for c in cfgs.values() if c["unique_id"] == "flipdot_timer")
        self.assertEqual(timer["command_topic"], "flipdot/ha/timer/set")


if __name__ == "__main__":
    unittest.main()
