"""JSON-file persistence for settings, scenes, playlists and the schedule (in ./data, a working copy of
storage.db: every save is also written to the database when one is attached)."""

import json
import logging
import re
import threading
import time
from pathlib import Path

DEFAULT_PLAYLISTS = {
    "Default": {
        "transition": "dissolve",
        "items": [
            # no Weather here: it needs a location first (playlists skip it until then anyway)
            {"app": "clock", "params": {}, "duration": 30},
            {"app": "analog_clock", "params": {}, "duration": 20},
            {"app": "life", "params": {}, "duration": 30},
        ],
    },
    "Night": {"transition": "wipe", "items": [{"app": "blank", "params": {"color": "black"}, "duration": 600}]},
}
DEFAULT_COMPOSITIONS = {
    "Life over weather": {"layers": [
        {"app": "weather", "params": {}},
        {"app": "life", "params": {"density": 0.2, "fps": 6}, "blend": "xor"},
    ]},
    "Snake over weather": {"layers": [
        {"app": "weather", "params": {"view": "current"}},
        {"app": "snake", "params": {}, "blend": "xor"},
    ]},
    "Clock in the rain": {"layers": [
        {"app": "rain", "params": {"density": 0.6}},
        {"app": "clock", "params": {"date": False}, "blend": "xor"},
    ]},
    "Plasma through the clock": {"layers": [
        {"app": "plasma", "params": {"style": "dither"}},
        {"app": "clock", "params": {"date": False}, "blend": "mask"},
    ]},
    "Starfield ticker": {"layers": [
        {"app": "starfield", "params": {}},
        {"app": "text", "params": {"text": "Hello from the flipdot board", "style": "scroll", "size": "large"}, "blend": "xor"},
    ]},
    "Balls bouncing on prices": {"layers": [
        {"app": "crypto", "params": {}},
        {"app": "bounce", "params": {"balls": 3}, "blend": "xor"},
    ]},
    "Clock with now playing": {"layers": [
        {"app": "clock", "params": {"date": False}},
        {"app": "music", "params": {"layout": "line", "position": "bottom", "progress": False, "song_transition": "none"},
         "blend": "normal", "y": 34, "h": 8},
    ]},
    "Life with a now-playing strip": {"layers": [
        {"app": "life", "params": {"fps": 6}},
        {"app": "music", "params": {"layout": "line", "song_transition": "none"}, "blend": "normal", "h": 10},
    ]},
    "Corner clock over Life": {"layers": [
        {"app": "life", "params": {"fps": 8}},
        {"app": "blank", "params": {"color": "white"}, "blend": "cut", "x": 63, "y": 21, "w": 21, "h": 21},
        {"app": "analog_clock", "params": {"seconds": True}, "blend": "add", "x": 64, "y": 22, "w": 20, "h": 20},
    ]},
}
DEFAULT_SCHEDULE = {"enabled": True, "default": "Default", "rules": []}
DEFAULT_SETTINGS = {
    "display": {"transition_fps": 15},
    "weather": {"units": "fahrenheit"},
    "music_assistant": {"url": "", "token": "", "player": ""},
    "sendspin": {"enabled": False, "name": "Flipdot board", "port": 8928, "unpaired": True, "server_url": "auto"},
    # live audio for the music visualizer: "sendspin", "snapcast" or "off"
    "visualizer": {"source": "off"},
    # host "auto" = the Music Assistant URL's host (its built-in Snapserver)
    "snapcast": {"host": "auto", "port": 1704, "control_port": 1705, "name": "Flipdot board", "delay_ms": 150},
    "mqtt": {"enabled": False, "host": "", "port": 1883, "username": "", "password": "", "prefix": "flipdot",
             "discovery": False, "discovery_prefix": "homeassistant"},
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00", "max_flips": 150, "slowdown": 3},
    "board_health": {"auto_exercise": False, "exercise_time": "12:00"},
    "ai": {"api_key": "", "model": "claude-opus-5-5"},
    "gamepad": {"enabled": True, "deadzone": 0.35, "mapping": {}, "menu_button": "hold_start"},  # menu_button: hold_start | off
}


class Store:
    def __init__(self, path, persist=None):
        self.dir = Path(path)
        self.persist = persist  # storage.Storage, or None to keep files only
        (self.dir / "scenes").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.settings = self._load("settings.json", DEFAULT_SETTINGS)
        if "visualizer" not in self.settings:  # older installs had only Sendspin's on/off switch
            on = self.settings.get("sendspin", {}).get("enabled")
            self.settings["visualizer"] = {"source": "sendspin" if on else "off"}
        for k, v in DEFAULT_SETTINGS.items():
            self.settings.setdefault(k, v)
        self.playlists = self._load("playlists.json", DEFAULT_PLAYLISTS)
        self.schedule = self._load("schedule.json", DEFAULT_SCHEDULE)
        self.compositions = self._load("compositions.json", DEFAULT_COMPOSITIONS)
        # add presets introduced since this install was set up (but don't resurrect deleted ones)
        seen = set(self.settings.get("presets_seen", []))
        new = [n for n in DEFAULT_COMPOSITIONS if n not in seen and n not in self.compositions]
        if (self.dir / "compositions.json").exists() and new:
            for n in new:
                self.compositions[n] = json.loads(json.dumps(DEFAULT_COMPOSITIONS[n]))
            self._save("compositions.json", self.compositions)
        if seen != set(DEFAULT_COMPOSITIONS) | seen or "presets_seen" not in self.settings:
            self.settings["presets_seen"] = sorted(seen | set(DEFAULT_COMPOSITIONS))
            self._save("settings.json", self.settings)
        # drawings: id -> {"name", "w", "h", "px": bytearray (one byte per dot), "version"}
        self.drawings = {}
        for did, d in self._load("drawings.json", {}).items():
            self.drawings[did] = {"name": d["name"], "w": len(d["rows"][0]), "h": len(d["rows"]),
                                  "px": bytearray(1 if c == "1" else 0 for r in d["rows"] for c in r), "version": 0}
        self._drawing_timer = None
        self.galleries = self._load("galleries.json", {})

    def _load(self, name, default):
        try:
            return json.loads((self.dir / name).read_text())
        except FileNotFoundError:
            return json.loads(json.dumps(default))

    def _save(self, name, data):
        with self._lock:
            tmp = self.dir / (name + ".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(self.dir / name)
        self.sync(name)

    def sync(self, *paths):
        """Copy files/folders (relative to the data folder) to the database right away."""
        if self.persist:
            try:
                self.persist.sync(*paths)
            except Exception:
                logging.getLogger("store").exception("couldn't save %s to the database", paths)

    def save_settings(self, data):
        self.settings = data
        self._save("settings.json", data)

    def save_playlists(self, data):
        self.playlists = data
        self._save("playlists.json", data)

    def save_compositions(self, data):
        self.compositions = data
        self._save("compositions.json", data)

    def save_schedule(self, data):
        self.schedule = data
        self._save("schedule.json", data)

    # ---- drawings (editable bitmaps used as layers) ------------------------------

    def _save_drawings_soon(self):
        """Debounced save: pencil strokes change drawings many times a second."""
        if self._drawing_timer:
            self._drawing_timer.cancel()
        self._drawing_timer = threading.Timer(1.0, self._save_drawings)
        self._drawing_timer.daemon = True
        self._drawing_timer.start()

    def _save_drawings(self):
        self._save("drawings.json", {did: {"name": d["name"], "rows": self.drawing_rows(did)} for did, d in list(self.drawings.items())})

    def drawing_rows(self, did):
        d = self.drawings[did]
        w = d["w"]
        return ["".join("1" if v else "0" for v in d["px"][y * w:(y + 1) * w]) for y in range(d["h"])]

    def list_drawings(self):
        return [{"id": did, "name": d["name"], "thumb": self.drawing_rows(did)} for did, d in list(self.drawings.items())]

    def get_drawing(self, did):
        if did not in self.drawings:
            raise KeyError(did)
        return {"id": did, "name": self.drawings[did]["name"], "rows": self.drawing_rows(did)}

    def create_drawing(self, name, w, h, rows=None):
        did = f"d{int(time.time() * 1000) % 10**9}"
        self.drawings[did] = {"name": name or "Drawing", "w": w, "h": h, "px": bytearray(w * h), "version": 0}
        if rows:
            self.update_drawing(did, rows=rows)
        self._save_drawings_soon()
        return did

    def update_drawing(self, did, rows=None, name=None, save=True):
        d = self.drawings[did]
        if rows is not None:
            if len(rows) != d["h"] or any(len(r) != d["w"] for r in rows):
                raise ValueError(f"drawing must be {d['h']} rows of {d['w']} characters")
            d["px"] = bytearray(1 if c == "1" else 0 for r in rows for c in r)
        if name:
            d["name"] = name
        d["version"] += 1
        if save:
            self._save_drawings_soon()

    def drawing_dots(self, did, dots):
        """dots: iterable of (x, y, value) with value True/False/None (toggle)."""
        d = self.drawings[did]
        w, h, px = d["w"], d["h"], d["px"]
        for x, y, v in dots:
            if 0 <= x < w and 0 <= y < h:
                px[y * w + x] = (1 - px[y * w + x]) if v is None else (1 if v else 0)
        d["version"] += 1
        self._save_drawings_soon()

    def delete_drawing(self, did):
        self.drawings.pop(did, None)
        self._save_drawings_soon()

    # ---- slideshows (galleries of uploaded images/GIFs) ---------------------------

    GALLERY_DEFAULTS = {"mode": "crop", "dither": False, "threshold": 128, "invert": False, "image_seconds": 10,
                        "gif_loops": 1, "gif_min_seconds": 0, "gif_speed": 1, "shuffle": False, "transition": "morph",
                        "transition_duration": 1.0,
                        # random effects: chance per item (%), which ones, whole item or a burst
                        "fx_chance": 0, "fx_effects": ["invert", "flash", "glitch", "mirror", "kaleidoscope", "wave",
                                                       "scanlines", "pixelate", "outline", "static", "life", "melt"],
                        "fx_mode": "burst", "fx_seconds": 3, "fx_strength": 50,
                        # React to music: signal -> effect rows (see apps/slidefx.py REACT_DEFAULTS), GIF speed
                        # follows loudness, a frame skipped per beat, and what to do without live audio
                        "react_enabled": False, "react_map": None, "react_speed": True, "react_skip": False,
                        "react_fallback": "none"}

    def gallery_dir(self, gid):
        if not re.fullmatch(r"g[0-9]+", gid or ""):
            raise ValueError("bad slideshow id")
        d = self.dir / "media" / gid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_galleries(self):
        self._save("galleries.json", self.galleries)

    def create_gallery(self, name):
        gid = f"g{int(time.time() * 1000) % 10**10}"
        self.galleries[gid] = {"name": name or "Slideshow", "items": [], "settings": dict(self.GALLERY_DEFAULTS)}
        self.save_galleries()
        return gid

    def gallery(self, gid):
        g = self.galleries.get(gid)
        if g is None:
            raise KeyError(gid)
        g["settings"] = {**self.GALLERY_DEFAULTS, **g.get("settings", {})}
        return g

    def delete_gallery(self, gid):
        import shutil
        self.galleries.pop(gid, None)
        shutil.rmtree(self.dir / "media" / gid, ignore_errors=True)
        self.sync(f"media/{gid}")
        self.save_galleries()

    # ---- scenes ----------------------------------------------------------------

    def _scene_path(self, scene_id):
        if not re.fullmatch(r"[a-z0-9_-]+", scene_id or ""):
            raise ValueError("bad scene id")
        return self.dir / "scenes" / f"{scene_id}.json"

    def list_scenes(self):
        out = []
        for f in sorted(self.dir.glob("scenes/*.json"), key=lambda f: -f.stat().st_mtime):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            out.append({"id": f.stem, "name": d["name"], "frames": len(d["frames"]), "thumb": d["frames"][0]["rows"]})
        return out

    def get_scene(self, scene_id):
        try:
            return json.loads(self._scene_path(scene_id).read_text())
        except FileNotFoundError:
            return None

    def save_scene(self, name, frames, scene_id=None):
        if not frames:
            raise ValueError("scene has no frames")
        if not scene_id:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "scene"
            scene_id = f"{slug}-{int(time.time() * 1000) % 10**8}"
        frames = [{"rows": f["rows"], "delay": int(f.get("delay", 100))} for f in frames]
        self._save(f"scenes/{scene_id}.json", {"name": name, "frames": frames})
        return scene_id

    def delete_scene(self, scene_id):
        self._scene_path(scene_id).unlink(missing_ok=True)
        self.sync(f"scenes/{scene_id}.json")
