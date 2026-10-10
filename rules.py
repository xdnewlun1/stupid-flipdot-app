"""Automations: "when <trigger>, do <action>" rules, saved in data/rules.json.

Triggers (edge-triggered: they fire when the condition starts, not while it lasts):
    music_start / music_stop   Music Assistant play state (only polled when MA is set up)
    weather                    the current weather turns rain / snow / clear (Open-Meteo, every 10 min)
    event                      POST /api/event/<name> or MQTT <prefix>/event with the name as payload
    idle                       nothing running and no change on the board for N minutes
Actions: app, composition, playlist, notify (text / scene / image URL), schedule (resume), black.
"""

import logging
import threading
import time
import urllib.parse

from apps.base import http_get

log = logging.getLogger("rules")

TICK = 3              # seconds between evaluations
MUSIC_EVERY = 5       # seconds between Music Assistant polls
WEATHER_EVERY = 600   # seconds between weather checks
DEFAULT_COOLDOWN = 60
TRIGGERS = ("music_start", "music_stop", "weather", "event", "idle")
ACTIONS = ("app", "composition", "playlist", "notify", "schedule", "black")
WEATHER_KINDS = ("rain", "snow", "clear")


def weather_class(code):
    """Open-Meteo WMO weather code -> rain | snow | clear | cloudy | fog."""
    if code in (0, 1):
        return "clear"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 51 <= code <= 67 or 80 <= code <= 82 or code >= 95:
        return "rain"
    if code in (45, 48):
        return "fog"
    return "cloudy"


def validate(rules):
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    out = []
    for i, r in enumerate(rules):
        trig, act = r.get("trigger") or {}, r.get("action") or {}
        if trig.get("type") not in TRIGGERS:
            raise ValueError(f"rule {i + 1}: unknown trigger {trig.get('type')!r}")
        if act.get("type") not in ACTIONS:
            raise ValueError(f"rule {i + 1}: unknown action {act.get('type')!r}")
        if trig["type"] == "weather" and trig.get("weather") not in WEATHER_KINDS:
            raise ValueError(f"rule {i + 1}: weather must be one of {', '.join(WEATHER_KINDS)}")
        if trig["type"] == "event" and not str(trig.get("event") or "").strip():
            raise ValueError(f"rule {i + 1}: give the event a name")
        if trig["type"] == "idle" and not float(trig.get("minutes") or 0) > 0:
            raise ValueError(f"rule {i + 1}: idle minutes must be more than 0")
        out.append({
            "id": str(r.get("id") or f"r{int(time.time() * 1000) % 10**9}{i}"),
            "name": str(r.get("name") or ""),
            "enabled": bool(r.get("enabled", True)),
            "cooldown": max(0.0, float(r.get("cooldown", DEFAULT_COOLDOWN) or 0)),
            "trigger": trig, "action": act,
        })
    if len({r["id"] for r in out}) != len(out):
        raise ValueError("rule ids must be unique")
    return out


class Automations:
    def __init__(self, runner, board, store, notify_fn):
        self.runner, self.board, self.store, self.notify_fn = runner, board, store, notify_fn
        self.rules = store._load("rules.json", {"rules": []}).get("rules", [])
        self.last_fired = {}  # rule id -> time
        self.last_error = {}  # rule id -> message
        self.music = None     # True / False / None (unknown)
        self.weather = None   # rain | snow | clear | cloudy | fog | None
        self._next_music = self._next_weather = 0
        self._idle_since = time.time()
        self._idle_fired = set()
        self._board_version = board.version
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def save(self, rules):
        rules = validate(rules)
        with self._lock:
            self.rules = rules
            self.store._save("rules.json", {"rules": rules})
        return rules

    def status(self):
        return {"rules": self.rules, "last_fired": self.last_fired, "errors": self.last_error,
                "music": self.music, "weather": self.weather,
                "idle_minutes": round((time.time() - self._idle_since) / 60, 1) if self._is_idle() else 0}

    # ---- firing ------------------------------------------------------------------

    def _matching(self, kind, **match):
        for r in self.rules:
            t = r["trigger"]
            if r.get("enabled", True) and t.get("type") == kind and all(
                    str(t.get(k) or "").strip().lower() == str(v).strip().lower() for k, v in match.items()):
                yield r

    def fire(self, rule, now=None, force=False):
        """Run a rule's action unless it's cooling down. Returns True if it ran."""
        now = now or time.time()
        if not force and now - self.last_fired.get(rule["id"], -1e9) < float(rule.get("cooldown", DEFAULT_COOLDOWN)):
            return False
        self.last_fired[rule["id"]] = now
        try:
            self.run_action(rule["action"])
            self.last_error.pop(rule["id"], None)
            log.info("automation %s fired", rule.get("name") or rule["id"])
        except Exception as e:
            self.last_error[rule["id"]] = str(e)
            log.warning("automation %s failed: %s", rule.get("name") or rule["id"], e)
        return True

    def run_action(self, a):
        kind = a.get("type")
        transition = a.get("transition") or "dissolve"
        if kind == "app":
            self.runner.run({"app": a["app"], "params": a.get("params") or {}}, transition)
        elif kind == "composition":
            comp = self.store.compositions.get(a.get("composition") or "")
            if comp is None:
                raise ValueError(f"no composition {a.get('composition')!r}")
            self.runner.update_layers(comp["layers"], transition)
        elif kind == "playlist":
            self.runner.play_playlist(a.get("playlist") or "")
        elif kind == "notify":
            body = {"duration": float(a.get("duration") or 10), "transition": transition}
            for k in ("text", "scene", "url"):
                if a.get(k):
                    body[k] = a[k]
                    break
            else:
                raise ValueError("the notification needs a text, scene or image URL")
            self.notify_fn(body)
        elif kind == "schedule":
            self.runner.resume_schedule()
        elif kind == "black":
            self.runner.stop()
            self.board.fill(False)
        else:
            raise ValueError(f"unknown action {kind!r}")

    def event(self, name):
        """An incoming named event (HTTP or MQTT). Returns the ids of the rules that ran."""
        return [r["id"] for r in list(self._matching("event", event=name)) if self.fire(r)]

    # ---- background evaluation -----------------------------------------------------

    def _loop(self):
        while True:
            time.sleep(TICK)
            try:
                self.tick()
            except Exception:
                log.exception("automations tick failed")

    def tick(self, now=None):
        now = now or time.time()
        types = {r["trigger"].get("type") for r in self.rules if r.get("enabled", True)}
        if types & {"music_start", "music_stop"}:
            self._check_music(now)
        else:
            self.music = None
        if "weather" in types:
            self._check_weather(now)
        self._check_idle(now)

    def _check_music(self, now):
        if now < self._next_music or not (self.store.settings.get("music_assistant") or {}).get("url"):
            return
        self._next_music = now + MUSIC_EVERY
        playing = self.music_playing()
        if playing is None:
            return  # unreachable: keep the last known state, don't fire
        was, self.music = self.music, playing
        if was is not None and playing != was:
            for r in list(self._matching("music_start" if playing else "music_stop")):
                self.fire(r, now)

    def music_playing(self):
        """Is the configured player (or any player) playing? None if Music Assistant can't be reached."""
        from apps.music import ma_command
        try:
            players = ma_command(self.store, "players/all", timeout=5)
        except Exception as e:
            log.debug("music state: %s", e)
            return None
        want = (self.store.settings.get("music_assistant", {}).get("player") or "").strip().lower()
        if want:
            players = [p for p in players if want in (str(p.get("player_id", "")).lower(),
                                                      str(p.get("display_name") or p.get("name") or "").lower())]
        return any((p.get("playback_state") or p.get("state")) == "playing" for p in players)

    def _check_weather(self, now):
        if now < self._next_weather:
            return
        self._next_weather = now + WEATHER_EVERY
        kind = self.current_weather()
        if kind is None:
            self._next_weather = now + 120  # try again sooner after an error
            return
        was, self.weather = self.weather, kind
        if was is not None and kind != was:
            for r in list(self._matching("weather", weather=kind)):
                self.fire(r, now)

    def current_weather(self):
        loc = self.store.settings.get("weather") or {}
        if loc.get("lat") is None:
            return None
        q = urllib.parse.urlencode({"latitude": loc["lat"], "longitude": loc["lon"], "current": "weather_code"})
        try:
            d = http_get(f"https://api.open-meteo.com/v1/forecast?{q}", ttl=WEATHER_EVERY - 30)
            return weather_class(int(d["current"]["weather_code"]))
        except Exception as e:
            log.debug("weather state: %s", e)
            return None

    def _is_idle(self):
        r = self.runner
        return r.mode == "manual" and r.app is None and not r.notif and not r.transition

    def _check_idle(self, now):
        if not self._is_idle() or self.board.version != self._board_version:
            self._board_version = self.board.version
            self._idle_since = now
            self._idle_fired.clear()
            return
        idle_min = (now - self._idle_since) / 60
        for r in list(self._matching("idle")):
            if r["id"] not in self._idle_fired and idle_min >= float(r["trigger"].get("minutes") or 0):
                self._idle_fired.add(r["id"])  # once per idle stretch
                self.fire(r, now)
