"""Per-character transitions: when one character of a clock or text changes, animate just
that character's box (odometer roll, flap, dissolve... any transition from transitions.py)."""

from apps.base import P
from canvas import Canvas
from transitions import NAMES, Transition

CHAR_PARAMS = [
    P("char_transition", "select", "none", "Character transition",
      options=[["none", "None"], ["split-flap", "split-flap (flaps through the alphabet)"]] + [[n, n] for n in NAMES if n not in ("none", "split-flap")]),
    P("char_time", "number", 0.5, "Character transition time (s)", min=0.1, max=3, step=0.1,
      show_if={"char_transition": {"not": "none"}}),
]


def crop(c, x, y, w, h):
    """The w x h box of c at (x, y) as a Canvas (black beyond c's edge), a row slice at a time."""
    out = Canvas(w, h)
    sx0, sx1 = max(0, x), min(c.w, x + w)
    if sx0 >= sx1:
        return out
    for yy in range(max(0, -y), min(h, c.h - y)):
        s = (y + yy) * c.w
        out.px[yy * w + sx0 - x:yy * w + sx1 - x] = c.px[s + sx0:s + sx1]
    return out


def _flapper(shown, old_key, key, slot, new, w, h, duration, now):
    """Split-flap: flap from the old character through the drum to the new one (digits of a seven-segment
    clock go round a digit drum), as many flaps as fit in `duration`; the last flap shows the real picture."""
    import splitflap
    seg = isinstance(slot, tuple) and slot[0] == "seg" and str(key) in splitflap.DIGITS
    limit = max(1, int(float(duration) / splitflap.FLAP_S))
    steps = splitflap.sequence(str(old_key)[:1], str(key)[:1], seg=seg, limit=limit)
    pics = [splitflap.glyph(ch, w, h, seg=seg) for ch in steps[:-1]] + [new]
    return splitflap.Flapper(shown, steps, pics, now, min(splitflap.FLAP_S, float(duration) / len(steps)))


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
                if self.kind == "split-flap":
                    t = _flapper(st["shown"], st["key"], key, slot, new, w, h, self.duration, now)
                else:
                    t = Transition(self.kind, st["shown"], w, h, self.duration)
                    t.start = now
                st["t"], st["key"] = t, key
            out = new
            if st["t"] is not None:
                if st["t"].done(now):
                    st["t"] = None
                else:
                    out = st["t"].frame(now) if self.kind == "split-flap" else st["t"].mix(new, now)
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
