"""Random effects for slideshow items: now and then an item gets inverted, glitched, melted,
run through Game of Life… for the whole item or a short burst. All cheap enough for a Pi."""

import math
import random

from canvas import Canvas

EFFECTS = {
    "invert": "Invert",
    "flash": "Flash",
    "glitch": "Glitch",
    "mirror": "Mirror",
    "kaleidoscope": "Kaleidoscope",
    "wave": "Wave",
    "scanlines": "Scanlines",
    "pixelate": "Pixelate",
    "outline": "Outline",
    "static": "Static",
    "life": "Game of Life",
    "melt": "Melt",
}
ANIMATED = {"flash", "glitch", "wave", "scanlines", "static", "life", "melt"}


def roll(settings, rng, now, item_seconds):
    """Maybe pick an effect for an item that's starting. Returns an Effect or None."""
    chance = float(settings.get("fx_chance") or 0) / 100
    allowed = [e for e in (settings.get("fx_effects") or list(EFFECTS)) if e in EFFECTS]
    if not allowed or rng.random() >= chance:
        return None
    kind = rng.choice(allowed)
    if settings.get("fx_mode", "burst") == "whole":
        start, end = now, None  # until the item changes
    else:
        length = max(0.3, float(settings.get("fx_seconds") or 3))
        lead = max(0.0, item_seconds - length) if item_seconds else 0.0
        start = now + rng.uniform(0, lead) if lead else now
        end = start + length
    return Effect(kind, start, end, float(settings.get("fx_strength") or 50) / 100, rng)


class Effect:
    def __init__(self, kind, start, end, strength, rng):
        self.kind, self.start, self.end = kind, start, end
        self.strength = max(0.05, min(1.0, strength))
        self.rng = rng or random.Random()
        self.state = {}
        self.cycles = None  # reacting to music: the effect's own motion clock in cycles (see Reactive), else wall time
        self.sway = 1.0     # reacting to music: the wave sways further in groovy (syncopated, off-beat) music

    def active(self, now):
        return now >= self.start and (self.end is None or now < self.end)

    @property
    def animated(self):
        return self.kind in ANIMATED

    def apply(self, frame, now):
        """Return a processed copy of `frame` (a Canvas)."""
        if not self.active(now):
            self.state.pop("life", None)
            return frame
        c = frame.copy()
        getattr(self, "_" + self.kind)(c, now - self.start)
        return c

    # ---- effects (operate in place on c) --------------------------------------------

    def _invert(self, c, t):
        c.invert()

    def _flash(self, c, t):
        if self.cycles is not None:  # one flash per cycle (on the beat when locked), longer when stronger
            if self.cycles % 1 < 0.25 + 0.5 * self.strength:
                c.invert()
        elif int(t * (3 + 6 * self.strength)) % 2 == 0:
            c.invert()

    def _held(self, make):
        """A random pattern: new every frame, or with a motion clock, new each whole cycle and held between."""
        if self.cycles is None:
            return make()
        step, held = int(self.cycles), self.state.get("held")
        if held is None or held[0] != step:
            held = self.state["held"] = (step, make())
        return held[1]

    def _glitch(self, c, t):
        w, h, px, rng = c.w, c.h, c.px, self.rng
        s = self.strength
        # per row: (chance to shift, shift as a fraction of the reach, chance to invert); strength applies on top,
        # so a held pattern still follows the strength
        plan = self._held(lambda: [(rng.random(), rng.uniform(-1, 1), rng.random()) for _ in range(h)])
        reach = 4 + 16 * s
        for y, (u, f, inv) in enumerate(plan):
            if u < 0.25 * s:
                shift = round(f * reach)
                row = px[y * w:(y + 1) * w]
                px[y * w:(y + 1) * w] = row[-shift:] + row[:-shift] if shift else row
            if inv < 0.06 * s:
                px[y * w:(y + 1) * w] = bytearray(1 - v for v in px[y * w:(y + 1) * w])

    def _mirror(self, c, t):
        w = c.w
        for y in range(c.h):
            c.px[y * w:(y + 1) * w] = c.px[y * w:(y + 1) * w][::-1]

    def _kaleidoscope(self, c, t):
        w, h, px = c.w, c.h, c.px
        for y in range(h):
            row = px[y * w:(y + 1) * w]
            half = row[:w // 2]
            px[y * w:(y + 1) * w] = half + half[::-1] + row[w // 2 * 2:]
        for y in range(h // 2, h):
            src = h - 1 - y
            px[y * w:(y + 1) * w] = px[src * w:(src + 1) * w]

    def _wave(self, c, t):
        w, px = c.w, c.px
        amp = (1 + 5 * self.strength) * self.sway
        src = bytes(px)
        for y in range(c.h):
            shift = round(amp * math.sin(y / 4 + t * 6))
            row = src[y * w:(y + 1) * w]
            px[y * w:(y + 1) * w] = (row[-shift:] + row[:-shift]) if shift else row

    def _scanlines(self, c, t):
        w = c.w
        gap = 2 if self.strength > 0.6 else 3
        off = int(t * 12)
        for y in range(c.h):
            if (y + off) % gap == 0:
                c.px[y * w:(y + 1) * w] = bytes(w)

    def _pixelate(self, c, t):
        w, h, px = c.w, c.h, c.px
        b = 2 + round(4 * self.strength)
        src = bytes(px)
        for by in range(0, h, b):
            for bx in range(0, w, b):
                cells = [src[y * w + x] for y in range(by, min(by + b, h)) for x in range(bx, min(bx + b, w))]
                v = 1 if sum(cells) * 2 >= len(cells) else 0
                for y in range(by, min(by + b, h)):
                    px[y * w + bx:y * w + min(bx + b, w)] = bytes([v]) * (min(bx + b, w) - bx)

    def _outline(self, c, t):
        w, h = c.w, c.h
        src = bytes(c.px)
        out = bytearray(w * h)
        for y in range(h):
            for x in range(w):
                v = src[y * w + x]
                if v and ((x and not src[y * w + x - 1]) or (x < w - 1 and not src[y * w + x + 1])
                          or (y and not src[(y - 1) * w + x]) or (y < h - 1 and not src[(y + 1) * w + x])):
                    out[y * w + x] = 1
        c.px[:] = out

    def _static(self, c, t):
        size = len(c.px)
        if self.cycles is None:
            for _ in range(int(size * 0.12 * self.strength)):
                c.px[self.rng.randrange(size)] ^= 1
            return
        dots = self._held(lambda: [self.rng.randrange(size) for _ in range(int(size * 0.12))])
        for i in dots[:int(len(dots) * self.strength)]:
            c.px[i] ^= 1

    def _life(self, c, t):
        """Run Game of Life starting from the picture; generations at ~10/s."""
        w, h = c.w, c.h
        st = self.state.get("life")
        if st is None:
            st = self.state["life"] = {"cells": bytearray(c.px), "gen": 0}
        target = int(t * 10)
        cells = st["cells"]
        while st["gen"] < target:
            nxt = bytearray(w * h)
            for y in range(h):
                up, dn, row = ((y - 1) % h) * w, ((y + 1) % h) * w, y * w
                for x in range(w):
                    l, r = (x - 1) % w, (x + 1) % w
                    k = (cells[up + l] + cells[up + x] + cells[up + r] + cells[row + l] + cells[row + r]
                         + cells[dn + l] + cells[dn + x] + cells[dn + r])
                    nxt[row + x] = 1 if k == 3 or (k == 2 and cells[row + x]) else 0
            cells = st["cells"] = nxt
            st["gen"] += 1
        c.px[:] = cells

    def _melt(self, c, t):
        w, h, px = c.w, c.h, c.px
        speeds = self.state.setdefault("speeds", [self.rng.uniform(0.5, 1.5) for _ in range(w)])
        src = bytes(px)
        rate = 4 + 14 * self.strength
        for x in range(w):
            drop = min(h, int((t * rate * speeds[x]) ** 1.3))
            if drop:
                for y in range(h - 1, -1, -1):
                    sy = y - drop
                    px[y * w + x] = src[sy * w + x] if sy >= 0 else 0


# ---- reacting to music ---------------------------------------------------------------------
# Each signal drives one effect with strength = signal above its threshold x amount: how much it does (how far
# the wave bends, how many dots of static). A band's signal is its level, its hits (each new bass note, snare,
# hat: audio_signal's per-band onsets) or both: a steady loud bassline reads high as a level but only its
# notes make hits, so "both" keeps some of the level and puts the notes on top. Separately, effects that move have their own motion clock, whose
# rate ("Speed follows") is fixed, the signal's intensity, the song's tempo, or both: how fast/often it moves.
# Life and Melt are left out: they build up over time from where they started, so a strength that changes
# every frame breaks them.

SIGNALS = ["bass", "lowmid", "mid", "high", "beat", "level"]
REACT_ORDER = ["pixelate", "mirror", "kaleidoscope", "wave", "glitch", "outline", "scanlines", "static", "flash", "invert"]
SWITCHED = {"invert", "mirror", "kaleidoscope", "outline"}  # no strength: on while the strength is past half
BAND_SIGNALS = ("bass", "lowmid", "mid", "high")
REACT_TO = ["level", "hits", "both"]  # what a band row follows
BOTH_MIX = (0.65, 0.6)  # "both": this much of the level plus this much of the hits (capped at 1)
REACT_DEFAULTS = [
    {"signal": "bass", "effect": "glitch", "amount": 80, "threshold": 45, "react": "both"},
    {"signal": "lowmid", "effect": "none", "amount": 50, "threshold": 35, "react": "level"},
    {"signal": "mid", "effect": "wave", "amount": 50, "threshold": 50, "react": "both"},
    {"signal": "high", "effect": "static", "amount": 40, "threshold": 50, "react": "hits"},
    {"signal": "beat", "effect": "invert", "amount": 100, "threshold": 0},
    {"signal": "level", "effect": "scale", "amount": 50, "threshold": 0},
]
# Effects with motion: cycles per second at 1x. A cycle is what "lock to beat" fits to one beat: one wave
# (a crest back where it was), the scanlines moving 6 rows, one flash, one new glitch/static pattern.
# 1x matches how they moved before they had their own clocks.
MOTION = {"wave": 6 / (2 * math.pi), "scanlines": 2.0, "flash": 2.5, "glitch": 10.0, "static": 10.0}
FOLLOW = ["fixed", "signal", "tempo", "both"]
LOCKS = [0, 1, 2, 4]  # beats per cycle when following the tempo; 0 = not locked (just faster/slower)
MOTION_DEFAULTS = {   # also used for rows saved before speed settings existed
    "wave": {"follow": "tempo", "speed": 1.0, "lock": 2},  # 1 per beat is frantic at 160 BPM; this is the old speed at 120
    "scanlines": {"follow": "tempo", "speed": 1.0, "lock": 1},
    "flash": {"follow": "tempo", "speed": 1.0, "lock": 1},
    "glitch": {"follow": "signal", "speed": 1.0, "lock": 0},
    "static": {"follow": "signal", "speed": 1.0, "lock": 0},
}
SPEED_RANGE = (0.25, 4.0)
TEMPO_BASE = 120     # BPM at which tempo-following runs at 1x
LOCK_GAIN = 0.8      # locked clocks correct this share of their phase error per beat
LEVEL_SMOOTH = 0.15  # seconds: "Its signal" eases the speed so it doesn't jitter


def clean_react_map(rows):
    """One row per signal, in SIGNALS order, with known effects and amounts/thresholds clamped to 0..100.
    Band rows get what they react to (level / hits / both; missing: the default for that band). Rows whose effect
    moves also get follow / speed / lock (missing ones: that effect's MOTION_DEFAULTS)."""
    given = {r.get("signal"): r for r in rows or [] if isinstance(r, dict)}
    out = []
    for d in REACT_DEFAULTS:
        r = given.get(d["signal"], d)
        eff = r.get("effect", d["effect"])
        if eff not in REACT_ORDER and eff != "none" and not (eff == "scale" and d["signal"] == "level"):
            eff = "none"
        num = lambda k: max(0, min(100, int(float(r.get(k, d[k]) or 0))))
        row = {"signal": d["signal"], "effect": eff, "amount": num("amount"), "threshold": min(90, num("threshold"))}
        if d["signal"] in BAND_SIGNALS:
            row["react"] = r.get("react") if r.get("react") in REACT_TO else d["react"]
        if eff in MOTION:
            md = MOTION_DEFAULTS[eff]
            try:
                speed = float(r.get("speed") or md["speed"])
            except (TypeError, ValueError):
                speed = md["speed"]
            try:
                lock = int(r["lock"]) if r.get("lock") is not None else md["lock"]
            except (TypeError, ValueError):
                lock = md["lock"]
            row.update(follow=r.get("follow") if r.get("follow") in FOLLOW else md["follow"],
                       speed=round(max(SPEED_RANGE[0], min(SPEED_RANGE[1], speed)), 2),
                       lock=lock if lock in LOCKS else md["lock"])
        out.append(row)
    return out


def react_plan(rows, reading):
    """{effect: (strength 0..1, row, signal 0..1)} for this music reading. Several signals on one effect: the
    strongest wins (and its speed settings); Loudness set to "scale" turns everything down in quiet parts."""
    out, scale = {}, 1.0
    for r in rows:
        sig = signal_value(r, reading)
        thr = r["threshold"] / 100
        v = max(0.0, (sig - thr) / (1 - thr)) * r["amount"] / 100
        if r["effect"] == "scale":
            scale = 1 - r["amount"] / 100 + r["amount"] / 100 * sig
        elif r["effect"] in REACT_ORDER and (r["effect"] not in out or v > out[r["effect"]][0]):
            out[r["effect"]] = (v, r, sig)
    return {k: (v * scale, r, sig) for k, (v, r, sig) in out.items()}


def signal_value(row, reading):
    """A row's signal 0..1 in this reading: the beat pulse, the loudness, or a band's level / hits / both."""
    name = row["signal"]
    if name == "beat":
        return reading["pulse"]
    level = reading.get(name, 0.0)
    if name not in BAND_SIGNALS:
        return level
    hit = (reading.get("hits") or {}).get(name, 0.0)
    react = row.get("react", "level")
    if react == "hits":
        return hit
    if react == "both":
        return min(1.0, BOTH_MIX[0] * level + BOTH_MIX[1] * hit)
    return level


def react_strengths(rows, reading):
    """{effect: strength 0..1} for this music reading (see react_plan)."""
    return {k: v[0] for k, v in react_plan(rows, reading).items()}


def motion_rate(row, kind, level, reading, cycles=0.0, dt=0.0):
    """Cycles per second for an effect's motion clock over the next dt. level: its signal, smoothed (0..1).
    Tempo-locked: one cycle per `lock` beats, nudged towards the beat (beat_phase) when the tempo is sure."""
    follow, speed, lock = row.get("follow", "fixed"), row.get("speed", 1.0), row.get("lock", 0)
    bpm = reading.get("bpm") or TEMPO_BASE
    if follow == "tempo" and lock:
        rate = bpm / 60 / lock
        phase = reading.get("beat_phase")
        if phase is not None:
            ahead = (cycles + rate * dt) % 1 * lock  # where a plain step would land, in beats
            err = (ahead - phase + 0.5) % 1 - 0.5  # beats ahead (+) or behind (-) of the beat
            rate *= 1 - max(-0.6, min(0.6, LOCK_GAIN * err))
        return rate
    rate = MOTION[kind] * speed
    if follow in ("tempo", "both"):
        rate *= bpm / TEMPO_BASE
    if follow in ("signal", "both"):
        rate *= 2 ** (3 * level - 1.5)  # quiet 0.35x .. half 1x .. loud 2.8x
    return rate


class Reactive:
    """Applies the music-driven effects to a frame, in REACT_ORDER. Effects that move run on their own clock:
    cycles += rate x dt each frame, so a change of rate changes the speed from there on, never the position."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.fx = {k: Effect(k, 0.0, None, 0.5, self.rng) for k in REACT_ORDER}
        self.level = dict.fromkeys(MOTION, 0.0)
        self.last = None

    def advance(self, now, plan, reading):
        """Move each moving effect's clock on to `now` at its current rate."""
        dt = 0.0 if self.last is None else max(0.0, min(0.5, now - self.last))
        self.last = now
        k = 1 - math.exp(-dt / LEVEL_SMOOTH)
        for kind, (_, row, sig) in plan.items():
            if kind in MOTION:
                e = self.fx[kind]
                self.level[kind] += (sig - self.level[kind]) * k
                e.cycles = (e.cycles or 0.0) + motion_rate(row, kind, self.level[kind], reading, e.cycles or 0.0, dt) * dt

    def apply(self, frame, now, strengths, reading=None):
        """strengths: {effect: strength} (wall-clock motion), or with a reading, react_plan()'s output."""
        if reading is not None:
            self.advance(now, strengths, reading)
            self.fx["wave"].sway = 0.85 + 0.5 * reading.get("groove", 0.0)
        for kind in REACT_ORDER:
            s = strengths.get(kind, 0.0)
            if isinstance(s, tuple):
                s = s[0]
            if s < (0.5 if kind in SWITCHED else 0.08):
                continue
            e = self.fx[kind]
            e.strength = min(1.0, s)
            if e.cycles is not None:
                c = frame.copy()
                getattr(e, "_" + kind)(c, e.cycles / MOTION[kind])  # 1x = the old wall-clock speed
                frame = c
            else:
                frame = e.apply(frame, now)
        return frame


def describe_settings():
    """For the UI: the effect choices."""
    return [{"id": k, "label": v} for k, v in EFFECTS.items()]
