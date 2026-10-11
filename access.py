"""People & access: scoped links besides the owner password.

Roles
    owner   everything (the config.yaml password, or anyone on the network when there is no password)
    player  the /play controller: board preview, game input, picking a game, the on-board game menu
    friend  the friend message page: compose, preview and send messages
    viewer  a read-only board preview with what's playing

A token is a random secret shown once, in the share link (/join/<token>). Only its SHA-256 is saved
(access.json, synced to storage.db like every other save). Opening the link sets an HttpOnly,
SameSite=Lax cookie and sends the browser to the role's page. Scripts can send it as
"Authorization: Bearer <token>" instead.

Every API route is owner-only unless it is listed in ALLOW for a role (default deny), and so is
every page except the role's own page and /static/ files (code and styles, no data).
"""

import hashlib
import hmac
import re
import secrets
import threading
import time

FILE = "access.json"
COOKIE = "flipdot_access"
ROLES = ("player", "friend", "viewer")  # the roles a link can have; "owner" is the password
ROLE_LABELS = {"player": "Player", "friend": "Friend", "viewer": "Viewer"}
NAME_MAX = 40
MAX_TOKENS = 200
TOUCH_EVERY = 600  # save "last used" at most this often (seconds) per link
BODY_MAX = 64 * 1024  # request bodies accepted from links (the owner's uploads can be bigger)
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
GONE = "This link doesn't work any more: it was revoked or has expired. Ask for a new one."
GONE_PAGE = (b'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
             b'<title>Link expired</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:420px;margin:15vh auto;padding:0 16px;'
             b'background:#0e0f11;color:#ececee}a{color:#e9b949}@media(prefers-color-scheme:light){body{background:#f5f5f3;color:#18181b}a{color:#8a6104}}</style>'
             b'<h1 style="font-size:22px;font-weight:600">This link doesn&#39;t work any more</h1>'
             b'<p>It was revoked or has expired. Ask the person who sent it for a new one.</p><p><a href="/">Continue</a></p></html>')

# (method, route pattern exactly as written in @route) a role may call; anything else is owner-only
ALLOW = {
    "player": {("GET", "/api/board"), ("POST", "/api/input"), ("POST", "/api/run"), ("POST", "/api/play/menu"),
               ("GET", "/api/me"), ("GET", "/api/party"), ("POST", "/api/party")},
    "friend": {("GET", "/api/friend"), ("POST", "/api/friend/preview"), ("POST", "/api/friend/send"), ("GET", "/api/me")},
    "viewer": {("GET", "/api/board"), ("GET", "/api/me")},
}
PAGES = {"player": "/play", "friend": "/friend", "viewer": "/view"}  # where each role's link opens
WS_TYPES = {"player": {"input", "party"}, "viewer": set()}  # WebSocket messages a role may send (no entry: no WebSocket)


def allowed(role, method, pattern):
    """May `role` call the route registered with `pattern` using `method`?"""
    return role == "owner" or (method, pattern) in ALLOW.get(role, ())


def page_allowed(role, path):
    return role == "owner" or path == PAGES.get(role) or path.startswith("/static/")


def digest(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


class Tokens:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.items = store._load(FILE, {"tokens": []}).get("tokens", [])

    def _save(self):
        self.store._save(FILE, {"tokens": self.items})

    @staticmethod
    def public(rec, now=None):
        now = now or time.time()
        out = {k: v for k, v in rec.items() if k != "hash"}
        out["expired"] = bool(rec.get("expires") and rec["expires"] <= now)
        return out

    def list(self):
        now = time.time()
        with self.lock:
            return [self.public(r, now) for r in sorted(self.items, key=lambda r: -r["created"])]

    def create(self, name, role, expires=None, trusted=False):
        """Returns (record, token). The token itself is never stored."""
        name = " ".join(str(name or "").split())[:NAME_MAX]
        if not name:
            raise ValueError("Give the link a name, e.g. who it's for.")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        expires = self._expiry(expires)
        token = secrets.token_urlsafe(18)
        rec = {"id": "t" + secrets.token_hex(4), "name": name, "role": role, "created": int(time.time()),
               "expires": expires, "trusted": bool(trusted), "last_used": None, "hash": digest(token)}
        with self.lock:
            if len(self.items) >= MAX_TOKENS:
                raise ValueError(f"There are already {MAX_TOKENS} links. Revoke some first.")
            self.items.append(rec)
            self._save()
        return self.public(rec), token

    @staticmethod
    def _expiry(expires):
        if expires in (None, "", 0):
            return None
        expires = int(float(expires))
        if expires <= time.time():
            raise ValueError("The expiry date is in the past.")
        return expires

    def update(self, tid, **fields):
        with self.lock:
            rec = self._find(tid)
            if "name" in fields:
                name = " ".join(str(fields["name"] or "").split())[:NAME_MAX]
                if not name:
                    raise ValueError("The name can't be empty.")
                rec["name"] = name
            if "trusted" in fields:
                rec["trusted"] = bool(fields["trusted"])
            if "expires" in fields:
                rec["expires"] = self._expiry(fields["expires"])
            self._save()
            return self.public(rec)

    def rotate(self, tid):
        """A new secret for the same person (the old link stops working)."""
        token = secrets.token_urlsafe(18)
        with self.lock:
            rec = self._find(tid)
            rec["hash"] = digest(token)
            self._save()
            return self.public(rec), token

    def revoke(self, tid):
        with self.lock:
            self.items.remove(self._find(tid))
            self._save()

    def get(self, tid):
        with self.lock:
            return self._find(tid)

    def _find(self, tid):
        for r in self.items:
            if r["id"] == tid:
                return r
        raise KeyError(tid)

    def check(self, token, now=None):
        """The record for a valid, unexpired token, else None. Compares every stored hash in constant time."""
        if not token or len(token) > 100:
            return None
        now = now or time.time()
        h = digest(token)
        found = None
        with self.lock:
            for r in self.items:
                if hmac.compare_digest(r["hash"], h):  # no early exit: the time doesn't depend on which one matched
                    found = r
            if found is None or (found.get("expires") and found["expires"] <= now):
                return None
            if not found.get("last_used") or now - found["last_used"] > TOUCH_EVERY:
                found["last_used"] = int(now)
                self._save()
        return found


_get_lock = threading.Lock()


def get(store):
    """The Tokens for this store (one per data folder)."""
    with _get_lock:
        if getattr(store, "access_tokens", None) is None:
            store.access_tokens = Tokens(store)
        return store.access_tokens


def same_origin(headers):
    """False when a browser says the request comes from another site (no Origin header: not a browser page)."""
    origin = headers.get("Origin")
    if not origin:
        return True
    return origin.split("://", 1)[-1].rstrip("/").lower() == (headers.get("Host") or "").lower()


def cookie_token(header):
    """Our token from a Cookie header, or ""."""
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v.strip()
    return ""


def set_cookie(token, expires=None):
    max_age = int(expires - time.time()) if expires else 365 * 86400
    return f"{COOKIE}={token}; Path=/; Max-Age={max(0, max_age)}; HttpOnly; SameSite=Lax"


def clear_cookie():
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
