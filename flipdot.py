"""Framebuffer + AlfaZeta flipdot protocol driver.

Each 28x7 panel is updated with one frame:
    0x80, 0x83, <address>, <28 column bytes>, 0x8F
Each column byte holds the 7 dots in that column; bit 0 is the top dot.
0x83 means "write and refresh immediately".
"""

import logging
import socket
import threading
import time
from array import array
from dataclasses import dataclass

log = logging.getLogger("flipdot")

CMD_WRITE_REFRESH = 0x83


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


class Board:
    def __init__(self, config, dry_run=False):
        self.pw = int(config.get("panel_width", 28))
        self.ph = int(config.get("panel_height", 7))
        self.transport = str(config.get("transport", "tcp")).lower()
        self.invert = bool(config.get("invert", False))
        self.dry_run = dry_run

        self.panels = parse_panels(config["panels"])
        self.width = self.pw * (max(p.col for p in self.panels) + 1)
        self.height = self.ph * (max(p.row for p in self.panels) + 1)

        self._panel_lookup = {(p.col, p.row): i for i, p in enumerate(self.panels)}
        # noise budget: dots allowed to flip per second (0 = unlimited); changes over budget wait
        # and are coalesced, so the board always ends up on the latest picture
        self.max_flips_per_sec = 0
        self._tokens = 0.0
        self._tokens_at = time.monotonic()
        self._sent = {}  # panel index -> last frame sent (to count real flips)
        self._since = {}  # panel index -> when it first went dirty (oldest gets sent first)
        # flips per disc (board coordinates), counted on the send path, dry-run included
        self.flips = array("I", bytes(4 * self.width * self.height))
        self.flips_changed = False
        # pixels[y][x]: True = white, False = black
        self.pixels = [[False] * self.width for _ in range(self.height)]
        self.version = 0
        self.listeners = []  # called (without the lock held) whenever the picture changes
        self._lock = threading.Lock()
        self._dirty = set(range(len(self.panels)))  # push the full (black) frame on startup
        self._wake = threading.Event()
        self._sockets = {}
        self._wake.set()
        threading.Thread(target=self._flush_loop, daemon=True).start()

    # ---- framebuffer -----------------------------------------------------

    def _check(self, x, y):
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"({x}, {y}) is outside the {self.width}x{self.height} board")

    def get(self, x, y):
        self._check(x, y)
        return self.pixels[y][x]

    def set_dots(self, dots):
        """dots: iterable of (x, y, value) where value is True (white), False (black) or None (toggle)."""
        dots = list(dots)
        for x, y, _ in dots:
            self._check(x, y)
        changed = False
        with self._lock:
            for x, y, value in dots:
                new = (not self.pixels[y][x]) if value is None else value
                if new != self.pixels[y][x]:
                    self.pixels[y][x] = new
                    self._mark(x, y)
                    changed = True
            if changed:
                self.version += 1
        if changed:
            self._wake.set()
            for fn in self.listeners:
                fn()

    def set_canvas(self, canvas):
        """Show a canvas.Canvas (same size as the board)."""
        w = self.width
        self.set_dots((i % w, i // w, bool(v)) for i, v in enumerate(canvas.px))

    def check_frame(self, rows):
        if len(rows) != self.height or any(len(r) != self.width for r in rows):
            raise ValueError(f"frame must be {self.height} rows of {self.width} characters")

    def set_frame(self, rows):
        """rows: list of `height` strings of `width` chars, '1' = white, '0' = black."""
        self.check_frame(rows)
        self.set_dots((x, y, c == "1") for y, r in enumerate(rows) for x, c in enumerate(r))

    def fill(self, value):
        self.set_dots((x, y, value) for y in range(self.height) for x in range(self.width))

    def invert_all(self):
        self.set_dots((x, y, None) for y in range(self.height) for x in range(self.width))

    def packed(self):
        """(version, bytes): the dots packed 8 per byte, row-major, most significant bit first."""
        with self._lock:
            out = bytearray((self.width * self.height + 7) // 8)
            i = 0
            for row in self.pixels:
                for v in row:
                    if v:
                        out[i >> 3] |= 0x80 >> (i & 7)
                    i += 1
            return self.version, bytes(out)

    def rows(self):
        with self._lock:
            return ["".join("1" if v else "0" for v in row) for row in self.pixels]

    def _mark(self, x, y):
        i = self._panel_lookup.get((x // self.pw, y // self.ph))
        if i is not None and i not in self._dirty:
            self._dirty.add(i)
            self._since.setdefault(i, time.monotonic())

    def set_panels(self, panels):
        """Swap in a new panel mapping (same board size) and resend everything."""
        new = parse_panels(panels)
        if (self.pw * (max(p.col for p in new) + 1), self.ph * (max(p.row for p in new) + 1)) != (self.width, self.height):
            raise ValueError("the new mapping changes the board size; restart the server to apply it")
        with self._lock:
            self.panels = new
            self._panel_lookup = {(p.col, p.row): i for i, p in enumerate(new)}
            self._sent = {}
            self._since = {}
            self._dirty = set(range(len(new)))
            for s in self._sockets.values():
                s.close()
            self._sockets = {}
        self._wake.set()

    # ---- hardware --------------------------------------------------------

    def _frame_for(self, panel):
        data = bytearray()
        flip = panel.rotate == 180
        for cx in range(self.pw):
            byte = 0
            for cy in range(self.ph):
                sx, sy = (self.pw - 1 - cx, self.ph - 1 - cy) if flip else (cx, cy)
                on = self.pixels[panel.row * self.ph + sy][panel.col * self.pw + sx]
                if on != self.invert:
                    byte |= 1 << cy
            data.append(byte)
        return bytes([0x80, CMD_WRITE_REFRESH, panel.address]) + bytes(data) + bytes([0x8F])

    def _send(self, panel, frame):
        if self.dry_run:
            log.debug("dry-run %s:%d addr %d: %s", panel.ip, panel.port, panel.address, frame.hex())
            return
        key = (panel.ip, panel.port)
        sock = self._sockets.get(key)
        if sock is None:
            if self.transport == "udp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.connect(key)
            else:
                sock = socket.create_connection(key, timeout=3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("connected to %s:%d", *key)
            self._sockets[key] = sock
        try:
            sock.sendall(frame)
        except OSError:
            self._sockets.pop(key, None)
            sock.close()
            raise

    def _flip_cost(self, i, frame):
        old = self._sent.get(i)
        if old is None:
            return self.pw * self.ph // 2  # state unknown: assume half the discs move
        return sum(bin(a ^ b).count("1") for a, b in zip(old[3:-1], frame[3:-1]))

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

    def _flush_loop(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            time.sleep(0.02)  # coalesce rapid updates (e.g. dragging on the web page)
            with self._lock:
                dirty, self._dirty = self._dirty, set()
                order = sorted(dirty, key=lambda i: self._since.get(i, 0))
                panels = self.panels
                frames = [(i, self._frame_for(panels[i])) for i in order]
            rate = float(self.max_flips_per_sec or 0)
            now = time.monotonic()
            if rate > 0:
                self._tokens = min(rate, self._tokens + (now - self._tokens_at) * rate)
            self._tokens_at = now
            failed, deferred, needed = set(), set(), 0
            for i, frame in frames:
                if frame == self._sent.get(i):
                    self._since.pop(i, None)
                    continue
                panel = panels[i]
                if rate > 0:
                    cost = self._flip_cost(i, frame)
                    if self._tokens < min(cost, rate):  # over budget: try again with the newest picture later
                        deferred.add(i)
                        needed = max(needed, min(cost, rate))
                        continue
                    self._tokens -= cost
                if any((panels[j].ip, panels[j].port) == (panel.ip, panel.port) for j in failed):
                    failed.add(i)  # bus already unreachable this round; don't wait on another timeout
                    continue
                try:
                    self._send(panel, frame)
                except OSError as e:
                    log.warning("send to %s:%d (addr %d) failed: %s", panel.ip, panel.port, panel.address, e)
                    failed.add(i)
                    continue
                self._count_flips(i, panel, frame)
                self._since.pop(i, None)
            if failed or deferred:
                with self._lock:
                    self._dirty |= failed | deferred
            if failed:
                time.sleep(2)  # retry later
                self._wake.set()
            elif deferred:
                time.sleep(min(1.0, max(0.05, (needed - self._tokens) / rate)))
                self._wake.set()
