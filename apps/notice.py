"""Notification cards: a 1-bit icon beside a message (the full-board card), and the thin banner that shows a
notification over a game without stopping it (runner.py draws it on top of the game's frame)."""

from canvas import wrap_text
from fonts import NORMAL, SMALL

from .base import App, P, register

# 9x9 icons, one string per row
ICONS = {
    "bell": [
        "....#....",
        "...###...",
        "..#####..",
        "..#####..",
        "..#####..",
        ".#######.",
        "#########",
        ".........",
        "....#....",
    ],
    "mail": [
        ".........",
        "#########",
        "##.....##",
        "#.#...#.#",
        "#..#.#..#",
        "#...#...#",
        "#.......#",
        "#########",
        ".........",
    ],
    "door": [
        ".#######.",
        ".#.....#.",
        ".#.....#.",
        ".#.....#.",
        ".#...#.#.",
        ".#.....#.",
        ".#.....#.",
        ".#.....#.",
        "#########",
    ],
    "alert": [
        "....#....",
        "...###...",
        "...#.#...",
        "..##.##..",
        "..##.##..",
        ".###.###.",
        ".#######.",
        "####.####",
        "#########",
    ],
    "timer": [
        "...###...",
        "....#....",
        "..#####..",
        ".#..#..#.",
        "#...#...#",
        "#...##..#",
        "#.......#",
        ".#.....#.",
        "..#####..",
    ],
    "music": [
        "...######",
        "...#....#",
        "...######",
        "...#....#",
        "...#....#",
        ".###..###",
        "####.####",
        "####.####",
        ".##...##.",
    ],
    "weather": [
        "...###...",
        "..#####..",
        ".#######.",
        "#########",
        "#########",
        ".........",
        ".#..#..#.",
        "#..#..#..",
        ".........",
    ],
    "calendar": [
        ".#.....#.",
        "#########",
        "#########",
        "#.......#",
        "#.#.#.#.#",
        "#.......#",
        "#.#.#.#.#",
        "#.......#",
        "#########",
    ],
    "person": [
        "...###...",
        "..#####..",
        "..#####..",
        "...###...",
        ".........",
        ".#######.",
        "#########",
        "#########",
        "#########",
    ],
}
ICON_NAMES = list(ICONS)
ICON_SIZE = 9


def draw_icon(c, name, x, y, scale=1, v=1):
    for gy, row in enumerate(ICONS.get(name) or ()):
        for gx, ch in enumerate(row):
            if ch == "#":
                c.rect(x + gx * scale, y + gy * scale, scale, scale, v=v, fill=True)


@register
class Notice(App):
    """An icon on the left and the message beside it, wrapped; a message too long for that scrolls instead."""
    id = "notice"
    title = "Notice"
    category = "Text"
    description = "A message with a small icon beside it, the way notifications show."
    params = [
        P("text", "textarea", "Someone is at the door"),
        P("title", "text", "", "Title (optional)"),
        P("icon", "select", "bell", options=[[n, n.capitalize()] for n in ICON_NAMES]),
        P("speed", "number", 20, "Scroll speed for long messages (dots/sec)", min=5, max=60),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.icon = self.p["icon"] if self.p["icon"] in ICONS else None
        self.scale = 2 if self.h >= 30 else 1
        isz = ICON_SIZE * self.scale
        self.x0 = isz + 6 if self.icon else 2
        room = self.w - self.x0 - 1
        title, text = str(self.p.get("title") or "").strip(), str(self.p.get("text") or "").strip()
        self.lines = (wrap_text(title, NORMAL, room) if title else []) + wrap_text(text, NORMAL, room)
        self.title_lines = len(wrap_text(title, NORMAL, room)) if title else 0
        self.scroll = len(self.lines) * 9 - 2 > self.h or any(NORMAL.width(l) > room for l in self.lines)
        self.title, self.text = title, text
        if self.scroll:
            self.interval = 1 / max(5, float(self.p.get("speed") or 20))
        else:
            self.interval = 1.0  # still: nothing to redraw

    def render(self, c, now):
        isz = ICON_SIZE * self.scale
        if self.icon:
            draw_icon(c, self.icon, 2, (self.h - isz) // 2, self.scale)
        if not self.scroll:
            pitch = 9
            y = (self.h - (len(self.lines) * pitch - 2)) // 2
            for i, line in enumerate(self.lines):
                c.text(self.x0, y + i * pitch, line, NORMAL)
                if i == self.title_lines - 1 and len(self.lines) > self.title_lines:
                    y += 2  # a little gap under the title
            return
        # long: title (if any) stays put in small type, the message scrolls in large type underneath
        room = self.w - self.x0
        clip = _Clip(c, self.x0, self.w)
        top = 4
        if self.title:
            t = self.title
            while t and SMALL.width(t) > room - 1:
                t = t[:-1]
            c.text(self.x0, top, t, SMALL)
            top += 9
        msg = self.text.replace("\n", "  ")
        marquee(clip, self.x0, room, top + 2 if self.title else (self.h - 14) // 2, msg, now - self.started,
                float(self.p.get("speed") or 20), scale=2)

    def describe(self):
        return f"Notice: {self.title or self.text}"[:80]


class _Clip:
    """Draws text clipped to the columns [x0, x1) of a canvas."""

    def __init__(self, c, x0, x1):
        self.c, self.x0, self.x1 = c, x0, x1

    def text(self, x, y, s, font=NORMAL, scale=1):
        from canvas import Canvas
        w = font.width(s) * scale
        lo, hi = max(self.x0, x), min(self.x1, x + w)
        if hi <= lo:
            return
        # draw only the characters that land in view
        tmp = Canvas(hi - lo, font.height * scale)
        cx = x
        for ch in s:
            gw = (len(font.glyph(ch)[0]) + font.spacing) * scale
            if cx + gw > lo and cx < hi:
                tmp.text(cx - lo, 0, ch, font, scale=scale)
            cx += gw
        self.c.blit(tmp, lo, y)


HOLD_S = 1.5  # scrolling text first stands still this long, so it can be read from its start
GAP = 24


def marquee(clip, x0, room, y, text, t, speed, font=NORMAL, scale=1):
    """Text that starts where it can be read, holds, then scrolls left and comes round again."""
    tw = font.width(text) * scale
    span = tw + GAP * scale
    off = int(max(0.0, t - HOLD_S) * speed) % span
    clip.text(x0 - off, y, text, font, scale)
    if x0 - off + span < x0 + room:
        clip.text(x0 - off + span, y, text, font, scale)


BANNER_H = 11


def draw_banner(c, text, icon=None, now=0.0, started=0.0, position="bottom", speed=20):
    """A strip over the bottom (or top) of the picture: a lit rule, then the icon and the text on black.
    Text wider than the strip scrolls."""
    y0 = c.h - BANNER_H if position != "top" else 0
    rule = y0 if position != "top" else BANNER_H - 1
    body = y0 + 1 if position != "top" else 0
    c.rect(0, y0, c.w, BANNER_H, v=0, fill=True)
    c.rect(0, rule, c.w, 1, fill=True)
    x0 = 1
    if icon in ICONS:
        draw_icon(c, icon, 1, body + 1)
        x0 = ICON_SIZE + 3
    text = " ".join(str(text or "").split())
    room = c.w - x0
    tw = NORMAL.width(text)
    ty = body + 2
    if tw <= room:
        c.text(x0, ty, text, NORMAL)
        return
    marquee(_Clip(c, x0, c.w), x0, room, ty, text, now - started, speed)
