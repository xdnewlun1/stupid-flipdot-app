"""App framework: everything that draws on the board over time is an App.

An app is created with its params, then `render(canvas, now)` is called every
`interval` seconds. render() returns False when the app has finished (e.g. a
one-shot notification), anything else means keep going.
"""

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("apps")

REGISTRY = {}


def register(cls):
    REGISTRY[cls.id] = cls
    return cls


def P(name, type="text", default=None, label=None, **extra):
    """Describe one app parameter (used by the web UI to build forms)."""
    return {"name": name, "type": type, "default": default, "label": label or name.replace("_", " ").capitalize(), **extra}


class App:
    id = "base"
    title = "Base"
    category = "Other"
    description = ""
    params = []
    interval = 0.1  # seconds between frames
    interactive = False  # accepts controller input
    pausable = False  # Pause freezes it: render() gets a clock that stands still (slideshows, animations)

    def __init__(self, ctx, **params):
        self.ctx = ctx
        self.w, self.h = ctx.width, ctx.height
        self.p = {p["name"]: p["default"] for p in self.params}
        self.p.update({k: v for k, v in params.items() if v is not None})
        self.started = time.time()

    def render(self, c, now):
        raise NotImplementedError

    @classmethod
    def needs_setup(cls, store, params):
        """What has to be set up first (shown in the UI; playlists skip the item), or None."""
        return None

    def input(self, key, action="down", player=1):
        pass

    def stop(self):
        pass

    def describe(self):
        return self.title


class NeedsSetup(ValueError):
    """Raised by fetch() when a setting is missing: shown as a calm hint, not an error."""


def setup_needed(store, spec):
    """The needs_setup() message for an app spec, or None."""
    cls = REGISTRY.get(ALIASES.get(spec.get("app"), spec.get("app")))
    try:
        return cls.needs_setup(store, spec.get("params") or {}) if cls else None
    except Exception:
        return None


ALIASES = {"spotify": "music"}  # Spotify was replaced by Music Assistant


def create(ctx, spec):
    """spec: {"app": id, "params": {...}}"""
    app_id = ALIASES.get(spec.get("app"), spec.get("app"))
    if app_id != spec.get("app"):
        spec = {**spec, "app": app_id, "params": {}}
    cls = REGISTRY.get(app_id)
    if cls is None:
        raise ValueError(f"unknown app {spec.get('app')!r}")
    app = cls(ctx, **(spec.get("params") or {}))
    app.spec = {"app": app_id, "params": spec.get("params") or {}}
    return app


def catalog():
    return [
        {"id": c.id, "title": c.title, "category": c.category, "description": c.description,
         "params": c.params, "interactive": c.interactive}
        for c in REGISTRY.values()
    ]


# ---- networking helpers -------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()
UA = "Mozilla/5.0 (X11; Linux x86_64) flipdot-board/1.0"


def http_get(url, ttl=60, headers=None, raw=False, timeout=10):
    """GET with a small in-memory cache so several apps/playlists share results."""
    key = (url, json.dumps(headers, sort_keys=True))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    value = data if raw else json.loads(data)
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


class Widget(App):
    """An app whose data comes from the network. fetch() runs in a background
    thread every `refresh` seconds; render() draws self.data (or a status)."""

    refresh = 300
    interval = 0.5

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.data = None
        self.error = None
        self.setup = None  # NeedsSetup message
        self._last_fetch = 0
        self._fetching = False
        self._stopped = False

    def fetch(self):
        raise NotImplementedError

    def _do_fetch(self):
        try:
            self.data = self.fetch()
            self.error = self.setup = None
        except NeedsSetup as e:
            self.error = self.setup = str(e)
        except Exception as e:  # network errors, bad config, API changes...
            log.warning("%s: %s", self.id, e)
            self.error = str(e)
        finally:
            self._fetching = False

    def poll(self):
        if self._stopped or self._fetching or time.time() - self._last_fetch < self.refresh:
            return
        self._fetching = True
        self._last_fetch = time.time()
        threading.Thread(target=self._do_fetch, daemon=True).start()

    def render(self, c, now):
        self.poll()
        if self.data is None and self.setup:
            # a missing setting isn't a fault: name the app and say what to set, no "Error"
            from canvas import wrap_text
            from fonts import SMALL
            lines = wrap_text(self.setup[:80], SMALL, c.w - 4)[:3]
            top = (c.h - 10 - 6 * len(lines)) // 2
            c.text_center(top, self.title.split(" (")[0][:14])
            for i, line in enumerate(lines):
                c.text_center(top + 10 + i * 6, line, SMALL)
            return
        if self.data is None:
            msg = "Error" if self.error else "Loading..."
            c.text_center(c.h // 2 - 8, msg)
            if self.error:
                from canvas import wrap_text
                from fonts import SMALL
                for i, line in enumerate(wrap_text(self.error[:80], SMALL, c.w)[:3]):
                    c.text_center(c.h // 2 + i * 6, line, SMALL)
            return
        self.draw(c, now)

    def draw(self, c, now):
        raise NotImplementedError

    def stop(self):
        self._stopped = True
