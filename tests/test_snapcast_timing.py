"""Snapcast timing: the sample clock that stamps audio with when it plays, the fixed board delay, and the
link's smoothing staying the same at another analysis rate. Run: python3 -m unittest tests.test_snapcast_timing"""

import array
import math
import os
import random
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcast_link  # noqa: E402
import sendspin_link  # noqa: E402
from sendspin_legacy import Frame  # noqa: E402

RATE = 48000
CHUNK = RATE // 100  # snapclient's 10 ms writes


def make_link(delay_ms=150):
    return snapcast_link.SnapcastLink(tempfile.mkdtemp(), host="127.0.0.1", binary_path="/nonexistent",
                                      delay_ms=delay_ms)


class ClockTest(unittest.TestCase):
    def feed_chunks(self, link, start, chunks, late, rnd):
        """Chunks written when due (start + k * 10 ms); each read sees them `late(k)` seconds after that, and
        sometimes two at once. Returns the play time the link gave each read's first sample, per read."""
        out, k = [], 0
        while k < chunks:
            n = 2 if rnd.random() < 0.2 else 1  # a read that picks up two chunks at once
            n = min(n, chunks - k)
            arrive = start + (k + n - 1) * 0.01 + late(k + n - 1)
            plays = link._tick_clock(arrive, n * CHUNK)
            out.append((k, plays))
            k += n
        return out

    def test_anchor_ignores_late_reads(self):
        link, rnd = make_link(), random.Random(1)
        start = 1000.0
        # most reads arrive within 1 ms, some up to 30 ms late (a busy Pi)
        late = lambda k: rnd.random() * 0.001 if rnd.random() < 0.8 else rnd.random() * 0.03
        res = self.feed_chunks(link, start, 600, late, rnd)
        for k, plays in res[50:]:  # once the clock has seen a few reads
            self.assertAlmostEqual(plays, start + k * 0.01, delta=0.0015, msg=f"chunk {k}")

    def test_restart_after_a_stall(self):
        link, rnd = make_link(), random.Random(2)
        self.feed_chunks(link, 1000.0, 300, lambda k: 0.0, rnd)
        # snapclient stops for a while, then its timeline restarts 2 s later than the old one would say
        link._pos = 300 * CHUNK
        res = self.feed_chunks(link, 1000.0 + 3.0 + 2.0, 100, lambda k: 0.0, rnd)
        # the old anchor is dropped after a few late reads; from then on the new timeline holds
        k, plays = res[-1]
        self.assertAlmostEqual(plays, 1000.0 + 5.0 + (k + 300) * 0.01 - 3.0, delta=0.002)


class DelayTest(unittest.TestCase):
    def test_frames_come_due_delay_after_they_play(self):
        link = make_link(delay_ms=200)
        now = time.time()
        f = Frame(60000, [60000] * 16, None, None)
        with link.lock:
            link._frames.append(((now - 0.1) * 1e6, f))   # played 100 ms ago: not due yet
            link._frames.append(((now - 0.25) * 1e6, f))  # out of order on purpose: the deque is in play order
        link._frames = type(link._frames)(sorted(link._frames, key=lambda x: x[0]))
        link.updated = 0
        link.signal()
        self.assertEqual(len(link._frames), 1)  # only the one played 250 ms ago came due
        due = link.next_due(now)
        self.assertAlmostEqual(due, now - 0.1 + 0.2, delta=0.001)

    def test_delay_changes_live(self):
        link = make_link(delay_ms=100)
        link.set_delay(400)
        self.assertEqual(link.delay_us, 400_000)
        link.set_delay(99999)
        self.assertEqual(link.delay_us, 2_000_000)


class SmoothingTest(unittest.TestCase):
    def run_frames(self, frame_dt, seconds):
        """Loudness after each frame, keyed by how much audio has gone in (ms): frame i covers i*dt..(i+1)*dt."""
        link = sendspin_link.SendspinLink(tempfile.mkdtemp())
        link.frame_dt = frame_dt
        dt = frame_dt or sendspin_link.REF_DT
        out = {}
        for i in range(int(seconds / dt)):
            t = i * dt
            loud = 60000 if (t % 1.0) < 0.3 - 1e-9 else 20000  # steps at multiples of 0.1 s: on both frame grids
            link._apply(Frame(loud, [loud] * 16, None, None), t)
            out[round((i + 1) * dt * 1000)] = link.loudness
        return out

    def test_same_time_constants_at_50_frames_a_second(self):
        slow = self.run_frames(None, 4)  # 20 a second, the rate the constants were tuned at
        fast = self.run_frames(0.02, 4)
        common = [ms for ms in slow if ms in fast and ms % 100 == 0]
        self.assertGreater(len(common), 30)
        for ms in common:
            self.assertAlmostEqual(fast[ms], slow[ms], delta=0.01, msg=f"after {ms} ms of audio")

    def test_unchanged_at_the_reference_rate(self):
        # frame_dt None (Sendspin): exactly the old per-frame weights
        link = sendspin_link.SendspinLink(tempfile.mkdtemp())
        link._apply(Frame(65535, [65535] * 16, None, None), 0.0)
        self.assertAlmostEqual(link.loudness, 0.7)
        self.assertAlmostEqual(link.spectrum[0], 0.65)


class SyncTestAppTest(unittest.TestCase):
    def test_every_click_flips_the_whole_board(self):
        import apps
        import audio_signal
        from canvas import Canvas
        from runner import Context
        clicks = [1.0, 2.013, 3.026]
        audio_signal.inject(lambda now: {"live": True, "loudness": 0.0, "spectrum": [0.0] * 16,
                                         "raw_peak": 0.9 if any(0 <= now - c < 0.02 for c in clicks) else 0.0})
        try:
            store = type("S", (), {"settings": {}, "dir": tempfile.mkdtemp()})()
            app = apps.create(Context(84, 42, store), {"app": "sync_test"})
            seen = []
            for i in range(200):  # 4 s at one draw per 20 ms
                c = Canvas(84, 42)
                app.render(c, i * 0.02)
                seen.append(c.px.count(1))
            self.assertEqual(app.clicks, 3)
            self.assertEqual(set(seen), {0, 84 * 42})  # always the whole board, one way or the other
            flips = [i * 0.02 for i in range(1, 200) if seen[i] != seen[i - 1]]
            for got, want in zip(flips, clicks):
                self.assertAlmostEqual(got, want, delta=0.021)
        finally:
            audio_signal.inject(None)

    def test_click_track(self):
        import io
        import wave
        from apps import synctest
        w = wave.open(io.BytesIO(synctest.click_wav(period=1.013, seconds=10)))
        s = array.array("h", w.readframes(w.getnframes()))
        rate = w.getframerate()
        starts = [i for i in range(1, len(s)) if s[i] and not any(s[max(0, i - 100):i])]
        self.assertEqual(len(starts), 9)  # at 1, 2.013, ... 9.104 s
        for k, i in enumerate(starts):
            self.assertAlmostEqual(i / rate, 1 + k * 1.013, delta=0.001)


if __name__ == "__main__":
    unittest.main()
