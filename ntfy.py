"""ntfy subscription: messages published to ntfy topics (ntfy.sh or a self-hosted server) become notifications.

Uses ntfy's JSON stream (GET <server>/<topic,topic>/json): one JSON object per line, "keepalive" events about every
45 s. Dropped connections come back with backoff, asking for what was missed (since=<last message id>).
ntfy priority 1-5 maps to low/low/normal/high/critical, and the first tag with an icon of ours picks the icon."""

import json
import logging
import re
import socket
import threading
import time
import urllib.parse
import urllib.request

import notifications

log = logging.getLogger("ntfy")

READ_TIMEOUT = 90  # ntfy sends a keepalive every 45 s; nothing for this long means the connection is dead
TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")

TAG_ICONS = {
    "bell": "bell", "bellhop_bell": "bell", "loudspeaker": "bell", "mega": "bell",
    "envelope": "mail", "email": "mail", "e-mail": "mail", "mailbox": "mail", "incoming_envelope": "mail",
    "package": "mail", "mail": "mail",
    "door": "door", "doorbell": "door", "house": "door", "key": "door", "lock": "door", "unlock": "door",
    "warning": "alert", "rotating_light": "alert", "rotating_lights": "alert", "skull": "alert", "x": "alert",
    "no_entry": "alert", "fire": "alert", "sos": "alert", "exclamation": "alert", "heavy_exclamation_mark": "alert",
    "rotating": "alert", "alert": "alert",
    "timer_clock": "timer", "alarm_clock": "timer", "hourglass": "timer", "stopwatch": "timer", "timer": "timer",
    "watch": "timer",
    "musical_note": "music", "notes": "music", "headphones": "music", "radio": "music", "music": "music",
    "sunny": "weather", "cloud": "weather", "umbrella": "weather", "snowflake": "weather", "zap": "weather",
    "partly_sunny": "weather", "rain_cloud": "weather", "thermometer": "weather", "tornado": "weather",
    "weather": "weather",
    "calendar": "calendar", "date": "calendar", "spiral_calendar": "calendar", "calendar_spiral": "calendar",
    "bust_in_silhouette": "person", "busts_in_silhouette": "person", "man": "person", "woman": "person",
    "person": "person", "wave": "person", "baby": "person", "dog": "person", "cat": "person",
}


def topics_of(value):
    """A list of valid topic names from a list or a comma/space separated string."""
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    out = []
    for t in value or []:
        t = str(t).strip()
        if not t:
            continue
        if not TOPIC.match(t):
            raise ValueError(f"not an ntfy topic name: {t!r} (letters, digits, - and _ only)")
        if t not in out:
            out.append(t)
    return out


def to_body(ev):
    """An ntfy message event as an /api/notify body."""
    tags = [str(t).lower() for t in ev.get("tags") or []]
    icon = next((TAG_ICONS[t] for t in tags if t in TAG_ICONS), None)
    text = str(ev.get("message") or "").strip() or str(ev.get("title") or "").strip() or "(empty message)"
    body = {"text": text[:300], "priority": notifications.priority(ev.get("priority") or 3), "source": "ntfy",
            "duration": 12 if len(text) > 30 else 10}
    if ev.get("title") and ev.get("message"):
        body["title"] = str(ev["title"])[:80]
    if icon:
        body["icon"] = icon
    return body


class Subscriber:
    def __init__(self, server, topics, on_message, token="", min_backoff=1.0, max_backoff=60.0):
        self.server = (server or "https://ntfy.sh").rstrip("/")
        self.topics = topics_of(topics)
        self.on_message, self.token = on_message, token or ""
        self.min_backoff, self.max_backoff = min_backoff, max_backoff
        self.connected = False
        self.error = None
        self.last_message = None
        self.received = 0
        self.connects = 0
        self.last_id = None
        self._down_since = None
        self._stop = threading.Event()
        self._resp = None
        self.thread = None

    def url(self):
        q = {}
        if self.last_id:
            q["since"] = self.last_id
        elif self._down_since:
            q["since"] = str(int(self._down_since))
        path = ",".join(urllib.parse.quote(t) for t in self.topics)
        return f"{self.server}/{path}/json" + (f"?{urllib.parse.urlencode(q)}" if q else "")

    def start(self):
        if not self.topics:
            return self
        self.thread = threading.Thread(target=self._run, daemon=True, name="ntfy")
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        r = self._resp
        if r is not None:
            try:  # wake the blocked read
                r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

    def _run(self):
        backoff = self.min_backoff
        while not self._stop.is_set():
            try:
                self._stream()
                backoff = self.min_backoff  # a clean end (the server closed it): reconnect soon
            except Exception as e:
                if self._stop.is_set():
                    break
                self.error = str(e) or e.__class__.__name__
                log.info("ntfy: %s; reconnecting in %.0f s", self.error, backoff)
            if self.connected or self._down_since is None:
                self._down_since = time.time()
            self.connected = False
            if self._stop.wait(backoff):
                break
            backoff = min(self.max_backoff, backoff * 2)

    def _stream(self):
        headers = {"User-Agent": "flipdot-board"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url(), headers=headers)
        with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as r:
            self._resp = r
            self.connected, self.error = True, None
            self.connects += 1
            try:
                for line in r:
                    if self._stop.is_set():
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("event") != "message":
                        continue  # open, keepalive, poll_request
                    self.last_id = ev.get("id") or self.last_id
                    self.last_message = time.time()
                    self.received += 1
                    try:
                        self.on_message(to_body(ev))
                    except Exception as e:
                        log.warning("ntfy message not shown: %s", e)
            finally:
                self._resp = None

    def status(self):
        return {"connected": self.connected, "error": self.error, "topics": self.topics, "server": self.server,
                "received": self.received, "last_message": self.last_message}


_sub = None
_lock = threading.Lock()


def start(store, notify_fn):
    """(Re)start the subscription from the notification settings; stops it when switched off."""
    global _sub
    cfg = notifications.settings(store)["ntfy"]
    with _lock:
        if _sub is not None:
            _sub.stop()
            _sub = None
        if not cfg.get("enabled"):
            return None
        try:
            topics = topics_of(cfg.get("topics"))
        except ValueError as e:
            log.warning("ntfy: %s", e)
            return None
        if not topics:
            return None
        _sub = Subscriber(cfg.get("server") or "https://ntfy.sh", topics, notify_fn, cfg.get("token") or "").start()
        log.info("ntfy: subscribed to %s on %s", ", ".join(topics), _sub.server)
        return _sub


def status():
    s = _sub
    return s.status() if s is not None else {"connected": False, "error": None, "topics": [], "received": 0}
