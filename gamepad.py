"""USB gamepads (Mayflash GameCube adapter and generic joysticks) as game input.

Stdlib only: reads Linux evdev nodes (/dev/input/eventN) directly.

Mayflash / DragonRise GameCube adapter, PC mode (USB 0079:1843)
----------------------------------------------------------------
The kernel binds it with HID_QUIRK_MULTI_INPUT (drivers/hid/hid-quirks.c and
drivers/hid/hid-mf.c, USB_DEVICE_ID_DRAGONRISE_GAMECUBE1), so each of the four
ports is its own input device / eventN node, created in port order. HID buttons
1..16 become BTN_TRIGGER (0x120) .. BTN_DEAD (0x12f). Mapping by button index:

  idx  evdev code    GameCube     idx  axis        GameCube
  b0   BTN_TRIGGER   X            a0   ABS_X       stick X
  b1   BTN_THUMB     A            a1   ABS_Y       stick Y (down = +)
  b2   BTN_THUMB2    B            a2   ABS_Z       C-stick Y
  b3   BTN_TOP       Y            a3   ABS_RX      L analog
  b4   BTN_TOP2      L (digital)  a4   ABS_RY      R analog
  b5   BTN_PINKIE    R (digital)  a5   ABS_RZ      C-stick X
  b7   BTN_BASE2     Z
  b9   BTN_BASE4     Start
  b12..b15  0x12c/0x12d/0x12e/BTN_DEAD = D-pad up/right/down/left

Sources: RetroArch udev autoconfig "MAYFLASH_GameCube_Controller_Adapter.cfg"
(vendor 121 / product 6211) and the SDL_GameControllerDB Windows entry
03000000790000004318000000000000 agree on the buttons above. The SDL Linux
entry 03000000790000004318000010010000 lists the D-pad as hat h0 and swaps
A/B/X, so the hat (ABS_HAT0X/Y) is handled too and the mapping is overridable;
the Settings live view shows the real code names. Axis codes a0..a5 are
inferred from evdev's index order (lowest ABS code first).
"""

import collections
import errno
import fcntl
import os
import re
import select
import struct
import threading
import time

# struct input_event: the kernel always uses native longs for sec/usec (even when
# 32-bit userland has a 64-bit time_t), so native "l" matches on 32- and 64-bit Pis.
EVENT = struct.Struct("@llHHi")
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3

ABSINFO = struct.Struct("6i")  # value, minimum, maximum, fuzz, flat, resolution


def _IOC(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | nr


def EVIOCGABS(axis):
    return _IOC(2, 0x40 + axis, ABSINFO.size)


def EVIOCGBIT(ev, size):
    return _IOC(2, 0x20 + ev, size)


EVIOCGNAME = _IOC(2, 0x06, 256)

KEY_NAMES = {
    0x120: "BTN_TRIGGER", 0x121: "BTN_THUMB", 0x122: "BTN_THUMB2", 0x123: "BTN_TOP",
    0x124: "BTN_TOP2", 0x125: "BTN_PINKIE", 0x126: "BTN_BASE", 0x127: "BTN_BASE2",
    0x128: "BTN_BASE3", 0x129: "BTN_BASE4", 0x12a: "BTN_BASE5", 0x12b: "BTN_BASE6",
    0x12c: "BTN_0x12c", 0x12d: "BTN_0x12d", 0x12e: "BTN_0x12e", 0x12f: "BTN_DEAD",
    0x130: "BTN_SOUTH", 0x131: "BTN_EAST", 0x132: "BTN_C", 0x133: "BTN_NORTH",
    0x134: "BTN_WEST", 0x135: "BTN_Z", 0x136: "BTN_TL", 0x137: "BTN_TR", 0x138: "BTN_TL2",
    0x139: "BTN_TR2", 0x13a: "BTN_SELECT", 0x13b: "BTN_START", 0x13c: "BTN_MODE",
    0x13d: "BTN_THUMBL", 0x13e: "BTN_THUMBR",
    0x220: "BTN_DPAD_UP", 0x221: "BTN_DPAD_DOWN", 0x222: "BTN_DPAD_LEFT", 0x223: "BTN_DPAD_RIGHT",
}
KEY_NAMES.update({0x2c0 + i: f"BTN_TRIGGER_HAPPY{i + 1}" for i in range(40)})
KEY_CODES = {v: k for k, v in KEY_NAMES.items()}
KEY_CODES.update({"BTN_A": 0x130, "BTN_B": 0x131, "BTN_X": 0x133, "BTN_Y": 0x134, "BTN_GAMEPAD": 0x130,
                  "BTN_JOYSTICK": 0x120})
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11

DEFAULT_MAPPING = {
    # Mayflash GameCube adapter (PC mode)
    "BTN_THUMB": "a", "BTN_THUMB2": "b", "BTN_TRIGGER": "x", "BTN_TOP": "y", "BTN_BASE2": "z",
    "BTN_TOP2": "l", "BTN_PINKIE": "r", "BTN_BASE4": "start",
    "BTN_0x12c": "up", "BTN_0x12d": "right", "BTN_0x12e": "down", "BTN_DEAD": "left",
    # standard Linux gamepads (xpad, hid-sony, hid-nintendo, 8BitDo ...)
    "BTN_SOUTH": "a", "BTN_EAST": "b", "BTN_NORTH": "x", "BTN_WEST": "y", "BTN_Z": "z",
    "BTN_TL": "l", "BTN_TR": "r", "BTN_START": "start",
    "BTN_DPAD_UP": "up", "BTN_DPAD_DOWN": "down", "BTN_DPAD_LEFT": "left", "BTN_DPAD_RIGHT": "right",
}

MAYFLASH = ("0079", "1843")
PERMISSION_HELP = ("No permission to read {path}. Add the service user to the input group "
                   "(sudo usermod -aG input $USER), then log out and back in or restart the service.")


def key_name(code):
    return KEY_NAMES.get(code, f"BTN_0x{code:x}")


def parse_code(name):
    """'BTN_SOUTH', 'BTN_0x12c', '0x12c' or '300' -> key code (or None)."""
    name = str(name).strip()
    if name in KEY_CODES:
        return KEY_CODES[name]
    m = re.fullmatch(r"(?:BTN_)?(0x[0-9a-fA-F]+|\d+)", name)
    return int(m.group(1), 0) if m else None


def parse_proc_devices(text):
    """Parse /proc/bus/input/devices into a list of dicts."""
    out = []
    for block in text.strip().split("\n\n"):
        d = {"vendor": "", "product": "", "name": "", "phys": "", "sysfs": "", "handlers": [], "ev": 0}
        for line in block.splitlines():
            if line.startswith("I:"):
                for k, v in re.findall(r"(\w+)=(\w+)", line):
                    if k in ("Vendor", "Product"):
                        d[k.lower()] = v.lower()
            elif line.startswith("N: Name="):
                d["name"] = line[8:].strip().strip('"')
            elif line.startswith("P: Phys="):
                d["phys"] = line[8:].strip()
            elif line.startswith("S: Sysfs="):
                d["sysfs"] = line[9:].strip()
            elif line.startswith("H: Handlers="):
                d["handlers"] = line[12:].split()
            elif line.startswith("B: EV="):
                d["ev"] = int(line[6:].split()[-1], 16)
        d["event"] = next((h for h in d["handlers"] if re.fullmatch(r"event\d+", h)), None)
        if d["event"]:
            out.append(d)
    return out


def _num(s):
    m = re.search(r"(\d+)$", s or "")
    return int(m.group(1)) if m else 0


class Axis:
    def __init__(self, info):
        # info = (value, min, max) from EVIOCGABS, or None -> learn the range from what we see
        self.known = info is not None and info[2] > info[1]
        value = info[0] if info else 0
        self.lo, self.hi = (info[1], info[2]) if self.known else (min(0, value), max(255, value))
        self.value = value
        self.pos = 0  # -1, 0, 1 after dead zone + hysteresis

    def norm(self, v):
        if not self.known:
            self.lo, self.hi = min(self.lo, v), max(self.hi, v)
        half = (self.hi - self.lo) / 2 or 1
        return max(-1.0, min(1.0, (v - (self.lo + self.hi) / 2) / half))


class Pad:
    def __init__(self, path, info, fd, player):
        self.path, self.fd, self.player = path, fd, player
        self.name, self.phys, self.sysfs = info["name"], info["phys"], info["sysfs"]
        self.mayflash = (info["vendor"], info["product"]) == MAYFLASH
        self.axes = {}
        self.sources = {}   # source -> game key currently held by it
        self.held = set()   # game keys reported down
        self.armed = False  # stick seen near centre at least once (empty ports may rest off-centre)
        self.last_input = None
        self.buf = b""


class GamepadManager:
    """Reads gamepads in one background thread and calls on_input(key, "down"|"up", player)."""

    RESCAN = 3.0

    def __init__(self, on_input, mapping=None, deadzone=0.35, any_joystick=True,
                 proc_path="/proc/bus/input/devices", dev_dir="/dev/input", absinfo=None):
        self.on_input = on_input
        self.deadzone = float(deadzone)
        self.any_joystick = any_joystick
        self.proc_path, self.dev_dir = proc_path, dev_dir
        self._absinfo = absinfo or self._ioctl_absinfo
        self.lock = threading.RLock()
        self.pads = {}           # path -> Pad
        self.ignored = set()     # paths opened and found not to be gamepads
        self.errors = {}         # path -> message
        self.message = ""
        self.events = collections.deque(maxlen=20)
        self._players = {}       # sysfs/path -> player, kept while the device is present
        self._stop = threading.Event()
        self._wake_r, self._wake_w = os.pipe()
        self.thread = None
        self.set_mapping(mapping)

    # ---- public API ----
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="gamepad", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        os.write(self._wake_w, b"x")
        if self.thread:
            self.thread.join(2)
        self.thread = None
        with self.lock:
            for path in list(self.pads):
                self._remove(path)

    def set_mapping(self, mapping=None):
        """mapping: {code name: game key}; merged over DEFAULT_MAPPING; "" or None disables a button."""
        merged = dict(DEFAULT_MAPPING)
        merged.update(mapping or {})
        codes = {}
        for name, key in merged.items():
            code = parse_code(name)
            if code is not None and key:
                codes[code] = str(key)
        with self.lock:
            self.mapping = {key_name(c): k for c, k in codes.items()}
            self._codes = codes

    def set_deadzone(self, deadzone):
        self.deadzone = max(0.05, min(0.95, float(deadzone)))

    def status(self):
        with self.lock:
            pads = [{"path": p.path, "name": p.name, "player": p.player, "connected": True,
                     "adapter": p.mayflash, "held": sorted(p.held), "last_input": p.last_input}
                    for p in sorted(self.pads.values(), key=lambda p: p.player)]
            pads += [{"path": path, "name": "", "player": None, "connected": False, "error": msg}
                     for path, msg in sorted(self.errors.items())]
            return {"running": bool(self.thread and self.thread.is_alive()), "message": self.message,
                    "pads": pads, "events": list(self.events), "mapping": dict(self.mapping),
                    "deadzone": self.deadzone}

    # ---- discovery ----
    def scan(self):
        try:
            with open(self.proc_path) as f:
                devices = parse_proc_devices(f.read())
        except OSError as e:
            devices, proc_error = [], f"Cannot read {self.proc_path}: {e.strerror}"
        else:
            proc_error = ""
        wanted = []
        for d in devices:
            if (d["vendor"], d["product"]) == MAYFLASH:
                wanted.append(d)
            elif self.any_joystick and (any(h.startswith("js") for h in d["handlers"])
                                        or (d["ev"] >> EV_ABS) & 1 and (d["ev"] >> EV_KEY) & 1):
                wanted.append(d)
        # stable order: adapters first, then by USB path and creation order (= port order)
        wanted.sort(key=lambda d: ((d["vendor"], d["product"]) != MAYFLASH, d["phys"].split("/")[0],
                                   _num(d["sysfs"]), _num(d["event"])))
        released = []
        with self.lock:
            present = {os.path.join(self.dev_dir, d["event"]) for d in wanted}
            for path in list(self.pads):
                if path not in present:
                    released += self._remove(path)
            for path in list(self.errors):
                if path not in present:
                    del self.errors[path]
            self.ignored &= present
            for d in wanted:
                path = os.path.join(self.dev_dir, d["event"])
                if path not in self.pads and path not in self.ignored:
                    self._open(path, d)
            self.message = proc_error or self._summary(devices)
        self._emit(released)

    def _summary(self, devices):
        if self.errors and not self.pads:
            return next(iter(self.errors.values()))
        if not self.pads:
            return "No gamepad found." if devices else "No input devices found."
        n = len(self.pads)
        return f"{n} gamepad{'s' if n != 1 else ''} ready." + (" " + next(iter(self.errors.values())) if self.errors else "")

    def _open(self, path, info):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError:
            # only complain about devices that are clearly game controllers (not touchpads etc.)
            if (info["vendor"], info["product"]) == MAYFLASH or any(h.startswith("js") for h in info["handlers"]):
                self.errors[path] = PERMISSION_HELP.format(path=path)
            else:
                self.ignored.add(path)
            return
        except OSError as e:
            self.errors[path] = f"Cannot open {path}: {e.strerror}"
            return
        self.errors.pop(path, None)
        keys, abses = self._caps(fd)
        if keys is not None and (info["vendor"], info["product"]) != MAYFLASH:
            # fallback devices must look like a joystick/gamepad (not a keyboard or touchpad)
            if not any(0x120 <= k < 0x140 or 0x2c0 <= k < 0x2e8 for k in keys):
                os.close(fd)
                self.ignored.add(path)
                return
        ident = info["sysfs"] or path
        used = {p.player for p in self.pads.values()}
        player = self._players.get(ident)
        if player is None or player in used:
            player = next(i for i in range(1, 100) if i not in used)
        self._players[ident] = player
        pad = Pad(path, info, fd, player)
        for code in (abses if abses is not None else (ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y)):
            if code in (ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y):
                pad.axes[code] = Axis(self._absinfo(fd, code))
        x, y = pad.axes.get(ABS_X), pad.axes.get(ABS_Y)
        pad.armed = not (x and y) or (abs(x.norm(x.value)) < self.deadzone and abs(y.norm(y.value)) < self.deadzone)
        self.pads[path] = pad

    @staticmethod
    def _caps(fd):
        """(key codes, abs codes) via EVIOCGBIT, or (None, None) if the node doesn't support it."""
        def bits(ev, nbytes):
            buf = bytearray(nbytes)
            fcntl.ioctl(fd, EVIOCGBIT(ev, nbytes), buf)
            return {i for i in range(nbytes * 8) if buf[i >> 3] >> (i & 7) & 1}
        try:
            return bits(EV_KEY, 96), bits(EV_ABS, 8)
        except OSError:
            return None, None

    @staticmethod
    def _ioctl_absinfo(fd, code):
        try:
            buf = bytearray(ABSINFO.size)
            fcntl.ioctl(fd, EVIOCGABS(code), buf)
            value, lo, hi = ABSINFO.unpack(buf)[:3]
            return value, lo, hi
        except OSError:
            return None

    def _remove(self, path):
        """Close a pad; returns the releases to emit (outside the lock)."""
        pad = self.pads.pop(path, None)
        if not pad:
            return []
        try:
            os.close(pad.fd)
        except OSError:
            pass
        self._players.pop(pad.sysfs or path, None)
        out = [(k, "up", pad.player) for k in sorted(pad.held)]
        pad.held.clear()
        pad.sources.clear()
        return out

    # ---- reading ----
    def _loop(self):
        next_scan = 0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:
                self.scan()
                next_scan = now + self.RESCAN
            with self.lock:
                fds = {p.fd: p for p in self.pads.values()}
            try:
                ready, _, _ = select.select(list(fds) + [self._wake_r], [], [], max(0.05, next_scan - now))
            except (OSError, ValueError):
                next_scan = 0
                continue
            if self._wake_r in ready:
                os.read(self._wake_r, 64)
            for fd in ready:
                if fd in fds:
                    self._read(fds[fd])

    def _read(self, pad):
        out = []
        try:
            data = os.read(pad.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError as e:
            data = b"" if e.errno in (errno.ENODEV, errno.EIO, errno.EBADF) else None
            if data is None:
                return
        with self.lock:
            if self.pads.get(pad.path) is not pad:
                return
            if not data:  # unplugged
                out = self._remove(pad.path)
            else:
                pad.buf += data
                n = len(pad.buf) // EVENT.size * EVENT.size
                for off in range(0, n, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(pad.buf, off)
                    out += self._event(pad, etype, code, value)
                pad.buf = pad.buf[n:]
        self._emit(out)

    def feed(self, pad_path, etype, code, value):
        """Process one event as if read from pad_path (used by tests and the reader)."""
        with self.lock:
            pad = self.pads.get(pad_path)
            out = self._event(pad, etype, code, value) if pad else []
        self._emit(out)

    def _event(self, pad, etype, code, value):
        now = time.time()
        if etype == EV_KEY:
            pad.last_input = now
            key = self._codes.get(code)
            self._log(pad, key_name(code), value, key)
            src = ("btn", code)
            if value:  # 1 = press, 2 = autorepeat
                if key and src not in pad.sources:
                    pad.sources[src] = key
            else:
                pad.sources.pop(src, None)
            return self._sync(pad)
        if etype == EV_ABS and code in pad.axes:
            ax = pad.axes[code]
            ax.value = value
            if code in (ABS_HAT0X, ABS_HAT0Y):
                ax.pos = (value > 0) - (value < 0)
            else:
                v = ax.norm(value)
                if not pad.armed:
                    other = pad.axes.get(ABS_Y if code == ABS_X else ABS_X)
                    if abs(v) < self.deadzone and (not other or abs(other.norm(other.value)) < self.deadzone):
                        pad.armed = True
                    return []
                release = self.deadzone * 0.7  # hysteresis: let go a bit closer to the centre
                if ax.pos == 0 and abs(v) >= self.deadzone:
                    ax.pos = 1 if v > 0 else -1
                elif ax.pos and (abs(v) < release or (v > 0) != (ax.pos > 0)):
                    ax.pos = (1 if v > 0 else -1) if abs(v) >= self.deadzone else 0
                pad.last_input = now
            neg, pos = ("left", "right") if code in (ABS_X, ABS_HAT0X) else ("up", "down")
            src = ("abs", code)
            before = pad.sources.get(src)
            after = {-1: neg, 1: pos}.get(ax.pos)
            if after:
                pad.sources[src] = after
            else:
                pad.sources.pop(src, None)
            if after != before and after:
                self._log(pad, ("ABS_HAT0" if code >= ABS_HAT0X else "ABS_") + ("X" if code in (ABS_X, ABS_HAT0X) else "Y"),
                          value, after)
            return self._sync(pad)
        return []

    def _sync(self, pad):
        want = set(pad.sources.values())
        out = [(k, "up", pad.player) for k in sorted(pad.held - want)]
        out += [(k, "down", pad.player) for k in sorted(want - pad.held)]
        pad.held = want
        return out

    def _log(self, pad, code, value, key):
        self.events.append({"t": round(time.time(), 3), "player": pad.player, "code": code,
                            "value": value, "key": key})

    def _emit(self, out):
        for key, action, player in out:
            try:
                self.on_input(key, action, player)
            except Exception as e:  # never let a game bug kill the reader thread
                print(f"gamepad: input handler failed: {e!r}")
