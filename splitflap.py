"""Split-flap characters, like a railway departures board: a character never jumps to its new value, it flaps
forward through the alphabet until it lands there, one flap at a time (each flap is an audible clack).

Used by the Split-flap app (apps/pack_ambient.py) and as the "split-flap" character transition (charfx.py), so
clocks and text can flap their digits too.
"""

from canvas import Canvas
from fonts import FONTS, NORMAL, SMALL

ALPHABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?'/&+#@"  # the order of the flaps on the drum
DIGITS = "0123456789"  # a seven-segment digit's drum (blank is reached in one flap)
FLAP_S = 0.065           # seconds per flap (a real Solari board does about 15 a second)


def drum_for(ch, seg=False):
    return DIGITS if seg else ALPHABET


def sequence(old, new, seg=False, limit=None):
    """The characters shown on the way from `old` to `new` (not including old, ending with new). Characters not on
    the drum are reached in one flap. limit: at most this many flaps (the last ones: it starts nearer)."""
    drum = drum_for(new, seg)
    o, n = (old or " ").upper(), (new or " ").upper()
    if n not in drum or o not in drum:
        steps = [new]
    else:
        i, j = drum.index(o), drum.index(n)
        dist = (j - i) % len(drum)
        steps = [drum[(i + k) % len(drum)] for k in range(1, dist + 1)] or [new]
        steps[-1] = new  # keep the case the text asked for
    if limit is not None and len(steps) > limit:
        steps = steps[-max(1, limit):]
    return steps


def font_for(h):
    """(font, scale) for a character box this tall: the built-in pixel font that fills it."""
    for f in (NORMAL, SMALL, FONTS.get("tall")):
        if f is not None and h % f.height == 0:
            return f, h // f.height
    return NORMAL, 1


def glyph(ch, w, h, seg=False, font=None, scale=None):
    """A w x h picture of one character, as the flap shows it."""
    c = Canvas(w, h)
    if ch in (None, " "):
        return c
    if seg:
        t = max(1, round(w / 5))
        c.seg_digit(0, 0, ch, w, h, t)
        return c
    if font is None:
        font, scale = font_for(h)
    scale = scale or 1
    g = font.glyph(ch)
    gw = len(g[0]) * scale
    c.text(max(0, (w - gw) // 2), 0, ch, font, scale=scale)
    return c


def flap_frame(prev, nxt, f):
    """One flap at fraction f (0..1): the top half of the next character drops over the previous one; past half
    way the whole next character shows."""
    if f >= 0.5:
        return nxt
    out = prev.copy()
    w, half = prev.w, prev.h // 2
    out.px[:half * w] = nxt.px[:half * w]
    return out


class Flapper:
    """One character's flap run: from what it showed to its new picture, flap by flap."""

    def __init__(self, start_pic, steps, pictures, start, flap_s=FLAP_S):
        self.pics = [start_pic] + pictures  # pictures[k] for steps[k]
        self.steps = steps
        self.start, self.flap_s = start, flap_s

    @property
    def end(self):
        return self.start + len(self.steps) * self.flap_s

    def done(self, now):
        return now >= self.end

    def frame(self, now):
        if now <= self.start:
            return self.pics[0]
        k, f = divmod((now - self.start) / self.flap_s, 1.0)
        k = int(k)
        if k >= len(self.steps):
            return self.pics[-1]
        return flap_frame(self.pics[k], self.pics[k + 1], f)
