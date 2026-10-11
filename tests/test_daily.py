"""Daily channels, the Claude postcard, launch countdown, rolling counter and ISS tracker (apps/pack_daily.py).
Every network call is answered from recorded fixtures in tests/fixtures/daily; the Anthropic API is mocked.
Run: python3 -m pytest -q tests/test_daily.py"""

import calendar
import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aidraw  # noqa: E402
import apps  # noqa: E402
from apps import pack_daily as pd  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402
from store import Store  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "daily")
ROUTES = {"zenquotes.io": "zenquotes_today.json", "onthisday": "wikipedia_onthisday_10_10.json",
          "thespacedevs.com": "ll2_upcoming.json", "/tles": "wheretheiss_tle.json",
          "satellites/25544": "wheretheiss_position.json"}


def fixture(name):
    with open(os.path.join(FX, name)) as f:
        return json.load(f)


class FakeNet:
    """Stands in for pack_daily.fetch_json: answers from fixtures, counts calls, can go offline."""

    def __init__(self):
        self.calls = []
        self.offline = False

    def __call__(self, url, headers=None, timeout=10):
        self.calls.append(url)
        if self.offline:
            raise OSError("network is down")
        for key, name in ROUTES.items():
            if key in url:
                return fixture(name)
        raise AssertionError(f"unexpected request to {url}")


def local(y, mo, d, h, mi, s=0):
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


def lit(c):
    return sum(c.px)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.ctx = Context(84, 42, self.store)
        self.net = FakeNet()
        p = mock.patch.object(pd, "fetch_json", self.net)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def app(self, app_id, **params):
        a = apps.create(self.ctx, {"app": app_id, "params": params})
        self.addCleanup(a.stop)
        return a

    def draw(self, app, now, wait=True):
        c = Canvas(84, 42)
        app.render(c, now)
        if wait and getattr(app, "_fetching", False):
            for _ in range(200):
                if not app._fetching:
                    break
                time.sleep(0.01)
            c = Canvas(84, 42)
            app.render(c, now)
        return c


class FileCache(Base):
    def test_fresh_copy_is_reused_and_survives_a_restart(self):
        url = "https://zenquotes.io/api/today"
        d1, _, stale = pd.cached_get(self.store, "zen", url, 900)
        self.assertFalse(stale)
        pd.cached_get(self.store, "zen", url, 900)
        self.assertEqual(len(self.net.calls), 1)
        again = Store(self.tmp.name)  # a restart: the file is still there
        d2, _, _ = pd.cached_get(again, "zen", url, 900)
        self.assertEqual(d1, d2)
        self.assertEqual(len(self.net.calls), 1)

    def test_stale_copy_when_offline_and_error_without_one(self):
        url = "https://zenquotes.io/api/today"
        pd.cached_get(self.store, "zen", url, 900)
        self.net.offline = True
        data, _, stale = pd.cached_get(self.store, "zen", url, 900, now=time.time() + 3600)
        self.assertTrue(stale)
        self.assertEqual(data[0]["a"], "John Wooden")
        with self.assertRaises(OSError):
            pd.cached_get(self.store, "other", url, 900)


class Text(unittest.TestCase):
    def test_every_bundled_text_fits_the_board(self):
        items = [pd.Item(q, a + ", " + s, "Quote") for q, a, s in pd.bundled("quotes.json")["quotes"]]
        items += [pd.Item(m, "", w, p) for w, p, m in pd.bundled("words.json")["words"]]
        items += [pd.Item(p, "") for p in pd.bundled("prompts.json")["prompts"]]
        minutes = pd.bundled("litclock.json")["minutes"]
        for k in sorted(minutes)[::7]:
            for q, a, b, title, author in minutes[k]:
                items.append(pd.Item(q, f"{title}, {author}", hl=(a, b)))
        for item in items:
            for size in ("auto", "fit", "small", "large"):
                pages = pd.layout(item, 84, 35, size)
                self.assertTrue(pages)
                for page in pages:
                    self.assertLessEqual(pd.page_height(page), 35, item.text)
                    for ln in page:
                        if ln:
                            self.assertLessEqual(pd.line_width(ln[1], ln[0]), 84, item.text)
                words = [w for pg in pages for ln in pg if ln and ln[2] != "right" for w, _ in ln[1]]
                self.assertEqual("".join(words), item.text.replace(" ", ""), "text lost in layout")

    def test_litclock_data(self):
        data = pd.bundled("litclock.json")
        self.assertIn("Project Gutenberg", data["source"])
        for key, quotes in data["minutes"].items():
            self.assertRegex(key, r"^([01]\d|2[0-3]):[0-5]\d$")
            for q, a, b, title, author in quotes:
                self.assertTrue(0 <= a < b <= len(q) and title and author, (key, q))
                self.assertEqual(q, pd.asciify(q))

    def test_time_words(self):
        self.assertEqual(pd.time_words(16, 23), "twenty-three minutes past four")
        self.assertEqual(pd.time_words(8, 45), "a quarter to nine")
        self.assertEqual(pd.time_words(23, 59), "one minute to midnight")
        self.assertEqual(pd.time_words(0, 0), "midnight")
        self.assertEqual(pd.time_words(12, 0), "noon")
        self.assertEqual(pd.time_words(7, 30), "half past seven")

    def test_highlight_is_drawn_inverted(self):
        item = pd.Item("It was ten past eight.", "", hl=(7, 21))
        c = Canvas(84, 42)
        pd.draw_page(c, pd.layout(item, 84, 42)[0], 0, 0, 84, 42)
        rows = c.rows()
        self.assertTrue(any("1" * 20 in r for r in rows), "the time sits on a lit box")


class Channels(Base):
    def test_offline_channels_show_text_and_change_daily(self):
        for ch in ("quote", "word", "oblique"):
            a = self.app("daily", channel=ch)
            today = self.draw(a, local(2026, 10, 10, 9, 0))
            self.assertGreater(lit(today), 40, ch)
            b = self.app("daily", channel=ch)
            self.assertNotEqual(self.draw(b, local(2026, 10, 11, 9, 0)).rows(), today.rows(), ch)
        self.assertEqual(self.net.calls, [], "bundled channels never touch the network")

    def test_zenquotes_and_its_fallback(self):
        with mock.patch.object(pd.dt, "date", wraps=dt.date) as d:
            d.today.return_value = dt.date(2026, 10, 10)
            a = self.app("daily", channel="quote", quote_source="zenquotes")
            self.draw(a, local(2026, 10, 10, 9, 0))
            items, _ = a.items(local(2026, 10, 10, 9, 0))
        self.assertEqual(items[0].by, "John Wooden")
        self.net.offline = True
        self.tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp2.cleanup)
        b = apps.create(Context(84, 42, Store(self.tmp2.name)), {"app": "daily", "params": {"channel": "quote", "quote_source": "zenquotes"}})
        self.assertGreater(lit(self.draw(b, local(2026, 10, 10, 9, 0))), 40)
        items, _ = b.items(local(2026, 10, 10, 9, 0))
        self.assertNotEqual(items[0].by, "John Wooden")  # the built-in list instead

    def test_on_this_day_pages_and_offline(self):
        with mock.patch.object(pd.dt, "date", wraps=dt.date) as d:
            d.today.return_value = dt.date(2026, 10, 10)
            a = self.app("daily", channel="onthisday", events=3)
            self.draw(a, local(2026, 10, 10, 9, 0))
            items, _ = a.items(local(2026, 10, 10, 9, 0))
        self.assertEqual([i.right for i in items], ["2004", "1992", "1973"])
        self.assertTrue(any("onthisday/selected/10/10" in u for u in self.net.calls))
        self.net.offline = True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        b = apps.create(Context(84, 42, Store(tmp.name)), {"app": "daily", "params": {"channel": "onthisday"}})
        c = self.draw(b, local(2026, 10, 10, 9, 0))
        self.assertGreater(lit(c), 20)  # a calm "no connection" card, not an error

    def test_literature_clock(self):
        a = self.app("daily", channel="litclock")
        minutes = pd.bundled("litclock.json")["minutes"]
        have = next(k for k in sorted(minutes))
        h, m = map(int, have.split(":"))
        item = pd.litclock_item(dt.datetime(2026, 10, 10, h, m))
        self.assertIn(item.text[item.hl[0]:item.hl[1]].lower(), [pd.asciify(q[0][q[1]:q[2]]).lower() for q in minutes[have]])
        missing = next(f"{x // 60:02d}:{x % 60:02d}" for x in range(1440) if f"{x // 60:02d}:{x % 60:02d}" not in minutes)
        h, m = map(int, missing.split(":"))
        item = pd.litclock_item(dt.datetime(2026, 10, 10, h, m))
        self.assertTrue(item.text.startswith("It was ") and item.by == "")
        self.assertGreater(lit(self.draw(a, local(2026, 10, 10, h, m))), 40)


def fake_claude(calls):
    def call_api(key, body, timeout=300):
        calls.append((key, body))
        text = body["messages"][0]["content"]
        if "haiku" in text and "FRAME" not in text:
            return {"content": [{"type": "text", "text": "Cold rain on the glass\nthe kettle starts its whisper\nOctober evening"}],
                    "stop_reason": "end_turn"}
        rows = ["".join("1" if (x + y) % 9 < 4 else "0" for x in range(84)) for y in range(42)]
        return {"content": [{"type": "text", "text": "FRAME 1\n" + "\n".join(rows)}], "stop_reason": "end_turn"}
    return call_api


class Postcards(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        p = mock.patch.object(aidraw, "call_api", fake_claude(self.calls))
        p.start()
        self.addCleanup(p.stop)
        pd._failed.clear()

    def test_needs_an_api_key(self):
        self.assertIn("API key", apps.base.setup_needed(self.store, {"app": "postcard"}))
        a = self.app("postcard")
        c = self.draw(a, local(2026, 10, 10, 9, 0))
        self.assertGreater(lit(c), 20)
        self.assertIsNotNone(a.setup)
        with self.assertRaises(pd.NeedsSetup):
            pd.make_postcard(self.store, dt.date(2026, 10, 10))
        self.assertEqual(self.calls, [])

    def test_morning_postcard_is_made_once_and_archived(self):
        self.store.settings["ai"] = {"api_key": "sk-test", "model": "claude-sonnet-5-5"}
        a = self.app("postcard", time="07:30", save_to="both")
        self.assertIsNone(pd.maybe_make(self.store, now=local(2026, 10, 10, 7, 0)), "not before its time")
        self.assertEqual(self.calls, [])
        entry = pd.maybe_make(self.store, now=local(2026, 10, 10, 7, 31))
        self.assertEqual(entry["haiku"][0], "Cold rain on the glass")
        self.assertEqual(len(entry["rows"]), 42)
        self.assertEqual({b["model"] for _, b in self.calls}, {"claude-sonnet-5-5"})
        self.assertEqual({k for k, _ in self.calls}, {"sk-test"})
        self.assertIsNone(pd.maybe_make(self.store, now=local(2026, 10, 10, 9, 0)), "once a day")
        self.assertEqual(len(self.calls), 2)
        gal = [g for g in self.store.galleries.values() if g["name"] == "Postcards"]
        self.assertEqual([i["file"] for i in gal[0]["items"]], ["postcard-2026-10-10.png", "postcard-2026-10-10-haiku.png"])
        self.assertTrue(self.store.get_scene("postcard-2026-10-10"))
        # the app shows the picture, then the haiku
        with mock.patch.object(pd.dt, "date", wraps=dt.date):
            a._last_fetch = 0
            pic = self.draw(a, local(2026, 10, 10, 9, 0))
        self.assertEqual(pic.rows(), entry["rows"])
        hk = Canvas(84, 42)
        a.render(hk, local(2026, 10, 10, 9, 0) + 30)  # 20 s of picture, then the haiku
        self.assertNotEqual(hk.rows(), entry["rows"])
        self.assertGreater(lit(hk), 30)

    def test_failure_backs_off(self):
        self.store.settings["ai"] = {"api_key": "sk-test"}
        self.app("postcard", time="07:00")

        def broken(key, body, timeout=300):
            self.calls.append(body)
            raise aidraw.AiError("overloaded")
        with mock.patch.object(aidraw, "call_api", broken):
            with self.assertRaises(aidraw.AiError):
                pd.maybe_make(self.store, now=local(2026, 10, 10, 8, 0))
            self.assertIsNone(pd.maybe_make(self.store, now=time.time()))
        self.assertEqual(len(self.calls), 1)

    def test_haiku_parsing(self):
        self.assertEqual(pd.parse_haiku('Here is a haiku:\n\n"Leaves on the water\nthe bridge holds its breath\nwind"'),
                         ["Leaves on the water", "the bridge holds its breath", "wind"])
        with self.assertRaises(ValueError):
            pd.parse_haiku("Sorry.")


NET = calendar.timegm((2026, 10, 10, 23, 0, 0))  # the first launch in the fixture


class Launch(Base):
    def test_parse_fixture(self):
        ls = pd.launch_list(fixture("ll2_upcoming.json"))
        self.assertEqual(ls[0]["rocket"], "Falcon 9")
        self.assertEqual(ls[0]["mission"], "Starlink Group 15-25")
        self.assertEqual(ls[0]["provider"], "SpaceX")
        self.assertEqual(ls[0]["net"], NET)
        self.assertEqual([l["net"] for l in ls], sorted(l["net"] for l in ls))

    def test_card_countdown_and_full_board_finish(self):
        a = self.app("launches", count=1)
        card = self.draw(a, NET - 3 * 3600)
        self.assertGreater(lit(card), 100)
        big = self.draw(a, NET - 7.5)
        self.assertGreater(lit(big), 400)  # one huge digit
        self.assertTrue(all(r[:30] == "0" * 30 for r in big.rows()), "centred, nothing else")
        self.assertGreater(lit(self.draw(a, NET + 5)), 10)  # liftoff
        after = self.draw(a, NET + 60)  # the next launch takes over
        self.assertGreater(lit(after), 100)

    def test_rate_limit_and_filter(self):
        a = self.app("launches")
        self.draw(a, NET - 3600)
        b = self.app("launches", filter="H3")  # a second app (or a restart) uses the file cache
        self.draw(b, NET - 3600)
        self.assertEqual(sum("thespacedevs" in u for u in self.net.calls), 1)
        self.assertGreaterEqual(pd.Launches.refresh, 900)
        self.assertEqual([l["rocket"] for l in b.upcoming(NET)], ["H3-24"])

    def test_offline_without_cache_is_calm(self):
        self.net.offline = True
        c = self.draw(self.app("launches"), NET)
        self.assertGreater(lit(c), 20)


class Counting(Base):
    def test_api_values(self):
        self.assertEqual(pd.set_counter(self.store, "stars", 41)["value"], 41)
        self.assertEqual(pd.set_counter(self.store, "stars", add=1)["value"], 42)
        self.assertEqual(Store(self.tmp.name)._load(pd.COUNTERS, {})["stars"]["value"], 42)
        for bad in ({"name": "a b", "value": 1}, {"name": "x"}, {"name": "x", "value": "nan"}, {"name": "x", "value": "lots"}):
            with self.assertRaises(ValueError):
                pd.set_counter(self.store, bad.pop("name"), **bad)

    def test_digits_roll_through_the_ones_between(self):
        pd.set_counter(self.store, "n", 1289)
        a = self.app("counter", name="n", label="", roll_seconds=1.0)
        start = self.draw(a, 100.0)
        pd.set_counter(self.store, "n", 1292)
        self.draw(a, 100.1)  # notices the change
        seen = set()
        for i in range(1, 30):
            c = self.draw(a, 100.1 + i * 0.05)
            seen.add(tuple(c.rows()))
        end = self.draw(a, 105)
        self.assertNotEqual(end.rows(), start.rows())
        self.assertGreater(len(seen), 5, "in-between frames while rolling")
        ref = self.app("counter", name="n", label="")
        self.assertEqual(self.draw(ref, 200).rows(), end.rows())

    def test_number_sources(self):
        self.assertEqual(pd.number_from('{"data": [{"count": "1,204"}]}', "data.0.count"), 1204)
        self.assertEqual(pd.number_from('{"state": "17.5", "attributes": {}}'), 17.5)
        self.assertEqual(pd.number_from(b"42"), 42)
        with self.assertRaises(ValueError):
            pd.number_from("unavailable")

    def test_mqtt_topic(self):
        self.store.settings["mqtt"] = {"enabled": True, "host": "127.0.0.1", "port": 1883, "prefix": "flipdot"}
        made = []

        class FakeClient:
            def __init__(self, cfg, on_message, subscribe=(), client_id=None, **kw):
                made.append((subscribe, client_id))
                self.on_message = on_message

            def close(self):
                made.append("closed")
        with mock.patch("mqtt.MqttClient", FakeClient):
            a = self.app("counter", source="mqtt", topic="homeassistant/sensor/visitors/state", label="Visitors")
            self.draw(a, 10)
            client = pd._mqtt_subs[a._mqtt[0]]["client"]
            client.on_message("homeassistant/sensor/visitors/state", "1234")
            client.on_message("flipdot/notify", "999")  # not ours
            c = self.draw(a, 11)
            self.assertEqual(a.value, 1234)
            self.assertGreater(lit(c), 100)
            a.stop()
        self.assertEqual(made[0][0], ["homeassistant/sensor/visitors/state"])
        self.assertTrue(made[0][1].startswith("flipdot-counter-"))
        self.assertEqual(made[-1], "closed")

    def test_needs_setup(self):
        self.assertEqual(apps.base.setup_needed(self.store, {"app": "counter", "params": {"source": "json"}}), "a JSON URL")
        self.assertIn("MQTT", apps.base.setup_needed(self.store, {"app": "counter", "params": {"source": "mqtt", "topic": "x"}}))


class Station(Base):
    def setUp(self):
        super().setUp()
        tle = fixture("wheretheiss_tle.json")
        self.el = pd.parse_tle(tle["line1"], tle["line2"])

    def test_orbit_matches_recorded_positions(self):
        for p in fixture("wheretheiss_positions.json") + [fixture("wheretheiss_position.json")]:
            lat, lon = pd.subpoint(self.el, p["timestamp"])
            self.assertLess(abs(lat - p["latitude"]), 2.0)
            self.assertLess(abs((lon - p["longitude"] + 180) % 360 - 180), 2.5)

    def test_next_pass(self):
        t0 = fixture("wheretheiss_position.json")["timestamp"]
        p = pd.next_pass(self.el, 47.61, -122.33, t0)
        self.assertTrue(t0 < p["rise"] < p["top"] < p["set"] < t0 + 36 * 3600)
        self.assertLess(p["set"] - p["rise"], 900)
        self.assertGreaterEqual(p["max_el"], 10)
        obs = pd.observer(47.61, -122.33)
        self.assertAlmostEqual(pd.look(obs, pd.ecef(self.el, p["rise"]))[0], 10, delta=0.5)

    def test_map_and_alert(self):
        self.store.settings["weather"] = {"lat": 47.61, "lon": -122.33}
        t0 = fixture("wheretheiss_position.json")["timestamp"]
        with mock.patch.object(pd.time, "time", return_value=t0):
            a = self.app("iss", view="map")
            c = self.draw(a, t0)
        land = Canvas(84, 42)
        land.px[:] = pd.world_map(84, 42)
        self.assertNotEqual(c.rows(), land.rows())  # track and station on top
        x, y = pd.map_xy(28.17, -117.06, 84, 42)
        self.assertEqual(c.get(x, y), 1)
        self.assertEqual(c.get(x, y - 2), 0)  # the dark margin
        p = a.passes[1]
        alert = Canvas(84, 42)
        a.render(alert, p["rise"] - 240)
        self.assertGreater(lit(alert), 100)
        self.assertNotEqual(alert.rows(), c.rows())

    def test_offline(self):
        self.net.offline = True
        a = self.app("iss")
        c = self.draw(a, 1791671958)
        self.assertGreater(lit(c), 500)  # the map with a quiet OFFLINE label


class Speed(Base):
    def test_frames_are_cheap(self):
        pd.set_counter(self.store, "n", 1)
        specs = [("daily", {"channel": "litclock"}), ("daily", {"channel": "quote", "style": "scroll"}),
                 ("counter", {"name": "n"}), ("launches", {}), ("iss", {})]
        for app_id, params in specs:
            a = self.app(app_id, **params)
            self.draw(a, NET - 3600)
            start = time.perf_counter()
            for i in range(20):
                a.render(Canvas(84, 42), NET - 3600 + i * 0.37)
            ms = (time.perf_counter() - start) / 20 * 1000
            self.assertLess(ms, 5, f"{app_id} {params}: {ms:.2f} ms a frame")


if __name__ == "__main__":
    unittest.main()
