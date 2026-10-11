# Plugins

Put a `.py` file, or a folder with an `__init__.py`, in this folder to add your own apps. Each app shows up
in **Apps → Plugins** with a settings form, and works everywhere a built-in app does: on its own, as a layer,
in a playlist, in the schedule and in automations.

Plugins load when the server starts and when you press **Reload plugins** in Settings → Plugins. Plugins
installed from that page (an uploaded `.py` or `.zip`, or a URL) are kept in `data/plugins/` instead, so
they're saved in `storage.db` and come back with a backup.

**Security:** a plugin is ordinary Python running inside the server, with all its privileges: it can read
your settings and passwords, change any file the server can, and reach your network. Only install code you
trust, and read it first. Plugins installed from the web page stay off until you've read the code and
confirmed that you trust it; if the code changes, you're asked again. Files you copy into this folder
yourself are trusted and on by default (you can turn them off in Settings → Plugins).

## Writing one

`example_hello.py` is a complete, commented example: a scrolling ticker and a widget that fetches JSON from a
URL. The short version:

```python
from apps.base import App, P, register

@register
class Hello(App):
    id = "my_hello"            # unique; stored in playlists, so keep it once you use it
    title = "Hello"
    params = [P("text", "text", "Hello", "Text")]
    interval = 0.5             # seconds between frames

    def render(self, c, now):  # c: a blank 84×42 1-bit Canvas; return False to finish
        c.text_center(17, self.p["text"])
```

- `App` subclasses draw with `render(c, now)`; `Widget` subclasses fetch data in a background thread
  (`fetch()`, every `refresh` seconds) and draw it with `draw(c, now)`. `http_get(url, ttl)` caches requests.
- Param types: `text`, `textarea`, `number` (`min`, `max`, `step`), `select` (`options=[[value, label]]`),
  `checkbox`, `datetime`, `font`. Values arrive in `self.p`.
- Drawing: `c.set(x, y)`, `c.line`, `c.rect`, `c.circle`, `c.text(x, y, s, font)`, `c.text_center(y, s)`,
  `c.seg_text(x, y, "12:34")`; fonts are in `fonts.py` (`NORMAL` 5×7, `SMALL` 3×5).
- Games: subclass `apps.games.Game` and set `interactive = True` to get controller input.
- A folder plugin can import its own modules with relative imports (`from . import helpers`).

## When something goes wrong

- A plugin that fails to import is skipped; Settings → Plugins shows the traceback. Fix the file and press
  **Reload plugins**.
- A crash while drawing shows "Plugin error" on the board instead of the picture. After 5 crashes in a row
  the plugin is turned off and you get a notification; turn it back on once it's fixed.
- Code at the top level of a plugin should only define things. A plugin that takes more than 15 seconds to
  import is skipped (do slow work in `fetch()`).
- App ids must not clash with built-in apps or other plugins; a clashing app isn't loaded.

## Installing from the web page

Settings → Plugins → **Install a plugin** takes:

- a `.py` file or a `.zip` (a package folder with `__init__.py`, or a single `.py`, at the top or inside
  one folder, like a GitHub download);
- a URL: a raw `.py` file, a GitHub file page (`…/blob/…`), a repository (`https://github.com/you/repo`) or a
  folder in one (`…/tree/main/my_plugin`).

## Pixlet apps

Settings → Plugins also installs apps from the Tidbyt/Tronbyt community catalogue
(github.com/tronbyt/apps, Apache-2.0). They need the Pixlet program: run `scripts/setup-pixlet.sh` once.
See the README's "Plugins and Pixlet apps" section.
