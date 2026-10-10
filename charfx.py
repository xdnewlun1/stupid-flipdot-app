"""Per-character transitions: when one character of a clock or text changes, animate just
that character's box (odometer roll, flap, dissolve... any transition from transitions.py)."""

from apps.base import P
from canvas import Canvas
from transitions import NAMES, Transition

CHAR_PARAMS = [
    P("char_transition", "select", "none", "Character transition",
      options=[["none", "None"]] + [[n, n] for n in NAMES if n != "none"]),
    P("char_time", "number", 0.5, "Character transition time (s)", min=0.1, max=3, step=0.1,
      show_if={"char_transition": {"not": "none"}}),
]


def crop(c, x, y, w, h):
    out = Canvas(w, h)
    for yy in range(h):
        sy = y + yy
        if 0 <= sy < c.h:
            for xx in range(w):
                sx = x + xx
                if 0 <= sx < c.w:
                    out.px[yy * w + xx] = c.px[sy * c.w + sx]
    return out


class CharFx:
    def __init__(self, kind="none", duration=0.5):
        self.kind, self.duration = kind, float(duration)
        self.slots = {}
        self.active = False

    def apply(self, c, boxes, now):
        """c already holds the new frame. boxes: [(slot, x, y, w, h, key)] for each character.
        Characters whose key changed since last frame are animated from what was shown before."""
        if self.kind in (None, "", "none"):
            return
        seen = set()
        self.active = False
        for slot, x, y, w, h, key in boxes:
            if w <= 0 or h <= 0:
                continue
            seen.add(slot)
            geom = (x, y, w, h)
            new = crop(c, x, y, w, h)
            st = self.slots.get(slot)
            if st is None or st["geom"] != geom:
                # first sight, or the layout moved (e.g. 9:59 -> 10:00): no animation
                self.slots[slot] = {"geom": geom, "key": key, "shown": new, "t": None}
                continue
            if key != st["key"]:
                t = Transition(self.kind, st["shown"], w, h, self.duration)
                t.start = now
                st["t"], st["key"] = t, key
            out = new
            if st["t"] is not None:
                if st["t"].done(now):
                    st["t"] = None
                else:
                    out = st["t"].mix(new, now)
                    self.active = True
                    # paste the in-between picture over this character's box
                    for yy in range(h):
                        sy = y + yy
                        if 0 <= sy < c.h:
                            for xx in range(w):
                                sx = x + xx
                                if 0 <= sx < c.w:
                                    c.px[sy * c.w + sx] = out.px[yy * w + xx]
            st["shown"] = out
        for slot in [s for s in self.slots if s not in seen]:
            del self.slots[slot]
