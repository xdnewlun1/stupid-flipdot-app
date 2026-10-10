"""Optional live audio for the music visualizer from a Snapcast server (Music Assistant's Snapcast provider).

Runs the stock `snapclient` binary (`sudo apt install snapclient`) with its file player writing raw
PCM to a pipe instead of a sound card, so nothing comes out of the Pi's speakers. snapclient does the
work that's hard to get right: the Snapcast protocol, clock sync with the server, every codec (flac,
pcm, ogg, opus) and reconnecting. It writes each 10 ms of audio at the moment it's due to play.

The board shows the music a fixed delay (delay_ms) after the speakers play it, so the other speakers in
the group can be delayed by the same amount and everything lines up. For that every analysis frame
(sendspin_legacy._Analyzer: loudness + 16 spectrum bands, pure Python, 50 a second) is stamped with when
its audio plays, from a sample clock: sample n plays at anchor + n / rate, where the anchor is the earliest
any chunk has arrived relative to its sample position over the last few seconds (snapclient writes each
chunk when it's due, so pipe and scheduling hiccups only ever make it later). A frame comes due delay_ms
after its audio plays, however long the reading and the analysis took; a constant error in the anchor
just moves everything by the same amount, which the speaker delay absorbs.

In Music Assistant the board is a Snapcast player. Its id is the --hostID kept in data/snapcast/client_id;
its name is set over the server's JSON-RPC control port (Client.SetName), since snapclient itself only
reports the Pi's hostname. snapclient is always given an explicit host: without one it browses mDNS.
"""

import atexit
import collections
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import types
import uuid
from pathlib import Path
from urllib.parse import urlparse

from sendspin_legacy import _Analyzer
from sendspin_link import BINS, SendspinLink

log = logging.getLogger("snapcast")

FPS = 50            # analysis frames per second: a 20 ms step, so the 21 ms FFT windows cover all the audio
DELAY_MS = 150      # default board delay behind the speakers (Settings → Music); must cover reading + analysis
CHUNK_S = 0.01      # snapclient writes this much audio at a time, when its start is due to play
ANCHOR_WINDOW = 4.0  # seconds of arrivals the sample clock's anchor is the earliest of
STALL_S = 0.25      # arrivals this much later than the clock for a while: snapclient restarted its stream
SILENCE = 0.02      # loudness (0..1 on the -60..0 dB scale) below this counts as silence
DEFAULT_FORMAT = "48000:16:2"  # what Music Assistant's Snapcast provider always streams

_RE_FORMAT = re.compile(r"\(Player\) Sampleformat: (\d+):(\d+):(\d+)|Codec: (\w+), sampleformat: (\d+):(\d+):(\d+)")
_RE_SETTINGS = re.compile(r"ServerSettings - buffer: (\d+), latency: (-?\d+), volume: (\d+), muted: (\d+)")
_RE_DIFF = re.compile(r"diff to server \[ms\]: (-?[\d.]+)")


def binary():
    return shutil.which("snapclient")


def snapclient_version(path=None):
    path = path or binary()
    if not path:
        return None
    try:
        out = subprocess.run([path, "-v"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"v(\d+\.\d+\.\d+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def resolve_host(host, music_assistant_url=""):
    """'auto' (or empty) means the host in the Music Assistant URL, where its built-in Snapserver runs."""
    host = (host or "").strip()
    if host and host != "auto":
        return host
    return urlparse(music_assistant_url or "").hostname or ""


class _Clock:
    """Frames are stamped with wall-clock time: snapclient hands us audio as it's due to play."""

    @staticmethod
    def now_us():
        return time.time() * 1e6


class SnapcastLink(SendspinLink):
    protocol = "Snapcast (snapclient)"

    def __init__(self, data_dir, host="", port=1704, control_port=1705, name="Flipdot board", binary_path=None,
                 delay_ms=DELAY_MS):
        super().__init__(data_dir, name=name)
        self.set_delay(delay_ms)
        self.dir = Path(data_dir) / "snapcast"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.host, self.port, self.control_port = host, int(port or 1704), int(control_port or 1705)
        self.binary = binary_path or binary()
        self.client_id = self._client_id()
        self.analyzer = _Analyzer()
        self._clock = _Clock()
        self.proc = None
        self._stopping = threading.Event()
        self._started = False
        self.version = None
        self.codec = self.sample_format = None
        self.buffer_ms = self.latency_ms = self.server_diff_ms = None
        self.server_muted = False
        self.is_connected = False
        self.bytes_at = 0.0     # time.time() when audio bytes last arrived (silence included)
        self.audible_at = 0.0   # ... and when they last weren't silent
        self.name_note = None
        self.status = "off" if self.binary else "snapclient isn't installed (sudo apt install snapclient)"
        self._configure(DEFAULT_FORMAT)

    def _client_id(self):
        f = self.dir / "client_id"
        if f.exists():
            return f.read_text().strip()
        cid = f"flipdot-{uuid.uuid4().hex[:12]}"
        f.write_text(cid)
        return cid

    def _configure(self, fmt):
        rate, bits, ch = (int(x) for x in fmt.split(":"))
        self.sample_format = fmt
        self.frame_bytes = bits // 8 * ch
        self.analyzer.configure(types.SimpleNamespace(sample_rate=rate, channels=ch, bit_depth=bits))
        self.analyzer.hop = max(1, round(self.analyzer.rate / FPS))
        self.frame_dt = self.analyzer.hop / self.analyzer.rate
        self.rate = rate
        self._reset_clock()

    def next_due(self, after):
        """Wall time (s) the first analysis frame comes due at or after `after` (None if none is queued): the
        visualizer draws exactly then, so what it shows is always the same time behind the speakers."""
        after_us = after * 1e6 - self.delay_us
        with self.lock:
            at = next((t for t, _ in self._frames if t >= after_us), None)
        return None if at is None else (at + self.delay_us) / 1e6

    def set_delay(self, delay_ms):
        self.delay_us = max(0, min(2000, int(delay_ms if delay_ms is not None else DELAY_MS))) * 1000

    def _reset_clock(self):
        self._pos = 0  # sample frames (at the stream's rate) read since the clock started
        self._arrivals = collections.deque()  # (arrival time, implied anchor), last ANCHOR_WINDOW s
        self._late = 0  # reads in a row well behind the clock
        self.anchor = None

    def _tick_clock(self, now, n):
        """n sample frames just arrived at `now`; returns the play time of the first of them."""
        start = self._pos
        self._pos += n
        # the last chunk in this read started at _pos - chunk and was written when due: it plays no later than now
        implied = now - max(0.0, self._pos / self.rate - CHUNK_S)
        a = self._arrivals
        if self.anchor is not None and implied > self.anchor + STALL_S:
            self._late += 1
            if self._late >= 5:  # a stall or restart, not a hiccup: start the clock over from here
                a.clear()
                self._late = 0
        else:
            self._late = 0
        a.append((now, implied))
        while a and a[0][0] < now - ANCHOR_WINDOW:
            a.popleft()
        self.anchor = min(v for _, v in a)
        return self.anchor + start / self.rate

    # ---- lifecycle --------------------------------------------------------------------------

    def start(self):
        if not self.binary or self._started:
            return
        if not self.host:
            self.status = "no Snapcast server set (enter its address, or set the Music Assistant URL above)"
            return
        self._started = True
        self.version = snapclient_version(self.binary)
        self.status = "starting"
        threading.Thread(target=self._run_forever, daemon=True, name="snapcast").start()

    def stop(self):
        self._stopping.set()
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.status = "off"

    def _args(self):
        return [self.binary, "-h", self.host, "-p", str(self.port), "--hostID", self.client_id,
                "--player", "file:filename=stdout", "--mixer", "none", "--logsink", "stderr"]

    def _run_forever(self):
        delay = 2
        while not self._stopping.is_set():
            started = time.time()
            try:
                self.proc = subprocess.Popen(self._args(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, bufsize=0)
            except OSError as e:
                self.last_error = f"couldn't run snapclient: {e}"
                self.status = "error"
                return
            log.info("Snapcast: snapclient %s started for %s:%d as %s", self.version or "", self.host, self.port,
                     self.client_id)
            self.status = f"connecting to {self.host}:{self.port}"
            threading.Thread(target=self._read_log, args=(self.proc,), daemon=True, name="snapcast-log").start()
            self._reset_clock()  # a new process: its stream starts a new timeline
            self.analyzer.reset()
            self._read_audio(self.proc)  # returns when snapclient exits
            code = self.proc.wait()
            self.is_connected = False
            if self._stopping.is_set():
                break
            self.last_error = self.last_error or f"snapclient exited with code {code}"
            log.warning("Snapcast: %s", self.last_error)
            if time.time() - started > 60:
                delay = 2
            self.status = f"restarting snapclient in {delay} s"
            self._stopping.wait(delay)
            delay = min(delay * 2, 60)

    # ---- snapclient output (link threads) ----------------------------------------------------

    def _read_log(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            if "Connected to " in line:
                self.is_connected, self.last_error = True, None
                self.status = f"connected to {self.host}:{self.port}"
                threading.Thread(target=self._set_name, daemon=True).start()
            elif "Failed to connect" in line or "Failed to resolve" in line or "Error reading message" in line:
                self.is_connected = False
                self.last_error = line.split(") ", 1)[-1][:200]
                self.status = f"reconnecting to {self.host}:{self.port}"
            elif m := _RE_SETTINGS.search(line):
                self.buffer_ms, self.latency_ms = int(m[1]), int(m[2])
                self.server_muted = m[4] == "1"
            elif m := _RE_DIFF.search(line):
                self.server_diff_ms = float(m[1])
            elif m := _RE_FORMAT.search(line):
                if m[4]:
                    self.codec = m[4]
                fmt = ":".join(m.group(1, 2, 3) if m[1] else m.group(5, 6, 7))
                if fmt != self.sample_format:
                    self._configure(fmt)
            if "[Error]" in line or "[Warning]" in line:
                log.debug("snapclient: %s", line)

    def _read_audio(self, proc):
        fd = proc.stdout.fileno()
        rest = b""
        while True:
            data = os.read(fd, 16384)
            if not data:
                return
            now = time.time()
            self.bytes_at = now
            data = rest + data
            usable = len(data) - len(data) % self.frame_bytes
            data, rest = data[:usable], data[usable:]
            if self.analyzer.bit_depth != 16:
                self.last_error = f"unsupported sample format {self.sample_format} (16-bit only)"
                continue
            # stamp the audio with when it plays (frames come due delay_ms later, see signal())
            plays_at = self._tick_clock(now, len(data) // self.frame_bytes)
            frames = self.analyzer.feed(int(plays_at * 1e6), data)
            if not frames:
                continue
            with self.lock:
                for at, f in frames:
                    self._frames.append((at, f))
                while len(self._frames) > 400:
                    self._frames.popleft()
            if max(f.loudness for _, f in frames) / 65535 > SILENCE:
                self.audible_at = now

    def _set_name(self):
        """Name the board in Music Assistant via the Snapserver control port (JSON-RPC over TCP)."""
        time.sleep(1.0)  # let the server process the client's Hello first
        try:
            with socket.create_connection((self.host, self.control_port), timeout=5) as s:
                f = s.makefile("rwb")

                def call(i, method, params):
                    f.write(json.dumps({"id": i, "jsonrpc": "2.0", "method": method, "params": params}).encode() + b"\r\n")
                    f.flush()
                    while True:  # skip notifications until our reply
                        msg = json.loads(f.readline() or b"{}")
                        if not msg or msg.get("id") == i:
                            return msg

                st = call(1, "Client.GetStatus", {"id": self.client_id})
                current = (((st.get("result") or {}).get("client") or {}).get("config") or {}).get("name")
                if current != self.name:
                    call(2, "Client.SetName", {"id": self.client_id, "name": self.name})
                    log.info("Snapcast: named this client %r on the server", self.name)
                self.name_note = None
        except Exception as e:
            self.name_note = (f"couldn't set the name over the control port {self.host}:{self.control_port} "
                              f"({type(e).__name__}); rename the player in Music Assistant if you like")

    # ---- for the visualizer (render thread) ------------------------------------------------

    def connected(self):
        return self.is_connected and self.proc is not None and self.proc.poll() is None

    def audible(self):
        return time.time() - self.audible_at < 3.0

    def signal(self):
        s = super().signal()
        # snapclient writes silence while nothing plays to this group; treat that as "not live" so the
        # visualizer falls back to Music Assistant's play state instead of showing an empty board
        s["live"] = s["live"] and self.audible()
        s["playing"] = self.audible() or None
        return s

    def current_status(self):
        if self.connected():
            if self.audible():
                return f"connected to {self.host}:{self.port} · receiving audio"
            if time.time() - self.bytes_at < 2:
                return f"connected to {self.host}:{self.port} · silence (nothing playing to this player)"
            return f"connected to {self.host}:{self.port} · waiting for audio"
        if self.last_error and self.status != "off":
            return f"{self.status} (last error: {self.last_error})"
        return self.status

    def describe(self):
        return {"available": bool(self.binary), "version": self.version, "protocol": self.protocol,
                "status": self.current_status(), "connected": self.connected(),
                "receiving": time.time() - self.bytes_at < 2.0, "audible": self.audible(),
                "host": self.host, "port": self.port, "control_port": self.control_port,
                "client_id": self.client_id, "name": self.name, "name_note": self.name_note,
                "codec": self.codec, "sample_format": self.sample_format, "buffer_ms": self.buffer_ms,
                "latency_ms": self.latency_ms, "muted": self.server_muted, "metadata": {}}


_link = None


def get_link():
    return _link


def start_link(data_dir, **kw):
    """(Re)start the link with these settings; stops the previous snapclient first."""
    global _link
    stop_link()
    _link = SnapcastLink(data_dir, **kw)
    _link.start()
    return _link


def stop_link():
    global _link
    if _link is not None:
        _link.stop()
        _link = None


atexit.register(stop_link)
