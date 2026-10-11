"""flipdot.Board: protocol frames, packing and the per-bus senders. Run: python3 -m unittest tests.test_board"""

import os
import random
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flipdot  # noqa: E402
from canvas import Canvas  # noqa: E402


def config(ports=(5000, 5001, 5002), ip="127.0.0.1", rotate=None, **extra):
    panels = [{"column": c + 1, "row": r + 1, "ip": ip, "port": ports[c], "address": r,
               "rotate": rotate if (c, r) == (1, 2) else 0}
              for r in range(6) for c in range(3)]
    return {"panel_width": 28, "panel_height": 7, "panels": panels, **extra}


def expected_frame(board, panel, px, cmd=0x83):
    """The frame as the protocol defines it, dot by dot (the original implementation)."""
    data = bytearray()
    flip = panel.rotate == 180
    for cx in range(board.pw):
        byte = 0
        for cy in range(board.ph):
            sx, sy = (board.pw - 1 - cx, board.ph - 1 - cy) if flip else (cx, cy)
            on = bool(px[(panel.row * board.ph + sy) * board.width + panel.col * board.pw + sx])
            if on != board.invert:
                byte |= 1 << cy
        data.append(byte)
    return bytes([0x80, cmd, panel.address]) + bytes(data) + bytes([0x8F])


def random_canvas(w, h, seed):
    rnd = random.Random(seed)
    c = Canvas(w, h)
    c.px = bytearray(rnd.getrandbits(1) for _ in range(w * h))
    return c


class FramebufferTest(unittest.TestCase):
    def test_frames_match_protocol(self):
        for invert in (False, True):
            b = flipdot.Board(config(rotate=180, invert=invert), dry_run=True)
            for seed in range(5):
                b.set_canvas(random_canvas(b.width, b.height, seed))
                _, px = b.dots()
                for p in b.panels:
                    self.assertEqual(b._frame_for(p, px), expected_frame(b, p, px), (invert, seed, p))

    def test_packed_and_rows(self):
        b = flipdot.Board(config(), dry_run=True)
        c = random_canvas(b.width, b.height, 7)
        b.set_canvas(c)
        version, bits = b.packed()
        for i, v in enumerate(c.px):
            self.assertEqual(bool(bits[i >> 3] & (0x80 >> (i & 7))), bool(v))
        self.assertEqual(b.rows(), c.rows())
        self.assertIs(b.packed()[1], bits)  # cached until the picture changes

    def test_set_frame_dots_fill_invert(self):
        b = flipdot.Board(config(), dry_run=True)
        rows = random_canvas(b.width, b.height, 3).rows()
        b.set_frame(rows)
        self.assertEqual(b.rows(), rows)
        b.set_dots([(0, 0, None), (5, 3, True), (6, 3, False)])
        self.assertEqual(b.get(0, 0), rows[0][0] == "0")
        self.assertTrue(b.get(5, 3))
        self.assertFalse(b.get(6, 3))
        b.invert_all()
        self.assertEqual(b.get(5, 3), False)
        b.fill(True)
        self.assertEqual(set(b.rows()), {"1" * b.width})
        with self.assertRaises(ValueError):
            b.set_dots([(b.width, 0, True)])

    def test_unchanged_picture_is_not_a_change(self):
        b = flipdot.Board(config(), dry_run=True)
        c = random_canvas(b.width, b.height, 1)
        b.set_canvas(c)
        seen = []
        b.listeners.append(lambda: seen.append(1))
        v = b.version
        b.set_canvas(c.copy())
        self.assertEqual((b.version, seen), (v, []))
        c.px[0] ^= 1
        b.set_canvas(c)
        self.assertEqual((b.version, len(seen)), (v + 1, 1))

    def test_non_binary_canvas_values(self):
        b = flipdot.Board(config(), dry_run=True)
        c = Canvas(b.width, b.height)
        c.px[0], c.px[1] = 255, 2
        b.set_canvas(c)
        self.assertEqual(b.rows()[0][:3], "110")


class Collector:
    """A fake RS485-to-Ethernet port: remembers the latest frame for each panel address."""

    def __init__(self):
        self.port = 0
        self.latest, self.refreshes, self.writes = {}, 0, 0
        self.events = []  # (time, "refresh" | panel address)
        self.start()

    def start(self):
        """Listen (again, on the same port after stop())."""
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", self.port))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.conns = []
        threading.Thread(target=self._run, args=(self.srv,), daemon=True).start()

    def stop(self):
        """The converter goes away: connections are dropped and new ones refused."""
        try:
            self.srv.shutdown(socket.SHUT_RDWR)  # wakes the accept() (close alone leaves the port listening)
        except OSError:
            pass
        self.srv.close()
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def _run(self, srv):
        while True:  # the board reconnects after a panel remap
            try:
                conn, _ = srv.accept()
            except OSError:
                return  # stopped
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        while True:
            try:
                data = conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            self.writes += 1
            buf += data
            while buf:
                if buf.startswith(flipdot.REFRESH):
                    self.refreshes += 1
                    self.events.append((time.monotonic(), "refresh"))
                    buf = buf[3:]
                elif len(buf) >= 32:
                    self.latest[buf[2]] = buf[:32]
                    self.events.append((time.monotonic(), buf[2]))
                    buf = buf[32:]
                else:
                    break


class SendTest(unittest.TestCase):
    def wait_for(self, check, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return True
            time.sleep(0.02)
        return False

    def run_board(self, **extra):
        buses = [Collector() for _ in range(3)]
        b = flipdot.Board(config(ports=[c.port for c in buses], **extra))
        return b, buses

    def board_matches(self, b, buses, cmd=0x83):
        _, px = b.dots()
        return all(buses[p.col].latest.get(p.address) == expected_frame(b, p, px, cmd) for p in b.panels)

    def test_every_panel_gets_the_latest_picture(self):
        b, buses = self.run_board()
        for seed in range(10):
            b.set_canvas(random_canvas(b.width, b.height, seed))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))

    def test_sync_refresh(self):
        b, buses = self.run_board(sync_refresh=True)
        b.set_canvas(random_canvas(b.width, b.height, 2))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        self.assertTrue(all(c.refreshes >= 1 for c in buses))

    def test_dead_bus_does_not_block_the_others(self):
        buses = [Collector(), Collector()]
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()  # nothing listens here: connections are refused
        b = flipdot.Board(config(ports=[buses[0].port, buses[1].port, dead_port]))
        b.set_canvas(random_canvas(b.width, b.height, 4))
        _, px = b.dots()
        live = [p for p in b.panels if p.col < 2]
        self.assertTrue(self.wait_for(lambda: all(
            buses[p.col].latest.get(p.address) == expected_frame(b, p, px) for p in live)))

    def test_panel_remap(self):
        b, buses = self.run_board()
        b.set_canvas(random_canvas(b.width, b.height, 9))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        # columns 1 and 3 swap buses, and one panel turns upside down
        b.set_panels(config(ports=[buses[2].port, buses[1].port, buses[0].port], rotate=180)["panels"])
        _, px = b.dots()
        port_of = {c.port: c for c in buses}
        self.assertTrue(self.wait_for(lambda: all(
            port_of[p.port].latest.get(p.address) == expected_frame(b, p, px) for p in b.panels)))

    def first_times(self, buses, kind):
        return [min(t for t, k in c.events if k == kind) for c in buses]

    def test_sweep_wave_with_sync_refresh(self):
        # each column of panels refreshes one sweep after the column to its left
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: all(c.refreshes for c in buses)))
        for c in buses:
            c.events.clear()
        b.set_canvas(random_canvas(b.width, b.height, 5))
        self.assertTrue(self.wait_for(lambda: all(any(k == "refresh" for _, k in c.events) for c in buses)))
        t = self.first_times(buses, "refresh")
        for a, z in zip(t, t[1:]):
            self.assertAlmostEqual(z - a, 0.15, delta=0.04)
        self.assertTrue(self.board_matches(b, buses, 0x84))

    def test_sweep_wave_without_sync_refresh(self):
        # write-and-refresh frames: each column's data is held back by one sweep instead
        b, buses = self.run_board(sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        time.sleep(0.6)  # longer than a wave: the next change stands on its own
        for c in buses:
            c.events.clear()
        b.set_canvas(random_canvas(b.width, b.height, 6))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        t = self.first_times(buses, 0)
        for a, z in zip(t, t[1:]):
            self.assertAlmostEqual(z - a, 0.15, delta=0.04)

    def play(self, b, frames=25, every=0.04):
        """Moving content: a new picture every `every` seconds. Returns when the last one was shown."""
        for seed in range(frames):
            b.set_canvas(random_canvas(b.width, b.height, 50 + seed))
            time.sleep(every)
        return time.monotonic()

    def last_refreshes(self, buses):
        return [max(t for t, k in c.events if k == "refresh") for c in buses]

    def test_moving_content_flips_columns_together(self):
        # sweep 150 ms: a wave would put the right column 300 ms behind the left one
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        time.sleep(0.6)
        self.play(b)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        time.sleep(0.1)
        t = self.last_refreshes(buses)
        self.assertLess(max(t) - min(t), 0.06)

    def test_moving_content_without_sync_refresh(self):
        b, buses = self.run_board(sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        time.sleep(0.6)
        self.play(b)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        t = [max(t for t, k in c.events if k == 0) for c in buses]
        self.assertLess(max(t) - min(t), 0.06)

    def test_sweep_mode_always_keeps_the_wave(self):
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150, sweep_mode="always")
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        self.play(b, frames=5)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        time.sleep(0.5)
        t = self.last_refreshes(buses)
        for a, z in zip(t, t[1:]):
            self.assertAlmostEqual(z - a, 0.15, delta=0.05)

    def test_standalone_change_after_moving_content_gets_the_wave(self):
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        self.play(b, frames=10)
        time.sleep(0.8)  # the content stops; then one picture on its own
        for c in buses:
            c.events.clear()
        b.set_canvas(random_canvas(b.width, b.height, 99))
        self.assertTrue(self.wait_for(lambda: all(any(k == "refresh" for _, k in c.events) for c in buses)))
        t = self.first_times(buses, "refresh")
        for a, z in zip(t, t[1:]):
            self.assertAlmostEqual(z - a, 0.15, delta=0.04)

    def test_games_never_get_the_wave(self):
        # the first move after standing still would otherwise reach the right column a whole wave late
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        b.interactive = True
        time.sleep(0.8)
        for c in buses:
            c.events.clear()
        b.set_canvas(random_canvas(b.width, b.height, 77))
        self.assertTrue(self.wait_for(lambda: all(any(k == "refresh" for _, k in c.events) for c in buses)))
        t = self.first_times(buses, "refresh")
        self.assertLess(max(t) - min(t), 0.06)

    def test_steady_timing_resends_the_whole_bus_in_a_fixed_order(self):
        b, buses = self.run_board(sync_refresh=True, sweep_ms=150)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        b.steady = True
        time.sleep(0.8)  # long enough that a non-steady change would get the wave
        for c in buses:
            c.events.clear()
        _, px = b.dots()
        c = Canvas(b.width, b.height)
        c.px = bytearray(px)
        c.px[0] ^= 1  # one dot in the top-left panel (bus 0, address 0)
        b.set_canvas(c)
        self.assertTrue(self.wait_for(lambda: any(k == "refresh" for _, k in buses[0].events)))
        time.sleep(0.2)
        # bus 0 got every one of its panels, in address order, then the refresh; the others had no change
        self.assertEqual([k for _, k in buses[0].events], [0, 1, 2, 3, 4, 5, "refresh"])
        self.assertEqual(buses[1].events + buses[2].events, [])

    def test_steady_timing_sends_at_the_presentation_time(self):
        b, buses = self.run_board(sync_refresh=True)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        b.steady = True
        for c in buses:
            c.events.clear()
        at = time.monotonic() + 0.15
        b.set_canvas(random_canvas(b.width, b.height, 31), present_at=at)
        self.assertTrue(self.wait_for(lambda: all(any(k == "refresh" for _, k in c.events) for c in buses)))
        first = min(t for c in buses for t, _ in c.events)
        self.assertGreaterEqual(first, at - 0.002)
        self.assertLess(first, at + 0.03)

    def test_sync_auto_writes_a_whole_picture_first_and_a_small_change_at_once(self):
        b, buses = self.run_board(sync_refresh="auto")
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))  # the startup picture: whole
        self.assertTrue(all(c.refreshes >= 1 for c in buses))
        b.set_canvas(random_canvas(b.width, b.height, 3))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        refreshes = [c.refreshes for c in buses]
        b.set_dots([(3, 2, None)])  # one panel on bus 0: written and flipped at once
        self.assertTrue(self.wait_for(lambda: buses[0].latest.get(0) == expected_frame(b, b.panels[0], b.dots()[1], 0x83)))
        time.sleep(0.05)
        self.assertEqual([c.refreshes for c in buses], refreshes)

    def test_board_wide_change_starts_every_bus_together(self):
        # 4800 baud: one panel frame takes 67 ms on the line, a whole bus 400 ms
        b, buses = self.run_board(sync_refresh=True, baud=4800)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        time.sleep(0.5)
        b.set_dots([(3, 2, None)])  # bus 0 alone: busy for the next 67 ms
        self.assertTrue(self.wait_for(lambda: buses[0].refreshes >= 2))
        busy_until = max(bus.free_at for bus in b._buses.values())  # when bus 0's line is free again
        self.assertGreater(busy_until, time.monotonic() + 0.03)
        for c in buses:
            c.events.clear()
        b.set_canvas(random_canvas(b.width, b.height, 9))  # the whole board: buses 1 and 2 are free, bus 0 isn't
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))
        starts = [min(t for t, k in c.events) for c in buses]
        self.assertGreaterEqual(min(starts), busy_until)  # nobody went ahead of bus 0
        self.assertLess(max(starts) - min(starts), 0.03)
        t = self.last_refreshes(buses)
        self.assertLess(max(t) - min(t), 0.03)

    def test_timing_can_change_live(self):
        b, buses = self.run_board()
        self.assertEqual(b.set_timing(sweep_ms=40, sync_refresh=True)["sweep_ms"], 40)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses, 0x84)))

    def test_baud_pacing_coalesces(self):
        # 6 panels x 32 bytes at 1920 baud = 1 s per full picture: 20 quick pictures can't all go out
        b, buses = self.run_board(baud=1920)
        for seed in range(20):
            b.set_canvas(random_canvas(b.width, b.height, seed))
            time.sleep(0.01)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses), timeout=5))
        self.assertLessEqual(max(c.writes for c in buses), 4)


class LinkTest(unittest.TestCase):
    """Buses going down and coming back, and the periodic full refresh."""
    wait_for, run_board, board_matches = SendTest.wait_for, SendTest.run_board, SendTest.board_matches

    def setUp(self):
        self._retry = flipdot.RETRY
        flipdot.RETRY = 0.2  # retry a dead bus quickly

    def tearDown(self):
        flipdot.RETRY = self._retry

    def toggle_dot(self, b, x, y):
        _, px = b.dots()
        c = Canvas(b.width, b.height)
        c.px = bytearray(px)
        c.px[y * b.width + x] ^= 1
        b.set_canvas(c)

    def test_dead_bus_is_reported(self):
        buses = [Collector(), Collector()]
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()
        b = flipdot.Board(config(ports=[buses[0].port, buses[1].port, dead_port]))
        self.assertTrue(self.wait_for(lambda: not b.link_status()["ok"]))
        link = b.link_status()
        self.assertEqual((link["panels"], link["panels_down"]), (18, 6))
        self.assertEqual([d["port"] for d in link["down"]], [dead_port])
        self.assertIsNotNone(link["down"][0]["since"])
        health = {h["bus"]: h for h in b.bus_health()}
        self.assertIs(health[f"127.0.0.1:{dead_port}"]["connected"], False)
        live = {f"127.0.0.1:{c.port}" for c in buses}
        self.assertTrue(self.wait_for(lambda: all(h["connected"] for h in b.bus_health() if h["bus"] in live)))

    def test_bus_that_comes_back_gets_every_panel_again(self):
        b, buses = self.run_board()
        b.set_canvas(random_canvas(b.width, b.height, 11))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        changed = []
        b.link_listeners.append(lambda: changed.append(b.link_status()["ok"]))
        buses[2].stop()
        # a change on that bus finds it gone (the first write may still land in the socket buffer)
        self.assertTrue(self.wait_for(lambda: self.toggle_dot(b, 2 * 28, 0) or not b.link_status()["ok"]))
        self.assertEqual(b.link_status()["panels_down"], 6)
        self.assertIn(False, changed)
        buses[2].latest.clear()
        buses[2].start()
        # only one panel of that bus changed, but all six are sent: the panels may have lost power meanwhile
        _, px = b.dots()
        self.assertTrue(self.wait_for(lambda: all(
            buses[2].latest.get(p.address) == expected_frame(b, p, px) for p in b.panels if p.col == 2)))
        self.assertTrue(self.wait_for(lambda: b.link_status()["ok"]))
        self.assertEqual(changed[-1], True)
        self.assertTrue(self.board_matches(b, buses))

    def test_converter_restart_between_changes_resends_everything(self):
        # the converter restarts while nothing changes: the next change finds the connection closed, connects again
        # and sends every panel of that bus, not just the one that changed
        b, buses = self.run_board()
        b.set_canvas(random_canvas(b.width, b.height, 13))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        buses[2].stop()
        time.sleep(0.1)
        buses[2].latest.clear()
        buses[2].start()
        self.toggle_dot(b, 2 * 28, 0)
        _, px = b.dots()
        self.assertTrue(self.wait_for(lambda: all(
            buses[2].latest.get(p.address) == expected_frame(b, p, px) for p in b.panels if p.col == 2)))
        self.assertTrue(b.link_status()["ok"])

    def test_refresh_retries_now(self):
        b, buses = self.run_board()
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        for c in buses:
            c.events.clear()
        b.refresh()  # nothing changed: every panel is sent anyway
        self.assertTrue(self.wait_for(lambda: all(sorted(k for _, k in c.events) == [0, 1, 2, 3, 4, 5] for c in buses)))

    def test_periodic_full_refresh_when_idle(self):
        b, buses = self.run_board(full_refresh_s=0.5)
        b.set_canvas(random_canvas(b.width, b.height, 12))
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        b.max_flips_per_sec = 10  # quiet hours: an unchanged frame flips nothing, so it isn't held back
        b._tokens = 0.0
        flips = sum(b.flips)
        for c in buses:
            c.events.clear()
        self.assertTrue(self.wait_for(lambda: all({k for _, k in c.events} >= {0, 1, 2, 3, 4, 5} for c in buses), timeout=3))
        self.assertTrue(self.board_matches(b, buses))
        self.assertEqual(sum(b.flips), flips)

    def test_no_periodic_refresh_during_steady_timing_or_games(self):
        b, buses = self.run_board(full_refresh_s=0.3)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        for flag in ("steady", "interactive"):
            setattr(b, flag, True)
            time.sleep(0.1)
            for c in buses:
                c.events.clear()
            time.sleep(1.0)
            self.assertEqual([c.events for c in buses], [[], [], []], flag)
            setattr(b, flag, False)
        self.assertTrue(self.wait_for(lambda: all(c.events for c in buses)))  # and back once it's idle again

    def test_no_periodic_refresh_when_off(self):
        b, buses = self.run_board(full_refresh_s=0)
        self.assertTrue(self.wait_for(lambda: self.board_matches(b, buses)))
        for c in buses:
            c.events.clear()
        time.sleep(0.8)
        self.assertEqual([c.events for c in buses], [[], [], []])


if __name__ == "__main__":
    unittest.main()
