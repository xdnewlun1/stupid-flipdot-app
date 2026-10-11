"""Decides what is on the board: manual drawing, a single app, a playlist, or the
schedule, with notifications shown on top and transitions between them."""

import collections
import datetime as dt
import logging
import os
import threading
import time
from dataclasses import dataclass

import apps
import notifications
import playback
import timetable
from apps.notice import draw_banner
from canvas import Canvas
from transitions import NAMES as TRANSITIONS, Transition

log = logging.getLogger("runner")

OFF = "__off__"  # schedule target meaning "all black"
LIVE_TIMEOUT = 4  # seconds without a frame before a browser's live stream counts as gone
STEADY_BUDGET = 0.025  # steady timing (music visualizer): frames go to the board this long after they're started
BEAT = 2.0  # while waiting longer than this for the next frame, the loop still ticks its heartbeat this often
LOG_EVERY = 60.0  # the same failure, repeating every frame, goes to the log at most this often
ERROR_WINDOW = 300.0  # recent errors: those in the last 5 minutes


@dataclass
class Context:
    width: int
    height: int
    store: object
    wake: object = None  # call to render again now (e.g. a frame arrived from an emulator); None inside layers


class Runner:
    def __init__(self, board, store):
        self.board = board
        self.store = store
        self.wake = threading.Event()
        self.ctx = Context(board.width, board.height, store, self.wake.set)
        self.mode = "manual"  # manual | app | playlist | schedule
        self.app = None
        self.playlist = None
        self.item_index = -1
        self.item_until = 0
        self.order = None  # playback.Order: which playlist item comes next (shuffled or not)
        self.effects = playback.Effects(store)  # the playlist's random effects and React to music
        self.probes = playback.Probes(Context(board.width, board.height, store), apps.create)  # "has something to show"
        self.sched = None  # what the schedule picked (timetable.resolve) while in schedule mode
        self.sched_label = None
        # test hook: the schedule's clock (a stand-in for time.time). FLIPDOT_FAKE_TIME="2026-10-10T18:30" starts it
        # there and lets it run on; unset, the schedule follows the real time
        self.sched_clock = None
        fake = os.environ.get("FLIPDOT_FAKE_TIME")
        if fake:
            shift = dt.datetime.fromisoformat(fake).timestamp() - time.time()
            self.sched_clock = lambda: time.time() + shift
            log.warning("schedule clock set to %s (FLIPDOT_FAKE_TIME)", fake)
        self.notif = None  # dict(app, until, restore, transition, transition_duration, entry): the notification on the board
        self.notify_queue = notifications.Center(store)  # what waits, what's held for do-not-disturb, the history
        self.banner = None  # dict(entry, until, started): a notification shown as a strip over a game
        self._dnd_on, self._dnd_at = False, 0.0
        self.transition = None
        self.prev_app = None  # the outgoing app keeps running during a transition
        self.lock = threading.RLock()
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
        # heartbeat, for /api/health and the systemd watchdog: the loop ticks at least every BEAT seconds
        self.beat = time.monotonic()
        self.beats = 0
        self._beat_marks = collections.deque(maxlen=31)  # (time, beats) once a second, for the tick rate
        self.errors = collections.deque(maxlen=200)  # monotonic times of failures caught in the loop
        self.last_error = None
        self.restarts = 0  # times the loop itself had to start over
        self._logged = {}  # what -> (last logged, times since)
        board.link_listeners.append(self._changed)  # a bus going down or coming back is in status()
        threading.Thread(target=self._loop, daemon=True, name="runner").start()

    # ---- control (called from HTTP/MQTT threads) --------------------------------

    def _current_canvas(self):
        return Canvas.from_dots(self.board.width, self.board.height, self.board.dots()[1])

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
                if self.order is not None and self.order.n:
                    self.order.back()
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
            self.board.interactive = self.board.steady = False  # drawing by hand gets the wave again
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
            if target == "board" and self.notif is not None:
                # a notification (high or critical) is on: the stream's frames wait under it
                if self.live and self.live["id"] == sid:
                    self.live["last"] = time.time()
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
            self.order = None
            self.paused_at = None
            self.wake.set()

    def resume_schedule(self):
        with self.lock:
            self.mode = "schedule"
            self.playlist = None
            self.sched = None
            self.item_index, self.item_until = -1, 0
            self.order = None
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

    def notify(self, spec=None, app=None, duration=10, transition="dissolve", transition_duration=1.0, *,
               priority="normal", icon=None, sound=None, repeat=1, hold=False, key=None, text=None, source=None,
               only_idle=False, dedupe=True, record=True, remake=None):
        """Show something over what's on, then go back to it. It may wait its turn (see notifications.py).
        Returns {"id", "status"}: status is show, banner, replace, queued, held, dropped, duplicate or busy."""
        if app is None:
            name = (spec or {}).get("app")
            if apps.base.ALIASES.get(name, name) not in apps.REGISTRY:
                raise ValueError(f"unknown app {name!r}")
        prio = notifications.priority(priority)
        cfg = notifications.settings(self.store)
        e = notifications.entry(spec, app, duration=duration, transition=transition,
                                transition_duration=transition_duration, priority=prio, icon=icon,
                                sound=sound or cfg.get("sound") or "none", repeat=repeat, hold=hold, key=key, text=text,
                                source=source, only_idle=only_idle, dedupe=dedupe, record=record, remake=remake)
        with self.lock:
            status = self._offer(e, time.time())
            self.wake.set()
            self._changed()
        return {"id": e["id"], "status": status, "priority": prio}

    def _busy(self):
        """What a low or normal notification has to wait for: "game", "live" (a browser streaming) or None."""
        if self.live and self.live.get("target") == "board":
            return "live"
        if self.app is not None and getattr(self.app, "interactive", False):
            return "game"
        return None

    def _current_entry(self):
        if not self.notif:
            return None
        # a notification put on the board some other way counts as a normal one
        return self.notif.get("entry") or {"priority": 1, "key": None, "only_idle": False, "sig": None, "hold": False}

    def _offer(self, e, now):
        banner = self.banner["entry"] if self.banner else None
        action, target = self.notify_queue.offer(e, now, self._current_entry(), self._busy(), banner, self._dnd(now))
        if action == "show":
            self._show(e, now, displaced=target)
        elif action == "banner":
            self._show_banner(e, now)
        elif action == "replace" and target == "current":
            self._replace_current(e, now)
        elif action == "replace" and target == "banner":
            self._show_banner(e, now, keep_place=True)
        return action

    def _maker(self, e):
        if e.get("remake"):
            return e["remake"]
        if e["app"] is not None:
            return lambda: e["app"]
        return lambda: apps.create(self.ctx, e["spec"])

    def _show(self, e, now, displaced=None, restore=False):
        """Put entry `e` on the board now. `displaced`: the entry it takes the place of (it waits for its turn)."""
        make = self._maker(e)
        first = e["app"] if e["app"] is not None and not e.get("_used") else make()
        e["_used"] = True
        if restore is False:
            restore = self.board.rows() if self.mode == "manual" else None
        if self.notif:
            old = self.notif
            restore = old["restore"]
            back = old.get("entry") is not None and displaced is old.get("entry") and self.notify_queue.requeue(
                old["entry"], old["until"] - now)
            if not (back and old["entry"]["spec"] is None):  # an app object coming back later keeps its state
                old["app"].stop()
        resumed = e.get("left") is not None
        sound = "none" if resumed else e["sound"]
        if self.board.max_flips_per_sec and sound == "sweep":
            sound = "tick"  # a whole-board sweep under quiet hours' flip budget would crawl
        if sound != "none" or e["repeat"] > 1:
            ready = [first]
            app = notifications.Attention(lambda: ready.pop() if ready else make(), self._current_canvas(), sound,
                                          e["duration"], 1 if resumed else e["repeat"], e["transition"],
                                          e["transition_duration"], now)
            if sound == "none":
                self._start_transition(e["transition"], e["transition_duration"])
            else:
                self.transition = None
        else:
            app = first
            self._start_transition(e["transition"], e["transition_duration"])
        pre = notifications.PRE_LEN.get(sound, 0)
        if e["hold"]:
            until = notifications.FOREVER
        elif resumed:
            until = now + e["left"]
        else:
            until = now + (e["duration"] + pre) * e["repeat"]
        e["left"] = None
        self.notif = {"app": app, "until": until, "restore": restore, "transition": e["transition"],
                      "transition_duration": e["transition_duration"], "entry": e}
        self.notify_queue.shown(e, now)
        self.wake.set()

    def _replace_current(self, e, now):
        """Same key as the notification on the board: swap in the new one where it is, no pattern, no transition."""
        old = self.notif
        app = self._maker(e)()
        e["_used"] = True
        old["app"].stop()
        old.update(app=app, entry=e, until=notifications.FOREVER if e["hold"] else now + e["duration"] * e["repeat"])
        self.notify_queue.shown(e, now)
        self.wake.set()

    def _show_banner(self, e, now, keep_place=False):
        """A notification as a strip over the game; a lower one it outranks waits for its turn."""
        if self.banner and not keep_place:
            old = self.banner
            self.notify_queue.requeue(old["entry"], old["until"] - now)
        started = self.banner["started"] if keep_place and self.banner else now
        until = notifications.FOREVER if e["hold"] else now + e["duration"] * e["repeat"]
        self.banner = {"entry": e, "until": until, "started": started}
        self.notify_queue.shown(e, now, "banner")
        self.wake.set()

    def _dnd(self, now):
        """Is do-not-disturb on (looked up at most once a second)? When it ends, what it held is shown."""
        if now - self._dnd_at >= 1.0 or now < self._dnd_at:
            self._dnd_at = now
            try:
                on = notifications.dnd_active(self.store)
            except Exception:
                on = False
            was, self._dnd_on = self._dnd_on, on
            if was and not on:
                for e in self.notify_queue.dnd_ended(now):
                    self._offer(e, now)
                self._changed()
        return self._dnd_on

    def _pump_notifications(self, now):
        """Each tick: end a finished banner, and put the next waiting notification on once it can show."""
        self._dnd(now)
        changed = False
        if self.banner and now >= self.banner["until"]:
            self.banner = None
            changed = True
        q = self.notify_queue
        if q.pending and self.notif is None:
            nxt = q.next_ready(self._busy(), self.banner["entry"] if self.banner else None)
            if nxt is not None:
                how, e = nxt
                self._show(e, now) if how == "show" else self._show_banner(e, now)
                changed = True
        if changed:
            self._changed()

    def dismiss(self, eid=None, key=None):
        """Take a notification away: the one on the board (no id/key, or its id/key), the banner, or one waiting."""
        with self.lock:
            hit = lambda e: e is not None and ((eid is None and key is None) or e.get("id") == eid
                                               or (key is not None and e.get("key") == key))
            done = False
            if self.notif and hit(self.notif.get("entry") or ({} if eid is None and key is None else None)):
                self.notif["until"] = 0
                done = True
            elif self.banner and hit(self.banner["entry"]):
                self.banner = None
                done = True
            elif eid is not None:
                done = self.notify_queue.remove(eid) is not None
            elif key is not None:
                for e in list(self.notify_queue.pending) + list(self.notify_queue.held):
                    if e.get("key") == key:
                        done = self.notify_queue.remove(e["id"]) is not None
            self.wake.set()
            self._changed()
            return done

    def clear_notifications(self):
        """Everything waiting or held goes, and so does what's showing."""
        with self.lock:
            n = self.notify_queue.clear()
            if self.notif:
                self.notif["until"] = 0
                n += 1
            if self.banner:
                self.banner = None
                n += 1
            self.wake.set()
            self._changed()
            return n

    def reshow(self, eid):
        """Show a notification from the history again."""
        old = self.notify_queue.find_history(eid)
        if old is None:
            raise ValueError("that notification is no longer in the history")
        if not (old.get("spec") or old.get("remake")):
            raise ValueError("that notification can't be shown again")
        return self.notify(spec=old["spec"], app=old["remake"]() if old.get("remake") else None,
                           duration=old["duration"], transition=old["transition"],
                           transition_duration=old["transition_duration"],
                           priority=notifications.NAMES[max(1, old["priority"])], icon=old["icon"], sound="none",
                           repeat=1, hold=old["hold"], text=old["text"], source="history", dedupe=False,
                           remake=old.get("remake"))

    def notifications_status(self):
        with self.lock:
            q = self.notify_queue
            pub = notifications.public
            return {
                "current": pub(self.notif.get("entry"), "showing") if self.notif else None,
                "banner": pub(self.banner["entry"], "banner") if self.banner else None,
                "pending": [pub(e, "waiting") for e in q.pending],
                "held": [pub(e, "held") for e in q.held],
                "history": [pub(e) for e in q.history],
                "dnd": self._dnd_on,
                "busy": self._busy(),
            }

    def input(self, key, action="down", player=1):
        n = self.notif
        if n and (n.get("entry") or {}).get("hold") and not getattr(n["app"], "interactive", False):
            if action == "down":
                self.dismiss()  # any button clears a notification that stays until dismissed
            return True
        # an app that just passes input on (an emulator's pipe) takes it without waiting for a frame being rendered;
        # input_now() returns False for anything that needs the usual path
        target = self.notif["app"] if self.notif else self.app
        fast = getattr(target, "input_now", None)
        if fast is not None and fast(key, action, player):
            return True
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
                "notifications": {"pending": len(self.notify_queue.pending), "held": len(self.notify_queue.held),
                                  "banner": bool(self.banner), "dnd": self._dnd_on,
                                  "current": (self.notif.get("entry") or {}).get("id") if self.notif else None,
                                  **self._notif_timing()},
                "drawings": self.drawings_on_board(),
                # what the schedule shows: its label, and which rule (index, id) picked it, None for the default
                "schedule_target": self.sched_label if self.mode == "schedule" else None,
                "schedule_rule": (self.sched or {}).get("rule") if self.mode == "schedule" else None,
                "schedule_rule_id": (self.sched or {}).get("id") if self.mode == "schedule" else None,
                "shuffle": bool((self._playlist_now() or {}).get("shuffle")) if self.mode in ("playlist", "schedule") else False,
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
                "link": self.board.link_status(),  # buses that can't be reached: the board isn't showing this
            }

    def _notif_timing(self):
        """How long the notification on the board stays: {"hold": True} until dismissed, else its seconds."""
        n = self.notif
        if not n:
            return {}
        e = n.get("entry") or {}
        if e.get("hold") or n["until"] >= notifications.FOREVER:
            return {"hold": True, "seconds": None, "until": None}
        started = e.get("shown_at") or time.time()
        return {"hold": False, "seconds": max(1, round(n["until"] - started)), "until": n["until"]}

    def heartbeat(self):
        """How the main loop is doing: seconds since it last ticked, ticks per second lately, recent errors."""
        now = time.monotonic()
        marks = list(self._beat_marks)
        rate = None
        if len(marks) >= 2 and marks[-1][0] > marks[0][0]:
            rate = round((marks[-1][1] - marks[0][1]) / (marks[-1][0] - marks[0][0]), 1)
        recent = sum(1 for t in list(self.errors) if now - t < ERROR_WINDOW)
        return {"age_s": round(now - self.beat, 2), "ticks": self.beats, "tick_rate": rate, "errors_5min": recent,
                "last_error": self.last_error, "restarts": self.restarts}

    def _tick_beat(self, now):
        self.beat = now
        self.beats += 1
        if not self._beat_marks or now - self._beat_marks[-1][0] >= 1.0:
            self._beat_marks.append((now, self.beats))

    def _failed(self, what, exc=True):
        """A step of the loop failed: count it, and log it with its traceback, but the same failure at most once
        a minute (a frame that fails 25 times a second mustn't fill the journal or the SD card)."""
        now = time.monotonic()
        self.errors.append(now)
        self.last_error = {"what": what, "at": time.time()}
        last, quiet = self._logged.get(what, (-1e9, 0))
        if now - last < LOG_EVERY:
            self._logged[what] = (last, quiet + 1)
            return
        self._logged[what] = (now, 0)
        more = f" (and {quiet} more times since)" if quiet else ""
        log.error("%s%s", what, more, exc_info=exc)

    def _changed(self):
        for fn in self.listeners:
            try:
                fn()
            except Exception as e:
                log.debug("listener failed: %s", e)

    # ---- schedule ----------------------------------------------------------------

    def sched_now(self, now=None):
        """The time the schedule goes by (the real time unless a test set sched_clock)."""
        return self.sched_clock() if self.sched_clock is not None else (time.time() if now is None else now)

    def schedule_rules(self):
        """The schedule in the current format (timetable.normalize), worked out again only when it's saved."""
        raw = self.store.schedule
        cached = getattr(self, "_sched_cache", None)
        if cached is None or cached[0] is not raw:
            cached = self._sched_cache = (raw, timetable.normalize(raw))
        return cached[1]

    def schedule_clock(self):
        """Rule times for the weather location (sunrise/sunset), cached while the location stays the same."""
        loc = self.store.settings.get("weather") or {}
        k = (loc.get("lat"), loc.get("lon"))
        c = getattr(self, "_clock", None)
        if c is None or c[0] != k or len(c[1]._sun) > 60:
            c = self._clock = (k, timetable.Clock({"lat": k[0], "lon": k[1]}))
        return c[1]

    def scheduled(self, now):
        """What the schedule shows at `now` (timetable.resolve's answer)."""
        t = dt.datetime.fromtimestamp(self.sched_now(now))
        return timetable.resolve(self.schedule_rules(), t, self.schedule_clock())

    def scheduled_playlist(self, now):
        """The playlist the schedule plays at `now` (OFF for all black), or None for nothing or a rule that shows
        an app, layer set, slideshow, video or picture instead."""
        res = self.scheduled(now)
        return None if res["spec"] else (res["playlist"] or None)

    def _playlist_now(self):
        """The playlist dict being played: a saved one, or a rule's single target shown until the rule ends."""
        if self.mode == "schedule" and self.sched and self.sched.get("spec"):
            return {"items": [{**self.sched["spec"], "duration": None}], "transition": "dissolve", "single": True}
        return self.store.playlists.get(self.playlist or "")

    # ---- main loop -----------------------------------------------------------------

    def _advance(self, now):
        """In playlist/schedule mode, switch to the next item when it's time."""
        if self.mode == "schedule":
            if self.store.schedule.get("enabled", True):
                res = self.scheduled(now)
            else:
                res = None
            if timetable.key(res) != timetable.key(self.sched):
                self.sched = res
                self.sched_label = target_label(self.store, res) if res else None
                self.playlist = None if res is None or res["spec"] else (res["playlist"] or None)
                self.item_index, self.item_until = -1, 0
                self.order = None
                self._changed()
        if self.mode not in ("playlist", "schedule") or now < self.item_until:
            return
        if self.playlist == OFF:
            if not (self.app and self.app.id == "blank"):
                self._set_app(apps.create(self.ctx, {"app": "blank", "params": {"color": "black"}}), "wipe")
            self.effects.reset()
            self.item_until = now + 60
            return
        pl = self._playlist_now()
        items = (pl or {}).get("items") or []
        if not items:
            if self.app:
                self._set_app(None)
            self.item_until = now + 5
            return
        if self.order is None or not self.order.matches(len(items), pl.get("shuffle")):
            self.order = playback.Order(len(items), pl.get("shuffle"))
        self.probes.sweep(now)
        for _ in range(len(items)):
            self.item_index = self.order.next()
            item = items[self.item_index]
            if apps.base.setup_needed(self.store, item):
                self.order.skipped()
                continue  # e.g. Weather with no location: skip rather than put an error on the board
            if item.get("when") and not playback.condition_ok(
                    item["when"], item, self.store, self.sched_now(now), self.probes, self.quiet):
                self.order.skipped()
                continue  # "only show if": not now
            try:
                app = apps.create(self.ctx, item)
            except Exception as e:
                self.order.skipped()
                log.warning("playlist %s item %d (%s) failed: %s", self.playlist, self.item_index, item.get("app"), e)
                continue
            kind = item.get("transition") or pl.get("transition", "none")
            tdur = item.get("transition_duration") or pl.get("transition_duration") or 1.0
            self._set_app(app, kind if self.app else "none", float(tdur))
            if pl.get("single"):
                self.item_until = float("inf")  # until the rule ends (or the app finishes and starts again)
                self.effects.reset()
            else:
                self.item_until = now + playback.item_seconds(item)
                self.effects.item_started(pl, now, playback.item_seconds(item))
            return
        self.item_until = now + 10  # nothing can show now: what's on stays, try again later

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
        if self.notify_queue.pending:
            nxt = self.notify_queue.next_ready(self._busy(), self.banner["entry"] if self.banner else None)
            if nxt is not None and nxt[0] == "show":
                self._show(nxt[1], time.time(), restore=n["restore"])  # straight on to the next, no picture in between
                self._changed()
                return
            if nxt is not None:
                self._show_banner(nxt[1], time.time())
        if n["restore"] is not None and self.mode == "manual":
            # show the picture that was there before; the loop drops this app once the transition is done
            self.app = apps.REGISTRY["scene"](self.ctx, frames=[{"rows": n["restore"]}], name="restore", loop=False)
            self.app.restore = True
            self._start_transition(n["transition"], n["transition_duration"])
        else:
            self._start_transition(n["transition"], n["transition_duration"])
        self._changed()

    def _end_notification_safely(self):
        try:
            self._end_notification()
        except Exception:
            self._failed("ending the notification failed")
            self.notif = None  # cleared either way: the board goes back to what was on
            self.transition = None
            self._changed()

    def _loop(self):
        # nothing that goes wrong in one frame may stop the board: each step below catches its own failures, and
        # this restarts the loop if anything else gets out
        while True:
            try:
                self._run()
            except Exception:
                self.restarts += 1
                self._failed("runner loop failed; starting it again")
                time.sleep(0.5)

    def _wait(self, timeout):
        """Wait for the next frame (or a wake), ticking the heartbeat while a long wait goes on."""
        if timeout <= BEAT:
            self.wake.wait(timeout)
        else:
            end = time.monotonic() + timeout
            while not self.wake.wait(min(BEAT, end - time.monotonic())):
                self._tick_beat(time.monotonic())
                if time.monotonic() >= end:
                    break
        self.wake.clear()

    def _run(self):
        while True:
            delay = 0.5
            ticked = False
            with self.lock:
                now = time.time()
                started = time.monotonic()
                self._tick_beat(started)
                try:
                    if self.paused_at is None:
                        self._advance(now)
                except Exception:
                    self._failed("playlist advance failed")
                if self.notif and now >= self.notif["until"]:
                    self._end_notification_safely()
                try:
                    self._pump_notifications(now)
                except Exception:
                    self._failed("showing the next notification failed")
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
                        self._failed(f"app {app.id} crashed")
                        result = False
                    # a playlist's random effects and React to music go on top of its items (not games being played)
                    fx_pl, plan = None, None
                    if (app is self.app and not self.notif and self.mode in ("playlist", "schedule")
                            and not app.interactive):
                        fx_pl = self._playlist_now()
                        if not playback.Effects.wanted(fx_pl):
                            fx_pl = None
                    if fx_pl is not None:
                        try:
                            c = self.effects.random_fx(c, now)
                            plan = self.effects.plan(fx_pl, now, self.quiet)
                        except Exception:
                            self._failed("playlist effects failed")
                            fx_pl, plan = None, None
                    out = c
                    try:
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
                                    self._failed(f"app {self.prev_app.id} crashed")
                                    old = None
                            out = self.transition.mix(c, now, old)
                            if self.transition.done(now):
                                self.transition = None
                                self._drop_prev()
                    except Exception:
                        # a broken transition is dropped: the incoming picture goes straight on
                        self._failed("transition failed")
                        out = c
                        self.transition = None
                        try:
                            self._drop_prev()
                        except Exception:
                            self.prev_app = None
                    if plan:  # on top of the transition too, so it carries on across items
                        try:
                            out = self.effects.music_fx(out, now, plan)
                        except Exception:
                            self._failed("React to music failed")
                    banner = self.banner if app is self.app else None
                    if banner is not None:
                        try:
                            e = banner["entry"]
                            pos = notifications.settings(self.store).get("banner_position") or "bottom"
                            draw_banner(out, e["text"], e.get("icon"), now, banner["started"], pos)
                        except Exception:
                            self._failed("drawing the notification banner failed")
                    self.board.interactive = bool(app.interactive)
                    self.board.steady = bool(getattr(app, "steady_timing", False))
                    shown = True
                    try:
                        # steady timing: the picture goes out a fixed time after this frame started, whatever the render took
                        self.board.set_canvas(out, started + STEADY_BUDGET if self.board.steady else None)
                    except Exception:
                        self._failed("showing the frame failed")
                        shown = False
                    tick = getattr(app, "status_tick", None) if app is self.app else None
                    if tick is not None and tick != self._tick:
                        self._tick = tick
                        ticked = True
                    if getattr(app, "restore", False) and not self.transition:
                        self.app = None  # restore finished; back to plain manual mode
                    try:
                        delay = min(app.interval, 1 / self.transition_fps()) if self.transition else app.interval
                        delay = float(delay)
                    except Exception:
                        self._failed(f"app {app.id} has no usable interval")
                        delay = 0.5
                    if banner is not None:
                        delay = min(delay, 0.05)  # the banner's text scrolls
                    if fx_pl is not None:
                        wait = self.effects.interval(now, self.transition_fps())
                        if wait is not None:
                            delay = min(delay, wait)
                    if not shown:
                        delay = min(delay, 0.5)  # try again soon, whatever the app's interval
                    if result is False:
                        if self.notif and app is self.notif["app"]:
                            self._end_notification_safely()
                        elif self.mode == "app":
                            self.app = None
                            self.mode = "manual"
                            self._changed()
                        elif self.mode in ("playlist", "schedule"):
                            self.item_until = 0
                        delay = 0.01
                try:
                    self._check_problem()
                except Exception:
                    self._failed("checking the app for problems failed")
            if ticked:  # outside the lock: listeners may ask for status()
                for fn in self.ticks:
                    try:
                        fn()
                    except Exception:
                        self._failed("progress listener failed")
            # time the next tick from when this frame started, so rendering time doesn't slow the frame rate
            self._wait(max(0.0, now + delay - time.time()) if delay > 0.011 else delay)


def target_label(store, res):
    """Words for what a schedule resolution shows: a playlist name, "Off (all black)", "Layer set: Party"..."""
    if res is None:
        return None
    spec = res.get("spec")
    if not spec:
        pl = res.get("playlist") or ""
        return "Off (all black)" if pl == OFF else pl or "Nothing"
    p = spec.get("params") or {}
    app = spec.get("app")
    try:
        if app == "layers" and p.get("composition"):
            return f"Layer set: {p['composition']}"
        if app == "slideshow":
            return f"Slideshow: {(store.galleries.get(p.get('gallery')) or {}).get('name') or 'deleted'}"
        if app == "scene":
            return f"Picture: {(store.get_scene(p.get('scene')) or {}).get('name') or 'deleted'}"
        if app == "drawing":
            return f"Drawing: {(store.drawings.get(p.get('drawing')) or {}).get('name') or 'deleted'}"
        if app == "video":
            import video
            v = video.library.videos.get(p.get("video")) if video.library else None
            return f"Video: {(v or {}).get('name') or 'deleted'}"
    except Exception:
        pass
    cls = apps.REGISTRY.get(apps.base.ALIASES.get(app, app))
    return cls.title if cls else str(app)
