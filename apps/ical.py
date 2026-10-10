"""Calendar: upcoming events from iCal (.ics) feeds, with a countdown to the next one.

Pure stdlib .ics reader: line unfolding, all-day events, TZID / UTC times, and simple RRULEs
(DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL, BYDAY; EXDATE; moved instances).
"""

import datetime as dt
import logging

from fonts import NORMAL, SMALL

from .base import P, Widget, http_get, register

log = logging.getLogger("apps")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
# Outlook/Exchange feeds use Windows zone names
WINDOWS_ZONES = {
    "Pacific Standard Time": "America/Los_Angeles", "Mountain Standard Time": "America/Denver",
    "Central Standard Time": "America/Chicago", "Eastern Standard Time": "America/New_York",
    "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris", "Central Europe Standard Time": "Europe/Budapest",
    "AUS Eastern Standard Time": "Australia/Sydney", "Tokyo Standard Time": "Asia/Tokyo",
    "UTC": "UTC",
}
MAX_STEPS = 20000  # recurrence expansion limit per event


def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def _zone(tzid):
    if not tzid or ZoneInfo is None:
        return None
    tzid = tzid.strip('"')
    # e.g. "/mozilla.org/20050126_1/Europe/Berlin" -> "Europe/Berlin"
    tail = "/".join(tzid.split("/")[-2:]) if tzid.count("/") >= 2 else None
    for name in (tzid, WINDOWS_ZONES.get(tzid), tail):
        if name:
            try:
                return ZoneInfo(name)
            except Exception:
                continue
    return None


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_line(line):
    """'DTSTART;TZID=Europe/Berlin:20240101T090000' -> ('DTSTART', {'TZID': 'Europe/Berlin'}, value)"""
    head, value, quoted = line, "", False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v for k, _, v in (p.partition("=") for p in params)}, value


def _parse_dt(value, params):
    """A date (all-day) or an aware datetime."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    d = dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return d.replace(tzinfo=dt.timezone.utc)
    return d.replace(tzinfo=_zone(params.get("TZID")) or _local_tz())


def _parse_duration(s):
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-").lstrip("P")
    total, num, in_time = 0, "", False
    units = {"W": 604800, "D": 86400, "H": 3600, "M": 60, "S": 1}
    for ch in s:
        if ch == "T":
            in_time = True
        elif ch.isdigit():
            num += ch
        elif ch in units and num:
            total += int(num) * units[ch]
            num = ""
    return dt.timedelta(seconds=sign * total)


def parse_ics(text):
    """Events as dicts: summary, start, end (date or aware datetime), all_day, rrule, exdates, uid, rid."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    events, ev, depth = [], None, 0
    for line in lines:
        name, params, value = _parse_line(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and ev is None:
                ev, depth = {"exdates": set(), "summary": ""}, 0
            elif ev is not None:
                depth += 1  # e.g. VALARM inside the event
            continue
        if name == "END":
            if ev is not None and depth:
                depth -= 1
            elif ev is not None and value.upper() == "VEVENT":
                if "start" in ev and ev.get("status") != "CANCELLED":
                    events.append(_finish(ev))
                ev = None
            continue
        if ev is None or depth:
            continue
        try:
            if name == "SUMMARY":
                ev["summary"] = _unescape(value)
            elif name == "DTSTART":
                ev["start"] = _parse_dt(value, params)
            elif name == "DTEND":
                ev["end"] = _parse_dt(value, params)
            elif name == "DURATION":
                ev["duration"] = _parse_duration(value)
            elif name == "RRULE":
                ev["rrule"] = {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";")) if k}
            elif name == "EXDATE":
                for v in value.split(","):
                    ev["exdates"].add(_key(_parse_dt(v, params)))
            elif name == "RECURRENCE-ID":
                ev["rid"] = _key(_parse_dt(value, params))
            elif name == "UID":
                ev["uid"] = value
            elif name == "STATUS":
                ev["status"] = value.upper()
        except (ValueError, IndexError) as e:
            log.debug("calendar: skipping %s: %s", name, e)
    return events


def _finish(ev):
    start = ev["start"]
    ev["all_day"] = not isinstance(start, dt.datetime)
    if "end" in ev and type(ev["end"]) is type(start):
        ev["length"] = ev["end"] - start
    elif "duration" in ev:
        ev["length"] = ev["duration"]
    else:
        ev["length"] = dt.timedelta(days=1) if ev["all_day"] else dt.timedelta(0)
    return ev


def _key(d):
    """Comparable identity of an occurrence start (UTC instant, or the date for all-day)."""
    if isinstance(d, dt.datetime):
        return d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return d


def _wall(start, day, tz):
    """The occurrence on `day` at the same wall-clock time as `start` (DST-correct)."""
    if isinstance(start, dt.datetime):
        return dt.datetime.combine(day, start.time().replace(tzinfo=None), tzinfo=tz)
    return day


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    try:
        return d.replace(year=y, month=m)
    except ValueError:
        return None  # e.g. the 31st in a short month: no occurrence that month


def occurrences(ev, until):
    """Occurrence starts of an event up to `until` (aware datetime)."""
    start = ev["start"]
    rule = ev.get("rrule")
    if not rule:
        yield start
        return
    tz = start.tzinfo if isinstance(start, dt.datetime) else None
    freq = rule.get("FREQ", "").upper()
    interval = max(1, int(rule.get("INTERVAL") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT") else None
    end = None
    if rule.get("UNTIL"):
        u = _parse_dt(rule["UNTIL"], {})
        end = u if isinstance(u, dt.datetime) else dt.datetime.combine(u, dt.time(23, 59, 59), tzinfo=tz or _local_tz())
    byday = [DAYS.index(d[-2:].upper()) for d in rule.get("BYDAY", "").split(",") if d[-2:].upper() in DAYS]
    first_day = start.date() if isinstance(start, dt.datetime) else start
    stop = until if end is None else min(until, end)

    def as_dt(o):
        return o if isinstance(o, dt.datetime) else dt.datetime.combine(o, dt.time(), tzinfo=_local_tz())

    n = 0
    if freq == "WEEKLY":
        days = sorted(byday) or [first_day.weekday()]
        week = first_day - dt.timedelta(days=first_day.weekday())
        steps = 0
        while steps < MAX_STEPS:
            for wd in days:
                day = week + dt.timedelta(days=wd)
                if day < first_day:
                    continue
                occ = _wall(start, day, tz)
                if as_dt(occ) > stop or (count is not None and n >= count):
                    return
                n += 1
                yield occ
            week += dt.timedelta(weeks=interval)
            steps += 1
        return
    step = 0
    while step < MAX_STEPS:
        if freq == "DAILY":
            day = first_day + dt.timedelta(days=step * interval)
        elif freq == "MONTHLY":
            day = _add_months(first_day, step * interval)
        elif freq == "YEARLY":
            day = _add_months(first_day, step * interval * 12)
        else:
            yield start  # unsupported rule: just the first occurrence
            return
        step += 1
        if day is None or (byday and freq == "DAILY" and day.weekday() not in byday):
            continue
        occ = _wall(start, day, tz)
        if as_dt(occ) > stop or (count is not None and n >= count):
            return
        n += 1
        yield occ


def upcoming(events, now, days=7, limit=20):
    """[(start, end, summary, all_day)] of events not over yet that start within `days`, soonest first.
    Timed events come back as local aware datetimes, all-day events as dates."""
    now = now.astimezone()
    horizon = now + dt.timedelta(days=days)
    moved = {(e.get("uid"), e["rid"]) for e in events if e.get("rid") is not None}
    local = lambda d: dt.datetime.combine(d, dt.time()).astimezone()
    out = []
    for ev in events:
        for occ in occurrences(ev, horizon):
            if _key(occ) in ev["exdates"] or (ev.get("rid") is None and (ev.get("uid"), _key(occ)) in moved):
                continue
            stop = occ + ev["length"]
            begin, finish = (local(occ), local(stop)) if ev["all_day"] else (occ.astimezone(), stop.astimezone())
            if begin > horizon or finish < now or (finish == now and begin < now):
                continue
            out.append((occ if ev["all_day"] else begin, stop if ev["all_day"] else finish,
                        ev["summary"] or "(no title)", ev["all_day"], begin))
    out.sort(key=lambda e: (e[4], not e[3]))  # all-day events first on their day
    return [e[:4] for e in out[:limit]]


def countdown(seconds):
    if seconds < 60:
        return "now"
    m = int(seconds // 60)
    if m < 60:
        return f"in {m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"in {h}h {m}m" if m else f"in {h}h"
    d, h = divmod(h, 24)
    return f"in {d}d {h}h" if h else f"in {d}d"


@register
class Calendar(Widget):
    id = "calendar"
    title = "Calendar"
    category = "Widgets"
    description = "Upcoming events from iCal (.ics) links (Google, iCloud, Outlook...) with a countdown to the next one."
    params = [
        P("urls", "textarea", "", "iCal links (.ics), one per line"),
        P("days", "number", 7, "Look ahead (days)", min=1, max=60),
        P("hour24", "checkbox", False, "24-hour times"),
        P("countdown", "checkbox", True, "Countdown to the next event"),
        P("refresh_minutes", "number", 15, "Check for changes every (minutes)", min=1, max=1440),
    ]
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(60, float(self.p["refresh_minutes"]) * 60)
        self.events = []
        self._items = (0, None, [])  # (computed at, events object, upcoming list) - expanding is not free

    def fetch(self):
        urls = [u.strip() for u in str(self.p["urls"]).splitlines() if u.strip()]
        if not urls:
            raise ValueError("Add an iCal link in the app settings")
        events, errors = [], []
        for url in urls:
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            try:
                events += parse_ics(http_get(url, ttl=self.refresh - 5, raw=True, timeout=20).decode("utf-8", "replace"))
            except Exception as e:
                errors.append(str(e))
        if errors and not events:
            raise ValueError(errors[0])
        self.events = events
        return True

    def _time(self, t):
        if self.p["hour24"]:
            return t.strftime("%H:%M")
        return t.strftime("%I:%M").lstrip("0") + ("a" if t.hour < 12 else "p")

    def _day_label(self, d, today):
        delta = (d - today).days
        if delta <= 0:
            return "Today"
        if delta == 1:
            return "Tmrw"
        return d.strftime("%a")

    def draw(self, c, now):
        t = dt.datetime.fromtimestamp(now).astimezone()
        if now - self._items[0] > 20 or self._items[1] is not self.events:
            self._items = (now, self.events, upcoming(self.events, t, float(self.p["days"]), limit=8))
        items = [e for e in self._items[2] if (e[1] if not e[3] else dt.datetime.combine(e[1], dt.time()).astimezone()) > t]
        if not items:
            c.text_center(10, "No events", NORMAL)
            c.text_center(22, f"NEXT {int(self.p['days'])} DAYS", SMALL)
            self.interval = 1.0
            return
        today = t.date()
        start, end, title, all_day = items[0]
        day = start if all_day else start.date()
        # top: the next event, scrolling if it's too wide
        tw = NORMAL.width(title)
        if tw <= c.w:
            c.text_center(0, title, NORMAL)
            self.interval = 1.0
        else:
            off = int((now - self.started) * 15) % (tw + 30)
            c.text(-off, 0, title, NORMAL)
            c.text(-off + tw + 30, 0, title, NORMAL)
            self.interval = 0.07
        if all_day:
            when = self._day_label(day, today) + (" all day" if day > today else "")
            left = when if day > today else "Today, all day"
        else:
            left = ("" if day == today else self._day_label(day, today) + " ") + self._time(start)
            if start <= t:
                left = "now, until " + self._time(end)
        right = ""
        if self.p["countdown"] and not all_day and start > t:
            right = countdown((start - t).total_seconds())
        if right and NORMAL.width(left) + NORMAL.width(right) + 4 <= c.w:
            c.text(0, 9, left, NORMAL)
            c.text(c.w - NORMAL.width(right), 9, right, NORMAL)
        else:
            c.text_center(9, right or left, NORMAL)
        c.line(0, 18, c.w - 1, 18)
        # the rest: one line each, small font, with a day label when the day changes
        y = 21
        last_day = day
        for start, end, title, all_day in items[1:]:
            if y + 5 > c.h:
                break
            d = start if all_day else start.date()
            prefix = (self._day_label(d, today).upper() + " ") if d != last_day or d != today else ""
            last_day = d
            line = prefix + ("ALL DAY" if all_day else self._time(start).upper()) + " " + title
            c.text(0, y, line, SMALL)
            y += 7

    def describe(self):
        return self.title
