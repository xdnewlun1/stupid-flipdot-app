"""Generative apps reacting to music (apps/react.py): off is exactly the old behaviour, amount 0 changes nothing,
the motion clocks never jump, beats and hits drive the apps, quiet hours pause it, layers share the signal."""

import hashlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_signal  # noqa: E402
import apps  # noqa: E402,F401
from apps import react  # noqa: E402
from apps.base import create  # noqa: E402
from canvas import Canvas  # noqa: E402

react.SAMPLE_HZ = 0  # the tests drive the clock; no sampler thread

# frames of each app as it was before React to music existed (fixed seeds, 40 frames; see _hash)
GOLDEN = {"life": "ed012466b4e02311", "rain": "2b3c96e1b86f56dc", "plasma": "3b4b9cea0013bcb8",
          "bounce": "8ef7e85a103232dd", "starfield": "b08a375019a6d6df", "fireworks": "b5aea6f060cc4c90",
          "ripples": "07fd5ce6f92ed672", "metaballs": "84ffd6bd13661213", "tunnel": "1017e6727e38bcd2",
          "spiral": "9d7484e0f0d97dbe", "maze": "3d85bb290538fff4", "lissajous": "f61e943303dba791",
          "fire": "d83a5aaa6521ce1d"}


class _Store:
    def __init__(self, quiet=False):
        self.settings = {"visualizer": {"source": "off"}}
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}


class _Ctx:
    def __init__(self, w, h, store):
        self.width, self.height, self.store = w, h, store


class _Seeded(random.Random):
    def __init__(self, x=None):
        super().__init__(7 if x is None else x)


def _pump(now):
    """A loud 120 BPM beat: a kick in the bass every 0.5 s, hats in between."""
    k, hat = now % 0.5 < 0.08, 0.25 <= now % 0.5 < 0.29
    return {"live": True, "loudness": 0.6,
            "spectrum": [0.9 if k else 0.4] * 4 + [0.4] * 8 + [0.8 if hat else 0.3] * 4}


def _frames(app_id, params=None, n=40, signal=None, store=None, t0=1000.0):
    """Render n frames with fixed seeds (and an injected signal on a simulated clock). -> ([Canvas], app)"""
    orig = random.Random
    random.Random = _Seeded
    random.seed(3)
    clock = [t0]
    audio_signal.inject(signal or (lambda now: None), clock=lambda: clock[0])
    react._quiet[:] = [0.0, False]
    try:
        a = create(_Ctx(84, 42, store or _Store()), {"app": app_id, "params": params or {}})
        a.started = t0
        out, now = [], t0
        for _ in range(n):
            clock[0] = now
            c = Canvas(84, 42)
            a.render(c, now)
            out.append(c)
            now += a.interval
    finally:
        random.Random = orig
        audio_signal.inject(None)
    return out, a


def _hash(frames):
    h = hashlib.sha1()
    for c in frames:
        h.update(bytes(c.px))
    return h.hexdigest()[:16]


def test_off_draws_exactly_what_it_drew_before():
    for app_id, want in GOLDEN.items():
        assert _hash(_frames(app_id)[0]) == want, app_id
        assert _hash(_frames(app_id, {"react": False}, signal=_pump)[0]) == want, app_id


def test_every_generative_app_has_the_option():
    for app_id in [*GOLDEN, "random_effects"]:
        names = [p["name"] for p in apps.base.REGISTRY[app_id].params]
        assert set(react.REACT_NAMES) <= set(names), app_id


def test_amount_zero_changes_nothing():
    for app_id in ["rain", "plasma", "bounce", "starfield", "fireworks", "metaballs", "tunnel", "spiral", "maze",
                   "lissajous", "fire"]:
        frames, a = _frames(app_id, {"react": True, "react_amount": 0}, signal=_pump)
        assert a.music.on, app_id  # it is listening...
        assert _hash(frames) == GOLDEN[app_id], app_id  # ...but nothing moves differently
    _, a = _frames("plasma", {"react": True, "react_amount": 0, "react_follow": "music"}, signal=_pump)
    m = a.music
    assert m.speed == 1.0 and m.presence == 0.0 and not m.beat and not m.new_hits
    assert all(v == 0.0 for v in m.vals.values())


def test_reacting_changes_the_motion():
    for app_id in ["fire", "starfield", "fireworks", "plasma", "life", "rain", "tunnel", "spiral", "bounce"]:
        frames, a = _frames(app_id, {"react": True}, n=60, signal=_pump)
        assert a.music.on
        assert _hash(frames[:40]) != GOLDEN[app_id], app_id


def test_beats_and_hits_reach_the_apps():
    beats = hits = 0
    clock = [0.0]
    audio_signal.inject(_pump, clock=lambda: clock[0])
    try:
        a = create(_Ctx(84, 42, _Store()), {"app": "fireworks", "params": {"react": True}})
        a.started = now = 2000.0
        launched = 0
        for _ in range(120):  # 6 s
            clock[0] = now
            before = {id(r) for r in a.rockets}
            a.render(Canvas(84, 42), now)
            beats += a.music.beat
            hits += a.music.hit("bass")
            launched += a.music.beat and any(len(r) == 5 and id(r) not in before for r in a.rockets)
            now += 0.05
    finally:
        audio_signal.inject(None)
    assert 9 <= beats <= 13  # 120 BPM for 6 s, after the first one or two
    assert hits >= 8
    assert launched >= beats - 1  # a rocket goes up on each beat


class _App:
    def __init__(self, **p):
        self.p = {"react": True, **p}
        self.started = 100.0
        self.ctx = _Ctx(84, 42, _Store())


def test_clock_is_exact_while_the_speed_holds_and_never_jumps():
    m = react.Music(_App())
    # exact while nothing changes the speed, as the plain clock was
    for now in (100.0, 100.07, 101.3, 250.5):
        assert m.clock("t", now, 1.7) == (now - 100.0) * 1.7
    # speed changes every frame: the clock moves on by about speed x rate x dt, never more
    rnd = random.Random(1)
    prev, now = m.clock("t", 250.5, 1.7), 250.5
    for _ in range(500):
        m.speed = rnd.choice([0.25, 0.5, 1.0, 2.0, 4.0])
        dt = rnd.uniform(0.01, 0.1)
        now += dt
        v = m.clock("t", now, 1.7)
        assert -1e-9 <= v - prev <= 1.7 * 4.0 * dt + 1e-9
        prev = v
    off = react.Music(_App(react=False))
    assert off.clock("t", 107.0, 2.0) == 14.0


def test_tempo_clock_lands_on_the_beat():
    m = react.Music(_App())
    m.on, m.follow, m.bpm = True, "tempo", 120.0
    now = 100.0
    for _ in range(400):  # 20 s at 20 frames a second; the beat grid starts at 100.2
        now += 0.05
        m.phase = ((now - 100.2) * 2) % 1.0
        v = m.clock("t", now, 1.0, per_beat=0.5)
    err = (v / 0.5 % 1 - m.phase + 0.5) % 1 - 0.5
    assert abs(err) < 0.05  # whole multiples of per_beat land on the beats
    assert abs(m._clocks["t"][2] - 1.0) < 0.05  # and it runs at one per_beat per beat (120 BPM: 1 unit/s)


def test_ticks_count_whole_steps():
    m = react.Music(_App())
    total, now = 0, 100.0
    for _ in range(100):
        now += 0.05
        total += m.ticks("g", now, 8.0)
    assert 39 <= total <= 40  # 5 s at 8 a second


def test_quiet_hours_pause_the_reaction():
    frames, a = _frames("fire", {"react": True}, signal=_pump, store=_Store(quiet=True))
    assert not a.music.on and a.music.speed == 1.0


def test_no_live_audio_does_nothing_unless_the_generated_beat_is_chosen():
    _, a = _frames("starfield", {"react": True})
    assert not a.music.on
    _, a = _frames("starfield", {"react": True, "react_fallback": "beat"})
    assert a.music.on and a.music.reading["generated"]


def test_effects_on_top_use_the_slideshow_table():
    rows = react.overlay_defaults()
    for r in rows:
        r.update(effect="invert", threshold=0) if r["signal"] == "beat" else r.update(effect="none")
    plain, _ = _frames("plasma", {"react": True}, signal=_pump)
    over, a = _frames("plasma", {"react": True, "react_overlay": True, "react_fx": rows}, signal=_pump)
    assert a.music.overlay is not None
    assert _hash(over) != _hash(plain)
    none, _ = _frames("plasma", {"react": True, "react_overlay": True, "react_fx": rows, "react_amount": 0}, signal=_pump)
    assert _hash(none) == GOLDEN["plasma"]


def test_layers_share_one_signal():
    from apps.layers import Layers
    stack = [{"app": "bounce", "params": {"react": True}}, {"app": "starfield", "params": {"react": True}, "blend": "add"}]
    clock = [3000.0]
    audio_signal.inject(_pump, clock=lambda: clock[0])
    try:
        lay = create(_Ctx(84, 42, _Store()), {"app": "layers", "params": {"layers": stack}})
        assert isinstance(lay, Layers)
        now = 3000.0
        for _ in range(40):
            clock[0] = now
            lay.render(Canvas(84, 42), now)
            now += 0.05
        musics = [l.app.music for l in lay.layers]
        assert all(m.on for m in musics)
        assert musics[0].reading is musics[1].reading  # the same cached reading
    finally:
        audio_signal.inject(None)


def test_random_effects_pass_it_on():
    _, a = _frames("random_effects", {"react": True, "react_amount": 150}, n=3, signal=_pump)
    child = a.cur.app
    if child.id != "visualizer":
        assert child.music.enabled and child.music.amount == 1.5 and child.music.overlay is None


def _no_effects():
    rows = react.overlay_defaults()
    for r in rows:
        r["effect"] = "none"
    return rows


def test_effects_only_leaves_the_motion_alone():
    """Effects only: with nothing in the effects table the picture is exactly the app without music, even on a
    pumping beat, so slow songs can't make it jumpy."""
    for app_id in ["fire", "starfield", "fireworks", "plasma", "life", "rain", "tunnel", "spiral", "bounce", "maze"]:
        frames, a = _frames(app_id, {"react": True, "react_mode": "effects", "react_fx": _no_effects()}, signal=_pump)
        m = a.music
        assert m.listening and not m.on and not m.motion, app_id
        assert m.speed == 1.0 and m.presence == 0.0 and m.bpm == 0.0 and not m.beat, app_id
        assert _hash(frames) == GOLDEN[app_id], app_id


def test_effects_only_still_draws_the_effects():
    rows = _no_effects()
    for r in rows:
        if r["signal"] == "beat":
            r.update(effect="invert", threshold=0)
    frames, a = _frames("plasma", {"react": True, "react_mode": "effects", "react_fx": rows}, signal=_pump)
    assert a.music.overlay is not None and not a.music.on
    assert _hash(frames) != GOLDEN["plasma"]


def test_mode_choice_and_older_setups():
    assert react.react_mode({"react": True, "react_mode": "motion"}) == "motion"
    assert react.react_mode({"react": True, "react_mode": "both"}) == "both"
    assert react.react_mode({}) == "effects"  # new setups default to effects only
    assert react.react_mode({"react": True}) == "motion"  # set up before the choice existed: as it was
    assert react.react_mode({"react": True, "react_overlay": True}) == "both"
    _, a = _frames("plasma", {"react": True, "react_mode": "both"}, n=5, signal=_pump)
    assert a.music.motion and a.music.overlay is not None


def test_random_effects_effects_only_keeps_the_picked_app_still():
    _, a = _frames("random_effects", {"react": True, "react_mode": "effects"}, n=3, signal=_pump)
    child = a.cur.app
    if child.id != "visualizer":
        assert not child.music.enabled  # the picked effect moves as usual...
    assert a.music.overlay is not None  # ...and the effects go on top of the whole picture
