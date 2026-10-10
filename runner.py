"""Decides what is on the board: manual drawing, a single app, a playlist, or the
schedule, with notifications shown on top and transitions between them."""

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

import apps
from canvas import Canvas
from transitions import NAMES as TRANSITIONS, Transition

log = logging.getLogger("runner")

OFF = "__off__"  # schedule target meaning "all black"
LIVE_TIMEOUT = 4  # seconds without a frame before a browser's live stream counts as gone


@dataclass
class Context:
    width: int
    height: int
    store: object


class Runner:
    def __init__(self, board, store):
        self.board = board
        self.store = store
        self.ctx = Context(board.width, board.height, store)
        self.mode = "manual"  # manual | app | playlist | schedule
        self.app = None
        self.playlist = None
        self.item_index = -1
        self.item_until = 0
        self.notif = None  # dict(app, until, restore_rows)
        self.transition = None
        self.prev_app = None  # the outgoing app keeps running during a transition
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.listeners = []  # called with status() whenever the mode/app changes
        self.quiet = False  # quiet hours active (set by boardcare)
        self.slowdown = 1.0  # transitions take this many times longer (quiet hours)
        # Pause: a pausable app (slideshow, animation) freezes on its own clock, and playlists stop advancing
        self.paused_at = None
        self.clock_offset = 0.0  # seconds a pausable app has spent paused (its clock runs this far behind)
        self.render_once = False  # paused, but show the result of a skip
        self._problem, self._problem_at = None, 0
        # progress ticks (a playing video's position): cheaper than _changed(), only live views listen
        self.ticks = []
        self._tick = None
        # a live stream from a browser (Import, Stream live): {"id", "name", "target", "since", "last"}
        self.live = None
        self._stopped_streams = []  # streams ended by Stop: frames still in flight are ignored
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- control (called from HTTP/MQTT threads) --------------------------------

    def _current_canvas(self):
        return Canvas.from_rows(self.board.rows())

    def _start_transition(self, kind, duration=1.0):
        if kind and kind != "none":
            self.transition = Transition(kind, self._current_canvas(), self.board.width, self.board.height,
                                         float(duration) * self.slowdown)

    def transition_fps(self):
        return max(5, min(30, float(self.store.settings.get("display", {}).get("transition_fps", 15))))

    def _drop_prev(self):
        if self.prev_app:
            self.prev_app.stop()
            self.prev_app = None

    def _set_app(self, app, transition="none", duration=1.0):
        self._drop_prev()
        if transition and transition != "none" and self.app is not None and app is not None:
            # keep the old app animating underneath the transition, so it blends live
            self.prev_app = self.app
        elif self.app:
            self.app.stop()
        if transition and transition != "none":
            self._start_transition(transition, duration)
        else:
            self.transition = None
        self.app = app
        self.paused_at, self.clock_offset = None, 0.0
        self.wake.set()
        self._changed()

    # ---- pause / skip ----------------------------------------------------------------

    def _top(self):
        return None if self.notif else self.app

    def can_pause(self):
        app = self._top()
        return bool(app is not None and getattr(app, "pausable", False)) or self.mode in ("playlist", "schedule")

    def _skippable(self):
        """The app whose own items Next/Previous step through (a slideshow you started), or None."""
        app = self._top()
        return app if self.mode == "app" and app is not None and hasattr(app, "skip") else None

    def pause(self, on=True):
        with self.lock:
            if on and self.paused_at is None and self.can_pause():
                self.paused_at = time.time()
            elif not on and self.paused_at is not None:
                held = time.time() - self.paused_at
                self.clock_offset += held
                self.item_until += held
                self.paused_at = None
            else:
                return
            self.wake.set()
            self._changed()

    def app_now(self, now):
        """The clock a pausable app sees: it stands still while paused."""
        return (self.paused_at if self.paused_at is not None else now) - self.clock_offset

    def prev(self):
        with self.lock:
            app = self._skippable()
            if app is not None:
                app.skip(-1, cut=self.paused_at is not None)
                self.render_once = True
            elif self.mode in ("playlist", "schedule"):
                pl = self.store.playlists.get(self.playlist or "")
                n = len((pl or {}).get("items") or [])
                if n:
                    self.item_index = (self.item_index - 2) % n
                    self.item_until = 0
                    self._resume_for_skip()
            self.wake.set()

    def _resume_for_skip(self):
        # a skipped-to playlist item starts fresh; a held playlist would never show it
        if self.paused_at is not None:
            self.pause(False)

    def stop(self, end_stream=True):
        """Back to manual mode: nothing runs, the board keeps whatever is on it."""
        with self.lock:
            if end_stream and self.live:
                self.end_stream(self.live["id"])
            if self.mode == "manual" and not self.app and not self.notif and not self.transition:
                return
            if self.notif:
                self.notif["app"].stop()
                self.notif = None
            if self.app:
                self.app.stop()
            self._drop_prev()
            self.app = None
            self.transition = None
            self.mode = "manual"
            self.playlist = None
            self.paused_at = None
            self._changed()

    # ---- live streams from a browser ------------------------------------------------

    def stream_frame(self, info, target):
        """A frame of a browser's live stream is arriving (target "board" or "layer"). Returns False when that
        stream was stopped here, so the frame should be dropped."""
        sid = str((info or {}).get("id") or "")[:64]
        if not sid:
            return True
        with self.lock:
            if sid in self._stopped_streams:
                return False
            if target == "board":
                self.stop(end_stream=False)
            now = time.time()
            if self.live is None or self.live["id"] != sid or self.live["target"] != target:
                self.live = {"id": sid, "name": str(info.get("name") or "")[:80], "target": target, "since": now, "last": now}
                self._changed()
            else:
                self.live["last"] = now
            return True

    def end_stream(self, sid, stopped=True):
        with self.lock:
            if not self.live or self.live["id"] != sid:
                return
            self.live = None
            if stopped:
                self._stopped_streams = (self._stopped_streams + [sid])[-20:]
            self._changed()

    def run(self, spec, transition="none", duration=1.0):
        if transition not in TRANSITIONS and transition not in ("wipe", "slide"):
            raise ValueError(f"unknown transition {transition!r}")
        app = apps.create(self.ctx, spec)
        with self.lock:
            self.mode = "app"
            self.playlist = None
            self._set_app(app, transition, duration)

    def update_layers(self, layers, transition="none", duration=1.0):
        """Change the running layer composition in place (unchanged layers keep running),
        or start one if something else is on the board."""
        with self.lock:
            if self.mode == "app" and self.app is not None and self.app.id == "layers" and not self.notif:
                if transition != "none":
                    self._start_transition(transition, duration)
                self.app.update(layers)
                self.wake.set()
                self._changed()
                return
        self.run({"app": "layers", "params": {"layers": layers}}, transition, duration)

    def run_frames(self, frames, loop=True, name="animation"):
        app = apps.REGISTRY["scene"](self.ctx, frames=frames, name=name, loop=loop)
        with self.lock:
            self.mode = "app"
            self.playlist = None
            self._set_app(app)

    def play_playlist(self, name):
        if name not in self.store.playlists:
            raise ValueError(f"no playlist {name!r}")
        with self.lock:
            self.mode = "playlist"
            self.playlist = name
            self.item_index, self.item_until = -1, 0
            self.paused_at = None
            self.wake.set()

    def resume_schedule(self):
        with self.lock:
            self.mode = "schedule"
            self.playlist = None
            self.item_index, self.item_until = -1, 0
            self.paused_at = None
            self.wake.set()
            self._changed()

    def next(self):
        with self.lock:
            app = self._skippable()
            if app is not None:
                app.skip(1, cut=self.paused_at is not None)
                self.render_once = True
            else:
                self.item_until = 0
                self._resume_for_skip()
            self.wake.set()

    def update_params(self, app_id, params):
        """Live options: restart the app you started with new params, without a transition.
        Returns False when that app isn't what's running (playlists and the schedule keep their own params)."""
        with self.lock:
            old = self._top()
            if self.mode != "app" or old is None or old.id != app_id or app_id == "layers":
                return False
            app = apps.create(self.ctx, {"app": app_id, "params": params})
            if not app.interactive:
                app.started = old.started  # a ticker keeps its place
            old.stop()
            self._drop_prev()
            self.transition = None
            self.app = app
            self.wake.set()
            self._changed()
            return True

    def add_layer(self, layer, bottom=False):
        """Add a layer to whatever is on the board now (turning it into a layer composition):
        on top by default, or underneath everything with bottom=True."""
        with self.lock:
            cur = None if self.notif else self.app
            if cur is not None and cur.id == "layers":
                specs = [l.spec for l in cur.layers]
            elif cur is not None and getattr(cur, "spec", None):
                specs = [cur.spec]
            elif any("1" in r for r in self.board.rows()):
                # manual drawing / ad-hoc animation: keep the current picture as a layer
                did = self.store.create_drawing("Board snapshot", self.board.width, self.board.height, self.board.rows())
                specs = [{"app": "drawing", "params": {"drawing": did}, "blend": "add" if bottom else "normal"}]
            else:
                specs = []
            if bottom:
                # full-board "normal" layers would hide the new background; draw them on top with a black
                # outline instead so they stay readable over it (a normal layer with its own box, like a
                # now-playing strip, is left alone)
                specs = [{**sp, "blend": "halo"} if sp.get("blend", "normal") == "normal"
                         and not any(sp.get(k) for k in ("x", "y", "w", "h")) else sp for sp in specs]
                specs = [layer] + specs
            elif not specs or specs[-1] != layer:
                specs = specs + [layer]
        self.update_layers(specs)

    def drawings_on_board(self):
        app = self.notif["app"] if self.notif else self.app
        if app is None:
            return []
        if app.id == "drawing":
            return [app.p["drawing"]]
        if app.id == "layers":
            return [l.app.p["drawing"] for l in app.layers if l.app.id == "drawing" and l.visible]
        return []

    def notify(self, spec=None, app=None, duration=10, transition="dissolve", transition_duration=1.0):
        app = app or apps.create(self.ctx, spec)
        with self.lock:
            restore = self.board.rows() if self.mode == "manual" else None
            if self.notif:
                self.notif["app"].stop()
                restore = self.notif["restore"]
            self.notif = {"app": app, "until": time.time() + float(duration), "restore": restore,
                          "transition": transition, "transition_duration": transition_duration}
            self._start_transition(transition, transition_duration)
            self.wake.set()
            self._changed()

    def input(self, key, action="down", player=1):
        with self.lock:
            target = self.notif["app"] if self.notif else self.app
            if target is None or not target.interactive:
                return False
            target.input(key, action, player)
            self.wake.set()
            return True

    def problem(self, app):
        """(setup message, error message, title of the app with the problem) for the app on the board,
        looking inside layers."""
        if app is None:
            return None, None, None
        subs = [l.app for l in app.layers] if app.id == "layers" else [app]
        for a in subs:
            spec = getattr(a, "spec", None)
            setup = (apps.base.setup_needed(self.store, spec) if spec else None) or getattr(a, "setup", None)
            if setup:
                return setup, None, a.title
            err = getattr(a, "error", None)
            if err and getattr(a, "data", None) is None:
                return None, err, a.title
        return None, None, None

    def status(self):
        with self.lock:
            app = self.notif["app"] if self.notif else self.app
            top = self._top()
            setup, error, problem_app = self.problem(app)
            skip = self._skippable()
            live = self.live
            slide = None
            if top is not None and top.id == "slideshow" and top.item:
                slide = {"gallery": top.gid, "file": top.item.get("file")}
            return {
                "mode": self.mode,
                "playlist": self.playlist,
                "app": app.id if app else None,
                "description": (f"Live stream: {live['name']}" if live else app.describe() if app else None),
                # a browser streaming to the board; pages compare the id with their own to say "this browser"
                "live": {k: live[k] for k in ("id", "name", "target", "since")} if live else None,
                # a playing video's position etc. (apps/video.py)
                "video": top.media_status() if top is not None and hasattr(top, "media_status") else None,
                "interactive": bool(app and app.interactive),
                "notification": bool(self.notif),
                "drawings": self.drawings_on_board(),
                "schedule_target": self.scheduled_playlist(time.time()),
                "quiet_hours": self.quiet,
                "setup": setup,
                "error": error,
                "problem_app": problem_app,  # which app (or layer) the setup/error message is about
                "schedule_enabled": bool(self.store.schedule.get("enabled", True)),
                "paused": self.paused_at is not None,
                "can_pause": self.can_pause(),
                "can_next": skip is not None or self.mode in ("playlist", "schedule"),
                "skip": getattr(skip, "skip_unit", "picture") if skip is not None else "item",
                # what you started, so its options form can open with the values on the board
                "spec": top.spec if top is not None and hasattr(top, "spec") and top.id != "layers" else None,
                "layer_set": top.name if top is not None and top.id == "layers" else None,
                "layers_rev": top.rev if top is not None and top.id == "layers" else None,
                "slideshow": slide,
            }

    def _changed(self):
        for fn in self.listeners:
            try:
                fn()
            except Exception as e:
                log.debug("listener failed: %s", e)

    # ---- schedule ----------------------------------------------------------------

    def scheduled_playlist(self, now):
        sched = self.store.schedule
        t = dt.datetime.fromtimestamp(now)
        hm = t.strftime("%H:%M")
        for rule in sched.get("rules", []):
            days = rule.get("days", list(range(7)))
            start, end = rule.get("start", "00:00"), rule.get("end", "24:00")
            if start <= end:
                hit = t.weekday() in days and start <= hm < end
            else:  # crosses midnight: the part after midnight belongs to the previous day
                hit = (t.weekday() in days and hm >= start) or ((t.weekday() - 1) % 7 in days and hm < end)
            if hit:
                return rule.get("playlist")
        return sched.get("default") or None

    # ---- main loop -----------------------------------------------------------------

    def _advance(self, now):
        """In playlist/schedule mode, switch to the next item when it's time."""
        if self.mode == "schedule":
            target = self.scheduled_playlist(now) if self.store.schedule.get("enabled", True) else None
            if target != self.playlist:
                self.playlist = target
                self.item_index, self.item_until = -1, 0
        if self.mode not in ("playlist", "schedule") or now < self.item_until:
            return
        if self.playlist == OFF:
            if not (self.app and self.app.id == "blank"):
                self._set_app(apps.create(self.ctx, {"app": "blank", "params": {"color": "black"}}), "wipe")
            self.item_until = now + 60
            return
        pl = self.store.playlists.get(self.playlist or "")
        items = (pl or {}).get("items") or []
        if not items:
            if self.app:
                self._set_app(None)
            self.item_until = now + 5
            return
        for _ in range(len(items)):
            self.item_index = (self.item_index + 1) % len(items)
            item = items[self.item_index]
            if apps.base.setup_needed(self.store, item):
                continue  # e.g. Weather with no location: skip rather than put an error on the board
            try:
                app = apps.create(self.ctx, item)
            except Exception as e:
                log.warning("playlist %s item %d (%s) failed: %s", self.playlist, self.item_index, item.get("app"), e)
                continue
            kind = item.get("transition") or pl.get("transition", "none")
            self._set_app(app, kind if self.app else "none", float(pl.get("transition_duration") or 1.0))
            self.item_until = now + float(item.get("duration") or 30)
            return
        self.item_until = now + 10  # every item failed; try again later

    def _check_problem(self):
        """Tell listeners when the running app starts or stops reporting an error (widgets fetch in the background)."""
        now = time.time()
        if now < self._problem_at:
            return
        self._problem_at = now + 1
        cur = self.problem(self.notif["app"] if self.notif else self.app)
        if cur != self._problem:
            self._problem = cur
            self._changed()

    def _end_notification(self):
        n = self.notif
        self.notif = None
        n["app"].stop()
        if n["restore"] is not None and self.mode == "manual":
            # show the picture that was there before; the loop drops this app once the transition is done
            self.app = apps.REGISTRY["scene"](self.ctx, frames=[{"rows": n["restore"]}], name="restore", loop=False)
            self.app.restore = True
            self._start_transition(n["transition"], n["transition_duration"])
        else:
            self._start_transition(n["transition"], n["transition_duration"])
        self._changed()

    def _loop(self):
        while True:
            delay = 0.5
            with self.lock:
                now = time.time()
                try:
                    if self.paused_at is None:
                        self._advance(now)
                except Exception:
                    log.exception("playlist advance failed")
                if self.notif and now >= self.notif["until"]:
                    self._end_notification()
                if self.live and now - self.live["last"] > LIVE_TIMEOUT:
                    log.info("live stream %s stopped sending frames", self.live["name"])
                    self.end_stream(self.live["id"], stopped=False)
                app = self.notif["app"] if self.notif else self.app
                frozen = (app is not None and app is self.app and not self.notif and self.paused_at is not None
                          and getattr(app, "pausable", False) and not self.transition)
                if frozen and not self.render_once:
                    app = None  # paused: the board keeps its picture
                    delay = 0.5
                self.render_once = False
                if app is not None:
                    c = Canvas(self.board.width, self.board.height)
                    try:
                        result = app.render(c, self.app_now(now) if getattr(app, "pausable", False) and app is self.app else now)
                    except Exception:
                        log.exception("app %s crashed", app.id)
                        result = False
                    # an app (e.g. the music widget on a new song) can ask for a whole-board transition
                    req = getattr(app, "request_transition", None)
                    if req:
                        app.request_transition = None
                        if not self.transition:
                            self._start_transition(*req)
                    if self.transition:
                        old = None
                        if self.prev_app is not None and not self.notif:
                            old = Canvas(self.board.width, self.board.height)
                            try:
                                self.prev_app.render(old, now)
                            except Exception:
                                log.exception("app %s crashed", self.prev_app.id)
                                old = None
                        out = self.transition.mix(c, now, old)
                        if self.transition.done(now):
                            self.transition = None
                            self._drop_prev()
                    else:
                        out = c
                    self.board.set_canvas(out)
                    tick = getattr(app, "status_tick", None) if app is self.app else None
                    if tick is not None and tick != self._tick:
                        self._tick = tick
                        for fn in self.ticks:
                            fn()
                    if getattr(app, "restore", False) and not self.transition:
                        self.app = None  # restore finished; back to plain manual mode
                    delay = min(app.interval, 1 / self.transition_fps()) if self.transition else app.interval
                    if result is False:
                        if self.notif and app is self.notif["app"]:
                            self._end_notification()
                        elif self.mode == "app":
                            self.app = None
                            self.mode = "manual"
                            self._changed()
                        elif self.mode in ("playlist", "schedule"):
                            self.item_until = 0
                        delay = 0.01
                self._check_problem()
            # time the next tick from when this frame started, so rendering time doesn't slow the frame rate
            self.wake.wait(max(0.0, now + delay - time.time()) if delay > 0.011 else delay)
            self.wake.clear()
