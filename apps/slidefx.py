"""Random effects for slideshow items: now and then an item gets inverted, glitched, melted,
run through Game of Life… for the whole item or a short burst. All cheap enough for a Pi."""

import math
import random

from canvas import Canvas

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


def roll(settings, rng, now, item_seconds):
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
    return Effect(kind, start, end, float(settings.get("fx_strength") or 50) / 100, rng)


class Effect:
    def __init__(self, kind, start, end, strength, rng):
        self.kind, self.start, self.end = kind, start, end
        self.strength = max(0.05, min(1.0, strength))
        self.rng = rng or random.Random()
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
        if int(t * (3 + 6 * self.strength)) % 2 == 0:
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
                px[y * w:(y + 1) * w] = bytearray(1 - v for v in px[y * w:(y + 1) * w])

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
        w, h, px = c.w, c.h, c.px
        b = 2 + round(4 * self.strength)
        src = bytes(px)
        for by in range(0, h, b):
            for bx in range(0, w, b):
                cells = [src[y * w + x] for y in range(by, min(by + b, h)) for x in range(bx, min(bx + b, w))]
                v = 1 if sum(cells) * 2 >= len(cells) else 0
                for y in range(by, min(by + b, h)):
                    px[y * w + bx:y * w + min(bx + b, w)] = bytes([v]) * (min(bx + b, w) - bx)

    def _outline(self, c, t):
        w, h = c.w, c.h
        src = bytes(c.px)
        out = bytearray(w * h)
        for y in range(h):
            for x in range(w):
                v = src[y * w + x]
                if v and ((x and not src[y * w + x - 1]) or (x < w - 1 and not src[y * w + x + 1])
                          or (y and not src[(y - 1) * w + x]) or (y < h - 1 and not src[(y + 1) * w + x])):
                    out[y * w + x] = 1
        c.px[:] = out

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
            nxt = bytearray(w * h)
            for y in range(h):
                up, dn, row = ((y - 1) % h) * w, ((y + 1) % h) * w, y * w
                for x in range(w):
                    l, r = (x - 1) % w, (x + 1) % w
                    k = (cells[up + l] + cells[up + x] + cells[up + r] + cells[row + l] + cells[row + r]
                         + cells[dn + l] + cells[dn + x] + cells[dn + r])
                    nxt[row + x] = 1 if k == 3 or (k == 2 and cells[row + x]) else 0
            cells = st["cells"] = nxt
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
                for y in range(h - 1, -1, -1):
                    sy = y - drop
                    px[y * w + x] = src[sy * w + x] if sy >= 0 else 0


def describe_settings():
    """For the UI: the effect choices."""
    return [{"id": k, "label": v} for k, v in EFFECTS.items()]
