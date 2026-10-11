"""Guest message wall: visitors post a name + message from /guest (no password needed), messages wait
in a moderation queue (guestwall.json, synced to storage.db like every other save) and approved ones
are shown by the "Guest wall" app."""

import socket
import threading
import time
import unicodedata

NAME_MAX = 24
TEXT_MAX = 140
DOODLE = 21              # a doodle is DOODLE x DOODLE dots, drawn on the /guest page
BODY_MAX = 2048          # bytes accepted by the submit endpoint
KEEP_MAX = 300           # messages kept in total (oldest rejected/approved go first)
PENDING_MAX = 100        # queue full: new messages are refused until someone moderates
RATE = (3, 600)          # per IP: at most 3 messages per 10 minutes
FILE = "guestwall.json"

server_port = 8080       # set by server.main()
seen_host = ""           # Host header of the last UI request from another device (e.g. "192.168.1.20:8080")


def lan_ip():
    """This machine's LAN address (a UDP connect sends no packets, it only picks the outgoing interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def default_url():
    host = seen_host or f"{lan_ip()}:{server_port}"
    return f"http://{host}/guest"


def note_host(host):
    """Remember how other devices reach the board (ignores localhost, which a phone couldn't use)."""
    global seen_host
    h = (host or "").strip()
    name = h.rsplit(":", 1)[0] if not h.startswith("[") else h.split("]")[0] + "]"
    if h and len(h) < 100 and name not in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0") and not name.startswith("127."):
        seen_host = h


def clean(s, limit):
    """Single line of printable text, at most `limit` characters."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    return " ".join(s.split())[:limit]


def clean_doodle(rows):
    """A doodle as DOODLE rows of DOODLE '0'/'1' characters, or None when there isn't one (or it's blank/bad)."""
    if not isinstance(rows, (list, tuple)) or len(rows) != DOODLE:
        return None
    out = []
    for r in rows:
        r = str(r)
        if len(r) != DOODLE or set(r) - {"0", "1"}:
            return None
        out.append(r)
    return out if any("1" in r for r in out) else None


class GuestWall:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        data = store._load(FILE, {"auto_approve": False, "messages": []})
        self.auto_approve = bool(data.get("auto_approve"))
        self.messages = data.get("messages", [])
        self.version = 0  # bumps on every change, so the app can notice
        self.hits = {}    # ip -> [timestamps]

    def _save(self):
        self.version += 1
        self.store._save(FILE, {"auto_approve": self.auto_approve, "messages": self.messages})

    def rate_ok(self, ip, now=None):
        now = now or time.time()
        n, window = RATE
        with self.lock:
            if len(self.hits) > 5000:  # forget idle addresses
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < window}
            recent = [t for t in self.hits.get(ip, []) if now - t < window]
            if len(recent) >= n:
                self.hits[ip] = recent
                return False
            self.hits[ip] = recent + [now]
            return True

    def submit(self, name, text, ip="", doodle=None):
        """Returns the new message. Raises ValueError with a message for the guest. A doodle (DOODLE x DOODLE
        rows of 0/1) is kept with the message and moderated with it."""
        name, text, doodle = clean(name, NAME_MAX), clean(text, TEXT_MAX), clean_doodle(doodle)
        if not text and not doodle:
            raise ValueError("Write a message first.")
        with self.lock:
            if sum(1 for m in self.messages if m["status"] == "pending") >= PENDING_MAX:
                raise ValueError("The wall has too many messages waiting right now. Try again later.")
            msg = {"id": f"m{time.time_ns() // 1000 % 10**12}", "name": name, "text": text, "ts": int(time.time()),
                   "status": "approved" if self.auto_approve else "pending"}
            if doodle:
                msg["doodle"] = doodle
            self.messages.append(msg)
            if len(self.messages) > KEEP_MAX:
                drop = next((m for m in self.messages if m["status"] != "pending"), self.messages[0])
                self.messages.remove(drop)
            self._save()
        return msg

    def set_status(self, mid, status):
        with self.lock:
            m = self._find(mid)
            m["status"] = status
            self._save()

    def delete(self, mid):
        with self.lock:
            self.messages.remove(self._find(mid))
            self._save()

    def set_auto_approve(self, on):
        with self.lock:
            self.auto_approve = bool(on)
            self._save()

    def _find(self, mid):
        for m in self.messages:
            if m["id"] == mid:
                return m
        raise KeyError(mid)

    def approved(self, limit=None):
        out = [m for m in self.messages if m["status"] == "approved"]
        return out[-limit:] if limit else out

    def info(self):
        return {"auto_approve": self.auto_approve, "messages": list(reversed(self.messages)),
                "pending": sum(1 for m in self.messages if m["status"] == "pending"),
                "url": default_url(), "limits": {"name": NAME_MAX, "text": TEXT_MAX, "doodle": DOODLE}}


_get_lock = threading.Lock()


def get(store):
    """The GuestWall for this store (one per data folder)."""
    with _get_lock:
        if getattr(store, "guestwall", None) is None:
            store.guestwall = GuestWall(store)
        return store.guestwall
