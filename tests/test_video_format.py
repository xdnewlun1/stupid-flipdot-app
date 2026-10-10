"""video.py's chunked frame files: every frame reads back exactly, in any order. Run: python3 -m unittest tests.test_video_format"""

import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import video  # noqa: E402

W, H = 84, 42


def make_frame(i):
    rnd = random.Random(i)
    # a moving gradient plus some noise, so the deltas aren't trivial
    return bytes(((x * 3 + y * 2 + i * 5) + rnd.randrange(8)) & 255 for y in range(H) for x in range(W))


class FormatTest(unittest.TestCase):
    def test_seek_any_frame(self):
        n = video.CHUNK * video.SEG_CHUNKS * 2 + 37  # three segments, the last one partial (ends mid-chunk)
        frames = [make_frame(i) for i in range(n)]
        with tempfile.TemporaryDirectory() as d:
            w = video.Writer(d, W, H)
            for f in frames:
                w.add(f)
            self.assertEqual(w.close(), n)
            self.assertEqual(len([f for f in os.listdir(d) if f.endswith(".fdv")]), 3)
            self.assertFalse([f for f in os.listdir(d) if f.endswith(".tmp")])
            r = video.Reader(d, n, W, H)
            order = list(range(n))
            random.Random(1).shuffle(order)
            for i in order[:400] + [0, n - 1, video.CHUNK - 1, video.CHUNK, video.CHUNK * video.SEG_CHUNKS]:
                self.assertEqual(r.frame(i), frames[i], f"frame {i}")
            # past the end clamps to the last frame
            self.assertEqual(r.frame(n + 50), frames[-1])
            # sequential reads too (the playback path: one chunk decoded, then reused)
            for i in range(n):
                self.assertEqual(r.frame(i), frames[i])

    def test_display(self):
        g = bytes(range(0, 256, 2)) * 28  # 3584 bytes; use the first W*H
        g = g[:W * H]
        for dither in ("ordered", "diffusion", "threshold"):
            dots = video.to_dots(g, W, H, video.display_opts({"dither": dither}))
            self.assertEqual(len(dots), W * H)
            self.assertTrue(set(dots) <= {0, 1})
        black = bytes(W * H)
        self.assertEqual(set(video.to_dots(black, W, H, video.display_opts({}))), {0})
        self.assertEqual(set(video.to_dots(black, W, H, video.display_opts({"invert": True}))), {1})


if __name__ == "__main__":
    unittest.main()
