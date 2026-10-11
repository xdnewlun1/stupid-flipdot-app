"""HTTP routes for People & access (scoped links) and friend messages. server.py calls install(), which
registers these with its @route; which role may call what is in access.ALLOW (everything here is
owner-only except the /api/friend*, /api/me and /api/play/menu routes)."""

import time

import access
import friends
import guestwall
import qr

S = None  # the server module (its store, runner, route, HttpError)


def _error(status, msg):
    return S.HttpError(status, msg)


def base_url():
    """How other devices reach this board, e.g. http://192.168.1.20:8080 (what the share links start with)."""
    return f"http://{guestwall.seen_host or f'{guestwall.lan_ip()}:{guestwall.server_port}'}"


def _link(token):
    url = f"{base_url()}/join/{token}"
    code = qr.encode(url, ecc="M")
    return {"link": url, "qr": code.rows(quiet=2)}


# ---- who am I (every role) -----------------------------------------------------------

def get_me(req):
    tok = getattr(req, "token_rec", None)
    return {"role": req.role, "name": tok["name"] if tok else None, "page": access.PAGES.get(req.role, "/")}


# ---- tokens (owner) --------------------------------------------------------------------

def get_access(req):
    return {"tokens": access.get(S.store).list(), "roles": access.ROLE_LABELS, "base": base_url(),
            "password": bool(S.password)}


def post_access(req):
    body = req.json()
    rec, token = access.get(S.store).create(body.get("name"), body.get("role"), body.get("expires"), body.get("trusted"))
    return {"token": rec, **_link(token)}


def post_access_item(req, tid):
    tokens = access.get(S.store)
    body = req.json()
    try:
        return {"token": tokens.update(tid, **{k: body[k] for k in ("name", "trusted", "expires") if k in body})}
    except KeyError:
        raise _error(404, "That link was revoked already.") from None


def post_access_action(req, tid, action):
    tokens = access.get(S.store)
    try:
        if action == "revoke":
            tokens.revoke(tid)
            return {"tokens": tokens.list()}
        rec, token = tokens.rotate(tid)
        return {"token": rec, **_link(token)}
    except KeyError:
        raise _error(404, "That link was revoked already.") from None


def post_access_qr(req):
    """Show a share link as a QR code on the board for a minute (the link comes from the owner's page)."""
    from apps.wifiqr import fit_qr
    from canvas import Canvas
    from fonts import NORMAL, SMALL
    body = req.json()
    url = str(body.get("link") or "")
    if not url.startswith(("http://", "https://")) or "/join/" not in url or len(url) > 200:
        raise ValueError("send the share link")
    label = str(body.get("name") or "")[:20]
    c = Canvas(S.board.width, S.board.height)
    code, quiet, inverted = fit_qr(url, c.h)
    side = code.size + 2 * quiet
    qr.draw(c, code, 0, (c.h - side) // 2, quiet=quiet, invert=inverted)
    x = side + 3
    for i, line in enumerate(["Scan me", label.upper()]):
        font = NORMAL if i == 0 else SMALL
        while line and font.width(line) > c.w - x:
            line = line[:-1]
        c.text(x + max(0, (c.w - x - font.width(line)) // 2), 14 + i * 10, line, font)
    app = S.apps.REGISTRY["scene"](S.runner.ctx, frames=[{"rows": c.rows(), "delay": 1000}], name="share link")
    S.runner.notify(app=app, duration=float(body.get("duration") or 60), transition="dissolve")
    return {"ok": True}


# ---- the friend page (friend role) -------------------------------------------------------

def _friend(req):
    tok = getattr(req, "token_rec", None)
    if not tok or tok.get("role") != "friend":
        raise _error(403, "Open this page from a friend link.")
    return tok


def get_friend(req):
    tok = _friend(req)
    box = friends.get(S.store)
    import fonts
    font_list = [f for f in fonts.font_list() if f["kind"] in ("pixel", "uploaded")]
    return {"name": tok["name"], "trusted": bool(tok.get("trusted")), "width": S.board.width, "height": S.board.height,
            "max_length": box.settings["max_length"], "lines": friends.LINES_MAX, "fonts": font_list,
            "transitions": friends.TRANSITIONS, "mine": box.mine(tok["id"]), "now": int(time.time())}


def _body(req):
    if len(req.raw()) > friends.BODY_MAX:
        raise _error(413, "That message is too big.")
    return req.json()


def post_friend_preview(req):
    tok = _friend(req)
    from apps.pack_friends import render_message
    box = friends.get(S.store)
    body = _body(req)
    text, style, doodle, _ = box.compose({**body, "at": None} if isinstance(body, dict) else body)
    c = render_message({"name": tok["name"], "text": text, "style": style, "doodle": doodle}, S.board.width, S.board.height)
    return {"rows": c.rows()}


def post_friend_send(req):
    tok = _friend(req)
    box = friends.get(S.store)
    try:
        msg = box.submit(tok, _body(req))
    except friends.RateLimited as e:
        raise _error(429, str(e)) from None
    return {"ok": True, "status": msg["status"], "at": msg["at"], "mine": box.mine(tok["id"])}


# ---- inbox (owner) -------------------------------------------------------------------

def _inbox(box):
    """The inbox, with a rendered thumbnail for messages waiting and the newest others."""
    from apps.pack_friends import render_message
    info = box.info()
    for i, m in enumerate(info["messages"]):
        if m["status"] == "pending" or i < 20:
            info["messages"][i] = {**m, "rows": render_message(m, S.board.width, S.board.height).rows()}
    return info


def get_friends(req):
    return _inbox(friends.get(S.store))


def post_friends_settings(req):
    box = friends.get(S.store)
    box.set_settings(req.json())
    return _inbox(box)


def post_friends_message(req, mid, action):
    box = friends.get(S.store)
    try:
        box.act(mid, action, hours=req.json().get("hours") if req.raw() else None)
    except KeyError:
        raise _error(404, "message not found (maybe already deleted)") from None
    box.tick()
    return _inbox(box)


# ---- the game controller (player role) -------------------------------------------------------

_menu = None


def post_play_menu(req):
    """Open the on-board game menu, as holding Start on a gamepad does."""
    global _menu
    from apps import gamemenu
    if _menu is None:
        _menu = gamemenu.GamepadMenu(S.runner, S.store)
    _menu.open(int((req.json() if req.raw() else {}).get("player", 1)))
    return S.state()


def install(server):
    global S
    S = server
    route = server.route
    route("GET", "/api/me")(get_me)
    route("GET", "/api/access")(get_access)
    route("POST", "/api/access")(post_access)
    route("POST", "/api/access/qr")(post_access_qr)
    route("POST", "/api/access/(t[0-9a-f]+)")(post_access_item)
    route("POST", "/api/access/(t[0-9a-f]+)/(revoke|rotate)")(post_access_action)
    route("GET", "/api/friend")(get_friend)
    route("POST", "/api/friend/preview")(post_friend_preview)
    route("POST", "/api/friend/send")(post_friend_send)
    route("GET", "/api/friends")(get_friends)
    route("POST", "/api/friends/settings")(post_friends_settings)
    route("POST", "/api/friends/messages/(f[0-9]+)/(approve|decline|delete|show|pin|unpin)")(post_friends_message)
    route("POST", "/api/play/menu")(post_play_menu)
