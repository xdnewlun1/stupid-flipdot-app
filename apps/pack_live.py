"""Live-data boards: next departures (GTFS-realtime or any JSON API), planes overhead (ADS-B receiver,
adsb.lol or OpenSky) and a live weather sky.

Every fetch is cached, sends a User-Agent, backs off after errors and keeps the last good data on screen,
so a dropped connection never blanks the board.
"""

import datetime as dt
import hashlib
import json
import logging
import math
import os
import random
import tempfile
import threading
import time
import urllib.error
import urllib.parse

import gtfs_rt
import gtfs_static
from charfx import CharFx
from fonts import MONO, NORMAL, SMALL
from transitions import NAMES as TRANSITIONS

from .base import NeedsSetup, P, Widget, http_get, register
from .widgets import WEATHER_TEXT, fetch_weather, geocode, json_path, weather_kind

log = logging.getLogger("apps.live")

MAX_BACKOFF = 900  # seconds between retries at worst


def cache_dir(ctx):
    d = getattr(ctx.store, "dir", None)
    path = os.path.join(str(d), "cache", "live") if d else os.path.join(tempfile.gettempdir(), "flipdot-live")
    os.makedirs(path, exist_ok=True)
    return path


class LiveWidget(Widget):
    """A Widget that waits longer after each failed fetch (up to MAX_BACKOFF) and keeps its last data."""

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self._fails = 0
        self._next_at = 0.0
        self.fetched_at = 0.0

    def poll(self):
        if self._stopped or self._fetching or time.time() < self._next_at:
            return
        self._fetching = True
        self._last_fetch = time.time()
        threading.Thread(target=self._do_fetch, daemon=True).start()

    def wait_after(self, ok):
        """Seconds until the next fetch."""
        return self.refresh if ok else min(MAX_BACKOFF, self.refresh * 2 ** min(self._fails, 6))

    def _do_fetch(self):
        ok = False
        try:
            data = self.fetch()
            self.data, self.error, self.setup = data, None, None
            self.fetched_at = time.time()
            self._fails = 0
            ok = True
        except NeedsSetup as e:
            self.error = self.setup = str(e)
        except Exception as e:
            log.warning("%s: %s", self.id, e)
            self.error = str(e)[:120]
            self._fails += 1
        finally:
            self._next_at = time.time() + self.wait_after(ok)
            self._fetching = False


def _headers(name, value):
    return {name.strip(): value.strip()} if name.strip() and value.strip() else None


def _fit(text, font, width):
    """Cut text to fit width (in dots)."""
    while text and font.width(text) > width:
        text = text[:-1]
    return text.rstrip()


def _fit_words(text, font, width):
    """Cut at a word boundary when one fits, else mid-word."""
    if font.width(text) <= width:
        return text
    words = text.split()
    while len(words) > 1:
        words.pop()
        if font.width(" ".join(words)) <= width:
            return " ".join(words)
    return _fit(text, font, width)


def _char_kind(choice):
    """'auto' = split-flap when that character transition exists, else plain updates."""
    if choice == "auto":
        return next((n for n in ("splitflap", "split-flap", "flap") if n in TRANSITIONS), "none")
    return choice


CHAR_CHOICES = [["auto", "Split-flap (if available)"], ["none", "None"]] + \
    [[n, n] for n in TRANSITIONS if n not in ("none", "random")]


# ---- next departures ----------------------------------------------------------------------------------------

def _event_time(stu, sched_t):
    """Predicted time from a StopTimeUpdate: absolute time, else the schedule plus the delay."""
    for ev in (stu.get("dep"), stu.get("arr")):
        if ev and ev.get("time"):
            return ev["time"]
    for ev in (stu.get("dep"), stu.get("arr")):
        if ev and ev.get("delay") is not None and sched_t is not None:
            return sched_t + ev["delay"]
    return None


def merge_departures(idx, feed, stop_ids, now, horizon=3 * 3600):
    """Scheduled departures (idx, may be None) corrected by GTFS-rt TripUpdates (feed, may be None).
    -> [{"route", "dest", "t", "live", "trip_id"}] sorted by time."""
    stops = {s.strip() for s in stop_ids if s.strip()}
    if idx:
        stops |= set(idx["stops"])
    updates = {}
    for tu in (feed or {}).get("updates", []):
        updates.setdefault(tu["trip"]["trip_id"], []).append(tu)

    def route_name(route_id):
        r = (idx or {}).get("routes", {}).get(route_id)
        return (r[0] or r[1]) if r else route_id

    def find_update(trip_id, date):
        for tu in updates.get(trip_id, []):
            if not tu["trip"]["start_date"] or not date or tu["trip"]["start_date"] == date:
                return tu
        return None

    out, matched = [], set()
    for d in (gtfs_static.scheduled(idx, now, horizon) if idx else []):
        tu = find_update(d["trip_id"], d["date"])
        t, live = d["t"], False
        if tu:
            matched.add(id(tu))
            if tu["trip"]["rel"] == gtfs_rt.CANCELED:
                continue
            stu = next((s for s in tu["stops"] if s["stop_id"] == d["stop_id"] or
                        (s["seq"] is not None and s["seq"] == d["seq"])), None)
            if stu and stu["rel"] == gtfs_rt.STU_SKIPPED:
                continue
            pred = _event_time(stu, d["t"]) if stu and stu["rel"] != gtfs_rt.STU_NO_DATA else None
            if pred is None and not stu:
                # no update for this stop: an earlier stop's delay carries on (GTFS-rt propagation)
                before = [s for s in tu["stops"] if s["seq"] is not None and s["seq"] < d["seq"]]
                for s in sorted(before, key=lambda s: s["seq"], reverse=True):
                    ev = s.get("dep") or s.get("arr") or {}
                    if ev.get("delay") is not None:
                        pred = d["t"] + ev["delay"]
                        break
                if pred is None and tu["delay"] is not None:
                    pred = d["t"] + tu["delay"]
            if pred is not None:
                t, live = pred, True
        out.append({"route": route_name(d["route_id"]), "dest": d["headsign"], "t": t, "live": live,
                    "trip_id": d["trip_id"]})
    # trips the schedule doesn't have (added trips, or no static feed at all)
    for tus in updates.values():
        for tu in tus:
            if id(tu) in matched or tu["trip"]["rel"] == gtfs_rt.CANCELED:
                continue
            trip_id = tu["trip"]["trip_id"]
            trip = (idx or {}).get("trips", {}).get(trip_id)
            for s in tu["stops"]:
                if s["stop_id"] in stops and s["rel"] != gtfs_rt.STU_SKIPPED:
                    t = _event_time(s, None)
                    if t and now - 120 <= t <= now + horizon:
                        dest = (trip[2] if trip else "") or (idx or {}).get("dest", {}).get(trip_id, "")
                        if not dest:  # e.g. an added trip: the route's long name says where it goes
                            r = (idx or {}).get("routes", {}).get(tu["trip"]["route_id"])
                            dest = r[1] if r else ""
                        out.append({"route": route_name(tu["trip"]["route_id"] or (trip[0] if trip else "")),
                                    "dest": dest, "t": t, "live": True, "trip_id": trip_id})
    out.sort(key=lambda d: d["t"])
    return out


def parse_time(v, kind, fetched):
    if v is None or v == "":
        return None
    if kind == "minutes":
        return fetched + float(v) * 60
    if kind == "epoch":
        v = float(v)
        return v / 1000 if v > 1e11 else v
    s = str(v).strip().replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        try:  # bare time of day "14:05": the next one
            hh, mm = s.split(":")[:2]
            base = dt.datetime.fromtimestamp(fetched).replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if base.timestamp() < fetched - 3600:
                base += dt.timedelta(days=1)
            return base.timestamp()
        except ValueError:
            return None
    return t.timestamp()


def json_departures(data, list_path, route_path, dest_path, time_path, kind, fetched):
    items = json_path(data, list_path) if list_path.strip() else data
    if isinstance(items, dict):
        items = list(items.values())
    out = []
    for it in items or []:
        def get(path):
            try:
                return json_path(it, path) if path.strip() else ""
            except (KeyError, IndexError, ValueError, TypeError):
                return ""
        t = parse_time(get(time_path), kind, fetched)
        if t is not None:
            out.append({"route": str(get(route_path)), "dest": str(get(dest_path)), "t": t, "live": True})
    out.sort(key=lambda d: d["t"])
    return out


def route_label(name):
    """At most 4 characters: 'Night Owl' -> 'NO', 'Express' -> 'Expr'."""
    name = name.strip()
    words = name.split()
    if len(name) > 4 and len(words) > 1:
        return "".join(w[0] for w in words)[:4].upper()
    return name[:4]


def minutes_text(secs, t, hour24=True):
    """'now' at the stop, 'due' within a minute, whole minutes below an hour, else the clock time."""
    if secs <= 15:
        return "now"
    if secs < 60:
        return "due"
    if secs < 3600:
        return str(int(secs // 60))
    tm = dt.datetime.fromtimestamp(t)
    return tm.strftime("%H:%M") if hour24 else tm.strftime("%I:%M").lstrip("0")


@register
class Departures(LiveWidget):
    id = "departures"
    title = "Next departures"
    category = "Widgets"
    description = ("A station departure sign: route, destination and minutes for your stop, from a GTFS-realtime "
                   "feed (with the agency's GTFS schedule for names) or any JSON departures API.")
    _gtfs, _json = {"source": "gtfs"}, {"source": "json"}
    params = [
        P("source", "select", "gtfs", "Data source", options=[["gtfs", "GTFS / GTFS-realtime"], ["json", "JSON URL"]]),
        P("stops", "text", "", "Stop IDs (comma separated)", show_if=_gtfs),
        P("static_url", "text", "", "GTFS schedule zip URL (names and times)", show_if=_gtfs),
        P("rt_url", "text", "", "GTFS-realtime TripUpdates URL (optional)", show_if=_gtfs),
        P("key_header", "text", "", "API key header name (optional, e.g. x-api-key)", show_if=_gtfs),
        P("key", "text", "", "API key", show_if=_gtfs),
        P("json_url", "text", "", "Departures JSON URL", show_if=_json),
        P("json_headers", "textarea", "", "Extra headers (JSON object, optional)", show_if=_json),
        P("json_list", "text", "departures", "Path to the list of departures", show_if=_json),
        P("json_route", "text", "route", "Route field", show_if=_json),
        P("json_dest", "text", "destination", "Destination field", show_if=_json),
        P("json_time", "text", "time", "Time field", show_if=_json),
        P("json_time_kind", "select", "iso", "Time format", show_if=_json,
          options=[["iso", "Date/time text (ISO 8601 or HH:MM)"], ["epoch", "Unix time"], ["minutes", "Minutes from now"]]),
        P("routes", "text", "", "Only these routes (comma separated, blank = all)"),
        P("walk", "number", 0, "Walk time to the stop (min): hides what you can't catch", min=0, max=60),
        P("rows", "number", 4, "Rows", min=1, max=6),
        P("header", "checkbox", True, "Stop name and clock on top"),
        P("hour24", "checkbox", True, "24-hour times"),
        P("refresh_seconds", "number", 30, "Update every (s)", min=15, max=3600),
        P("char_transition", "select", "auto", "Character transition", options=CHAR_CHOICES),
        P("char_time", "number", 0.4, "Character transition time (s)", min=0.1, max=3, step=0.1,
          show_if={"char_transition": {"not": "none"}}),
    ]
    interval = 0.5

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(15.0, float(self.p["refresh_seconds"] or 30))
        self.fx = CharFx(_char_kind(self.p["char_transition"]), self.p["char_time"])
        self._feed = None  # last good realtime feed: (fetched at, parsed)
        self.rt_error = None

    @classmethod
    def needs_setup(cls, store, params):
        p = {q["name"]: q["default"] for q in cls.params}
        p.update({k: v for k, v in params.items() if v is not None})
        if p["source"] == "json":
            return None if p["json_url"].strip() else "a departures JSON URL"
        if not p["stops"].strip():
            return "your stop IDs"
        if not (p["static_url"].strip() or p["rt_url"].strip()):
            return "a GTFS schedule or realtime feed URL"
        return None

    def stop_ids(self):
        return [s.strip() for s in self.p["stops"].split(",") if s.strip()]

    def fetch(self, now=None):
        msg = self.needs_setup(self.ctx.store, self.p)
        if msg:
            raise NeedsSetup("Set " + msg + " in the app's options")
        now = time.time() if now is None else now
        if self.p["source"] == "json":
            hdr = json.loads(self.p["json_headers"]) if self.p["json_headers"].strip() else None
            data = http_get(self.p["json_url"].strip(), ttl=self.refresh - 1, headers=hdr)
            deps = json_departures(data, self.p["json_list"], self.p["json_route"], self.p["json_dest"],
                                   self.p["json_time"], self.p["json_time_kind"], now)
            return {"deps": deps, "stop": "", "live": True}
        headers = _headers(self.p["key_header"], self.p["key"])
        idx = None
        if self.p["static_url"].strip():
            idx = gtfs_static.load_index(cache_dir(self.ctx), self.p["static_url"].strip(), self.stop_ids(), headers)
        live = False
        if self.p["rt_url"].strip():
            try:
                raw = http_get(self.p["rt_url"].strip(), ttl=self.refresh - 1, headers=headers, raw=True)
                wanted = set(self.stop_ids()) | set((idx or {}).get("stops", {}))
                trips = (idx or {}).get("trips", {})
                feed = gtfs_rt.parse_feed(raw, keep=lambda tu: tu["trip"]["trip_id"] in trips or
                                          any(s["stop_id"] in wanted for s in tu["stops"]))
                self._feed, self.rt_error, live = (now, feed), None, True
            except Exception as e:
                if not idx and not self._feed:
                    raise
                log.warning("departures realtime: %s", e)
                self.rt_error = str(e)[:80]
        feed = self._feed[1] if self._feed and now - self._feed[0] < 900 else None
        live = live or feed is not None
        deps = merge_departures(idx, feed, self.stop_ids(), now)
        name = ""
        if idx:
            names = [idx["stops"].get(s, "") for s in self.stop_ids()]
            name = next((n for n in names if n), "")
        return {"deps": deps, "stop": name, "live": live}

    def visible(self, now):
        """Departures still worth showing: not gone, catchable on foot, on a chosen route."""
        routes = {r.strip().lower() for r in self.p["routes"].split(",") if r.strip()}
        walk = float(self.p["walk"] or 0) * 60
        out = []
        for d in self.data["deps"]:
            secs = d["t"] - now
            if secs < -30 or (walk and secs < walk):
                continue
            if routes and d["route"].lower() not in routes:
                continue
            out.append(d)
        return out

    def has_data(self, now, minutes=None):
        """For a playlist's "only show if it has something to show": a departure to catch (within `minutes`)."""
        if not self.data:
            return False
        deps = self.visible(now)
        return any(d["t"] - now <= minutes * 60 for d in deps) if minutes else bool(deps)

    def render(self, c, now):
        self.poll()
        if self.data is None:
            return super().render(c, now)
        boxes = []
        self.draw(c, now, boxes)
        self.fx.apply(c, boxes, now)
        self.interval = 0.04 if self.fx.active else 0.5

    def draw(self, c, now, boxes=None):
        rows = max(1, min(6, int(self.p["rows"] or 4)))
        top = 0
        if self.p["header"]:
            clock = dt.datetime.fromtimestamp(now).strftime("%H:%M" if self.p["hour24"] else "%I:%M")
            clock = clock if self.p["hour24"] else clock.lstrip("0")
            cw = SMALL.width(clock)
            c.text(c.w - cw, 0, clock, SMALL)
            stale = self.error or self.rt_error
            label = "SCHEDULED" if stale and self.p["source"] == "gtfs" else (self.data.get("stop") or "")
            c.text(0, 0, _fit(label.upper(), SMALL, c.w - cw - 3), SMALL)
            top = 7
        pitch = (c.h - top) // rows
        font = NORMAL if pitch >= 8 else SMALL
        deps = self.visible(now)[:rows]
        if not deps:
            c.text_center(top + (c.h - top - 7) // 2, "No departures", NORMAL if c.w >= 80 else SMALL)
            return
        walk = float(self.p["walk"] or 0) * 60
        rw = max(font.width(route_label(d["route"])) for d in deps)
        mono = MONO if font is NORMAL else SMALL
        cell = (5 if font is NORMAL else 3) + 1
        for i, d in enumerate(deps):
            y = top + i * pitch + max(0, (pitch - font.height) // 2)
            secs = d["t"] - now
            mins = minutes_text(secs, d["t"], self.p["hour24"])
            leave = (walk and secs < walk + 60) or (not walk and secs < 60)
            v = 1
            if leave:  # "leave now": the row inverted, like a highlighted line on a station sign
                c.rect(0, y - 1, c.w, font.height + 2, fill=True)
                v = 0
            c.text(0, y, route_label(d["route"]), font, v=v, boxes=boxes, slot=("r", i))
            mw = len(mins) * cell - 1
            mx = c.w - 5 * cell + 1  # minutes: a fixed 5-character column, right aligned, so digits flap in place
            pad = 5 - len(mins)
            for j, ch in enumerate(mins):
                c.text(mx + (pad + j) * cell, y, ch, mono, v=v, boxes=boxes, slot=("m", i, pad + j))
            dx = rw + 3
            room = c.w - mw - dx - 3
            c.text(dx, y, _fit_words(d["dest"], font, room), font, v=v, boxes=boxes, slot=("d", i))

    def describe(self):
        stop = (self.data or {}).get("stop")
        return "Departures" + (f": {stop}" if stop else "")


# ---- planes overhead ----------------------------------------------------------------------------------------

KT_PER_MS = 1.943844
FT_PER_M = 3.28084
EARTH_KM = 6371.0
OPENSKY_DAILY = 400   # anonymous API credits per day (1 credit per small-area request)
OPENSKY_MIN = 15      # seconds: never faster than this
_opensky = {"day": "", "used": 0, "until": 0.0}
_routes = {}          # callsign -> (looked up at, "LHR-JFK" or "")
_routes_lock = threading.Lock()


def offset_km(lat0, lon0, lat, lon):
    """(east, north) km from (lat0, lon0): flat-earth, fine within a few hundred km."""
    north = math.radians(lat - lat0) * EARTH_KM
    east = math.radians((lon - lon0 + 540) % 360 - 180) * EARTH_KM * math.cos(math.radians(lat0))
    return east, north


def parse_readsb(d, now):
    """dump1090 / readsb / tar1090 aircraft.json, or adsb.lol's v2 API ('ac')."""
    out = []
    ref = d.get("now") or now
    for a in d.get("aircraft") or d.get("ac") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        alt = a.get("alt_baro", a.get("altitude"))
        if alt == "ground":
            continue
        seen = a.get("seen_pos", a.get("seen", 0)) or 0
        out.append({"id": a.get("hex", ""), "callsign": (a.get("flight") or "").strip(),
                    "lat": float(a["lat"]), "lon": float(a["lon"]),
                    "alt_ft": float(alt) if isinstance(alt, (int, float)) else None,
                    "gs_kt": float(a.get("gs") or a.get("speed") or 0), "track": a.get("track"),
                    "type": a.get("t", ""), "t_pos": ref - float(seen)})
    return out


def parse_opensky(d, now):
    out = []
    for s in d.get("states") or []:
        # [icao24, callsign, country, time_position, last_contact, lon, lat, baro_alt(m), on_ground,
        #  velocity(m/s), true_track, vertical_rate, sensors, geo_alt, squawk, spi, position_source]
        if s[5] is None or s[6] is None or s[8]:
            continue
        alt = s[7] if s[7] is not None else s[13]
        out.append({"id": s[0], "callsign": (s[1] or "").strip(), "lat": s[6], "lon": s[5],
                    "alt_ft": alt * FT_PER_M if alt is not None else None, "gs_kt": (s[9] or 0) * KT_PER_MS,
                    "track": s[10], "type": "", "t_pos": float(s[3] or s[4] or d.get("time") or now)})
    return out


def project(p, now):
    """Where a plane is now: its last position moved on along its track at its ground speed (max 60 s)."""
    if p.get("track") is None or not p.get("gs_kt"):
        return p["lat"], p["lon"]
    dt_s = max(0.0, min(60.0, now - p["t_pos"]))
    km = p["gs_kt"] / KT_PER_MS * dt_s / 1000
    tr = math.radians(p["track"])
    lat = p["lat"] + math.degrees(km * math.cos(tr) / EARTH_KM)
    lon = p["lon"] + math.degrees(km * math.sin(tr) / (EARTH_KM * math.cos(math.radians(p["lat"]))))
    return lat, lon


def lookup_route(callsign, now=None, getter=None):
    """'LHR-JFK' from adsbdb.com (free, no key), cached for 12 h; '' when unknown. Routes there are what the
    callsign usually flies, so treat them as a hint."""
    now = time.time() if now is None else now
    cs = callsign.strip().upper()
    if not cs:
        return ""
    with _routes_lock:
        hit = _routes.get(cs)
        if hit and now - hit[0] < 12 * 3600:
            return hit[1]
    route = ""
    try:
        d = (getter or http_get)(f"https://api.adsbdb.com/v0/callsign/{urllib.parse.quote(cs)}", ttl=3600)
        fr = (d.get("response") or {}).get("flightroute") if isinstance(d.get("response"), dict) else None
        if fr:
            o, de = fr.get("origin") or {}, fr.get("destination") or {}
            a, b = o.get("iata_code") or o.get("icao_code"), de.get("iata_code") or de.get("icao_code")
            route = f"{a}-{b}" if a and b else ""
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    with _routes_lock:
        if len(_routes) > 500:
            _routes.clear()
        _routes[cs] = (now, route)
    return route


def plane_icon(c, x, y, track, v=1):
    """A 7-dot plane pointing along track (degrees, 0 = up/north): fuselage, swept wings, tailplane."""
    a = math.radians(track or 0)
    fx, fy = math.sin(a), -math.cos(a)   # forward
    px, py = -fy, fx                     # to the right

    def pt(f, r):
        return round(x + f * fx + r * px), round(y + f * fy + r * py)

    c.line(*pt(-3, 0), *pt(3, 0), v)
    for side in (-1, 1):
        c.line(*pt(0.5, 0), *pt(-1, 3 * side), v)
        c.line(*pt(-2.5, 0), *pt(-3, 1.5 * side), v)


@register
class Planes(LiveWidget):
    id = "planes"
    title = "Planes overhead"
    category = "Widgets"
    description = ("Aircraft near you on a radar or a map, with the closest one's callsign, route, altitude and "
                   "speed. Uses your own ADS-B receiver (dump1090/readsb), adsb.lol or the OpenSky Network.")
    _rx = {"source": "receiver"}
    params = [
        P("source", "select", "receiver", "Data source", options=[
            ["receiver", "My ADS-B receiver (aircraft.json URL)"], ["adsblol", "adsb.lol (free, no key)"],
            ["opensky", "OpenSky Network (anonymous, 400 requests a day)"]]),
        P("url", "text", "http://localhost:8080/data/aircraft.json", "aircraft.json URL", show_if=_rx),
        P("location", "text", "", "Location: a place, or exact coordinates 'lat, lon' (blank = Settings default)"),
        P("radius_km", "number", 30, "Radius (km)", min=2, max=250),
        P("max_alt_ft", "number", 0, "Ignore planes above (ft, 0 = no limit)", min=0, max=60000, step=1000),
        P("view", "select", "radar", options=[["radar", "Radar and closest plane"], ["map", "Map with plane icons"]]),
        P("units", "select", "ft", options=[["ft", "Feet and knots"], ["m", "Metres and km/h"]]),
        P("routes", "checkbox", True, "Look up routes (adsbdb.com)"),
        P("poll_seconds", "number", 60, "Update every (s; OpenSky at least 15, receiver at least 1)", min=1, max=600),
    ]
    interval = 0.25

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        floor = {"opensky": OPENSKY_MIN, "adsblol": 10}.get(self.p["source"], 1)
        default = 5 if self.p["source"] == "receiver" and "poll_seconds" not in params else self.p["poll_seconds"]
        self.refresh = max(floor, float(default or 60))
        self.loc = None

    @classmethod
    def needs_setup(cls, store, params):
        if not (params.get("location") or store.settings.get("weather", {}).get("lat") is not None):
            return "a location (Settings, Location)"
        if params.get("source", "receiver") == "receiver" and params.get("url") == "":
            return "your receiver's aircraft.json URL"
        return None

    def _location(self):
        if self.loc is None:
            if self.p["location"]:
                hits = geocode(self.p["location"])
                if not hits:
                    raise ValueError(f"location {self.p['location']!r} not found")
                self.loc = hits[0]
            else:
                s = self.ctx.store.settings.get("weather", {})
                if s.get("lat") is None:
                    raise NeedsSetup("Set a location in Settings")
                self.loc = {"lat": float(s["lat"]), "lon": float(s["lon"]), "name": s.get("name", "")}
        return self.loc

    def _opensky(self, lat, lon, r):
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        if _opensky["day"] != today:
            _opensky.update(day=today, used=0)
        if time.time() < _opensky["until"]:
            raise ValueError("OpenSky asked us to wait")
        if _opensky["used"] >= OPENSKY_DAILY - 10:
            raise ValueError("OpenSky daily limit reached")
        dlat = math.degrees(r / EARTH_KM)
        dlon = dlat / max(0.1, math.cos(math.radians(lat)))
        q = urllib.parse.urlencode({"lamin": round(lat - dlat, 4), "lamax": round(lat + dlat, 4),
                                    "lomin": round(lon - dlon, 4), "lomax": round(lon + dlon, 4)})
        _opensky["used"] += 1
        try:
            return http_get(f"https://opensky-network.org/api/states/all?{q}", ttl=self.refresh - 1, timeout=15)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("X-Rate-Limit-Retry-After-Seconds") or 3600)
                _opensky["until"] = time.time() + wait
            raise

    def fetch(self):
        loc = self._location()
        lat, lon, r = float(loc["lat"]), float(loc["lon"]), float(self.p["radius_km"])
        now = time.time()
        src = self.p["source"]
        if src == "opensky":
            planes = parse_opensky(self._opensky(lat, lon, r), now)
        elif src == "adsblol":
            nm = max(1, round(r / 1.852))
            d = http_get(f"https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{nm}", ttl=self.refresh - 1)
            planes = parse_readsb(d, now)
        else:
            url = self.p["url"].strip()
            if not url:
                raise NeedsSetup("Set your receiver's aircraft.json URL")
            planes = parse_readsb(http_get(url, ttl=max(0.5, self.refresh - 0.5), timeout=5), now)
        top = float(self.p["max_alt_ft"] or 0)
        keep = []
        for p in planes:
            e, n = offset_km(lat, lon, p["lat"], p["lon"])
            p["km"] = math.hypot(e, n)
            if p["km"] <= r * 1.2 and not (top and p["alt_ft"] and p["alt_ft"] > top):
                keep.append(p)
        keep.sort(key=lambda p: p["km"])
        route = ""
        if keep and self.p["routes"] and keep[0]["callsign"]:
            try:
                route = lookup_route(keep[0]["callsign"], now)
            except Exception as e:  # the route is a nice-to-have
                log.info("route lookup: %s", e)
        return {"planes": keep, "route": route, "lat": lat, "lon": lon}

    def _positions(self, now):
        d = self.data
        out = []
        for p in d["planes"]:
            la, lo = project(p, now)
            e, n = offset_km(d["lat"], d["lon"], la, lo)
            out.append((math.hypot(e, n), e, n, p))
        out.sort(key=lambda x: x[0])
        r = float(self.p["radius_km"])
        return [x for x in out if x[0] <= r]

    def _info(self, p):
        metric = self.p["units"] == "m"
        alt = "" if p["alt_ft"] is None else (f"{round(p['alt_ft'] / FT_PER_M, -1):.0f}m" if metric
                                               else f"{round(p['alt_ft'], -2):.0f}ft")
        spd = f"{p['gs_kt'] * 1.852:.0f}km/h" if metric else f"{p['gs_kt']:.0f}kt"
        return p["callsign"] or p["id"].upper(), alt, spd

    def draw(self, c, now):
        pos = self._positions(now)
        if self.p["view"] == "map":
            return self._draw_map(c, now, pos)
        r = float(self.p["radius_km"])
        R = c.h // 2 - 1
        cx, cy = R, c.h // 2
        c.circle(cx, cy, R)
        for i in range(0, 360, 4):  # dotted inner ring at half range
            a = math.radians(i * 2)
            if i % 8 == 0:
                c.set(round(cx + R / 2 * math.cos(a)), round(cy + R / 2 * math.sin(a)))
        c.set(cx, cy)
        for k, (dist, e, n, p) in enumerate(pos):
            x, y = cx + e / r * R, cy - n / r * R
            if k == 0 and int(now * 2) % 2:  # the closest blinks
                continue
            c.rect(round(x), round(y), 2, 2, fill=True)
            if p.get("track") is not None:
                a = math.radians(p["track"])
                c.line(round(x), round(y), round(x + 4 * math.sin(a)), round(y - 4 * math.cos(a)))
        tx = 2 * R + 2
        tw = c.w - tx
        if not pos:
            c.text_center(14, "No", SMALL, x0=tx, w=tw)
            c.text_center(21, "planes", SMALL, x0=tx, w=tw)
            c.text_center(c.h - 6, f"{r:.0f}km", SMALL, x0=tx, w=tw)
            return
        dist, e, n, p = pos[0]
        cs, alt, spd = self._info(p)
        c.text(tx, 1, _fit(cs, NORMAL, tw), NORMAL)
        route = self.data.get("route") if pos[0][3] is self.data["planes"][0] else ""
        y = 10
        if route:
            c.text(tx, y, _fit(route, SMALL, tw), SMALL)
            y += 7
        for line in (alt, spd, f"{dist:.1f}km" if dist < 10 else f"{dist:.0f}km"):
            if line:
                c.text(tx, y, _fit(line, SMALL, tw), SMALL)
                y += 7
        if len(pos) > 1 and y <= c.h - 5:
            c.text(tx, c.h - 5, f"+{len(pos) - 1}", SMALL)

    def _draw_map(self, c, now, pos):
        r = float(self.p["radius_km"])
        mh = c.h - 8
        scale = min(c.w / 2, mh / 2) / r
        cx, cy = c.w / 2, mh / 2
        c.set(round(cx), round(cy))
        c.set(round(cx) - 1, round(cy)), c.set(round(cx) + 1, round(cy))
        c.set(round(cx), round(cy) - 1), c.set(round(cx), round(cy) + 1)
        for dist, e, n, p in pos:
            plane_icon(c, round(cx + e * scale), round(cy - n * scale), p.get("track"))
        c.line(0, mh, c.w - 1, mh)
        if not pos:
            c.text_center(mh + 2, f"No planes within {r:.0f}km", SMALL)
            return
        dist, e, n, p = pos[0]
        cs, alt, spd = self._info(p)
        route = self.data.get("route") if p is self.data["planes"][0] else ""
        parts = [cs, route, alt, spd]
        line = " ".join(x for x in parts if x)
        if SMALL.width(line) > c.w:
            line = " ".join(x for x in (cs, alt, spd) if x)
        c.text_center(mh + 2, _fit(line, SMALL, c.w), SMALL)

    def describe(self):
        n = len((self.data or {}).get("planes") or [])
        return f"Planes overhead ({n})" if self.data else "Planes overhead"


# ---- live weather sky ---------------------------------------------------------------------------------------

def _jd(t):
    return t / 86400 - 10957.5  # days since J2000.0


def _horizontal(ra, dec, lat, lon, d):
    gmst = (280.46061837 + 360.98564736629 * d) % 360
    ha = math.radians((gmst + lon - math.degrees(ra)) % 360)
    la = math.radians(lat)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(dec) * math.cos(la))
    return (math.degrees(az) + 180) % 360, math.degrees(el)


def sun_position(lat, lon, t):
    """(azimuth from north, elevation) in degrees; good to a fraction of a degree."""
    d = _jd(t)
    g = math.radians(357.529 + 0.98560028 * d)
    q = 280.459 + 0.98564736 * d
    L = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    e = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(e) * math.sin(L), math.cos(L))
    dec = math.asin(math.sin(e) * math.sin(L))
    return _horizontal(ra, dec, lat, lon, d)


def moon_position(lat, lon, t):
    """(azimuth, elevation, elongation from the sun in degrees: 0 new, 90 first quarter, 180 full), to ~1 degree."""
    d = _jd(t)
    L = 218.316 + 13.176396 * d
    M = math.radians(134.963 + 13.064993 * d)
    F = math.radians(93.272 + 13.229350 * d)
    lam = math.radians(L + 6.289 * math.sin(M))
    beta = math.radians(5.128 * math.sin(F))
    e = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.sin(lam) * math.cos(e) - math.tan(beta) * math.sin(e), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(e) + math.cos(beta) * math.sin(e) * math.sin(lam))
    az, el = _horizontal(ra, dec, lat, lon, d)
    g = math.radians(357.529 + 0.98560028 * d)
    sun_l = 280.459 + 0.98564736 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)
    return az, el, (math.degrees(lam) - sun_l) % 360


def draw_moon(c, cx, cy, r, elong, v=1):
    k = math.cos(math.radians(elong))
    waxing = elong < 180
    for dy in range(-r, r + 1):
        half = math.sqrt(max(0.0, (r + 0.4) ** 2 - dy * dy))
        for dx in range(-r, r + 1):
            if abs(dx) > half:
                continue
            side = dx if waxing else -dx
            if side >= half * k - 0.3:
                c.set(cx + dx, cy + dy, v)


FACING = {"south": 180, "north": 0, "east": 90, "west": 270}
TEX_W, TEX_H = 256, 48


def cloud_texture(seed=11):
    """A tileable fractal value-noise field (0..1), made once: the sky only shifts a window over it."""
    rnd = random.Random(seed)
    tex = [0.0] * (TEX_W * TEX_H)
    total = 0.0
    for cell, amp in ((32, 1.0), (16, 0.5), (8, 0.25), (4, 0.12)):
        gw, gh = TEX_W // cell, max(1, TEX_H // cell)
        grid = [rnd.random() for _ in range(gw * (gh + 1))]
        for y in range(TEX_H):
            gy, fy = divmod(y / cell, 1)
            gy = int(gy)
            fy = fy * fy * (3 - 2 * fy)
            for x in range(TEX_W):
                gx, fx = divmod(x / cell, 1)
                gx = int(gx)
                fx = fx * fx * (3 - 2 * fx)
                a = grid[gy * gw + gx % gw]
                b = grid[gy * gw + (gx + 1) % gw]
                cc = grid[min(gy + 1, gh) * gw + gx % gw]
                dd = grid[min(gy + 1, gh) * gw + (gx + 1) % gw]
                top = a + (b - a) * fx
                bot = cc + (dd - cc) * fx
                tex[y * TEX_W + x] += amp * (top + (bot - top) * fy)
        total += amp
    return [v / total for v in tex]


_TEX = []
BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
_THR = {}


def _bias(y, layer):
    """Clouds thin out towards the bottom of the layer, so the sky reads as a dome with a cloud base."""
    return 0.22 * (y / layer) ** 2


def _threshold(cover, layer):
    """The noise level above which `cover` of the cloud layer is cloud (cached per cover and layer height)."""
    key = (round(cover, 2), layer)
    if key not in _THR:
        if len(_THR) > 64:
            _THR.clear()
        tex = _texture()
        vals = sorted(tex[y * TEX_W + x] - _bias(y, layer) for y in range(layer) for x in range(0, TEX_W, 2))
        _THR[key] = vals[min(len(vals) - 1, int((1 - cover) * len(vals)))]
    return _THR[key]


def _texture():
    if not _TEX:
        _TEX.append(cloud_texture())
    return _TEX[0]


def wind_ms(speed, unit):
    return speed / 2.236936 if unit == "mph" else speed / 3.6


@register
class WeatherSky(LiveWidget):
    id = "weathersky"
    title = "Live weather sky"
    category = "Widgets"
    description = ("The sky outside as a slow animation: clouds drift with the real wind and cover as much sky as "
                   "they do now, rain or snow falls as hard as it does, and the sun or moon sits where it is.")
    params = [
        P("location", "text", "", "Location (blank = Settings default)"),
        P("units", "select", "", options=[["", "Settings default"], ["fahrenheit", "°F"], ["celsius", "°C"]]),
        P("facing", "select", "south", "The board looks", options=[[k, k.capitalize()] for k in FACING]),
        P("drift", "number", 100, "Cloud speed (% of real wind)", min=0, max=400, step=10),
        P("temperature", "checkbox", True, "Show the temperature"),
        P("stars", "checkbox", True, "Stars at night"),
    ]
    refresh = 600
    interval = 0.2

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.drops = []
        self.last = None
        self.ox = random.Random(5).random() * TEX_W
        self.flash_until = 0.0
        self.next_flash = 0.0
        rnd = random.Random(23)
        self.star_xy = [(rnd.randrange(self.w), rnd.randrange(self.h * 2 // 3)) for _ in range(28)]
        self.rnd = random.Random(31)
        self.bolt = (0, 0.0)  # (x, until)

    @classmethod
    def needs_setup(cls, store, params):
        if not (params.get("location") or store.settings.get("weather", {}).get("lat") is not None):
            return "a weather location (Settings, Location)"
        return None

    def _cache_path(self):
        key = hashlib.sha1(f"{self.p['location']}|{self.p['units']}".encode()).hexdigest()[:10]
        return os.path.join(cache_dir(self.ctx), f"weathersky-{key}.json")

    def fetch(self):
        try:
            d = fetch_weather(self.ctx.store, self.p["location"], self.p["units"])
        except NeedsSetup:
            raise
        except Exception:
            if self.data is None:  # after a restart while offline: the last weather we saw
                try:
                    with open(self._cache_path()) as f:
                        self.data = json.load(f)
                except (OSError, ValueError):
                    pass
            raise
        try:
            tmp = self._cache_path() + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, self._cache_path())
        except OSError:
            pass
        return d

    def conditions(self):
        cur = self.data.get("current") or {}
        unit = self.data.get("_wind", "km/h")
        interval = float(cur.get("interval") or 3600)
        return {
            "cover": float(cur.get("cloud_cover") if cur.get("cloud_cover") is not None else 50) / 100,
            "wind": wind_ms(float(cur.get("wind_speed_10m") or 0), unit),
            "wind_from": float(cur.get("wind_direction_10m") or 270),
            "rate": float(cur.get("precipitation") or 0) * 3600 / interval,  # mm in the interval -> mm/h
            "kind": weather_kind(cur.get("weather_code", 0)),
            "code": cur.get("weather_code", 0),
            "temp": cur.get("temperature_2m"),
        }

    def screen_x(self, az):
        """Azimuth -> x: the board shows 180 degrees of horizon centred on the way it faces."""
        rel = (az - FACING[self.p["facing"]] + 540) % 360 - 180
        return None if abs(rel) > 90 else (rel + 90) / 180 * (self.w - 1)

    def draw(self, c, now):
        w, h = c.w, c.h
        horizon = h - 1
        cond = self.conditions()
        lat, lon = float(self.data.get("_lat", 0)), float(self.data.get("_lon", 0))
        step = 0.0 if self.last is None else max(0.0, min(2.0, now - self.last))
        self.last = now

        # wind: 'from' -> where the clouds go, then its component along the board's left-to-right axis
        right_az = FACING[self.p["facing"]] + 90
        to = math.radians(cond["wind_from"] + 180)
        vx = cond["wind"] * math.cos(to - math.radians(right_az)) * float(self.p["drift"] or 0) / 100 * 0.3
        self.ox = (self.ox - vx * step) % TEX_W

        sun_az, sun_el = sun_position(lat, lon, now)
        night = sun_el < -6
        if night and self.p["stars"] and cond["cover"] < 0.9:
            for x, y in self.star_xy:
                c.set(x, y)
        body = None
        if cond["cover"] >= 0.9:
            pass  # overcast: no sun or moon to see
        elif sun_el > -1:
            x = self.screen_x(sun_az)
            if x is not None:
                y = horizon - 4 - max(0.0, sun_el) / 70 * (horizon - 8)
                body = (round(x), round(max(4, y)), 3)
                c.circle(body[0], body[1], 3, fill=True)
        else:
            m_az, m_el, elong = moon_position(lat, lon, now)
            x = self.screen_x(m_az)
            if m_el > 0 and x is not None:
                y = horizon - 4 - m_el / 70 * (horizon - 8)
                body = (round(x), round(max(4, y)), 3)
                c.circle(body[0], body[1], 3, v=0, fill=True)
                draw_moon(c, body[0], body[1], 3, elong)

        # clouds: a window over the noise field, cut at the level that covers `cover` of the cloud layer;
        # outlined puffs with a dithered inside, so rain and the sun still read through an overcast sky
        cover = cond["cover"]
        sky_h = horizon - 2
        layer = max(6, int(sky_h * (0.5 + 0.35 * cover)))
        base = 0
        if cover > 0.02:
            tex = _texture()
            thr = _threshold(cover, layer)
            ox = int(self.ox)
            mask = bytearray(w * layer)
            for y in range(layer):
                row = y * TEX_W
                bias = _bias(y, layer)
                for x in range(w):
                    if tex[row + (x + ox) % TEX_W] - bias > thr:
                        mask[y * w + x] = 1
            for y in range(layer):
                for x in range(w):
                    i = y * w + x
                    if not mask[i]:
                        continue
                    base = max(base, y)
                    edge = (x == 0 or not mask[i - 1] or x == w - 1 or not mask[i + 1] or
                            y == 0 or not mask[i - w] or y == layer - 1 or not mask[i + w])
                    v = tex[y * TEX_W + (x + ox) % TEX_W] - _bias(y, layer) - thr
                    # denser dots deeper inside a cloud (ordered dither)
                    on = edge or BAYER[(y & 3) * 4 + ((x + ox) & 3)] < 3 + v * 40
                    if body and (x - body[0]) ** 2 + (y - body[1]) ** 2 <= 10:
                        on = not on if edge else 0  # a cloud in front of the sun shows as its silhouette
                    c.set(x, y, 1 if on else 0)
        self.cloud_base = base

        # precipitation: drops spawn under the clouds, slanting with the wind
        rate = cond["rate"]
        kind = cond["kind"]
        snow = kind == "snow"
        target = 0 if rate <= 0.02 else int(min(140, max(3, rate * (10 if not snow else 25))))
        speed = 9.0 if snow else 28.0
        while len(self.drops) < target:
            self.drops.append([self.rnd.uniform(0, w), self.rnd.uniform(base - h, horizon)])
        del self.drops[target:]
        slant = max(-1.0, min(1.0, vx * 0.15))
        for dp in self.drops:
            dp[1] += speed * step
            dp[0] = (dp[0] + slant * speed * step + (math.sin(now * 2 + dp[1]) * 0.3 if snow else 0)) % w
            if dp[1] >= horizon:
                dp[0], dp[1] = self.rnd.uniform(0, w), self.rnd.uniform(base * 0.6, base + 2)
            x, y = int(dp[0]), int(dp[1])
            if 0 <= y < horizon:
                c.set(x, y)
                if not snow and y + 1 < horizon:
                    c.set(int(dp[0] + slant), y + 1)

        if kind == "fog":
            for y in range(horizon - 10, horizon):
                for x in range((y + int(now / 3)) % 3, w, 3 if y % 2 else 4):
                    c.set(x, y)
        if kind == "storm":
            if now >= self.next_flash:
                self.flash_until = now + 0.12
                self.next_flash = now + self.rnd.uniform(6, 20)
            if now < self.flash_until:
                if self.bolt[1] != self.flash_until:  # one bolt shape per flash
                    self.bolt = (self.rnd.randrange(10, w - 10), self.flash_until)
                x, y = self.bolt[0], 6
                bolt = random.Random(int(self.flash_until * 1000))
                while y < horizon:
                    nx = x + bolt.choice((-2, -1, 1, 2))
                    c.line(x, y, nx, y + 4)
                    x, y = nx, y + 4
        c.line(0, horizon, w - 1, horizon)

        if self.p["temperature"] and cond["temp"] is not None:
            s = f"{round(cond['temp'])}°"
            tw = SMALL.width(s)
            c.rect(w - tw - 2, horizon - 7, tw + 2, 7, v=0, fill=True)
            c.text(w - tw - 1, horizon - 6, s, SMALL)
        self.interval = 0.1 if self.drops or kind == "storm" else 0.25

    def describe(self):
        if not self.data:
            return "Live weather sky"
        name = self.data.get("_name") or ""
        text = WEATHER_TEXT.get((self.data.get("current") or {}).get("weather_code"), "")
        return "Weather sky" + (f": {name}" if name else "") + (f", {text.lower()}" if text else "")

