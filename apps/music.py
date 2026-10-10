"""Now playing from a Music Assistant server (https://music-assistant.io).

Talks to the server's HTTP API: POST {url}/api {"command": "players/all", "args": {}} with an
optional "Authorization: Bearer <token>" (needed on servers that have login enabled; create a
long-lived token in Music Assistant under Settings > Security).
"""

import json
import time
import urllib.parse
import urllib.request

from canvas import Canvas
from fonts import NORMAL, SMALL

from .base import P, UA, Widget, http_get, register
from .media import convert_image
from .text import resolve_font
from transitions import NAMES as TRANSITIONS


def _cfg(store):
    return store.settings.get("music_assistant", {})


def ma_command(store, command, args=None, timeout=8):
    cfg = _cfg(store)
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise ValueError("Set the Music Assistant URL in Settings")
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    body = json.dumps({"message_id": "flipdot", "command": command, "args": args or {}}).encode()
    req = urllib.request.Request(url + "/api", body, headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Music Assistant needs a token (Settings > Security in Music Assistant)") from None
        raise ValueError(f"Music Assistant error {e.code}: {e.read()[:200].decode(errors='replace')}") from None


def list_players(store):
    players = ma_command(store, "players/all")
    return [{
        "id": p.get("player_id"), "name": p.get("display_name") or p.get("name") or p.get("player_id"),
        "state": p.get("playback_state") or p.get("state"), "available": p.get("available", True),
        "title": (p.get("current_media") or {}).get("title"),
    } for p in players if not p.get("hide_in_ui")]


def _elapsed(obj, playing):
    e = obj.get("elapsed_time")
    if e is None:
        return None
    if playing and obj.get("elapsed_time_last_updated"):
        e += max(0, time.time() - obj["elapsed_time_last_updated"])
    return e


@register
class Music(Widget):
    id = "music"
    title = "Now playing (Music Assistant)"
    category = "Widgets"
    description = "Album art, song and artist from your Music Assistant server."
    params = [
        P("player", "text", "", "Player name or id (blank = Settings default, or whatever is playing)"),
        P("layout", "select", "full", "Layout", options=[
            ["full", "Full (album art, title, artist, progress)"],
            ["line", "Single line of text (great as a layer)"]]),
        P("position", "select", "top", "Single line: where", options=[["top", "Top"], ["bottom", "Bottom"]]),
        P("font", "font", "normal", "Single line: font"),
        P("scale", "number", 1, "Single line: scale (pixel fonts)", min=1, max=4),
        P("font_size", "number", 9, "Single line: size in dots (other fonts)", min=5, max=20),
        P("line_mode", "select", "always", "Single line: when to show it", options=[
            ["always", "Always (keeps scrolling)"],
            ["once", "Once per song (scrolls across when the song changes, then disappears)"]]),
        P("progress", "checkbox", True, "Single line: thin progress line under the text"),
        P("scroll_speed", "number", 20, "Single line: scroll speed (dots/sec)", min=4, max=60),
        P("art", "checkbox", True, "Show album art"),
        P("dither", "checkbox", True, "Dither album art"),
        P("idle", "select", "text", "When nothing is playing",
          options=[["text", "Say 'Nothing playing'"], ["blank", "Stay blank"], ["last", "Keep the last song"]]),
        P("song_transition", "select", "dissolve", "Whole-board transition when the song changes",
          options=[["none", "None"]] + [[t, t] for t in TRANSITIONS if t != "none"]),
        P("song_transition_time", "number", 1.5, "Song transition time (s)", min=0.2, max=10, step=0.1),
    ]
    refresh = 3
    interval = 0.1

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.line = self.p["layout"] == "line"
        self.once = self.line and self.p.get("line_mode") == "once"
        self.hidden = False          # nothing on screen right now (layers then show what's underneath)
        self.scroll_track = None     # the song the one-time scroll is for
        self.scroll_start = None
        if self.line:
            self.p["art"] = False  # no album art in the one-line layout
            self.interval = 0.05
        self.art_url = None
        self.art = None
        self.last = None
        self.track = None
        self.request_transition = None

    def fetch(self):
        players = ma_command(self.ctx.store, "players/all")
        want = (self.p["player"] or _cfg(self.ctx.store).get("player") or "").strip().lower()

        def playing(p):
            return (p.get("playback_state") or p.get("state")) == "playing"

        candidates = [p for p in players if p.get("available", True) and p.get("current_media")]
        if want:
            candidates = [p for p in candidates if want in (str(p.get("player_id", "")).lower(),
                                                              str(p.get("display_name") or p.get("name") or "").lower())]
        candidates.sort(key=lambda p: not playing(p))  # playing players first
        if not candidates:
            return {"playing": False}
        p = candidates[0]
        media = p["current_media"]
        is_playing = playing(p)
        image = media.get("image_url")
        if image and image.startswith("/"):
            image = (_cfg(self.ctx.store).get("url") or "").rstrip("/") + image
        if self.p["art"] and image and image != self.art_url:
            size = self.h
            try:
                data = http_get(image, ttl=3600, raw=True)
                frames = convert_image(data, size, size, mode="crop", dither=self.p["dither"])
                self.art = Canvas.from_rows(frames[0]["rows"])
            except Exception:
                self.art = None
            self.art_url = image
        elapsed = _elapsed(media, is_playing)
        if elapsed is None:
            elapsed = _elapsed(p, is_playing)
        return {
            "playing": is_playing, "title": media.get("title") or media.get("uri", ""),
            "artist": media.get("artist") or "", "album": media.get("album") or "",
            "player": p.get("display_name") or p.get("name") or "",
            "elapsed": elapsed or 0, "duration": media.get("duration") or 0, "at": time.time(),
        }

    def render(self, c, now):
        if self.line and self.data is None:
            self.poll()
            if self.error:
                self.draw_line(c, now, "Music Assistant: " + self.error, None)
            return
        return super().render(c, now)

    def draw_line(self, c, now, text, d):
        """One line of text (scrolling if it doesn't fit) along the top or bottom, plus a progress line."""
        font, scale = resolve_font(self.p, self.p)
        th = font.height * scale
        bar = 2 if self.p["progress"] and d and d.get("duration") else 0
        y = 0 if self.p["position"] == "top" else c.h - th - bar
        tw = font.width(text) * scale
        if self.once:
            # scroll across once, from just off the right edge to just off the left, then hide
            if self.scroll_start is None:
                self.hidden = True
                return
            x = c.w - int((now - self.scroll_start) * float(self.p["scroll_speed"]))
            if x + tw < 0:
                self.scroll_start = None
                self.hidden = True
                return
            self.hidden = False
            c.text(x, y, text, font, scale=scale)
            if bar:
                pos = d["elapsed"] + ((time.time() - d["at"]) if d["playing"] else 0)
                c.line(0, y + th + 1, round((c.w - 1) * max(0.0, min(1.0, pos / d["duration"]))), y + th + 1)
            return
        self.hidden = False
        if tw <= c.w:
            c.text((c.w - tw) // 2, y, text, font, scale=scale)
        else:
            gap = 16 * scale
            off = int((now - self.started) * float(self.p["scroll_speed"])) % (tw + gap)
            c.text(-off, y, text, font, scale=scale)
            c.text(-off + tw + gap, y, text, font, scale=scale)
        if bar:
            pos = d["elapsed"] + ((time.time() - d["at"]) if d["playing"] else 0)
            c.line(0, y + th + 1, round((c.w - 1) * max(0.0, min(1.0, pos / d["duration"]))), y + th + 1)

    def draw(self, c, now):
        d = self.data
        if d.get("playing"):
            track = (d["title"], d["artist"], d["album"])
            if self.track is not None and track != self.track and self.p["song_transition"] != "none":
                # new song: ask the runner to transition the whole board into this frame
                self.request_transition = (self.p["song_transition"], float(self.p["song_transition_time"]))
            self.track = track
        if self.line and self.once:
            # start the one-time scroll whenever a new song starts playing
            if d.get("playing"):
                track = (d["title"], d["artist"])
                if track != self.scroll_track:
                    self.scroll_track = track
                    self.scroll_start = now
                self.last = d
            shown = d if d.get("playing") else self.last
            if shown is None:
                self.hidden = True
                return
            text = shown["title"] + (f"  -  {shown['artist']}" if shown["artist"] else "")
            return self.draw_line(c, now, text, shown)
        if self.line:
            if not d.get("playing"):
                if self.p["idle"] == "last" and self.last:
                    d = self.last
                elif self.p["idle"] == "text":
                    return self.draw_line(c, now, "Nothing playing", None)
                else:
                    return
            else:
                self.last = d
            text = d["title"] + (f"  -  {d['artist']}" if d["artist"] else "")
            return self.draw_line(c, now, text, d)
        if not d.get("playing"):
            mode = self.p["idle"]
            if mode == "last" and self.last:
                d = self.last
            else:
                if mode == "text":
                    c.text_center(c.h // 2 - 9, "Nothing", NORMAL)
                    c.text_center(c.h // 2 + 2, "playing", NORMAL)
                return
        else:
            self.last = d
        art = self.art if self.p["art"] else None
        if art:
            c.blit(art, 0, 0, transparent=False)
        x0 = art.w + 2 if art else 0
        w = c.w - x0
        t = now - self.started

        def marquee(y, text, font):
            tw = font.width(text)
            panel = Canvas(w, font.height)
            if tw <= w:
                panel.text((w - tw) // 2 if not art else 0, 0, text, font)
            else:
                off = int(t * 10) % (tw + 12)
                panel.text(-off, 0, text, font)
                panel.text(-off + tw + 12, 0, text, font)
            c.blit(panel, x0, y)

        marquee(4, d["title"], NORMAL)
        marquee(15, d["artist"], SMALL)
        if not art and d.get("album"):
            marquee(23, d["album"], SMALL)
        if d["duration"]:
            pos = d["elapsed"] + ((time.time() - d["at"]) if d["playing"] else 0)
            progress = max(0.0, min(1.0, pos / d["duration"]))
            c.rect(x0, c.h - 10, w, 5)
            c.rect(x0 + 1, c.h - 9, round((w - 2) * progress), 3, fill=True)

    def describe(self):
        if self.data and self.data.get("playing"):
            return f"Now playing: {self.data['title']}"
        return "Now playing (Music Assistant)"
