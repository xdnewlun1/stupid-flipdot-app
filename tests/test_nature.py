"""Nature and automata (apps/pack_nature.py): every app renders cheaply, React to music off is exactly the plain
app, on changes the motion; the aquarium's fish eat what you drop, the ants carry food home along trails, the
Turing patterns start again when they settle, rule 30 matches a reference, Langton's ant builds its highway, the
Voronoi cells fill the board; the mushroom garden follows the rain outside.
Run: python3 -m pytest -q tests/test_nature.py"""

import hashlib
import os
import random
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
import audio_signal  # noqa: E402
from apps import mushrooms, pack_nature, react  # noqa: E402
from canvas import Canvas  # noqa: E402

react.SAMPLE_HZ = 0  # the tests drive the clock

APPS = ["aquarium", "ants", "boids", "dunes", "turing", "wolfram", "langton", "voronoi"]
GOLDEN = {"aquarium": "039d65acfcd052fd", "ants": "2ae3ea0188c9746b", "boids": "816c14e4c364b7eb",
          "dunes": "90c1303428ec905c", "turing": "513f1bc2c70b3850", "wolfram": "4a74338847e940ab",
          "langton": "4136fa6266b2e988", "voronoi": "e581dab0a2c39898"}


class _Store:
    def __init__(self, quiet=False, weather=None):
        self.settings = {"visualizer": {"source": "off"}}
        if weather:
            self.settings["weather"] = weather
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}


class _Ctx:
    def __init__(self, store):
        self.width, self.height, self.store = 84, 42, store


def _pump(now):
    k, hat = now % 0.5 < 0.08, 0.25 <= now % 0.5 < 0.29
    return {"live": True, "loudness": 0.6,
            "spectrum": [0.9 if k else 0.4] * 4 + [0.4] * 8 + [0.8 if hat else 0.3] * 4}


def make(app_id, params=None, store=None, seed=3, t0=1000.0):
    random.seed(seed)
    a = apps.create(_Ctx(store or _Store()), {"app": app_id, "params": params or {}})
    a.started = t0
    return a


def run(a, n, t0=1000.0, signal=None):
    """n frames on the app's own interval (an injected signal on a simulated clock). -> (frames, seconds per frame)"""
    clock = [t0]
    audio_signal.inject(signal or (lambda now: None), clock=lambda: clock[0])
    react._quiet[:] = [0.0, False]
    out, now, cost = [], t0, 0.0
    try:
        for _ in range(n):
            clock[0] = now
            c = Canvas(84, 42)
            t = time.perf_counter()
            a.render(c, now)
            cost += time.perf_counter() - t
            out.append(c)
            now += a.interval
    finally:
        audio_signal.inject(None)
    return out, cost / n


def _hash(frames):
    h = hashlib.sha1()
    for c in frames:
        h.update(bytes(c.px))
    return h.hexdigest()[:16]


def test_every_app_renders_cheaply_and_draws_something():
    for app_id in APPS:
        a = make(app_id)
        frames, per = run(a, 100)
        assert per < 0.012, (app_id, per)  # a few ms on a Pi 5
        assert any(sum(c.px) for c in frames[-20:]), app_id
        assert all(0 < sum(c.px) < 84 * 42 for c in frames[-5:]) or app_id == "wolfram", app_id
        assert a.describe()


def test_off_is_exactly_the_plain_app_and_on_moves_differently():
    for app_id in APPS:
        plain, _ = run(make(app_id), 40)
        assert _hash(plain) == GOLDEN[app_id], app_id
        off, _ = run(make(app_id, {"react": False}), 40, signal=_pump)
        assert _hash(off) == GOLDEN[app_id], app_id
        names = [p["name"] for p in apps.base.REGISTRY[app_id].params]
        assert set(react.REACT_NAMES) <= set(names), app_id
    for app_id in ["aquarium", "ants", "boids", "dunes", "turing", "langton", "voronoi", "wolfram"]:
        a = make(app_id, {"react": True, "react_mode": "motion", "react_follow": "music"})
        on, _ = run(a, 60, signal=_pump)
        assert a.music.on, app_id
        assert _hash(on[:40]) != GOLDEN[app_id], app_id


def test_quiet_hours_slow_every_app_down():
    for app_id in APPS:
        react._quiet[:] = [0.0, False]
        a = make(app_id, store=_Store(quiet=True))
        fast = a.interval
        try:
            a.render(Canvas(84, 42), 1000.0)
            a.render(Canvas(84, 42), 1000.1)
            assert a.interval > fast or app_id == "wolfram", app_id
        finally:
            react._quiet[:] = [0.0, False]


def test_aquarium_fish_eat_what_you_drop():
    a = make("aquarium", {"fish": 8})
    run(a, 30)
    assert not a.food
    a.input("a")
    assert 4 <= len(a.food) <= 7
    run(a, 250, t0=1000.0 + 30 * a.interval)
    assert a.eaten >= 3  # most of it was eaten on the way down
    assert not a.food  # and the rest reached the sand
    b = make("aquarium", {"feed_every": 5})
    run(b, 80)
    assert b.eaten + len(b.food) > 0  # it feeds them itself
    kinds = {f["kind"] for f in a.fish}
    assert len(kinds) >= 3


def test_ants_find_food_and_carry_it_home_along_trails():
    a = make("ants", {"ants": 40, "piles": 2})
    run(a, 700)
    assert a.carried >= 10
    assert max(a.scent) > 0.1  # a trail has been laid...
    assert any(ant["food"] for ant in a.ants) or a.carried > 20
    assert len(a.piles) == 2  # ...and an emptied pile was replaced
    assert all(0 <= ant["x"] < 84 and 0 <= ant["y"] < 42 for ant in a.ants)


def test_boids_flock_and_flee_the_hawk():
    a = make("boids", {"boids": 30, "hawks": 1})
    run(a, 150)
    xs = [b[0] for b in a.boids]
    ys = [b[1] for b in a.boids]
    assert all(0 <= x < 84 and 0 <= y < 42 for x, y in zip(xs, ys))
    # the flock lines up: most neighbours head roughly the same way
    headings = [__import__("math").atan2(b[3], b[2]) for b in a.boids]
    spread = sum(abs(((h - headings[0] + 3.14159) % 6.28318) - 3.14159) < 1.2 for h in headings)
    assert spread >= len(headings) // 3
    b = make("boids", {"edges": "bounce"})
    run(b, 100)
    assert all(0 <= x[0] <= 83 and 0 <= x[1] <= 41 for x in b.boids)


def test_dunes_move_downwind():
    a = make("dunes", {"wind": 1, "grains": False})
    first, _ = run(a, 1)
    later, _ = run(a, 1, t0=1000.0 + 8.0)
    assert first[0].px != later[0].px
    assert sum(first[0].px[40 * 84:]) > 50  # sand along the bottom
    assert sum(first[0].px[:5 * 84]) == 0  # sky on top


def test_turing_grows_a_pattern_and_starts_again():
    a = make("turing", {"pattern": "spots", "max_seconds": 10})
    frames, _ = run(a, 100)  # 10 s
    assert 0.05 < sum(frames[50].px) / (84 * 42) < 0.7
    assert a.kind == "spots"
    seeded = a.seeded_at
    run(a, 15, t0=1000.0 + 100 * a.interval)
    assert a.seeded_at > seeded  # max_seconds passed: a new start
    # a settled (unchanging) picture starts again too
    b = make("turing", {"pattern": "spots", "max_seconds": 3600})
    b.v = [0.0] * len(b.v)
    b.seeded_at = -900.0  # (the clock is seconds since the app started)
    run(b, 35)
    assert b.seeded_at > -900.0  # reseeded on the first frame


def test_wolfram_rule_30_matches_the_reference():
    a = make("wolfram", {"rule": "30", "seed": "single", "rows_per_second": 10})
    rows = [list(a.rows[0])]
    for _ in range(6):
        a.step()
        rows.append(list(a.rows[-1]))
    mid = 42
    want = ["1", "111", "11001", "1101111", "110010001", "11011110111", "1100100001001"]
    for k, w in enumerate(want):
        got = "".join(str(v) for v in rows[k][mid - k:mid + k + 1])
        assert got == w, k
    b = make("wolfram", {"rule": "random"})
    assert 0 <= b.rule < 256


def test_langton_builds_its_highway():
    a = make("langton", {"rule": "RL", "ants": 1, "steps_per_second": 2000})
    a.ants = [[42, 21, 0]]
    a.step(12000)
    x0, y0 = a.ants[0][:2]
    a.step(104)  # the highway: the ant moves 2 cells diagonally every 104 steps
    x1, y1 = a.ants[0][:2]
    assert (abs(x1 - x0) % 84 in (2, 82)) and (abs(y1 - y0) % 42 in (2, 40))
    b = make("langton", {"rule": "LLRR", "ants": 2})
    run(b, 50)
    assert b.k == 4 and max(b.grid) >= 2


def test_voronoi_fills_the_board_and_starts_again():
    a = make("voronoi", {"seeds": 10, "growth": 2, "hold": 1})
    frames, _ = run(a, 150)
    full = [c for c in frames if sum(c.px) > 200]
    assert full  # the boundaries filled the board
    run(a, 200, t0=1000.0 + 150 * a.interval)
    assert a.started_at > 0.0  # held, then started again (the clock is seconds since the app started)
    b = make("voronoi", {"look": "fill"})
    fr, _ = run(b, 120)
    assert max(sum(c.px) for c in fr) > 500  # dithered cells cover a good part of the board


def test_mushrooms_follow_the_rain_outside():
    store = _Store(weather={"lat": 51.5, "lon": -0.12, "name": "Testville"})
    a = make("mushrooms", {"crowd": 10, "weather": "rain"}, store=store)
    assert a.crowd() == 4  # dry (or not known yet): sparse
    fixture = {"current": {"precipitation": 6.0, "weather_code": 63}}
    with mock.patch("apps.widgets.fetch_weather", return_value=fixture) as get:
        a.wx = a.fetch_weather()
    assert get.call_args[0][0] is store
    assert a.wx["mm"] == 6.0
    assert 11 <= a.crowd() <= 15  # a downpour: a flush
    a.wx = {"mm": 0.0, "code": 1}
    assert a.crowd() == 4
    run(a, 20)
    plain = make("mushrooms", {"crowd": 10})
    assert plain.crowd() == 10  # the default behaviour is untouched
    assert apps.base.setup_needed(_Store(), {"app": "mushrooms", "params": {"weather": "rain"}})
    assert apps.base.setup_needed(_Store(), {"app": "mushrooms", "params": {}}) is None
    assert apps.base.setup_needed(store, {"app": "mushrooms", "params": {"weather": "rain"}}) is None
