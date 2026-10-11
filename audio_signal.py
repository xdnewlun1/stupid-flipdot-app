"""Live music signal shared by the music visualizer and slideshows that react to music.

Reads the link picked in Settings → Music (Sendspin or Snapcast, see visualizer.source), and turns its
loudness + 16-band spectrum (40 Hz - 8 kHz, log spaced, 0..1 on a -60..0 dB scale) into:

  * four bands: bass (40-150 Hz), low-mid (150-565 Hz), mid (565 Hz-2.1 kHz), high (2.1-8 kHz);
  * baseline-robust band values (level, bass, ...; see _Range): each band mapped into its recent range, so
    quiet and loud songs both use 0..1 and a steady loud level reads high; re-adjusted within ~2 s on a new
    song or a jump in loudness, slowly otherwise; `rel` / with_dynamics() say how loud the passage is for
    the song (DYNAMICS of that is in the plain values);
  * display versions (display_bands / display_spectrum) for things drawn continuously, like spectrum bars;
  * per-band hits (see _Hits): onsets from spectral flux with adaptive thresholds - bass notes, snares and
    claps, hats - as pulses and counters: the "change" channel next to the levels;
  * tempo, beat phase, groove and swing from the autocorrelation of the onset envelope (see _Tempo), and
    beats (from that beat grid once the tempo is sure, else onsets and the link's own beats) as a counter
    and a decaying pulse.

`reading(store)` is cheap (well under 0.1 ms on a PC, cached for CACHE_S) and thread-safe, so the render
thread and the web UI's level meter can both call it. Tests can feed a fake signal with `inject()`.
"""

import math
import threading
import time

BINS = 16
BANDS = {"bass": (0, 4), "lowmid": (4, 8), "mid": (8, 12), "high": (12, 16)}  # spectrum bin ranges
NAMES = {"sendspin": "Sendspin", "snapcast": "Snapcast", "generated": "generated beat", "test": "test signal"}

# ---- tuning (seconds are time constants) ------------------------------------------------------
CACHE_S = 0.015         # readings this close together share one computation
SMOOTH_ATTACK = 0.02    # normalised values follow rises this fast...
SMOOTH_RELEASE = 0.12   # ...and fall back this slowly
# baseline-robust band values (see _Range)
RANGE_UP = 0.15         # a band's ceiling rises to louder passages this fast (a single hit barely moves it)...
RANGE_DRIFT = 10.0      # ...and both ends drift to a new level over this long, so a breakdown reads quieter for a while
FLOOR_DROP = 0.15       # the floor follows dips quickly
ADAPT_TAU = 0.5         # while adapting fast (new song, intro -> drop), both ends move this fast
ADAPT_S = 2.0           # for this long
JUMP = 0.1              # loudness rising this far (6 dB) above its recent average is a jump (a drop after an intro)
JUMP_SHORT, JUMP_LONG = 0.3, 4.0  # the two averages compared for that
GAP_S = 0.7             # sound after at least this much silence counts as a new song
MIN_SPAN = 0.15         # floor at least this far (9 dB) under the ceiling: a steady level reads high, noise stays small
BAND_TILT = 0.4         # a band's ceiling is never more than this (24 dB) below the loudest band's
SONG_FORGET = 30.0      # dynamics: the song's loudest passage is remembered about this long
DYN_SPAN = 0.3          # dynamics: a passage this far (18 dB) below the song's loudest counts as fully quiet
DYNAMICS = 0.5          # how much the true loudness still counts in level/bass/... (0 = fully normalised)
GATE = 0.08             # raw values below this (about -55 dB) count as silence
# per-band hits (see _Hits)
HIT_AVG = 1.2           # a band's flux is measured against its mean and mean deviation over this long
HIT_K = 0.6             # a hit: flux above mean + HIT_K x deviation...
HIT_MIN = 0.012         # ...and above this (about 0.7 dB per bin)
HIT_TOP = 3.0           # a band's flux: its bins' rises, plus its biggest rise counted this many times extra
HIT_GAP = 0.08          # at most one hit per band per this many seconds
HIT_DECAY = 0.12        # a hit's pulse decays exp(-age / HIT_DECAY)
TEMPO_WEIGHTS = {"bass": 1.0, "lowmid": 0.5, "mid": 0.9, "high": 0.5}  # bands in the tempo's onset envelope
PHASE_WEIGHTS = {"bass": 1.0, "lowmid": 0.3, "mid": 0.5, "high": 0.0}  # and in the beat phase's (hats: off-beat)
LEVEL_WEIGHTS = (0.5, 0.0)  # the overall loudness rise in those two (the kick is the biggest jump in loudness)
LINK_BEAT_WEIGHT = 2.0  # a beat from the link (server beat tracking or its bass detector) in that envelope
# beats
ONSET_AVG = 1.0         # onset strength is measured against the low-end rise averaged over this long
ONSET_THRESHOLD = 1.6   # onset strength that counts as a beat (while the tempo isn't sure)
BEAT_MIN_GAP = 0.25     # at most one beat per this many seconds (240 BPM)
BEAT_DECAY = 0.15       # the beat pulse decays exp(-age / BEAT_DECAY)
GENERATED_BPM = 120
# tempo (see _Tempo)
ENV_HZ = 50             # onset envelope sample rate
TEMPO_LAGS = 100        # autocorrelation up to this many samples (2 s)
TEMPO_MEMORY = 4.0      # the autocorrelation forgets with this time constant (about 8 s of music)
TEMPO_EVERY = 0.25      # tempo / phase re-estimated this often
TEMPO_WARMUP = 2.5      # seconds of envelope before a first estimate
TEMPO_RANGE = (60, 180)  # BPM
TEMPO_CENTER = 115      # octave preference: half/double time ties go to the tempo nearer this...
TEMPO_WIDTH = 0.9       # ...weighted by a log-normal this many octaves wide
PHASE_S = 4.0           # the beat phase / groove fold over this much envelope
PHASE_GAIN = 0.5        # the beat phase moves this share of the way to each new measurement
TEMPO_FIT = (0.22, 0.55)  # autocorrelation at the period (vs at 0): random onsets reach ~0.25, music 0.5-0.8
TEMPO_COH = 0.6         # onset clustering on the beat grid (0..1) that counts as fully locked
TEMPO_SURE = 0.6        # confidence from which beat_phase is given
DISPLAY_PEAK_ATTACK = 0.01    # display ceiling: jumps up to new peaks...
DISPLAY_PEAK_RELEASE = 4.0    # ...and re-ranges to a quieter passage over a few seconds
DISPLAY_FLOOR_RISE = 4.0      # display floor: creeps up slowly under steady sound...
DISPLAY_FLOOR_FALL = 0.25     # ...and follows dips quickly
DISPLAY_SPAN = 0.2            # but never closer than this below the peak (12 dB): sustained = high
DISPLAY_TILT = 0.3            # a bin's ceiling is never more than this below the loudest bin's, so near-silent
                              # bins (e.g. no treble) aren't blown up to full height
DISPLAY_RELEASE = 0.05        # light smoothing of the display values (the visualizer adds its own)

_lock = threading.Lock()
_injected = None
_clock = time.time


def inject(fn, clock=None):
    """Test hook: fn(now) -> a link-style signal dict (live, loudness, spectrum, beat_age, ...), or None to
    go back to the real link. While set it replaces the live link for everyone (visualizer included).
    clock: a stand-in for time.time (simulated time in tests)."""
    global _injected, _clock
    with _lock:
        _injected, _clock = fn, clock or time.time
        _cache.clear()


def link(store):
    """(name, link) for the source chosen in Settings → Music; link is None when it's off or not started."""
    source = store.settings.get("visualizer", {}).get("source")
    if source == "snapcast":
        import snapcast_link
        return source, snapcast_link.get_link()
    if source == "sendspin":
        import sendspin_link
        return source, sendspin_link.get_link()
    return source, None


def next_frame_due(store, after):
    """When the live link's next analysis frame comes due at or after `after` (wall time), or None (no live
    link, or it doesn't schedule its frames: only Snapcast does)."""
    if _injected is not None:
        return None
    _, lk = link(store)
    fn = getattr(lk, "next_due", None)
    return fn(after) if fn is not None and lk.connected() else None


def steady_interval(store, now, fps):
    """Seconds until the next draw for something kept a constant time behind the music: exactly when the live
    link's next analysis frame comes due, about 1/fps from now (a free-running timer would add up to a whole
    frame of wobble). None without a link that schedules its frames (only Snapcast does)."""
    step = 1 / fps
    due = next_frame_due(store, now + step * 0.75)
    return None if due is None else max(0.005, due - now)


def raw(store, now=None):
    """(name, signal) straight from the link (its own format), signal None when not connected."""
    with _lock:
        return _raw(store, _clock() if now is None else now)


def _raw(store, now):
    if _injected is not None:
        return "test", _injected(now)
    name, lk = link(store)
    if lk is None or not lk.connected():
        return name, None
    return name, lk.signal()


def generated(t, bpm=GENERATED_BPM, seed=0, playing=True):
    """A beat-shaped signal in link format: kick on each beat, hats in between, a slowly wandering body."""
    period = 60 / bpm
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


def band_values(spectrum):
    """16 spectrum bins -> {bass, lowmid, mid, high}: the mean of each band's bins."""
    spec = list(spectrum[:BINS]) + [0.0] * max(0, BINS - len(spectrum))
    return {k: sum(spec[a:b]) / (b - a) for k, (a, b) in BANDS.items()}


def _k(dt, tau):
    return 1.0 if tau <= 0 else 1.0 - math.exp(-dt / tau)


class _Range:
    """Baseline-robust normalisation of one value. Tracks its recent range: a ceiling near the loud end (rises
    within RANGE_UP, sinks over RANGE_DRIFT) and a floor near the quiet end (drops within FLOOR_DROP, rises over
    RANGE_DRIFT); while `fast` (a new song, a jump up in level) both ends move within ADAPT_TAU. The value is
    mapped between them, the floor kept at least MIN_SPAN under the ceiling: a steady level reads high, not 0,
    and its small changes still move it. `song` is the song's loudest ceiling so far (forgotten over
    SONG_FORGET), for the dynamics: how far the current passage is below it."""
    __slots__ = ("lo", "hi", "song", "out")

    def __init__(self):
        self.lo = self.hi = self.song = self.out = 0.0

    def track(self, v, dt, fast, live=True):
        """live: music is playing (when this value alone is silent, its ceiling still drifts down)."""
        if v < GATE:
            if live and self.hi > 0:
                self.hi += (GATE - self.hi) * _k(dt, ADAPT_TAU if fast else RANGE_DRIFT)
            return  # all quiet: keep the range for when the music resumes
        if self.hi <= 0:
            self.hi, self.lo, self.song = v, v - MIN_SPAN, v
        drift = ADAPT_TAU if fast else RANGE_DRIFT
        self.hi += (v - self.hi) * _k(dt, RANGE_UP if v > self.hi else drift)
        self.lo += (v - self.lo) * _k(dt, FLOOR_DROP if v < self.lo else drift)
        if fast or self.hi > self.song:
            self.song = self.hi
        else:
            self.song += (self.hi - self.song) * _k(dt, SONG_FORGET)

    def value(self, v, dt, top=0.0):
        """v mapped onto 0..1 and smoothed; top: a lower bound for the ceiling (the loudest band's - BAND_TILT),
        so a near-silent band isn't blown up to full height."""
        if v < GATE:
            target = 0.0
        else:
            hi = max(self.hi, top)
            lo = min(self.lo, hi - MIN_SPAN)
            target = max(0.0, min(1.0, (v - lo) / (hi - lo)))
        self.out += (target - self.out) * _k(dt, SMOOTH_ATTACK if target > self.out else SMOOTH_RELEASE)
        return self.out

    def rel(self):
        """How loud this passage is for the song: 1 = as loud as it gets, 0 = DYN_SPAN or more below that."""
        return max(0.0, min(1.0, 1 - (self.song - self.hi) / DYN_SPAN))


def _ranged(ranges, values, dt, fast, tilt, live):
    for r, v in zip(ranges, values):
        r.track(v, dt, fast, live)
    top = max(r.hi for r in ranges) - tilt
    return [r.value(v, dt, top) for r, v in zip(ranges, values)]


def with_dynamics(r, amount):
    """{level, bass, lowmid, mid, high} of a reading with `amount` (0..1) of dynamics: 0 = every passage uses the
    full range, 1 = a passage DYN_SPAN below the song's loudest reads 0. The reading's own values use DYNAMICS."""
    amount = max(0.0, min(1.0, amount))
    return {k: v * (1 - amount * (1 - r["rel"][k])) for k, v in r["norm"].items()}


class _Envelope:
    """Display normalisation of one value: (v - floor) / (peak - floor), fast attack, slow release."""
    __slots__ = ("peak", "floor", "out")

    def __init__(self):
        self.peak, self.floor, self.out = 0.0, 0.0, 0.0

    def track(self, v, dt):
        """Update the running peak/floor (skipped in silence, so the range is kept for when music resumes)."""
        if v < GATE:
            return
        if self.peak <= 0:
            self.peak, self.floor = v, v - DISPLAY_SPAN
        self.peak += (v - self.peak) * _k(dt, DISPLAY_PEAK_ATTACK if v > self.peak else DISPLAY_PEAK_RELEASE)
        self.floor += (v - self.floor) * _k(dt, DISPLAY_FLOOR_RISE if v > self.floor else DISPLAY_FLOOR_FALL)

    def value(self, v, dt, ceiling=0.0):
        """v mapped onto 0..1; ceiling: a lower bound for the peak (the loudest bin's peak - DISPLAY_TILT)."""
        if v < GATE:
            target = 0.0
        else:
            peak = max(self.peak, ceiling)
            floor = min(self.floor, peak - DISPLAY_SPAN)
            target = max(0.0, min(1.0, (v - floor) / (peak - floor)))
        self.out = target if target > self.out else self.out + (target - self.out) * _k(dt, DISPLAY_RELEASE)
        return self.out


def _display(envs, values, dt):
    for e, v in zip(envs, values):
        e.track(v, dt)
    ceiling = max(e.peak for e in envs) - DISPLAY_TILT
    return [e.value(v, dt, ceiling) for e, v in zip(envs, values)]


class _Hits:
    """Per-band onsets from spectral flux: the summed rise of a band's bins since the last reading (on the dB
    scale, so already log-compressed). A rise over an adaptive threshold (its recent mean + HIT_K mean deviations)
    is a hit: a bass note, a snare/clap/guitar chop, a hat. A bassline moving between notes shows up as a rise in
    the new note's bins even when the band's total level holds steady."""

    def __init__(self):
        self.prev = None
        self.prev_level, self.level_mean = 0.0, 0.01
        self.mean = dict.fromkeys(BANDS, 0.01)
        self.dev = dict.fromkeys(BANDS, 0.01)
        self.at = dict.fromkeys(BANDS, -99.0)
        self.armed = dict.fromkeys(BANDS, True)  # a band's flux must fall back under its threshold between hits
        self.amp = dict.fromkeys(BANDS, 0.0)
        self.count = dict.fromkeys(BANDS, 0)

    def step(self, spec, loud, dt, now):
        """-> combined onset strengths (each band's flux against its own mean, weighted) for the tempo and for
        the beat phase."""
        prev, self.prev = self.prev, spec
        rise, self.prev_level = max(0.0, loud - self.prev_level) if loud >= GATE else 0.0, loud
        if prev is None or dt <= 0:
            return 0.0, 0.0
        k = _k(dt, HIT_AVG)
        o = min(4.0, rise / max(self.level_mean, HIT_MIN / 3))
        self.level_mean += (rise - self.level_mean) * k
        env, penv = LEVEL_WEIGHTS[0] * o, LEVEL_WEIGHTS[1] * o
        for name, (a, b) in BANDS.items():
            f = top = 0.0
            for i in range(a, b):
                d = spec[i] - prev[i]
                if d > 0 and spec[i] >= GATE:
                    f += d
                    top = max(top, d)
            f = (f + HIT_TOP * top) / (b - a + HIT_TOP)
            m, dv = self.mean[name], self.dev[name]
            thr = max(HIT_MIN, m + HIT_K * dv)
            if f <= thr:
                self.armed[name] = True
            elif self.armed[name] and now - self.at[name] >= HIT_GAP:
                self.at[name], self.armed[name] = now, False
                self.count[name] += 1
                self.amp[name] = min(1.0, 0.6 + 0.4 * (f - thr) / thr)
            o = min(4.0, f / max(m, HIT_MIN / 3))
            env += TEMPO_WEIGHTS[name] * o
            penv += PHASE_WEIGHTS[name] * o
            self.mean[name] = m + (f - m) * k
            self.dev[name] = dv + (abs(f - m) - dv) * k
        return env, penv

    def pulses(self, now):
        return {n: self.amp[n] * math.exp(-(now - self.at[n]) / HIT_DECAY) if now - self.at[n] < 1 else 0.0
                for n in BANDS}


class _Tempo:
    """Tempo, beat phase and groove from the onset-strength envelope.

    The envelope is resampled to ENV_HZ and its autocorrelation kept up to date sample by sample, forgetting over
    TEMPO_MEMORY (about 8 s of music). Every TEMPO_EVERY s, each beat period in TEMPO_RANGE is scored by the
    autocorrelation at the period and twice it, weighted towards TEMPO_CENTER (settles half/double
    time; groove music sits around 90-130 BPM), then refined over the period's multiples. Syncopated kicks,
    missing downbeats and swing barely move it, unlike a fit to beat intervals. The beat phase comes from folding
    the last PHASE_S of the envelope onto the period (a comb), followed smoothly (a simple PLL); the same fold
    gives the off-beat share (groove) and where the off-beats sit (swing)."""

    BIN = 48  # beat phase histogram bins

    def __init__(self):
        n = int(PHASE_S * ENV_HZ)
        self.hist = [0.0] * n    # tempo envelope, mean removed
        self.qhist = [0.0] * n   # beat phase envelope, above its mean
        self.acf = [0.0] * (TEMPO_LAGS + 1)
        self.slot, self.pend, self.mean, self.qmean = None, (0.0, 0.0), 0.0, 0.0
        self.recent = self.typical = 0.0
        self.pushed, self.next_est = 0, 0
        self.period = None      # slots
        self.anchor = None      # slot (float) of a recent beat
        self.fit = self.coh = 0.0
        self.groove, self.swing = 0.0, 1.0
        self.decay = math.exp(-1 / (TEMPO_MEMORY * ENV_HZ))
        lo, hi = 60 * ENV_HZ / TEMPO_RANGE[1], 60 * ENV_HZ / TEMPO_RANGE[0]
        self.lags = range(int(lo), int(hi) + 2)
        self.prior = {l: math.exp(-0.5 * (math.log2(60 * ENV_HZ / l / TEMPO_CENTER) / TEMPO_WIDTH) ** 2)
                      for l in self.lags}
        self.phasor = [(math.cos(2 * math.pi * (b + 0.5) / self.BIN), math.sin(2 * math.pi * (b + 0.5) / self.BIN))
                       for b in range(self.BIN)]

    def new_song(self):
        self.acf = [a * 0.2 for a in self.acf]
        self.pushed = min(self.pushed, ENV_HZ)
        self.next_est = 0

    def add(self, now, o, q):
        """The onset strengths (tempo o, beat phase q) since the last reading, spread over the envelope samples
        it covers."""
        cur = int(now * ENV_HZ)
        if self.slot is None or cur < self.slot or cur - self.slot > ENV_HZ:
            self.slot, self.pend = cur, (o, q)
            return
        n = cur - self.slot
        if n == 0:
            self.pend = (self.pend[0] + o, self.pend[1] + q)
            return
        o, q = o / n, q / n
        self._push(self.pend[0] + o, self.pend[1] + q)
        for _ in range(n - 1):
            self._push(o, q)
        self.slot, self.pend = cur, (0.0, 0.0)
        if self.pushed >= self.next_est:
            self.next_est = self.pushed + int(TEMPO_EVERY * ENV_HZ)
            self._estimate()

    def _push(self, x, q):
        self.mean += (x - self.mean) / (1.5 * ENV_HZ)
        self.qmean += (q - self.qmean) / (1.5 * ENV_HZ)
        y = x - self.mean
        h = self.hist
        h.append(y)
        self.qhist.append(max(0.0, q - self.qmean))
        if len(h) > 2 * PHASE_S * ENV_HZ:
            del h[:-int(PHASE_S * ENV_HZ)]
            del self.qhist[:-int(PHASE_S * ENV_HZ)]
        d = self.decay
        self.acf = [a * d + y * v for a, v in zip(self.acf, h[:-TEMPO_LAGS - 2:-1])]
        p = y if y > 0 else 0.0
        self.recent += (p - self.recent) / ENV_HZ
        self.typical += (p - self.typical) / (6 * ENV_HZ)
        self.pushed += 1

    def _estimate(self):
        A, L = self.acf, TEMPO_LAGS
        if A[0] <= 1e-9 or self.pushed < TEMPO_WARMUP * ENV_HZ:
            return

        def at(x):
            i = int(x)
            if i >= L:
                return 0.0
            f = x - i
            return A[i] * (1 - f) + A[i + 1] * f

        old = self.period
        score = {}
        for l in self.lags:
            s = (A[l] + 0.5 * (at(2 * l) if 2 * l < L else A[l])) * self.prior[l]
            if old is not None and abs(l / old - 1) < 0.05:
                s *= 1.15  # hysteresis: a settled tempo (and its octave) is kept on near-ties
            score[l] = s
        lags = list(self.lags)[1:-1]
        best = max(lags, key=score.get)
        if score[best] <= 0:
            self.fit = 0.0
            return
        a, b, c = score[best - 1], score[best], score[best + 1]
        den = a - 2 * b + c
        p = best + (0.5 * (a - c) / den if den < 0 else 0.0)
        # refine over the period's multiples (least squares through the autocorrelation peaks near k x p)
        sw = swk = 0.0
        for k in range(1, 5):
            j0 = round(k * p)
            if j0 + 3 > L:
                break
            j = max(range(j0 - 2, j0 + 3), key=A.__getitem__)
            if A[j] <= 0 or j in (j0 - 2, j0 + 2):
                continue
            den = A[j - 1] - 2 * A[j] + A[j + 1]
            x = j + (0.5 * (A[j - 1] - A[j + 1]) / den if den < 0 else 0.0)
            sw += A[j] * k * x
            swk += A[j] * k * k
        if swk > 0 and abs(sw / swk / p - 1) < 0.06:
            p = sw / swk
        if old is not None and abs(p / old - 1) < 0.05:
            p = old * (p / old) ** 0.5
        self.period = p
        # periodicity: the normalised autocorrelation at the period, of the envelope smoothed over 3 samples
        # (timing jitter of a few tens of ms shouldn't count against it)
        tri = ((-2, 1), (-1, 2), (0, 3), (1, 2), (2, 1))
        self.fit = max(0.0, sum(w * at(p + j) for j, w in tri)) / sum(w * A[abs(j)] for j, w in tri)
        self._phase(p)

    def _phase(self, p):
        """Fold the recent envelopes onto the period: beat phase (PLL), how locked it is, groove and swing."""
        B, n = self.BIN, int(PHASE_S * ENV_HZ)
        n0 = self.slot - n  # slot of the first sample folded
        start, step = (n0 % p) / p, 1 / p
        bins, qbins = [0.0] * B, [0.0] * B
        for i, (y, q) in enumerate(zip(self.hist[-n:], self.qhist[-n:])):
            if y > 0 or q > 0:
                b = int((start + i * step) % 1.0 * B)
                if y > 0:
                    bins[b] += y
                qbins[b] += q
        tot, qtot = sum(bins), sum(qbins)
        if tot <= 0 or qtot <= 0:
            return
        ph = self.phasor
        x = sum(v * c for v, (c, _) in zip(qbins, ph))
        y = sum(v * s for v, (_, s) in zip(qbins, ph))
        x2 = sum(v * ph[2 * b % B][0] for b, v in enumerate(qbins))
        y2 = sum(v * ph[2 * b % B][1] for b, v in enumerate(qbins))
        # locked: onsets cluster on the beat, or on the beat and half way (kick + off-beat bass/hats)
        self.coh = max(math.hypot(x, y), math.hypot(x2, y2)) / qtot
        # the beat: where the onsets cluster most (a circular mean would be pulled off the beat by syncopation),
        # refined by the centroid around it
        tri = (1, 2, 3, 4, 3, 2, 1)
        best = max(range(B), key=lambda b: sum(w * qbins[(b + j - 3) % B] for j, w in enumerate(tri)))
        near = [(j, qbins[(best + j) % B]) for j in range(-4, 5)]
        f = (best + 0.5 + sum(j * v for j, v in near) / (sum(v for _, v in near) or 1)) / B % 1.0
        # the most recent such slot (+1: an onset is spread over the slots before the reading that saw it)
        meas = f * p + math.floor((self.slot - f * p) / p) * p + 1
        if self.anchor is None or self.fit < 0.05:
            self.anchor = meas
        else:
            base = self.anchor + round((meas - self.anchor) / p) * p
            self.anchor = base + PHASE_GAIN * (meas - base)
        # bins relative to the beat: on-beat share -> groove; off-beat centroid -> swing
        rb = [bins[(b + round(f * B)) % B] for b in range(B)]
        on = sum(rb[:5]) + sum(rb[-4:])
        off = 1 - on / tot
        k = 0.3
        self.groove += (off - self.groove) * k
        e = sum(rb[10:18])  # the 16th after the beat
        if e > 0.5 * sum(rb[20:34]):  # 16ths: swing of the second 16th of each half beat
            half = [rb[j] + rb[j + B // 2] for j in range(B // 2)]
            lo, hi = 10, 18
            win, size = half, B // 2
        else:  # 8ths
            lo, hi, win, size = 20, 36, rb, B
        w = sum(win[lo:hi])
        if w > 0:
            c = sum((j + 0.5) * win[j] for j in range(lo, hi)) / w / size
            ratio = max(1.0, min(3.0, c / (1 - c)))
            self.swing += (ratio - self.swing) * k

    def reading(self, now):
        """(bpm, confidence 0..1, beat_phase or None)."""
        if self.period is None:
            return 0.0, 0.0, None
        fade = max(0.0, min(1.0, 2 * self.recent / self.typical)) if self.typical > 1e-9 else 0.0
        fit = max(0.0, min(1.0, (self.fit - TEMPO_FIT[0]) / (TEMPO_FIT[1] - TEMPO_FIT[0])))
        conf = math.sqrt(fit) * (0.75 + 0.25 * min(1.0, self.coh / TEMPO_COH)) * fade
        phase = ((now * ENV_HZ - self.anchor) / self.period) % 1.0 if conf >= TEMPO_SURE else None
        return 60 * ENV_HZ / self.period, conf, phase


class _Processor:
    def __init__(self):
        self.last = None
        self.ranges = {k: _Range() for k in ("level", *BANDS)}
        self.bins = [_Range() for _ in range(BINS)]
        self.d_level = [_Envelope()]
        self.d_bands = [_Envelope() for _ in BANDS]
        self.d_bins = [_Envelope() for _ in range(BINS)]
        self.hits = _Hits()
        self.prev_low = 0.0
        self.flux_avg = 0.02
        self.onset = 0.0
        self.beat_at = -99.0
        self.link_beat_at = -99.0
        self.link_down = False
        self.beat_count = 0
        self.downbeat = False
        self.grid_at = -99.0  # the last beat taken from the beat grid
        self.tempo = _Tempo()
        self.track = None
        self.quiet_for = GAP_S  # the first sound counts as a new song
        self.short = self.mid = None
        self.fast_until = -99.0

    def _adapt(self, s, loud, dt, now):
        """True while the ranges should adapt fast: for ADAPT_S after a new song (metadata, or sound after a gap)
        or a jump up in loudness (intro -> drop). A drop in loudness (a breakdown) adapts slowly."""
        md = s.get("metadata") or {}
        track = (md.get("title"), md.get("artist")) if md.get("title") else None
        new = track is not None and self.track is not None and track != self.track
        if track is not None:
            self.track = track
        if loud < GATE:
            self.quiet_for += dt
        else:
            if self.quiet_for >= GAP_S:
                new = True
            self.quiet_for = 0.0
            if self.short is None or new:
                self.short = self.mid = loud
            self.short += (loud - self.short) * _k(dt, JUMP_SHORT)
            self.mid += (loud - self.mid) * _k(dt, JUMP_LONG)
            if self.short > self.mid + JUMP:
                self.fast_until = now + ADAPT_S
            if now < self.fast_until:
                self.mid = self.short
        if new:
            self.fast_until = now + ADAPT_S
            self.tempo.new_song()
        return now < self.fast_until

    def step(self, s, now):
        if self.last is not None and now < self.last - 1:
            self.__init__()  # the clock went back (tests): start over
        dt = 0.05 if self.last is None else max(0.0, min(0.5, now - self.last))
        self.last = now
        spec = list(s.get("spectrum") or [])[:BINS]
        spec += [0.0] * (BINS - len(spec))
        loud = float(s.get("loudness") or 0.0)
        raw = {"level": loud, **band_values(spec)}
        fast = self._adapt(s, loud, dt, now)
        lvl = self.ranges["level"]
        lvl.track(loud, dt, fast)
        norm = {"level": lvl.value(loud, dt)}
        live = loud >= GATE
        norm.update(zip(BANDS, _ranged([self.ranges[k] for k in BANDS], [raw[k] for k in BANDS], dt, fast, BAND_TILT, live)))
        rel = {k: r.rel() for k, r in self.ranges.items()}
        nspec = _ranged(self.bins, spec, dt, fast, BAND_TILT, live)
        dbands = dict(zip(BANDS, _display(self.d_bands, [raw[k] for k in BANDS], dt)))
        dbands["level"] = _display(self.d_level, [loud], dt)[0]
        dspec = _display(self.d_bins, spec, dt)

        # onset strength: how much the low end rose since the last reading, against its recent average rise
        low = sum(spec[:6]) / 6
        flux = max(0.0, low - self.prev_low)
        self.prev_low = low
        self.onset = flux / max(0.01, self.flux_avg)
        if dt > 0:
            self.flux_avg += (flux - self.flux_avg) * _k(dt, ONSET_AVG)
        env, penv = self.hits.step(spec, loud, dt, now)
        beat, down, link = False, False, False
        age = s.get("beat_age")
        if age is not None and age < 1.0 and abs((now - age) - self.link_beat_at) > 0.1:
            self.link_beat_at, self.link_down = now - age, bool(s.get("downbeat"))  # the link's own beat
            link = age < 0.2
            env += LINK_BEAT_WEIGHT
            penv += LINK_BEAT_WEIGHT
        self.tempo.add(now, env, penv)
        bpm, conf, phase = self.tempo.reading(now)
        beat_t = now
        if phase is not None:
            # a sure tempo: beats come from the beat grid, so syncopated hits don't pull them off the beat
            # (the reading nearest each beat: up to ~half a reading early, so the pulse peaks on time)
            period = 60 / bpm
            t_b = now - phase * period if phase < 0.5 else now + (1 - phase) * period
            if t_b - now <= 0.6 * dt and t_b - self.grid_at > 0.5 * period:
                self.grid_at = t_b
                beat, beat_t = True, min(now, t_b)
                down = self.link_down and abs(t_b - self.link_beat_at) < 0.15
        elif link:
            beat, down, beat_t = True, self.link_down, self.link_beat_at
        elif self.onset >= ONSET_THRESHOLD and low >= GATE:
            beat = True
        gap = BEAT_MIN_GAP if phase is None else 0.5 * 60 / bpm
        if beat and now - self.beat_at >= gap:
            self.beat_at, self.downbeat = beat_t, down
            self.beat_count += 1
        beat_age = max(0.0, now - self.beat_at)
        values = {k: v * (1 - DYNAMICS * (1 - rel[k])) for k, v in norm.items()}
        return {**values, "norm": norm, "rel": rel, "raw": raw, "spectrum": nspec, "spectrum_raw": spec,
                "display_bands": dbands, "display_spectrum": dspec,
                "hits": self.hits.pulses(now), "hit_count": dict(self.hits.count),
                "hit_age": {k: now - v for k, v in self.hits.at.items()},
                "beat": beat_age < 0.1, "beat_count": self.beat_count, "beat_age": beat_age,
                "pulse": math.exp(-beat_age / BEAT_DECAY) if beat_age < 2 else 0.0,
                "onset": self.onset, "downbeat": self.downbeat and beat_age < 0.2,
                "bpm": bpm, "bpm_confidence": conf, "beat_phase": phase,
                "groove": self.tempo.groove if conf >= TEMPO_SURE else 0.0,
                "swing": self.tempo.swing if conf >= TEMPO_SURE else 1.0, "adapting": fast}


Processor = _Processor  # step(link-style signal, now) -> a reading without live/source/metadata (own state)
_live = _Processor()
_gen = _Processor()
_cache = {}
_T0 = time.time()
SILENT = {"loudness": 0.0, "spectrum": [0.0] * BINS}


def reading(store, fallback=None, now=None):
    """The current music signal, smoothed and auto-gained. fallback="beat": a generated 120 BPM beat when
    there's no live audio. Returns
      live (bool), generated (bool), source ("sendspin"/"snapcast"/"generated"/"test"/None),
      level, bass, lowmid, mid, high: 0..1 in each band's recent range, smoothed, with DYNAMICS of the song's
      loudness; norm: the same without dynamics, rel: how loud the passage is for the song (with_dynamics()),
      raw: {level, bass, lowmid, mid, high} 0..1 on the -60..0 dB scale, spectrum / spectrum_raw: 16 bins,
      display_bands: {level, bass, lowmid, mid, high} and display_spectrum: 16 bins, 0..1 for continuous
      display (a sustained loud level stays high; see _Envelope),
      beat: True for ~0.1 s after a beat, beat_count: +1 per beat (compare to catch each one exactly once),
      beat_age: seconds since the last beat, pulse: 1 on a beat decaying to 0, onset: onset strength
      (about 1 = an ordinary rise, >= ONSET_THRESHOLD = a beat), downbeat, playing, metadata,
      hits: {bass, lowmid, mid, high} 1 on a new note / drum hit decaying to 0, hit_count, hit_age,
      bpm: the tempo (0 = none yet; 60..180), bpm_confidence: 0..1, beat_phase: 0..1 through the current beat
      (0 = on the beat) when the tempo is sure (bpm_confidence >= TEMPO_SURE), else None, groove: share of
      onsets off the beat (0 when unsure), swing: 1 straight .. 2 triplet swing, adapting: re-adjusting fast."""
    now = _clock() if now is None else now
    key = fallback == "beat"
    with _lock:
        hit = _cache.get(key)
        if hit is not None and 0 <= now - hit[0] < CACHE_S:
            return hit[1]
        name, s = _raw(store, now)
        live = bool(s and s.get("live", True))
        if live:
            out = {**_live.step(s, now), "live": True, "generated": False, "source": name}
        else:
            _live.step(SILENT, now)  # let the live values fall back to rest
            if key:
                out = {**_gen.step(generated(now - _T0), now), "live": False, "generated": True, "source": "generated",
                       "bpm": float(GENERATED_BPM), "bpm_confidence": 1.0,  # its tempo is known
                       "beat_phase": ((now - _T0) * GENERATED_BPM / 60) % 1.0}
            else:
                out = {**_gen.step(SILENT, now), "live": False, "generated": False, "source": None}
        out["playing"] = (s or {}).get("playing")
        out["metadata"] = (s or {}).get("metadata")
        _cache[key] = (now, out)
        return out


def status(store):
    """What the UI shows about live audio: {source, connected, receiving, text}."""
    name, s = raw(store)
    label = NAMES.get(name, name)
    if s is None:
        if name in ("sendspin", "snapcast"):
            return {"source": name, "connected": False, "receiving": False,
                    "text": f"{label} is chosen in Settings → Music but isn't connected"}
        return {"source": None, "connected": False, "receiving": False, "text": "No live audio"}
    receiving = bool(s.get("live", True))
    return {"source": name, "connected": True, "receiving": receiving,
            "text": f"Listening: {label} · " + ("receiving audio" if receiving else "connected, nothing playing")}
