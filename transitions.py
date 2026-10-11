"""Transitions between two pictures.

Most transitions are "reveal maps": every dot gets a time in 0..1 at which it switches from the
old picture to the new one (a wipe is just reveal time = x / width). The rest move or distort
the pictures (slides, pushes, zoom, melt...).
"""

import collections
import math
import random
import time
from array import array

from PIL import Image

import fastpath
from canvas import Canvas, _BITS, _FLIP

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
_FF = bytes([0] + [255] * 255)  # dot -> 0x00/0xFF, for masks used as big integers
_NONZERO = bytes([0] + [1] * 255)


def _ones(n):
    """Big-integer mask with 0xFF in every byte (whole-picture bitwise work, as apps/layers.py does)."""
    return (1 << (8 * n)) - 1


def smoothstep(p):
    return p * p * (3 - 2 * p)


# ---- reveal-map transitions -----------------------------------------------------
# Each function returns the reveal time (0..1) for the dot at x, y; `rnd` is a per-transition
# random.Random so things like dissolve are different every time.

def _norm(values):
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return [(v - lo) / span for v in values]


def _reveal(fn):
    def build(w, h, rnd):
        return _norm([fn(x, y, w, h, rnd) for y in range(h) for x in range(w)])
    return build


def _spiral(w, h, rnd):
    order, seen = [], set()
    x0, y0, x1, y1 = 0, 0, w - 1, h - 1
    while x0 <= x1 and y0 <= y1:
        for x in range(x0, x1 + 1):
            order.append((x, y0))
        for y in range(y0 + 1, y1 + 1):
            order.append((x1, y))
        if y0 < y1:
            for x in range(x1 - 1, x0 - 1, -1):
                order.append((x, y1))
        if x0 < x1:
            for y in range(y1 - 1, y0, -1):
                order.append((x0, y))
        x0, y0, x1, y1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
    times = [0.0] * (w * h)
    for i, (x, y) in enumerate(order):
        if (x, y) not in seen:
            seen.add((x, y))
            times[y * w + x] = i / len(order)
    return times


def _tiles(w, h, rnd, size=7):
    blocks = {}
    return _norm([blocks.setdefault((x // size, y // size), rnd.random()) for y in range(h) for x in range(w)])


def _drip(w, h, rnd):
    delays = [rnd.random() * 0.6 for _ in range(w)]
    return _norm([delays[x] + (y / h) * 0.4 for y in range(h) for x in range(w)])


def _noise_wipe(w, h, rnd):
    jitter = [rnd.uniform(-0.12, 0.12) for _ in range(h)]
    return _norm([x / w + jitter[y] for y in range(h) for x in range(w)])


REVEALS = {
    "dissolve": (lambda w, h, rnd: [rnd.random() for _ in range(w * h)], False),
    "fade": (_reveal(lambda x, y, w, h, r: BAYER4[y % 4][x % 4] + r.random() * 0.01), False),
    "wipe-right": (_reveal(lambda x, y, w, h, r: x), True),
    "wipe-left": (_reveal(lambda x, y, w, h, r: w - x), True),
    "wipe-down": (_reveal(lambda x, y, w, h, r: y), True),
    "wipe-up": (_reveal(lambda x, y, w, h, r: h - y), True),
    "wipe-ragged": (_noise_wipe, True),
    "diagonal": (_reveal(lambda x, y, w, h, r: x + y * 2), True),
    "iris-open": (_reveal(lambda x, y, w, h, r: math.hypot((x - w / 2) / 2, y - h / 2)), True),
    "iris-close": (_reveal(lambda x, y, w, h, r: -math.hypot((x - w / 2) / 2, y - h / 2)), True),
    "diamond": (_reveal(lambda x, y, w, h, r: abs(x - w / 2) / 2 + abs(y - h / 2)), True),
    "clock": (_reveal(lambda x, y, w, h, r: (math.atan2(x - w / 2, -(y - h / 2)) + 2 * math.pi) % (2 * math.pi)), True),
    "blinds": (_reveal(lambda x, y, w, h, r: y % 7 + x / w * 0.5), False),
    "blinds-vertical": (_reveal(lambda x, y, w, h, r: x % 7 + y / h * 0.5), False),
    "checker": (_reveal(lambda x, y, w, h, r: ((x // 7 + y // 7) % 2) * 2 + ((x % 7) + (y % 7)) / 14), False),
    "split-horizontal": (_reveal(lambda x, y, w, h, r: abs(x - (w - 1) / 2)), True),
    "split-vertical": (_reveal(lambda x, y, w, h, r: abs(y - (h - 1) / 2)), True),
    "interlace": (_reveal(lambda x, y, w, h, r: (y % 2) * h + y), False),
    "tiles": (_tiles, False),
    "drip": (_drip, False),
    "spiral": (_spiral, False),
}


# ---- moving / distorting transitions ------------------------------------------------

def _slide(direction, push):
    dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[direction]

    def render(t, old, new, p):
        w, h = old.w, old.h
        out = Canvas(w, h)
        # the new picture enters from the opposite side; with push the old one moves out with it
        ox, oy = round(-dx * w * (1 - p)), round(-dy * h * (1 - p))
        px_, py_ = round(dx * w * p), round(dy * h * p)
        if push:
            out.blit(old, px_, py_, transparent=False)  # the old picture on its way out (black beyond its edge)
        else:
            out.px[:] = old.px
        out.blit(new, ox, oy, transparent=False)  # row-slice copies, no per-dot work
        return out
    return render


def _zoom(grow):
    def render(t, old, new, p):
        w, h = old.w, old.h
        out = Canvas(w, h)
        front, back, s = (new, old, p) if grow else (old, new, 1 - p)
        if s <= 0.01:
            return back.copy()
        # Pillow scales the front picture about the middle (nearest neighbour, in C); where it doesn't reach,
        # the back picture shows through
        img = _turned(front, 0.0, s)
        inside = _scaled_mask(w, h, s)
        out.px = bytearray(Image.composite(img, Image.frombytes("L", (w, h), bytes(back.px)), inside).tobytes())
        return out
    return render


def _scaled_mask(w, h, s):
    """0xFF where a picture scaled by s about the middle lands on the board."""
    cx, cy = w / 2, h / 2
    a = 1 / s
    return Image.new("L", (w, h), 255).transform((w, h), Image.AFFINE, (a, 0, cx - a * cx, 0, a, cy - a * cy), Image.NEAREST)


def _flip(t, old, new, p):
    w, h = old.w, old.h
    out = Canvas(w, h)
    src, s = (old, 1 - 2 * p) if p < 0.5 else (new, 2 * p - 1)
    if s <= 0.02:
        out.rect(w // 2, 0, 1, h, fill=True)  # the card edge-on
        return out
    cx = (w - 1) / 2
    for x in range(w):
        fx = round((x - cx) / s + cx)
        if 0 <= fx < w:
            out.px[x::w] = src.px[fx::w]  # the whole column in one slice
    return out


def _melt(t, old, new, p):
    """The old picture slides off the bottom column by column, at different speeds."""
    w, h = old.w, old.h
    if "speeds" not in t.state:
        t.state["speeds"] = [t.rnd.uniform(1.0, 2.2) for _ in range(w)]
    out = new.copy()
    for x in range(w):
        drop = round(h * min(1.0, p * t.state["speeds"][x]) ** 2)
        if drop < h:
            out.px[drop * w + x::w] = old.px[x:(h - drop) * w:w]  # the column, shifted down, in one slice
    return out


def _pixelate(t, old, new, p):
    w, h = old.w, old.h
    src = old if p < 0.5 else new
    block = 1 + round((1 - abs(2 * p - 1)) * 9)
    if block <= 1:
        return src.copy()
    out = Canvas(w, h)
    out.px = bytearray(pixelate(bytes(src.px), w, h, block))
    return out


def pixelate(px, w, h, block):
    """px (bytes of 0/1) in blocks of `block` dots, each block the majority of its dots: Pillow's reduce() averages
    the blocks (partial ones at the right/bottom edge over the dots they have) and the result is blown back up."""
    img = Image.frombytes("L", (w, h), bytes(px).translate(_FF)).reduce(block)
    bw, bh = img.size
    return img.point(_HALF).resize((bw * block, bh * block), Image.NEAREST).crop((0, 0, w, h)).tobytes()


_HALF = [1 if v >= 127 else 0 for v in range(256)]  # half or more white: no block of up to 100 dots averages in [127, 127.5)


def _flash(t, old, new, p):
    step = int(p * 6)
    if step >= 5:
        return new.copy()
    out = (old if step < 3 else new).copy()
    if step % 2 == 1 or step == 4:
        out.invert()
    return out


def _glitch(t, old, new, p):
    w, h = old.w, old.h
    src = old if p < 0.5 else new
    strength = math.sin(p * math.pi)
    out = Canvas(w, h)
    for y in range(h):
        shift = round(t.rnd.gauss(0, 1) * 12 * strength) if t.rnd.random() < strength * 0.7 else 0
        row = src.px[y * w:(y + 1) * w]
        row = row[-shift:] + row[:-shift] if shift else row
        if t.rnd.random() < strength * 0.15:
            row = row.translate(_FLIP)
        out.px[y * w:(y + 1) * w] = row
    return out


def _ghost(t, old, new, p):
    if p < 1 / 3:
        return old.copy()
    if p > 2 / 3:
        return new.copy()
    out = old.copy()
    n = len(old.px)
    out.px = bytearray((int.from_bytes(old.px, "big") ^ int.from_bytes(new.px, "big")).to_bytes(n, "big"))
    return out


# ---- generative transitions: build the change out of the pictures' own dots ----------

def _hilbert(x, y, n=128):
    """Position along a Hilbert curve, so pairing dots by it keeps neighbours together."""
    d, s = 0, n // 2
    while s > 0:
        rx, ry = (x & s) > 0, (y & s) > 0
        d += s * s * ((3 * rx) ^ ry)
        if not ry:
            if rx:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s //= 2
    return d


def _whites(c):
    w = c.w
    return [(i % w, i // w) for i, v in enumerate(c.px) if v]


def _pair_dots(t, old, new):
    """Match every white dot of the old picture with one of the new picture (by Hilbert order)."""
    a = sorted(_whites(old), key=lambda p: _hilbert(*p))
    b = sorted(_whites(new), key=lambda p: _hilbert(*p))
    if not a and not b:
        return []
    n = max(len(a), len(b))
    # stretch the shorter list over the longer one (dots split or merge)
    if not a:
        a = [(old.w // 2, old.h // 2)]
    if not b:
        b = [(new.w // 2, new.h // 2)]
    a = [a[i * len(a) // n] for i in range(n)]
    b = [b[i * len(b) // n] for i in range(n)]
    return [(ax, ay, bx, by, t.rnd.random() * 0.35) for (ax, ay), (bx, by) in zip(a, b)]


def _migrate(scatter):
    def render(t, old, new, p):
        if p >= 1:
            return new.copy()
        if "pairs" not in t.state:
            t.state["pairs"] = _pair_dots(t, old, new)
            if scatter:
                t.state["mid"] = [(t.rnd.uniform(0, old.w - 1), t.rnd.uniform(0, old.h - 1)) for _ in t.state["pairs"]]
        out = Canvas(old.w, old.h)
        for k, (ax, ay, bx, by, delay) in enumerate(t.state["pairs"]):
            q = smoothstep(min(1.0, max(0.0, (p - delay) / 0.65)))  # staggered start for an organic swarm
            if scatter:
                mx, my = t.state["mid"][k]
                if q < 0.5:
                    f = smoothstep(q * 2)
                    x, y = ax + (mx - ax) * f, ay + (my - ay) * f
                else:
                    f = smoothstep((q - 0.5) * 2)
                    x, y = mx + (bx - mx) * f, my + (by - my) * f
            else:
                x, y = ax + (bx - ax) * q, ay + (by - ay) * q
            out.set(round(x), round(y))
        return out
    return render


def _distance_field(c):
    """Signed distance (chamfer 3-4) to the white/black boundary: positive inside white areas. An array('i') in
    chamfer units; only the sign of a blend of two fields matters, so the scale is free (fastpath.distance_field)."""
    return fastpath.distance_field(c.px, c.w, c.h)


_df_cache = collections.OrderedDict()  # picture bytes -> distance field (GIF frames repeat every loop)


def _cached_df(c):
    key = bytes(c.px)
    df = _df_cache.get(key)
    if df is None:
        df = _df_cache[key] = _distance_field(c)
        if len(_df_cache) > 400:
            _df_cache.popitem(last=False)
    else:
        _df_cache.move_to_end(key)
    return df


def _morph(t, old, new, p):
    """Shape tween: blend the two pictures' distance fields, so white shapes flow into each other.
    Both pictures may be animating (e.g. one GIF morphing into another); it follows them live."""
    if p >= 1:
        return new.copy()
    a, b = _cached_df(old), _cached_df(new)
    out = Canvas(old.w, old.h)
    out.px = bytearray(fastpath.morph_blend(a, b, p))
    return out


def _life(t, old, new, p):
    """Game of Life runs on the old picture while cells increasingly settle into the new one."""
    if p >= 1:
        return new.copy()
    w, h = old.w, old.h
    st = t.state
    if "cells" not in st:
        st["cells"], st["steps"] = bytearray(old.px), 0
    target_steps = int(p * 14 * t.duration)  # about 14 generations a second
    cells = st["cells"]
    n = w * h
    while st["steps"] < target_steps:
        st["steps"] += 1
        q = st["steps"] / max(1, 14 * t.duration)
        nxt = fastpath.life_step(cells, w, h)
        # settle into the new picture more and more: each dot independently with probability q*q, decided by one
        # random byte per dot (a 256-entry table turns the bytes into a mask; the mask picks new over nxt)
        thr = min(256, int(q * q * 256))
        lut = bytes([255] * thr + [0] * (256 - thr))
        settle = int.from_bytes(t.rnd.randbytes(n).translate(lut), "big")
        mixed = (int.from_bytes(nxt, "big") & ~settle) | (int.from_bytes(new.px, "big") & settle)
        cells = st["cells"] = bytearray(mixed.to_bytes(n, "big"))
    out = Canvas(w, h)
    out.px = bytearray(cells)
    return out


def _turned(c, angle, scale):
    """c turned by angle (radians) and scaled about the middle, as a Pillow image (Pillow does the work in C)."""
    w, h = c.w, c.h
    cx, cy = w / 2, h / 2
    a, b = math.cos(angle) / scale, math.sin(angle) / scale
    img = Image.frombytes("L", (w, h), bytes(c.px))
    return img.transform((w, h), Image.AFFINE, (a, b, cx - a * cx - b * cy, -b, a, cy + b * cx - a * cy),
                         Image.NEAREST)


def _flow(turn):
    """Both pictures keep moving: the old one turns and grows past you while the new one turns in from
    small, mixed by a dither pattern that opens from the middle. Good between two animations."""
    def render(t, old, new, p):
        if p >= 1:
            return new.copy()
        w, h = old.w, old.h
        if "order" not in t.state:
            cx, cy = (w - 1) / 2, (h - 1) / 2
            far = math.hypot(cx, cy)
            order = [0.6 * math.hypot(x - cx, y - cy) / far + 0.4 * (BAYER4[y % 4][x % 4] + 0.5) / 16
                     for y in range(h) for x in range(w)]
            t.state["order"] = bytes(min(255, int(v * 256)) for v in order)  # in 256 steps: one byte per dot
        a = _turned(old, turn * p, 1 + 1.5 * p * p)
        b = _turned(new, -turn * (1 - p), 0.35 + 0.65 * p)
        thr = min(256, int(p * 256))
        mask = Image.frombytes("L", (w, h), t.state["order"].translate(bytes([255] * thr + [0] * (256 - thr))))
        out = Canvas(w, h)
        out.px = bytearray(Image.composite(b, a, mask).tobytes())
        return out
    return render


MOVES = {
    "slide-left": _slide("left", False), "slide-right": _slide("right", False),
    "slide-up": _slide("up", False), "slide-down": _slide("down", False),
    "push-left": _slide("left", True), "push-right": _slide("right", True),
    "push-up": _slide("up", True), "push-down": _slide("down", True),
    "zoom-in": _zoom(True), "zoom-out": _zoom(False), "flip": _flip, "melt": _melt,
    "pixelate": _pixelate, "flash": _flash, "glitch": _glitch, "ghost": _ghost,
    "swirl": _flow(1.0), "zoom-through": _flow(0.0), "morph": _morph, "migrate": _migrate(False), "scatter": _migrate(True), "life": _life,
}

ALIASES = {"wipe": "wipe-right", "slide": "push-up"}
NAMES = ["none"] + list(REVEALS) + list(MOVES) + ["random"]


class Transition:
    def __init__(self, kind, old, w, h, duration=1.0):
        kind = ALIASES.get(kind, kind)
        if kind == "random":
            kind = random.choice([n for n in NAMES if n not in ("none", "random")])
        if kind not in REVEALS and kind not in MOVES:
            raise ValueError(f"unknown transition {kind!r}")
        self.kind, self.old, self.w, self.h = kind, old, w, h
        self.start, self.duration = time.time(), max(0.1, float(duration))
        self.rnd = random.Random()
        self.state = {}
        if kind in REVEALS:
            build, self.edge = REVEALS[kind]
            times = build(w, h, self.rnd)
            self.order = sorted(range(w * h), key=times.__getitem__)
            self.times = times
            self._reset_reveal()

    def _reset_reveal(self):
        """The reveal mask grows as p does (p only ever rises within a transition), so each frame only marks the
        dots whose time has just come: the whole transition does one pass over the dots, not one per frame."""
        n = self.w * self.h
        self._pos = self._edge_pos = 0  # how far along `order` the mask and the leading edge have got
        self._p = -1.0
        self._mask, self._edge_mask = bytearray(n), bytearray(n)
        self._all = _ones(n)
        self._one = int.from_bytes(b"\x01" * n, "big")

    def done(self, now):
        return now - self.start >= self.duration

    def mix(self, new, now, old=None):
        """Blend toward `new` at time `now`. Pass `old` to use a live (still animating) old picture."""
        if old is not None:
            self.old = old
        p = smoothstep(min(1.0, max(0.0, (now - self.start) / self.duration)))
        if self.kind in MOVES:
            return MOVES[self.kind](self, self.old, new, p)
        if p < self._p:
            self._reset_reveal()  # the clock went backwards
        self._p = p
        order, times, mask, n = self.order, self.times, self._mask, len(self.order)
        i = self._pos
        if p >= 1:
            while i < n:
                mask[order[i]] = 1
                i += 1
        else:
            while i < n and times[order[i]] < p:
                mask[order[i]] = 1
                i += 1
        self._pos = i
        m = int.from_bytes(mask.translate(_FF), "big")
        out = (int.from_bytes(self.old.px, "big") & ~m & self._all) | (int.from_bytes(new.px, "big") & m)
        if self.edge and 0 < p < 1:
            # a bright leading edge, which looks great as the discs flip: dots about to switch, not switched yet
            edge, j = self._edge_mask, max(self._edge_pos, i)
            limit = p + 0.04
            while j < n and times[order[j]] <= limit:
                edge[order[j]] = 1
                j += 1
            self._edge_pos = j
            out |= int.from_bytes(edge.translate(_FF), "big") & ~m & self._one
        c = Canvas(self.w, self.h)
        c.px = bytearray(out.to_bytes(n, "big"))
        return c
