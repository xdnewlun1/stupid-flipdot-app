"""Flipdot-native apps where the click of the discs is the point: a percussion layer that plays along with the
music, split-flap text, a mechanical clock that ticks and chimes, rain you hear more than see, a pendulum wave,
kitchen / Pomodoro timers with a clattering alarm, and an ON AIR sign driven by the calendar.

Each one keeps its flip rate deliberate (that's the volume) and goes quiet in quiet hours.
"""

import datetime as dt
import math
import random
import urllib.parse

import audio_signal
import splitflap
import timers as timers_mod
from canvas import Canvas
from fonts import MONO, NORMAL, SMALL

from .base import App, NeedsSetup, P, Widget, http_get, register
from .react import _keep_sampling, quiet_now, reactive
from .visualizer import LIVE_FPS


def ring_order(w, h, start_x=None):
    """The border dots in clockwise order, starting at the top edge (at start_x, default the left corner)."""
    pts = [(x, 0) for x in range(w)] + [(w - 1, y) for y in range(1, h)]
    pts += [(x, h - 1) for x in range(w - 2, -1, -1)] + [(0, y) for y in range(h - 2, 0, -1)]
    if start_x:
        pts = pts[start_x:] + pts[:start_x]
    return pts


def _quiet(app):
    try:
        return quiet_now(app.ctx.store)
    except Exception:
        return False


# ---- 1. Percussion -------------------------------------------------------------------------------------------

WHERE = [["border", "Border (a frame that plays around anything)"], ["corners", "Corners"],
         ["board", "Whole board"]]


@register
class Percussion(App):
    id = "percussion"
    title = "Percussion (plays along)"
    category = "Widgets"
    description = ("The board clicks along with the music: a few dots flip on each hi-hat, a bigger block on each "
                   "snare and beat, and a full sweep on the downbeat, sized by how loud each part is. Timed like the "
                   "music visualizer, a constant Board delay behind the speakers. Best as a layer (Invert blend) over "
                   "a clock or text.")
    steady_timing = True  # the board keeps every flip the same time behind the music (see flipdot.Board.steady)
    params = [
        P("where", "select", "border", "Where it plays", options=WHERE),
        P("thickness", "number", 2, "Border thickness (dots)", min=1, max=4, show_if={"where": "border"}),
        P("hats", "checkbox", True, "Hi-hats: a few dots"),
        P("snares", "checkbox", True, "Snares and claps: a block"),
        P("kicks", "checkbox", True, "Beats: a block"),
        P("bar", "number", 4, "Full sweep every N beats (0 = never)", min=0, max=16),
        P("offbeats", "checkbox", True, "Off-beat ticks when the groove swings"),
        P("size", "number", 100, "Size (%)", min=25, max=200, step=25),
        P("fallback", "select", "none", "Without live music",
          options=[["none", "Stay still"], ["beat", "Play along with a generated beat (120 BPM)"]]),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.pic = bytearray(self.w * self.h)
        self.rnd = random.Random()
        self.counts = None
        self.beats = 0
        self.flips = 0
        self.sweep = None  # [order of dot lists, start, duration, done, clear]
        self.prev_phase = None
        self.interval = 1 / LIVE_FPS
        t = max(1, min(4, int(self.p["thickness"] or 2)))
        self.ring = [self._spoke(x, y, t) for x, y in ring_order(self.w, self.h)]
        cw, ch = max(4, self.w // 6), max(3, self.h // 4)
        self.corners = [(0, 0, cw, ch), (self.w - cw, 0, cw, ch), (self.w - cw, self.h - ch, cw, ch), (0, self.h - ch, cw, ch)]
        self.corner_i = 0

    def _spoke(self, x, y, t):
        """The dots across the border at one point of it (t thick)."""
        w, h = self.w, self.h
        out = set()
        for k in range(t):
            if y == 0:
                out.add((x, k))
            if y == h - 1:
                out.add((x, h - 1 - k))
            if x == 0:
                out.add((k, y))
            if x == w - 1:
                out.add((w - 1 - k, y))
        return sorted(out)

    # ---- flips ----

    def toggle(self, dots):
        w, pic = self.w, self.pic
        for x, y in dots:
            if 0 <= x < self.w and 0 <= y < self.h:
                pic[y * w + x] ^= 1
                self.flips += 1

    def rect(self, x, y, w, h):
        return [(xx, yy) for yy in range(int(y), int(y + h)) for xx in range(int(x), int(x + w))]

    def segment(self, start, length):
        n = len(self.ring)
        seen, out = set(), []
        for i in range(int(length)):
            for d in self.ring[(start + i) % n]:
                if d not in seen:
                    seen.add(d)
                    out.append(d)
        return out

    def scaled(self, energy, lo, hi):
        e = max(0.0, min(1.0, energy)) * float(self.p["size"] or 100) / 100
        return lo + (hi - lo) * min(1.5, e)

    def hat(self, energy):
        """A few dots: hats and off-beat ticks."""
        n = max(1, round(self.scaled(energy, 1, 5)))
        where = self.p["where"]
        if where == "border":
            self.toggle(self.segment(self.rnd.randrange(len(self.ring)), n))
        elif where == "corners":
            x0, y0, cw, ch = self.rnd.choice(self.corners)
            self.toggle([(x0 + self.rnd.randrange(cw), y0 + self.rnd.randrange(ch)) for _ in range(n)])
        else:
            self.toggle([(self.rnd.randrange(self.w), self.rnd.randrange(self.h)) for _ in range(n)])

    def block(self, energy, big):
        """Snares (big) and beats: a block sized by how loud that part is."""
        where = self.p["where"]
        if where == "border":
            n = len(self.ring)
            length = round(self.scaled(energy, n / 16, n / 4 if big else n / 8))
            start = self.rnd.randrange(n) if big else (self.beats % 4) * n // 4  # beats walk round the frame
            self.toggle(self.segment(start, length))
        elif where == "corners":
            k = self.corner_i % 4
            self.corner_i += 1
            picks = [k, (k + 2) % 4] if big else [k]
            for i in picks:
                x0, y0, cw, ch = self.corners[i]
                f = self.scaled(energy, 0.5, 1.0)
                bw, bh = max(2, round(cw * min(1, f))), max(2, round(ch * min(1, f)))
                self.toggle(self.rect(x0 + (cw - bw if x0 else 0), y0 + (ch - bh if y0 else 0), bw, bh))
        else:
            if big:
                bw, bh = round(self.scaled(energy, 8, 24)), round(self.scaled(energy, 5, 16))
                self.toggle(self.rect(self.rnd.randrange(max(1, self.w - bw)), self.rnd.randrange(max(1, self.h - bh)),
                                      bw, bh))
            else:
                bw = round(self.scaled(energy, 2, 8))
                self.toggle(self.rect(self.rnd.randrange(max(1, self.w - bw)), 0, bw, self.h))

    def start_sweep(self, now, beat_s):
        """The downbeat: the whole region flips as a fast wipe (round the frame, or across the board)."""
        where = self.p["where"]
        if where == "border":
            order = self.ring
        elif where == "corners":
            order = [sum((self.rect(*c) for c in self.corners), [])]
        else:
            order = [[(x, y) for y in range(self.h)] for x in range(self.w)]
        dur = max(2 / LIVE_FPS, min(0.25, (beat_s or 0.5) / 3))
        # the whole board wipes clean (what's lit flips back), so a bar of blocks never piles up into noise
        self.sweep = [order, now, dur, 0, where == "board"]

    def run_sweep(self, now):
        order, t0, dur, done, clear = self.sweep
        upto = len(order) if now - t0 >= dur else int(len(order) * (now - t0) / dur) + 1
        for dots in order[done:upto]:
            self.toggle([(x, y) for x, y in dots if self.pic[y * self.w + x]] if clear else dots)
        self.sweep[3] = upto
        if upto >= len(order):
            self.sweep = None

    # ---- frames ----

    def render(self, c, now):
        store = self.ctx.store
        fallback = "beat" if self.p["fallback"] == "beat" else None
        if _quiet(self):
            self.counts, self.sweep = None, None  # quiet hours: hold still
            c.px[:] = self.pic
            self.interval = 1.0
            return
        r = audio_signal.reading(store, fallback, now)
        if not (r["live"] or r["generated"]):
            self.counts = None
            c.px[:] = self.pic
            self.interval = 0.25
            return
        counts = (r["beat_count"], dict(r["hit_count"]))
        if self.counts is not None:
            new_beat = counts[0] != self.counts[0]
            hits = {k for k, n in counts[1].items() if n != self.counts[1].get(k)}
            bpm = r["bpm"] if r["bpm_confidence"] >= 0.3 else 0
            beat_s = 60 / bpm if bpm else None
            if new_beat:
                self.beats += 1
            bar = int(self.p["bar"] or 0)
            down = new_beat and bar > 0 and (r.get("downbeat") or (self.beats % bar == 1 if bar > 1 else True))
            if down:
                self.start_sweep(now, beat_s)
            elif new_beat and self.p["kicks"]:
                self.block(r["bass"], big=False)
            if "mid" in hits and self.p["snares"] and not down:
                self.block(r["mid"], big=True)
            ticked = False
            if "high" in hits and self.p["hats"]:
                self.hat(r["high"])
                ticked = True
            ph = r.get("beat_phase")
            if (self.p["offbeats"] and not ticked and ph is not None and self.prev_phase is not None
                    and r.get("groove", 0) >= 0.25 and self.prev_phase < 0.5 <= ph):
                self.hat(r["high"] * 0.5)
            self.prev_phase = ph
        self.counts = counts
        if self.sweep is not None:
            self.run_sweep(now)
        c.px[:] = self.pic
        # draw next exactly when the link's next analysis frame comes due, like the visualizer
        self.interval = audio_signal.steady_interval(store, now, LIVE_FPS) or 1 / LIVE_FPS
        if fallback:
            _keep_sampling(store, fallback)

    def describe(self):
        return f"Percussion: {self.beats} beats"


# ---- 2. Split-flap text --------------------------------------------------------------------------------------

FLAP_SIZES = {  # (font, scale, cell width, cell height)
    "small": (SMALL, 1, 4, 6),
    "normal": (MONO, 1, 6, 8),
    "large": (MONO, 2, 12, 16),
}


def fill_placeholders(s, t, hour24=True):
    hm = t.strftime("%H:%M") if hour24 else (t.strftime("%I:%M").lstrip("0") or "12:00")
    return (s.replace("{time}", hm).replace("{date}", f"{t.strftime('%a %b').upper()} {t.day}")
            .replace("{day}", t.strftime("%A").upper()))


@register
class SplitFlap(App):
    id = "splitflap"
    title = "Split-flap board"
    category = "Text"
    description = ("Text on a departures board: each character flaps through the alphabet before it lands, one "
                   "after another along the line, and the clatter is the point. Separate pages with a blank line; "
                   "{time}, {date} and {day} fill themselves in.")
    params = [
        P("text", "textarea", "DEPARTURES\n{time} LONDON\nPLATFORM 4\nON TIME", "Text"),
        P("size", "select", "normal", options=[["small", "Small (21 x 7)"], ["normal", "Normal (14 x 5)"],
                                               ["large", "Large (7 x 2)"]]),
        P("align", "select", "left", options=[["left", "Left"], ["center", "Center"]]),
        P("tiles", "checkbox", False, "Flap tiles (white tiles, dark letters)"),
        P("hold", "number", 10, "Each page for (s)", min=2, max=600),
        P("flaps", "number", 15, "Flaps per second", min=4, max=30),
        P("stagger", "number", 60, "Delay between characters (ms)", min=0, max=500, step=10),
        P("hour24", "checkbox", True, "24-hour {time}"),
    ]
    interval = 0.04

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.font, self.scale, self.cw, self.ch = FLAP_SIZES.get(self.p["size"], FLAP_SIZES["normal"])
        self.cols, self.rows = self.w // self.cw, self.h // self.ch
        self.x0, self.y0 = (self.w - self.cols * self.cw) // 2, (self.h - self.rows * self.ch) // 2
        n = self.cols * self.rows
        self.shown = [" "] * n
        self.target = [" "] * n
        self.runs = [None] * n
        self.rnd = random.Random()
        self._glyphs = {}
        text = str(self.p["text"] or "").replace("\r", "")
        pages, cur = [], []
        for line in text.split("\n"):
            if line.strip() == "" and cur:
                pages.append(cur)
                cur = []
            elif line.strip() or cur:
                cur.append(line)
        if cur:
            pages.append(cur)
        self.pages = pages or [[""]]
        self.flap_s = 1 / max(4.0, min(30.0, float(self.p["flaps"] or 15)))
        self.flips = 0

    def glyph(self, ch):
        g = self._glyphs.get(ch)
        if g is None:
            gw, gh = self.cw - self.scale, self.ch - self.scale
            g = splitflap.glyph(ch.upper() if self.font is not SMALL else ch, gw, gh, font=self.font, scale=self.scale)
            if self.p["tiles"]:
                g.invert()
            g = self._glyphs[ch] = g
        return g

    def layout(self, lines):
        out = [" "] * (self.cols * self.rows)
        lines = lines[:self.rows]
        for r, line in enumerate(lines):
            s = line.upper()[:self.cols]
            pad = (self.cols - len(s)) // 2 if self.p["align"] == "center" else 0
            for i, ch in enumerate(s):
                out[r * self.cols + pad + i] = ch
        return out

    def render(self, c, now):
        hold = max(2.0, float(self.p["hold"] or 10))
        page = self.pages[int((now - self.started) / hold) % len(self.pages)]
        t = dt.datetime.fromtimestamp(now)
        want = self.layout([fill_placeholders(line, t, self.p["hour24"]) for line in page])
        quiet = _quiet(self)
        stagger = max(0.0, float(self.p["stagger"] or 0)) / 1000
        for i, ch in enumerate(want):
            if ch != self.target[i]:
                self.target[i] = ch
                row, col = divmod(i, self.cols)
                frm = self.runs[i].steps[-1] if self.runs[i] else self.shown[i]
                # quiet hours: one flap straight to the new letter, not the whole drum
                steps = splitflap.sequence(frm, ch, limit=1 if quiet else None)
                start = now + col * stagger + row * stagger * 2 + self.rnd.random() * 0.02
                start_pic = self.runs[i].frame(now) if self.runs[i] else self.glyph(self.shown[i])
                self.runs[i] = splitflap.Flapper(start_pic, steps, [self.glyph(s) for s in steps], start, self.flap_s)
        busy = False
        if self.p["tiles"]:
            for r in range(self.rows):
                for col in range(self.cols):
                    c.rect(self.x0 + col * self.cw, self.y0 + r * self.ch, self.cw - self.scale, self.ch - self.scale, fill=True)
        for i in range(len(want)):
            run = self.runs[i]
            if run is not None:
                pic = run.frame(now)
                if run.done(now):
                    self.shown[i], self.runs[i] = self.target[i], None
                else:
                    busy = True
            else:
                pic = self.glyph(self.shown[i])
            row, col = divmod(i, self.cols)
            x, y = self.x0 + col * self.cw, self.y0 + row * self.ch
            for gy in range(pic.h):
                base = (y + gy) * c.w + x
                c.px[base:base + pic.w] = pic.px[gy * pic.w:(gy + 1) * pic.w]
        self.interval = 0.03 if busy else 0.25

    def describe(self):
        t = dt.datetime.now()
        return f"Split-flap: {fill_placeholders(' '.join(self.pages[0]), t, self.p['hour24'])[:30]}"


# ---- 3. Mechanical clock -------------------------------------------------------------------------------------

@register
class MechClock(App):
    id = "mechclock"
    title = "Mechanical clock"
    category = "Clocks"
    description = ("A clock you hear like a wall clock: one disc runs round the border once a minute, a step each "
                   "second. On the hour it strikes the hour with sweep waves across the board (muted in quiet hours).")
    params = [
        P("hour24", "checkbox", False, "24-hour"),
        P("style", "select", "bead", "Seconds", options=[["bead", "A bead running round the border"],
                                                       ["fill", "The border filling up"]]),
        P("chime", "checkbox", True, "Strike the hours"),
        # longer than a sweep wave takes to cross the board, so each strike gets a wave of its own
        P("chime_gap", "number", 3.0, "Time between strikes (s)", min=1.0, max=10, step=0.5, show_if={"chime": True}),
        P("half_hour", "checkbox", False, "One strike on the half hour", show_if={"chime": True}),
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.ring = ring_order(self.w, self.h, start_x=self.w // 2)
        self.strikes = 0

    def chime_state(self, t, now):
        """(inverted, striking): where a chime is on the board. Each strike inverts the board (a sweep wave, as
        nothing else moves); an odd number ends with a quick un-invert right after the last one."""
        if not self.p["chime"] or _quiet(self):
            return False, False
        if t.minute == 0:
            n = t.hour % 12 or 12
        elif t.minute == 30 and self.p["half_hour"]:
            n = 1
        else:
            return False, False
        gap = max(1.0, float(self.p["chime_gap"] or 3))
        into = t.second + (now % 1)
        if into >= n * gap + 0.5:
            return False, False
        done = min(n, int(into // gap) + 1)
        self.strikes = done
        inverted = done % 2 == 1
        if done == n and inverted and into >= (n - 1) * gap + 0.6:
            inverted = False  # the damper: back to normal (columns together, a single clack)
        return inverted, True

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        inverted, striking = self.chime_state(t, now)
        s = t.strftime("%H:%M") if self.p["hour24"] else (t.strftime("%I:%M").lstrip("0") or "12:00")
        size = dict(w=14, h=27, t=3, gap=3)
        tw = c.seg_width(s, size["w"], size["t"], size["gap"])
        c.seg_text((c.w - tw) // 2, (c.h - size["h"]) // 2, s, **size)
        # the seconds: frozen at the top while it strikes, so each strike stands alone and gets the sweep wave
        sec = 0 if striking else t.second
        n = len(self.ring)
        upto = sec * n // 60
        if self.p["style"] == "fill":
            for x, y in self.ring[:upto + 1]:
                c.set(x, y)
        else:
            c.set(*self.ring[upto])
        if inverted:
            c.invert()
        frac = now % 1
        self.interval = 0.05 if striking else max(0.02, 1.0 - frac + 0.003)

    def describe(self):
        return self.title


# ---- 4. ASMR rain --------------------------------------------------------------------------------------------

def rain_rate(mm_per_h, code, top):
    """Drops a second for a rain rate: drizzle a few a second, a downpour `top`. 0 when it isn't raining."""
    from rules import weather_class
    mm = max(0.0, float(mm_per_h or 0))
    if mm <= 0:
        return 0.15 * top if code is not None and weather_class(int(code)) == "rain" else 0.0
    return top * min(1.0, 0.1 + 0.9 * math.log2(1 + 4 * mm) / math.log2(1 + 4 * 8))


@register
class AsmrRain(Widget):
    id = "asmrrain"
    title = "Rain on the roof"
    category = "Generative"
    description = ("Single discs flip at random, like rain on a tin roof: you hear it more than see it. The volume "
                   "is how many drops fall a second (capped in quiet hours). Follow the weather: it rains on the "
                   "board when it rains outside, as hard as it rains (Open-Meteo, Settings location).")
    params = [
        P("mode", "select", "fixed", "Rain", options=[["fixed", "Always (at the volume below)"],
                                                     ["weather", "When it rains outside, as hard as it rains"]]),
        P("volume", "number", 4, "Volume (drops a second; with the weather, at most)", min=0.2, max=30, step=0.2),
        P("quiet_volume", "number", 1, "Volume in quiet hours", min=0, max=10, step=0.2),
        P("linger", "number", 3, "Each drop stays (s)", min=0.3, max=20, step=0.1),
        P("location", "text", "", "Location (blank = Settings default)", show_if={"mode": "weather"}),
    ]
    interval = 0.05
    refresh = 600

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rnd = random.Random()
        self.drops = []  # [(dots, gone at)]
        self.next_at = None
        self.hidden = False
        self.drop_count = 0

    @classmethod
    def needs_setup(cls, store, params):
        if params.get("mode") == "weather" and not (params.get("location")
                                                   or (store.settings.get("weather") or {}).get("lat") is not None):
            return "a weather location (Settings, Location)"
        return None

    def fetch(self):
        settings = self.ctx.store.settings.get("weather") or {}
        if self.p["location"]:
            from .widgets import geocode
            hits = geocode(self.p["location"])
            if not hits:
                raise ValueError(f"location {self.p['location']!r} not found")
            loc = hits[0]
        elif settings.get("lat") is not None:
            loc = settings
        else:
            raise NeedsSetup("Set a location in Settings")
        q = urllib.parse.urlencode({"latitude": loc["lat"], "longitude": loc["lon"],
                                    "current": "precipitation,rain,showers,weather_code"})
        cur = http_get(f"https://api.open-meteo.com/v1/forecast?{q}", ttl=self.refresh - 30)["current"]
        return {"mm": max(float(cur.get("precipitation") or 0), float(cur.get("rain") or 0) + float(cur.get("showers") or 0)),
                "code": cur.get("weather_code")}

    def rate(self):
        top = max(0.0, float(self.p["volume"] or 0))
        if self.p["mode"] == "weather":
            if self.data is None:
                return 0.0
            top = rain_rate(self.data["mm"], self.data["code"], top)
        if _quiet(self):
            cap = (self.ctx.store.settings.get("quiet_hours") or {}).get("max_flips") or 150
            # every drop is ~3 flips (on, then off); stay well inside the quiet-hours flip budget
            top = min(top, float(self.p["quiet_volume"] or 0), float(cap) / 6)
        return top

    def render(self, c, now):
        if self.p["mode"] == "weather":
            self.poll()
        rate = self.rate()
        linger = max(0.3, float(self.p["linger"] or 3))
        self.drops = [d for d in self.drops if d[1] > now]
        if rate <= 0:
            self.next_at = None
        else:
            if self.next_at is None:
                self.next_at = now + self.rnd.expovariate(rate)
            while self.next_at <= now:  # Poisson arrivals: rain doesn't keep time
                x, y = self.rnd.randrange(self.w), self.rnd.randrange(self.h)
                dots = [(x, y)] + ([(x, y + 1)] if self.rnd.random() < 0.3 and y + 1 < self.h else [])
                self.drops.append((dots, now + linger * self.rnd.uniform(0.6, 1.4)))
                self.drop_count += 1
                self.next_at += self.rnd.expovariate(rate)
        for dots, _ in self.drops:
            for x, y in dots:
                c.set(x, y)
        dry = self.p["mode"] == "weather" and rate <= 0 and not self.drops
        self.hidden = dry  # as a layer: nothing over what's below while it's dry
        if dry and self.data is not None and not _quiet(self):
            c.text_center(c.h // 2 - 2, "NO RAIN NOW", SMALL)
        elif dry and self.setup:
            c.text_center(c.h // 2 - 2, "SET A LOCATION", SMALL)
        self.interval = 0.05 if rate > 0 or self.drops else 1.0

    def describe(self):
        return f"Rain: {self.rate():.1f} drops/s"


# ---- 5. Pendulum wave ----------------------------------------------------------------------------------------

@register
@reactive
class Pendulums(App):
    id = "pendulums"
    title = "Pendulum wave"
    category = "Generative"
    description = ("A row of pendulums, each a little faster than the next: they drift through travelling waves and "
                   "stripes, then all line up again and rest a moment, calm and quiet, before the next round.")
    params = [
        P("count", "select", "84", "Pendulums", options=[["84", "84 (one per column)"], ["42", "42"], ["28", "28"],
                                                       ["21", "21"]]),
        P("cycle", "number", 90, "They line up again every (s)", min=20, max=600),
        P("slowest", "number", 12, "Swings of the slowest per cycle", min=4, max=60),
        P("rest", "number", 4, "Rest when lined up (s)", min=0, max=30),
        P("style", "select", "bobs", options=[["bobs", "Bobs"], ["wave", "Joined into a wave"], ["strings", "On strings"]]),
    ]
    interval = 1 / 15

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.n = max(2, min(self.w, int(self.p["count"] or 84)))

    def positions(self, tau, amp):
        T = max(1.0, float(self.p["cycle"] or 90))
        k = max(1, int(self.p["slowest"] or 12))
        cy = (self.h - 1) / 2
        return [cy - amp * math.cos(2 * math.pi * (k + i) * tau / T) for i in range(self.n)]

    def render(self, c, now):
        m = self.music
        T = max(1.0, float(self.p["cycle"] or 90))
        rest = max(0.0, float(self.p["rest"] or 0))
        t = m.clock("t", now, 1.0)  # the music sets the pace (tempo / loudness); exact time without it
        into = t % (T + rest)
        resting = into < rest
        tau = 0.0 if resting else into - rest
        amp = (self.h - 3) / 2
        if m.enabled:
            amp *= max(0.4, min(1.0, 0.75 + 0.25 * m.level("bass") + 0.2 * m.pulse - 0.15 * m.presence))
        ys = self.positions(tau, amp)
        colw = self.w / self.n
        style = self.p["style"]
        prev = None
        for i, y in enumerate(ys):
            x0 = int(i * colw)
            x1 = max(x0 + 1, int((i + 1) * colw) - (1 if colw >= 3 else 0))
            yi = int(round(y))
            if style == "strings":
                for x in range(x0, x1):
                    c.line(x, 0, x, yi)
            for x in range(x0, x1):
                c.set(x, yi)
                if colw < 2:
                    c.set(x, yi + 1)  # one-column bobs are two dots tall so they read
            if style == "wave" and prev is not None:
                c.line(prev[0], prev[1], x0, yi)
            prev = (x1 - 1, yi)
        quiet = _quiet(self)
        self.interval = (0.5 if resting else (0.2 if quiet else 1 / 15))

    def describe(self):
        return self.title


# ---- 6. Timers -----------------------------------------------------------------------------------------------

CLATTER = [0.0, 0.14, 0.28, 0.75, 0.89, 1.03, 1.5, 1.64, 1.78, 2.25, 2.39, 2.53]  # three-flip bursts
CLATTER_S = 3.0


@register
class TimersApp(App):
    id = "timers"
    title = "Timers"
    category = "Clocks"
    description = ("Up to four named kitchen timers at once, or a Pomodoro (25 min work, 5 min break), as bars "
                   "and countdowns. At zero the board clatters, then flashes the name. Start them here, with "
                   "POST /api/timers, or by voice through Home Assistant (MQTT).")
    params = [
        P("controls", "timers", None, "Timers"),
        P("presets", "textarea", "Pasta 8:00\nTea 3:00\nEggs 7:00", "Quick starts (one per line)"),
        P("alarm", "select", "clatter", "Alarm", options=[["clatter", "Clatter, then flash the name"],
                                                         ["flash", "Just flash the name (quieter)"]]),
        P("work", "number", 25, "Pomodoro work (min)", min=1, max=180),
        P("break", "number", 5, "Pomodoro break (min)", min=1, max=60),
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.timers = timers_mod.for_store(ctx.store)

    def draw_alarm(self, c, t, now):
        into = now - t.get("rang_at", now)
        quiet = _quiet(self)
        if self.p["alarm"] == "clatter" and into < CLATTER_S:
            n = sum(1 for at in CLATTER if at <= into)
            if quiet:  # a gentler knock: only the border
                if n % 2:
                    for x, y in ring_order(c.w, c.h):
                        c.set(x, y)
            elif n % 2:
                c.clear(1)
            self.interval = 0.03
            return
        on = int(into / (1.0 if quiet else 0.5)) % 2 == 0
        name = t["name"].upper()
        scale = 2 if NORMAL.width(name) * 2 <= c.w - 2 else 1
        c.text_center(4 if scale == 2 else 8, name[:16], NORMAL, scale=scale)
        c.text_center(c.h - 12, "DONE" if t["kind"] == "timer" else ("BREAK!" if t["kind"] == "work" else "BACK TO WORK"), NORMAL)
        if on:
            c.invert()
        self.interval = 0.1

    def draw_row(self, c, t, y, h):
        left = t["left"]
        name = t["name"].upper()
        txt = timers_mod.fmt_left(left)
        paused = t["state"] == "paused"
        if t["kind"] in ("work", "break") and (self.timers.pomodoro.get("sessions") or 0):
            name = f"{name} {self.timers.pomodoro['sessions']}"
        if h >= 20:  # one timer: big countdown
            c.text_center(y + 1, name[:14], NORMAL)
            size = dict(w=9, h=17, t=2, gap=2)
            tw = c.seg_width(txt, size["w"], size["t"], size["gap"])
            c.seg_text((c.w - tw) // 2, y + 10, txt, **size)
            if paused:
                c.rect(1, y + 13, 2, 8, fill=True)
                c.rect(5, y + 13, 2, 8, fill=True)
            bar_y = y + h - 3
        else:
            tw = NORMAL.width(txt)
            c.text(c.w - tw, y, txt, NORMAL)
            room = c.w - tw - 3
            font = NORMAL if NORMAL.width(name) <= room else SMALL
            while name and font.width(name) > room:
                name = name[:-1]
            c.text(0, y + (0 if font is NORMAL else 1), name, font)
            if paused:
                c.rect(c.w - tw - 3, y + 1, 1, 5, fill=True)
            bar_y = y + 8
        frac = max(0.0, min(1.0, left / max(1, t["duration"])))
        c.rect(0, bar_y, max(0, round(c.w * frac)), 1 if h < 20 else 2, fill=True)

    def render(self, c, now):
        snap = self.timers.snapshot(now)
        items = snap["timers"]
        ring = [t for t in items if t["state"] == "ringing"]
        if ring:
            self.draw_alarm(c, ring[0], now)
            return
        if self.p.get("alarm_only"):  # (not a listed param: the server passes it when a timer rings elsewhere)
            return False  # shown over something else for the alarm: done
        items = items[:timers_mod.MAX_TIMERS]
        if not items:
            c.text_center(10, "NO TIMERS", NORMAL)
            c.text_center(22, "START ONE FROM THE APP,", SMALL)
            c.text_center(29, "THE API OR HOME ASSISTANT", SMALL)
            self.interval = 0.5
            return
        if len(items) == 1:
            self.draw_row(c, items[0], 2, c.h - 2)
        else:
            rows = 2 if len(items) == 2 else len(items)
            h = c.h // rows
            for i, t in enumerate(items):
                self.draw_row(c, t, i * h + (h - 9) // 2, 9)
        # wake just after the next second ticks over, so the countdown changes on the second
        nxt = min((t["left"] % 1) or 1.0 for t in items if t["state"] == "running") if any(
            t["state"] == "running" for t in items) else 0.5
        self.interval = max(0.02, min(0.5, nxt + 0.01))

    def describe(self):
        n = sum(1 for t in self.timers.snapshot()["timers"] if t["state"] != "ringing")
        return f"Timers: {n} running" if n else "Timers"


# ---- 7. On air -----------------------------------------------------------------------------------------------

def matches(title, keyword):
    words = [k.strip().lower() for k in str(keyword or "").split(",") if k.strip()]
    return not words or any(w in str(title or "").lower() for w in words)


def meetings_now(events, now, keyword="", days=1):
    """[(start, end, title)] of timed events happening at `now` (an aware datetime) whose title has the keyword
    (comma-separated: any of them; blank: every event). All-day events don't count as meetings."""
    from .ical import upcoming
    out = []
    for start, end, title, all_day in upcoming(events, now, days=days, limit=50):
        if not all_day and start <= now < end and matches(title, keyword):
            out.append((start, end, title))
    return out


def next_meeting(events, now, keyword="", days=7):
    from .ical import upcoming
    for start, end, title, all_day in upcoming(events, now, days=days, limit=50):
        if not all_day and start > now and matches(title, keyword):
            return start, end, title
    return None


@register
class OnAir(Widget):
    id = "onair"
    title = "On air"
    category = "Widgets"
    description = ("ON AIR, with the meeting and when it ends, while a calendar event is on (optionally only events "
                   "with a word like \"meeting\" in the title). As the top layer it stays out of the way between "
                   "meetings; Automations can also bring it up when a meeting starts.")
    params = [
        P("urls", "textarea", "", "iCal links (.ics), one per line"),
        P("keyword", "text", "", "Only events with (comma-separated; blank = any)"),
        P("label", "text", "ON AIR", "Sign"),
        P("idle", "select", "hide", "Between meetings", options=[["hide", "Nothing (as a layer: what's below)"],
                                                                ["next", "The next meeting"]]),
        P("hour24", "checkbox", False, "24-hour times"),
        P("refresh_minutes", "number", 5, "Check the calendar every (minutes)", min=1, max=120),
    ]
    interval = 1.0

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(60, float(self.p["refresh_minutes"] or 5) * 60)
        self.events = None
        self.hidden = False
        self.live = None

    @classmethod
    def needs_setup(cls, store, params):
        return None if str(params.get("urls") or "").strip() else "an iCal link in the app settings"

    def fetch(self):
        from .ical import parse_ics
        urls = [u.strip() for u in str(self.p["urls"]).splitlines() if u.strip()]
        if not urls:
            raise NeedsSetup("Add an iCal link in the app settings")
        events, errors = [], []
        for url in urls:
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            try:
                events += parse_ics(http_get(url, ttl=self.refresh - 5, raw=True, timeout=20).decode("utf-8", "replace"))
            except Exception as e:
                errors.append(str(e))
        if errors and not events:
            raise ValueError(errors[0])
        return events

    def _time(self, t):
        return t.strftime("%H:%M") if self.p["hour24"] else t.strftime("%I:%M").lstrip("0") + ("am" if t.hour < 12 else "pm")

    def render(self, c, now):
        self.poll()
        if self.data is None:
            self.hidden = self.p["idle"] == "hide"
            if not self.hidden:
                return super().render(c, now)
            return
        t = dt.datetime.fromtimestamp(now).astimezone()
        on = meetings_now(self.data, t, self.p["keyword"])
        self.live = on[0] if on else None
        if self.live:
            self.hidden = False
            start, end, title = max(on, key=lambda e: e[1])  # back-to-back: until the last one ends
            label = str(self.p["label"] or "ON AIR").upper()[:7]
            scale = 2 if NORMAL.width(label) * 2 <= c.w - 6 else 1
            box_h = 7 * scale + 6
            c.rect(0, 0, c.w, box_h, fill=True)
            c.text_center(3, label, NORMAL, v=0, scale=scale)
            title = title.upper()
            while title and SMALL.width(title) > c.w:
                title = title[:-1]
            c.text_center(box_h + 3, title, SMALL)
            c.text_center(c.h - 8, "UNTIL " + self._time(end).upper(), NORMAL)
            self.interval = 5.0
            return
        if self.p["idle"] == "hide":
            self.hidden = True
            self.interval = 15.0
            return
        self.hidden = False
        nxt = next_meeting(self.data, t, self.p["keyword"])
        if nxt is None:
            c.text_center(c.h // 2 - 4, "NO MEETINGS", NORMAL)
        else:
            start, end, title = nxt
            c.text_center(4, "NEXT", SMALL)
            title = title.upper()
            while title and SMALL.width(title) > c.w:
                title = title[:-1]
            c.text_center(14, title, SMALL)
            when = self._time(start).upper() if start.date() == t.date() else start.strftime("%a ").upper() + self._time(start).upper()
            c.text_center(26, when, NORMAL)
        self.interval = 15.0

    def describe(self):
        return f"On air: {self.live[2]}" if self.live else self.title
