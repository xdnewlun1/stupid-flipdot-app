"""Random effects for slideshow items: now and then an item gets inverted, glitched, melted,
run through Game of Life… for the whole item or a short burst. All cheap enough for a Pi."""

import math
import random

import fastpath
from canvas import Canvas, _BITS, _FLIP
from transitions import pixelate, vortex

_FF = bytes([0] + [255] * 255)  # dot -> 0x00/0xFF, for masks used as big integers
_EDGES = {}  # (w, h) -> masks of the dots that have a neighbour on each side


def _edges(w, h):
    """Big-integer masks (0xFF per dot, as apps/layers.py works) for the dots that are not in the first/last column
    or row, so shifting a picture by one dot can tell a missing neighbour from the board's edge."""
    m = _EDGES.get((w, h))
    if m is None:
        n = w * h
        not_first, not_last = bytearray(b"\x01" * n), bytearray(b"\x01" * n)
        not_first[0::w] = bytes(h)
        not_last[w - 1::w] = bytes(h)
        m = _EDGES[w, h] = {
            "left": int.from_bytes(not_first.translate(_FF), "big"), "right": int.from_bytes(not_last.translate(_FF), "big"),
            "up": int.from_bytes((bytes(w) + b"\x01" * (n - w)).translate(_FF), "big"),
            "down": int.from_bytes((b"\x01" * (n - w) + bytes(w)).translate(_FF), "big"),
            "all": (1 << (8 * n)) - 1,
        }
    return m

EFFECTS = {
    "invert": "Invert",
    "sweep": "Invert sweep",
    "flash": "Flash",
    "glitch": "Glitch",
    "mirror": "Mirror",
    "kaleidoscope": "Kaleidoscope",
    "wave": "Wave",
    "vortex": "Vortex",
    "scanlines": "Scanlines",
    "pixelate": "Pixelate",
    "outline": "Outline",
    "static": "Static",
    "life": "Game of Life",
    "melt": "Melt",
}
ANIMATED = {"flash", "sweep", "glitch", "wave", "vortex", "scanlines", "static", "life", "melt"}


def roll(settings, rng, now, item_seconds, hold=0.0, cross=0.25):
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
    return Effect(kind, start, end, float(settings.get("fx_strength") or 50) / 100, rng, hold, cross)


class Effect:
    def __init__(self, kind, start, end, strength, rng, hold=0.0, cross=0.25):
        self.kind, self.start, self.end = kind, start, end
        self.strength = max(0.05, min(1.0, strength))
        self.rng = rng or random.Random()
        self.hold = hold  # how long the discs take to show a whole new picture (Board.settle_time()): Flash's floor
        self.cross = cross  # how long the panels' own flip wave takes to cross the board (Board.cross_time()): Sweep's pace
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
            return
        rate = 3 + 6 * self.strength  # toggles a second...
        if self.hold:
            rate = min(rate, 1 / self.hold)  # ...but each picture stays until the discs have shown it
        if int(t * rate) % 2 == 0:
            c.invert()

    def sweep_length(self):
        """How long one sweep takes: the band's front crosses the board at the panels' wave speed and its back follows
        (the band is `strength` of the board wide: 1 = the whole picture inverted at the peak, 0.5 = a half-board bar)."""
        return self.cross * (1 + max(0.1, self.strength))

    def _sweep(self, c, t):
        """An inverted band crossing the picture left to right, the way a change sweeps the panels; the picture keeps
        moving under it. On its own (a random effect) it crosses again after a pause."""
        w, h, px = c.w, c.h, c.px
        length = self.sweep_length()
        t %= length + self.cross / 2
        front = w * t / self.cross
        back = front - w * max(0.1, self.strength)
        x0, x1 = max(0, round(back)), min(w, round(front))
        if x1 <= x0:
            return
        for y in range(h):
            s = y * w
            px[s + x0:s + x1] = px[s + x0:s + x1].translate(_FLIP)

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
                px[y * w:(y + 1) * w] = px[y * w:(y + 1) * w].translate(_FLIP)

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

    VORTEX_TURN = 1.5 * math.pi  # at full strength the middle is turned this far, swaying one way then the other

    def _vortex(self, c, t):
        """A whirlpool: the middle of the picture turns, the edges stay put. It sways there and back once a cycle
        (on the beat when locked to the tempo), as far as the strength says."""
        twist = self.VORTEX_TURN * self.strength * math.sin(2 * math.pi * t * MOTION["vortex"])
        c.px[:] = vortex(c.px, c.w, c.h, twist)

    def _scanlines(self, c, t):
        w = c.w
        gap = 2 if self.strength > 0.6 else 3
        off = int(t * 12)
        for y in range(c.h):
            if (y + off) % gap == 0:
                c.px[y * w:(y + 1) * w] = bytes(w)

    def _pixelate(self, c, t):
        c.px[:] = pixelate(c.px, c.w, c.h, 2 + round(4 * self.strength))

    def _outline(self, c, t):
        """Keep the white dots that have a black neighbour on the board (the board's edge doesn't count): the
        picture as one big integer, shifted a dot each way, as apps/layers.py composites."""
        w, h = c.w, c.h
        m = _edges(w, h)
        s = int.from_bytes(c.px.translate(_FF), "big")
        left, right = s >> 8, (s << 8) & m["all"]  # the dot to the left of / right of each dot
        up, down = s >> (8 * w), (s << (8 * w)) & m["all"]
        missing = (m["left"] & ~left) | (m["right"] & ~right) | (m["up"] & ~up) | (m["down"] & ~down)
        c.px[:] = (s & missing).to_bytes(w * h, "big").translate(_BITS)

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
            cells = st["cells"] = fastpath.life_step(cells, w, h)
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
                px[x::w] = bytes(drop) + src[x:(h - drop) * w:w]  # the column, shifted down, in one slice


# ---- reacting to music ---------------------------------------------------------------------
# Each signal drives one effect with strength = signal above its threshold x amount: how much it does (how far
# the wave bends, how many dots of static). A band's signal is its level, its hits (each new bass note, snare,
# hat: audio_signal's per-band onsets) or both: a steady loud bassline reads high as a level but only its
# notes make hits, so "both" keeps some of the level and puts the notes on top. Separately, effects that move have their own motion clock, whose
# rate ("Speed follows") is fixed, the signal's intensity, the song's tempo, or both: how fast/often it moves.
# Life and Melt are left out: they build up over time from where they started, so a strength that changes
# every frame breaks them.

SIGNALS = ["bass", "lowmid", "mid", "high", "beat", "level"]
REACT_ORDER = ["pixelate", "mirror", "kaleidoscope", "wave", "vortex", "glitch", "outline", "scanlines", "static", "flash", "sweep", "invert"]
SWITCHED = {"invert", "mirror", "kaleidoscope", "outline"}  # no strength: on while the strength is past half
TRIGGERED = {"sweep"}  # starts as its signal rises past half and runs to the end (the amount sets the band's width)
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
MOTION = {"wave": 6 / (2 * math.pi), "vortex": 0.5, "scanlines": 2.0, "flash": 2.5, "glitch": 10.0, "static": 10.0}
FOLLOW = ["fixed", "signal", "tempo", "both"]
LOCKS = [0, 1, 2, 4]  # beats per cycle when following the tempo; 0 = not locked (just faster/slower)
MOTION_DEFAULTS = {   # also used for rows saved before speed settings existed
    "wave": {"follow": "tempo", "speed": 1.0, "lock": 2},  # 1 per beat is frantic at 160 BPM; this is the old speed at 120
    "vortex": {"follow": "tempo", "speed": 1.0, "lock": 2},  # in on one beat, out on the next
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
    cycles += rate x dt each frame, so a change of rate changes the speed from there on, never the position.

    A switched effect (invert on a beat) changes every dot of the board, which the discs take a while to show: the
    serial data for the busiest bus and then the panels' sweep, well over 100 ms on a three-column board. A beat
    pulse is past half for about 100 ms, and meanwhile the GIF and the other effects keep changing the picture, so
    the board was sent the inverted picture, two more inverted pictures and the plain one before the first had
    landed: a patchwork of panels in different states instead of a flash. So after a switch the picture is held,
    exactly as sent, for `hold` seconds (Board.settle_time()): the board gets the inverted picture, shows it whole,
    gets the plain one, shows that, and only then the GIF carries on.

    The sweep is the other way to invert on a beat with fast content: nothing is held, the inverted band just
    crosses the picture at the speed the panels' own wave would (`cross`, Board.cross_time()) while the GIF and
    the other effects carry on under it. It starts as the beat rises past half and runs to the end whatever the
    pulse does after, then waits for the next beat."""

    def __init__(self, rng=None, hold=0.0, cross=0.25):
        self.rng = rng or random.Random()
        self.hold = hold
        self.cross = cross
        self.fx = {k: Effect(k, 0.0, None, 0.5, self.rng) for k in REACT_ORDER}
        self.level = dict.fromkeys(MOTION, 0.0)
        self.last = None
        self.on = {k: False for k in SWITCHED | TRIGGERED}  # triggered: whether its signal was past half last frame
        self.running = {k: False for k in TRIGGERED}
        self.held = None  # (until, picture) while a switch settles on the discs

    def reset(self):
        """Reacting stopped: the motion clocks pause rather than catch up, and the next picture isn't a switch."""
        self.last = None
        self.on = {k: False for k in SWITCHED | TRIGGERED}
        self.running = {k: False for k in TRIGGERED}
        self.held = None

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
            self.advance(now, strengths, reading)  # the clocks run on through a hold
            self.fx["wave"].sway = 0.85 + 0.5 * reading.get("groove", 0.0)
        if self.held is not None:
            until, pic = self.held
            if now < until:
                return pic
            self.held = None
        switched = False
        for kind in REACT_ORDER:
            s = strengths.get(kind, 0.0)
            sig = s[2] if isinstance(s, tuple) else s  # the raw signal (the beat pulse), before amount and threshold
            if isinstance(s, tuple):
                s = s[0]
            if kind in TRIGGERED:
                e = self.fx[kind]
                e.cross = self.cross
                past, self.on[kind] = self.on[kind], sig >= 0.5
                if self.running[kind] and now - e.start >= e.sweep_length() - 1e-9:
                    self.running[kind] = False
                if not self.running[kind] and self.on[kind] and not past and s > 0:
                    e.start, e.strength = now, min(1.0, s)
                    self.running[kind] = True
                if self.running[kind]:
                    frame = e.apply(frame, now)
                continue
            if kind in SWITCHED:
                on = s >= 0.5
                if on != self.on[kind]:
                    self.on[kind] = on
                    switched = True
                if not on:
                    continue
            elif s < 0.08:
                continue
            e = self.fx[kind]
            e.strength = min(1.0, s)
            if e.cycles is not None:
                c = frame.copy()
                getattr(e, "_" + kind)(c, e.cycles / MOTION[kind])  # 1x = the old wall-clock speed
                frame = c
            else:
                frame = e.apply(frame, now)
        if switched and self.hold > 0:
            frame = frame.copy()
            self.held = (now + self.hold, frame)
        return frame


def describe_settings():
    """For the UI: the effect choices."""
    return [{"id": k, "label": v} for k, v in EFFECTS.items()]
