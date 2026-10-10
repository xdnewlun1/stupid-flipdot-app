"""Music visualizer Mix: when it changes style (MixScheduler), which style comes next, and the app end to end."""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
import audio_signal  # noqa: E402
from apps.base import create  # noqa: E402
from apps.visualizer import STYLE_IDS, Mix, MixScheduler  # noqa: E402
from canvas import Canvas  # noqa: E402

FPS = 15


def _feed(sched, seconds, beats=(), downbeats=(), songs=(), live=True, level=lambda t: 0.5):
    """Run the scheduler frame by frame; beats/downbeats/songs are times. Returns [(time, reason)]."""
    beats, downbeats, songs = sorted(beats), set(downbeats), sorted(songs)
    out, count, bi, si = [], 0, 0, 0
    for i in range(int(seconds * FPS)):
        t = i / FPS
        down = False
        while bi < len(beats) and beats[bi] <= t:
            count += 1
            down = beats[bi] in downbeats
            bi += 1
        changed = False
        while si < len(songs) and songs[si] <= t:
            changed, si = True, si + 1
        why = sched.update(t, count, down, live, level(t), changed)
        if why:
            out.append((t, why))
    return out


def _grid(start, end, period=0.5, bar_offset=0):
    beats = [start + k * period for k in range(int((end - start) / period))]
    return beats, [b for k, b in enumerate(beats) if (k + bar_offset) % 4 == 0]


def test_switches_land_on_16_bar_boundaries_and_the_song_change():
    b1, d1 = _grid(0, 77.3)
    b2, d2 = _grid(77.3, 160)
    got = _feed(MixScheduler(bars=16, seconds=20, song=True, min_gap=8), 160, b1 + b2, d1 + d2, songs=[77.3])
    want = [(32.0, "bars"), (64.0, "bars"), (77.3, "song"), (77.3 + 32, "bars"), (77.3 + 64, "bars")]
    assert [w for _, w in got] == [w for _, w in want]
    for (t, _), (wt, _) in zip(got, want):
        assert wt <= t < wt + 1.5 / FPS  # the first frame at or after the beat


def test_downbeats_realign_the_bar_count():
    beats, downs = _grid(0, 70, bar_offset=2)  # the music starts on beat 3 of a bar
    got = _feed(MixScheduler(bars=8, seconds=0, song=False, min_gap=4), 70, beats, downs)
    assert got, "no switches"
    for t, why in got:
        assert why == "bars"
        assert any(abs(t - d) < 1.5 / FPS for d in downs), t  # every switch is on a downbeat


def test_min_gap_holds_a_song_change_back():
    beats, downs = _grid(0, 60)
    got = _feed(MixScheduler(bars=16, seconds=20, song=True, min_gap=8), 60, beats, downs, songs=[33.0])
    assert [w for _, w in got] == ["bars", "song"]
    assert got[0][0] == 32.0 and 40.0 <= got[1][0] < 40.0 + 1.5 / FPS
    times = [t for t, _ in got]
    assert all(b - a >= 8 for a, b in zip(times, times[1:]))


def test_time_schedule_without_live_audio():
    beats, downs = _grid(0, 100)
    got = _feed(MixScheduler(bars=16, seconds=20, song=True, min_gap=8), 100, beats, downs, live=False)
    assert [w for _, w in got] == ["time"] * 4
    assert [round(t) for t, _ in got] == [20, 40, 60, 80]


def test_time_schedule_when_the_beat_is_not_steady():
    rnd = random.Random(1)
    beats, t = [], 0.0
    while t < 90:
        t += rnd.uniform(0.3, 1.4)
        beats.append(round(t * FPS) / FPS)
    got = _feed(MixScheduler(bars=4, seconds=20, song=False, min_gap=8), 90, beats)
    assert [w for _, w in got] == ["time"] * 4


def test_energy_jump_switches_on_the_next_beat():
    beats, downs = _grid(0, 60)
    level = lambda t: 0.35 if t < 30 else 0.8  # noqa: E731  (a drop at 30 s)
    got = _feed(MixScheduler(bars=0, seconds=0, song=False, energy=True, min_gap=8), 60, beats, downs, level=level)
    assert len(got) == 1 and got[0][1] == "energy"
    assert 30 < got[0][0] < 33
    assert any(abs(got[0][0] - b) < 1.5 / FPS for b in beats)


def test_no_style_repeats_and_sequence_order():
    p = {"mix_styles": STYLE_IDS, "mix_order": "random", "mix_bars": "16", "mix_seconds": 20, "mix_song": True,
         "mix_min_gap": 8, "mix_transition": "dissolve", "mix_duration": 1.2}
    mix = Mix(p, 84, 42, random.Random(3))
    seen = [mix.cur.style]
    for k in range(200):
        mix.switch(k * 10.0)
        seen.append(mix.cur.style)
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert set(seen) == set(STYLE_IDS)
    mix = Mix(dict(p, mix_order="sequence", mix_styles=["wave", "bars", "warp"]), 84, 42)
    order = [mix.cur.style]
    for k in range(4):
        mix.switch(k * 10.0)
        order.append(mix.cur.style)
    assert order == ["bars", "wave", "warp", "bars", "wave"]  # in the order of the style list
    assert Mix(dict(p, mix_styles="sunburst"), 84, 42).cur.style == "sunburst"


class _Store:
    settings = {"visualizer": {"source": "off"}, "display": {}}


class _Ctx:
    def __init__(self, w=84, h=42):
        self.width, self.height, self.store = w, h, _Store()


def _music(now):
    since = now % 0.5
    k = math.exp(-since / 0.12)
    return {"live": True, "loudness": 0.6 + 0.3 * k, "beat_age": since, "downbeat": int(now / 0.5) % 4 == 0,
            "spectrum": [min(1, 0.35 + 0.55 * k * (1 - i / 15) + 0.15 * math.sin(now * 2 + i)) for i in range(16)],
            "metadata": {"title": "One" if now < 1060 else "Two", "artist": "X"}}


def test_mix_app_runs_both_styles_through_the_transition():
    audio_signal.inject(_music, clock=lambda: 0)
    try:
        app = create(_Ctx(), {"app": "visualizer", "params": {"style": "mix", "mix_bars": "4", "mix_min_gap": 4}})
        styles, in_transition, last_switch = [app.mix.cur.style], 0, None
        for i in range(90 * FPS):
            now = 1000 + i / FPS
            before = app.mix.switches
            app.render(Canvas(84, 42), now)
            if app.mix.switches != before:
                assert last_switch is None or now - last_switch >= 4
                last_switch = now
                styles.append(app.mix.cur.style)
            if app.mix.trans:
                in_transition += 1
                assert app.mix.prev is not None and app.mix.prev is not app.mix.cur
        assert len(styles) >= 8 and all(a != b for a, b in zip(styles, styles[1:]))
        assert in_transition > 0
        assert app.describe().startswith(f"Music visualizer · Mix (now: {app.mix.cur.style}")
        assert app.status_tick == app.mix.cur.style
    finally:
        audio_signal.inject(None)


def test_single_styles_still_render():
    audio_signal.inject(_music, clock=lambda: 0)
    try:
        for style in STYLE_IDS:
            app = create(_Ctx(), {"app": "visualizer", "params": {"style": style}})
            assert app.mix is None
            lit = 0
            for i in range(30):
                c = Canvas(84, 42)
                app.render(c, 1000 + i / FPS)
                lit += sum(c.px)
            assert lit > 0, style
            assert app.describe().startswith(f"Music visualizer ({style}")
    finally:
        audio_signal.inject(None)


def _silence(now):
    return {"live": True, "loudness": 0.0, "spectrum": [0.0] * 16, "beat_age": 99,
            "metadata": {"title": "One", "artist": "X"}}


def test_every_style_moves_in_silence():
    audio_signal.inject(_silence, clock=lambda: 0)
    try:
        for style in STYLE_IDS + ["mix"]:
            app = create(_Ctx(), {"app": "visualizer", "params": {"style": style, "mix_seconds": 3, "mix_min_gap": 2}})
            frames = []
            for i in range(10 * FPS):
                c = Canvas(84, 42)
                app.render(c, 1000 + i / FPS)
                frames.append(bytes(c.px))
            frames = frames[2 * FPS:]  # after the music has faded out of the smoothing
            for i in range(0, len(frames) - FPS, FPS // 3):
                assert frames[i] != frames[i + FPS], (style, i)  # never the same picture a second later
            assert any(frames), style
    finally:
        audio_signal.inject(None)


def test_bass_disc_inverts_what_is_under_it():
    from apps.visualizer import Style
    c = Canvas(84, 42)
    bands = {"level": 0.5, "bass": 0.75, "lowmid": 0.0, "mid": 0.0, "high": 0.0}
    Style("rings", {}).draw(c, [0.5] * 16, bands, 1 / FPS, 0, flow=0.0)
    row = c.px[21 * 84:22 * 84]
    # disc radius ~13 covers the first ring (radius 10..12, plus the idle breath): it shows as dark dots
    # inside the lit disc, and the disc keeps a lit edge
    inside = row[42 - 11:42 + 11]
    assert 0 in inside and inside.count(1) > 10
    assert row[42 - 13] == 1 or row[42 - 14] == 1


def _morph_frames(lazy_after=None):
    """Mix switches with morph; returns (lazy morph output, the board-wide morph's output) for every frame of it."""
    import transitions
    from apps import visualizer
    saved = visualizer.LAZY_AFTER
    if lazy_after is not None:
        visualizer.LAZY_AFTER = lazy_after
    audio_signal.inject(_music, clock=lambda: 0)
    try:
        app = create(_Ctx(), {"app": "visualizer", "params": {"style": "mix", "mix_transition": "morph"}})
        pairs = []
        orig = visualizer.LazyMorph.mix

        def spy(self, new, now, old):
            out = orig(self, new, now, old)
            p = transitions.smoothstep(min(1.0, max(0.0, (now - self.start) / self.duration)))
            transitions._df_cache.clear()
            pairs.append((bytes(out.px), bytes(transitions._morph(None, old, new, p).px), self.lazy))
            return out
        visualizer.LazyMorph.mix = spy
        try:
            for i in range(6 * FPS):
                now = 1000 + i / FPS
                if i % (3 * FPS) == 0:
                    app.mix.switch(now)
                    assert isinstance(app.mix.trans, visualizer.LazyMorph)
                app.render(Canvas(84, 42), now)
        finally:
            visualizer.LazyMorph.mix = orig
        return pairs
    finally:
        visualizer.LAZY_AFTER = saved
        audio_signal.inject(None)


def test_morph_in_a_mix_is_the_board_wide_morph():
    from apps.visualizer import MIX_TRANSITIONS
    assert "morph" in MIX_TRANSITIONS  # pickable, and one of Random's choices
    pairs = _morph_frames()
    assert len(pairs) > 10
    assert all(lazy == exact for lazy, exact, _ in pairs)


def test_morph_goes_lazy_on_a_slow_machine():
    pairs = _morph_frames(lazy_after=0.0)  # "too slow" from the first frame
    assert any(was_lazy for _, _, was_lazy in pairs)
    # one side's shape a frame old: close to the full morph, not equal to it
    differ = [sum(a != b for a, b in zip(x, y)) / len(x) for x, y, _ in pairs]
    assert sum(differ) / len(differ) < 0.15
