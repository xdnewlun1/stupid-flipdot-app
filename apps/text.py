from canvas import wrap_text
from charfx import CHAR_PARAMS, CharFx
from fonts import get_font, is_pixel

from .base import App, P, register

# the old "size" setting, still accepted from saved playlists and the API
SIZES = {"small": ("small", 1), "normal": ("normal", 1), "large": ("normal", 2), "huge": ("normal", 3)}

FONT_PARAMS = [
    P("font", "font", "normal", "Font"),
    P("scale", "number", 2, "Scale (pixel fonts)", min=1, max=6, show_if={"font": "pixel"}),
    P("font_size", "number", 14, "Size in dots (other fonts)", min=5, max=42, show_if={"font": {"not": "pixel"}}),
]
STILL = ["static", "typewriter"]  # styles that lay text out in lines (not the one-line ticker)


def resolve_font(p, raw):
    """(font, scale) from text params; understands the legacy "size" param."""
    if "size" in raw and "font" not in raw and raw["size"] in SIZES:
        fid, scale = SIZES[raw["size"]]
        return get_font(fid), scale
    fid = p.get("font") or "normal"
    if is_pixel(fid):
        return get_font(fid), max(1, int(p.get("scale") or 1))
    return get_font(fid, int(p.get("font_size") or 14)), 1


@register
class Text(App):
    id = "text"
    title = "Text"
    category = "Text"
    description = "Static, scrolling-ticker or typewriter text."
    params = [
        P("text", "textarea", "Hello, world!"),
        P("style", "select", "scroll", options=[["static", "Static"], ["scroll", "Scrolling ticker"], ["typewriter", "Typewriter"]]),
        *FONT_PARAMS,
        P("fit", "checkbox", False, "Auto-size to fill the board", show_if={"style": STILL}),
        P("align", "select", "center", options=[["left", "Left"], ["center", "Center"], ["right", "Right"]], show_if={"style": STILL}),
        P("line_gap", "number", 1, "Space between lines (dots)", min=0, max=10, show_if={"style": STILL}),
        P("speed", "number", 20, "Scroll speed (dots/sec)", min=2, max=60, show_if={"style": "scroll"}),
        P("chars_per_sec", "number", 8, "Typewriter speed (chars/sec)", min=1, max=40, show_if={"style": "typewriter"}),
        P("repeat", "number", 0, "Repeat count (0 = forever)", min=0, max=100, show_if={"style": ["scroll", "typewriter"]}),
        P("invert", "checkbox", False, "Negative (swap lit and dark dots)"),
        # character transitions animate changed letters, which only happens in static text
        *[{**p, "show_if": {**p.get("show_if", {}), "style": "static"}} for p in CHAR_PARAMS],
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.raw = params
        self._fit = None
        self.fx = CharFx(self.p["char_transition"], self.p["char_time"])

    def fitted(self, text, w, h):
        """Largest font size/scale at which the wrapped text fits in w x h."""
        key = (text, w, h)
        if self._fit and self._fit[0] == key:
            return self._fit[1]
        fid = self.p.get("font") or "normal"
        gap = int(self.p.get("line_gap") or 0)
        options = ([(get_font(fid), s) for s in range(8, 0, -1)] if is_pixel(fid)
                   else [(get_font(fid, size), 1) for size in range(min(h, 42), 4, -1)])
        best = options[-1]
        for font, scale in options:
            lines = wrap_text(text, font, w // scale)
            if (len(lines) * (font.height + gap) - gap) * scale <= h and all(font.width(l) * scale <= w for l in lines):
                best = (font, scale)
                break
        self._fit = (key, best)
        return best

    def render(self, c, now):
        text = str(self.p["text"])
        if self.p.get("fit") and self.p["style"] != "scroll":
            font, scale = self.fitted(text, c.w, c.h)
        else:
            font, scale = resolve_font(self.p, self.raw)
        elapsed = now - self.started
        repeat = int(self.p["repeat"] or 0)
        gap = int(self.p.get("line_gap") if self.p.get("line_gap") is not None else 1)
        line_h = (font.height + gap) * scale
        align = self.p.get("align") or "center"

        boxes = []

        def place(y, line, slot):
            tw = font.width(line) * scale
            x = 0 if align == "left" else (c.w - tw if align == "right" else (c.w - tw) // 2)
            c.text(x, y, line, font, scale=scale, boxes=boxes, slot=slot)
        done = None

        if self.p["style"] == "scroll":
            line = "   ".join(text.splitlines())
            tw = font.width(line) * scale
            period = tw + c.w
            offset = int(elapsed * float(self.p["speed"]))
            if repeat and offset >= period * repeat:
                done = False
            x = c.w - offset % period
            c.text(x, (c.h - font.height * scale) // 2, line, font, scale=scale)
        else:
            lines = wrap_text(text, font, c.w // scale)
            max_lines = max(1, (c.h + gap * scale) // line_h)
            if self.p["style"] == "typewriter":
                total = sum(len(l) for l in lines)
                shown = int(elapsed * float(self.p["chars_per_sec"]))
                cycle = total + int(float(self.p["chars_per_sec"]) * 3)  # hold for ~3 s, then restart
                if repeat and shown >= cycle * repeat:
                    done = False
                shown %= cycle
                visible = []
                for l in lines:
                    visible.append(l[:max(0, shown)])
                    shown -= len(l)
                lines = visible
                # keep the line being typed on screen
                last = max((i for i, l in enumerate(lines) if l), default=0)
                first = max(0, last - max_lines + 1)
                lines = lines[first:first + max_lines]
                y = (c.h - min(len(visible), max_lines) * line_h + gap * scale) // 2
                for i, l in enumerate(lines):
                    c.text(0 if align != "right" else c.w - font.width(l) * scale, y + i * line_h, l, font, scale=scale)
            else:
                lines = lines[:max_lines]
                y = (c.h - len(lines) * line_h + gap * scale) // 2
                for i, l in enumerate(lines):
                    place(y + i * line_h, l, i)
        self.fx.apply(c, boxes, now)
        if self.p["invert"]:
            c.invert()
        return done

    def describe(self):
        return f"Text: {str(self.p['text'])[:30]}"
