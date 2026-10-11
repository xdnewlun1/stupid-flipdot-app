"""HTTP API + web UI for the flipdot board.

    python3 server.py              # talk to the board
    python3 server.py --dry-run    # no hardware, just the web page/API
"""

import argparse
import atexit
import base64
import gzip
import io
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import signal
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import access
import aidraw
import camera
import friends
import friends_api
import gamepad as gamepad_mod
import apps
import guestwall
import health
import media_upload
import notifications
import ntfy
import panelsetup
import party_api
import programme_api
import plugins_api
import refs
import rules
import sendspin_link
import snapcast_link
import storage as storage_mod
import timelapse
import timers as timers_mod
import backups as backups_mod
import localconfig
import updater as updater_mod
import video as video_mod
from apps import gamemenu, slideshow
from apps import music, nes
from apps.media import convert_image
from apps.notice import ICONS
from apps.widgets import geocode
from boardcare import BoardCare
from canvas import Canvas
from flipdot import Board, load_config
import fonts
from fonts import FONTS, NORMAL
from mqtt import MqttClient, discovery_configs, ha_topics, handle_message
from runner import OFF, TRANSITIONS, Runner
from store import Store
from ws import Hub, accept_key

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
COLORS = {"white": True, "black": False, "toggle": None}
SECRET_MASK = "********"
TEXT_SIZES = {"small": ("small", 1), "normal": ("normal", 1), "large": ("normal", 2), "huge": ("normal", 3)}

board: Board = None
store: Store = None
runner: Runner = None
password = ""
mqtt_client = None
gamepad: gamepad_mod.GamepadManager = None  # USB gamepads / GameCube adapter
hub: Hub = None
care: BoardCare = None
recorder: timelapse.Recorder = None
automations: rules.Automations = None
persist: storage_mod.Storage = None
config_path = None
data_dir = storage_path = None  # where --data and --storage point (free disk in /api/health)
backups = updater = None  # backups.Backups (scheduled backups) and updater.Updater (update from git)
ha_lock = threading.Lock()
ha_sent = {}  # topic -> payload last published for Home Assistant (states and discovery configs)


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Redirect:
    def __init__(self, url):
        self.url = url


class Raw:
    """A non-JSON response (e.g. a GIF download)."""
    def __init__(self, body, ctype, headers=None):
        self.body, self.ctype, self.headers = body, ctype, headers or {}


class RawFile:
    """A file streamed as the response, deleted afterwards when `temporary`."""
    def __init__(self, path, ctype, headers=None, temporary=False):
        self.path, self.ctype, self.headers, self.temporary = Path(path), ctype, headers or {}, temporary


def parse_color(value):
    if isinstance(value, bool):
        return value
    if value not in COLORS:
        raise ValueError('color must be "white", "black" or "toggle"')
    return COLORS[value]


def parse_dot(d):
    return int(d["x"]), int(d["y"]), parse_color(d.get("color", "toggle"))


def state():
    return {"width": board.width, "height": board.height, "version": board.version,
            "rows": board.rows(), "runner": runner.status()}


def image_bytes(body):
    """Image data from a JSON body: {"image": base64 or data: URL} or {"url": ...}."""
    if body.get("image"):
        data = body["image"]
        if data.startswith("data:"):
            data = data.split(",", 1)[1]
        return base64.b64decode(data)
    if body.get("url"):
        req = urllib.request.Request(body["url"], headers={"User-Agent": "flipdot-board"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    raise ValueError("send 'image' (base64) or 'url'")


def convert_from_body(body, data=None):
    return convert_image(
        data if data is not None else image_bytes(body), board.width, board.height,
        mode=body.get("mode", "crop"), dither=bool(body.get("dither", False)),
        threshold=int(body.get("threshold", 128)), invert=bool(body.get("invert", False)))


def _truthy(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def notify_options(body, source="api"):
    """The queue options of a notification body: priority, icon, sound, repeat, hold, key (notifications.py)."""
    icon = body.get("icon") or None
    if icon is not None and icon not in ICONS:
        raise ValueError(f"unknown icon {icon!r}: use {', '.join(ICONS)}")
    sound = body.get("sound") or None
    if sound is not None and sound not in notifications.SOUNDS:
        raise ValueError(f"unknown sound pattern {sound!r}: use {', '.join(notifications.SOUNDS)}")
    text = body.get("text")
    if text is not None and body.get("title"):
        text = f"{body['title']}: {text}"
    return {"priority": notifications.priority(body.get("priority")), "icon": icon, "sound": sound,
            "repeat": max(1, min(20, int(body.get("repeat") or 1))), "hold": _truthy(body.get("hold")),
            "key": str(body["key"])[:80] if body.get("key") else None,
            "text": str(text)[:300] if text is not None else None, "source": body.get("source") or source}


def notify(body, source="api"):
    """Show something temporarily, then go back to whatever was running. It joins the notification queue
    (priority, do-not-disturb, de-duplication: notifications.py); returns what the queue did with it."""
    cfg = notifications.settings(store)
    duration = float(body.get("duration") or cfg.get("duration") or 10)
    transition = body.get("transition", "dissolve")
    tdur = float(body.get("transition_duration") or 1.0)
    opts = notify_options(body, source)
    if body.get("image") or body.get("url"):
        frames = convert_from_body(body)
        make = lambda: apps.REGISTRY["scene"](runner.ctx, frames=frames, name="notification")
        opts["text"] = opts["text"] or "Picture"
        return runner.notify(app=make(), remake=make, duration=duration, transition=transition,
                             transition_duration=tdur, **opts)
    if body.get("scene"):
        spec = {"app": "scene", "params": {"scene": body["scene"]}}
    elif body.get("app"):
        spec = {"app": body["app"], "params": body.get("params") or {}}
    elif body.get("text") is not None:
        text = str(body["text"])
        fits_large = NORMAL.width(text) * 2 <= board.width
        style = body.get("style") or ("static" if fits_large or len(text) > 40 else "scroll")
        size = body.get("size") or ("large" if fits_large or style == "scroll" else "normal")
        params = {"text": text, "style": style, "invert": str(body.get("invert", "")).lower() in ("1", "true")}
        if body.get("font"):
            params.update(font=body["font"], scale=int(body.get("scale") or 1), font_size=int(body.get("font_size") or 14))
            if style == "static" and not body.get("scale") and not body.get("font_size"):
                params["fit"] = True
        else:
            params["size"] = size
        spec = {"app": "text", "params": params}
    else:
        raise ValueError("send 'text', 'image', 'url', 'scene' or 'app'")
    if spec["app"] == "text" and opts["icon"] and not body.get("font") and not body.get("style"):
        # with an icon: the icon beside the message, the way a notification card looks
        spec = {"app": "notice", "params": {"text": str(body["text"]), "title": str(body.get("title") or ""),
                                            "icon": opts["icon"]}}
    elif spec["app"] == "text" and body.get("title"):
        spec["params"]["text"] = opts["text"]
    return runner.notify(spec=spec, duration=duration, transition=transition, transition_duration=tdur, **opts)


def masked_settings():
    s = json.loads(json.dumps(store.settings))
    s.pop("spotify", None)
    ma = s.setdefault("music_assistant", {})
    if ma.get("token"):
        ma["token"] = SECRET_MASK
    ai = s.setdefault("ai", {})
    if ai.get("api_key"):
        ai["api_key"] = SECRET_MASK
    mq = s.setdefault("mqtt", {})
    if mq.get("password"):
        mq["password"] = SECRET_MASK
    mq["connected"] = bool(mqtt_client and mqtt_client.connected)
    mq["error"] = mqtt_client.error if mqtt_client else None
    nt = s.setdefault("notifications", {}).setdefault("ntfy", {})
    if nt.get("token"):
        nt["token"] = SECRET_MASK
    return s


def save_settings(new):
    old = store.settings
    merged = json.loads(json.dumps(old))
    for section, values in new.items():
        if not isinstance(values, dict):
            continue
        dest = merged.setdefault(section, {})
        for k, v in values.items():
            if k in ("connected", "error") or v == SECRET_MASK:
                continue
            dest[k] = v
    store.save_settings(merged)
    if care and merged.get("quiet_hours") != old.get("quiet_hours"):
        care.apply()
    # restarting snapclient, the gamepad reader or the MQTT client takes up to a few seconds: the page gets its
    # answer now and one worker applies the changes in order (so two quick saves can't race each other)
    _effects.submit(_apply_settings, old, merged)


_effects = ThreadPoolExecutor(max_workers=1, thread_name_prefix="settings")


def _apply_settings(old, merged):
    try:
        if merged.get("mqtt") != old.get("mqtt"):
            start_mqtt()
        else:
            ha_sync()
        source_changed = merged.get("visualizer") != old.get("visualizer")
        sc_old, sc_new = old.get("snapcast") or {}, merged.get("snapcast") or {}
        live = ("delay_ms", "name")  # changed without reconnecting
        conn = lambda cfg: {k: v for k, v in cfg.items() if k not in live}
        link = snapcast_link.get_link()
        if source_changed or conn(sc_new) != conn(sc_old) or (
                merged.get("music_assistant", {}).get("url") != old.get("music_assistant", {}).get("url")):
            start_snapcast()
        elif link:
            if sc_new.get("delay_ms") != sc_old.get("delay_ms"):
                link.set_delay(sc_new.get("delay_ms"))  # live: tune it by ear
            if sc_new.get("name") != sc_old.get("name"):
                link.set_name(sc_new.get("name") or "Flipdot board")  # over the control port, no restart
        if merged.get("sendspin") != old.get("sendspin") or source_changed:
            start_sendspin()
            link = sendspin_link.get_link()
            if link and merged["sendspin"].get("unpaired") != old.get("sendspin", {}).get("unpaired"):
                link.set_unpaired_access(bool(merged["sendspin"].get("unpaired")))
        if merged.get("gamepad") != old.get("gamepad"):
            start_gamepad()
        if merged.get("notifications") != old.get("notifications"):
            nt = (merged["notifications"].get("ntfy") or {})
            if nt.get("token") == SECRET_MASK:  # the masked token came back unchanged
                nt["token"] = ((old.get("notifications") or {}).get("ntfy") or {}).get("token", "")
                store.save_settings(merged)
            if nt != ((old.get("notifications") or {}).get("ntfy") or {}):
                start_ntfy()
            runner._dnd_at = 0  # look at do-not-disturb again now
    except Exception:
        logging.exception("applying settings failed")


def start_ntfy():
    """(Re)subscribe to the ntfy topics in the notification settings."""
    ntfy.start(store, lambda body: notify(body, source="ntfy"))


def start_mqtt():
    global mqtt_client
    cfg = store.settings.get("mqtt", {})
    if mqtt_client:
        old = mqtt_client.cfg
        # discovery switched off (or moved): remove the entities from Home Assistant
        dropped = old.get("discovery") and (not cfg.get("enabled") or not cfg.get("discovery")
                                            or ha_disc_prefix(old) != ha_disc_prefix(cfg) or old.get("prefix") != cfg.get("prefix"))
        mqtt_client.close(before=ha_remove if dropped else None)
        mqtt_client = None
    if cfg.get("enabled") and cfg.get("host"):
        prefix = cfg.get("prefix") or "flipdot"
        subs = [f"{prefix}/ha/+/set"]
        if cfg.get("discovery"):
            subs.append(f"{ha_disc_prefix(cfg)}/status")  # Home Assistant's birth message
        mqtt_client = MqttClient(cfg, mqtt_message, on_connect=mqtt_connected, subscribe=subs,
                                 will=(f"{prefix}/availability", "offline", True))


def mqtt_message(topic, payload):
    cfg = mqtt_client.cfg if mqtt_client else {}
    if topic.startswith("ha/") and topic.endswith("/set"):
        ha_command(topic[3:-4], payload.strip())
    elif topic == "event":
        automations.event(payload.strip())
    elif topic == "timer":
        timers_mod.for_store(store).command(payload)
    elif topic == f"{ha_disc_prefix(cfg)}/status":
        if payload.strip() == "online":  # Home Assistant restarted: announce everything again
            ha_sync(force=True)
    else:
        handle_message(runner, board, notify, topic, payload)


def mqtt_connected(client):
    client.publish(f"{client.prefix}/availability", "online", retain=True)
    publish_status()
    ha_sync(force=True)


# ---- Home Assistant MQTT discovery --------------------------------------------------

def ha_disc_prefix(cfg):
    return (cfg.get("discovery_prefix") or "homeassistant").strip("/")


NOTHING = "Nothing"


def ha_app_options():
    """{title: app id} of apps that work without setup (no scene/slideshow/drawing to pick)."""
    needs_setup = {"scene", "gallery", "drawing", "composition", "hidden"}
    out = {}
    for a in apps.catalog():
        if a["category"] == "Layers" or any(p["type"] in needs_setup for p in a["params"]):
            continue
        title = a["title"] if a["title"] not in out and a["title"] != NOTHING else f"{a['title']} ({a['id']})"
        out[title] = a["id"]
    return out


def ha_states(t):
    app = runner.app
    titles = {v: k for k, v in ha_app_options().items()}
    playing = runner.playlist if runner.mode in ("playlist", "schedule") and runner.playlist in store.playlists else None
    return {
        t["app_state"]: titles.get(app.id, NOTHING) if app is not None and not getattr(app, "restore", False) else NOTHING,
        t["playlist_state"]: playing or NOTHING,
        t["schedule_state"]: "ON" if store.schedule.get("enabled") else "OFF",
        t["quiet_state"]: "ON" if (store.settings.get("quiet_hours") or {}).get("enabled") else "OFF",
        t["timer_state"]: timers_mod.for_store(store).summary(),
        t["dnd_state"]: "ON" if notifications.settings(store)["dnd"].get("now") else "OFF",
        t["notifications_state"]: str(len(runner.notify_queue.pending) + len(runner.notify_queue.held)),
    }


def ha_configs(client):
    return discovery_configs(client.prefix, ha_disc_prefix(client.cfg), [NOTHING] + list(ha_app_options()),
                             [NOTHING] + list(store.playlists), model=f"{board.width}x{board.height} flipdot")


def ha_sync(force=False):
    """Publish Home Assistant states (and discovery configs, e.g. when playlists change) that changed."""
    client = mqtt_client
    if not client or not client.connected or not client.cfg.get("discovery"):
        return
    with ha_lock:
        if force:
            ha_sent.clear()
        out = {topic: json.dumps(cfg) for topic, cfg in ha_configs(client)}
        out.update(ha_states(ha_topics(client.prefix)))
        for topic, payload in out.items():
            if ha_sent.get(topic) != payload:
                client.publish(topic, payload, retain=True)
                ha_sent[topic] = payload


def ha_remove(client):
    for topic, _ in ha_configs(client):
        client.publish(topic, "", retain=True)  # an empty retained config deletes the entity
    ha_sent.clear()


def ha_command(entity, payload):
    if entity == "app":
        if payload == NOTHING:
            runner.stop()
        else:
            app_id = ha_app_options().get(payload)
            if app_id is None:
                raise ValueError(f"unknown app {payload!r}")
            runner.run({"app": app_id, "params": {}}, "dissolve")
    elif entity == "playlist":
        if payload == NOTHING:
            runner.stop()
        else:
            runner.play_playlist(payload)
    elif entity == "schedule":
        on = payload.upper() == "ON"
        store.save_schedule({**store.schedule, "enabled": on})
        if on:
            runner.resume_schedule()
        runner.wake.set()
    elif entity == "quiet":
        save_settings({"quiet_hours": {"enabled": payload.upper() == "ON"}})
    elif entity == "message":
        if payload:
            notify({"text": payload}, source="home assistant")
    elif entity == "dnd":
        dnd = {**notifications.settings(store)["dnd"], "now": payload.upper() == "ON"}
        save_settings({"notifications": {"dnd": dnd}})
    elif entity == "timer":
        if payload:
            timers_mod.for_store(store).command(payload)
    else:
        raise ValueError(f"unknown entity {entity!r}")
    ha_sync()


def ws_message(msg):
    """Messages from browsers over the WebSocket (low-latency drawing and game input)."""
    if msg.get("type") == "input":
        runner.input(msg["key"], msg.get("action", "down"), int(msg.get("player", 1)))
    elif msg.get("type") == "frame":
        # a frame of a browser's live stream to the whole board (Import, Stream live): no HTTP round trip per frame
        board.check_frame(msg["rows"])
        if not runner.stream_frame(msg.get("stream"), "board"):
            return  # that stream was stopped here; frames still on their way are dropped
        board.set_frame(msg["rows"])
    elif msg.get("type") == "drawing_rows":
        # a whole picture for a drawing layer, e.g. live video streamed into a composition
        if msg.get("stream") and not runner.stream_frame(msg["stream"], "layer"):
            return  # that stream was stopped here; frames still on their way are dropped
        store.update_drawing(msg["drawing"], rows=msg["rows"], save=False)
        runner.wake.set()
    elif msg.get("type") == "party":
        party_api.ws(msg)  # quiz buzzer, Pictionary strokes, drum machine edits from /play
    elif msg.get("type") == "camera":
        # a grayscale frame from a browser's camera for the silhouette mirror (Mirror page); kept in memory only
        camera.get(store).push_browser(msg)
    elif msg.get("type") == "dots" and msg.get("drawing"):
        # drawing on a layer: leave whatever is running alone
        store.drawing_dots(msg["drawing"], [parse_dot(d) for d in msg["dots"]])
        runner.wake.set()
    elif msg.get("type") == "dots":
        runner.stop()
        board.set_dots([parse_dot(d) for d in msg["dots"]])
    else:
        raise ValueError(f"unknown message type {msg.get('type')!r}")


def audio_source():
    """Where the music visualizer's live audio comes from: "sendspin", "snapcast" or "off"."""
    return store.settings.get("visualizer", {}).get("source") or "off"


def start_snapcast():
    """(Re)start snapclient for the visualizer when Snapcast is the audio source, else stop it."""
    if audio_source() != "snapcast":
        snapcast_link.stop_link()
        return
    cfg = store.settings.get("snapcast", {})
    host = snapcast_link.resolve_host(cfg.get("host", "auto"), store.settings.get("music_assistant", {}).get("url"))
    snapcast_link.start_link(store.dir, host=host, port=int(cfg.get("port") or 1704),
                             control_port=int(cfg.get("control_port") or 1705), name=cfg.get("name") or "Flipdot board",
                             delay_ms=int(cfg.get("delay_ms") or snapcast_link.DELAY_MS))


def start_sendspin():
    cfg = store.settings.get("sendspin", {})
    # the Sendspin link can't be stopped while running; with another source the visualizer just ignores it
    if audio_source() != "sendspin" or not sendspin_link.AVAILABLE:
        return
    def show_code(code):
        # Music Assistant is asking for the pairing code: put it on the board
        if code:
            runner.notify(spec={"app": "text", "params": {"text": f"PAIR {code}", "style": "static", "fit": True}},
                          duration=120, transition="none", priority="high", key="sendspin-pair", source="sendspin",
                          dedupe=False)
        else:
            runner.dismiss(key="sendspin-pair")
    link = sendspin_link.start_link(store.dir, name=cfg.get("name") or "Flipdot board",
                                    port=int(cfg.get("port") or 8928), on_pair_code=show_code,
                                    server_url=cfg.get("server_url", "auto"))
    link.set_unpaired_access(bool(cfg.get("unpaired")))


def start_gamepad():
    global gamepad
    if gamepad:
        gamepad.stop()
        gamepad = None
    cfg = store.settings.get("gamepad", {})
    if cfg.get("enabled"):
        # holding Start opens the on-board game menu (apps/gamemenu.py); other input goes to runner.input
        gamepad = gamepad_mod.GamepadManager(gamemenu.GamepadMenu(runner, store).input,
                                             mapping=cfg.get("mapping") or {},
                                             deadzone=float(cfg.get("deadzone") or 0.35))
        gamepad.start()


def stop_gamepad():
    if gamepad:
        gamepad.stop()


def publish_status():
    if mqtt_client:
        mqtt_client.publish(f"{mqtt_client.prefix}/status", json.dumps(runner.status()), retain=True)
        ha_sync()


# ---- routes ---------------------------------------------------------------------

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return deco


def manual(fn):
    """Direct drawing stops whatever app/playlist is running."""
    def wrapper(req, *a):
        runner.stop()
        fn(req, *a)
        return state()
    return wrapper


@route("GET", "/api/board")
def get_board(req):
    return state()


@route("GET", "/api/dot")
def get_dot(req):
    x, y = int(req.query["x"][0]), int(req.query["y"][0])
    return {"x": x, "y": y, "color": "white" if board.get(x, y) else "black"}


@route("POST", "/api/dot")
@manual
def post_dot(req):
    board.set_dots([parse_dot(req.json())])


@route("POST", "/api/dots")
@manual
def post_dots(req):
    board.set_dots([parse_dot(d) for d in req.json()["dots"]])


@route("POST", "/api/frame")
def post_frame(req):
    """{"rows": [...]}; with "stream": {"id", "name"} it's a frame of a browser's live stream (Import page)."""
    body = req.json()
    if body.get("stream"):
        if not runner.stream_frame(body["stream"], "board"):
            return {"stopped": True, **state()}
    else:
        runner.stop()
    board.set_frame(body["rows"])
    return state()


@route("POST", "/api/stream/stop")
def post_stream_stop(req):
    """{"id": ...}: the browser ended its live stream (or its tab is closing: sent with sendBeacon)."""
    runner.end_stream(str(req.json().get("id") or ""), stopped=False)
    return {"ok": True}


@route("POST", "/api/fill")
@manual
def post_fill(req):
    board.fill(parse_color(req.json().get("color", "black")))


@route("POST", "/api/invert")
@manual
def post_invert(req):
    board.invert_all()


@route("POST", "/api/animation")
def post_animation(req):
    body = req.json()
    for f in body["frames"]:
        board.check_frame(f["rows"])
    runner.run_frames(body["frames"], loop=bool(body.get("loop", True)), name=body.get("name", "animation"))
    return state()


@route("POST", "/api/(?:animation/)?stop")
def post_stop(req):
    runner.stop()
    return state()


@route("GET", "/api/apps")
def get_apps(req):
    return {"apps": apps.catalog(), "transitions": TRANSITIONS}


@route("POST", "/api/run")
def post_run(req):
    body = req.json()
    if getattr(req, "role", "owner") != "owner":  # the player link may start games, nothing else
        cls = apps.REGISTRY.get(body.get("app"))
        if cls is None or cls.category != "Games":
            raise HttpError(403, "This link can only start games.")
        body = {"app": body["app"]}
    runner.run({"app": body["app"], "params": body.get("params") or {}}, body.get("transition", "none"),
               float(body.get("transition_duration") or 1.0))
    return state()


@route("POST", "/api/layers")
def post_layers(req):
    """Run (or live-update) a layer composition: {"layers": [...]} or {"composition": name}."""
    body = req.json()
    layers = body.get("layers")
    if layers is None:
        comp = store.compositions.get(body.get("composition") or "")
        if comp is None:
            raise ValueError("send 'layers' or the name of a saved 'composition'")
        layers = comp["layers"]
    for layer in layers:
        if layer.get("app") not in apps.REGISTRY:
            raise ValueError(f"unknown app {layer.get('app')!r}")
    runner.update_layers(layers, body.get("transition", "none"), float(body.get("transition_duration") or 1.0))
    # remember which saved set this is ("name" when the editor sends the layers of a saved set)
    if body.get("layers") is None or "name" in body:
        name = body.get("composition") if body.get("layers") is None else body.get("name")
        with runner.lock:
            if runner.app is not None and runner.app.id == "layers" and runner.app.name != (name or None):
                runner.app.name = name or None
                runner._changed()
    return state()


@route("GET", "/api/drawings")
def get_drawings(req):
    return {"drawings": store.list_drawings()}


@route("POST", "/api/drawings")
def create_drawing(req):
    """New drawing: {"name": ..., "rows": [...] (optional; blank if missing), "show": true to put it on the board}."""
    body = req.json()
    rows = body.get("rows")
    if rows == "board":
        rows = board.rows()
    did = store.create_drawing(body.get("name") or f"Drawing {len(store.drawings) + 1}", board.width, board.height, rows)
    if body.get("show"):
        bottom = body.get("position") == "bottom"
        runner.add_layer({"app": "drawing", "params": {"drawing": did},
                          "blend": body.get("blend") or ("normal" if bottom else "add")}, bottom=bottom)
    return {"id": did, "drawings": store.list_drawings(), **state()}


@route("POST", "/api/layers/add")
def add_layer(req):
    """Add one layer to what's on the board: {"layer": {...}, "position": "top" | "bottom"}."""
    body = req.json()
    layer = body["layer"]
    if layer.get("app") not in apps.REGISTRY:
        raise ValueError(f"unknown app {layer.get('app')!r}")
    bottom = body.get("position") == "bottom"
    layer.setdefault("blend", "normal" if bottom else "add")
    runner.add_layer(layer, bottom=bottom)
    return state()


@route("GET", "/api/drawings/([A-Za-z0-9_-]+)")
def get_drawing(req, did):
    return store.get_drawing(did)


@route("POST", "/api/drawings/([A-Za-z0-9_-]+)")
def update_drawing(req, did):
    """Replace a drawing's dots and/or rename it: {"rows": [...], "name": ...}. Doesn't stop what's running."""
    body = req.json()
    if body.get("stream") and not runner.stream_frame(body["stream"], "layer"):
        return {"stopped": True}
    store.update_drawing(did, rows=body.get("rows"), name=body.get("name"))
    runner.wake.set()
    return {"ok": True}


@route("POST", "/api/drawings/([A-Za-z0-9_-]+)/dots")
def drawing_dots(req, did):
    store.drawing_dots(did, [parse_dot(d) for d in req.json()["dots"]])
    runner.wake.set()
    return {"ok": True}


@route("POST", "/api/drawings/([A-Za-z0-9_-]+)/show")
def show_drawing(req, did):
    """Put a drawing on the board as the top layer, over whatever is running."""
    if did not in store.drawings:
        raise HttpError(404, "drawing not found")
    runner.add_layer({"app": "drawing", "params": {"drawing": did}, "blend": req.json().get("blend", "add")})
    return state()


@route("POST", "/api/drawings/([A-Za-z0-9_-]+)/delete")
def delete_drawing(req, did):
    store.delete_drawing(did)
    return {"drawings": store.list_drawings(), "cleaned": clean_refs("drawing", did)}


@route("GET", "/api/layers/current")
def get_current_layers(req):
    """The layers of the composition on the board right now (null if it isn't a composition)."""
    app = runner.app
    if app is None or app.id != "layers":
        return {"layers": None}
    return {"layers": [l.spec for l in app.layers], "name": app.name}


@route("GET", "/api/galleries")
def get_galleries(req):
    return {"galleries": [{"id": gid, "name": g["name"], "count": len(g["items"])} for gid, g in store.galleries.items()]}


@route("POST", "/api/galleries")
def create_gallery(req):
    gid = store.create_gallery(req.json().get("name") or f"Slideshow {len(store.galleries) + 1}")
    return {"id": gid}


def gallery_out(gid, g):
    from apps import slidefx  # the React to music table, filled in with its defaults
    return {"id": gid, **g, "settings": {**g["settings"], "react_map": slidefx.clean_react_map(g["settings"].get("react_map"))}}


@route("GET", "/api/galleries/(g[0-9]+)")
def get_gallery(req, gid):
    return gallery_out(gid, store.gallery(gid))


@route("POST", "/api/galleries/(g[0-9]+)")
def update_gallery(req, gid):
    """Change name / settings / items (reorder, remove, per-item seconds, loops or GIF speed)."""
    g = store.gallery(gid)
    body = req.json()
    if body.get("name"):
        g["name"] = str(body["name"])
    reconvert = False
    if isinstance(body.get("settings"), dict):
        new = {k: v for k, v in body["settings"].items() if k in store.GALLERY_DEFAULTS}
        if "react_map" in new:
            from apps import slidefx
            new["react_map"] = slidefx.clean_react_map(new["react_map"])
        if "react_fallback" in new and new["react_fallback"] not in ("none", "beat"):
            new["react_fallback"] = "none"
        reconvert = any(g["settings"].get(k) != v for k, v in new.items() if k in ("mode", "dither", "threshold", "invert"))
        g["settings"].update(new)
    if isinstance(body.get("items"), list):
        known = {i["file"]: i for i in g["items"]}
        new_items = []
        for it in body["items"]:
            if it.get("file") in known:
                item = dict(known.pop(it["file"]))
                for k in ("seconds", "loops", "speed"):
                    if it.get(k) in (None, ""):
                        item.pop(k, None)
                    elif k == "speed":
                        item[k] = max(0.25, min(4.0, float(it[k])))
                    else:
                        item[k] = float(it[k]) if k == "seconds" else int(it[k])
                new_items.append(item)
        for removed in known.values():  # dropped from the list: delete the file
            (store.gallery_dir(gid) / removed["file"]).unlink(missing_ok=True)
        if known:
            store.sync(f"media/{gid}")
        g["items"] = new_items
    store.save_galleries()
    if reconvert:
        with runner.lock:
            app = running_slideshow(gid)
            if app is not None:
                app.reload()  # the picture on the board follows the new look right away
    runner.wake.set()
    return gallery_out(gid, g)


@route("POST", "/api/galleries/(g[0-9]+)/upload")
def upload_gallery(req, gid):
    """Raw file bytes with ?name=FILE: an image, GIF, or a .zip of them (unpacked automatically)."""
    store.gallery(gid)
    name = req.query.get("name", ["upload"])[0]
    skip_covers = req.query.get("covers", ["skip"])[0] != "keep"
    added, skipped = media_upload.add_upload(store, gid, name, req.raw(), skip_covers)
    return {"added": len(added), "skipped": skipped, "id": gid, **store.gallery(gid)}


@route("GET", "/api/galleries/(g[0-9]+)/thumb")
def gallery_thumb(req, gid):
    """First frame of one item as dots, using the slideshow's conversion settings."""
    g = store.gallery(gid)
    fname = req.query["file"][0]
    if fname not in {i["file"] for i in g["items"]}:
        raise HttpError(404, "no such item")
    frames = slideshow.convert(store.gallery_dir(gid) / fname, g["settings"], board.width, board.height, first_only=True)
    return {"rows": frames[0][0].rows() if frames else None}


@route("POST", "/api/galleries/(g[0-9]+)/remove_covers")
def gallery_remove_covers(req, gid):
    removed = media_upload.remove_covers(store, gid)
    runner.wake.set()
    return {"removed": removed, "id": gid, **store.gallery(gid)}


def running_slideshow(gid):
    """The Slideshow app for this slideshow if it's on the board (directly or as a layer)."""
    app = runner.app
    if app is None or runner.notif:
        return None
    if app.id == "slideshow" and app.gid == gid:
        return app
    if app.id == "layers":
        for layer in app.layers:
            if layer.app.id == "slideshow" and layer.app.gid == gid:
                return layer.app
    return None


@route("POST", "/api/galleries/(g[0-9]+)/play")
def play_gallery_item(req, gid):
    """Play one item now: jump to it if this slideshow is already showing, otherwise start the slideshow there."""
    g = store.gallery(gid)
    file = req.json().get("file")
    if file and file not in {i["file"] for i in g["items"]}:
        raise HttpError(404, "no such item")
    with runner.lock:
        app = running_slideshow(gid)
        if app is not None:
            if file:
                app.jump(file, cut=runner.paused_at is not None and app is runner.app)
                runner.render_once = True  # shows it even while paused
            elif app is runner.app:
                runner.pause(False)  # Show on board for the slideshow that's already showing: carry on
            runner.wake.set()
            return state()
    params = {"gallery": gid}
    if file:
        params["start"] = file
    runner.run({"app": "slideshow", "params": params})
    return state()


@route("POST", "/api/galleries/(g[0-9]+)/delete")
def delete_gallery(req, gid):
    store.delete_gallery(gid)
    return {"ok": True, "cleaned": clean_refs("gallery", gid)}


# ---- videos (video.py converts and stores them, apps/video.py plays them) ----------------

STREAMED_UPLOADS = {"/api/videos/upload", "/api/storage/restore"}  # big bodies these routes read from req.rfile themselves


def video_display(req):
    return {k: v[0] for k, v in req.query.items() if k in video_mod.DISPLAY_DEFAULTS}


def running_video(vid=None):
    """The Video app on the board (directly, or the first video layer), optionally only if it plays `vid`."""
    app = runner.app
    if app is None or runner.notif:
        return None
    found = [app] if app.id == "video" else [l.app for l in app.layers if l.app.id == "video"] if app.id == "layers" else []
    return next((a for a in found if vid is None or a.vid == vid), None)


@route("GET", "/api/videos")
def get_videos(req):
    lib = video_mod.library
    return {"videos": lib.listing(), "ffmpeg": video_mod.have_ffmpeg(), "no_ffmpeg": video_mod.NO_FFMPEG,
            "settings": lib.settings()}


@route("POST", "/api/videos/upload")
def upload_video(req):
    """Raw file bytes (streamed to disk) with ?name=FILE&mode=crop|fit|stretch&fps=15&keep=0|1. Converts in the background."""
    q = {k: v[0] for k, v in req.query.items()}
    try:
        keep = None if "keep" not in q else q["keep"] in ("1", "true")
        vid = video_mod.library.receive(req.rfile, req.body_length, q.get("name") or "video", q.get("mode", "crop"),
                                        q.get("fps"), keep)
    except Exception:
        req.close_connection = True  # the rest of the body may still be on its way
        raise
    return {"id": vid, "videos": video_mod.library.listing()}


@route("GET", "/api/videos/(v[0-9]+)")
def get_video(req, vid):
    video_mod.library.get(vid)
    return video_mod.library.public(vid)


@route("POST", "/api/videos/(v[0-9]+)")
def update_video(req, vid):
    """{"name", "title" ("" = none), "show_title": "auto"|"on"|"off", "display": {dither, threshold, contrast, gamma,
    invert}, "range": {start, end, loop}}."""
    body = req.json()
    v = video_mod.library.update(vid, name=body.get("name"), title=body.get("title"), display=body.get("display"),
                                 rng=body.get("range"), show_title=body.get("show_title"))
    runner.wake.set()
    return v


@route("POST", "/api/videos/(v[0-9]+)/delete")
def delete_video(req, vid):
    """Deleted for real after video.UNDO_SECONDS, unless /restore comes first."""
    video_mod.library.delete(vid)
    return {"ok": True, "undo_seconds": video_mod.UNDO_SECONDS}


@route("POST", "/api/videos/(v[0-9]+)/restore")
def restore_video(req, vid):
    return video_mod.library.restore(vid)


@route("POST", "/api/videos/(v[0-9]+)/convert")
def reconvert_video(req, vid):
    """Convert again from the kept original: {"mode": "crop"|"fit"|"stretch", "fps": 15}."""
    body = req.json()
    video_mod.library.reconvert(vid, body.get("mode"), body.get("fps"))
    return video_mod.library.public(vid)


@route("GET", "/api/videos/(v[0-9]+)/frame")
def video_frame(req, vid):
    """?t=SECONDS (+ display options): that frame as dots, for the scrub preview."""
    t, rows = video_mod.library.frame_rows(vid, req.query.get("t", ["0"])[0], video_display(req))
    return {"t": t, "rows": rows}


@route("GET", "/api/videos/(v[0-9]+)/strip")
def video_strip(req, vid):
    return {"frames": [{"t": t, "rows": rows} for t, rows in video_mod.library.strip(vid, video_display(req))]}


@route("POST", "/api/videos/(v[0-9]+)/play")
def play_video(req, vid):
    """Show on board: {"t": seconds to start from, "start", "end", "loop", "speed"}; extra app params pass through."""
    body = req.json()
    v = video_mod.library.get(vid)
    if v.get("status") != "ready":
        raise ValueError(f"{v['name']} isn't converted yet.")
    rng = v.get("range") or {}
    params = {"video": vid, "start": rng.get("start", 0), "end": rng.get("end", 0), "loop": rng.get("loop", False),
              **{k: body[k] for k in ("start", "end", "loop", "speed", "show_title", "title_position") if k in body}}
    runner.run({"app": "video", "params": params}, body.get("transition", "none"))
    if body.get("t") is not None:
        with runner.lock:
            app = running_video(vid)
            if app is not None:
                app.seek(body["t"])
    return state()


@route("POST", "/api/videos/control")
def video_control(req):
    """For the video on the board: {"seek": seconds} | {"nudge": ±seconds} | {"speed", "loop", "start", "end"}."""
    body = req.json()
    with runner.lock:
        app = running_video(body.get("id"))
        if app is None:
            raise HttpError(409, "That video isn't on the board. Press Show on board first.")
        if "seek" in body:
            app.seek(body["seek"])
        elif "nudge" in body:
            app.seek(app.pos + float(body["nudge"]))
        app.set_options(**{k: body[k] for k in ("speed", "loop", "start", "end") if k in body})
        runner.render_once = True  # shows the new frame even while paused
        runner.wake.set()
    runner._changed()
    return state()


@route("GET", "/api/compositions")
def get_compositions(req):
    return {"compositions": store.compositions}


@route("POST", "/api/compositions")
def post_compositions(req):
    data = req.json()["compositions"]
    for name, comp in data.items():
        for layer in comp.get("layers", []):
            if layer.get("app") not in apps.REGISTRY:
                raise ValueError(f"composition {name}: unknown app {layer.get('app')!r}")
    store.save_compositions(data)
    return {"compositions": store.compositions}


# ---- references: renames follow through, deletes clean up (refs.py) ---------------------

def ref_data():
    """Deep copies of everything that can point at a saved thing, for refs.py to edit."""
    return json.loads(json.dumps({"playlists": store.playlists, "compositions": store.compositions,
                                  "schedule": store.schedule, "rules": automations.rules if automations else []}))


def save_ref_data(data, changed):
    if "playlists" in changed:
        store.save_playlists(data["playlists"])
    if "compositions" in changed:
        store.save_compositions(data["compositions"])
    if "schedule" in changed:
        store.save_schedule(data["schedule"])
    if "rules" in changed and automations:
        automations.save(data["rules"])
    if changed & {"playlists", "schedule"}:
        runner.wake.set()
        ha_sync()


def clean_refs(kind, key):
    data = ref_data()
    done, changed = refs.remove(data, kind, key)
    save_ref_data(data, changed)
    return done


@route("GET", "/api/refs")
def get_refs(req):
    """Where a playlist / composition (by name) or scene / gallery / drawing (by id) is used."""
    kind, key = req.query.get("kind", [""])[0], req.query.get("key", [""])[0]
    if kind not in refs.KINDS:
        raise ValueError(f"kind must be one of {', '.join(refs.KINDS)}")
    return {"used_by": refs.find(ref_data(), kind, key)}


def rename_named(kind, coll, old, new):
    """Rename a playlist or composition and everything that refers to it by name."""
    new = str(new or "").strip()
    if old not in coll:
        raise HttpError(404, f"no {kind} {old!r}")
    if not new:
        raise ValueError("the new name is empty")
    if new in coll and new != old:
        raise ValueError(f"there's already a {kind} called {new!r}")
    data = ref_data()
    key = "playlists" if kind == "playlist" else "compositions"
    data[key] = {(new if k == old else k): v for k, v in data[key].items()}  # keeps the order
    n, changed = refs.rename(data, kind, old, new)
    save_ref_data(data, changed | {key})
    with runner.lock:
        if kind == "playlist" and runner.playlist == old:
            runner.playlist = new
        if kind == "composition" and runner.app is not None and runner.app.id == "layers" and runner.app.name == old:
            runner.app.name = new
    return n


@route("POST", "/api/playlists/rename")
def rename_playlist(req):
    body = req.json()
    n = rename_named("playlist", store.playlists, body.get("old"), body.get("new"))
    return {"updated": n, "playlists": store.playlists, "schedule": store.schedule}


@route("POST", "/api/playlists/delete")
def delete_playlist(req):
    name = req.json().get("name")
    if name not in store.playlists:
        raise HttpError(404, f"no playlist {name!r}")
    pls = dict(store.playlists)
    del pls[name]
    store.save_playlists(pls)
    return {"cleaned": clean_refs("playlist", name), "playlists": store.playlists, "schedule": store.schedule}


@route("POST", "/api/compositions/rename")
def rename_composition(req):
    body = req.json()
    n = rename_named("composition", store.compositions, body.get("old"), body.get("new"))
    return {"updated": n, "compositions": store.compositions}


@route("POST", "/api/compositions/delete")
def delete_composition(req):
    name = req.json().get("name")
    if name not in store.compositions:
        raise HttpError(404, f"no composition {name!r}")
    comps = dict(store.compositions)
    del comps[name]
    store.save_compositions(comps)
    return {"cleaned": clean_refs("composition", name), "compositions": store.compositions}


@route("POST", "/api/input")
def post_input(req):
    body = req.json()
    ok = runner.input(body["key"], body.get("action", "down"), int(body.get("player", 1)))
    return {"ok": ok, "runner": runner.status()}


@route("GET|POST", "/api/notify")
def api_notify(req):
    body = {k: v[0] for k, v in req.query.items() if k != "token"} if req.command == "GET" else req.json()
    result = notify(body)
    return {**state(), "notify": result}


@route("GET|POST", "/api/notifications")
def api_notifications(req):
    """The notification queue: GET what's showing, waiting, held for do-not-disturb and the last 20 shown.
    POST {"action": "dismiss", "id"?, "key"?} (no id/key: the one on the board) | "clear" | "reshow" + id |
    "test" (+ priority, sound, icon)."""
    if req.command == "POST":
        body = req.json()
        action = body.get("action")
        if action == "dismiss":
            eid = int(body["id"]) if body.get("id") is not None else None
            if not runner.dismiss(eid, body.get("key")):
                raise HttpError(404, "no such notification")
        elif action == "clear":
            runner.clear_notifications()
        elif action == "reshow":
            runner.reshow(int(body["id"]))
        elif action == "test":
            notify({"text": "Test notification", "icon": body.get("icon") or "bell", "key": "test",
                    "priority": body.get("priority") or "normal", "sound": body.get("sound"),
                    "duration": body.get("duration")}, source="test")
        else:
            raise ValueError("action is dismiss, clear, reshow or test")
    return {**runner.notifications_status(), "icons": list(ICONS), "sounds": list(notifications.SOUNDS),
            "priorities": list(notifications.PRIORITIES), "ntfy": ntfy.status()}


@route("POST", "/api/next")
def post_next(req):
    runner.next()
    return state()


@route("POST", "/api/prev")
def post_prev(req):
    runner.prev()
    return state()


@route("POST", "/api/pause")
def post_pause(req):
    """{"paused": true|false}: freeze a slideshow or animation, or hold the current playlist item."""
    runner.pause(bool(req.json().get("paused", True)))
    return state()


@route("POST", "/api/params")
def post_params(req):
    """Live options: {"app": id, "params": {...}} restarts that app with these params if you started it."""
    body = req.json()
    applied = runner.update_params(body["app"], body.get("params") or {})
    return {"applied": applied, **state()}


@route("POST", "/api/schedule/resume")
def post_resume(req):
    runner.resume_schedule()
    return state()


@route("POST", "/api/playlist/play")
def post_play_playlist(req):
    runner.play_playlist(req.json()["name"])
    return state()


@route("GET", "/api/playlists")
def get_playlists(req):
    return {"playlists": store.playlists, "transitions": TRANSITIONS, "warnings": playlist_warnings(),
            **programme_api.playlist_extras()}


@route("POST", "/api/playlists")
def post_playlists(req):
    data = programme_api.clean_playlists(req.json()["playlists"])  # checks apps, tidies shuffle/effects/conditions
    store.save_playlists(data)
    ha_sync()  # the Home Assistant playlist list
    return {"playlists": store.playlists, "warnings": playlist_warnings()}


def playlist_warnings():
    """{playlist: {item index: what it needs}} for items that can't show anything until set up."""
    out = {}
    for name, pl in store.playlists.items():
        for i, item in enumerate(pl.get("items", [])):
            msg = apps.base.setup_needed(store, item)
            if msg:
                out.setdefault(name, {})[i] = msg
    return out


@route("GET", "/api/schedule")
def get_schedule(req):
    return programme_api.schedule_payload()  # older schedules come back in the current format


@route("POST", "/api/schedule")
def post_schedule(req):
    store.save_schedule(programme_api.clean_schedule(req.json()["schedule"]))
    runner.wake.set()
    ha_sync()
    return {"schedule": store.schedule}


@route("GET", "/api/scenes")
def get_scenes(req):
    return {"scenes": store.list_scenes()}


@route("GET", "/api/scenes/([a-z0-9_-]+)")
def get_scene(req, scene_id):
    scene = store.get_scene(scene_id)
    if scene is None:
        raise HttpError(404, "scene not found")
    return scene


@route("POST", "/api/scenes")
def post_scene(req):
    body = req.json()
    frames = body.get("frames") or [{"rows": board.rows(), "delay": 100}]
    for f in frames:
        board.check_frame(f["rows"])
    return {"id": store.save_scene(body.get("name") or "Untitled", frames, body.get("id"))}


@route("POST", "/api/scenes/([a-z0-9_-]+)/delete")
def delete_scene(req, scene_id):
    store.delete_scene(scene_id)
    return {"ok": True, "cleaned": clean_refs("scene", scene_id)}


# ---- AI drawing ---------------------------------------------------------------------

@route("GET", "/api/ai")
def get_ai(req):
    return {"models": aidraw.MODELS, "default_model": aidraw.DEFAULT_MODEL, "max_frames": aidraw.MAX_FRAMES,
            "configured": bool((store.settings.get("ai") or {}).get("api_key"))}


@route("POST", "/api/ai/draw")
def post_ai_draw(req):
    body = req.json()
    try:
        return aidraw.draw(store.settings, body.get("prompt"), body.get("frames", 1), body.get("style", ""),
                           board.width, board.height)
    except aidraw.AiError as e:
        raise HttpError(e.status, str(e)) from None


# ---- daily postcard and counters (apps/pack_daily.py) ---------------------------------

@route("POST", "/api/postcard")
def post_postcard(req):
    """Make today's postcard now (again, if it was already made)."""
    from apps import pack_daily
    try:
        entry = pack_daily.make_postcard(store, force=bool(req.json().get("force", True)), w=board.width, h=board.height)
    except pack_daily.NeedsSetup as e:
        raise HttpError(400, str(e)) from None
    except aidraw.AiError as e:
        raise HttpError(e.status, str(e)) from None
    runner.wake.set()
    return {"haiku": entry["haiku"], "rows": entry["rows"]}


@route("GET|POST", "/api/counter/([A-Za-z0-9_.-]+)")
def counter_value(req, name):
    """GET the counter's value; POST {"value": n} to set it or {"add": n} to change it (Counter app)."""
    from apps import pack_daily
    if req.command == "GET":
        rec = pack_daily.counters(store).get(name)
        if rec is None:
            raise HttpError(404, "no such counter")
        return {"name": name, **rec}
    body = req.json() if req.raw() else {}
    value = body.get("value", req.query.get("value", [None])[0])
    add = body.get("add", req.query.get("add", [None])[0])
    try:
        out = pack_daily.set_counter(store, name, value=value, add=add)
    except (TypeError, ValueError) as e:
        raise HttpError(400, str(e)) from None
    runner.wake.set()
    return out


# ---- guest wall -------------------------------------------------------------------

GUEST_PUBLIC = {("GET", "/guest"), ("POST", "/api/guest/submit")}  # the only paths that skip the password


def guest_submit(req):
    """Public: a guest posts a message. Rate limited per IP; the body size is checked before it's read."""
    ctype = req.headers.get("Content-Type", "")
    is_form = ctype.startswith("application/x-www-form-urlencoded")
    try:
        if is_form:
            body = {k: v[0] for k, v in parse_qs(req.raw().decode("utf-8", "replace")).items()}
        else:
            body = json.loads(req.raw() or b"{}")
            if not isinstance(body, dict):
                raise ValueError
    except ValueError:
        return 400, {"error": "Couldn't read the message."}, is_form
    if body.get("website"):  # hidden field only bots fill in
        return 200, {"ok": True}, is_form
    if not guestwall.clean(body.get("message"), guestwall.TEXT_MAX):
        return 400, {"error": "Write a message first."}, is_form
    wall = guestwall.get(store)
    if not wall.rate_ok(req.client_address[0]):
        return 429, {"error": "You've sent a few messages already. Please wait a few minutes."}, is_form
    try:
        msg = wall.submit(body.get("name"), body.get("message"), req.client_address[0])
    except ValueError as e:
        return 400, {"error": str(e)}, is_form
    return 200, {"ok": True, "status": msg["status"]}, is_form


@route("GET", "/api/guest")
def get_guest(req):
    return guestwall.get(store).info()


@route("POST", "/api/guest/settings")
def post_guest_settings(req):
    wall = guestwall.get(store)
    wall.set_auto_approve(bool(req.json().get("auto_approve")))
    return wall.info()


@route("POST", "/api/guest/messages/(m[0-9]+)/(approve|reject|delete)")
def post_guest_message(req, mid, action):
    wall = guestwall.get(store)
    try:
        if action == "delete":
            wall.delete(mid)
        else:
            wall.set_status(mid, "approved" if action == "approve" else "rejected")
    except KeyError:
        raise HttpError(404, "message not found (maybe already deleted)") from None
    return wall.info()


@route("GET", "/api/rules")
def get_rules(req):
    return automations.status()


@route("POST", "/api/rules")
def post_rules(req):
    automations.save(req.json()["rules"])
    return automations.status()


@route("POST", "/api/rules/([A-Za-z0-9_-]+)/run")
def run_rule(req, rid):
    """Run a rule's action now (ignores the trigger and the cooldown)."""
    rule = next((r for r in automations.rules if r["id"] == rid), None)
    if rule is None:
        raise HttpError(404, "no such rule")
    automations.fire(rule, force=True)
    if rid in automations.last_error:
        raise ValueError(automations.last_error[rid])
    return state()


@route("POST", "/api/event/([A-Za-z0-9_.:-]+)")
def post_event(req, name):
    """Trigger the automations waiting for this event name."""
    return {"event": name, "fired": automations.event(name)}


@route("GET|POST", "/api/timers")
def api_timers(req):
    """Kitchen / Pomodoro timers (timers.py). POST {"command": "pasta 8m"} or {"action": "start"|"pause"|"resume"|
    "toggle"|"cancel"|"dismiss"|"pomodoro", "name", "duration", "on", "work", "break"}; GET lists them."""
    box = timers_mod.for_store(store)
    out = box.command(req.json()) if req.command == "POST" else {}
    return {**out, **box.snapshot()}


def timer_rang(t):
    """A timer finished: unless the Timers app is on the board (it rings there), show its alarm over what's on."""
    with runner.lock:
        top = runner.notif["app"] if runner.notif else runner.app
        ids = {l.app.id for l in top.layers} if top is not None and top.id == "layers" else {getattr(top, "id", None)}
    if "timers" not in ids:
        name = str((t or {}).get("name") or "").strip()
        runner.notify(spec={"app": "timers", "params": {"alarm_only": True}}, duration=timers_mod.RING_S + 1,
                      transition="none", priority="high", icon="timer", key="timer-alarm", source="timer",
                      text=f"{name} timer done" if name else "Timer done", dedupe=False)


@route("GET", "/api/mirror")
def get_mirror(req):
    """Silhouette mirror: camera settings, what camera hardware there is, and live numbers (camera.py)."""
    cam = camera.get(store)
    return {**cam.status(), "takeover": cam.takeover.status() if getattr(cam, "takeover", None) else None}


@route("POST", "/api/mirror")
def post_mirror(req):
    save_settings({"mirror": camera.clean_settings(req.json())})
    camera.get(store).apply_settings()
    return get_mirror(req)


@route("POST", "/api/mirror/background")
def post_mirror_background(req):
    """Take the current picture as the empty room."""
    if not camera.get(store).capture_background():
        raise ValueError("the camera isn't sending pictures yet")
    return get_mirror(req)


@route("POST", "/api/mirror/frame")
def post_mirror_frame(req):
    """A browser camera frame over HTTP (when the WebSocket is down): {"w", "h", "data" (base64 gray), "id"}."""
    return {"used": camera.get(store).push_browser(req.json())}


@route("GET", "/api/mirror/preview")
def get_mirror_preview(req):
    """The mirror's picture right now, for the Mirror page (?mode=…). Keeps a Pi camera running while polled."""
    mode = (req.query.get("mode") or [""])[0]
    rows = camera.preview(runner.ctx, mode if mode in camera.MODES else "")
    cam = camera.get(store)
    return {"rows": rows, "live": cam.latest() is not None, "present": cam.presence.present,
            "fg": round(cam.snap.fg * 100, 1) if cam.snap else 0, "motion": round(cam.snap.motion * 100, 1) if cam.snap else 0,
            "ms": round(cam.ms, 2), "error": cam.error}


@route("GET", "/api/settings")
def get_settings(req):
    return masked_settings()


@route("POST", "/api/settings")
def post_settings(req):
    save_settings(req.json())
    return masked_settings()


@route("GET", "/api/geocode")
def get_geocode(req):
    return {"results": geocode(req.query["q"][0])}


@route("GET", "/api/fonts")
def get_fonts(req):
    return {"fonts": fonts.font_list()}


@route("POST", "/api/fonts")
def post_font(req):
    """Upload a font: raw file bytes, ?name=MyFont.ttf (TrueType/OpenType, or BDF/PCF bitmap fonts)."""
    name = Path(req.query.get("name", [""])[0]).name
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if not stem or ext not in (".ttf", ".otf", ".bdf", ".pcf"):
        raise ValueError("send a .ttf, .otf, .bdf or .pcf file with ?name=FILENAME")
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", stem)[:60] or "font"
    folder = Path(fonts.USER_FONT_DIR)
    if ext in (".ttf", ".otf"):
        from PIL import ImageFont
        try:
            ImageFont.truetype(io.BytesIO(req.raw()), 12)  # make sure it's a usable font
        except OSError:
            raise ValueError("that isn't a font file Pillow can read") from None
        (folder / f"{stem}{ext}").write_bytes(req.raw())
        font_id = f"user:{stem}{ext}"
    else:
        from PIL import BdfFontFile, PcfFontFile
        parser = BdfFontFile.BdfFontFile if ext == ".bdf" else PcfFontFile.PcfFontFile
        try:
            parser(io.BytesIO(req.raw())).save(str(folder / stem))  # writes .pil + .pbm
        except Exception:
            raise ValueError(f"that isn't a {ext} font file Pillow can read") from None
        font_id = f"user:{stem}.pil"
    store.sync(folder)
    fonts.get_font(font_id, 12)
    return {"id": font_id, "fonts": fonts.font_list()}


@route("POST", "/api/fonts/delete")
def delete_font(req):
    font_id = req.json()["id"]
    source = fonts.user_fonts().get(font_id)
    if source is None:
        raise ValueError("only uploaded fonts can be deleted")
    path = Path(source[1])
    for f in (path, path.with_suffix(".pbm")):
        f.unlink(missing_ok=True)
    store.sync(Path(fonts.USER_FONT_DIR))
    return {"fonts": fonts.font_list()}


@route("GET", "/api/nes/roms")
def get_nes_roms(req):
    """Uploaded NES ROMs, and whether the emulator is built."""
    return {"roms": nes.list_roms(store), "built": nes.built(), "setup": nes.SETUP_HINT, "max_bytes": nes.MAX_ROM,
            "mappers": [f"{n} {m}" for n, m in nes.MAPPERS.items()]}


@route("POST", "/api/nes/roms")
def post_nes_rom(req):
    """Upload a ROM: raw .nes file bytes with ?name=Game.nes. Checks the iNES header, the size and the mapper."""
    name = req.query.get("name", [""])[0]
    if Path(name).suffix.lower() != ".nes":
        raise ValueError("send a .nes file with ?name=FILENAME")
    data = req.raw()
    info = nes.parse_ines(data)
    problem = nes.mapper_problem(info["mapper"])
    if problem:
        raise ValueError(problem)
    folder = nes.rom_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    name = nes.safe_name(name)
    replaced = (folder / name).exists()
    tmp = folder / (name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(folder / name)
    store.sync(nes.ROM_DIR)
    return {"name": name, "replaced": replaced, "roms": nes.list_roms(store)}


@route("POST", "/api/nes/roms/delete")
def delete_nes_rom(req):
    """{"name": "Game.nes"}: removes the ROM and its saved game."""
    name = Path(str(req.json()["name"])).name
    path = nes.rom_dir(store) / name
    if path.suffix.lower() != ".nes" or not path.is_file():
        raise HttpError(404, f"no ROM called {name}")
    path.unlink()
    (Path(store.dir) / nes.SAVE_DIR / (path.stem + ".state")).unlink(missing_ok=True)
    store.sync(nes.ROM_DIR)
    return {"roms": nes.list_roms(store)}


@route("POST", "/api/render_text")
def post_render_text(req):
    body = req.json()
    if body.get("font"):
        if fonts.is_pixel(body["font"]):
            font, scale = fonts.get_font(body["font"]), max(1, int(body.get("scale") or 1))
        else:
            font, scale = fonts.get_font(body["font"], int(body.get("font_size") or 14)), 1
    else:
        font_name, scale = TEXT_SIZES.get(body.get("size", "normal"), TEXT_SIZES["normal"])
        font = FONTS[font_name]
    text = str(body.get("text", ""))[:200]
    c = Canvas(max(1, font.width(text) * scale), font.height * scale)
    c.text(0, 0, text, font, scale=scale)
    return {"rows": c.rows()}


@route("POST", "/api/decode")
def post_decode(req):
    """Split an uploaded image/GIF into frames (PNG, max 400px) for the browser converter."""
    from PIL import Image, ImageSequence
    img = Image.open(io.BytesIO(req.raw()))
    frames = []
    for frame in ImageSequence.Iterator(img):
        if len(frames) >= 500:
            break
        delay = frame.info.get("duration") or img.info.get("duration") or 100
        f = frame.convert("RGBA")
        f.thumbnail((400, 400))
        buf = io.BytesIO()
        f.save(buf, "PNG")
        frames.append({"png": base64.b64encode(buf.getvalue()).decode(), "delay": delay if delay > 10 else 100})
    return {"frames": frames}


@route("POST", "/api/image")
def post_image(req):
    """Convert an image on the server: raw image bytes (options in the query string) or JSON."""
    if (req.headers.get("Content-Type") or "").startswith("application/json"):
        body = req.json()
        frames = convert_from_body(body)
    else:
        body = {k: v[0] for k, v in req.query.items() if k != "token"}
        for k in ("dither", "invert", "loop", "show"):
            if k in body:
                body[k] = body[k].lower() in ("1", "true", "yes")
        frames = convert_from_body(body, req.raw())
    if body.get("save"):
        store.save_scene(str(body["save"]), frames)
    if body.get("show", True):
        if len(frames) == 1:
            runner.stop()
            board.set_frame(frames[0]["rows"])
        else:
            runner.run_frames(frames, loop=bool(body.get("loop", True)), name="image")
    return {"frames": len(frames), **state()}


@route("GET", "/api/gamepad")
def get_gamepad(req):
    st = gamepad.status() if gamepad else {"running": False, "message": "Off", "pads": [], "events": []}
    return {**st, "defaults": gamepad_mod.DEFAULT_MAPPING}


@route("GET", "/api/audio/levels")
def audio_levels(req):
    """Live music levels for the slideshow's React to music meter (polled a few times a second)."""
    import audio_signal
    fallback = "beat" if (req.query.get("fallback") or [""])[0] == "beat" else None
    r = audio_signal.reading(store, fallback)
    from boardcare import quiet_active
    import datetime as dt
    return {**audio_signal.status(store), "live": r["live"], "generated": r["generated"],
            "quiet": quiet_active(store.settings.get("quiet_hours") or {}, dt.datetime.now()),
            "levels": {k: round(r[k], 3) for k in ("bass", "lowmid", "mid", "high", "level")},
            # hits fade slower here than in the reading, so a meter polled a few times a second still shows them
            "hits": {k: round(math.exp(-max(0.0, a) / 0.3), 3) for k, a in r["hit_age"].items()},
            "beat": round(r["pulse"], 3), "bpm": round(r["bpm"]),
            "bpm_sure": r["bpm_confidence"] >= audio_signal.TEMPO_SURE, "groove": round(r["groove"], 2)}


@route("GET", "/api/snapcast")
def get_snapcast(req):
    link = snapcast_link.get_link()
    if link is not None:
        return link.describe()
    path = snapcast_link.binary()
    return {"available": bool(path), "version": snapcast_link.snapclient_version(path) if path else None,
            "connected": False, "status": "off" if path else "snapclient isn't installed (sudo apt install snapclient)"}


@route("GET", "/api/sendspin")
def get_sendspin(req):
    link = sendspin_link.get_link()
    if link is None:
        return {"available": sendspin_link.AVAILABLE, "status": "off" if sendspin_link.AVAILABLE
                else "aiosendspin isn't installed (pip install -r requirements-sendspin.txt)", "connected": False}
    return link.describe()


@route("GET", "/api/sendspin/discover")
def sendspin_discover(req):
    """Music Assistant / Sendspin servers advertising on the network."""
    if not sendspin_link.AVAILABLE:
        raise ValueError("aiosendspin isn't installed (pip install -r requirements-sendspin.txt)")
    return {"servers": sendspin_link.discover_servers(3.0)}


@route("POST", "/api/sendspin/pair")
def sendspin_pair(req):
    link = sendspin_link.get_link()
    if link is None or not link.open_pairing_window():
        raise ValueError("Music Assistant hasn't connected yet — add the board as a Sendspin player in Music Assistant first")
    return {"ok": True}


@route("GET", "/api/music/players")
def get_music_players(req):
    """List Music Assistant players (also a connection test for the Settings tab)."""
    return {"players": music.list_players(store)}


@route("GET", "/api/wear")
def get_wear(req):
    """Flips per disc since tracking started: total, per-disc counts (row-major), most/least flipped."""
    return care.stats()


@route("POST", "/api/wear/exercise")
def post_wear_exercise(req):
    return {**care.exercise(int(req.json().get("times") or 3)), **state()}


@route("GET", "/api/scores")
def get_scores(req):
    """The games' high-score tables: {game key: {"lower", "top": [{score, name, at}]}}."""
    from apps import scores
    return {"scores": scores.everything(store)}


@route("GET", "/api/panels")
def get_panels(req):
    return {"panels": panelsetup.panel_list(board), "panel_width": board.pw, "panel_height": board.ph,
            "width": board.width, "height": board.height}


@route("POST", "/api/panels")
def post_panels(req):
    """Save a new panel mapping to config.local.yaml and apply it right away (when the board size stays the same)."""
    panels = panelsetup.validate(req.json().get("panels"))
    localconfig.update(config_path, {"panels": panels})  # config.local.yaml, not the tracked config.yaml
    backups_mod.save_config_copy(persist, config_path)
    try:
        board.set_panels(panels)
    except ValueError as e:
        return {"saved": True, "applied": False, "message": f"Saved. {e}", "panels": panels}
    runner.wake.set()
    return {"saved": True, "applied": True, "panels": panelsetup.panel_list(board)}


@route("GET", "/api/board/timing")
def get_board_timing(req):
    return board.timing()


@route("POST", "/api/board/timing")
def post_board_timing(req):
    """{"sweep_ms": 40, "sync_refresh": true, "sweep_mode": "auto"}: change how panel columns are timed, live, to tune
    it by eye. Not saved: put the values that look right in config.yaml."""
    body = req.json()
    return board.set_timing(body.get("sweep_ms"), body.get("sync_refresh"), body.get("sweep_mode"))


@route("POST", "/api/board/refresh")
def post_board_refresh(req):
    """Resend every panel now, and retry buses that can't be reached without waiting (Retry now)."""
    board.refresh()
    return {"ok": True, "link": board.link_status()}


@route("GET", "/api/health")
def get_health(req):
    """Runner heartbeat, every bus, temperature, throttling, disk, memory, uptime, version, and an overall
    status: ok, degraded or failing (with the reasons). Same password as the rest of the API."""
    out = health.report(board, runner, data_dir, storage_path)
    if backups:
        st = backups.status()
        out["backup"] = {k: st[k] for k in ("last", "last_auto", "next")}
    return out


@route("POST", "/api/panels/identify")
def post_panels_identify(req):
    """Label panels on the board: {"index": n} for one panel (with an outline), or no index for all."""
    index = req.json().get("index")
    if index is not None and not 0 <= int(index) < len(board.panels):
        raise ValueError("no such panel")
    runner.run_frames(panelsetup.identify_frames(board, None if index is None else int(index)), name="panel setup")
    return state()


@route("GET", "/api/timelapse")
def get_timelapse(req):
    return recorder.info()


@route("GET", r"/api/timelapse\.gif")
def get_timelapse_gif(req):
    """?minutes=5&speed=10&scale=4 — the last minutes of the board as an animated GIF."""
    q = lambda k, d: float(req.query.get(k, [d])[0])
    minutes = max(0.1, min(timelapse.MAX_SECONDS / 60, q("minutes", 5)))
    speed = max(1.0, min(600.0, q("speed", 10)))
    scale = int(max(2, min(12, q("scale", 4))))
    gif = timelapse.render_gif(recorder, minutes, speed, scale)
    return Raw(gif, "image/gif", {"Content-Disposition": 'attachment; filename="flipdot-timelapse.gif"'})


@route("GET", r"/api/sync/click\.wav")
def get_click_track(req):
    """?period=1.013&seconds=120 — the click track for the Sync test app (play it to the board's Snapcast group)."""
    from apps import synctest
    q = lambda k, d: float(req.query.get(k, [d])[0])
    wav = synctest.click_wav(q("period", synctest.CLICK_PERIOD), q("seconds", 120))
    return Raw(wav, "audio/wav", {"Content-Disposition": 'attachment; filename="flipdot-click-track.wav"'})


@route("GET", "/api/storage")
def get_storage(req):
    return persist.info()


@route("GET", r"/api/storage/backup")
def get_storage_backup(req):
    """A consistent copy of storage.db (settings, playlists, scenes, drawings, slideshows, fonts…)."""
    persist.mirror_once()  # include anything written in the last few seconds
    backups_mod.save_config_copy(persist, config_path)  # config.local.yaml goes in the backup too
    fd, tmp = tempfile.mkstemp(prefix="backup-", suffix=".db", dir=persist.path.parent)
    os.close(fd)
    try:
        persist.backup_to(tmp)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    name = f"flipdot-storage-{time.strftime('%Y%m%d-%H%M')}.db"
    return RawFile(tmp, "application/vnd.sqlite3", {"Content-Disposition": f'attachment; filename="{name}"'}, temporary=True)


@route("POST", r"/api/storage/restore")
def post_storage_restore(req):
    """Raw bytes of a backup .db (streamed to disk), or ?name=<a backup in the backups folder>. It's checked now and
    replaces the database (and config.local.yaml) when the server next starts."""
    try:
        if req.query.get("confirm", [""])[0] != "1":
            raise ValueError("add ?confirm=1 to replace all saved data with this backup")
        name = req.query.get("name", [""])[0]
        if name:
            files = persist.stage_restore_file(backups.path(name))
        else:
            if req.body_length <= 0:
                raise ValueError("send the backup file as the request body")
            free = shutil.disk_usage(persist.path.parent).free
            if req.body_length + 50 * 2**20 > free:
                raise ValueError(f"not enough free space for the upload: {req.body_length // 2**20 + 1} MB "
                                 f"(+50 MB spare) needed, {free // 2**20} MB free")
            files = persist.stage_restore_stream(req.rfile, req.body_length)
            req.body_length = 0
    finally:
        if req.body_length:
            req.close_connection = True  # the unread body would otherwise be taken for the next request
    return {**persist.info(), "ok": True, "files": files,
            "message": f"Backup checked ({files} files). Restart the server to restore it; until then nothing changes."}


# ---- backups folder, version, restart and update (backups.py, updater.py) ----------------

@route("GET", "/api/backups")
def get_backups(req):
    return backups.status()


@route("POST", "/api/backups")
def post_backups(req):
    """Make a backup in the backups folder now."""
    entry = backups.make("manual")
    return {"ok": True, "backup": entry, **backups.status()}


@route("POST", "/api/backups/settings")
def post_backup_settings(req):
    """{"schedule": "off|daily|weekly", "time": "03:00", "weekday": 0-6 (Monday = 0), "folder": "../backups", "keep": 7}"""
    clean = backups_mod.clean_settings(req.json())
    save_settings({"backup": clean})
    if "keep" in clean:
        backups.rotate(clean["keep"])
    return backups.status()


@route("GET", r"/api/backups/(flipdot-backup-[0-9a-z-]+\.db)")
def get_backup_file(req, name):
    return RawFile(backups.path(name), "application/vnd.sqlite3", {"Content-Disposition": f'attachment; filename="{name}"'})


@route("POST", r"/api/backups/(flipdot-backup-[0-9a-z-]+\.db)/delete")
def delete_backup_file(req, name):
    backups.delete(name)
    return backups.status()


@route("GET", "/api/system")
def get_system(req):
    """Version (git describe, commit and its date), uptime, and how a restart works here."""
    return {**health.version_info(), "uptime_s": round(time.time() - health.STARTED), "started": health.STARTED,
            "systemd": updater_mod.under_systemd(), "python": sys.executable,
            "venv": sys.prefix != sys.base_prefix, "update": updater.status() if updater else None,
            "update_source": dict(zip(("remote", "branch"), updater.target())) if updater else None}


@route("POST", r"/api/(?:system/)?restart")
def post_restart(req):
    return updater_mod.restart()


@route("GET", "/api/update")
def get_update(req):
    return updater.status()


@route("POST", "/api/update/check")
def post_update_check(req):
    try:
        return updater.check()
    except updater_mod.UpdateError as e:
        raise HttpError(409, str(e)) from None


@route("POST", "/api/update")
def post_update(req):
    """Back up, git pull --ff-only, install changed requirements, self-check, restart; rolls back on failure.
    Runs in the background: poll GET /api/update."""
    try:
        return updater.start()
    except updater_mod.UpdateError as e:
        raise HttpError(409, str(e)) from None


@route("POST", "/api/update/source")
def post_update_source(req):
    """{"remote": "origin", "branch": "main"} ("" = the current branch's upstream)."""
    body = req.json()
    clean = {}
    for k in ("remote", "branch"):
        if k in body:
            v = str(body[k] or "").strip()
            if v and not re.fullmatch(r"[^-\s]\S{0,500}", v):  # a name, URL or path; never an option
                raise ValueError(f"{v!r} isn't a valid git {k} name")
            clean[k] = v
    save_settings({"update": clean})
    return {"update_source": dict(zip(("remote", "branch"), updater.target()))}


@route("POST", "/api/update/reset-config")
def post_update_reset_config(req):
    """Put config.yaml back to the committed version when all its edits are kept in config.local.yaml."""
    try:
        return updater.reset_config()
    except updater_mod.UpdateError as e:
        raise HttpError(409, str(e)) from None


friends_api.install(sys.modules[__name__])  # People & access, friend messages (friends_api.py)
programme_api.install(sys.modules[__name__])  # Add to playlist, schedule preview: owner-only (programme_api.py)
party_api.install(sys.modules[__name__])  # party games' /play modes: player + owner (party_api.py)
plugins_api.install(sys.modules[__name__])  # Settings → Plugins: Python plugins and Pixlet apps, owner-only


# ---- HTTP plumbing ----------------------------------------------------------------

class StaticFile:
    """A page, script or stylesheet held in memory (gzipped too), so a page open is a few 304s rather than
    reading 17 files off the SD card and sending 400 KB every time."""

    def __init__(self, f, st):
        self.key = (st.st_mtime_ns, st.st_size)
        self.body = f.read_bytes()
        self.etag = '"%x-%x"' % self.key
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        text = ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml"))
        self.ctype = ctype + "; charset=utf-8" if text else ctype
        self.gz = gzip.compress(self.body, 6) if text and len(self.body) > 1024 else None


_static_files = {}
_static_lock = threading.Lock()


def static_file(f):
    st = f.stat()
    with _static_lock:
        entry = _static_files.get(f)
        if entry is None or entry.key != (st.st_mtime_ns, st.st_size):  # edited on disk: pick up the new one
            entry = _static_files[f] = StaticFile(f, st)
        return entry


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def raw(self):
        return self._raw

    def json(self):
        return json.loads(self.raw() or b"{}")

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rf):
        try:
            with open(rf.path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", rf.ctype)
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                for k, v in rf.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                shutil.copyfileobj(f, self.wfile, 1 << 20)
        finally:
            if rf.temporary:
                rf.path.unlink(missing_ok=True)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _authorized(self):
        return self._access()[0] == "owner"

    def _access(self):
        """(role, link record): "owner" with the password (or when there is none), a link's role
        (access.py) with a valid link token, else (None, None), or (None, "bad") for a link that stopped working."""
        q = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        auth = self.headers.get("Authorization", "")
        try:
            if password and q and secrets.compare_digest(q, password):
                return "owner", None
            if password and auth.startswith("Basic "):
                _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                if secrets.compare_digest(pw, password):
                    return "owner", None
        except (ValueError, TypeError):
            pass
        bearer = auth[7:].strip() if auth.startswith("Bearer ") else ""
        token = bearer or q or access.cookie_token(self.headers.get("Cookie"))
        if token and store is not None:
            rec = access.get(store).check(token)
            if rec:
                return rec["role"], rec
            if bearer or token != q:  # (a wrong ?token= is just a wrong password)
                return None, "bad"
        return ("owner", None) if not password else (None, None)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        url = urlparse(self.path)
        self.query = parse_qs(url.query)
        # Read the body up front, every request: the handler object is reused for all requests on a
        # keep-alive connection, and an unread body would corrupt the next request.
        public = (self.command, url.path) in GUEST_PUBLIC
        self.role, self.token_rec = self._access()
        scoped = self.role not in ("owner", None)  # someone with a player, friend or viewer link
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or (public and length > guestwall.BODY_MAX) or (scoped and length > access.BODY_MAX):
            self.close_connection = True  # don't read a body like that; drop the connection instead
            return self._json({"error": "That message is too long." if length > 0 else "bad Content-Length"}, 413 if length > 0 else 400)
        streamed = self.command == "POST" and url.path in STREAMED_UPLOADS
        if streamed:
            self._raw, self.body_length = b"", length  # the route reads the body itself, straight to disk
        else:
            self._raw = self.rfile.read(length)
        if public:
            return self._guest(url)
        if self.command == "GET" and url.path.startswith("/join/"):
            return self._join(url.path[len("/join/"):])
        if url.path == "/leave":
            return self._send(303, b"", "text/plain", {"Location": "/", "Set-Cookie": access.clear_cookie()})
        if self.role is None:
            if streamed:
                self.close_connection = True  # the unread body would otherwise be taken for the next request
            if self.token_rec == "bad":  # a revoked or expired link: forget it here, and say so
                gone = {"Set-Cookie": access.clear_cookie(), "Cache-Control": "no-store"}
                if url.path.startswith("/api/") or url.path == "/ws":
                    return self._send(401, json.dumps({"error": access.GONE}).encode(), "application/json", gone)
                return self._send(401, access.GONE_PAGE, "text/html; charset=utf-8", gone)
            return self._send(401, b"password required", "text/plain", {"WWW-Authenticate": 'Basic realm="flipdot"'})
        guestwall.note_host(self.headers.get("Host"))
        if url.path == "/ws":
            if scoped and (self.role not in access.WS_TYPES or not access.same_origin(self.headers)):
                return self._json({"error": "not allowed with this link"}, 403)
            return self._websocket()
        if self.command == "GET" and not url.path.startswith("/api/"):
            if not access.page_allowed(self.role, url.path):
                if url.path in ("/", "/index.html") and self.role in access.PAGES:
                    return self._send(302, b"", "text/plain", {"Location": access.PAGES[self.role]})
                return self._json({"error": "not allowed with this link"}, 403)
            return self._static(url.path)
        for method, pattern, fn in ROUTES:
            m = pattern.match(url.path)
            if m and self.command in method.split("|"):
                if not access.allowed(self.role, self.command, pattern.pattern[1:-1]):
                    return self._json({"error": "not allowed with this link"}, 403)
                try:
                    result = fn(self, *m.groups())
                except HttpError as e:
                    return self._json({"error": str(e)}, e.status)
                except (KeyError, ValueError, TypeError, OSError) as e:
                    msg = f"missing field {e}" if isinstance(e, KeyError) else str(e)
                    return self._json({"error": msg}, 400)
                except Exception as e:
                    logging.exception("error handling %s", url.path)
                    return self._json({"error": str(e)}, 500)
                if isinstance(result, Raw):
                    return self._send(200, result.body, result.ctype, result.headers)
                if isinstance(result, RawFile):
                    return self._send_file(result)
                if isinstance(result, Redirect):
                    return self._send(302, b"", "text/plain", {"Location": result.url})
                return self._json(result if result is not None else {"ok": True})
        self._json({"error": "not found"}, 404)

    def _join(self, token):
        """A share link: remember its token in a cookie and open the role's page."""
        rec = access.get(store).check(token) if access.TOKEN_RE.fullmatch(token) else None
        if rec is None:
            return self._send(404, access.GONE_PAGE, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
        return self._send(303, b"", "text/plain", {"Location": access.PAGES[rec["role"]], "Cache-Control": "no-store",
                                                  "Referrer-Policy": "no-referrer",
                                                  "Set-Cookie": access.set_cookie(token, rec.get("expires"))})

    def _guest(self, url):
        if self.command == "GET":
            return self._static("/guest")
        status, body, is_form = guest_submit(self)
        if is_form:  # plain HTML form post (no JavaScript): back to the page with the result
            q = "sent=1" if status == 200 else "error=" + quote(body.get("error", ""))
            return self._send(303, b"", "text/plain", {"Location": "/guest?" + q})
        return self._json(body, status)

    def _websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            return self._json({"error": "expected a WebSocket upgrade"}, 400)
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key(key))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        types = access.WS_TYPES.get(self.role) if self.role != "owner" else None

        def limited(msg):  # a player link sends game input only; a viewer only watches
            if msg.get("type") not in types:
                raise PermissionError("not allowed with this link")
            ws_message(msg)
        hub.serve(self.connection, self.rfile, limited if types is not None else None)

    def _static(self, path):
        page = {"/": "index.html", "/play": "play.html", "/guest": "guest.html", "/friend": "friend.html",
                "/view": "view.html"}.get(path, path.lstrip("/"))
        if page.startswith("static/"):
            page = page[len("static/"):]
        f = (STATIC / page).resolve()
        if not f.is_file() or STATIC.resolve() not in f.parents:
            return self._json({"error": "not found"}, 404)
        entry = static_file(f)
        headers = {"Cache-Control": "no-cache", "ETag": entry.etag, "Vary": "Accept-Encoding"}
        if entry.etag in self.headers.get("If-None-Match", ""):
            return self._send(304, b"", entry.ctype, headers)  # the browser's copy is current: no bytes to send
        body = entry.body
        if entry.gz is not None and "gzip" in self.headers.get("Accept-Encoding", ""):
            body, headers["Content-Encoding"] = entry.gz, "gzip"
        self._send(200, body, entry.ctype, headers)

    def log_message(self, fmt, *args):
        logging.debug(fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # default is 5, which drops connections when several clients poll


def main():
    global board, store, runner, password, hub, care, recorder, config_path, automations, persist, data_dir, storage_path
    global backups, updater
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=ROOT / "config.yaml")
    ap.add_argument("--data", default=ROOT / "data", help="working copy of the saved data (restored from --storage at startup)")
    ap.add_argument("--storage", default=ROOT.parent / "storage.db",
                    help="SQLite database that keeps settings, scenes, slideshows… (default: ../storage.db, outside the app folder)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dry-run", action="store_true", help="don't talk to the hardware")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")

    config_path = args.config
    data_dir, storage_path = args.data, args.storage
    guestwall.server_port = args.port
    persist = storage_mod.Storage(args.storage, args.data)  # first: a restored backup brings its config.local.yaml
    if persist.restored_backup:
        backups_mod.restore_config_copy(persist, args.config)
    try:
        localconfig.migrate(args.config)  # first run: hand edits of config.yaml move into config.local.yaml
    except OSError as e:
        logging.warning("config: can't create %s: %s", localconfig.local_path(args.config), e)
    config = localconfig.load(args.config)  # config.yaml with config.local.yaml on top
    password = str(config.get("password") or "")
    board = Board(config, dry_run=args.dry_run)
    persist.startup()  # restore data/ from the database before anything reads it
    backups_mod.save_config_copy(persist, args.config)
    store = Store(args.data, persist=persist)
    fonts.USER_FONT_DIR = Path(args.data) / "fonts"
    fonts.USER_FONT_DIR.mkdir(parents=True, exist_ok=True)
    video_mod.library = video_mod.Library(store, board.width, board.height, on_purge=lambda vid: clean_refs("video", vid))
    runner = Runner(board, store)
    apps.base.notify = runner.notify  # apps that raise a notification themselves (the hourglass running out)
    plugins_api.start(store, notify=runner.notify)  # plugin apps exist before a playlist or the schedule needs them
    care = BoardCare(board, runner, store)
    atexit.register(persist.flush)  # runs last (atexit is LIFO), after the wear counts are written
    atexit.register(care.save)  # keep wear counts across restarts
    persist.start_mirror()
    backups = backups_mod.Backups(persist, lambda: store.settings.get("backup"), ROOT, args.config)
    backups.start()
    updater = updater_mod.Updater(ROOT, settings=lambda: store.settings.get("update"), backup=backups.make,
                                  restart=updater_mod.restart, state_path=Path(f"{args.storage}.update.json"),
                                  config_name=Path(args.config).name if Path(args.config).resolve().parent == ROOT.resolve() else "",
                                  reset_config_ok=lambda: localconfig.covers_edits(args.config))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    recorder = timelapse.Recorder(board)
    automations = rules.Automations(runner, board, store, notify)
    timer_box = timers_mod.for_store(store)  # kitchen timers ring even when their app isn't on the board
    timer_box.on_ring = timer_rang
    timer_box.on_change = lambda: threading.Thread(target=ha_sync, daemon=True).start()
    timer_box.watch()
    friends.get(store).start(runner, (board.width, board.height))  # friend messages: due, scheduled and pinned
    camera.setup(store, runner, automations, board.width, board.height)  # silhouette mirror and its takeover
    from apps import pack_daily
    pack_daily.start_scheduler(store, board.width, board.height)  # the morning postcard, once its app is set up
    atexit.register(camera.get().stop)
    runner.listeners.append(lambda: mqtt_client and threading.Thread(target=publish_status, daemon=True).start())
    hub = Hub(board, runner, ws_message)
    board.listeners.append(hub.notify)
    runner.listeners.append(hub.notify)
    runner.ticks.append(hub.notify)  # a playing video's position, live on every page
    if store.schedule.get("enabled"):
        runner.resume_schedule()
    start_mqtt()
    start_ntfy()
    start_sendspin()
    start_snapcast()  # snapcast_link stops snapclient at exit
    start_gamepad()
    atexit.register(stop_gamepad)

    logging.info("%dx%d board, %d panels%s", board.width, board.height, len(board.panels),
                 " (dry run)" if args.dry_run else "")
    logging.info("open http://localhost:%d%s", args.port, " (password protected)" if password else "")
    httpd = Server((args.host, args.port), Handler)
    # systemd (Type=notify): up and serving; then watchdog pings for as long as the runner keeps ticking
    health.sd_notify("READY=1")
    health.start_watchdog(lambda: time.monotonic() - runner.beat)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
