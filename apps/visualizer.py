"""Music visualizer.

Signal source, best first:
  * live audio from Music Assistant over the link picked in Settings → Music: Sendspin (loudness/
    spectrum/beats for what's playing) or Snapcast (snapcast_link.py analyses the audio itself), if
    connected and the music reaches it — the dots follow the actual music;
  * otherwise a generated beat at a set BPM (or a tempo picked per song), started and stopped
    by Music Assistant's play/pause and changed on every new song. Light enough for a Pi.
"""

import bisect
import math
import random
import threading
import time
import zlib

import audio_signal

from .base import App, P, register

BINS = audio_signal.BINS


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


STYLES = [
    ["rain", "Rain (columns follow the spectrum, the bass fills a puddle)"],
    ["bars", "Spectrum bars (full height, falling peak caps)"],
    ["mountain", "Spectrum mountain (the spectrum as a filled ridge)"],
    ["sunburst", "Sunburst (the spectrum as rays from the middle)"],
    ["waterfall", "Waterfall (scrolling spectrogram, mirrored)"],
    ["rings", "Rings (bass sizes the middle, the other bands the rings)"],
    ["blob", "Blob (metaballs pulsing with the bass)"],
    ["wave", "Waveform (oscilloscope)"],
    ["warp", "Warp speed starfield"],
    ["sparkle", "Sparkle (columns follow the spectrum)"],
]
GAIN = 0.8  # Scale x GAIN multiplies the display values, so the default Scale 1.5 puts loud parts at the edges
ONE = b"\x01" * 256
RAYS = 8  # sunburst rays per quarter


def _span(px, w, y, x0, x1):
    """Dots x0..x1-1 on row y (clipped)."""
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    if x1 > x0:
        px[y * w + x0:y * w + x1] = ONE[:x1 - x0]


def _column(px, w, h, x, y0, y1):
    """Dots y0..y1-1 in column x (clipped)."""
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if y1 > y0 and 0 <= x < w:
        px[y0 * w + x:y1 * w:w] = ONE[:y1 - y0]


def _annulus(c, cx, cy, ri, ro):
    """Fill the ring between radius ri and ro (ri <= 0: a disc), one span pair per row."""
    px, w = c.px, c.w
    for y in range(max(0, int(cy - ro)), min(c.h, int(cy + ro) + 2)):
        dy = y + 0.5 - cy
        if abs(dy) > ro:
            continue
        ho = math.sqrt(ro * ro - dy * dy)
        hi = math.sqrt(ri * ri - dy * dy) if abs(dy) < ri else 0.0
        if hi <= 0:
            _span(px, w, y, round(cx - ho), round(cx + ho))
        else:
            _span(px, w, y, round(cx - ho), round(cx - hi))
            _span(px, w, y, round(cx + hi), round(cx + ho))


def _resample(vals, n):
    """Linear interpolation of vals onto n points (bin centres line up)."""
    m, out = len(vals), []
    for j in range(n):
        p = min(m - 1.0, max(0.0, (j + 0.5) * m / n - 0.5))
        i = min(m - 2, int(p))
        f = p - i
        out.append(vals[i] * (1 - f) + vals[i + 1] * f)
    return out


@register
class Visualizer(App):
    id = "visualizer"
    title = "Music visualizer"
    category = "Widgets"
    description = ("Moves with the frequencies of the music: real loudness/spectrum from Music Assistant over Sendspin "
                   "or Snapcast when connected (Settings → Music), otherwise a generated beat that follows play/pause "
                   "and song changes.")
    params = [
        P("style", "select", "rain", options=STYLES),
        P("scale", "number", 1.5, "Scale: how far it moves (1.5 = loud parts reach the edges)", min=0.5, max=4, step=0.25),
        P("sensitivity", "number", 1.0, "Sensitivity: higher shows quiet parts bigger", min=0.2, max=3, step=0.1),
        P("smoothing", "number", 0.3, "Smoothing: 0 = snappy, 1 = slow fall", min=0, max=1, step=0.1),
        P("beat_effects", "select", "off", "Beat effects", options=[
            ["off", "Off (follow the frequencies only)"], ["subtle", "Subtle (a ring from the middle)"],
            ["big", "Big (a thick shockwave, the board flashes every 4th beat)"]]),
        P("layout", "select", "bottom", options=[
            ["bottom", "From the bottom"], ["center", "Mirrored up and down from the middle"],
            ["mirror", "Bass in the middle, mirrored left and right"]], show_if={"style": ["bars", "mountain"]}),
        P("bar_count", "select", "21", "Bars", options=[["16", "16"], ["21", "21"], ["42", "42 (thin)"]],
          show_if={"style": "bars"}),
        P("caps", "checkbox", True, "Falling peak caps", show_if={"style": "bars"}),
        P("wave_look", "select", "filled", "Wave look", options=[
            ["filled", "Filled from the middle"], ["line", "Thick line"], ["mirror", "Line and its mirror image"]],
          show_if={"style": "wave"}),
        P("source", "select", "auto", options=[
            ["auto", "Live audio if connected (Settings → Music), else generated beat"], ["beat", "Always the generated beat"]]),
        P("bpm", "number", 0, "Generated beat tempo (0 = pick one per song)", min=0, max=220),
        P("song_change", "select", "flash", "When the song changes", options=[
            ["flash", "Flash (invert a few times)"], ["invert", "Invert until the next song"],
            ["burst", "Burst from the middle"], ["none", "Nothing"]]),
        P("invert", "checkbox", False, "Invert"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        if params.get("beat_flash") and not params.get("beat_effects"):
            self.p["beat_effects"] = "big"  # the old "Invert briefly on strong beats" checkbox
        self.ma = _MusicState(ctx.store)
        self.proc = audio_signal.Processor()
        self.rng = random.Random()
        self.fps = max(5, min(30, float(ctx.store.settings.get("display", {}).get("transition_fps", 15))))
        self.interval = 1 / self.fps
        self.track = None
        self.flash_until = 0
        self.inverted = False
        self.burst_at = -10
        self.last = None
        self.levels = [0.0] * BINS  # the smoothed, scaled spectrum (self.spec is the app spec)
        self.bands = dict.fromkeys(("level", "bass", "lowmid", "mid", "high"), 0.0)
        self.beat_seen = None
        self.shock_at = -10
        self.flash_frames = 0
        self.caps = []
        self.drops = []
        self.stars = [self._star(self.rng.uniform(0.1, 1)) for _ in range(150)]
        self.phase = [0.0] * 5
        self.fall = []
        self.ray_table = None
        self.perm = None
        self.source_name = "beat"
        self._seed = 0


    # ---- the music signal --------------------------------------------------------------

    def _generated(self, now, playing):
        """A beat-shaped signal: kick on each beat, hats in between, a slowly wandering body."""
        bpm = float(self.p["bpm"]) or (88 + self._seed % 52)  # 88..139 bpm, stable per song
        return audio_signal.generated(now - self.started, bpm, self._seed, playing)

    def _signal(self, now):
        name, s = audio_signal.raw(self.ctx.store, now) if self.p["source"] != "beat" else (None, None)
        if s is not None:
            md = s.get("metadata") or {}
            if name == "snapcast":  # Snapcast carries only audio: song changes come from Music Assistant's API
                self.ma.poll()
                self._song(self.ma.track, now)
            else:
                self._song((md.get("title"), md.get("artist")) if md.get("title") else None, now)
            if s["live"]:
                self.source_name = name
                return s
            playing = s["playing"] if s["playing"] is not None else self.ma.playing
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
        r = self.proc.step(self._signal(now), now)
        dt = 1 / self.fps if self.last is None else max(0.0, min(0.3, now - self.last))
        self.last = now
        g = 1 / max(0.2, float(self.p["sensitivity"]))
        k = max(0.1, float(self.p["scale"])) * GAIN
        sm = max(0.0, min(1.0, float(self.p["smoothing"])))
        up = 1 - math.exp(-dt / (0.005 + 0.06 * sm))
        down = 1 - math.exp(-dt / (0.03 + 0.5 * sm))

        def follow(old, v):
            t = min(1.0, v ** g * k)
            return old + (t - old) * (up if t > old else down)

        self.levels = [follow(o, v) for o, v in zip(self.levels, r["display_spectrum"])]
        db = r["display_bands"]
        self.bands = {n: follow(o, db[n]) for n, o in self.bands.items()}
        getattr(self, "_" + self.p["style"], self._rain)(c, self.levels, self.bands, dt, now)

        fx = self.p["beat_effects"]
        if self.beat_seen != r["beat_count"]:
            if self.beat_seen is not None and fx != "off" and r["beat_age"] < 0.2:
                self.shock_at = now
                self.flash_frames = 1 if fx == "big" and r["beat_count"] % 4 == 0 else 0  # every 4th beat
            self.beat_seen = r["beat_count"]
        age = now - self.shock_at
        if age < 0.45:  # shockwave: a ring racing out from the middle
            rad = age * (130 if fx == "big" else 100)
            _annulus(c, c.w / 2, c.h / 2, rad - (3 if fx == "big" else 1), rad)
        if now - self.burst_at < 1.2:  # song-change burst: an expanding ring from the middle
            rad = (now - self.burst_at) * 60
            _annulus(c, c.w / 2, c.h / 2, rad - 2, rad)
            _annulus(c, c.w / 2, c.h / 2, rad * 0.7 - 1, rad * 0.7)
        flash = now < self.flash_until and int((self.flash_until - now) * 6.6) % 2 == 0
        beat_flash = self.flash_frames > 0
        self.flash_frames = max(0, self.flash_frames - 1)
        if (self.inverted + bool(self.p["invert"]) + flash + beat_flash) % 2:  # each one toggles
            c.invert()

    def _bars(self, c, sp, b, dt, now):
        w, h, px = c.w, c.h, c.px
        layout = self.p["layout"]
        n = int(self.p["bar_count"]) if str(self.p["bar_count"]).isdigit() else 21
        if layout == "mirror":
            half = _resample(sp, n // 2)
            vals = half[::-1] + half  # bass in the middle
        else:
            vals = _resample(sp, n)
        m = len(vals)
        bw = max(1, w // m)
        gap = 1 if bw > 1 else 0
        x0 = (w - bw * m) // 2
        if len(self.caps) != m:
            self.caps = [[0.0, 0.0, 0.0] for _ in range(m)]  # [height, hold time left, fall speed]
        centre = layout == "center"
        full = h // 2 if centre else h
        for i, v in enumerate(vals):
            x = x0 + i * bw
            hh = round(v * full)
            for y in (range(full - hh, full + hh) if centre else range(h - hh, h)):
                _span(px, w, y, x, x + bw - gap)
            if not self.p["caps"]:
                continue
            cap = self.caps[i]
            if v >= cap[0]:
                cap[:] = [v, 0.25, 0.0]  # pushed up: hold a moment, then fall with gravity
            elif cap[1] > 0:
                cap[1] -= dt
            else:
                cap[2] += 3.0 * dt
                cap[0] = max(v, cap[0] - cap[2] * dt)
            ch = round(cap[0] * full)
            if centre:
                for y in (full - ch - 1, full + ch):
                    if 0 <= y < h:
                        _span(px, w, y, x, x + bw - gap)
            elif ch < h:
                _span(px, w, max(0, h - ch - 1), x, x + bw - gap)

    def _mountain(self, c, sp, b, dt, now):
        w, h, px = c.w, c.h, c.px
        layout = self.p["layout"]
        if layout == "mirror":
            half = _resample(sp, w // 2)
            vals = half[::-1] + half
        else:
            vals = _resample(sp, w)
        for x, v in enumerate(vals):
            if layout == "center":
                hh = round(v * h / 2)
                _column(px, w, h, x, h // 2 - max(1, hh), h // 2 + max(1, hh))
            else:
                _column(px, w, h, x, h - 1 - round(v * (h - 1)), h)

    def _sunburst(self, c, sp, b, dt, now):
        """RAYS rays per quarter from the middle, following the spectrum, mirrored four ways (bass horizontal)."""
        if self.ray_table is None:
            self.ray_table = self._rays(c.w, c.h)
        px = c.px
        for v, (dists, idxs, rmax) in zip(_resample(sp, RAYS), self.ray_table):
            for i in idxs[:bisect.bisect_right(dists, 1.5 + v * (rmax - 1.5))]:
                px[i] = 1
        _annulus(c, c.w / 2, c.h / 2, 0, 1.5 + b["bass"] * 6)

    @staticmethod
    def _rays(w, h):
        cx, cy = w / 2, h / 2
        sectors = [[] for _ in range(RAYS)]
        for y in range(h):
            for x in range(w):
                dx, dy = x + 0.5 - cx, y + 0.5 - cy
                d = math.hypot(dx, dy)
                u = abs(abs(math.atan2(dy, dx)) - math.pi / 2) / (math.pi / 2)  # 0 = vertical, 1 = horizontal
                f = (1 - u) * RAYS
                j = min(RAYS - 1, int(f))
                if d > 4 and f - j < 0.35:
                    continue  # a gap between neighbouring rays
                sectors[j].append((d, y * w + x))
        out = []
        for s in sectors:
            s.sort()
            out.append(([d for d, _ in s], [i for _, i in s], s[-1][0] if s else 1.0))
        return out

    def _waterfall(self, c, sp, b, dt, now):
        """A spectrogram scrolling out from the middle row: newest in the middle, bass in the middle column."""
        w, h, px = c.w, c.h, c.px
        half = _resample(sp, w // 2)
        row = bytes(1 if v > 0.5 else 0 for v in half[::-1] + half)
        self.fall.insert(0, row)
        del self.fall[(h + 1) // 2:]
        top = h // 2 - 1
        for age, row in enumerate(self.fall):
            for y in (top - age, top + 1 + age):
                if 0 <= y < h:
                    px[y * w:(y + 1) * w] = row

    def _rings(self, c, sp, b, dt, now):
        cx, cy = c.w / 2, c.h / 2
        _annulus(c, cx, cy, 0, 2 + b["bass"] * 15)  # the bass: a filled disc
        for band, base, reach in (("lowmid", 12, 7), ("mid", 21, 8), ("high", 31, 10)):
            r = base + b[band] * reach  # each ring moves with its own band only, so a kick doesn't shift them all
            _annulus(c, cx, cy, r - 2, r)

    def _blob(self, c, sp, b, dt, now):
        """Metaballs: a big one sized by the bass, three orbiting it sized by the other bands."""
        w, h, px = c.w, c.h, c.px
        self.phase[0] += dt * (0.5 + b["level"] * 2.5)
        a = self.phase[0]
        cx, cy = w / 2, h / 2
        balls = [(cx + math.sin(a * 0.7) * 6, cy, 3 + b["bass"] * 13)]
        for n, (band, orbit) in enumerate((("lowmid", 1.0), ("mid", -1.3), ("high", 1.7))):
            t = a * orbit + n * 2.1
            spread = 10 + b[band] * 22
            balls.append((cx + math.cos(t) * spread, cy + math.sin(t) * spread * 0.45, 1 + b[band] * 8))
        cols = [[(x + 0.5 - bx) ** 2 for x in range(w)] for bx, _, _ in balls]
        rr = [br * br for _, _, br in balls]
        (c0, c1, c2, c3), (r0, r1, r2, r3) = cols, rr
        for y in range(h):
            d0, d1, d2, d3 = [(y + 0.5 - by) ** 2 + 0.5 for _, by, _ in balls]
            if r0 / d0 + r1 / d1 + r2 / d2 + r3 / d3 < 1:
                continue  # even straight above/below every ball the field stays under the threshold
            px[y * w:(y + 1) * w] = bytes(r0 / (p + d0) + r1 / (q + d1) + r2 / (s + d2) + r3 / (t + d3) >= 1
                                          for p, q, s, t in zip(c0, c1, c2, c3))

    def _wave(self, c, sp, b, dt, now):
        w, h, px = c.w, c.h, c.px
        mid = h / 2
        parts = []
        for n, (band, amp, waves, speed) in enumerate((("bass", 0.45, 1.2, 2.0), ("lowmid", 0.25, 2.7, -3.1),
                                                       ("mid", 0.14, 6.0, 4.3), ("high", 0.08, 13.0, -6.0))):
            self.phase[n + 1] += dt * speed * (1 + b["level"])
            parts.append((b[band] * amp * h, waves * 2 * math.pi / w, self.phase[n + 1]))
        look = self.p["wave_look"]
        prev = None
        for x in range(w):
            y = mid + sum(a * math.sin(kx * x + ph) for a, kx, ph in parts)
            y = max(0, min(h - 1, round(y)))
            if look == "filled":
                _column(px, w, h, x, min(y, h // 2), max(y, h // 2) + 1)
                continue
            lo, hi = (y, y) if prev is None else (min(prev, y), max(prev, y))
            _column(px, w, h, x, lo - 1, hi + 2)  # a 3-dot thick line
            if look == "mirror":
                _column(px, w, h, x, h - 2 - hi, h + 1 - lo)
            prev = y

    def _rain(self, c, sp, b, dt, now):
        w, h, px = c.w, c.h, c.px
        cols = _resample(sp, w)
        rng = self.rng
        frames = dt * 15  # tuned at 15 updates/s
        for x, v in enumerate(cols):
            if rng.random() < v ** 1.5 * 0.3 * frames:
                self.drops.append([x, -rng.uniform(0, 3), 14 + b["level"] * 40 + rng.random() * 8,
                                   2 + int(b["bass"] * 6)])
        pool = round(b["bass"] * h * 0.3)  # the puddle rises with the bass
        surface = [h - pool + (round(math.sin(x * 0.35 + now * 5) * min(2, pool / 3)) if pool else 0)
                   for x in range(w)]
        for x, top in enumerate(surface):
            _column(px, w, h, x, top, h)
        alive = []
        for d in self.drops:  # d = [x, y, speed in dots/s, length]
            d[1] += d[2] * dt
            x, y = d[0], int(d[1])
            floor = surface[x]
            if y >= floor:  # splash on the surface
                for sx in (x - 1, x + 1):
                    if 0 <= sx < w and floor > 0:
                        px[(floor - 1) * w + sx] = 1
                continue
            _column(px, w, h, x, y - d[3] + 1, y + 1)
            alive.append(d)
        self.drops = alive[-400:]

    def _star(self, z=1.0):
        return [self.rng.uniform(-1, 1), self.rng.uniform(-1, 1), z]

    def _warp(self, c, sp, b, dt, now):
        w, h = c.w, c.h
        cx, cy = w / 2, h / 2
        speed = (0.12 + b["level"] * 1.8) * dt  # depth per frame
        count = 50 + int(b["mid"] * 100)
        streak = b["level"] > 0.2
        for st in self.stars[:count]:
            x0, y0 = cx + st[0] / st[2] * cx, cy + st[1] / st[2] * cy
            st[2] -= speed
            if st[2] <= 0.04:
                st[:] = self._star()
                continue
            x1, y1 = cx + st[0] / st[2] * cx, cy + st[1] / st[2] * cy
            if not (0 <= x1 < w and 0 <= y1 < h):
                st[:] = self._star()
                continue
            if streak:
                c.line(x0, y0, x1, y1)
            else:
                c.set(x1, y1)
            if st[2] < 0.35:  # close stars are bigger
                c.set(x1 + 1, y1)
                c.set(x1, y1 + 1)
                c.set(x1 + 1, y1 + 1)
        _annulus(c, cx, cy, 0, b["bass"] * 7)

    def _sparkle(self, c, sp, b, dt, now):
        """Each column lights a share of its dots that follows its part of the spectrum. The dots come from a
        fixed shuffle per column that turns slowly, so a steady sound twinkles instead of flipping everything."""
        w, h, px = c.w, c.h, c.px
        if self.perm is None:
            self.perm = [self.rng.sample(range(h), h) for _ in range(w)]
            self.offsets = [0] * w
        for x, v in enumerate(_resample(sp, w)):
            if self.rng.random() < 0.5:
                self.offsets[x] += 1
            perm, off = self.perm[x], self.offsets[x]
            for i in range(round(v * h * 0.8)):
                px[perm[(off + i) % h] * w + x] = 1

    def describe(self):
        src = {"sendspin": "live from Music Assistant (Sendspin)",
               "snapcast": "live from Music Assistant (Snapcast)"}.get(self.source_name, "generated beat")
        return f"Music visualizer ({self.p['style']}, {src})"
