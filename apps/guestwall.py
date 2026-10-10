"""Guest wall: a QR code that opens the /guest page, then approved guest messages as scrolling text."""

import guestwall
import qr
from canvas import Canvas
from fonts import NORMAL, SMALL

from .base import App, P, register
from .wifiqr import fit_qr


def _strip(text, font, scale=1):
    """Pre-rendered text, so scrolling is a cheap row copy each frame."""
    c = Canvas(max(1, font.width(text) * scale), font.height * scale)
    c.text(0, 0, text, font, scale=scale)
    return c


def _paste(c, src, x, y):
    """Copy `src` onto `c` at (x, y), clipped; much cheaper than drawing the text again."""
    x0, x1 = max(0, x), min(c.w, x + src.w)
    if x0 >= x1:
        return
    for sy in range(src.h):
        dy = y + sy
        if 0 <= dy < c.h:
            row = sy * src.w
            c.px[dy * c.w + x0:dy * c.w + x1] = src.px[row + x0 - x:row + x1 - x]


@register
class GuestWallApp(App):
    id = "guest_wall"
    title = "Guest wall"
    category = "Text"
    description = "Guests scan a QR code to post a message from their phone; approved messages scroll across the board."
    params = [
        P("url", "text", "", "Link in the QR code (empty = this board's /guest page)"),
        P("qr_seconds", "number", 12, "Show the QR code for (seconds)", min=3, max=600),
        P("size", "select", "large", options=[["normal", "Normal"], ["large", "Large"]], label="Message size"),
        P("speed", "number", 20, "Scroll speed (dots/sec)", min=4, max=60),
        P("show_names", "checkbox", True, "Show who wrote it"),
        P("max_messages", "number", 20, "Newest messages to show", min=1, max=200),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.wall = guestwall.get(ctx.store)
        self.url = None
        self.qr = None
        self.queue = []          # messages left in this round
        self.phase = None        # ("qr", until) or ("msg", started, strip, name_strip)

    def _qr_canvas(self):
        url = str(self.p["url"] or "").strip() or guestwall.default_url()
        if url == self.url:
            return self.qr
        self.url = url
        c = Canvas(self.w, self.h)
        try:
            code, quiet, inverted = fit_qr(url, self.h)
        except ValueError:
            c.text_center(12, "Link too long", NORMAL)
            c.text_center(22, "for a QR code", NORMAL)
            self.qr = c
            return c
        side = code.size + 2 * quiet
        lines = ["Scan to", "leave a", "message"]
        gap = 5 if inverted else 3
        tw = max(NORMAL.width(l) for l in lines)
        x0 = max(0, (self.w - side - gap - tw) // 2)
        label = side + gap + tw <= self.w
        qr.draw(c, code, x0 if label else (self.w - side) // 2, (self.h - side) // 2, quiet=quiet, invert=inverted)
        if label:
            lh = NORMAL.height + 3
            y = (self.h - lh * len(lines) + 3) // 2
            for i, line in enumerate(lines):
                c.text(x0 + side + gap, y + i * lh, line, NORMAL)
        self.qr = c
        return c

    def _next(self, now):
        if not self.queue:
            msgs = self.wall.approved(int(self.p["max_messages"] or 20))
            self.queue = list(msgs)
            self.phase = ("qr", now + float(self.p["qr_seconds"] or 12))
            return
        m = self.queue.pop(0)
        scale = 2 if self.p["size"] == "large" else 1
        name = _strip(m["name"].upper(), SMALL) if self.p["show_names"] and m.get("name") else None
        self.phase = ("msg", now, _strip(m["text"], NORMAL, scale), name)

    def render(self, c, now):
        if self.phase is None:
            self._next(now)
        if self.phase[0] == "qr":
            c.px[:] = self._qr_canvas().px
            if now >= self.phase[1] and self.queue:
                self._next(now)
            elif now >= self.phase[1]:
                self.phase = None  # no messages yet: keep showing the code, check again
            return
        _, started, strip, name = self.phase
        offset = int((now - started) * float(self.p["speed"] or 20))
        if offset > strip.w + c.w:
            self._next(now)
            return self.render(c, now)
        if name:
            _paste(c, name, (c.w - name.w) // 2, 3)
            y = 3 + name.h + max(0, (c.h - 3 - name.h - strip.h) // 2)
        else:
            y = (c.h - strip.h) // 2
        _paste(c, strip, c.w - offset, y)

    def describe(self):
        return self.title
