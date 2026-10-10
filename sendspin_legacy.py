"""Sendspin link for Music Assistant 2.7.x, which speaks the alpha protocol of aiosendspin 2.0.0.

Same interface as sendspin_link.SendspinLink; sendspin_link.start_link() picks this class when
aiosendspin 2.x is installed (pip install -r requirements-sendspin-ma27.txt).

How it differs from the modern link:
  * no encryption or pairing; Music Assistant 2.7 accepts any client on the network
  * the 2.0.0 server never sends visualizer data, so the board joins as a *player*: Music Assistant
    streams it PCM audio (mono, 22.05 kHz), which is analysed here into the same loudness/spectrum
    frames the modern visualizer role delivers, then thrown away. Nothing is played.
  * aiosendspin 2.0.0 has no client-side listener, so the incoming connection (Music Assistant finds
    `_sendspin._tcp` on mDNS and connects to ws://board:8928/sendspin) is served by aiohttp here.

In Music Assistant the board shows up as a Sendspin player. Play to it, or group it with the speaker
that's playing (a Music Assistant player group if that speaker isn't a Sendspin one), and it gets the
song info plus the audio to analyse. Tested by tests/sendspin_legacy_e2e.py.
"""

import array
import asyncio
import cmath
import collections
import logging
import math
import sys
import threading
import time
import uuid

from sendspin_link import BINS, UNIT, SendspinLink, _defined, _FailureCapture, discover_servers

log = logging.getLogger("sendspin")

try:
    from aiohttp import web
    from aiosendspin.client import SendspinClient
    from aiosendspin.models.core import DeviceInfo
    from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
    from aiosendspin.models.types import AudioCodec, PlaybackStateType, PlayerCommand, PlayerStateType, Roles
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

PATH = "/sendspin"
RATE = 22050      # what we ask Music Assistant for; enough for a 40 Hz - 8 kHz spectrum
FPS = 30          # analysis frames per second, like the modern visualizer role's rate_max
N = 512           # FFT size: 43 Hz resolution at 22.05 kHz, so the bass bands are distinct

Frame = collections.namedtuple("Frame", "loudness spectrum is_downbeat peak_strength")


class _Analyzer:
    """PCM -> visualizer frames (loudness + 16 log-spaced spectrum bins, both on the uint16 dB scale).

    Pure Python (no numpy on the Pi): one 512-point FFT per frame.
    """

    def __init__(self):
        self.rate = None
        self.window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (N - 1)) for i in range(N)]
        bits = N.bit_length() - 1
        self.rev = [int(f"{i:0{bits}b}"[::-1], 2) for i in range(N)]
        self.tw = [cmath.exp(-2j * math.pi * k / N) for k in range(N // 2)]
        self.reset()

    def reset(self):
        self.buf = array.array("h", [0] * N)
        self.pending = 0       # samples since the last frame
        self.next_ts = None    # server time (us) of the sample after the last one received

    def configure(self, fmt):
        rate = fmt.sample_rate
        self.channels, self.bit_depth = fmt.channels, fmt.bit_depth
        self.step = max(1, round(rate / RATE)) if rate > 24000 else 1   # crude decimation if we get more
        self.rate = rate / self.step
        self.hop = max(1, round(self.rate / FPS))
        res = self.rate / N
        edges = [40 * (8000 / 40) ** (i / BINS) for i in range(BINS + 1)]
        self.bands = []
        for lo, hi in zip(edges, edges[1:]):
            ks = [k for k in range(1, N // 2) if lo <= k * res < hi]
            self.bands.append(ks or [max(1, min(N // 2 - 1, round((lo * hi) ** 0.5 / res)))])
        self.reset()

    def feed(self, ts_us, payload):
        """Returns [(server_time_us, Frame)] for each frame completed by this chunk."""
        if self.rate is None or self.bit_depth != 16:
            return []
        s = array.array("h")
        s.frombytes(payload[:len(payload) // 2 * 2])
        if sys.byteorder == "big":
            s.byteswap()
        stride = self.channels * self.step   # left channel only when stereo
        if stride > 1:
            s = s[::stride]
        if not s:
            return []
        if self.next_ts is not None and abs(ts_us - self.next_ts) > 50_000:
            self.reset()  # gap or seek
        us_per = 1e6 / self.rate
        out = []
        pos = 0
        while pos < len(s):
            take = min(self.hop - self.pending, len(s) - pos)
            self.buf = self.buf[take:] + s[pos:pos + take]
            pos += take
            self.pending += take
            if self.pending >= self.hop:
                self.pending = 0
                # stamp the frame at the middle of the samples it covers
                out.append((int(ts_us + (pos - N / 2) * us_per), self._frame()))
        self.next_ts = ts_us + len(s) * us_per
        return out

    def _frame(self):
        buf = self.buf
        hop = buf[-self.hop:]
        rms = math.sqrt(sum(v * v for v in hop) / len(hop)) / 32768
        x = [buf[i] * self.window[i] for i in self.rev]
        size = 2
        while size <= N:  # iterative radix-2 FFT
            half, step = size // 2, N // size
            tw = self.tw[::step][:half]
            for start in range(0, N, size):
                for k in range(half):
                    a, b = start + k, start + k + half
                    t = tw[k] * x[b]
                    x[b] = x[a] - t
                    x[a] = x[a] + t
            size *= 2
        norm = 4 / N / 32768  # a full-scale sine reads 1.0 (Hann window gain 0.5)
        spec = [_scale(max(abs(x[k]) for k in ks) * norm) for ks in self.bands]
        return Frame(_scale(rms * 1.4142), spec, None, None)


def _scale(amplitude):
    """0..1 amplitude -> uint16 on the -60..0 dB scale the visualizer role uses."""
    if amplitude <= 0.001:
        return 0
    return int(min(1.0, (20 * math.log10(amplitude) + 60) / 60) * UNIT)


class _Clock:
    """What SendspinLink.signal() needs from a client: the clock frames are timed against."""

    def __init__(self, client):
        self.client = client

    def now_us(self):
        return self.client._now_us()


if AVAILABLE:
    class _Client(SendspinClient):
        async def attach_websocket(self, ws):
            """Run the client handshake over a websocket Music Assistant opened to us; returns when it closes."""
            done = asyncio.Event()
            self.add_disconnect_listener(done.set)
            self._server_hello_event = asyncio.Event()
            self._ws, self._connected = ws, True
            self._reader_task = self._loop.create_task(self._reader_loop())
            await self._send_client_hello()
            try:
                await asyncio.wait_for(self._server_hello_event.wait(), timeout=10)
            except TimeoutError:
                await self.disconnect()
                raise TimeoutError("timed out waiting for server/hello") from None
            await self.send_player_state(state=PlayerStateType.SYNCHRONIZED,
                                         volume=self._initial_volume, muted=self._initial_muted)
            await self._send_time_message()
            self._time_task = self._loop.create_task(self._time_sync_loop())
            await done.wait()


class LegacySendspinLink(SendspinLink):
    protocol = "Music Assistant 2.7.x mode (Sendspin alpha, aiosendspin 2.0.0)"

    def __init__(self, data_dir, **kw):
        super().__init__(data_dir, **kw)
        self.status = "off" if AVAILABLE else "not installed (pip install -r requirements-sendspin-ma27.txt)"
        self.analyzer = _Analyzer()
        self.volume, self.muted = 100, False
        self.pair_code = None

    def _client_id(self):
        f = self.dir / "client_id"
        if f.exists():
            return f.read_text().strip()
        cid = f"flipdot-{uuid.uuid4().hex[:12]}"
        f.write_text(cid)
        return cid

    def start(self):
        if not AVAILABLE or self.loop is not None:
            return
        lg = logging.getLogger("aiosendspin.client")
        lg.setLevel(logging.INFO)
        lg.propagate = False
        lg.addHandler(_FailureCapture(self))
        self.status = "starting"
        threading.Thread(target=self._thread, daemon=True, name="sendspin").start()

    async def _main(self):
        self.client_id = self._client_id()
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
        # Server-initiated: advertise _sendspin._tcp and wait for Music Assistant to connect to us.
        self.mode = "listen"
        app = web.Application()
        app.router.add_get(PATH, self._handle_ws)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, None, self.port).start()
        if self.advertise:
            await self._advertise()
        self.status = "waiting for Music Assistant"
        log.info("Sendspin (Music Assistant 2.7 mode): listening as %r on port %d", self.name, self.port)
        await asyncio.Event().wait()

    async def _advertise(self):
        from zeroconf import NonUniqueNameException
        from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
        from aiosendspin.server.server import _get_local_ip
        ip = _get_local_ip()
        if not ip:
            log.warning("Sendspin: no local IP address found; Music Assistant won't find the board")
            return
        self._zc = AsyncZeroconf()
        service = "_sendspin._tcp.local."
        info = AsyncServiceInfo(service, f"{self.client_id}.{service}", server=f"{self.client_id}.local.",
                                parsed_addresses=[ip], port=self.port, properties={"name": self.name, "path": PATH})
        try:
            await self._zc.async_register_service(info)
        except NonUniqueNameException:
            log.error("Sendspin: another device on the network uses the id %s", self.client_id)

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        log.info("Sendspin: Music Assistant connected from %s", request.remote)
        client = self._make_client()
        self.client = client
        try:
            await client.attach_websocket(ws)
            self.last_error = None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("Sendspin: incoming connection failed: %s", self.last_error)
        finally:
            self._lost(client)
            if not ws.closed:
                await ws.close()
        return ws

    async def _dial_forever(self, url):
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
                await client.disconnect()
            finally:
                self._lost(client)
            self.status = f"reconnecting to {url}"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _lost(self, client):
        if self.client is client:
            self.client = None
            self.playing = None
            with self.lock:
                self._frames.clear()

    def _make_client(self):
        support = ClientHelloPlayerSupport(
            # only mono 16-bit PCM at 22.05 kHz: Music Assistant downmixes/resamples, keeping the Pi's work small
            supported_formats=[SupportedAudioFormat(codec=AudioCodec.PCM, channels=1, sample_rate=RATE, bit_depth=16)],
            buffer_capacity=256_000,
            supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE])
        client = _Client(self.client_id, self.name, [Roles.PLAYER, Roles.METADATA],
                         device_info=DeviceInfo(product_name="Flipdot board", manufacturer="flipdisk"),
                         player_support=support, initial_volume=self.volume, initial_muted=self.muted)
        client.add_metadata_listener(self._on_metadata)
        client.add_group_update_listener(self._on_group)
        client.add_stream_start_listener(lambda msg: self._on_stream_start(client, msg))
        client.add_stream_clear_listener(lambda roles: self._clear())
        client.add_audio_chunk_listener(lambda ts, data, fmt: self._on_audio(client, ts, data, fmt))
        client.add_server_command_listener(lambda cmd: self._on_command(client, cmd))
        self._clock = _Clock(client)
        return client

    # ---- incoming data (link thread) ----------------------------------------------------------

    def _on_stream_start(self, client, msg):
        p = msg.payload.player
        if p is not None:
            self.analyzer.configure(p)
            log.info("Sendspin: stream started (%s Hz, %s ch, %s bit)", p.sample_rate, p.channels, p.bit_depth)
        self._clear()

    def _clear(self):
        self.analyzer.reset()
        with self.lock:
            self._frames.clear()

    def _on_audio(self, client, ts_us, data, fmt):
        frames = self.analyzer.feed(ts_us, data)
        if not frames:
            return
        with self.lock:
            for ts, f in frames:
                self._frames.append((client.compute_play_time(ts), f))
            while len(self._frames) > 4000:
                self._frames.popleft()

    def _on_command(self, client, cmd):
        p = cmd.player
        if p is None:
            return
        if p.command == PlayerCommand.VOLUME and p.volume is not None:
            self.volume = p.volume
        elif p.command == PlayerCommand.MUTE and p.mute is not None:
            self.muted = p.mute
        # nothing is played, but report the new state back so Music Assistant's slider sticks
        asyncio.ensure_future(client.send_player_state(state=PlayerStateType.SYNCHRONIZED,
                                                       volume=self.volume, muted=self.muted))

    def _on_metadata(self, state):
        md = getattr(state, "metadata", None)
        if md is None:
            return
        # 2.0.0 sends only the fields that changed; None clears a field
        new = dict(self.metadata) if self.metadata else {"title": "", "artist": "", "album": "", "artwork_url": None,
                                                           "progress_ms": None, "duration_ms": None, "speed": None}
        for key in ("title", "artist", "album", "artwork_url"):
            value = getattr(md, key, None)
            if type(value).__name__ != "UndefinedField":
                new[key] = value if key == "artwork_url" else (value or "")
        prog = getattr(md, "progress", None)
        if type(prog).__name__ != "UndefinedField":
            prog = _defined(prog)
            new.update(progress_ms=prog.track_progress if prog else None, duration_ms=prog.track_duration if prog else None,
                       speed=prog.playback_speed if prog else None)
            if prog is not None:
                self.playing = prog.playback_speed > 0
        new["at"] = time.time()
        self.metadata = new if new.get("title") else {}

    def _on_group(self, update):
        if update.playback_state is not None:
            self.playing = update.playback_state == PlaybackStateType.PLAYING

    # ---- pairing: Music Assistant 2.7 has none -----------------------------------------------------

    def open_pairing_window(self):
        return self.connected()  # nothing to pair; connected already means approved

    def set_unpaired_access(self, enabled):
        self._unpaired = enabled  # every client is accepted in this protocol version

