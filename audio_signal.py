"""Live music signal shared by the music visualizer and slideshows that react to music.

Reads the link picked in Settings → Music (Sendspin or Snapcast, see visualizer.source), and turns its
loudness + 16-band spectrum (40 Hz - 8 kHz, log spaced, 0..1 on a -60..0 dB scale) into:

  * four bands: bass (40-150 Hz), low-mid (150-565 Hz), mid (565 Hz-2.1 kHz), high (2.1-8 kHz);
  * auto-gained versions of everything (per band: a running peak and floor, fast attack / slow release),
    so quiet and loud songs both use the full 0..1 range;
  * display versions (display_bands / display_spectrum) for things drawn continuously, like spectrum
    bars: per value a running peak and floor too, but the floor stays at least DISPLAY_SPAN below the peak,
    so a loud sustained note keeps reading high instead of sinking to 0 like the auto-gained values do;
  * beats: an onset strength (rise in low-end energy relative to its recent average), merged with the
    link's own beats, as a counter + a decaying pulse.

`reading(store)` is cheap (a few dozen float ops, cached for CACHE_S) and thread-safe, so the render
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
PEAK_ATTACK = 0.02      # auto-gain ceiling jumps up to new peaks
PEAK_RELEASE = 4.0      # and sinks back slowly, so one loud hit doesn't squash the rest of the song
FLOOR_ATTACK = 3.0      # auto-gain floor creeps up under a steady level
FLOOR_RELEASE = 0.3     # and drops quickly when things get quieter
MIN_SPAN = 0.12         # peak - floor never narrower than this (0..1 dB scale = 7 dB), so noise isn't blown up
GATE = 0.12             # raw values below this (about -53 dB) count as silence
ONSET_AVG = 1.0         # onset strength is measured against the low-end rise averaged over this long
ONSET_THRESHOLD = 1.6   # onset strength that counts as a beat
BEAT_MIN_GAP = 0.25     # at most one beat per this many seconds (240 BPM)
BEAT_DECAY = 0.15       # the beat pulse decays exp(-age / BEAT_DECAY)
GENERATED_BPM = 120
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


class _AutoGain:
    """One value's running peak/floor; maps the raw value onto 0..1 between them, then smooths it."""
    __slots__ = ("peak", "floor", "out")

    def __init__(self):
        self.peak, self.floor, self.out = 0.0, 0.0, 0.0

    def step(self, v, dt):
        if v < GATE:
            target = 0.0  # silence: let the output fall, keep the gain where it was for when music resumes
        else:
            if self.peak <= 0:  # first sound: start the range around it rather than at silence
                self.peak, self.floor = v + MIN_SPAN / 2, v - MIN_SPAN / 2
            self.peak += (v - self.peak) * _k(dt, PEAK_ATTACK if v > self.peak else PEAK_RELEASE)
            self.floor += (v - self.floor) * _k(dt, FLOOR_ATTACK if v > self.floor else FLOOR_RELEASE)
            # a level that holds steady sinks towards 0 (the floor catches up); rises above it read high
            target = max(0.0, min(1.0, (v - self.floor) / max(MIN_SPAN, self.peak - self.floor)))
        self.out += (target - self.out) * _k(dt, SMOOTH_ATTACK if target > self.out else SMOOTH_RELEASE)
        return self.out


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


class _Processor:
    def __init__(self):
        self.last = None
        self.gains = {k: _AutoGain() for k in ("level", *BANDS)}
        self.bins = [_AutoGain() for _ in range(BINS)]
        self.d_level = [_Envelope()]
        self.d_bands = [_Envelope() for _ in BANDS]
        self.d_bins = [_Envelope() for _ in range(BINS)]
        self.prev_low = 0.0
        self.flux_avg = 0.02
        self.onset = 0.0
        self.beat_at = -99.0
        self.link_beat_at = -99.0
        self.beat_count = 0
        self.downbeat = False

    def step(self, s, now):
        if self.last is not None and now < self.last - 1:
            self.__init__()  # the clock went back (tests): start over
        dt = 0.05 if self.last is None else max(0.0, min(0.5, now - self.last))
        self.last = now
        spec = list(s.get("spectrum") or [])[:BINS]
        spec += [0.0] * (BINS - len(spec))
        loud = float(s.get("loudness") or 0.0)
        raw = {"level": loud, **band_values(spec)}
        norm = {k: self.gains[k].step(v, dt) for k, v in raw.items()}
        nspec = [g.step(v, dt) for g, v in zip(self.bins, spec)]
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
        beat, down = False, False
        age = s.get("beat_age")
        if age is not None and age < 1.0 and abs((now - age) - self.link_beat_at) > 0.1:
            self.link_beat_at = now - age  # the link's own beat (server beat tracking or its bass detector)
            beat, down = age < 0.2, bool(s.get("downbeat"))
        if self.onset >= ONSET_THRESHOLD and low >= GATE:
            beat = True
        if beat and now - self.beat_at >= BEAT_MIN_GAP:
            self.beat_at, self.downbeat = now, down
            self.beat_count += 1
        beat_age = max(0.0, now - self.beat_at)
        return {**norm, "raw": raw, "spectrum": nspec, "spectrum_raw": spec,
                "display_bands": dbands, "display_spectrum": dspec,
                "beat": beat_age < 0.1, "beat_count": self.beat_count, "beat_age": beat_age,
                "pulse": math.exp(-beat_age / BEAT_DECAY) if beat_age < 2 else 0.0,
                "onset": self.onset, "downbeat": self.downbeat and beat_age < 0.2}


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
      level, bass, lowmid, mid, high: 0..1 auto-gained and smoothed,
      raw: {level, bass, lowmid, mid, high} 0..1 on the -60..0 dB scale, spectrum / spectrum_raw: 16 bins,
      display_bands: {level, bass, lowmid, mid, high} and display_spectrum: 16 bins, 0..1 for continuous
      display (a sustained loud level stays high; see _Envelope),
      beat: True for ~0.1 s after a beat, beat_count: +1 per beat (compare to catch each one exactly once),
      beat_age: seconds since the last beat, pulse: 1 on a beat decaying to 0, onset: onset strength
      (about 1 = an ordinary rise, >= ONSET_THRESHOLD = a beat), downbeat, playing, metadata."""
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
                out = {**_gen.step(generated(now - _T0), now), "live": False, "generated": True, "source": "generated"}
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
