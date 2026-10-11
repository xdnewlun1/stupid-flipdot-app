"""Rhythm game: notes fall in up to 4 lanes, one per player, and you press when each reaches the line.

With live music (Settings → Music) the notes come from the music itself: each bass note, snare or clap, hi-hat
and beat is put on the beat grid (beat phase and BPM from audio_signal) and comes back as a note in its lane
one bar later, so the notes follow what's playing. Without live music there's a built-in practice track at a
set tempo, where the board's own clicks as notes land are the beat.

Timing is judged against when you hear the music: the board shows live music a fixed Board delay (Settings →
Music) after the speakers play it, so unless the speakers are delayed to match the board, a note is due that
much earlier than the analysis says. Presses are timestamped as they arrive (input_now), not when the next
frame is drawn. Scores and the best combo go in the shared high-score table.
"""

import math

import audio_signal
from fonts import SMALL

from . import scores
from .base import P, register
from .pack_party import CPU_MODES, PartyGame, box_text, fill_rect
from .visualizer import LIVE_FPS

FALL = 1.5          # seconds a note takes from the top to the line
HIT_Y = 34          # the line
LEAD_IN = 3.0       # seconds before the first practice note lands
WINDOWS = ((0.045, "perfect", 300), (0.09, "great", 200), (0.135, "good", 100))
MISS = 0.135        # a note this late without a press is missed
REACH = 0.2         # a press this far from any note is ignored (no penalty)
MIN_GAP = 0.18      # notes in one lane at least this far apart
LANE_BANDS = {1: [("bass", "mid", "high", "beat")],
              2: [("bass", "beat"), ("mid", "high")],
              3: [("bass",), ("mid",), ("high", "beat")],
              4: [("bass",), ("mid",), ("high",), ("beat",)]}
# practice track: one bar of eighth notes per band (two bars alternate for the hats)
PATTERN = {"bass": ["x...x.x.", "x..x..x."], "mid": ["..x...x.", "..x...xx"],
           "high": [".x.x.x.x", "x.x.x.x."], "beat": ["x.x.x.x.", "x.x.x.x."]}


def judge(delta):
    """(name, points) for a press `delta` seconds from its note, or None when it's outside every window."""
    d = abs(delta)
    for limit, name, pts in WINDOWS:
        if d <= limit:
            return name, pts
    return None


def multiplier(combo):
    return 1 + min(3, combo // 10)


def board_delay(store):
    """Settings → Music: how far the board runs behind the speakers (s)."""
    try:
        return max(0, min(2000, int((store.settings.get("snapcast") or {}).get("delay_ms") or 150))) / 1000
    except (AttributeError, TypeError, ValueError):
        return 0.15


@register
class Rhythm(PartyGame):
    id = "rhythm"
    title = "Rhythm"
    description = ("Notes fall in time with the music, one lane per player: press when each reaches the line. Uses "
                   "the live music from Settings → Music, or a practice beat when nothing is playing.")
    steady_timing = True  # every picture goes out the same time after it's drawn, like the music visualizer
    join_hint = "A TO JOIN, START GO"
    params = [
        P("length", "number", 90, "Song length (seconds)", min=20, max=600, step=10),
        P("source", "select", "auto", "Notes from",
          options=[["auto", "Live music when it plays, else the practice beat"], ["practice", "Practice beat only"]]),
        P("bpm", "number", 100, "Practice tempo (BPM)", min=60, max=180),
        P("speakers_delayed", "checkbox", False, "The speakers are delayed to match the board"),
        P("offset", "number", 0, "Timing fine tune (ms, + = later)", min=-300, max=300, step=5),
        P("cpu", "select", "none", "CPU players", options=CPU_MODES),
    ]

    def start_game(self, now):
        self.lanes = list(self.seats)
        self.bands = LANE_BANDS[len(self.lanes)]
        self.notes = []  # {"lane", "t" (when it's heard), "done": None | judgement}
        self.stats = {s: {"score": 0, "combo": 0, "best": 0, "hits": 0, "misses": 0, "last": None, "at": -9}
                      for s in self.seats}
        self.t0 = now
        self.end_at = now + LEAD_IN + float(self.p["length"] or 90)
        self.live = False
        self.counts = None
        self.practice_from = None
        self.planned = now  # practice notes are placed up to here
        self.flash = {}
        self.cpu_plan = {}
        self.pick_source(now)

    # ---- where the notes come from -------------------------------------------------------------

    def pick_source(self, now):
        live = False
        if self.p["source"] == "auto":
            try:
                live = bool(audio_signal.reading(self.ctx.store, None, now).get("live"))
            except Exception:
                live = False
        if live != self.live or self.practice_from is None and not live:
            self.live = live
            self.counts = None
            if not live:
                # the practice track starts on a bar line after the notes already in the air
                bpm = float(self.p["bpm"] or 100)
                bar = 240 / bpm
                start = max(now + LEAD_IN, max([n["t"] for n in self.notes] + [0]) + 0.3)
                self.practice_from = self.t0 + LEAD_IN + math.ceil((start - self.t0 - LEAD_IN) / bar) * bar
                self.planned = self.practice_from

    def delay(self):
        """Seconds between the analysis' beat grid and what players hear."""
        lag = 0.0 if self.p["speakers_delayed"] or not self.live else board_delay(self.ctx.store)
        return lag - float(self.p["offset"] or 0) / 1000

    def lanes_for(self, band):
        return [i for i, bands in enumerate(self.bands) if band in bands]

    def add_note(self, lane, t):
        if t >= self.end_at:
            return
        for n in self.notes:
            if n["lane"] == lane and abs(n["t"] - t) < MIN_GAP:
                return
        self.notes.append({"lane": lane, "t": t, "done": None})

    def plan_practice(self, now):
        bpm = float(self.p["bpm"] or 100)
        eighth = 30 / bpm
        while self.planned < now + FALL + 0.5 and self.planned < self.end_at:
            k = round((self.planned - self.practice_from) / eighth)
            bar, slot = divmod(k, 8)
            for band, bars in PATTERN.items():
                if bars[bar % len(bars)][slot] == "x":
                    for lane in self.lanes_for(band):
                        self.add_note(lane, self.planned)
            self.planned = self.practice_from + (k + 1) * eighth

    def plan_live(self, now):
        """Each new hit and beat, put on the beat grid, returns as a note one bar later."""
        r = audio_signal.reading(self.ctx.store, None, now)
        counts = (r["beat_count"], dict(r["hit_count"]))
        old, self.counts = self.counts, counts
        if old is None or r.get("beat_phase") is None or not r.get("bpm"):
            return
        period = 60 / r["bpm"]
        on_beat = now - r["beat_phase"] * period
        bar = 4 * period
        new = [k for k, n in counts[1].items() if n != old[1].get(k)]
        if counts[0] != old[0]:
            new.append("beat")
        for band in new:
            if band == "lowmid":
                band = "mid"
            half = period / 2
            slot = on_beat + round((now - on_beat) / half) * half
            for lane in self.lanes_for(band):
                self.add_note(lane, slot + bar - self.delay())

    # ---- presses -----------------------------------------------------------------------------------

    def key(self, t, key, action, seat):
        if action != "down" or seat not in self.lanes or key not in ("a", "b", "x", "y", "up", "down", "left", "right"):
            return
        self.press(t, seat)

    def press(self, t, seat):
        lane = self.lanes.index(seat)
        cands = [n for n in self.notes if n["lane"] == lane and n["done"] is None and abs(n["t"] - t) <= REACH]
        if not cands:
            return None
        note = min(cands, key=lambda n: abs(n["t"] - t))
        st = self.stats[seat]
        got = judge(t - note["t"])
        if got is None:  # too early or too late for this note: it's lost, and so is the combo
            note["done"] = "miss"
            st["combo"], st["last"], st["at"] = 0, "miss", t
            st["misses"] += 1
            return "miss"
        name, pts = got
        note["done"] = name
        st["combo"] += 1
        st["best"] = max(st["best"], st["combo"])
        st["score"] += pts * multiplier(st["combo"] - 1)
        st["hits"] += 1
        st["last"], st["at"] = name, t
        self.totals[seat] = st["score"]
        self.flash[lane] = t
        return name

    def tick(self, now):
        if now >= self.end_at + 1.0:
            for s in self.seats:
                self.totals[s] = self.stats[s]["score"]
            best = max([self.stats[s]["best"] for s in self.seats if not self.is_cpu(s)] + [0])
            if best:
                self.file_score("rhythm:combo", best)
            return self.finish(now)
        if self.p["source"] == "auto" and int(now * 2) != int((now - self.interval) * 2):
            self.pick_source(now)
        if self.live:
            try:
                self.plan_live(now)
            except Exception:
                self.live = False
        else:
            self.plan_practice(now)
        for n in self.notes:
            if n["done"] is None and now - n["t"] > MISS:
                n["done"] = "miss"
                st = self.stats[self.lanes[n["lane"]]]
                st["combo"], st["last"], st["at"] = 0, "miss", n["t"] + MISS
                st["misses"] += 1
        self.notes = [n for n in self.notes if now - n["t"] < 0.6]
        for s in self.seats:
            if self.is_cpu(s):
                self.cpu_play(s, now)
        self.interval = (audio_signal.steady_interval(self.ctx.store, now, LIVE_FPS) if self.live else None) \
            or 1 / LIVE_FPS

    def cpu_play(self, seat, now):
        lane = self.lanes.index(seat)
        for n in self.notes:
            if n["lane"] == lane and n["done"] is None and id(n) not in self.cpu_plan and n["t"] - now < 0.5:
                self.cpu_plan[id(n)] = None if self.rnd.random() < 0.08 else n["t"] + self.rnd.gauss(0, 0.035)
        for k, at in list(self.cpu_plan.items()):
            if at is not None and now >= at:
                self.press(at, seat)
                self.cpu_plan[k] = None

    # ---- board -------------------------------------------------------------------------------------

    def draw(self, c, now):
        n = len(self.lanes)
        lw = c.w // n
        for i, seat in enumerate(self.lanes):
            x0 = i * lw
            if i:
                for y in range(0, HIT_Y + 3, 3):
                    c.set(x0, y)
            # the line: brackets at the lane's edges, solid while it flashes after a hit
            if now - self.flash.get(i, -9) < 0.12:
                fill_rect(c, x0 + 2, HIT_Y - 1, lw - 3, 3)
            else:
                for dx in (1, 2, lw - 3, lw - 2):
                    c.set(x0 + dx, HIT_Y - 1)
                    c.set(x0 + dx, HIT_Y + 1)
                c.line(x0 + 1, HIT_Y, x0 + lw - 2, HIT_Y)
            st = self.stats[seat]
            s = scores.short(st["score"])
            c.text(x0 + (lw - SMALL.width(s)) // 2 + (1 if i else 0), 37, s, SMALL)
            if now - st["at"] < 0.6 and st["last"]:
                mark = {"perfect": "!!!", "great": "!!", "good": "!", "miss": "X"}[st["last"]]
                if st["last"] != "miss" and st["combo"] >= 5:
                    mark = f"{st['combo']}"
                c.text(x0 + (lw - SMALL.width(mark)) // 2 + 1, 2, mark, SMALL)
            elif now - self.t0 < LEAD_IN:
                c.text(x0 + (lw - SMALL.width(self.name(seat))) // 2 + 1, 2, self.name(seat), SMALL)
        for note in self.notes:
            if note["done"] not in (None, "miss") or now - note["t"] > MISS:
                continue
            y = HIT_Y - (note["t"] - now) / FALL * HIT_Y
            if y < -2:
                continue
            x0 = note["lane"] * lw
            fill_rect(c, x0 + 3, int(round(y)) - 1, lw - 5, 2)
        if now - self.t0 < LEAD_IN:
            box_text(c, 2, 14, "LIVE MUSIC" if self.live else f"PRACTICE {int(self.p['bpm'] or 100)} BPM")
        left = self.end_at - now
        if 0 < left < 5:
            box_text(c, c.w - 6, 14, str(int(left) + 1))

    def status_text(self):
        best = max((self.stats[s]["score"] for s in self.seats), default=0) if getattr(self, "stats", None) else 0
        return ("live music" if getattr(self, "live", False) else "practice beat") + f", top score {best}"

    def phone_state(self, seat):
        out = super().phone_state(seat)
        if getattr(self, "stats", None) and seat in self.stats:
            st = self.stats[seat]
            out.update(score=st["score"], combo=st["combo"], best=st["best"], live=self.live)
        return out
