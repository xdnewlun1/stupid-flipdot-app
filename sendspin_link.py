"""Optional link to Music Assistant over the Sendspin protocol (metadata + visualizer roles).

The board advertises itself on the network (mDNS); Music Assistant connects to it and streams
song info plus visualizer data (loudness, spectrum, beats) for whatever is playing, each with a
timestamp so the dots line up with what you hear. No audio is played or captured here.

Needs `pip install -r requirements-sendspin.txt` (Python 3.12+). The aiosendspin version must match
the one Music Assistant uses — the encryption handshake changed between versions (a 10.x board can't
talk to Music Assistant's 9.1.1 server). Without it everything else still works and the music
visualizer falls back to a generated beat. Tested by tests/sendspin_e2e.py.
"""

import asyncio
import collections
import inspect
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("sendspin")

# The aiosendspin version Music Assistant pins (music-assistant/server, providers/sendspin/manifest.json).
# Keep requirements-sendspin.txt in step with it.
MA_AIOSENDSPIN = "9.1.1"

try:
    from aiosendspin.client import ClientListener, PairingSupport, SendspinClient
    from aiosendspin.models.types import PlaybackStateType, Roles
    from aiosendspin.models.visualizer import ClientHelloVisualizerSpectrum, ClientHelloVisualizerSupport
    from aiosendspin.noise.keys import Identity
    from aiosendspin.noise.trust_store import FileClientPairingStore
    try:  # 10.x moved the visualizer stream settings out of client/hello into client/state
        from aiosendspin.models.visualizer import VisualizerStatePayload
    except ImportError:
        VisualizerStatePayload = None
    AVAILABLE = True
except ImportError:  # optional dependency
    AVAILABLE = False

try:
    from importlib.metadata import version as _pkg_version
    VERSION = _pkg_version("aiosendspin") if AVAILABLE else None
except Exception:
    VERSION = None

BINS = 16
TYPES = ["loudness", "beat", "spectrum", "peak"]
UNIT = 65535.0  # uint16 scale: -60 dB .. 0 dB


def _defined(value):
    """aiosendspin marks omitted message fields with an UndefinedField placeholder; treat it as None."""
    return None if value is None or type(value).__name__ == "UndefinedField" else value


class _FailureCapture(logging.Handler):
    """aiosendspin reports why an incoming connection failed only at debug level; surface it."""

    def __init__(self, link):
        super().__init__(logging.DEBUG)
        self.link = link

    def emit(self, record):
        msg = record.getMessage()
        if any(w in msg.lower() for w in ("failed", "malformed", "refus", "reject", "abort")):
            self.link.last_error = msg
            log.warning("Music Assistant connection attempt failed: %s", msg)


class SendspinLink:
    def __init__(self, data_dir, name="Flipdot board", port=8928, on_pair_code=None, advertise=True):
        self.dir = Path(data_dir) / "sendspin"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name, self.port, self.advertise = name, port, advertise
        self.on_pair_code = on_pair_code  # called with "123-456" / None from the link thread
        self.status = "off" if AVAILABLE else "not installed (pip install aiosendspin)"
        self.server_name = None
        self.pair_code = None
        self.client = None
        self.loop = None
        self.lock = threading.Lock()
        self._frames = collections.deque()  # (play_time_us, frame), oldest first
        self.metadata = {}
        self.playing = None
        # current signal, updated as frames come due
        self.loudness = 0.0
        self.spectrum = [0.0] * BINS
        self.beat_at = 0.0          # time.time() of the last beat (sent by the server or detected)
        self.downbeat = False
        self.peak = 0.0
        self.updated = 0.0
        self._bass_avg = 0.0
        self._prev_bass = 0.0
        self._last_beat_frame = 0.0
        self._unpaired = None  # desired "unpaired access" setting, applied once the loop runs
        self.last_error = None

    # ---- lifecycle -------------------------------------------------------------------

    def start(self):
        if not AVAILABLE or self.loop is not None:
            return
        for name in ("aiosendspin.client.client", "aiosendspin.client.connection", "aiosendspin.client.listener"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.DEBUG)
            lg.propagate = False  # keep the library's debug chatter out of the main log
            lg.addHandler(_FailureCapture(self))
        self.status = "starting"
        threading.Thread(target=self._thread, daemon=True, name="sendspin").start()

    def _thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            log.exception("Sendspin link stopped")
            self.status = f"error: {e}"

    def _identity(self):
        key = self.dir / "identity.key"
        if key.exists():
            return Identity.from_private_bytes(key.read_bytes())
        ident = Identity.generate()
        key.write_bytes(ident.private_bytes)
        key.chmod(0o600)
        return ident

    async def _main(self):
        self.identity = self._identity()
        self.store = await FileClientPairingStore.open(self.dir / "pairing.json")
        if self._unpaired is not None:
            await self._apply_unpaired(self._unpaired)
        spectrum = ClientHelloVisualizerSpectrum(n_disp_bins=BINS, scale="log", f_min=40, f_max=8000)
        if VisualizerStatePayload is not None:  # aiosendspin 10.x
            self.vis_support = ClientHelloVisualizerSupport(buffer_capacity=256 * 1024)
            self.vis_state = VisualizerStatePayload(types=list(TYPES), rate_max=30, spectrum=spectrum)
        else:  # aiosendspin 9.x (what Music Assistant uses): everything goes in client/hello
            self.vis_support = ClientHelloVisualizerSupport(buffer_capacity=256 * 1024, rate_max=30,
                                                            types=list(TYPES), spectrum=spectrum)
            self.vis_state = None
        listener = ClientListener(self.identity.peer_id, self._on_connection, port=self.port,
                                  client_name=self.name, advertise_mdns=self.advertise)
        await listener.start()
        self.status = "waiting for Music Assistant"
        log.info("Sendspin: advertising as %r on port %d", self.name, self.port)
        await asyncio.Event().wait()  # run forever

    async def _on_connection(self, ws):
        kwargs = {"pairing_store": self.store, "visualizer_support": self.vis_support,
                  "pairing_support": self._pairing_support()}
        if self.vis_state is not None and "visualizer_state" in inspect.signature(SendspinClient).parameters:
            kwargs["visualizer_state"] = self.vis_state
        client = SendspinClient(self.identity, self.name, [Roles.METADATA, Roles.VISUALIZER], **kwargs)
        client.add_visualizer_listener(lambda frames: self._on_frames(client, frames))
        client.add_metadata_listener(self._on_metadata)
        client.add_group_update_listener(self._on_group)
        self.client = client
        try:
            await client.attach_websocket(ws)  # serves the connection until it closes
        finally:
            if self.client is client:
                self.client = None
                self.playing = None
                self.pair_code = None

    # ---- pairing ------------------------------------------------------------------------

    def _pairing_support(self):
        """Show pairing codes on the board. 10.x calls it a pairing code, 9.x a PIN."""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PairingSupport)}
        if "pairing_code_display" in fields:  # aiosendspin 10.x
            return PairingSupport(pairing_code_display=self._show_code)
        async def show_pin(pin):  # aiosendspin 9.x
            await self._show_code(pin, grouped=pin)
        # no fixed PIN printed on this device, so only offer the PIN it displays
        return PairingSupport(pin_display=show_pin, offer_static_pin=False)

    async def _show_code(self, code, *, grouped=None):
        self.pair_code = grouped
        self.status = f"pairing — enter {grouped} in Music Assistant" if grouped else self.status
        if self.on_pair_code:
            self.on_pair_code(grouped)

    def open_pairing_window(self):
        """The operator gesture: allow pairing for a short while (Settings > Pair)."""
        if self.loop and self.client:
            self.loop.call_soon_threadsafe(self.client.open_pairing_window)
            return True
        return False

    def set_unpaired_access(self, enabled):
        """Allow Music Assistant to connect without pairing (fine on a trusted home network)."""
        self._unpaired = enabled
        if self.loop and getattr(self, "store", None):
            asyncio.run_coroutine_threadsafe(self._apply_unpaired(enabled), self.loop)

    async def _apply_unpaired(self, enabled):
        if self.client and hasattr(self.client, "set_unpaired_access"):  # 10.x
            await self.client.set_unpaired_access(enabled=enabled)
        else:  # 9.x, or no connection yet: stored, and advertised in the next client/hello
            from dataclasses import replace
            cfg = await self.store.get_pairing_config()
            await self.store.store_pairing_config(replace(cfg, unpaired_access_enabled=enabled))

    # ---- incoming data (link thread) --------------------------------------------------------

    def _on_frames(self, client, frames):
        with self.lock:
            for f in frames:
                try:
                    at = client.compute_play_time(f.timestamp_us)
                except Exception:
                    continue  # clock not synchronised yet
                self._frames.append((at, f))
            while len(self._frames) > 4000:
                self._frames.popleft()
        self._clock = client

    def _on_metadata(self, state):
        md = _defined(getattr(state, "metadata", None) if state is not None else None)
        if md is None or not hasattr(md, "title"):
            self.metadata = {}
            return
        prog = _defined(getattr(md, "progress", None))
        if prog is not None and not hasattr(prog, "track_progress"):
            prog = None
        self.metadata = {
            "title": _defined(md.title) or "", "artist": _defined(md.artist) or "", "album": _defined(md.album) or "",
            "artwork_url": _defined(md.artwork_url),
            "progress_ms": prog.track_progress if prog else None,
            "duration_ms": prog.track_duration if prog else None,
            "speed": prog.playback_speed if prog else None, "at": time.time(),
        }
        if prog is not None:
            self.playing = prog.playback_speed > 0

    def _on_group(self, update):
        if update.playback_state is not None:
            self.playing = update.playback_state == PlaybackStateType.PLAYING

    # ---- for the visualizer (render thread) -----------------------------------------------

    def connected(self):
        return self.client is not None and self.client.connected

    def signal(self):
        """Advance through frames that are due now; return the current music signal."""
        client = getattr(self, "_clock", None)
        now = time.time()
        if client is not None:
            now_us = client.now_us()
            with self.lock:
                while self._frames and self._frames[0][0] <= now_us:
                    _, f = self._frames.popleft()
                    self._apply(f, now)
        fresh = now - self.updated < 1.0
        return {
            "live": fresh, "loudness": self.loudness if fresh else 0.0,
            "spectrum": list(self.spectrum) if fresh else [0.0] * BINS,
            "beat_age": now - self.beat_at, "downbeat": self.downbeat, "peak": self.peak,
            "playing": self.playing, "metadata": self.metadata,
        }

    def _apply(self, f, now):
        self.updated = now
        if f.loudness is not None:
            self.loudness = self.loudness * 0.3 + (f.loudness / UNIT) * 0.7
        if f.spectrum:
            vals = [v / UNIT for v in f.spectrum[:BINS]] + [0.0] * max(0, BINS - len(f.spectrum))
            self.spectrum = [a * 0.35 + b * 0.65 for a, b in zip(self.spectrum, vals)]
            # our own beat detection, for servers that don't send beats: a sharp rise in bass energy
            # compared with both the previous frame and the recent average (works at any volume)
            bass = sum(vals[:3]) / 3
            rise = bass - self._prev_bass
            if (now - self._last_beat_frame > 2.0 and rise > max(0.04, self._bass_avg * 0.5)
                    and bass > self._bass_avg * 1.2 and now - self.beat_at > 0.25):
                self.beat_at, self.downbeat = now, False
            self._prev_bass = bass
            self._bass_avg = bass if not self._bass_avg else self._bass_avg * 0.92 + bass * 0.08
        if f.is_downbeat is not None:  # a beat from the server's beat tracking
            self.beat_at, self.downbeat = now, bool(f.is_downbeat)
            self._last_beat_frame = now
        if f.peak_strength is not None:
            self.peak = f.peak_strength / 255.0

    def current_status(self):
        if self.connected():
            info = self.client.server_info
            self.server_name = getattr(info, "name", None) or "Music Assistant"
            return f"connected to {self.server_name}"
        if self.pair_code:
            return f"pairing — enter {self.pair_code} in Music Assistant"
        if self.last_error:
            return f"{self.status} (last attempt failed: {self.last_error})"
        return self.status

    def describe(self):
        warning = None
        if VERSION and VERSION.split(".")[0] != MA_AIOSENDSPIN.split(".")[0]:
            warning = (f"aiosendspin {VERSION} is installed but Music Assistant uses {MA_AIOSENDSPIN}; "
                       f"they can't connect — run: pip install -r requirements-sendspin.txt")
        return {"available": AVAILABLE, "version": VERSION, "warning": warning,
                "status": self.current_status(), "server": self.server_name,
                "pair_code": self.pair_code, "connected": self.connected(), "port": self.port,
                "receiving": time.time() - self.updated < 2.0, "metadata": self.metadata}


_link = None


def get_link():
    return _link


def start_link(data_dir, **kw):
    global _link
    if _link is None:
        _link = SendspinLink(data_dir, **kw)
    _link.start()
    return _link
