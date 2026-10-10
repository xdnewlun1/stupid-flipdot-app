"""HTTP API + web UI for the flipdot board.

    python3 server.py              # talk to the board
    python3 server.py --dry-run    # no hardware, just the web page/API
"""

import argparse
import base64
import io
import json
import logging
import mimetypes
import re
import secrets
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import apps
import media_upload
import sendspin_link
from apps import slideshow
from apps import music
from apps.media import convert_image
from apps.widgets import geocode
from canvas import Canvas
from flipdot import Board, load_config
import fonts
from fonts import FONTS, NORMAL
from mqtt import MqttClient, handle_message
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
hub: Hub = None


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Redirect:
    def __init__(self, url):
        self.url = url


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
    if merged.get("mqtt") != old.get("mqtt"):
        start_mqtt()
    if merged.get("sendspin") != old.get("sendspin"):
        start_sendspin()
        link = sendspin_link.get_link()
        if link and merged["sendspin"].get("unpaired") != old.get("sendspin", {}).get("unpaired"):
            link.set_unpaired_access(bool(merged["sendspin"].get("unpaired")))


def start_mqtt():
    global mqtt_client
    if mqtt_client:
        mqtt_client.close()
        mqtt_client = None
    cfg = store.settings.get("mqtt", {})
    if cfg.get("enabled") and cfg.get("host"):
        mqtt_client = MqttClient(cfg, lambda topic, payload: handle_message(runner, board, notify, topic, payload))


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
                                    port=int(cfg.get("port") or 8928), on_pair_code=show_code)
    link.set_unpaired_access(bool(cfg.get("unpaired")))


def publish_status():
    if mqtt_client:
        mqtt_client.publish(f"{mqtt_client.prefix}/status", json.dumps(runner.status()), retain=True)


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
    return {"playlists": store.playlists}


@route("GET", "/api/schedule")
def get_schedule(req):
    return {"schedule": store.schedule, "off": OFF}


@route("POST", "/api/schedule")
def post_schedule(req):
    store.save_schedule(req.json()["schedule"])
    runner.wake.set()
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


@route("GET", "/api/sendspin")
def get_sendspin(req):
    link = sendspin_link.get_link()
    if link is None:
        return {"available": sendspin_link.AVAILABLE, "status": "off" if sendspin_link.AVAILABLE
                else "aiosendspin isn't installed (pip install -r requirements-sendspin.txt)", "connected": False}
    return link.describe()


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
        self._raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._authorized():
            return self._send(401, b"password required", "text/plain", {"WWW-Authenticate": 'Basic realm="flipdot"'})
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
                if isinstance(result, Redirect):
                    return self._send(302, b"", "text/plain", {"Location": result.url})
                return self._json(result if result is not None else {"ok": True})
        self._json({"error": "not found"}, 404)

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
        page = {"/": "index.html", "/play": "play.html"}.get(path, path.lstrip("/"))
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
    global board, store, runner, password, hub
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=ROOT / "config.yaml")
    ap.add_argument("--data", default=ROOT / "data", help="where scenes, playlists and settings are saved")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dry-run", action="store_true", help="don't talk to the hardware")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")

    config = load_config(args.config)
    password = str(config.get("password") or "")
    board = Board(config, dry_run=args.dry_run)
    store = Store(args.data)
    fonts.USER_FONT_DIR = Path(args.data) / "fonts"
    fonts.USER_FONT_DIR.mkdir(parents=True, exist_ok=True)
    runner = Runner(board, store)
    runner.listeners.append(lambda: threading.Thread(target=publish_status, daemon=True).start())
    hub = Hub(board, runner, ws_message)
    board.listeners.append(hub.notify)
    runner.listeners.append(hub.notify)
    if store.schedule.get("enabled"):
        runner.resume_schedule()
    start_mqtt()
    start_sendspin()

    logging.info("%dx%d board, %d panels%s", board.width, board.height, len(board.panels),
                 " (dry run)" if args.dry_run else "")
    logging.info("open http://localhost:%d%s", args.port, " (password protected)" if password else "")
    Server((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
