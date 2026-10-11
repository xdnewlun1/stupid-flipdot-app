"""Minimal WebSocket server side (RFC 6455, stdlib only) that pushes board updates.

Server -> client:
    binary: u32 version, u16 width, u16 height, then the dots packed 8 per byte (MSB first)
    text:   {"type": "status", "runner": {...}}  |  {"type": "error", "error": "..."}
Client -> server (text JSON):
    {"type": "input", "key": "up", "action": "down", "player": 1}
    {"type": "dots", "dots": [{"x": 1, "y": 2, "color": "white"}, ...]}
"""

import base64
import hashlib
import json
import logging
import struct
import threading
import time

log = logging.getLogger("ws")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FPS = 40
PING_EVERY = 15


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


class Closed(Exception):
    pass


def _read(rfile, n):
    data = rfile.read(n)
    if len(data) < n:
        raise Closed()
    return data


def recv_message(rfile):
    """Returns (opcode, payload) for one complete message (fragments joined)."""
    chunks, first_op = [], None
    while True:
        b1, b2 = _read(rfile, 2)
        op, fin = b1 & 0x0F, b1 & 0x80
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", _read(rfile, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _read(rfile, 8))[0]
        if n > 1 << 20:
            raise Closed()
        mask = _read(rfile, 4) if b2 & 0x80 else None
        payload = bytearray(_read(rfile, n))
        if mask:
            for i in range(n):
                payload[i] ^= mask[i & 3]
        if op >= 0x8:  # control frames can arrive between fragments
            return op, bytes(payload)
        if first_op is None:
            first_op = op
        chunks.append(bytes(payload))
        if fin:
            return first_op, b"".join(chunks)


def frame(op, payload):
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | op, n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | op, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | op, 127, n)
    return head + payload


class Hub:
    """Tracks connected clients and wakes their sender threads when something changes."""

    def __init__(self, board, runner, on_message):
        self.board, self.runner, self.on_message = board, runner, on_message
        self.cond = threading.Condition()
        self.seq = 0
        self.clients = 0

    def notify(self):
        with self.cond:
            self.seq += 1
            self.cond.notify_all()

    def serve(self, sock, rfile, on_message=None):
        """Run one client connection (blocks until it closes). `on_message` replaces the hub's handler
        for this connection (e.g. one that only accepts game input)."""
        on_message = on_message or self.on_message
        send_lock = threading.Lock()
        closed = threading.Event()

        def send(op, payload):
            with send_lock:
                sock.sendall(frame(op, payload))

        def sender():
            seen, last_version, last_status = -1, None, None
            last_send = last_ping = 0
            try:
                while not closed.is_set():
                    with self.cond:
                        self.cond.wait_for(lambda: self.seq != seen or closed.is_set(), timeout=PING_EVERY)
                        seen = self.seq
                    if closed.is_set():
                        return
                    wait = 1 / MAX_FPS - (time.time() - last_send)
                    if wait > 0:
                        time.sleep(wait)  # throttle; later changes are picked up below
                    version, bits = self.board.packed()
                    if version != last_version:
                        send(0x2, struct.pack("!IHH", version & 0xFFFFFFFF, self.board.width, self.board.height) + bits)
                        last_version = version
                    status = json.dumps({"type": "status", "runner": self.runner.status()})
                    if status != last_status:
                        send(0x1, status.encode())
                        last_status = status
                    last_send = time.time()
                    if last_send - last_ping > PING_EVERY:
                        send(0x9, b"")
                        last_ping = last_send
            except OSError:
                pass
            finally:
                closed.set()

        with self.cond:
            self.clients += 1
        threading.Thread(target=sender, daemon=True).start()
        self.notify()  # send the current state right away
        try:
            while not closed.is_set():
                op, data = recv_message(rfile)
                if op == 0x8:  # close
                    try:
                        send(0x8, data[:2])
                    except OSError:
                        pass
                    break
                if op == 0x9:  # ping
                    send(0xA, data)
                elif op == 0x1:
                    try:
                        on_message(json.loads(data))
                    except Exception as e:
                        send(0x1, json.dumps({"type": "error", "error": str(e)}).encode())
        except (Closed, OSError, ValueError):
            pass
        finally:
            closed.set()
            with self.cond:
                self.clients -= 1
                self.cond.notify_all()
