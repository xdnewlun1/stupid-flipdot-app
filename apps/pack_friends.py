"""Friend messages on the board: one message with its sender's name (shown as a notification), and the
"Message wall" app that rotates the recent ones. The friend page's preview uses render_message too, so
it shows exactly what the board will."""

import time
from types import SimpleNamespace

import friends
from canvas import Canvas
from fonts import NORMAL, SMALL

from .base import App, P, register
from .text import Text

HEADER = SMALL.height + 3  # rows taken by the sender's name


def _text_app(msg, w, h, transition="none"):
    st = {**friends.STYLE_DEFAULTS, **(msg.get("style") or {})}
    params = {"text": msg.get("text") or "", "style": "static", "font": st["font"], "align": st["align"],
              "fit": st["size"] == "fit", "scale": 1 if st["size"] == "fit" else int(st["size"]),
              "font_size": 14, "char_transition": transition, "invert": False}
    return Text(SimpleNamespace(width=w, height=h, store=None, wake=None), **params)


def _paste(c, src, y0):
    for y in range(src.h):
        if 0 <= y0 + y < c.h:
            c.px[(y0 + y) * c.w:(y0 + y) * c.w + min(c.w, src.w)] = src.px[y * src.w:y * src.w + min(c.w, src.w)]


def render_message(msg, w=84, h=42, show_name=True):
    """The finished frame of a message, as a Canvas."""
    named = show_name and bool(msg.get("name"))
    area = Canvas(w, h - HEADER if named else h)
    if msg.get("text"):
        _text_app(msg, area.w, area.h).render(area, time.time() + 3600)
    c = Canvas(w, h)
    _compose(c, msg, area, named)
    return c


def _compose(c, msg, area, named):
    if named:
        name = msg["name"].upper()
        while SMALL.width(name) > c.w - 2 and len(name) > 1:
            name = name[:-1]
        c.text((c.w - SMALL.width(name)) // 2, 1, name, SMALL)
        _paste(c, area, HEADER)
    else:
        c.px[:] = area.px
    for y, row in enumerate((msg.get("doodle") or [])[:c.h]):
        base = y * c.w
        for x, v in enumerate(row[:c.w]):
            if v == "1":
                c.px[base + x] = 1
    if (msg.get("style") or {}).get("invert"):
        c.invert()


class FriendMessage(App):
    """One friend message with the sender's name (not in the app list: it's shown as a notification)."""
    id = "friend_message"
    title = "Friend message"
    category = "Text"
    interval = 0.05

    def __init__(self, ctx, message=None, show_name=True, **params):
        super().__init__(ctx, **params)
        self.msg = message or {}
        self.named = show_name and bool(self.msg.get("name"))
        self.area = Canvas(self.w, self.h - HEADER if self.named else self.h)
        transition = (self.msg.get("style") or {}).get("transition") or "none"
        self.text = _text_app(self.msg, self.area.w, self.area.h, transition) if self.msg.get("text") else None
        self.still = transition == "none"
        self.frame = None

    def render(self, c, now):
        if self.frame is None or not self.still:
            self.area.px[:] = bytes(len(self.area.px))
            if self.text:
                self.text.render(self.area, now)
            f = Canvas(self.w, self.h)
            _compose(f, self.msg, self.area, self.named)
            self.frame = f
        c.px[:] = self.frame.px

    def describe(self):
        return f"Message from {self.msg.get('name') or 'a friend'}"


@register
class MessageWall(App):
    id = "message_wall"
    title = "Message wall"
    category = "Text"
    description = "Recent messages from friends (Settings, People & access), one after another with who sent them."
    params = [
        P("seconds", "number", 12, "Show each message for (seconds)", min=3, max=600),
        P("count", "number", 10, "Newest messages to rotate", min=1, max=50),
        P("days", "number", 14, "Only messages from the last (days)", min=1, max=365),
        P("show_names", "checkbox", True, "Show who sent it"),
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.box = friends.get(ctx.store) if ctx.store is not None else None
        self.i = -1
        self.until = 0
        self.seen = None
        self.frame = None
        self.empty = None

    def render(self, c, now):
        msgs = self.box.recent(int(self.p["count"] or 10), float(self.p["days"] or 14), now) if self.box else []
        if not msgs:
            if self.empty is None:
                self.empty = Canvas(self.w, self.h)
                self.empty.text_center(self.h // 2 - 8, "No messages", NORMAL)
                self.empty.text_center(self.h // 2 + 2, "yet", NORMAL)
            c.px[:] = self.empty.px
            return
        key = (self.box.version, len(msgs))
        if now >= self.until or key != self.seen:
            if key != self.seen and self.seen is not None:
                self.i = -1  # something new: start again from the newest
            self.seen = key
            self.i = (self.i + 1) % len(msgs)
            self.frame = render_message(msgs[self.i], self.w, self.h, bool(self.p["show_names"]))
            self.until = now + float(self.p["seconds"] or 12)
        c.px[:] = self.frame.px

    def describe(self):
        return self.title
