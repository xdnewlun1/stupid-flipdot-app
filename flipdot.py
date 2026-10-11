"""Framebuffer + AlfaZeta flipdot protocol driver.

Each 28x7 panel is updated with one frame:
    0x80, 0x83, <address>, <28 column bytes>, 0x8F
Each column byte holds the 7 dots in that column; bit 0 is the top dot.
0x83 means "write and refresh immediately". With `sync_refresh: true` in config.yaml panels are
written with 0x84 ("write, don't refresh yet") and then one 0x80 0x82 0x8F per bus flips them all at once;
`auto` does that only for a change to at least half of a bus's panels (a whole new picture), and writes a small
change (a clock digit) with 0x83 so the rest of the bus isn't refreshed for it.

A change to at least half the board starts on every bus at the same moment: a bus still busy with the previous
picture holds the others back for it (up to one picture's worth of serial time), so the three columns of panels
flip together instead of one column showing the new picture while another still shows the old one.

A panel doesn't flip all its columns at once: it sweeps across them left to right. With every bus starting
together, the right edge of one panel flips well after the left edge of the panel next to it. With `sweep_ms:`
set to how long that sweep takes, each column of panels starts that much after the one to its left, so a big
change runs across the whole board as one wave. That only suits a change that stands on its own: in moving
content (video, games, transitions) the columns would show different moments and tear, so with `sweep_mode: auto`
a change arriving before the previous wave has finished flips every column together.

Each bus (ip, port) has its own sender thread, so a bus that is down or slow never holds up the others.
With `baud:` set, a bus is never handed data faster than its RS485 line can carry it: changes wait here
and are coalesced, so the board shows the newest picture instead of working through a backlog.

Panels that already show a frame aren't sent it again, so a bus that comes back after failing resends all its
panels (the converter or the panels may have lost power), and with `full_refresh_s:` every bus resends all its
panels that often while it's idle, to put right discs that were knocked or missed a frame.
"""

import json
import logging
import select
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
IDLE = 0.5  # a periodic full refresh waits for a bus to have had no change for this long
LOG_EVERY = 300.0  # while a bus stays unreachable, say so in the log this often (not on every retry)
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
        return parse_config(f.read())


def parse_config(text):
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    def scalar(v):
        v = v.strip()
        if v.startswith('"'):  # double-quoted (config.local.yaml writes JSON strings): keep '#', unescape
            try:
                return json.JSONDecoder().raw_decode(v)[0]
            except ValueError:
                pass
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
        self.retry = threading.Event()  # cut the wait before retrying an unreachable bus (Board.refresh)
        self.resend = False  # send every panel of this bus next time, even unchanged ones
        self.next_full = 0.0  # when the periodic full refresh is due (monotonic)
        self.changed_at = 0.0  # when a panel of this bus last changed (monotonic)
        # link state, for status and health: connected is None until the first send
        self.connected = None
        self.down_since = None  # wall time of the first failure in a row
        self.last_ok = None  # wall time of the last successful send
        self.last_error = None
        self.logged_at = 0.0


class Board:
    def __init__(self, config, dry_run=False):
        self.pw = int(config.get("panel_width", 28))
        self.ph = int(config.get("panel_height", 7))
        self.transport = str(config.get("transport", "tcp")).lower()
        self.invert = bool(config.get("invert", False))
        self.baud = int(config.get("baud") or 0)  # RS485 speed, to pace sends (0 = don't pace)
        self.sync_refresh = self._sync_mode(config.get("sync_refresh", False))  # True, False or "auto"
        self.sweep = max(0.0, float(config.get("sweep_ms") or 0)) / 1000  # see the module docstring
        self.sweep_mode = self._sweep_mode(config.get("sweep_mode"))
        self._wave_start = 0.0  # when the latest change was made: the sweep wave is timed from it
        self._wave = False  # whether that change gets the sweep wave (False: every column flips together)
        self._start_at = 0.0  # when the latest board-wide change goes out, on every bus at once
        self._changed_at = -1e9  # when the picture last changed
        self.interactive = False  # a game is on the board (set by the runner): no wave, every column at once
        # steady timing (set by the runner for the music visualizer): every change takes the same time to reach the
        # discs, so the board can be kept in step with speakers. No wave, and a bus with any change resends all its
        # panels in a fixed order (unchanged ones get the same data again: no extra flips), so each panel's place
        # in the send, and so its flip time, never varies
        self.steady = False
        self._present_at = None  # steady timing: when (monotonic) the latest picture is to go out, see set_canvas()
        self.dry_run = dry_run
        self.full_refresh = max(0.0, float(config.get("full_refresh_s", 300) or 0))  # 0 = no periodic full refresh
        self.link_listeners = []  # called (from a bus thread) when a bus goes down or comes back

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
                bus.next_full = time.monotonic() + self.full_refresh
                bus.thread = threading.Thread(target=self._bus_loop, args=(bus,), daemon=True, name=f"flipdot-{key[0]}:{key[1]}")
                bus.thread.start()
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

    @staticmethod
    def _sync_mode(mode):
        if isinstance(mode, str):
            mode = mode.strip().lower()
            return "auto" if mode == "auto" else mode in ("true", "yes", "on", "1")
        return bool(mode)

    def _sync(self, n, total):
        """Whether a send of n of a bus's `total` panels writes them first and flips them together: as configured,
        or in auto mode when it's a big change (at least half the bus)."""
        return self.sync_refresh if self.sync_refresh in (True, False) else n * 2 >= total

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
            self.sync_refresh = self._sync_mode(sync_refresh)
        with self._lock:
            self._dirty = set(range(len(self.panels)))
            self._sent = {}
        for bus in list(self._buses.values()):
            bus.wake.set()
        return self.timing()

    def steady_interval(self):
        """The shortest time between pictures that steady timing can keep: the busiest bus sending all its panels."""
        most = max(self._bus_of.count(k) for k in set(self._bus_of))
        return (most * (self.pw + 4) + len(REFRESH)) * 10 / (self.baud or 57600)

    def settle_time(self):
        """About how long a change to the whole board takes to land on the discs: the coalescing wait, the busiest
        bus's serial time and the panels' sweep. An app that switches the whole picture (the slideshow's beat invert)
        holds each picture at least this long, so the discs show it whole before the next one comes."""
        return COALESCE + self.steady_interval() + (self.sweep or 0.08)  # 0.08: a typical panel, when not measured

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

    def _show(self, new=None, edit=None, present_at=None):
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
            self._present_at = present_at
            now = time.monotonic()
            if dirty:
                # moving content (a change while the last wave is still crossing) flips all columns together
                # and so does a game: the first move after standing still mustn't wait for a wave
                self._wave = not self.interactive and not self.steady and (self.sweep_mode == "always"
                                                       or now - self._changed_at >= self._wave_length())
                self._changed_at = self._wave_start = now
                if len(dirty) * 2 >= len(self.panels) and not self._wave and not self.steady:
                    # a board-wide change: every bus starts it when the slowest one is free (steady timing has
                    # its own presentation time, and a wave staggers the columns on purpose)
                    self._start_at = max([now] + [max(b.free_at, b.last_flush + COALESCE) for b in self._buses.values()])
            wake = set()
            for i in dirty:
                if i not in self._dirty:
                    self._dirty.add(i)
                    self._since.setdefault(i, now)
                wake.add(self._bus_of[i])
            buses = [self._buses[k] for k in wake]
            for bus in buses:
                bus.changed_at = now
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

    def set_canvas(self, canvas, present_at=None):
        """Show a canvas.Canvas (same size as the board). present_at (time.monotonic()), with steady timing: send it
        then rather than as soon as possible, so how long the picture took to make doesn't change when it lands."""
        px = canvas.px
        if len(px) != len(self.px):
            raise ValueError(f"canvas must be {self.width}x{self.height}")
        self._show(px.translate(_BITS), present_at=present_at)

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

    def refresh(self):
        """Resend every panel, and retry unreachable buses now rather than after their wait (Retry now)."""
        for bus in list(self._buses.values()):
            bus.resend = True
            bus.retry.set()
            bus.wake.set()

    def link_status(self):
        """Which buses can't be reached, for the runner status the pages get. Only changes when a bus goes down or
        comes back (no per-send times), so it doesn't cause a status push on every frame."""
        counts = {}
        for key in self._bus_of:
            counts[key] = counts.get(key, 0) + 1
        down = []
        if not self.dry_run:
            for key, bus in list(self._buses.items()):
                if bus.connected is False and key in counts:
                    down.append({"bus": f"{key[0]}:{key[1]}", "ip": key[0], "port": key[1], "panels": counts[key],
                                 "since": bus.down_since, "error": bus.last_error})
        return {"ok": not down, "panels": len(self._bus_of), "panels_down": sum(d["panels"] for d in down), "down": down}

    def bus_health(self):
        """Every bus in detail, for /api/health."""
        now, wall = time.monotonic(), time.time()
        with self._lock:
            keys = list(self._bus_of)
            pending = [keys[i] for i in self._dirty]
            buses = list(self._buses.items())
        out = []
        for key, bus in buses:
            if key not in keys:
                continue  # left over from an earlier panel mapping
            out.append({
                "bus": f"{key[0]}:{key[1]}", "panels": keys.count(key),
                "connected": True if self.dry_run else bus.connected,
                "sender_alive": bus.thread.is_alive(),
                "last_ok_age_s": None if bus.last_ok is None else round(wall - bus.last_ok, 1),
                "down_since": bus.down_since, "last_error": bus.last_error,
                "pending_panels": pending.count(key),  # changes waiting to go out
                "backlog_ms": round(max(0.0, bus.free_at - now) * 1000),  # what the line is still sending
                "next_full_refresh_s": round(max(0.0, bus.next_full - now)) if self.full_refresh else None,
            })
        return out

    def _link_changed(self):
        for fn in self.link_listeners:
            try:
                fn()
            except Exception as e:
                log.debug("link listener failed: %s", e)

    def _link_down(self, bus, err):
        mono, first = time.monotonic(), bus.connected is not False
        bus.connected = False
        bus.last_error = str(err) or type(err).__name__
        if first:
            bus.down_since = time.time()
            bus.logged_at = mono
            log.warning("send to %s:%d failed: %s (retrying every %g s)", *bus.key, err, RETRY)
            self._link_changed()
        elif mono - bus.logged_at >= LOG_EVERY:  # a converter that's off for hours mustn't fill the journal
            bus.logged_at = mono
            log.warning("%s:%d still unreachable (for %d min): %s", *bus.key, (time.time() - bus.down_since) // 60, err)

    def _link_up(self, bus, sent_all):
        was = bus.connected
        bus.connected = True
        bus.last_ok = time.time()
        if was is True:
            return
        if was is False:
            log.info("%s:%d is reachable again after %d s: resending all its panels", *bus.key, bus.last_ok - bus.down_since)
            bus.down_since = bus.last_error = None
            if not sent_all:
                # the converter or the panels may have lost power: what they show isn't what was last sent
                bus.resend = True
                bus.wake.set()
        self._link_changed()

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
                if hasattr(socket, "TCP_USER_TIMEOUT"):
                    # a converter that lost power never answers: give up on unacknowledged data after 5 s rather
                    # than TCP's usual 15 minutes, so the bus shows as unreachable and is resent when it's back
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 5000)
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
                if self._closed_by_peer(sock):
                    # the converter dropped the connection (restarted, or another client took the port): a write
                    # now would vanish without an error, so connect again first
                    sock.close()
                    bus.sock = None
                    sock = self._connect(bus)
                    log.info("%s:%d had closed the connection: resending all its panels", *bus.key)
                    bus.resend = True  # it may have restarted, and the panels with it
                    bus.wake.set()
                sock.sendall(b"".join(frames))
        except OSError:
            bus.sock = None
            sock.close()
            raise

    @staticmethod
    def _closed_by_peer(sock):
        if not select.select([sock], [], [], 0)[0]:
            return False  # open, nothing to read: the usual case
        return sock.recv(1, socket.MSG_PEEK) == b""  # readable: data (ignored) or the end of the connection

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
        # one big XOR tells whether anything flipped; then only the changed columns, and only their set bits
        if int.from_bytes(old[3:-1], "big") == int.from_bytes(frame[3:-1], "big"):
            return
        flip, pw, ph, W, flips = p.rotate == 180, self.pw, self.ph, self.width, self.flips
        row0, col0 = p.row * ph, p.col * pw
        for cx in range(pw):
            d = old[3 + cx] ^ frame[3 + cx]
            while d:
                low = d & -d
                cy = low.bit_length() - 1
                d ^= low
                sx, sy = (pw - 1 - cx, ph - 1 - cy) if flip else (cx, cy)
                flips[(row0 + sy) * W + col0 + sx] += 1
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
            try:
                self._bus_step(bus)
            except Exception:  # a bug here mustn't leave this bus dead for good
                if time.monotonic() - bus.logged_at >= 60:
                    bus.logged_at = time.monotonic()
                    log.exception("sender for %s:%d failed", *bus.key)
                time.sleep(RETRY)
                bus.wake.set()

    def _bus_step(self, bus):
        """Wait for a change (or the periodic full refresh) and send it to this bus."""
        woke = bus.wake.wait(max(0.05, bus.next_full - time.monotonic()) if self.full_refresh else None)
        bus.wake.clear()
        if not woke:
            now = time.monotonic()
            if not self.full_refresh or now < bus.next_full:
                return
            # periodic full refresh: never during steady timing or a game, and only while this bus is idle (resending
            # a frame the panel already shows flips nothing, so it costs nothing in quiet hours either)
            with self._lock:
                idle = (not self.steady and not self.interactive and now - bus.changed_at >= IDLE
                        and bus.free_at <= now and not any(self._bus_of[i] == bus.key for i in self._dirty))
            if not idle:
                bus.next_full = now + 1.0  # look again soon
                return
            bus.resend = True
        while True:
            # coalesce bursts, don't hand the line more than it can carry, and start a board-wide change together
            # with the other buses (later changes merge into one send, and a newer change brings its own timing)
            wait = max(bus.last_flush + COALESCE, bus.free_at, self._start_at) - time.monotonic()
            if wait <= 0 or not bus.wake.wait(wait):
                break
            bus.wake.clear()
        total = self._bus_of.count(bus.key)
        with self._lock:
            pending = sum(1 for i in self._dirty if self._bus_of[i] == bus.key)
        if self.sweep and self._wave and not self._sync(pending, total):
            # no separate refresh: hold this column's data back until its turn in the wave (the picture is
            # taken after the wait, so it's the newest)
            self._wait_turn(bus, self._wave_start + bus.col * self.sweep)
        with self._lock:
            mine = [i for i in self._dirty if self._bus_of[i] == bus.key]
            self._dirty.difference_update(mine)
            steady = self.steady and bool(mine)
            resend, bus.resend = bus.resend, False
            present = self._present_at if steady else None
            if steady or resend:
                order = [i for i, k in enumerate(self._bus_of) if k == bus.key]  # all of them, always this order
            else:
                order = sorted(mine, key=lambda i: self._since.get(i, 0))
            panels = self.panels
            px = bytes(self.px)
            wave, staggered = self._wave_start, self._wave
        if not order:
            return
        sweep, sync = (self.sweep if staggered else 0.0), self._sync(len(order), total)
        bus.last_flush = time.monotonic()
        cmd = CMD_WRITE if sync else CMD_WRITE_REFRESH
        batch, deferred, needed = [], [], 0
        for i in order:
            frame = self._frame_for(panels[i], px, cmd)
            sent = self._sent.get(i)
            if sent is not None and sent[3:] == frame[3:] and not steady and not resend:  # the same dots, any command
                self._since.pop(i, None)
                continue
            short = self._take_budget(i, frame)  # an unchanged frame costs nothing
            if short:
                deferred.append(i)
                needed = max(needed, short)
                continue
            batch.append((i, frame))
        if batch:
            # in sync mode with a sweep the refresh goes separately, when this column's turn in the wave comes
            frames = [f for _, f in batch] + ([REFRESH] if sync and not sweep else [])
            if present is not None:
                self._sleep_until(present)  # steady timing: the same time after every picture was started
            try:
                self._send(bus, frames)
                if sync and sweep:
                    self._pace(bus, frames)
                    frames = [REFRESH]
                    self._wait_turn(bus, wave + self._lead + bus.col * sweep)
                    self._send(bus, frames)
            except OSError as e:
                self._link_down(bus, e)
                with self._lock:
                    self._dirty.update(i for i, _ in batch)
                    self._dirty.update(deferred)
                    # keep trying even if nothing changes meanwhile, and once it's back send every panel: the last
                    # frames "sent" before the failure showed up may never have arrived
                    bus.resend = True
                bus.retry.wait(RETRY)  # only this bus waits; the others carry on
                bus.retry.clear()
                bus.wake.set()
                return
            self._pace(bus, frames)
            for i, frame in batch:
                self._count_flips(i, panels[i], frame)
                self._since.pop(i, None)
            sent_all = len(batch) == len(order) and (steady or resend)
            if sent_all and self.full_refresh:
                bus.next_full = time.monotonic() + self.full_refresh
            if bus.connected is not True:
                self._link_up(bus, sent_all)
            else:
                bus.last_ok = time.time()
        if deferred:
            with self._lock:
                self._dirty.update(deferred)
            rate = float(self.max_flips_per_sec or 1)
            time.sleep(min(1.0, max(0.05, (needed - self._tokens) / rate)))
            bus.wake.set()
