"""React to music: band mapping, auto-gain, beats, effect strengths and the variable-rate GIF clock."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_signal  # noqa: E402
from apps import slidefx  # noqa: E402
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
    hits = {"bass": 1.0, "lowmid": 0, "mid": 0, "high": 0}
    reading = {"bass": 1.0, "lowmid": 0, "mid": 0, "high": 0, "level": 1.0, "pulse": 0.0, "hits": hits}
    s = slidefx.react_strengths(rows, reading)
    assert abs(s["glitch"] - 0.8) < 1e-9 and s["wave"] == 0 and s["invert"] == 0
    quiet = slidefx.react_strengths(rows, {**reading, "level": 0.0})  # loudness scales the rest by 50%
    assert abs(quiet["glitch"] - 0.4) < 1e-9
    rows = slidefx.clean_react_map([{"signal": "bass", "effect": "life", "amount": 500, "threshold": 99}])
    assert rows[0] == {"signal": "bass", "effect": "none", "amount": 100, "threshold": 90, "react": "both"}
    assert len(rows) == len(slidefx.SIGNALS)


def test_react_to_level_hits_or_both():
    steady = {"bass": 0.9, "level": 0.9, "pulse": 0.0, "hits": {"bass": 0.0}}  # a steady loud bassline
    note = {**steady, "hits": {"bass": 1.0}}  # ...and the moment a new note starts
    row = lambda react: {"signal": "bass", "effect": "glitch", "amount": 100, "threshold": 0, "react": react}
    val = slidefx.signal_value
    assert val(row("level"), steady) == val(row("level"), note) == 0.9  # the level can't tell the notes apart
    assert val(row("hits"), steady) == 0 and val(row("hits"), note) == 1
    assert 0.4 < val(row("both"), steady) < 0.7 and val(row("both"), note) == 1
    assert slidefx.clean_react_map([{"signal": "high", "effect": "static", "react": "sideways"}])[3]["react"] == "hits"
    assert "react" not in slidefx.clean_react_map(None)[4]  # the beat row has no choice


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
    assert all(r["bass"] > 0.8 for r in rs[20:])  # the band value too: a steady loud bass reads high...
    assert rs[-1]["hits"]["bass"] == 0  # ...while its change channel (hits) stays quiet


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


# ---- tempo and the effects' motion clocks ------------------------------------------------------------

import random  # noqa: E402


def _beat_train(bpm, seed, beats=40, start=100.0, jitter=0.012, miss=0.12, extra=0.12):
    """Beat times at `bpm` with timing jitter, some beats missed and some extra (off-beat) ones."""
    rng, p, out = random.Random(seed), 60 / bpm, []
    for k in range(beats):
        t = start + k * p + rng.gauss(0, jitter)
        if rng.random() < extra:
            out.append(t + rng.uniform(0.3, 0.7) * p)
        if rng.random() >= miss:
            out.append(t)
    return sorted(out)


def _feed_beats(beats, until=None, proc=None):
    """Through the whole reading path: the link reports each beat (beat_age), read 25 times a second over a
    steady spectrum (so the beats are all there is). -> [(t, reading)]"""
    proc, t, i, out = proc or audio_signal.Processor(), beats[0] - 0.5, 0, []
    while t < (beats[-1] + 0.05 if until is None else until):
        while i + 1 < len(beats) and beats[i + 1] <= t:
            i += 1
        age = t - beats[i] if beats[i] <= t else 5.0
        out.append((t, proc.step({"loudness": 0.5, "spectrum": [0.4] * 16, "beat_age": age}, t)))
        t += 0.04
    return out


def test_bpm_from_beat_trains_with_jitter_missed_and_extra_beats():
    for bpm in (80, 120, 140, 160):
        for seed in range(4):
            r = _feed_beats(_beat_train(bpm, seed))[-1][1]
            # 160 may read as half time: groove music is preferred around 90-130 BPM
            assert any(abs(r["bpm"] - b) < b * 0.03 for b in (bpm, bpm / 2 if bpm > 130 else bpm)), (bpm, seed, r["bpm"])
            assert r["bpm_confidence"] > 0.5 and r["beat_phase"] is not None, (bpm, seed)


def test_tempo_octave_correction():
    # every hi-hat (240), half time (50), every 16th at 90 (360), one beat a bar (35)
    for bpm, expect in ((240, 120), (50, 100), (360, 120), (35, 70)):
        r = _feed_beats(_beat_train(bpm, 1, beats=int(bpm / 6), jitter=0.004, miss=0.05, extra=0))[-1][1]
        assert abs(r["bpm"] - expect) < expect * 0.02, (bpm, r["bpm"])


def test_no_tempo_without_beats_and_confidence_fades_after_they_stop():
    proc = audio_signal.Processor()
    r = proc.step({"loudness": 0.5, "spectrum": [0.4] * 16}, 10.0)
    assert r["bpm"] == 0 and r["bpm_confidence"] == 0 and r["beat_phase"] is None
    beats = _beat_train(120, 2, beats=20, miss=0, extra=0)
    rs = _feed_beats(beats, until=beats[-1] + 10)
    sure = [r for t, r in rs if beats[-1] - 3 < t < beats[-1]]
    assert all(r["bpm_confidence"] > 0.5 for r in sure)
    assert rs[-1][1]["bpm_confidence"] < 0.1 and rs[-1][1]["beat_phase"] is None


def test_random_onsets_give_no_sure_tempo():
    for seed in range(4):
        rng, beats = random.Random(seed), [100.0]
        while beats[-1] < 130:
            beats.append(beats[-1] + rng.expovariate(2.5))  # 2.5 onsets a second at random
        rs = _feed_beats(beats)[150:]
        assert sum(r["beat_phase"] is not None for _, r in rs) < 0.1 * len(rs), seed


def test_generated_beat_has_its_tempo():
    clock = [500.0]
    audio_signal.inject(lambda now: None, clock=lambda: clock[0])
    try:
        r = audio_signal.reading(_Store(), "beat")
    finally:
        audio_signal.inject(None)
    assert r["generated"] and r["bpm"] == audio_signal.GENERATED_BPM and r["bpm_confidence"] == 1
    assert 0 <= r["beat_phase"] < 1


WAVE = {"signal": "mid", "effect": "wave", "amount": 50, "threshold": 0}


def _clock_run(row, readings, kind="wave", fps=25):
    """Advance one effect's motion clock through [(now, reading, signal)]; returns [cycles]."""
    rx, out = slidefx.Reactive(random.Random(1)), []
    for now, reading, sig in readings:
        rx.advance(now, {kind: (0.5, row, sig)}, reading)
        out.append(rx.fx[kind].cycles)
    return out


def test_clock_never_jumps_when_the_rate_changes():
    rng = random.Random(3)
    for follow in slidefx.FOLLOW:
        row = {**WAVE, "follow": follow, "speed": 2.0, "lock": 0}
        steps = [(k / 25, {"bpm": rng.choice([70, 180, 0, 120]), "bpm_confidence": 0.9, "beat_phase": rng.random()},
                  rng.random()) for k in range(300)]  # tempo, phase and signal jumping around every frame
        cyc = _clock_run(row, steps)
        top = slidefx.MOTION["wave"] * 2 * 180 / 120 * 2 ** 1.5  # fastest possible rate
        for a, b in zip(cyc, cyc[1:]):
            assert 0 <= b - a <= top / 25 + 1e-9, follow


def test_speed_follows_tempo_and_signal():
    def rate(row, bpm=120, sig=0.5):
        cyc = _clock_run(row, [(k / 25, {"bpm": bpm, "bpm_confidence": 0.9, "beat_phase": None}, sig) for k in range(100)])
        return (cyc[-1] - cyc[50]) / (49 / 25)
    base = slidefx.MOTION["wave"]
    fixed = {**WAVE, "follow": "fixed", "speed": 1.0, "lock": 0}
    assert abs(rate(fixed, bpm=80) - base) < 1e-6 and abs(rate({**fixed, "speed": 0.5}) - base / 2) < 1e-6
    tempo = {**fixed, "follow": "tempo"}
    assert abs(rate(tempo, bpm=80) / rate(tempo, bpm=160) - 0.5) < 1e-6
    signal = {**fixed, "follow": "signal"}
    assert rate(signal, sig=0.1) < base * 0.6 < base * 1.8 < rate(signal, sig=0.9)
    both = {**fixed, "follow": "both"}
    assert rate(both, bpm=80, sig=0.9) < rate(both, bpm=160, sig=0.9) and rate(both, sig=0.1) < rate(both, sig=0.9)


def test_lock_to_beat_completes_a_cycle_per_beat():
    for bpm, lock in ((80, 1), (128, 1), (160, 2), (100, 4)):
        p, offset = 60 / bpm, 0.37  # the clock starts out of phase with the beat
        steps = [(k / 25, {"bpm": bpm, "bpm_confidence": 0.9, "beat_phase": ((k / 25 + offset) / p) % 1}, 0.5)
                 for k in range(25 * 20)]
        cyc = _clock_run({**WAVE, "follow": "tempo", "speed": 1.0, "lock": lock}, steps)
        for k in range(25 * 8, len(steps), 7):  # after settling: on the beat, and 1/lock cycles per beat
            beat_pos = (steps[k][0] + offset) / p
            err = (cyc[k] * lock - beat_pos + 0.5) % 1 - 0.5
            assert abs(err) < 0.03, (bpm, lock, err)
        per_beat = (cyc[-1] - cyc[25 * 8]) / ((steps[-1][0] - steps[25 * 8][0]) / p)
        assert abs(per_beat - 1 / lock) < 0.01, (bpm, lock, per_beat)
    # tempo unsure (no beat_phase): runs at the tempo's rate
    cyc = _clock_run({**WAVE, "follow": "tempo", "lock": 1}, [(k / 25, {"bpm": 90, "beat_phase": None}, 0) for k in range(51)])
    assert abs((cyc[-1] - cyc[0]) / 2 - 1.5) < 1e-6


def test_old_saved_rows_still_load():
    old = [{"signal": "bass", "effect": "glitch", "amount": 80, "threshold": 35},
           {"signal": "mid", "effect": "wave", "amount": 60, "threshold": 50},
           {"signal": "high", "effect": "scanlines", "amount": 40, "threshold": 50},
           {"signal": "beat", "effect": "invert", "amount": 100, "threshold": 0}]
    rows = {r["signal"]: r for r in slidefx.clean_react_map(old)}
    assert rows["mid"] == {**old[1], "follow": "tempo", "speed": 1.0, "lock": 2, "react": "both"}
    assert rows["bass"] == {**old[0], "follow": "signal", "speed": 1.0, "lock": 0, "react": "both"}
    assert rows["beat"] == old[3]  # no motion: no speed settings
    bad = slidefx.clean_react_map([{**old[1], "follow": "sideways", "speed": 99, "lock": 3}])[2]
    assert (bad["follow"], bad["speed"], bad["lock"]) == ("tempo", 4.0, 2)
    kept = slidefx.clean_react_map([{**old[1], "follow": "fixed", "speed": 0.5, "lock": 0}])[2]
    assert (kept["follow"], kept["speed"], kept["lock"]) == ("fixed", 0.5, 0)
    # and they still drive the effects (a plain {effect: strength} still works too)
    from canvas import Canvas
    frame = Canvas(84, 42)
    frame.rect(10, 10, 30, 20, 1, fill=True)
    reading = {"bass": 1.0, "lowmid": 0, "mid": 1.0, "high": 1.0, "level": 1.0, "pulse": 1.0,
               "bpm": 100, "bpm_confidence": 0.8, "beat_phase": 0.2}
    rx = slidefx.Reactive(random.Random(2))
    for k in range(5):
        out = rx.apply(frame, k / 25, slidefx.react_plan(slidefx.clean_react_map(old), reading), reading)
    assert (out.w, out.h) == (84, 42) and rx.fx["wave"].cycles > 0
    slidefx.Reactive().apply(frame, 1.0, slidefx.react_strengths(slidefx.clean_react_map(old), reading))
