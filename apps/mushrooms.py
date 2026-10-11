"""Mushroom garden: all sorts of fungi sprout, grow, drop spores and wither on a patch of ground.

Twelve species, each with its own shape: not just the dome-on-a-stem. Toadstools with spots and a skirt, pitted
morels, trumpet-shaped chanterelles with ridges running down the stem, shaggy ink caps that melt away from the
rim, enoki clusters of thin stems, puffballs that burst in a cloud of spores, bracket shelves on a stump, branching
coral fungus, a lion's mane with dangling spines, oyster fans with radial gills, little glowing ones with a
twinkling halo, and the plain button mushroom too. Mycelium threads spread under the ground and new mushrooms like
to come up where they reach. React to music: the garden grows to the tempo, puffballs burst on the bass, the halos
sparkle on the highs.
"""

import math
import random

from canvas import Canvas

from .base import App, P, register
from .react import quiet_now, reactive

SPECIES = ["toadstool", "morel", "chanterelle", "inkcap", "enoki", "puffball", "bracket", "coral", "lionsmane",
           "oyster", "glow", "button"]
TITLES = {"toadstool": "Toadstool", "morel": "Morel", "chanterelle": "Chanterelle", "inkcap": "Shaggy ink cap",
          "enoki": "Enoki cluster", "puffball": "Puffball", "bracket": "Bracket fungus", "coral": "Coral fungus",
          "lionsmane": "Lion's mane", "oyster": "Oyster mushroom", "glow": "Glowing mushroom",
          "button": "Button mushroom"}
ODD = [s for s in SPECIES if s != "button"]
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def ease(x):
    """Smooth 0..1 (slow start, quick middle, soft landing)."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class Brush:
    """Draws a mushroom into an ink canvas and its silhouette into a mask canvas. The mask (grown by one dot)
    clears whatever is behind, so overlapping mushrooms stay readable; holes are punched in the ink only."""

    def __init__(self, w, h):
        self.ink, self.mask = Canvas(w, h), Canvas(w, h)
        self.w, self.h = w, h
        self.x0, self.x1, self.y0, self.y1 = w, -1, h, -1

    def _touch(self, x, y):
        if x < self.x0:
            self.x0 = x
        if x > self.x1:
            self.x1 = x
        if y < self.y0:
            self.y0 = y
        if y > self.y1:
            self.y1 = y

    def dot(self, x, y, v=1):
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            self.mask.px[i] = 1
            self.ink.px[i] = 1 if v else 0
            self._touch(x, y)

    def hole(self, x, y):
        """A dark dot inside the silhouette (does nothing outside it)."""
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            if self.mask.px[i]:
                self.ink.px[i] = 0

    def hline(self, x0, x1, y, v=1):
        if x1 < x0:
            x0, x1 = x1, x0
        for x in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
            self.dot(x, y, v)

    def vline(self, x, y0, y1, v=1):
        if y1 < y0:
            y0, y1 = y1, y0
        for y in range(int(round(y0)), int(round(y1)) + 1):
            self.dot(x, y, v)

    def line(self, x0, y0, x1, y1, v=1):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.dot(x0, y0, v)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def ellipse(self, cx, cy, rx, ry, top=1.0, bottom=1.0):
        """A filled ellipse; top/bottom scale the halves (a dome: bottom=0; a bell: bottom small)."""
        if rx < 0.5 or ry < 0.5:
            self.dot(cx, cy)
            return
        for y in range(int(math.floor(cy - ry * top)), int(math.ceil(cy + ry * bottom)) + 1):
            s = (cy - y) / (ry * top) if y < cy else ((y - cy) / (ry * bottom) if bottom > 0 else 2)
            if s > 1:
                continue
            half = rx * math.sqrt(max(0.0, 1 - s * s))
            self.hline(cx - half, cx + half, y)

    def trapezoid(self, cx, y_top, y_bot, w_top, w_bot, curve=0.0):
        """Filled, symmetric about cx; curve bows the sides out (+) or in (-)."""
        y_top, y_bot = int(round(y_top)), int(round(y_bot))
        n = max(1, y_bot - y_top)
        for y in range(min(y_top, y_bot), max(y_top, y_bot) + 1):
            f = (y - y_top) / n
            wdt = w_top + (w_bot - w_top) * f + curve * math.sin(math.pi * f) * (w_top + w_bot) / 2
            self.hline(cx - wdt / 2, cx + wdt / 2, y)

    def ring_arc(self, cx, cy, rx, ry, top=1.0):
        """A dark arc (holes) along the top of an ellipse: ridges on brackets, the gill edge on caps."""
        for a in range(0, 181, 4):
            t = math.radians(a)
            self.hole(cx + rx * math.cos(t), cy - ry * top * math.sin(t))

    def dissolve(self, amount, sink=0):
        """Withering: dots drop out by Bayer threshold (amount 0..1), and the picture sinks by `sink` rows."""
        w, h = self.w, self.h
        if sink:
            ink, mask = self.ink.px, self.mask.px
            self.ink.px = bytearray(w * h)
            self.mask.px = bytearray(w * h)
            self.ink.px[sink * w:] = ink[:w * h - sink * w]
            self.mask.px[sink * w:] = mask[:w * h - sink * w]
            self.y0, self.y1 = self.y0 + sink, min(h - 1, self.y1 + sink)
        if amount <= 0:
            return
        thr = amount * 16
        ink, mask = self.ink.px, self.mask.px
        for y in range(max(0, self.y0), min(h, self.y1 + 1)):
            row = BAYER[y & 3]
            for x in range(max(0, self.x0), min(w, self.x1 + 1)):
                if row[x & 3] < thr:
                    i = y * w + x
                    ink[i] = mask[i] = 0

    def stamp(self, c):
        """Composite onto c: dots under the (grown) mask are cleared, then the ink is painted."""
        if self.x1 < 0:
            return
        w = self.w
        n = w * self.h
        m = int.from_bytes(bytes(self.mask.px), "big")
        grown = m | m << 8 | m >> 8 | m << 8 * w | m >> 8 * w
        grown |= m << 8 * (w + 1) | m << 8 * (w - 1) | m >> 8 * (w + 1) | m >> 8 * (w - 1)
        grown &= (1 << 8 * n) - 1
        main = int.from_bytes(bytes(c.px), "big")
        ink = int.from_bytes(bytes(self.ink.px), "big")
        out = (main & ~grown) | ink
        c.px[:] = out.to_bytes(n, "big")


# ---- the species ------------------------------------------------------------------------------------------------
# Each draws itself with its base at (x, y) on brush b, grown g (0..1), height S (dots) at full size, its own
# random generator r (same every frame: the details hold still), age t (s since mature, <0 while growing) and the
# music m. Spores/puffs return particle seeds via `shroom.emit`.


def _stem(b, x, y, top, wdt, g=1.0):
    """A stem from the ground (y) up to `top`, `wdt` dots wide, slightly wider at the foot."""
    wdt = max(1, wdt)
    for yy in range(int(round(top)), int(y) + 1):
        f = (y - yy) / max(1, y - top)
        ww = wdt + (1 if f < 0.2 and wdt >= 2 else 0)
        b.hline(x - ww / 2 + 0.5, x + ww / 2 - 0.5, yy)


def draw_button(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * g
    cap_w = S * 0.9 * (0.3 + 0.7 * g) * sh.wobble
    cap_h = cap_w * r.uniform(0.42, 0.55)
    stem_w = max(2, S * 0.18)
    _stem(b, x, y, y - H + cap_h * 0.6, stem_w)
    cy = y - H + cap_h
    b.ellipse(x, cy, cap_w / 2, cap_h, top=1.0, bottom=0.25)
    if g > 0.6:  # gills along the rim once it opens
        for xx in range(int(x - cap_w / 2) + 1, int(x + cap_w / 2), 2):
            b.hole(xx, cy + 1)


def draw_toadstool(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * g
    cap_w = S * 1.1 * (0.25 + 0.75 * g) * sh.wobble
    cap_h = cap_w * r.uniform(0.38, 0.5)
    stem_w = max(2, S * 0.16)
    top_of_stem = y - H + cap_h * 0.5
    _stem(b, x, y, top_of_stem, stem_w)
    cy = y - H + cap_h
    b.ellipse(x, cy, cap_w / 2, cap_h, top=1.0, bottom=0.35)
    if g > 0.5:
        for (fx, fy, s) in sh.detail:  # white spots, as holes, spread as the cap opens
            px, py = x + fx * cap_w / 2, cy - fy * cap_h
            for dx in range(-s, s + 1):
                for dy in range(-s, s + 1):
                    if abs(dx) + abs(dy) <= s:
                        b.hole(px + dx, py + dy)
        ring_y = cy + cap_h * 0.35 + 3
        if ring_y < y - 3:  # the skirt: a flare round the stem with a dark line above it
            b.hline(x - stem_w / 2 - 2, x + stem_w / 2 + 2, ring_y + 1)
            b.hline(x - stem_w / 2 - 1, x + stem_w / 2 + 1, ring_y)
            for xx in range(int(x - stem_w / 2), int(x + stem_w / 2) + 1):
                b.hole(xx, ring_y - 1)


def draw_morel(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * g
    cap_h = H * 0.62
    cap_w = S * 0.5 * (0.4 + 0.6 * g) * sh.wobble
    stem_w = max(3, S * 0.22)
    top_of_stem = y - H + cap_h
    _stem(b, x, y, top_of_stem, stem_w)
    if stem_w >= 4:  # a hollow, pale stem: just its outline
        for yy in range(int(top_of_stem) + 1, int(y)):
            for xx in range(int(x - stem_w / 2) + 2, int(x + stem_w / 2) - 1):
                b.hole(xx, yy)
    cy = y - H + cap_h
    b.trapezoid(x, y - H + 1, cy, cap_w * 0.25, cap_w, curve=0.25)
    b.ellipse(x, y - H + 1.5, cap_w * 0.2, 1.5, bottom=0)
    if g > 0.3:  # the honeycomb pits
        for yy in range(int(y - H) + 2, int(cy)):
            off = 1 if (yy // 3) % 2 else 0
            if (yy - int(y - H)) % 3 == 1:
                for xx in range(int(x - cap_w / 2) + off, int(x + cap_w / 2) + 1, 3):
                    b.hole(xx, yy)
                    b.hole(xx + 1, yy)


def draw_chanterelle(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * 0.8 * g
    top_w = S * 0.9 * (0.2 + 0.8 * g) * sh.wobble
    foot_w = max(2, S * 0.15)
    top = y - H
    # a trumpet: narrow foot flaring up to a wide, wavy rim, with a dip in the middle of the top
    for yy in range(int(round(top)), int(y) + 1):
        f = (y - yy) / max(1.0, H)
        wdt = foot_w + (top_w - foot_w) * f ** 1.8
        b.hline(x - wdt / 2, x + wdt / 2, yy)
    for i in range(int(x - top_w / 2), int(x + top_w / 2) + 1):  # the wavy rim and the funnel's dip
        wave = 1 if math.sin((i - x) * 1.3 + sh.seed) > 0.3 else 0
        dip = int(2 * (1 - ((i - x) / max(1, top_w / 2)) ** 2)) if g > 0.5 else 0
        for k in range(wave + dip):
            b.hole(i, top + k)
    if g > 0.4:  # ridges running down into the stem (decurrent gills)
        n = max(2, int(top_w / 3))
        for k in range(n):
            fx = -1 + 2 * (k + 0.5) / n
            for yy in range(int(top) + 2, int(y - H * 0.25)):
                f = (y - yy) / max(1.0, H)
                wdt = foot_w + (top_w - foot_w) * f ** 1.8
                if (yy + k) % 2 == 0:
                    b.hole(x + fx * wdt / 2 * 0.9, yy)


def draw_inkcap(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * g
    cap_w = max(3, S * 0.3 * (0.5 + 0.5 * g)) * sh.wobble
    cap_h = H * 0.55
    stem_w = max(1, S * 0.1)
    melt = ease((sh.age - 2.0) / max(1.0, sh.hold_s - 2.0)) if sh.age > 2.0 else 0.0  # deliquesces as it ages
    cap_top = y - H
    cap_bot = cap_top + cap_h * (1 - 0.9 * melt)
    _stem(b, x, y, cap_top + cap_h * 0.3, stem_w)
    b.ellipse(x, cap_top + cap_w * 0.45, cap_w / 2, cap_w * 0.45, bottom=0)
    b.trapezoid(x, cap_top + cap_w * 0.45, cap_bot, cap_w, cap_w * (1.0 + 0.3 * melt))
    for yy in range(int(cap_top) + 2, int(cap_bot)):  # shaggy scales: a brick pattern of ticks
        off = 1 if (yy // 2) % 2 else 0
        if yy % 2:
            for xx in range(int(x - cap_w / 2) + off, int(x + cap_w / 2) + 1, 3):
                b.hole(xx, yy)
    if melt > 0.05:  # ragged, dripping rim
        for xx in range(int(x - cap_w / 2) - 1, int(x + cap_w / 2) + 2):
            if r.random() < 0.5:
                b.dot(xx, cap_bot + 1)
        sh.want = ("drip", cap_bot + 1, cap_w * 0.65)


def draw_enoki(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    n = len(sh.detail)
    for i, (fh, delay) in enumerate(sh.detail):  # thin stems two dots apart at the foot, fanning apart up top
        gg = ease((g - delay) / max(0.05, 1 - delay))
        if gg <= 0:
            continue
        H = S * fh * gg
        fx = (i - (n - 1) / 2)
        foot = x + fx * 2
        tip_x = foot + fx * 1.6 * gg
        b.line(foot, y, tip_x, y - H)
        if gg > 0.4:  # a tiny round cap, a dark dot under it
            b.hline(tip_x - 1, tip_x + 1, y - H)
            b.hline(tip_x - 1, tip_x + 1, y - H - 1)
            b.dot(tip_x, y - H - 2)
            b.hole(tip_x, y - H + 1)


def draw_puffball(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    R = S * 0.45 * (0.15 + 0.85 * g) * sh.wobble
    squash = 1 - 0.5 * sh.collapse  # after its puffs it sags
    cy = y - R * 0.9 * squash
    b.ellipse(x, cy, R, R * 0.95 * squash, top=1.0, bottom=0.9)
    if g > 0.5:
        for (fx, fy, s) in sh.detail:  # warts
            if fx * fx + fy * fy < 0.8:
                b.hole(x + fx * R, cy + fy * R * squash)
    if sh.collapse > 0.05 or sh.puffing:  # the opening on top
        o = max(1, int(R * 0.3))
        for xx in range(int(x - o), int(x + o) + 1):
            b.hole(xx, cy - R * squash + 1)
            b.hole(xx, cy - R * squash + 2)
    if g >= 1 and not sh.puffing and sh.puffs < 3:
        if (sh.age > sh.next_puff) or (m.on and m.hit("bass") and sh.age > 1.0):
            sh.puffing = True
            sh.want = ("puff", cy - R * squash, R)


def draw_bracket(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * 0.9
    stump_w = max(3, S * 0.22)
    b.trapezoid(x, y - H, y, stump_w, stump_w * 1.4)
    for yy in range(int(y - H) + 1, int(y)):  # bark: a diagonal grain
        for xx in range(int(x - stump_w / 2) + 1, int(x + stump_w / 2)):
            if (xx - int(x) + yy // 3) % 3 == 0:
                b.hole(xx, yy)
    side = sh.side
    for (fy, fw, delay) in sh.detail:  # shelves, from the bottom up
        gg = ease((g - delay) / max(0.05, 1 - delay))
        if gg <= 0:
            continue
        sw = S * fw * gg
        th = max(2.0, sw * 0.3)
        cy = y - H * fy
        cx = x + side * (stump_w / 2 - 1)
        for yy in range(int(cy - th), int(cy) + 1):  # a half ellipse sticking out of the stump
            f = max(0.0, 1 - ((cy - yy) / th) ** 2)
            ext = sw * math.sqrt(f)
            b.hline(cx, cx + side * ext, yy)
        if sw >= 6:  # one growth ring, a few dots along it
            rr = sw * 0.62
            for a in range(10, 90, 12):
                t = math.radians(a)
                b.hole(cx + side * rr * math.cos(t), cy - th * math.sin(t))
        if gg > 0.9:  # the pores underneath: a dark line along the bottom
            for xx in range(int(cx + side * 2), int(cx + side * (sw - 1)), 2 * side or 2):
                b.hole(xx, cy + 1)


def draw_coral(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    depth = 4
    total = S * g

    def branch(bx, by, ang, length, d, rnd):
        if d > depth or length < 1:
            return
        ex, ey = bx + length * math.cos(ang), by - length * math.sin(ang)
        b.line(bx, by, ex, ey)
        if d <= 2:  # the lower branches are two dots thick
            b.line(bx + 1, by, ex + 1, ey)
        if d == depth or length < 2.5:
            b.hline(ex - 1, ex + 1, ey)  # a little club at the tip
            b.dot(ex, ey - 1)
            return
        n = 2 if rnd.random() < 0.7 else 3
        spread = rnd.uniform(0.4, 0.75)
        for i in range(n):
            a = ang + spread * (i - (n - 1) / 2) + rnd.uniform(-0.12, 0.12)
            branch(ex, ey, a, length * rnd.uniform(0.6, 0.78), d + 1, rnd)

    rnd = random.Random(sh.seed)
    trunk = total * 0.28
    b.trapezoid(x, y - trunk, y, 3, 5)
    nb = 2 + sh.seed % 2
    for i in range(nb):
        a = math.pi / 2 + 0.7 * (i - (nb - 1) / 2) + rnd.uniform(-0.1, 0.1)
        branch(x - 1 + i, y - trunk, a, total * 0.3, 1, rnd)


def draw_lionsmane(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    R = S * 0.38 * (0.2 + 0.8 * g) * sh.wobble
    stub = max(2, S * 0.2)
    _stem(b, x, y, y - stub - 1, max(2, S * 0.2))
    cy = y - stub - R * 0.9
    b.ellipse(x, cy, R, R * 0.85, top=1.0, bottom=0.9)
    if g > 0.4:  # dangling spines: alternate columns reach below, holes between them inside the lower half
        for xx in range(int(x - R) + 1, int(x + R)):
            k = xx - int(x - R)
            if k % 2 == 0:
                drop = R * 0.5 * g * (0.6 + 0.4 * math.sin(k * 1.7 + sh.seed) ** 2)
                base = cy + R * 0.85 * 0.9 * math.sqrt(max(0.0, 1 - ((xx - x) / R) ** 2))
                b.vline(xx, base, base + drop)
            else:
                for yy in range(int(cy), int(cy + R * 0.8)):
                    if (yy + k // 2) % 2:
                        b.hole(xx, yy)


def draw_oyster(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    side = sh.side
    stem_top = y - S * 0.3 * g
    b.trapezoid(x, stem_top, y, 2, 3)  # a short, thick stem...
    for i, (fy, fw, delay) in enumerate(sh.detail):  # ...and oval caps shingled up out of its top
        gg = ease((g - delay) / max(0.05, 1 - delay))
        if gg <= 0:
            continue
        fw_ = S * fw * gg
        th = max(2.0, fw_ * 0.36)
        cy = stem_top - S * (fy - 0.3) * gg
        cx = x + side * fw_ * 0.5
        b.ellipse(cx, cy, fw_ * 0.5, th, top=1.0, bottom=0.4)
        if fw_ >= 6:  # a dark underside so each cap stands out from the one below, with a few gills
            for a in range(0, 181, 6):
                t = math.radians(a)
                b.hole(cx + fw_ * 0.5 * math.cos(t), cy + th * 0.4 * math.sin(t) + 0.5)
            if gg > 0.6:
                for k in range(1, 4):
                    gx = x + side * (fw_ * k / 4)
                    b.hole(gx, cy + th * 0.4 - 1)
                    b.hole(gx, cy + th * 0.4 - 2)


def draw_glow(sh, b, g, m):
    x, y, S, r = sh.x, sh.y, sh.size, sh.r
    H = S * 0.7 * g
    cap_w = max(3, S * 0.35 * (0.3 + 0.7 * g)) * sh.wobble
    _stem(b, x, y, y - H + 2, 1)
    cy = y - H + cap_w * 0.35
    b.ellipse(x, cy, cap_w / 2, cap_w * 0.35, top=1.0, bottom=0.6)  # a little bell
    for xx in range(int(x - cap_w / 2) + 1, int(x + cap_w / 2), 2):
        b.hole(xx, cy + cap_w * 0.2)
    if g > 0.7:
        sh.want = ("halo", cy, cap_w)


DRAW = {"button": draw_button, "toadstool": draw_toadstool, "morel": draw_morel, "chanterelle": draw_chanterelle,
        "inkcap": draw_inkcap, "enoki": draw_enoki, "puffball": draw_puffball, "bracket": draw_bracket,
        "coral": draw_coral, "lionsmane": draw_lionsmane, "oyster": draw_oyster, "glow": draw_glow}
GILLED = {"button", "toadstool", "chanterelle", "oyster", "glow", "lionsmane", "morel"}  # drop spores when mature
WIDTH = {"button": 0.9, "toadstool": 1.1, "morel": 0.5, "chanterelle": 0.9, "inkcap": 0.35, "enoki": 0.8,
         "puffball": 0.9, "bracket": 1.0, "coral": 0.8, "lionsmane": 0.8, "oyster": 1.0, "glow": 0.4}


class Shroom:
    def __init__(self, species, x, y, size, born, pace, seed):
        self.species, self.x, self.y, self.size, self.born = species, x, y, size, born
        self.r = random.Random(seed)
        self.seed = seed
        r = self.r
        self.grow_s = pace * r.uniform(0.5, 1.0)
        self.hold_s = pace * r.uniform(0.7, 1.6)
        self.fade_s = pace * r.uniform(0.3, 0.5)
        self.side = r.choice([-1, 1])
        self.wobble = 1.0
        self.age = -1.0  # s since fully grown
        self.want = None  # a particle request from the drawing (kind, y, width)
        self.puffing, self.puffs, self.collapse, self.next_puff = False, 0, 0.0, r.uniform(1.5, 4.0)
        self.spore_at = 0.0
        self.width = size * WIDTH[species]
        if species == "toadstool":
            n = max(3, int(size / 3))
            self.detail = [(r.uniform(-0.8, 0.8), r.uniform(0.15, 0.85), r.choice([0, 0, 1])) for _ in range(n)]
        elif species == "puffball":
            self.detail = [(r.uniform(-1, 1), r.uniform(-1, 1), 0) for _ in range(max(4, int(size / 2)))]
        elif species == "enoki":
            n = 4 + int(size / 8)
            self.detail = [(r.uniform(0.55, 1.0), r.uniform(0, 0.5)) for _ in range(n)]
        elif species == "bracket":
            n = 2 + int(size / 10)
            self.detail = [((i + 1) / (n + 0.5), r.uniform(0.5, 1.0), i / (n + 1)) for i in range(n)]
        elif species == "oyster":
            n = 2 + int(size / 10)
            self.detail = [(0.3 + 0.22 * i, r.uniform(0.9, 1.0) - 0.15 * i, i / (n + 1)) for i in range(n)]
        else:
            self.detail = []

    def phase(self, t):
        """-> ("grow", g) | ("hold", age) | ("fade", w) | ("gone", 0)"""
        a = t - self.born
        if a < self.grow_s:
            return "grow", ease(a / self.grow_s)
        a -= self.grow_s
        if a < self.hold_s:
            return "hold", a
        a -= self.hold_s
        if a < self.fade_s:
            return "fade", a / self.fade_s
        return "gone", 0.0

    def draw(self, c, t, m, pulse):
        kind, v = self.phase(t)
        if kind == "gone":
            return False
        g = v if kind == "grow" else 1.0
        self.age = (t - self.born - self.grow_s) if kind != "grow" else -1.0
        self.wobble = 1 + 0.06 * pulse
        if self.puffing and self.puffs >= 3:
            self.collapse = min(1.0, self.collapse + 0.02)
        b = Brush(c.w, c.h)
        self.want = None
        self.r.seed(self.seed)  # the same details every frame
        DRAW[self.species](self, b, g, m)
        if kind == "fade":
            b.dissolve(v, sink=int(v * 3))
        b.stamp(c)
        return True


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "kind", "t0")

    def __init__(self, x, y, vx, vy, life, kind, t0):
        self.x, self.y, self.vx, self.vy, self.life, self.kind, self.t0 = x, y, vx, vy, life, kind, t0


@register
@reactive
class Mushrooms(App):
    id = "mushrooms"
    title = "Mushroom garden"
    category = "Generative"
    description = ("Fungi of all shapes sprout, grow, drop spores and wither: toadstools, morels, chanterelles, ink caps "
                   "that melt, enoki clusters, puffballs that burst, brackets, coral, lion's mane, oysters, glowing "
                   "ones and the plain button. Mycelium spreads under the ground.")
    params = [
        P("species", "select", "mix", "Which mushrooms", options=[["mix", "A mix of all kinds"],
                                                                 ["odd", "The odd ones (no plain button mushrooms)"],
                                                                 *[[s, TITLES[s]] for s in SPECIES]]),
        P("crowd", "number", 6, "Mushrooms at a time", min=1, max=14),
        P("pace", "number", 30, "Seconds to grow (about)", min=5, max=300),
        P("sizes", "select", "mixed", "Sizes", options=[["mixed", "Mixed"], ["small", "Small"], ["big", "Big"]]),
        P("ground", "select", "mycelium", "Ground", options=[["mycelium", "Grass and mycelium underneath"],
                                                              ["grass", "Grass"], ["none", "None"]]),
        P("spores", "checkbox", True, "Spores drift"),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rng = random.Random(random.random())  # seeded from the module's generator (tests seed it)
        self.ground_y = self.h - (1 if self.p["ground"] == "none" else 5)
        self.shrooms = []
        self.parts = []
        self.threads = []  # mycelium: [x, y, dir, left]
        self.next_spawn = 0.0
        self.next_thread = 0.0
        self.last_species = None
        self.grass = [(x, self.rng.choice([1, 1, 2])) for x in range(self.w) if self.rng.random() < 0.3]
        self.soil = set()
        self.last_t = None
        self.pace = max(5.0, float(self.p["pace"] or 30))
        t = 0.0
        for _ in range(int(self.p["crowd"])):  # a garden already in progress
            self.spawn(t)
            s = self.shrooms[-1]
            s.born = -self.rng.uniform(0, s.grow_s + s.hold_s)

    # ---- the garden ---------------------------------------------------------------------------------------------

    def pool(self):
        s = self.p["species"]
        return SPECIES if s == "mix" else ODD if s == "odd" else [s]

    def pick_size(self, species):
        lo, hi = {"small": (8, 16), "big": (20, 34), "mixed": (8, 30)}[self.p["sizes"]]
        if species in ("enoki", "glow"):
            hi = min(hi, 20)
        if species in ("bracket", "oyster", "coral"):
            lo = max(lo, 12)
        return self.rng.uniform(lo, min(hi, self.ground_y - 1))

    def spawn(self, t):
        pool = self.pool()
        if len(pool) > 1 and self.shrooms:  # a kind that isn't up yet, and never the same as the last one
            here = {s.species for s in self.shrooms} | {self.last_species}
            pool = [p for p in pool if p not in here] or [p for p in pool if p != self.last_species] or pool
        species = self.rng.choice(pool)
        self.last_species = species
        size = self.pick_size(species)
        room = self.w * 1.3 - sum(s.width for s in self.shrooms)
        if room < size * WIDTH[species]:  # crowded: a small one, so the garden doesn't become one big tangle
            size = min(size, self.rng.uniform(8, 13))
        half = size * WIDTH[species] / 2
        tips = [th[0] for th in self.threads if th[3] <= 0]
        best, score = None, -1e9
        for _ in range(14):
            if tips and self.rng.random() < 0.4:
                x = tips[self.rng.randrange(len(tips))] + self.rng.uniform(-2, 2)
            else:
                x = self.rng.uniform(half * 0.6, self.w - half * 0.6)
            x = max(2, min(self.w - 3, x))
            gap = min([abs(x - s.x) - (half + s.width / 2) for s in self.shrooms] or [self.w])
            if gap > score:
                best, score = x, gap
        sh = Shroom(species, best, self.ground_y, size, t, self.pace, self.rng.randrange(1 << 30))
        if species in ("bracket", "oyster"):  # shelves grow towards the middle, not off the edge
            sh.side = -1 if best > self.w / 2 else 1
        self.shrooms.append(sh)
        self.shrooms.sort(key=lambda s: -s.size)  # big ones at the back
        self.threads.append([best, self.ground_y + 2, self.rng.choice([-1, 1]), 12 + int(size)])
        return sh

    def emit(self, sh, t, m):
        want, sh.want = sh.want, None
        kind, y, wdt = want if want else (None, 0, 0)
        if kind == "puff":
            n = 10 + int(wdt * 2)
            for _ in range(n):
                a = self.rng.uniform(-1.2, 1.2)
                v = self.rng.uniform(4, 10 + 6 * wdt / 8)
                self.parts.append(Particle(sh.x + self.rng.uniform(-1, 1), y, v * math.sin(a), -v * math.cos(a) * 0.6,
                                           self.rng.uniform(1.0, 2.2), "puff", t))
            sh.puffs += 1
            sh.puffing = False
            sh.next_puff = sh.age + self.rng.uniform(2.5, 6.0)
        elif kind == "drip" and self.rng.random() < 0.25:
            self.parts.append(Particle(sh.x + self.rng.uniform(-wdt / 2, wdt / 2), y, 0, 6, 1.5, "drip", t))
        elif kind == "halo":
            self.parts.append(Particle(sh.x + self.rng.uniform(-wdt, wdt), y + self.rng.uniform(-wdt, wdt * 0.8),
                                       0, 0, self.rng.uniform(0.15, 0.4), "spark", t))
            if m.on and m.level("high") > 0.4:
                self.parts.append(Particle(sh.x + self.rng.uniform(-wdt * 1.3, wdt * 1.3), y + self.rng.uniform(-wdt, wdt),
                                           0, 0, 0.2, "spark", t))
        if self.p["spores"] and sh.species in GILLED and sh.age > 0.5 and sh.phase(t)[0] == "hold":
            if t >= sh.spore_at:
                rate = 0.5 if not m.on else 0.25 / (1 + m.level("bass"))
                sh.spore_at = t + self.rng.expovariate(1 / rate)
                self.parts.append(Particle(sh.x + self.rng.uniform(-sh.width / 3, sh.width / 3), sh.y - sh.size * 0.5,
                                           self.rng.uniform(-1.5, 1.5), self.rng.uniform(1.0, 3.0), 4.0, "spore", t))

    def particles(self, c, t, dt, wind):
        keep = []
        for pt in self.parts:
            age = t - pt.t0
            if age > pt.life or pt.y >= self.ground_y + 1:
                continue
            if pt.kind == "puff":
                pt.vx *= 1 - 1.5 * dt
                pt.vy = pt.vy * (1 - 1.5 * dt) - 2.0 * dt
                pt.x += (pt.vx + wind) * dt
                pt.y += pt.vy * dt
                if (int(age * 8) + int(pt.x)) % 2 == 0 or age < 0.4:
                    c.set(pt.x, pt.y)
            elif pt.kind == "drip":
                pt.y += pt.vy * dt
                c.set(pt.x, pt.y)
            elif pt.kind == "spark":
                c.set(pt.x, pt.y)
            else:  # spore: a slow fall with a wobble, drawn as a flicker so it reads as tiny
                pt.x += (wind + 1.5 * math.sin(age * 5 + pt.x)) * dt
                pt.y += pt.vy * dt
                if int(age * 10) % 2 == 0:
                    c.set(pt.x, pt.y)
            keep.append(pt)
        self.parts = keep[-120:]

    def grow_threads(self, t):
        if self.p["ground"] != "mycelium" or t < self.next_thread:
            return
        self.next_thread = t + 0.25
        for th in self.threads:
            if th[3] <= 0:
                continue
            x, y, d, left = th
            self.soil.add((int(x), int(y)))
            r = self.rng.random()
            if r < 0.15:
                y += self.rng.choice([-1, 1])
                y = max(self.ground_y + 2, min(self.h - 1, y))
            elif r < 0.2:
                d = -d
            x += d
            if not 0 <= x < self.w:
                left = 0
            th[0], th[1], th[2], th[3] = x, y, d, left - 1
            if r > 0.97 and len(self.threads) < 40:  # a branch
                self.threads.append([x, y, -d, left // 2])
        self.threads = [th for th in self.threads if th[3] > 0][-40:] + [th for th in self.threads if th[3] <= 0][-20:]

    def draw_grass(self, c):
        if self.p["ground"] != "none":
            for x, hgt in self.grass:
                for k in range(hgt):
                    c.set(x, self.ground_y - k)

    def draw_ground(self, c):
        """The ground line and the mycelium under it, over the mushrooms' feet."""
        if self.p["ground"] == "none":
            return
        gy = self.ground_y
        for x in range(self.w):
            c.set(x, gy + 1)
        if self.p["ground"] == "mycelium":
            for (x, y) in self.soil:
                c.set(x, y)

    def render(self, c, now):
        m = self.music
        t = m.clock("t", now, 1.0)
        dt = 0.0 if self.last_t is None else max(0.0, min(0.5, t - self.last_t))
        self.last_t = t
        wind = 1.2 * math.sin(t * 0.3) + 0.6 * math.sin(t * 0.07)
        pulse = m.pulse if m.on else 0.0
        self.grow_threads(t)
        self.draw_grass(c)
        alive = []
        for sh in self.shrooms:
            if sh.draw(c, t, m, pulse):
                alive.append(sh)
                self.emit(sh, t, m)
        self.shrooms = alive
        crowd = int(self.p["crowd"])
        if len(self.shrooms) < crowd and t >= self.next_spawn:
            self.spawn(t)
            self.next_spawn = t + self.rng.uniform(0.2, 0.3) * self.pace / max(1, crowd) * (0.5 if m.on else 1.0)
        self.draw_ground(c)
        self.particles(c, t, dt, wind)
        quiet = False
        try:
            quiet = quiet_now(self.ctx.store)
        except Exception:
            pass
        self.interval = 0.25 if quiet else 0.1

    def describe(self):
        return self.title
