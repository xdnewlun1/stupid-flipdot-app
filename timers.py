"""Kitchen timers and a Pomodoro timer, shared by the Timers app, POST /api/timers and MQTT / Home Assistant.

Up to MAX_TIMERS run at once. Each is {id, name, duration, state, ends (running), left (paused), kind}; kind is
"timer", or "work" / "break" for the Pomodoro (which starts its next phase by itself and counts the sessions).
When one reaches zero it rings for RING_S seconds: the Timers app plays the alarm (a clatter, then the name
flashing), and if the app isn't on the board, it's shown over whatever is (on_ring, set up by the server).
Timers are kept in timers.json, so a restart doesn't lose them.

Text commands (MQTT <prefix>/timer, the Home Assistant "Timer" text entity, or {"command": "..."}):
    "pasta 8m"  "8 minutes pasta"  "tea 3:30"  "laundry 1h 20m"  "start eggs 7 min"  "10 min" (named "Timer")
    "pause pasta"  "resume"  "cancel pasta"  "cancel all"  "dismiss"  "pomodoro"  "stop pomodoro"
"""

import datetime as dt
import logging
import re
import threading
import time

log = logging.getLogger("timers")

MAX_TIMERS = 4
RING_S = 20.0          # seconds a finished timer rings before it clears itself
MAX_SECONDS = 24 * 3600
POMODORO = {"work": 25, "break": 5, "long_break": 15, "every": 4}

_UNITS = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")


def parse_duration(value):
    """Seconds from 480, "8", "8m", "8 minutes", "1h 30m", "8:00" (m:ss), "1:30:00" (h:mm:ss) or "90s".
    A bare number means minutes. Raises ValueError."""
    if isinstance(value, (int, float)):
        secs = float(value)
    else:
        s = str(value or "").strip().lower()
        m = _CLOCK.fullmatch(s)
        if m:
            a, b, c = int(m[1]), int(m[2]), m[3]
            secs = a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b
        elif re.fullmatch(r"\d+(?:\.\d+)?", s):
            secs = float(s) * 60
        else:
            parts = _DUR.findall(s)
            if not parts or _DUR.sub("", s).strip(" ,and"):
                raise ValueError(f"can't read a duration from {value!r} (try 8m, 1h 30m or 8:00)")
            secs = sum(float(n) * _UNITS[u.lower()] for n, u in parts)
    if not 1 <= secs <= MAX_SECONDS:
        raise ValueError("a timer runs from 1 second to 24 hours")
    return int(round(secs))


def split_command(text):
    """("start", name, seconds) | ("pause"/"resume"/"cancel", name or None) | ("dismiss",) | ("pomodoro", on)."""
    s = " ".join(str(text or "").strip().split())
    low = s.lower()
    if not s:
        raise ValueError("empty timer command")
    if low in ("pomodoro", "start pomodoro", "pomodoro start", "pomodoro on"):
        return ("pomodoro", True)
    if low in ("stop pomodoro", "pomodoro stop", "pomodoro off", "cancel pomodoro", "end pomodoro"):
        return ("pomodoro", False)
    if low in ("dismiss", "ok", "stop alarm", "silence"):
        return ("dismiss",)
    first, _, rest = s.partition(" ")
    verb = first.lower()
    if verb in ("pause", "hold"):
        return ("pause", rest or None)
    if verb in ("resume", "continue", "unpause"):
        return ("resume", rest or None)
    if verb in ("cancel", "stop", "delete", "clear", "remove"):
        return ("cancel", None if rest.lower() in ("", "all", "timers", "all timers") else rest)
    if verb in ("start", "set", "timer"):
        s = rest or s
    # the name is what's left once the duration (8m / 8 minutes / 8:00 / a bare number) is taken out
    m = _CLOCK.search(s)
    if m:
        secs, name = parse_duration(m[0]), (s[:m.start()] + s[m.end():])
    else:
        found = _DUR.findall(s)
        if found:
            secs = parse_duration(" ".join(f"{n}{u}" for n, u in found))
            name = _DUR.sub(" ", s)
        else:
            num = re.search(r"\b\d+(?:\.\d+)?\b", s)
            if not num:
                raise ValueError(f"no duration in {text!r} (try: pasta 8m)")
            secs, name = parse_duration(num[0]), s[:num.start()] + s[num.end():]
    name = re.sub(r"\b(for|a|an|of|timer|minutes?|and)\b", " ", name, flags=re.I)
    name = " ".join(name.split()).strip(" ,.") or "Timer"
    return ("start", name[:16], secs)


def fmt_left(secs):
    """7:59, 1:05:00; whole seconds rounded up, so 0:00 shows only at the end."""
    secs = max(0, int(-(-secs // 1)))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Timers:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.lock = threading.RLock()
        saved = store._load("timers.json", {}) if hasattr(store, "_load") else {}
        self.items = [t for t in saved.get("timers", []) if isinstance(t, dict) and t.get("id")]
        self.pomodoro = {**POMODORO, "on": False, "sessions": 0, "day": None, **(saved.get("pomodoro") or {})}
        self._next = 1 + max([int(str(t["id"]).lstrip("t") or 0) for t in self.items if str(t["id"]).lstrip("t").isdigit()] or [0])
        self.on_ring = None     # fn(timer): a timer just finished (the server shows the alarm)
        self.on_change = None   # fn(): the list changed (Home Assistant state)
        self.version = 0

    # ---- reading -------------------------------------------------------------------------------

    def left(self, t, now=None):
        now = self.clock() if now is None else now
        if t["state"] == "running":
            return max(0.0, t["ends"] - now)
        if t["state"] == "paused":
            return float(t.get("left") or 0)
        return 0.0

    def snapshot(self, now=None):
        """The timers as plain dicts with `left` (seconds) filled in, soonest first, ringing ones first of all."""
        now = self.clock() if now is None else now
        with self.lock:
            self._tick(now)
            out = [{**t, "left": round(self.left(t, now), 1)} for t in self.items]
            pomo = dict(self.pomodoro)
        out.sort(key=lambda t: (t["state"] != "ringing", t["state"] == "paused", t["left"]))
        return {"timers": out, "pomodoro": pomo, "max": MAX_TIMERS, "now": now}

    def summary(self, now=None):
        """One line for Home Assistant: "Pasta until 18:42, Tea paused", or "None"."""
        parts = []
        for t in self.snapshot(now)["timers"]:
            if t["state"] == "running":
                parts.append(f"{t['name']} until {dt.datetime.fromtimestamp(t['ends']).strftime('%H:%M')}")
            elif t["state"] == "paused":
                parts.append(f"{t['name']} paused at {fmt_left(t['left'])}")
            elif t["state"] == "ringing":
                parts.append(f"{t['name']} done")
        return ", ".join(parts) or "None"

    def ringing(self, now=None):
        return [t for t in self.snapshot(now)["timers"] if t["state"] == "ringing"]

    # ---- changing ------------------------------------------------------------------------------

    def _find(self, name):
        """Timers matching a name or id (case-insensitive); None/"" = all that aren't ringing."""
        if not name:
            return [t for t in self.items if t["state"] in ("running", "paused")]
        key = str(name).strip().lower()
        exact = [t for t in self.items if t["id"] == name or t["name"].lower() == key]
        found = exact or [t for t in self.items if t["name"].lower().startswith(key)]
        if not found:
            raise ValueError(f"no timer called {name!r}")
        return found

    def start(self, name, duration, kind="timer", now=None):
        now = self.clock() if now is None else now
        secs = parse_duration(duration)
        name = (" ".join(str(name or "").split()) or "Timer")[:16]
        with self.lock:
            self._tick(now)
            same = [t for t in self.items if t["name"].lower() == name.lower() and t["kind"] == kind]
            for t in same:  # starting "Pasta" again restarts it
                self.items.remove(t)
            if len([t for t in self.items if t["state"] != "ringing"]) >= MAX_TIMERS:
                raise ValueError(f"{MAX_TIMERS} timers are already running; cancel one first")
            t = {"id": f"t{self._next}", "name": name, "duration": secs, "state": "running", "ends": now + secs,
                 "left": None, "kind": kind, "started": now}
            self._next += 1
            self.items.append(t)
            self._changed()
            return dict(t)

    def pause(self, name=None, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            self._tick(now)
            hit = [t for t in self._find(name) if t["state"] == "running"]
            for t in hit:
                t["left"], t["state"], t["ends"] = max(0.0, t["ends"] - now), "paused", None
            self._changed()
            return len(hit)

    def resume(self, name=None, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            hit = [t for t in self._find(name) if t["state"] == "paused"]
            for t in hit:
                t["ends"], t["state"], t["left"] = now + float(t["left"] or 0), "running", None
            self._changed()
            return len(hit)

    def toggle(self, name=None, now=None):
        """Pause what's running, or resume if everything is paused."""
        found = self._find(name)
        if any(t["state"] == "running" for t in found):
            return self.pause(name, now)
        return self.resume(name, now)

    def cancel(self, name=None):
        with self.lock:
            if not name:
                hit = list(self.items)
                self.pomodoro["on"] = False
            else:
                hit = self._find(name)
                if any(t["kind"] != "timer" for t in hit):
                    self.pomodoro["on"] = False
            for t in hit:
                self.items.remove(t)
            self._changed()
            return len(hit)

    def dismiss(self):
        """Stop the alarm of every ringing timer."""
        with self.lock:
            hit = [t for t in self.items if t["state"] == "ringing"]
            for t in hit:
                self.items.remove(t)
            if hit:
                self._changed()
            return len(hit)

    def set_pomodoro(self, on, work=None, rest=None, long_break=None, every=None, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            p = self.pomodoro
            for k, v in (("work", work), ("break", rest), ("long_break", long_break), ("every", every)):
                if v is not None:
                    p[k] = max(1, min(180, int(float(v))))
            self.items = [t for t in self.items if t["kind"] == "timer"]
            p["on"] = bool(on)
            if on:
                self._today(now)
                self.start("Work", p["work"] * 60, kind="work", now=now)
            self._changed()
            return dict(p)

    def _today(self, now):
        day = dt.date.fromtimestamp(now).isoformat()
        if self.pomodoro.get("day") != day:
            self.pomodoro["day"], self.pomodoro["sessions"] = day, 0

    def command(self, body, now=None):
        """A command from the API, MQTT or Home Assistant: a text command or a dict
        {action: start|pause|resume|toggle|cancel|dismiss|pomodoro, name, duration, on, work, break}."""
        if isinstance(body, str):
            s = body.strip()
            if s.startswith("{"):
                import json
                body = json.loads(s)
            else:
                body = {"command": s}
        if body.get("command"):
            cmd = split_command(body["command"])
            body = {"action": cmd[0], **({"name": cmd[1]} if len(cmd) > 1 and cmd[0] != "pomodoro" else {}),
                    **({"duration": cmd[2]} if cmd[0] == "start" else {}),
                    **({"on": cmd[1]} if cmd[0] == "pomodoro" else {})}
        act = body.get("action") or ("start" if body.get("duration") else None)
        name = body.get("name") or body.get("id")
        if act == "start":
            return {"started": self.start(name, body.get("duration"), now=now)}
        if act in ("pause", "resume", "toggle"):
            return {act: getattr(self, act)(name, now=now)}
        if act == "cancel":
            return {"cancelled": self.cancel(name)}
        if act == "dismiss":
            return {"dismissed": self.dismiss()}
        if act == "pomodoro":
            return {"pomodoro": self.set_pomodoro(body.get("on", True) not in (False, "off", "false", 0), body.get("work"),
                                                  body.get("break"), body.get("long_break"), body.get("every"), now=now)}
        raise ValueError(f"unknown timer action {act!r}")

    # ---- time passing ----------------------------------------------------------------------------

    def _tick(self, now):
        """Timers that reached zero start ringing; rung-out ones clear; the Pomodoro moves to its next phase."""
        rang, changed = [], False
        for t in list(self.items):
            if t["state"] == "running" and t["ends"] <= now:
                t["state"], t["rang_at"], t["ends"] = "ringing", now, None
                rang.append(t)
                changed = True
                if t["kind"] in ("work", "break") and self.pomodoro.get("on"):
                    p = self.pomodoro
                    self._today(now)
                    if t["kind"] == "work":
                        p["sessions"] = int(p.get("sessions") or 0) + 1
                        long = p["sessions"] % max(1, int(p.get("every") or 4)) == 0
                        nxt = ("Long break" if long else "Break", (p["long_break"] if long else p["break"]) * 60, "break")
                    else:
                        nxt = ("Work", p["work"] * 60, "work")
                    self.items.append({"id": f"t{self._next}", "name": nxt[0], "duration": int(nxt[1]),
                                       "state": "running", "ends": now + nxt[1], "left": None, "kind": nxt[2],
                                       "started": now})
                    self._next += 1
            elif t["state"] == "ringing" and now - t.get("rang_at", now) >= RING_S:
                self.items.remove(t)
                changed = True
        if changed:
            self._changed()
        for t in rang:
            log.info("timer %s finished", t["name"])
            if self.on_ring:  # on its own thread: it takes the runner's lock, and the runner may be waiting for ours
                threading.Thread(target=self._ring, args=(dict(t),), daemon=True).start()

    def _ring(self, t):
        try:
            self.on_ring(t)
        except Exception:
            log.exception("showing the timer alarm failed")

    def tick(self, now=None):
        with self.lock:
            self._tick(self.clock() if now is None else now)

    def _changed(self):
        self.version += 1
        if hasattr(self.store, "_save"):
            self.store._save("timers.json", {"timers": self.items, "pomodoro": self.pomodoro})
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("timer listener failed")

    def watch(self, every=0.5):
        """Keep time in the background (alarms ring even when the Timers app isn't on the board)."""
        def loop():
            while True:
                time.sleep(every)
                try:
                    self.tick()
                except Exception:
                    log.exception("timers tick failed")
        threading.Thread(target=loop, daemon=True, name="timers").start()


_by_store = {}
_lock = threading.Lock()


def for_store(store):
    """The one Timers for this store (the app, the API and MQTT share it)."""
    with _lock:
        t = _by_store.get(id(store))
        if t is None or t.store is not store:
            t = _by_store[id(store)] = Timers(store)
        return t
