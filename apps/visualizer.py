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
import fastpath
from canvas import Canvas
from transitions import Transition, _distance_field, smoothstep

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
    ["tunnel", "Tunnel (a kaleidoscope rushing towards you)"],
    ["vortex", "Vortex (spiral arms turning into the middle)"],
    ["moire", "Moire (two sets of rings crossing)"],
    ["lissajous", "Lissajous (a turning figure, bass sizes it)"],
    ["plasma", "Plasma (flowing stripes, bass ripples from the middle)"],
]
STYLE_IDS = [s for s, _ in STYLES]
IDLE = 0.18        # the idle ripple's height (0..1): what moves in silence
IDLE_SPEED = 0.7   # the flow clock runs this fast in silence...
FLOW_SPEED = 1.5   # ...plus this times the level
LIVE_FPS = 25  # with Snapcast audio: draws a second, each exactly when an analysis frame comes due (the board's
               # RS485 lines carry a whole steady picture in 34 ms at 57600 baud, so 25 leaves them headroom)
PUNCH = 0.25  # a part with no new notes or hits draws this much lower, so a steady loud bass still pumps with the kicks
BIN_BAND = [n for n, (a, b) in audio_signal.BANDS.items() for _ in range(a, b)]
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


def _xspan(px, w, y, x0, x1):
    """Invert dots x0..x1-1 on row y (clipped)."""
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    if x1 > x0:
        a = y * w
        px[a + x0:a + x1] = bytes(1 - v for v in px[a + x0:a + x1])


def _clear(px, w, y, x0, x1):
    """Clear dots x0..x1-1 on row y (clipped)."""
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    if x1 > x0:
        px[y * w + x0:y * w + x1] = bytes(x1 - x0)


def _bass_disc(c, cx, cy, r):
    """The bass disc, drawn last and inverting what's under it (with a solid 1-dot edge once it's big
    enough), so its whole outline reads over busy rings and patterns."""
    if r > 2.5:
        _annulus(c, cx, cy, 0, r - 1, _xspan)
        _annulus(c, cx, cy, r - 1, r)
    else:
        _annulus(c, cx, cy, 0, r, _xspan)


def _annulus(c, cx, cy, ri, ro, fill=_span):
    """Fill the ring between radius ri and ro (ri <= 0: a disc), one span pair per row (fill=_xspan inverts)."""
    px, w = c.px, c.w
    for y in range(max(0, int(cy - ro)), min(c.h, int(cy + ro) + 2)):
        dy = y + 0.5 - cy
        if abs(dy) > ro:
            continue
        ho = math.sqrt(ro * ro - dy * dy)
        hi = math.sqrt(ri * ri - dy * dy) if abs(dy) < ri else 0.0
        if hi <= 0:
            fill(px, w, y, round(cx - ho), round(cx + ho))
        else:
            fill(px, w, y, round(cx - ho), round(cx - hi))
            fill(px, w, y, round(cx + hi), round(cx + ho))


def _resample(vals, n):
    """Linear interpolation of vals onto n points (bin centres line up)."""
    m, out = len(vals), []
    for j in range(n):
        p = min(m - 1.0, max(0.0, (j + 0.5) * m / n - 0.5))
        i = min(m - 2, int(p))
        f = p - i
        out.append(vals[i] * (1 - f) + vals[i + 1] * f)
    return out


_RAY_TABLES = {}  # (w, h) -> sunburst rays
_GEOMS = {}       # (w, h) -> per-dot geometry for the pattern styles


def _geom(w, h):
    """Per dot (row by row): distance from the middle and things derived from it, the angle, and a
    distance table twice the board's size for patterns centred anywhere (moire)."""
    g = _GEOMS.get((w, h))
    if g is None:
        cx, cy = w / 2, h / 2
        r, a = [], []
        for y in range(h):
            for x in range(w):
                dx, dy = x + 0.5 - cx, y + 0.5 - cy
                r.append(math.hypot(dx, dy))
                a.append(math.atan2(dy, dx) / (2 * math.pi) % 1.0)
        g = _GEOMS[w, h] = {
            "inv": [1 / max(6.0, d) for d in r], "log": [math.log(max(1.0, d)) for d in r],
            "ri": [int(d) for d in r], "rmax": int(max(r)) + 1,
            "a6": [v * 6 for v in a], "a3": [v * 3 for v in a],
            "d2": [math.hypot(x + 0.5 - w, y + 0.5 - h) for y in range(2 * h) for x in range(2 * w)]}
    return g


class Style:
    """One style's drawing and its own state (rain drops, peak caps, waterfall history...), so two styles
    can run side by side while Mix changes between them. p: the params it reads (layout, bar_count, caps,
    wave_look); scale multiplies the levels it gets (Mix's random looks)."""

    def __init__(self, style, p, rng=None, scale=1.0):
        self.style = style if style in STYLE_IDS else "rain"
        self.p = p
        self.rng = rng or random.Random()
        self.scale = scale
        self.caps = []
        self.drops = []
        self.stars = None
        self.phase = [0.0] * 5
        self.fall = []
        self.perm = None
        self.flow = 0.0
        self.z = 0.0      # tunnel depth / vortex and moire phase
        self.eased = {}
        self.ratio = None  # lissajous frequencies

    def draw(self, c, sp, b, dt, now, flow=None):
        """flow: a clock that runs slowly in silence and faster with the music (the app's, shared by both
        styles in a Mix transition so the motion carries across). Without it the style keeps its own."""
        self.flow = flow if flow is not None else self.flow + dt * (IDLE_SPEED + FLOW_SPEED * b["level"])
        if self.scale != 1.0:
            sp = [min(1.0, v * self.scale) for v in sp]
            b = {k: min(1.0, v * self.scale) for k, v in b.items()}
        # a slow travelling ripple under everything, so quiet passages still move
        f = self.flow
        sp = [max(v, IDLE * (0.55 + 0.45 * math.sin(f * 0.8 - i * 0.7))) for i, v in enumerate(sp)]
        b = {k: max(v, IDLE * (0.35 + 0.25 * math.sin(f * 0.6 + n * 1.9))) for n, (k, v) in enumerate(b.items())}
        getattr(self, "_" + self.style)(c, sp, b, dt, now)

    def _ease(self, name, target, dt, tau=0.4):
        """target, followed smoothly (shape changes glide instead of jumping)."""
        v = self.eased.get(name, target)
        v += (target - v) * (1 - math.exp(-dt / tau))
        self.eased[name] = v
        return v

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
        table = _RAY_TABLES.get((c.w, c.h))
        if table is None:
            table = _RAY_TABLES[c.w, c.h] = self._rays(c.w, c.h)  # shared: the same for every instance
        px = c.px
        f = self.flow * 9
        for j, (v, (dists, idxs, rmax)) in enumerate(zip(_resample(sp, RAYS), table)):
            for i in idxs[:bisect.bisect_right(dists, 1.5 + v * (rmax - 1.5))]:
                px[i] = 1
            d0 = (f + j * 11.3) % rmax  # a dash running out along each ray, so it never stands still
            for i in idxs[bisect.bisect_left(dists, d0):bisect.bisect_right(dists, d0 + 2)]:
                px[i] = 1
        _bass_disc(c, c.w / 2, c.h / 2, 1.5 + b["bass"] * 6)

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
        f = self.flow
        half = [v + 0.45 * max(0.0, math.sin(f * 1.1 - j * 0.45)) ** 8 for j, v in enumerate(_resample(sp, w // 2))]
        row = bytes(1 if v > 0.5 else 0 for v in half[::-1] + half)  # (+ thin idle streams, so silence moves)
        self.fall.insert(0, row)
        del self.fall[(h + 1) // 2:]
        top = h // 2 - 1
        for age, row in enumerate(self.fall):
            for y in (top - age, top + 1 + age):
                if 0 <= y < h:
                    px[y * w:(y + 1) * w] = row

    def _rings(self, c, sp, b, dt, now):
        cx, cy = c.w / 2, c.h / 2
        f = self.flow
        for n, (band, base, reach) in enumerate((("lowmid", 12, 7), ("mid", 21, 8), ("high", 31, 10))):
            # each ring moves with its own band only, so a kick doesn't shift them all; plus a slow breath
            r = base + b[band] * reach + math.sin(f * 0.9 - (n + 1) * 0.8) * 1.5
            _annulus(c, cx, cy, r - 2, r)
        _bass_disc(c, cx, cy, 2 + b["bass"] * 15 + math.sin(f * 0.9) * 0.8)  # last, so it shows over the rings

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

    # ---- hypnotic styles: patterns over precomputed per-dot geometry (_geom), one pass per frame ------

    def _sparkles(self, c, high):
        """The highs as texture: a few dots flipped at random."""
        px, n = c.px, len(c.px)
        for _ in range(int(high * high * 40)):
            px[self.rng.randrange(n)] ^= 1

    def _tunnel(self, c, sp, b, dt, now):
        """Kaleidoscope tunnel: checkered rings rushing towards you, faster with the music, in six mirrored
        segments turning slowly. The bass widens the tunnel, the mids twist it, the highs sparkle."""
        g = _geom(c.w, c.h)
        self.z += dt * (0.4 + 2.5 * b["level"])
        k = 20 * (1 + 0.4 * self._ease("bass", b["bass"], dt, 0.1))
        tw = 0.05 + 0.25 * self._ease("mid", b["mid"], dt, 0.8)
        z, rot = self.z, self.flow * 0.2
        c.px[:] = bytes((int(iv * k + z) + int(abs((a + rot + iv * tw) % 2 - 1) * 3)) & 1
                        for iv, a in zip(g["inv"], g["a6"]))
        self._sparkles(c, b["high"])
        _annulus(c, c.w / 2, c.h / 2, 0, 6, _clear)
        _bass_disc(c, c.w / 2, c.h / 2, 3 + b["bass"] * 8)  # the far end, pumping with the bass

    def _vortex(self, c, sp, b, dt, now):
        """Three spiral arms turning into the middle, faster with the music; the bass thickens the arms
        and the middle, the mids wind the spiral tighter."""
        g = _geom(c.w, c.h)
        self.z += dt * (0.15 + 0.8 * b["level"])
        k = 1.0 + 1.6 * self._ease("mid", b["mid"], dt, 0.8)
        duty = 0.25 + 0.4 * self._ease("bass", b["bass"], dt, 0.1)
        z = self.z
        c.px[:] = bytes((a + lr * k - z) % 1.0 < duty for a, lr in zip(g["a3"], g["log"]))
        self._sparkles(c, b["high"])
        _bass_disc(c, c.w / 2, c.h / 2, 2 + b["bass"] * 8)

    def _moire(self, c, sp, b, dt, now):
        """Two sets of rings from two orbiting centres; where they cross they make moire patterns. The rings
        stream outwards with the bass, the mids spread the centres, the highs make the rings finer."""
        w, h = c.w, c.h
        g = _geom(w, h)
        d, w2 = g["d2"], 2 * w
        sep = 4 + 12 * self._ease("mid", b["mid"], dt, 0.8)
        f = self.flow
        ox, oy = round(math.sin(f * 0.37) * sep), round(math.cos(f * 0.29) * sep * 0.5)
        self.z += dt * (0.3 + 1.5 * b["bass"])
        fr = 0.17 + 0.06 * self._ease("high", b["high"], dt, 0.8)
        ph = 4 - self.z % 2.0
        x1, x2 = w - w // 2 - ox, w - w // 2 + ox
        y1, y2 = h - h // 2 - oy, h - h // 2 + oy
        out = bytearray()
        for y in range(h):
            r1, r2 = (y1 + y) * w2 + x1, (y2 + y) * w2 + x2
            out += bytes((int(p * fr + ph) ^ int(q * fr + ph)) & 1 for p, q in zip(d[r1:r1 + w], d[r2:r2 + w]))
        c.px[:] = out

    def _lissajous(self, c, sp, b, dt, now):
        """A Lissajous figure turning in 3D, with an echo a moment behind it. The bass sizes it, the mids
        wobble it like a harmonograph, the highs throw sparks off it."""
        w, h = c.w, c.h
        cx, cy = (w - 1) / 2, (h - 1) / 2
        if self.ratio is None:
            self.ratio = self.rng.choice(((3, 2), (1, 2), (2, 3), (3, 4)))
        fa, fb = self.ratio
        size = 0.5 + 0.5 * self._ease("bass", b["bass"], dt, 0.12)
        wob = 4 * self._ease("mid", b["mid"], dt, 0.6)
        ax, ay = (w / 2 - 2 - wob) * size, (h / 2 - 1 - wob / 2) * size
        f, sin = self.flow, math.sin
        n = 120
        for lag in (0.25, 0.0):
            ph = f * 0.45 - lag
            pts = [(cx + ax * sin(fa * t + ph) + wob * sin(7 * t + f), cy + ay * sin(fb * t) + wob * 0.5 * sin(5 * t - f))
                   for t in (i * 2 * math.pi / n for i in range(n + 1))]
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                c.line(round(xa), round(ya), round(xb), round(yb))
        rng = self.rng
        for _ in range(int(b["high"] ** 2 * 30)):
            x, y = rng.choice(pts)
            c.set(round(x + rng.uniform(-3, 3)), round(y + rng.uniform(-2, 2)))

    def _plasma(self, c, sp, b, dt, now):
        """Plasma: flowing contour stripes from overlapping waves. The bass sends ripples out of the middle,
        the mids stretch the diagonal waves, the highs add more, finer stripes."""
        w, h = c.w, c.h
        g = _geom(w, h)
        f, sin = self.flow, math.sin
        bass = self._ease("bass", b["bass"], dt, 0.12)
        mid = self._ease("mid", b["mid"], dt, 0.8)
        k = 0.8 + 0.5 * self._ease("high", b["high"], dt, 0.8)
        ax = [sin(x * 0.11 + f * 0.6) for x in range(w)]
        ad = [sin(i * (0.06 + 0.05 * mid) - f * 0.8) for i in range(w + h)]
        ar = [sin(i * 0.4 - f * 1.0) * (0.4 + 1.4 * bass) + 9 for i in range(g["rmax"] + 1)]
        ri = g["ri"]
        out = bytearray()
        for y in range(h):
            ay = sin(y * 0.19 - f * 0.45)
            out += bytes(int((a + d + ar[r] + ay) * k) & 1 for a, d, r in zip(ax, ad[y:y + w], ri[y * w:(y + 1) * w]))
        c.px[:] = out

    def _star(self, z=1.0):
        return [self.rng.uniform(-1, 1), self.rng.uniform(-1, 1), z]

    def _warp(self, c, sp, b, dt, now):
        w, h = c.w, c.h
        cx, cy = w / 2, h / 2
        speed = (0.12 + b["level"] * 1.8) * dt  # depth per frame
        count = 50 + int(b["mid"] * 100)
        streak = b["level"] > 0.2
        if self.stars is None:
            self.stars = [self._star(self.rng.uniform(0.1, 1)) for _ in range(150)]
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
        _bass_disc(c, cx, cy, b["bass"] * 7)

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


# ---- Mix: change styles while the music plays -------------------------------------------------

# transitions that look right between two moving pictures. Left out: migrate/scatter/life (built from the
# first frame only), ghost, flash and glitch (hard flicker), zoom-in/out (zoom-through does it with both sides
# moving, and cheaper). Morph is LazyMorph below (the board-wide morph is too slow with both sides moving).
# Random picks one of FLOWING half the time: they keep both pictures in motion.
FLOWING = ["swirl", "zoom-through"]
MIX_TRANSITIONS = FLOWING + ["morph","dissolve", "fade", "wipe-right", "wipe-left", "wipe-up", "wipe-down", "wipe-ragged",
                   "diagonal", "iris-open", "iris-close", "diamond", "clock", "split-horizontal",
                   "split-vertical", "blinds", "tiles", "drip", "push-left", "push-right", "push-up", "push-down",
                   "flip", "melt", "pixelate"]
VARIANTS = {"bars": {"layout": ["bottom", "center", "mirror"], "bar_count": ["16", "21", "42"]},
            "mountain": {"layout": ["bottom", "center", "mirror"]},
            "wave": {"wave_look": ["filled", "line", "mirror"]}}


class MixScheduler:
    """Decides when Mix changes style. Call update() every frame; it returns why to switch now
    ("song", "bars", "energy", "time") or None.

      * bars: every N bars (4 beats each), counted from the first beat of the song and realigned on the
        link's downbeats, so switches land on phrase boundaries. Only while the beat is steady (regular
        beats from live audio); otherwise the time schedule takes over.
      * seconds: every N seconds when bars is off or the beat isn't steady (0 = never).
      * song: on each new song. energy: on a drop or break (the loudness of the last ~1.5 s moving far
        from that of the last ~10 s), on the next beat when the beat is steady.
      * min_gap: never two switches closer than this; a song change or energy change that comes too
        soon waits for it."""

    ENERGY_FAST, ENERGY_SLOW = 1.5, 10.0  # time constants of the two loudness averages (s)
    ENERGY_JUMP = 0.1                     # their gap that counts as a drop/break (0..1 dB scale, 6 dB)
    ENERGY_WARMUP = 4.0                   # no energy events this soon after a song starts

    def __init__(self, bars=16, seconds=20.0, song=True, energy=False, min_gap=8.0):
        self.bars, self.seconds = int(bars), float(seconds)
        self.song, self.energy = bool(song), bool(energy)
        self.min_gap = float(min_gap)
        self.last = None
        self.seen = None
        self.beat_times = []
        self.index = None          # beats since the phrase grid's start (None: wait for the next beat)
        self.pending = None        # "song" or "energy" waiting for min_gap / the next beat
        self.pending_at = 0.0
        self.fast = self.slow = None
        self.armed = True
        self.song_at = None
        self._t = 0.0

    def period(self, now):
        """Seconds per beat while the beat is steady, else None."""
        t = self.beat_times
        if len(t) < 6 or now - t[-1] > 2.0:
            return None
        gaps = sorted(b - a for a, b in zip(t, t[1:]))
        med = gaps[len(gaps) // 2]
        if not 0.25 <= med <= 1.5 or now - t[-1] > 2 * med + 0.5:
            return None
        if sum(abs(g - med) > 0.2 * med for g in gaps) > len(gaps) // 4:
            return None  # too irregular to count on
        return (t[-1] - t[0]) / (len(t) - 1)  # the mean: steadier than one gap measured in whole frames

    def update(self, now, beat_count=None, downbeat=False, live=False, level=0.0, song_changed=False):
        if self.last is None:
            self.last = now
        new_beat = beat_count is not None and beat_count != self.seen
        if new_beat:
            if self.seen is not None and live:
                self.beat_times.append(now)
                del self.beat_times[:-9]
                self.index = 0 if self.index is None else self.index + 1
                if downbeat and self.index % 4:
                    self.index = (self.index + 2) // 4 * 4  # the link says bar 1 is here: snap to the nearest bar
            self.seen = beat_count
        if not live:
            self.beat_times.clear()
            self.index = None
        if song_changed:
            self.index = None  # the new song's first beat starts the phrase count
            self.song_at = now
            self.fast = None
            if self.song:
                self.pending, self.pending_at = "song", now
        steady = live and self.period(now) is not None
        if self.energy and live:
            self._energy(now, level)
        if self.pending == "energy" and now - self.pending_at > 4:
            self.pending = None  # never found a good moment: drop it
        if now - self.last < self.min_gap:
            return None
        why = None
        if self.pending == "song" or (self.pending == "energy" and (new_beat or not steady)):
            why = self.pending
        elif steady and self.bars > 0:
            if new_beat and self.index and self.index % (self.bars * 4) == 0:
                why = "bars"
        elif self.seconds > 0 and now - self.last >= self.seconds:
            why = "time"
        if why:
            self.last = now
            self.pending = None
        return why

    def _energy(self, now, level):
        if self.fast is None:
            self.fast = self.slow = level
            self._t = now
            return
        dt = max(0.0, min(0.5, now - self._t))
        self._t = now
        self.fast += (level - self.fast) * (1 - math.exp(-dt / self.ENERGY_FAST))
        self.slow += (level - self.slow) * (1 - math.exp(-dt / self.ENERGY_SLOW))
        gap = abs(self.fast - self.slow)
        if self.song_at is not None and now - self.song_at < self.ENERGY_WARMUP:
            return
        if self.armed and gap > self.ENERGY_JUMP:
            self.armed = False
            if self.pending is None:
                self.pending, self.pending_at = "energy", now
        elif gap < self.ENERGY_JUMP / 3:
            self.armed = True


LAZY_AFTER = 0.012  # LazyMorph: two distance fields taking longer than this (s) switches to one a frame


class LazyMorph:
    """Morph between two moving styles: blends the two pictures' distance fields (transitions._morph's shape
    tween, same field), so white shapes flow into each other. With both pictures changing every frame nothing
    can be cached: both fields are recomputed each frame (the same result as the board-wide morph; about 6 ms
    on a Pi 5). On a machine where that takes over LAZY_AFTER it goes lazy, recomputing one side a frame and
    reusing the other's from the frame before, so the music visualizer's steady timing holds. The Transition
    interface Mix uses: start, duration, mix(new, now, old), done(now)."""

    def __init__(self, w, h, duration):
        self.w, self.h = w, h
        self.start, self.duration = time.time(), max(0.1, float(duration))
        self.fields = [None, None]  # old, new
        self.turn = 0
        self.lazy = False

    def done(self, now):
        return now - self.start >= self.duration

    def mix(self, new, now, old):
        p = smoothstep(min(1.0, max(0.0, (now - self.start) / self.duration)))
        if p >= 1:
            return new.copy()
        if self.fields[0] is None or not self.lazy:  # both sides (the first frame, or while there's time)
            t = time.perf_counter()
            self.fields = [_distance_field(old), _distance_field(new)]
            # too slow for this machine: from now on one side a frame (a shape at most one frame old)
            self.lazy = self.lazy or time.perf_counter() - t > LAZY_AFTER
        else:  # one side a frame, taking turns
            self.fields[self.turn] = _distance_field((old, new)[self.turn])
            self.turn ^= 1
        a, b = self.fields
        out = Canvas(self.w, self.h)
        out.px = bytearray(fastpath.morph_blend(a, b, p))
        return out


class Mix:
    """Runs the styles of a Mix: the current one, and while a transition runs the outgoing one too, both
    following the music. Each style instance keeps its own state."""

    def __init__(self, p, w, h, rng=None):
        self.p, self.w, self.h = p, w, h
        self.rng = rng or random.Random()
        styles = p.get("mix_styles")
        if isinstance(styles, str):
            styles = [s.strip() for s in styles.split(",")]
        self.styles = [s for s in STYLE_IDS if s in (styles or ())] or list(STYLE_IDS)
        self.sched = MixScheduler(int(p.get("mix_bars") or 0), float(p.get("mix_seconds") or 0),
                                  bool(p.get("mix_song")), bool(p.get("mix_energy")),
                                  max(float(p.get("mix_min_gap") or 0), self._duration(None) + 0.5))
        self.cur = self._make(self._pick(None))
        self.prev = None
        self.trans = None
        self.switches = 0

    def _pick(self, last):
        if self.p.get("mix_order") == "sequence":
            return self.styles[(self.styles.index(last) + 1) % len(self.styles)] if last in self.styles else self.styles[0]
        choices = [s for s in self.styles if s != last] or self.styles
        return self.rng.choice(choices)

    def _make(self, style):
        p, scale = self.p, 1.0
        if self.p.get("mix_variants"):
            p = dict(p, **{k: self.rng.choice(v) for k, v in VARIANTS.get(style, {}).items()})
            scale = self.rng.choice((0.85, 1.0, 1.0, 1.2))
        return Style(style, p, random.Random(self.rng.random()), scale)

    def _duration(self, period):
        d = max(0.3, min(3.0, float(self.p.get("mix_duration") or 1.5)))
        if period:  # a whole number of beats
            d = max(1, round(d / period)) * period
            while d > 3.0 and d > period:
                d -= period
        return d

    def switch(self, now, period=None):
        """Start the change to the next style (the outgoing one keeps drawing until the transition ends)."""
        kind = self.p.get("mix_transition") or "random"
        if kind not in MIX_TRANSITIONS and kind != "none":
            kind = self.rng.choice(FLOWING if self.rng.random() < 0.5 else MIX_TRANSITIONS)
        self.prev, self.cur = self.cur, self._make(self._pick(self.cur.style))
        self.switches += 1
        self.trans = None
        if kind == "morph":
            self.trans = LazyMorph(self.w, self.h, self._duration(period))
            self.trans.start = now
        elif kind != "none":
            self.trans = Transition(kind, Canvas(self.w, self.h), self.w, self.h, self._duration(period))
            self.trans.start = now
        else:
            self.prev = None

    def draw(self, c, sp, b, dt, now, flow=None):
        self.cur.draw(c, sp, b, dt, now, flow)
        if self.trans is None:
            return
        old = Canvas(c.w, c.h)
        self.prev.draw(old, sp, b, dt, now, flow)
        c.px[:] = self.trans.mix(c, now, old).px
        if self.trans.done(now):
            self.trans = self.prev = None


MIX_ONLY = {"style": "mix"}


@register
class Visualizer(App):
    id = "visualizer"
    title = "Music visualizer"
    category = "Widgets"
    description = ("Moves with the frequencies of the music: real loudness/spectrum from Music Assistant over Sendspin "
                   "or Snapcast when connected (Settings → Music), otherwise a generated beat that follows play/pause "
                   "and song changes.")
    steady_timing = True  # the board keeps every change the same time behind the music (see flipdot.Board.steady)
    params = [
        P("style", "select", "rain", options=STYLES + [["mix", "Mix (changes style as the music plays)"]]),
        P("mix_styles", "checklist", list(STYLE_IDS), "Styles in the mix",
          options=[[s, s.capitalize()] for s in STYLE_IDS], show_if=MIX_ONLY),
        P("mix_order", "select", "random", "Order", options=[
            ["random", "Random (never the same one twice in a row)"], ["sequence", "In the order above"]],
          show_if=MIX_ONLY),
        P("mix_bars", "select", "16", "Change every", options=[
            ["4", "4 bars"], ["8", "8 bars"], ["16", "16 bars"], ["32", "32 bars"], ["0", "Off (use seconds)"]],
          show_if=MIX_ONLY),
        P("mix_seconds", "number", 20, "Every … seconds when there's no steady beat (generated beat, "
          "or bars off; 0 = never)", min=0, max=600, step=1, show_if=MIX_ONLY),
        P("mix_song", "checkbox", True, "Also change on each new song", show_if=MIX_ONLY),
        P("mix_energy", "checkbox", False, "Also change on a drop or break (a big jump in loudness)",
          show_if=MIX_ONLY),
        P("mix_min_gap", "number", 8, "At least … seconds between changes", min=1, max=600, step=1,
          show_if=MIX_ONLY),
        P("mix_transition", "select", "random", "Transition", options=[
            ["random", "Random"]] + [[t, t.replace("-", " ").capitalize()] for t in MIX_TRANSITIONS]
          + [["none", "None (cut)"]], show_if=MIX_ONLY),
        P("mix_duration", "number", 1.5, "Transition length in seconds (rounded to whole beats when the beat "
          "is steady)", min=0.3, max=3, step=0.1, show_if=MIX_ONLY),
        P("mix_variants", "checkbox", False, "Random look for each style (layout, bar count, wave look, scale)",
          show_if=MIX_ONLY),
        P("scale", "number", 1.5, "Scale: how far it moves (1.5 = loud parts reach the edges)", min=0.5, max=4, step=0.25),
        P("sensitivity", "number", 1.0, "Sensitivity: how big quieter sounds look (1 = normal, 2 = quiet sounds "
          "nearly as big as loud ones, 0.5 = only the loudest parts reach far)", min=0.2, max=3, step=0.1),
        P("dynamics", "number", 0.3, "Dynamics: how much a quiet passage looks quieter (0 = every part of a song "
          "fills the display, 1 = a breakdown or quiet intro stays small)", min=0, max=1, step=0.1),
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
        self.song_changed = False
        self.flash_until = 0
        self.inverted = False
        self.burst_at = -10
        self.last = None
        self.flow = 0.0  # the motion clock every style follows (kept across Mix changes)
        self.levels = [0.0] * BINS  # the smoothed, scaled spectrum (self.spec is the app spec)
        self.bands = dict.fromkeys(("level", "bass", "lowmid", "mid", "high"), 0.0)
        self.beat_seen = None
        self.shock_at = -10
        self.flash_frames = 0
        self.mix = Mix(self.p, self.w, self.h, self.rng) if self.p["style"] == "mix" else None
        self.style = None if self.mix else Style(self.p["style"], self.p, self.rng)
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
        self.song_changed = True
        mode = self.p["song_change"]
        if mode == "flash":
            self.flash_until = now + 0.9
        elif mode == "invert":
            self.inverted = not self.inverted
        elif mode == "burst":
            self.burst_at = now

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

        # dynamics: quieter for a passage below the song's loudest; punch: steady parts sit a bit lower, so new
        # notes and hits (bassline, kicks, hats) stand out even when a part is loud the whole time
        dyn = max(0.0, min(1.0, float(self.p.get("dynamics", 0.3))))
        rel, hits = r["rel"], r["hits"]
        hits = {**hits, "level": max(hits.values())}
        gain = {n: (1 - dyn * (1 - rel[n])) * (1 - PUNCH * (1 - hits[n])) for n in rel}
        self.levels = [follow(o, v * gain[n]) for o, v, n in zip(self.levels, r["display_spectrum"], BIN_BAND)]
        db = r["display_bands"]
        self.bands = {n: follow(o, db[n] * gain[n]) for n, o in self.bands.items()}
        self.flow += dt * (IDLE_SPEED + FLOW_SPEED * self.bands["level"])
        if self.mix:
            self._mix_step(r, now)
            self.mix.draw(c, self.levels, self.bands, dt, now, self.flow)
        else:
            self.style.draw(c, self.levels, self.bands, dt, now, self.flow)
        self.song_changed = False

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
        self.interval = self._next_interval(now)

    def _next_interval(self, now):
        """Live Snapcast audio: draw next exactly when an analysis frame comes due, about 1/LIVE_FPS from now, so
        the picture is always the same time behind the speakers (a free-running timer would add up to a whole
        frame of wobble). Otherwise the display frame rate."""
        if self.source_name == "snapcast":
            return audio_signal.steady_interval(self.ctx.store, now, LIVE_FPS) or 1 / LIVE_FPS
        return 1 / self.fps

    def _mix_step(self, r, now):
        live = self.source_name != "beat"
        sched = self.mix.sched
        why = sched.update(now, r["beat_count"], r["downbeat"], live, r["raw"]["level"], self.song_changed)
        if why:
            self.mix.switch(now, sched.period(now) if live else None)

    @property
    def status_tick(self):
        return self.mix.cur.style if self.mix else None  # the "Now showing" line names the style in a Mix

    def describe(self):
        src = {"sendspin": "live from Music Assistant (Sendspin)",
               "snapcast": "live from Music Assistant (Snapcast)"}.get(self.source_name, "generated beat")
        if self.mix:
            return f"Music visualizer · Mix (now: {self.mix.cur.style}, {src})"
        return f"Music visualizer ({self.p['style']}, {src})"
