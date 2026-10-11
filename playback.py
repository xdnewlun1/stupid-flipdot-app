"""Playlist playback helpers for the runner: the order items play in (shuffle without repeats), the
"only show if" conditions on items, and the playlist's random effects and React to music, applied on top of
whatever an item draws (the same effects as slideshows: apps/slidefx.py)."""

import datetime as dt
import logging
import random

import audio_signal
from apps import slidefx

log = logging.getLogger("playback")

# ---- order ----------------------------------------------------------------------------------------


class Order:
    """Which item plays next. In order: 0, 1, 2... Shuffled: every item once per pass, in a new order each
    pass, and never the same item twice in a row (also across passes)."""

    def __init__(self, n, shuffle=False, rng=None):
        self.n, self.shuffle = n, bool(shuffle)
        self.rng = rng or random.Random()
        self.queue, self.history = [], []

    def matches(self, n, shuffle):
        return self.n == n and self.shuffle == bool(shuffle)

    def _refill(self):
        if not self.shuffle:
            last = self.history[-1] if self.history else -1
            self.queue = [(last + 1 + k) % self.n for k in range(self.n)]
            return
        order = list(range(self.n))
        self.rng.shuffle(order)
        if self.n > 1 and self.history and order[0] == self.history[-1]:
            swap = self.rng.randrange(1, self.n)
            order[0], order[swap] = order[swap], order[0]
        self.queue = order

    def next(self):
        if self.n <= 0:
            return None
        if not self.queue:
            self._refill()
        i = self.queue.pop(0)
        self.history = (self.history + [i])[-200:]
        return i

    def back(self):
        """Make next() return the item before the current one (then carry on from there)."""
        if not self.history:
            return
        if not self.shuffle:
            cur = self.history[-1]
            self.history, self.queue = [(cur - 2) % self.n], []  # the in-order refill starts at cur - 1
        elif len(self.history) >= 2:
            cur, prev = self.history.pop(), self.history.pop()
            self.queue[:0] = [prev, cur]
        else:
            self.queue.insert(0, self.history.pop())

    def skipped(self):
        """The item just taken couldn't play (its condition is false, or it needs setup)."""
        if self.history:
            self.history.pop()


# ---- conditions -------------------------------------------------------------------------------------

CONDITIONS = {
    "": "Always",
    "music": "Music is playing",
    "no_music": "No music is playing",
    "dark": "After sunset",
    "daylight": "In daylight",
    "present": "Someone is at the mirror camera",
    "absent": "Nobody is at the mirror camera",
    "quiet": "During quiet hours",
    "not_quiet": "Outside quiet hours",
    "has_data": "It has something to show",
}
NIGHT = (19 * 60, 7 * 60)  # after sunset / daylight without a location: 19:00 to 07:00
PROBE_KEEP = 900  # seconds an unused "has something to show" check is kept (it fetches in the background)


def music_playing(store):
    """Is live music reaching the board (Settings → Music)?"""
    try:
        _, s = audio_signal.raw(store)
    except Exception:
        return False
    return bool(s and s.get("live", True) and s.get("playing", True) is not False)


def is_dark(store, t):
    """Between sunset and sunrise at the weather location (19:00 to 07:00 without one)."""
    import sun
    loc = store.settings.get("weather") or {}
    m = t.hour * 60 + t.minute
    if loc.get("lat") is None or loc.get("lon") is None:
        return m >= NIGHT[0] or m < NIGHT[1]
    s = sun.local_minutes(t.date(), float(loc["lat"]), float(loc["lon"]))
    if s["sunrise"] is None or s["sunset"] is None:  # polar day (sun on the pole's side) or polar night
        decl = sun._solar(sun._jd(t.date()) + 0.5)[1]
        return float(loc["lat"]) * decl < 0
    return m < s["sunrise"] or m >= s["sunset"]


def someone_present():
    """The mirror camera's presence (camera.py); False when no camera is running."""
    import camera
    cam = camera._cam
    return bool(cam is not None and cam.presence.present)


def quiet_now(store, t):
    from boardcare import quiet_active
    return quiet_active(store.settings.get("quiet_hours") or {}, t)


class Probes:
    """For "has something to show": a copy of the item's app kept in the background, so its data is fetched
    before the item comes up. Probes nobody asked about for a while are stopped."""

    def __init__(self, ctx, create):
        self.ctx, self.create = ctx, create
        self.items = {}  # key -> [app, last asked]

    def has_data(self, item, now, minutes=None):
        key = repr((item.get("app"), sorted((item.get("params") or {}).items(), key=lambda kv: kv[0])))
        hit = self.items.get(key)
        if hit is None:
            try:
                hit = self.items[key] = [self.create(self.ctx, {"app": item.get("app"), "params": item.get("params") or {}}), now]
            except Exception as e:
                log.debug("probe for %s failed: %s", item.get("app"), e)
                return False
        hit[1] = now
        return app_has_data(hit[0], now, minutes)

    def sweep(self, now):
        for k, (app, last) in list(self.items.items()):
            if now - last > PROBE_KEEP:
                del self.items[k]
                try:
                    app.stop()
                except Exception:
                    pass


def app_has_data(app, now, minutes=None):
    """Has this app something worth showing? Apps may say (has_data(now, minutes)); widgets have data once
    fetched; everything else always has."""
    poll = getattr(app, "poll", None)
    if poll is not None:
        try:
            poll()  # widgets fetch in the background
        except Exception:
            pass
    fn = getattr(app, "has_data", None)
    if callable(fn):
        try:
            return bool(fn(now, minutes))
        except Exception:
            return False
    if hasattr(app, "data") and hasattr(app, "fetch"):
        return bool(app.data)
    return True


def condition_ok(cond, item, store, now, probes=None, quiet=None):
    """Does item's condition hold at `now` (timestamp)? cond: {"type", "minutes"?} or None."""
    kind = (cond or {}).get("type") or ""
    if not kind:
        return True
    t = dt.datetime.fromtimestamp(now)
    if kind in ("music", "no_music"):
        return music_playing(store) == (kind == "music")
    if kind in ("dark", "daylight"):
        return is_dark(store, t) == (kind == "dark")
    if kind in ("present", "absent"):
        return someone_present() == (kind == "present")
    if kind in ("quiet", "not_quiet"):
        q = quiet_now(store, t) if quiet is None else quiet
        return q == (kind == "quiet")
    if kind == "has_data":
        minutes = cond.get("minutes")
        minutes = float(minutes) if minutes not in (None, "") else None
        return probes.has_data(item, now, minutes) if probes is not None else True
    return True


def clean_condition(cond):
    if not isinstance(cond, dict) or cond.get("type") not in CONDITIONS or not cond.get("type"):
        return None
    out = {"type": cond["type"]}
    if cond["type"] == "has_data" and cond.get("minutes") not in (None, ""):
        out["minutes"] = max(1, min(1440, int(float(cond["minutes"]))))
    return out


# ---- effects on top -----------------------------------------------------------------------------------

FX_KEYS = ("fx_chance", "fx_effects", "fx_mode", "fx_seconds", "fx_strength")
REACT_KEYS = ("react_enabled", "react_map", "react_fallback")


def clean_playlist(pl):
    """Tidy a playlist's playback options and item conditions in place."""
    pl["shuffle"] = bool(pl.get("shuffle"))
    if pl.get("fx_chance") is not None:
        pl["fx_chance"] = max(0, min(100, int(float(pl["fx_chance"] or 0))))
    if pl.get("fx_effects") is not None:
        pl["fx_effects"] = [e for e in pl["fx_effects"] if e in slidefx.EFFECTS] or list(slidefx.EFFECTS)
    if pl.get("fx_mode") not in (None, "burst", "whole"):
        pl["fx_mode"] = "burst"
    if pl.get("fx_seconds") is not None:
        pl["fx_seconds"] = max(0.3, min(30.0, float(pl["fx_seconds"] or 3)))
    if pl.get("fx_strength") is not None:
        pl["fx_strength"] = max(0, min(100, int(float(pl["fx_strength"] or 0))))
    pl["react_enabled"] = bool(pl.get("react_enabled"))
    if pl.get("react_map") is not None:
        pl["react_map"] = slidefx.clean_react_map(pl["react_map"])
    if pl.get("react_fallback") not in (None, "none", "beat"):
        pl["react_fallback"] = "none"
    for item in pl.get("items") or []:
        c = clean_condition(item.get("when"))
        if c:
            item["when"] = c
        else:
            item.pop("when", None)
        if item.get("transition_duration") not in (None, ""):
            item["transition_duration"] = max(0.1, min(10.0, float(item["transition_duration"])))
        else:
            item.pop("transition_duration", None)
    return pl


class Effects:
    """A playlist's random effect for the item on the board and its React to music, applied to each frame."""

    def __init__(self, store, rng=None):
        self.store = store
        self.rng = rng or random.Random()
        self.fx = None
        self.react = slidefx.Reactive(self.rng)
        self.react_on = False
        self.reading = None

    @staticmethod
    def wanted(pl):
        return bool(pl) and (float(pl.get("fx_chance") or 0) > 0 or bool(pl.get("react_enabled")))

    def item_started(self, pl, now, seconds):
        """A new item: maybe roll it a random effect (chance and kinds from the playlist)."""
        self.fx = slidefx.roll(pl, self.rng, now, seconds) if pl and float(pl.get("fx_chance") or 0) > 0 else None

    def reset(self):
        self.fx = None
        self.react.last = None
        self.react_on = False

    def plan(self, pl, now, quiet):
        """React to music: the effect strengths for this frame, or None (off, quiet hours or no music)."""
        on = bool(pl and pl.get("react_enabled")) and not quiet
        r = None
        if on:
            r = audio_signal.reading(self.store, "beat" if pl.get("react_fallback") == "beat" else None)
            on = r["live"] or r["generated"]
        self.react_on = on
        if not on:
            self.react.last = None  # the motion clocks pause rather than catch up
            return None
        self.reading = r
        return slidefx.react_plan(slidefx.clean_react_map(pl.get("react_map")), r)

    def random_fx(self, frame, now):
        return self.fx.apply(frame, now) if self.fx is not None else frame

    def music_fx(self, frame, now, plan):
        return self.react.apply(frame, now, plan, self.reading) if plan else frame

    def interval(self, now, fps):
        """The longest the runner may wait for the next frame so the effects keep moving (None: no limit)."""
        waits = []
        if self.react_on:
            waits.append(1 / fps)
        fx = self.fx
        if fx is not None:
            if fx.active(now) and fx.animated:
                waits.append(1 / fps)
            elif now < fx.start:
                waits.append(max(0.005, fx.start - now))
            elif fx.end is not None and now < fx.end:
                waits.append(max(0.005, fx.end - now))
        return min(waits) if waits else None


def item_seconds(item):
    try:
        return max(1.0, float(item.get("duration") or 30))
    except (TypeError, ValueError):
        return 30.0

