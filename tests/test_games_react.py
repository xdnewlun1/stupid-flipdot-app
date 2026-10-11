"""Music effects on games (apps/react.py game_music / attach_game_music): off draws exactly what the game drew,
a game's own React to music puts the slideshows' effects over its frames, the global setting (Settings → Music →
Music effects on games) does the same for games started without it, and the game itself never changes."""

import hashlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_signal  # noqa: E402
import apps  # noqa: E402
from apps import react  # noqa: E402
from apps.base import REGISTRY, create  # noqa: E402
from canvas import Canvas  # noqa: E402

react.SAMPLE_HZ = 0


class _Store:
    def __init__(self, games=None, quiet=False):
        self.settings = {"visualizer": {"source": "off"}}
        if games is not None:
            self.settings["games"] = {"music": games}
        if quiet:
            self.settings["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}


class _Ctx:
    def __init__(self, store):
        self.width, self.height, self.store = 84, 42, store


def _pump(now):
    k = now % 0.5 < 0.08
    return {"live": True, "loudness": 0.6, "spectrum": [0.9 if k else 0.4] * 4 + [0.4] * 12}


def _beat_rows():
    rows = react.overlay_defaults()
    for r in rows:
        r.update(effect="invert", threshold=0) if r["signal"] == "beat" else r.update(effect="none")
    return rows


def _frames(app_id, params=None, store=None, n=40, signal=_pump):
    orig = random.Random
    random.Random = lambda *a: orig(7)
    random.seed(3)
    clock = [1000.0]
    audio_signal.inject(signal, clock=lambda: clock[0])
    react._quiet[:] = [0.0, False]
    try:
        a = create(_Ctx(store or _Store()), {"app": app_id, "params": params or {}})
        a.started = 1000.0
        a.input("a", "down", 1)  # start the game
        out, now = [], 1000.0
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


def test_every_game_has_the_option():
    from apps.games import Game
    n = 0
    for app_id, cls in REGISTRY.items():
        if issubclass(cls, Game) or app_id in ("doom", "nes"):
            names = [p["name"] for p in cls.params]
            assert "react" in names and "react_fx" in names, app_id
            assert names.count("react") == 1, app_id
            assert "react_mode" not in names, app_id  # games never get the motion
            n += 1
    assert n > 40
    # interactive apps that aren't games (the hourglass, the game menu) are left alone
    store = _Store({"enabled": True, "fx": _beat_rows()})
    a = create(_Ctx(store), {"app": "hourglass", "params": {}})
    assert getattr(a, "music", None) is None


def test_off_is_exactly_the_game():
    plain, a = _frames("snake", signal=lambda now: None)
    assert not a.music.enabled
    loud, b = _frames("snake", {"react": False})
    assert _hash(plain) == _hash(loud)


def test_a_games_own_option_puts_effects_on_top():
    plain, _ = _frames("snake")
    over, a = _frames("snake", {"react": True, "react_fx": _beat_rows()})
    assert a.music.enabled and a.music.mode == "effects" and not a.music.motion
    assert a.music.listening and a.music.overlay is not None
    assert _hash(over) != _hash(plain)
    # the game underneath is the same: undo the inverts and compare
    same = 0
    for p, o in zip(plain, over):
        if bytes(p.px) == bytes(o.px) or bytes(p.px) == bytes(bytearray(v ^ 1 for v in o.px)):
            same += 1
    assert same == len(plain)
    none, _ = _frames("snake", {"react": True, "react_fx": _beat_rows(), "react_amount": 0})
    assert _hash(none) == _hash(plain)


def test_global_setting_applies_to_games_without_their_own():
    store = _Store({"enabled": True, "amount": 100, "fallback": "none", "fx": _beat_rows()})
    plain, _ = _frames("snake")
    over, a = _frames("snake", store=store)
    assert a.music.enabled and a.music.overlay is not None
    assert _hash(over) != _hash(plain)
    off, b = _frames("snake", store=_Store({"enabled": False, "fx": _beat_rows()}))
    assert not b.music.enabled and _hash(off) == _hash(plain)
    quiet, q = _frames("snake", store=_Store({"enabled": True, "fx": _beat_rows()}, quiet=True))
    assert _hash(quiet) == _hash(plain)  # quiet hours: nothing on top


def test_games_music_settings_defaults():
    cfg = react.games_music_settings(_Store())
    assert cfg["enabled"] is False and cfg["amount"] == 100 and cfg["fx"]
    cfg = react.games_music_settings(_Store({"enabled": True, "amount": 150, "bogus": 1}))
    assert cfg["enabled"] and cfg["amount"] == 150 and "bogus" not in cfg


def test_party_and_doom_get_it_too():
    for app_id in ("party", "doom", "nes"):
        names = [p["name"] for p in REGISTRY[app_id].params]
        assert "react_fx" in names, app_id
    store = _Store({"enabled": True, "fx": _beat_rows()})
    a = create(_Ctx(store), {"app": "party", "params": {}})
    assert a.music.enabled
