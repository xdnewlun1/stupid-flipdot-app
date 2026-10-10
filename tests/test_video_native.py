"""Board-size videos shown dot for dot, frame timing, and when the title scrolls by. Run: python3 -m pytest -q tests/"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import video  # noqa: E402
from apps.video import title_wanted  # noqa: E402


class NativeTest(unittest.TestCase):
    def test_dot_for_dot(self):
        gray = bytes([0, 127, 128, 255])
        self.assertEqual(video.native_dots(gray), bytes([0, 0, 1, 1]))
        self.assertEqual(video.native_dots(gray, invert=True), bytes([1, 1, 0, 0]))
        # display options don't touch a native video
        d = video.display_opts({"dither": "ordered", "threshold": 10, "contrast": 3, "gamma": 0.3})
        self.assertEqual(video.dots_for({"native": True}, gray, 4, 1, d), bytes([0, 0, 1, 1]))

    def test_frame_index(self):
        with tempfile.TemporaryDirectory() as d:
            even = video.Reader(d, 30, 84, 42, fps=30)
            self.assertEqual([even.index(t) for t in (0, 0.0334, 0.999, 5)], [0, 1, 29, 29])
            timed = video.Reader(d, 4, 84, 42, fps=10, times=[0, 0.1, 0.4, 0.45])
            self.assertEqual([timed.index(t) for t in (0.05, 0.1, 0.39, 0.42, 0.9)], [0, 1, 1, 2, 3])
            self.assertEqual(timed.start(2), 0.4)

    def test_title_choice(self):
        on, off = {"show_title": True}, {"show_title": False}
        self.assertTrue(title_wanted("auto", {}, on))
        self.assertFalse(title_wanted("auto", {}, off))
        self.assertTrue(title_wanted(True, {}, on))  # older saved items: True follows the setting
        self.assertFalse(title_wanted(True, {}, off))
        self.assertFalse(title_wanted(False, {}, on))
        self.assertTrue(title_wanted("on", {"show_title": "off"}, off))  # the app's option wins
        self.assertTrue(title_wanted("auto", {"show_title": "on"}, off))  # then the video's own choice
        self.assertFalse(title_wanted("auto", {"show_title": "off"}, on))
        self.assertFalse(title_wanted("auto", {"native": True}, on))  # board-size: off unless asked for
        self.assertTrue(title_wanted("auto", {"native": True, "show_title": "on"}, on))


if __name__ == "__main__":
    unittest.main()
