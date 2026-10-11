"""Synthetic music for the analysis tests (stdlib only): funk, disco/house, a quiet jazz trio, a loud brickwalled
EDM loop, an intro that goes into a drop, a loud part that falls into a quiet breakdown, and changes between songs
of very different levels.

Each signal is rendered to 16-bit PCM at 22.05 kHz and run through the real link pipeline: the Sendspin/Snapcast
analyser (sendspin_legacy._Analyzer, 50 frames a second) and the link's smoothing (SendspinLink._apply), so the
tests see what audio_signal sees on the board. `link_signal(song)` returns [(t, link-style signal dict)] at 50/s
and the song's note times. Results are cached per process.
"""

import array
import functools
import math
import os
import random
import sys
import tempfile
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RATE = 22050


class Mix:
    """A float sample buffer plus note times, by part (kick, bass, snare, hat, ...)."""

    def __init__(self, seconds, seed=1):
        self.n = int(seconds * RATE)
        self.buf = [0.0] * self.n
        self.rng = random.Random(seed)
        self.noise = [self.rng.uniform(-1, 1) for _ in range(RATE)]
        self.hp = [a - b for a, b in zip(self.noise, self.noise[1:] + self.noise[:1])]  # crude high-pass
        lp, x = [], 0.0
        for v in self.noise:  # crude band-pass for snares: low-passed noise, minus a slower low-pass
            x += (v - x) * 0.25
            lp.append(x)
        y, self.bp = 0.0, []
        for v in lp:
            y += (v - y) * 0.03
            self.bp.append((v - y) * 2.5)
        self.notes = {}

    def _add(self, part, t0, dur, fn):
        self.notes.setdefault(part, []).append(t0)
        i0 = int(t0 * RATE)
        buf = self.buf
        for i in range(max(0, i0), min(self.n, i0 + int(dur * RATE))):
            buf[i] += fn((i - i0) / RATE, i)

    def kick(self, t0, amp=1.0):
        ph = [0.0]

        def f(t, i):
            ph[0] += 2 * math.pi * (48 + 90 * math.exp(-t / 0.025)) / RATE
            return amp * math.sin(ph[0]) * math.exp(-t / 0.22)
        self._add("kick", t0, 0.4, f)

    def bass(self, t0, dur, freq, amp=0.6):
        w = 2 * math.pi * freq

        def f(t, i):
            env = min(1.0, t / 0.006) * min(1.0, (dur - t) / 0.02) * (0.6 + 0.4 * math.exp(-t / 0.1))
            return amp * env * (math.sin(w * t) + 0.3 * math.sin(2 * w * t))
        self._add("bass", t0, dur, f)

    def sub(self, t0, dur, freq, amp):
        """A held sine (no note times): constant sub-bass."""
        w = 2 * math.pi * freq
        buf = self.buf
        for i in range(int(t0 * RATE), min(self.n, int((t0 + dur) * RATE))):
            buf[i] += amp * math.sin(w * i / RATE)

    def snare(self, t0, amp=0.7):
        bp = self.bp
        self._add("snare", t0, 0.25, lambda t, i: amp * (bp[i % RATE] * math.exp(-t / 0.07)
                                                         + 0.5 * math.sin(2 * math.pi * 190 * t) * math.exp(-t / 0.04)))

    def hat(self, t0, amp=0.3, open_=False):
        hp, tau = self.hp, 0.15 if open_ else 0.025
        self._add("open" if open_ else "hat", t0, 5 * tau, lambda t, i: amp * hp[(i * 7) % RATE] * math.exp(-t / tau))

    def ride(self, t0, amp=0.25):
        hp = self.hp
        self._add("ride", t0, 0.6, lambda t, i: amp * math.exp(-t / 0.25) * (
            0.6 * hp[(i * 3) % RATE] + 0.4 * math.sin(2 * math.pi * 3800 * t) * math.sin(2 * math.pi * 5100 * t)))

    def chord(self, t0, dur, freqs, amp=0.25, part="chord", saw=False):
        ws = [2 * math.pi * f for f in freqs]

        def f(t, i):
            env = min(1.0, t / 0.005) * min(1.0, (dur - t) / 0.03)
            if saw:
                return amp * env * sum(((t * w / (2 * math.pi)) % 1.0) * 2 - 1 for w in ws) / len(ws)
            return amp * env * sum(math.sin(w * t) for w in ws) / len(ws)
        self._add(part, t0, dur, f)

    def master(self, peak_db, drive=0.0):
        """Scale (or with drive, tanh-clip: brickwalled) so the peak sits at peak_db dBFS."""
        buf = self.buf
        if drive:
            top = max(abs(v) for v in buf) or 1
            buf[:] = [math.tanh(drive * v / top) for v in buf]
        top = max(abs(v) for v in buf) or 1
        g = 10 ** (peak_db / 20) / top
        return array.array("h", (int(max(-32767, min(32767, v * g * 32767))) for v in buf))


def funk(seconds=16.0, bpm=100.0, t0=0.0, m=None):
    """Syncopated kick (no kick on beat 3), snare on 2 and 4 with ghost notes, swung 16th hats, a moving
    bassline on 16ths and guitar chops."""
    m = m or Mix(seconds, 2)
    s16 = 60 / bpm / 4
    swing = 0.23 * s16  # the second 16th of each 8th comes late: ratio (1 + 0.23) / (1 - 0.23) = 1.6
    kick_steps = (0, 3, 7, 10)
    ghost = (7, 9, 15)
    bassline = ((0, 55.0, 2), (3, 55.0, 1), (6, 82.4, 2), (8, 73.4, 1), (10, 65.4, 2), (11, 55.0, 1), (14, 98.0, 2))
    chops = (2, 6, 10, 14)
    t, bar = t0, 16 * s16
    while t + bar <= t0 + seconds + 1e-9:
        for st in range(16):
            at = t + st * s16 + (swing if st % 2 else 0)
            m.hat(at, 0.45 if st % 2 else 0.6)
            if st in kick_steps:
                m.kick(at, 1.0)
            if st in (4, 12):
                m.snare(at, 0.75)
            elif st in ghost:
                m.snare(at, 0.18)
            if st in chops:
                m.chord(at, 0.07, (660, 830, 990), 0.22, part="chop")
        for st, f, n in bassline:
            m.bass(t + st * s16 + (swing if st % 2 else 0), n * s16 * 0.9, f, 0.55)
        t += bar
    return m


def disco(seconds=16.0, bpm=120.0, t0=0.0, m=None):
    """Four on the floor, open hats on the off-beats, a clap on 2 and 4, an octave bassline on 8ths."""
    m = m or Mix(seconds, 3)
    beat = 60 / bpm
    t, k = t0, 0
    while t + beat <= t0 + seconds + 1e-9:
        m.kick(t, 1.0)
        m.hat(t + beat / 2, 0.6, open_=True)
        m.hat(t + beat / 4, 0.2)
        m.hat(t + 3 * beat / 4, 0.2)
        if k % 2:
            m.snare(t, 0.5)
        m.bass(t, beat / 2 * 0.85, 55.0, 0.45)
        m.bass(t + beat / 2, beat / 2 * 0.85, 110.0, 0.4)
        if k % 4 == 1:
            m.chord(t + beat / 2, beat * 0.3, (523, 659, 784), 0.15)
        t += beat
        k += 1
    return m


def jazz(seconds=16.0, bpm=132.0, t0=0.0, m=None):
    """A trio at a low level: swung ride (ding, ding-a ding), walking bass on quarters, soft brushes on 2 and 4,
    piano comping on the swung off-beats."""
    m = m or Mix(seconds, 4)
    beat = 60 / bpm
    walk = (55.0, 61.7, 65.4, 73.4, 82.4, 73.4, 65.4, 61.7)
    t, k = t0, 0
    rng = random.Random(9)
    while t + beat <= t0 + seconds + 1e-9:
        m.ride(t, 0.6)
        if k % 2:
            m.ride(t + beat * 2 / 3, 0.4)  # the swung "a"
            m.snare(t, 0.12)
        m.bass(t, beat * 0.85, walk[k % 8], 0.5)
        if rng.random() < 0.6:
            m.chord(t + beat * 2 / 3, beat * 0.4, (262, 330, 392, 466), 0.45, part="comp")
        t += beat
        k += 1
    return m


def edm(seconds=16.0, bpm=128.0, t0=0.0, m=None, peak_db=None):
    """Brickwalled: a constant loud sub-bass, kicks on every beat, off-beat hats, saw chords."""
    m = m or Mix(seconds, 5)
    beat = 60 / bpm
    m.sub(t0, seconds, 45.0, 0.9)
    t, k = t0, 0
    while t + beat <= t0 + seconds + 1e-9:
        m.kick(t, 1.2)
        m.hat(t + beat / 2, 0.6, open_=True)
        if k % 2:
            m.snare(t, 0.5)
        m.chord(t, beat * 0.9, (220, 277, 330), 0.35, saw=True)
        t += beat
        k += 1
    return m


Song = namedtuple("Song", "signal notes bpm changes")  # changes: [(t, label)]


def _render(name):
    if name == "funk":
        m = funk(14.4)
        return m, m.master(-10), 100.0, []
    if name == "disco":
        m = disco(16)
        return m, m.master(-8), 120.0, []
    if name == "jazz":
        m = jazz(16)
        return m, m.master(-25), 132.0, []
    if name == "edm":
        m = edm(16)
        return m, m.master(-3, drive=4.0), 128.0, []
    if name == "drop":  # 8 s quiet intro (hats and a pad), then the drop
        m = Mix(18, 6)
        beat = 60 / 124
        for k in range(int(8 / beat)):
            m.hat(k * beat + beat / 2, 0.15)
            m.chord(k * beat, beat * 0.95, (110, 220, 330), 0.08)
        edm(10, 124.0, t0=8.0, m=m)
        return m, m.master(-3, drive=3.0), 124.0, [(8.0, None)]
    if name == "breakdown":  # 10 s of the loud loop, then (no gap) 8 s of a quiet pad and hats, 18 dB down
        m = edm(10, 128.0, m=Mix(18, 7))
        beat = 60 / 128
        for k in range(int(8 / beat)):
            m.hat(10 + k * beat + beat / 2, 0.06)
            m.chord(10 + k * beat, beat * 0.95, (110, 220, 330), 0.12)
        return m, m.master(-3, drive=4.0), 128.0, [(10.0, None)]
    if name == "change":  # a quiet jazz trio, then (new song) loud EDM, then the jazz trio again
        a, b, c = jazz(10), edm(10), jazz(10)
        quiet, loud = a.master(-25), b.master(-3, drive=4.0)
        tail = c.master(-25)
        notes = {}
        for off, mm in ((0, a), (10, b), (20, c)):
            for part, ts in mm.notes.items():
                notes.setdefault(part, []).extend(t + off for t in ts)
        m = Mix(0.01)
        m.notes = notes
        return m, quiet + loud + tail, None, [(10.0, ("Loud song", "B")), (20.0, ("Quiet song", "C"))]
    raise KeyError(name)


@functools.lru_cache(maxsize=None)
def song(name):
    """Song(signal=[(t, link signal)], notes={part: [t]}, bpm, changes)."""
    import sendspin_legacy
    from sendspin_link import SendspinLink, UNIT
    m, pcm, bpm, changes = _render(name)
    fmt = namedtuple("Fmt", "sample_rate channels bit_depth")(RATE, 1, 16)
    an = sendspin_legacy._Analyzer()
    an.configure(fmt)
    an.hop = round(RATE / 50)  # as snapcast_link sets it: 50 frames a second
    link = SendspinLink(tempfile.mkdtemp(prefix="flipdot-sig-"), advertise=False)
    link.frame_dt = an.hop / an.rate
    meta = {"title": "Song", "artist": "A"}
    out, chunk = [], 2205
    for i in range(0, len(pcm), chunk):
        for ts, f in an.feed(int(i / RATE * 1e6), pcm[i:i + chunk].tobytes()):
            t = ts / 1e6 + 0.5 * an.hop / RATE  # when the frame's audio has been heard
            for at, md in changes:
                if md and t >= at:
                    meta = {"title": md[0], "artist": md[1]}
            link._apply(f, t)
            out.append((t, {"live": True, "loudness": link.loudness, "spectrum": list(link.spectrum),
                            "beat_age": t - link.beat_at, "downbeat": link.downbeat, "metadata": dict(meta)}))
    return Song(out, m.notes, bpm, changes)


def run(name, fps=25, start=1000.0, proc=None):
    """Step an audio_signal.Processor through a song at `fps` (the board's frame rate). -> [(t, reading)]."""
    import audio_signal
    proc = proc or audio_signal.Processor()
    out, nxt = [], None
    for t, s in song(name).signal:
        if nxt is None or t >= nxt:
            nxt = (nxt or t) + 1 / fps
            out.append((t, proc.step(s, start + t)))
    return out
