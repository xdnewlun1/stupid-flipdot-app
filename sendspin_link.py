"""Optional link to Music Assistant over the Sendspin protocol (metadata + visualizer roles).

The board advertises itself on the network (mDNS); Music Assistant connects to it and streams
song info plus visualizer data (loudness, spectrum, beats) for whatever is playing, each with a
timestamp so the dots line up with what you hear. No audio is played or captured here.

Needs `pip install -r requirements-sendspin.txt` (Python 3.12+). The aiosendspin version must match
the one Music Assistant uses — the encryption handshake changed between versions (a 10.x board can't
talk to Music Assistant's 9.1.1 server). For Music Assistant 2.7.x install requirements-sendspin-ma27.txt
instead (aiosendspin 2.0.0); sendspin_legacy.py then handles the link. Without it everything else still works and the music
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
# Music Assistant 2.7.x pins the alpha protocol, aiosendspin 2.0.0 (requirements-sendspin-ma27.txt);
# with that installed, start_link() uses sendspin_legacy.LegacySendspinLink instead.
MA27_AIOSENDSPIN = "2.0.0"

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
    VERSION = _pkg_version("aiosendspin")
except Exception:
    VERSION = None

LEGACY = bool(VERSION) and VERSION.split(".")[0] == MA27_AIOSENDSPIN.split(".")[0]
if not AVAILABLE and LEGACY:
    try:
        from aiosendspin.client import SendspinClient as _LegacyClient  # noqa: F401
        AVAILABLE = True
    except ImportError:
        pass
if not AVAILABLE:
    VERSION = None

BINS = 16
TYPES = ["loudness", "beat", "spectrum", "peak"]
UNIT = 65535.0  # uint16 scale: -60 dB .. 0 dB
REF_DT = 0.05  # frame spacing the signal smoothing in _apply() was tuned at (20 frames a second)


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
    protocol = "Sendspin with encryption (Music Assistant 2.8+)"

    def __init__(self, data_dir, name="Flipdot board", port=8928, on_pair_code=None, advertise=True, server_url="auto"):
        self.dir = Path(data_dir) / "sendspin"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name, self.port, self.advertise = name, port, advertise
        # "auto": find Music Assistant's Sendspin server on the network and dial it (falls back to
        # waiting for Music Assistant to find us); a ws:// URL: dial that; "" : only wait to be found
        self.server_url = server_url
        self.mode = None
        self.on_pair_code = on_pair_code  # called with "123-456" / None from the link thread
        self.status = "off" if AVAILABLE else "not installed (pip install aiosendspin)"
        self.server_name = None
        self.pair_code = None
        self.client = None
        self.loop = None
        self.lock = threading.Lock()
        self._frames = collections.deque()  # (play_time_us, frame), oldest first
        self.delay_us = 0  # frames come due this long after their audio plays (a fixed delay for the board)
        self.frame_dt = None  # seconds between frames if not REF_DT: the smoothing below is scaled to it
        self._bass_hist = collections.deque(maxlen=8)
        self.raw_loudness = 0.0  # the latest frame's loudness, unsmoothed (0..1)
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
        url = self.server_url
        if url == "auto":
            self.status = "looking for Music Assistant on the network"
            found = await asyncio.get_running_loop().run_in_executor(None, discover_servers, 4.0)
            url = found[0]["url"] if found else ""
            if found:
                log.info("Sendspin: found %s at %s", found[0]["name"], url)
        if url:
            self.mode, self.server_url_used = "dial", url
            await self._dial_forever(url)
            return
        # Server-initiated: advertise on mDNS and wait for Music Assistant to connect to us.
        self.mode = "listen"
        listener = ClientListener(self.identity.peer_id, self._on_connection, port=self.port,
                                  client_name=self.name, advertise_mdns=self.advertise)
        await listener.start()
        self.status = "waiting for Music Assistant"
        log.info("Sendspin: advertising as %r on port %d", self.name, self.port)
        await asyncio.Event().wait()  # run forever

    async def _dial_forever(self, url):
        """Client-initiated: connect to Music Assistant's Sendspin server, reconnecting as needed."""
        delay = 2
        while True:
            client = self._make_client()
            done = asyncio.Event()
            client.add_disconnect_listener(done.set)
            self.status = f"connecting to {url}"
            try:
                await client.connect(url)
                self.client, delay = client, 2
                self.last_error = None
                await done.wait()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                log.warning("Sendspin: couldn't connect to %s: %s", url, self.last_error)
            finally:
                if self.client is client:
                    self.client = None
                    self.playing = None
            self.status = f"reconnecting to {url}"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _make_client(self):
        kwargs = {"pairing_store": self.store, "visualizer_support": self.vis_support,
                  "pairing_support": self._pairing_support()}
        if self.vis_state is not None and "visualizer_state" in inspect.signature(SendspinClient).parameters:
            kwargs["visualizer_state"] = self.vis_state
        client = SendspinClient(self.identity, self.name, [Roles.METADATA, Roles.VISUALIZER], **kwargs)
        client.add_visualizer_listener(lambda frames: self._on_frames(client, frames))
        client.add_metadata_listener(self._on_metadata)
        client.add_group_update_listener(self._on_group)
        return client

    async def _on_connection(self, ws):
        client = self._make_client()
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
        peak = None  # the loudest frame (unsmoothed) that came due in this call: a click is never smoothed away
        if client is not None:
            now_us = client.now_us() - self.delay_us
            with self.lock:
                while self._frames and self._frames[0][0] <= now_us:
                    _, f = self._frames.popleft()
                    self._apply(f, now)
                    if f.loudness is not None:
                        self.raw_loudness = f.loudness / UNIT
                        peak = max(peak or 0.0, self.raw_loudness)
        fresh = now - self.updated < 1.0
        return {
            "live": fresh, "loudness": self.loudness if fresh else 0.0,
            "raw_peak": (self.raw_loudness if peak is None else peak) if fresh else 0.0,
            "spectrum": list(self.spectrum) if fresh else [0.0] * BINS,
            "beat_age": now - self.beat_at, "downbeat": self.downbeat, "peak": self.peak,
            "playing": self.playing, "metadata": self.metadata,
        }

    def _apply(self, f, now):
        self.updated = now
        # the smoothing was tuned at one frame per REF_DT; at another frame rate the same time constants hold
        r = 1.0 if not self.frame_dt else self.frame_dt / REF_DT
        if f.loudness is not None:
            keep = 0.3 ** r
            self.loudness = self.loudness * keep + (f.loudness / UNIT) * (1 - keep)
        if f.spectrum:
            vals = [v / UNIT for v in f.spectrum[:BINS]] + [0.0] * max(0, BINS - len(f.spectrum))
            keep = 0.35 ** r
            self.spectrum = [a * keep + b * (1 - keep) for a, b in zip(self.spectrum, vals)]
            # our own beat detection, for servers that don't send beats: a sharp rise in bass energy
            # compared with both about REF_DT ago and the recent average (works at any volume)
            bass = sum(vals[:3]) / 3
            back = max(1, int(1 / r + 0.5))  # frames in REF_DT
            self._bass_hist.append(bass)
            if len(self._bass_hist) > back:
                self._prev_bass = self._bass_hist[-1 - back]
            rise = bass - self._prev_bass
            if (now - self._last_beat_frame > 2.0 and rise > max(0.04, self._bass_avg * 0.5)
                    and bass > self._bass_avg * 1.2 and now - self.beat_at > 0.25):
                self.beat_at, self.downbeat = now, False
            keep = 0.92 ** r
            self._bass_avg = bass if not self._bass_avg else self._bass_avg * keep + bass * (1 - keep)
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
        return {"available": AVAILABLE, "version": VERSION, "warning": version_warning(),
                "protocol": self.protocol, "mode": self.mode,
                "server_url": getattr(self, "server_url_used", None),
                "status": self.current_status(), "server": self.server_name,
                "pair_code": self.pair_code, "connected": self.connected(), "port": self.port,
                "receiving": time.time() - self.updated < 2.0, "metadata": self.metadata}


def version_warning():
    """None when the installed aiosendspin speaks a protocol some Music Assistant version uses."""
    if not VERSION or LEGACY or VERSION.split(".")[0] == MA_AIOSENDSPIN.split(".")[0]:
        return None
    return (f"aiosendspin {VERSION} is installed but Music Assistant uses {MA_AIOSENDSPIN} "
            f"({MA27_AIOSENDSPIN} for Music Assistant 2.7.x); they can't connect — run: "
            f"pip install -r requirements-sendspin.txt (or requirements-sendspin-ma27.txt)")


def discover_servers(timeout=3.0):
    """Sendspin servers (e.g. Music Assistant) advertising on the local network via mDNS."""
    import socket
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found = {}

    class _L:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if not info:
                return
            addrs = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
            if addrs:
                path = (info.properties.get(b"path") or b"/sendspin").decode()
                found[name] = {"name": name.split(".")[0], "url": f"ws://{addrs[0]}:{info.port}{path}"}

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_sendspin-server._tcp.local.", _L())
        time.sleep(timeout)
    finally:
        zc.close()
    return list(found.values())


_link = None


def get_link():
    return _link


def start_link(data_dir, **kw):
    global _link
    if _link is None:
        if LEGACY:  # Music Assistant 2.7.x
            from sendspin_legacy import LegacySendspinLink
            _link = LegacySendspinLink(data_dir, **kw)
        else:
            _link = SendspinLink(data_dir, **kw)
    _link.start()
    return _link
