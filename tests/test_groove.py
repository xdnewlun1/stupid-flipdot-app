"""The music analysis on synthetic songs (tests/music_signals.py) run through the real link pipeline: tempo, beat
phase, swing, per-band hits and baseline-robust band levels, for funk, disco/house, a quiet jazz trio, brickwalled
EDM, an intro going into a drop, a breakdown and song changes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import music_signals as ms  # noqa: E402

BAND_KEYS = ("bass", "lowmid", "mid", "high")


def _pct(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * len(vals)))]


def _window(rs, a, b):
    return [r for t, r in rs if a <= t < b]


def _hit_times(rs, band):
    out, last = [], None
    for t, r in rs:
        c = r["hit_count"][band]
        if last is not None and c != last:
            out.append(t)
        last = c
    return out


def _onsets(song, parts, after=3.0):
    """Note start times of these parts, notes closer than 60 ms merged (one hit can't tell them apart)."""
    out = []
    for n in sorted(t for p in parts for t in song.notes.get(p, [])):
        if n > after and (not out or n - out[-1] > 0.06):
            out.append(n)
    return out


def _recall(notes, hits, lo=-0.03, hi=0.1):
    """Share of notes with a hit at most 30 ms before (the analysis frame is centred) to 100 ms after them."""
    return sum(any(lo <= h - n <= hi for h in hits) for n in notes) / len(notes)


def _precision(notes, hits, after=3.0, lo=-0.03, hi=0.1):
    hits = [h for h in hits if h > after]
    return sum(any(lo <= h - n <= hi for n in notes) for h in hits) / len(hits)


def test_tempo_lands_on_each_groove():
    for name, start in (("funk", 6), ("disco", 6), ("jazz", 6), ("edm", 6), ("drop", 13), ("breakdown", 6)):
        song, rs = ms.song(name), ms.run(name)
        late = [r for t, r in rs if start <= t < (10 if name == "breakdown" else 99)]
        for r in late:
            assert any(abs(r["bpm"] - b) < b * 0.03 for b in (song.bpm, song.bpm * 2, song.bpm / 2)), (name, r["bpm"])
        assert all(abs(r["bpm"] - song.bpm) < song.bpm * 0.03 for r in late), name  # in fact the right octave
        assert sum(r["beat_phase"] is not None for r in late) > 0.9 * len(late), name


def test_beat_phase_locks_to_the_beat():
    for name, grid in (("disco", 1.0), ("edm", 1.0), ("jazz", 1.0), ("funk", 0.5)):
        song, rs = ms.song(name), ms.run(name)
        errs = []
        for t, r in rs:
            if t > 6 and r["beat_phase"] is not None:
                true = t * song.bpm / 60
                errs.append(abs((r["beat_phase"] - true) / grid % 1.0 - 0.5) - 0.5)  # 0 = on the grid
        # four on the floor and the jazz ride: on the beat. Funk (kick and bass mostly on the off-beats): on the
        # eighth-note grid, maybe the "and" rather than the beat
        assert max(abs(e) for e in errs) * grid < 0.1, (name, max(errs), min(errs))


def test_swing_and_groove():
    funk, disco, jazz = (ms.run(n)[-1][1] for n in ("funk", "disco", "jazz"))
    assert funk["swing"] > 1.1 and jazz["swing"] > 1.5  # swung 16ths (1.6) / the jazz ride's swung eighths (2.0)
    assert disco["swing"] < 1.15  # straight
    assert funk["groove"] > 0.3 and disco["groove"] > 0.3  # off-beat bass, chops, open hats
    assert jazz["groove"] < funk["groove"]  # the ride and walking bass sit on the beat


def test_bass_hits_follow_the_bassline():
    for name, rec, prec in (("funk", 0.7, 0.85), ("disco", 0.85, 0.85), ("jazz", 0.9, 0.85)):
        song, rs = ms.song(name), ms.run(name)
        notes, hits = _onsets(song, ("bass", "kick")), _hit_times(rs, "bass")
        assert _recall(notes, hits) >= rec, (name, _recall(notes, hits))
        assert _precision(notes, hits) >= prec, (name, _precision(notes, hits))


def test_off_beat_hats_register_on_the_high_band():
    for name, parts, rec in (("disco", ("open",), 0.9), ("edm", ("open",), 0.9), ("funk", ("hat",), 0.85)):
        song, rs = ms.song(name), ms.run(name)
        hits = _hit_times(rs, "high")
        assert _recall(_onsets(song, parts), hits) >= rec, name
        assert _precision(_onsets(song, ("open", "hat", "ride")), hits) >= 0.8, name
    # and the high band's pulse peaks on the off-beats in disco, between the kicks
    song, rs = ms.song("disco"), ms.run("disco")
    on = [r["hits"]["high"] for t, r in rs if t > 4 and (t * 2) % 1 < 0.15]
    off = [r["hits"]["high"] for t, r in rs if t > 4 and 0.5 <= (t * 2) % 1 < 0.65]
    assert sum(off) / len(off) > 3 * sum(on) / len(on)


def test_bands_use_their_range_quiet_or_loud():
    for name in ("funk", "disco", "jazz", "edm"):
        rs = _window(ms.run(name), 4, 99)
        for k in BAND_KEYS:
            vals = [r[k] for r in rs]
            assert _pct(vals, 0.95) > 0.85, (name, k)  # reaches the top, quiet song or loud
        for k in (("bass", "mid") if name == "jazz" else BAND_KEYS):
            vals = [r[k] for r in rs]
            # and moves through most of it (brickwalled EDM: steady and loud, so it stays in the upper part)
            assert _pct(vals, 0.95) - _pct(vals, 0.05) > (0.3 if name == "edm" else 0.4), (name, k)
    # the quiet trio (25 dB down) uses the range as fully as the loud loop
    jazz, edm = (_window(ms.run(n), 4, 99) for n in ("jazz", "edm"))
    assert _pct([r["bass"] for r in jazz], 0.95) > 0.9 and _pct([r["bass"] for r in edm], 0.95) > 0.9


def test_steady_loud_bass_stays_high():
    rs = _window(ms.run("edm"), 4, 99)  # constant sub-bass under everything
    bass = [r["bass"] for r in rs]
    assert _pct(bass, 0.05) > 0.4 and sum(bass) / len(bass) > 0.6
    assert sum(r["bass"] for r in rs[-50:]) / 50 > 0.6  # no slow decay towards 0
    assert max(r["hits"]["bass"] for r in rs) > 0.6  # the kicks still show as hits on top


def test_adapts_within_seconds_after_a_drop():
    rs = ms.run("drop")  # quiet intro, the drop at 8 s
    assert any(r["adapting"] for r in _window(rs, 8, 9))  # the jump in loudness was seen
    after = _window(rs, 10.5, 13)  # 2.5 s after the drop: back to using the range, not pinned at the top
    for k in BAND_KEYS:
        vals = [r[k] for r in after]
        assert _pct(vals, 0.5) < 0.95 and _pct(vals, 0.05) < 0.7, k
    intro = _window(rs, 3, 8)  # the intro used the range too
    assert _pct([r["level"] for r in intro], 0.95) > 0.9


def test_breakdown_reads_quieter_for_a_while():
    rs = ms.run("breakdown")  # loud until 10 s, then a quiet breakdown without a gap
    loud = _window(rs, 5, 10)
    quiet = _window(rs, 10.5, 14)
    for k in ("level", "bass"):
        assert _pct([r[k] for r in quiet], 0.5) < 0.5 * _pct([r[k] for r in loud], 0.5), k
        assert max(r[k] for r in quiet) > 0.15, k  # quieter, not dead
    import audio_signal
    w = _window(rs, 14, 18)  # dynamics 0 normalises the breakdown up (slowly), 1 keeps it quietest
    full, some, most = ([audio_signal.with_dynamics(r, d)["bass"] for r in w] for d in (0.0, 0.5, 1.0))
    assert _pct(full, 0.5) > 1.2 * _pct(some, 0.5) > 1.2 * _pct(most, 0.5)


def test_song_changes_between_very_different_levels():
    rs = ms.run("change")  # quiet trio, loud loop from 10 s, quiet trio from 20 s (metadata changes)
    assert any(r["adapting"] for r in _window(rs, 10, 10.5)) and any(r["adapting"] for r in _window(rs, 20, 20.5))
    for a, b in ((12.5, 15), (22.5, 25)):  # within 2.5 s of each change
        w = _window(rs, a, b)
        bass = [r["bass"] for r in w]
        assert 0.3 < _pct(bass, 0.5) < 0.97 and _pct(bass, 0.95) > 0.85, (a, _pct(bass, 0.5))
        assert _pct([r["level"] for r in w], 0.95) > 0.85, a
    late = _window(rs, 13, 20)  # and the tempo follows the new song
    assert abs(late[-1]["bpm"] - 128) < 128 * 0.03


def test_analysis_cost_per_reading():
    import time

    import audio_signal
    sig = [s for _, s in ms.song("funk").signal][::2]
    proc, t = audio_signal.Processor(), 1000.0
    start = time.perf_counter()
    for s in sig:
        proc.step(s, t)
        t += 0.04
    per = (time.perf_counter() - start) / len(sig)
    assert per < 0.001, per  # well under 1 ms on a PC (bench: tests/bench_core.py)
