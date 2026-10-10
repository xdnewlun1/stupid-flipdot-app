"""Looking after the hardware: quiet hours (a flip budget + slower transitions), disc wear
counts, and exercising discs that rarely move."""

import datetime as dt
import logging
import random
import threading
import time

from apps.base import App
from canvas import Canvas

log = logging.getLogger("boardcare")

SAVE_EVERY = 120  # seconds between wear-count saves


def quiet_active(cfg, t):
    """Is `t` (a datetime) inside the quiet hours in cfg? Windows may cross midnight."""
    if not cfg.get("enabled"):
        return False
    start, end = cfg.get("start") or "22:00", cfg.get("end") or "07:00"
    hm = t.strftime("%H:%M")
    return start <= hm < end if start <= end else (hm >= start or hm < end)


class Exercise(App):
    """Flips a set of discs back and forth, then leaves the picture as it found it."""
    id = "exercise"
    title = "Exercising rarely flipped discs"

    def __init__(self, ctx, base, dots, times=3, interval=0.6):
        super().__init__(ctx)
        self.base, self.dots, self.steps, self.interval = base, dots, times * 2, interval
        self.step = 0

    def render(self, c, now):
        c.px[:] = self.base.px
        if self.step % 2 == 1:
            for i in self.dots:
                c.px[i] ^= 1
        self.step += 1
        return self.step <= self.steps


class BoardCare:
    def __init__(self, board, runner, store):
        self.board, self.runner, self.store = board, runner, store
        self.since = time.time()
        saved = store._load("wear.json", {})
        if saved.get("width") == board.width and saved.get("height") == board.height:
            for i, n in enumerate(saved.get("counts", [])[:len(board.flips)]):
                board.flips[i] = int(n)
            self.since = saved.get("since", self.since)
        self._last_save = time.time()
        self._auto_day = None
        self.apply()
        threading.Thread(target=self._loop, daemon=True).start()

    def settings(self, section):
        return self.store.settings.get(section) or {}

    def apply(self):
        """Push the quiet-hours state to the board and runner (call after settings change)."""
        cfg = self.settings("quiet_hours")
        quiet = quiet_active(cfg, dt.datetime.now())
        self.board.max_flips_per_sec = max(10, int(cfg.get("max_flips") or 150)) if quiet else 0
        self.runner.slowdown = max(1.0, min(10.0, float(cfg.get("slowdown") or 1))) if quiet else 1.0
        if quiet != self.runner.quiet:
            self.runner.quiet = quiet
            log.info("quiet hours %s", "started" if quiet else "ended")
            self.runner._changed()

    def save(self):
        self.board.flips_changed = False
        self.store._save("wear.json", {"width": self.board.width, "height": self.board.height,
                                       "since": self.since, "counts": list(self.board.flips)})
        self._last_save = time.time()

    def _loop(self):
        while True:
            time.sleep(5)
            try:
                self.apply()
                if self.board.flips_changed and time.time() - self._last_save >= SAVE_EVERY:
                    self.save()
                self._auto_exercise()
            except Exception:
                log.exception("board care tick failed")

    def _auto_exercise(self):
        cfg = self.settings("board_health")
        now = dt.datetime.now()
        if not cfg.get("auto_exercise") or self.runner.quiet or now.strftime("%H:%M") != cfg.get("exercise_time", "12:00"):
            return
        if self._auto_day == now.date():
            return
        self._auto_day = now.date()
        log.info("daily disc exercise")
        self.exercise()

    # ---- wear ------------------------------------------------------------------

    def stats(self, top=5):
        b = self.board
        counts = list(b.flips)
        order = sorted(range(len(counts)), key=counts.__getitem__)
        spot = lambda i: {"x": i % b.width, "y": i // b.width, "count": counts[i]}
        return {"width": b.width, "height": b.height, "total": sum(counts), "since": self.since,
                "counts": counts, "max": counts[order[-1]], "min": counts[order[0]],
                "most": [spot(i) for i in reversed(order[-top:])], "least": [spot(i) for i in order[:top]]}

    def least_used(self, fraction=0.125):
        counts = self.board.flips
        idx = list(range(len(counts)))
        random.shuffle(idx)  # ties (e.g. a fresh install) get a random pick, not the top-left corner
        idx.sort(key=counts.__getitem__)
        return idx[:max(1, int(len(idx) * fraction))]

    def exercise(self, times=3):
        """Flip the least-used eighth of the discs `times` times, then go back to what was showing."""
        dots = self.least_used()
        base = Canvas.from_rows(self.board.rows())
        rate = self.board.max_flips_per_sec
        # under a flip budget, give each step time to reach the board instead of being coalesced away
        interval = max(0.6, len(dots) / rate * 1.2) if rate else 0.6
        app = Exercise(self.runner.ctx, base, dots, times, interval)
        app.interval = interval
        self.runner.notify(app=app, duration=interval * (times * 2 + 2), transition="none")
        return {"discs": len(dots), "seconds": round(interval * times * 2, 1)}
