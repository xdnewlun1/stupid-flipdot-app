"""Slideshow GIF speed: delays scale with the speed, per-item speed wins, values are clamped."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.slideshow import Player, gif_speed  # noqa: E402

FRAMES = [("a", 0.1), ("b", 0.1), ("c", 0.2)]  # 0.4 s per loop


def test_speed_scales_loop_length():
    assert abs(Player({}, FRAMES, 0, 1).length - 0.4) < 1e-9
    assert abs(Player({}, FRAMES, 0, 2).length - 0.2) < 1e-9
    assert abs(Player({}, FRAMES, 0, 0.5).length - 0.8) < 1e-9


def test_frames_follow_the_faster_clock():
    p = Player({}, FRAMES, 0, 2)  # frames now last 0.05, 0.05, 0.1
    assert [p.index(t) for t in (0.0, 0.06, 0.11, 0.21)] == [0, 1, 2, 0]


def test_whole_loops_end_sooner():
    settings = {"gif_loops": 2, "gif_min_seconds": 0, "image_seconds": 10}
    assert abs(Player({}, FRAMES, 0, 2).end_time(settings) - 0.4) < 1e-9


def test_item_speed_overrides_slideshow_and_is_clamped():
    assert gif_speed({}, {"gif_speed": 2}) == 2
    assert gif_speed({"speed": 3}, {"gif_speed": 2}) == 3
    assert gif_speed({}, {}) == 1
    assert gif_speed({"speed": 99}, {}) == 4
    assert gif_speed({"speed": 0.01}, {}) == 0.25
    assert gif_speed({"speed": "fast"}, {}) == 1
