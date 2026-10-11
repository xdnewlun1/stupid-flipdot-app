"""Home and data widgets: a habit tracker (streak grid), a Home Assistant / MQTT / JSON entity board, package
tracking, an offline word of the day and on this day, and a network status dashboard.

Saved data lives in the data folder like the counters do: habits.json and packages.json (store._load/_save).
"""

import datetime as dt
import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from canvas import wrap_text
from fonts import NORMAL, SMALL

from .base import NeedsSetup, P, Widget, http_get, register
from .pack_daily import (Item, PAGE_PARAMS, Pager, asciify, bundled, cached_get, calm, day_pick, draw_page,
                         fit_text, mqtt_unwatch, mqtt_watch, number_from)
from .widgets import json_path

log = logging.getLogger("pack_home")

HABITS = "habits.json"
PACKAGES = "packages.json"
_lock = threading.Lock()

DOW = "MTWTFSS"


def today_str(now=None):
    return dt.date.fromtimestamp(time.time() if now is None else now).isoformat()


# ---- 1. Habits ------------------------------------------------------------------------------------------------

def habits(store):
    """{"habits": {name: {"days": ["YYYY-MM-DD", ...], "created": iso}}}, kept in habits.json."""
    if not hasattr(store, "_home_habits"):
        data = store._load(HABITS, {"habits": {}})
        if not isinstance(data, dict) or not isinstance(data.get("habits"), dict):
            data = {"habits": {}}
        store._home_habits = data
    return store._home_habits


def _save_habits(store):
    store._save(HABITS, habits(store))


def habit_names(store):
    return list(habits(store)["habits"])


def set_habit(store, name, done=True, day=None, create=True):
    """Tick (or untick) a habit for a day (default today). -> the habit's record. POST /api/habits/<name>."""
    name = str(name).strip()
    if not re.fullmatch(r"[A-Za-z0-9 _.'-]{1,24}", name):
        raise ValueError("habit names use letters, digits, spaces, . _ ' - (up to 24)")
    day = day or today_str()
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise ValueError("day must be YYYY-MM-DD") from None
    with _lock:
        data = habits(store)["habits"]
        rec = data.get(name)
        if rec is None:
            if not create:
                raise KeyError(name)
            rec = data[name] = {"days": [], "created": today_str()}
        days = set(rec["days"])
        if done:
            days.add(day)
        else:
            days.discard(day)
        rec["days"] = sorted(days)[-3660:]
        _save_habits(store)
        return rec


def remove_habit(store, name):
    with _lock:
        data = habits(store)["habits"]
        if name in data:
            del data[name]
            _save_habits(store)
            return True
        return False


def streaks(days, today):
    """(current streak ending today or yesterday, best streak ever) from a set of ISO days."""
    if not days:
        return 0, 0
    dates = sorted(dt.date.fromisoformat(d) for d in days)
    best = run = 1
    for a, b in zip(dates, dates[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        best = max(best, run)
    cur = 0
    d = today if today in dates else today - dt.timedelta(days=1)
    s = set(dates)
    while d in s:
        cur += 1
        d -= dt.timedelta(days=1)
    return cur, best


def habit_summary(store, today=None):
    """For the API: [{"name", "done_today", "streak", "best", "days"}]."""
    today = today or dt.date.today()
    out = []
    for name, rec in habits(store)["habits"].items():
        cur, best = streaks(rec["days"], today)
        out.append({"name": name, "done_today": today.isoformat() in rec["days"], "streak": cur, "best": best,
                    "days": len(rec["days"])})
    return out


@register
class Habits(Widget):
    id = "habits"
    title = "Habit tracker"
    category = "Widgets"
    description = ("A streak grid per habit (one dot per day, a column a week, like a contributions chart) with the "
                   "current and best streak. Tick today with a controller button while it shows, or from the API "
                   "(POST /api/habits/<name>) and MQTT.")
    params = [
        P("names", "text", "", "Habits to show, comma-separated (blank = all)"),
        P("mode", "select", "one", "Show", options=[["one", "One habit at a time (grid)"], ["all", "All habits (a row each)"]]),
        P("weeks", "number", 20, "Weeks across", min=4, max=26, show_if={"mode": "one"}),
        P("flip_seconds", "number", 6, "Seconds per habit", min=2, max=60, show_if={"mode": "one"}),
        P("weekday_letters", "checkbox", True, "Weekday letters down the side", show_if={"mode": "one"}),
    ]
    interactive = True
    interval = 0.5
    refresh = 10 ** 9  # nothing to fetch: the data is local

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.index = 0
        self.held_until = 0.0  # after a press the shown habit stays a while
        self.flash = 0.0

    def fetch(self):
        return None

    def names(self):
        wanted = [n.strip() for n in str(self.p["names"] or "").split(",") if n.strip()]
        have = habit_names(self.ctx.store)
        return wanted or have

    def shown(self, now):
        names = self.names()
        if not names:
            return None
        if self.p["mode"] == "one" and now >= self.held_until:
            self.index = int(now / float(self.p["flip_seconds"])) % len(names)
        return names[self.index % len(names)]

    def input(self, key, action="down", player=1):
        if action != "down":
            return
        now = time.time()
        names = self.names()
        if not names:
            return
        if key in ("left", "right"):
            self.index = (self.index + (1 if key == "right" else -1)) % len(names)
            self.held_until = now + 20
        elif key in ("a", "start"):
            name = names[self.index % len(names)]
            rec = habits(self.ctx.store)["habits"].get(name)
            done = today_str(now) not in (rec["days"] if rec else [])
            set_habit(self.ctx.store, name, done)
            self.held_until = now + 20
            self.flash = now + 0.6

    def render(self, c, now):
        names = self.names()
        if not names:
            calm(c, "Habits", ["No habits yet.", "POST /api/habits/<name>", "or set names in the options."])
            return
        if self.p["mode"] == "all":
            self.draw_rows(c, now, names)
        else:
            self.draw_grid(c, now, self.shown(now))

    def draw_grid(self, c, now, name):
        store = self.ctx.store
        rec = habits(store)["habits"].get(name) or {"days": []}
        days = set(rec["days"])
        today = dt.date.fromtimestamp(now)
        cur, best = streaks(days, today)
        fit_text(c, 0, name, w=48)
        right = f"{cur}d" + (f" best {best}" if best > cur else "")
        c.text(c.w - SMALL.width(right), 1, right, SMALL)
        weeks = int(self.p["weeks"])
        letters = bool(self.p["weekday_letters"])
        x0 = c.w - weeks * 3 + 1
        if letters:
            x0 = max(x0, 7)
            weeks = min(weeks, (c.w - 7) // 3)
            x0 = c.w - weeks * 3 + 1
        y0 = 10
        # the grid: columns are weeks (the last one is this week), rows Monday to Sunday
        monday = today - dt.timedelta(days=today.weekday())
        start = monday - dt.timedelta(weeks=weeks - 1)
        blink = int(now * 2) % 2 == 0
        for wk in range(weeks):
            for dow in range(7):
                d = start + dt.timedelta(weeks=wk, days=dow)
                x, y = x0 + wk * 3, y0 + dow * 3
                if d > today:
                    continue
                on = d.isoformat() in days
                if on or (d == today and blink) or now < self.flash:
                    c.rect(x, y, 2, 2, fill=True)
                elif d == today:
                    c.set(x, y)
        if letters:
            for dow in (0, 2, 4, 6):  # Mon, Wed, Fri, Sun: every other row, so the letters don't overlap
                c.text(0, y0 + dow * 3 - 1, DOW[dow], SMALL)
        done = today.isoformat() in days
        hint = "TODAY: DONE" if done else "PRESS A FOR TODAY"
        if len(self.names()) > 1:
            hint += "  < >"
        c.text_center(c.h - 6, hint, SMALL)

    def draw_rows(self, c, now, names):
        store = self.ctx.store
        today = dt.date.fromtimestamp(now)
        rows = names[:5]
        pitch = max(7, min(8, c.h // max(1, len(rows))))
        label_w = 32
        ndays = (c.w - label_w - 14) // 2
        for i, name in enumerate(rows):
            y = i * pitch + (c.h - pitch * len(rows)) // 2
            rec = habits(store)["habits"].get(name) or {"days": []}
            days = set(rec["days"])
            cur, _ = streaks(days, today)
            label = asciify(name).upper()
            while label and SMALL.width(label) > label_w - 2:
                label = label[:-1]
            c.text(0, y + 1, label, SMALL)
            for k in range(ndays):
                d = today - dt.timedelta(days=ndays - 1 - k)
                x = label_w + k * 2
                if d.isoformat() in days:
                    c.rect(x, y + 1, 2, 4, fill=True)
                else:  # an empty day: a dot on the baseline
                    c.set(x, y + 4)
            s = f"{cur}"
            c.text(c.w - SMALL.width(s), y + 1, s, SMALL)

    def describe(self):
        return self.title


# ---- 2. Entity board ------------------------------------------------------------------------------------------

ICONS = {
    "temperature": ["0010000", "0101000", "0101000", "0101000", "0111000", "1111100", "0111000"],
    "humidity": ["0001000", "0011100", "0111110", "0111110", "1111111", "0111110", "0011100"],
    "door": ["1111100", "1000100", "1000100", "1001100", "1000100", "1000100", "1111100"],
    "window": ["1111111", "1001001", "1001001", "1111111", "1001001", "1001001", "1111111"],
    "battery": ["0111110", "1111111", "1000001", "1000001", "1000001", "1000001", "1111111"],
    "power": ["0001100", "0011000", "0110000", "1111110", "0001100", "0011000", "0110000"],
    "motion": ["0011000", "0011000", "0111100", "1011010", "0011000", "0100100", "1000010"],
    "light": ["0011100", "0100010", "0100010", "0100010", "0011100", "0011100", "0001000"],
    "lock": ["0011100", "0100010", "0100010", "1111111", "1111111", "1101111", "1111111"],
    "plug": ["0100010", "0100010", "1111111", "1111111", "0111110", "0011100", "0001000"],
    "car": ["0011100", "0100010", "1111111", "1000001", "1111111", "0100010", "0000000"],
    "signal": ["0000001", "0000011", "0000111", "0001111", "0011111", "0111111", "1111111"],
    "pressure": ["0011100", "0100010", "1000001", "1001001", "1010001", "0100010", "0011100"],
    "gas": ["0001000", "0010100", "0100010", "0101010", "0100010", "0010100", "0001000"],
}
ICON_WORDS = {"temp": "temperature", "temperature": "temperature", "humid": "humidity", "door": "door", "window": "window",
              "opening": "door", "battery": "battery", "charge": "battery", "power": "power", "energy": "power",
              "watt": "power", "motion": "motion", "occupancy": "motion", "presence": "motion", "light": "light",
              "lamp": "light", "illuminance": "light", "lock": "lock", "plug": "plug", "outlet": "plug", "switch": "plug",
              "car": "car", "vehicle": "car", "signal": "signal", "wifi": "signal", "rssi": "signal", "pressure": "pressure",
              "gas": "gas", "co2": "gas", "carbon": "gas", "smoke": "gas"}
STATE_WORDS = {"on": "ON", "off": "OFF", "open": "OPEN", "closed": "SHUT", "locked": "LOCKED", "unlocked": "OPEN",
               "home": "HOME", "not_home": "AWAY", "away": "AWAY", "true": "YES", "false": "NO", "unavailable": "--",
               "unknown": "--", "none": "--", "charging": "CHRG", "detected": "YES", "clear": "NO", "wet": "WET", "dry": "DRY"}


def icon_for(*hints):
    """An icon name from a device class, entity id or label."""
    for h in hints:
        text = str(h or "").lower()
        if text in ICONS:
            return text
        for word, icon in ICON_WORDS.items():
            if word in text:
                return icon
    return None


def draw_icon(c, x, y, name, v=1):
    rows = ICONS.get(name)
    if not rows:
        return False
    for yy, row in enumerate(rows):
        for xx, ch in enumerate(row):
            if ch == "1":
                c.set(x + xx, y + yy, v)
    return True


def format_value(state, unit="", decimals=None):
    """A short value for the board: numbers trimmed, known states as words, °C/°F/% kept, long units cut."""
    if state is None:
        return "--", ""
    s = str(state).strip()
    key = s.lower()
    if key in STATE_WORDS:
        return STATE_WORDS[key], ""
    try:
        n = float(s.replace(",", ""))
    except ValueError:
        return asciify(s)[:10].upper(), asciify(unit)[:4]
    if decimals is None:
        decimals = 0 if abs(n) >= 100 or n == int(n) else 1
    text = f"{n:,.{decimals}f}" if abs(n) >= 10000 else f"{n:.{decimals}f}"
    u = str(unit or "").strip()
    if u in ("°C", "°F", "C", "F"):
        u = "°" + u[-1]
    return text, asciify(u)[:5] if u not in ("°C", "°F") else u


def parse_entity_lines(text):
    """One entity per line: `id | label | unit | path` (label, unit and path optional). -> [dict]"""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = [p.strip() for p in ln.split("|")]
        ent = {"id": parts[0], "label": parts[1] if len(parts) > 1 and parts[1] else "",
               "unit": parts[2] if len(parts) > 2 else "", "path": parts[3] if len(parts) > 3 else ""}
        if not ent["label"]:
            ent["label"] = ent["id"].split(".")[-1].split("/")[-1].replace("_", " ")
        out.append(ent)
    return out[:6]


def ha_state_value(doc, ent):
    """{"value", "unit", "class", "name"} from a Home Assistant /api/states/<id> document."""
    attrs = doc.get("attributes") or {} if isinstance(doc, dict) else {}
    value = doc.get("state") if isinstance(doc, dict) else doc
    if ent.get("path"):
        value = json_path(doc, ent["path"])
    return {"value": value, "unit": ent.get("unit") or attrs.get("unit_of_measurement", ""),
            "class": attrs.get("device_class", ""), "name": attrs.get("friendly_name", "")}


@register
class Entities(Widget):
    id = "entities"
    title = "Home entities"
    category = "Widgets"
    description = ("Up to six named values from your home on one board: temperature, a door, the car's charge... "
                   "From Home Assistant's REST API, MQTT state topics, or one JSON document. Icons for the usual kinds.")
    params = [
        P("source", "select", "ha", "Read from", options=[["ha", "Home Assistant (REST API)"], ["mqtt", "MQTT topics"],
                                                           ["json", "A JSON URL"]]),
        P("url", "text", "http://homeassistant.local:8123", "Home Assistant URL", show_if={"source": "ha"}),
        P("token", "text", "", "Long-lived access token (Home Assistant, Profile, Security)", show_if={"source": "ha"}),
        P("json_url", "text", "", "JSON URL", show_if={"source": "json"}),
        P("entities", "textarea", "sensor.living_room_temperature | Living room\nbinary_sensor.front_door | Front door",
          "Entities, one per line: id | label | unit | path (label, unit, path optional)"),
        P("refresh_seconds", "number", 60, "Check every (seconds)", min=10, max=3600),
        P("layout", "select", "auto", options=[["auto", "Auto"], ["grid", "Grid"], ["list", "List"]]),
        P("icons", "checkbox", True, "Icons"),
    ]
    interval = 0.5

    @classmethod
    def needs_setup(cls, store, params):
        src = params.get("source", "ha")
        if not parse_entity_lines(params.get("entities", cls.params[4]["default"])):
            return "at least one entity"
        if src == "ha":
            if not params.get("url", cls.params[1]["default"]):
                return "the Home Assistant URL"
            if not (params.get("token") or (store.settings.get("home_assistant") or {}).get("token")):
                return "a Home Assistant access token"
        elif src == "json" and not params.get("json_url"):
            return "a JSON URL"
        elif src == "mqtt":
            mq = store.settings.get("mqtt") or {}
            if not (mq.get("enabled") and mq.get("host")):
                return "an MQTT broker (Settings, MQTT)"
        return None

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(10.0, float(self.p["refresh_seconds"]))
        self.ents = parse_entity_lines(self.p["entities"])
        self._subs = []
        need = self.needs_setup(ctx.store, self.p)
        if need:
            self.setup = self.error = need
        elif self.p["source"] == "mqtt":
            cfg = ctx.store.settings["mqtt"]
            self._subs = [mqtt_watch(cfg, e["id"]) for e in self.ents]
            self.refresh = 10 ** 9

    def token(self):
        return self.p["token"] or (self.ctx.store.settings.get("home_assistant") or {}).get("token", "")

    def fetch(self):
        src = self.p["source"]
        if self.setup:
            raise NeedsSetup(self.setup)
        if src == "ha":
            base = str(self.p["url"]).rstrip("/")
            headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}
            out = []
            for e in self.ents:
                try:
                    doc = http_get(f"{base}/api/states/{e['id']}", ttl=self.refresh - 1, headers=headers)
                    out.append({**e, **ha_state_value(doc, e)})
                except Exception as ex:
                    log.info("entities: %s: %s", e["id"], ex)
                    out.append({**e, "value": None, "class": "", "name": ""})
            if all(v["value"] is None for v in out):
                raise ValueError("Home Assistant didn't answer for any entity")
            return out
        if src == "json":
            doc = http_get(str(self.p["json_url"]), ttl=self.refresh - 1)
            out = []
            for e in self.ents:
                try:
                    v = json_path(doc, e["path"] or e["id"])
                except (KeyError, IndexError, ValueError, TypeError):
                    v = None
                out.append({**e, "value": v, "class": "", "name": ""})
            return out
        return None

    def values(self):
        """What to draw now: fetched values, or the MQTT records' latest payloads."""
        if self.p["source"] == "mqtt":
            out = []
            for e, (_, rec) in zip(self.ents, self._subs):
                v = rec["value"]
                if v is not None and e.get("path"):
                    try:
                        v = number_from(v, e["path"])
                    except (ValueError, KeyError, IndexError, TypeError):
                        pass
                elif isinstance(v, str) and v.startswith("{"):
                    try:
                        v = json.loads(v).get("state", v)
                    except ValueError:
                        pass
                out.append({**e, "value": v, "class": "", "name": ""})
            return out
        return self.data or []

    def render(self, c, now):
        if self.p["source"] == "mqtt":
            if self.setup:
                return super().render(c, now)
            self.draw(c, now)
            return
        super().render(c, now)

    def draw(self, c, now):
        vals = self.values()
        if not vals:
            calm(c, "Entities", ["Nothing to show yet."])
            return
        n = len(vals)
        layout = self.p["layout"]
        if layout == "auto":
            layout = "list" if n <= 3 else "grid"
        icons = bool(self.p["icons"])
        if layout == "list":
            rows = vals[:4]
            pitch = c.h // len(rows)
            big = pitch >= 14
            for i, v in enumerate(rows):
                y = i * pitch + (pitch - (13 if big else 7)) // 2
                self.cell(c, 0, y, c.w, v, icons, big)
        else:
            cols, rows = 2, (n + 1) // 2
            cw, ch = c.w // cols, c.h // rows
            for i, v in enumerate(vals):
                x, y = (i % cols) * cw, (i // cols) * ch + (ch - 13) // 2
                self.cell(c, x, y, cw, v, icons, True, compact=ch < 14)

    def cell(self, c, x, y, w, v, icons, big, compact=False):
        icon = icon_for(v.get("class"), v["id"], v["label"]) if icons else None
        tx = x
        if icon:
            draw_icon(c, x + 1, y + (3 if big and not compact else 0), icon)
            tx = x + 10
        text, unit = format_value(v.get("value"), v.get("unit", ""))
        label = asciify(v["label"]).upper()
        room = w - (tx - x) - 1
        if big and not compact:
            while label and SMALL.width(label) > room:
                label = label[:-1]
            c.text(tx, y, label, SMALL)
            font = NORMAL if NORMAL.width(text) + (SMALL.width(unit) + 1 if unit else 0) <= room else SMALL
            c.text(tx, y + 6, text, font)
            if unit:
                c.text(tx + font.width(text) + 1, y + 6 + (2 if font is NORMAL else 0), unit, SMALL)
        else:
            vtxt = text + (" " + unit if unit else "")
            while label and SMALL.width(label) + SMALL.width(vtxt) + 3 > room:
                label = label[:-1]
            c.text(tx, y + 1, label, SMALL)
            c.text(x + w - SMALL.width(vtxt) - 1, y + 1, vtxt, SMALL)

    def stop(self):
        super().stop()
        for key, _ in self._subs:
            mqtt_unwatch(key)
        self._subs = []

    def describe(self):
        return self.title


# ---- 3. Packages ----------------------------------------------------------------------------------------------

def packages(store):
    if not hasattr(store, "_home_packages"):
        data = store._load(PACKAGES, {"packages": []})
        if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
            data = {"packages": []}
        store._home_packages = data
    return store._home_packages


def _save_packages(store):
    store._save(PACKAGES, packages(store))


def add_package(store, name, number, carrier=""):
    """POST /api/packages {"name", "number", "carrier"} -> the entry."""
    name, number, carrier = str(name or "").strip(), str(number or "").strip(), str(carrier or "").strip().lower()
    if not number:
        raise ValueError("a tracking number is needed")
    if not re.fullmatch(r"[A-Za-z0-9 -]{3,40}", number):
        raise ValueError("tracking numbers use letters, digits, spaces and dashes (3 to 40)")
    with _lock:
        data = packages(store)["packages"]
        for p in data:
            if p["number"] == number:
                p.update(name=name or p["name"], carrier=carrier or p["carrier"])
                _save_packages(store)
                return p
        pid = max([p["id"] for p in data] or [0]) + 1
        entry = {"id": pid, "name": name or number, "number": number, "carrier": carrier, "added": time.time(),
                 "status": "", "eta": "", "updated": 0, "delivered": False}
        data.append(entry)
        _save_packages(store)
        return entry


def remove_package(store, pid):
    with _lock:
        data = packages(store)["packages"]
        before = len(data)
        data[:] = [p for p in data if str(p["id"]) != str(pid) and p["number"] != str(pid)]
        if len(data) != before:
            _save_packages(store)
            return True
        return False


def set_package_status(store, pid, status, eta="", delivered=None):
    """POST /api/packages/<id> {"status", "eta", "delivered"}: a status from outside (an automation, a script)."""
    with _lock:
        for p in packages(store)["packages"]:
            if str(p["id"]) == str(pid) or p["number"] == str(pid):
                p["status"], p["eta"], p["updated"] = str(status or "")[:60], str(eta or "")[:20], time.time()
                if delivered is not None:
                    p["delivered"] = bool(delivered)
                elif re.search(r"deliver", p["status"], re.I) and not re.search(r"out for|will be|expected", p["status"], re.I):
                    p["delivered"] = True
                _save_packages(store)
                return p
    raise KeyError(pid)


def parse_carrier_lines(text):
    """`carrier | url with {number} | status path | eta path | headers JSON` per line -> {carrier: dict}"""
    out = {}
    for ln in str(text or "").splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1] or parts[0].startswith("#"):
            continue
        headers = None
        if len(parts) > 4 and parts[4]:
            try:
                headers = json.loads(parts[4])
            except ValueError:
                headers = None
        out[parts[0].lower()] = {"url": parts[1], "status": parts[2] if len(parts) > 2 else "",
                                 "eta": parts[3] if len(parts) > 3 else "", "headers": headers}
    return out


def shape_status(doc, carrier):
    """(status text, eta text) from a carrier's JSON document."""
    status = eta = ""
    try:
        v = json_path(doc, carrier["status"]) if carrier["status"] else doc
        status = v if isinstance(v, str) else json.dumps(v)[:60]
    except (KeyError, IndexError, ValueError, TypeError):
        status = ""
    if carrier["eta"]:
        try:
            eta = str(json_path(doc, carrier["eta"]) or "")
        except (KeyError, IndexError, ValueError, TypeError):
            eta = ""
    return asciify(status)[:60], short_date(eta)


def short_date(s):
    """'2026-10-14T18:00:00Z' -> 'Oct 14'; other text kept short."""
    s = str(s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3])).strftime("%b %d").replace(" 0", " ")
        except ValueError:
            pass
    return asciify(s)[:12]


@register
class Packages(Widget):
    id = "packages"
    title = "Packages"
    category = "Widgets"
    description = ("Your parcels on the way: each one's last status line and delivery date. Add them from the API "
                   "(POST /api/packages); the status comes from a JSON URL you set per carrier, with {number} in it, "
                   "or is posted in by an automation.")
    params = [
        P("carriers", "textarea", "", "Carriers, one per line: name | JSON URL with {number} | status path | eta path | headers JSON"),
        P("refresh_seconds", "number", 900, "Check every (seconds)", min=60, max=86400),
        P("hide_delivered_days", "number", 2, "Hide delivered after (days)", min=0, max=30),
        P("page_seconds", "number", 6, "Seconds per page", min=2, max=60),
    ]
    interval = 0.5

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(60.0, float(self.p["refresh_seconds"]))
        self.carriers = parse_carrier_lines(self.p["carriers"])
        self.data = []  # the list itself is always drawable

    def fetch(self):
        got = []
        for p in list(packages(self.ctx.store)["packages"]):
            car = self.carriers.get(p["carrier"]) or self.carriers.get("default") or (self.carriers.get("*"))
            if not car or p.get("delivered"):
                continue
            url = car["url"].replace("{number}", p["number"])
            try:
                doc, _, _ = cached_get(self.ctx.store, f"package-{p['number']}", url, self.refresh - 1, headers=car["headers"],
                                       shape=lambda d, car=car: shape_status(d, car))
                status, eta = doc
                if status or eta:
                    set_package_status(self.ctx.store, p["id"], status, eta)
                    got.append(p["number"])
            except Exception as e:
                log.info("packages: %s: %s", p["number"], e)
        return got

    def visible(self, now):
        hide = float(self.p["hide_delivered_days"]) * 86400
        out = []
        for p in packages(self.ctx.store)["packages"]:
            if p.get("delivered") and now - (p.get("updated") or p.get("added") or 0) > hide:
                continue
            out.append(p)
        return out

    def render(self, c, now):
        self.poll()
        rows = self.visible(now)
        if not rows:
            calm(c, "Packages", ["Nothing on the way.", "POST /api/packages", '{"name", "number", "carrier"}'])
            return
        per = 3
        pages = [rows[i:i + per] for i in range(0, len(rows), per)]
        page = pages[int(now / float(self.p["page_seconds"])) % len(pages)]
        pitch = c.h // per
        for i, p in enumerate(page):
            y = i * pitch
            fit_text(c, y, p["name"], w=c.w - 30)
            eta = p.get("eta") or ""
            right = ("DONE" if p.get("delivered") else eta.upper())
            if right:
                c.text(c.w - SMALL.width(right), y + 1, right, SMALL)
            status = asciify(p.get("status") or "no status").upper()
            while status and SMALL.width(status) > c.w:
                status = status[:-1]
            c.text(0, y + 8, status, SMALL)
        if len(pages) > 1:
            for k in range(len(pages)):
                c.set(c.w - 1 - 2 * (len(pages) - 1 - k), c.h - 1, 1 if pages[k] is page else 0)

    def describe(self):
        return self.title


# ---- 4. Word of the day, On this day (offline) ----------------------------------------------------------------

def all_words():
    """[(word, part of speech, definition, example)] from both bundled lists."""
    out = [(w, p, d, "") for w, p, d in bundled("words.json")["words"]]
    out += [(w, p, d, e) for w, p, d, e in bundled("words_more.json")["words"]]
    return out


@register
class WordOfDay(Widget):
    id = "wordofday"
    title = "Word of the day"
    category = "Widgets"
    description = ("A word a day from a built-in list of a few hundred, with its meaning and an example sentence; works "
                   "offline. Quiz mode shows the meaning first and reveals the word after a moment.")
    params = [
        P("mode", "select", "word", options=[["word", "Word, then meaning"], ["quiz", "Meaning first, then the word"]]),
        P("example", "checkbox", True, "Show the example sentence"),
        *PAGE_PARAMS,
    ]
    interval = 0.5
    refresh = 10 ** 9

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.pager = Pager(self.p["page_seconds"], "pages", self.p["text_size"], bool(self.p["header"]))

    def fetch(self):
        return None

    def today(self, now):
        day = dt.date.fromtimestamp(now)
        return day, day_pick(all_words(), day, "wordofday")

    def render(self, c, now):
        day, (word, pos, meaning, example) = self.today(now)
        items = [Item(meaning, "", "Word of the day", pos)]
        if example and self.p["example"]:
            items.append(Item(f'"{example}"', "", word, pos))
        self.pager.set(items, (str(day), self.p["mode"]), now)
        per = float(self.p["page_seconds"])
        pages = [pg for i in range(len(items)) for pg in self.pager.pages(i, c.w, c.h - (7 if self.pager.header else 0))]
        seq = ["word"] + ["page"] * len(pages)
        if self.p["mode"] == "quiz":
            seq = ["page"] * len(self.pager.pages(0, c.w, c.h - 7)) + ["word"] + ["page"] * (len(pages) - len(self.pager.pages(0, c.w, c.h - 7)))
        k = int((now - self.pager.start) / per) % len(seq)
        if seq[k] == "word":
            if self.pager.header:
                self.pager.draw_header(c, Item("", "", "Word of the day", pos))
            scale = 2 if NORMAL.width(word) * 2 <= c.w else 1
            font = NORMAL if NORMAL.width(word) <= c.w else SMALL
            y = 12 if self.pager.header else 8
            c.text_center(y, word, font, scale=scale if font is NORMAL else 1)
            c.text_center(y + 7 * scale + 5, pos, SMALL)
            return
        pi = k - (1 if self.p["mode"] == "word" else 0)
        if self.p["mode"] == "quiz" and k > len(self.pager.pages(0, c.w, c.h - 7)):
            pi = k - 1
        pi = max(0, min(len(pages) - 1, pi))
        item = items[0] if pi < len(self.pager.pages(0, c.w, c.h - 7)) else items[1]
        if self.pager.header:
            label = "WHAT WORD?" if self.p["mode"] == "quiz" and pi < len(self.pager.pages(0, c.w, c.h - 7)) else item.label
            self.pager.draw_header(c, Item("", "", label, item.right))
        top = 7 if self.pager.header else 0
        draw_page(c, pages[pi], 0, top, c.w, c.h - top)

    def describe(self):
        return self.title


def events_for(day, events=None):
    """Today's facts (month, day match), oldest first."""
    events = bundled("onthisday.json")["events"] if events is None else events
    out = [e for e in events if e[0] == day.month and e[1] == day.day]
    return sorted(out, key=lambda e: (e[2] if isinstance(e[2], int) else -10 ** 6))


def years_ago(year, today):
    if not isinstance(year, int):
        return str(year)
    n = today.year - year
    return f"{n} years ago" if n != 1 else "a year ago"


@register
class OnThisDay(Widget):
    id = "onthisday"
    title = "On this day"
    category = "Widgets"
    description = ("What happened on today's date, from a built-in list with at least one event for every day of the "
                   "year: works offline. (Daily channels has the Wikipedia version.)")
    params = [
        P("show_years_ago", "checkbox", True, "Say how many years ago"),
        *PAGE_PARAMS,
    ]
    interval = 0.5
    refresh = 10 ** 9

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.pager = Pager(self.p["page_seconds"], self.p["style"], self.p["text_size"], bool(self.p["header"]))

    def fetch(self):
        return None

    def render(self, c, now):
        day = dt.date.fromtimestamp(now)
        evs = events_for(day)
        items = []
        for m, d, year, text in evs:
            by = years_ago(year, day) if self.p["show_years_ago"] else ""
            items.append(Item(text, by, day.strftime("%d %B").lstrip("0"), str(year)))
        if not items:
            calm(c, "On this day", ["Nothing on file for today."])
            return
        self.pager.set(items, str(day), now)
        self.pager.draw(c, now)

    def describe(self):
        return self.title


# ---- 5. Network status ----------------------------------------------------------------------------------------

def gateway():
    """The default gateway's IPv4 address from /proc/net/route, or None."""
    try:
        for ln in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = ln.split()
            if len(f) >= 3 and f[1] == "00000000":
                gw = int(f[2], 16)
                return socket.inet_ntoa(gw.to_bytes(4, "little"))
    except (OSError, ValueError):
        pass
    return None


def probe_host(host, method="ping", port=443, timeout=1.0):
    """Round-trip time in ms, or None when the host doesn't answer."""
    if method == "tcp":
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return (time.perf_counter() - t0) * 1000
        except OSError:
            return None
    try:
        out = subprocess.run(["ping", "-n", "-c", "1", "-W", str(max(1, int(math.ceil(timeout)))), host],
                             capture_output=True, text=True, timeout=timeout + 2)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out.stdout)
    return float(m.group(1)) if m and out.returncode == 0 else None


def read_sys():
    """{"temp": CPU °C or None, "load": 1-minute load or None, "up": uptime seconds or None, "mem": used fraction}"""
    out = {"temp": None, "load": None, "up": None, "mem": None}
    for p in ("/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"):
        try:
            out["temp"] = int(Path(p).read_text().strip()) / 1000
            break
        except (OSError, ValueError):
            continue
    try:
        out["load"] = float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        out["up"] = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        mem = {}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            mem[k] = int(v.split()[0])
        out["mem"] = 1 - mem["MemAvailable"] / mem["MemTotal"]
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    return out


def uptime_text(secs):
    if secs is None:
        return "--"
    d, r = divmod(int(secs), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return f"{d}d{h}h" if d else (f"{h}h{m:02d}" if h else f"{m}m")


@register
class NetStat(Widget):
    id = "netstat"
    title = "Network status"
    category = "Widgets"
    description = ("Latency to a few hosts as sparklines (the router and 1.1.1.1 by default) with the last reading, "
                   "and the Pi's CPU temperature, load and uptime along the bottom.")
    params = [
        P("hosts", "text", "gateway, 1.1.1.1", "Hosts, comma-separated (gateway = your router)"),
        P("method", "select", "ping", "Probe", options=[["ping", "Ping (ICMP)"], ["tcp", "TCP connect"]]),
        P("port", "number", 443, "Port (TCP connect)", min=1, max=65535, show_if={"method": "tcp"}),
        P("every", "number", 5, "Probe every (seconds)", min=2, max=120),
        P("ceiling_ms", "number", 100, "Sparkline top (ms)", min=10, max=2000),
        P("system", "checkbox", True, "CPU temperature, load and uptime"),
    ]
    interval = 0.5

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(2.0, float(self.p["every"]))
        names = [h.strip() for h in str(self.p["hosts"] or "").split(",") if h.strip()][:3]
        self.hosts = []
        for n in names:
            addr = gateway() if n.lower() in ("gateway", "router") else n
            self.hosts.append({"name": "ROUTER" if n.lower() in ("gateway", "router") else n, "addr": addr, "hist": []})
        self.sys = {}

    def fetch(self):
        for h in self.hosts:
            ms = probe_host(h["addr"], self.p["method"], int(self.p["port"])) if h["addr"] else None
            h["hist"] = (h["hist"] + [ms])[-40:]
        if self.p["system"]:
            self.sys = read_sys()
        return time.time()

    def draw(self, c, now):
        rows = self.hosts or []
        if not rows:
            calm(c, "Network", ["No hosts set."])
            return
        bottom = 7 if self.p["system"] else 0
        pitch = (c.h - bottom) // max(1, len(rows))
        pitch = min(pitch, 12)
        top = max(0, (c.h - bottom - pitch * len(rows)) // 2)
        ceiling = float(self.p["ceiling_ms"])
        spark_w = 40
        for i, h in enumerate(rows):
            y = top + i * pitch
            name = asciify(h["name"]).upper()
            while name and SMALL.width(name) > 26:
                name = name[:-1]
            c.text(0, y + (pitch - 5) // 2, name, SMALL)
            x0 = 28
            hist = h["hist"][-spark_w:]
            gh = pitch - 2
            for k, ms in enumerate(hist):
                x = x0 + spark_w - len(hist) + k
                if ms is None:
                    c.set(x, y + gh)  # a dropped probe: a dot on the floor, nothing above
                    c.set(x, y)
                    continue
                hgt = max(1, min(gh, int(round(math.log1p(ms) / math.log1p(ceiling) * gh))))
                c.line(x, y + gh, x, y + gh - hgt + 1)
            last = hist[-1] if hist else None
            txt = "--" if last is None else (f"{last:.0f}ms" if last >= 10 else f"{last:.1f}ms")
            c.text(c.w - SMALL.width(txt), y + (pitch - 5) // 2, txt, SMALL)
        if self.p["system"]:
            s = self.sys or {}
            parts = []
            if s.get("temp") is not None:
                parts.append(f"{s['temp']:.0f}°C")
            if s.get("load") is not None:
                parts.append(f"LD {s['load']:.2f}")
            if s.get("up") is not None:
                parts.append("UP " + uptime_text(s["up"]))
            line = " ".join(parts) or "SYSTEM --"
            while SMALL.width(line) > c.w and " " in line:
                line = line.rsplit(" ", 1)[0]
            c.text_center(c.h - 6, line, SMALL)

    def describe(self):
        return self.title
