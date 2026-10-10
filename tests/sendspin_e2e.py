"""End-to-end test of the board's Sendspin link against a real aiosendspin server.

The server side is the same library Music Assistant uses, at the version it pins, so this checks
what matters in production: the encryption handshake, role activation, song metadata, timestamped
visualizer data (loudness / spectrum / beats), and the Music visualizer reacting to it.
mDNS advertising is off, so nothing on your network sees these test devices.

Setup (once, on a dev machine; needs ffmpeg-free numpy/av wheels, Python 3.12+):
    python3 -m venv .venv-test
    .venv-test/bin/pip install "aiosendspin[server]==9.1.1" pillow
Run:
    .venv-test/bin/python tests/sendspin_e2e.py
Exit code 0 = all checks passed.
"""

import asyncio
import math
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from aiosendspin.audio.format import AudioFormat  # noqa: E402
from aiosendspin.noise.keys import Identity  # noqa: E402
from aiosendspin.noise.trust_store import InMemoryServerPairingStore  # noqa: E402
from aiosendspin.server import SendspinServer  # noqa: E402
from aiosendspin.server.roles.metadata.state import Metadata  # noqa: E402

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
    t = np.arange(int(seconds * RATE)) / RATE
    period = 60 / bpm
    phase = np.mod(t, period)
    kick = 0.9 * np.sin(2 * np.pi * 55 * t) * np.exp(-phase * 14)
    hat = 0.2 * np.random.default_rng(1).uniform(-1, 1, len(t)) * np.exp(-np.mod(t + period / 2, period) * 40)
    mono = np.clip(kick + hat, -1, 1)
    return (np.repeat(mono[:, None], 2, axis=1) * 32000).astype("<i2").tobytes()


async def wait_for(cond, timeout, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()


def make_link(data_dir, port, unpaired, server_url=""):
    link = sendspin_link.SendspinLink(data_dir, name="Flipdot e2e", port=port, advertise=False, server_url=server_url)
    link.set_unpaired_access(unpaired)
    link.start()
    return link


async def main():
    print(f"aiosendspin {sendspin_link.VERSION} (Music Assistant pins {sendspin_link.MA_AIOSENDSPIN})")
    check("aiosendspin version matches Music Assistant's",
          sendspin_link.VERSION == sendspin_link.MA_AIOSENDSPIN, sendspin_link.VERSION)
    loop = asyncio.get_running_loop()
    tmp = Path(tempfile.mkdtemp(prefix="sendspin-e2e-"))

    # ---- the board ----------------------------------------------------------------
    port = free_port()
    link = make_link(tmp / "board", port, unpaired=True)
    sendspin_link._link = link  # what the visualizer app looks up
    ok = await wait_for(lambda: (tmp / "board" / "sendspin" / "identity.key").exists() and link.status != "starting", 10)
    check("board link starts and listens", ok, link.status)
    board_id = Identity.from_private_bytes((tmp / "board" / "sendspin" / "identity.key").read_bytes()).peer_id

    # ---- a server, like Music Assistant -------------------------------------------------
    server = SendspinServer(loop, Identity.generate(), "Test Music Assistant", pairing_store=InMemoryServerPairingStore())
    await server.trust_unpaired(board_id)  # = "allow it to play without pairing" in Music Assistant
    server.connect_to_client(f"ws://127.0.0.1:{port}/sendspin", retry_initial_connection=True)
    ok = await wait_for(link.connected, 15)
    check("server connects (encryption handshake + admission)", ok, link.current_status())
    if not ok:
        return
    client = server.get_client(board_id)
    roles = {type(r).__name__ for r in (getattr(client, "active_roles", None) or [])}
    check("metadata + visualizer roles are active",
          any("Metadata" in r for r in roles) and any("Visualizer" in r for r in roles), ", ".join(sorted(roles)))

    # ---- song info ---------------------------------------------------------------------
    group = client.group
    stream = group.start_stream()
    meta = group.group_role("metadata")
    meta.set_metadata(Metadata(title="Song A", artist="Tester"))
    ok = await wait_for(lambda: link.metadata.get("title") == "Song A", 5)
    check("song title arrives", ok, link.metadata.get("title"))

    # ---- visualizer data, and the visualizer app on top of it ----------------------------------
    import apps
    from canvas import Canvas
    from runner import Context
    from store import Store
    store = Store(tmp / "app")
    viz = apps.create(Context(84, 42, store), {"app": "visualizer", "params": {"style": "bars", "song_change": "flash"}})

    fmt = AudioFormat(sample_rate=RATE, bit_depth=16, channels=2)
    pcm = beat_audio(10)
    chunk = RATE // 20 * 4  # 50 ms of 16-bit stereo
    live_seen, max_loud, max_spec, beats, last_beat_age = False, 0.0, 0.0, 0, 99.0
    bass_log = []
    descs, flashed, switched = set(), False, False
    t0 = time.time()
    pos = 0
    while time.time() - t0 < 10:
        if pos < len(pcm):
            stream.prepare_audio(pcm[pos:pos + chunk], fmt)
            await stream.commit_audio()
            pos += chunk
        if not switched and time.time() - t0 > 6:
            meta.set_metadata(Metadata(title="Song B", artist="Tester"))
            switched = True
        s = link.signal()
        if s["live"]:
            bass_log.append(round(sum(s["spectrum"][:3]) / 3, 3))
        live_seen |= s["live"]
        max_loud = max(max_loud, s["loudness"])
        max_spec = max(max_spec, max(s["spectrum"]))
        if s["beat_age"] < last_beat_age - 0.2:  # the beat timer restarted: a new beat
            beats += 1
        last_beat_age = s["beat_age"]
        c = Canvas(84, 42)
        viz.render(c, time.time())
        descs.add(viz.describe())
        flashed |= viz.flash_until > time.time()
        await stream.sleep_to_limit_buffer(400_000)
        await asyncio.sleep(0.03)
    group.stop_stream()
    if os.environ.get("E2E_DEBUG"):
        print("bass:", bass_log[:120])

    check("visualizer frames arrive and come due on time", live_seen)
    check("loudness follows the audio", max_loud > 0.2, f"max {max_loud:.2f}")
    check("spectrum follows the audio", max_spec > 0.2, f"max {max_spec:.2f}")
    check("beats are picked up (expect ~2/s at 120 bpm)", beats >= 6, f"{beats} beats in ~{10 - 1.5:.0f}s of playback")
    check("visualizer app runs on the live data", any("live from Music Assistant" in d for d in descs), "; ".join(sorted(descs)))
    check("song change arrives", link.metadata.get("title") == "Song B", link.metadata.get("title"))
    check("visualizer flashes on the song change", flashed)

    # ---- a server that wasn't approved must be refused -----------------------------------------
    port2 = free_port()
    link2 = make_link(tmp / "board2", port2, unpaired=False)
    await wait_for(lambda: (tmp / "board2" / "sendspin" / "identity.key").exists(), 10)
    board2_id = Identity.from_private_bytes((tmp / "board2" / "sendspin" / "identity.key").read_bytes()).peer_id
    stranger = SendspinServer(loop, Identity.generate(), "Stranger", pairing_store=InMemoryServerPairingStore())
    stranger.connect_to_client(f"ws://127.0.0.1:{port2}/sendspin")
    await asyncio.sleep(3)
    # An unpaired server may hold a connection open (that's how pairing starts), but it must not get
    # any roles activated, so no song info or visualizer data can flow.
    sc = stranger.get_client(board2_id)
    s_roles = [type(r).__name__ for r in (getattr(sc, "active_roles", None) or [])] if sc else []
    if sc is not None and sc.is_connected:
        try:
            g = sc.group
            st = g.start_stream()
            mr = g.group_role("metadata")
            if mr is not None:
                mr.set_metadata(Metadata(title="Sneaky", artist="Stranger"))
            st.prepare_audio(beat_audio(1), fmt)
            await st.commit_audio()
        except Exception as e:  # refusing to stream to it is also fine
            print("    (stranger couldn't stream:", type(e).__name__, ")")
    await asyncio.sleep(2)
    sig2 = link2.signal()
    check("unapproved server gets no roles", not s_roles, ", ".join(s_roles) or "none")
    check("unapproved server can't send song info or visualizer data",
          not link2.metadata.get("title") and not sig2["live"], link2.metadata.get("title") or "nothing received")


async def dial_test():
    """The board dials Music Assistant's Sendspin server itself (client-initiated)."""
    loop = asyncio.get_running_loop()
    tmp = Path(tempfile.mkdtemp(prefix="sendspin-dial-"))
    port = free_port()
    server = SendspinServer(loop, Identity.generate(), "Listening Music Assistant", pairing_store=InMemoryServerPairingStore())
    await server.start_server(port=port, host="127.0.0.1", discover_clients=False)
    link = make_link(tmp / "board", free_port(), unpaired=True, server_url=f"ws://127.0.0.1:{port}/sendspin")
    await wait_for(lambda: (tmp / "board" / "sendspin" / "identity.key").exists(), 10)
    board_id = Identity.from_private_bytes((tmp / "board" / "sendspin" / "identity.key").read_bytes()).peer_id
    await server.trust_unpaired(board_id)
    ok = await wait_for(link.connected, 25)
    check("board dials Music Assistant itself (client-initiated)", ok, link.current_status())
    if ok:
        client = server.get_client(board_id)
        client.group.start_stream()
        client.group.group_role("metadata").set_metadata(Metadata(title="Dialed in", artist="Tester"))
        ok = await wait_for(lambda: link.metadata.get("title") == "Dialed in", 5)
        check("song info arrives over the dialled connection", ok, link.metadata.get("title"))


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(dial_test())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.stdout.flush()
    os._exit(1 if failed else 0)  # the link threads are daemons; don't wait for them
