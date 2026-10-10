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
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.latest, self.refreshes, self.writes = {}, 0, 0
        self.events = []  # (time, "refresh" | panel address)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:  # the board reconnects after a panel remap
            conn, _ = self.srv.accept()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        while True:
            data = conn.recv(4096)
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


if __name__ == "__main__":
    unittest.main()
