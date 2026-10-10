"""HTTP API + web UI for the flipdot board.

    python3 server.py              # talk to the board
    python3 server.py --dry-run    # no hardware, just the web page/API
"""

import argparse
import atexit
import base64
import io
import json
import logging
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import aidraw
import gamepad as gamepad_mod
import apps
import guestwall
import media_upload
import panelsetup
import rules
import sendspin_link
import storage as storage_mod
import timelapse
from apps import slideshow
from apps import music
from apps.media import convert_image
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


def notify(body):
    """Show something temporarily, then go back to whatever was running."""
    duration = float(body.get("duration", 10))
    transition = body.get("transition", "dissolve")
    tdur = float(body.get("transition_duration") or 1.0)
    if body.get("image") or body.get("url"):
        frames = convert_from_body(body)
        app = apps.REGISTRY["scene"](runner.ctx, frames=frames, name="notification")
        return runner.notify(app=app, duration=duration, transition=transition, transition_duration=tdur)
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
    runner.notify(spec=spec, duration=duration, transition=transition, transition_duration=tdur)


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
    if merged.get("mqtt") != old.get("mqtt"):
        start_mqtt()
    else:
        ha_sync()
    if merged.get("sendspin") != old.get("sendspin"):
        start_sendspin()
        link = sendspin_link.get_link()
        if link and merged["sendspin"].get("unpaired") != old.get("sendspin", {}).get("unpaired"):
            link.set_unpaired_access(bool(merged["sendspin"].get("unpaired")))
    if merged.get("gamepad") != old.get("gamepad"):
        start_gamepad()


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
            notify({"text": payload})
    else:
        raise ValueError(f"unknown entity {entity!r}")
    ha_sync()


def ws_message(msg):
    """Messages from browsers over the WebSocket (low-latency drawing and game input)."""
    if msg.get("type") == "input":
        runner.input(msg["key"], msg.get("action", "down"), int(msg.get("player", 1)))
    elif msg.get("type") == "drawing_rows":
        # a whole picture for a drawing layer, e.g. live video streamed into a composition
        store.update_drawing(msg["drawing"], rows=msg["rows"], save=False)
        runner.wake.set()
    elif msg.get("type") == "dots" and msg.get("drawing"):
        # drawing on a layer: leave whatever is running alone
        store.drawing_dots(msg["drawing"], [parse_dot(d) for d in msg["dots"]])
        runner.wake.set()
    elif msg.get("type") == "dots":
        runner.stop()
        board.set_dots([parse_dot(d) for d in msg["dots"]])
    else:
        raise ValueError(f"unknown message type {msg.get('type')!r}")


def start_sendspin():
    cfg = store.settings.get("sendspin", {})
    if not cfg.get("enabled") or not sendspin_link.AVAILABLE:
        return
    def show_code(code):
        # Music Assistant is asking for the pairing code: put it on the board
        if code:
            runner.notify(spec={"app": "text", "params": {"text": f"PAIR {code}", "style": "static", "fit": True}},
                          duration=120, transition="none")
        elif runner.notif:
            runner.notif["until"] = 0
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
        gamepad = gamepad_mod.GamepadManager(lambda key, action, player: runner.input(key, action, player),
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
@manual
def post_frame(req):
    board.set_frame(req.json()["rows"])


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
    if body.get("composition") and body.get("layers") is None:
        if runner.app is not None and runner.app.id == "layers":
            runner.app.name = body["composition"]
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
    return {"drawings": store.list_drawings()}


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


@route("GET", "/api/galleries/(g[0-9]+)")
def get_gallery(req, gid):
    return {"id": gid, **store.gallery(gid)}


@route("POST", "/api/galleries/(g[0-9]+)")
def update_gallery(req, gid):
    """Change name / settings / items (reorder, remove, per-item seconds or loops)."""
    g = store.gallery(gid)
    body = req.json()
    if body.get("name"):
        g["name"] = str(body["name"])
    if isinstance(body.get("settings"), dict):
        g["settings"].update({k: v for k, v in body["settings"].items() if k in store.GALLERY_DEFAULTS})
    if isinstance(body.get("items"), list):
        known = {i["file"]: i for i in g["items"]}
        new_items = []
        for it in body["items"]:
            if it.get("file") in known:
                item = dict(known.pop(it["file"]))
                for k in ("seconds", "loops"):
                    if it.get(k) in (None, ""):
                        item.pop(k, None)
                    else:
                        item[k] = float(it[k]) if k == "seconds" else int(it[k])
                new_items.append(item)
        for removed in known.values():  # dropped from the list: delete the file
            (store.gallery_dir(gid) / removed["file"]).unlink(missing_ok=True)
        if known:
            store.sync(f"media/{gid}")
        g["items"] = new_items
    store.save_galleries()
    runner.wake.set()
    return {"id": gid, **g}


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
            app.jump(file)
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
    return {"ok": True}


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


@route("POST", "/api/input")
def post_input(req):
    body = req.json()
    ok = runner.input(body["key"], body.get("action", "down"), int(body.get("player", 1)))
    return {"ok": ok, "runner": runner.status()}


@route("GET|POST", "/api/notify")
def api_notify(req):
    body = {k: v[0] for k, v in req.query.items() if k != "token"} if req.command == "GET" else req.json()
    notify(body)
    return state()


@route("POST", "/api/next")
def post_next(req):
    runner.next()
    return state()


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
    return {"playlists": store.playlists, "transitions": TRANSITIONS}


@route("POST", "/api/playlists")
def post_playlists(req):
    data = req.json()["playlists"]
    for name, pl in data.items():
        for item in pl.get("items", []):
            if item.get("app") not in apps.REGISTRY and item.get("app") not in apps.base.ALIASES:
                raise ValueError(f"playlist {name}: unknown app {item.get('app')!r}")
    store.save_playlists(data)
    ha_sync()  # the Home Assistant playlist list
    return {"playlists": store.playlists}


@route("GET", "/api/schedule")
def get_schedule(req):
    return {"schedule": store.schedule, "off": OFF}


@route("POST", "/api/schedule")
def post_schedule(req):
    store.save_schedule(req.json()["schedule"])
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
    return {"ok": True}


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


@route("GET", "/api/panels")
def get_panels(req):
    return {"panels": panelsetup.panel_list(board), "panel_width": board.pw, "panel_height": board.ph,
            "width": board.width, "height": board.height}


@route("POST", "/api/panels")
def post_panels(req):
    """Save a new panel mapping to config.yaml and apply it right away (when the board size stays the same)."""
    panels = panelsetup.validate(req.json().get("panels"))
    panelsetup.write_config(config_path, panels)
    try:
        board.set_panels(panels)
    except ValueError as e:
        return {"saved": True, "applied": False, "message": f"Saved. {e}", "panels": panels}
    runner.wake.set()
    return {"saved": True, "applied": True, "panels": panelsetup.panel_list(board)}


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


@route("GET", "/api/storage")
def get_storage(req):
    return persist.info()


@route("GET", r"/api/storage/backup")
def get_storage_backup(req):
    """A consistent copy of storage.db (settings, playlists, scenes, drawings, slideshows, fonts…)."""
    persist.mirror_once()  # include anything written in the last few seconds
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
    """Raw bytes of a backup .db. It's checked now and replaces the database when the server next starts."""
    if req.query.get("confirm", [""])[0] != "1":
        raise ValueError("add ?confirm=1 to replace all saved data with this backup")
    files = persist.stage_restore(req.raw())
    return {"ok": True, "files": files, **persist.info(),
            "message": f"Backup checked ({files} files). Restart the server to restore it; until then nothing changes."}


# ---- HTTP plumbing ----------------------------------------------------------------

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
        if not password:
            return True
        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if token and secrets.compare_digest(token, password):
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                return secrets.compare_digest(pw, password)
            except ValueError:
                return False
        return False

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
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or (public and length > guestwall.BODY_MAX):
            self.close_connection = True  # don't read a body like that; drop the connection instead
            return self._json({"error": "That message is too long." if length > 0 else "bad Content-Length"}, 413 if length > 0 else 400)
        self._raw = self.rfile.read(length)
        if public:
            return self._guest(url)
        if not self._authorized():
            return self._send(401, b"password required", "text/plain", {"WWW-Authenticate": 'Basic realm="flipdot"'})
        guestwall.note_host(self.headers.get("Host"))
        if url.path == "/ws":
            return self._websocket()
        if self.command == "GET" and not url.path.startswith("/api/"):
            return self._static(url.path)
        for method, pattern, fn in ROUTES:
            m = pattern.match(url.path)
            if m and self.command in method.split("|"):
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
        hub.serve(self.connection, self.rfile)

    def _static(self, path):
        page = {"/": "index.html", "/play": "play.html", "/guest": "guest.html"}.get(path, path.lstrip("/"))
        if page.startswith("static/"):
            page = page[len("static/"):]
        f = (STATIC / page).resolve()
        if not f.is_file() or STATIC.resolve() not in f.parents:
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def log_message(self, fmt, *args):
        logging.debug(fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # default is 5, which drops connections when several clients poll


def main():
    global board, store, runner, password, hub, care, recorder, config_path, automations, persist
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
    guestwall.server_port = args.port
    config = load_config(args.config)
    password = str(config.get("password") or "")
    board = Board(config, dry_run=args.dry_run)
    persist = storage_mod.Storage(args.storage, args.data)
    persist.startup()  # restore data/ from the database before anything reads it
    store = Store(args.data, persist=persist)
    fonts.USER_FONT_DIR = Path(args.data) / "fonts"
    fonts.USER_FONT_DIR.mkdir(parents=True, exist_ok=True)
    runner = Runner(board, store)
    care = BoardCare(board, runner, store)
    atexit.register(persist.flush)  # runs last (atexit is LIFO), after the wear counts are written
    atexit.register(care.save)  # keep wear counts across restarts
    persist.start_mirror()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    recorder = timelapse.Recorder(board)
    automations = rules.Automations(runner, board, store, notify)
    runner.listeners.append(lambda: threading.Thread(target=publish_status, daemon=True).start())
    hub = Hub(board, runner, ws_message)
    board.listeners.append(hub.notify)
    runner.listeners.append(hub.notify)
    if store.schedule.get("enabled"):
        runner.resume_schedule()
    start_mqtt()
    start_sendspin()
    start_gamepad()
    atexit.register(stop_gamepad)

    logging.info("%dx%d board, %d panels%s", board.width, board.height, len(board.panels),
                 " (dry run)" if args.dry_run else "")
    logging.info("open http://localhost:%d%s", args.port, " (password protected)" if password else "")
    Server((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
