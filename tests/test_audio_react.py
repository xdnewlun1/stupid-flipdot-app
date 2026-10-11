"""React to music: band mapping, auto-gain, beats, effect strengths and the variable-rate GIF clock."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_signal  # noqa: E402
from apps import slidefx  # noqa: E402
from canvas import Canvas  # noqa: E402
from apps.slideshow import Player  # noqa: E402

FRAMES = [("a", 0.1), ("b", 0.1), ("c", 0.2)]  # 0.4 s per loop


class _Store:
    settings = {"visualizer": {"source": "off"}}


def test_band_mapping():
    spec = [1.0] * 4 + [0.5] * 4 + [0.25] * 4 + [0.0] * 4
    assert audio_signal.band_values(spec) == {"bass": 1.0, "lowmid": 0.5, "mid": 0.25, "high": 0.0}
    assert abs(audio_signal.band_values([0.4] * 3)["bass"] - 0.3) < 1e-9  # short spectra are padded with silence


def _run(sig, seconds, start=0.0, fps=20):
    out, t = [], start
    clock = [t]
    audio_signal.inject(sig, clock=lambda: clock[0])
    try:
        while t < start + seconds:
            clock[0] = t
            out.append(audio_signal.reading(_Store()))
            t += 1 / fps
    finally:
        audio_signal.inject(None)
    return out


def test_auto_gain_uses_full_range_for_quiet_and_loud_music():
    for base in (0.35, 0.75):  # quiet song and loud song: bass pumping by 0.15 at 2 Hz
        sig = lambda now, b=base: {"live": True, "loudness": b,
                                   "spectrum": [b + (0.15 if now % 0.5 < 0.1 else 0)] * 4 + [b] * 12}
        rs = _run(sig, 6, start=1000)[-40:]
        assert max(r["bass"] for r in rs) > 0.9, base
        assert min(r["bass"] for r in rs) < 0.2, base
        assert all(r["raw"]["bass"] >= base for r in rs)


def test_beats_counted_once_each_at_120_bpm():
    sig = lambda now: {"live": True, "loudness": 0.6,
                       "spectrum": [0.85 if now % 0.5 < 0.08 else 0.4] * 6 + [0.4] * 10}
    rs = _run(sig, 4, start=2000)
    assert 7 <= rs[-1]["beat_count"] - rs[0]["beat_count"] <= 8
    assert max(r["pulse"] for r in rs) > 0.9


def test_silence_reads_as_no_live_audio():
    r = _run(lambda now: None, 0.2)[-1]
    assert not r["live"] and r["bass"] == 0


def test_react_strengths():
    rows = slidefx.clean_react_map(None)
    reading = {"bass": 1.0, "lowmid": 0, "mid": 0, "high": 0, "level": 1.0, "pulse": 0.0}
    s = slidefx.react_strengths(rows, reading)
    assert abs(s["glitch"] - 0.8) < 1e-9 and s["wave"] == 0 and s["invert"] == 0
    quiet = slidefx.react_strengths(rows, {**reading, "level": 0.0})  # loudness scales the rest by 50%
    assert abs(quiet["glitch"] - 0.4) < 1e-9
    rows = slidefx.clean_react_map([{"signal": "bass", "effect": "life", "amount": 500, "threshold": 99}])
    assert rows[0] == {"signal": "bass", "effect": "none", "amount": 100, "threshold": 90}
    assert len(rows) == len(slidefx.SIGNALS)


def test_clock_is_plain_wall_clock_at_rate_1():
    p = Player({}, FRAMES, 10, 1)
    assert [p.index(10 + t) for t in (0.0, 0.11, 0.21, 0.41)] == [0, 1, 2, 0]


def test_rate_change_is_continuous_and_monotonic():
    p = Player({}, FRAMES, 0, 1)
    last, t = 0.0, 0.0
    for k in range(400):
        t = k * 0.013
        before = p.position(t)
        p.set_rate(t, 0.75 + 1.25 * ((k * 7) % 11) / 10)  # jumpy rates
        assert abs(p.position(t) - before) < 1e-9  # no jump when the rate changes
        assert p.position(t) >= last
        last = p.position(t)
    p.set_rate(t, 2.0)
    assert abs(p.position(t + 0.1) - p.position(t) - 0.2) < 1e-9


def test_beat_skip_advances_exactly_one_frame():
    p = Player({}, FRAMES, 0, 1)
    for t in (0.0, 0.05, 0.15, 0.31, 0.399, 0.45, 1.7):
        q = Player({}, FRAMES, 0, 1)
        q.set_rate(0, 1.3)
        i = q.index(t)
        q.skip_frame(t)
        assert q.index(t) == (i + 1) % len(FRAMES), t
        assert q.position(t + 0.001) > q.position(t)
    p.skip_frame(0.0)
    p.skip_frame(0.0)
    p.skip_frame(0.0)
    assert p.index(0.0) == 0  # three skips = one whole loop


def test_end_time_follows_the_rate():
    settings = {"gif_loops": 1, "gif_min_seconds": 0, "image_seconds": 10}
    p = Player({}, FRAMES, 0, 1)
    p.set_rate(0.2, 2.0)  # half way through, twice as fast: the rest takes 0.1 s
    assert abs(p.end_time(settings) - 0.3) < 1e-9


# ---- display normalisation (continuous visuals: the music visualizer) ----------------------------------

def test_display_keeps_a_sustained_level_high():
    held = lambda now: {"live": True, "loudness": 0.7, "spectrum": [0.8] * 4 + [0.3] * 12}
    rs = _run(held, 20, start=3000)
    assert all(r["display_bands"]["bass"] > 0.9 for r in rs[20:])  # 1 s in .. 20 s: stays up
    assert rs[-1]["bass"] < 0.2  # while the transient (auto-gained) value has sunk


def test_display_attack_is_fast_and_release_slow():
    loud = lambda now: {"live": True, "loudness": 0.7,
                        "spectrum": [0.8 if now % 4 < 2 else 0.4] * 4 + [0.3] * 12}
    rs = _run(loud, 12, start=4000)  # 2 s loud / 2 s quiet, three times
    bass = [r["display_bands"]["bass"] for r in rs]
    step = 8 * 20  # the loud part at t = 8 s
    assert bass[step + 1] > 0.9  # attack: up within one reading
    assert bass[step - 1] < 0.3  # the quiet half reads low against the loud peak
    drop = [bass[step + 40 + k] for k in range(5)]  # the fall after t = 10 s takes a few readings
    assert drop[0] > drop[1] > drop[3] and drop[1] > 0.1


def test_display_auto_gain_uses_full_range():
    for base in (0.3, 0.8):  # a quiet and a loud song, bass pumping by 0.25
        sig = lambda now, b=base: {"live": True, "loudness": b,
                                   "spectrum": [b + (0.25 if now % 0.5 < 0.15 else 0)] * 4 + [b] * 12}
        bass = [r["display_bands"]["bass"] for r in _run(sig, 8, start=5000)[-40:]]
        assert max(bass) > 0.95 and min(bass) < 0.2, base


def test_display_spectrum_keeps_quiet_bins_low():
    sig = lambda now: {"live": True, "loudness": 0.7, "spectrum": [0.8] * 8 + [0.2] * 8}  # 36 dB quieter treble
    r = _run(sig, 15, start=6000)[-1]  # long enough to forget the earlier tests' louder bass
    assert min(r["display_spectrum"][:8]) > 0.9 and max(r["display_spectrum"][8:]) < 0.1


def _picture(seed):
    c = Canvas(84, 42)
    c.px[:] = bytes((i * seed + i // 7) % 3 == 0 for i in range(84 * 42))
    return c


def test_switched_effect_holds_the_picture_until_the_discs_have_shown_it():
    r = slidefx.Reactive(hold=0.15)
    a, b = _picture(5), _picture(11)
    inverted = r.apply(a, 0.0, {"invert": 1.0})  # a beat: the inverted picture goes out
    assert inverted.px == a.copy().px.translate(bytes([1, 0] + [0] * 254))
    held = r.apply(b, 0.05, {"invert": 0.4})  # the pulse already fell, the GIF moved on: the board keeps the picture
    assert held.px == inverted.px
    assert r.apply(b, 0.14, {"invert": 1.0, "glitch": 1.0}).px == inverted.px
    plain = r.apply(b, 0.2, {"invert": 0.0})  # settled: back to the plain picture, held in turn
    assert plain.px == b.px
    assert r.apply(a, 0.3, {"invert": 0.0}).px == b.px
    assert r.apply(a, 0.36, {"invert": 0.0}).px == a.px  # and the GIF carries on
    r.reset()
    assert r.apply(b, 0.4, {}).px == b.px  # no switch happened: nothing to hold


def test_without_a_hold_the_switch_follows_the_pulse_at_once():
    r = slidefx.Reactive(hold=0.0)
    a = _picture(5)
    assert r.apply(a, 0.0, {"invert": 1.0}).px != a.px
    assert r.apply(a, 0.01, {"invert": 0.0}).px == a.px


def test_flash_never_toggles_faster_than_the_discs_can_show():
    a = _picture(3)
    quick = slidefx.Effect("flash", 0.0, None, 1.0, None)  # 9 toggles a second
    assert quick.apply(a, 0.0).px != a.px and quick.apply(a, 0.12).px == a.px
    held = slidefx.Effect("flash", 0.0, None, 1.0, None, hold=0.2)  # at most one every 200 ms
    assert held.apply(a, 0.0).px != a.px and held.apply(a, 0.12).px != a.px and held.apply(a, 0.2).px == a.px
