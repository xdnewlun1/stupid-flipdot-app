"""GTFS static feeds, cut down to the stops a departures board shows.

A city's GTFS zip can be 100 MB with millions of stop_times rows, too much to hold on a Pi. The zip is
downloaded to disk (refreshed weekly, If-Modified-Since), then streamed once to keep only what the chosen
stops need: their scheduled departures, the trips and routes serving them, the destination of each trip and
the calendars of those services. That small index is saved as JSON next to the zip.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from email.utils import formatdate

log = logging.getLogger("gtfs")

REFRESH = 7 * 86400  # re-download the static feed weekly
INDEX_VERSION = 1
_locks = {}
_locks_guard = threading.Lock()


def _lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _rows(zf, name):
    """Stream a CSV member as dicts (tolerates a BOM and a missing file)."""
    try:
        f = zf.open(name)
    except KeyError:
        return
    with f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def gtfs_seconds(s):
    """'25:10:00' -> seconds after the service day's start (may be past 24 h)."""
    try:
        h, m, sec = (s or "").strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except ValueError:
        return None


def build_index(zip_path, stop_ids):
    """Index the parts of a GTFS zip that departures from stop_ids need. A parent station's id includes its platforms."""
    wanted = {s.strip() for s in stop_ids if s.strip()}
    with zipfile.ZipFile(zip_path) as zf:
        names, parents = {}, {}
        for r in _rows(zf, "stops.txt"):
            sid = r.get("stop_id", "")
            names[sid] = r.get("stop_name", "")
            if r.get("parent_station"):
                parents[sid] = r["parent_station"]
        stops = set(wanted) | {s for s, p in parents.items() if p in wanted}

        times, last = [], {}
        cur, cur_best, cur_hit = None, None, False

        def close():
            if cur is not None and cur_hit:
                prev = last.get(cur)
                if prev is None or cur_best[0] > prev[0]:
                    last[cur] = cur_best

        for r in _rows(zf, "stop_times.txt"):
            trip = r.get("trip_id", "")
            try:
                seq = int(r.get("stop_sequence") or 0)
            except ValueError:
                seq = 0
            if trip != cur:  # stop_times is normally grouped by trip: track each trip's last stop as we go
                close()
                cur, cur_best, cur_hit = trip, (seq, r.get("stop_id", "")), trip in last
            elif seq > cur_best[0]:
                cur_best = (seq, r.get("stop_id", ""))
            if r.get("stop_id") in stops:
                secs = gtfs_seconds(r.get("departure_time") or r.get("arrival_time"))
                if secs is not None and r.get("pickup_type", "0") != "1":
                    times.append([trip, r["stop_id"], seq, secs, r.get("stop_headsign", "")])
                    cur_hit = True
        close()

        trip_ids = {t[0] for t in times}
        trips = {}
        for r in _rows(zf, "trips.txt"):
            if r.get("trip_id") in trip_ids:
                trips[r["trip_id"]] = [r.get("route_id", ""), r.get("service_id", ""), r.get("trip_headsign", ""),
                                       r.get("direction_id", "")]
        route_ids = {t[0] for t in trips.values()}
        routes = {}
        for r in _rows(zf, "routes.txt"):
            if r.get("route_id") in route_ids:
                routes[r["route_id"]] = [r.get("route_short_name", ""), r.get("route_long_name", "")]
        services = {t[1] for t in trips.values()}
        calendar, dates = {}, {}
        for r in _rows(zf, "calendar.txt"):
            if r.get("service_id") in services:
                days = "".join(r.get(d, "0") or "0" for d in
                               ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
                calendar[r["service_id"]] = [days, r.get("start_date", ""), r.get("end_date", "")]
        for r in _rows(zf, "calendar_dates.txt"):
            if r.get("service_id") in services:
                dates.setdefault(r["service_id"], {})[r.get("date", "")] = int(r.get("exception_type") or 0)
        tz = next((r.get("agency_timezone", "") for r in _rows(zf, "agency.txt")), "")

    dest = {trip: names.get(stop, "") for trip, (_, stop) in last.items() if trip in trip_ids}
    times.sort(key=lambda t: t[3])
    return {"version": INDEX_VERSION, "built": time.time(), "tz": tz,
            "stops": {s: names.get(s, "") for s in stops}, "stop_ids": sorted(wanted),
            "routes": routes, "trips": trips, "dest": dest, "times": times,
            "calendar": calendar, "dates": dates}


def _download(url, path, headers=None, timeout=60):
    """Fetch url to path (atomically). Returns False when the server says it hasn't changed."""
    h = {"User-Agent": "flipdot-board/1.0 (departures board)", **(headers or {})}
    if os.path.exists(path):
        h["If-Modified-Since"] = formatdate(os.path.getmtime(path), usegmt=True)
    req = urllib.request.Request(url, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            os.utime(path)
            return False
        raise
    tmp = path + ".part"
    with r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)
    return True


def load_index(cache_dir, url, stop_ids, headers=None, now=None, download=_download):
    """The index for these stops, from disk when it is under a week old. Rebuilt after a download; if the
    download fails an older index is still used (the board keeps working offline)."""
    now = time.time() if now is None else now
    os.makedirs(cache_dir, exist_ok=True)
    ukey = hashlib.sha1(url.encode()).hexdigest()[:12]
    skey = hashlib.sha1(",".join(sorted(s.strip() for s in stop_ids)).encode()).hexdigest()[:8]
    zip_path = os.path.join(cache_dir, f"gtfs-{ukey}.zip")
    idx_path = os.path.join(cache_dir, f"gtfs-{ukey}-{skey}.json")
    with _lock(zip_path):
        idx = None
        try:
            with open(idx_path) as f:
                idx = json.load(f)
            if idx.get("version") != INDEX_VERSION:
                idx = None
        except (OSError, ValueError):
            idx = None
        zip_fresh = os.path.exists(zip_path) and now - os.path.getmtime(zip_path) < REFRESH
        if idx and zip_fresh and idx.get("built", 0) >= os.path.getmtime(zip_path):
            return idx
        changed = False
        if not zip_fresh:
            try:
                changed = download(url, zip_path, headers)
            except Exception as e:
                if idx or os.path.exists(zip_path):
                    log.warning("GTFS download failed, using the cached copy: %s", e)
                else:
                    raise
        if idx and not changed and idx.get("built", 0) >= os.path.getmtime(zip_path):
            return idx
        if not os.path.exists(zip_path):
            return idx
        fresh = build_index(zip_path, stop_ids)
        tmp = idx_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fresh, f, separators=(",", ":"))
        os.replace(tmp, idx_path)
        return fresh


# ---- schedule --------------------------------------------------------------------------------------------

def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def service_active(idx, service_id, day):
    ymd = day.strftime("%Y%m%d")
    exc = idx["dates"].get(service_id, {}).get(ymd)
    if exc == 1:
        return True
    if exc == 2:
        return False
    cal = idx["calendar"].get(service_id)
    if not cal:
        return False
    days, start, end = cal
    return (not start or start <= ymd) and (not end or ymd <= end) and days[day.weekday()] == "1"


def day_start(day, tz):
    """GTFS times count from 'noon minus 12 h' of the service day (midnight except across DST changes)."""
    return dt.datetime(day.year, day.month, day.day, 12, tzinfo=tz).timestamp() - 43200  # tz None = local time


def scheduled(idx, now, horizon=3 * 3600, behind=120):
    """Scheduled departures in [now - behind, now + horizon]:
    [{"trip_id", "stop_id", "seq", "t", "route_id", "headsign", "direction"}]."""
    tz = _zone(idx.get("tz"))
    today = dt.datetime.fromtimestamp(now, tz).date()
    out = []
    for offset in (-1, 0, 1):
        day = today + dt.timedelta(days=offset)
        base = day_start(day, tz)
        active = {}
        for trip_id, stop_id, seq, secs, stop_headsign in idx["times"]:
            t = base + secs
            if t < now - behind or t > now + horizon:
                continue
            trip = idx["trips"].get(trip_id)
            if not trip:
                continue
            sid = trip[1]
            if sid not in active:
                active[sid] = service_active(idx, sid, day)
            if active[sid]:
                out.append({"trip_id": trip_id, "stop_id": stop_id, "seq": seq, "t": t, "route_id": trip[0],
                            "headsign": stop_headsign or trip[2] or idx["dest"].get(trip_id, ""),
                            "direction": trip[3], "date": day.strftime("%Y%m%d")})
    out.sort(key=lambda d: d["t"])
    return out
