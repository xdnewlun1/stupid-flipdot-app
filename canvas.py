"""A 1-bit drawing surface with text, shapes and big seven-segment digits."""

import math

from fonts import FONTS, NORMAL


class Canvas:
    def __init__(self, w, h, fill=0):
        self.w, self.h = w, h
        self.px = bytearray([1 if fill else 0]) * (w * h)

    @classmethod
    def from_rows(cls, rows):
        c = cls(len(rows[0]), len(rows))
        c.px = bytearray(1 if ch == "1" else 0 for r in rows for ch in r)
        return c

    def rows(self):
        w = self.w
        return ["".join("1" if v else "0" for v in self.px[y * w:(y + 1) * w]) for y in range(self.h)]

    def copy(self):
        c = Canvas(self.w, self.h)
        c.px = bytearray(self.px)
        return c

    # ---- pixels ------------------------------------------------------------

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y * self.w + x]
        return 0

    def set(self, x, y, v=1):
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = 1 if v else 0

    def clear(self, v=0):
        self.px[:] = bytearray([1 if v else 0]) * (self.w * self.h)

    def invert(self):
        self.px = bytearray(1 - v for v in self.px)

    def blit(self, other, x0, y0, transparent=True):
        for y in range(other.h):
            for x in range(other.w):
                v = other.px[y * other.w + x]
                if v or not transparent:
                    self.set(x0 + x, y0 + y, v)

    # ---- shapes ------------------------------------------------------------

    def line(self, x0, y0, x1, y1, v=1):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.set(x0, y0, v)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x, y, w, h, v=1, fill=False):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.set(xx, yy, v)
        else:
            self.line(x, y, x + w - 1, y, v)
            self.line(x, y + h - 1, x + w - 1, y + h - 1, v)
            self.line(x, y, x, y + h - 1, v)
            self.line(x + w - 1, y, x + w - 1, y + h - 1, v)

    def circle(self, cx, cy, r, v=1, fill=False):
        for y in range(int(cy - r - 1), int(cy + r + 2)):
            for x in range(int(cx - r - 1), int(cx + r + 2)):
                d = math.hypot(x - cx, y - cy)
                if (d <= r + 0.5) if fill else (r - 0.5 <= d <= r + 0.5):
                    self.set(x, y, v)

    # ---- text --------------------------------------------------------------

    def text(self, x, y, s, font=NORMAL, v=1, spacing=None, scale=1, boxes=None, slot=0):
        """Draw text with its top-left at (x, y). Returns the width drawn.
        If `boxes` is a list, appends (slot, x, y, w, h, char) for each character (for CharFx)."""
        if isinstance(font, str):
            font = FONTS[font]
        if spacing is None:
            spacing = font.spacing
        cx = x
        for i, ch in enumerate(s):
            g = font.glyph(ch)
            gw = len(g[0])
            if boxes is not None:
                boxes.append(((slot, i), cx, y, gw * scale, font.height * scale, ch))
            for gy, row in enumerate(g):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        for sy in range(scale):
                            for sx in range(scale):
                                self.set(cx + gx * scale + sx, y + gy * scale + sy, v)
            cx += (gw + (spacing if i < len(s) - 1 else 0)) * scale
        return cx - x

    def text_center(self, y, s, font=NORMAL, v=1, scale=1, x0=0, w=None, boxes=None, slot=0):
        if isinstance(font, str):
            font = FONTS[font]
        w = self.w if w is None else w
        tw = font.width(s) * scale
        self.text(x0 + (w - tw) // 2, y, s, font, v, scale=scale, boxes=boxes, slot=slot)
        return tw

    # ---- seven-segment digits (for big clocks/numbers) ----------------------

    SEGMENTS = {
        "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
        "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g", " ": "",
    }

    def seg_digit(self, x, y, ch, w=14, h=27, t=3, v=1):
        mid = y + (h - t) // 2
        segs = {
            "a": (x, y, w, t), "d": (x, y + h - t, w, t), "g": (x, mid, w, t),
            "f": (x, y, t, mid - y + t), "b": (x + w - t, y, t, mid - y + t),
            "e": (x, mid, t, y + h - mid), "c": (x + w - t, mid, t, y + h - mid),
        }
        for s in self.SEGMENTS.get(ch, ""):
            self.rect(*segs[s], v=v, fill=True)

    def seg_text(self, x, y, s, w=14, h=27, t=3, gap=3, v=1, boxes=None, slot="seg"):
        """Draw digits/colons in seven-segment style. Returns width drawn."""
        cx = x
        for i, ch in enumerate(s):
            if boxes is not None:
                boxes.append(((slot, i), cx, y, t if ch == ":" else w, h, ch))
            if ch == ":":
                self.rect(cx, y + h // 3 - t // 2, t, t, v, fill=True)
                self.rect(cx, y + 2 * h // 3 - t // 2, t, t, v, fill=True)
                cx += t + gap
            else:
                self.seg_digit(cx, y, ch, w, h, t, v)
                cx += w + gap
        return cx - x - gap

    @staticmethod
    def seg_width(s, w=14, t=3, gap=3):
        return sum((t if ch == ":" else w) + gap for ch in s) - gap


def wrap_text(s, font, width):
    """Greedy word wrap; returns a list of lines."""
    lines = []
    for para in s.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = (line + " " + word) if line else word
            if font.width(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines
