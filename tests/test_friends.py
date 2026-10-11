"""People & access (roles and share links) and friend messages.
Run: python3 -m unittest tests.test_friends"""

import base64
import http.client
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import access  # noqa: E402
import flipdot  # noqa: E402
import friends  # noqa: E402
import runner as runner_mod  # noqa: E402
import server  # noqa: E402
from store import Store  # noqa: E402
from test_board import config  # noqa: E402
from ws import Hub  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "owner-secret-pw"


def sample_path(pattern):
    """A concrete path that the route pattern matches (groups get a plausible value)."""
    p = re.sub(r"\(\?:[^)]*\)\?", "", pattern)
    p = re.sub(r"\(([A-Za-z_-]+(?:\|[A-Za-z_-]+)+)\)", lambda m: m.group(1).split("|")[0], p)
    p = re.sub(r"\[[^\]]*\][+*]", "1", p)
    p = p.replace("(", "").replace(")", "").replace("\\", "")
    return p


class FakeRunner:
    def __init__(self):
        self.notif = None
        self.ctx = runner_mod.Context(84, 42, None)
        self.calls = []
        self.kw = []
        self.dismissed = []

    def notify(self, app=None, duration=10, transition="dissolve", **kw):
        self.calls.append((app, duration))
        self.kw.append(kw)
        self.notif = {"app": app, "until": time.time() + duration}

    def dismiss(self, eid=None, key=None):
        self.dismissed.append(key)


class FriendsLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.box = friends.Friends(self.store)
        self.runner = FakeRunner()
        self.box.runner, self.box.notify = self.runner, self.runner.notify
        self.trusted = {"id": "t1", "name": "Mia", "trusted": True}
        self.untrusted = {"id": "t2", "name": "Sam", "trusted": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_trusted_shows_right_away(self):
        m = self.box.submit(self.trusted, {"text": "Hello"})
        self.assertEqual(m["status"], "approved")
        self.box.tick()
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.runner.calls[0][0].msg["name"], "Mia")
        self.assertEqual(self.runner.calls[0][1], friends.DEFAULTS["display_seconds"])
        self.box.tick()
        self.assertEqual(len(self.runner.calls), 1, "shown once")

    def test_untrusted_waits_for_approval(self):
        m = self.box.submit(self.untrusted, {"text": "Hi there"})
        self.assertEqual(m["status"], "pending")
        self.box.tick()
        self.assertEqual(self.runner.calls, [])
        self.box.act(m["id"], "approve")
        self.box.tick()
        self.assertEqual(len(self.runner.calls), 1)

    def test_declined_never_shows(self):
        m = self.box.submit(self.untrusted, {"text": "nope"})
        self.box.act(m["id"], "decline")
        self.box.tick()
        self.assertEqual(self.runner.calls, [])

    def test_scheduled_delivery(self):
        now = time.time()
        m = self.box.submit(self.trusted, {"text": "Happy birthday", "at": now + 3600}, now=now)
        self.assertEqual(m["at"], int(now + 3600))
        self.box.tick(now + 60)
        self.assertEqual(self.runner.calls, [], "not before its time")
        self.box.tick(now + 3601)
        self.assertEqual(len(self.runner.calls), 1)
        # a scheduled message from an untrusted friend, approved early, still waits for its time
        m2 = self.box.submit(self.untrusted, {"text": "Later", "at": now + 7200}, now=now)
        self.box.act(m2["id"], "approve")
        self.runner.notif = None
        self.box.tick(now + 3700)
        self.assertEqual(len(self.runner.calls), 1)
        self.box.tick(now + 7201)
        self.assertEqual(len(self.runner.calls), 2)

    def test_schedule_limits(self):
        self.assertIsNone(friends.clean_time(time.time() - 100), "a past time means now")
        with self.assertRaises(ValueError):
            friends.clean_time(time.time() + 500 * 86400)

    def test_goes_through_the_queue(self):
        self.box.submit(self.trusted, {"text": "Hello"})
        self.box.submit(self.trusted, {"text": "Hello"})
        self.box.tick()
        self.assertEqual(len(self.runner.calls), 2, "both handed to the queue, which decides when")
        kw = self.runner.kw[0]
        self.assertEqual((kw["priority"], kw["source"], kw["dedupe"]), ("normal", "friend", False))
        self.assertEqual(kw["text"], "Mia: Hello")
        self.assertEqual(kw["remake"]().msg["text"], "Hello", "the queue can show it again")

    def test_rate_limit_per_token(self):
        self.box.set_settings({"rate_count": 2, "rate_minutes": 10})
        self.box.submit(self.trusted, {"text": "one"})
        self.box.submit(self.trusted, {"text": "two"})
        with self.assertRaises(friends.RateLimited):
            self.box.submit(self.trusted, {"text": "three"})
        self.box.submit(self.untrusted, {"text": "another friend isn't limited by Mia's sends"})
        self.assertTrue(self.box.rate_ok("t1", now=time.time() + 601), "the window passes")

    def test_max_length_and_empty(self):
        self.box.set_settings({"max_length": 20})
        with self.assertRaises(ValueError):
            self.box.submit(self.trusted, {"text": "x" * 21})
        with self.assertRaises(ValueError):
            self.box.submit(self.trusted, {"text": "   "})
        self.box.submit(self.trusted, {"text": "x" * 20})

    def test_profanity_filter(self):
        self.assertTrue(friends.profane("what the fuck"))
        self.assertTrue(friends.profane("SH1T happens"))
        self.assertFalse(friends.profane("Scunthorpe classic"))
        m = self.box.submit(self.trusted, {"text": "oh shit"})
        self.assertEqual(m["status"], "approved", "off by default")
        self.box.set_settings({"profanity_filter": True})
        m = self.box.submit(self.trusted, {"text": "oh shit"})
        self.assertEqual((m["status"], m["flagged"]), ("pending", True), "held for the owner, even from a trusted friend")

    def test_doodle_and_style_checked(self):
        rows = ["0" * 84] * 42
        rows = rows[:10] + ["1" * 84] + rows[11:]
        m = self.box.submit(self.trusted, {"doodle": rows, "style": {"font": "<script>", "size": "9", "align": "left"}})
        self.assertEqual(m["doodle"][10], "1" * 84)
        self.assertEqual(m["style"]["font"], "normal")
        self.assertEqual(m["style"]["size"], "fit")
        self.assertEqual(m["style"]["align"], "left")
        with self.assertRaises(ValueError):
            self.box.submit(self.trusted, {"text": "x", "doodle": ["1" * 10]})

    def test_pin_and_unpin(self):
        m = self.box.submit(self.trusted, {"text": "Pinned"})
        self.box.tick()
        self.runner.notif = None
        self.box.act(m["id"], "pin", hours=2)
        self.box.tick()
        app, duration = self.runner.calls[-1]
        self.assertAlmostEqual(duration, 7200, delta=5)
        self.assertEqual((self.runner.kw[-1]["priority"], self.runner.kw[-1]["key"]), ("low", friends.PIN_KEY),
                         "other notifications go first, then it comes back")
        self.assertEqual(self.box.recent()[0]["id"], m["id"])
        self.box.tick()
        self.assertEqual(len(self.runner.calls), 2, "handed over once")
        self.box.act(m["id"], "pin", hours=1)
        self.box.tick()
        self.assertAlmostEqual(self.runner.calls[-1][1], 3600, delta=5)
        self.box.act(m["id"], "unpin")
        self.box.tick()
        self.assertEqual(self.runner.dismissed, [friends.PIN_KEY], "taken down")

    def test_message_frame(self):
        from apps.pack_friends import render_message
        c = render_message({"name": "Mia", "text": "Hi", "style": {}}, 84, 42)
        self.assertTrue(any("1" in r for r in c.rows()[:6]), "the name is on top")
        self.assertTrue(any("1" in r for r in c.rows()[8:]))


class Tokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.tokens = access.Tokens(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stored_hashed(self):
        rec, token = self.tokens.create("Mia", "friend")
        saved = (Path(self.store.dir) / access.FILE).read_text()
        self.assertNotIn(token, saved)
        self.assertIn(access.digest(token), saved)
        self.assertNotIn("hash", rec)
        self.assertEqual(self.tokens.check(token)["id"], rec["id"])
        self.assertIsNone(self.tokens.check(token + "x"))

    def test_expiry(self):
        now = time.time()
        with self.assertRaises(ValueError):
            self.tokens.create("Old", "viewer", expires=now - 10)
        rec, token = self.tokens.create("Soon", "viewer", expires=now + 60)
        self.assertIsNotNone(self.tokens.check(token, now=now))
        self.assertIsNone(self.tokens.check(token, now=now + 61))

    def test_revoke_and_rotate(self):
        rec, token = self.tokens.create("Sam", "player")
        rec2, token2 = self.tokens.rotate(rec["id"])
        self.assertIsNone(self.tokens.check(token), "the old link stops working")
        self.assertIsNotNone(self.tokens.check(token2))
        self.tokens.revoke(rec["id"])
        self.assertIsNone(self.tokens.check(token2))

    def test_roles(self):
        with self.assertRaises(ValueError):
            self.tokens.create("X", "owner")
        with self.assertRaises(ValueError):
            self.tokens.create("", "friend")


class Server(unittest.TestCase):
    """The real HTTP handler on a loopback port, with a password set."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: getattr(server, k) for k in ("board", "store", "runner", "hub", "password")}
        server.board = flipdot.Board(config(), dry_run=True)
        server.store = Store(os.path.join(cls.tmp.name, "data"))
        server.runner = runner_mod.Runner(server.board, server.store)
        server.hub = Hub(server.board, server.runner, server.ws_message)
        server.password = PASSWORD
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        tokens = access.get(server.store)
        cls.tok = {}
        for role in access.ROLES:
            cls.tok[role] = tokens.create(f"{role} person", role, trusted=role == "friend")[1]
        cls.untrusted = tokens.create("Untrusted", "friend")[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.runner.stop()
        for k, v in cls.saved.items():
            setattr(server, k, v)
        cls.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def request(self, method, path, body=None, token=None, owner=False, headers=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        if owner:
            h["Authorization"] = "Basic " + base64.b64encode(f"x:{PASSWORD}".encode()).decode()
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            raw = r.read()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw
            return r.status, payload, r
        finally:
            conn.close()

    def test_every_route_is_classified(self):
        """Each route × role: owner-only routes refuse every link role with 403; allowed ones don't."""
        checked = 0
        for methods, pattern, _ in server.ROUTES:
            raw = pattern.pattern[1:-1]
            path = sample_path(raw)
            self.assertTrue(pattern.match(path), f"no sample path for {raw}")
            for method in methods.split("|"):
                for role in access.ROLES:
                    allowed = (method, raw) in access.ALLOW[role]
                    if allowed and raw in ("/api/run",):
                        continue  # covered below (games only)
                    status, body, _ = self.request(method, path, body={} if method == "POST" else None, token=self.tok[role])
                    if allowed:
                        self.assertNotEqual(status, 403, f"{role} should reach {method} {raw}: {body}")
                    else:
                        self.assertEqual(status, 403, f"{role} must not reach {method} {raw}")
                    checked += 1
        self.assertGreater(checked, 300)
        # and the allowlists only name routes that exist
        known = {(m, p.pattern[1:-1]) for ms, p, _ in server.ROUTES for m in ms.split("|")}
        for role, allowed in access.ALLOW.items():
            self.assertLessEqual(allowed, known, f"{role} allowlist names a route that doesn't exist")

    def test_owner_still_reaches_everything(self):
        self.assertEqual(self.request("GET", "/api/settings", owner=True)[0], 200)
        self.assertEqual(self.request("GET", "/api/access", owner=True)[0], 200)
        self.assertEqual(self.request("GET", "/api/settings")[0], 401, "no password, no access")

    def test_player_runs_games_only(self):
        status, body, _ = self.request("POST", "/api/run", {"app": "snake"}, token=self.tok["player"])
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("POST", "/api/run", {"app": "clock"}, token=self.tok["player"])[0], 403)
        self.assertEqual(self.request("POST", "/api/input", {"key": "up"}, token=self.tok["player"])[0], 200)
        self.assertEqual(self.request("POST", "/api/play/menu", {"player": 1}, token=self.tok["player"])[0], 200)
        server.runner.stop()

    def test_pages_per_role(self):
        for role, page in access.PAGES.items():
            self.assertEqual(self.request("GET", page, token=self.tok[role])[0], 200)
            status, _, r = self.request("GET", "/", token=self.tok[role])
            self.assertEqual((status, r.getheader("Location")), (302, page))
            self.assertEqual(self.request("GET", "/static/style.css", token=self.tok[role])[0], 200)
            for other in set(access.PAGES.values()) - {page}:
                self.assertEqual(self.request("GET", other, token=self.tok[role])[0], 403)

    def test_join_link_sets_cookie(self):
        status, _, r = self.request("GET", f"/join/{self.tok['friend']}")
        self.assertEqual((status, r.getheader("Location")), (303, "/friend"))
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn(PASSWORD, cookie)
        value = cookie.split(";")[0]
        status, body, _ = self.request("GET", "/api/friend", headers={"Cookie": value})
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "friend person")
        self.assertEqual(self.request("GET", "/api/settings", headers={"Cookie": value})[0], 403)
        self.assertEqual(self.request("GET", "/join/not-a-real-token-at-all")[0], 404)

    def test_share_link_has_no_password(self):
        status, body, _ = self.request("POST", "/api/access", {"name": "Link", "role": "viewer"}, owner=True)
        self.assertEqual(status, 200)
        self.assertNotIn(PASSWORD, body["link"])
        self.assertIn("/join/", body["link"])
        self.assertTrue(body["qr"])

    def test_revoked_and_expired_links(self):
        status, body, _ = self.request("POST", "/api/access", {"name": "Brief", "role": "friend"}, owner=True)
        token = body["link"].rsplit("/", 1)[1]
        self.assertEqual(self.request("GET", "/api/friend", token=token)[0], 200)
        self.request("POST", f"/api/access/{body['token']['id']}/revoke", {}, owner=True)
        status, body2, r = self.request("GET", "/api/friend", token=token)
        self.assertEqual(status, 401)
        self.assertIn("doesn't work", body2["error"])
        status, _, r = self.request("GET", "/friend", headers={"Cookie": f"{access.COOKIE}={token}"})
        self.assertEqual(status, 401)
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"), "a dead link is forgotten")
        # expired
        status, body, _ = self.request("POST", "/api/access", {"name": "Exp", "role": "viewer", "expires": time.time() + 3600}, owner=True)
        token = body["link"].rsplit("/", 1)[1]
        rec = access.get(server.store).get(body["token"]["id"])
        rec["expires"] = int(time.time()) - 1
        self.assertEqual(self.request("GET", "/api/board", token=token)[0], 401)

    def test_friend_flow(self):
        status, body, _ = self.request("POST", "/api/friend/preview", {"text": "Hi <b>there</b>"}, token=self.tok["friend"])
        self.assertEqual(status, 200)
        self.assertEqual(len(body["rows"]), 42)
        status, body, _ = self.request("POST", "/api/friend/send", {"text": "From a trusted friend"}, token=self.tok["friend"])
        self.assertEqual((status, body["status"]), (200, "approved"))
        status, body, _ = self.request("POST", "/api/friend/send", {"text": "<img src=x onerror=alert(1)>"}, token=self.untrusted)
        self.assertEqual((status, body["status"]), (200, "pending"))
        status, inbox, _ = self.request("GET", "/api/friends", owner=True)
        pending = [m for m in inbox["messages"] if m["status"] == "pending"]
        self.assertEqual(pending[0]["text"], "<img src=x onerror=alert(1)>", "kept as text; the pages never parse it as HTML")
        status, inbox, _ = self.request("POST", f"/api/friends/messages/{pending[0]['id']}/approve", {}, owner=True)
        self.assertEqual(status, 200)
        # the friend can't moderate their own message
        self.assertEqual(self.request("POST", f"/api/friends/messages/{pending[0]['id']}/approve", {}, token=self.untrusted)[0], 403)

    def test_rate_limit_http(self):
        tok = access.get(server.store).create("Chatty", "friend", trusted=True)[1]
        box = friends.get(server.store)
        old = dict(box.settings)
        box.set_settings({"rate_count": 1})
        try:
            self.assertEqual(self.request("POST", "/api/friend/send", {"text": "one"}, token=tok)[0], 200)
            self.assertEqual(self.request("POST", "/api/friend/send", {"text": "two"}, token=tok)[0], 429)
        finally:
            box.set_settings(old)

    def test_body_size_limit_for_links(self):
        status, _, _ = self.request("POST", "/api/friend/send", {"text": "x" * (access.BODY_MAX + 10)}, token=self.tok["friend"])
        self.assertEqual(status, 413)

    def test_guest_wall_still_public(self):
        status, body, _ = self.request("POST", "/api/guest/submit", {"name": "G", "message": "hello"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("GET", "/guest")[0], 200)

    def ws_open(self, token):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n"
                   f"Authorization: Bearer {token}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1)
            if not chunk:
                break
            head += chunk
        return s, head.split(b"\r\n")[0].decode()

    def test_websocket_roles(self):
        self.assertIn("403", self.ws_open(self.tok["friend"])[1])
        s, status = self.ws_open(self.tok["player"])
        self.assertIn("101", status)
        payload = json.dumps({"type": "dots", "dots": [{"x": 0, "y": 0, "color": "white"}]}).encode()
        mask = b"\x01\x02\x03\x04"
        s.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        rf = s.makefile("rb")
        from ws import recv_message
        for _ in range(10):
            op, data = recv_message(rf)
            if op == 0x1 and json.loads(data)["type"] == "error":
                self.assertIn("not allowed", json.loads(data)["error"])
                break
        else:
            self.fail("drawing over the player's WebSocket wasn't refused")
        s.close()

    def test_pages_escape_text(self):
        for f in ("static/friend.html", "static/js/friends.js", "static/js/people.js", "static/view.html"):
            self.assertNotIn("innerHTML", (ROOT / f).read_text(), f)


if __name__ == "__main__":
    unittest.main()
