"""Daily things: content channels (quote, word, on this day, Oblique Strategies, literature clock), a daily
Claude postcard, the next rocket launch, a counter with rolling digits and an ISS tracker.

Network results are cached as files in data/cache/ so a restart doesn't ask the APIs again; every app
falls back to its cache or a calm offline screen. Bundled content lives in apps/daily_data/.
"""

import datetime as dt
import io
import json
import logging
import math
import random
import re
import threading
import time
import unicodedata
from pathlib import Path

from canvas import Canvas, wrap_text
from fonts import NORMAL, SMALL

from .base import NeedsSetup, P, Widget, http_get, register

log = logging.getLogger("daily")

DATA = Path(__file__).with_name("daily_data")
_bundled = {}


def bundled(name):
    if name not in _bundled:
        _bundled[name] = json.loads((DATA / name).read_text())
    return _bundled[name]


def asciify(s):
    """Text the pixel fonts can draw: curly quotes and dashes made plain, accents dropped."""
    s = str(s or "")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", " - "),
                 ("–", "-"), ("…", "..."), (" ", " "), ("°", "\x00")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().replace("\x00", "°")
    return re.sub(r"\s+", " ", s).strip()


def day_pick(seq, day, salt):
    """The same item all day, a different one each day, no repeats until the list has gone round."""
    order = list(range(len(seq)))
    random.Random(salt).shuffle(order)
    return seq[order[day.toordinal() % len(seq)]]


# ---- file cache for network results -------------------------------------------------------

def fetch_json(url, headers=None, timeout=10):
    """One GET (tests replace this with recorded fixtures)."""
    return http_get(url, ttl=5, headers=headers, timeout=timeout)


def cache_path(store, key):
    d = Path(store.dir) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9_.-]+", "_", key)[:120] + ".json")


def read_cache(store, key):
    try:
        return json.loads(cache_path(store, key).read_text())
    except (OSError, ValueError):
        return None


def write_cache(store, key, data):
    p = cache_path(store, key)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved": time.time(), "data": data}))
    tmp.replace(p)


_cache_locks = {}
_cache_guard = threading.Lock()


def cached_get(store, key, url, ttl, headers=None, now=None, shape=None):
    """(data, saved_at, stale). Fresh file cache wins; otherwise fetch and save. When the fetch fails an
    older copy is returned with stale=True; with no copy at all the error is raised. shape(data) keeps just
    what's needed (the data folder is mirrored into the database, so the copies stay small)."""
    now = time.time() if now is None else now
    with _cache_guard:
        lock = _cache_locks.setdefault(key, threading.Lock())
    with lock:
        hit = read_cache(store, key)
        if hit and now - hit.get("saved", 0) < ttl:
            return hit["data"], hit["saved"], False
        try:
            data = fetch_json(url, headers=headers)
            if shape:
                data = shape(data)
        except Exception as e:
            if hit:
                log.info("%s: %s (showing the copy from %s)", key, e, time.strftime("%H:%M", time.localtime(hit["saved"])))
                return hit["data"], hit["saved"], True
            raise
        write_cache(store, key, data)
        return data, now, False


# ---- text pages ----------------------------------------------------------------------------

PITCH = {id(NORMAL): 8, id(SMALL): 6}


def wrap_spans(text, font, width):
    """Greedy wrap that keeps each word's position in `text`: [[(word, start), ...], ...]."""
    lines, cur, cur_w = [], [], 0
    space = font.width(" ") + 2 * font.spacing
    for m in re.finditer(r"\S+", text):
        word = m.group()
        ww = font.width(word)
        while ww > width and len(word) > 1:  # a word wider than the board: break it
            cut = max(1, int(len(word) * width / ww) - 1)
            while cut > 1 and font.width(word[:cut]) > width:
                cut -= 1
            if cur:
                lines.append(cur)
                cur, cur_w = [], 0
            lines.append([(word[:cut], m.start())])
            word = word[cut:]
            ww = font.width(word)
        if cur and cur_w + space + ww > width:
            lines.append(cur)
            cur, cur_w = [], 0
        cur.append((word, m.start() + len(m.group()) - len(word)))
        cur_w += (space if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    return lines


def line_width(words, font):
    return sum(font.width(w) for w, _ in words) + (len(words) - 1) * (font.width(" ") + 2 * font.spacing)


def draw_words(c, x, y, words, font, hl=None, v=1):
    """Draw one wrapped line; words inside the char span `hl` are shown inverted (lit box, dark text)."""
    space = font.width(" ") + 2 * font.spacing
    for i, (w, start) in enumerate(words):
        ww = font.width(w)
        lit = hl and start < hl[1] and start + len(w) > hl[0]
        if lit:
            nxt = words[i + 1] if i + 1 < len(words) else None
            joined = nxt and nxt[1] < hl[1]
            c.rect(x - 1, y - 1, ww + 2 + (space - 1 if joined else 0), font.height + 2, v=v, fill=True)
            c.text(x, y, w, font, v=1 - v)
        else:
            c.text(x, y, w, font, v=v)
        x += ww + space


class Item:
    """One thing to show: main text (with an optional highlighted span), a small byline and header."""

    def __init__(self, text, by="", label="", right="", hl=None, big=None):
        self.text, self.by, self.label, self.right, self.hl, self.big = asciify(text), asciify(by), label, right, hl, big


def layout(item, w, h, size="auto"):
    """Pages of lines [(font, words, align, hl)] that fit w x h; the byline stays with the text's end."""
    def build(font):
        lines = [(font, ws, "left", item.hl) for ws in wrap_spans(item.text, font, w)]
        by = [(SMALL, ws, "right", None) for ws in wrap_spans(item.by, SMALL, w)] if item.by else []
        return lines, by

    pitch = line_pitch

    def paginate(lines, by, cap=None):
        pages, cur, used = [], [], 0
        for ln in lines:
            p = pitch(ln)
            if cur and (used + p - 1 > h or (cap and len(cur) >= cap)):
                pages.append(cur)
                cur, used = [], 0
            cur.append(ln)
            used += p
        need = sum(pitch(b) for b in by) + (2 if by else 0)
        if by and cur and used + need - 1 > h:
            pages.append(cur)
            cur, used = [], 0
        cur = cur + [None] * bool(by and cur) + by
        if cur:
            pages.append(cur)
        if cap is None and len(pages) > 1 and lines:
            # even out the pages so the last one isn't a single orphaned line
            n = len([pg for pg in pages if any(ln and ln[2] != "right" for ln in pg)])
            even = paginate(lines, by, cap=-(-len(lines) // max(1, n)))
            if len(even) <= len(pages):
                return even
        return pages

    if size == "small":  # "fit": large only if it all fits on one page
        return paginate(*build(SMALL))
    big = paginate(*build(NORMAL))
    too_wide = any(NORMAL.width(word) > w for word in item.text.split())
    if size == "large" or (not too_wide and (len(big) == 1 or (len(big) == 2 and size != "fit"))):
        return big
    return paginate(*build(SMALL))


def line_lit(words, hl):
    return any(start < hl[1] and start + len(w) > hl[0] for w, start in words)


def line_pitch(ln):
    """Height of a line; a line with a highlighted (inverted) phrase gets a clear row above and below."""
    if ln is None:
        return 2
    return PITCH[id(ln[0])] + (2 if ln[3] and line_lit(ln[1], ln[3]) else 0)


def page_height(page):
    return sum(line_pitch(ln) for ln in page) - 1


def draw_page(c, page, x0, y0, w, h):
    y = y0 + max(0, (h - page_height(page)) // 2)
    for ln in page:
        if ln is None:
            y += 2
            continue
        font, words, align, hl = ln
        lw = line_width(words, font)
        x = x0 + (w - lw if align == "right" else (w - lw) // 2 if align == "center" else 0)
        extra = line_pitch(ln) - PITCH[id(font)]
        draw_words(c, x, y + extra // 2, words, font, hl)
        y += line_pitch(ln)


class Pager:
    """Shows a list of Items one page at a time (or rolling upwards), in a loop."""

    def __init__(self, page_seconds=8, style="pages", size="auto", header=True, align="left"):
        self.page_seconds, self.style, self.size, self.header, self.align = float(page_seconds), style, size, header, align
        self.items, self.key, self.start = [], None, 0
        self._cache = {}

    def set(self, items, key, now):
        if key != self.key:
            self.items, self.key, self.start = items, key, now
            self._cache = {}

    def pages(self, i, w, h):
        if i not in self._cache:
            pages = layout(self.items[i], w, h, self.size)
            if self.align == "center":
                pages = [[ln if ln is None or ln[2] == "right" else (ln[0], ln[1], "center", ln[3]) for ln in pg] for pg in pages]
            self._cache[i] = pages
        return self._cache[i]

    def draw(self, c, now, page_cap=None):
        if not self.items:
            return
        top = 7 if self.header else 0
        w, h = c.w, c.h - top
        per = self.page_seconds
        seq = [(i, k) for i in range(len(self.items)) for k in range(len(self.pages(i, w, h)))]
        if page_cap:
            per = min(per, page_cap / max(1, len(seq)))
        if self.style == "scroll":
            return self._scroll(c, now, top, w, h)
        i, k = seq[int((now - self.start) / per) % len(seq)]
        item = self.items[i]
        if self.header:
            self.draw_header(c, item)
        draw_page(c, self.pages(i, w, h)[k], 0, top, w, h)

    def draw_header(self, c, item):
        right = item.right.upper()
        room = c.w - (SMALL.width(right) + 2 if right else 0)
        label = asciify(item.label).upper()
        while label and SMALL.width(label) > room:
            label = label[:-1]
        c.text(0, 0, label.rstrip(), SMALL)
        if right:
            c.text(c.w - SMALL.width(right), 0, right, SMALL)

    def _scroll(self, c, now, top, w, h):
        """All items as one tall strip that rolls up a line at a time, pausing on each line."""
        strip = []  # (item index, line or None)
        for i in range(len(self.items)):
            for pg in self.pages(i, w, h):
                strip += [(i, ln) for ln in pg]
            strip += [(i, "gap")]
        pitches = [h if ln == "gap" else line_pitch(ln) for _, ln in strip]
        pause, speed = max(1.0, self.page_seconds / 4), 12.0  # seconds per line, dots per second
        cycle = sum(pause + p / speed for p in pitches)
        t = (now - self.start) % cycle
        off = 0.0
        for p in pitches:
            if t < pause:
                break
            t -= pause
            if t < p / speed:
                off += t * speed
                break
            t -= p / speed
            off += p
        total = sum(pitches)
        y = top - int(off)
        idx = 0
        if self.header:
            # the header names the item that's at the top
            acc = 0
            for n, p in enumerate(pitches):
                if acc + p > off:
                    idx = strip[n][0]
                    break
                acc += p
        for rep in (0, 1):
            yy = y + rep * total
            for (i, ln), p in zip(strip, pitches):
                if top - 9 < yy < c.h and ln not in (None, "gap"):
                    font, words, align, hl = ln
                    lw = line_width(words, font)
                    x = w - lw if align == "right" else (w - lw) // 2 if self.align == "center" else 0
                    draw_words(c, x, yy + (line_pitch(ln) - PITCH[id(font)]) // 2, words, font, hl)
                yy += p
        if self.header:
            c.rect(0, 0, c.w, top, v=0, fill=True)
            self.draw_header(c, self.items[idx])


def calm(c, title, lines):
    """The quiet offline / waiting screen."""
    lines = [ln for s in lines for ln in wrap_text(asciify(s), SMALL, c.w - 4)][:4]
    top = (c.h - 10 - 6 * len(lines)) // 2
    c.text_center(top, title[:14], NORMAL)
    for i, line in enumerate(lines):
        c.text_center(top + 10 + i * 6, line, SMALL)


PAGE_PARAMS = [
    P("style", "select", "pages", "Long text", options=[["pages", "Pages"], ["scroll", "Rolls up line by line"]]),
    P("text_size", "select", "auto", options=[["auto", "Auto (large if it fits on two pages)"], ["large", "Large"], ["small", "Small"]]),
    P("page_seconds", "number", 8, "Seconds per page", min=2, max=60),
    P("header", "checkbox", True, "Show the channel name"),
]


# ---- daily channels ------------------------------------------------------------------------

def user_deck(store):
    """data/oblique_strategies.txt: your own deck, one card per line."""
    try:
        cards = [ln.strip() for ln in (Path(store.dir) / "oblique_strategies.txt").read_text().splitlines()]
    except OSError:
        return None
    cards = [c for c in cards if c and not c.startswith("#")]
    return cards or None


NUMBER_WORDS = ("twelve one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                "sixteen seventeen eighteen nineteen").split()


def minute_words(m):
    if m < 20:
        return NUMBER_WORDS[m] if m else ""
    tens = ["", "", "twenty", "thirty", "forty", "fifty"][m // 10]
    return tens + ("-" + NUMBER_WORDS[m % 10] if m % 10 else "")


def time_words(h, m):
    """'twenty-three minutes past four', 'a quarter to nine', 'midnight'."""
    if m == 0:
        return "midnight" if h == 0 else "noon" if h == 12 else f"{NUMBER_WORDS[h % 12]} o'clock"
    if m == 15:
        return f"a quarter past {NUMBER_WORDS[h % 12]}"
    if m == 30:
        return f"half past {NUMBER_WORDS[h % 12]}"
    nxt = "midnight" if h == 23 else NUMBER_WORDS[(h + 1) % 12]
    if m == 45:
        return f"a quarter to {nxt}"
    if m < 30:
        return f"{minute_words(m)} minute{'s' if m != 1 else ''} past {'midnight' if h == 0 else NUMBER_WORDS[h % 12]}"
    return f"{minute_words(60 - m)} minute{'s' if m != 59 else ''} to {nxt}"


def litclock_item(t):
    """The literature clock's Item for local time t (a datetime)."""
    minutes = bundled("litclock.json")["minutes"]
    key = f"{t.hour:02d}:{t.minute:02d}"
    label = t.strftime("%H:%M")
    if minutes.get(key):
        quote, a, b, title, author = day_pick(minutes[key], t.date(), "lit" + key)
        return Item(quote, f"{title}, {author}", "Literature clock", label, hl=(a, b))
    phrase = time_words(t.hour, t.minute)
    text = f"It was {phrase}."
    return Item(text, "", "Literature clock", label, hl=(7, 7 + len(phrase)))


ZEN_URL = "https://zenquotes.io/api/today"


def otd_events(data):
    return [{"year": e.get("year"), "text": e.get("text", "")} for e in (data or {}).get("selected") or [] if e.get("text")]
OTD_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/{m:02d}/{d:02d}"


@register
class DailyChannels(Widget):
    id = "daily"
    title = "Daily channels"
    category = "Widgets"
    description = ("Quote, word of the day, on this day (Wikipedia), Oblique Strategies-style prompts, or a literature clock "
                   "that quotes a book for every minute it can. New content each day.")
    params = [
        P("channel", "select", "quote", options=[
            ["quote", "Quote of the day"], ["word", "Word of the day"], ["onthisday", "On this day (Wikipedia)"],
            ["oblique", "Oblique Strategies"], ["litclock", "Literature clock (every minute)"]]),
        P("quote_source", "select", "bundled", "Quotes from", show_if={"channel": "quote"}, options=[
            ["bundled", "Built-in list (works offline)"], ["zenquotes", "ZenQuotes.io (falls back to the built-in list)"]]),
        P("events", "number", 6, "Events to show", min=1, max=20, show_if={"channel": "onthisday"}),
        *PAGE_PARAMS,
    ]
    interval = 0.5
    refresh = 1800

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        size = self.p["text_size"]
        if size == "auto" and self.p["channel"] == "litclock":
            size = "fit"  # a new quote every minute: one page if it can
        self.pager = Pager(self.p["page_seconds"], self.p["style"], size, bool(self.p["header"]),
                           align="center" if self.p["channel"] == "oblique" else "left")
        self.day = None

    def online(self):
        ch = self.p["channel"]
        return ch == "onthisday" or (ch == "quote" and self.p["quote_source"] == "zenquotes")

    def fetch(self):
        if not self.online():
            return None
        today = dt.date.today()
        ttl = 86400 * 2  # keyed by date, so a copy is only ever for its own day
        if self.p["channel"] == "quote":
            data, _, _ = cached_get(self.ctx.store, f"zenquotes-{today}", ZEN_URL, ttl)
            q = data[0] if isinstance(data, list) and data else {}
            if not q.get("q"):
                raise ValueError("ZenQuotes sent no quote")
            return {"day": str(today), "quote": [q["q"], q.get("a", "")]}
        events, _, _ = cached_get(self.ctx.store, f"onthisday-{today:%m-%d}-{today.year}",
                                  OTD_URL.format(m=today.month, d=today.day), ttl, shape=otd_events)
        return {"day": str(today), "events": events}

    def items(self, now):
        ch = self.p["channel"]
        t = dt.datetime.fromtimestamp(now)
        day = t.date()
        if ch == "litclock":
            return [litclock_item(t)], t.strftime("%Y-%m-%d %H:%M")
        data = self.data if self.data and self.data.get("day") == str(day) else None
        if ch == "quote":
            if data and data.get("quote"):
                q, a = data["quote"]
                return [Item(q, a, "Quote", "zenquotes.io")], ("zen", str(day))
            text, author, source = day_pick(bundled("quotes.json")["quotes"], day, "quote")
            return [Item(text, author + (f", {source}" if source and not author.endswith(source) else ""), "Quote of the day")], str(day)
        if ch == "word":
            word, pos, meaning = day_pick(bundled("words.json")["words"], day, "word")
            return [Item(word, pos, "Word of the day", big=True),
                    Item(meaning, "", word, pos)], str(day)
        if ch == "oblique":
            deck = user_deck(self.ctx.store) or bundled("prompts.json")["prompts"]
            return [Item(day_pick(deck, day, "oblique"), "", "Oblique Strategies")], (str(day), len(deck))
        if ch == "onthisday":
            if not data:
                return None, None
            evs = data["events"][:int(self.p["events"])]
            return [Item(e["text"], "", "On this day", str(e["year"] or "")) for e in evs], str(day)
        return [], None

    def render(self, c, now):
        today = dt.date.fromtimestamp(now)
        if today != self.day:  # a new day: look again right away
            self.day = today
            self._last_fetch = 0
        if self.online():
            self.poll()
        items, key = self.items(now)
        if items is None:
            if self.error and not self._fetching:
                calm(c, "On this day", ["No connection to Wikipedia yet.", "Trying again in a few minutes."])
                self.refresh = 300
            else:
                calm(c, "On this day", ["Loading..."])
            return
        self.refresh = 1800
        self.pager.set(items, key, now)
        if self.p["channel"] == "word" and items[0].big:
            self._word(c, now, items)
            return
        self.pager.draw(c, now, page_cap=60 if self.p["channel"] == "litclock" else None)

    def _word(self, c, now, items):
        """The word big for a few seconds, then its definition in pages."""
        per = float(self.p["page_seconds"])
        defs = self.pager.pages(1, c.w, c.h - (7 if self.pager.header else 0))
        n = 1 + len(defs)
        k = int((now - self.pager.start) / per) % n
        if k == 0:
            item = items[0]
            if self.pager.header:
                self.pager.draw_header(c, item)
            word = item.text
            scale = 2 if NORMAL.width(word) * 2 <= c.w else 1
            font = NORMAL if NORMAL.width(word) <= c.w else SMALL
            y = 12 if self.pager.header else 8
            c.text_center(y, word, font, scale=scale if font is NORMAL else 1)
            c.text_center(y + 7 * scale + 5, item.by, SMALL)
            return
        if self.pager.header:
            self.pager.draw_header(c, items[1])
        top = 7 if self.pager.header else 0
        draw_page(c, defs[k - 1], 0, top, c.w, c.h - top)

    def describe(self):
        names = dict(self.params[0]["options"])
        return names.get(self.p["channel"], self.title)


# ---- daily Claude postcard -------------------------------------------------------------------

POSTCARDS = "postcards.json"
GALLERY_NAME = "Postcards"
_make_lock = threading.Lock()  # one postcard being made at a time
_book_lock = threading.Lock()  # short read-modify-writes of postcards.json
_failed = {}  # date -> (time, message) of the last failed attempt
RETRY = 1800


def postcards(store):
    d = store._load(POSTCARDS, {"config": {}, "days": {}})
    d.setdefault("config", {})
    d.setdefault("days", {})
    return d


def season(day, lat=None):
    north = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "autumn", "autumn", "autumn", "winter"]
    s = north[day.month - 1]
    if lat is not None and lat < 0:
        s = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}[s]
    return s


def weather_line(store):
    """'Light rain, 12°C' for the Settings location, or '' (best effort, never raises)."""
    w = store.settings.get("weather") or {}
    if w.get("lat") is None:
        return ""
    try:
        from .widgets import WEATHER_TEXT
        units = w.get("units", "fahrenheit")
        d = fetch_json(f"https://api.open-meteo.com/v1/forecast?latitude={w['lat']}&longitude={w['lon']}"
                       f"&current=temperature_2m,weather_code&temperature_unit={units}&timezone=auto")
        cur = d["current"]
        return f"{WEATHER_TEXT.get(cur['weather_code'], 'Weather')}, {round(cur['temperature_2m'])}°{'F' if units == 'fahrenheit' else 'C'}"
    except Exception as e:
        log.info("postcard: no weather (%s)", e)
        return ""


def haiku_prompt(day, cfg, weather):
    w = f"Today's weather: {weather}.\n" if weather else ""
    theme = (cfg.get("subject") or "").strip()
    return (f"Today is {day:%A %d %B %Y}, in {cfg.get('_season', 'the season')}.\n{w}"
            + (f"Theme: {theme}\n" if theme else "")
            + "Write one haiku for today (three lines, about 5, 7 and 5 syllables), concrete and quiet, "
              "with one clear image that could be drawn. Plain ASCII, no title, no quotes. "
              "Answer with only the three lines.")


def parse_haiku(text):
    lines = []
    for ln in str(text or "").splitlines():
        ln = asciify(ln).strip().strip('"').strip()
        ln = re.sub(r"^(\d+[.)]|[-*])\s*", "", ln)
        if ln and not ln.lower().startswith(("haiku", "here", "title")):
            lines.append(ln)
    if len(lines) < 2:
        raise ValueError("no haiku in the reply")
    return lines[:3]


def write_haiku(settings, day, cfg, weather):
    import aidraw
    ai = settings.get("ai") or {}
    key = (ai.get("api_key") or "").strip()
    if not key:
        raise NeedsSetup("Add an Anthropic API key in Settings, AI drawing")
    model = ai.get("model") or aidraw.DEFAULT_MODEL
    body = {"model": model, "max_tokens": 1000, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": haiku_prompt(day, cfg, weather)}]}
    if model in aidraw.FALLBACK_MODELS:
        body["fallbacks"] = "default"
    resp = aidraw.call_api(key, body, timeout=120)
    if resp.get("stop_reason") == "refusal":
        raise aidraw.AiError("The model declined to write today's haiku.")
    text = "".join(b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
    return parse_haiku(text)


def haiku_card(lines, w, h, day=None, chars=None):
    """The haiku as a picture; chars = how many characters to show so far (typewriter)."""
    c = Canvas(w, h)
    for font, pitch in ((NORMAL, 10), (NORMAL, 8), (SMALL, 8), (SMALL, 7), (SMALL, 6)):
        wrapped = [wl for ln in lines for wl in wrap_text(ln, font, w)]
        if len(wrapped) * pitch - (pitch - font.height) <= h:
            break
    height = len(wrapped) * pitch - (pitch - font.height)
    stamp = f"{day:%b} {day.day}".upper() if day else ""
    room = stamp and height <= h - 7
    total = len("\n".join(lines))
    left = total if chars is None else chars
    y = (h - (6 if room else 0) - height) // 2
    for wl in wrapped:
        c.text((w - font.width(wl)) // 2, y, wl[:max(0, left)], font)
        left -= len(wl) + 1
        y += pitch
    if room and (chars is None or chars >= total):
        c.text(w - SMALL.width(stamp) - 1, h - 5, stamp, SMALL)
    return c


def png_bytes(rows):
    from PIL import Image
    h, w = len(rows), len(rows[0])
    img = Image.new("L", (w, h), 0)
    img.putdata([255 if ch == "1" else 0 for r in rows for ch in r])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def postcard_gallery(store, cfg):
    gid = cfg.get("gallery")
    if gid in store.galleries:
        return gid
    gid = next((g for g, v in store.galleries.items() if v.get("name") == GALLERY_NAME), None)
    if gid is None:
        gid = store.create_gallery(GALLERY_NAME)
        store.galleries[gid]["settings"].update({"image_seconds": 12, "transition": "dissolve"})
    cfg["gallery"] = gid
    return gid


def archive(store, cfg, day, entry, w, h):
    """Save into the Postcards slideshow and/or Pictures, as the app's save_to says."""
    import media_upload
    where = cfg.get("save_to", "both")
    card = haiku_card(entry["haiku"], w, h, day).rows()
    if where in ("both", "slideshow"):
        gid = postcard_gallery(store, cfg)
        g = store.gallery(gid)
        names = (f"postcard-{day}.png", f"postcard-{day}-haiku.png")
        for old in [i for i in g["items"] if i["file"] in names]:  # made again today: replace
            (store.gallery_dir(gid) / old["file"]).unlink(missing_ok=True)
            g["items"].remove(old)
        media_upload.add_file(store, gid, names[0], png_bytes(entry["rows"]))
        media_upload.add_file(store, gid, names[1], png_bytes(card))
        store.save_galleries()
        store.sync(f"media/{gid}")
        entry["gallery"] = gid
    if where in ("both", "pictures"):
        entry["scene"] = store.save_scene(f"Postcard {day}", [{"rows": entry["rows"], "delay": 15000},
                                                              {"rows": card, "delay": 10000}], scene_id=f"postcard-{day}")


def make_postcard(store, day=None, force=False, w=84, h=42):
    """Write today's haiku, draw its picture and save it. Returns the entry. Raises NeedsSetup / AiError."""
    import aidraw
    day = day or dt.date.today()
    with _make_lock:
        book = postcards(store)
        cfg = book["config"]
        if not force and str(day) in book["days"]:
            return book["days"][str(day)]
        if not (store.settings.get("ai") or {}).get("api_key"):
            raise NeedsSetup("Add an Anthropic API key in Settings, AI drawing")
        try:
            weather = weather_line(store)
            lat = (store.settings.get("weather") or {}).get("lat")
            haiku = write_haiku(store.settings, day, {**cfg, "_season": season(day, lat)}, weather)
            style = (cfg.get("style") or "").strip() or "a calm postcard illustration"
            pic = aidraw.draw(store.settings, "An illustration for this haiku: " + " / ".join(haiku), 1, style, w, h, timeout=300)
        except Exception as e:
            _failed[str(day)] = (time.time(), str(e))
            raise
        entry = {"haiku": haiku, "rows": pic["frames"][0]["rows"], "made": time.time(), "weather": weather}
        try:
            archive(store, cfg, day, entry, w, h)
        except Exception:
            log.exception("postcard: couldn't save to the slideshow/Pictures")
        _failed.pop(str(day), None)
        with _book_lock:
            book = postcards(store)  # re-read: keep only the last 60 days here (the slideshow is the archive)
            if cfg.get("gallery"):
                book["config"]["gallery"] = cfg["gallery"]
            book["days"][str(day)] = entry
            for old in sorted(book["days"])[:-60]:
                del book["days"][old]
            store._save(POSTCARDS, book)
        log.info("postcard for %s: %s", day, " / ".join(haiku))
        return entry


def due(cfg, now):
    """True once today's postcard time has passed."""
    try:
        hh, mm = (int(x) for x in str(cfg.get("time") or "07:00").split(":")[:2])
    except ValueError:
        hh, mm = 7, 0
    t = dt.datetime.fromtimestamp(now)
    return (t.hour, t.minute) >= (hh, mm)


def maybe_make(store, now=None, w=84, h=42):
    """Make today's postcard if it's time, it's not made yet and the last try wasn't too recent."""
    now = time.time() if now is None else now
    book = postcards(store)
    cfg = book["config"]
    day = dt.date.fromtimestamp(now)
    if not cfg.get("auto", True) or not cfg.get("time") or str(day) in book["days"] or not due(cfg, now):
        return None
    if not (store.settings.get("ai") or {}).get("api_key"):
        return None
    last = _failed.get(str(day))
    if last and now - last[0] < RETRY:
        return None
    if _make_lock.locked():  # already being made (the scheduler and the app both ask)
        return None
    return make_postcard(store, day, w=w, h=h)


_scheduler = None


def start_scheduler(store, w=84, h=42):
    """Background thread: makes the morning postcard once a Daily postcard app has been set up (its params
    are saved on first use), even while something else is on the board."""
    global _scheduler
    if _scheduler:
        return

    def loop():
        while True:
            try:
                maybe_make(store, w=w, h=h)
            except Exception as e:
                log.warning("postcard: %s", e)
            time.sleep(60)

    _scheduler = threading.Thread(target=loop, name="postcards", daemon=True)
    _scheduler.start()


@register
class Postcard(Widget):
    id = "postcard"
    title = "Daily postcard"
    category = "Widgets"
    description = ("Each morning Claude writes a haiku and draws a picture for it; this shows today's, picture then haiku. "
                   "Every postcard is also saved to the Postcards slideshow and/or Pictures. Uses the AI drawing settings.")
    params = [
        P("time", "text", "07:00", "Make it at (HH:MM)"),
        P("subject", "text", "", "Theme (optional, e.g. the garden, the city, the sea)"),
        P("style", "text", "", "Picture style (optional)"),
        P("save_to", "select", "both", "Keep each postcard in", options=[
            ["both", "Postcards slideshow and Pictures"], ["slideshow", "Postcards slideshow"], ["pictures", "Pictures"], ["none", "Don't keep"]]),
        P("auto", "checkbox", True, "Make it every morning, even when it isn't on the board"),
        P("picture_seconds", "number", 20, min=3, max=600),
        P("haiku_seconds", "number", 15, min=3, max=600),
    ]
    interval = 0.5
    refresh = 60

    @classmethod
    def needs_setup(cls, store, params):
        if not (store.settings.get("ai") or {}).get("api_key"):
            return "an Anthropic API key (Settings, AI drawing)"
        return None

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.cycle_start = time.time()
        self.making = False
        try:
            self._save_config()
        except Exception:
            log.exception("postcard: couldn't save settings")

    def _save_config(self):
        """The morning scheduler follows the most recently used postcard app's settings."""
        with _book_lock:
            book = postcards(self.ctx.store)
            want = {k: self.p[k] for k in ("time", "subject", "style", "save_to", "auto")}
            if any(book["config"].get(k) != v for k, v in want.items()):
                book["config"].update(want)
                self.ctx.store._save(POSTCARDS, book)

    def fetch(self):
        store = self.ctx.store
        if not (store.settings.get("ai") or {}).get("api_key"):
            raise NeedsSetup("Add an Anthropic API key in Settings, AI drawing")
        self.making = True
        try:
            maybe_make(store, w=self.w, h=self.h)
        finally:
            self.making = False
        days = postcards(store)["days"]
        if not days:
            return None
        day = max(days)
        return {"day": day, **days[day]}

    def render(self, c, now):
        self.poll()
        if self.setup:
            calm(c, "Postcard", [self.setup])
            return
        d = self.data
        if not d:
            last = _failed.get(str(dt.date.fromtimestamp(now)))
            if self.making or _make_lock.locked():
                calm(c, "Postcard", ["Claude is writing today's haiku..."])
            elif last:
                calm(c, "Postcard", ["Couldn't make today's postcard.", "Trying again in a while."])
            else:
                calm(c, "Postcard", [f"The first one arrives at {self.p['time']}."])
            return
        if d.get("day") != getattr(self, "_shown_day", None):
            self._shown_day = d.get("day")
            self.cycle_start = now
        pic, hk = float(self.p["picture_seconds"]), float(self.p["haiku_seconds"])
        t = (now - self.cycle_start) % (pic + hk)
        if t < pic:
            self.interval = 0.5
            c.px[:] = Canvas.from_rows(fit_rows(d["rows"], c.w, c.h)).px
            return
        chars = int((t - pic) * 14)
        total = len("\n".join(d["haiku"]))
        self.interval = 0.07 if chars < total else 0.5
        day = dt.date.fromisoformat(d["day"])
        c.px[:] = haiku_card(d["haiku"], c.w, c.h, day, chars=chars).px

    def describe(self):
        return "Daily postcard"


def fit_rows(rows, w, h):
    """Rows cropped/padded to w x h around the middle (for layers of another size)."""
    if len(rows) == h and len(rows[0]) == w:
        return rows
    import aidraw
    return aidraw.repair(rows, w, h)


# ---- launch countdown ------------------------------------------------------------------------

LL2_HOSTS = {"ll": "ll.thespacedevs.com", "lldev": "lldev.thespacedevs.com"}
LL2_PATH = "/2.3.0/launches/upcoming/?limit=10"
LIFTOFF_SECONDS = 20

ROCKET = ["00100", "01110", "01110", "01110", "01110", "01110", "11111", "11011"]


def parse_time(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def launch_list(data):
    out = []
    for r in (data or {}).get("results") or []:
        try:
            net = parse_time(r["net"])
        except (KeyError, ValueError, TypeError):
            continue
        name = r.get("name") or ""
        rocket = ((r.get("rocket") or {}).get("configuration") or {}).get("name") or name.split("|")[0].strip()
        mission = (r.get("mission") or {}).get("name") or (name.split("|", 1)[1].strip() if "|" in name else "")
        pad = r.get("pad") or {}
        lsp = r.get("launch_service_provider") or {}
        out.append({
            "name": name, "rocket": rocket, "mission": mission, "net": net,
            "provider": lsp.get("name") or "", "abbrev": lsp.get("abbrev") or "",
            "status": (r.get("status") or {}).get("abbrev") or "",
            "precision": (r.get("net_precision") or {}).get("abbrev") or "MIN",
            "pad": (pad.get("location") or {}).get("name") or pad.get("name") or "",
        })
    out.sort(key=lambda x: x["net"])
    return out


def fit_text(c, y, s, w=None, x0=0, align="left"):
    """Draw s in NORMAL if it fits, else SMALL, else SMALL cut with '..'."""
    w = c.w if w is None else w
    s = asciify(s)
    font = NORMAL if NORMAL.width(s) <= w else SMALL
    if font.width(s) > w:
        while s and SMALL.width(s + "..") > w:
            s = s[:-1]
        s = s.rstrip() + ".."
    x = x0 + ((w - font.width(s)) // 2 if align == "center" else 0)
    c.text(x, y + (1 if font is SMALL else 0), s, font)


@register
class Launches(Widget):
    id = "launches"
    title = "Launch countdown"
    category = "Widgets"
    description = ("The next rocket launches from Launch Library 2 (The Space Devs) with a T-minus countdown; the last "
                   "ten seconds take the whole board. Checked at most every 15 minutes.")
    params = [
        P("count", "number", 3, "Launches to cycle through", min=1, max=10),
        P("page_seconds", "number", 10, "Seconds per launch", min=3, max=120),
        P("filter", "text", "", "Only these providers or rockets (comma separated, e.g. SpaceX, Rocket Lab)"),
        P("server", "select", "ll", "Data", options=[["ll", "Live (free tier, 15 calls an hour)"],
                                                     ["lldev", "Development server (unlimited, data may be stale)"]]),
        P("big_countdown", "checkbox", True, "Full-board countdown for the last 10 seconds"),
    ]
    interval = 0.25
    refresh = 900  # LL2's free tier allows 15 calls an hour; the file cache makes this 4 at most

    def fetch(self):
        host = LL2_HOSTS.get(self.p["server"], LL2_HOSTS["ll"])
        data, saved, stale = cached_get(self.ctx.store, f"ll2-{self.p['server']}", f"https://{host}{LL2_PATH}", 900,
                                        shape=launch_list)
        return {"launches": data, "saved": saved, "stale": stale}

    def upcoming(self, now):
        want = [w.strip().lower() for w in self.p["filter"].split(",") if w.strip()]
        out = []
        for l in (self.data or {}).get("launches", []):
            if l["net"] < now - LIFTOFF_SECONDS:
                continue
            hay = f"{l['provider']} {l['abbrev']} {l['rocket']} {l['name']}".lower()
            if want and not any(w in hay for w in want):
                continue
            out.append(l)
        return out

    def render(self, c, now):
        self.poll()
        if self.data is None:
            calm(c, "Launches", ["No connection to Launch Library yet.", "Trying again in a few minutes."]
                 if self.error else ["Loading..."])
            if self.error:
                self.refresh = 300
            return
        self.refresh = 900
        ls = self.upcoming(now)
        if not ls:
            calm(c, "Launches", ["No upcoming launches in the list.", "Checking again later."])
            return
        first = ls[0]
        left = first["net"] - now
        exact = first["precision"] in ("SEC", "MIN") and first["status"] in ("Go", "TBC", "Success", "In Flight")
        if exact and self.p["big_countdown"] and -LIFTOFF_SECONDS <= left <= 10:
            self.interval = 0.05
            if left > 0:
                n = str(math.ceil(left))
                tw = c.seg_width(n, 22, 5, 4)
                c.seg_text((c.w - tw) // 2, 1, n, w=22, h=40, t=5, gap=4)
            else:
                self.liftoff(c, -left)
            return
        self.interval = 0.25
        shown = ls[:int(self.p["count"])]
        i = int((now - self.started) / float(self.p["page_seconds"])) % len(shown)
        self.card(c, shown[i], now)

    def card(self, c, l, now):
        prov = (l["abbrev"] if SMALL.width(l["provider"].upper()) > 56 else l["provider"]).upper()
        c.text(0, 0, prov[:16], SMALL)
        status = {"Go": "", "TBD": "TBD", "TBC": "TBC", "Hold": "HOLD", "In Flight": "FLYING"}.get(l["status"], l["status"].upper())
        if status:
            c.text(c.w - SMALL.width(status), 0, status, SMALL)
        fit_text(c, 7, l["rocket"])
        second = l["mission"] if int(now / 5) % 2 == 0 or not l["pad"] else l["pad"]
        fit_text(c, 16, second)
        left = l["net"] - now
        if l["precision"] not in ("SEC", "MIN", "HR"):
            t = dt.datetime.fromtimestamp(l["net"])
            when = {"MON": f"{t:%B}", "QUAR": f"Q{(t.month - 1) // 3 + 1} {t.year}", "YEAR": str(t.year)}.get(l["precision"], f"{t:%b} {t.day}")
            c.text_center(29, "NET " + when.upper(), NORMAL)
            return
        if left <= 0:
            c.text_center(29, "LIFTOFF", NORMAL)
            return
        days, rem = divmod(int(left), 86400)
        hh, rem = divmod(rem, 3600)
        mm, ss = divmod(rem, 60)
        approx = l["precision"] == "HR"
        size = dict(w=7, h=13, t=2, gap=2)
        x = 0
        c.text(x, 30, "T-", NORMAL)
        x += NORMAL.width("T-") + 3
        if days:
            x += c.seg_text(x, 27, str(days), **size) + 2
            c.text(x, 34, "D", SMALL)
            x += SMALL.width("D") + 4
            s = f"{hh:02}:{mm:02}"
        else:
            s = f"{hh:02}:{mm:02}" if approx else f"{hh:02}:{mm:02}:{ss:02}"
        c.seg_text(x, 27, s, **size)
        if approx:
            c.text(c.w - SMALL.width("~"), 27, "~", SMALL)

    def liftoff(self, c, t):
        """A rocket climbing out of a smoke cloud, with LIFTOFF on the right."""
        y = int(c.h - 12 - t * t * 1.5)
        x = 14
        for ry, row in enumerate(ROCKET):
            for rx, ch in enumerate(row):
                if ch == "1":
                    c.set(x + rx, y + ry)
        rnd = random.Random(int(t * 10))
        for i in range(10):  # flame flickers
            c.set(x + 1 + rnd.randrange(3), y + 8 + rnd.randrange(4 + int(t)))
        for i in range(int(min(t, 6) * 12)):  # smoke at the pad
            sx = x + 2 + rnd.gauss(0, 3 + t)
            sy = c.h - 1 - abs(rnd.gauss(0, 1.5))
            c.set(sx, sy)
        if t > 1:
            c.text_center(17, "LIFTOFF", NORMAL, x0=30, w=c.w - 30)

    def describe(self):
        return "Launch countdown"


# ---- counter with rolling digits ---------------------------------------------------------------

COUNTERS = "counters.json"
_counters_lock = threading.Lock()


def counters(store):
    if not hasattr(store, "_daily_counters"):
        store._daily_counters = store._load(COUNTERS, {})
    return store._daily_counters


def set_counter(store, name, value=None, add=None):
    """POST /api/counter/<name>: set or add to a manual counter."""
    name = str(name)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
        raise ValueError("counter names use letters, digits, . _ - (up to 40)")
    with _counters_lock:
        data = counters(store)
        cur = (data.get(name) or {}).get("value") or 0
        try:
            if value is not None:
                new = float(value)
            elif add is not None:
                new = cur + float(add)
            else:
                raise ValueError('send {"value": n} or {"add": n}')
        except (TypeError, ValueError) as e:
            raise ValueError(str(e) if "send" in str(e) else "the value must be a number") from None
        if not math.isfinite(new):
            raise ValueError("the value must be a number")
        data[name] = {"value": int(new) if new == int(new) else new, "updated": time.time()}
        store._save(COUNTERS, data)
        return {"name": name, **data[name]}


def number_from(v, path=""):
    """A number from a value, JSON text or a JSON document plus a dotted path."""
    from .widgets import json_path
    if isinstance(v, (bytes, str)):
        s = v.decode() if isinstance(v, bytes) else v
        try:
            v = json.loads(s)
        except ValueError:
            v = s.strip()
    if path and isinstance(v, (dict, list)):
        v = json_path(v, path)
    if isinstance(v, dict):  # a Home Assistant state message
        v = v.get("state", v.get("value"))
    if isinstance(v, str):
        v = re.sub(r"[,_\s]", "", v)
    n = float(v)
    if not math.isfinite(n):
        raise ValueError("not a number")
    return n


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Odometer:
    """Digits on wheels: when the value changes each wheel rolls through the digits in between
    (forwards when the number goes up), the rightmost first like a mechanical counter."""

    def __init__(self, duration=1.5, stagger=0.08):
        self.duration, self.stagger = float(duration), stagger
        self.shown = None  # text drawn when settled
        self.prev = None
        self.up = True
        self.t0 = 0
        self._glyphs = {}

    def set(self, text, value, now):
        if self.shown is None:
            self.shown, self.value = text, value
            return
        if text != self.shown:
            self.prev, self.up = self.shown, value >= (self.value or 0)
            self.shown, self.value, self.t0 = text, value, now

    def active(self, now):
        return self.prev is not None and now - self.t0 < self.duration + self.stagger * len(self.shown or "")

    def glyph(self, ch, w, h, t):
        key = (ch, w, h, t)
        if key not in self._glyphs:
            g = Canvas(w, h)
            if ch.isdigit() or ch == "-":
                g.seg_digit(0, 0, ch, w, h, t)
            elif ch == ".":
                g.rect(0, h - t, t, t, fill=True)
            elif ch == ",":  # a dot with a tail
                g.rect(0, h - t - 1, t, t, fill=True)
                g.set(max(0, t - 2), h - 1)
            self._glyphs[key] = [(i % w, i // w) for i, v in enumerate(g.px) if v]
        return self._glyphs[key]

    def draw(self, c, x0, y0, w, h, t, gap, now):
        """Draw self.shown right-aligned in the box ending at x0 (wheel boxes w x h)."""
        if self.shown is None:
            return
        new, old = self.shown, self.prev if self.active(now) else None
        n = max(len(new), len(old or ""))
        new, old = new.rjust(n), (old or new).rjust(n)
        pitch = h + 3
        x = x0
        for k in range(n):  # from the right
            a, b = old[n - 1 - k], new[n - 1 - k]
            cw = t if b in ",." and a in ",. " else w
            x -= cw
            p = 1.0 if a == b else ease((now - self.t0 - k * self.stagger) / self.duration)
            if a == b or p >= 1:
                self.blit(c, x, y0, self.glyph(b, w, h, t), 0, y0, h)
            elif a.isdigit() and b.isdigit():
                da, db = int(a), int(b)
                steps = (db - da) % 10 if self.up else -((da - db) % 10)
                pos = da + steps * p
                base = math.floor(pos)
                frac = pos - base
                self.blit(c, x, y0, self.glyph(str(base % 10), w, h, t), -frac * pitch, y0, h)
                self.blit(c, x, y0, self.glyph(str((base + 1) % 10), w, h, t), (1 - frac) * pitch, y0, h)
            else:  # appearing or vanishing digit: slide
                d = -1 if self.up else 1
                self.blit(c, x, y0, self.glyph(a, w, h, t), d * p * pitch, y0, h)
                self.blit(c, x, y0, self.glyph(b, w, h, t), -d * (1 - p) * pitch, y0, h)
            x -= gap

    @staticmethod
    def blit(c, x, y, pts, dy, clip_y, clip_h):
        dy = int(round(dy))
        lo, hi = clip_y, clip_y + clip_h
        for px, py in pts:
            yy = y + py + dy
            if lo <= yy < hi:
                c.set(x + px, yy)


def fmt_count(v, decimals, separators):
    if decimals:
        s = f"{v:,.{int(decimals)}f}" if separators else f"{v:.{int(decimals)}f}"
    else:
        s = f"{round(v):,}" if separators else str(round(v))
    return s


_mqtt_subs = {}  # (host, port, user, topic) -> {"client", "value", "users"}
_mqtt_lock = threading.Lock()


def mqtt_watch(cfg, topic):
    """A shared MQTT subscription to one topic; returns its record (record["value"] = last payload)."""
    from mqtt import MqttClient
    key = (cfg.get("host"), cfg.get("port"), cfg.get("username"), topic)
    with _mqtt_lock:
        rec = _mqtt_subs.get(key)
        if rec is None:
            rec = {"value": None, "users": 0, "at": 0}

            prefix = (cfg.get("prefix") or "flipdot") + "/"

            def on_message(t, payload, rec=rec):
                # the client strips its own prefix from topics under it
                if t == topic or prefix + t == topic:
                    rec["value"], rec["at"] = payload, time.time()

            rec["client"] = MqttClient(dict(cfg), on_message, subscribe=[topic], client_id=f"flipdot-counter-{abs(hash(topic)) % 100000}")
            _mqtt_subs[key] = rec
        rec["users"] += 1
        return key, rec


def mqtt_unwatch(key):
    with _mqtt_lock:
        rec = _mqtt_subs.get(key)
        if rec:
            rec["users"] -= 1
            if rec["users"] <= 0:
                rec["client"].close()
                del _mqtt_subs[key]


@register
class Counter(Widget):
    id = "counter"
    title = "Counter"
    category = "Widgets"
    description = ("A big number whose digits roll like a mechanical counter when it changes: followers, stars, sales, "
                   "days without... From a JSON URL, an MQTT topic (e.g. a Home Assistant entity) or POST /api/counter/<name>.")
    params = [
        P("label", "text", "Followers"),
        P("source", "select", "manual", options=[["manual", "Set by the API (POST /api/counter/<name>)"],
                                                 ["json", "JSON URL"], ["mqtt", "MQTT topic"]]),
        P("name", "text", "followers", "Counter name (for the API)", show_if={"source": "manual"}),
        P("url", "text", "", "URL", show_if={"source": "json"}),
        P("path", "text", "", "Path to the number (e.g. data.0.count)", show_if={"source": ["json", "mqtt"]}),
        P("headers", "textarea", "", "Extra headers (JSON object, optional)", show_if={"source": "json"}),
        P("refresh_seconds", "number", 300, "Check every (seconds)", min=30, max=86400, show_if={"source": "json"}),
        P("topic", "text", "", "MQTT topic (e.g. homeassistant/sensor/followers/state)", show_if={"source": "mqtt"}),
        P("goal", "number", 0, "Goal (0 = no goal bar)", min=0),
        P("decimals", "number", 0, min=0, max=3),
        P("separators", "checkbox", False, "Thousands separators"),
        P("roll_seconds", "number", 1.5, "Roll time (s)", min=0.2, max=5, step=0.1),
    ]
    interval = 0.5

    @classmethod
    def needs_setup(cls, store, params):
        src = params.get("source", "manual")
        if src == "json" and not params.get("url"):
            return "a JSON URL"
        if src == "mqtt":
            if not params.get("topic"):
                return "an MQTT topic"
            mq = store.settings.get("mqtt") or {}
            if not (mq.get("enabled") and mq.get("host")):
                return "an MQTT broker (Settings, MQTT)"
        return None

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(30.0, float(self.p["refresh_seconds"]))
        self.odo = Odometer(self.p["roll_seconds"])
        self.value = None
        self.cache_key = f"counter-{self.p['source']}-{self.p['url'] or self.p['topic']}-{self.p['path']}"
        self._mqtt = None
        if self.p["source"] in ("json", "mqtt"):
            # the last value, so a restart doesn't roll
            hit = read_cache(ctx.store, self.cache_key + ("-doc" if self.p["source"] == "json" else ""))
            if hit:
                self.value = hit["data"]

    def fetch(self):
        headers = json.loads(self.p["headers"]) if self.p["headers"].strip() else None
        path = self.p["path"]
        value, _, _ = cached_get(self.ctx.store, self.cache_key + "-doc", self.p["url"], self.refresh - 1, headers=headers,
                                 shape=lambda d: number_from(d, path))
        return value

    def current(self, now):
        src = self.p["source"]
        if src == "manual":
            rec = counters(self.ctx.store).get(self.p["name"])
            return None if rec is None else float(rec["value"])
        if src == "json":
            if self.needs_setup(self.ctx.store, self.p):
                return None
            self.poll()
            return self.data if self.data is not None else self.value
        if src == "mqtt":
            if self._mqtt is None and not self.needs_setup(self.ctx.store, self.p):
                self._mqtt = mqtt_watch(self.ctx.store.settings["mqtt"], self.p["topic"])
            rec = self._mqtt[1] if self._mqtt else None
            if rec and rec["value"] is not None:
                try:
                    v = number_from(rec["value"], self.p["path"])
                except (ValueError, KeyError, IndexError, TypeError):
                    return self.value
                if v != self.value:
                    write_cache(self.ctx.store, self.cache_key, v)
                return v
            return self.value
        return None

    def render(self, c, now):
        setup = self.needs_setup(self.ctx.store, self.p)
        v = self.current(now)
        if v is not None:
            self.value = v
        label = asciify(self.p["label"])
        goal = float(self.p["goal"] or 0)
        top = 9 if label else 1
        bottom = 5 if goal > 0 else 1
        if label:
            fit_text(c, 0, label, w=c.w - (24 if goal > 0 else 0))
        if self.value is None:
            msg = f"Needs {setup}" if setup else ("No value yet" if self.p["source"] != "json" or not self.error else "Offline")
            c.text_center(top + (c.h - top - bottom) // 2 - 3, msg[:21], SMALL)
            return
        text = fmt_count(self.value, self.p["decimals"], self.p["separators"])
        self.odo.set(text, self.value, now)
        n_digits = sum(ch.isdigit() or ch == "-" for ch in text)
        n_marks = len(text) - n_digits
        h = c.h - top - bottom
        gap = 2
        w = min(int(h * 0.55), int((c.w - 2 - (len(text) - 1) * gap) / max(1, n_digits + 0.4 * n_marks)))
        w = max(3, w)
        t = max(1, round(min(w, h / 2) / 5))
        if n_marks:
            w = max(3, int((c.w - 2 - (len(text) - 1) * gap - n_marks * t) / max(1, n_digits)))
            w = min(w, int(h * 0.55))
        h = min(h, int(w * 1.9) + 1)
        width = n_digits * w + n_marks * t + (len(text) - 1) * gap
        x_right = (c.w + width) // 2
        y0 = top + (c.h - top - bottom - h) // 2
        self.odo.draw(c, x_right, y0, w, h, t, gap, now)
        if goal > 0:
            frac = max(0.0, min(1.0, self.value / goal))
            c.rect(0, c.h - 3, c.w, 3)
            c.rect(0, c.h - 3, round(c.w * frac), 3, fill=True)
            pct = f"{int(frac * 100)}%"
            c.text(c.w - SMALL.width(pct), 1, pct, SMALL)
        self.interval = 0.04 if self.odo.active(now) else (0.25 if self.p["source"] == "manual" else 0.5)

    def stop(self):
        super().stop()
        if self._mqtt:
            mqtt_unwatch(self._mqtt[0])
            self._mqtt = None

    def describe(self):
        return f"Counter: {self.p['label']}" if self.p["label"] else "Counter"


# ---- ISS tracker -------------------------------------------------------------------------------

MU, RE, J2, FLAT = 398600.4418, 6378.137, 1.08262668e-3, 1 / 298.257223563
ISS_NOW = "https://api.wheretheiss.at/v1/satellites/25544"
ISS_TLE = "https://api.wheretheiss.at/v1/satellites/25544/tles"
MAP_TOP, MAP_BOTTOM = 84.0, -60.0
_maps = {}


def world_map(w, h):
    """The bundled 84x42 land mask (Natural Earth), resampled to w x h, as a bytearray of 0/1."""
    if (w, h) not in _maps:
        rows = [r for r in (DATA / "world.txt").read_text().splitlines() if r and not r.startswith("#")]
        sh, sw = len(rows), len(rows[0])
        _maps[(w, h)] = bytearray(1 if rows[y * sh // h][x * sw // w] == "1" else 0 for y in range(h) for x in range(w))
    return _maps[(w, h)]


def map_xy(lat, lon, w, h):
    x = int((lon + 180) / 360 * w) % w
    y = int((MAP_TOP - lat) / (MAP_TOP - MAP_BOTTOM) * h)
    return x, y


def parse_tle(l1, l2):
    yy = int(l1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    epoch = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp() + (float(l1[20:32]) - 1) * 86400
    return {"epoch": epoch, "ndot": float(l1[33:43]), "i": math.radians(float(l2[8:16])),
            "raan": math.radians(float(l2[17:25])), "e": float("0." + l2[26:33].strip()),
            "argp": math.radians(float(l2[34:42])), "M": math.radians(float(l2[43:51])), "n": float(l2[52:63])}


def gmst(t):
    jd = t / 86400 + 2440587.5
    return math.radians((280.46061837 + 360.98564736629 * (jd - 2451545)) % 360)


def eci(el, t):
    """Position (km, inertial) from TLE mean elements with J2 secular drift and drag from ndot. Good to
    a degree or so within a few days of the epoch: plenty for a dot on an 84-dot map or a pass time."""
    m = (t - el["epoch"]) / 60
    n0 = el["n"] * 2 * math.pi / 1440
    a = (MU / (n0 / 60) ** 2) ** (1 / 3)
    e, i = el["e"], el["i"]
    k = 1.5 * J2 * (RE / (a * (1 - e * e))) ** 2 * n0
    raan = el["raan"] - k * math.cos(i) * m
    argp = el["argp"] + k * (2 - 2.5 * math.sin(i) ** 2) * m
    M = el["M"] + n0 * m + k * math.sqrt(1 - e * e) * (1 - 1.5 * math.sin(i) ** 2) * m + el["ndot"] * 2 * math.pi / 1440 ** 2 * m * m
    E = M
    for _ in range(6):
        E -= (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))
    u = argp + nu
    cr, sr, cu, su, ci = math.cos(raan), math.sin(raan), math.cos(u), math.sin(u), math.cos(i)
    return (r * (cr * cu - sr * su * ci), r * (sr * cu + cr * su * ci), r * su * math.sin(i))


def ecef(el, t):
    x, y, z = eci(el, t)
    g = gmst(t)
    return x * math.cos(g) + y * math.sin(g), -x * math.sin(g) + y * math.cos(g), z


def subpoint(el, t):
    x, y, z = ecef(el, t)
    lat = math.atan2(z, math.hypot(x, y))
    lat = math.atan(math.tan(lat) / (1 - FLAT) ** 2)
    return math.degrees(lat), math.degrees(math.atan2(y, x))


def observer(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    e2 = FLAT * (2 - FLAT)
    n = RE / math.sqrt(1 - e2 * math.sin(la) ** 2)
    pos = (n * math.cos(la) * math.cos(lo), n * math.cos(la) * math.sin(lo), n * (1 - e2) * math.sin(la))
    east = (-math.sin(lo), math.cos(lo), 0)
    north = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    return pos, east, north, up


def look(obs, p):
    """(elevation, azimuth) in degrees of ECEF point p from observer obs."""
    pos, east, north, up = obs
    d = [p[k] - pos[k] for k in range(3)]
    rng = math.sqrt(sum(v * v for v in d))
    u = sum(d[k] * up[k] for k in range(3)) / rng
    az = math.degrees(math.atan2(sum(d[k] * east[k] for k in range(3)), sum(d[k] * north[k] for k in range(3)))) % 360
    return math.degrees(math.asin(max(-1, min(1, u)))), az


def sun_eci(t):
    n = t / 86400 + 2440587.5 - 2451545
    L = math.radians((280.46 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam)


def sunlit(el, t):
    r = eci(el, t)
    s = sun_eci(t)
    d = sum(r[k] * s[k] for k in range(3))
    if d > 0:
        return True
    perp = math.sqrt(max(0.0, sum(v * v for v in r) - d * d))
    return perp > RE


def sun_elevation(obs, t):
    s = sun_eci(t)
    g = gmst(t)
    se = (s[0] * math.cos(g) + s[1] * math.sin(g), -s[0] * math.sin(g) + s[1] * math.cos(g), s[2])
    up = obs[3]
    return math.degrees(math.asin(sum(se[k] * up[k] for k in range(3))))


def compass(az):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((az + 22.5) % 360 // 45)]


def next_pass(el, lat, lon, start, hours=36, min_el=10.0, step=30):
    """The next pass above min_el degrees: {rise, top, set, max_el, rise_az, set_az, visible} or None."""
    obs = observer(lat, lon)

    def elev(t):
        return look(obs, ecef(el, t))[0]

    def edge(a, b, rising):
        for _ in range(12):  # bisect to about a second
            mid = (a + b) / 2
            if (elev(mid) >= min_el) == rising:
                b = mid
            else:
                a = mid
        return b if rising else a

    t, end = start, start + hours * 3600
    prev = elev(t)
    if prev >= min_el:  # already up: find when this pass started
        back = t
        while back > t - 1200 and elev(back - step) >= min_el:
            back -= step
        rise = edge(back - step, back, True)
    else:
        rise = None
        while t < end:
            t += step
            e = elev(t)
            if e >= min_el:
                rise = edge(t - step, t, True)
                break
        if rise is None:
            return None
    t = rise
    best, best_t = elev(t), t
    while elev(t + step) >= min_el and t < rise + 1800:
        t += step
        e = elev(t)
        if e > best:
            best, best_t = e, t
    setting = edge(t, t + step, False)
    for d in (-step / 2, step / 2):  # refine the top a little
        e = elev(best_t + d)
        if e > best:
            best, best_t = e, best_t + d
    visible = sunlit(el, best_t) and sun_elevation(obs, best_t) < -6
    return {"rise": rise, "top": best_t, "set": setting, "max_el": round(best),
            "rise_az": look(obs, ecef(el, rise))[1], "set_az": look(obs, ecef(el, setting))[1], "visible": visible}


@register
class ISS(Widget):
    id = "iss"
    title = "ISS tracker"
    category = "Widgets"
    description = ("Where the International Space Station is on a small world map, its ground track, and when it next "
                   "passes over you (from wheretheiss.at; pass times are worked out on the board).")
    params = [
        P("view", "select", "both", options=[["both", "Map, then details"], ["map", "Map only"], ["info", "Details only"]]),
        P("location", "text", "", "Your location for passes (blank = Settings location)"),
        P("track", "checkbox", True, "Show the ground track"),
        P("map_seconds", "number", 20, min=3, max=600, show_if={"view": "both"}),
        P("info_seconds", "number", 8, min=3, max=600, show_if={"view": "both"}),
    ]
    interval = 1.0
    refresh = 20

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.el = None
        self.tle_key = None
        self.where = None
        self.passes = None  # (key, pass)
        self._track = (None, [])

    def here(self):
        if self.p["location"].strip():
            from .widgets import geocode
            hit = cached_geocode(self.ctx.store, self.p["location"].strip(), geocode)
            return hit
        w = self.ctx.store.settings.get("weather") or {}
        if w.get("lat") is not None:
            return {"lat": float(w["lat"]), "lon": float(w["lon"]), "name": w.get("name", "")}
        return None

    def fetch(self):
        now = time.time()
        out = dict(self.data or {})
        try:
            tle, saved, stale = cached_get(self.ctx.store, "iss-tle", ISS_TLE, 6 * 3600)
            self.el = parse_tle(tle["line1"], tle["line2"])
            out["tle_age"] = now - self.el["epoch"]
        except Exception as e:
            log.info("iss: no TLE (%s)", e)
        try:
            pos = fetch_json(ISS_NOW)
            out["pos"] = {"lat": float(pos["latitude"]), "lon": float(pos["longitude"]), "alt": float(pos["altitude"]),
                          "vel": float(pos["velocity"]), "at": float(pos.get("timestamp") or now), "visibility": pos.get("visibility")}
            out["offline"] = False
        except Exception as e:
            out["offline"] = True
            if not self.el:
                raise
            log.info("iss: position from the API failed (%s); using the orbit", e)
        try:
            self.where = self.here()
        except Exception as e:
            log.info("iss: location: %s", e)
        if self.el and self.where:
            key = (self.where["lat"], self.where["lon"], self.el["epoch"])
            p = self.passes[1] if self.passes and self.passes[0] == key else None
            if p is None or p["set"] < now:
                p = next_pass(self.el, self.where["lat"], self.where["lon"], now)
                self.passes = (key, p)
        return out

    def position(self, now):
        d = self.data or {}
        pos = d.get("pos")
        if self.el and (not pos or now - pos["at"] > 45):
            lat, lon = subpoint(self.el, now)
            return lat, lon
        if pos:
            return pos["lat"], pos["lon"]
        return None

    def track(self, now, w):
        """Ground track points (every 2 minutes, 45 minutes back to 50 ahead), recomputed each minute."""
        key = int(now // 60)
        if self._track[0] != key and self.el:
            pts = [subpoint(self.el, now + m * 60) for m in range(-44, 51, 2)]
            self._track = (key, pts)
        return self._track[1]

    def render(self, c, now):
        self.poll()
        if self.data is None and self.el is None:
            c.px[:] = world_map(c.w, c.h)
            msg = "OFFLINE" if self.error else "LOADING"
            c.rect(c.w // 2 - 16, c.h // 2 - 4, 32, 9, v=0, fill=True)
            c.text_center(c.h // 2 - 2, msg, SMALL)
            return
        p = self.passes[1] if self.passes else None
        if p and p["rise"] - 600 <= now <= p["set"]:  # pass alert takes over
            self.alert(c, now, p)
            return
        view = self.p["view"]
        if view == "both":
            ms, info = float(self.p["map_seconds"]), float(self.p["info_seconds"])
            view = "map" if (now - self.started) % (ms + info) < ms else "info"
        if view == "map":
            self.draw_map(c, now)
        else:
            self.draw_info(c, now, p)

    def draw_map(self, c, now):
        c.px[:] = world_map(c.w, c.h)
        if self.p["track"]:
            for i, (la, lo) in enumerate(self.track(now, c.w)):
                x, y = map_xy(la, lo, c.w, c.h)
                c.set(x, y, 1 - c.get(x, y))
        if self.where:
            x, y = map_xy(self.where["lat"], self.where["lon"], c.w, c.h)
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                c.set(x + dx, y + dy, 1 - c.get(x + dx, y + dy))
        pos = self.position(now)
        if pos:
            x, y = map_xy(pos[0], pos[1], c.w, c.h)
            c.rect(x - 4, y - 2, 9, 5, v=0, fill=True)  # a dark margin so it stands out on land
            c.rect(x - 1, y - 1, 3, 3, fill=True)  # the station: a body with solar panels
            c.line(x - 3, y, x + 3, y)
            c.set(x - 3, y - 1), c.set(x - 3, y + 1), c.set(x + 3, y - 1), c.set(x + 3, y + 1)
        if (self.data or {}).get("offline"):
            c.rect(0, c.h - 6, 31, 6, v=0, fill=True)
            c.text(0, c.h - 5, "OFFLINE", SMALL)

    def draw_info(self, c, now, p):
        d = self.data or {}
        pos = self.position(now)
        c.text(0, 0, "ISS", NORMAL)
        if pos:
            la, lo = pos
            where = f"{abs(la):.0f}{'N' if la >= 0 else 'S'} {abs(lo):.0f}{'E' if lo >= 0 else 'W'}"
            c.text(c.w - NORMAL.width(where), 0, where, NORMAL)
        alt = d.get("pos", {}).get("alt")
        vel = d.get("pos", {}).get("vel")
        if alt:
            c.text(0, 9, f"{alt:.0f} KM  {vel:,.0f} KM/H", SMALL)
        if not self.where:
            c.text(0, 19, "SET A LOCATION IN", SMALL)
            c.text(0, 26, "SETTINGS FOR PASSES", SMALL)
            return
        if not p:
            c.text(0, 19, "NO PASS ABOVE 10\u00b0", SMALL)
            c.text(0, 26, "IN THE NEXT DAY", SMALL)
            return
        t = dt.datetime.fromtimestamp(p["rise"])
        day = "TODAY" if t.date() == dt.date.fromtimestamp(now) else t.strftime("%a").upper()
        when = f"{day} {t:%H:%M}"
        c.text(0, 19, "NEXT", SMALL)
        c.text(c.w - NORMAL.width(when), 17, when, NORMAL)
        c.text(0, 27, f"UP TO {p['max_el']}\u00b0 {compass(p['rise_az'])} TO {compass(p['set_az'])}", SMALL)
        c.text(0, 34, "VISIBLE" if p["visible"] else "NOT VISIBLE", SMALL)

    def alert(self, c, now, p):
        """Before and during a pass over you: where to look."""
        obs = observer(self.where["lat"], self.where["lon"])
        if now < p["rise"]:
            mins = max(1, math.ceil((p["rise"] - now) / 60))
            c.text_center(1, f"ISS IN {mins} MIN", NORMAL)
            c.text_center(12, f"LOOK {compass(p['rise_az'])}", NORMAL)
            c.text_center(23, f"UP TO {p['max_el']}°", NORMAL)
        else:
            e, az = look(obs, ecef(self.el, now))
            c.text_center(1, "ISS OVERHEAD", NORMAL)
            c.text_center(12, f"LOOK {compass(az)}", NORMAL)
            c.text_center(23, f"{max(0, round(e))}° UP", NORMAL)
        c.text_center(35, "VISIBLE" if p["visible"] else "PROBABLY NOT VISIBLE", SMALL)

    def describe(self):
        return "ISS tracker"


def cached_geocode(store, name, geocode):
    hit = read_cache(store, "geocode-" + name.lower())
    if hit:
        return hit["data"]
    hits = geocode(name)
    if not hits:
        raise ValueError(f"location {name!r} not found")
    write_cache(store, "geocode-" + name.lower(), hits[0])
    return hits[0]
