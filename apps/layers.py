"""Layer several apps on top of each other, each keeping its own timing.

A layer spec looks like:
    {"app": "life", "params": {...}, "blend": "xor", "x": 0, "y": 0, "w": 84, "h": 42,
     "dx": 0, "dy": 0, "density": 1.0, "invert": false, "visible": true, "speed": 1.0}
Layers are listed bottom first. Compositing is done on whole-board bitmaps held as Python
ints (one byte per dot, each 0 or 1), so OR/AND/XOR of a full layer is a single operation.
"""

import itertools
import json
import logging

from canvas import Canvas

from .base import App, P, create, register

log = logging.getLogger("layers")

BLENDS = [
    ["normal", "Normal (covers what's below)"],
    ["add", "Add (white dots on top)"],
    ["halo", "Halo (on top with a black outline, stays readable)"],
    ["xor", "Invert (flips what's below)"],
    ["mask", "Mask (below shows only where this is white)"],
    ["cut", "Cut out (erases below where this is white)"],
]
_revs = itertools.count(1)  # bumped on every change to any running stack, so the web editor can tell
BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


class Layer:
    def __init__(self, parent, spec):
        self.parent = parent
        W, H = parent.w, parent.h
        self.cw = max(1, min(int(spec.get("w") or W), 4 * W))
        self.ch = max(1, min(int(spec.get("h") or H), 4 * H))
        ctx = type(parent.ctx)(self.cw, self.ch, parent.ctx.store)
        ctx.board = getattr(parent.ctx, "board", None)  # the same discs: the same settle time
        self.app = create(ctx, {"app": spec["app"], "params": spec.get("params") or {}})
        self.key = Layer.key_of(spec)
        self.canvas = Canvas(self.cw, self.ch)
        self.due = 0
        self.done = False
        self.configure(spec)

    @staticmethod
    def key_of(spec):
        return json.dumps([spec.get("app"), spec.get("params") or {}, spec.get("w"), spec.get("h")], sort_keys=True)

    def configure(self, spec):
        self.spec = spec
        self.blend = spec.get("blend") or "normal"
        self.x, self.y = int(spec.get("x") or 0), int(spec.get("y") or 0)
        self.dx, self.dy = float(spec.get("dx") or 0), float(spec.get("dy") or 0)
        self.density = max(0.0, min(1.0, float(spec.get("density", 1.0))))
        self.invert = bool(spec.get("invert"))
        self.visible = spec.get("visible", True) is not False
        self.speed = max(0.05, float(spec.get("speed") or 1.0))
        self.halo = max(0, min(5, int(spec.get("halo", 1) if spec.get("halo") is not None else 1)))
        self._box = None

    def interval(self):
        iv = self.app.interval / self.speed
        if self.dx or self.dy:
            iv = min(iv, 0.05)
        return iv

    def box(self):
        """Bitmap (as int) that is 1 inside this layer's rectangle on the board."""
        if self._box is None:
            W, H = self.parent.w, self.parent.h
            buf = bytearray(W * H)
            x0, x1 = max(0, self.x), min(W, self.x + self.cw)
            for ty in range(max(0, self.y), min(H, self.y + self.ch)):
                buf[ty * W + x0:ty * W + x1] = b"\x01" * max(0, x1 - x0)
            self._box = int.from_bytes(buf, "big")
        return self._box

    def bitmap(self, now):
        """This layer's dots placed on the board, as an int bitmap."""
        if now >= self.due and not self.done:
            c = Canvas(self.cw, self.ch)
            child_now = self.app.started + (now - self.app.started) * self.speed
            try:
                result = self.app.render(c, child_now)
            except Exception:
                log.exception("layer %s crashed", self.app.id)
                result = False
            self.canvas = c
            if result is False:
                self.done = True  # finished apps keep showing their last frame
            self.due = now + self.interval()
        W, H, cw, ch = self.parent.w, self.parent.h, self.cw, self.ch
        t = now - self.app.started
        ox, oy = int(t * self.dx) % cw, int(t * self.dy) % ch
        buf = bytearray(W * H)
        s0, s1 = max(0, -self.x), min(cw, W - self.x)
        if s0 < s1:
            px = self.canvas.px
            for ry in range(ch):
                ty = self.y + ry
                if not 0 <= ty < H:
                    continue
                sy = (ry - oy) % ch
                row = px[sy * cw:(sy + 1) * cw]
                if ox:
                    row = row[-ox:] + row[:-ox]
                buf[ty * W + self.x + s0:ty * W + self.x + s1] = row[s0:s1]
        return int.from_bytes(buf, "big")


class Layers(App):
    id = "layers"
    title = "Layers"
    category = "Layers"
    description = "Several apps stacked with blend modes (e.g. Game of Life inverting the weather). Build them on the Layers page."
    params = [
        P("composition", "composition", "", "Layer set"),
        P("layers", "hidden", None),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        specs = self.p.get("layers")
        if specs is None:
            comp = ctx.store.compositions.get(self.p["composition"] or "")
            if comp is None:
                raise ValueError(f"composition {self.p['composition']!r} not found")
            specs = comp["layers"]
            self.name = self.p["composition"]
        else:
            self.name = None
        n = self.w * self.h
        self.ones = int.from_bytes(b"\x01" * n, "big")
        # masks that drop the dot which wraps onto the next/previous row when shifting sideways
        first = bytearray(n)
        first[::self.w] = b"\x01" * self.h
        last = bytearray(n)
        last[self.w - 1::self.w] = b"\x01" * self.h
        self.not_first_col = self.ones ^ int.from_bytes(first, "big")
        self.not_last_col = self.ones ^ int.from_bytes(last, "big")
        self._density = {}
        self.layers = []
        self.update(specs)

    def update(self, specs):
        """Apply a new list of layer specs, reusing layers whose app/params/size didn't change."""
        old = {}
        for layer in self.layers:
            old.setdefault(layer.key, []).append(layer)
        new = []
        for spec in specs:
            if not spec.get("app"):
                continue
            reuse = old.get(Layer.key_of(spec))
            if reuse:
                layer = reuse.pop(0)
                layer.configure(spec)
            else:
                layer = Layer(self, spec)
            new.append(layer)
        for leftovers in old.values():
            for layer in leftovers:
                layer.app.stop()
        self.layers = new
        self.interactive = any(l.app.interactive for l in new)
        # a layer kept in time with the music (Percussion, the visualizer) keeps the whole stack's timing steady
        self.steady_timing = any(getattr(l.app, "steady_timing", False) for l in new)
        self.rev = next(_revs)

    def density_mask(self, d):
        level = round(d * 16)
        if level not in self._density:
            W, H = self.w, self.h
            buf = bytearray(1 if BAYER4[y % 4][x % 4] < level else 0 for y in range(H) for x in range(W))
            self._density[level] = int.from_bytes(buf, "big")
        return self._density[level]

    def dilate(self, bits, times):
        """Grow the white dots by `times` dots in every direction (8-neighbourhood)."""
        row = 8 * self.w
        for _ in range(times):
            h = bits | ((bits << 8) & self.not_last_col) | ((bits >> 8) & self.not_first_col)
            bits = h | ((h << row) & self.ones) | (h >> row)
        return bits

    def render(self, c, now):
        ones = self.ones
        acc = 0
        intervals = []
        for layer in self.layers:
            if not layer.visible:
                continue
            bits = layer.bitmap(now)
            if getattr(layer.app, "hidden", False):
                continue  # the app has nothing to show right now (e.g. a one-time song scroll that finished)
            req = getattr(layer.app, "request_transition", None)
            if req:  # pass a layer's whole-board transition request up to the runner
                layer.app.request_transition = None
                self.request_transition = req
            box = layer.box()
            intervals.append(layer.interval())
            if layer.invert:
                bits = (bits ^ ones) & box
            if layer.density < 1:
                dm = self.density_mask(layer.density)
                bits &= dm
                box &= dm
            if layer.blend == "add":
                acc |= bits
            elif layer.blend == "halo":
                acc = (acc & (self.dilate(bits, layer.halo) ^ ones)) | bits
            elif layer.blend == "xor":
                acc ^= bits
            elif layer.blend == "mask":
                acc &= bits | (box ^ ones)
            elif layer.blend == "cut":
                acc &= bits ^ ones
            else:  # normal
                acc = (acc & (box ^ ones)) | (bits & box)
        c.px[:] = acc.to_bytes(self.w * self.h, "big")
        due = [l.due - now for l in self.layers if l.visible and not l.done]
        self.interval = max(0.02, min(due + [min(intervals or [0.5]), 0.5]))

    def input(self, key, action="down", player=1):
        for layer in reversed(self.layers):
            if layer.visible and layer.app.interactive:
                layer.app.input(key, action, player)
                layer.due = 0  # redraw right away
                return

    def stop(self):
        for layer in self.layers:
            layer.app.stop()

    def describe(self):
        names = " + ".join(l.app.describe() if l.app.interactive else l.app.title for l in self.layers if l.visible)
        return f"{self.name}: {names}" if self.name else f"Layers: {names}"


register(Layers)
