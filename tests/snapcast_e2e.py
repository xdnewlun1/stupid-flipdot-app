"""End-to-end test of the board's Snapcast link (snapcast_link.py) against a real snapserver.

A local snapserver (127.0.0.1 only, HTTP off) plays a pipe source fed with a known signal: a 60 Hz
kick at 120 BPM over a steady 2 kHz tone. The link's snapclient connects to it, and this checks that
the loudness, spectrum peak and beats follow that signal, for the flac and pcm codecs; that the board
names itself over the control port; and that it reconnects after the server restarts.

Needs the snapserver and snapclient binaries (PATH, or SNAPSERVER / SNAPCLIENT env vars); skipped
without them. snapserver announces itself on mDNS when an Avahi daemon is reachable, so it runs with
the D-Bus system bus pointed nowhere. Run:
    python3 tests/snapcast_e2e.py
Exit code 0 = all checks passed (or skipped).
"""

import array
import json
import math
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import snapcast_link  # noqa: E402

RATE, BPM, TONE = 48000, 120, 2000
SERVER = os.environ.get("SNAPSERVER") or shutil.which("snapserver")
CLIENT = os.environ.get("SNAPCLIENT") or shutil.which("snapclient")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Snapserver:
    def __init__(self, tmp, codec, port, control_port):
        self.fifo = tmp / f"fifo-{codec}"
        self.args = [SERVER, f"--server.datadir={tmp}", "--http.enabled=false",
                     "--tcp.bind_to_address=127.0.0.1", f"--tcp.port={control_port}",
                     "--stream.bind_to_address=127.0.0.1", f"--stream.port={port}", f"--stream.codec={codec}",
                     f"--stream.source=pipe://{self.fifo}?name=test&sampleformat={RATE}:16:2&mode=create",
                     "--logging.sink=stderr"]
        self.env = {**os.environ, "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/nonexistent"}  # no mDNS
        self.log = open(tmp / f"snapserver-{codec}.log", "ab")
        self.proc = None
        self.feeding = threading.Event()

    def start(self):
        self.proc = subprocess.Popen(self.args, env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(50):
            if self.fifo.exists():
                break
            time.sleep(0.1)
        self.feeding.set()
        threading.Thread(target=self._feed, args=(self.proc,), daemon=True).start()

    def stop(self):
        self.feeding.clear()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def _feed(self, proc):
        """The test signal, written as fast as snapserver reads it (it reads the pipe in real time)."""
        period, n = 60 / BPM, 0
        block = RATE // 50
        try:
            with open(self.fifo, "wb") as f:
                while self.feeding.is_set() and proc.poll() is None:
                    buf = array.array("h")
                    for i in range(n, n + block):
                        t = i / RATE
                        since = t % period
                        v = 0.25 * math.sin(2 * math.pi * TONE * t)
                        v += 0.7 * math.exp(-since / 0.06) * math.sin(2 * math.pi * 60 * since)
                        s = int(max(-1, min(1, v)) * 32767)
                        buf.append(s)
                        buf.append(s)
                    if sys.byteorder == "big":
                        buf.byteswap()
                    f.write(buf.tobytes())
                    n += block
        except (BrokenPipeError, OSError):
            pass


def watch(link, seconds):
    """Poll the link like the visualizer does; returns (loudness samples, spectra, beat times)."""
    level, spectra, beats, last = [], [], [], None
    end = time.time() + seconds
    while time.time() < end:
        s = link.signal()
        if s["live"]:
            level.append(s["loudness"])
            spectra.append(s["spectrum"])
            if last is not None and link.beat_at != last:
                beats.append(link.beat_at)
            last = link.beat_at
        time.sleep(0.02)
    return level, spectra, beats


def control(port, method, params):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        f = s.makefile("rwb")
        f.write(json.dumps({"id": 1, "jsonrpc": "2.0", "method": method, "params": params}).encode() + b"\r\n")
        f.flush()
        while True:
            msg = json.loads(f.readline())
            if msg.get("id") == 1:
                return msg


def run(codec, tmp):
    port, cport = free_port(), free_port()
    server = Snapserver(tmp, codec, port, cport)
    server.start()
    link = snapcast_link.start_link(tmp, host="127.0.0.1", port=port, control_port=cport,
                                    name="Flipdot board", binary_path=CLIENT)
    try:
        for _ in range(100):
            if link.audible():
                break
            time.sleep(0.1)
        d = link.describe()
        check(f"[{codec}] connected and receiving audio", link.connected() and link.audible(), d["status"])
        check(f"[{codec}] codec and format reported", d["codec"] == codec and d["sample_format"] == f"{RATE}:16:2",
              f"{d['codec']} {d['sample_format']} buffer {d['buffer_ms']} ms")

        t0 = time.process_time()
        level, spectra, beats = watch(link, 6)
        cpu = (time.process_time() - t0) / 6 * 100
        check(f"[{codec}] signal is live", len(level) > 100, f"{len(level)} samples")
        mean = [sum(col) / len(col) for col in zip(*spectra)] if spectra else [0] * 16
        tone_bin = round(math.log(TONE / 40) / math.log(8000 / 40) * 16 - 0.5)
        peak = max(range(4, 16), key=lambda i: mean[i])
        check(f"[{codec}] 2 kHz tone peaks in band {tone_bin}", peak == tone_bin,
              "bands " + " ".join(f"{v:.2f}" for v in mean))
        check(f"[{codec}] kick shows in the bass bands", max(mean[:3]) > 0.3, f"{max(mean[:3]):.2f}")
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        med = statistics.median(gaps) if gaps else 0
        check(f"[{codec}] beats at {BPM} BPM", len(beats) >= 8 and abs(med - 60 / BPM) < 0.06,
              f"{len(beats)} beats, median gap {med:.3f} s")
        check(f"[{codec}] loudness pulses with the kick", level and max(level) - min(level) > 0.1,
              f"{min(level):.2f}..{max(level):.2f}")
        print(f"      link CPU (analysis, this process): {cpu:.1f}% of one core")

        st = control(cport, "Client.GetStatus", {"id": link.client_id})
        name = st["result"]["client"]["config"]["name"]
        check(f"[{codec}] named 'Flipdot board' on the server", name == "Flipdot board", repr(name))

        if codec == "flac":
            server.stop()
            time.sleep(2)
            check("[flac] notices the server going away", not link.audible() or not link.connected(),
                  link.describe()["status"])
            server.start()
            for _ in range(200):
                if link.connected() and link.audible():
                    break
                time.sleep(0.1)
            check("[flac] reconnects after a snapserver restart", link.connected() and link.audible(),
                  link.describe()["status"])
    finally:
        snapcast_link.stop_link()
        server.stop()
    alive = link.proc is not None and link.proc.poll() is None
    check(f"[{codec}] snapclient stopped with the link", not alive)


def main():
    if not SERVER or not CLIENT:
        print("SKIP: snapserver/snapclient not found (install them, or set SNAPSERVER and SNAPCLIENT)")
        return 0
    with tempfile.TemporaryDirectory() as d:
        for codec in ("flac", "pcm"):
            run(codec, Path(d))
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
