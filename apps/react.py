"""Generative apps that react to music: the shared plumbing.

Decorate an app class with @reactive and it gets the "React to music" options (REACT_PARAMS) and a
`self.music` (Music), updated at the start of every render(). The app reads the music from it to drive its
own motion - fire height from the bass, fireworks launched on the beat - and its clocks come from
`music.clock()`, so a change of speed never makes anything jump. Optionally the slideshows' "effects on top"
table (slidefx) runs over the finished frame.

With "React to music" off, everything here is neutral and exact: clock() is (now - started) x rate,
speed is 1.0, every value is 0.0 and no event fires, so the app draws exactly what it drew before.
All apps (and every layer in a stack) read the same shared audio_signal reading, so several reactive apps
cost little more than one.
"""

import math
import random
import threading
import time

import audio_signal

from . import slidefx
from .base import P

FOLLOW = [["fixed", "Fixed"], ["music", "The music: louder is faster"], ["tempo", "Tempo: in time with the beat"]]
BANDS = ("level", "bass", "lowmid", "mid", "high")
ATTACK, RELEASE = 0.04, 0.25  # seconds: the music values ease up fast and down gently
SPEED_EASE = 0.35             # seconds: the speed eases to a new target (no jerks)
TEMPO_BASE = slidefx.TEMPO_BASE
LOCK_GAIN = slidefx.LOCK_GAIN
MAX_STEP = 0.5                # seconds: a clock never moves on more than this in one frame (after a pause)


def overlay_defaults():
    """The slideshows' default table, minus the beat inverting everything (too many flips for busy effects)."""
    rows = slidefx.clean_react_map(None)
    for r in rows:
        if r["signal"] == "beat":
            r["effect"] = "none"
    return rows


# what the music drives: only the effects on top (the picture moves exactly as it does without music, so slow
# songs don't make it jumpy), only the app's own motion (it dances to the beat and volume), or both
MODES = [["effects", "Effects only: the picture moves as usual, the music adds effects"],
         ["motion", "Motion only: beats and volume drive the movement"],
         ["both", "Effects and motion"]]
_on = {"react": True}
_moving = {"react": True, "react_mode": ["motion", "both"]}
REACT_PARAMS = [
    P("react", "music", False, "React to music"),
    P("react_mode", "select", "effects", "Reacts with", options=MODES, show_if=_on),
    P("react_amount", "number", 100, "Music amount (%)", min=0, max=200, step=10, show_if=_on),
    P("react_follow", "select", "tempo", "Speed follows", options=FOLLOW, show_if=_moving),
    P("react_fallback", "select", "none", "Without live music",
      options=[["none", "Do nothing"], ["beat", "Use a generated beat (120 BPM)"]], show_if=_on),
    P("react_fx", "react_map", overlay_defaults(), "Effects", show_if={"react": True, "react_mode": ["effects", "both"]},
      motion=slidefx.MOTION_DEFAULTS),
]
REACT_NAMES = [p["name"] for p in REACT_PARAMS]

_quiet = [0.0, False]  # (checked at, quiet hours active): shared by every reactive app

# The analysis (hits, tempo) needs a steady stream of readings, but a slow app (plasma at 12/s) would sample
# it unevenly. While any reactive app is listening, a small thread takes a reading SAMPLE_HZ times a second;
# the apps then share those (audio_signal caches them). 0 turns it off (tests drive the clock themselves).
SAMPLE_HZ = 25
_sampler = {"until": 0.0, "running": False, "wants": {}}
_sampler_lock = threading.Lock()


def _keep_sampling(store, fallback):
    if not SAMPLE_HZ:
        return
    with _sampler_lock:
        _sampler["until"] = time.time() + 2.0
        _sampler["wants"][fallback] = store
        if _sampler["running"]:
            return
        _sampler["running"] = True
    threading.Thread(target=_sample_loop, daemon=True, name="music-sampler").start()


def _sample_loop():
    try:
        while True:
            with _sampler_lock:
                if time.time() > _sampler["until"] or not SAMPLE_HZ:
                    _sampler["running"] = False
                    _sampler["wants"] = {}
                    return
                wants = list(_sampler["wants"].items())
            for fallback, store in wants:
                try:
                    audio_signal.reading(store, fallback)
                except Exception:
                    pass
            time.sleep(1 / SAMPLE_HZ)
    except BaseException:
        with _sampler_lock:
            _sampler["running"] = False
        raise


def react_mode(params):
    """"effects", "motion" or "both" from the params as given (not filled with defaults). Set up before the
    choice existed: "Effects on top" ticked = both, otherwise the motion it always had."""
    mode = params.get("react_mode")
    if mode in ("effects", "motion", "both"):
        return mode
    if "react_overlay" in params or params.get("react"):
        return "both" if params.get("react_overlay") else "motion"
    return "effects"


def quiet_now(store):
    t = time.time()
    if t - _quiet[0] > 1:
        from boardcare import quiet_active
        import datetime as dt
        _quiet[:] = [t, quiet_active(store.settings.get("quiet_hours") or {}, dt.datetime.now())]
    return _quiet[1]


def _k(dt, tau):
    return 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0


class Music:
    """One app's view of the music. After update(now):
      enabled   the app's "React to music" option
      motion    the music drives the app's own motion ("Reacts with" motion or both); with effects only, every
                value below stays neutral (0, speed 1, on False) so the app moves exactly as without music
      on        the motion is reacting right now: motion, music (or the generated beat), not quiet hours
      listening music (or the generated beat) is there right now, for the effects on top
      amount    0..2 ("Music amount")
      level(b)  eased band level x amount (b: level, bass, lowmid, mid, high); 0 when not on
      both(b)   eased level and hits of a band mixed (as slideshows' "level and hits") x amount
      pulse     the beat pulse (1 on the beat, decaying) x amount; hits(b) a band's hit pulse x amount
      beat      True on the update that sees a new beat; hit(b): True on a new hit in that band
      presence  0..1 (x amount up to 100%): eases in while reacting, out after; for a calmer baseline that
                doesn't jump
      speed     motion speed multiplier from "Speed follows", eased (1.0 when fixed, off or quiet)
      bpm, phase, groove   tempo (0 = unknown), beat phase 0..1 (None unless sure), off-beat share
    """

    def __init__(self, app, given=None, source=None):
        """source: the params to read instead of app.p (the games' global setting)."""
        self.app = app
        p = app.p if source is None else source
        self.enabled = bool(p.get("react"))
        self.mode = react_mode(p if given is None else given)
        self.motion = self.enabled and self.mode in ("motion", "both")
        try:
            self.amount = max(0.0, min(2.0, float(p.get("react_amount", 100)) / 100))
        except (TypeError, ValueError):
            self.amount = 1.0
        self.follow = p.get("react_follow") if p.get("react_follow") in ("fixed", "music", "tempo") else "tempo"
        self.fallback = "beat" if p.get("react_fallback") == "beat" else None
        self.overlay = (slidefx.clean_react_map(p.get("react_fx"))
                        if self.enabled and self.mode in ("effects", "both") else None)
        self.fx = slidefx.Reactive(random.Random()) if self.overlay else None
        self.on = self.listening = False
        self.reading = None
        self.vals = dict.fromkeys(BANDS, 0.0)
        self.hitp = dict.fromkeys(BANDS[1:], 0.0)
        self.pulse = 0.0
        self.beat = False
        self.new_hits = set()
        self.speed = 1.0
        self.presence = 0.0  # eases to 1 while reacting, back to 0 after: for baselines that differ with music
        self.bpm, self.phase, self.groove = 0.0, None, 0.0
        self._counts = None  # (beat_count, hit_count) last seen
        self._clocks, self._ticks = {}, {}
        self.last = None
        self.dt = 0.0

    # ---- per frame ----------------------------------------------------------------------------------

    def update(self, now):
        if not self.enabled:
            return self
        dt = 0.0 if self.last is None else max(0.0, min(MAX_STEP, now - self.last))
        self.last, self.dt = now, dt
        r = None
        if not quiet_now(self.app.ctx.store):
            _keep_sampling(self.app.ctx.store, self.fallback)
            r = audio_signal.reading(self.app.ctx.store, self.fallback)
            if not (r["live"] or r["generated"]):
                r = None
        self.listening, self.reading = r is not None, r
        self.on = self.listening and self.motion
        a = self.amount if self.on else 0.0
        for k in BANDS:
            target = r[k] * a if r is not None else 0.0
            v = self.vals[k]
            self.vals[k] = v + (target - v) * _k(dt, ATTACK if target > v else RELEASE)
        hits = r["hits"] if r is not None else {}
        for k in self.hitp:
            self.hitp[k] = hits.get(k, 0.0) * a
        self.pulse = r["pulse"] * a if r is not None else 0.0
        counts = (r["beat_count"], dict(r["hit_count"])) if r is not None else None
        fire = r is not None and a > 0 and self._counts is not None
        self.beat = fire and counts[0] != self._counts[0]
        self.new_hits = {k for k, n in counts[1].items() if n != self._counts[1].get(k)} if fire else set()
        self._counts = counts
        if self.on:
            sure = r["bpm_confidence"] >= 0.3 and r["bpm"] > 0
            self.bpm, self.phase, self.groove = (r["bpm"] if sure else 0.0), r["beat_phase"], r["groove"]
        else:
            self.bpm, self.phase, self.groove = 0.0, None, 0.0
        self.presence += (min(1.0, a) - self.presence) * _k(dt, SPEED_EASE)
        target = self._speed_target()
        self.speed += (target - self.speed) * _k(dt, SPEED_EASE)
        if abs(self.speed - 1.0) < 1e-3 and target == 1.0:
            self.speed = 1.0
        if self.fx is not None and not self.listening:
            self.fx.last = None  # the effects' clocks pause rather than catch up
        return self

    def _speed_target(self):
        if not self.on or self.follow == "fixed":
            return 1.0
        a = min(1.5, self.amount)
        if self.follow == "tempo":
            return max(0.25, 1 + (self.bpm / TEMPO_BASE - 1) * min(1.0, a)) if self.bpm else 1.0
        lvl = self.vals["level"] / self.amount if self.amount else 0.0
        return 2 ** (2 * (lvl - 0.5) * a)  # quiet 0.5x .. loud 2x at 100%

    def finish(self, c, now):
        """After the app drew: the "effects on top" (slidefx), when chosen."""
        if self.fx is None or not self.listening:
            return
        plan = {k: (min(1.0, v * self.amount), row, sig)
                for k, (v, row, sig) in slidefx.react_plan(self.overlay, self.reading).items()}
        out = self.fx.apply(c, now, plan, self.reading)
        if out is not c:
            c.px[:] = out.px

    # ---- what apps read -----------------------------------------------------------------------------

    def level(self, band="level"):
        return self.vals[band]

    def hits(self, band):
        return self.hitp.get(band, 0.0)

    def both(self, band):
        if band not in self.hitp:
            return self.vals[band]
        return min(2.0, slidefx.BOTH_MIX[0] * self.vals[band] + slidefx.BOTH_MIX[1] * self.hitp[band])

    def hit(self, band):
        return band in self.new_hits

    def surge(self, k=0.6):
        """A speed factor that kicks on each beat and settles (1.0 off the beat): motion that pumps."""
        return 1.0 + k * self.pulse

    def clock(self, key, now, rate=1.0, per_beat=None):
        """A motion clock (the app's own units, `rate` per second at 1x). Disabled: exactly (now - started) x
        rate, as before. Enabled: it moves on by rate x speed x dt each frame, so a change of speed changes how
        fast it moves from here on, never where it is. per_beat: following the tempo with a sure beat, it moves
        this many units per beat instead, nudged so whole multiples of per_beat land on the beat."""
        if not self.motion:
            return (now - self.app.started) * rate
        c = self._clocks.get(key)
        if c is None:  # [value at t0, t0, units per second since t0]: exact while the speed holds
            c = self._clocks[key] = [0.0, self.app.started, rate]
        step = rate * self.speed
        if per_beat and self.on and self.follow == "tempo" and self.bpm and self.phase is not None:
            locked = per_beat * self.bpm / 60
            here = c[0] + (now - c[1]) * c[2]
            err = (here / per_beat % 1 - self.phase + 0.5) % 1 - 0.5  # beats ahead (+) or behind (-)
            locked *= 1 - max(-0.6, min(0.6, LOCK_GAIN * err))
            step = rate + (locked - rate) * min(1.0, self.amount)
        if step != c[2]:  # from now on at the new speed: the position carries on from where it is
            c[0], c[1], c[2] = c[0] + (now - c[1]) * c[2], now, step
        return c[0] + (now - c[1]) * c[2]

    def ticks(self, key, now, rate, per_beat=None, cap=3):
        """Whole steps due on a clock (e.g. generations), at most `cap` per frame."""
        t = self.clock(key, now, rate, per_beat)
        done = self._ticks.get(key)
        if done is None or t - done > cap:
            done = math.floor(t) - (0 if done is None else cap)
        n = max(0, math.floor(t) - done)
        self._ticks[key] = done + n
        return n


def reactive(cls):
    """Class decorator: React to music options + self.music on an App."""
    cls.params = [*cls.params, *REACT_PARAMS]
    init, render = cls.__init__, cls.render

    def __init__(self, ctx, **params):
        init(self, ctx, **params)
        self.music = Music(self, params)

    def wrapped(self, c, now):
        m = self.music
        if not m.enabled:
            return render(self, c, now)
        m.update(now)
        out = render(self, c, now)
        m.finish(c, now)
        return out

    cls.__init__, cls.render = __init__, wrapped
    cls.reactive = True
    return cls


def react_params(params):
    """The React to music options from a params dict (for apps that pass them on to the apps they run)."""
    return {k: params[k] for k in REACT_NAMES if k in params}


# ---- games ----------------------------------------------------------------------------------------------------
# Games get the effects on top only (never the motion: the game plays exactly as it does without music). Every
# game has its own "React to music" option; a game that doesn't set it follows Settings → Music → "Music effects
# on games", so games started from /play or the gamepad menu get the effects too.

GAME_REACT_PARAMS = [
    P("react", "music", False, "React to music"),
    P("react_amount", "number", 100, "Music amount (%)", min=0, max=200, step=10, show_if=_on),
    P("react_fallback", "select", "none", "Without live music",
      options=[["none", "Do nothing"], ["beat", "Use a generated beat"]], show_if=_on),
    P("react_fx", "react_map", overlay_defaults(), "Effects", show_if=_on, motion=slidefx.MOTION_DEFAULTS),
]
GAMES_MUSIC_DEFAULTS = {"enabled": False, "amount": 100, "fallback": "none", "fx": None}


def games_music_settings(store):
    """Settings → Music → Music effects on games, with defaults filled in."""
    cfg = (getattr(store, "settings", None) or {}).get("games", {}).get("music") or {}
    out = dict(GAMES_MUSIC_DEFAULTS, **{k: v for k, v in cfg.items() if k in GAMES_MUSIC_DEFAULTS})
    if not out["fx"]:
        out["fx"] = overlay_defaults()
    return out


def game_music(app):
    """The Music for a game: its own options when it has React to music on, else the global setting (or off)."""
    if app.p.get("react"):
        return Music(app, {"react_mode": "effects"})
    cfg = games_music_settings(getattr(app.ctx, "store", None))
    if not cfg["enabled"]:
        return Music(app, None, source={"react": False})
    source = {"react": True, "react_amount": cfg["amount"], "react_fallback": cfg["fallback"], "react_fx": cfg["fx"]}
    return Music(app, {"react_mode": "effects"}, source=source)


def attach_game_music(app):
    """Give an interactive app (a game, Doom, NES, the party games) music effects on top of its frames, unless it
    already has its own self.music. Wraps its render on the instance."""
    if getattr(app, "music", None) is not None:
        return app
    m = app.music = game_music(app)
    if not m.enabled:
        return app
    render = app.render

    def wrapped(c, now):
        m.update(now)
        out = render(c, now)
        m.finish(c, now)
        return out

    app.render = wrapped
    return app
