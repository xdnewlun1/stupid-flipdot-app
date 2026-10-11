"""Python plugins (plugins.py), Pixlet apps (pixlet.py, apps/pixlet_apps.py) and their routes (plugins_api.py).

No third-party requests: URL installs and the Pixlet catalogue are served by a local HTTP server, and
Pixlet itself is replaced by a small fake that writes a two-frame GIF.
"""

import base64
import http.client
import http.server
import io
import json
import logging
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import access  # noqa: E402
import apps  # noqa: E402
import flipdot  # noqa: E402
import pixlet  # noqa: E402
import plugins  # noqa: E402
import runner as runner_mod  # noqa: E402
import server  # noqa: E402
from apps import base, pixlet_apps  # noqa: E402
from canvas import Canvas  # noqa: E402
from store import Store  # noqa: E402
from test_board import config  # noqa: E402
from ws import Hub  # noqa: E402

GOOD = '''"""A test plugin."""
__version__ = "0.1"
from apps.base import App, P, register

@register
class Dots(App):
    id = "t_dots"
    title = "Test dots"
    params = [P("n", "number", 3)]

    def render(self, c, now):
        for i in range(int(self.p["n"])):
            c.set(i, 0)
'''

CRASHY = '''from apps.base import App, register

@register
class Crash(App):
    id = "t_crash"
    title = "Crashes"

    def render(self, c, now):
        raise RuntimeError("boom")
'''

BAD_INIT = '''from apps.base import App, register

@register
class BadInit(App):
    id = "t_badinit"
    title = "Bad init"

    def __init__(self, ctx, **p):
        raise KeyError("missing")

    def render(self, c, now):
        pass
'''

STEALS_CLOCK = '''from apps.base import App, register

@register
class Clock(App):
    id = "clock"
    title = "Not the clock"

    def render(self, c, now):
        pass
'''


class Ctx:
    width, height = 84, 42

    def __init__(self, store):
        self.store = store


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class Served:
    """A local HTTP server for a dict of path -> bytes (URL installs, the catalogue)."""

    def __init__(self, files, port=0):
        files_ref = files

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = files_ref.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class PluginTestCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "plugins"
        self.folder.mkdir()
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.notes = []
        self.mgr = plugins.Manager(self.store, folder=self.folder, notify=lambda **kw: self.notes.append(kw))

    def tearDown(self):
        for name in list(self.mgr.plugins):
            self.mgr._unload(name)
        self.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def write(self, name, text):
        (self.folder / name).write_text(text)

    def info(self, name):
        return next(p for p in self.mgr.list()["plugins"] if p["name"] == name)


class Loader(PluginTestCase):
    def test_good_plugin_loads_as_plugins_category(self):
        self.write("good.py", GOOD)
        self.mgr.load_all()
        self.assertIn("t_dots", apps.REGISTRY)
        cls = apps.REGISTRY["t_dots"]
        self.assertEqual(cls.category, "Plugins")
        self.assertIn("t_dots", [a["id"] for a in apps.catalog() if a["category"] == "Plugins"])
        app = apps.create(Ctx(self.store), {"app": "t_dots", "params": {"n": 5}})
        c = Canvas(84, 42)
        app.render(c, 0)
        self.assertEqual(sum(c.px), 5)
        i = self.info("good")
        self.assertEqual((i["version"], i["description"], i["source"]), ("0.1", "A test plugin.", "folder"))

    def test_broken_plugins_are_isolated(self):
        self.write("good.py", GOOD)
        self.write("syntax.py", "def broken(:\n")
        self.write("raises.py", "raise RuntimeError('import time failure')\n")
        self.write("exits.py", "import sys\nsys.exit(3)\n")
        self.write("missing.py", "import no_such_module_here\n")
        self.write("nothing.py", "x = 1\n")
        self.write("stealer.py", STEALS_CLOCK)
        builtin_clock = apps.REGISTRY["clock"]
        self.mgr.load_all()
        self.assertIn("t_dots", apps.REGISTRY)
        self.assertIs(apps.REGISTRY["clock"], builtin_clock, "a plugin can't replace a built-in app")
        self.assertIn("SyntaxError", self.info("syntax")["error"])
        self.assertIn("import time failure", self.info("raises")["error"])
        self.assertIn("SystemExit", self.info("exits")["error"])
        self.assertIn("No module named", self.info("missing")["error"])
        self.assertIn("doesn't register any apps", self.info("nothing")["error"])
        self.assertIn("already used by a built-in app", self.info("stealer")["error"])
        self.assertIsNone(self.info("good")["error"])

    def test_slow_import_is_skipped(self):
        self.write("slow.py", "import time\ntime.sleep(3)\n" + GOOD.replace("t_dots", "t_slow"))
        old = plugins.IMPORT_TIMEOUT
        plugins.IMPORT_TIMEOUT = 0.3
        try:
            self.mgr.load_all()
        finally:
            plugins.IMPORT_TIMEOUT = old
        self.assertIn("longer than", self.info("slow")["error"])
        import time
        time.sleep(3.2)
        self.assertNotIn("t_slow", apps.REGISTRY, "an abandoned import's apps don't appear later")

    def test_package_plugin_with_relative_import(self):
        pkg = self.folder / "pack"
        pkg.mkdir()
        (pkg / "helpers.py").write_text("N = 4\n")
        (pkg / "__init__.py").write_text(GOOD.replace('P("n", "number", 3)', 'P("n", "number", N)')
                                         .replace("from apps.base", "from .helpers import N\nfrom apps.base"))
        self.mgr.load_all()
        self.assertIsNone(self.info("pack")["error"])
        self.assertEqual(apps.REGISTRY["t_dots"].params[0]["default"], 4)

    def test_disable_enable_and_reload(self):
        self.write("good.py", GOOD)
        self.mgr.load_all()
        app = apps.create(Ctx(self.store), {"app": "t_dots"})
        self.mgr.set_enabled("good", False)
        self.assertNotIn("t_dots", apps.REGISTRY)
        self.assertIs(app.render(Canvas(84, 42), 0), False, "an app already running stops when its plugin is turned off")
        self.mgr = plugins.Manager(self.store, folder=self.folder)  # the choice is saved
        self.mgr.load_all()
        self.assertNotIn("t_dots", apps.REGISTRY)
        self.mgr.set_enabled("good", True)
        self.assertIn("t_dots", apps.REGISTRY)
        self.write("good.py", GOOD.replace('P("n", "number", 3)', 'P("n", "number", 7)'))
        self.mgr.reload("good")
        self.assertEqual(apps.REGISTRY["t_dots"].params[0]["default"], 7, "reload picks up the new code")

    def test_example_plugin(self):
        mgr = plugins.Manager(self.store, folder=plugins.FOLDER)
        try:
            mgr.load_all()
            ex = next(p for p in mgr.list()["plugins"] if p["name"] == "example_hello")
            self.assertIsNone(ex["error"])
            self.assertEqual({a["id"] for a in ex["apps"]}, {"hello_ticker", "url_number"})
            app = apps.create(Ctx(self.store), {"app": "hello_ticker", "params": {"text": "HI"}})
            c = Canvas(84, 42)
            app.render(c, app.started + 2)
            self.assertGreater(sum(c.px), 5)
            # the data widget, fetching from a local server
            srv = Served({"/n.json": b'{"data": {"count": 1234}}'})
            try:
                w = apps.create(Ctx(self.store), {"app": "url_number", "params": {"url": srv.url + "/n.json", "path": "data.count"}})
                w._do_fetch()
                self.assertEqual(w.data["value"], 1234)
                c = Canvas(84, 42)
                w.render(c, 0)
                self.assertGreater(sum(c.px), 50)
            finally:
                srv.close()
        finally:
            for n in list(mgr.plugins):
                mgr._unload(n)


class Crashes(PluginTestCase):
    def test_render_failure_auto_disables(self):
        self.write("crashy.py", CRASHY)
        self.mgr.load_all()
        app = apps.create(Ctx(self.store), {"app": "t_crash"})
        c = Canvas(84, 42)
        results = [app.render(c, 0) for _ in range(plugins.MAX_FAILS)]
        self.assertEqual(results[:-1], [None] * (plugins.MAX_FAILS - 1), "a crash doesn't end the app straight away")
        self.assertGreater(sum(c.px), 20, "it says Plugin error on the board")
        self.assertIn("boom", app.error)
        self.assertIs(results[-1], False, "after MAX_FAILS in a row the app ends")
        self.assertNotIn("t_crash", apps.REGISTRY)
        i = self.info("crashy")
        self.assertFalse(i["enabled"])
        self.assertIn("crashed", i["reason"])
        self.assertIn("RuntimeError: boom", i["error"])
        import time
        for _ in range(50):
            if self.notes:
                break
            time.sleep(0.02)
        self.assertEqual(self.notes[0]["priority"], "low")

    def test_success_resets_the_count(self):
        self.write("flaky.py", CRASHY.replace('raise RuntimeError("boom")', 'self.n = getattr(self, "n", 0) + 1\n        if self.n % 3:\n            raise RuntimeError("boom")'))
        self.mgr.load_all()
        app = apps.create(Ctx(self.store), {"app": "t_crash"})
        for _ in range(30):
            app.render(Canvas(84, 42), 0)
        self.assertTrue(self.info("flaky")["enabled"])

    def test_init_failure_counts(self):
        self.write("badinit.py", BAD_INIT)
        self.mgr.load_all()
        for _ in range(plugins.MAX_FAILS):
            with self.assertRaises(ValueError) as e:
                apps.create(Ctx(self.store), {"app": "t_badinit"})
        self.assertIn("couldn't start", str(e.exception))
        self.assertFalse(self.info("badinit")["enabled"])

    def test_runner_keeps_going(self):
        """On the real runner: the board shows the error, then the app ends and the runner stays healthy."""
        self.write("crashy.py", CRASHY)
        self.mgr.load_all()
        board = flipdot.Board(config(), dry_run=True)
        r = runner_mod.Runner(board, self.store)
        try:
            r.run({"app": "t_crash"})
            import time
            for _ in range(100):
                if r.status()["mode"] == "manual":
                    break
                time.sleep(0.05)
            self.assertEqual(r.status()["mode"], "manual")
            self.assertEqual(r.heartbeat()["errors_5min"], 0, "the crash never reached the runner")
        finally:
            r.stop()


class Install(PluginTestCase):
    def test_install_py_needs_trust(self):
        info = self.mgr.install(GOOD.encode(), filename="My-Plugin.py")
        self.assertEqual(info["name"], "my_plugin")
        self.assertFalse(info["enabled"])
        self.assertTrue(info["needs_trust"])
        self.assertNotIn("t_dots", apps.REGISTRY, "installed code doesn't run before it's trusted")
        self.assertTrue((Path(self.store.dir) / "plugins" / "my_plugin.py").exists(), "kept in the data folder")
        with self.assertRaises(ValueError):
            self.mgr.set_enabled("my_plugin", True)
        src = self.mgr.source("my_plugin")
        self.assertIn("class Dots", src["files"][0]["text"])
        with self.assertRaises(ValueError):
            self.mgr.set_enabled("my_plugin", True, trust=True, trusted_hash="0" * 64)
        self.mgr.set_enabled("my_plugin", True, trust=True, trusted_hash=src["hash"])
        self.assertIn("t_dots", apps.REGISTRY)
        # a changed file has to be trusted again
        (Path(self.store.dir) / "plugins" / "my_plugin.py").write_text(GOOD + "\n# changed\n")
        self.mgr.load_all()
        self.assertNotIn("t_dots", apps.REGISTRY)
        self.assertTrue(self.info("my_plugin")["needs_trust"])

    def test_install_refuses_overwrite_without_replace(self):
        self.mgr.install(GOOD.encode(), filename="one.py")
        with self.assertRaises(FileExistsError):
            self.mgr.install(GOOD.encode(), filename="one.py")
        self.mgr.install(GOOD.encode() + b"\n", filename="one.py", replace=True)

    def test_install_github_style_zip(self):
        data = zip_bytes({"cool-plugin-main/__init__.py": GOOD, "cool-plugin-main/README.md": "hi",
                          "cool-plugin-main/.git/config": "x", "cool-plugin-main/__pycache__/a.pyc": "x"})
        info = self.mgr.install(data, filename="cool-plugin-main.zip")
        self.assertEqual(info["name"], "cool_plugin")
        files = {f["path"] for f in self.mgr.source("cool_plugin")["files"]}
        self.assertEqual(files, {"__init__.py", "README.md"})
        src = self.mgr.source("cool_plugin")
        self.mgr.set_enabled("cool_plugin", True, trust=True, trusted_hash=src["hash"])
        self.assertIn("t_dots", apps.REGISTRY)

    def test_zip_layouts(self):
        self.assertEqual(self.mgr._from_zip(zip_bytes({"repo-main/weather_x/__init__.py": GOOD, "repo-main/README.md": ""}), "r.zip")[0], "weather_x")
        name, files = self.mgr._from_zip(zip_bytes({"only.py": GOOD}), "x.zip")
        self.assertEqual((name, list(files)), ("only", ["__init__.py"]))
        with self.assertRaises(ValueError):
            self.mgr._from_zip(zip_bytes({"a.py": "", "b.py": ""}), "x.zip")
        with self.assertRaises(ValueError):
            self.mgr._from_zip(b"PK\x03\x04garbage", "x.zip")
        evil = zip_bytes({"../../escape/__init__.py": GOOD, "pkg/__init__.py": GOOD})
        name, files = self.mgr._from_zip(evil, "e.zip")
        self.assertEqual(name, "pkg")
        self.assertTrue(all(".." not in f for f in files))
        name, files = self.mgr._from_zip(zip_bytes({"repo-main/sub/thing/__init__.py": GOOD}), "r.zip", subdir="sub/thing")
        self.assertEqual(name, "thing")

    def test_install_from_url(self):
        zipped = zip_bytes({"repo-main/zplug/__init__.py": GOOD})
        srv = Served({"/raw/urlplug.py": GOOD.encode(), "/repo.zip": zipped, "/page": b"<!doctype html><html>hi</html>"})
        try:
            info = self.mgr.install_url(srv.url + "/raw/urlplug.py")
            self.assertEqual((info["name"], info["installed_from"]), ("urlplug", srv.url + "/raw/urlplug.py"))
            self.assertEqual(self.mgr.install_url(srv.url + "/repo.zip")["name"], "zplug")
            with self.assertRaises(ValueError):
                self.mgr.install_url(srv.url + "/page")
            with self.assertRaises(ValueError):
                self.mgr.install_url(srv.url + "/missing.py")
            with self.assertRaises(ValueError):
                self.mgr.install_url("file:///etc/passwd")
        finally:
            srv.close()

    def test_github_urls(self):
        g = plugins.github_download
        self.assertEqual(g("https://github.com/a/b"), ("https://codeload.github.com/a/b/zip/HEAD", "b.zip", ""))
        self.assertEqual(g("https://github.com/a/b/tree/dev/plugins/x"), ("https://codeload.github.com/a/b/zip/dev", "b.zip", "plugins/x"))
        self.assertEqual(g("https://github.com/a/b/blob/main/p/hello.py"),
                         ("https://raw.githubusercontent.com/a/b/main/p/hello.py", "hello.py", ""))
        self.assertEqual(g("https://example.com/x/thing.py"), ("https://example.com/x/thing.py", "thing.py", ""))

    def test_delete_and_undo(self):
        self.mgr.install(GOOD.encode(), filename="gone.py")
        src = self.mgr.source("gone")
        self.mgr.set_enabled("gone", True, trust=True, trusted_hash=src["hash"])
        self.mgr.delete("gone")
        self.assertNotIn("t_dots", apps.REGISTRY)
        self.assertNotIn("gone", [p["name"] for p in self.mgr.list()["plugins"]])
        self.mgr.restore("gone")
        self.assertIn("t_dots", apps.REGISTRY, "Undo brings it back on, still trusted")
        self.mgr.delete("gone")
        self.mgr.empty_trash()
        with self.assertRaises(KeyError):
            self.mgr.restore("gone")

    def test_folder_plugins_cant_be_deleted_here(self):
        self.write("good.py", GOOD)
        self.mgr.load_all()
        with self.assertRaises(ValueError):
            self.mgr.delete("good")
        self.assertTrue((self.folder / "good.py").exists())

    def test_names(self):
        self.assertEqual(plugins.clean_name("My Cool-Plugin.py"), "my_cool_plugin")
        self.assertEqual(plugins.clean_name("flipdot-thing-master"), "flipdot_thing")
        self.assertEqual(plugins.clean_name("3d.py"), "plugin_3d")


# ---- Pixlet ----------------------------------------------------------------------------------

FAKE_PIXLET = r'''#!/usr/bin/env python3
"""A stand-in for pixlet: `schema` prints a schema, `render` writes a two-frame GIF."""
import json, sys
from PIL import Image
args = sys.argv[1:]
if args[0] == "version":
    print("Pixlet version: v0.0.1-test"); sys.exit()
if args[0] == "schema":
    print(json.dumps({"version": "1", "schema": [
        {"type": "text", "id": "msg", "name": "Message", "default": "hi"},
        {"type": "dropdown", "id": "speed", "name": "Speed", "default": "1",
         "options": [{"display": "Slow", "value": "1"}, {"display": "Fast", "value": "2"}]},
        {"type": "onoff", "id": "big", "name": "Big", "default": "false"},
        {"type": "location", "id": "location", "name": "Location"},
        {"type": "oauth2", "id": "auth", "name": "Account"}]}))
    sys.exit()
out = args[args.index("-o") + 1]
w = int(args[args.index("-w") + 1]) if "-w" in args else 128
h = int(args[args.index("-t") + 1]) if "-t" in args else 64
kv = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("-"))
if kv.get("msg") == "fail":
    print("Error in main: the app failed", file=sys.stderr); sys.exit(1)
a = Image.new("RGB", (w, h)); b = Image.new("RGB", (w, h))
for x in range(10):
    a.putpixel((x, 0), (0, 0, 200))       # dark blue: lit, but below a brightness threshold
    b.putpixel((x, 1), (255, 255, 255))
a.save(out, format="GIF", save_all=True, append_images=[b], duration=[100, 300], loop=0)
'''


class PixletConvert(unittest.TestCase):
    def test_modes(self):
        from PIL import Image
        im = Image.new("RGB", (64, 32))
        im.putpixel((0, 0), (0, 0, 120))   # dim blue
        im.putpixel((5, 5), (255, 255, 255))
        lit = pixlet.to_dots(im, "center", "lit", 48)
        self.assertEqual(len(lit), 84 * 42)
        self.assertEqual(lit[5 * 84 + 10], 1, "64x32 sits in the middle: (0,0) -> (10,5)")
        self.assertEqual(lit[10 * 84 + 15], 1)
        luma = pixlet.to_dots(im, "center", "luma", 48)
        self.assertEqual(luma[5 * 84 + 10], 0, "dim blue is below the brightness threshold")
        inv = pixlet.to_dots(im, "center", "lit", 48, invert=True)
        self.assertEqual(sum(inv), 84 * 42 - sum(lit))
        st = pixlet.to_dots(im, "stretch", "lit", 48)
        self.assertEqual(st[0], 1)
        full = Image.new("RGB", (64, 32), (128, 128, 128))
        d = pixlet.to_dots(full, "stretch", "dither")
        self.assertTrue(0.3 < sum(d) / len(d) < 0.7, "mid grey dithers to about half the dots")

    def test_identical_frames_merge(self):
        from PIL import Image
        a = Image.new("RGB", (64, 32))
        out = pixlet.convert([(a, 50), (a.copy(), 50), (Image.new("RGB", (64, 32), (255, 255, 255)), 20)])
        self.assertEqual([ms for _, ms in out], [100, 20])

    def test_schema_params(self):
        params, skipped = pixlet_apps.schema_params({"schema": [
            {"type": "text", "id": "t", "name": "T", "default": "x"},
            {"type": "toggle", "id": "b", "name": "B", "default": "true"},
            {"type": "dropdown", "id": "d", "name": "D", "default": "2", "options": [{"display": "Two", "value": "2"}]},
            {"type": "location", "id": "loc", "name": "L"},
            {"type": "typeahead", "id": "x", "name": "Search"}]})
        self.assertEqual([(p["name"], p["type"], p["default"]) for p in params],
                         [("t", "text", "x"), ("b", "checkbox", True), ("d", "select", "2")])
        self.assertEqual(skipped, ["Search"])


class PixletApps(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.tmp = tempfile.TemporaryDirectory()
        fake = Path(self.tmp.name) / "pixlet"
        fake.write_text(FAKE_PIXLET.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
        fake.chmod(0o755)
        self.old_env = os.environ.get("FLIPDOT_PIXLET")
        os.environ["FLIPDOT_PIXLET"] = str(fake)
        self.store = Store(os.path.join(self.tmp.name, "data"))
        self.store.settings["weather"] = {"lat": 52.5, "lon": 13.4, "name": "Berlin, DE"}
        star = b'load("render.star", "render")\ndef main(config):\n    return render.Root(child = render.Text("hi"))\n'
        index = [{"name": "hello_star", "displayName": "Hello Star", "summary": "Says hi", "author": "Tester",
                  "recommendedInterval": 1, "supports2x": True, "image": "hello_star/preview.webp"},
                 {"name": "other", "displayName": "Other clock", "summary": "Tells time", "category": "clocks"}]
        self.files = {"/apps.json": json.dumps(index).encode(), "/raw/hello.star": star,
                      "/raw/img.png": b"\x89PNG fake", "/raw/preview.webp": b"RIFF"}
        self.srv = Served(self.files)
        base_url = self.srv.url
        self.files["/contents/hello_star"] = json.dumps([
            {"type": "file", "name": "hello.star", "size": len(star), "download_url": base_url + "/raw/hello.star"},
            {"type": "file", "name": "preview.webp", "size": 4, "download_url": base_url + "/raw/preview.webp"},
            {"type": "dir", "name": "images", "size": 0, "download_url": None}]).encode()
        self.files["/contents/hello_star/images"] = json.dumps([
            {"type": "file", "name": "a.png", "size": 9, "download_url": base_url + "/raw/img.png"}]).encode()
        self.saved_urls = pixlet.INDEX_URL, pixlet.CONTENTS_URL
        pixlet.INDEX_URL = base_url + "/apps.json"
        pixlet.CONTENTS_URL = base_url + "/contents/{name}"
        pixlet.RENDERS = pixlet.Renders()
        self.lib = pixlet.start(self.store)

    def tearDown(self):
        pixlet.INDEX_URL, pixlet.CONTENTS_URL = self.saved_urls
        for m in self.lib.installed():
            apps.REGISTRY.pop(pixlet_apps.app_id(m["name"]), None)
        pixlet_apps._registered.clear()
        pixlet.LIBRARY = None
        if self.old_env is None:
            os.environ.pop("FLIPDOT_PIXLET", None)
        else:
            os.environ["FLIPDOT_PIXLET"] = self.old_env
        self.srv.close()
        self.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def wait_frames(self, app, now=1000.0):
        import time
        for _ in range(200):
            c = Canvas(84, 42)
            app.render(c, now)
            if app.frames:
                return c
            time.sleep(0.02)
        self.fail(f"no frames: {app.error}")

    def test_catalogue_install_and_render(self):
        self.lib.refresh_index()
        found = self.lib.search("clock")
        self.assertEqual([a["name"] for a in found["apps"]], ["other"])
        meta = self.lib.install("hello_star")
        files = sorted(p.relative_to(self.lib.apps_dir / "hello_star").as_posix()
                       for p in (self.lib.apps_dir / "hello_star").rglob("*") if p.is_file())
        self.assertEqual(files, ["hello.star", "images/a.png"], "the preview picture isn't downloaded")
        self.assertEqual(meta["license"], "Apache-2.0")
        cls = apps.REGISTRY["pixlet_hello_star"]
        self.assertEqual(cls.category, "Plugins")
        names = [p["name"] for p in cls.params]
        self.assertEqual(names[:3], ["msg", "speed", "big"])
        self.assertIn("pixlet_size", names)
        self.assertIn("2x", [o[0] for o in next(p for p in cls.params if p["name"] == "pixlet_size")["options"]])
        self.assertIn("Account", cls.description, "settings that can't be set here are named")
        self.assertTrue(self.lib.search("hello")["apps"][0]["installed"])

        app = apps.create(Ctx(self.store), {"app": "pixlet_hello_star", "params": {"msg": "yo", "big": True}})
        c = self.wait_frames(app, 1000.0)
        self.assertEqual(sum(c.px[5 * 84 + 10: 5 * 84 + 20]), 10, "frame 1: the blue line, centred")
        c = Canvas(84, 42)
        app.render(c, 1000.2)
        self.assertEqual(sum(c.px[6 * 84 + 10: 6 * 84 + 20]), 10, "after 100 ms: frame 2")
        self.assertLessEqual(app.interval, 0.3)
        cfg = app.config()
        self.assertEqual(cfg["big"], "true")
        self.assertEqual(json.loads(cfg["location"])["lat"], "52.5", "the location comes from Settings")

        luma = apps.create(Ctx(self.store), {"app": "pixlet_hello_star", "params": {"msg": "yo", "big": True, "pixlet_mode": "luma"}})
        c = self.wait_frames(luma, 1000.0)
        self.assertEqual(sum(c.px[5 * 84 + 10: 5 * 84 + 20]), 0, "same render, other tuning: dark blue is off")

    def test_render_error_is_shown(self):
        self.lib.refresh_index()
        self.lib.install("hello_star")
        app = apps.create(Ctx(self.store), {"app": "pixlet_hello_star", "params": {"msg": "fail"}})
        import time
        for _ in range(200):
            app.render(Canvas(84, 42), 0)
            if app.error:
                break
            time.sleep(0.02)
        self.assertIn("the app failed", app.error)

    def test_missing_binary(self):
        os.environ["FLIPDOT_PIXLET"] = "/nonexistent/pixlet"
        self.assertIsNone(pixlet.binary())
        self.lib.refresh_index()
        self.lib.install("hello_star")  # works without pixlet; the settings come later
        cls = apps.REGISTRY["pixlet_hello_star"]
        self.assertIn("setup-pixlet.sh", cls.needs_setup(self.store, {}))
        self.assertIn("once Pixlet is installed", cls.description)

    def test_remove_and_undo(self):
        self.lib.refresh_index()
        self.lib.install("hello_star")
        self.lib.delete("hello_star")
        self.assertNotIn("pixlet_hello_star", apps.REGISTRY)
        self.lib.restore("hello_star")
        self.assertIn("pixlet_hello_star", apps.REGISTRY)

    def test_unknown_app(self):
        self.lib.refresh_index()
        with self.assertRaises(ValueError):
            self.lib.install("../etc")
        with self.assertRaises(ValueError):
            self.lib.install("nope")


# ---- routes ----------------------------------------------------------------------------------

PASSWORD = "plugins-owner-pw"


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: getattr(server, k) for k in ("board", "store", "runner", "hub", "password")}
        cls.saved_mods = plugins.MANAGER, pixlet.LIBRARY, plugins.FOLDER
        server.board = flipdot.Board(config(), dry_run=True)
        server.store = Store(os.path.join(cls.tmp.name, "data"))
        server.runner = runner_mod.Runner(server.board, server.store)
        server.hub = Hub(server.board, server.runner, server.ws_message)
        server.password = PASSWORD
        folder = Path(cls.tmp.name) / "plugins"
        folder.mkdir()
        (folder / "good.py").write_text(GOOD)
        plugins.start(server.store, folder=folder)
        pixlet.start(server.store)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        tokens = access.get(server.store)
        cls.tok = {role: tokens.create(f"{role} person", role)[1] for role in access.ROLES}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.runner.stop()
        for n in list(plugins.MANAGER.plugins):
            plugins.MANAGER._unload(n)
        for k, v in cls.saved.items():
            setattr(server, k, v)
        plugins.MANAGER, pixlet.LIBRARY, plugins.FOLDER = cls.saved_mods
        cls.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def request(self, method, path, body=None, token=None, owner=False, raw=None):
        h = {}
        if token:
            h["Authorization"] = f"Bearer {token}"
        if owner:
            h["Authorization"] = "Basic " + base64.b64encode(f"x:{PASSWORD}".encode()).decode()
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            payload = r.read()
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
            return r.status, payload
        finally:
            conn.close()

    ROUTES = [("GET", "/api/plugins"), ("POST", "/api/plugins/reload"), ("POST", "/api/plugins/upload?filename=x.py"),
              ("POST", "/api/plugins/install"), ("GET", "/api/plugins/good/source"), ("POST", "/api/plugins/good"),
              ("POST", "/api/plugins/good/delete"), ("POST", "/api/plugins/good/reload"), ("GET", "/api/pixlet"),
              ("GET", "/api/pixlet/catalog"), ("POST", "/api/pixlet/install"), ("POST", "/api/pixlet/apps/x/delete")]

    def test_owner_only(self):
        for role, tok in self.tok.items():
            for method, path in self.ROUTES:
                status, _ = self.request(method, path, body={} if method == "POST" else None, token=tok)
                self.assertEqual(status, 403, f"{role} must not reach {method} {path}")
        for _, pattern, _ in server.ROUTES:
            raw = pattern.pattern[1:-1]
            if raw.startswith(("/api/plugins", "/api/pixlet")):
                for role in access.ALLOW:
                    self.assertNotIn(raw, {p for _, p in access.ALLOW[role]})

    def test_owner_flow(self):
        status, body = self.request("GET", "/api/plugins", owner=True)
        self.assertEqual(status, 200)
        self.assertIn("good", [p["name"] for p in body["plugins"]])
        self.assertIn("pixlet", body)
        status, body = self.request("POST", "/api/plugins/upload?filename=up.py", owner=True,
                                    raw=GOOD.replace("t_dots", "t_up").encode())
        self.assertEqual(status, 200, body)
        self.assertIn("class Dots", body["source"]["files"][0]["text"])
        self.assertFalse(body["plugin"]["enabled"])
        status, body2 = self.request("POST", "/api/plugins/up", {"enabled": True}, owner=True)
        self.assertEqual(status, 400, "trust first")
        status, body2 = self.request("POST", "/api/plugins/up", {"enabled": True, "trust": True, "hash": body["source"]["hash"]}, owner=True)
        self.assertEqual(status, 200, body2)
        self.assertEqual([a["id"] for a in body2["plugin"]["apps"]], ["t_up"])
        status, apps_body = self.request("GET", "/api/apps", owner=True)
        self.assertIn("t_up", [a["id"] for a in apps_body["apps"]])
        self.assertEqual(self.request("POST", "/api/plugins/upload?filename=up.py", owner=True, raw=GOOD.encode())[0], 409)
        # shown on the board, as a layer and in a playlist
        self.assertEqual(self.request("POST", "/api/run", {"app": "t_up", "params": {"n": 4}}, owner=True)[0], 200)
        status, body3 = self.request("POST", "/api/plugins/reload", {}, owner=True)
        self.assertEqual(status, 200)
        self.assertEqual(server.runner.status()["app"], "t_up", "Reload restarts the plugin app that's on the board")
        self.assertEqual(self.request("POST", "/api/plugins/up/delete", {}, owner=True)[0], 200)
        self.assertNotIn("t_up", apps.REGISTRY)
        self.assertEqual(self.request("POST", "/api/plugins/up/restore", {}, owner=True)[0], 200)
        self.assertIn("t_up", apps.REGISTRY)
        self.assertEqual(self.request("GET", "/api/plugins/nope/source", owner=True)[0], 404)
        server.runner.stop()

    def test_install_url_route(self):
        srv = Served({"/p/viaurl.py": GOOD.replace("t_dots", "t_via").encode()})
        try:
            status, body = self.request("POST", "/api/plugins/install", {"url": srv.url + "/p/viaurl.py"}, owner=True)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["plugin"]["name"], "viaurl")
            status, body = self.request("POST", "/api/plugins/install", {"url": srv.url + "/p/missing.py"}, owner=True)
            self.assertEqual(status, 400)
            self.assertIn("404", body["error"])
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
