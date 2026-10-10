import io

from canvas import Canvas

from .base import App, P, register

MIN_DELAY = 0.05  # the discs can't physically flip much faster than this


@register
class Scene(App):
    id = "scene"
    title = "Saved picture"
    category = "Media"
    description = "Show a saved drawing, image or animation."
    pausable = True
    params = [
        P("scene", "scene", "", "Picture"),
        P("speed", "number", 1, "Animation speed", min=0.1, max=5, step=0.1),
        P("loop", "checkbox", True, "Loop animation"),
    ]

    def __init__(self, ctx, frames=None, name=None, **params):
        super().__init__(ctx, **params)
        if frames is None:
            scene = ctx.store.get_scene(self.p["scene"])
            if scene is None:
                raise ValueError(f"scene {self.p['scene']!r} not found")
            frames, name = scene["frames"], scene["name"]
        self.frames = [(Canvas.from_rows(f["rows"]), float(f.get("delay", 100)) / 1000) for f in frames]
        self.name = name or "animation"
        self.index = -1
        self.next_at = 0

    def render(self, c, now):
        if now >= self.next_at:
            self.index += 1
            if self.index >= len(self.frames):
                if not self.p["loop"] or len(self.frames) == 1:
                    self.index = len(self.frames) - 1
                    if len(self.frames) > 1:
                        c.blit(self.frames[self.index][0], 0, 0)
                        return False
                else:
                    self.index = 0
            delay = self.frames[self.index][1] / max(float(self.p["speed"]), 0.01)
            self.next_at = now + max(delay, MIN_DELAY)
        self.interval = 1.0 if len(self.frames) == 1 else max(self.next_at - now, 0.01)
        c.blit(self.frames[self.index][0], 0, 0)

    def describe(self):
        n = len(self.frames)
        return f"{self.name}" + (f" (frame {max(self.index, 0) + 1}/{n})" if n > 1 else "")


@register
class Drawing(App):
    id = "drawing"
    title = "Drawing"
    category = "Media"
    description = "A drawing layer from Pictures that you can keep editing in Draw while it's on the board."
    params = [P("drawing", "drawing", "", "Drawing")]
    interval = 0.05  # pick up edits quickly

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        if self.p["drawing"] not in ctx.store.drawings:
            raise ValueError(f"drawing {self.p['drawing']!r} not found")
        self.version = None
        self.canvas = None

    def render(self, c, now):
        d = self.ctx.store.drawings.get(self.p["drawing"])
        if d is None:
            return
        if d["version"] != self.version or self.canvas is None:
            self.canvas = Canvas(d["w"], d["h"])
            self.canvas.px = bytearray(d["px"])
            self.version = d["version"]
        if (c.w, c.h) == (self.canvas.w, self.canvas.h):
            c.px[:] = self.canvas.px
        else:
            c.blit(self.canvas, 0, 0, transparent=False)

    def describe(self):
        d = self.ctx.store.drawings.get(self.p["drawing"])
        return f"Drawing: {d['name']}" if d else "Drawing"


@register
class Blank(App):
    id = "blank"
    title = "Solid color"
    category = "Utilities"
    description = "All black (e.g. overnight) or all white."
    params = [P("color", "select", "black", options=[["black", "Black"], ["white", "White"]])]
    interval = 1.0

    def render(self, c, now):
        c.clear(self.p["color"] == "white")

    def describe(self):
        return f"All {self.p['color']}"


@register
class Exerciser(App):
    id = "exerciser"
    title = "Disc exerciser"
    category = "Utilities"
    description = "Flips every disc back and forth to free stuck discs. Ends all black."
    params = [
        P("cycles", "number", 20, min=1, max=500),
        P("pattern", "select", "full", options=[["full", "All on / all off"], ["checker", "Checkerboard"], ["stripes", "Stripes"]]),
        P("delay", "number", 0.4, "Seconds per flip", min=0.1, max=5, step=0.1),
    ]

    def render(self, c, now):
        self.interval = float(self.p["delay"])
        step = int((now - self.started) / self.interval)
        if step >= int(self.p["cycles"]) * 2:
            return False  # leave the board black
        phase = step % 2
        for y in range(c.h):
            for x in range(c.w):
                if self.p["pattern"] == "checker":
                    v = (x + y + phase) % 2
                elif self.p["pattern"] == "stripes":
                    v = (x // 2 + phase) % 2
                else:
                    v = 1 - phase
                c.set(x, y, v)


def convert_image(data, width, height, mode="crop", dither=False, threshold=128, invert=False, max_frames=500):
    """Decode image bytes (any Pillow format, animated GIFs included) into board frames."""
    from PIL import Image, ImageOps, ImageSequence

    img = Image.open(io.BytesIO(data))
    if img.format == "JPEG":
        img.draft("RGB", (width * 4, height * 4))  # decode big photos at reduced size: much faster
    # Art made for the board (exactly its size, or an exact multiple like 168x84) is used dot for
    # dot: no resampling, no dithering. Sizing/dither settings only apply to other images.
    iw, ih = img.size
    factor = iw // width if iw % width == 0 and ih % height == 0 and iw // width == ih // height else 0
    native = factor >= 1
    frames = []
    for frame in ImageSequence.Iterator(img):
        if len(frames) >= max_frames:
            break
        delay = frame.info.get("duration") or img.info.get("duration") or 100
        rgba = frame.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        gray = Image.alpha_composite(bg, rgba).convert("L")
        if native:
            if factor > 1:
                gray = gray.reduce(factor)  # average each factor x factor block
        elif mode == "stretch":
            gray = gray.resize((width, height), Image.LANCZOS)
        elif mode == "fit":
            gray = ImageOps.pad(gray, (width, height), Image.LANCZOS, color=0)
        else:
            gray = ImageOps.fit(gray, (width, height), Image.LANCZOS)
        if dither and not native:
            bw = gray.convert("1")  # Floyd–Steinberg
        else:
            cut = 128 if native else threshold
            bw = gray.point(lambda v: 255 if v >= cut else 0).convert("1", dither=Image.NONE)
        px = bw.load()
        rows = ["".join("1" if (px[x, y] > 0) != invert else "0" for x in range(width)) for y in range(height)]
        frames.append({"rows": rows, "delay": delay if delay > 10 else 100})
    return frames
