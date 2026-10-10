"""Music visualizer.

Signal source, best first:
  * Sendspin (Music Assistant streams loudness/spectrum/beats for what's playing), if the
    optional link is connected — the dots follow the actual music;
  * otherwise a generated beat at a set BPM (or a tempo picked per song), started and stopped
    by Music Assistant's play/pause and changed on every new song. Light enough for a Pi.
"""

import math
import random
import threading
import time
import zlib

from .base import App, P, register

BINS = 16


class _MusicState:
    """Polls Music Assistant (HTTP API) for play state + current song, for the generated beat."""

    def __init__(self, store):
        self.store = store
        self.playing = None
        self.track = None
        self._next = 0
        self._busy = False

    def poll(self):
        if self._busy or time.time() < self._next or not self.store.settings.get("music_assistant", {}).get("url"):
            return
        self._busy = True
        self._next = time.time() + 2
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        from .music import ma_command
        try:
            players = ma_command(self.store, "players/all")
            want = (self.store.settings.get("music_assistant", {}).get("player") or "").lower()
            cands = [p for p in players if p.get("current_media")]
            if want:
                cands = [p for p in cands if want in (str(p.get("player_id", "")).lower(),
                                                      str(p.get("display_name") or p.get("name") or "").lower())]
            playing = [p for p in cands if (p.get("playback_state") or p.get("state")) == "playing"]
            p = (playing or cands or [None])[0]
            self.playing = bool(playing)
            m = (p or {}).get("current_media") or {}
            self.track = (m.get("title"), m.get("artist")) if p else None
        except Exception:
            self.playing = None  # unknown: just keep going
        finally:
            self._busy = False


@register
class Visualizer(App):
    id = "visualizer"
    title = "Music visualizer"
    category = "Widgets"
    description = ("Reacts to the music: real loudness/spectrum/beats from Music Assistant over Sendspin "
                   "when connected (Settings), otherwise a generated beat that follows play/pause and song changes.")
    params = [
        P("style", "select", "rain", options=[
            ["rain", "Rain (heavier with the music, bursts on beats)"], ["bars", "Spectrum bars"],
            ["rings", "Rings on the beat"], ["wave", "Waveform"], ["warp", "Warp speed starfield"],
            ["sparkle", "Sparkle"]]),
        P("source", "select", "auto", options=[
            ["auto", "Sendspin if connected, else generated beat"], ["beat", "Always the generated beat"]]),
        P("bpm", "number", 0, "Generated beat tempo (0 = pick one per song)", min=0, max=220),
        P("song_change", "select", "flash", "When the song changes", options=[
            ["flash", "Flash (invert a few times)"], ["invert", "Invert until the next song"],
            ["burst", "Burst from the middle"], ["none", "Nothing"]]),
        P("beat_flash", "checkbox", False, "Invert briefly on strong beats"),
        P("sensitivity", "number", 1.0, min=0.2, max=3, step=0.1),
        P("invert", "checkbox", False, "Invert"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.ma = _MusicState(ctx.store)
        self.rng = random.Random()
        self.fps = max(5, min(30, float(ctx.store.settings.get("display", {}).get("transition_fps", 15))))
        self.interval = 1 / self.fps
        self.track = None
        self.flash_until = 0
        self.inverted = False
        self.burst_at = -10
        self.last_beat_seen = 0
        self.bars = [0.0] * BINS
        self.caps = [0.0] * BINS
        self.drops = []
        self.rings = []
        self.stars = [[self.rng.uniform(-1, 1), self.rng.uniform(-1, 1), self.rng.uniform(0.1, 1)] for _ in range(70)]
        self.phase = 0.0
        self.source_name = "beat"
        self._seed = 0

    # ---- the music signal --------------------------------------------------------------

    def _generated(self, now, playing):
        """A beat-shaped signal: kick on each beat, hats in between, a slowly wandering body."""
        seed = self._seed
        bpm = float(self.p["bpm"]) or (88 + seed % 52)  # 88..139 bpm, stable per song
        period = 60 / bpm
        t = now - self.started
        beat_no, frac = divmod(t / period, 1)
        since = frac * period
        if not playing:
            return {"loudness": 0.0, "spectrum": [0.0] * BINS, "beat_age": 99, "downbeat": False, "peak": 0}
        body = 0.35 + 0.15 * math.sin(t * 0.37 + seed) + 0.1 * math.sin(t * 1.3 + seed * 0.1)
        kick = math.exp(-since / 0.14)
        hat = math.exp(-abs(since - period / 2) / 0.05)
        spectrum = []
        for i in range(BINS):
            f = i / (BINS - 1)
            v = (kick * (1 - f) ** 2 * 0.9 + hat * f ** 1.5 * 0.6
                 + body * (0.5 + 0.5 * math.sin(t * (0.8 + i * 0.37) + i * 1.7 + seed)) * 0.6)
            spectrum.append(max(0.0, min(1.0, v)))
        return {"loudness": min(1.0, body * 0.6 + kick * 0.5), "spectrum": spectrum,
                "beat_age": since, "downbeat": int(beat_no) % 4 == 0, "peak": kick if int(beat_no) % 4 == 0 else 0}

    def _signal(self, now):
        from sendspin_link import get_link
        link = get_link()
        if self.p["source"] != "beat" and link is not None and link.connected():
            s = link.signal()
            md = s.get("metadata") or {}
            self._song((md.get("title"), md.get("artist")) if md.get("title") else None, now)
            if s["live"]:
                self.source_name = "sendspin"
                return s
            playing = s["playing"]
        else:
            self.ma.poll()
            self._song(self.ma.track, now)
            playing = self.ma.playing
        self.source_name = "beat"
        return self._generated(now, playing is not False)

    def _song(self, track, now):
        if track == self.track:
            return
        changed = self.track is not None and track is not None
        self.track = track
        self._seed = zlib.crc32(repr(track).encode()) if track else 0
        if not changed:
            return
        mode = self.p["song_change"]
        if mode == "flash":
            self.flash_until = now + 0.9
        elif mode == "invert":
            self.inverted = not self.inverted
        elif mode == "burst":
            self.burst_at = now

    # ---- drawing --------------------------------------------------------------------------

    def render(self, c, now):
        s = self._signal(now)
        sens = float(self.p["sensitivity"])
        level = min(1.0, s["loudness"] * sens)
        spec = [min(1.0, v * sens) for v in s["spectrum"]]
        beat = s["beat_age"] < 0.09 and now - self.last_beat_seen > 0.2
        if beat:
            self.last_beat_seen = now
        getattr(self, "_" + self.p["style"], self._rain)(c, now, level, spec, beat, s)

        if now - self.burst_at < 1.2:  # song-change burst: an expanding ring from the middle
            r = (now - self.burst_at) * 60
            self._ring(c, c.w / 2, c.h / 2, r)
            self._ring(c, c.w / 2, c.h / 2, r * 0.7)
        flash = now < self.flash_until and int((self.flash_until - now) * 6.6) % 2 == 0
        beat_flash = self.p["beat_flash"] and s["beat_age"] < 0.07 and (s.get("downbeat") or s.get("peak", 0) > 0.6)
        if self.inverted != bool(self.p["invert"]) != flash != bool(beat_flash):
            c.invert()

    def _rain(self, c, now, level, spec, beat, s):
        w, h, px = c.w, c.h, c.px
        spawn = level * 3.5 + (10 if beat else 0)
        for _ in range(int(spawn) + (1 if self.rng.random() < spawn % 1 else 0)):
            self.drops.append([self.rng.randrange(w), -self.rng.uniform(0, 4),
                               0.6 + level * 1.6 + self.rng.random() * 0.4, 2 + int(level * 4)])
        alive = []
        for d in self.drops:  # d = [x, y, speed, length]; dots written straight into the canvas
            d[1] += d[2]
            x, y = d[0], int(d[1])
            if y - d[3] >= h:
                continue
            for yy in range(max(0, y - d[3] + 1), min(h, y + 1)):
                px[yy * w + x] = 1
            if y >= h - 1:  # splash
                if x > 0: px[(h - 1) * w + x - 1] = 1
                if x < w - 1: px[(h - 1) * w + x + 1] = 1
            alive.append(d)
        self.drops = alive[-160:]

    def _bars(self, c, now, level, spec, beat, s):
        w = c.w // BINS
        for i, v in enumerate(spec):
            self.bars[i] = max(v, self.bars[i] * 0.82)
            self.caps[i] = max(self.bars[i], self.caps[i] - 0.025)
            h = round(self.bars[i] * (c.h - 2))
            x = i * w + (c.w - w * BINS) // 2
            c.rect(x, c.h - h, w - 1, h, fill=True)
            cap = c.h - 1 - round(self.caps[i] * (c.h - 2))
            c.line(x, cap, x + w - 2, cap)

    def _rings(self, c, now, level, spec, beat, s):
        if beat:
            self.rings.append([self.rng.uniform(15, c.w - 15), self.rng.uniform(8, c.h - 8), now,
                               1.4 if s.get("downbeat") else 1.0])
        keep = []
        for x, y, t0, big in self.rings:
            r = (now - t0) * 30 * big
            if r < 50:
                self._ring(c, x, y, r)
                keep.append([x, y, t0, big])
        self.rings = keep[-12:]
        c.circle(c.w / 2, c.h / 2, 2 + level * 10 * (1.3 if beat else 1), fill=True)

    @staticmethod
    def _ring(c, cx, cy, r):
        """Circle outline by walking its circumference (much cheaper than scanning the area)."""
        steps = max(8, int(r * 6.3))
        for k in range(steps):
            a = k * 6.2832 / steps
            c.set(cx + r * math.cos(a), cy + r * math.sin(a))

    def _wave(self, c, now, level, spec, beat, s):
        self.phase += 0.25 + level * 0.6
        mid = c.h / 2
        prev = None
        for x in range(c.w):
            y = mid
            for i in range(0, BINS, 2):
                y += spec[i] * (c.h / 6) * math.sin(x * (0.06 + i * 0.03) + self.phase * (1 + i * 0.15))
            pt = (x, max(0, min(c.h - 1, round(y))))
            if prev:
                c.line(*prev, *pt)
            prev = pt
            c.set(x, round(mid + (mid - pt[1])))  # mirror

    def _warp(self, c, now, level, spec, beat, s):
        speed = 0.008 + level * 0.06 + (0.05 if beat else 0)
        for st in self.stars:
            x0, y0 = c.w / 2 + st[0] / st[2] * c.w / 2, c.h / 2 + st[1] / st[2] * c.h / 2
            st[2] -= speed
            if st[2] <= 0.03:
                st[:] = [self.rng.uniform(-1, 1), self.rng.uniform(-1, 1), 1.0]
                continue
            x1, y1 = c.w / 2 + st[0] / st[2] * c.w / 2, c.h / 2 + st[1] / st[2] * c.h / 2
            if not (0 <= x1 < c.w and 0 <= y1 < c.h):
                st[:] = [self.rng.uniform(-1, 1), self.rng.uniform(-1, 1), 1.0]
                continue
            c.line(x0, y0, x1, y1) if level > 0.4 else c.set(x1, y1)

    def _sparkle(self, c, now, level, spec, beat, s):
        n = int(level * 160) + (300 if beat else 0)
        for _ in range(n):
            band = self.rng.randrange(BINS)
            if self.rng.random() < spec[band] + 0.1:
                x = int((band + self.rng.random()) * c.w / BINS)
                c.set(x, self.rng.randrange(c.h))

    def describe(self):
        src = "live from Music Assistant" if self.source_name == "sendspin" else "generated beat"
        return f"Music visualizer ({self.p['style']}, {src})"
