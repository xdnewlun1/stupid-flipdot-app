import datetime as dt
import math

from fonts import NORMAL, SMALL

from charfx import CHAR_PARAMS, CharFx

from .base import App, P, register


def _fmt_time(now, hour24, seconds):
    fmt = ("%H" if hour24 else "%I") + ":%M" + (":%S" if seconds else "")
    s = now.strftime(fmt)
    return s if hour24 else s.lstrip("0") or "12"


@register
class DigitalClock(App):
    id = "clock"
    title = "Digital clock"
    category = "Clocks"
    description = "Big seven-segment clock with optional date."
    params = [
        P("hour24", "checkbox", False, "24-hour"),
        P("seconds", "checkbox", False, "Show seconds"),
        P("date", "checkbox", True, "Show date"),
        *CHAR_PARAMS,
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.fx = CharFx(self.p["char_transition"], self.p["char_time"])

    def render(self, c, now):
        boxes = []
        self._render(c, now, boxes)
        self.fx.apply(c, boxes, now)
        self.interval = 0.04 if self.fx.active else (0.25 if self.fx.kind == "none" else 0.1)

    def _render(self, c, now, boxes):
        t = dt.datetime.fromtimestamp(now)
        s = _fmt_time(t, self.p["hour24"], self.p["seconds"])
        if self.p["seconds"]:
            size = dict(w=9, h=17, t=2, gap=2)
        else:
            size = dict(w=14, h=27, t=3, gap=3)
        tw = c.seg_width(s, size["w"], size["t"], size["gap"])
        y = 2 if self.p["date"] else (c.h - size["h"]) // 2
        if self.p["seconds"] and self.p["date"]:
            y = 6
        c.seg_text((c.w - tw) // 2, y, s, boxes=boxes, **size)
        if not self.p["hour24"]:
            c.text(c.w - 9, y, t.strftime("%p")[0], SMALL, boxes=boxes, slot="ampm")
        if self.p["date"]:
            c.text_center(c.h - 9, t.strftime("%a %b ") + str(t.day), NORMAL, boxes=boxes, slot="date")


@register
class AnalogClock(App):
    id = "analog_clock"
    title = "Analog clock"
    category = "Clocks"
    description = "Clock face on the left, date and time on the right."
    params = [
        P("seconds", "checkbox", True, "Seconds hand"),
        P("hour24", "checkbox", False, "24-hour digital time"),
    ]
    interval = 0.5

    def render(self, c, now):
        t = dt.datetime.fromtimestamp(now)
        r = c.h // 2 - 1
        cx, cy = r + 1, c.h // 2
        for i in range(12):
            a = math.radians(i * 30)
            inner = r - (3 if i % 3 == 0 else 1)
            c.line(cx + inner * math.sin(a), cy - inner * math.cos(a), cx + r * math.sin(a), cy - r * math.cos(a))

        def hand(angle, length):
            a = math.radians(angle)
            c.line(cx, cy, cx + length * math.sin(a), cy - length * math.cos(a))

        hand((t.hour % 12 + t.minute / 60) * 30, r * 0.5)
        hand((t.minute + t.second / 60) * 6, r * 0.8)
        if self.p["seconds"]:
            hand(t.second * 6, r * 0.9)
        c.rect(cx - 1, cy - 1, 3, 3, fill=True)

        x0 = 2 * r + 4
        w = c.w - x0
        c.text_center(6, t.strftime("%A")[:9], NORMAL, x0=x0, w=w)
        c.text_center(17, t.strftime("%b ") + str(t.day), NORMAL, x0=x0, w=w)
        c.text_center(29, _fmt_time(t, self.p["hour24"], False), NORMAL, x0=x0, w=w)


@register
class Countdown(App):
    id = "countdown"
    title = "Countdown"
    category = "Clocks"
    description = "Counts down to a date and time."
    params = [
        P("label", "text", "New Year"),
        P("target", "datetime", f"{dt.date.today().year + 1}-01-01T00:00", "Target date/time"),
        P("done_text", "text", "It's time!", "Text when finished"),
        *CHAR_PARAMS,
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.fx = CharFx(self.p["char_transition"], self.p["char_time"])

    def render(self, c, now):
        boxes = []
        self._render(c, now, boxes)
        self.fx.apply(c, boxes, now)
        self.interval = 0.04 if self.fx.active else (0.25 if self.fx.kind == "none" else 0.1)

    def _render(self, c, now, boxes):
        target = dt.datetime.fromisoformat(self.p["target"]).timestamp()
        left = int(target - now)
        c.text_center(1, self.p["label"][:16], NORMAL)
        if left <= 0:
            if int(now * 2) % 2:
                c.text_center(19, self.p["done_text"][:16], NORMAL)
            return
        days, rem = divmod(left, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        hms = f"{h:02}:{m:02}:{s:02}"
        size = dict(w=8, h=15, t=2, gap=2)
        if days:
            c.text_center(11, f"{days} day{'s' if days != 1 else ''}", NORMAL, boxes=boxes, slot="days")
            y = 22
        else:
            y = 14
        c.seg_text((c.w - c.seg_width(hms, size["w"], size["t"], size["gap"])) // 2, y, hms, boxes=boxes, **size)
