"""Live-data boards (apps/pack_live.py): the GTFS-realtime decoder against a feed made by a tiny protobuf
encoder below, the GTFS static index from a small zip, merging realtime into the schedule, JSON departures,
plane sources (recorded readsb/adsb.lol and OpenSky shapes) and the weather sky. No network: http_get and
downloads are replaced with fixtures."""

import csv
import datetime as dt
import io
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401
import gtfs_rt  # noqa: E402
import gtfs_static  # noqa: E402
from apps import pack_live  # noqa: E402
from apps.base import create  # noqa: E402
from canvas import Canvas  # noqa: E402


# ---- a tiny protobuf encoder (test side only) ---------------------------------------------------------------

def varint(v):
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def f_int(num, v):
    return varint(num << 3) + varint(v)


def f_bytes(num, b):
    if isinstance(b, str):
        b = b.encode()
    return varint(num << 3 | 2) + varint(len(b)) + b


def f_fixed32(num, v):
    return varint(num << 3 | 5) + v.to_bytes(4, "little")


def event(time_=None, delay=None):
    return (f_int(1, delay) if delay is not None else b"") + (f_int(2, time_) if time_ is not None else b"")


def stu(seq=None, stop_id="", arr=None, dep=None, rel=None):
    b = f_int(1, seq) if seq is not None else b""
    if arr is not None:
        b += f_bytes(2, arr)
    if dep is not None:
        b += f_bytes(3, dep)
    if stop_id:
        b += f_bytes(4, stop_id)
    if rel is not None:
        b += f_int(5, rel)
    return b


def trip_update(trip_id, route_id="", start_date="", rel=None, stops=(), delay=None):
    td = f_bytes(1, trip_id) + (f_bytes(3, start_date) if start_date else b"") + (f_bytes(5, route_id) if route_id else b"")
    if rel is not None:
        td += f_int(4, rel)
    b = f_bytes(1, td) + b"".join(f_bytes(2, s) for s in stops)
    b += f_bytes(3, f_bytes(1, "veh1") + f_bytes(2, "Bus 12"))
    if delay is not None:
        b += f_int(5, delay)
    b += f_fixed32(99, 1234)  # an unknown extension field: must be skipped
    return b


def feed(entities, ts=1_760_000_000):
    header = f_bytes(1, "2.0") + f_int(3, ts)
    return f_bytes(1, header) + b"".join(f_bytes(2, f_bytes(1, f"e{i}") + f_bytes(3, tu)) for i, tu in enumerate(entities))


# ---- a small GTFS static feed -------------------------------------------------------------------------------

def _csv(rows):
    s = io.StringIO()
    w = csv.writer(s)
    for r in rows:
        w.writerow(r)
    return s.getvalue()


def make_gtfs(path, tz="UTC"):
    stop_times = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "stop_headsign"]]
    # T1..T6: route 12 every 10 min from 08:00 (A -> B(platform of station S) -> C); T9: route 7 at 25:05
    for i in range(6):
        h, m = 8, i * 10
        stop_times += [[f"T{i + 1}", f"{h:02}:{m:02}:00", f"{h:02}:{m:02}:00", "A", 1, ""],
                       [f"T{i + 1}", f"{h:02}:{m + 3:02}:00", f"{h:02}:{m + 3:02}:00", "B1", 2, ""],
                       [f"T{i + 1}", f"{h:02}:{m + 8:02}:00", f"{h:02}:{m + 8:02}:00", "C", 3, ""]]
    stop_times += [["T9", "25:00:00", "25:00:00", "A", 1, ""], ["T9", "25:05:00", "25:05:00", "B2", 2, "Night"],
                   ["T9", "25:20:00", "25:20:00", "D", 3, ""]]
    files = {
        "agency.txt": [["agency_id", "agency_name", "agency_url", "agency_timezone"], ["X", "Test", "http://x", tz]],
        "stops.txt": [["stop_id", "stop_name", "location_type", "parent_station"],
                      ["A", "Alpha", "", ""], ["S", "Central Station", "1", ""], ["B1", "Central Station", "", "S"],
                      ["B2", "Central Station", "", "S"], ["C", "Charlie Road", "", ""], ["D", "Depot", "", ""]],
        "routes.txt": [["route_id", "route_short_name", "route_long_name", "route_type"],
                       ["R12", "12", "Twelve", "3"], ["R7", "", "Night Owl", "3"], ["R99", "99", "Unused", "3"]],
        "trips.txt": [["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"]] +
                     [["R12", "WK", f"T{i + 1}", "", "0"] for i in range(6)] + [["R7", "WK", "T9", "", "0"]],
        "calendar.txt": [["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                          "start_date", "end_date"], ["WK", "1", "1", "1", "1", "1", "1", "1", "20260101", "20271231"]],
        "calendar_dates.txt": [["service_id", "date", "exception_type"], ["WK", "20261225", "2"]],
        "stop_times.txt": stop_times,
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in files.items():
            zf.writestr(name, ("﻿" if name == "stops.txt" else "") + _csv(rows))


def utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


NOW = utc(2026, 10, 12, 8, 1)  # a Monday, 08:01 UTC


def _index(tmp_path):
    z = tmp_path / "feed.zip"
    make_gtfs(z)
    return gtfs_static.build_index(str(z), ["S"])


# ---- tests ---------------------------------------------------------------------------------------------------

def test_protobuf_decoder_reads_trip_updates():
    data = feed([trip_update("T1", "R12", "20261012", stops=[stu(2, "B1", dep=event(delay=-30))], delay=60),
                 trip_update("T2", rel=gtfs_rt.CANCELED)])
    f = gtfs_rt.parse_feed(data)
    assert f["timestamp"] == 1_760_000_000
    a, b = f["updates"]
    assert a["trip"]["trip_id"] == "T1" and a["trip"]["route_id"] == "R12" and a["trip"]["start_date"] == "20261012"
    assert a["stops"][0] == {"seq": 2, "stop_id": "B1", "arr": None, "dep": {"delay": -30}, "rel": 0}
    assert a["delay"] == 60 and a["vehicle"] == "Bus 12"
    assert b["trip"]["rel"] == gtfs_rt.CANCELED
    assert gtfs_rt.parse_feed(data, keep=lambda tu: tu["trip"]["trip_id"] == "T2")["updates"][0]["trip"]["trip_id"] == "T2"


def test_protobuf_decoder_rejects_garbage():
    import pytest
    with pytest.raises(gtfs_rt.DecodeError):
        gtfs_rt.parse_feed(b"\x0a\xff\xff\xff")


def test_static_index_keeps_only_the_chosen_stops(tmp_path):
    idx = _index(tmp_path)
    assert set(idx["stops"]) == {"S", "B1", "B2"}  # the station's platforms come along
    assert set(idx["trips"]) == {f"T{i}" for i in range(1, 7)} | {"T9"}
    assert set(idx["routes"]) == {"R12", "R7"}
    assert idx["dest"]["T1"] == "Charlie Road" and idx["dest"]["T9"] == "Depot"
    assert len(idx["times"]) == 7 and idx["tz"] == "UTC"


def test_schedule_handles_after_midnight_and_holidays(tmp_path):
    idx = _index(tmp_path)
    deps = gtfs_static.scheduled(idx, NOW)
    assert [d["trip_id"] for d in deps] == ["T1", "T2", "T3", "T4", "T5", "T6"]
    assert deps[0]["t"] == utc(2026, 10, 12, 8, 3)
    late = gtfs_static.scheduled(idx, utc(2026, 10, 13, 0, 50))  # T9 at 25:05 belongs to the day before
    assert [d["trip_id"] for d in late] == ["T9"] and late[0]["headsign"] == "Night"
    assert gtfs_static.scheduled(idx, utc(2026, 12, 25, 8, 1)) == []  # removed by calendar_dates


def test_merge_realtime_into_schedule(tmp_path):
    idx = _index(tmp_path)
    rt = gtfs_rt.parse_feed(feed([
        trip_update("T1", stops=[stu(2, "B1", dep=event(time_=int(utc(2026, 10, 12, 8, 6))))]),  # absolute time
        trip_update("T2", stops=[stu(1, "A", dep=event(delay=120))]),   # delay at an earlier stop carries on
        trip_update("T3", rel=gtfs_rt.CANCELED),
        trip_update("T4", stops=[stu(2, "B1", rel=gtfs_rt.STU_SKIPPED)]),
        trip_update("X1", "R7", stops=[stu(None, "B2", arr=event(time_=int(utc(2026, 10, 12, 8, 20))))]),  # added
        trip_update("Z", "R99", stops=[stu(1, "Q", dep=event(time_=int(NOW + 60)))]),  # another stop: ignored
    ]))
    deps = pack_live.merge_departures(idx, rt, ["S"], NOW)
    got = [(d["trip_id"], round((d["t"] - NOW) / 60), d["route"], d["live"]) for d in deps]
    assert got == [("T1", 5, "12", True), ("T2", 14, "12", True), ("X1", 19, "Night Owl", True),
                   ("T5", 42, "12", False), ("T6", 52, "12", False)]
    assert deps[0]["dest"] == "Charlie Road"


def test_realtime_only_without_schedule():
    rt = gtfs_rt.parse_feed(feed([trip_update("A1", "5", stops=[stu(3, "P", dep=event(time_=int(NOW + 400)))])]))
    deps = pack_live.merge_departures(None, rt, ["P"], NOW)
    assert [(d["route"], round(d["t"] - NOW)) for d in deps] == [("5", 400)]


def test_load_index_caches_and_survives_offline(tmp_path):
    calls = []

    def fake_download(url, path, headers=None):
        calls.append(url)
        make_gtfs(path)
        return True

    def offline(url, path, headers=None):
        raise OSError("no network")

    d = str(tmp_path / "cache")
    a = gtfs_static.load_index(d, "http://example.invalid/g.zip", ["S"], download=fake_download)
    b = gtfs_static.load_index(d, "http://example.invalid/g.zip", ["S"], download=fake_download)
    assert len(calls) == 1 and a["times"] == b["times"]
    week_later = time.time() + 8 * 86400
    c = gtfs_static.load_index(d, "http://example.invalid/g.zip", ["S"], now=week_later, download=offline)
    assert c["times"] == a["times"]  # stale, but still working
    other = gtfs_static.load_index(d, "http://example.invalid/g.zip", ["A"], download=fake_download)
    assert set(other["stops"]) == {"A"} and len(calls) == 1  # a new stop re-indexes the cached zip


class _Store:
    def __init__(self, tmp, weather=None):
        self.dir = str(tmp)
        self.settings = {"weather": weather or {"lat": 51.5, "lon": -0.1, "name": "Testville", "units": "celsius"}}


class _Ctx:
    def __init__(self, store, w=84, h=42):
        self.width, self.height, self.store = w, h, store


def _render(app, now):
    app._next_at = float("inf")  # never fetch from the network in tests
    c = Canvas(app.w, app.h)
    app.render(c, now)
    return c


def _text(c):
    return "\n".join("".join("#" if v else "." for v in row) for row in c.rows())


def test_departures_app_renders_rows_and_hides_unreachable(tmp_path, monkeypatch):
    idx = _index(tmp_path)
    monkeypatch.setattr(gtfs_static, "load_index", lambda *a, **k: idx)
    rt = feed([trip_update("T1", stops=[stu(2, "B1", dep=event(time_=int(NOW + 20)))])])
    monkeypatch.setattr(pack_live, "http_get", lambda url, **k: rt)
    app = create(_Ctx(_Store(tmp_path)), {"app": "departures", "params": {
        "stops": "S", "static_url": "http://example.invalid/g.zip", "rt_url": "http://example.invalid/rt",
        "char_transition": "none"}})
    app.data = app.fetch(NOW)
    assert app.data["stop"] == "Central Station" and app.data["live"]
    vis = app.visible(NOW)
    assert [d["trip_id"] for d in vis][:2] == ["T1", "T2"]
    assert pack_live.minutes_text(vis[0]["t"] - NOW, vis[0]["t"]) == "due"
    c = _render(app, NOW)
    assert sum(c.px) > 200
    # the "due" row is inverted (leave now): its band is mostly lit
    assert sum(c.px[8 * 84:9 * 84]) > 60
    app.p["walk"] = 5
    assert [d["trip_id"] for d in app.visible(NOW)][0] == "T2"
    app.p["routes"] = "7"
    assert app.visible(NOW) == []
    _render(app, NOW)  # "No departures" without errors


def test_departures_falls_back_to_schedule_when_realtime_fails(tmp_path, monkeypatch):
    idx = _index(tmp_path)
    monkeypatch.setattr(gtfs_static, "load_index", lambda *a, **k: idx)

    def down(url, **k):
        raise OSError("offline")
    monkeypatch.setattr(pack_live, "http_get", down)
    app = create(_Ctx(_Store(tmp_path)), {"app": "departures", "params": {
        "stops": "S", "static_url": "http://x.invalid/g.zip", "rt_url": "http://x.invalid/rt"}})
    data = app.fetch(NOW)
    assert not data["live"] and app.rt_error and data["deps"]


def test_json_departures():
    payload = {"result": {"list": [
        {"line": {"name": "U1"}, "towards": "Leopoldau", "when": "2026-10-12T08:09:00Z"},
        {"line": {"name": "13A"}, "towards": "Hauptbahnhof", "when": "2026-10-12T08:04:00+00:00"},
        {"line": {"name": "x"}, "towards": "no time"}]}}
    deps = pack_live.json_departures(payload, "result.list", "line.name", "towards", "when", "iso", NOW)
    assert [(d["route"], round((d["t"] - NOW) / 60)) for d in deps] == [("13A", 3), ("U1", 8)]
    mins = pack_live.json_departures([{"r": "5", "m": 4}], "", "r", "", "m", "minutes", NOW)
    assert mins[0]["t"] == NOW + 240
    assert pack_live.parse_time(1_760_000_000_000, "epoch", NOW) == 1_760_000_000


def test_minutes_text():
    assert pack_live.minutes_text(5, 0) == "now"
    assert pack_live.minutes_text(40, 0) == "due"
    assert pack_live.minutes_text(61, 0) == "1"
    assert pack_live.minutes_text(59 * 60 + 59, 0) == "59"
    t = dt.datetime(2026, 10, 12, 14, 5).timestamp()
    assert pack_live.minutes_text(4000, t) == "14:05"


def test_needs_setup():
    store = _Store("/nonexistent")
    assert pack_live.Departures.needs_setup(store, {}) == "your stop IDs"
    assert pack_live.Departures.needs_setup(store, {"stops": "1"}).startswith("a GTFS")
    assert pack_live.Departures.needs_setup(store, {"stops": "1", "rt_url": "http://x"}) is None
    assert pack_live.Departures.needs_setup(store, {"source": "json"}) == "a departures JSON URL"


# ---- planes -------------------------------------------------------------------------------------------------

READSB = {"now": 1000.0, "aircraft": [  # trimmed from a real dump1090/readsb aircraft.json
    {"hex": "4070e3", "flight": "EXS73WL ", "alt_baro": 27150, "gs": 297.1, "track": 271.74,
     "lat": 51.52, "lon": -0.15, "seen_pos": 0.2, "t": "B738"},
    {"hex": "4080e1", "alt_baro": "ground", "gs": 0.0, "lat": 51.47, "lon": -0.48},
    {"hex": "abc123", "flight": "BAW117  ", "alt_baro": 3500, "gs": 180, "track": 90, "lat": 51.6, "lon": 0.1,
     "seen_pos": 1.0},
    {"hex": "nopos", "flight": "X"}]}

OPENSKY = {"time": 1000, "states": [
    ["3c6444", "DLH9LF  ", "Germany", 999, 1000, -0.12, 51.51, 1000.0, False, 100.0, 45.0, 0, None, 1050.0, "1000",
     False, 0],
    ["3c6445", "GND1    ", "Germany", 999, 1000, -0.11, 51.5, None, True, 0, 0, 0, None, None, None, False, 0]]}

ADSBDB = {"response": {"flightroute": {"callsign": "EXS73WL", "origin": {"iata_code": "LGW", "icao_code": "EGKK"},
                                       "destination": {"iata_code": "PMI", "icao_code": "LEPA"}}}}


def test_parse_plane_sources():
    p = pack_live.parse_readsb(READSB, 1000.0)
    assert [x["callsign"] for x in p] == ["EXS73WL", "BAW117"]
    assert p[0]["alt_ft"] == 27150 and p[0]["t_pos"] == 999.8
    assert pack_live.parse_readsb({"ac": READSB["aircraft"]}, 1000.0)[0]["id"] == "4070e3"  # adsb.lol shape
    o = pack_live.parse_opensky(OPENSKY, 1000.0)
    assert len(o) == 1 and o[0]["callsign"] == "DLH9LF" and round(o[0]["alt_ft"]) == 3281
    assert round(o[0]["gs_kt"]) == 194


def test_projection_moves_planes_along_their_track():
    p = {"lat": 51.5, "lon": 0.0, "gs_kt": 194.4, "track": 90, "t_pos": 0.0}
    la, lo = pack_live.project(p, 10.0)  # 100 m/s east for 10 s = 1 km
    e, n = pack_live.offset_km(51.5, 0.0, la, lo)
    assert abs(e - 1.0) < 0.01 and abs(n) < 0.01


def test_route_lookup_is_cached():
    pack_live._routes.clear()
    calls = []

    def getter(url, ttl):
        calls.append(url)
        return ADSBDB if "EXS73WL" in url else {"response": "unknown callsign"}
    assert pack_live.lookup_route("EXS73WL", 0, getter) == "LGW-PMI"
    assert pack_live.lookup_route("exs73wl", 10, getter) == "LGW-PMI"
    assert pack_live.lookup_route("NOPE1", 10, getter) == ""
    assert len(calls) == 2


def test_planes_app_radar_and_map(tmp_path, monkeypatch):
    pack_live._routes.clear()
    monkeypatch.setattr(pack_live, "http_get", lambda url, **k: ADSBDB if "adsbdb" in url else READSB)
    for view in ("radar", "map"):
        app = create(_Ctx(_Store(tmp_path)), {"app": "planes", "params": {"view": view, "radius_km": 30}})
        app.data = app.fetch()
        assert [p["callsign"] for p in app.data["planes"]] == ["EXS73WL", "BAW117"]
        assert app.data["route"] == "LGW-PMI"
        c = _render(app, 1000.0)
        assert sum(c.px) > 80, view
    app.data = {**app.data, "planes": []}
    _render(app, 1000.0)


def test_opensky_daily_budget(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pack_live, "http_get", lambda url, **k: calls.append(url) or OPENSKY)
    app = create(_Ctx(_Store(tmp_path)), {"app": "planes", "params": {"source": "opensky", "poll_seconds": 1,
                                                                      "routes": False}})
    assert app.refresh >= pack_live.OPENSKY_MIN
    pack_live._opensky.update(day=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), used=0, until=0)
    assert app.fetch()["planes"][0]["callsign"] == "DLH9LF"
    assert "lamin=" in calls[0]
    pack_live._opensky["used"] = pack_live.OPENSKY_DAILY
    import pytest
    with pytest.raises(ValueError):
        app.fetch()
    assert len(calls) == 1
    pack_live._opensky.update(used=0)


def test_exact_coordinates_centre_the_radar_without_a_lookup(tmp_path, monkeypatch):
    from apps import widgets
    monkeypatch.setattr(widgets, "http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("looked up")))
    for text, want in [("41.1234, -73.4567", (41.1234, -73.4567)), ("41.1234,-73.4567", (41.1234, -73.4567)),
                       ("41.1234 -73.4567", (41.1234, -73.4567)), ("41.1234° N, 73.4567° W", (41.1234, -73.4567)),
                       ("33.9 S 151.2 E", (-33.9, 151.2)), (" -12.5 ; 130.8 ", (-12.5, 130.8))]:
        assert widgets.parse_coords(text) == want, text
    for text in ["Paris", "91, 10", "10, 181", "41.1234", "", None, "12 Oak Street"]:
        assert widgets.parse_coords(text) is None, text
    assert widgets.geocode("41.1234, -73.4567") == [{"name": "41.1234, -73.4567", "lat": 41.1234, "lon": -73.4567}]
    calls = []
    monkeypatch.setattr(pack_live, "http_get", lambda url, **k: calls.append(url) or {"ac": []})
    app = create(_Ctx(_Store(tmp_path)), {"app": "planes", "params": {"source": "adsblol", "location": "41.1234, -73.4567"}})
    app.fetch()
    assert app.loc == {"name": "41.1234, -73.4567", "lat": 41.1234, "lon": -73.4567}
    assert calls and "/v2/point/41.1234/-73.4567/" in calls[0]  # adsb.lol asked for exactly that point


def test_backoff_grows_after_errors(tmp_path):
    app = create(_Ctx(_Store(tmp_path)), {"app": "planes", "params": {"source": "adsblol"}})
    app._fails = 3
    assert app.wait_after(False) == min(pack_live.MAX_BACKOFF, app.refresh * 8)
    assert app.wait_after(True) == app.refresh


# ---- weather sky --------------------------------------------------------------------------------------------

def weather(cover=60, wind=20.0, wdir=270, precip=0.0, code=3, temp=12.4):
    return {"current": {"time": "2026-10-12T12:00", "interval": 900, "temperature_2m": temp, "weather_code": code,
                        "is_day": 1, "cloud_cover": cover, "wind_speed_10m": wind, "wind_direction_10m": wdir,
                        "precipitation": precip},
            "_name": "Testville", "_wind": "km/h", "_lat": 51.5, "_lon": -0.1}


def test_sun_and_moon_positions():
    az, el = pack_live.sun_position(51.5, 0.0, utc(2026, 6, 21, 12, 0))
    assert 170 < az < 190 and 60 < el < 63  # midsummer noon in London
    az, el = pack_live.sun_position(51.5, 0.0, utc(2026, 3, 20, 6, 5))
    assert 85 < az < 95 and -2 < el < 2      # equinox sunrise, due east
    _, _, elong = pack_live.moon_position(51.5, 0.0, utc(2026, 10, 26, 4, 12))  # full moon 2026-10-26
    assert 170 < elong < 190


def test_weathersky_clouds_drift_with_the_wind(tmp_path):
    app = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
    app.data = weather(cover=60, wind=36.0, wdir=270)  # 10 m/s from the west
    t0 = utc(2026, 10, 12, 12, 0)
    _render(app, t0)
    x0 = app.ox
    _render(app, t0 + 1.0)
    # facing south, west wind carries clouds east = to the left: the texture window moves right
    assert app.ox != x0
    clear = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
    clear.data = weather(cover=0, code=0)
    overcast = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
    overcast.data = weather(cover=100)
    a, b = _render(clear, t0), _render(overcast, t0)
    assert sum(b.px) > sum(a.px) + 1000


def test_weathersky_rain_density_follows_rate(tmp_path):
    t0 = utc(2026, 10, 12, 12, 0)
    counts = []
    for rate in (0.0, 0.25, 2.5):  # mm per 15 min
        app = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
        app.data = weather(cover=90, precip=rate, code=63)
        _render(app, t0)
        _render(app, t0 + 0.2)
        counts.append(len(app.drops))
    assert counts[0] == 0 < counts[1] < counts[2]


def test_weathersky_uses_cached_weather_when_offline(tmp_path, monkeypatch):
    app = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
    monkeypatch.setattr(pack_live, "fetch_weather", lambda *a: weather())
    app.fetch()
    again = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})

    def offline(*a):
        raise OSError("offline")
    monkeypatch.setattr(pack_live, "fetch_weather", offline)
    again._do_fetch()
    assert again.data and again.data["_name"] == "Testville" and again.error


def test_render_cost(tmp_path):
    app = create(_Ctx(_Store(tmp_path)), {"app": "weathersky", "params": {}})
    app.data = weather(cover=70, precip=1.0, code=63)
    pack_live._texture()
    t0 = utc(2026, 10, 12, 12, 0)
    start = time.perf_counter()
    for i in range(20):
        _render(app, t0 + i * 0.1)
    assert (time.perf_counter() - start) / 20 < 0.02
