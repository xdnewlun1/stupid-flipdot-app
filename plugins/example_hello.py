"""Example plugin: a scrolling hello ticker and a number fetched from a web address.

Copy this file, rename it (lowercase letters, digits and _), change the app ids and titles, and press
Reload plugins in Settings → Plugins. Every app here shows up in Apps under Plugins, with a form built
from its `params`, and works like a built-in app: on its own, as a layer, in a playlist or the schedule.
"""

__version__ = "1.0"

import time

# Everything a plugin needs comes from the app framework (apps/base.py), the same as built-in apps.
from apps.base import App, NeedsSetup, P, Widget, http_get, register
from canvas import wrap_text
from fonts import NORMAL, SMALL


@register  # adds the class to the app list; its `id` must be unique across all apps
class HelloTicker(App):
    id = "hello_ticker"  # stored in playlists and layer sets: don't change it once you use the app
    title = "Hello ticker"
    description = "Scrolls a line of text across the board (an example plugin)."
    # One P() per setting: P(name, type, default, label, **extra). Types: text, textarea, number (min, max,
    # step), select (options=[[value, label], ...]), checkbox, datetime, font. The values arrive in self.p.
    params = [
        P("text", "text", "Hello from a plugin!", "Text"),
        P("speed", "number", 20, "Speed (dots per second)", min=2, max=60),
        P("invert", "checkbox", False, "Light background"),
    ]
    interval = 0.05  # seconds between frames: the runner calls render() this often
    pausable = True  # Pause freezes it (render() then gets a clock that stands still)

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)  # sets self.p (the params), self.w and self.h (84 x 42), self.started
        self.text_w = NORMAL.width(self.p["text"])

    def render(self, c, now):
        """Draw one frame on `c` (a blank 1-bit Canvas, c.w x c.h). `now` is time.time() or the paused clock.
        Return False when the app is finished; anything else keeps it on the board."""
        if self.p["invert"]:
            c.clear(1)
        span = c.w + self.text_w
        x = c.w - int((now - self.started) * float(self.p["speed"])) % span
        c.text(x, (c.h - NORMAL.height) // 2, self.p["text"], NORMAL, v=0 if self.p["invert"] else 1)

    def describe(self):
        return f"{self.title}: {self.p['text'][:30]}"  # shown as "what's on the board"


def dig(data, path):
    """data["a"]["b"][0] for the path "a.b.0"."""
    for part in str(path).split("."):
        if part == "":
            continue
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


@register
class UrlNumber(Widget):
    """A Widget fetches in a background thread every `refresh` seconds, so a slow network never holds up
    the board: fetch() returns the data (or raises), and draw() shows it. Until the first fetch works the
    Widget base class shows "Loading..." or the error for you."""

    id = "url_number"
    title = "Number from a URL"
    description = "Fetches JSON from a web address and shows one value in big digits (an example plugin)."
    params = [
        P("url", "text", "https://api.github.com/repos/python/cpython", "JSON address (URL)"),
        P("path", "text", "stargazers_count", "Value to show (e.g. data.items.0.count)"),
        P("label", "text", "CPython stars", "Label"),
        P("every", "number", 600, "Check every (seconds)", min=30, max=86400),
    ]
    interval = 1.0  # the picture only changes when new data arrives

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.refresh = max(30, float(self.p["every"]))

    def fetch(self):
        if not str(self.p["url"]).strip():
            raise NeedsSetup("Set the JSON address in this app's settings.")  # shown as a calm hint, not an error
        # http_get caches by URL (ttl seconds), so several copies of the app share one request
        data = http_get(self.p["url"], ttl=self.refresh - 1)
        return {"value": dig(data, self.p["path"]), "at": time.time()}

    def draw(self, c, now):
        value = self.data["value"]
        text = f"{value:,}" if isinstance(value, int) else str(value)
        if text.replace(",", "").isdigit() and len(text.replace(",", "")) <= 5:
            digits = text.replace(",", "")
            w = c.seg_width(digits, w=12, t=3)
            c.seg_text((c.w - w) // 2, 3, digits, w=12, h=25, t=3)  # big seven-segment digits
        else:
            for i, line in enumerate(wrap_text(text, NORMAL, c.w - 2)[:3]):
                c.text_center(4 + i * 9, line)
        c.text_center(c.h - 7, str(self.p["label"])[:20].upper(), SMALL)
