"""Framebuffer + AlfaZeta flipdot protocol driver.

Each 28x7 panel is updated with one frame:
    0x80, 0x83, <address>, <28 column bytes>, 0x8F
Each column byte holds the 7 dots in that column; bit 0 is the top dot.
0x83 means "write and refresh immediately". With `sync_refresh: true` in config.yaml panels are
written with 0x84 ("write, don't refresh yet") and then one 0x80 0x82 0x8F per bus flips them all at once.

A panel doesn't flip all its columns at once: it sweeps across them left to right. With every bus starting
together, the right edge of one panel flips well after the left edge of the panel next to it. With `sweep_ms:`
set to how long that sweep takes, each column of panels starts that much after the one to its left, so a big
change runs across the whole board as one wave. That only suits a change that stands on its own: in moving
content (video, games, transitions) the columns would show different moments and tear, so with `sweep_mode: auto`
a change arriving before the previous wave has finished flips every column together.

Each bus (ip, port) has its own sender thread, so a bus that is down or slow never holds up the others.
With `baud:` set, a bus is never handed data faster than its RS485 line can carry it: changes wait here
and are coalesced, so the board shows the newest picture instead of working through a backlog.
"""

import logging
import socket
import threading
import time
from array import array
from dataclasses import dataclass

log = logging.getLogger("flipdot")

CMD_WRITE_REFRESH = 0x83
CMD_WRITE = 0x84
REFRESH = bytes([0x80, 0x82, 0x8F])
COALESCE = 0.02  # updates closer together than this go out as one (e.g. dragging on the web page)
RETRY = 2.0  # seconds before trying an unreachable bus again
_BITS = bytes([0] + [1] * 255)  # any non-zero byte is a white dot
_CHARS = bytes([48, 49] + [48] * 254)  # dot 0/1 -> "0"/"1"
_ROW = bytes([0] * 49 + [1] + [0] * 206)  # "1" -> 1, anything else -> 0
_FLIP = bytes([1, 0] + [0] * 254)


@dataclass
class Panel:
    col: int  # 0-based panel column
    row: int  # 0-based panel row
    ip: str
    port: int
    address: int
    rotate: int = 0  # 0, or 180 for a panel mounted upside down


def parse_panels(panels):
    return [Panel(int(p["column"]) - 1, int(p["row"]) - 1, str(p["ip"]), int(p["port"]), int(p["address"]),
                  int(p.get("rotate") or 0) % 360)
            for p in panels]


def load_config(path):
    """Load config.yaml with PyYAML if available, else a tiny parser for Maestro's flat format."""
    with open(path) as f:
        text = f.read()
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        pass

    def scalar(v):
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v

    cfg, panels, current = {}, None, None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "panels:":
            panels = cfg["panels"] = []
            continue
        if panels is not None and stripped.startswith("- "):
            current = {}
            panels.append(current)
            stripped = stripped[2:]
        key, _, value = stripped.partition(":")
        if panels is not None and line.startswith(" "):
            current[key.strip()] = scalar(value)
        else:
            panels = None
            cfg[key.strip()] = scalar(value)
    return cfg


class _Bus:
    """One RS485 line behind the Ethernet box (one ip:port): its socket and sender thread."""

    def __init__(self, key):
        self.key = key
        self.wake = threading.Event()
        self.sock = None
        self.reset = False  # close the socket before the next send (the panel mapping changed)
        self.free_at = 0.0  # when the line will have finished sending what it was given (with baud set)
        self.last_flush = 0.0
        self.col = 0  # leftmost panel column on this bus: its place in the sweep wave


class Board:
    def __init__(self, config, dry_run=False):
        self.pw = int(config.get("panel_width", 28))
        self.ph = int(config.get("panel_height", 7))
        self.transport = str(config.get("transport", "tcp")).lower()
        self.invert = bool(config.get("invert", False))
        self.baud = int(config.get("baud") or 0)  # RS485 speed, to pace sends (0 = don't pace)
        self.sync_refresh = bool(config.get("sync_refresh", False))
        self.sweep = max(0.0, float(config.get("sweep_ms") or 0)) / 1000  # see the module docstring
        self.sweep_mode = self._sweep_mode(config.get("sweep_mode"))
        self._wave_start = 0.0  # when the latest change was made: the sweep wave is timed from it
        self._wave = False  # whether that change gets the sweep wave (False: every column flips together)
        self._changed_at = -1e9  # when the picture last changed
        self.interactive = False  # a game is on the board (set by the runner): no wave, every column at once
        self.dry_run = dry_run

        self.panels = parse_panels(config["panels"])
        self.width = self.pw * (max(p.col for p in self.panels) + 1)
        self.height = self.ph * (max(p.row for p in self.panels) + 1)

        # noise budget: dots allowed to flip per second (0 = unlimited); changes over budget wait
        # and are coalesced, so the board always ends up on the latest picture
        self.max_flips_per_sec = 0
        self._tokens = 0.0
        self._tokens_at = time.monotonic()
        self._token_lock = threading.Lock()
        self._sent = {}  # panel index -> last frame sent (to count real flips)
        self._since = {}  # panel index -> when it first went dirty (oldest gets sent first)
        # flips per disc (board coordinates), counted on the send path, dry-run included
        self.flips = array("I", bytes(4 * self.width * self.height))
        self.flips_changed = False
        # one byte per dot, row-major: 1 = white, 0 = black (the same layout as Canvas.px)
        self.px = bytearray(self.width * self.height)
        self.version = 0
        self._packed = (None, b"")  # (version, packed bits), shared by every WebSocket client
        self.listeners = []  # called (without the lock held) whenever the picture changes
        self._lock = threading.Lock()
        self._buses = {}
        self._dirty = set()
        self._map_panels(self.panels)
        self._dirty = set(range(len(self.panels)))  # push the full (black) frame on startup
        for bus in self._buses.values():
            bus.wake.set()

    def _map_panels(self, panels):
        """Lookups for a panel mapping; starts a sender thread for any bus not seen before. Call with the lock held
        (or before any thread runs)."""
        self.panels = panels
        self._bus_of = [(p.ip, p.port) for p in panels]
        # per panel row of the board: (panel index, first dot) for each panel in it, to find what changed
        self._panel_rows = {}
        for i, p in enumerate(panels):
            self._panel_rows.setdefault(p.row, []).append((i, p.col * self.pw))
        for key in self._bus_of:
            if key not in self._buses:
                bus = self._buses[key] = _Bus(key)
                threading.Thread(target=self._bus_loop, args=(bus,), daemon=True, name=f"flipdot-{key[0]}:{key[1]}").start()
        for key, bus in self._buses.items():
            cols = [p.col for p, k in zip(panels, self._bus_of) if k == key]
            bus.col = min(cols) if cols else 0
        # time for the busiest bus to receive a whole new picture: in sync mode no column flips before every bus has
        # its data, so the wave isn't broken by a bus still busy receiving
        most = max(self._bus_of.count(k) for k in set(self._bus_of))
        self._lead = most * (self.pw + 4) * 10 / (self.baud or 57600)
        self._cols = max(p.col for p in panels) + 1

    @staticmethod
    def _sweep_mode(mode):
        mode = str(mode or "auto").lower()
        return mode if mode in ("auto", "always") else "auto"

    def _wave_length(self):
        """From a change until its wave has crossed the board (data in, then every column's sweep)."""
        return self._lead + self._cols * self.sweep

    def set_timing(self, sweep_ms=None, sync_refresh=None, sweep_mode=None):
        """Change the sweep wave and refresh mode while running (to tune them by eye on the real board)."""
        if sweep_mode is not None:
            self.sweep_mode = self._sweep_mode(sweep_mode)
        if sweep_ms is not None:
            self.sweep = max(0.0, min(1000.0, float(sweep_ms))) / 1000
        if sync_refresh is not None:
            self.sync_refresh = bool(sync_refresh)
        with self._lock:
            self._dirty = set(range(len(self.panels)))
            self._sent = {}
        for bus in list(self._buses.values()):
            bus.wake.set()
        return self.timing()

    def timing(self):
        return {"sweep_ms": round(self.sweep * 1000, 1), "sweep_mode": self.sweep_mode, "sync_refresh": self.sync_refresh,
                "baud": self.baud}

    # ---- framebuffer -----------------------------------------------------

    def _check(self, x, y):
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"({x}, {y}) is outside the {self.width}x{self.height} board")

    def get(self, x, y):
        self._check(x, y)
        return bool(self.px[y * self.width + x])

    def _show(self, new=None, edit=None):
        """Make `new` (bytes of 0/1, one per dot) the picture, or `edit(current picture)` (worked out under the lock,
        so toggles don't race): mark the panels that changed and wake their buses."""
        W, ph, pw = self.width, self.ph, self.pw
        with self._lock:
            old = self.px
            if edit is not None:
                new = edit(old)
            if new == old:
                return
            dirty = []
            for prow, cells in self._panel_rows.items():
                y0 = prow * ph * W
                for i, x0 in cells:
                    for s in range(y0 + x0, y0 + x0 + ph * W, W):
                        if old[s:s + pw] != new[s:s + pw]:
                            dirty.append(i)
                            break
            self.px[:] = new
            self.version += 1
            now = time.monotonic()
            if dirty:
                # moving content (a change while the last wave is still crossing) flips all columns together
                # and so does a game: the first move after standing still mustn't wait for a wave
                self._wave = not self.interactive and (self.sweep_mode == "always"
                                                       or now - self._changed_at >= self._wave_length())
                self._changed_at = self._wave_start = now
            wake = set()
            for i in dirty:
                if i not in self._dirty:
                    self._dirty.add(i)
                    self._since.setdefault(i, now)
                wake.add(self._bus_of[i])
            buses = [self._buses[k] for k in wake]
        for bus in buses:
            bus.wake.set()
        for fn in self.listeners:
            fn()

    def set_dots(self, dots):
        """dots: iterable of (x, y, value) where value is True (white), False (black) or None (toggle)."""
        dots = list(dots)
        for x, y, _ in dots:
            self._check(x, y)
        W = self.width

        def edit(old):
            new = bytearray(old)
            for x, y, value in dots:
                i = y * W + x
                new[i] = (new[i] ^ 1) if value is None else (1 if value else 0)
            return new
        self._show(edit=edit)

    def set_canvas(self, canvas):
        """Show a canvas.Canvas (same size as the board)."""
        px = canvas.px
        if len(px) != len(self.px):
            raise ValueError(f"canvas must be {self.width}x{self.height}")
        self._show(px.translate(_BITS))

    def check_frame(self, rows):
        if len(rows) != self.height or any(len(r) != self.width for r in rows):
            raise ValueError(f"frame must be {self.height} rows of {self.width} characters")

    def set_frame(self, rows):
        """rows: list of `height` strings of `width` chars, '1' = white, '0' = black."""
        self.check_frame(rows)
        self._show("".join(rows).encode("ascii", "replace").translate(_ROW))

    def fill(self, value):
        self._show(bytes([1 if value else 0]) * len(self.px))

    def invert_all(self):
        self._show(edit=lambda old: old.translate(_FLIP))

    def dots(self):
        """(version, bytes): one byte per dot (0/1), row-major — a snapshot of the picture."""
        with self._lock:
            return self.version, bytes(self.px)

    def packed(self):
        """(version, bytes): the dots packed 8 per byte, row-major, most significant bit first."""
        with self._lock:
            version, bits = self._packed
            if version != self.version:
                n = len(self.px)
                bits = (int(self.px.translate(_CHARS), 2) << (-n % 8)).to_bytes((n + 7) // 8, "big")
                self._packed = (self.version, bits)
            return self.version, bits

    def rows(self):
        with self._lock:
            s = self.px.translate(_CHARS).decode()
        W = self.width
        return [s[y * W:(y + 1) * W] for y in range(self.height)]

    def set_panels(self, panels):
        """Swap in a new panel mapping (same board size) and resend everything."""
        new = parse_panels(panels)
        if (self.pw * (max(p.col for p in new) + 1), self.ph * (max(p.row for p in new) + 1)) != (self.width, self.height):
            raise ValueError("the new mapping changes the board size; restart the server to apply it")
        with self._lock:
            self._map_panels(new)
            self._sent = {}
            self._since = {}
            self._dirty = set(range(len(new)))
            for bus in self._buses.values():
                bus.reset = True
        for bus in list(self._buses.values()):
            bus.wake.set()

    # ---- hardware --------------------------------------------------------

    def _frame_for(self, panel, px, cmd=CMD_WRITE_REFRESH):
        """The protocol frame for one panel from the picture `px`. Column byte bit k is the dot k rows down: with
        one byte per dot (0/1), a row of the panel read as a big integer and shifted by k sets exactly that bit."""
        W, pw, ph = self.width, self.pw, self.ph
        x0, y0 = panel.col * pw, panel.row * ph
        acc = 0
        if panel.rotate == 180:
            for k in range(ph):
                s = (y0 + ph - 1 - k) * W + x0
                acc |= int.from_bytes(px[s:s + pw][::-1], "big") << k
        else:
            for k in range(ph):
                s = (y0 + k) * W + x0
                acc |= int.from_bytes(px[s:s + pw], "big") << k
        if self.invert:
            acc ^= int.from_bytes(bytes([(1 << ph) - 1]) * pw, "big")
        return bytes([0x80, cmd, panel.address]) + acc.to_bytes(pw, "big") + b"\x8f"

    def _connect(self, bus):
        if bus.reset:
            bus.reset = False
            if bus.sock is not None:
                bus.sock.close()
                bus.sock = None
        if bus.sock is None:
            if self.transport == "udp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.connect(bus.key)
            else:
                sock = socket.create_connection(bus.key, timeout=3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("connected to %s:%d", *bus.key)
            bus.sock = sock
        return bus.sock

    def _send(self, bus, frames):
        """Send a bus its frames: in one write over TCP (one packet), one datagram each over UDP."""
        if self.dry_run:
            if log.isEnabledFor(logging.DEBUG):
                log.debug("dry-run %s:%d: %s", *bus.key, b"".join(frames).hex())
            return
        sock = self._connect(bus)
        try:
            if self.transport == "udp":
                for f in frames:
                    sock.send(f)
            else:
                sock.sendall(b"".join(frames))
        except OSError:
            bus.sock = None
            sock.close()
            raise

    def _flip_cost(self, i, frame):
        old = self._sent.get(i)
        if old is None:
            return self.pw * self.ph // 2  # state unknown: assume half the discs move
        return (int.from_bytes(old[3:-1], "big") ^ int.from_bytes(frame[3:-1], "big")).bit_count()

    def _count_flips(self, i, p, frame):
        old = self._sent.get(i)
        self._sent[i] = frame
        if old is None:
            return
        flip = p.rotate == 180
        for cx in range(self.pw):
            d = old[3 + cx] ^ frame[3 + cx]
            if not d:
                continue
            for cy in range(self.ph):
                if d >> cy & 1:
                    sx, sy = (self.pw - 1 - cx, self.ph - 1 - cy) if flip else (cx, cy)
                    self.flips[(p.row * self.ph + sy) * self.width + p.col * self.pw + sx] += 1
                    self.flips_changed = True

    def _take_budget(self, i, frame):
        """Quiet hours: spend flip tokens on this frame. Returns 0 if it may go now, else the tokens it needs."""
        rate = float(self.max_flips_per_sec or 0)
        with self._token_lock:
            now = time.monotonic()
            if rate > 0:
                self._tokens = min(rate, self._tokens + (now - self._tokens_at) * rate)
            self._tokens_at = now
            if rate <= 0:
                return 0
            cost = self._flip_cost(i, frame)
            if self._tokens < min(cost, rate):  # over budget: try again with the newest picture later
                return min(cost, rate)
            self._tokens -= cost
            return 0

    def _pace(self, bus, frames):
        """Note how long the line needs for what it was just given (10 bits per byte on the wire, 8N1)."""
        if self.baud:
            bus.free_at = max(time.monotonic(), bus.free_at) + sum(map(len, frames)) * 10 / self.baud

    @staticmethod
    def _sleep_until(t):
        wait = t - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _wait_turn(self, bus, t):
        """Wait for this column's turn in a wave, unless moving content arrives meanwhile: that flips every column
        together, so it mustn't queue behind the wave (the wake stays set, so the loop picks it up next)."""
        wait = t - time.monotonic()
        if wait > 0 and bus.wake.wait(wait) and self._wave:
            self._sleep_until(t)  # another standalone change: its own wave comes later anyway

    def _bus_loop(self, bus):
        while True:
            bus.wake.wait()
            bus.wake.clear()
            # coalesce bursts, and don't hand the line more than it can carry (later changes merge into one send)
            wait = max(bus.last_flush + COALESCE, bus.free_at) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            if self.sweep and self._wave and not self.sync_refresh:
                # no separate refresh: hold this column's data back until its turn in the wave (the picture is
                # taken after the wait, so it's the newest)
                self._wait_turn(bus, self._wave_start + bus.col * self.sweep)
            with self._lock:
                mine = [i for i in self._dirty if self._bus_of[i] == bus.key]
                self._dirty.difference_update(mine)
                order = sorted(mine, key=lambda i: self._since.get(i, 0))
                panels = self.panels
                px = bytes(self.px)
                wave, staggered = self._wave_start, self._wave
            if not order:
                continue
            sweep, sync = (self.sweep if staggered else 0.0), self.sync_refresh
            bus.last_flush = time.monotonic()
            cmd = CMD_WRITE if sync else CMD_WRITE_REFRESH
            batch, deferred, needed = [], [], 0
            for i in order:
                frame = self._frame_for(panels[i], px, cmd)
                if frame == self._sent.get(i):
                    self._since.pop(i, None)
                    continue
                short = self._take_budget(i, frame)
                if short:
                    deferred.append(i)
                    needed = max(needed, short)
                    continue
                batch.append((i, frame))
            if batch:
                # in sync mode with a sweep the refresh goes separately, when this column's turn in the wave comes
                frames = [f for _, f in batch] + ([REFRESH] if sync and not sweep else [])
                try:
                    self._send(bus, frames)
                    if sync and sweep:
                        self._pace(bus, frames)
                        frames = [REFRESH]
                        self._wait_turn(bus, wave + self._lead + bus.col * sweep)
                        self._send(bus, frames)
                except OSError as e:
                    log.warning("send to %s:%d failed: %s", *bus.key, e)
                    with self._lock:
                        self._dirty.update(i for i, _ in batch)
                        self._dirty.update(deferred)
                    time.sleep(RETRY)  # only this bus waits; the others carry on
                    bus.wake.set()
                    continue
                self._pace(bus, frames)
                for i, frame in batch:
                    self._count_flips(i, panels[i], frame)
                    self._since.pop(i, None)
            if deferred:
                with self._lock:
                    self._dirty.update(deferred)
                rate = float(self.max_flips_per_sec or 1)
                time.sleep(min(1.0, max(0.05, (needed - self._tokens) / rate)))
                bus.wake.set()
