"""The real Doom (doomgeneric + Freedoom or the shareware WAD) on the board.

The engine runs as a child process (native/doom, built by scripts/setup-doom.sh). For the outline and
wireframe looks it turns its own geometry into the board's dots (native/doom/geodots.h) and writes those,
plus the player's health/ammo, to its stdout; menus, the automap and the shaded look come as small 8-bit
gray frames this app turns into dots with Pillow. Key events go the other way on its stdin. Only the newest
frame is kept.
"""

import collections
import logging
import os
import struct
import subprocess
import threading
import time
from pathlib import Path

from canvas import wrap_text
from fonts import NORMAL, SMALL

from .base import App, P, register

log = logging.getLogger("doom")

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "native" / "doom" / "build"
BINARY = Path(os.environ.get("FLIPDOT_DOOM_BIN") or BUILD / "doomgeneric_flipdot")
SETUP_HINT = "run scripts/setup-doom.sh"

HEADER = struct.Struct("<4sHHhhhBBHHI")  # see native/doom/doomgeneric_flipdot.c
# planes after the header: FDM1 gray; FDM2 gray + geometry (an older build); FDM3 the board's dots, made by the engine
PLANES = {b"FDM1": 1, b"FDM2": 2, b"FDM3": 1}
STRIP = 6  # rows at the bottom for health/ammo

# Doom key codes (doomkeys.h)
KEYS = {
    "up": 0xAD, "down": 0xAF, "left": 0xAC, "right": 0xAE, "a": 0xA3, "b": 0xA2,
    "strafe_left": 0xA0, "strafe_right": 0xA1, "weapon": 0xC9, "map": 0x09, "pause": 0xFF,
}
SHIFT = 0xB6  # held = run
# spare gamepad buttons (gamepad.py) -> Doom actions; other games never see these names
ALIASES = {"l": "strafe_left", "r": "strafe_right", "x": "weapon", "y": "run", "z": "b", "start": "pause"}

BAYER4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]

# geometry plane (see native/doom/doomgeneric_flipdot.c): surface type << 5 | distance 0..31
G_NONE, G_WALL, G_FLOOR, G_CEIL, G_SKY, G_THING, G_WEAPON = range(7)
FLAT = (G_NONE, G_FLOOR, G_CEIL, G_SKY)


def draw_geometry(geo, w, h, look="outline"):
    """Turn a board-sized geometry plane (w*h bytes) into dots: walls dithered by distance (near =
    dense, far = sparse) or left empty ("wireframe"), floors/ceilings/sky dark, an outline wherever
    surfaces meet or one wall stands in front of another, monsters/items solid with a dark ring
    and the weapon as an outline. Pure Python, ~3k pixels: a few ms on a Pi, so the engine normally does this
    itself (native/doom/geodots.h, the same algorithm); this is the fallback for a build without it, and the
    reference tests/test_doom_geo.py checks the C version against."""
    n = w * h
    typ = [v >> 5 for v in geo]
    dep = [v & 31 for v in geo]
    out = bytearray(n)
    if look != "wireframe":
        for i in range(n):
            if typ[i] == G_WALL:
                y, x = divmod(i, w)
                out[i] = 1 if 236 - dep[i] * 7 > BAYER4[(y & 3) * 4 + (x & 3)] * 16 + 8 else 0
    lit, dark = set(), set()
    for i in range(n):
        t, d = typ[i], dep[i]
        x = i % w
        for j in ((i + 1,) if x < w - 1 else ()) + ((i + w,) if i + w < n else ()):
            u, e = typ[j], dep[j]
            if t == u and (t != G_WALL or abs(d - e) < 3):
                continue
            if G_THING in (t, u):  # dark ring around monsters/items so they stand out
                if t != G_THING:
                    dark.add(i)
                if u != G_THING:
                    dark.add(j)
            elif G_WEAPON in (t, u):
                lit.add(i if t == G_WEAPON else j)
            elif t == u:  # one wall in front of another: light the near edge, gap behind it
                near, far = (i, j) if d < e else (j, i)
                lit.add(near)
                dark.add(far)
            elif t == G_WALL or u == G_WALL:  # where a wall meets floor/ceiling/sky
                lit.add(i if t == G_WALL else j)
            elif t != G_NONE and u != G_NONE:  # floor meeting ceiling (a closing door), steps…
                lit.add(i if d <= e else j)
    for i in range(n):
        t = typ[i]
        if t == G_THING:
            out[i] = 1
        elif t == G_WEAPON:
            out[i] = 0
    for i in dark:
        out[i] = 0
    for i in lit:
        if i not in dark or typ[i] == G_WEAPON:
            out[i] = 1
    return out

_active = None  # the one Doom app whose engine is running
_active_lock = threading.Lock()


def find_wad(data_dir, name):
    d = Path(data_dir) / "doom"
    if d.is_dir():
        for f in d.iterdir():
            if f.name.lower() == name.lower() and f.is_file():
                return f
    return None


def quiet_now(store):
    from boardcare import quiet_active
    import datetime as dt
    cfg = store.settings.get("quiet_hours") or {}
    return quiet_active(cfg, dt.datetime.now()), cfg.get("end") or "07:00"


@register
class Doom(App):
    id = "doom"
    title = "Doom"
    category = "Games"
    interactive = True
    description = ("The real Doom, shrunk to the board. Up/down walk, left/right turn, A fires, "
                   "B opens doors (and respawns). Needs scripts/setup-doom.sh. Noisy: it flips a lot of dots.")
    params = [
        P("wad", "select", "freedoom1.wad", "Game data", options=[
            ["freedoom1.wad", "Freedoom: Phase 1 (free)"], ["doom1.wad", "DOOM shareware (doom1.wad)"]]),
        P("level", "number", 1, "Level (episode 1, map 1-9)", min=1, max=9, step=1),
        P("skill", "select", "3", "Skill", options=[
            ["1", "I'm too young to die"], ["2", "Hey, not too rough"], ["3", "Hurt me plenty"],
            ["4", "Ultra-violence"], ["5", "Nightmare!"]]),
        P("fps", "number", 10, "Board updates per second", min=2, max=20, step=1),
        P("look", "select", "outline", "Look", options=[
            ["outline", "Outlines + walls shaded by distance (easiest to read)"],
            ["wireframe", "Wireframe (edges and monsters only, fewest flips)"],
            ["shaded", "Shaded picture (the textures, dithered)"]]),
        P("dither", "select", "bayer", "Shaded picture: dithering", options=[
            ["bayer", "Ordered (steady, fewer flips)"], ["threshold", "Threshold (outlines only)"],
            ["diffusion", "Error diffusion (detailed, flickers)"]]),
        P("contrast", "select", "auto", options=[
            ["auto", "Stretch each frame"], ["equalize", "Equalize (more detail, busier)"], ["off", "Off"]]),
        P("gamma", "number", 1.0, "Gamma (higher = brighter shadows)", min=0.5, max=4, step=0.1),
        P("brightness", "number", 0, "Brightness", min=-100, max=100, step=5),
        P("hud", "checkbox", True, "Show health and ammo at the bottom"),
        P("quiet", "select", "refuse", "During quiet hours", options=[
            ["refuse", "Don't run"], ["slow", "Run at 3 updates per second"]]),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.fps = max(2, min(20, int(float(self.p["fps"] or 10))))
        self.interval = 1 / self.fps
        self.proc = None
        self.frame = None  # (header tuple, gray bytes)
        self.frame_lock = threading.Lock()
        self.stderr = collections.deque(maxlen=8)
        self.error = None
        self.quiet_msg = None
        self.running = False  # run toggle
        self.started_once = False
        self.stopped = False
        self.last_draw = 0
        self.last_px = None
        self.drawn = None  # the frame last drawn
        self.last_quiet_check = 0
        self.in_lock = threading.Lock()
        self._lut = self._make_lut()
        self._bayer = {}

    # ---- engine process -----------------------------------------------------------

    def _start(self):
        global _active
        self.error = None
        wad = find_wad(self.ctx.store.dir, self.p["wad"])
        if not BINARY.is_file() or not os.access(BINARY, os.X_OK):
            self.error = f"Doom isn't built yet: {SETUP_HINT}"
            return
        if wad is None:
            self.error = f"No {self.p['wad']} in data/doom: {SETUP_HINT}" + (
                " --shareware" if self.p["wad"] == "doom1.wad" else "")
            return
        with _active_lock:
            old, _active = _active, self
        if old is not None and old is not self:
            old.stop()  # one engine at a time (e.g. the outgoing app during a transition)
        self.ctx.store.sync("doom")
        level = max(1, min(9, int(float(self.p["level"] or 1))))
        args = [str(BINARY), "-iwad", str(wad), "-warp", "1", str(level), "-skill", str(self.p["skill"]),
                "-flipfps", str(self.fps), "-nogui"]  # -nogui: errors go to stderr, no zenity pop-up
        if self.p["look"] != "shaded":
            # the engine makes the outline/wireframe dots itself (FDM3 frames, native/doom/geodots.h): no per-dot
            # Python pass per frame, and a tenth of the pipe traffic. An older build ignores this and sends FDM2.
            vh = self.h - STRIP if self.p["hud"] else self.h
            args += ["-flipdots", str(self.w), str(vh), "wireframe" if self.p["look"] == "wireframe" else "outline"]
        try:
            self.proc = subprocess.Popen(args, cwd=str(BINARY.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0)
        except OSError as e:
            if e.errno == 8:  # ENOEXEC: built on another machine (e.g. copied from an x86 PC)
                self.error = f"The Doom build is for a different CPU. Run {SETUP_HINT.split()[-1]} again on this machine."
            else:
                self.error = f"Couldn't start Doom: {e}"
            return
        self.started_once = True
        self.running = False
        self.frame = self.last_px = None
        log.info("started Doom (pid %s): %s", self.proc.pid, " ".join(args[1:]))
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
                planes = PLANES.get(h[0])
                if planes is None:
                    raise ValueError("bad frame header")
                buf = read(h[1] * h[2] * planes)
                if buf is None:
                    break
                with self.frame_lock:
                    self.frame = (h, bytes(buf))
                if self.ctx.wake is not None:
                    self.ctx.wake()  # draw it now, not on the runner's next tick
        except Exception as e:
            log.warning("doom: %s", e)
        rc = proc.wait()
        if proc is self.proc and not self.stopped:
            last = next((l for l in reversed(self.stderr) if l.strip()), "")
            self.error = f"Doom quit (exit {rc}). {last}".strip()
            log.warning("doom exited with %s: %s", rc, list(self.stderr))

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
            proc.stdin.close()  # the engine quits on EOF
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        log.info("stopped Doom (pid %s)", proc.pid)

    def stop(self):
        self.stopped = True
        self._kill()

    # ---- input ---------------------------------------------------------------------

    def _send(self, pressed, code):
        proc = self.proc
        if proc is None:
            return
        with self.in_lock:
            try:
                proc.stdin.write(bytes((1 if pressed else 0, code)))
            except (OSError, ValueError):
                pass

    def input_now(self, key, action="down", player=1):
        """A key for the running engine, straight down its pipe (called without the runner's lock).
        Returns False for anything else (starting it, the run toggle), which goes through input() as usual."""
        code = KEYS.get(ALIASES.get(key, key))
        proc = self.proc
        if code is None or proc is None or proc.poll() is not None:
            return False
        self._send(action == "down", code)
        return True

    def input(self, key, action="down", player=1):
        down = action == "down"
        key = ALIASES.get(key, key)
        if self.proc is None or self.proc.poll() is not None:
            if down and key in ("a", "b") and not self.stopped:
                self.quiet_msg = None
                self.last_quiet_check = 0
                if not self._refused():
                    self._start()
            return
        if key == "run":
            if down:
                self.running = not self.running
                self._send(self.running, SHIFT)
            return
        code = KEYS.get(key)
        if code is not None:
            self._send(down, code)

    # ---- drawing -------------------------------------------------------------------

    def _make_lut(self):
        g = max(0.2, float(self.p["gamma"] or 1))
        b = float(self.p["brightness"] or 0) * 2.55
        return [max(0, min(255, int(255 * (v / 255) ** (1 / g) + b + 0.5))) for v in range(256)]

    def _refused(self):
        """Quiet hours: returns True when Doom must not run now (and sets the message)."""
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
            # never return False/raise: the runner would drop the app without stopping the engine
            log.exception("doom render failed")

    def _render(self, c, now):
        if self._refused():
            if self.proc is not None:
                self._kill()
            self._message(c, "QUIET HOURS", f"Doom is off until {self.quiet_msg}")
            return
        if self.proc is None and not self.started_once and not self.stopped:
            self._start()
        if self.error and self.proc is None or self.error and self.proc and self.proc.poll() is not None:
            self._message(c, "DOOM", self.error + (" Press A to retry." if self.started_once else ""))
            return
        with self.frame_lock:
            frame = self.frame
        if frame is None:
            self._message(c, "DOOM", "Loading...")
            return
        # the engine sends `fps` frames a second, and every tic for a moment after a key event: draw each one as
        # it arrives, and keep the last picture when woken for anything else
        slowed = self.interval > 1 / self.fps + 1e-6  # quiet hours, "slow": fewer updates than the engine sends
        if self.last_px is not None and (frame is self.drawn or slowed and now - self.last_draw < self.interval * 0.8):
            c.px[:] = self.last_px
            return
        self.drawn = frame
        self.last_draw = now
        self._draw(c, frame, now)
        self.last_px = bytes(c.px)

    def _draw(self, c, frame, now):
        from PIL import Image, ImageOps
        h, buf = frame
        kind, fw, fh, health, armor, ammo, weapon, flags, owned, view_rows, _n = h
        in_level = flags & 1
        hud = bool(self.p["hud"]) and in_level
        if kind == b"FDM3":  # the engine sent the 3D view as dots already (see _start)
            c.px[:min(fw * fh, len(c.px))] = buf[:len(c.px)]
            if hud:
                self._hud(c, health, armor, ammo, flags, now)
            return
        gray, geo = buf[:fw * fh], buf[fw * fh:]
        img = Image.frombuffer("L", (fw, fh), gray, "raw", "L", 0, 1)
        if in_level and view_rows < fh:
            img = img.crop((0, 0, fw, view_rows))
        vh = self.h - STRIP if hud else self.h
        # outlines need the geometry plane, and only make sense for the 3D view (not menus/automap)
        if self.p["look"] != "shaded" and geo and flags & 16 and not flags & (4 | 8) and view_rows <= fh:
            g = Image.frombuffer("L", (fw, fh), geo, "raw", "L", 0, 1).crop((0, 0, fw, view_rows))
            c.px[:self.w * vh] = draw_geometry(g.resize((self.w, vh), Image.NEAREST).tobytes(), self.w, vh,
                                               self.p["look"])
            if hud:
                self._hud(c, health, armor, ammo, flags, now)
            return
        img = img.resize((self.w, vh), Image.BOX)
        if self.p["contrast"] == "auto":
            img = ImageOps.autocontrast(img, cutoff=2)
        elif self.p["contrast"] == "equalize":
            img = ImageOps.equalize(img)
        img = img.point(self._lut)
        mode = self.p["dither"]
        if mode == "diffusion":
            c.px[:self.w * vh] = img.convert("1").convert("L").point(_NONZERO).tobytes()
        elif mode == "threshold":
            c.px[:self.w * vh] = img.point(_THRESH).tobytes()
        else:
            from PIL import ImageChops
            t = self._bayer.get(vh)
            if t is None:
                t = Image.new("L", (self.w, vh))
                t.putdata([BAYER4[(y % 4) * 4 + x % 4] * 16 + 8 for y in range(vh) for x in range(self.w)])
                self._bayer[vh] = t
            c.px[:self.w * vh] = ImageChops.subtract(img, t).point(_NONZERO).tobytes()
        if hud:
            self._hud(c, health, armor, ammo, flags, now)

    def _hud(self, c, health, armor, ammo, flags, now):
        y = self.h - 5
        if flags & 2:
            if int(now * 2) % 2:
                c.text_center(y, "DEAD - PRESS B", SMALL)
            return
        c.text(0, y, f"+{max(0, health)}", SMALL)
        if armor > 0:
            c.text_center(y, f"[{armor}]", SMALL)
        right = ("R " if self.running else "") + ("--" if ammo < 0 else str(ammo))
        c.text(self.w - SMALL.width(right), y, right, SMALL)

    def describe(self):
        if self.quiet_msg:
            return f"Doom (off during quiet hours, until {self.quiet_msg})"
        if self.error:
            return f"Doom: {self.error}"
        frame = self.frame
        if frame is None or not frame[0][7] & 1:
            return "Doom"
        h = frame[0]
        if h[7] & 2:
            return "Doom: dead, press B to respawn"
        ammo = "" if h[5] < 0 else f", ammo {h[5]}"
        return f"Doom E1M{int(float(self.p['level'] or 1))}: health {h[3]}%{ammo}"


_THRESH = [1 if v >= 128 else 0 for v in range(256)]
_NONZERO = [1 if v else 0 for v in range(256)]
