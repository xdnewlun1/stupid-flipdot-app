"""More clocks (apps/pack_clocks2.py): a word clock, a binary clock, a rolling-ball marble clock and a sun path
clock. Each tells the time its own way on an 84 x 42 board; the marble clock makes its flips on purpose (the
balls drop once a minute) and keeps still in quiet hours."""

import datetime as dt
import math

from fonts import NORMAL, SMALL

from .base import App, NeedsSetup, P, register
from .pack_live import draw_moon, moon_position, sun_position
from .react import quiet_now


def _quiet(app):
    try:
        return quiet_now(app.ctx.store)
    except Exception:
        return False


# ---- 1. Word clock -------------------------------------------------------------------------------------------
# A 14 x 7 grid of 5 x 5 capitals on a 6-dot pitch: 84 x 42 exactly. Two layouts, since both "IT IS" and
# "O'CLOCK" don't fit: one opens with IT IS, the other closes the hour with O'CLOCK.

GLYPHS = {  # 5 x 5 capitals
    "A": "01110 10001 11111 10001 10001", "B": "11110 10001 11110 10001 11110", "C": "01111 10000 10000 10000 01111",
    "D": "11110 10001 10001 10001 11110", "E": "11111 10000 11110 10000 11111", "F": "11111 10000 11110 10000 10000",
    "G": "01111 10000 10011 10001 01111", "H": "10001 10001 11111 10001 10001", "I": "11111 00100 00100 00100 11111",
    "J": "00111 00010 00010 10010 01100", "K": "10001 10010 11100 10010 10001", "L": "10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10001 10001", "N": "10001 11001 10101 10011 10001", "O": "01110 10001 10001 10001 01110",
    "P": "11110 10001 11110 10000 10000", "Q": "01110 10001 10101 10010 01101", "R": "11110 10001 11110 10010 10001",
    "S": "01111 10000 01110 00001 11110", "T": "11111 00100 00100 00100 00100", "U": "10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 01010 00100", "W": "10001 10001 10101 11011 10001", "X": "10001 01010 00100 01010 10001",
    "Y": "10001 01010 00100 00100 00100", "Z": "11111 00010 00100 01000 11111",
}
GLYPHS = {k: v.split() for k, v in GLYPHS.items()}

# Each layout: the rows of letters, and where each word sits: (row, first column, length). The filler letters
# between words are fixed, so a faint grid looks the same every day.
LAYOUTS = {
    "itis": {
        "rows": ["ITKISBQUARTERD", "TWENTYFIVEZTEN", "HALFSPASTRTOGM", "ONETWOTHREESIX",
                 "FOURFIVESEVENP", "EIGHTNINETENYJ", "ELEVENTWELVEWC"],
        "words": {"IT": (0, 0, 2), "IS": (0, 3, 2), "QUARTER": (0, 6, 7), "TWENTY": (1, 0, 6), "MFIVE": (1, 6, 4),
                  "MTEN": (1, 11, 3), "HALF": (2, 0, 4), "PAST": (2, 5, 4), "TO": (2, 10, 2),
                  "ONE": (3, 0, 3), "TWO": (3, 3, 3), "THREE": (3, 6, 5), "SIX": (3, 11, 3), "FOUR": (4, 0, 4),
                  "FIVE": (4, 4, 4), "SEVEN": (4, 8, 5), "EIGHT": (5, 0, 5), "NINE": (5, 5, 4), "TEN": (5, 9, 3),
                  "ELEVEN": (6, 0, 6), "TWELVE": (6, 6, 6)},
    },
    "oclock": {
        "rows": ["QUARTERBTWENTY", "FIVEKTENDHALFZ", "PASTGTORONETWO", "THREEFOURFIVEM",
                 "SIXSEVENEIGHTP", "NINETENELEVENY", "TWELVEJOCLOCKW"],
        "words": {"QUARTER": (0, 0, 7), "TWENTY": (0, 8, 6), "MFIVE": (1, 0, 4), "MTEN": (1, 5, 3), "HALF": (1, 9, 4),
                  "PAST": (2, 0, 4), "TO": (2, 5, 2), "ONE": (2, 8, 3), "TWO": (2, 11, 3), "THREE": (3, 0, 5),
                  "FOUR": (3, 5, 4), "FIVE": (3, 9, 4), "SIX": (4, 0, 3), "SEVEN": (4, 3, 5), "EIGHT": (4, 8, 5),
                  "NINE": (5, 0, 4), "TEN": (5, 4, 3), "ELEVEN": (5, 7, 6), "TWELVE": (6, 0, 6), "OCLOCK": (6, 7, 6)},
    },
}
HOURS = ["TWELVE", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN"]
MINUTES = {0: [], 5: ["MFIVE", "PAST"], 10: ["MTEN", "PAST"], 15: ["QUARTER", "PAST"], 20: ["TWENTY", "PAST"],
           25: ["TWENTY", "MFIVE", "PAST"], 30: ["HALF", "PAST"], 35: ["TWENTY", "MFIVE", "TO"], 40: ["TWENTY", "TO"],
           45: ["QUARTER", "TO"], 50: ["MTEN", "TO"], 55: ["MFIVE", "TO"]}


def words_for(hour, minute, layout="itis", nearest=True):
    """The lit words for a time, in reading order (minute words carry an M so FIVE and TEN the minute aren't FIVE
    and TEN the hour). nearest: round to the nearest five minutes (7:03 -> five past); else floor."""
    m = (minute + 2) // 5 * 5 if nearest else minute // 5 * 5
    h = hour
    if m == 60:
        m, h = 0, h + 1
    if m > 30:
        h += 1
    out = ["IT", "IS"] if layout == "itis" else []
    out += MINUTES[m]
    out.append(HOURS[h % 12])
    if m == 0 and layout == "oclock":
        out.append("OCLOCK")
    return out


def sentence(hour, minute, layout="itis", nearest=True):
    return " ".join(w.lstrip("M") if w in ("MFIVE", "MTEN") else w for w in words_for(hour, minute, layout, nearest))


@register
class WordClock(App):
    id = "wordclock"
    title = "Word clock"
    category = "Clocks"
    description = ("The time in words, lit up in a grid of letters: IT IS TWENTY FIVE PAST SEVEN. To the nearest "
                   "five minutes, with dots along the bottom for the minutes in between if you like.")
    params = [
        P("layout", "select", "itis", "Wording", options=[["itis", "IT IS ... (opens with IT IS)"],
                                                           ["oclock", "... O'CLOCK (closes the hour with O'CLOCK)"]]),
        P("dots", "checkbox", True, "Minute dots along the bottom (1 to 4)"),
        P("faint", "checkbox", False, "Show the unlit letters faintly (every other dot)"),
    ]
    interval = 1.0

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.layout = LAYOUTS.get(self.p["layout"], LAYOUTS["itis"])

    def glyph(self, c, col, row, ch, faint=False):
        x0, y0 = col * 6, row * 6
        g = GLYPHS.get(ch)
        if g is None:
            return
        for gy, line in enumerate(g):
            for gx, bit in enumerate(line):
                if bit == "1" and (not faint or (gx + gy) % 2 == 0):
                    c.set(x0 + gx, y0 + gy)

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        words = words_for(t.hour, t.minute, self.p["layout"], nearest=not self.p["dots"])
        lit = set()
        for w in words:
            row, col, n = self.layout["words"][w]
            lit |= {(row, col + k) for k in range(n)}
        for row, line in enumerate(self.layout["rows"]):
            for col, ch in enumerate(line):
                if (row, col) in lit:
                    self.glyph(c, col, row, ch)
                elif self.p["faint"]:
                    self.glyph(c, col, row, ch, faint=True)
        if self.p["dots"]:
            for k in range(t.minute % 5):
                c.set(36 + k * 4, 41)
                c.set(37 + k * 4, 41)
        self.interval = max(0.5, 60 - t.second - (now % 1) + 0.05)

    def describe(self):
        t = dt.datetime.now()
        return sentence(t.hour, t.minute, self.p["layout"], nearest=not self.p["dots"]).capitalize()


# ---- 2. Binary clock -----------------------------------------------------------------------------------------

@register
class BinaryClock(App):
    id = "binaryclock"
    title = "Binary clock"
    category = "Clocks"
    description = ("The time in binary: six columns of BCD digits (8-4-2-1 from the top), or three rows of plain "
                   "binary for hours, minutes and seconds. Lit dots are 1, hollow dots 0.")
    params = [
        P("style", "select", "bcd", "Style", options=[["bcd", "BCD columns (one per digit)"],
                                                     ["rows", "Binary rows (hours, minutes, seconds)"]]),
        P("seconds", "checkbox", True, "Show seconds"),
        P("hour24", "checkbox", True, "24-hour"),
        P("labels", "checkbox", True, "Labels (the digits, or H M S)"),
    ]
    interval = 0.25

    def digits(self, t):
        h = t.hour if self.p["hour24"] else (t.hour % 12 or 12)
        parts = [h, t.minute] + ([t.second] if self.p["seconds"] else [])
        return parts

    def dot(self, c, x, y, r, on):
        c.circle(x, y, r, fill=True) if on else c.circle(x, y, r)

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        parts = self.digits(t)
        if self.p["style"] == "bcd":
            cols = [d for v in parts for d in (v // 10, v % 10)]
            n = len(cols)
            pitch = 12 if n == 6 else 16
            r = 3
            x0 = (c.w - pitch * (n - 1)) // 2
            top = 5 if self.p["labels"] else 8
            for i, d in enumerate(cols):
                x = x0 + i * pitch
                for bit in range(4):  # 8 4 2 1 from the top
                    self.dot(c, x, top + bit * 9, r, d >> (3 - bit) & 1)
                if self.p["labels"]:
                    c.text(x - 1, c.h - 5, str(d), SMALL)
            if n == 6 and self.p["labels"]:
                for k in (1, 2):  # the colons between hours, minutes and seconds
                    x = x0 + (2 * k - 1) * pitch + pitch // 2
                    c.set(x, 22)
                    c.set(x, 25)
        else:
            bits = 6
            names = ["H", "M", "S"]
            pitch = 11
            r = 3
            x0 = (c.w - pitch * (bits - 1)) // 2 + 4
            rows = len(parts)
            y0 = (c.h - (rows - 1) * 13) // 2
            for i, v in enumerate(parts):
                y = y0 + i * 13
                if self.p["labels"]:
                    c.text(x0 - 12, y - 2, names[i], SMALL)
                for b in range(bits):
                    self.dot(c, x0 + b * pitch, y, r, v >> (bits - 1 - b) & 1)
        self.interval = 0.25 if self.p["seconds"] else 1.0

    def describe(self):
        return self.title


# ---- 3. Marble clock -----------------------------------------------------------------------------------------
# A rolling-ball clock: once a minute a ball is lifted to the top and rolls into the minutes rail. The fifth ball
# tips that rail: four roll home and the fifth drops into the fives rail. The twelfth ball there tips it into the
# hours rail, whose twelfth ball sends them all home. One ball is fixed at the end of the hours rail, so the rail
# reads 1 to 12. Minutes = 5 x fives + ones.

ONES_MAX, FIVES_MAX, HOURS_MAX = 4, 11, 11
LIFT_X, TOP_Y = 3, 5
ONES_Y, FIVES_Y, HOURS_Y = 10, 22, 34         # ball centres on each rail
ONES_X0, FIVES_X0, HOURS_X0 = 78, 78, 76      # the slot nearest the stop; slots go left 4 dots apart
HOME_Y = 39                                   # the reservoir row


def counts_for(t):
    """{"hours", "fives", "ones"}: the balls on each rail at this time."""
    return {"hours": (t.hour % 12 - 1) % 12, "fives": t.minute // 5, "ones": t.minute % 5}


def advance(counts):
    """One minute on the mechanism: a ball into the ones rail, tipping the rails that fill. -> (new counts, tips):
    tips is the list of rails that tipped, in order."""
    c, tips = dict(counts), []
    c["ones"] += 1
    if c["ones"] > ONES_MAX:
        c["ones"] = 0
        tips.append("ones")
        c["fives"] += 1
        if c["fives"] > FIVES_MAX:
            c["fives"] = 0
            tips.append("fives")
            c["hours"] += 1
            if c["hours"] > HOURS_MAX:
                c["hours"] = 0
                tips.append("hours")
    return c, tips


def slot_x(rail, k):
    return {"ones": ONES_X0, "fives": FIVES_X0, "hours": HOURS_X0}[rail] - 4 * k


def rail_y(rail):
    return {"ones": ONES_Y, "fives": FIVES_Y, "hours": HOURS_Y}[rail]


def _lerp(a, b, f):
    return a + (b - a) * f


class Move:
    """A ball following waypoints [(x, y, at)] with an easing per leg ("roll" steady, "fall" speeding up)."""

    def __init__(self, points, eases):
        self.points, self.eases = points, eases
        self.t0, self.t1 = points[0][2], points[-1][2]

    def at(self, t):
        if t <= self.t0:
            return self.points[0][:2]
        for (x0, y0, a), (x1, y1, b), ease in zip(self.points, self.points[1:], self.eases):
            if t <= b:
                f = (t - a) / max(1e-6, b - a)
                f = f * f if ease == "fall" else f
                return _lerp(x0, x1, f), _lerp(y0, y1, f)
        return self.points[-1][:2]


def plan_minute(counts, t0):
    """The moves for one minute's drop, starting at t0, and when each rail's count changes:
    -> (moves, changes: [(time, rail, new count)], new counts, end time)"""
    moves, changes = [], []
    new, tips = advance(counts)
    # 1. lift and roll along the top ramp into the ones rail
    k = counts["ones"]  # the slot it lands in (a fifth ball lands beyond the rail's four, then tips it)
    land = (slot_x("ones", k), ONES_Y, t0 + 2.2)
    moves.append(Move([(LIFT_X, HOME_Y, t0), (LIFT_X, TOP_Y, t0 + 1.0), (40, ONES_Y - 1, t0 + 1.7), land],
                      ["roll", "roll", "roll"]))
    t = t0 + 2.2
    cur = dict(counts)
    if "ones" not in tips:
        changes.append((t, "ones", new["ones"]))
        return moves, changes, new, t + 0.1
    # 2. the rails that fill tip: their balls fall home, the extra ball rolls on to the next rail
    order = ["ones", "fives", "hours"]
    for i, rail in enumerate(tips):
        nxt = order[i + 1] if i + 1 < len(order) else None
        n_old = cur[rail]
        changes.append((t, rail, 0))  # the rail empties: its balls are now moving
        for j in range(n_old + 1):  # the balls that were on it, plus the one that just arrived
            x = slot_x(rail, j)
            if j == n_old and nxt is not None:  # the newcomer rolls to the end of the rail and drops to the next
                x_end = slot_x(nxt, cur[nxt])
                moves.append(Move([(x, rail_y(rail), t), (x_end, rail_y(rail) + 1, t + 0.5),
                                   (x_end, rail_y(nxt), t + 0.8)], ["roll", "fall"]))
            else:  # home: a fall to the reservoir row, then a roll to the left
                moves.append(Move([(x, rail_y(rail), t + 0.05 * j), (x, HOME_Y, t + 0.45 + 0.05 * j),
                                   (8 + (j % 7) * 4, HOME_Y, t + 0.9 + 0.05 * j)], ["fall", "roll"]))
        cur[rail] = 0
        t += 0.8
        if nxt is not None:
            if nxt in tips:
                continue  # it arrives on a full rail: that one tips next
            changes.append((t, nxt, new[nxt]))
    return moves, changes, new, t + 0.6


@register
class MarbleClock(App):
    id = "marbleclock"
    title = "Marble clock"
    category = "Clocks"
    description = ("A rolling-ball clock: once a minute a ball is lifted to the top and rolls into the minutes "
                   "rail; a full rail tips and sends its balls home, passing one on to the fives, then the hours. "
                   "Read the time from the balls on the rails. Still in quiet hours (the rails just update).")
    params = [
        P("digits", "checkbox", True, "Show the time in the corner too"),
        P("reservoir", "checkbox", True, "Show the balls waiting at the bottom"),
    ]
    interval = 0.5

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.counts = None
        self.minute = None
        self.moves, self.changes, self.end = [], [], 0.0
        self.shown = None

    def ball(self, c, x, y):
        x, y = int(round(x)), int(round(y))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                c.set(x + dx, y + dy)

    def start_minute(self, t, now):
        new = counts_for(t)
        if self.counts is None or _quiet(self) or self.minute is None or t.minute == self.minute:
            self.counts, self.shown = new, dict(new)
            self.moves, self.changes = [], []
            return
        moves, changes, planned, end = plan_minute(self.counts, now)
        if planned != new:  # the clock was off for a while: catch up without the show
            self.counts, self.shown, self.moves, self.changes = new, dict(new), [], []
            return
        self.moves, self.changes, self.end = moves, changes, end
        self.counts = new
        if self.shown is None:
            self.shown = dict(new)

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        if t.minute != self.minute:
            self.start_minute(t, now)
            self.minute = t.minute
        for when, rail, n in list(self.changes):
            if now >= when:
                self.shown[rail] = n
                self.changes.remove((when, rail, n))
        moving = [m for m in self.moves if m.t0 <= now < m.t1]
        self.moves = [m for m in self.moves if now < m.t1]
        # the frame: the lift, the top ramp, three rails with their stops
        for y in range(TOP_Y - 2, HOME_Y + 2):
            c.set(LIFT_X - 2, y)
        c.line(LIFT_X + 2, TOP_Y - 2, 40, ONES_Y + 1)
        c.line(40, ONES_Y + 2, 82, ONES_Y + 2)
        c.line(34, FIVES_Y + 2, 82, FIVES_Y + 2)
        c.line(28, HOURS_Y + 2, 82, HOURS_Y + 2)
        for y in (ONES_Y, FIVES_Y, HOURS_Y):
            c.set(82, y)
            c.set(82, y + 1)
            c.set(82, y - 1)
        self.ball(c, HOURS_X0 + 4, HOURS_Y)  # the fixed ball: the hours rail reads 1 to 12
        c.set(HOURS_X0 + 4, HOURS_Y - 2)
        for rail in ("ones", "fives", "hours"):
            for k in range(self.shown[rail]):
                self.ball(c, slot_x(rail, k), rail_y(rail))
        for m in moving:
            self.ball(c, *m.at(now))
        if self.p["reservoir"]:
            total = 1 + ONES_MAX + FIVES_MAX + HOURS_MAX
            home = total - sum(self.shown.values()) - len(moving)
            for k in range(max(0, min(7, home))):
                self.ball(c, 8 + k * 4, HOME_Y)
            c.line(LIFT_X + 2, HOME_Y + 2, 34, HOME_Y + 2)
        if self.p["digits"]:
            s = t.strftime("%I:%M").lstrip("0") or "12:00"
            c.text(c.w - 2 - SMALL.width(s), 1, s, SMALL)
        busy = bool(self.moves or self.changes)
        self.interval = 0.05 if busy else max(0.2, 60 - t.second - (now % 1) + 0.02)

    def describe(self):
        return self.title


# ---- 4. Sun path clock ---------------------------------------------------------------------------------------

SUN_RISE_EL = 0.833  # degrees: the sun has "risen" when its centre is this far below the horizon


def _location(store):
    s = store.settings.get("weather", {}) if store is not None else {}
    if s.get("lat") is None or s.get("lon") is None:
        raise NeedsSetup("Set a location in Settings")
    return float(s["lat"]), float(s["lon"])


def solar_noon(lon, now):
    """The unix time of the location's solar noon nearest to now (sun arcs are reckoned noon to noon)."""
    base = now - now % 86400
    noon = base + (12 - lon / 15) * 3600
    return min((noon - 86400, noon, noon + 86400), key=lambda n: abs(n - now))


def crossings(fn, start, span=86400, step=120):
    """(rise, set) from `fn(t) -> elevation` over [start, start + span): the first climb through 0 and the first
    fall after it (searched on past the window for the moon's sake). No climb: a fall if there is one. None where
    there is none (polar day or night)."""
    rise = set_ = None
    prev, t = fn(start), start
    while t < start + span + (14 * 3600 if rise is not None else 0):
        t2 = t + step
        el = fn(t2)
        if rise is None and prev < 0 <= el and t2 <= start + span:
            rise = _refine(fn, t, t2)
        elif prev >= 0 > el and (rise is not None or t2 <= start + span) and set_ is None:
            set_ = _refine(fn, t, t2)
            if rise is not None:
                break
        prev, t = el, t2
    return rise, set_


def _refine(fn, a, b):
    for _ in range(8):
        m = (a + b) / 2
        if (fn(a) < 0) == (fn(m) < 0):
            a = m
        else:
            b = m
    return (a + b) / 2


@register
class SunClock(App):
    id = "sunclock"
    title = "Sun path clock"
    category = "Clocks"
    description = ("Today's sun arc from sunrise to sunset for your location, with the sun where it is now; at "
                   "night the moon on its own arc, with its phase. Sunrise and sunset times at the ends.")
    params = [
        P("hour24", "checkbox", False, "24-hour"),
        P("show_time", "checkbox", True, "The time in the middle"),
    ]
    interval = 10.0

    @classmethod
    def needs_setup(cls, store, params):
        s = store.settings.get("weather", {})
        return None if s.get("lat") is not None and s.get("lon") is not None else "a location (Settings, Location)"

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.day = None
        self.times = None

    def day_times(self, now):
        """(sunrise, sunset, moonrise, moonset) unix times for the location's solar day around now (midnight to
        midnight by the sun there, so sunrise comes before sunset wherever the board is); cached per day."""
        lat, lon = _location(self.ctx.store)
        noon = solar_noon(lon, now)
        if self.day != noon:
            start = noon - 43200
            sun = lambda u: sun_position(lat, lon, u)[1] + SUN_RISE_EL  # the usual sunrise: refraction and the disc
            moon = lambda u: moon_position(lat, lon, u)[1]
            self.times = (*crossings(sun, start), *crossings(moon, start))
            self.day = noon
        return self.times

    def fmt(self, ts):
        if ts is None:
            return "--:--"
        t = dt.datetime.fromtimestamp(ts)
        return t.strftime("%H:%M") if self.p["hour24"] else (t.strftime("%I:%M").lstrip("0") or "12:00")

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        lat, lon = _location(self.ctx.store)
        sunrise, sunset, moonrise, moonset = self.day_times(now)
        horizon = 27
        x0, x1 = 6, c.w - 7
        peak = 5
        for x in range(x0, x1 + 1, 2):  # the arc, dotted
            f = (x - x0) / (x1 - x0)
            y = horizon - (horizon - peak) * math.sin(math.pi * f)
            c.set(x, round(y))
        for x in range(0, c.w, 2):  # the horizon
            c.set(x, horizon)
        sun_az, sun_el = sun_position(lat, lon, now)
        day = sun_el + SUN_RISE_EL >= 0
        if sunrise is not None and sunset is not None and sunset > sunrise:
            frac = (now - sunrise) / (sunset - sunrise)
        else:
            frac = (t.hour * 60 + t.minute) / 1440  # polar day or night: just the time of day
        if day:
            f = max(0.0, min(1.0, frac))
            sx = x0 + (x1 - x0) * f
            sy = horizon - (horizon - peak) * math.sin(math.pi * f)
            c.circle(sx, sy, 3, fill=True)
            for a in range(0, 360, 45):  # rays
                r = math.radians(a)
                c.set(sx + 5 * math.cos(r), sy + 5 * math.sin(r))
        else:
            # the sun below the horizon, a hollow circle on the mirrored arc...
            if sunrise is not None and sunset is not None:
                night = (now - sunset) % 86400 / max(1.0, (sunrise - sunset) % 86400)
            else:
                night = frac
            sx = x1 - (x1 - x0) * night  # on from where it set, round to where it rises
            sy = horizon + 2.5 * math.sin(math.pi * night)  # just under the horizon, clear of the times
            c.circle(sx, sy, 2)
            # ...and the moon on its own arc when it's up, with its phase
            m_az, m_el, elong = moon_position(lat, lon, now)
            if m_el >= 0:
                if moonrise is not None and moonset is not None and moonset > moonrise:
                    mf = max(0.0, min(1.0, (now - moonrise) / (moonset - moonrise)))
                else:
                    mf = max(0.0, min(1.0, (m_az - 90) / 180)) if 90 <= m_az <= 270 else 0.5
                mx = x0 + (x1 - x0) * mf
                my = horizon - (horizon - peak) * math.sin(math.pi * mf)
                draw_moon(c, round(mx), round(my), 3, elong)
        c.text(1, horizon + 3, self.fmt(sunrise), SMALL)
        s = self.fmt(sunset)
        c.text(c.w - 1 - SMALL.width(s), horizon + 3, s, SMALL)
        if self.p["show_time"]:
            c.text_center(c.h - 8, self.fmt(now), NORMAL)
        self.interval = 10.0

    def describe(self):
        return self.title
