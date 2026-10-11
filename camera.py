"""Camera input for the silhouette mirror (apps/mirror.py).

Frames come from one of:
    browser   a phone or laptop webcam on the Mirror page, sent as small grayscale frames over the WebSocket
    device    a USB/V4L2 webcam on the Pi (/dev/videoN), read through ffmpeg as raw gray frames
    rpicam    a Raspberry Pi camera module through rpicam-vid (libcamera-vid on older systems)
    file      a video file on the Pi, looped (for testing the mirror without a camera)
    off

Every frame is shrunk to the board's size and turned into a 1-bit silhouette with Pillow only: background
difference (or local contrast, or edges), morphological cleanup, and per-dot hysteresis over time so sensor
noise doesn't flip discs. Frames live in memory only, are never written anywhere and never leave this process.

Presence: when enough of the picture differs from the empty room (or moves) for `arm_s`, someone is there;
`release_s` without that, they've gone. That drives the "Someone walks up" automation trigger and the
takeover, which shows the mirror as a notification (so the runner puts back whatever was on afterwards).
"""

import base64
import datetime as dt
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

log = logging.getLogger("camera")

SOURCES = ("browser", "device", "rpicam", "file", "off")
METHODS = ("background", "adaptive", "edges")
MODES = ("solid", "outline", "trails", "life", "pixel")
DEFAULTS = {
    "source": "browser", "device": "/dev/video0", "file": "", "fps": 10,
    "method": "background", "sensitivity": 50,
    "mode": "solid", "selfie": True, "invert": False, "trail_s": 2.0,
    "takeover": False, "arm_s": 1.5, "release_s": 20, "presence_pct": 4, "night": False,
    "transition": "dissolve",
}
STALE = 2.0          # seconds without a frame: the camera counts as gone
MAX_FPS = 15         # frames processed per second at most, whatever the sources send
BG_FAST_EVERY = 5    # the background follows the room where nobody is: every this many frames...
BG_FAST = 0.1        # ...this much of the way
BG_SLOW_EVERY = 50   # and under a silhouette very slowly (someone standing still for minutes fades in)
BG_SLOW = 0.05
RELIGHT_FRAC = 0.7   # this much of the picture "in front" for RELIGHT_S: the lights changed, take a new background
RELIGHT_S = 3.0
ON_LUT = [0] * 128 + [255] * 128

_cam = None
_lock = threading.Lock()


def settings(store):
    return {**DEFAULTS, **((store.settings.get("mirror") or {}) if store else {})}


def get(store=None):
    global _cam
    with _lock:
        if _cam is None:
            _cam = Camera(store)
        elif store is not None and _cam.store is None:
            _cam.store = store
        return _cam


def clean_settings(body):
    """Validated mirror settings from the Mirror page (unknown keys dropped, numbers clamped)."""
    out = {}
    choices = {"source": SOURCES, "method": METHODS, "mode": MODES}
    numbers = {"sensitivity": (0, 100), "fps": (2, 15), "trail_s": (0.5, 10), "arm_s": (0.2, 30),
               "release_s": (3, 3600), "presence_pct": (0.5, 50)}
    for k, v in (body or {}).items():
        if k in choices:
            if v not in choices[k]:
                raise ValueError(f"{k} must be one of {', '.join(choices[k])}")
            out[k] = v
        elif k in numbers:
            lo, hi = numbers[k]
            out[k] = max(lo, min(hi, float(v)))
        elif k in ("selfie", "invert", "takeover", "night"):
            out[k] = bool(v)
        elif k in ("device", "file", "transition"):
            out[k] = str(v or "")[:300]
    if out.get("device") and not out["device"].startswith("/dev/video"):
        raise ValueError("the camera device is /dev/videoN")
    return out


_previews = {}


def preview(ctx, mode=""):
    """The mirror's picture as rows of "0"/"1", for the Mirror page. Polling it keeps a Pi camera running."""
    import apps
    from canvas import Canvas
    cam = get(ctx.store)
    cam.lease()
    app = _previews.get(mode)
    if app is None:
        app = _previews[mode] = apps.create(ctx, {"app": "mirror", "params": {"mode": mode}})
        app._acquired = True  # the lease above keeps the camera on, not this app
    c = Canvas(ctx.width, ctx.height)
    app.render(c, time.time())
    return c.rows()


def detect():
    """What camera hardware and tools this machine has. Looks at /sys and PATH only: no device is opened."""
    devices = []
    for p in sorted(Path("/sys/class/video4linux").glob("video*"), key=lambda p: int(p.name[5:] or 0)):
        try:
            name = (p / "name").read_text().strip()
        except OSError:
            name = ""
        if "pisp" in name.lower() or "codec" in name.lower() or "isp" in name.lower():
            continue  # the Pi's ISP/codec nodes aren't cameras
        devices.append({"path": f"/dev/{p.name}", "name": name or p.name})
    rpicam = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
    return {"ffmpeg": bool(shutil.which("ffmpeg")), "devices": devices, "rpicam": os.path.basename(rpicam) if rpicam else None}


def _threshold_lut(t):
    return [255 if v > t else 0 for v in range(256)]


class Processor:
    """Gray frames (board size) in, a steady 1-bit silhouette out."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.reset()

    def reset(self):
        self.bg = self.prev = self.gray = None
        self.conf = Image.new("L", (self.w, self.h), 0)
        self.mask = Image.new("L", (self.w, self.h), 0)
        self.motion_mask = self.mask
        self.n = 0
        self.fg_frac = self.motion = 0.0
        self.bg_auto = True     # the background is the first frame, not one captured on purpose
        self._relight_since = None

    def capture_background(self):
        if self.gray is not None:
            self.bg = self.gray.copy()
            self.bg_auto = False
            self.conf = Image.new("L", (self.w, self.h), 0)
            self.mask = self.conf

    def feed(self, img, method="background", sensitivity=50, now=None):
        now = time.time() if now is None else now
        if img.size != (self.w, self.h):
            img = ImageOps.fit(img, (self.w, self.h), Image.BILINEAR)
        g = img.filter(ImageFilter.BoxBlur(1)) if self.w * self.h <= 20000 else img
        if self.gray is not None:
            g = Image.blend(self.gray, g, 0.6)  # a little temporal smoothing against sensor noise
        self.prev, self.gray = self.gray, g
        self.n += 1
        s = max(0, min(100, float(sensitivity)))
        if method == "edges":
            fg = g.filter(ImageFilter.FIND_EDGES).point(_threshold_lut(int(70 - s * 0.55)))
            fg = fg.filter(ImageFilter.MaxFilter(3))
        elif method == "adaptive":
            local = g.filter(ImageFilter.BoxBlur(6))
            fg = ImageChops.subtract(local, g).point(_threshold_lut(int(40 - s * 0.35)))  # darker than around it
            fg = fg.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        else:
            if self.bg is None:
                self.bg = g.copy()
            fg = ImageChops.difference(g, self.bg).point(_threshold_lut(int(60 - s * 0.52)))
            # open (drop speckles), then close (fill small holes)
            fg = fg.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
            fg = fg.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
        # hysteresis: a dot turns on once it's been "in front" for a couple of frames, and off only once it's
        # clearly been background for a couple; in between it keeps its state, so noise doesn't flip discs
        self.conf = Image.blend(self.conf, fg, 0.5)
        on = self.conf.point(_ON_HI)
        off = self.conf.point(_OFF_LO)
        self.mask = ImageChops.darker(ImageChops.lighter(self.mask, on), ImageChops.invert(off))
        n = self.w * self.h
        self.fg_frac = self.mask.histogram()[255] / n
        if self.prev is not None:
            self.motion_mask = ImageChops.difference(g, self.prev).point(_threshold_lut(int(30 - s * 0.2)))
            self.motion = self.motion_mask.histogram()[255] / n
        if method == "background":
            self._update_background(g, now)
        return self.mask

    def _update_background(self, g, now):
        if self.n % BG_FAST_EVERY == 0:
            self.bg = Image.composite(self.bg, Image.blend(self.bg, g, BG_FAST), self.mask)
        if self.n % BG_SLOW_EVERY == 0:
            self.bg = Image.composite(Image.blend(self.bg, g, BG_SLOW), self.bg, self.mask)
        if self.fg_frac >= RELIGHT_FRAC:
            self._relight_since = self._relight_since or now
            if now - self._relight_since >= RELIGHT_S:
                log.info("camera: most of the picture changed for %.0f s, taking a new background", RELIGHT_S)
                self.bg, self.bg_auto, self._relight_since = g.copy(), True, None
                self.conf = Image.new("L", (self.w, self.h), 0)
                self.mask = self.conf
        else:
            self._relight_since = None


_ON_HI = [255 if v >= 170 else 0 for v in range(256)]
_OFF_LO = [255 if v < 80 else 0 for v in range(256)]


class Presence:
    """Someone is there once the raw signal has held for arm_s; gone after release_s without it."""

    def __init__(self):
        self.present = False
        self._since = None   # raw signal on since
        self.last_seen = 0.0

    def update(self, raw, now, arm_s, release_s):
        """Returns "arrive", "leave" or None."""
        if raw:
            self.last_seen = now
            self._since = self._since or now
            if not self.present and now - self._since >= arm_s:
                self.present = True
                return "arrive"
        else:
            self._since = None
            if self.present and now - self.last_seen >= release_s:
                self.present = False
                return "leave"
        return None


class Snapshot:
    """One processed frame, as the apps see it (images are never changed after this is made)."""
    __slots__ = ("seq", "t", "gray", "mask", "motion_mask", "fg", "motion")

    def __init__(self, seq, t, gray, mask, motion_mask, fg, motion):
        self.seq, self.t, self.gray, self.mask, self.motion_mask, self.fg, self.motion = seq, t, gray, mask, motion_mask, fg, motion


class Camera:
    def __init__(self, store, w=84, h=42):
        self.store = store
        self.w, self.h = w, h
        self.proc = Processor(w, h)
        self.presence = Presence()
        self.snap = None
        self.seq = 0
        self.lock = threading.Lock()
        self.browser = None        # {"id", "name", "last"} of the browser whose frames are used
        self.error = None
        self.users = 0             # mirror apps showing it right now (device sources only run while needed)
        self.on_presence = []      # callbacks(event, now): "arrive" | "leave"
        self.on_frame = []         # callbacks(now) after each processed frame
        self.ms = 0.0              # processing time of the last frame
        self._last_proc = 0.0
        self._proc = None          # the ffmpeg / rpicam-vid process
        self._reader = None
        self._want = None          # the source config the reader was started for
        self._stop = threading.Event()
        self._watch = None
        self._lease = 0.0          # the Mirror page's preview keeps a Pi camera on until then

    # ---- settings and lifecycle ----------------------------------------------------

    def cfg(self):
        return settings(self.store)

    def apply_settings(self):
        """Settings changed: restart the Pi-side camera if its source changed, reset the picture if the method did."""
        c = self.cfg()
        key = (c["method"], c["source"], c["device"], c["file"])
        if getattr(self, "_key", None) not in (None, key):
            with self.lock:  # another camera or way of finding people: start over from an empty room
                self.proc.reset()
                self.presence = Presence()
                self.snap = None
        self._key = key
        self.ensure()

    def acquire(self):
        with self.lock:
            self.users += 1
        self.ensure()

    def lease(self, seconds=10):
        self._lease = time.time() + seconds
        self.ensure()

    def release(self):
        with self.lock:
            self.users = max(0, self.users - 1)
        self.ensure()

    def wanted(self):
        """The Pi-side source that should be running now, or None."""
        c = self.cfg()
        if c["source"] in ("device", "rpicam", "file") and (self.users > 0 or c["takeover"] or time.time() < self._lease):
            return (c["source"], c["device"], c["file"], int(c["fps"]))
        return None

    def ensure(self):
        want = self.wanted()
        if want == self._want and (want is None or (self._reader and self._reader.is_alive())):
            return
        self._stop_reader()
        self._want = want
        if want:
            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, args=(want,), daemon=True, name="camera")
            self._reader.start()
        if self._watch is None:
            self._watch = threading.Thread(target=self._watchdog, daemon=True, name="camera-watch")
            self._watch.start()

    def _watchdog(self):
        # takeover with a Pi camera runs without any app asking for it: restart it if it died
        while True:
            time.sleep(5)
            try:
                self.ensure()
            except Exception:
                log.exception("camera watchdog")

    def stop(self):
        self._stop_reader()
        self._want = None

    def _stop_reader(self):
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(2)
            except subprocess.TimeoutExpired:
                p.kill()
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(3)
        self._proc = self._reader = None

    # ---- Pi-side sources ---------------------------------------------------------------

    def command(self, want):
        """The command line that produces raw gray frames of the board's size on stdout (None: can't)."""
        source, device, path, fps = want
        crop = r"crop=min(iw\,2*ih):min(iw\,2*ih)/2"
        vf = f"{crop},scale={self.w}:{self.h}:flags=area,format=gray"
        out = ["-vf", vf, "-r", str(fps), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        if source == "device":
            if not shutil.which("ffmpeg"):
                raise RuntimeError("ffmpeg isn't installed (sudo apt install ffmpeg)")
            if not os.path.exists(device):
                raise RuntimeError(f"no camera at {device}")
            return ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "v4l2", "-framerate", "15",
                    "-video_size", "320x240", "-i", device] + out
        if source == "file":
            if not shutil.which("ffmpeg"):
                raise RuntimeError("ffmpeg isn't installed")
            if path.startswith("lavfi:"):  # e.g. lavfi:testsrc2=size=320x240:rate=10 (for tests)
                src = ["-f", "lavfi", "-i", path[6:]]
            elif not os.path.isfile(path):
                raise RuntimeError(f"no video file at {path or '(not set)'}")
            else:
                src = ["-stream_loop", "-1", "-i", path]
            return ["ffmpeg", "-nostdin", "-loglevel", "error", "-re"] + src + out
        if source == "rpicam":
            exe = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
            if not exe:
                raise RuntimeError("rpicam-vid isn't installed (sudo apt install rpicam-apps)")
            return [exe, "-t", "0", "-n", "--width", "320", "--height", "240", "--framerate", str(fps),
                    "--codec", "yuv420", "-o", "-"]
        return None

    def _read_loop(self, want):
        try:
            cmd = self.command(want)
        except RuntimeError as e:
            self.error = str(e)
            return
        rpicam = want[0] == "rpicam"
        size = (320, 240) if rpicam else (self.w, self.h)
        frame_bytes = size[0] * size[1] * 3 // 2 if rpicam else size[0] * size[1]
        y_bytes = size[0] * size[1]
        try:
            self._proc = p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            self.error = f"couldn't start {cmd[0]}: {e}"
            return
        self.error = None
        log.info("camera: %s", " ".join(cmd[:12]))
        while not self._stop.is_set():
            buf = p.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            try:
                self.feed(Image.frombytes("L", size, buf[:y_bytes]))
            except Exception:
                log.exception("camera frame")
        if not self._stop.is_set():
            err = (p.stderr.read(400) or b"").decode(errors="replace").strip().splitlines()
            self.error = f"{cmd[0]} stopped" + (f": {err[-1]}" if err else "")
            log.warning("camera: %s", self.error)
        if p.poll() is None:
            p.terminate()

    # ---- frames in -------------------------------------------------------------------

    def push_browser(self, msg, now=None):
        """A frame from a browser: {"w", "h", "data": base64 gray bytes, "id", "name"}. False if it's not used."""
        now = time.time() if now is None else now
        if self.cfg()["source"] != "browser":
            return False
        sid = str(msg.get("id") or "")[:64]
        b = self.browser
        if b and b["id"] != sid and now - b["last"] < STALE:
            return False  # another browser is already the camera
        w, h = int(msg["w"]), int(msg["h"])
        if not (8 <= w <= 640 and 4 <= h <= 480):
            raise ValueError("camera frame size out of range")
        data = base64.b64decode(msg["data"])
        if len(data) != w * h:
            raise ValueError("camera frame has the wrong length")
        self.browser = {"id": sid, "name": str(msg.get("name") or "a browser")[:60], "last": now}
        return self.feed(Image.frombytes("L", (w, h), data), now)

    def stop_browser(self, sid):
        if self.browser and self.browser["id"] == sid:
            self.browser = None

    def feed(self, img, now=None):
        now = time.time() if now is None else now
        if now - self._last_proc < 1 / MAX_FPS * 0.8:
            return False
        self._last_proc = now
        c = self.cfg()
        t0 = time.perf_counter()
        with self.lock:
            self.proc.feed(img, c["method"], c["sensitivity"], now)
            p = self.proc
            self.seq += 1
            self.snap = Snapshot(self.seq, now, p.gray, p.mask, p.motion_mask, p.fg_frac, p.motion)
            if c["method"] == "background":
                raw = p.fg_frac * 100 >= float(c["presence_pct"]) or p.motion * 100 >= float(c["presence_pct"]) * 1.5
            else:
                raw = p.motion * 100 >= float(c["presence_pct"])
            event = self.presence.update(raw, now, float(c["arm_s"]), float(c["release_s"]))
        self.ms = (time.perf_counter() - t0) * 1000
        for fn in self.on_frame:
            fn(now, raw)
        if event:
            log.info("camera: someone %s", "walked up" if event == "arrive" else "left")
            for fn in self.on_presence:
                fn(event, now)
        return True

    def capture_background(self):
        with self.lock:
            self.proc.capture_background()
            return self.proc.gray is not None

    # ---- frames out ------------------------------------------------------------------

    def latest(self, now=None):
        """The newest snapshot, or None when the camera has gone quiet."""
        now = time.time() if now is None else now
        s = self.snap
        return s if s is not None and now - s.t < STALE else None

    def status(self, now=None):
        now = time.time() if now is None else now
        c = self.cfg()
        s = self.latest(now)
        b = self.browser if self.browser and now - self.browser["last"] < STALE else None
        return {
            "settings": c, "detected": detect(), "live": s is not None, "error": self.error,
            "browser": {"id": b["id"], "name": b["name"]} if b else None,
            "running": bool(self._reader and self._reader.is_alive()),
            "present": self.presence.present, "fg": round(s.fg * 100, 1) if s else 0,
            "motion": round(s.motion * 100, 1) if s else 0, "ms": round(self.ms, 2),
            "background": "none" if self.proc.bg is None else ("auto" if self.proc.bg_auto else "captured"),
        }


# ---- takeover ---------------------------------------------------------------------------

class Takeover:
    """Shows the mirror while someone is in front of the camera, then gives the board back.

    It's a runner notification that keeps getting extended: each frame with someone there pushes its end
    `release_s` ahead, so it ends by itself once they've gone (or the camera stops)."""

    def __init__(self, cam, runner, store, automations=None):
        self.cam, self.runner, self.store, self.automations = cam, runner, store, automations
        self.app = None
        self.last_reason = None
        cam.on_presence.append(self.presence)
        cam.on_frame.append(self.frame)

    def blocked(self, now):
        """Why the mirror shouldn't take over now, or None."""
        from boardcare import quiet_active
        c = self.cam.cfg()
        r = self.runner
        if not c["takeover"]:
            return "takeover is off"
        if not c["night"] and quiet_active(self.store.settings.get("quiet_hours") or {}, dt.datetime.fromtimestamp(now)):
            return "quiet hours"
        if r.live:
            return "a live stream is on"
        if r.notif and r.notif["app"] is not self.app:
            return "a notification is showing"
        if r.app is not None and r.app.id == "mirror":
            return "the mirror is already on"
        if r.app is not None and getattr(r.app, "interactive", False):
            return "a game is being played"
        if r.mode == "schedule" and r.playlist == "__off__":
            return "the schedule has the board off"
        return None

    def presence(self, event, now):
        if self.automations is not None and hasattr(self.automations, "presence"):
            self.automations.presence(event, now)
        if event == "arrive":
            self.start(now)

    def ours(self):
        n = self.runner.notif
        return n is not None and self.app is not None and n["app"] is self.app

    def start(self, now):
        why = self.blocked(now)
        self.last_reason = why
        if why or self.ours():
            return False
        c = self.cam.cfg()
        res = self.runner.notify(spec={"app": "mirror", "params": {}}, duration=float(c["release_s"]),
                                 transition=c.get("transition") or "dissolve", priority="low", only_idle=True,
                                 source="mirror", record=False, dedupe=False)
        if isinstance(res, dict) and res.get("status") != "show":
            self.last_reason = "a notification is on the board"
            return False
        self.app = self.runner.notif["app"]
        log.info("camera: mirror takes over the board")
        return True

    def frame(self, now, raw):
        if raw and self.ours() and self.cam.cfg()["takeover"]:
            n = self.runner.notif
            n["until"] = max(n["until"], now + float(self.cam.cfg()["release_s"]))

    def status(self):
        return {"active": self.ours(), "blocked": self.last_reason}


def setup(store, runner, automations=None, w=None, h=None):
    """Wire the camera to the board (server start)."""
    cam = get(store)
    if w and h and (w, h) != (cam.w, cam.h):
        cam.w, cam.h = w, h
        cam.proc = Processor(w, h)
    cam.takeover = Takeover(cam, runner, store, automations)
    cam.apply_settings()
    return cam
