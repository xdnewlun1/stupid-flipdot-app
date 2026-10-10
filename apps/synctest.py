"""Sync test: the whole board flips (black <-> white) on every click of a click track, to measure how far the
board runs behind the speakers.

Play the click track (GET /api/sync/click.wav) to a Snapcast group with the board in it and film a speaker and
the board together in slow motion: the time from a click to the discs flipping is what to delay the other
speakers by. The clicks are read from the raw loudness of each analysis frame (no smoothing or auto-gain, so the
flip is instant), and the board is driven exactly like the music visualizer: the same board delay, a draw as each
analysis frame comes due at the visualizer's rate, and steady board timing. Only the visualizer's bars rising
(its smoothing) isn't included.
"""

import array
import io
import math
import wave

import audio_signal
from fonts import SMALL
from canvas import wrap_text

from .base import App, P, register
from .visualizer import LIVE_FPS

CLICK_PERIOD = 1.013  # seconds between clicks: not a whole number of the board's 40 ms pictures, so successive
                      # clicks land at different points between pictures (average a few, like the music does)
CLICK_RATE = 44100
CLICK_MS = 10         # each click: a 2 kHz tone burst this long, with 1 ms fades so it starts sharply but cleanly


def click_wav(period=CLICK_PERIOD, seconds=120):
    """A mono 16-bit WAV of clicks every `period` seconds (the first at 1 s)."""
    period = max(0.25, min(10.0, float(period)))
    seconds = max(5, min(600, int(seconds)))
    n = int(CLICK_RATE * CLICK_MS / 1000)
    fade = CLICK_RATE // 1000
    click = array.array("h", [
        int(29000 * min(1.0, i / fade, (n - 1 - i) / fade) * math.sin(2 * math.pi * 2000 * i / CLICK_RATE))
        for i in range(n)]).tobytes()
    pcm = bytearray(CLICK_RATE * seconds * 2)
    t = 1.0
    while t < seconds - CLICK_MS / 1000:
        at = int(t * CLICK_RATE) * 2
        pcm[at:at + len(click)] = click
        t += period
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(CLICK_RATE)
        w.writeframes(bytes(pcm))
    return buf.getvalue()


@register
class SyncTest(App):
    id = "sync_test"
    title = "Sync test (click track)"
    category = "Utilities"
    description = ("Flips every disc on each click of the click track, to line the board up with your speakers. Download "
                   "the track from /api/sync/click.wav, play it to the board's Snapcast group, and film a speaker and the "
                   "board in slow motion: the time from a click to the flip is what to delay the other speakers by.")
    steady_timing = True  # the same board timing as the music visualizer
    params = [
        P("threshold", "number", 0.5, "Click level (0 = -60 dB, 1 = full scale)", min=0.1, max=0.95, step=0.05),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.white = False
        self.armed = True
        self.clicks = 0
        self.interval = 1 / LIVE_FPS

    def render(self, c, now):
        name, s = audio_signal.raw(self.ctx.store, now)
        if s is None or not s.get("live"):
            self.interval = 0.25
            msg = ("Waiting for the click track: play it to the board's group" if s is not None
                   else "No live audio: choose Snapcast in Settings, Music")
            for i, line in enumerate(wrap_text(msg.upper(), SMALL, c.w - 2)[:6]):
                c.text_center(2 + i * 7, line, SMALL)
            return
        thr = max(0.1, min(0.95, float(self.p["threshold"] or 0.5)))
        peak = float(s.get("raw_peak") or 0.0)
        if self.armed and peak >= thr:
            self.white = not self.white  # every click flips every disc
            self.armed = False
            self.clicks += 1
        elif not self.armed and peak < thr * 0.6:
            self.armed = True
        c.clear(1 if self.white else 0)
        self.interval = audio_signal.steady_interval(self.ctx.store, now, LIVE_FPS) or 1 / LIVE_FPS

    def describe(self):
        return f"Sync test: {self.clicks} clicks"
