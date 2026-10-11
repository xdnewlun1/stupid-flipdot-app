"""Live-data widgets: weather, crypto, stocks, sports scores and a generic JSON widget."""

import datetime as dt
import json
import math
import re
import urllib.parse

from fonts import NORMAL, SMALL

from .base import NeedsSetup, P, Widget, http_get, register


# ---- weather -----------------------------------------------------------------

def weather_kind(code, is_day=True):
    if code in (0, 1):
        return "sun" if is_day else "moon"
    if code == 2:
        return "partly" if is_day else "cloud"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    if 51 <= code <= 82:
        return "rain"
    return "cloud"


WEATHER_TEXT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 56: "Icy drizzle", 57: "Icy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Icy rain", 67: "Icy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
    82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
    96: "Thunderstorm", 99: "Thunderstorm",
}


def draw_icon(c, x, y, kind, s=20):
    """Draw a weather icon in an s x s box."""
    cx, cy = x + s / 2, y + s / 2

    def cloud(ox, oy, k=1.0):
        c.circle(ox + s * 0.35 * k, oy + s * 0.45 * k, s * 0.17 * k, fill=True)
        c.circle(ox + s * 0.58 * k, oy + s * 0.35 * k, s * 0.22 * k, fill=True)
        c.rect(ox + s * 0.18 * k, oy + s * 0.45 * k, s * 0.65 * k, s * 0.18 * k, fill=True)
        c.circle(ox + s * 0.78 * k, oy + s * 0.5 * k, s * 0.13 * k, fill=True)

    if kind == "sun":
        c.circle(cx, cy, s * 0.22, fill=True)
        for i in range(8):
            a = math.radians(i * 45)
            c.line(cx + s * 0.32 * math.cos(a), cy + s * 0.32 * math.sin(a),
                   cx + s * 0.46 * math.cos(a), cy + s * 0.46 * math.sin(a))
    elif kind == "moon":
        c.circle(cx, cy, s * 0.36, fill=True)
        c.circle(cx + s * 0.18, cy - s * 0.12, s * 0.3, v=0, fill=True)
    elif kind == "partly":
        c.circle(x + s * 0.65, y + s * 0.3, s * 0.18, fill=True)
        for i in range(8):
            a = math.radians(i * 45)
            c.line(x + s * 0.65 + s * 0.24 * math.cos(a), y + s * 0.3 + s * 0.24 * math.sin(a),
                   x + s * 0.65 + s * 0.32 * math.cos(a), y + s * 0.3 + s * 0.32 * math.sin(a))
        c.circle(x + s * 0.32, y + s * 0.62, s * 0.2, v=0, fill=True)
        c.rect(x, y + s * 0.6, s * 0.8, s * 0.3, v=0, fill=True)
        cloud(x - s * 0.05, y + s * 0.25, 0.85)
    elif kind == "fog":
        for i in range(4):
            c.line(x + (i % 2) * 2, y + s * 0.25 + i * s * 0.17, x + s - 1 - ((i + 1) % 2) * 2, y + s * 0.25 + i * s * 0.17)
    else:
        cloud(x, y - s * 0.1)
        if kind == "rain":
            for i in range(4):
                bx = x + s * 0.2 + i * s * 0.18
                c.line(bx, y + s * 0.65, bx - 2, y + s * 0.95)
        elif kind == "snow":
            for i in range(4):
                bx, by = x + s * 0.2 + i * s * 0.18, y + s * (0.72 if i % 2 else 0.88)
                c.set(bx, by)
                c.set(bx - 1, by), c.set(bx + 1, by), c.set(bx, by - 1), c.set(bx, by + 1)
        elif kind == "storm":
            pts = [(0.55, 0.6), (0.4, 0.8), (0.55, 0.8), (0.42, 1.0)]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                c.line(x + ax * s, y + ay * s, x + bx * s, y + by * s)


def geocode(name):
    q = urllib.parse.quote(name)
    r = http_get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=5", ttl=86400)
    return [
        {"name": ", ".join(filter(None, [g.get("name"), g.get("admin1"), g.get("country_code")])),
         "lat": g["latitude"], "lon": g["longitude"]}
        for g in r.get("results") or []
    ]


def weather_location(store, location=""):
    """{"name", "lat", "lon"} from a place name, or the Settings location."""
    settings = store.settings.get("weather", {})
    if location:
        hits = geocode(location)
        if not hits:
            raise ValueError(f"location {location!r} not found")
        return hits[0]
    if settings.get("lat") is not None:
        return settings
    raise NeedsSetup("Set a location in Settings")


def fetch_weather(store, location="", units=""):
    """Open-Meteo current conditions + 4-day forecast. Shared by the weather apps: one URL, so one cached request."""
    loc = weather_location(store, location)
    units = units or store.settings.get("weather", {}).get("units", "fahrenheit")
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": 4,
        "current": "temperature_2m,weather_code,is_day,relative_humidity_2m,wind_speed_10m,"
                   "wind_direction_10m,cloud_cover,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "temperature_unit": units, "wind_speed_unit": "mph" if units == "fahrenheit" else "kmh",
    })
    d = http_get(f"https://api.open-meteo.com/v1/forecast?{q}", ttl=300)
    d["_name"] = loc.get("name", "")
    d["_wind"] = "mph" if units == "fahrenheit" else "km/h"
    d["_lat"], d["_lon"] = loc["lat"], loc["lon"]
    return d


@register
class Weather(Widget):
    id = "weather"
    title = "Weather"
    category = "Widgets"
    description = "Current conditions and forecast from Open-Meteo (no API key needed)."
    params = [
        P("location", "text", "", "Location (blank = Settings default)"),
        P("view", "select", "both", options=[["both", "Alternate current/forecast"], ["current", "Current only"], ["forecast", "Forecast only"]]),
        P("units", "select", "", options=[["", "Settings default"], ["fahrenheit", "°F"], ["celsius", "°C"]]),
    ]
    refresh = 600

    @classmethod
    def needs_setup(cls, store, params):
        if not (params.get("location") or store.settings.get("weather", {}).get("lat") is not None):
            return "a weather location (Settings, Location)"
        return None

    def fetch(self):
        return fetch_weather(self.ctx.store, self.p["location"], self.p["units"])

    def draw(self, c, now):
        view = self.p["view"]
        if view == "both":
            view = "current" if int(now / 10) % 2 == 0 else "forecast"
        d = self.data
        if view == "current":
            cur = d["current"]
            draw_icon(c, 1, 2, weather_kind(cur["weather_code"], cur["is_day"]), 24)
            temp = f"{round(cur['temperature_2m'])}°"
            c.text(31, 3, temp, NORMAL, scale=2)
            day = d["daily"]
            c.text(31, 20, f"H{round(day['temperature_2m_max'][0])} L{round(day['temperature_2m_min'][0])}", SMALL)
            c.text(31, 27, f"{cur['relative_humidity_2m']}% {round(cur['wind_speed_10m'])}{d['_wind']}", SMALL)
            c.text_center(c.h - 7, WEATHER_TEXT.get(cur["weather_code"], ""), NORMAL)
        else:
            day = d["daily"]
            for i in range(1, 4):
                x = (i - 1) * 28
                name = dt.date.fromisoformat(day["time"][i]).strftime("%a")
                c.text_center(1, name, NORMAL, x0=x, w=28)
                draw_icon(c, x + 6, 10, weather_kind(day["weather_code"][i]), 16)
                c.text_center(29, f"{round(day['temperature_2m_max'][i])}°", SMALL, x0=x, w=28)
                c.text_center(36, f"{round(day['temperature_2m_min'][i])}°", SMALL, x0=x, w=28)

    def describe(self):
        return "Weather" + (f": {self.data['_name']}" if self.data and self.data.get("_name") else "")


# ---- tickers (crypto + stocks) ---------------------------------------------

def fmt_price(v):
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.2f}"
    return f"{v:.4g}"


def draw_sparkline(c, x, y, w, h, values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = []
    for i in range(w):
        v = values[int(i * (len(values) - 1) / max(w - 1, 1))]
        pts.append((x + i, y + h - 1 - round((v - lo) / span * (h - 1))))
    for a, b in zip(pts, pts[1:]):
        c.line(*a, *b)


class Ticker(Widget):
    """Shows one quote per page (or a scrolling line) from self.data = [{symbol, price, change, series}]."""

    common_params = [
        P("style", "select", "pages", options=[["pages", "One per page with chart"], ["scroll", "Scrolling ticker"]]),
        P("page_seconds", "number", 6, "Seconds per page", min=2, max=60, show_if={"style": "pages"}),
    ]
    refresh = 120
    interval = 0.05

    def draw(self, c, now):
        quotes = self.data
        if not quotes:
            c.text_center(17, "No data", NORMAL)
            return
        if self.p["style"] == "scroll":
            line = "   ".join(f"{q['symbol']} {fmt_price(q['price'])} {q['change']:+.1f}%" for q in quotes)
            tw = NORMAL.width(line) * 2
            off = int((now - self.started) * 20) % (tw + c.w)
            c.text(c.w - off, (c.h - 14) // 2, line, NORMAL, scale=2)
            return
        self.interval = 0.5
        q = quotes[int((now - self.started) / float(self.p["page_seconds"])) % len(quotes)]
        c.text(1, 1, q["symbol"][:6], NORMAL)
        ch = f"{q['change']:+.1f}%"
        c.text(c.w - NORMAL.width(ch) - 1, 1, ch, NORMAL)
        # up/down arrow
        ax = c.w - NORMAL.width(ch) - 8
        if q["change"] >= 0:
            for i in range(4):
                c.line(ax + 2 - i, 2 + i, ax + 2 + i, 2 + i)
        else:
            for i in range(4):
                c.line(ax + 2 - (3 - i), 3 + i, ax + 2 + (3 - i), 3 + i)
        price = fmt_price(q["price"])
        scale = 2 if NORMAL.width(price) * 2 <= c.w else 1
        c.text_center(10, price, NORMAL, scale=scale)
        draw_sparkline(c, 0, 27, c.w, 15, q.get("series") or [])


@register
class Crypto(Ticker):
    id = "crypto"
    title = "Crypto prices"
    category = "Widgets"
    description = "Prices and 7-day charts from CoinGecko."
    params = [
        P("coins", "text", "bitcoin,ethereum", "CoinGecko coin ids (comma separated)"),
        P("currency", "text", "usd"),
    ] + Ticker.common_params

    def fetch(self):
        ids = urllib.parse.quote(self.p["coins"].replace(" ", ""))
        cur = urllib.parse.quote(self.p["currency"])
        d = http_get(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency={cur}&ids={ids}&sparkline=true", ttl=110)
        return [{"symbol": x["symbol"].upper(), "price": x["current_price"] or 0,
                 "change": x.get("price_change_percentage_24h") or 0,
                 "series": (x.get("sparkline_in_7d") or {}).get("price", [])} for x in d]


@register
class Stocks(Ticker):
    id = "stocks"
    title = "Stock prices"
    category = "Widgets"
    description = "Quotes and intraday charts from Yahoo Finance."
    params = [P("symbols", "text", "AAPL,MSFT,^GSPC", "Symbols (comma separated)")] + Ticker.common_params

    def fetch(self):
        out = []
        for sym in [s.strip() for s in self.p["symbols"].split(",") if s.strip()]:
            d = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1d&interval=5m", ttl=110)
            r = d["chart"]["result"][0]
            meta = r["meta"]
            price = meta["regularMarketPrice"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
            series = r["indicators"]["quote"][0].get("close") or []
            out.append({"symbol": sym.lstrip("^"), "price": price, "change": (price - prev) / prev * 100, "series": series})
        return out


# ---- sports ------------------------------------------------------------------

LEAGUES = {
    "nfl": "football/nfl", "ncaaf": "football/college-football", "nba": "basketball/nba",
    "wnba": "basketball/wnba", "ncaab": "basketball/mens-college-basketball", "mlb": "baseball/mlb",
    "nhl": "hockey/nhl", "mls": "soccer/usa.1", "epl": "soccer/eng.1",
}


@register
class Sports(Widget):
    id = "sports"
    title = "Sports scores"
    category = "Widgets"
    description = "Live scores and schedules from ESPN."
    params = [
        P("league", "select", "nfl", options=[[k, k.upper()] for k in LEAGUES]),
        P("teams", "text", "", "Only these teams (abbreviations, comma separated)"),
        P("page_seconds", "number", 6, "Seconds per page", min=2, max=60),
    ]
    refresh = 60

    def fetch(self):
        d = http_get(f"https://site.api.espn.com/apis/site/v2/sports/{LEAGUES[self.p['league']]}/scoreboard", ttl=50)
        teams = {t.strip().upper() for t in self.p["teams"].split(",") if t.strip()}
        games = []
        for e in d.get("events", []):
            comp = e["competitions"][0]
            sides = {t["homeAway"]: t for t in comp["competitors"]}
            away, home = sides.get("away"), sides.get("home")
            if not away or not home:
                continue
            abbrs = {away["team"]["abbreviation"], home["team"]["abbreviation"]}
            if teams and not abbrs & teams:
                continue
            st = e["status"]["type"]
            if st["state"] == "pre":
                status = dt.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone().strftime("%a %-I:%M%p")
            else:
                status = st.get("shortDetail", "")
            games.append({
                "away": away["team"]["abbreviation"], "home": home["team"]["abbreviation"],
                "as": away.get("score", ""), "hs": home.get("score", ""), "state": st["state"], "status": status,
            })
        return games

    def draw(self, c, now):
        games = self.data
        if not games:
            c.text_center(10, self.p["league"].upper(), NORMAL)
            c.text_center(22, "No games", NORMAL)
            return
        pages = [games[i:i + 3] for i in range(0, len(games), 3)]
        page = pages[int((now - self.started) / float(self.p["page_seconds"])) % len(pages)]
        for i, g in enumerate(page):
            y = i * 14
            if g["state"] == "pre":
                line = f"{g['away']} @ {g['home']}"
            else:
                line = f"{g['away']} {g['as']}-{g['hs']} {g['home']}"
            c.text_center(y, line, NORMAL)
            c.text_center(y + 8, g["status"].upper()[:20], SMALL)

    def describe(self):
        return f"Sports: {self.p['league'].upper()}"


# ---- generic JSON ------------------------------------------------------------

def json_path(data, path):
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(data, list):
            data = data[int(part)]
        else:
            data = data[part]
    return data


@register
class JsonWidget(Widget):
    id = "json"
    title = "JSON / API value"
    category = "Widgets"
    description = "Fetch any JSON URL (transit times, home sensors...) and show values via a template like 'Bus in {data.0.minutes} min'."
    params = [
        P("url", "text", "http://api.open-notify.org/astros.json", "URL"),
        P("template", "textarea", "{number} people in space", "Template ({path.to.value})"),
        P("headers", "textarea", "", "Extra headers (JSON object, optional)"),
        P("refresh_seconds", "number", 60, min=5, max=86400),
        P("style", "select", "static", options=[["static", "Static"], ["scroll", "Scrolling"]]),
        P("font", "font", "normal", "Font"),
        P("scale", "number", 1, "Scale (pixel fonts)", min=1, max=6, show_if={"font": "pixel"}),
        P("font_size", "number", 14, "Size in dots (other fonts)", min=5, max=42, show_if={"font": {"not": "pixel"}}),
        P("char_transition", "select", "push-up", "Character transition when a value changes",
          options=[["none", "None"], ["push-up", "Roll up"], ["push-down", "Roll down"], ["flip", "Flip"],
                   ["dissolve", "Dissolve"], ["wipe-down", "Wipe down"], ["random", "Random"]]),
    ]
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = float(self.p["refresh_seconds"])
        from .text import Text
        text_params = {k: self.p[k] for k in ("font", "scale", "font_size", "char_transition")}
        if "size" in params and "font" not in params:  # older saved settings
            text_params = {"size": params["size"]}
        self.text_app = Text(ctx, style=self.p["style"], text="", **text_params)

    def fetch(self):
        headers = json.loads(self.p["headers"]) if self.p["headers"].strip() else None
        data = http_get(self.p["url"], ttl=self.refresh - 1, headers=headers)

        def sub(m):
            try:
                v = json_path(data, m.group(1))
                return fmt_price(v) if isinstance(v, float) else str(v)
            except (KeyError, IndexError, ValueError, TypeError):
                return "?"

        return re.sub(r"\{([^{}]+)\}", sub, self.p["template"])

    def draw(self, c, now):
        self.text_app.p["text"] = self.data
        self.text_app.render(c, now)
