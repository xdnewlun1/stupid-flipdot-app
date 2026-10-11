"""Sunrise and sunset, worked out locally with NOAA's solar equations (no network, no dependencies).

Good to about a minute between the polar circles. sun_times() gives UTC timestamps; local_minutes() gives
minutes after local midnight for the schedule."""

import datetime as dt
import math

ZENITH = 90.833  # the sun's centre this far from overhead at sunrise/sunset: refraction plus the sun's radius


def _jd(d):
    """Julian day at 0:00 UTC of date d."""
    return d.toordinal() + 1721424.5


def _solar(jd):
    """(equation of time in minutes, declination in degrees) at Julian day jd."""
    t = (jd - 2451545.0) / 36525
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    decl = math.degrees(math.asin(math.sin(eps) * math.sin(app_long)))
    y = math.tan(eps / 2) ** 2
    l0r = math.radians(l0)
    eqt = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return eqt, decl


def _event(d, lat, lon, rising):
    """Minutes after 0:00 UTC on date d of sunrise (rising) or sunset; None when the sun doesn't cross the
    horizon that day (polar day or night)."""
    jd0 = _jd(d)
    minutes = 720 - 4 * lon  # first guess: solar noon
    for _ in range(3):  # refine with the sun's position at the event itself
        eqt, decl = _solar(jd0 + minutes / 1440)
        latr, declr = math.radians(lat), math.radians(decl)
        cos_ha = (math.cos(math.radians(ZENITH)) / (math.cos(latr) * math.cos(declr)) - math.tan(latr) * math.tan(declr))
        if not -1 <= cos_ha <= 1:
            return None
        ha = math.degrees(math.acos(cos_ha))
        minutes = 720 - 4 * (lon + (ha if rising else -ha)) - eqt
    return minutes


def sun_times(d, lat, lon):
    """(sunrise, sunset) as UTC timestamps for the local calendar date d at lat/lon (east positive);
    either is None on a day the sun doesn't rise or set."""
    base = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()
    out = []
    for rising in (True, False):
        m = _event(d, lat, lon, rising)
        out.append(None if m is None else base + m * 60)
    return tuple(out)


def local_minutes(d, lat, lon):
    """{"sunrise", "sunset"}: minutes after local midnight of date d in this machine's time zone (None when
    the sun doesn't rise or set)."""
    midnight = dt.datetime(d.year, d.month, d.day).timestamp()
    rise, set_ = sun_times(d, lat, lon)
    return {"sunrise": None if rise is None else (rise - midnight) / 60,
            "sunset": None if set_ is None else (set_ - midnight) / 60}
