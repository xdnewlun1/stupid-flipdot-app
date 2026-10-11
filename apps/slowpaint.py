"""Slow painting: the board morphs from one picture to the next on a flip budget.

Pictures come from Pictures (the first frame of each), a slideshow, or the daily AI
postcards. The app spends at most `budget` flips per day (or hour), spread evenly over
it, and each flip moves one dot toward the next picture, so the board converges in
days or hours instead of a second. Which dot goes next:
  - error-weighted: dots in areas that are most wrong go first, so whole shapes appear
    before stray single dots;
  - spread out: a dot next to a recent flip waits, so the change creeps in all over;
  - wear-aware: between otherwise equal dots, the less-flipped disc (wear.json) wins.
The picture so far and the flips spent are kept in slowpaint.json, so a restart or a
playlist going round neither starts over nor spends the budget twice.
"""

import datetime as dt
import logging
import random
import time

from .base import App, P, register

log = logging.getLogger("slowpaint")

STATE = "slowpaint.json"
RADIUS = 2          # error density is counted over a (2R+1)^2 square
HEAT_RADIUS = 3     # a flip warms this square; warm dots wait
HEAT_FLIPS = 12     # ...for about this many further flips
CATCH_UP = 10       # after a pause it may flip this many seconds' worth of budget at once, no more
SAVE_EVERY = 10     # seconds between state saves while flipping


def window(now, per):
    """(key, start, length in seconds) of the budget window containing `now`."""
    t = dt.datetime.fromtimestamp(now)
    if per == "hour":
        start = t.replace(minute=0, second=0, microsecond=0)
        return start.strftime("%Y-%m-%dT%H"), start.timestamp(), 3600
    start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%d"), start.timestamp(), (start + dt.timedelta(days=1)).timestamp() - start.timestamp()


def allowance(budget, per, now):
    """Flips allowed so far in the current window: the budget spread evenly over it."""
    _, start, length = window(now, per)
    return min(budget, int(budget * (now - start) / length) + 1)


def box_sums(bits, w, h, r):
    """For each dot, how many set dots are within the (2r+1)^2 square around it (summed-area table)."""
    sat = [0] * ((w + 1) * (h + 1))
    for y in range(h):
        row, acc = y * w, 0
        o, p = (y + 1) * (w + 1), y * (w + 1)
        for x in range(w):
            acc += bits[row + x]
            sat[o + x + 1] = sat[p + x + 1] + acc
    out = [0] * (w * h)
    for y in range(h):
        y0, y1 = max(0, y - r), min(h, y + r + 1)
        for x in range(w):
            x0, x1 = max(0, x - r), min(w, x + r + 1)
            out[y * w + x] = (sat[y1 * (w + 1) + x1] - sat[y0 * (w + 1) + x1]
                              - sat[y1 * (w + 1) + x0] + sat[y0 * (w + 1) + x0])
    return out


def choose(cur, target, w, h, n, heat=None, wear=None, rng=random):
    """Pick up to n dots to flip (indexes where cur differs from target), best first: most error
    around them, not warm from recent flips, less wear; picks in one batch keep apart."""
    diff = bytes(a ^ b for a, b in zip(cur, target))
    wrong = [i for i, d in enumerate(diff) if d]
    if not wrong or n <= 0:
        return []
    density = box_sums(diff, w, h, RADIUS)
    heat = heat or {}
    top_wear = max(wear) if wear else 0

    def score(i):
        s = density[i] - 3.0 * heat.get(i, 0)
        if top_wear:
            s -= 0.5 * wear[i] / top_wear  # under one dot of density: it only decides near-ties
        return s + rng.random() * 0.25

    ranked = sorted(wrong, key=score, reverse=True)
    picked, near = [], set()
    for i in ranked:
        if i in near:
            continue
        picked.append(i)
        if len(picked) == n:
            return picked
        bx, by = i % w, i // w
        near.update(y * w + x for y in range(max(0, by - HEAT_RADIUS), min(h, by + HEAT_RADIUS + 1))
                    for x in range(max(0, bx - HEAT_RADIUS), min(w, bx + HEAT_RADIUS + 1)))
    chosen = set(picked)
    picked += [i for i in ranked if i not in chosen][:n - len(picked)]
    return picked


@register
class SlowPaint(App):
    id = "slowpaint"
    title = "Slow painting"
    category = "Generative"
    description = ("Morphs from one picture to the next a few dots at a time, within a daily or hourly "
                   "flip budget: the board becomes a painting that changes over hours or days. "
                   "Areas that are most wrong change first, flips are spread out, and less-worn discs are preferred.")
    params = [
        P("source", "select", "pictures", "Pictures from",
          options=[["pictures", "Pictures"], ["slideshow", "A slideshow"], ["postcards", "Daily postcards"]]),
        P("gallery", "gallery", "", "Slideshow", show_if={"source": "slideshow"}),
        P("budget", "number", 1000, "Flip budget", min=10, max=100000, step=10),
        P("per", "select", "day", "Budget per", options=[["day", "Day"], ["hour", "Hour"]]),
        P("linger", "number", 60, "Keep a finished picture (minutes)", min=0, max=10080),
        P("shuffle", "checkbox", False, "Random order"),
    ]
    interval = 1.0

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.budget = max(1, int(float(self.p["budget"])))
        self.per = "hour" if self.p["per"] == "hour" else "day"
        self.size = self.w * self.h
        st = self._state()
        cur = st.get("cur")
        self.cur = self._unpack(cur) if cur else bytearray(self.size)
        self.used = int(st.get("used", 0))
        self.win = st.get("window")
        self.target_id = st.get("target")
        self.target_name = st.get("target_name", "")
        self.done_at = st.get("done_at")
        self.target = None
        self.heat = {}
        self.wear = None
        self.wear_at = 0
        self.saved_at = 0
        self.dirty = False
        self.error = None
        self.total_wrong = 0  # how far the current target was when it was picked

    # ---- state ----------------------------------------------------------------------

    def _key(self):
        return f"{self.p['source']}:{self.p.get('gallery') or ''}"

    def _state(self):
        try:
            return (self.ctx.store._load(STATE, {}) or {}).get(self._key(), {})
        except Exception:
            return {}

    def _unpack(self, hexbits):
        try:
            raw = bytes.fromhex(hexbits)
        except ValueError:
            return bytearray(self.size)
        out = bytearray(self.size)
        for i in range(min(self.size, len(raw) * 8)):
            out[i] = (raw[i >> 3] >> (7 - (i & 7))) & 1
        return out

    def _pack(self):
        raw = bytearray((self.size + 7) // 8)
        for i, v in enumerate(self.cur):
            if v:
                raw[i >> 3] |= 1 << (7 - (i & 7))
        return raw.hex()

    def save(self):
        try:
            data = self.ctx.store._load(STATE, {}) or {}
            data[self._key()] = {"cur": self._pack(), "used": self.used, "window": self.win, "target": self.target_id,
                                 "target_name": self.target_name, "done_at": self.done_at,
                                 "order": getattr(self, "order", None)}
            self.ctx.store._save(STATE, data)
        except Exception as e:
            log.warning("can't save the slow painting: %s", e)
        self.saved_at = time.time()
        self.dirty = False

    def stop(self):
        if self.dirty:
            self.save()

    # ---- pictures -----------------------------------------------------------------

    def pictures(self):
        """[(id, name)] of the source, in order."""
        store, src = self.ctx.store, self.p["source"]
        if src == "slideshow":
            try:
                items = store.gallery(self.p["gallery"])["items"]
            except Exception:
                return []
            return [(it["file"], it["file"].rsplit(".", 1)[0]) for it in items]
        if src == "postcards":
            days = (store._load("postcards.json", {}) or {}).get("days", {})
            return [(day, f"Postcard {day}") for day in sorted(days) if days[day].get("rows")]
        return [(s["id"], s["name"]) for s in sorted(store.list_scenes(), key=lambda s: s["id"])]

    def load(self, pid):
        """The picture as a bytearray of dots, or None."""
        store, src = self.ctx.store, self.p["source"]
        rows = None
        if src == "slideshow":
            from . import slideshow
            g = store.gallery(self.p["gallery"])
            frames = slideshow.convert(store.gallery_dir(self.p["gallery"]) / pid, g["settings"], self.w, self.h, first_only=True)
            return bytearray(frames[0][0].px) if frames else None
        if src == "postcards":
            rows = ((store._load("postcards.json", {}) or {}).get("days", {}).get(pid) or {}).get("rows")
        else:
            scene = store.get_scene(pid)
            rows = scene["frames"][0]["rows"] if scene and scene.get("frames") else None
        if not rows:
            return None
        out = bytearray(self.size)
        for y, r in enumerate(rows[:self.h]):
            for x, ch in enumerate(r[:self.w]):
                out[y * self.w + x] = ch == "1"
        return out

    def next_target(self, now):
        pics = self.pictures()
        if not pics:
            self.error = {"pictures": "No pictures in Pictures yet", "slideshow": "The slideshow is empty",
                          "postcards": "No postcards yet"}.get(self.p["source"], "No pictures")
            return False
        ids = [p for p, _ in pics]
        if self.p["shuffle"]:
            choices = [p for p in ids if p != self.target_id] or ids
            pid = random.choice(choices)
        else:
            pid = ids[(ids.index(self.target_id) + 1) % len(ids)] if self.target_id in ids else ids[0]
        try:
            img = self.load(pid)
        except Exception as e:
            log.warning("slow painting: can't load %s: %s", pid, e)
            img = None
        if img is None:
            self.error = f"Can't load {pid}"
            self.target_id = pid  # skip it next time
            return False
        self.error = None
        self.target_id, self.target = pid, img
        self.target_name = dict(pics)[pid]
        self.done_at = None
        self.total_wrong = max(1, sum(a != b for a, b in zip(self.cur, img)))
        self.heat = {}
        self.dirty = True
        return True

    def ensure_target(self, now):
        if self.target is not None:
            return True
        if self.target_id:  # carry on with the saved target
            try:
                img = self.load(self.target_id)
            except Exception:
                img = None
            if img is not None:
                self.target = img
                self.total_wrong = max(1, sum(a != b for a, b in zip(self.cur, img)))
                return True
        return self.next_target(now)

    def load_wear(self, now):
        if now - self.wear_at < 600:
            return
        self.wear_at = now
        try:
            saved = self.ctx.store._load("wear.json", {}) or {}
            counts = saved.get("counts") or []
            self.wear = counts if saved.get("width") == self.w and len(counts) == self.size else None
        except Exception:
            self.wear = None

    # ---- frames -------------------------------------------------------------------

    def wrong(self):
        return sum(a != b for a, b in zip(self.cur, self.target)) if self.target is not None else 0

    def progress(self):
        if self.target is None:
            return 0.0
        return max(0.0, 1 - self.wrong() / self.total_wrong) if self.total_wrong else 1.0

    def step(self, now):
        """Spend what the budget allows right now. Returns the dots flipped."""
        key, _, length = window(now, self.per)
        if key != self.win:
            self.win, self.used = key, 0
        if not self.ensure_target(now):
            return []
        if self.done_at is None and self.wrong() == 0:
            self.done_at = now
            self.dirty = True
        if self.done_at is not None:
            if now - self.done_at < float(self.p["linger"]) * 60 or not self.next_target(now):
                return []
        can = min(allowance(self.budget, self.per, now) - self.used,
                  max(1, int(self.budget * CATCH_UP / length)))
        if can <= 0:
            return []
        self.load_wear(now)
        flips = choose(self.cur, self.target, self.w, self.h, can, self.heat, self.wear)
        # the heat map remembers roughly the last HEAT_FLIPS flips: their neighbours wait their turn
        cool = len(flips)
        self.heat = {i: v - cool for i, v in self.heat.items() if v > cool}
        for i in flips:
            self.cur[i] ^= 1
            bx, by = i % self.w, i // self.w
            for y in range(max(0, by - HEAT_RADIUS), min(self.h, by + HEAT_RADIUS + 1)):
                for x in range(max(0, bx - HEAT_RADIUS), min(self.w, bx + HEAT_RADIUS + 1)):
                    self.heat[y * self.w + x] = min(self.heat.get(y * self.w + x, 0) + HEAT_FLIPS, 3 * HEAT_FLIPS)
        self.used += len(flips)
        if flips:
            self.dirty = True
        if self.dirty and time.time() - self.saved_at > SAVE_EVERY:
            self.save()
        return flips

    def render(self, c, now):
        self.step(now)
        c.px[:] = self.cur

    def describe(self):
        if self.error and self.target is None:
            return f"Slow painting ({self.error})"
        unit = "today" if self.per == "day" else "this hour"
        what = f"{round(self.progress() * 100)}% of {self.target_name}" if self.target is not None else "starting"
        state = ", finished" if self.done_at is not None else ""
        return f"Slow painting ({what}{state}; {self.used} of {self.budget} flips {unit})"
