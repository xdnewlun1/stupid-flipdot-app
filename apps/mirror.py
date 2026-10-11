"""Silhouette mirror: whoever stands in front of the camera, in dots (camera.py does the camera side)."""

from PIL import Image, ImageChops, ImageFilter, ImageOps

import camera
from fonts import SMALL

from .base import App, P, register

BAYER4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
LIFE_EVERY = 0.2    # seconds per Game of Life generation
PIXEL_MARGIN = 14   # pixel mirror: how far past its threshold a dot's gray has to move before it flips


def bayer(w, h, offset=0):
    """A 4×4 ordered-dither threshold image (values spread over 8..248, shifted by offset)."""
    vals = bytes(max(0, min(255, 8 + BAYER4[(y % 4) * 4 + x % 4] * 16 + offset)) for y in range(h) for x in range(w))
    return Image.frombytes("L", (w, h), vals)


_LIFE_LUT = [255 if v in (3, 18, 19) else 0 for v in range(256)]  # count + 16 × alive: born on 3, survives on 2-3
_LIFE_KERNEL = ImageFilter.Kernel((3, 3), [1, 1, 1, 1, 0, 1, 1, 1, 1], scale=1, offset=0)
_TO1 = [0] * 128 + [1] * 128
_TO16 = [0] * 128 + [16] * 128
_NZ = [0] + [255] * 255


@register
class Mirror(App):
    id = "mirror"
    title = "Silhouette mirror"
    category = "Interactive"
    description = ("Whoever stands in front of the camera, in dots. Set up the camera on the Mirror page (Create); "
                   "frames stay in memory on the board and are never stored.")
    params = [
        P("mode", "select", "", "Mode", options=[["", "As on the Mirror page"], ["solid", "Solid silhouette"],
                                                   ["outline", "Outline"], ["trails", "Fading trails"],
                                                   ["life", "Game of Life from your shape"], ["pixel", "Pixel mirror (dithered)"]]),
        P("invert", "select", "", "Colours", options=[["", "As on the Mirror page"], ["no", "Lit silhouette"], ["yes", "Dark silhouette (inverted)"]]),
        P("selfie", "select", "", "Orientation", options=[["", "As on the Mirror page"], ["yes", "Mirrored, like a selfie"], ["no", "As the camera sees it"]]),
        P("trail_s", "number", 0, "Trail length (s, 0 = as on the Mirror page)", min=0, max=10, step=0.5, show_if={"mode": "trails"}),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.cam = camera.get(ctx.store)
        self._acquired = False  # a Pi camera runs only while a mirror is actually drawing
        size = (self.w, self.h)
        self.state = Image.new("L", size, 0)    # what's on the board, before invert (0/255)
        self.age = Image.new("L", size, 0)      # trails: 255 where you are now, fading
        self.gray = None                        # pixel mirror: smoothed gray
        self.levels = None                      # pixel mirror: smoothed black/white points
        self.seen = -1
        self.next_gen = 0.0
        self.bayer = bayer(self.w, self.h)
        self.bayer_hi = bayer(self.w, self.h, PIXEL_MARGIN)
        self.bayer_lo = bayer(self.w, self.h, -PIXEL_MARGIN)

    def setting(self, name):
        c = self.cam.cfg()
        v = self.p.get(name)
        if name in ("invert", "selfie"):
            return c[name] if v in ("", None) else v == "yes"
        if name == "trail_s":
            return float(v) if v else float(c["trail_s"])
        return v or c[name]

    def stop(self):
        if self._acquired:
            self._acquired = False
            self.cam.release()

    def describe(self):
        return f"{self.title} ({self.setting('mode')})"

    def render(self, c, now):
        if not self._acquired:
            self._acquired = True
            self.cam.acquire()
        snap = self.cam.latest(now)
        if snap is None:
            return self.no_camera(c)
        mode = self.setting("mode")
        if snap.seq != self.seen:
            self.seen = snap.seq
            gray, mask, motion = snap.gray, snap.mask, snap.motion_mask
            if self.setting("selfie"):
                gray, mask, motion = ImageOps.mirror(gray), ImageOps.mirror(mask), ImageOps.mirror(motion)
            self.step(mode, gray, mask, motion)
        if mode == "life" and now >= self.next_gen:
            self.next_gen = now + LIFE_EVERY
            self.life_gen()
        c.px[:] = self.state.point(_TO1).tobytes()
        if self.setting("invert"):
            c.invert()

    def step(self, mode, gray, mask, motion):
        if mode == "outline":
            self.state = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
        elif mode == "trails":
            fps = camera.MAX_FPS if self.cam.cfg()["source"] == "browser" else max(1, int(self.cam.cfg()["fps"]))
            decay = max(1, round(255 / (max(0.3, self.setting("trail_s")) * min(fps, 10))))
            self.age = ImageChops.lighter(mask, ImageChops.subtract(self.age, Image.new("L", mask.size, decay)))
            # a fixed dither order: as a trail fades its dots switch off one by one, each once (no flicker)
            self.state = ImageChops.subtract(self.age, self.bayer).point(_NZ)
        elif mode == "life":
            # the moving parts of your outline seed the board; the cells they start live on after you've moved,
            # and standing still lets it settle (a body that keeps seeding would keep every disc busy)
            edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
            moving = motion.filter(ImageFilter.MaxFilter(5))
            self.state = ImageChops.lighter(self.state, ImageChops.darker(edge, moving))
        elif mode == "pixel":
            self.pixel(gray)
        else:
            self.state = mask

    def life_gen(self):
        cells = self.state.point(_TO1)
        counts = cells.filter(_LIFE_KERNEL)
        self.state = ImageChops.add(counts, self.state.point(_TO16)).point(_LIFE_LUT)

    def pixel(self, gray):
        self.gray = gray if self.gray is None else Image.blend(self.gray, gray, 0.5)
        lo, hi = _percentiles(self.gray.histogram(), 0.03, 0.97)
        if self.levels is None:
            self.levels = [lo, hi]
        else:  # black and white points move slowly, so the whole picture doesn't pump with every frame
            self.levels = [self.levels[0] * 0.85 + lo * 0.15, self.levels[1] * 0.85 + hi * 0.15]
        a, b = self.levels
        span = max(24.0, b - a)
        g = self.gray.point([max(0, min(255, int((v - a) * 255 / span))) for v in range(256)])
        on = ImageChops.subtract(g, self.bayer_hi).point(_NZ)
        off = ImageChops.subtract(self.bayer_lo, g).point(_NZ)
        self.state = ImageChops.darker(ImageChops.lighter(self.state, on), ImageChops.invert(off))

    def no_camera(self, c):
        c.clear()
        src = self.cam.cfg()["source"]
        lines = {"off": ["MIRROR", "NO CAMERA SET UP", "CREATE > MIRROR"],
                 "browser": ["MIRROR", "START THE CAMERA", "ON THE MIRROR PAGE"]}.get(
            src, ["MIRROR", "CAMERA NOT READY", (self.cam.error or "STARTING...").upper()[:21]])
        # a simple camera icon above the text
        c.rect(36, 4, 12, 8, fill=False)
        c.rect(39, 2, 5, 2, fill=True)
        c.circle(42, 8, 2)
        for i, line in enumerate(lines):
            c.text_center(16 + i * 8, line, SMALL)


def _percentiles(hist, lo_frac, hi_frac):
    total = sum(hist)
    lo_n, hi_n = total * lo_frac, total * hi_frac
    acc, lo, hi = 0, 0, 255
    for v, n in enumerate(hist):
        acc += n
        if acc <= lo_n:
            lo = v
        if acc < hi_n:
            hi = v + 1
    return lo, min(255, hi)
