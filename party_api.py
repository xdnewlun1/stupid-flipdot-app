"""HTTP and WebSocket input for the party games' /play modes (apps/pack_party.py and apps/party_*.py): the quiz's
buzzer and answers, Pictionary's drawing and guesses, the drum machine's grid. server.py calls install(), which
registers the routes with its @route; the player role may call both (access.ALLOW) and send "party" messages
over the WebSocket (access.WS_TYPES), so a /join player link can play.

    GET  /api/party?seat=2              -> {"app": "quiz", "party": {...what seat 2's phone shows...}}
    POST /api/party {"action": "buzz", "seat": 2, "data": {...}}
    WebSocket {"type": "party", "action": "draw", "seat": 1, "data": {"segs": [[x0, y0, x1, y1]], "v": 1}}
"""

S = None  # the server module (runner, route, HttpError)


def _seat(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 1
    return v if 1 <= v <= 4 else 1


def _app():
    """The running party game (not a notification over it), or None."""
    app = S.runner.app
    return app if app is not None and callable(getattr(app, "phone_state", None)) else None


def get_party(req):
    seat = _seat((req.query.get("seat") or ["1"])[0])
    with S.runner.lock:
        app = _app()
        return {"app": app.id if app else None, "party": app.phone_state(seat) if app else None}


def act(body, reply=True):
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    action = str(body.get("action") or "")
    if not action:
        raise ValueError("action is missing")
    seat = _seat(body.get("seat"))
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    with S.runner.lock:
        app = _app()
        if app is None:
            raise S.HttpError(409, "No party game is running.")
        out = app.phone(action, data, seat) or {}
        S.runner.wake.set()
        return {"ok": True, **out, "party": app.phone_state(seat)} if reply else None


def post_party(req):
    return act(req.json())


def ws(msg):
    """A "party" WebSocket message (drawing strokes, drum edits: no reply needed)."""
    act(msg, reply=False)


def install(server):
    global S
    S = server
    server.route("GET", "/api/party")(get_party)    # player + owner
    server.route("POST", "/api/party")(post_party)  # player + owner
