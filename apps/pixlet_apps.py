"""Each installed Pixlet app (pixlet.py) as an app of its own in Apps → Plugins.

The form is the app's own settings (pixlet's schema: text, dropdowns, switches, colours, dates; a
location comes from Settings → Location), plus how its colour pictures become dots: where the 64×32
picture goes on the 84×42 board, the threshold or dithering, inverting, and how often to render again.
"""

import datetime
import json
from pathlib import Path

import pixlet

from .base import REGISTRY, App, P

PREFIX = "pixlet_"

SIZE_OPTIONS = [["center", "64×32 in the middle (dots stay sharp)"], ["stretch", "64×32 stretched to fill"],
                ["native", "Drawn at 84×42 (apps that adapt to the size)"]]
MODE_OPTIONS = [["lit", "Any lit colour is a dot"], ["luma", "Brightness above the threshold"],
                ["dither", "Dithered (pictures and gradients)"]]


def app_id(name):
    return PREFIX + "".join(ch if ch.isalnum() else "_" for ch in name.lower())


def schema_params(schema):
    """App params from a pixlet schema, and the labels of settings that can't be set here."""
    params, skipped = [], []
    for f in (schema or {}).get("schema") or []:
        t, fid, label = f.get("type"), f.get("id"), (f.get("name") or f.get("id") or "")[:60]
        if not fid or t == "location":
            continue  # the location comes from Settings
        if t in ("dropdown", "radio"):
            opts = [[o.get("value"), o.get("display") or o.get("text") or o.get("value")] for o in f.get("options") or []]
            if opts:
                params.append(P(fid, "select", f.get("default", opts[0][0]), label, options=opts))
        elif t in ("toggle", "onoff"):
            params.append(P(fid, "checkbox", str(f.get("default", "false")).lower() == "true", label))
        elif t in ("text", "color"):
            params.append(P(fid, "text", f.get("default") or "", label + (" (#rrggbb)" if t == "color" else "")))
        elif t == "datetime":
            params.append(P(fid, "datetime", "", label))
        else:
            skipped.append(label)
    return params, skipped


def tuning_params(meta):
    sizes = SIZE_OPTIONS + ([["2x", "128×64 scaled down (finer, can blur)"]] if meta.get("supports2x") else [])
    every = int(meta.get("interval") or 5) * 60
    return [
        P("pixlet_size", "select", "center", "Picture size", options=sizes),
        P("pixlet_mode", "select", "lit", "Colours to dots", options=MODE_OPTIONS),
        P("pixlet_threshold", "number", 48, "Threshold (0–255, not for dithering)", min=0, max=254, step=1),
        P("pixlet_invert", "checkbox", False, "Invert"),
        P("pixlet_every", "number", max(30, min(3600, every)), "Render again every (seconds)", min=15, max=86400),
    ]


def local_timezone():
    try:
        link = Path("/etc/localtime").resolve()
        if "zoneinfo/" in str(link):
            return str(link).split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def location_value(store):
    loc = (store.settings.get("weather") or {}) if store is not None else {}
    if loc.get("lat") is None:
        return None
    name = str(loc.get("name") or "")
    return json.dumps({"lat": str(loc["lat"]), "lng": str(loc["lon"]), "description": name,
                       "locality": name.split(",")[0], "timezone": local_timezone()})


class PixletApp(App):
    category = "Plugins"
    interval = 0.2
    pausable = True
    pixlet_name = None
    schema_ids = ()
    datetime_ids = ()
    location_ids = ()

    @classmethod
    def needs_setup(cls, store, params):
        if pixlet.binary() is None:
            return pixlet.SETUP_HINT
        return None

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.error = None
        self.data = None
        self.frames, self.loop_start, self.shown_src = None, None, None
        self.app_dir = pixlet.LIBRARY.apps_dir / self.pixlet_name if pixlet.LIBRARY else None

    def config(self):
        cfg = {}
        for k in self.schema_ids:
            v = self.p.get(k)
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif k in self.datetime_ids:
                try:
                    v = datetime.datetime.fromisoformat(str(v)).astimezone().isoformat()
                except ValueError:
                    continue
            cfg[k] = str(v)
        loc = location_value(getattr(self.ctx, "store", None)) if self.location_ids else None
        for k in self.location_ids:
            if loc:
                cfg[k] = loc
        return cfg

    def render(self, c, now):
        if self.app_dir is None or pixlet.binary() is None:
            self.error = pixlet.SETUP_HINT
            return self._message(c, "Pixlet missing", self.error)
        size = self.p["pixlet_size"]
        every = max(15.0, float(self.p["pixlet_every"] or 300))
        e = pixlet.RENDERS.get(self.app_dir, self.config(), size, every)
        frames = pixlet.RENDERS.frames(e, size, self.p["pixlet_mode"], self.p["pixlet_threshold"],
                                       self.p["pixlet_invert"], c.w, c.h)
        if not frames:
            self.error = e["error"]
            self.interval = 0.5
            if frames == []:
                return  # the app has nothing to show right now: a blank board
            return self._message(c, "Pixlet error" if e["error"] else "Rendering…", e["error"] or self.title)
        self.error, self.data = None, True
        if frames is not self.frames:
            self.frames, self.loop_start = frames, now
        total = sum(ms for _, ms in frames) / 1000
        if len(frames) == 1 or total <= 0:
            c.px[:] = frames[0][0]
            self.interval = 0.5
            return
        t = (now - self.loop_start) % total
        acc = 0.0
        for dots, ms in frames:
            acc += ms / 1000
            if t < acc:
                c.px[:] = dots
                self.interval = max(0.02, min(0.5, acc - t))
                return
        c.px[:] = frames[-1][0]

    def _message(self, c, head, text):
        from canvas import wrap_text
        from fonts import SMALL
        c.text_center(6, head[:14])
        for i, line in enumerate(wrap_text(str(text)[:90], SMALL, c.w - 4)[:4]):
            c.text_center(17 + i * 6, line, SMALL)

    def describe(self):
        return f"{self.title} (Pixlet)"


def make_class(meta):
    params, skipped = schema_params(meta.get("schema"))
    fields = (meta.get("schema") or {}).get("schema") or []
    desc = (meta.get("summary") or meta.get("description") or "").rstrip(".")
    desc = f"{desc}. A Pixlet app by {meta.get('author') or 'the community'} ({meta.get('license', 'Apache-2.0')})."
    if skipped:
        desc += f" Not available here: {', '.join(skipped)}."
    if meta.get("schema") is None:
        desc += " Its settings appear once Pixlet is installed."
    return type(f"Pixlet_{meta['name']}", (PixletApp,), {
        "id": app_id(meta["name"]), "title": meta.get("title") or meta["name"], "description": desc,
        "params": params + tuning_params(meta), "pixlet_name": meta["name"],
        "schema_ids": tuple(p["name"] for p in params),
        "datetime_ids": tuple(p["name"] for p in params if p["type"] == "datetime"),
        "location_ids": tuple(f["id"] for f in fields if f.get("type") == "location" and f.get("id")),
        "__module__": __name__,
    })


_registered = set()


def sync(lib):
    """Register an app for every installed Pixlet app (and drop the ones deleted)."""
    want = {}
    for meta in lib.installed():
        if meta.get("schema") is None:
            meta = lib.ensure_schema(meta["name"]) or meta
        want[app_id(meta["name"])] = meta
    for aid in _registered - set(want):
        REGISTRY.pop(aid, None)
    for aid, meta in want.items():
        existing = REGISTRY.get(aid)
        if existing is not None and not issubclass(existing, PixletApp):
            continue  # never shadow another app
        REGISTRY[aid] = make_class(meta)
    _registered.clear()
    _registered.update(want)
    return sorted(want)

