"""The notification queue: ordering, preemption, do-not-disturb, de-duplication, key/replace, the runner's side,
the ntfy stream (against a local fake server) and the banner/icon frames.
Run: python3 -m pytest -q tests/test_notifications.py"""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps  # noqa: E402
import flipdot  # noqa: E402
import notifications as N  # noqa: E402
import ntfy  # noqa: E402
import runner as runner_mod  # noqa: E402
from apps.notice import ICONS, draw_banner  # noqa: E402
from canvas import Canvas  # noqa: E402
from store import Store  # noqa: E402
from test_board import config  # noqa: E402


class FakeStore:
    def __init__(self, **cfg):
        self.settings = {"notifications": cfg, "quiet_hours": {"enabled": False}}


def mk(text, prio="normal", **kw):
    return N.entry({"app": "text", "params": {"text": text}}, priority=prio, now=kw.pop("now", 0), **kw)


class CenterTest(unittest.TestCase):
    def setUp(self):
        self.c = N.Center(FakeStore())

    def test_nothing_on_shows_now(self):
        self.assertEqual(self.c.offer(mk("a"), 0), ("show", None))

    def test_queues_behind_current_unless_higher(self):
        cur = mk("on")
        self.assertEqual(self.c.offer(mk("n1"), 0, current=cur)[0], "queued")
        self.assertEqual(self.c.offer(mk("low", "low"), 0, current=cur)[0], "queued")
        hi = mk("hi", "high")
        self.assertEqual(self.c.offer(hi, 0, current=cur), ("show", cur))
        self.assertEqual(self.c.offer(mk("n2"), 0, current=hi)[0], "queued")
        self.assertEqual(self.c.offer(mk("h2", "high"), 0, current=hi)[0], "queued")
        # waiting: highest priority first, then oldest first
        self.assertEqual([e["text"] for e in self.c.pending], ["h2", "n1", "n2", "low"])

    def test_critical_interrupts_anything_even_a_game_and_another_critical(self):
        crit = mk("fire", "critical")
        self.assertEqual(self.c.offer(crit, 0, busy="game"), ("show", None))
        crit2 = mk("flood", "critical")
        self.assertEqual(self.c.offer(crit2, 0, current=crit, busy="game"), ("show", crit))

    def test_displaced_comes_back_first_with_its_time_left(self):
        cur = mk("on")
        self.c.offer(mk("waiting"), 0, current=cur)
        self.assertTrue(self.c.requeue(cur, 6.0))
        how, e = self.c.next_ready()
        self.assertEqual((how, e["text"], e["left"]), ("show", "on", 6.0))
        self.assertFalse(self.c.requeue(mk("nearly done"), 1.0), "under 2 s left: not worth coming back")

    def test_games_hold_low_and_normal_back(self):
        self.assertEqual(self.c.offer(mk("n"), 0, busy="game")[0], "queued")
        self.assertEqual(self.c.offer(mk("l", "low"), 0, busy="game")[0], "queued")
        self.assertIsNone(self.c.next_ready(busy="game"))
        self.assertEqual(self.c.next_ready()[1]["text"], "n", "shown once the game ends")

    def test_high_shows_as_a_banner_over_games(self):
        self.assertEqual(self.c.offer(mk("timer", "high"), 0, busy="game")[0], "banner")

    def test_banner_setting_lets_normal_show_over_games(self):
        c = N.Center(FakeStore(banner_over_games=True))
        self.assertEqual(c.offer(mk("n"), 0, busy="game")[0], "banner")
        b = mk("b")
        self.assertEqual(c.offer(mk("n2"), 0, busy="game", banner=b)[0], "queued", "one banner at a time")
        self.assertEqual(c.next_ready(busy="game")[0], "banner")

    def test_live_stream_holds_back_below_high(self):
        self.assertEqual(self.c.offer(mk("n"), 0, busy="live")[0], "queued")
        self.assertEqual(self.c.offer(mk("h", "high"), 0, busy="live")[0], "show")

    def test_dedupe_within_window(self):
        self.assertEqual(self.c.offer(mk("ding", now=0), 0)[0], "show")
        self.assertEqual(self.c.offer(mk("ding", now=10), 10)[0], "duplicate")
        self.assertEqual(self.c.offer(mk("dong", now=10), 10)[0], "show")
        self.assertEqual(self.c.offer(mk("ding", now=70), 70)[0], "show", "the window is 60 s")
        cur = mk("x")
        self.c.offer(mk("queued one"), 100, current=cur)
        self.assertEqual(self.c.offer(mk("queued one"), 500, current=cur)[0], "duplicate", "same as one waiting")
        self.assertEqual(self.c.offer(mk("ding", dedupe=False), 71)[0], "queued", "not a duplicate: waits its turn")

    def test_key_replaces_instead_of_queueing(self):
        cur = mk("door 1", key="door")
        self.assertEqual(self.c.offer(mk("door 2", key="door"), 0, current=cur), ("replace", "current"))
        other = mk("other")
        self.c.offer(mk("score 1-0", key="score"), 0, current=other)
        self.c.offer(mk("later"), 0, current=other)
        self.assertEqual(self.c.offer(mk("score 2-0", key="score"), 0, current=other), ("replace", "pending"))
        self.assertEqual([e["text"] for e in self.c.pending], ["score 2-0", "later"], "keeps its place")
        self.assertEqual(self.c.offer(mk("b2", key="b"), 0, banner=mk("b1", key="b")), ("replace", "banner"))

    def test_only_idle_never_waits_and_gives_way(self):
        mirror = N.entry({"app": "mirror"}, priority="low", only_idle=True, dedupe=False)
        self.assertEqual(self.c.offer(mirror, 0, current=mk("x"))[0], "busy")
        self.assertEqual(self.c.offer(mirror, 0)[0], "show")
        low = mk("low one", "low")
        self.assertEqual(self.c.offer(low, 0, current=mirror), ("show", mirror), "anything outranks the mirror")
        self.assertFalse(self.c.requeue(mirror, 30), "and it doesn't come back")

    def test_dnd_holds_then_summarises(self):
        self.assertEqual(self.c.offer(mk("a"), 0, dnd=True)[0], "held")
        self.assertEqual(self.c.offer(mk("b", "low"), 0, dnd=True)[0], "held")
        self.assertEqual(self.c.offer(mk("c"), 0, dnd=True)[0], "held")
        hi = mk("h", "high", sound="clatter")
        self.assertEqual(self.c.offer(hi, 0, dnd=True)[0], "show")
        self.assertEqual(hi["sound"], "none", "high shows silently")
        crit = mk("k", "critical", sound="sweep")
        self.assertEqual(self.c.offer(crit, 0, current=hi, dnd=True)[0], "show")
        self.assertEqual(crit["sound"], "sweep")
        out = self.c.dnd_ended(100)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["spec"]["params"]["title"], "3 notifications")
        self.assertEqual({e["text"] for e in self.c.history if e.get("state") == "held"}, {"a", "b", "c"})
        self.assertEqual(self.c.held, [])

    def test_dnd_single_held_comes_back_as_itself(self):
        self.c.offer(mk("only"), 0, dnd=True)
        out = self.c.dnd_ended(100)
        self.assertEqual([e["text"] for e in out], ["only"])

    def test_dnd_drop_setting(self):
        c = N.Center(FakeStore(dnd={"during": "drop"}))
        self.assertEqual(c.offer(mk("a"), 0, dnd=True)[0], "dropped")
        self.assertEqual(c.held, [])
        self.assertEqual(c.history[0]["state"], "dropped")

    def test_dnd_window(self):
        st = FakeStore(dnd={"mode": "custom", "start": "22:00", "end": "07:00"})
        self.assertTrue(N.dnd_active(st, dt.datetime(2026, 1, 1, 23, 0)))
        self.assertTrue(N.dnd_active(st, dt.datetime(2026, 1, 1, 6, 59)))
        self.assertFalse(N.dnd_active(st, dt.datetime(2026, 1, 1, 12, 0)))
        quiet = FakeStore()
        quiet.settings["quiet_hours"] = {"enabled": True, "start": "01:00", "end": "02:00"}
        self.assertTrue(N.dnd_active(quiet, dt.datetime(2026, 1, 1, 1, 30)), "follows quiet hours by default")
        self.assertFalse(N.dnd_active(FakeStore(dnd={"mode": "off"}), dt.datetime(2026, 1, 1, 1, 30)))
        self.assertTrue(N.dnd_active(FakeStore(dnd={"now": True})))

    def test_priority_names_and_numbers(self):
        self.assertEqual([N.priority(n) for n in (1, 2, 3, 4, 5)], ["low", "low", "normal", "high", "critical"])
        self.assertEqual(N.priority(None), "normal")
        self.assertEqual(N.priority("urgent"), "critical")
        with self.assertRaises(ValueError):
            N.priority("loud")

    def test_history_keeps_twenty(self):
        for i in range(30):
            e = mk(f"m{i}")
            self.c.shown(e, i)
        self.assertEqual(len(self.c.history), 20)
        self.assertEqual(self.c.history[0]["text"], "m29")


def wait_for(check, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if check():
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
        self.runner.clear_notifications()
        self.tmp.cleanup()

    def note(self, text, **kw):
        kw.setdefault("transition", "none")
        return self.runner.notify(spec={"app": "text", "params": {"text": text, "style": "static"}}, **kw)

    def showing(self):
        n = self.runner.notif
        return n and n.get("entry") and n["entry"]["text"]

    def test_second_waits_then_shows(self):
        self.assertEqual(self.note("one", duration=0.4)["status"], "show")
        self.assertEqual(self.note("two", duration=0.4)["status"], "queued")
        self.assertEqual(self.showing(), "one")
        self.assertTrue(wait_for(lambda: self.showing() == "two"))
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        self.assertEqual([h["text"] for h in self.runner.notifications_status()["history"]], ["two", "one"])

    def test_game_then_normal_waits_then_critical_interrupts(self):
        self.runner.run({"app": "snake", "params": {}})
        self.assertTrue(self.runner.app.interactive)
        self.assertEqual(self.note("normal")["status"], "queued")
        self.assertIsNone(self.runner.notif)
        self.assertEqual(self.note("fire", priority="critical", duration=0.3)["status"], "show")
        self.assertEqual(self.showing(), "fire")
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        self.assertEqual(self.runner.app.id, "snake", "the game is still there afterwards")
        self.assertEqual(len(self.runner.notify_queue.pending), 1, "the normal one still waits for the game")
        self.runner.stop()
        self.assertTrue(wait_for(lambda: self.showing() == "normal"), "shown when the game ends")

    def test_banner_over_game(self):
        self.store.settings["notifications"] = {"banner_over_games": True}
        self.runner.run({"app": "snake", "params": {}})
        self.assertEqual(self.note("Pizza is here", duration=0.5)["status"], "banner")
        self.assertIsNone(self.runner.notif)
        self.assertTrue(wait_for(lambda: self.board.rows()[-11] == "1" * self.board.width), "the banner's rule")
        self.assertTrue(wait_for(lambda: self.runner.banner is None))

    def test_hold_until_dismissed_and_button(self):
        self.note("stays", hold=True, duration=0.1)
        time.sleep(0.3)
        self.assertEqual(self.showing(), "stays")
        self.runner.input("a", "down")
        self.assertTrue(wait_for(lambda: self.runner.notif is None))

    def test_key_replaces_on_board(self):
        self.note("score 1-0", key="score", duration=5)
        first = self.runner.notif["app"]
        self.assertEqual(self.note("score 2-0", key="score", duration=5)["status"], "replace")
        self.assertEqual(self.showing(), "score 2-0")
        self.assertIsNot(self.runner.notif["app"], first)
        self.assertEqual(self.runner.notify_queue.pending, [])

    def test_dismiss_clear_and_reshow(self):
        self.note("a", duration=5)
        self.note("b", duration=5)
        self.note("c", duration=5)
        st = self.runner.notifications_status()
        self.assertEqual([e["text"] for e in st["pending"]], ["b", "c"])
        self.assertTrue(self.runner.dismiss(st["pending"][0]["id"]))
        self.assertEqual([e["text"] for e in self.runner.notifications_status()["pending"]], ["c"])
        self.assertEqual(self.runner.clear_notifications(), 2)
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        hid = self.runner.notifications_status()["history"][0]["id"]
        self.assertEqual(self.runner.reshow(hid)["status"], "show")

    def test_sound_pattern_then_content(self):
        self.board.fill(False)
        self.note("TICK", sound="clatter", duration=2)
        app = self.runner.notif["app"]
        self.assertIsInstance(app, N.Attention)
        c = Canvas(84, 42)
        app.render(c, app.started + 0.1)
        self.assertGreater(sum(c.px), 300, "the clatter flips discs")
        c2 = Canvas(84, 42)
        app.render(c2, app.started + 1.0)
        self.assertEqual(app.request_transition, None, "transition 'none' asks for nothing")

    def test_mirror_only_when_idle(self):
        self.note("busy", duration=5)
        r = self.runner.notify(spec={"app": "mirror", "params": {}}, priority="low", only_idle=True, duration=5)
        self.assertEqual(r["status"], "busy")
        self.assertEqual(self.runner.notify_queue.pending, [])

    def test_legacy_call_still_works(self):
        self.runner.notify(spec={"app": "text", "params": {"text": "hi"}}, duration=0.2, transition="none")
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        self.assertTrue(self.runner.status()["notifications"] is not None)


class FakeNtfy(BaseHTTPRequestHandler):
    events = []
    paths = []
    drop_first = True

    def do_GET(self):
        FakeNtfy.paths.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(b'{"id":"o1","event":"open","topic":"door"}\n')
        if FakeNtfy.drop_first:
            FakeNtfy.drop_first = False
            self.wfile.write(json.dumps(FakeNtfy.events[0]).encode() + b"\n")
            self.wfile.flush()
            return  # connection closes: the client has to come back
        for ev in FakeNtfy.events[1:]:
            self.wfile.write(b'{"event":"keepalive"}\n')
            self.wfile.write(json.dumps(ev).encode() + b"\n")
            self.wfile.flush()
        time.sleep(1.5)

    def log_message(self, *a):
        pass


class NtfyTest(unittest.TestCase):
    def test_stream_maps_and_reconnects(self):
        FakeNtfy.events = [
            {"id": "m1", "event": "message", "topic": "door", "message": "Doorbell", "priority": 4, "tags": ["door"]},
            {"id": "m2", "event": "message", "topic": "door", "title": "Backup", "message": "failed",
             "priority": 5, "tags": ["warning", "skull"]},
            {"id": "m3", "event": "message", "topic": "door", "message": "fyi", "priority": 2},
        ]
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeNtfy)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        got = []
        sub = ntfy.Subscriber(f"http://127.0.0.1:{srv.server_address[1]}", "door, garage", got.append,
                              min_backoff=0.05).start()
        try:
            self.assertTrue(wait_for(lambda: len(got) >= 3, 5))
        finally:
            sub.stop()
            srv.shutdown()
        self.assertEqual(got[0], {"text": "Doorbell", "priority": "high", "source": "ntfy", "duration": 10,
                                  "icon": "door"})
        self.assertEqual((got[1]["title"], got[1]["text"], got[1]["priority"], got[1]["icon"]),
                         ("Backup", "failed", "critical", "alert"))
        self.assertEqual(got[2]["priority"], "low")
        self.assertNotIn("icon", got[2])
        self.assertTrue(FakeNtfy.paths[0].startswith("/door,garage/json"))
        self.assertIn("since=m1", FakeNtfy.paths[1], "comes back asking for what it missed")
        self.assertGreaterEqual(sub.connects, 2)

    def test_topic_names(self):
        self.assertEqual(ntfy.topics_of("a, b c,a"), ["a", "b", "c"])
        with self.assertRaises(ValueError):
            ntfy.topics_of(["../etc"])


class FramesTest(unittest.TestCase):
    def test_icons_are_9x9(self):
        self.assertEqual(len(ICONS), 9)
        for name, rows in ICONS.items():
            self.assertEqual(len(rows), 9, name)
            self.assertTrue(all(len(r) == 9 for r in rows), name)

    def test_banner_and_notice_render_fast(self):
        from runner import Context
        c = Canvas(84, 42)
        t = time.perf_counter()
        for i in range(50):
            draw_banner(c, "Timer pasta done, take it off the heat", "timer", now=i * 0.05)
        self.assertLess((time.perf_counter() - t) / 50, 0.005)
        app = apps.create(Context(84, 42, None), {"app": "notice", "params": {"text": "Someone is at the door",
                                                                               "icon": "door"}})
        c = Canvas(84, 42)
        app.render(c, time.time())
        self.assertGreater(sum(c.px), 50)


if __name__ == "__main__":
    unittest.main()
