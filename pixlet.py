"""Pixlet apps: the Tidbyt/Tronbyt community catalogue (github.com/tronbyt/apps) on the flipdot board.

Those apps are Starlark (.star) programs that the `pixlet` tool renders to a 64×32 colour WebP. Here
they are downloaded from the catalogue into <data>/pixlet/apps/<name>/ (so a backup keeps them), each
becomes an app in Apps → Plugins with a form made from the app's own schema, and the pictures it
renders are turned into 1-bit frames (a threshold or dithering per app) that play like a GIF.

Pixlet itself is a separate program: scripts/setup-pixlet.sh puts a pinned, checksummed build in
native/pixlet/build/. Starlark apps can't touch files or run programs, only fetch web pages, so they're
much safer to run than Python plugins; they do make the network requests their app needs.
"""

import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("pixlet")

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "native" / "pixlet" / "build" / "pixlet"
INDEX_URL = "https://tronbyt.github.io/apps/apps.json"
CONTENTS_URL = "https://api.github.com/repos/tronbyt/apps/contents/apps/{name}?ref=main"
PAGE_URL = "https://github.com/tronbyt/apps/tree/main/apps/{name}"
LICENSE = "Apache-2.0"  # the catalogue's licence (each app keeps its author)
UA = "flipdot-board/1.0 (+https://github.com/tronbyt/apps)"
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,60}")
MAX_APP_BYTES = 12 * 2**20
MAX_APP_FILES = 300
RENDER_TIMEOUT = 40  # seconds for one pixlet run
SIZES = {"center": (64, 32), "stretch": (64, 32), "native": (84, 42), "2x": (128, 64)}
DEFAULT_DURATION = 50  # ms per frame when the image doesn't say
CACHE_DIR = Path(tempfile.gettempdir()) / "flipdot-pixlet-cache"

SKIPPED_TYPES = {"oauth2", "generated", "typeahead", "png", "notification", "locationbased"}


def fetch(url, limit=8 * 2**20, timeout=20):
    """GET a URL (tests point the URLs at a local server)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"download failed: HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"download failed: {getattr(e, 'reason', e)}") from None
    if len(data) > limit:
        raise ValueError("download too big")
    return data


# ---- the pixlet program -------------------------------------------------------------------

def binary():
    """Path of a pixlet that runs on this machine, or None."""
    env = os.environ.get("FLIPDOT_PIXLET")
    if env:
        return env if os.access(env, os.X_OK) else None
    if BIN.is_file() and os.access(BIN, os.X_OK):
        stamp = BIN.parent / ".arch"
        if not stamp.exists() or stamp.read_text().strip() == platform.machine():
            return str(BIN)
    return shutil.which("pixlet")


def version():
    b = binary()
    if not b:
        return None
    try:
        out = subprocess.run([b, "version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"v?\d+\.\d+\.\d+", out)
        return m.group(0) if m else out.strip()[:40]
    except (OSError, subprocess.SubprocessError):
        return None


SETUP_HINT = "Install Pixlet first: run scripts/setup-pixlet.sh on the board's computer, then reload this page."


def _nice():
    try:
        os.nice(10)  # renders run beside the board loop: let the board go first
    except OSError:
        pass


def run_pixlet(args, timeout=RENDER_TIMEOUT):
    b = binary()
    if not b:
        raise RuntimeError(SETUP_HINT)
    try:
        # its compiled-app cache goes in /tmp, not beside the app (that's the data folder, copied to storage.db)
        env = {**os.environ, "PIXLET_APPLET_CACHE_DIR": str(CACHE_DIR)}
        r = subprocess.run([b, *args], capture_output=True, timeout=timeout, preexec_fn=_nice,
                           stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir(), env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Pixlet took longer than {timeout} s") from None
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
        msg = [m for m in msg if m.strip()]
        raise RuntimeError(msg[-1][:300] if msg else f"pixlet exited with {r.returncode}")
    return r.stdout


def schema(app_dir):
    """The app's settings as pixlet describes them ({"version", "schema": [...]}), or {} when it has none."""
    try:
        out = run_pixlet(["schema", str(app_dir)], timeout=30)
        return json.loads(out or b"{}") or {}
    except (RuntimeError, ValueError) as e:
        log.info("pixlet schema %s: %s", app_dir, e)
        return {}


# ---- turning colour pictures into dots -----------------------------------------------------

def to_dots(im, size="center", mode="lit", threshold=48, invert=False, w=84, h=42):
    """One 1-bit frame (bytes, one per dot, row-major) from a colour PIL image."""
    from PIL import Image, ImageChops
    im = im.convert("RGB")
    if size == "stretch":
        im = im.resize((w, h), Image.NEAREST)
    elif size == "2x":
        im = im.resize((w, h), Image.BOX)  # average 128x64 down: thin lines survive as grey
    elif im.size != (w, h):
        canvas = Image.new("RGB", (w, h))
        canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
        im = canvas
    if mode == "luma" or mode == "dither":
        gray = im.convert("L")
    else:  # "lit": an LED that's on in any colour is a dot that's on (pixlet apps draw on black)
        r, g, b = im.split()
        gray = ImageChops.lighter(ImageChops.lighter(r, g), b)
    if mode == "dither":
        bw = gray.convert("1")  # Floyd-Steinberg
    else:
        t = int(threshold)
        bw = gray.point(lambda v: 255 if v > t else 0).convert("1", dither=Image.NONE)
    dots = bw.convert("L").tobytes().translate(ON_TABLE)
    if invert:
        dots = dots.translate(FLIP_TABLE)
    return dots


ON_TABLE = bytes([0] * 128 + [1] * 128)
FLIP_TABLE = bytes([1, 0] + [0] * 254)


def decode(data):
    """[(PIL RGB image, duration ms)] for each frame of a WebP or GIF."""
    from PIL import Image, ImageSequence
    im = Image.open(io.BytesIO(data))
    frames = []
    for f in ImageSequence.Iterator(im):
        frames.append((f.convert("RGB"), int(f.info.get("duration") or DEFAULT_DURATION)))
    return frames


def convert(frames, size="center", mode="lit", threshold=48, invert=False, w=84, h=42):
    """[(dots, ms)]: identical frames in a row are merged (fewer redraws, same timing)."""
    out = []
    for im, ms in frames:
        dots = to_dots(im, size, mode, threshold, invert, w, h)
        if out and out[-1][0] == dots:
            out[-1] = (dots, out[-1][1] + ms)
        else:
            out.append((dots, ms))
    return out


def output_format():
    try:
        from PIL import features
        return "webp" if features.check("webp") else "gif"
    except Exception:
        return "gif"


def render(app_dir, config, size="center"):
    """Run the app once: [(PIL image, ms)]."""
    pw, ph = SIZES.get(size, SIZES["center"])
    fmt = output_format()
    with tempfile.TemporaryDirectory(prefix="pixlet-") as tmp:
        out = Path(tmp) / f"out.{fmt}"
        args = ["render", str(app_dir), *[f"{k}={v}" for k, v in config.items()], "-o", str(out),
                "--format", fmt, "--silent", "--timeout", f"{RENDER_TIMEOUT - 5}s"]
        args += ["-2"] if size == "2x" else ["-w", str(pw), "-t", str(ph)]
        run_pixlet(args)
        if not out.exists() or out.stat().st_size == 0:
            return []  # the app chose to show nothing right now
        return decode(out.read_bytes())


class Renders:
    """Renders shared by every copy of an app with the same settings (the board, a preview, a layer)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.busy = threading.Semaphore(1)  # one pixlet at a time: it's the heaviest thing on a Pi
        self.items = {}  # key -> {"frames", "at", "error", "pending", "converted": {tuning: frames}}

    def get(self, app_dir, config, size, every):
        key = (str(app_dir), tuple(sorted(config.items())), size)
        with self.lock:
            e = self.items.get(key)
            if e is None:
                e = self.items[key] = {"frames": None, "at": 0, "error": None, "pending": False, "converted": {}, "used": 0}
            e["used"] = time.time()
            if not e["pending"] and time.time() - e["at"] >= every:
                e["pending"] = True
                threading.Thread(target=self._work, args=(e, app_dir, dict(config), size), daemon=True,
                                 name="pixlet-render").start()
            if len(self.items) > 40:  # forget settings nobody has used for a while
                for k in sorted(self.items, key=lambda k: self.items[k]["used"])[:10]:
                    if not self.items[k]["pending"]:
                        del self.items[k]
        return e

    def _work(self, e, app_dir, config, size):
        try:
            with self.busy:
                frames = render(app_dir, config, size)
            with self.lock:
                e["frames"], e["converted"], e["error"] = frames, {}, None
        except Exception as ex:
            log.warning("pixlet %s: %s", Path(app_dir).name, ex)
            e["error"] = str(ex)
        finally:
            e["at"] = time.time()
            e["pending"] = False

    def frames(self, e, size, mode, threshold, invert, w, h):
        tuning = (size, mode, int(threshold), bool(invert), w, h)
        with self.lock:
            src = e["frames"]
            hit = e["converted"].get(tuning)
        if src is None:
            return None
        if hit is None:
            hit = convert(src, size, mode, threshold, invert, w, h)
            with self.lock:
                if e["frames"] is src:
                    e["converted"][tuning] = hit
        return hit


RENDERS = Renders()


# ---- the catalogue and installed apps -------------------------------------------------------

INDEX_KEYS = ("name", "displayName", "summary", "description", "author", "recommendedInterval", "supports2x",
              "starFile", "category", "tags", "image", "image2x", "updated")


class Library:
    def __init__(self, store):
        self.store = store
        self.dir = Path(store.dir) / "pixlet"
        self.apps_dir = self.dir / "apps"
        self.trash = self.dir / "trash"
        self.apps_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.on_change = []

    def _sync(self, *rel):
        try:
            self.store.sync(*rel)
        except Exception as e:
            log.warning("pixlet: storage sync failed: %s", e)

    # the catalogue index (cached in the data folder; refreshed when asked)
    def index(self):
        try:
            return json.loads((self.dir / "index.json").read_text())
        except (OSError, ValueError):
            return {"fetched": None, "apps": []}

    def refresh_index(self):
        raw = json.loads(fetch(INDEX_URL))
        if not isinstance(raw, list):
            raise ValueError("the catalogue index has an unexpected format")
        apps = [{k: a.get(k) for k in INDEX_KEYS} for a in raw if isinstance(a, dict) and NAME_RE.fullmatch(str(a.get("name", "")))]
        data = {"fetched": int(time.time()), "source": INDEX_URL, "apps": apps}
        tmp = self.dir / "index.json.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(self.dir / "index.json")
        self._sync("pixlet/index.json")
        return data

    def search(self, q="", category="", limit=60):
        idx = self.index()
        words = str(q).lower().split()
        out = []
        for a in idx["apps"]:
            text = " ".join(str(a.get(k) or "") for k in ("name", "displayName", "summary", "description", "author", "category"))
            text += " " + " ".join(a.get("tags") or [])
            if category and a.get("category") != category:
                continue
            if all(w in text.lower() for w in words):
                out.append(a)
        out.sort(key=lambda a: str(a.get("displayName") or a["name"]).lower())
        installed = {m["name"] for m in self.installed()}
        cats = sorted({a.get("category") for a in idx["apps"] if a.get("category")})
        return {"fetched": idx.get("fetched"), "total": len(idx["apps"]), "matches": len(out), "categories": cats,
                "apps": [{**{k: a.get(k) for k in ("name", "displayName", "summary", "author", "category", "supports2x",
                                                  "recommendedInterval")},
                          "installed": a["name"] in installed, "page": PAGE_URL.format(name=a["name"])}
                         for a in out[:limit]]}

    # installed apps: files in apps/<name>/, details in apps/<name>.json
    def meta(self, name):
        try:
            return json.loads((self.apps_dir / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    def installed(self):
        out = []
        for f in sorted(self.apps_dir.glob("*.json")):
            m = self.meta(f.stem)
            if m and (self.apps_dir / f.stem).is_dir():
                out.append(m)
        return out

    def _list_files(self, name, sub="", depth=0):
        """[(relative path, download URL, size)] of a catalogue app's folder (GitHub contents API)."""
        url = CONTENTS_URL.format(name=f"{name}/{sub}".rstrip("/"))
        items = json.loads(fetch(url))
        if not isinstance(items, list):
            raise ValueError(f"{name} isn't in the catalogue")
        out = []
        for it in items:
            rel = f"{sub}/{it['name']}".lstrip("/")
            if it.get("type") == "dir" and depth < 3:
                out += self._list_files(name, rel, depth + 1)
            elif it.get("type") == "file" and it.get("download_url"):
                out.append((rel, it["download_url"], int(it.get("size") or 0)))
        return out

    def install(self, name):
        if not NAME_RE.fullmatch(str(name)):
            raise ValueError("That isn't a catalogue app name.")
        entry = next((a for a in self.index()["apps"] if a["name"] == name), None)
        if entry is None:
            raise ValueError(f"{name} isn't in the catalogue (refresh the catalogue and try again)")
        files = self._list_files(name)
        previews = {Path(str(entry.get(k) or "")).name for k in ("image", "image2x")} - {""}
        files = [f for f in files if f[0] not in previews]  # the catalogue's preview pictures aren't needed
        if not any(f[0].endswith(".star") for f in files):
            raise ValueError(f"{name} has no .star file")
        if len(files) > MAX_APP_FILES or sum(f[2] for f in files) > MAX_APP_BYTES:
            raise ValueError(f"{name} is too big to install here")
        with self.lock:
            tmp = self.apps_dir / f".{name}.tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            for rel, url, _ in files:
                parts = Path(rel).parts
                if ".." in parts or rel.startswith("/"):
                    continue
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(fetch(url, limit=MAX_APP_BYTES))
            dest = self.apps_dir / name
            shutil.rmtree(dest, ignore_errors=True)
            tmp.replace(dest)
            meta = {"name": name, "title": entry.get("displayName") or name, "summary": entry.get("summary") or "",
                    "description": entry.get("description") or "", "author": entry.get("author") or "",
                    "category": entry.get("category"), "supports2x": bool(entry.get("supports2x")),
                    "interval": entry.get("recommendedInterval"), "page": PAGE_URL.format(name=name),
                    "license": LICENSE, "installed_at": int(time.time()), "schema": schema(dest) if binary() else None}
            (self.apps_dir / f"{name}.json").write_text(json.dumps(meta, indent=1))
            self._sync(f"pixlet/apps/{name}", f"pixlet/apps/{name}.json")
        self._changed()
        return meta

    def ensure_schema(self, name):
        """Fill in the schema of an app installed before Pixlet was (None until Pixlet can read it)."""
        m = self.meta(name)
        if m is None or m.get("schema") is not None or not binary():
            return m
        m["schema"] = schema(self.apps_dir / name)
        (self.apps_dir / f"{name}.json").write_text(json.dumps(m, indent=1))
        self._sync(f"pixlet/apps/{name}.json")
        return m

    def delete(self, name):
        with self.lock:
            if self.meta(name) is None:
                raise KeyError(name)
            self.trash.mkdir(parents=True, exist_ok=True)
            for f in (name, f"{name}.json"):
                dest = self.trash / f
                if dest.is_dir():
                    shutil.rmtree(dest)
                elif dest.exists():
                    dest.unlink()
                (self.apps_dir / f).replace(dest)
            self._sync(f"pixlet/apps/{name}", f"pixlet/apps/{name}.json")
        self._changed()

    def restore(self, name):
        with self.lock:
            if not (self.trash / f"{name}.json").exists():
                raise KeyError(name)
            for f in (name, f"{name}.json"):
                (self.trash / f).replace(self.apps_dir / f)
            self._sync(f"pixlet/apps/{name}", f"pixlet/apps/{name}.json")
        self._changed()

    def empty_trash(self):
        if self.trash.exists():
            shutil.rmtree(self.trash, ignore_errors=True)
            self._sync("pixlet/trash")

    def _changed(self):
        for fn in self.on_change:
            try:
                fn()
            except Exception as e:
                log.debug("pixlet: listener failed: %s", e)


LIBRARY = None


def start(store):
    """At server start: the library for this data folder, with its apps registered."""
    global LIBRARY
    LIBRARY = Library(store)
    LIBRARY.empty_trash()
    from apps import pixlet_apps
    LIBRARY.on_change.append(lambda: pixlet_apps.sync(LIBRARY))
    pixlet_apps.sync(LIBRARY)
    return LIBRARY
