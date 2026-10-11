"""Guest doodles (guestwall.py, apps/guestwall.py, POST /api/guest/submit) and the leaderboard API.
Run: python3 -m pytest -q tests/test_guest_doodle.py"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
import guestwall  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

SMILE = ["0" * 21] * 5 + ["000001000000010000000"] * 3 + ["0" * 21] * 4 + ["000010000000001000000", "000001111111110000000"] + ["0" * 21] * 7


class Store:
    def __init__(self):
        self.saved, self.settings = {}, {}

    def _load(self, name, default):
        return json.loads(json.dumps(self.saved.get(name, default)))

    def _save(self, name, data):
        self.saved[name] = json.loads(json.dumps(data))


def test_doodles_are_checked_and_kept_with_the_message():
    assert guestwall.clean_doodle(SMILE) == SMILE
    assert guestwall.clean_doodle(["0" * 21] * 21) is None  # blank: no doodle
    assert guestwall.clean_doodle(SMILE[:20]) is None
    assert guestwall.clean_doodle(["2" * 21] * 21) is None
    assert guestwall.clean_doodle("nope") is None
    wall = guestwall.GuestWall(Store())
    m = wall.submit("Ann", "", doodle=SMILE)  # a doodle alone is enough
    assert m["doodle"] == SMILE and m["status"] == "pending"
    try:
        wall.submit("Bo", "", doodle=["0" * 21] * 21)
        raise AssertionError("an empty message with a blank doodle was accepted")
    except ValueError:
        pass
    assert "doodle" not in wall.submit("Cy", "hello")


def test_doodles_are_moderated_with_their_message():
    st = Store()
    wall = guestwall.get(st)
    m = wall.submit("Ann", "hi there", doodle=SMILE)
    assert wall.approved() == []
    wall.set_status(m["id"], "approved")
    assert wall.approved()[0]["doodle"] == SMILE
    wall.set_status(m["id"], "rejected")
    assert wall.approved() == []


def test_the_board_shows_the_doodle_beside_the_scrolling_text():
    st = Store()
    wall = guestwall.get(st)
    wall.set_auto_approve(True)
    wall.submit("Ann", "hello from the party", doodle=SMILE)
    wall.submit("", "", doodle=SMILE)
    a = apps.create(Context(84, 42, st), {"app": "guest_wall", "params": {"qr_seconds": 3}})
    a.render(Canvas(84, 42), 100.0)  # the QR code first
    a.render(Canvas(84, 42), 103.5)  # its time is up: the first message comes next
    y0 = (42 - 21) // 2
    shown = 0
    for t in range(0, 200):
        c = Canvas(84, 42)
        a.render(c, 103.6 + t * 0.05)
        if a.phase and a.phase[0] == "msg" and a.phase[4] is not None:
            shown += 1
            assert ["".join(str(c.get(1 + x, y0 + y)) for x in range(21)) for y in range(21)] == SMILE
            for y in range(42):  # the divider, and the scrolling text never runs over the doodle
                assert c.get(0, y) == 0 and c.get(22, y) == 0 and c.get(23, y) == 1
    assert shown > 20


def test_leaderboard_api():
    import server
    from apps import scores
    old = server.store
    try:
        server.store = Store()
        scores.submit(server.store, "snake", 7)
        scores.award(server.store, "snake", 0, False)
        d = server.get_leaderboard(None)
        assert d["scores"]["snake"]["top"][0]["score"] == 7
        assert d["today"][0]["key"] == "snake"
        assert d["played"] == {"snake": 1}
        assert next(a for a in d["achievements"] if a["id"] == "first")["at"]
        assert d["featured"]["id"] in apps.REGISTRY and d["titles"]["snake"] == "Snake"
    finally:
        server.store = old
