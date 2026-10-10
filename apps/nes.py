"""NES games (your own .nes ROMs) on the board, emulated by agnes.

The emulator runs as a child process (native/nes, built by scripts/setup-nes.sh) at the NES's 60
frames per second. At the board rate it sends the picture as NES palette indices plus a "layer"
plane (background or sprite, and which palette entry) per pixel, the sprite table and the palette;
it reads button presses and save/load requests on its stdin. This app keeps only the newest frame
and turns it into dots with Pillow: at 84x42 the colours alone don't survive, but "background or
sprite" and "how much does it stand out from the backdrop" do.
"""

import collections
import logging
import os
import re
import struct
import subprocess
import threading
import time
from pathlib import Path

from canvas import wrap_text
from fonts import NORMAL, SMALL

from .base import App, P, register
from .doom import quiet_now

log = logging.getLogger("nes")

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "native" / "nes" / "build"
BINARY = Path(os.environ.get("FLIPDOT_NES_BIN") or BUILD / "nes_flipdot")
SETUP_HINT = "run scripts/setup-nes.sh"

HEADER = struct.Struct("<4sHHIHBBIHH")  # see native/nes/nes_flipdot.c
F_PAUSED, F_SAVED, F_LOADED, F_NOSAVE, F_SAVEFAIL, F_BADSAVE = 1, 2, 4, 8, 16, 32
EVENTS = {F_SAVED: "SAVED", F_LOADED: "LOADED", F_NOSAVE: "NO SAVE YET", F_SAVEFAIL: "SAVE FAILED",
          F_BADSAVE: "SAVE DOESN'T FIT"}

MAPPERS = {0: "NROM", 1: "MMC1", 2: "UxROM", 3: "CNROM", 4: "MMC3", 7: "AxROM"}
MAX_ROM = (1 << 20) + 16 + 512  # 1 MB of game data, the header and a trainer
ROM_DIR = "nes"
SAVE_DIR = "nes/saves"

BUTTONS = {"a": 0, "b": 1, "select": 2, "start": 3, "up": 4, "down": 5, "left": 6, "right": 7}
# spare gamepad buttons (gamepad.py) -> NES actions; other games never see these names
ALIASES = {"z": "select", "x": "select", "y": "save", "l": "load"}

# 2C02 palette (the one agnes uses)
PAL = [(124, 124, 124), (0, 0, 252), (0, 0, 188), (68, 40, 188), (148, 0, 132), (168, 0, 32), (168, 16, 0), (136, 20, 0),
       (80, 48, 0), (0, 120, 0), (0, 104, 0), (0, 88, 0), (0, 64, 88), (0, 0, 0), (0, 0, 0), (0, 0, 0),
       (188, 188, 188), (0, 120, 248), (0, 88, 248), (104, 68, 252), (216, 0, 204), (228, 0, 88), (248, 56, 0), (228, 92, 16),
       (172, 124, 0), (0, 184, 0), (0, 168, 0), (0, 168, 68), (0, 136, 136), (0, 0, 0), (0, 0, 0), (0, 0, 0),
       (248, 248, 248), (60, 188, 252), (104, 136, 252), (152, 120, 248), (248, 120, 248), (248, 88, 152), (248, 120, 88), (252, 160, 68),
       (248, 184, 0), (184, 248, 24), (88, 216, 84), (88, 248, 152), (0, 232, 216), (120, 120, 120), (0, 0, 0), (0, 0, 0),
       (252, 252, 252), (164, 228, 252), (184, 184, 248), (216, 184, 248), (248, 184, 248), (248, 164, 192), (240, 208, 176), (252, 224, 168),
       (248, 216, 120), (216, 248, 120), (184, 248, 184), (184, 248, 216), (0, 252, 252), (248, 216, 248), (0, 0, 0), (0, 0, 0)]
LUMA = [int(0.299 * r + 0.587 * g + 0.114 * b) for r, g, b in PAL] + [0] * 192


def _dist(a, b):  # perceptual-ish distance between two NES colours, 0..255
    (r1, g1, b1), (r2, g2, b2) = PAL[a], PAL[b]
    rm = (r1 + r2) / 2
    d = ((2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2) ** 0.5
    return min(255, int(d / 3))


DIST = [[_dist(a, b) for b in range(64)] for a in range(64)]
BAYER4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
SPRITE = [255 if v & 0x80 else 0 for v in range(256)]                      # sprite in front
BG = [255 if v & 0x20 and not v & 0x80 else 0 for v in range(256)]         # opaque background showing
NONZERO = [1 if v else 0 for v in range(256)]
FULL = (0, 8, 256, 232)  # the 256x224 a TV shows


# ---- ROM files -------------------------------------------------------------------------

def parse_ines(data):
    """Check an iNES file; returns {"mapper", "prg_kb", "chr_kb"} or raises ValueError."""
    if len(data) < 16 or data[:4] != b"NES\x1a":
        raise ValueError("that isn't a NES ROM (.nes files start with an iNES header)")
    if len(data) > MAX_ROM:
        raise ValueError(f"that ROM is {len(data) // 1024} KB; the limit is 1 MB")
    prg, chr_ = data[4], data[5]
    nes2 = data[7] & 0x0c == 0x08
    if nes2 and (data[9] & 0x0f or data[9] & 0xf0):
        raise ValueError("that ROM is bigger than this emulator handles")
    trainer = 512 if data[6] & 4 else 0
    need = 16 + trainer + prg * 16384 + chr_ * 8192
    if prg == 0 or len(data) < need:
        raise ValueError("that ROM file is cut short (smaller than its header says)")
    mapper = (data[6] >> 4) | (data[7] & 0xf0)  # as the emulator reads it
    return {"mapper": mapper, "prg_kb": prg * 16, "chr_kb": chr_ * 8}


def mapper_problem(mapper):
    if mapper in MAPPERS:
        return None
    names = ", ".join(f"{n} {m}" for n, m in MAPPERS.items())
    return f"This game uses mapper {mapper}, which the emulator doesn't support. It runs mappers {names}."


def rom_dir(store):
    return Path(store.dir) / ROM_DIR


def safe_name(name):
    stem = re.sub(r"[^A-Za-z0-9 _.()'&!-]", "", Path(str(name)).stem).strip(" .")[:80]
    return (stem or "game") + ".nes"


def list_roms(store):
    out = []
    d = rom_dir(store)
    if d.is_dir():
        for f in sorted(d.iterdir(), key=lambda f: f.name.lower()):
            if f.is_file() and f.suffix.lower() == ".nes":
                try:
                    info = parse_ines(f.read_bytes())
                except (OSError, ValueError) as e:
                    info = {"mapper": None, "problem": str(e)}
                info.setdefault("problem", mapper_problem(info["mapper"]))
                saved = (Path(store.dir) / SAVE_DIR / (f.stem + ".state"))
                out.append({"name": f.name, "title": f.stem, "size": f.stat().st_size, "saved": saved.is_file(),
                            "mapper_name": MAPPERS.get(info["mapper"]), **info})
    return out


def built():
    return BINARY.is_file() and os.access(BINARY, os.X_OK)


# ---- frames -> dots -----------------------------------------------------------------

class Camera:
    """A (rw, rh) window that follows the player: the sprite cluster nearest to the last target,
    re-locking on the one nearest the screen centre if it's gone for 2 s (HUD sprites sit in the
    corners). A dead zone and easing keep it steady."""

    def __init__(self, rw, rh, dead=(16, 12)):
        self.rw, self.rh, self.dead = rw, rh, dead
        self.cx = self.tx = 128
        self.cy = self.ty = 120
        self.lost = 0

    @staticmethod
    def clusters(oam):
        groups = []
        for i in range(64):
            y = oam[i * 4]
            if y >= 0xEF:
                continue
            p = (oam[i * 4 + 3], y + 1)
            for g in groups:
                if any(abs(p[0] - q[0]) <= 9 and abs(p[1] - q[1]) <= 17 for q in g):
                    g.append(p)
                    break
            else:
                groups.append([p])
        return [(sum(x for x, _ in g) / len(g) + 4, sum(y for _, y in g) / len(g) + 4, len(g)) for g in groups if len(g) >= 2]

    def __call__(self, oam, steps=1):
        cs = self.clusters(oam)
        near = [c for c in cs if (c[0] - self.tx) ** 2 + (c[1] - self.ty) ** 2 < 48 ** 2]
        if near:
            c = min(near, key=lambda c: (c[0] - self.tx) ** 2 + (c[1] - self.ty) ** 2)
            self.tx, self.ty, self.lost = c[0], c[1], 0
        else:
            self.lost += steps
            if self.lost > 20 and cs:
                c = min(cs, key=lambda c: (c[0] - 128) ** 2 + (c[1] - 120) ** 2)
                self.tx, self.ty, self.lost = c[0], c[1], 0
        for attr, t, d in (("cx", self.tx, self.dead[0]), ("cy", self.ty, self.dead[1])):
            cur = getattr(self, attr)
            if abs(t - cur) > d:
                cur += (t - cur - (d if t > cur else -d)) * 0.5
            setattr(self, attr, cur)
        x0 = int(min(max(self.cx - self.rw / 2, 0), 256 - self.rw))
        y0 = int(min(max(self.cy - self.rh / 2, FULL[1]), FULL[3] - self.rh))
        return (x0, y0, x0 + self.rw, y0 + self.rh)


class Renderer:
    """Turns one emulator frame into board dots (0/1 bytes). Pillow only: ~1 ms on a PC."""

    def __init__(self, w, h, look="outline", gain=1.6, threshold=24):
        from PIL import Image
        self.w, self.h, self.look = w, h, look
        self.gain, self.threshold = gain, threshold
        self.bayer = Image.new("L", (w, h))
        self.bayer.putdata([BAYER4[(y % 4) * 4 + x % 4] * 16 + 8 for y in range(h) for x in range(w)])
        self.solid_lut = [255 if v > threshold else 0 for v in range(256)]
        self.fill_lut = [min(255, int(v * gain)) for v in range(256)]
        self.inner_lut = [min(255, int(v * gain * 0.5)) for v in range(256)]
        self.spr_lut = [255 if v > 40 else 0 for v in range(256)]

    def _dither(self, grey):
        from PIL import ImageChops
        return ImageChops.subtract(grey, self.bayer)

    def __call__(self, color, layer, roi):
        from PIL import Image, ImageChops, ImageFilter, ImageOps
        size = (self.w, self.h)
        hist = color.histogram()[:64]
        back = max(range(64), key=hist.__getitem__)  # the backdrop: usually the sky, or a solid playfield
        if self.look == "plain":  # brightness, turned over when the backdrop is light so it stays dark
            grey = color.point(LUMA if LUMA[back] < 128 else [255 - v for v in LUMA]).resize(size, Image.BOX, box=roi)
            return self._dither(grey).point(NONZERO).tobytes()
        # how much each background pixel stands out from the backdrop
        lut = DIST[back] + [0] * 192
        bgmask = layer.point(BG)
        bv = ImageChops.multiply(color.point(lut), bgmask).resize(size, Image.BOX, box=roi)
        if self.look == "fill":
            out = self._dither(bv.point(self.fill_lut))
        else:  # outline: terrain (strong contrast) gets an edge and a light dither inside
            solid = bv.point(self.solid_lut)
            inner = ImageOps.expand(solid, 1, 0).filter(ImageFilter.MinFilter(3)).crop((1, 1, self.w + 1, self.h + 1))
            edge = ImageChops.subtract(solid, inner)
            fill = ImageChops.multiply(self._dither(bv.point(self.inner_lut)).point(SPRITE_ANY), inner)
            out = ImageChops.lighter(edge, fill)
        # sprites solid, with a dark ring so they stand out from whatever is behind them
        spr = layer.point(SPRITE).resize(size, Image.BOX, box=roi).point(self.spr_lut)
        ring = ImageChops.subtract(spr.filter(ImageFilter.MaxFilter(3)), spr)
        out = ImageChops.lighter(ImageChops.subtract(out, ring), spr)
        return out.point(NONZERO).tobytes()


SPRITE_ANY = [255 if v else 0 for v in range(256)]


# ---- the app -----------------------------------------------------------------------

_active = None  # the one NES app whose emulator is running
_active_lock = threading.Lock()


@register
class NES(App):
    id = "nes"
    title = "NES"
    category = "Games"
    interactive = True
    description = ("Your own NES games (.nes files you upload), emulated on the board. D-pad, A, B and Start as on "
                   "the NES; Select is Z or X on a gamepad. Save and load from /play. Needs scripts/setup-nes.sh. "
                   "Scrolling games flip a lot of dots.")
    params = [
        P("rom", "nesrom", "", "Game"),
        P("look", "select", "outline", "Look", options=[
            ["outline", "Layered outline: terrain outlined, characters solid (easiest to read)"],
            ["fill", "Filled: background shaded by contrast, characters solid"],
            ["plain", "Plain dither of the picture (busiest, most flips)"]]),
        P("camera", "select", "auto", "Camera", options=[
            ["auto", "Auto: follow the player in games that scroll"],
            ["follow", "Follow the player (3x closer)"],
            ["off", "Whole screen"]]),
        P("gain", "number", 1.6, "Shading strength", min=0.5, max=3, step=0.1, show_if={"look": {"not": "plain"}}),
        P("threshold", "number", 24, "Outline threshold (lower = more outlines)", min=4, max=120, step=4,
          show_if={"look": "outline"}),
        P("fps", "number", 10, "Board updates per second", min=2, max=20, step=1),
        P("quiet", "select", "refuse", "During quiet hours", options=[
            ["refuse", "Don't run"], ["slow", "Run at 3 updates per second"]]),
    ]

    @classmethod
    def needs_setup(cls, store, params):
        if not built():
            return f"the NES emulator ({SETUP_HINT} on the server)"
        if not list_roms(store):
            return "a game: upload your own .nes file in Apps, NES"
        return None

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        num = lambda k, lo, hi, d: max(lo, min(hi, float(self.p[k] if self.p[k] not in (None, "") else d)))
        self.fps = int(num("fps", 2, 20, 10))
        self.interval = 1 / self.fps
        self.render_dots = Renderer(self.w, self.h, self.p["look"] if self.p["look"] in ("outline", "fill", "plain") else "outline",
                                    num("gain", 0.5, 3, 1.6), num("threshold", 4, 120, 24))
        rw, rh = min(256, self.w * 3), min(FULL[3] - FULL[1], self.h * 3)
        self.camera = Camera(rw, rh)
        self.rom = None
        self.proc = None
        self.frame = None  # (header tuple, payload bytes)
        self.frame_lock = threading.Lock()
        self.in_lock = threading.Lock()
        self.stderr = collections.deque(maxlen=8)
        self.error = None
        self.quiet_msg = None
        self.started_once = False
        self.stopped = False
        self.last_draw = 0
        self.last_px = None
        self.last_quiet_check = 0
        self.last_frame_no = 0
        self.scroll = None
        self.scrolls = collections.deque()  # when the scroll position changed (last 30 s)
        self.note = None  # (text, until)
        self.paused = False
        self.ms = 0.0     # Python time per board frame (for describe/logs)

    # ---- emulator process -------------------------------------------------------------

    def _pick_rom(self):
        roms = list_roms(self.ctx.store)
        want = str(self.p.get("rom") or "")
        rom = next((r for r in roms if r["name"] == want), None)
        if rom is None and not want:
            rom = roms[0] if roms else None
        return roms, rom

    def _start(self):
        global _active
        self.error = None
        if not built():
            self.error = f"The NES emulator isn't built yet: {SETUP_HINT}"
            return
        roms, rom = self._pick_rom()
        if not roms:
            self.error = "Upload a ROM: Apps, NES, NES ROMs. Bring your own .nes files."
            return
        if rom is None:
            self.error = f"{self.p['rom']} isn't uploaded any more. Pick another game."
            return
        if rom.get("problem"):
            self.error = rom["problem"]
            return
        self.rom = rom
        with _active_lock:
            old, _active = _active, self
        if old is not None and old is not self:
            old.stop()  # one emulator at a time (e.g. the outgoing app during a transition)
        saves = Path(self.ctx.store.dir) / SAVE_DIR
        saves.mkdir(parents=True, exist_ok=True)
        path = rom_dir(self.ctx.store) / rom["name"]
        args = [str(BINARY), str(path), "-fps", str(self.fps), "-state", str(saves / (path.stem + ".state"))]
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except OSError as e:
            if e.errno == 8:  # ENOEXEC: built on another machine (e.g. copied from an x86 PC)
                self.error = "The NES build is for a different CPU. Run scripts/setup-nes.sh again on this machine."
            else:
                self.error = f"Couldn't start the emulator: {e}"
            return
        self.started_once = True
        self.frame = self.last_px = None
        self.paused = False
        log.info("started NES (pid %s): %s", self.proc.pid, rom["name"])
        threading.Thread(target=self._read_frames, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True).start()

    def _read_frames(self, proc):
        out = proc.stdout

        def read(n):  # the pipe is unbuffered: reads can come back short
            buf = bytearray()
            while len(buf) < n:
                chunk = out.read(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        try:
            while True:
                head = read(HEADER.size)
                if head is None:
                    break
                h = HEADER.unpack(head)
                if h[0] != b"NES1":
                    raise ValueError("bad frame header")
                buf = read(h[1] * h[2] * 2 + 256 + 32)
                if buf is None:
                    break
                if h[5] & (F_SAVED | F_LOADED | F_NOSAVE | F_SAVEFAIL | F_BADSAVE):
                    self._event(h[5])
                with self.frame_lock:
                    self.frame = (h, bytes(buf))
        except Exception as e:
            log.warning("nes: %s", e)
        rc = proc.wait()
        if proc is self.proc and not self.stopped:
            last = next((l for l in reversed(self.stderr) if l.strip()), "")
            if rc == 3:
                self.error = mapper_problem(int(re.sub(r"\D", "", last) or -1)) or last
            else:
                self.error = f"The emulator quit (exit {rc}). {last}".strip()
            log.warning("nes exited with %s: %s", rc, list(self.stderr))

    def _event(self, flags):
        text = next(t for f, t in EVENTS.items() if flags & f)
        self.note = (text, time.time() + 1.5)
        if flags & F_SAVED:
            self.ctx.store.sync(SAVE_DIR)

    def _read_stderr(self, proc):
        for line in proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())

    def _kill(self):
        global _active
        proc, self.proc = self.proc, None
        with _active_lock:
            if _active is self:
                _active = None
        if proc is None:
            return
        try:
            proc.stdin.close()  # the emulator quits on EOF
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        log.info("stopped NES (pid %s)", proc.pid)

    def stop(self):
        self.stopped = True
        self._kill()

    # ---- input ---------------------------------------------------------------------

    def _send(self, cmd, player=1, button=0):
        proc = self.proc
        if proc is None:
            return
        with self.in_lock:
            try:
                proc.stdin.write(bytes((ord(cmd), 2 if player == 2 else 1, button)))
            except (OSError, ValueError):
                pass

    def input(self, key, action="down", player=1):
        down = action == "down"
        key = ALIASES.get(key, key)
        if self.proc is None or self.proc.poll() is not None:
            if down and key in ("a", "b") and not self.stopped and self.started_once:
                self.quiet_msg = None
                self.last_quiet_check = 0
                if not self._refused():
                    self._start()
            return
        if key in BUTTONS:
            self._send("D" if down else "U", player, BUTTONS[key])
        elif down and key in ("save", "load", "pause"):
            if key == "pause":
                self.paused = not self.paused
            self._send({"save": "S", "load": "L", "pause": "P"}[key])

    # ---- drawing -------------------------------------------------------------------

    def _refused(self):
        """Quiet hours: returns True when the NES must not run now (and sets the message)."""
        now = time.time()
        if now - self.last_quiet_check < 1:
            return self.quiet_msg is not None
        self.last_quiet_check = now
        quiet, until = quiet_now(self.ctx.store)
        if quiet and self.p["quiet"] != "slow":
            self.quiet_msg = until
            return True
        self.quiet_msg = None
        self.interval = 1 / (min(self.fps, 3) if quiet else self.fps)
        return False

    def _message(self, c, title, text):
        c.text_center(2, title, NORMAL)
        for i, line in enumerate(wrap_text(text.upper(), SMALL, c.w - 2)[:5]):
            c.text_center(12 + i * 6, line, SMALL)

    def render(self, c, now):
        try:
            return self._render(c, now)
        except Exception:
            # never return False/raise: the runner would drop the app without stopping the emulator
            log.exception("nes render failed")

    def _render(self, c, now):
        if self._refused():
            if self.proc is not None:
                self._kill()
            self._message(c, "QUIET HOURS", f"NES games are off until {self.quiet_msg}")
            return
        if self.proc is None and not self.started_once and not self.stopped:
            self._start()
        if self.error and (self.proc is None or self.proc.poll() is not None):
            self._message(c, "NES", self.error + (" Press A to retry." if self.started_once else ""))
            return
        with self.frame_lock:
            frame = self.frame
        if frame is None:
            self._message(c, "NES", "Loading...")
            return
        if self.last_px is not None and now - self.last_draw < self.interval * 0.8:
            c.px[:] = self.last_px  # woken early (input): keep the board update rate capped
        else:
            self.last_draw = now
            t = time.perf_counter()
            c.px[:] = self._dots(frame, now)
            self.ms = self.ms * 0.9 + (time.perf_counter() - t) * 100
            self.last_px = bytes(c.px)
        if self.note and now < self.note[1]:
            w = SMALL.width(self.note[0])
            c.rect((c.w - w) // 2 - 2, 0, w + 3, 7, v=0, fill=True)
            c.text_center(1, self.note[0], SMALL)

    def _dots(self, frame, now):
        from PIL import Image
        h, buf = frame
        fw, fh, n = h[1], h[2], h[1] * h[2]
        color = Image.frombuffer("L", (fw, fh), buf[:n], "raw", "L", 0, 1)
        layer = Image.frombuffer("L", (fw, fh), buf[n:2 * n], "raw", "L", 0, 1)
        oam = buf[2 * n:2 * n + 256]
        steps = max(1, round((h[3] - self.last_frame_no) / 6)) if self.last_frame_no else 1  # camera in 10/s steps
        self.last_frame_no = h[3]
        if self.scroll is not None and h[4] != self.scroll:
            self.scrolls.append(now)
        self.scroll = h[4]
        while self.scrolls and now - self.scrolls[0] > 30:
            self.scrolls.popleft()
        mode = self.p["camera"]
        roi = self.camera(oam, steps) if mode != "off" else FULL
        # auto: follow only once the screen has scrolled for about 4 s of the last 30 (a short slide in a
        # cut scene doesn't count); menus and one-screen games keep the whole picture
        if mode == "auto" and len(self.scrolls) < 4 * self.fps:
            roi = FULL
        return self.render_dots(color, layer, roi)

    def describe(self):
        if self.quiet_msg:
            return f"NES (off during quiet hours, until {self.quiet_msg})"
        if self.error:
            return f"NES: {self.error}"
        if self.rom is None:
            return "NES"
        return f"NES: {self.rom['title']}" + (" (paused)" if self.paused else "")
