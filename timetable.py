"""The schedule: rules that say what the board shows when, worked out for any moment, plus a preview.

A schedule is {"version": 2, "enabled", "default": playlist name, "rules": [rule, ...]}. A rule:
    id, enabled, name
    when       "weekly" (on `days`), "dates" (between `from` and `to` every year, "MM-DD", on `days`;
               "YYYY-MM-DD" dates are that year only) or "once" (on `date`, "YYYY-MM-DD")
    days       weekdays it may start on, 0 = Monday
    start, end "HH:MM", or "sunrise" / "sunset" with an offset in minutes: "sunset+30", "sunrise-15".
               An end at or before the start runs past midnight; the part after midnight belongs to the day it
               started. "24:00" is the end of the day.
    target     "playlist": a playlist name (or OFF, or "" for nothing), unless "spec" is set: an app spec
               (an app with params, a layer set, slideshow, video, picture or drawing), shown until the rule ends.
The first enabled rule in the list that matches wins; nothing matching means the default playlist.
Older schedules (rules with only days/start/end/playlist) read the same and are upgraded by normalize()."""

import datetime as dt
import re
import secrets

import sun

OFF = "__off__"
WHEN = ("weekly", "dates", "once")
TIME_RE = re.compile(r"^(?:(\d{1,2}):(\d{2})|(sunrise|sunset)([+-]\d{1,3})?)$")
NO_SUN = {"sunrise": 6 * 60 + 30, "sunset": 18 * 60 + 30}  # stand-ins when no location is set (flagged in the UI)
MD_RE = re.compile(r"^(\d{4}-)?(\d{2})-(\d{2})$")


# ---- format ------------------------------------------------------------------------------

def normalize(sched):
    """A clean version-2 copy of a schedule in either format (old rules keep what they did)."""
    sched = dict(sched or {})
    out = {"version": 2, "enabled": bool(sched.get("enabled", True)), "default": sched.get("default") or "",
           "rules": []}
    seen = set()
    for r in sched.get("rules") or []:
        if not isinstance(r, dict):
            continue
        rule = clean_rule(r)
        while rule["id"] in seen:
            rule["id"] = new_id()
        seen.add(rule["id"])
        out["rules"].append(rule)
    return out


def new_id():
    return "r" + secrets.token_hex(3)


def clean_rule(r):
    when = r.get("when") if r.get("when") in WHEN else "weekly"
    days = sorted({int(d) for d in (r.get("days") if isinstance(r.get("days"), list) else range(7))
                   if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    rule = {"id": str(r.get("id") or new_id())[:16], "enabled": bool(r.get("enabled", True)),
            "name": str(r.get("name") or "")[:60], "when": when, "days": days,
            "start": _clean_time(r.get("start"), "00:00"), "end": _clean_time(r.get("end"), "24:00"),
            "playlist": str(r.get("playlist") or "")}
    if when == "dates":
        rule["from"] = _clean_md(r.get("from"), "01-01")
        rule["to"] = _clean_md(r.get("to"), "12-31")
    if when == "once":
        rule["date"] = _clean_date(r.get("date"))
    spec = r.get("spec")
    if isinstance(spec, dict) and spec.get("app"):
        rule["spec"] = {"app": str(spec["app"]), "params": dict(spec.get("params") or {})}
    return rule


def _clean_time(v, default):
    v = str(v or "").strip().replace(" ", "")
    m = TIME_RE.match(v)
    if not m:
        return default
    if m.group(1) is not None:
        h, mi = int(m.group(1)), int(m.group(2))
        if (h, mi) == (24, 0):
            return "24:00"
        return f"{min(h, 23):02d}:{min(mi, 59):02d}"
    off = int(m.group(4) or 0)
    off = max(-720, min(720, off))
    return m.group(3) + (f"{off:+d}" if off else "")


def _clean_md(v, default):
    m = MD_RE.match(str(v or "").strip())
    if not m:
        return default
    mo, d = max(1, min(12, int(m.group(2)))), max(1, min(31, int(m.group(3))))
    return f"{m.group(1) or ''}{mo:02d}-{d:02d}"


def _clean_date(v):
    try:
        return dt.date.fromisoformat(str(v)).isoformat()
    except ValueError:
        return dt.date.today().isoformat()


def target_key(rule):
    """A short key for what a rule shows (to tell targets apart)."""
    if rule.get("spec"):
        p = rule["spec"].get("params") or {}
        ref = next((str(p[k]) for k in ("composition", "gallery", "video", "scene", "drawing") if p.get(k)), "")
        return f"app:{rule['spec']['app']}:{ref}"
    return "pl:" + (rule.get("playlist") or "")


# ---- times -------------------------------------------------------------------------------------

class Clock:
    """Turns rule times into minutes after local midnight for a date, with sun times from `location`
    ({"lat", "lon"} or None); sun times are worked out once per date."""

    def __init__(self, location=None):
        loc = location or {}
        self.lat, self.lon = loc.get("lat"), loc.get("lon")
        self.located = self.lat is not None and self.lon is not None
        self._sun = {}

    def sun(self, d):
        if d not in self._sun:
            s = sun.local_minutes(d, float(self.lat), float(self.lon)) if self.located else {}
            # polar day/night: no crossing; keep the stand-in so a rule still has a time
            self._sun[d] = {k: (s.get(k) if s.get(k) is not None else NO_SUN[k]) for k in NO_SUN}
        return self._sun[d]

    def minutes(self, spec, d):
        """Minutes after midnight of date d for a time spec ("HH:MM", "24:00", "sunset+30")."""
        m = TIME_RE.match(spec or "")
        if not m:
            return 0
        if m.group(1) is not None:
            return int(m.group(1)) * 60 + int(m.group(2))
        return round(self.sun(d)[m.group(3)]) + int(m.group(4) or 0)  # whole minutes, like the clock times


def day_ok(rule, d):
    """May the rule start on date d?"""
    when = rule.get("when", "weekly")
    if when == "once":
        return rule.get("date") == d.isoformat()
    if d.weekday() not in rule.get("days", range(7)):
        return False
    if when == "dates":
        return in_range(d, rule.get("from") or "01-01", rule.get("to") or "12-31")
    return True


def in_range(d, a, b):
    """Is date d between a and b (inclusive)? "MM-DD" repeats every year and may wrap past New Year
    (12-01 to 01-01); "YYYY-MM-DD" is that day only."""
    if len(a) == 10 or len(b) == 10:
        lo = a if len(a) == 10 else f"{d.year}-{a}"
        hi = b if len(b) == 10 else f"{d.year}-{b}"
        return lo <= d.isoformat() <= hi
    md = d.strftime("%m-%d")
    return a <= md <= b if a <= b else (md >= a or md <= b)


def window(rule, d, clock):
    """(start, end) as minutes after midnight of the start day d; end may run into the next day."""
    s = clock.minutes(rule.get("start", "00:00"), d)
    end_spec = rule.get("end", "24:00")
    e = clock.minutes(end_spec, d)
    if e <= s:
        e = clock.minutes(end_spec, d + dt.timedelta(days=1)) + 1440  # past midnight: the next day's sun
    return s, e


def active(rule, t, clock):
    """Is the rule on at local datetime t?"""
    if not rule.get("enabled", True):
        return False
    today = t.date()
    now_min = t.hour * 60 + t.minute + t.second / 60
    for back in (0, 1):
        d = today - dt.timedelta(days=back)
        if not day_ok(rule, d):
            continue
        s, e = window(rule, d, clock)
        if s <= now_min + back * 1440 < e:
            return True
    return False


# ---- what wins ---------------------------------------------------------------------------------

def resolve(sched, t, clock):
    """What the schedule shows at local datetime t: {"source": "rule" | "default", "rule": index or None,
    "id", "playlist", "spec", "also": [indexes of other rules that match but lose]}."""
    sched = sched or {}
    hits = [i for i, r in enumerate(sched.get("rules") or []) if active(r, t, clock)]
    if hits:
        r = sched["rules"][hits[0]]
        return {"source": "rule", "rule": hits[0], "id": r.get("id"), "playlist": r.get("playlist") or "",
                "spec": r.get("spec") or None, "also": hits[1:]}
    return {"source": "default", "rule": None, "id": None, "playlist": sched.get("default") or "", "spec": None,
            "also": []}


def key(res):
    """Two resolutions with the same key show the same thing."""
    if res is None:
        return None
    if res.get("spec"):
        return ("spec", res.get("id"), repr(sorted((res["spec"].get("params") or {}).items())), res["spec"]["app"])
    return ("playlist", res.get("playlist") or "")


def boundaries(sched, start, end, clock):
    """Local datetimes in [start, end) where some rule may start or stop (plus each midnight)."""
    out = {start}
    d = start.date() - dt.timedelta(days=1)
    while d <= end.date():
        midnight = dt.datetime.combine(d, dt.time())
        out.add(midnight)
        for r in sched.get("rules") or []:
            if not r.get("enabled", True) or not day_ok(r, d):
                continue
            for m in window(r, d, clock):
                out.add(midnight + dt.timedelta(minutes=round(m)))
        d += dt.timedelta(days=1)
    return sorted(x for x in out if start <= x < end)


def preview(sched, start, hours, clock, label=None):
    """What shows from local datetime `start` for `hours`: [{"start", "end" (local ISO), "rule", "id",
    "playlist", "spec", "label"}], one entry per stretch (adjacent same targets merged)."""
    end = start + dt.timedelta(hours=hours)
    out = []
    for i, b in enumerate(points := boundaries(sched, start, end, clock)):
        nxt = points[i + 1] if i + 1 < len(points) else end
        res = resolve(sched, b, clock)
        if out and key(out[-1]["res"]) == key(res) and out[-1]["res"]["rule"] == res["rule"]:
            out[-1]["end"] = nxt
            continue
        out.append({"start": b, "end": nxt, "res": res})
    return [{"start": s["start"].isoformat(timespec="minutes"), "end": s["end"].isoformat(timespec="minutes"),
             "rule": s["res"]["rule"], "id": s["res"]["id"], "playlist": s["res"]["playlist"],
             "spec": s["res"]["spec"], "label": label(s["res"]) if label else None} for s in out]


def next_change(sched, t, clock, within_hours=48):
    """The local datetime after t when what shows next changes, or None within that window."""
    now = resolve(sched, t, clock)
    end = t + dt.timedelta(hours=within_hours)
    for b in boundaries(sched, t + dt.timedelta(seconds=1), end, clock):
        res = resolve(sched, b, clock)
        if key(res) != key(now) or res["rule"] != now["rule"]:
            return b
    return None
