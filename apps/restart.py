"""A transition for effects that finish and start over (a completed maze, a settled Game of Life)."""

import random

from canvas import Canvas
from transitions import NAMES, Transition

from .base import P

NICE = ["morph", "migrate", "scatter", "life", "dissolve", "fade", "iris-open", "iris-close", "clock",
        "spiral", "tiles", "drip", "diagonal", "wipe-ragged", "push-up", "zoom-in", "melt", "pixelate", "blinds"]

RESTART_PARAMS = [
    P("restart_transition", "select", "dissolve", "Transition when it restarts",
      options=[["none", "None"], ["random", "Random"]] + [[n, n] for n in NAMES if n not in ("none", "random")]),
    P("restart_time", "number", 1.5, "Restart transition time (s)", min=0.2, max=6, step=0.1,
      show_if={"restart_transition": {"not": "none"}}),
]


class RestartFx:
    """Remembers the last frame shown; on restart, blends from it into the fresh start (live)."""

    def __init__(self, app):
        self.app = app
        self.trans = None
        self.last = None
        self.fps = max(5, min(30, float(app.ctx.store.settings.get("display", {}).get("transition_fps", 15))))

    def restarted(self, now):
        """Call right after the effect resets itself."""
        kind = self.app.p.get("restart_transition", "none")
        if kind == "random":
            kind = random.choice(NICE)
        if kind != "none" and self.last is not None:
            self.trans = Transition(kind, self.last, self.last.w, self.last.h, float(self.app.p.get("restart_time", 1.5)))
            self.trans.start = now

    def finish(self, c, now):
        """Call at the end of render(): applies the transition (if any) to the frame in `c`."""
        if self.trans is not None:
            new = Canvas(c.w, c.h)
            new.px[:] = c.px
            out = self.trans.mix(new, now)
            c.px[:] = out.px
            if self.trans.done(now):
                self.trans = None
        self.last = Canvas(c.w, c.h)
        self.last.px[:] = c.px

    @property
    def active(self):
        return self.trans is not None

    def interval(self, normal):
        return 1 / self.fps if self.trans is not None else normal
