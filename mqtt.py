"""Minimal MQTT 3.1.1 client (QoS 0, stdlib only) for Home Assistant and friends.

Topics (with the default prefix "flipdot"):
    flipdot/notify   payload: plain text, or JSON like the /api/notify body (priority, icon, sound, repeat, hold, key...)
    flipdot/run      payload: JSON app spec, e.g. {"app": "clock"}
    flipdot/command  payload: stop | schedule | next | black | white | invert | dismiss | clear (the notifications)
    flipdot/event    payload: an event name for Automations (e.g. "doorbell")
    flipdot/timer    payload: a timer command, e.g. "pasta 8m", "pause pasta", "cancel all", "pomodoro" (timers.py)
    flipdot/status   published (retained) by us whenever the mode/app changes
    flipdot/availability  online / offline (retained; "offline" is also our Last Will)

With Home Assistant discovery on, entities are announced under homeassistant/... and controlled
through flipdot/ha/<entity>/set (see discovery_configs()).
"""

import json
import logging
import re
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
    def __init__(self, cfg, on_message, on_connect=None, will=None, subscribe=(), client_id=None):
        """will: (topic, payload, retain) the broker publishes if we vanish; subscribe: extra topics
        (full names, wildcards allowed). Messages under the prefix reach on_message with the prefix
        stripped ("notify"), others with their full topic."""
        self.cfg = cfg
        self.on_message = on_message
        self.on_connect = on_connect
        self.will = will
        self.extra_subs = list(subscribe)
        self.client_id = client_id  # None: flipdot-<number>; a second connection needs its own
        self.prefix = cfg.get("prefix") or "flipdot"
        self.sock = None
        self.connected = False
        self.error = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def close(self, before=None):
        """Disconnect cleanly. A clean DISCONNECT cancels the Last Will, so its message is sent
        ourselves first. `before(client)` can publish anything else while still connected."""
        self._stop.set()
        if self.connected:
            try:
                if before:
                    before(self)
                if self.will:
                    self.publish(*self.will)
                with self._send_lock:
                    self.sock.sendall(b"\xe0\x00")  # DISCONNECT
            except Exception as e:
                log.debug("clean disconnect failed: %s", e)
        self.connected = False
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
        payload = _str(self.client_id or f"flipdot-{int(time.time()) % 100000}")
        if self.will:
            topic, message, retain = self.will
            flags |= 0x04 | (0x20 if retain else 0)  # will flag (QoS 0), will retain
            message = message if isinstance(message, bytes) else message.encode()
            payload += _str(topic) + struct.pack("!H", len(message)) + message
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
        for t in [f"{self.prefix}/{t}" for t in ("notify", "run", "command", "event", "timer")] + self.extra_subs:
            sub += _str(t) + b"\x00"
        self.sock.sendall(_packet(0x82, sub))
        self.sock.settimeout(30)
        self.connected = True
        self.error = None
        log.info("connected to MQTT broker %s", c["host"])
        if self.on_connect:
            try:
                self.on_connect(self)
            except Exception:
                log.exception("MQTT on_connect failed")

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
                        if topic.startswith(self.prefix + "/"):
                            topic = topic[len(self.prefix) + 1:]
                        try:
                            self.on_message(topic, data[start:].decode(errors="replace"))
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


def node_id(prefix):
    return re.sub(r"[^A-Za-z0-9_-]", "_", prefix) or "flipdot"


def ha_topics(prefix):
    """Our own topics used by the Home Assistant entities."""
    return {
        "availability": f"{prefix}/availability",
        "status": f"{prefix}/status",
        "command": f"{prefix}/command",
        **{f"{e}_{k}": f"{prefix}/ha/{e}/{k}" for e in ("app", "playlist", "schedule", "quiet", "message", "timer", "dnd",
                                                                "notifications")
           for k in ("set", "state")},
    }


def discovery_configs(prefix, disc_prefix, app_options, playlist_options, model="flipdot board"):
    """[(config topic, payload dict)] announcing the board to Home Assistant as one device."""
    t = ha_topics(prefix)
    node = node_id(prefix)
    uid = node if node.startswith("flipdot") else f"flipdot_{node}"
    device = {"identifiers": [uid], "name": "Flipdot board", "manufacturer": "AlfaZeta",
              "model": model}
    common = {"availability_topic": t["availability"], "payload_available": "online",
              "payload_not_available": "offline", "device": device}

    def entity(component, key, name, icon, **extra):
        cfg = {"name": name, "unique_id": f"{uid}_{key}", "object_id": f"{uid}_{key}",
               "icon": icon, **common, **extra}
        return f"{disc_prefix}/{component}/{node}/{key}/config", cfg

    return [
        entity("select", "app", "App", "mdi:apps", command_topic=t["app_set"], state_topic=t["app_state"],
               options=app_options),
        entity("select", "playlist", "Playlist", "mdi:playlist-play", command_topic=t["playlist_set"],
               state_topic=t["playlist_state"], options=playlist_options),
        entity("switch", "schedule", "Schedule", "mdi:calendar-clock", command_topic=t["schedule_set"],
               state_topic=t["schedule_state"]),
        entity("switch", "quiet_hours", "Quiet hours", "mdi:volume-off", command_topic=t["quiet_set"],
               state_topic=t["quiet_state"]),
        entity("text", "message", "Message", "mdi:message-text", command_topic=t["message_set"], max=255),
        # notifications: notify.send_message to the Board entity, or JSON with priority/icon/... on <prefix>/notify
        entity("notify", "board", "Board", "mdi:message-badge", command_topic=f"{prefix}/notify"),
        entity("switch", "dnd", "Do not disturb", "mdi:bell-off", command_topic=t["dnd_set"], state_topic=t["dnd_state"]),
        entity("sensor", "notifications", "Notifications waiting", "mdi:bell-badge",
               state_topic=t["notifications_state"]),
        entity("button", "dismiss", "Dismiss notification", "mdi:bell-cancel", command_topic=t["command"],
               payload_press="dismiss"),
        entity("button", "next", "Next", "mdi:skip-next", command_topic=t["command"], payload_press="next"),
        entity("button", "stop", "Stop", "mdi:stop", command_topic=t["command"], payload_press="stop"),
        # timers: say "pasta 8 minutes" to the Timer text (e.g. from an Assist sentence automation)
        entity("text", "timer", "Timer", "mdi:timer-plus-outline", command_topic=t["timer_set"], max=255),
        entity("sensor", "timers", "Timers", "mdi:timer-outline", state_topic=t["timer_state"]),
        entity("button", "timers_cancel", "Cancel timers", "mdi:timer-off-outline", command_topic=t["timer_set"],
               payload_press="cancel all"),
        entity("button", "pomodoro", "Start Pomodoro", "mdi:timer-sand", command_topic=t["timer_set"],
               payload_press="pomodoro"),
        entity("sensor", "showing", "Showing", "mdi:dots-grid", state_topic=t["status"],
               value_template="{{ value_json.description or 'Nothing' }}", json_attributes_topic=t["status"]),
    ]


def handle_message(runner, board, notify_fn, topic, payload):
    """Route an MQTT message to the runner."""
    if topic == "notify":
        try:
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            body = {"text": payload}
        body.setdefault("source", "mqtt")
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
        elif cmd == "dismiss":
            runner.dismiss()
        elif cmd == "clear":
            runner.clear_notifications()
        else:
            raise ValueError(f"unknown command {cmd!r}")
