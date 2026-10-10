"""Minimal MQTT 3.1.1 client (QoS 0, stdlib only) for Home Assistant and friends.

Topics (with the default prefix "flipdot"):
    flipdot/notify   payload: plain text, or JSON like the /api/notify body
    flipdot/run      payload: JSON app spec, e.g. {"app": "clock"}
    flipdot/command  payload: stop | schedule | next | black | white | invert
    flipdot/status   published (retained) by us whenever the mode/app changes
"""

import json
import logging
import socket
import struct
import threading
import time

log = logging.getLogger("mqtt")


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(ptype, body):
    out = bytearray([ptype])
    n = len(body)
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + body


class MqttClient:
    def __init__(self, cfg, on_message):
        self.cfg = cfg
        self.on_message = on_message
        self.prefix = cfg.get("prefix") or "flipdot"
        self.sock = None
        self.connected = False
        self.error = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def close(self):
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def publish(self, topic, payload, retain=False):
        if not self.connected:
            return
        body = _str(topic) + (payload if isinstance(payload, bytes) else payload.encode())
        try:
            with self._send_lock:
                self.sock.sendall(_packet(0x30 | (1 if retain else 0), body))
        except OSError as e:
            log.debug("publish failed: %s", e)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return buf

    def _read_packet(self):
        head = self._recv_exact(1)[0]
        mult, length = 1, 0
        while True:
            b = self._recv_exact(1)[0]
            length += (b & 0x7F) * mult
            mult *= 128
            if not b & 0x80:
                break
        return head, self._recv_exact(length) if length else b""

    def _connect(self):
        c = self.cfg
        self.sock = socket.create_connection((c["host"], int(c.get("port") or 1883)), timeout=10)
        flags = 0x02  # clean session
        payload = _str(f"flipdot-{int(time.time()) % 100000}")
        if c.get("username"):
            flags |= 0x80
            payload += _str(c["username"])
            if c.get("password"):
                flags |= 0x40
                payload += _str(c["password"])
        body = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", 60) + payload
        self.sock.sendall(_packet(0x10, body))
        head, data = self._read_packet()
        if head >> 4 != 2 or data[1] != 0:
            raise ConnectionError(f"broker refused connection (code {data[1] if len(data) > 1 else '?'})")
        sub = struct.pack("!H", 1)
        for t in ("notify", "run", "command"):
            sub += _str(f"{self.prefix}/{t}") + b"\x00"
        self.sock.sendall(_packet(0x82, sub))
        self.sock.settimeout(30)
        self.connected = True
        self.error = None
        log.info("connected to MQTT broker %s", c["host"])

    def _run(self):
        while not self._stop.is_set():
            try:
                self._connect()
                last_ping = time.time()
                while not self._stop.is_set():
                    if time.time() - last_ping > 25:
                        with self._send_lock:
                            self.sock.sendall(b"\xc0\x00")  # PINGREQ
                        last_ping = time.time()
                    try:
                        head, data = self._read_packet()
                    except socket.timeout:
                        continue
                    if head >> 4 == 3:  # PUBLISH
                        tlen = struct.unpack("!H", data[:2])[0]
                        topic = data[2:2 + tlen].decode()
                        start = 2 + tlen + (2 if (head >> 1) & 3 else 0)
                        try:
                            self.on_message(topic[len(self.prefix) + 1:], data[start:].decode(errors="replace"))
                        except Exception as e:
                            log.warning("MQTT message on %s failed: %s", topic, e)
            except (OSError, ConnectionError, IndexError) as e:
                self.error = str(e)
                if not self._stop.is_set():
                    log.warning("MQTT: %s (retrying in 10 s)", e)
            self.connected = False
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self._stop.wait(10)


def handle_message(runner, board, notify_fn, topic, payload):
    """Route an MQTT message to the runner."""
    if topic == "notify":
        try:
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            body = {"text": payload}
        notify_fn(body)
    elif topic == "run":
        body = json.loads(payload)
        runner.run(body, body.get("transition", "none"))
    elif topic == "command":
        cmd = payload.strip().lower()
        if cmd == "stop":
            runner.stop()
        elif cmd == "schedule":
            runner.resume_schedule()
        elif cmd == "next":
            runner.next()
        elif cmd in ("black", "white"):
            runner.stop()
            board.fill(cmd == "white")
        elif cmd == "invert":
            runner.stop()
            board.invert_all()
        else:
            raise ValueError(f"unknown command {cmd!r}")
