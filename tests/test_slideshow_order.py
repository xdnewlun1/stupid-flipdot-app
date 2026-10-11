"""Slideshow play order (apps/slideshow.py): shuffle plays every picture once before any comes round again, keeps
that up across restarts (a playlist coming back to the slideshow) and while pictures are added or removed, and
never shows the same picture twice running. In order is unchanged.
Run: python3 -m pytest -q tests/test_slideshow_order.py"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
from apps import slideshow as ss  # noqa: E402


class _Store:
    GALLERY_DEFAULTS = {"shuffle": False}

    def __init__(self, n):
        self.galleries = {"g": {"id": "g", "name": "G", "items": [{"file": f"p{i}.png"} for i in range(n)],
                                "settings": {"shuffle": True}}}
        self.settings = {}

    def gallery(self, gid):
        g = self.galleries[gid]
        g["settings"] = {**self.GALLERY_DEFAULTS, **g["settings"]}
        return g


class _Ctx:
    def __init__(self, store):
        self.width, self.height, self.store = 84, 42, store


def _show(store, shuffle=True):
    """A slideshow instance, as a (re)start makes one."""
    store.galleries["g"]["settings"]["shuffle"] = shuffle
    return ss.Slideshow(_Ctx(store), gallery="g")


def _step(app, store):
    """What render() does when the next item's frames are ready: pick it and commit it. -> its file"""
    g = store.gallery("g")
    items, settings = g["items"], g["settings"]
    pos, nxt, order = app._peek_next(items, settings)
    if order is not None:
        app.order = order
    app.pos, app.jump_to = pos, None
    if settings["shuffle"]:
        ss._PASSES[app.gid] = {"files": [items[i]["file"] for i in app.order], "pos": app.pos}
    return nxt["file"]


@pytest.fixture(autouse=True)
def _fresh():
    ss._PASSES.clear()
    random.seed(7)
    yield
    ss._PASSES.clear()


def _passes(played, n):
    return [played[i:i + n] for i in range(0, len(played), n)]


def test_every_picture_once_per_pass_and_never_twice_running():
    store = _Store(6)
    app = _show(store)
    played = [_step(app, store) for _ in range(60)]
    for p in _passes(played, 6):
        assert sorted(p) == sorted(f"p{i}.png" for i in range(6))
    assert all(a != b for a, b in zip(played, played[1:]))
    assert len({tuple(p) for p in _passes(played, 6)}) > 1  # a new order each pass


def test_the_pass_carries_on_after_a_restart():
    store = _Store(8)
    played = []
    for _ in range(4):  # a playlist slot long enough for two pictures, four times over
        app = _show(store)
        played += [_step(app, store) for _ in range(2)]
    assert sorted(played) == sorted(f"p{i}.png" for i in range(8))  # eight restarts' worth, each picture once
    app = _show(store)
    assert _step(app, store) != played[-1]


def test_pictures_added_or_removed_mid_pass():
    store = _Store(5)
    app = _show(store)
    first = [_step(app, store) for _ in range(2)]
    store.galleries["g"]["items"].append({"file": "new.png"})  # added: it plays within this pass
    rest = [_step(app, store) for _ in range(4)]
    assert sorted(first + rest) == sorted([f"p{i}.png" for i in range(5)] + ["new.png"])
    gone = rest[-1]
    store.galleries["g"]["items"] = [it for it in store.galleries["g"]["items"] if it["file"] != gone]
    nxt = [_step(app, store) for _ in range(5)]  # a new pass of the five that are left
    assert sorted(nxt) == sorted(it["file"] for it in store.galleries["g"]["items"])
    assert nxt[0] != gone
    # removing the pictures not yet played leaves the pass to end cleanly
    store.galleries["g"]["items"] = store.galleries["g"]["items"][:2]
    for _ in range(4):
        assert _step(app, store) in {"p0.png", "p1.png", "new.png", "p2.png", "p3.png", "p4.png"}


def test_restart_after_an_edit_keeps_what_was_played():
    store = _Store(4)
    app = _show(store)
    done = {_step(app, store), _step(app, store)}
    store.galleries["g"]["items"].insert(0, {"file": "zero.png"})  # the indices all shift
    app = _show(store)
    rest = {_step(app, store) for _ in range(3)}
    assert not (done & rest)
    assert done | rest == {"p0.png", "p1.png", "p2.png", "p3.png", "zero.png"}


def test_in_order_is_unchanged():
    store = _Store(3)
    app = _show(store, shuffle=False)
    assert [_step(app, store) for _ in range(7)] == ["p0.png", "p1.png", "p2.png"] * 2 + ["p0.png"]
    app = _show(store, shuffle=False)
    assert _step(app, store) == "p0.png"  # a restart starts at the top, as before
    assert not ss._PASSES


def test_skip_and_jump_follow_the_pass():
    store = _Store(5)
    app = _show(store)
    cur = _step(app, store)
    app.skip(1)
    nxt = _step(app, store)
    assert nxt != cur and app.pos == 1
    app.skip(-1)
    assert _step(app, store) == cur and app.pos == 0
    app.jump("p3.png")
    assert _step(app, store) == "p3.png"
    played = {cur, "p3.png"}
    assert ss._PASSES["g"]["files"][ss._PASSES["g"]["pos"]] == "p3.png"
    later = [_step(app, store) for _ in range(5 - ss._PASSES["g"]["pos"] - 1)]
    assert played.isdisjoint(later)  # the rest of the pass is what hasn't played
