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


# ---- play modes: repeat, boomerang, random ------------------------------------------------------------------

from apps.slideshow import gif_mode, sequence  # noqa: E402

FIVE = [(c, 0.1) for c in "abcde"]


def _played(p, n, step=0.1):
    return [p.frame(i * step + 0.05) for i in range(n)]


def test_repeat_is_unchanged():
    assert _played(Player({}, FIVE, 0), 10) == list("abcdeabcde")
    assert _played(Player({}, FIVE, 0, mode="repeat"), 10) == list("abcdeabcde")


def test_boomerang_plays_forward_then_back_without_doubling_the_ends():
    p = Player({}, FIVE, 0, mode="boomerang")
    assert _played(p, 12) == list("abcdedcbabcd")
    settings = {"gif_loops": 1, "gif_min_seconds": 0, "image_seconds": 10}
    assert abs(p.end_time(settings) - 0.8) < 1e-9  # one play = there and back


def test_random_plays_every_frame_each_pass_and_lasts_one_play():
    p = Player({"file": "x.gif"}, FIVE, 0, mode="random")
    seq = _played(p, 20)
    for k in range(0, 20, 5):
        assert sorted(seq[k:k + 5]) == list("abcde")
    assert all(a != b for a, b in zip(seq, seq[1:]))
    assert seq != list("abcde") * 4
    settings = {"gif_loops": 2, "gif_min_seconds": 0, "image_seconds": 10}
    assert abs(p.end_time(settings) - 1.0) < 1e-9  # two plays of five frames, not two whole shuffled sequences


def test_mode_choice():
    assert gif_mode({}, {}) == "repeat"
    assert gif_mode({}, {"gif_mode": "boomerang"}) == "boomerang"
    assert gif_mode({"play": "random"}, {"gif_mode": "boomerang"}) == "random"
    assert gif_mode({"play": "sideways"}, {}) == "repeat"
    assert sequence([("a", 0.1)], "boomerang") == ([("a", 0.1)], 1)  # one frame: nothing to do
