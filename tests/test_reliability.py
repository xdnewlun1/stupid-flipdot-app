"""The runner loop surviving failures, /api/health and the systemd notify/watchdog pings.
Run: python3 -m unittest tests.test_reliability"""

import logging
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flipdot  # noqa: E402
import health  # noqa: E402
import runner as runner_mod  # noqa: E402
from store import Store  # noqa: E402
from test_board import config  # noqa: E402


def wait_for(check, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = flipdot.Board(config(), dry_run=True)
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.runner = runner_mod.Runner(self.board, self.store)
        logging.disable(logging.CRITICAL)  # the failures below are on purpose

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self.runner.stop()
        self.tmp.cleanup()

    def ticks_on(self, n=2):
        """The loop keeps ticking: at least every BEAT seconds, even while an app waits longer between frames."""
        start = self.runner.beats
        return wait_for(lambda: self.runner.beats >= start + n, timeout=n * runner_mod.BEAT + 1)


class BrokenTransition:
    def __init__(self):
        self.calls = 0

    def mix(self, *a):
        self.calls += 1
        raise RuntimeError("mix broke")

    def done(self, now):
        return False


class RunnerSurvivesTest(Fixture):
    def test_failing_transition_is_dropped_and_the_picture_shown(self):
        self.runner.run({"app": "blank", "params": {"color": "white"}})
        self.assertTrue(wait_for(lambda: "0" not in "".join(self.board.rows())))
        broken = BrokenTransition()
        with self.runner.lock:
            self.runner.run({"app": "blank", "params": {"color": "black"}})
            self.runner.transition = broken
        self.assertTrue(wait_for(lambda: "1" not in "".join(self.board.rows())))  # the incoming picture, unmixed
        self.assertEqual(broken.calls, 1)
        self.assertIsNone(self.runner.transition)
        self.assertTrue(self.ticks_on())
        self.assertGreaterEqual(self.runner.heartbeat()["errors_5min"], 1)

    def test_failing_send_is_retried_next_tick(self):
        real, fails = self.board.set_canvas, [0]

        def flaky(canvas, present_at=None):
            if fails[0] < 3:
                fails[0] += 1
                raise OSError("framebuffer gone")
            return real(canvas, present_at)
        self.board.set_canvas = flaky
        self.runner.run({"app": "blank", "params": {"color": "white"}})
        self.assertTrue(wait_for(lambda: "0" not in "".join(self.board.rows())))
        self.assertEqual(fails[0], 3)
        self.assertTrue(self.ticks_on())

    def test_failing_send_forever_keeps_ticking(self):
        def broken(canvas, present_at=None):
            raise ValueError("canvas must be 84x42")
        self.board.set_canvas = broken
        self.runner.run({"app": "clock"})
        self.assertTrue(self.ticks_on(5))
        hb = self.runner.heartbeat()
        self.assertLess(hb["age_s"], 1)
        self.assertGreaterEqual(hb["errors_5min"], 3)

    def test_failing_notification_end_is_cleared(self):
        self.runner.notify(spec={"app": "text", "params": {"text": "hi"}}, duration=0.2, transition="none")
        def broken():
            raise RuntimeError("end broke")
        self.runner._end_notification = broken
        self.assertTrue(wait_for(lambda: self.runner.notif is None))
        self.assertTrue(self.ticks_on())
        self.assertFalse(self.runner.status()["notification"])

    def test_loop_restarts_when_something_else_escapes(self):
        def boom():
            raise RuntimeError("check broke")
        self.runner._check_problem = boom  # caught by its own step
        self.assertTrue(self.ticks_on())
        self.assertEqual(self.runner.restarts, 0)

        class Weird:  # comparing it with the time fails outside every step's own guard
            pass
        with self.runner.lock:
            self.runner.notif = {"app": None, "until": Weird(), "restore": None}
        self.assertTrue(wait_for(lambda: self.runner.restarts >= 1))
        with self.runner.lock:
            self.runner.notif = None
        self.assertTrue(self.ticks_on())

    def test_errors_are_logged_rate_limited(self):
        logging.disable(logging.NOTSET)
        with self.assertLogs("runner", "ERROR") as logs:
            for _ in range(50):
                try:
                    raise RuntimeError("x")
                except RuntimeError:
                    self.runner._failed("transition failed")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(len(self.runner.errors), 50)

    def test_long_waits_still_tick_the_heartbeat(self):
        old = runner_mod.BEAT
        runner_mod.BEAT = 0.1
        try:
            self.runner.run({"app": "blank"})  # interval 0.5 s, longer than the beat
            self.assertTrue(self.ticks_on(10))
            self.assertLess(self.runner.heartbeat()["age_s"], 0.3)
        finally:
            runner_mod.BEAT = old

    def test_status_reports_the_link(self):
        link = self.runner.status()["link"]
        self.assertEqual((link["ok"], link["panels"], link["panels_down"]), (True, 18, 0))


class HealthTest(Fixture):
    def test_report_fields(self):
        r = health.report(self.board, self.runner, self.tmp.name, os.path.join(self.tmp.name, "s.db"))
        for k in ("status", "reasons", "runner", "buses", "cpu_temp_c", "throttled", "disk", "memory", "uptime_s", "version"):
            self.assertIn(k, r)
        self.assertEqual(r["status"], "ok", r["reasons"])
        self.assertEqual(len(r["buses"]), 3)
        self.assertTrue(all(b["connected"] and b["sender_alive"] for b in r["buses"]))
        for k in ("age_s", "tick_rate", "errors_5min", "ticks"):
            self.assertIn(k, r["runner"])
        self.assertGreater(r["disk"]["data"]["free_mb"], 0)
        self.assertTrue(r["disk"]["storage"]["path"].endswith("s.db"))

    def test_server_route(self):
        import server
        server.board, server.runner, server.data_dir, server.storage_path = self.board, self.runner, self.tmp.name, self.tmp.name
        self.assertEqual(server.get_health(None)["status"], "ok")
        self.assertTrue(server.post_board_refresh(None)["ok"])
        self.assertTrue(any(p.pattern == "^/api/health$" for _, p, _ in server.ROUTES))

    def test_assess(self):
        up = {"bus": "a:1", "connected": True, "sender_alive": True}
        down = {"bus": "a:2", "connected": False, "sender_alive": True}
        fresh = {"age_s": 0.1, "errors_5min": 0}
        self.assertEqual(health.assess(fresh, [up, up])[0], "ok")
        self.assertEqual(health.assess(fresh, [up, down])[0], "degraded")
        self.assertEqual(health.assess(fresh, [down, down])[0], "failing")
        self.assertEqual(health.assess({"age_s": 12, "errors_5min": 0}, [up])[0], "failing")
        self.assertEqual(health.assess({"age_s": 4, "errors_5min": 0}, [up])[0], "degraded")
        self.assertEqual(health.assess({"age_s": 0.1, "errors_5min": 3}, [up])[0], "degraded")
        self.assertEqual(health.assess(fresh, [{**up, "sender_alive": False}, up])[0], "degraded")
        self.assertEqual(health.assess(fresh, [up], temp=83)[0], "degraded")
        self.assertEqual(health.assess(fresh, [up], throttle={"now": ["under-voltage"]})[0], "degraded")
        self.assertEqual(health.assess(fresh, [up], disks=[{"path": "/x", "free_mb": 50}])[0], "degraded")
        status, reasons = health.assess({"age_s": 20, "errors_5min": 1}, [up, down])
        self.assertEqual(status, "failing")
        self.assertEqual(len(reasons), 3)

    def test_unreachable_bus_degrades(self):
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()
        b = flipdot.Board(config(ports=(port, port, port)))
        self.assertTrue(wait_for(lambda: not b.link_status()["ok"]))
        r = health.report(b, self.runner, self.tmp.name, self.tmp.name)
        self.assertEqual(r["status"], "failing")
        self.assertIn("no bus can be reached", r["reasons"])


class NotifyTest(unittest.TestCase):
    def listen(self, abstract=False):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if abstract:
            name = f"flipdot-test-{os.getpid()}-{time.monotonic_ns()}"
            sock.bind("\0" + name)
            addr = "@" + name
        else:
            self.tmp = tempfile.TemporaryDirectory()
            addr = os.path.join(self.tmp.name, "notify")
            sock.bind(addr)
        sock.settimeout(0.05)
        self.addCleanup(sock.close)
        return sock, {"NOTIFY_SOCKET": addr}

    def drain(self, sock, seconds):
        got, end = [], time.time() + seconds
        while time.time() < end:
            try:
                got.append(sock.recv(256).decode())
            except socket.timeout:
                pass
        return got

    def test_ready(self):
        for abstract in (False, True):
            sock, env = self.listen(abstract)
            self.assertTrue(health.sd_notify("READY=1", env))
            self.assertEqual(sock.recv(256), b"READY=1")

    def test_nothing_without_a_socket(self):
        self.assertFalse(health.sd_notify("READY=1", {}))
        self.assertIsNone(health.watchdog_interval({}))
        self.assertIsNone(health.start_watchdog(lambda: 0, env={}))

    def test_watchdog_interval(self):
        env = {"NOTIFY_SOCKET": "/x", "WATCHDOG_USEC": "30000000"}
        self.assertEqual(health.watchdog_interval(env), 15)
        self.assertIsNone(health.watchdog_interval({**env, "WATCHDOG_PID": str(os.getpid() + 1)}))
        self.assertIsNone(health.watchdog_interval({"NOTIFY_SOCKET": "/x"}))

    def test_watchdog_pings_only_while_healthy(self):
        sock, env = self.listen()
        age = [0.0]
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        health.start_watchdog(lambda: age[0], env=env, interval=0.05)
        got = self.drain(sock, 0.4)
        self.assertGreaterEqual(got.count("WATCHDOG=1"), 3)
        self.assertEqual(set(got), {"WATCHDOG=1"})
        age[0] = health.FRESH + 1  # the runner stopped ticking: no more pings
        time.sleep(0.1)
        self.drain(sock, 0.05)
        self.assertEqual(self.drain(sock, 0.4), [])
        age[0] = 0.5  # ticking again
        self.assertIn("WATCHDOG=1", self.drain(sock, 0.3))


if __name__ == "__main__":
    unittest.main()
