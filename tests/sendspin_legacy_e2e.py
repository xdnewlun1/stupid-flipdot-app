"""End-to-end test of the board's Music Assistant 2.7.x link (sendspin_legacy.py).

The stand-in Music Assistant is the aiosendspin 2.0.0 server that Music Assistant 2.7.8 uses, driven
the way its Sendspin provider drives it: it connects to the board (as it does after finding
`_sendspin._tcp` on mDNS), sets song metadata on the board's group and plays audio to it. The 2.0.0
server sends no visualizer data, so this also checks the board's own analysis of that audio.
Everything runs on 127.0.0.1 and nothing is advertised on mDNS.

Setup (once, Python 3.12+):
    python3 -m venv .venv-ma27
    .venv-ma27/bin/pip install "aiosendspin==2.0.0" pillow
Run:
    .venv-ma27/bin/python tests/sendspin_legacy_e2e.py
Exit code 0 = all checks passed.
"""

import array
import asyncio
import math
import os
import random
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiohttp import web  # noqa: E402
from aiosendspin.models.types import Roles  # noqa: E402
from aiosendspin.server import AudioFormat, ClientAddedEvent, SendspinServer  # noqa: E402
from aiosendspin.server.metadata import Metadata  # noqa: E402
from aiosendspin.server.stream import MediaStream  # noqa: E402

import sendspin_link  # noqa: E402

RATE = 44100
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def beat_audio(seconds, bpm=120):
    """Stereo 16-bit PCM: a kick on every beat and a hi-hat in between."""
    period = 60 / bpm
    rnd = random.Random(1)
    out = array.array("h")
    for i in range(int(seconds * RATE)):
        t = i / RATE
        kick = 0.9 * math.sin(2 * math.pi * 55 * t) * math.exp(-(t % period) * 14)
        hat = 0.2 * rnd.uniform(-1, 1) * math.exp(-((t + period / 2) % period) * 40)
        v = int(max(-1.0, min(1.0, kick + hat)) * 32000)
        out.append(v)
        out.append(v)
    return out.tobytes()


async def pcm_source(pcm, chunk=RATE // 20 * 4):
    for pos in range(0, len(pcm), chunk):
        yield pcm[pos:pos + chunk]


async def wait_for(cond, timeout, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()


class StandIn:
    """aiosendspin 2.0.0 server, like Music Assistant 2.7.8's Sendspin provider (minus mDNS)."""

    def __init__(self, loop, name):
        self.server = SendspinServer(loop, f"ma-{name}", name)
        self.added = asyncio.Event()
        self.server.add_event_listener(self._event)

    async def _event(self, server, event):
        if isinstance(event, ClientAddedEvent):
            self.added.set()

    async def listen(self, port):
        """Music Assistant's own listener on 8927 (start_server without the mDNS part)."""
        app = web.Application()
        app.router.add_get("/sendspin", self.server.on_client_connect)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()

    def client(self):
        return next(iter(self.server.clients), None)


def make_link(data_dir, port, server_url=""):
    link = sendspin_link.start_link(data_dir, name="Flipdot e2e", port=port, advertise=False, server_url=server_url)
    link.set_unpaired_access(True)
    return link


async def main():
    print(f"aiosendspin {sendspin_link.VERSION} (Music Assistant 2.7.x pins {sendspin_link.MA27_AIOSENDSPIN})")
    check("aiosendspin version matches Music Assistant 2.7.x", sendspin_link.VERSION == sendspin_link.MA27_AIOSENDSPIN,
          sendspin_link.VERSION)
    loop = asyncio.get_running_loop()
    tmp = Path(tempfile.mkdtemp(prefix="sendspin-legacy-e2e-"))

    # ---- the board -----------------------------------------------------------------------------
    port = free_port()
    link = make_link(tmp / "board", port)
    check("start_link picks the Music Assistant 2.7 link", type(link).__name__ == "LegacySendspinLink", type(link).__name__)
    ok = await wait_for(lambda: link.status == "waiting for Music Assistant", 10)
    check("board link starts and listens", ok, link.status)
    d = link.describe()
    check("status names the protocol and has no version warning", "2.7" in d["protocol"] and not d["warning"],
          f"{d['protocol']}; warning={d['warning']}")

    # ---- Music Assistant finds the board and connects to it (server-initiated) ---------------------
    ma = StandIn(loop, "Test Music Assistant")
    ma.server.connect_to_client(f"ws://127.0.0.1:{port}/sendspin")
    ok = await wait_for(link.connected, 15) and await wait_for(ma.added.is_set, 10)
    check("Music Assistant connects to the board and registers it", ok, link.current_status())
    if not ok:
        return
    client = ma.client()
    roles = {r.value for r in client.roles}
    check("board joins as player + metadata", {Roles.PLAYER.value, Roles.METADATA.value} <= roles, ", ".join(sorted(roles)))

    group = client.group
    group.set_metadata(Metadata(title="Song A", artist="Tester", album="Album", track_progress=0,
                                track_duration=180_000, playback_speed=1000))
    ok = await wait_for(lambda: link.metadata.get("title") == "Song A", 5)
    check("song info arrives", ok and link.metadata.get("artist") == "Tester" and link.metadata.get("duration_ms") == 180_000,
          str({k: link.metadata.get(k) for k in ("title", "artist", "duration_ms")}))

    # ---- playback: the board analyses the audio Music Assistant streams to it ------------------------
    import apps
    from canvas import Canvas
    from runner import Context
    from store import Store
    store = Store(tmp / "app")
    viz = apps.create(Context(84, 42, store), {"app": "visualizer", "params": {"style": "bars", "song_change": "flash"}})

    play = loop.create_task(group.play_media(MediaStream(main_channel_source=pcm_source(beat_audio(10)),
                                                         main_channel_format=AudioFormat(RATE, 16, 2))))
    ok = await wait_for(lambda: link.playing is True, 5)
    check("playing state arrives", ok, f"playing={link.playing}")
    live_seen, max_loud, max_spec, beats, last_beat_age = False, 0.0, 0.0, 0, 99.0
    descs, flashed, switched = set(), False, False
    t0 = time.time()
    while time.time() - t0 < 10:
        if not switched and time.time() - t0 > 6:
            group.set_metadata(Metadata(title="Song B", artist="Tester", track_progress=0,
                                        track_duration=200_000, playback_speed=1000))
            switched = True
        s = link.signal()
        live_seen |= s["live"]
        max_loud = max(max_loud, s["loudness"])
        max_spec = max(max_spec, max(s["spectrum"]))
        if s["beat_age"] < last_beat_age - 0.2:  # the beat timer restarted: a new beat
            beats += 1
        last_beat_age = s["beat_age"]
        viz.render(Canvas(84, 42), time.time())
        descs.add(viz.describe())
        flashed |= viz.flash_until > time.time()
        await asyncio.sleep(0.03)

    check("visualizer frames from the audio come due on time", live_seen)
    check("loudness follows the audio", max_loud > 0.2, f"max {max_loud:.2f}")
    check("spectrum follows the audio", max_spec > 0.2, f"max {max_spec:.2f}")
    check("beats are picked up (expect ~2/s at 120 bpm)", beats >= 6, f"{beats} beats in ~9s of playback")
    check("visualizer app goes live from Music Assistant", any("live from Music Assistant" in d for d in descs),
          "; ".join(sorted(descs)))
    check("song change arrives", link.metadata.get("title") == "Song B", link.metadata.get("title"))
    check("visualizer flashes on the song change", flashed)
    await group.stop()
    play.cancel()
    ok = await wait_for(lambda: link.playing is False, 5)
    check("stopping arrives", ok, f"playing={link.playing}")
    ok = await wait_for(lambda: not link.signal()["live"], 3)
    check("visualizer data stops after playback", ok)


async def dial_test():
    """The board dials Music Assistant's Sendspin server on 8927 itself (client-initiated)."""
    loop = asyncio.get_running_loop()
    tmp = Path(tempfile.mkdtemp(prefix="sendspin-legacy-dial-"))
    port = free_port()
    ma = StandIn(loop, "Listening Music Assistant")
    await ma.listen(port)
    sendspin_link._link = None
    link = make_link(tmp / "board", free_port(), server_url=f"ws://127.0.0.1:{port}/sendspin")
    ok = await wait_for(link.connected, 15) and await wait_for(ma.added.is_set, 10)
    check("board dials Music Assistant itself (client-initiated)", ok, link.current_status())
    if ok:
        ma.client().group.set_metadata(Metadata(title="Dialed in", artist="Tester"))
        ok = await wait_for(lambda: link.metadata.get("title") == "Dialed in", 5)
        check("song info arrives over the dialled connection", ok, link.metadata.get("title"))
        check("status shows the server name", "Listening Music Assistant" in link.current_status(), link.current_status())


async def run_all():
    await main()
    await dial_test()


if __name__ == "__main__":
    # one event loop, never closed: cancelling the 2.0.0 server's connection tasks can hang
    loop = asyncio.new_event_loop()
    loop.run_until_complete(run_all())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.stdout.flush()
    os._exit(1 if failed else 0)  # the link threads are daemons; don't wait for them
