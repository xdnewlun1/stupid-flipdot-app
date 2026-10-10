"""Slideshow: cycles through a saved list of uploaded images and GIFs.

Images stay up for a set time (10 s by default); GIFs play through (once by default) and
then it moves on, with a whole-board transition between items. Files are converted to dots
on a background thread, the next item ahead of time, so playback doesn't stall.
"""

import bisect
import collections
import logging
import math
import queue
import random
import threading

from canvas import Canvas
from fonts import NORMAL

from .base import App, P, register
from .media import MIN_DELAY, convert_image
from transitions import Transition

from . import slidefx

log = logging.getLogger("slideshow")

# ---- background conversion with a small cache -------------------------------------

_cache = collections.OrderedDict()  # key -> [(Canvas, delay_seconds)]
_cache_lock = threading.Lock()
_jobs = queue.Queue()
_pending = set()
CACHE_SIZE = 40


def _key(path, settings, w, h, first_only=False):
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    opts = (settings["mode"], bool(settings["dither"]), int(settings["threshold"]), bool(settings["invert"]))
    return (str(path), mtime, opts, w, h, first_only)


def convert(path, settings, w, h, first_only=False):
    """Convert now (blocking) and cache. Returns [(Canvas, delay)] or raises."""
    key = _key(path, settings, w, h, first_only)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    frames = convert_image(path.read_bytes(), w, h, mode=settings["mode"], dither=bool(settings["dither"]),
                           threshold=int(settings["threshold"]), invert=bool(settings["invert"]),
                           max_frames=1 if first_only else 500)
    result = [(Canvas.from_rows(f["rows"]), max(MIN_DELAY, f["delay"] / 1000)) for f in frames]
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result


def cached(path, settings, w, h):
    with _cache_lock:
        return _cache.get(_key(path, settings, w, h))


def prefetch(path, settings, w, h):
    key = _key(path, settings, w, h)
    with _cache_lock:
        if key in _cache or key in _pending:
            return
        _pending.add(key)
    _jobs.put((key, path, dict(settings), w, h))


def _worker():
    while True:
        key, path, settings, w, h = _jobs.get()
        try:
            convert(path, settings, w, h)
        except Exception as e:
            log.warning("couldn't convert %s: %s", path.name, e)
            with _cache_lock:
                _cache[key] = []  # remember the failure so it gets skipped
        finally:
            with _cache_lock:
                _pending.discard(key)


threading.Thread(target=_worker, daemon=True).start()


# ---- the app --------------------------------------------------------------------

def gif_speed(item, settings):
    """Playback speed for a GIF: the item's own, else the slideshow's (0.25x-4x)."""
    try:
        v = float(item.get("speed") or settings.get("gif_speed") or 1)
    except (TypeError, ValueError):
        v = 1.0
    return max(0.25, min(4.0, v))


class Player:
    """Plays one item's frames from a start time; the frame is computed from the clock, so it never drifts."""

    def __init__(self, item, frames, started, speed=1.0):
        self.item, self.frames, self.started = item, frames, started
        self.cum = []
        t = 0.0
        for _, d in frames:  # GIF speed shortens/stretches every delay; frames faster than the board are skipped
            t += d / speed
            self.cum.append(t)
        self.length = t if len(frames) > 1 else 0.0

    EPS = 1e-6  # so a tick that lands exactly on a frame boundary counts as the new frame

    def index(self, now):
        if self.length <= 0:
            return 0
        t = (now - self.started) % self.length + self.EPS
        return min(bisect.bisect_right(self.cum, t), len(self.frames) - 1)

    def frame(self, now):
        return self.frames[self.index(now)][0]

    def next_change(self, now):
        """Seconds until this item shows a different frame."""
        if self.length <= 0:
            return 1.0
        t = (now - self.started) % self.length + self.EPS
        i = self.index(now)
        return max(0.005, self.cum[i] - t + self.EPS)

    def end_time(self, settings):
        if self.length > 0:  # GIF: whole loops, at least `loops` of them and at least the minimum time
            loops = int(self.item.get("loops") or settings["gif_loops"] or 1)
            min_secs = float(settings.get("gif_min_seconds") or 0)
            loops = max(loops, math.ceil(min_secs / self.length - 1e-9))
            return self.started + loops * self.length
        return self.started + float(self.item.get("seconds") or settings["image_seconds"] or 10)


@register
class Slideshow(App):
    id = "slideshow"
    title = "Slideshow"
    category = "Media"
    description = "Cycles through your uploaded images and GIFs (set them up on the Slideshows page)."
    params = [P("gallery", "gallery", "", "Slideshow"), P("start", "hidden", None)]
    interval = 0.05
    pausable = True

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.cut = False  # the next jump skips the transition (stepping through while paused)
        self.gid = self.p["gallery"]
        ctx.store.gallery(self.gid)  # raises if it doesn't exist
        self.order = []
        self.pos = -1
        self.cur = None    # Player for the item on screen
        self.prev = None   # Player for the outgoing item while a transition runs
        self.trans = None
        self.request_transition = None
        self.jump_to = self.p.get("start")
        self.fx = None
        self.fx_rng = random.Random()  # file name to play next, right away

    @property
    def item(self):
        return self.cur.item if self.cur else None

    def _path(self, item):
        return self.ctx.store.gallery_dir(self.gid) / item["file"]

    def jump(self, file, cut=False):
        """Play this item now (with the usual transition, unless cut), then carry on from there."""
        self.jump_to = file
        self.cut = cut

    def skip(self, delta, cut=False):
        """Next (+1) or previous (-1) item in play order."""
        try:
            items = self.ctx.store.gallery(self.gid)["items"]
        except KeyError:
            return
        if not items:
            return
        order = self.order if len(self.order) == len(items) else list(range(len(items)))
        k = (max(self.pos, 0) + delta) % len(order)
        self.jump(items[order[k]]["file"], cut)

    def reload(self):
        """Conversion settings changed: show the current item again with them (no transition)."""
        if self.cur is not None:
            self.jump(self.cur.item["file"], cut=True)

    def _peek_next(self, items, settings):
        """(position, item) that plays next; reshuffles/rebuilds the order at the end of each pass."""
        if self.jump_to:
            order = self.order if len(self.order) == len(items) else list(range(len(items)))
            for k, idx in enumerate(order):
                if items[idx]["file"] == self.jump_to:
                    return k, items[idx], order
            self.jump_to = None  # not in the slideshow (any more)
        if not self.order or len(self.order) != len(items) or self.pos + 1 >= len(self.order):
            order = list(range(len(items)))
            if settings["shuffle"]:
                random.shuffle(order)
            return 0, items[order[0]], order
        return self.pos + 1, items[self.order[self.pos + 1]], None

    def _outgoing_frame(self, now):
        """The outgoing GIF advances exactly one frame per incoming frame, so neither skips or repeats
        frames even when their timings differ a little (e.g. 70/60 ms alternation for 15 fps)."""
        if self.cur.length > 0 and self.prev.length > 0:
            i = (self.prev_start_index + self.cur.index(now)) % len(self.prev.frames)
            return self.prev.frames[i][0]
        return self.prev.frame(now)

    def _fps(self):
        return max(5, min(30, float(self.ctx.store.settings.get("display", {}).get("transition_fps", 15))))

    def render(self, c, now):
        try:
            g = self.ctx.store.gallery(self.gid)
        except KeyError:
            c.text_center(17, "Slideshow deleted", NORMAL)
            return
        items, settings = g["items"], g["settings"]
        if not items:
            c.text_center(12, "Empty slideshow", NORMAL)
            c.text_center(22, "upload some images", NORMAL)
            return
        tdur = float(settings["transition_duration"]) if settings["transition"] != "none" and len(items) > 1 else 0.0

        # start the next item when the current one is about to end (minus the transition time,
        # so the end of this item plays *through* the transition)
        if self.jump_to and self.trans is not None:
            self.trans, self.prev = None, None  # cut the running transition short; start the jump from here
        if self.cur is None or self.jump_to or (self.trans is None and now >= self.cur.end_time(settings) - tdur):
            for _ in range(len(items)):
                pos, nxt, new_order = self._peek_next(items, settings)
                frames = cached(self._path(nxt), settings, c.w, c.h)
                if frames is None:
                    prefetch(self._path(nxt), settings, c.w, c.h)
                    break  # not converted yet: keep playing the current item (it loops)
                if new_order is not None:
                    self.order = new_order
                self.pos = pos
                self.jump_to = None
                if not frames:
                    continue  # couldn't be converted; skip it
                incoming = Player(nxt, frames, now, gif_speed(nxt, settings))
                cut, self.cut = self.cut, False
                if self.cur is not None and tdur > 0 and not cut:
                    self.prev = self.cur
                    self.prev_start_index = self.prev.index(now)
                    self.trans = Transition(settings["transition"], self.prev.frame(now), c.w, c.h, tdur)
                    self.trans.start = now
                self.cur = incoming
                # maybe give this item a random effect (rate and kinds set per slideshow)
                length = max(0.0, incoming.end_time(settings) - now)
                self.fx = slidefx.roll(settings, self.fx_rng, now, length)
                after_pos, after, _ = self._peek_next(items, settings)
                prefetch(self._path(after), settings, c.w, c.h)
                break

        if self.cur is None:
            prefetch(self._path(items[0]), settings, c.w, c.h)
            c.text_center(17, "Loading...", NORMAL)
            return

        frame = self.cur.frame(now)
        if self.fx is not None:
            frame = self.fx.apply(frame, now)
        if self.trans is not None:
            out = self.trans.mix(frame, now, self._outgoing_frame(now))  # both items keep animating
            if self.trans.done(now):
                self.trans, self.prev = None, None
            frame = out
        if (frame.w, frame.h) == (c.w, c.h):
            c.px[:] = frame.px
        else:
            c.blit(frame, 0, 0, transparent=False)

        # wake up exactly when something changes: a GIF frame, the next item, or the next transition tick
        if self.trans is not None:
            if self.cur.length > 0:
                # tick on the incoming GIF's own frames (15 fps for typical flipdot GIFs)
                self.interval = self.cur.next_change(now)
            elif self.prev.length > 0:
                self.interval = self.prev.next_change(now)
            else:
                # two still images: a steady grid (Settings > transition frame rate) from the start
                step = 1 / self._fps()
                k = math.floor((now - self.trans.start) / step + 1e-6) + 1
                self.interval = max(0.005, self.trans.start + k * step - now)
        else:
            waits = [self.cur.next_change(now), max(0.005, self.cur.end_time(settings) - tdur - now)]
            self.interval = max(0.005, min(waits + [1.0]))
        if self.fx is not None:
            if self.fx.active(now) and self.fx.animated:
                self.interval = min(self.interval, 1 / self._fps())
            elif now < self.fx.start:  # wake up when a burst begins
                self.interval = min(self.interval, max(0.005, self.fx.start - now))
            elif self.fx.end is not None and now < self.fx.end:
                self.interval = min(self.interval, max(0.005, self.fx.end - now))

    def describe(self):
        g = self.ctx.store.galleries.get(self.gid)
        name = g["name"] if g else "Slideshow"
        if self.cur:
            return f"{name}: {self.cur.item.get('name', '')} ({self.pos + 1}/{len(self.order)})"
        return name
