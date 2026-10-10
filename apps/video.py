"""Video: plays a converted video from the Videos library (see video.py), with seeking, a range, loop and speed.

Frames are picked from the clock (frame = position x fps), so a slow tick skips frames instead of
drifting. The display options (dither, cutoff, contrast, gamma, negative) are applied as it plays,
so they can change while it's on the board. A board-size ("native") video skips all of that: each pixel is
one dot, lit from 128 up (Negative still applies if asked for).

Whether the title scrolls by: this app's "show_title" option if it says yes or no, else the video's own
choice (Videos page, player panel), else the "Show the title when a video starts" setting (off for
board-size videos, so it doesn't draw over pixel art).
"""

import time

import video as lib
from canvas import Canvas
from fonts import NORMAL

from .base import App, P, register
from .music import Music

QUIET_FPS = 2  # frames per second during quiet hours (the flip budget still applies on top)
TITLE_SPEED = next((p["default"] for p in Music.params if p["name"] == "scroll_speed"), 20)  # same as the now-playing line


def _auto(v):
    return v in (None, "", "auto")


def title_wanted(param, entry, settings):
    """Should the title scroll by? `param` is the app option (older saved items hold True/False: True was the
    old default, so it follows the setting; False stays off)."""
    if param is False or param == "off":
        return False
    if param == "on":
        return True
    own = entry.get("show_title")
    if own in ("on", "off"):
        return own == "on"
    if entry.get("native"):
        return False
    return bool(settings.get("show_title", True))


@register
class Video(App):
    id = "video"
    title = "Video"
    category = "Media"
    description = "Play a video from the Videos page, or part of one, once or on a loop."
    pausable = True
    params = [
        P("video", "video", "", "Video"),
        P("start", "number", 0, "Start at (seconds)", min=0, step=1),
        P("end", "number", 0, "End at (seconds, 0 = the end)", min=0, step=1),
        P("loop", "checkbox", False, "Loop"),
        P("speed", "number", 1.0, "Speed", min=0.5, max=2, step=0.25),
        P("show_title", "select", "auto", "Title when it starts", options=[
            ["auto", "As set on the Videos page"], ["on", "Show it"], ["off", "Don't show it"]]),
        P("title_position", "select", "top", "Title position", options=[["top", "Top"], ["bottom", "Bottom"]],
          show_if={"show_title": {"not": "off"}}),
        P("dither", "select", "auto", "Style", options=[
            ["auto", "As set for this video"], ["ordered", "Ordered dither (fewer flips)"],
            ["diffusion", "Diffusion dither (more detail, more flips)"], ["threshold", "Threshold (crisp)"]]),
        P("threshold", "number", None, "Brightness cutoff (blank = as set for this video)", min=1, max=254, step=1),
        P("contrast", "number", None, "Contrast (blank = as set for this video)", min=0.2, max=4, step=0.1),
        P("gamma", "number", None, "Gamma (blank = as set for this video)", min=0.2, max=5, step=0.1),
        P("invert", "select", "auto", "Negative", options=[["auto", "As set for this video"], ["off", "No"], ["on", "Yes"]]),
        P("quiet", "select", "slow", "During quiet hours", options=[
            ["slow", f"Slow down to {QUIET_FPS} frames a second"], ["full", "Play normally (the flip limit still applies)"]]),
    ]
    skip_unit = "video"  # Previous/Next step through the library

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        if lib.library is None:
            raise ValueError("videos aren't available")
        self.speed = max(0.5, min(2.0, float(self.p["speed"] or 1)))
        self.loop = bool(self.p["loop"])
        self._load(self.p["video"])
        self.range = (float(self.p["start"] or 0), float(self.p["end"] or 0))
        self.anchor_pos = self.range[0]
        self.anchor_t = None  # set on the first frame (the app's clock may stand still while paused)
        self.pending_seek = None
        self.pos = self.anchor_pos
        self.title_start = None
        self.ended = False
        self._quiet, self._quiet_at = False, 0

    @classmethod
    def needs_setup(cls, store, params):
        v = lib.library.videos.get(params.get("video") or "") if lib.library else None
        if v is None or v.get("deleted"):
            return "a video picked from the Videos page"
        if v.get("status") != "ready":
            return f"{v['name']} to finish converting"
        return None

    def _load(self, vid):
        v = lib.library.get(vid) if vid else None
        if v is None:
            raise ValueError("pick a video")
        self.vid, self.entry = vid, v
        self.reader = lib.library.reader(vid)
        self.fps = float(v["fps"])
        self.native = bool(v.get("native"))
        self.duration = float(v["duration"])
        self.rev = v.get("rev")
        self._checked = time.time()

    # ---- controls (from the web page, under the runner's lock) ----

    def seek(self, t):
        self.pending_seek = max(0.0, min(self.duration, float(t)))
        self.ended = False

    def set_options(self, speed=None, loop=None, start=None, end=None):
        if speed is not None:
            # keep the current position, then carry on at the new speed
            self.pending_seek = self.pos if self.pending_seek is None else self.pending_seek
            self.speed = max(0.5, min(2.0, float(speed)))
        if loop is not None:
            self.loop = bool(loop)
        if start is not None or end is not None:
            s = self.range[0] if start is None else max(0.0, float(start))
            e = self.range[1] if end is None else max(0.0, float(end))
            self.range = (s, e if e > s else 0.0)
        self.ended = False

    def skip(self, delta, cut=False):
        ids = lib.library.ready_ids()
        if not ids:
            return
        i = ids.index(self.vid) if self.vid in ids else -1
        self._load(ids[(i + delta) % len(ids)])
        self.range = (0.0, 0.0)
        self.pending_seek = 0.0
        self.title_start = None  # a new video: its title scrolls by again
        self.ended = False

    # ---- playing ----

    def bounds(self):
        start, end = self.range
        end = end if 0 < end <= self.duration else self.duration
        return min(start, end), end

    def _position(self, now):
        pos = self.anchor_pos + (now - self.anchor_t) * self.speed
        start, end = self.bounds()
        if self.anchor_pos > end + 0.1:  # sought past the end of the range: play on to the end of the video
            end = self.duration
        if pos >= end:
            if self.loop and end - start > 0.05:
                base = start if self.anchor_pos >= start else self.anchor_pos
                pos = base + (pos - base) % (end - base)
            else:
                return end, True
        return pos, False

    def title_on(self):
        return title_wanted(self.p.get("show_title"), self.entry, lib.library.settings())

    def display(self):
        p = self.p
        explicit = {k: p[k] for k in ("dither", "threshold", "contrast", "gamma") if not _auto(p.get(k))}
        if not _auto(p.get("invert")):
            explicit["invert"] = p["invert"] == "on"
        return lib.display_opts({**(self.entry.get("display") or {}), **explicit})

    def _refresh(self, now_wall):
        """Once a second: notice a rename, a new look, a re-conversion or a deletion."""
        if now_wall - self._checked < 1:
            return True
        self._checked = now_wall
        v = lib.library.videos.get(self.vid)
        if v is None or v.get("deleted") or v.get("status") != "ready":
            return False
        self.entry = v
        if v.get("rev") != self.rev:
            pos = self.pos
            self._load(self.vid)
            self.pending_seek = pos
        return True

    def _quiet_now(self, now_wall):
        if now_wall - self._quiet_at >= 5:
            from boardcare import quiet_active
            import datetime as dt
            self._quiet_at = now_wall
            self._quiet = quiet_active(self.ctx.store.settings.get("quiet_hours") or {}, dt.datetime.now())
        return self._quiet

    def render(self, c, now):
        wall = time.time()
        if not self._refresh(wall):
            c.text_center(c.h // 2 - 4, "Video deleted", NORMAL)
            self.interval = 1.0
            return
        if self.anchor_t is None:
            self.anchor_t = now
            if self.title_start is None and self.title_on():
                self.title_start = now
        if self.pending_seek is not None:
            self.anchor_pos, self.anchor_t, self.pending_seek = self.pending_seek, now, None
        if self.title_start is None and self.title_on():
            self.title_start = now  # after Next/Previous
        pos, done = self._position(now)
        self.pos = pos
        gray = self.reader.frame(self.reader.index(pos))
        w, h = self.reader.w, self.reader.h
        if (c.w, c.h) != (w, h):
            gray = lib.scale_gray(gray, w, h, c.w, c.h, nearest=self.native)
            w, h = c.w, c.h
        c.px[:] = lib.dots_for(self.entry, gray, w, h, self.display())
        self._draw_title(c, now)
        iv = 1 / (self.fps * self.speed)
        if self.p["quiet"] == "slow" and self._quiet_now(wall):
            iv = max(iv, 1 / QUIET_FPS)
        if self.title_start is not None:
            iv = min(iv, 1 / TITLE_SPEED)
        self.interval = max(1 / 30, iv)
        if done:
            self.ended = True
            return False  # the last frame stays on the board

    def _draw_title(self, c, now):
        """The title scrolls across once, right to left, on a black band, like the now-playing line."""
        if not self.title_start:
            return
        text = (self.entry.get("title") or "").strip()
        if not text or not self.title_on():
            self.title_start = False
            return
        th = NORMAL.height
        tw = NORMAL.width(text)
        x = c.w - int((now - self.title_start) * TITLE_SPEED)
        if x + tw < 0:
            self.title_start = False  # done: not again on a loop, seek or resume
            return
        y = 1 if self.p["title_position"] == "top" else c.h - th - 1
        band0, band1 = max(0, y - 1), min(c.h, y + th + 1)
        c.px[band0 * c.w:band1 * c.w] = bytes((band1 - band0) * c.w)
        c.text(x, y, text, NORMAL)

    # ---- status ----

    def media_status(self):
        start, end = self.bounds()
        return {"id": self.vid, "name": self.entry.get("name"), "pos": int(self.pos * 2) / 2,
                "duration": round(self.duration, 2), "start": self.range[0], "end": self.range[1] or 0,
                "loop": self.loop, "speed": self.speed, "ended": self.ended}

    @property
    def status_tick(self):
        return (self.vid, int(self.pos * 2), self.ended)

    def describe(self):
        return f"{self.entry.get('name')} · {lib.fmt_time(self.pos)} / {lib.fmt_time(self.duration)}"
