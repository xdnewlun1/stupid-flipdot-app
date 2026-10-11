"""Home and data widgets (apps/pack_home.py): habits and streaks, the entity board, packages, the offline word of
the day and on this day, and network status. No network: http_get, fetch_json and the probes are replaced.
Run: python3 -m pytest -q tests/test_home.py"""

import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
from apps import pack_daily, pack_home as ph  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402
from store import Store  # noqa: E402


def local(y, mo, d, h=12, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def lit(c):
    return sum(c.px)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.ctx = Context(84, 42, self.store)
        self.calls = []
        for mod in (ph, pack_daily):  # nothing may reach the network
            p = mock.patch.object(mod, "http_get", self.offline)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(pack_daily, "fetch_json", self.offline)
        p.start()
        self.addCleanup(p.stop)

    def offline(self, url, *a, **k):
        self.calls.append(url)
        raise OSError("no network in tests")

    def app(self, app_id, **params):
        a = apps.create(self.ctx, {"app": app_id, "params": params})
        self.addCleanup(a.stop)
        return a

    def draw(self, a, now):
        c = Canvas(84, 42)
        a.render(c, now)
        return c


class HabitsTest(Base):
    def test_streaks(self):
        d = dt.date(2026, 10, 11)
        iso = lambda *ks: {str(d - dt.timedelta(days=k)) for k in ks}
        self.assertEqual(ph.streaks(set(), d), (0, 0))
        self.assertEqual(ph.streaks(iso(0, 1, 2), d), (3, 3))
        self.assertEqual(ph.streaks(iso(1, 2), d), (2, 2))  # today not ticked yet: the streak still counts
        self.assertEqual(ph.streaks(iso(2, 3), d), (0, 2))  # a missed day breaks it
        self.assertEqual(ph.streaks(iso(0, 5, 6, 7, 8), d), (1, 4))

    def test_ticks_are_saved_and_survive_a_restart(self):
        ph.set_habit(self.store, "Run", day="2026-10-10")
        ph.set_habit(self.store, "Run", day="2026-10-11")
        ph.set_habit(self.store, "Read", day="2026-10-11")
        ph.set_habit(self.store, "Read", done=False, day="2026-10-11")
        again = Store(self.tmp.name)
        self.assertEqual(ph.habits(again)["habits"]["Run"]["days"], ["2026-10-10", "2026-10-11"])
        self.assertEqual(ph.habits(again)["habits"]["Read"]["days"], [])
        s = {h["name"]: h for h in ph.habit_summary(again, dt.date(2026, 10, 11))}
        self.assertEqual((s["Run"]["streak"], s["Run"]["done_today"]), (2, True))
        with self.assertRaises(ValueError):
            ph.set_habit(self.store, "bad/name")
        with self.assertRaises(ValueError):
            ph.set_habit(self.store, "Run", day="yesterday")
        self.assertTrue(ph.remove_habit(self.store, "Read"))
        self.assertEqual(ph.habit_names(self.store), ["Run"])

    def test_a_button_ticks_today(self):
        a = self.app("habits")
        empty = self.draw(a, time.time())
        self.assertGreater(lit(empty), 10)  # the "no habits yet" hint
        ph.set_habit(self.store, "Stretch", day="2020-01-01")
        a.input("a")
        self.assertIn(ph.today_str(), ph.habits(self.store)["habits"]["Stretch"]["days"])
        a.input("a")  # again: untick
        self.assertNotIn(ph.today_str(), ph.habits(self.store)["habits"]["Stretch"]["days"])
        a.input("a", "up")
        self.assertNotIn(ph.today_str(), ph.habits(self.store)["habits"]["Stretch"]["days"])

    def test_grid_shows_one_dot_block_per_day(self):
        now = local(2026, 10, 11)
        for k in range(10):
            ph.set_habit(self.store, "Run", day=str(dt.date(2026, 10, 11) - dt.timedelta(days=k)))
        few = self.draw(self.app("habits", weekday_letters=False), now)
        ph.set_habit(self.store, "Run", day="2026-09-01")
        more = self.draw(self.app("habits", weekday_letters=False), now)
        self.assertGreater(lit(more), lit(few))
        rows = self.draw(self.app("habits", mode="all"), now)
        self.assertGreater(lit(rows), 20)


class EntitiesTest(Base):
    def test_parsing_icons_and_values(self):
        ents = ph.parse_entity_lines("sensor.kitchen_temp | Kitchen | °C\n# a comment\n\nbinary_sensor.front_door\n"
                                     "sensor.car | Car | % | attributes.battery")
        self.assertEqual([e["label"] for e in ents], ["Kitchen", "front door", "Car"])
        self.assertEqual(ents[2]["path"], "attributes.battery")
        self.assertEqual(len(ph.parse_entity_lines("\n".join(f"s.{i}" for i in range(9)))), 6)
        self.assertEqual(ph.icon_for("temperature"), "temperature")
        self.assertEqual(ph.icon_for("", "binary_sensor.front_door"), "door")
        self.assertEqual(ph.icon_for("", "sensor.tesla", "Car charge"), "battery")
        self.assertIsNone(ph.icon_for("", "sensor.xyz", "Thing"))
        self.assertEqual(ph.format_value("21.456", "°C"), ("21.5", "°C"))
        self.assertEqual(ph.format_value("off"), ("OFF", ""))
        self.assertEqual(ph.format_value("unavailable"), ("--", ""))
        self.assertEqual(ph.format_value("1234", "W"), ("1234", "W"))
        self.assertEqual(ph.format_value(None), ("--", ""))
        doc = {"state": "on", "attributes": {"device_class": "door", "friendly_name": "Front", "battery": 80}}
        self.assertEqual(ph.ha_state_value(doc, {"path": ""})["class"], "door")
        self.assertEqual(ph.ha_state_value(doc, {"path": "attributes.battery"})["value"], 80)

    def test_home_assistant_fetch_and_draw(self):
        states = {"sensor.t": {"state": "20.5", "attributes": {"unit_of_measurement": "°C", "device_class": "temperature"}},
                  "binary_sensor.door": {"state": "off", "attributes": {"device_class": "door"}}}
        seen = []

        def fake(url, ttl=60, headers=None, **k):
            seen.append((url, headers))
            return states[url.rsplit("/", 1)[1]]
        a = self.app("entities", url="http://ha.test:8123/", token="tok", entities="sensor.t | Lounge\nbinary_sensor.door | Door")
        with mock.patch.object(ph, "http_get", fake):
            a.data = a.fetch()
        self.assertEqual(seen[0], ("http://ha.test:8123/api/states/sensor.t",
                                   {"Authorization": "Bearer tok", "Content-Type": "application/json"}))
        self.assertEqual([v["value"] for v in a.data], ["20.5", "off"])
        self.assertGreater(lit(self.draw(a, time.time())), 40)

    def test_setup_and_json_source(self):
        self.assertIn("token", apps.base.setup_needed(self.store, {"app": "entities", "params": {}}))
        self.assertIn("JSON", apps.base.setup_needed(self.store, {"app": "entities", "params": {"source": "json"}}))
        a = self.app("entities", source="json", json_url="http://x.test/d.json", entities="a | A | | temps.inside\nb | B")
        with mock.patch.object(ph, "http_get", lambda *x, **k: {"temps": {"inside": 19}, "b": "open"}):
            a.data = a.fetch()
        self.assertEqual([v["value"] for v in a.data], [19, "open"])


class PackagesTest(Base):
    def test_list_status_and_delivery(self):
        p = ph.add_package(self.store, "Boots", "1Z 999", "ups")
        self.assertEqual(ph.add_package(self.store, "", "1Z 999")["id"], p["id"])  # same number: the same entry
        ph.add_package(self.store, "Book", "AB123")
        with self.assertRaises(ValueError):
            ph.add_package(self.store, "x", "")
        self.assertEqual(len(ph.packages(Store(self.tmp.name))["packages"]), 2)
        ph.set_package_status(self.store, p["id"], "Out for delivery", "Oct 14")
        self.assertFalse(ph.packages(self.store)["packages"][0]["delivered"])
        ph.set_package_status(self.store, "1Z 999", "Delivered, front porch")
        self.assertTrue(ph.packages(self.store)["packages"][0]["delivered"])
        self.assertTrue(ph.remove_package(self.store, "AB123"))
        with self.assertRaises(KeyError):
            ph.set_package_status(self.store, 99, "x")

    def test_carrier_template_fetch_and_fallback(self):
        cars = ph.parse_carrier_lines('ups | https://t.test/{number} | events.0.text | eta | {"X-Key": "k"}\nbad line')
        self.assertEqual(set(cars), {"ups"})
        self.assertEqual(cars["ups"]["headers"], {"X-Key": "k"})
        self.assertEqual(ph.shape_status({"events": [{"text": "In transit"}], "eta": "2026-10-14T18:00:00Z"}, cars["ups"]),
                         ("In transit", "Oct 14"))
        ph.add_package(self.store, "Boots", "1Z999", "ups")
        ph.add_package(self.store, "Mystery", "ZZ000", "other")
        a = self.app("packages", carriers='ups | https://t.test/{number} | events.0.text | eta')
        urls = []

        def net(url, headers=None, timeout=10):
            urls.append(url)
            return {"events": [{"text": "In transit"}], "eta": "2026-10-14"}
        with mock.patch.object(pack_daily, "fetch_json", net):
            self.assertEqual(a.fetch(), ["1Z999"])
        self.assertEqual(urls, ["https://t.test/1Z999"])
        ps = {p["number"]: p for p in ph.packages(self.store)["packages"]}
        self.assertEqual((ps["1Z999"]["status"], ps["1Z999"]["eta"]), ("In transit", "Oct 14"))
        self.assertEqual(ps["ZZ000"]["status"], "")
        self.assertGreater(lit(self.draw(a, time.time())), 40)  # both listed, the second with "no status"
        a.fetch()  # offline now: no crash, statuses kept
        self.assertEqual(ph.packages(self.store)["packages"][0]["status"], "In transit")

    def test_delivered_ones_hide_after_a_while(self):
        p = ph.add_package(self.store, "Old", "OLD1")
        ph.set_package_status(self.store, p["id"], "Delivered", delivered=True)
        a = self.app("packages", hide_delivered_days=1)
        self.assertEqual(len(a.visible(time.time())), 1)
        self.assertEqual(len(a.visible(time.time() + 2 * 86400)), 0)


class DailyTest(Base):
    def test_data_files(self):
        words = ph.all_words()
        self.assertGreater(len(words), 300)
        self.assertEqual(len({w[0] for w in words}), len(words))  # no word twice
        evs = pack_daily.bundled("onthisday.json")["events"]
        for y in (2024, 2026):  # every day of the year has something, leap day included
            d = dt.date(y, 1, 1)
            while d.year == y:
                self.assertTrue(ph.events_for(d, evs), d)
                d += dt.timedelta(days=1)
        self.assertTrue(all(isinstance(e[3], str) and e[3].isascii() for e in evs))
        self.assertEqual(ph.years_ago(1969, dt.date(2026, 7, 20)), "57 years ago")
        self.assertEqual(ph.years_ago("44 BC", dt.date(2026, 3, 15)), "44 BC")

    def test_apps_page_and_change_daily(self):
        w = self.app("wordofday")
        _, a = w.today(local(2026, 10, 11))
        _, b = w.today(local(2026, 10, 12))
        self.assertNotEqual(a, b)
        for mode in ("word", "quiz"):
            w = self.app("wordofday", mode=mode)
            pics = {bytes(self.draw(w, local(2026, 10, 11) + k * 8).px) for k in range(4)}
            self.assertGreater(len(pics), 1, mode)  # the word and its meaning take turns
        o = self.app("onthisday")
        c = self.draw(o, local(2026, 7, 20))
        self.assertGreater(lit(c), 40)
        self.assertEqual(self.calls, [])  # offline by design


class NetStatTest(Base):
    def test_ping_parsing_and_dashboard(self):
        ok = mock.Mock(returncode=0, stdout="64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.3 ms\n")
        with mock.patch.object(ph.subprocess, "run", return_value=ok) as run:
            self.assertEqual(ph.probe_host("1.1.1.1"), 12.3)
            self.assertEqual(run.call_args[0][0][:2], ["ping", "-n"])
        lost = mock.Mock(returncode=1, stdout="1 packets transmitted, 0 received\n")
        with mock.patch.object(ph.subprocess, "run", return_value=lost):
            self.assertIsNone(ph.probe_host("10.9.9.9"))
        with mock.patch.object(ph.socket, "create_connection", side_effect=OSError):
            self.assertIsNone(ph.probe_host("x.test", "tcp", 443))
        self.assertEqual(ph.uptime_text(90061), "1d1h")
        self.assertEqual(ph.uptime_text(3720), "1h02")
        sysinfo = ph.read_sys()
        self.assertEqual(set(sysinfo), {"temp", "load", "up", "mem"})

        with mock.patch.object(ph, "gateway", return_value="192.0.2.1"):
            a = self.app("netstat", hosts="gateway, example.test")
        self.assertEqual([h["addr"] for h in a.hosts], ["192.0.2.1", "example.test"])
        rtts = iter([3.0, None, 4.0, 40.0] * 10)
        with mock.patch.object(ph, "probe_host", lambda *x, **k: next(rtts)), \
                mock.patch.object(ph, "read_sys", return_value={"temp": 52.1, "load": 0.3, "up": 4000, "mem": 0.4}):
            for _ in range(8):
                a.data = a.fetch()
        self.assertEqual(len(a.hosts[0]["hist"]), 8)
        self.assertIn(None, a.hosts[1]["hist"])
        self.assertGreater(lit(self.draw(a, time.time())), 40)


if __name__ == "__main__":
    unittest.main()
