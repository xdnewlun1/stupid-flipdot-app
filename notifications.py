"""The notification queue: priorities, do-not-disturb, de-duplication, replace-by-key and history.

The Runner owns one Center and does the showing; the Center only decides. A new notification:
  - with a key that matches one on the board, waiting or held replaces it in place;
  - identical to one shown, waiting or held within `dedupe_s` seconds is dropped;
  - during do-not-disturb: low/normal are held for a summary when it ends (or dropped, per setting),
    high shows without its sound pattern, critical shows as usual;
  - shows now when nothing is on, or when it outranks what is (critical outranks everything, even another
    critical); what it displaces waits at the front of the queue with the time it had left;
  - otherwise waits, highest priority first, then oldest first.
While a game is played: low/normal wait (or show as a banner over the game, if the banner setting is on),
high shows as a banner, critical takes the board. While a browser streams live to the board, everything but
high and critical waits.
"""

import collections
import datetime as dt
import itertools
import json
import random
import time

PRIORITIES = {"low": 0, "normal": 1, "high": 2, "critical": 3}
NAMES = {v: k for k, v in PRIORITIES.items()}
SOUNDS = ("none", "tick", "clatter", "sweep")
HISTORY = 20
MAX_PENDING = 50
FOREVER = 1e18

DEFAULTS = {
    "duration": 10,
    "sound": "none",
    "dedupe_s": 60,
    "banner_over_games": False,
    "banner_position": "bottom",
    # do-not-disturb: "quiet" follows quiet hours, "custom" has its own window, "off" never
    "dnd": {"mode": "quiet", "start": "22:00", "end": "07:00", "during": "hold", "now": False},
    "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topics": [], "token": ""},
}


def settings(store):
    s = (getattr(store, "settings", None) or {}).get("notifications") or {}
    out = {**DEFAULTS, **s}
    out["dnd"] = {**DEFAULTS["dnd"], **(s.get("dnd") or {})}
    out["ntfy"] = {**DEFAULTS["ntfy"], **(s.get("ntfy") or {})}
    return out


def in_window(start, end, t):
    hm = t.strftime("%H:%M")
    return start <= hm < end if start <= end else (hm >= start or hm < end)


def dnd_active(store, t=None):
    """Is do-not-disturb on at `t` (a datetime, default now)?"""
    cfg = settings(store)["dnd"]
    if cfg.get("now"):
        return True
    t = t or dt.datetime.now()
    mode = cfg.get("mode") or "quiet"
    if mode == "custom":
        return in_window(cfg.get("start") or "22:00", cfg.get("end") or "07:00", t)
    if mode == "quiet":
        q = (getattr(store, "settings", None) or {}).get("quiet_hours") or {}
        return bool(q.get("enabled")) and in_window(q.get("start") or "22:00", q.get("end") or "07:00", t)
    return False


def priority(value, default="normal"):
    """A priority name from a name or an ntfy-style number 1-5."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) or str(value).isdigit():
        n = int(value)
        return "low" if n <= 2 else "normal" if n == 3 else "high" if n == 4 else "critical"
    v = str(value).strip().lower()
    v = {"min": "low", "default": "normal", "max": "critical", "urgent": "critical"}.get(v, v)
    if v not in PRIORITIES:
        raise ValueError(f"unknown priority {value!r}: use low, normal, high or critical")
    return v


_ids = itertools.count(1)


def entry(spec=None, app=None, *, duration=10, transition="dissolve", transition_duration=1.0, priority="normal",
          icon=None, sound="none", repeat=1, hold=False, key=None, text=None, source=None, only_idle=False,
          dedupe=True, record=True, remake=None, now=None):
    if sound not in SOUNDS:
        raise ValueError(f"unknown sound pattern {sound!r}: use {', '.join(SOUNDS)}")
    now = time.time() if now is None else now
    if text is None:
        p = (spec or {}).get("params") or {}
        text = p.get("text") or (spec or {}).get("app") or getattr(app, "title", None) or "Notification"
        if p.get("title"):
            text = f"{p['title']}: {text}"
    sig = json.dumps(spec, sort_keys=True, default=str) if spec else f"text:{text}"
    return {
        "id": next(_ids), "key": str(key)[:80] if key else None, "priority": PRIORITIES[priority],
        "text": str(text)[:300], "icon": icon, "sound": sound, "repeat": max(1, min(20, int(repeat or 1))),
        "hold": bool(hold), "duration": max(0.1, float(duration)), "transition": transition,
        "transition_duration": float(transition_duration or 1.0), "spec": spec, "app": app, "remake": remake,
        "source": source, "only_idle": bool(only_idle), "dedupe": bool(dedupe), "record": bool(record),
        "created": now, "sig": sig, "left": None, "seq": 0,
    }


def public(e, state=None):
    """What the web page and API see of an entry."""
    if e is None:
        return None
    out = {k: e[k] for k in ("id", "key", "text", "icon", "sound", "repeat", "hold", "duration", "source", "created")}
    out["priority"] = NAMES[e["priority"]]
    out["can_reshow"] = bool(e.get("spec") or e.get("remake"))
    if state or e.get("state"):
        out["state"] = state or e.get("state")
    if e.get("shown_at"):
        out["shown_at"] = e["shown_at"]
    return out


class Center:
    def __init__(self, store):
        self.store = store
        self.pending = []
        self.held = []  # kept during do-not-disturb for the summary
        self.history = collections.deque(maxlen=HISTORY)
        self.recent = {}  # sig -> last time offered/shown, for de-duplication
        self._seq = itertools.count(1)

    def cfg(self):
        return settings(self.store)

    # ---- deciding ------------------------------------------------------------------

    def offer(self, e, now, current=None, busy=None, banner=None, dnd=False):
        """Decide what happens to a new notification. `current`/`banner`: the entries on the board, `busy`: "game",
        "live" or None. Returns (action, target): action is one of
          show       show it now (target: the entry it displaces, or None)
          banner     show it as a banner over the game
          replace    it replaces `target` in place (on the board, in the banner, waiting or held)
          queued / held / dropped / duplicate / busy   (nothing to show now)"""
        cfg = self.cfg()
        if e["key"]:
            for where, other in (("current", current), ("banner", banner)):
                if other is not None and other.get("key") == e["key"]:
                    return "replace", where
            for lst in (self.pending, self.held):
                for i, other in enumerate(lst):
                    if other.get("key") == e["key"]:
                        e["seq"] = other["seq"]
                        lst[i] = e
                        self._sort()
                        return "replace", "pending" if lst is self.pending else "held"
        elif e["dedupe"] and self._duplicate(e, now, current, banner, float(cfg.get("dedupe_s") or 0)):
            return "duplicate", None
        self.recent[e["sig"]] = now
        if dnd and e["priority"] <= PRIORITIES["normal"] and not e["only_idle"]:
            if (cfg["dnd"].get("during") or "hold") == "drop":
                self._remember(e, "dropped", now)
                return "dropped", None
            self.held.append(e)
            return "held", None
        if dnd and e["priority"] == PRIORITIES["high"]:
            e["sound"] = "none"
        if e["only_idle"]:
            # e.g. the mirror: shows when nothing else is going on, never waits, and gives way to anything
            if current is not None or busy or self.pending or banner is not None:
                return "busy", None
            return "show", None
        if current is not None:
            if current.get("only_idle") or e["priority"] > current["priority"] or (
                    e["priority"] == PRIORITIES["critical"]):
                if busy == "game" and e["priority"] < PRIORITIES["critical"]:
                    return self._while_game(e, banner, cfg)
                return "show", current
            return self._queue(e)
        if busy == "game" and e["priority"] < PRIORITIES["critical"]:
            return self._while_game(e, banner, cfg)
        if busy == "live" and e["priority"] < PRIORITIES["high"]:
            return self._queue(e)
        if self.pending and self.pending[0]["priority"] >= e["priority"]:
            return self._queue(e)  # others were waiting first
        return "show", None

    def _while_game(self, e, banner, cfg):
        if e["priority"] >= PRIORITIES["high"] or cfg.get("banner_over_games"):
            if banner is None or e["priority"] > banner["priority"]:
                return "banner", banner
        return self._queue(e)

    def _duplicate(self, e, now, current, banner, window):
        if window <= 0:
            return False
        for other in [current, banner] + self.pending + self.held:
            if other is not None and other.get("sig") == e["sig"]:
                return True
        last = self.recent.get(e["sig"])
        return last is not None and now - last < window

    def _queue(self, e):
        e["seq"] = next(self._seq)
        self.pending.append(e)
        self._sort()
        while len(self.pending) > MAX_PENDING:
            self.pending.pop()  # the lowest-priority, newest one
        return "queued", None

    def _sort(self):
        self.pending.sort(key=lambda x: (-x["priority"], x["seq"]))

    def requeue(self, e, left):
        """A notification taken off the board by a more urgent one waits at the front of its priority."""
        if e.get("only_idle") or (not e["hold"] and left < 2):
            return False
        e["left"] = left
        e["seq"] = -next(self._seq)
        self.pending.append(e)
        self._sort()
        return True

    def next_ready(self, busy=None, banner=None):
        """(how, entry) to show now that the board is free, or None: how is "show" or "banner"."""
        cfg = self.cfg()
        for i, e in enumerate(self.pending):
            if busy == "game" and e["priority"] < PRIORITIES["critical"]:
                if banner is None and (e["priority"] >= PRIORITIES["high"] or cfg.get("banner_over_games")):
                    return "banner", self.pending.pop(i)
                continue
            if busy == "live" and e["priority"] < PRIORITIES["high"]:
                continue
            return "show", self.pending.pop(i)
        return None

    def dnd_ended(self, now):
        """Do-not-disturb is over: the entries to offer again. Several held ones become one summary entry, and go
        to the history so each can be shown again from there."""
        held, self.held = self.held, []
        if len(held) <= 1:
            for e in held:
                e["dedupe"] = False
            return held
        for e in held:
            self._remember(e, "held", now)
        n = len(held)
        body = " / ".join(e["text"] for e in held[-5:])
        spec = {"app": "notice", "params": {"title": f"{n} notifications", "text": body, "icon": "bell"}}
        return [entry(spec, duration=max(10, min(30, 4 * n)), priority="normal", text=f"{n} notifications: {body}",
                      icon="bell", source="summary", dedupe=False, now=now)]

    # ---- the board's side -----------------------------------------------------------

    def shown(self, e, now, how="shown"):
        e["shown_at"] = now
        self.recent[e["sig"]] = now
        if e["record"] and not e.get("_recorded"):
            e["_recorded"] = True
            self._remember(e, how, now)
        self._prune(now)

    def _remember(self, e, state, now):
        e["state"] = state
        e.setdefault("shown_at", now)
        if e["record"]:
            self.history.appendleft(e)

    def _prune(self, now):
        window = max(60.0, float(self.cfg().get("dedupe_s") or 0))
        if len(self.recent) > 200:
            self.recent = {k: t for k, t in self.recent.items() if now - t < window}

    def remove(self, eid):
        for lst in (self.pending, self.held):
            for i, e in enumerate(lst):
                if e["id"] == eid:
                    return lst.pop(i)
        return None

    def clear(self):
        n = len(self.pending) + len(self.held)
        self.pending, self.held = [], []
        return n

    def find_history(self, eid):
        return next((e for e in self.history if e["id"] == eid), None)


class Attention:
    """Wraps a notification's app: first the sound pattern (discs flipped over the picture that was on, for the
    clicks), then the app, brought in with the notification's transition. With repeat > 1 the pattern and the
    app play again every `duration` seconds."""
    pausable = False

    def __init__(self, make, base, sound="none", duration=10, repeat=1, transition="dissolve", transition_duration=1.0,
                 now=None):
        self.make, self.base = make, base
        self.sound, self.duration, self.repeat = sound, float(duration), max(1, int(repeat))
        self.transition, self.transition_duration = transition, transition_duration
        self.app = make()
        self.id, self.title = self.app.id, self.app.title
        self.spec = getattr(self.app, "spec", None)
        self.started = time.time() if now is None else now
        self.round = 0
        self.request_transition = None
        self.rng = random.Random(1)
        self._pre_done = False

    @property
    def interactive(self):
        return self.app.interactive

    @property
    def interval(self):
        if not self._pre_done and self.sound != "none":
            return PRE_STEP[self.sound]
        return self.app.interval

    def input(self, key, action="down", player=1):
        self.app.input(key, action, player)

    def describe(self):
        return self.app.describe()

    def stop(self):
        self.app.stop()

    def __getattr__(self, name):
        # anything else the runner asks of an app (media_status, layers, ...) comes from the wrapped one
        if name in ("app", "make", "base"):
            raise AttributeError(name)
        return getattr(self.app, name)

    def render(self, c, now):
        t = now - self.started
        rnd = min(self.repeat - 1, int(t // self.duration)) if self.repeat > 1 else 0
        if rnd != self.round:
            self.round = rnd
            self.app.stop()
            self.app = self.make()
            self._pre_done = False
            self.base = None  # later rounds flip the pattern over the notification itself
        t_round = t - rnd * self.duration if self.repeat > 1 else t
        if not self._pre_done and self.sound != "none":
            if t_round < PRE_LEN[self.sound]:
                if self.base is None:
                    self.base = c.copy()
                    self.app.render(self.base, now)
                pattern(self.sound, c, self.base, t_round, self.rng)
                return None
            self._pre_done = True
            if self.transition and self.transition != "none":
                self.request_transition = (self.transition, self.transition_duration)
        return self.app.render(c, now)


PRE_LEN = {"tick": 0.75, "clatter": 0.8, "sweep": 1.0}
PRE_STEP = {"tick": 0.125, "clatter": 0.08, "sweep": 0.04}


def pattern(kind, c, base, t, rng):
    """One frame of a sound pattern at `t` seconds, drawn over `base`."""
    c.px[:] = base.px
    w, h = c.w, c.h
    if kind == "tick":
        # a small block in the top-right corner flips back and forth: three crisp ticks
        if int(t / PRE_STEP["tick"]) % 2 == 0:
            for y in range(1, 5):
                for x in range(w - 5, w - 1):
                    c.px[y * w + x] ^= 1
    elif kind == "clatter":
        n = w * h // 6
        for _ in range(n):
            i = rng.randrange(w * h)
            c.px[i] ^= 1
    elif kind == "sweep":
        x0 = int(t / PRE_LEN["sweep"] * (w + 4)) - 4
        for y in range(h):
            for x in range(max(0, x0), min(w, x0 + 4)):
                c.px[y * w + x] ^= 1
