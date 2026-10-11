"""Friend messages (Vestaboard style): people with a friend link post a message, optionally with a doodle
and a time to show it. Trusted friends' messages show right away (or at their time); the others wait
in the owner's inbox. Messages show as notifications with the sender's name, can be pinned for a few
hours, and the "Message wall" app rotates the recent ones.

Saved in friend_messages.json (synced to storage.db like every other save)."""

import logging
import re
import threading
import time
import unicodedata

FILE = "friend_messages.json"
KEEP_MAX = 500          # messages kept in total (oldest finished ones go first)
PENDING_MAX = 20        # per friend: waiting for approval or for their time
SCHEDULE_MAX_DAYS = 400
LINES_MAX = 8
LENGTH_CAP = 280        # the owner's "max length" can't go above this
BODY_MAX = 8192         # bytes accepted by the friend endpoints (text + a doodle)
DEFAULTS = {"max_length": 140, "profanity_filter": False, "rate_count": 5, "rate_minutes": 60, "display_seconds": 15}
STYLE_DEFAULTS = {"font": "normal", "size": "fit", "align": "center", "invert": False, "transition": "none"}
SIZES = ("fit", "1", "2", "3", "4")
ALIGNS = ("left", "center", "right")
TRANSITIONS = ("none", "split-flap", "dissolve", "wipe-down")
PIN_KEY = "friend-pin"  # the pinned message's notification key (a new pin replaces it, unpin dismisses it)

# A short bundled list for the optional filter (whole words, after undoing common letter swaps).
PROFANITY = frozenset("""
arse arsehole ass asshole asswipe bastard bastards bitch bitches bitchy bollocks bullshit cock cocks cocksucker
crap cunt cunts damn dick dickhead dicks dildo douche douchebag fag faggot fags fuck fucked fucker fuckers fuckin
fucking fucks fuk jackass jerkoff motherfucker motherfucking nigga nigger niggers piss pissed prick pussy retard
retarded shit shits shitty shithead slut sluts twat wank wanker whore whores
""".split())
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "*": ""})

log = logging.getLogger("friends")


def clean_text(s, limit):
    """Printable text with at most LINES_MAX lines, spaces collapsed, at most `limit` characters."""
    s = unicodedata.normalize("NFKC", str(s or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join("".join(ch if ch.isprintable() else " " for ch in line).split()) for line in s.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines[:LINES_MAX])[:limit]


def profane(text):
    words = re.findall(r"[a-z]+", text.lower().translate(_LEET))
    return any(w in PROFANITY for w in words)


def clean_style(style):
    style = style if isinstance(style, dict) else {}
    out = dict(STYLE_DEFAULTS)
    font = str(style.get("font") or "normal")
    import fonts
    if fonts.is_pixel(font) or font in fonts.user_fonts():
        out["font"] = font
    if str(style.get("size")) in SIZES:
        out["size"] = str(style["size"])
    if style.get("align") in ALIGNS:
        out["align"] = style["align"]
    out["invert"] = bool(style.get("invert"))
    if style.get("transition") in TRANSITIONS:
        out["transition"] = style["transition"]
    return out


def clean_doodle(rows, w, h):
    """A doodle is the board's size: h strings of w '0'/'1'. Empty doodles become None."""
    if not rows:
        return None
    if not isinstance(rows, list) or len(rows) != h or any(not isinstance(r, str) or len(r) != w or r.strip("01") for r in rows):
        raise ValueError("The doodle doesn't match the board's size.")
    return rows if any("1" in r for r in rows) else None


def clean_time(at, now=None):
    """A Unix time to show the message, or None for right away."""
    if at in (None, "", 0):
        return None
    now = now or time.time()
    at = int(float(at))
    if at <= now + 30:
        return None  # in the past or the next few seconds: now
    if at > now + SCHEDULE_MAX_DAYS * 86400:
        raise ValueError("That's too far ahead. Pick a time within a year.")
    return at


class Friends:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        data = store._load(FILE, {})
        self.settings = {**DEFAULTS, **(data.get("settings") or {})}
        self.messages = data.get("messages", [])
        self.version = 0     # bumps on every change, so the Message wall app can notice
        self.hits = {}       # token id -> [timestamps]
        self.notify = None   # runner.notify, set by start()
        self.runner = None
        self.size = (84, 42)
        self.showing = None  # id of the pinned message handed to the notification queue
        self.wake = threading.Event()
        self.tick_lock = threading.Lock()  # the loop and an owner's click can both call tick()
        self._thread = None

    def _save(self):
        self.version += 1
        self.store._save(FILE, {"settings": self.settings, "messages": self.messages})

    # ---- settings -------------------------------------------------------------------

    def set_settings(self, values):
        with self.lock:
            s = dict(self.settings)
            if "max_length" in values:
                s["max_length"] = max(10, min(LENGTH_CAP, int(values["max_length"])))
            if "profanity_filter" in values:
                s["profanity_filter"] = bool(values["profanity_filter"])
            if "rate_count" in values:
                s["rate_count"] = max(1, min(100, int(values["rate_count"])))
            if "rate_minutes" in values:
                s["rate_minutes"] = max(1, min(24 * 60, int(values["rate_minutes"])))
            if "display_seconds" in values:
                s["display_seconds"] = max(3, min(600, int(values["display_seconds"])))
            self.settings = s
            self._save()

    # ---- posting ----------------------------------------------------------------------

    def rate_ok(self, tid, now=None):
        now = now or time.time()
        n, window = int(self.settings["rate_count"]), int(self.settings["rate_minutes"]) * 60
        with self.lock:
            recent = [t for t in self.hits.get(tid, []) if now - t < window]
            if len(recent) >= n:
                self.hits[tid] = recent
                return False
            self.hits[tid] = recent + [now]
            return True

    def compose(self, body, now=None):
        """(text, style, doodle, at) from a friend's request body. Raises ValueError for the friend."""
        if not isinstance(body, dict):
            raise ValueError("Couldn't read the message.")
        text = clean_text(body.get("text"), LENGTH_CAP + 1)
        if len(text) > int(self.settings["max_length"]):
            raise ValueError(f"That's too long: {self.settings['max_length']} characters at most.")
        doodle = clean_doodle(body.get("doodle"), *self.size)
        if not text and not doodle:
            raise ValueError("Write a message or draw something first.")
        return text, clean_style(body.get("style")), doodle, clean_time(body.get("at"), now)

    def submit(self, tok, body, now=None):
        """A friend (token record `tok`) posts. Returns the saved message."""
        now = now or time.time()
        text, style, doodle, at = self.compose(body, now)
        flagged = bool(self.settings["profanity_filter"] and profane(text))
        with self.lock:
            waiting = sum(1 for m in self.messages if m["tid"] == tok["id"] and not m.get("shown_at")
                          and m["status"] in ("pending", "approved"))
            if waiting >= PENDING_MAX:
                raise ValueError("You have a lot of messages waiting already. Try again once some have shown.")
            if not self.rate_ok(tok["id"], now):
                raise RateLimited(f"That's a few messages already. Please wait a while "
                                  f"(at most {self.settings['rate_count']} every {self.settings['rate_minutes']} minutes).")
            msg = {"id": f"f{time.time_ns() // 1000 % 10**13}", "tid": tok["id"], "name": tok["name"], "text": text,
                   "style": style, "doodle": doodle, "created": int(now), "at": at,
                   "status": "approved" if tok.get("trusted") and not flagged else "pending",
                   "flagged": flagged, "shown_at": None, "pinned_until": None}
            self.messages.append(msg)
            self._trim()
            self._save()
        self.wake.set()
        return msg

    def _trim(self):
        while len(self.messages) > KEEP_MAX:
            done = [m for m in self.messages if m["status"] == "declined" or m.get("shown_at")]
            self.messages.remove(done[0] if done else self.messages[0])

    def mine(self, tid, limit=10):
        """A friend's own recent messages (no doodles), newest first, for their page."""
        with self.lock:
            out = [m for m in self.messages if m["tid"] == tid][-limit:]
        return [{k: m[k] for k in ("id", "text", "created", "at", "status", "shown_at")} for m in reversed(out)]

    # ---- moderation -------------------------------------------------------------------

    def _find(self, mid):
        for m in self.messages:
            if m["id"] == mid:
                return m
        raise KeyError(mid)

    def act(self, mid, action, hours=None, now=None):
        now = now or time.time()
        with self.lock:
            m = self._find(mid)
            if action == "approve":
                m["status"] = "approved"
            elif action == "decline":
                m["status"] = "declined"
                m["pinned_until"] = None
            elif action == "delete":
                self.messages.remove(m)
            elif action == "show":  # show it again now (or for the first time, approving it)
                m["status"] = "approved"
                m["at"], m["shown_at"] = None, None
            elif action == "pin":
                hours = float(hours or 1)
                if not 0 < hours <= 7 * 24:
                    raise ValueError("Pin for between a few minutes and 7 days (168 hours).")
                m["status"] = "approved"
                m["pinned_until"] = int(now + hours * 3600)
                m["shown_at"] = m.get("shown_at") or int(now)
                for other in self.messages:  # one pinned message at a time
                    if other is not m:
                        other["pinned_until"] = None
                self.showing = None  # (again) with the new time
            elif action == "unpin":
                m["pinned_until"] = None
            else:
                raise ValueError(f"unknown action {action!r}")
            self._save()
        self.wake.set()

    def info(self, now=None):
        now = now or time.time()
        with self.lock:
            msgs = list(reversed(self.messages))
            pending = sum(1 for m in self.messages if m["status"] == "pending")
        return {"settings": self.settings, "messages": msgs, "pending": pending, "now": int(now),
                "limits": {"length_cap": LENGTH_CAP, "lines": LINES_MAX}}

    def recent(self, limit=10, days=30, now=None):
        """Messages that have been shown (newest first), pinned one first, for the Message wall app."""
        now = now or time.time()
        with self.lock:
            out = [m for m in self.messages if m["status"] == "approved" and m.get("shown_at") and now - m["shown_at"] < days * 86400]
        out.sort(key=lambda m: (bool(m.get("pinned_until") and m["pinned_until"] > now), m["shown_at"]), reverse=True)
        return out[:limit]

    # ---- delivery ---------------------------------------------------------------------

    def start(self, runner, size):
        """Show due messages and keep the pinned one up, in a background thread."""
        self.runner, self.notify, self.size = runner, runner.notify, size
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="friends")
            self._thread.start()

    def _loop(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            try:
                self.tick()
            except Exception:
                log.exception("friend messages")

    def tick(self, now=None):
        with self.tick_lock:
            self._tick(now or time.time())

    def _tick(self, now):
        """Hand due messages to the notification queue, and keep the pinned one there."""
        if self.notify is None:
            return
        with self.lock:
            pinned = next((m for m in self.messages if m.get("pinned_until") and m["status"] == "approved"), None)
            if pinned and pinned["pinned_until"] <= now:
                pinned["pinned_until"] = None
                pinned = None
            due = [m for m in self.messages if m["status"] == "approved" and not m.get("shown_at") and (m.get("at") or 0) <= now]
            for m in due:
                m["shown_at"] = int(now)
            if due or (self.showing and not pinned):
                self._save()
        for m in due:
            self._show(m, float(self.settings["display_seconds"]))
        if self.showing and (not pinned or pinned["id"] != self.showing):
            self.showing = None
            dismiss = getattr(self.runner, "dismiss", None)
            if dismiss:
                dismiss(key=PIN_KEY)  # unpinned, expired or another one pinned: take it down
        if pinned and self.showing != pinned["id"]:
            # low priority: other notifications (new messages too) go first, then the pinned one comes back
            self._show(pinned, max(1.0, pinned["pinned_until"] - now), priority="low", key=PIN_KEY)
            self.showing = pinned["id"]

    def _show(self, msg, duration, priority="normal", key=None):
        from apps.pack_friends import FriendMessage
        ctx = self.runner.ctx
        make = lambda: FriendMessage(ctx, message=msg)  # noqa: E731
        self.notify(app=make(), duration=duration, transition="dissolve", priority=priority, icon="mail", key=key,
                    text=f"{msg['name']}: {msg.get('text') or '(a doodle)'}"[:300], source="friend", dedupe=False, remake=make)


class RateLimited(ValueError):
    pass


_get_lock = threading.Lock()


def get(store):
    """The Friends inbox for this store (one per data folder)."""
    with _get_lock:
        if getattr(store, "friends", None) is None:
            store.friends = Friends(store)
        return store.friends
