"""Random effects for slideshow items: now and then an item gets inverted, glitched, melted,
run through Game of Life… for the whole item or a short burst. All cheap enough for a Pi."""

import math
import random

import fastpath
from canvas import Canvas, _BITS, _FLIP
from transitions import pixelate

_FF = bytes([0] + [255] * 255)  # dot -> 0x00/0xFF, for masks used as big integers
_EDGES = {}  # (w, h) -> masks of the dots that have a neighbour on each side


def _edges(w, h):
    """Big-integer masks (0xFF per dot, as apps/layers.py works) for the dots that are not in the first/last column
    or row, so shifting a picture by one dot can tell a missing neighbour from the board's edge."""
    m = _EDGES.get((w, h))
    if m is None:
        n = w * h
        not_first, not_last = bytearray(b"\x01" * n), bytearray(b"\x01" * n)
        not_first[0::w] = bytes(h)
        not_last[w - 1::w] = bytes(h)
        m = _EDGES[w, h] = {
            "left": int.from_bytes(not_first.translate(_FF), "big"), "right": int.from_bytes(not_last.translate(_FF), "big"),
            "up": int.from_bytes((bytes(w) + b"\x01" * (n - w)).translate(_FF), "big"),
            "down": int.from_bytes((b"\x01" * (n - w) + bytes(w)).translate(_FF), "big"),
            "all": (1 << (8 * n)) - 1,
        }
    return m

EFFECTS = {
    "invert": "Invert",
    "flash": "Flash",
    "glitch": "Glitch",
    "mirror": "Mirror",
    "kaleidoscope": "Kaleidoscope",
    "wave": "Wave",
    "scanlines": "Scanlines",
    "pixelate": "Pixelate",
    "outline": "Outline",
    "static": "Static",
    "life": "Game of Life",
    "melt": "Melt",
}
ANIMATED = {"flash", "glitch", "wave", "scanlines", "static", "life", "melt"}


def roll(settings, rng, now, item_seconds, hold=0.0):
    """Maybe pick an effect for an item that's starting. Returns an Effect or None."""
    chance = float(settings.get("fx_chance") or 0) / 100
    allowed = [e for e in (settings.get("fx_effects") or list(EFFECTS)) if e in EFFECTS]
    if not allowed or rng.random() >= chance:
        return None
    kind = rng.choice(allowed)
    if settings.get("fx_mode", "burst") == "whole":
        start, end = now, None  # until the item changes
    else:
        length = max(0.3, float(settings.get("fx_seconds") or 3))
        lead = max(0.0, item_seconds - length) if item_seconds else 0.0
        start = now + rng.uniform(0, lead) if lead else now
        end = start + length
    return Effect(kind, start, end, float(settings.get("fx_strength") or 50) / 100, rng, hold)


class Effect:
    def __init__(self, kind, start, end, strength, rng, hold=0.0):
        self.kind, self.start, self.end = kind, start, end
        self.strength = max(0.05, min(1.0, strength))
        self.rng = rng or random.Random()
        self.hold = hold  # how long the discs take to show a whole new picture (Board.settle_time()): Flash's floor
        self.state = {}

    def active(self, now):
        return now >= self.start and (self.end is None or now < self.end)

    @property
    def animated(self):
        return self.kind in ANIMATED

    def apply(self, frame, now):
        """Return a processed copy of `frame` (a Canvas)."""
        if not self.active(now):
            self.state.pop("life", None)
            return frame
        c = frame.copy()
        getattr(self, "_" + self.kind)(c, now - self.start)
        return c

    # ---- effects (operate in place on c) --------------------------------------------

    def _invert(self, c, t):
        c.invert()

    def _flash(self, c, t):
        rate = 3 + 6 * self.strength  # toggles a second...
        if self.hold:
            rate = min(rate, 1 / self.hold)  # ...but each picture stays until the discs have shown it
        if int(t * rate) % 2 == 0:
            c.invert()

    def _glitch(self, c, t):
        w, h, px, rng = c.w, c.h, c.px, self.rng
        s = self.strength
        for y in range(h):
            if rng.random() < 0.25 * s:
                shift = rng.randint(-int(4 + 16 * s), int(4 + 16 * s))
                row = px[y * w:(y + 1) * w]
                px[y * w:(y + 1) * w] = row[-shift:] + row[:-shift] if shift else row
            if rng.random() < 0.06 * s:
                px[y * w:(y + 1) * w] = px[y * w:(y + 1) * w].translate(_FLIP)

    def _mirror(self, c, t):
        w = c.w
        for y in range(c.h):
            c.px[y * w:(y + 1) * w] = c.px[y * w:(y + 1) * w][::-1]

    def _kaleidoscope(self, c, t):
        w, h, px = c.w, c.h, c.px
        for y in range(h):
            row = px[y * w:(y + 1) * w]
            half = row[:w // 2]
            px[y * w:(y + 1) * w] = half + half[::-1] + row[w // 2 * 2:]
        for y in range(h // 2, h):
            src = h - 1 - y
            px[y * w:(y + 1) * w] = px[src * w:(src + 1) * w]

    def _wave(self, c, t):
        w, px = c.w, c.px
        amp = 1 + 5 * self.strength
        src = bytes(px)
        for y in range(c.h):
            shift = round(amp * math.sin(y / 4 + t * 6))
            row = src[y * w:(y + 1) * w]
            px[y * w:(y + 1) * w] = (row[-shift:] + row[:-shift]) if shift else row

    def _scanlines(self, c, t):
        w = c.w
        gap = 2 if self.strength > 0.6 else 3
        off = int(t * 12)
        for y in range(c.h):
            if (y + off) % gap == 0:
                c.px[y * w:(y + 1) * w] = bytes(w)

    def _pixelate(self, c, t):
        c.px[:] = pixelate(c.px, c.w, c.h, 2 + round(4 * self.strength))

    def _outline(self, c, t):
        """Keep the white dots that have a black neighbour on the board (the board's edge doesn't count): the
        picture as one big integer, shifted a dot each way, as apps/layers.py composites."""
        w, h = c.w, c.h
        m = _edges(w, h)
        s = int.from_bytes(c.px.translate(_FF), "big")
        left, right = s >> 8, (s << 8) & m["all"]  # the dot to the left of / right of each dot
        up, down = s >> (8 * w), (s << (8 * w)) & m["all"]
        missing = (m["left"] & ~left) | (m["right"] & ~right) | (m["up"] & ~up) | (m["down"] & ~down)
        c.px[:] = (s & missing).to_bytes(w * h, "big").translate(_BITS)

    def _static(self, c, t):
        n = int(len(c.px) * 0.12 * self.strength)
        for _ in range(n):
            i = self.rng.randrange(len(c.px))
            c.px[i] ^= 1

    def _life(self, c, t):
        """Run Game of Life starting from the picture; generations at ~10/s."""
        w, h = c.w, c.h
        st = self.state.get("life")
        if st is None:
            st = self.state["life"] = {"cells": bytearray(c.px), "gen": 0}
        target = int(t * 10)
        cells = st["cells"]
        while st["gen"] < target:
            cells = st["cells"] = fastpath.life_step(cells, w, h)
            st["gen"] += 1
        c.px[:] = cells

    def _melt(self, c, t):
        w, h, px = c.w, c.h, c.px
        speeds = self.state.setdefault("speeds", [self.rng.uniform(0.5, 1.5) for _ in range(w)])
        src = bytes(px)
        rate = 4 + 14 * self.strength
        for x in range(w):
            drop = min(h, int((t * rate * speeds[x]) ** 1.3))
            if drop:
                px[x::w] = bytes(drop) + src[x:(h - drop) * w:w]  # the column, shifted down, in one slice


# ---- reacting to music ---------------------------------------------------------------------
# Each signal drives one effect with strength = signal above its threshold x amount. Life and Melt are left
# out: they build up over time from where they started, so a strength that changes every frame breaks them.

SIGNALS = ["bass", "lowmid", "mid", "high", "beat", "level"]
REACT_ORDER = ["pixelate", "mirror", "kaleidoscope", "wave", "glitch", "outline", "scanlines", "static", "flash", "invert"]
SWITCHED = {"invert", "mirror", "kaleidoscope", "outline"}  # no strength: on while the strength is past half
REACT_DEFAULTS = [
    {"signal": "bass", "effect": "glitch", "amount": 80, "threshold": 35},
    {"signal": "lowmid", "effect": "none", "amount": 50, "threshold": 35},
    {"signal": "mid", "effect": "wave", "amount": 50, "threshold": 50},
    {"signal": "high", "effect": "static", "amount": 40, "threshold": 50},
    {"signal": "beat", "effect": "invert", "amount": 100, "threshold": 0},
    {"signal": "level", "effect": "scale", "amount": 50, "threshold": 0},
]


def clean_react_map(rows):
    """One row per signal, in SIGNALS order, with known effects and amounts/thresholds clamped to 0..100."""
    given = {r.get("signal"): r for r in rows or [] if isinstance(r, dict)}
    out = []
    for d in REACT_DEFAULTS:
        r = given.get(d["signal"], d)
        eff = r.get("effect", d["effect"])
        if eff not in REACT_ORDER and eff != "none" and not (eff == "scale" and d["signal"] == "level"):
            eff = "none"
        num = lambda k: max(0, min(100, int(float(r.get(k, d[k]) or 0))))
        out.append({"signal": d["signal"], "effect": eff, "amount": num("amount"), "threshold": min(90, num("threshold"))})
    return out


def react_strengths(rows, reading):
    """{effect: strength 0..1} for this music reading. Several signals on one effect: the strongest wins;
    Loudness set to "scale" turns everything down in quiet parts."""
    out, scale = {}, 1.0
    for r in rows:
        sig = reading["pulse"] if r["signal"] == "beat" else reading.get(r["signal"], 0.0)
        thr = r["threshold"] / 100
        v = max(0.0, (sig - thr) / (1 - thr)) * r["amount"] / 100
        if r["effect"] == "scale":
            scale = 1 - r["amount"] / 100 + r["amount"] / 100 * sig
        elif r["effect"] in REACT_ORDER:
            out[r["effect"]] = max(out.get(r["effect"], 0.0), v)
    return {k: v * scale for k, v in out.items()}


class Reactive:
    """Applies the music-driven effects to a frame, in REACT_ORDER.

    A switched effect (invert on a beat) changes every dot of the board, which the discs take a while to show: the
    serial data for the busiest bus and then the panels' sweep, well over 100 ms on a three-column board. A beat
    pulse is past half for about 100 ms, and meanwhile the GIF and the other effects keep changing the picture, so
    the board was sent the inverted picture, two more inverted pictures and the plain one before the first had
    landed: a patchwork of panels in different states instead of a flash. So after a switch the picture is held,
    exactly as sent, for `hold` seconds (Board.settle_time()): the board gets the inverted picture, shows it whole,
    gets the plain one, shows that, and only then the GIF carries on."""

    def __init__(self, rng=None, hold=0.0):
        self.rng = rng or random.Random()
        self.hold = hold
        self.fx = {k: Effect(k, 0.0, None, 0.5, self.rng) for k in REACT_ORDER}
        self.on = {k: False for k in SWITCHED}
        self.held = None  # (until, picture) while a switch settles on the discs

    def reset(self):
        """Reacting stopped: the next picture isn't a switch to hold."""
        self.on = {k: False for k in SWITCHED}
        self.held = None

    def apply(self, frame, now, strengths):
        if self.held is not None:
            until, pic = self.held
            if now < until:
                return pic
            self.held = None
        switched = False
        for kind in REACT_ORDER:
            s = strengths.get(kind, 0.0)
            if kind in SWITCHED:
                on = s >= 0.5
                if on != self.on[kind]:
                    self.on[kind] = on
                    switched = True
                if not on:
                    continue
            elif s < 0.08:
                continue
            e = self.fx[kind]
            e.strength = min(1.0, s)
            frame = e.apply(frame, now)
        if switched and self.hold > 0:
            frame = frame.copy()
            self.held = (now + self.hold, frame)
        return frame


def describe_settings():
    """For the UI: the effect choices."""
    return [{"id": k, "label": v} for k, v in EFFECTS.items()]
