"""Drum machine: a 16-step sequencer where every row is a board "instrument", programmed on the phone (/play shows
the grid while this runs; several phones can edit the same pattern at once).

The instruments are the sound of the discs: a single dot ticks, a small cluster taps, a whole panel cracks, a
column roll rattles across a panel and a full sweep washes over the board. Steps are timed from a clock, not
from frames: the next frame is drawn exactly when the next step is due (and the board sends every picture the
same time after it's drawn), so a 16th note lands within a few ms of its time. Swing delays every other 16th.
It can follow the tempo and beat of the live music. In quiet hours the loud instruments play as quiet ones and
the flip budget caps how much flips per second.
"""

import random
import time

import audio_signal
from fonts import NORMAL, SMALL

from .base import P, register
from .games import Game
from .react import quiet_now

STEPS = 16
FILE = "drum_patterns.json"
INSTRUMENTS = [("tick", "Tick (one dot)"), ("cluster", "Tap (a small cluster)"), ("crack", "Crack (a whole panel)"),
               ("roll", "Roll (columns across a panel)"), ("sweep", "Sweep (the whole board)"),
               ("scatter", "Shaker (scattered dots)")]
QUIET = {"sweep": "cluster", "crack": "cluster", "roll": "tick", "scatter": "tick"}  # quiet hours
EARLY = 0.004       # a step fires when its frame is this close
CATCH_UP = 0.25     # further behind than this (a stall): skip to now instead of firing every missed step
ANIM_FPS = 60       # rolls and sweeps move this often between steps
PRESETS = {
    "Basic beat": {"bpm": 100, "swing": 50, "rows": ["x.x.x.x.x.x.x.x.", "................", "....x.......x...",
                                                     "................", "x...............", "......x.......x."]},
    "Four on the floor": {"bpm": 120, "swing": 50, "rows": ["..x...x...x...x.", "x...x...x...x...", "....x.......x...",
                                                            "..............x.", "................", "................"]},
    "Breakbeat": {"bpm": 92, "swing": 58, "rows": ["x.xxx.x.x.xxx.x.", "x.........x.....", "....x..x.x..x...",
                                                   "...........x....", "x...............", "................"]},
    "Clockwork": {"bpm": 80, "swing": 50, "rows": ["x...x...x...x...", "..x...x...x...x.", "................",
                                                   "................", "................", "................"]},
}
DEFAULT = "Basic beat"


def offsets(k, d, swing):
    """When 16th k plays, from step 0: pairs of 2d, the second of each pair delayed by swing (0.5 = straight)."""
    pair, odd = divmod(k, 2)
    return pair * 2 * d + (odd * 2 * d * swing)


class Clock:
    """Step times from an anchor (step k_a plays at t_a); tempo or swing changes re-anchor at the next step,
    so playing carries on without a jump."""

    def __init__(self, bpm=100, swing=0.5):
        self.bpm, self.swing = float(bpm), float(swing)
        self.anchor = None  # (t, k)
        self.k = 0  # the next step to play (counts on past 16)

    @property
    def d(self):
        return 60 / self.bpm / 4

    def time(self, k):
        t_a, k_a = self.anchor
        return t_a + offsets(k, self.d, self.swing) - offsets(k_a, self.d, self.swing)

    def start(self, now):
        self.anchor, self.k = (now, 0), 0

    def retime(self, bpm=None, swing=None):
        if self.anchor is not None:
            t = self.time(self.k)
            self.anchor = (t, self.k)
        if bpm is not None:
            self.bpm = float(bpm)
        if swing is not None:
            self.swing = float(swing)

    def due(self, now):
        """The steps due by now: [(k, scheduled time)], k counting on past 16."""
        out = []
        if self.anchor is None:
            return out
        if now - self.time(self.k) > CATCH_UP:
            k = self.k
            while self.time(k + 1) <= now:
                k += 1
            self.k = k
        while self.time(self.k) <= now + EARLY:
            out.append((self.k, self.time(self.k)))
            self.k += 1
        return out

    def until_next(self, now):
        return None if self.anchor is None else self.time(self.k) - now


def blank():
    return ["." * STEPS for _ in INSTRUMENTS]


def clean_rows(rows):
    out = []
    for i in range(len(INSTRUMENTS)):
        r = str(rows[i]) if rows and i < len(rows) else ""
        out.append("".join("x" if ch in "x1#X" else "." for ch in r[:STEPS]).ljust(STEPS, "."))
    return out


@register
class DrumMachine(Game):
    id = "drummachine"
    title = "Drum Machine"
    description = ("A 16-step drum machine played by the board's clicks: program it on the phone (/play), several "
                   "phones at once. Rows are a tick, a tap, a panel crack, a column roll, a full sweep and a shaker.")
    interactive = True
    steady_timing = True  # each picture goes out the same time after it's drawn: steps keep their spacing
    params = [
        P("rows", "number", 5, "Instruments (rows)", min=4, max=6),
        P("follow", "checkbox", False, "Follow the live music's tempo"),
        P("autoplay", "checkbox", False, "Start playing straight away"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.rows_n = max(4, min(6, int(self.p["rows"] or 5)))
        self.rnd = random.Random()
        self.pic = bytearray(self.w * self.h)
        self.anims = []  # [columns of dots, start, duration, done]
        self.flips = []  # (time, count) in the last second, for the flip budget
        self.rev = 0
        self.beats = None
        self.panel = 0
        self.now = time.time()
        saved = self._load()
        cur = saved.get("current") or PRESETS[DEFAULT]
        self.name = saved.get("current_name") or DEFAULT
        self.grid = clean_rows(cur.get("rows"))
        self.seq = Clock(max(40, min(240, float(cur.get("bpm") or 100))), max(50, min(75, float(cur.get("swing") or 50))) / 100)
        self.follow = bool(self.p["follow"])
        self.playing = False
        self.step = None  # the 16th last played (0-15)
        self.step_at = None
        if self.p["autoplay"]:
            self.play(self.now)

    # ---- storage ------------------------------------------------------------------------------

    def _load(self):
        try:
            data = self.ctx.store._load(FILE, {}) or {}
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _save(self, **changes):
        data = self._load()
        data.setdefault("patterns", {})
        data.update(changes)
        try:
            self.ctx.store._save(FILE, data)
        except Exception:
            pass
        return data

    def current(self):
        return {"bpm": round(self.seq.bpm), "swing": round(self.seq.swing * 100), "rows": list(self.grid)}

    def remember(self):
        self.rev += 1
        self._save(current=self.current(), current_name=self.name)

    def patterns(self):
        mine = self._load().get("patterns") or {}
        return [{"name": n, "preset": True} for n in PRESETS] + [{"name": n, "preset": False} for n in sorted(mine)]

    # ---- transport and editing -------------------------------------------------------------------

    def play(self, now):
        if not self.playing:
            self.playing = True
            self.seq.start(now + 0.05)
            self.pic[:] = bytes(len(self.pic))
            self.rev += 1

    def stop_playing(self):
        if self.playing:
            self.playing = False
            self.seq.anchor = None
            self.anims = []
            self.step = None
            self.rev += 1

    def set_tempo(self, bpm=None, swing=None):
        if bpm is not None:
            bpm = max(40, min(240, float(bpm)))
        if swing is not None:
            swing = max(50, min(75, float(swing))) / 100
        self.seq.retime(bpm, swing)

    def toggle(self, r, s, on=None):
        if not (0 <= r < len(INSTRUMENTS) and 0 <= s < STEPS):
            raise ValueError("no such step")
        row = list(self.grid[r])
        row[s] = ("x" if row[s] == "." else ".") if on is None else ("x" if on else ".")
        self.grid[r] = "".join(row)

    # ---- the instruments ----------------------------------------------------------------------------

    def quiet(self):
        try:
            return quiet_now(self.ctx.store)
        except Exception:
            return False

    def budget(self):
        """Flips a second allowed now (quiet hours' max flips), or None for no cap."""
        if not self.quiet():
            return None
        try:
            return max(10, int((self.ctx.store.settings.get("quiet_hours") or {}).get("max_flips") or 150))
        except (AttributeError, TypeError, ValueError):
            return 150

    def toggle_dots(self, dots, now):
        dots = [(x, y) for x, y in dots if 0 <= x < self.w and 0 <= y < self.h]
        cap = self.budget()
        if cap is not None:
            self.flips = [(t, n) for t, n in self.flips if now - t < 1.0]
            used = sum(n for _, n in self.flips)
            if used + len(dots) > cap:
                dots = dots[:max(0, cap - used)]
            self.flips.append((now, len(dots)))
        w, pic = self.w, self.pic
        for x, y in dots:
            pic[y * w + x] ^= 1
        return len(dots)

    def hit(self, inst, t, now):
        if self.quiet():
            inst = QUIET.get(inst, inst)
        rnd, d = self.rnd, self.seq.d
        if inst == "tick":
            self.toggle_dots([(rnd.randrange(self.w), rnd.randrange(self.h))], now)
        elif inst == "cluster":
            x, y = rnd.randrange(self.w - 3), rnd.randrange(self.h - 3)
            self.toggle_dots([(x + i, y + j) for i in range(3) for j in range(3)], now)
        elif inst == "scatter":
            self.toggle_dots([(rnd.randrange(self.w), rnd.randrange(self.h)) for _ in range(10)], now)
        elif inst == "crack":
            cols, rows = max(1, self.w // 28), max(1, self.h // 7)
            px, py = self.panel % cols, (self.panel // cols) % rows
            self.panel = (self.panel + 7) % (cols * rows)  # hop round the panels
            self.toggle_dots([(px * 28 + i, py * 7 + j) for i in range(28) for j in range(7)], now)
        elif inst == "roll":
            py = rnd.randrange(max(1, self.h // 7))
            x0 = rnd.randrange(max(1, self.w // 28)) * 28
            cols = [[(x0 + i, py * 7 + j) for j in range(7)] for i in range(28)]
            self.anims.append([cols, t, d * 0.9, 0])
        elif inst == "sweep":
            cols = [[(x, y) for y in range(self.h)] for x in range(self.w)]
            self.anims.append([cols, t, max(0.06, d * 1.5), 0])

    def run_anims(self, now):
        for a in self.anims:
            cols, t0, dur, done = a
            upto = len(cols) if now - t0 >= dur else max(0, int(len(cols) * (now - t0) / dur) + 1)
            for col in cols[done:upto]:
                self.toggle_dots(col, now)
            a[3] = max(done, upto)
        self.anims = [a for a in self.anims if a[3] < len(a[0])]

    # ---- following the music ------------------------------------------------------------------------

    def follow_music(self, now):
        try:
            r = audio_signal.reading(self.ctx.store, None, now)
        except Exception:
            return
        if not r.get("live") or r.get("beat_phase") is None or not r.get("bpm"):
            return
        bpm = round(r["bpm"])
        if abs(bpm - self.seq.bpm) >= 1:
            self.set_tempo(bpm=bpm)
            self.rev += 1
        if self.beats != r["beat_count"] and self.seq.anchor is not None:
            self.beats = r["beat_count"]
            # the quarter note nearest this beat moves onto it (steps 0, 4, 8, 12 are the quarters)
            beat_t = now - r["beat_phase"] * 60 / r["bpm"]
            k0 = self.seq.k - self.seq.k % 4
            k4 = k0 + round((beat_t - self.seq.time(k0)) / (4 * self.seq.d)) * 4
            self.seq.anchor = (beat_t, k4)

    # ---- frames ---------------------------------------------------------------------------------------

    def render(self, c, now):
        self.now = now
        if not self.playing:
            c.text_center(2, "DRUM MACHINE", NORMAL)
            c.text_center(13, f"{round(self.seq.bpm)} BPM", SMALL)
            c.text_center(21, self.name.upper()[:20], SMALL)
            c.text_center(31, "A: PLAY  UP/DN: TEMPO", SMALL)
            self.interval = 0.25
            return
        if self.follow:
            self.follow_music(now)
        for k, t in self.seq.due(now):
            s = k % STEPS
            self.step, self.step_at = s, t
            for r in range(self.rows_n):
                if self.grid[r][s] == "x":
                    self.hit(INSTRUMENTS[r][0], t, now)
        if self.anims:
            self.run_anims(now)
        c.px[:] = self.pic
        nxt = self.seq.until_next(now)
        wait = max(0.002, nxt) if nxt is not None else 0.05
        self.interval = min(wait, 1 / ANIM_FPS) if self.anims else wait

    def input(self, key, action="down", player=1):
        if action != "down":
            return
        if key in ("a", "start"):
            self.stop_playing() if self.playing else self.play(time.time())
        elif key == "b":
            self.stop_playing()
        elif key in ("up", "down"):
            self.set_tempo(bpm=self.seq.bpm + (5 if key == "up" else -5))
            self.remember()
        elif key in ("left", "right"):
            self.set_tempo(swing=self.seq.swing * 100 + (5 if key == "right" else -5))
            self.remember()

    def describe(self):
        return f"Drum machine: {round(self.seq.bpm)} BPM, " + ("playing" if self.playing else "stopped")

    def stop(self):
        self.playing = False

    # ---- the /play page --------------------------------------------------------------------------------

    def phone_state(self, seat):
        since = time.time() - self.step_at if self.step_at is not None else None
        return {"app": self.id, "mode": "drum", "rev": self.rev, "rows": [{"id": i, "label": l} for i, l in
                                                                            INSTRUMENTS[:self.rows_n]],
                "grid": self.grid[:self.rows_n], "bpm": round(self.seq.bpm), "swing": round(self.seq.swing * 100),
                "playing": self.playing, "step": self.step, "step_ms": round(self.seq.d * 1000, 1),
                "since_ms": round(since * 1000) if since is not None else None, "follow": self.follow,
                "name": self.name, "patterns": self.patterns(), "quiet": self.quiet()}

    def phone(self, action, data, seat):
        data = data or {}
        if action == "toggle":
            try:
                r, s = int(data.get("r")), int(data.get("s"))
            except (TypeError, ValueError):
                raise ValueError("toggle needs r and s") from None
            if r >= self.rows_n:
                raise ValueError("no such row")
            self.toggle(r, s, data.get("on"))
            self.remember()
        elif action == "set":
            if "bpm" in data and not self.follow:
                self.set_tempo(bpm=data["bpm"])
            if "swing" in data:
                self.set_tempo(swing=data["swing"])
            if "follow" in data:
                self.follow = bool(data["follow"])
                self.beats = None
            self.remember()
        elif action == "play":
            self.play(time.time())
        elif action == "stop":
            self.stop_playing()
        elif action == "clear":
            self.grid = blank()
            self.remember()
        elif action == "save":
            name = " ".join(str(data.get("name") or "").split())[:30]
            if not name:
                raise ValueError("Give the pattern a name.")
            if name in PRESETS:
                raise ValueError("That's the name of a built-in pattern; pick another.")
            pats = self._load().get("patterns") or {}
            if name not in pats and len(pats) >= 50:
                raise ValueError("There are already 50 saved patterns. Delete some first.")
            pats[name] = self.current()
            self.name = name
            self._save(patterns=pats)
            self.remember()
        elif action == "load":
            name = str(data.get("name") or "")
            pat = PRESETS.get(name) or (self._load().get("patterns") or {}).get(name)
            if pat is None:
                raise ValueError("No pattern with that name.")
            self.grid = clean_rows(pat.get("rows"))
            if not self.follow:
                self.set_tempo(bpm=pat.get("bpm") or 100)
            self.set_tempo(swing=pat.get("swing") or 50)
            self.name = name
            self.remember()
        elif action == "delete":
            name = str(data.get("name") or "")
            pats = self._load().get("patterns") or {}
            if pats.pop(name, None) is None:
                raise ValueError("No saved pattern with that name.")
            self._save(patterns=pats)
            self.rev += 1
        else:
            raise ValueError(f"unknown action {action!r}")
        return {"ok": True}
