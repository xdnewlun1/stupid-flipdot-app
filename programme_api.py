"""HTTP side of playlists and the schedule beyond plain saving: playlist playback options, "Add to playlist"
from any page, the schedule's current format, and its preview. server.py calls install(), which registers
the routes below with its @route. All of them are owner-only (none is listed in access.ALLOW)."""

import datetime as dt

import apps
import playback
import timetable
from apps import slidefx

S = None  # the server module (its store, runner, route, HttpError, ha_sync)

NAME_MAX = 60
PREVIEW_HOURS = (24, 168)


# ---- playlists -------------------------------------------------------------------------

def playlist_extras():
    """What the Playlists page needs besides the playlists: condition and effect choices, React defaults."""
    return {"conditions": [{"id": k, "label": v} for k, v in playback.CONDITIONS.items()],
            "effects": slidefx.describe_settings(),
            "react_defaults": slidefx.clean_react_map(None), "motion": slidefx.MOTION_DEFAULTS}


def clean_playlists(data):
    """Check every item's app, and tidy playback options and conditions (in place)."""
    if not isinstance(data, dict):
        raise ValueError("playlists must be an object")
    for name, pl in data.items():
        for item in pl.get("items", []):
            _check_app(item.get("app"), f"playlist {name}")
        playback.clean_playlist(pl)
    return data


def _check_app(app_id, where):
    if app_id not in apps.REGISTRY and app_id not in apps.base.ALIASES:
        raise ValueError(f"{where}: unknown app {app_id!r}")


def post_playlist_add(req):
    """Owner-only. {"playlist": name, "item": {app, params}, "duration": s, "create": bool}: append one item
    (creating the playlist when asked). Returns the playlist's name, the new item's index and all playlists."""
    body = req.json()
    name = " ".join(str(body.get("playlist") or "").split())[:NAME_MAX]
    item = body.get("item") or {}
    if not name:
        raise ValueError("Choose a playlist, or give the new one a name.")
    if not isinstance(item, dict) or not item.get("app"):
        raise ValueError("Nothing to add.")
    _check_app(item["app"], "item")
    try:
        duration = max(1, min(86400, int(float(body.get("duration") or item.get("duration") or 30))))
    except (TypeError, ValueError):
        raise ValueError("The duration must be a number of seconds.") from None
    pls = dict(S.store.playlists)
    if name not in pls:
        if not body.get("create"):
            raise S.HttpError(404, f"no playlist {name!r}")
        pls[name] = {"transition": "dissolve", "items": []}
    elif body.get("create"):
        raise ValueError(f'There\'s already a playlist called "{name}".')
    pl = {**pls[name], "items": list(pls[name].get("items") or [])}
    new = {"app": item["app"], "params": dict(item.get("params") or {}), "duration": duration}
    pl["items"].append(new)
    pls[name] = playback.clean_playlist(pl)
    S.store.save_playlists(pls)
    S.ha_sync()
    return {"playlist": name, "index": len(pl["items"]) - 1, "playlists": S.store.playlists}


# ---- schedule ---------------------------------------------------------------------------------

def clean_schedule(sched):
    """The schedule to save, in the current format, with every app target checked."""
    out = timetable.normalize(sched)
    for i, r in enumerate(out["rules"]):
        if r.get("spec"):
            _check_app(r["spec"]["app"], f"schedule rule {i + 1}")
    return out


def _location():
    loc = S.store.settings.get("weather") or {}
    if loc.get("lat") is None or loc.get("lon") is None:
        return None
    return {"lat": loc["lat"], "lon": loc["lon"], "name": loc.get("name") or loc.get("location") or ""}


def schedule_payload():
    return {"schedule": S.runner.schedule_rules(), "off": timetable.OFF, "location": _location()}


def _label(res):
    from runner import target_label
    return target_label(S.store, res)


def get_schedule_preview(req):
    """Owner-only. What the schedule shows when: ?hours=24|168 from the start of today (local time). Also what
    wins right now and why, the quiet hours and each day's sunrise and sunset."""
    try:
        hours = int(req.query.get("hours", ["24"])[0])
    except ValueError:
        hours = 24
    hours = hours if hours in PREVIEW_HOURS else 24
    r = S.runner
    sched = r.schedule_rules()
    clock = r.schedule_clock()
    now = dt.datetime.fromtimestamp(r.sched_now())
    start = dt.datetime.combine(now.date(), dt.time())
    segs = timetable.preview(sched, start, hours, clock, label=_label)
    res = timetable.resolve(sched, now, clock)
    nxt = timetable.next_change(sched, now, clock)
    days = []
    for k in range(max(1, hours // 24)):
        d = start.date() + dt.timedelta(days=k)
        s = clock.sun(d)
        days.append({"date": d.isoformat(), "sunrise": round(s["sunrise"]), "sunset": round(s["sunset"])})
    q = S.store.settings.get("quiet_hours") or {}
    return {
        "now": now.isoformat(timespec="seconds"), "start": start.isoformat(timespec="minutes"), "hours": hours,
        "segments": segs, "enabled": sched["enabled"], "following": r.mode == "schedule",
        "current": {**res, "label": _label(res), "until": nxt.isoformat(timespec="minutes") if nxt else None},
        "quiet": {"enabled": bool(q.get("enabled")), "start": q.get("start") or "22:00", "end": q.get("end") or "07:00"},
        "days": days, "location": _location(),
    }


def install(server):
    global S
    S = server
    route = server.route
    route("POST", "/api/playlists/add")(post_playlist_add)          # owner-only
    route("GET", "/api/schedule/preview")(get_schedule_preview)     # owner-only
