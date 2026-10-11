"""Python plugins: extra apps from files you add, without editing the app itself.

A plugin is a .py file (or a folder with an __init__.py) that defines apps with the same API as the
built-in ones (`from apps.base import App, P, register, Widget`). Two places are searched:

    plugins/            next to server.py, for files you copy there yourself (git-ignored)
    <data>/plugins/     plugins installed from Settings → Plugins (uploaded or from a URL); being in the
                        data folder, they're copied into storage.db and come back with a backup

A plugin runs with the server's full privileges, so the ones installed from the web page stay off until
you have read the code and said you trust it (that trust is for this exact code: a changed file needs
it again). A plugin that fails to import is skipped and its traceback shown; an app that keeps
crashing is turned off (its render() is wrapped here, so a crash never reaches the runner).
"""

import ast
import hashlib
import importlib.util
import io
import logging
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from apps import base

log = logging.getLogger("plugins")

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "plugins"
STATE_FILE = "plugins.json"
CATEGORY = "Plugins"
MAX_FAILS = 5  # crashes in a row (creating or drawing) before an app's plugin is turned off
IMPORT_TIMEOUT = 15.0  # seconds; a plugin that takes longer to import is skipped
MAX_DOWNLOAD = 8 * 2**20
MAX_UNZIPPED = 20 * 2**20
MAX_FILES = 400
SOURCE_SHOW_MAX = 400_000  # characters of source sent to the page for review
NAME_RE = re.compile(r"[a-z0-9_]{1,40}")
SKIP_PARTS = {"__pycache__", ".git", "__MACOSX", ".github"}
UA = base.UA

MANAGER = None  # the Manager for the running server (set by start())


def clean_name(raw):
    """A plugin name from a file, folder or repo name: lowercase letters, digits and _ ("my-plugin-main" -> my_plugin)."""
    s = Path(str(raw)).stem if str(raw).endswith((".py", ".zip")) else str(raw)
    s = re.sub(r"[-_.](main|master)$", "", s.strip().lower())
    s = re.sub(r"[^a-z0-9_]+", "_", s).strip("_")[:40]
    if not s or s[0].isdigit():
        s = ("plugin_" + s)[:40].rstrip("_")
    return s


def _files_of(path):
    """(relative path, absolute Path) for a plugin's files, in a stable order."""
    p = Path(path)
    if p.is_file():
        return [(p.name, p)]
    out = []
    for f in sorted(p.rglob("*")):
        rel = f.relative_to(p)
        if f.is_file() and not SKIP_PARTS & set(rel.parts) and f.suffix != ".pyc":
            out.append((rel.as_posix(), f))
    return out


def code_hash(path):
    h = hashlib.sha256()
    for rel, f in _files_of(path):
        h.update(rel.encode() + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _about(path):
    """The docstring's first paragraph and a __version__, read without running the code."""
    entry = Path(path) / "__init__.py" if Path(path).is_dir() else Path(path)
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return "", None
    doc = (ast.get_docstring(tree) or "").strip().split("\n\n")[0].replace("\n", " ")[:300]
    version = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__version__" for t in node.targets):
            try:
                version = str(ast.literal_eval(node.value))[:20]
            except ValueError:
                pass
    return doc, version


class Plugin:
    def __init__(self, name, path, source):
        self.name, self.path, self.source = name, Path(path), source  # source: "folder" or "installed"
        self.module = None
        self.apps = []  # app ids it registered
        self.error = None  # traceback text of the last failure (import, or an app that crashed)
        self.error_at = None
        self.loaded_at = None


class Manager:
    def __init__(self, store, folder=FOLDER, notify=None):
        self.store = store
        self.folder = Path(folder) if folder else None
        self.installed = Path(store.dir) / "plugins"
        self.trash = Path(store.dir) / "plugins-trash"  # a deleted plugin waits here for Undo until the next start
        self.installed.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state = store._load(STATE_FILE, {"plugins": {}})
        self.state.setdefault("plugins", {})
        self.plugins = {}  # name -> Plugin (everything found, loaded or not)
        self.builtin = dict(base.REGISTRY)  # app ids that are taken before any plugin loads
        self.owner = {}  # app id -> plugin name
        self.guarded = {}  # app id -> the wrapped class in the registry
        self.fails = {}  # (plugin, app id) -> crashes in a row
        self.notify = notify
        self.gen = {}  # plugin name -> load generation; an app made by an older load stops
        self.on_change = []  # called after anything is loaded or unloaded (e.g. to refresh the Apps list)

    # ---- state ----------------------------------------------------------------------

    def _rec(self, name):
        return self.state["plugins"].setdefault(name, {})

    def _save(self):
        self.store._save(STATE_FILE, self.state)

    def _sync(self, *paths):
        try:
            self.store.sync(*paths)
        except Exception as e:  # the file is on disk; the background mirror tries again
            log.warning("plugins: storage sync failed: %s", e)

    def enabled(self, p):
        rec = self.state["plugins"].get(p.name, {})
        if p.source == "folder":
            return rec.get("enabled", True)  # you put it there yourself
        return bool(rec.get("enabled")) and rec.get("trusted") == code_hash(p.path)

    def needs_trust(self, p):
        return p.source == "installed" and self.state["plugins"].get(p.name, {}).get("trusted") != code_hash(p.path)

    # ---- finding and loading ----------------------------------------------------------

    def discover(self):
        found, problems = {}, []
        for source, d in (("installed", self.installed), ("folder", self.folder)):
            if d is None or not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.name.startswith((".", "_")) or f.name in SKIP_PARTS:
                    continue
                if f.is_file() and f.suffix == ".py":
                    name = f.stem
                elif f.is_dir() and (f / "__init__.py").is_file():
                    name = f.name
                else:
                    continue
                if not NAME_RE.fullmatch(name):
                    problems.append(f"{f.name}: rename it to lowercase letters, digits and _ only")
                    continue
                if name in found:
                    problems.append(f"{source}/{f.name}: a plugin called {name} is already installed, so this copy is skipped")
                    continue
                found[name] = (f, source)
        return found, problems

    def load_all(self):
        """(Re)load every plugin: called at startup and by Reload plugins."""
        with self.lock:
            for name in list(self.plugins):
                self._unload(name)
            found, self.problems = self.discover()
            self.plugins = {n: Plugin(n, f, src) for n, (f, src) in found.items()}
            for p in self.plugins.values():
                if self.enabled(p):
                    self._load(p)
        self._changed()
        return self.list()

    def reload(self, name):
        with self.lock:
            p = self.plugins.get(name)
            if p is None:
                raise KeyError(name)
            self._unload(name)
            if self.enabled(p):
                self._load(p)
        self._changed()

    def _load(self, p):
        modname = f"flipdot_plugin_{p.name}"
        self.gen[p.name] = self.gen.get(p.name, 0) + 1
        gen = self.gen[p.name]
        p.error = p.error_at = None
        result = {}
        dont_write = sys.dont_write_bytecode

        def work():
            try:
                entry = p.path / "__init__.py" if p.path.is_dir() else p.path
                spec = importlib.util.spec_from_file_location(
                    modname, entry, submodule_search_locations=[str(p.path)] if p.path.is_dir() else None)
                module = importlib.util.module_from_spec(spec)
                sys.modules[modname] = module
                # run the source itself, and write no __pycache__: a cached copy could hide a quick edit
                # (same size, same second), and the data folder is copied into storage.db
                sys.dont_write_bytecode = True
                code = compile(entry.read_bytes(), str(entry), "exec")
                exec(code, module.__dict__)
                result["module"] = module
                if self.gen.get(p.name) != gen:  # gave up waiting for it: take back what it registered
                    self._forget_classes(modname)
                    self._drop_modules(modname)
            except BaseException:  # SyntaxError, ImportError, SystemExit, anything a plugin raises
                result["error"] = traceback.format_exc(limit=-12)
            finally:
                sys.dont_write_bytecode = dont_write
            result["done"] = True

        t = threading.Thread(target=work, daemon=True, name=f"plugin-import-{p.name}")
        t.start()
        t.join(IMPORT_TIMEOUT)
        if not result.get("done"):
            self.gen[p.name] += 1  # whatever it registers later belongs to an abandoned load
            self._fail(p, f"Loading took longer than {IMPORT_TIMEOUT:.0f} s, so it was skipped. "
                          "Code at the top level of a plugin should only define things; do slow work in fetch() or render().")
            self._drop_modules(modname)
            return
        if "error" in result:
            self._drop_modules(modname)
            self._forget_classes(modname)
            self._fail(p, result["error"])
            return
        p.module = result["module"]
        p.loaded_at = time.time()
        problems = []
        for app_id, cls in list(base.REGISTRY.items()):
            if not str(getattr(cls, "__module__", "")).split(".")[0] == modname:
                continue
            if app_id in self.builtin or (self.owner.get(app_id) not in (None, p.name)):
                taken = "a built-in app" if app_id in self.builtin else f"the plugin {self.owner[app_id]}"
                problems.append(f"The app id {app_id!r} is already used by {taken}: give the app another id.")
                prev = self.builtin.get(app_id) or self.guarded.get(app_id)
                if prev is not None:
                    base.REGISTRY[app_id] = prev
                else:
                    base.REGISTRY.pop(app_id, None)
                continue
            base.REGISTRY[app_id] = self.guarded[app_id] = self._guard(cls, p.name, gen)
            self.owner[app_id] = p.name
            p.apps.append(app_id)
        if problems:
            self._fail(p, "\n".join(problems))
        elif not p.apps:
            self._fail(p, "It loaded, but it doesn't register any apps. Put @register above each App class.")
        log.info("plugin %s loaded: %s", p.name, ", ".join(p.apps) or "no apps")

    @staticmethod
    def _forget_classes(modname):
        for app_id, cls in list(base.REGISTRY.items()):
            if str(getattr(cls, "__module__", "")).split(".")[0] == modname:
                base.REGISTRY.pop(app_id, None)

    @staticmethod
    def _drop_modules(modname):
        for m in [m for m in sys.modules if m == modname or m.startswith(modname + ".")]:
            del sys.modules[m]

    def _unload(self, name):
        p = self.plugins.get(name)
        if p is None:
            return
        for app_id in p.apps:
            if self.owner.get(app_id) == name:
                base.REGISTRY.pop(app_id, None)
                del self.owner[app_id]
                self.guarded.pop(app_id, None)
        p.apps = []
        p.module = None
        self.gen[name] = self.gen.get(name, 0) + 1  # apps already on the board stop at their next frame
        modname = f"flipdot_plugin_{name}"
        self._forget_classes(modname)
        self._drop_modules(modname)

    def _fail(self, p, text):
        p.error, p.error_at = text, time.time()
        log.warning("plugin %s: %s", p.name, text.strip().splitlines()[-1] if text.strip() else text)

    def _changed(self):
        for fn in self.on_change:
            try:
                fn()
            except Exception as e:
                log.debug("plugins: listener failed: %s", e)

    # ---- the safety wrapper around each plugin app --------------------------------------

    def active(self, name, gen):
        return self.gen.get(name) == gen and name in self.plugins and self.plugins[name].module is not None

    def _guard(self, cls, plugin, gen):
        mgr = self
        key = (plugin, cls.id)

        def __init__(self, ctx, **params):
            try:
                cls.__init__(self, ctx, **params)
            except Exception as e:
                mgr.crashed(key, e, "starting")
                raise ValueError(f"The plugin app {cls.title} couldn't start: {type(e).__name__}: {e}") from None

        def render(self, c, now):
            if not mgr.active(plugin, gen):
                return False  # turned off, deleted or reloaded since this app started
            try:
                result = cls.render(self, c, now)
            except Exception as e:
                self.error = self._crash_error = f"{type(e).__name__}: {e}"[:200]
                c.clear()
                c.text_center(c.h // 2 - 9, "Plugin error")
                from canvas import wrap_text
                from fonts import SMALL
                for i, line in enumerate(wrap_text(self.error[:60], SMALL, c.w - 2)[:3]):
                    c.text_center(c.h // 2 + i * 6, line, SMALL)
                return False if mgr.crashed(key, e, "drawing") else None
            if mgr.fails.get(key):
                mgr.fails[key] = 0
            if getattr(self, "_crash_error", None):
                if self.error == self._crash_error:
                    self.error = None  # the runner shows `error` as the app's problem: it drew fine this time
                self._crash_error = None
            return result

        def input(self, key_, action="down", player=1):
            try:
                return cls.input(self, key_, action, player)
            except Exception as e:
                mgr.crashed(key, e, "handling a button")

        def stop(self):
            try:
                return cls.stop(self)
            except Exception as e:
                log.warning("plugin app %s: stop() failed: %s", cls.id, e)

        attrs = {"__init__": __init__, "render": render, "input": input, "stop": stop, "category": CATEGORY,
                 "plugin": plugin, "__module__": cls.__module__, "__qualname__": cls.__qualname__,
                 "__doc__": cls.__doc__}
        return type(cls.__name__, (cls,), attrs)

    def crashed(self, key, exc, doing):
        """Count a crash; True when the plugin has just been turned off because of it."""
        plugin, app_id = key
        n = self.fails[key] = self.fails.get(key, 0) + 1
        p = self.plugins.get(plugin)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=-8))
        if p is not None:
            p.error, p.error_at = f"The app {app_id} crashed while {doing} ({n} in a row):\n{tb}", time.time()
        if n == 1 or n == MAX_FAILS:
            log.warning("plugin app %s crashed while %s: %s", app_id, doing, exc)
        if n < MAX_FAILS:
            return False
        self.fails[key] = 0
        with self.lock:
            rec = self._rec(plugin)
            rec["enabled"] = False
            rec["reason"] = f"Turned off after {app_id} crashed {MAX_FAILS} times in a row ({type(exc).__name__}: {exc})"[:300]
            self._save()
            self._unload(plugin)
            if p is not None:
                p.error, p.error_at = f"{rec['reason']}\n\n{tb}", time.time()
        self._changed()
        if self.notify:
            spec = {"app": "notice", "params": {"title": "Plugin off", "text": f"{plugin} kept crashing", "icon": "bell"}}
            threading.Thread(target=self._notify_off, args=(spec, plugin), daemon=True).start()
        return True

    def _notify_off(self, spec, plugin):
        try:
            self.notify(spec=spec, duration=8, transition="dissolve", priority="low", key=f"plugin-{plugin}",
                        source="plugins", text=f"Plugin {plugin} turned off")
        except Exception as e:
            log.debug("plugins: notification failed: %s", e)

    # ---- what the Settings page shows ----------------------------------------------------

    def info(self, p):
        rec = self.state["plugins"].get(p.name, {})
        doc, version = _about(p.path)
        files = _files_of(p.path)
        return {
            "name": p.name, "source": p.source, "package": p.path.is_dir(),
            "path": str(p.path if p.source == "folder" else Path("plugins") / p.path.name),
            "enabled": self.enabled(p), "needs_trust": self.needs_trust(p), "loaded": p.module is not None,
            "apps": [{"id": a, "title": getattr(base.REGISTRY.get(a), "title", a)} for a in p.apps],
            "error": p.error, "error_at": p.error_at, "reason": rec.get("reason"),
            "description": doc, "version": version, "files": len(files), "size": sum(f.stat().st_size for _, f in files),
            "installed_from": rec.get("from"), "installed_at": rec.get("installed_at"),
        }

    def list(self):
        with self.lock:
            return {"plugins": [self.info(p) for p in sorted(self.plugins.values(), key=lambda p: p.name)],
                    "problems": list(getattr(self, "problems", [])),
                    "folder": str(self.folder) if self.folder else None, "installed_folder": str(self.installed),
                    "max_fails": MAX_FAILS}

    def get(self, name):
        p = self.plugins.get(name)
        if p is None:
            raise KeyError(name)
        return p

    def source(self, name):
        p = self.get(name)
        out, total = [], 0
        for rel, f in _files_of(p.path):
            data = f.read_bytes()
            if b"\0" in data[:4096]:
                text = f"(binary file, {len(data)} bytes)"
            else:
                text = data.decode("utf-8", "replace")
                if total + len(text) > SOURCE_SHOW_MAX:
                    text = text[:max(0, SOURCE_SHOW_MAX - total)] + "\n… (cut short here; read the rest in the file itself)"
            total += len(text)
            out.append({"path": rel, "text": text, "size": len(data)})
        return {"name": name, "files": out, "hash": code_hash(p.path)}

    # ---- turning on and off --------------------------------------------------------------

    def set_enabled(self, name, on, trust=False, trusted_hash=None):
        with self.lock:
            p = self.get(name)
            rec = self._rec(name)
            if on and p.source == "installed":
                current = code_hash(p.path)
                if trust:
                    if trusted_hash and trusted_hash != current:
                        raise ValueError("The code changed after you looked at it. Look at it again before you turn it on.")
                    rec["trusted"] = current
                elif rec.get("trusted") != current:
                    raise ValueError("Read the code and confirm that you trust it before turning it on.")
            rec["enabled"] = bool(on)
            rec.pop("reason", None)
            self._save()
            self._unload(name)
            if on:
                for k in [k for k in self.fails if k[0] == name]:
                    self.fails[k] = 0
                self._load(p)
            else:
                p.error = None
        self._changed()
        return self.info(p)

    # ---- install, delete, undo -----------------------------------------------------------

    def install(self, data, filename="plugin.py", origin=None, replace=False, subdir=""):
        """Install a .py file or a .zip (a package folder, or a repo with one in it). It stays off until trusted."""
        if data[:4] == b"PK\x03\x04":
            name, files = self._from_zip(data, filename, subdir)
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("That isn't a Python file or a zip.") from None
            if "<html" in text[:500].lower():
                raise ValueError("That's a web page, not a Python file. For a file on GitHub use its Raw link.")
            name, files = clean_name(filename), None
            data = text.encode()
        with self.lock:
            existing = self.plugins.get(name)
            if existing and existing.source == "folder":
                raise ValueError(f"There's already a plugin called {name} in the plugins folder. Rename one of them.")
            dest_file, dest_dir = self.installed / f"{name}.py", self.installed / name
            if (dest_file.exists() or dest_dir.exists()) and not replace:
                raise FileExistsError(name)
            if existing:
                self._unload(name)
            for old in (dest_file, dest_dir):
                if old.is_dir():
                    shutil.rmtree(old)
                elif old.exists():
                    old.unlink()
            if files is None:
                dest_file.write_bytes(data)
                path = dest_file
            else:
                tmp = self.installed / f".{name}.tmp"
                if tmp.exists():
                    shutil.rmtree(tmp)
                for rel, blob in files.items():
                    f = tmp / rel
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(blob)
                tmp.replace(dest_dir)
                path = dest_dir
            rec = self.state["plugins"][name] = {"enabled": False, "from": (origin or filename)[:300],
                                                 "installed_at": int(time.time())}
            self._save()
            self._sync(f"plugins/{name}.py", f"plugins/{name}")
            self.plugins[name] = Plugin(name, path, "installed")
            log.info("plugin %s installed from %s (off until trusted)", name, rec["from"])
        self._changed()
        return self.info(self.plugins[name])

    def _from_zip(self, data, filename, subdir=""):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("That zip file is damaged.") from None
        entries = []
        total = 0
        for i in z.infolist():
            if i.is_dir() or (i.external_attr >> 16) & 0o170000 == 0o120000:  # folders and symlinks
                continue
            parts = Path(i.filename.replace("\\", "/")).parts
            if not parts or i.filename.startswith("/") or ".." in parts or SKIP_PARTS & set(parts) or parts[-1].endswith(".pyc"):
                continue
            total += i.file_size
            entries.append((parts, i))
        if len(entries) > MAX_FILES or total > MAX_UNZIPPED:
            raise ValueError(f"That zip is too big for a plugin ({len(entries)} files, {total // 2**20} MB unpacked).")
        # a GitHub download has everything inside one folder (repo-main/): look inside it
        tops = {p[0] for p, _ in entries}
        prefix = ()
        label = Path(filename).stem
        if len(tops) == 1 and all(len(p) > 1 for p, _ in entries):
            prefix = (next(iter(tops)),)
            label = prefix[0]
        if subdir:
            prefix += tuple(p for p in subdir.strip("/").split("/") if p)
            label = prefix[-1]
        inside = [(p[len(prefix):], i) for p, i in entries if p[:len(prefix)] == prefix and len(p) > len(prefix)]
        if not inside:
            raise ValueError(f"There's no folder {subdir} in that zip.")
        names = {p for p, _ in inside}
        if ("__init__.py",) in names:
            root, name = (), clean_name(label)
        else:
            pkgs = sorted({p[0] for p in names if len(p) == 2 and p[1] == "__init__.py"})
            mods = sorted(p[0] for p in names if len(p) == 1 and p[0].endswith(".py") and p[0] != "setup.py")
            if len(pkgs) == 1:
                root, name = (pkgs[0],), clean_name(pkgs[0])
            elif len(mods) == 1 and not pkgs:
                (_, info), = [(p, i) for p, i in inside if p == (mods[0],)]
                return clean_name(mods[0]), {"__init__.py": z.read(info)}
            else:
                raise ValueError("Couldn't find the plugin in that zip: it needs one .py file, or one folder with an "
                                 "__init__.py, at the top (or inside the repository's folder).")
        files = {"/".join(p[len(root):]): z.read(i) for p, i in inside if p[:len(root)] == root}
        return name, files

    def install_url(self, url, replace=False):
        url = (url or "").strip()
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("Paste a web address starting with https://")
        fetch, filename, subdir = github_download(url)
        data = download(fetch)
        return self.install(data, filename=filename, origin=url, replace=replace, subdir=subdir)

    def delete(self, name):
        """Move an installed plugin to the trash (Undo puts it back; the trash is emptied at the next start)."""
        with self.lock:
            p = self.get(name)
            if p.source == "folder":
                raise ValueError(f"{name} is in the plugins folder ({self.folder}). Remove the file there to delete it, "
                                 "or turn it off here.")
            self._unload(name)
            self.trash.mkdir(parents=True, exist_ok=True)
            dest = self.trash / p.path.name
            if dest.is_dir():
                shutil.rmtree(dest)
            elif dest.exists():
                dest.unlink()
            p.path.replace(dest)
            rec = self.state["plugins"].pop(name, {})
            self.state.setdefault("trash", {})[name] = {"file": p.path.name, "rec": rec}
            self._save()
            del self.plugins[name]
            self._sync(f"plugins/{p.path.name}")
        self._changed()

    def restore(self, name):
        with self.lock:
            t = self.state.get("trash", {}).pop(name, None)
            if t is None or not (self.trash / t["file"]).exists():
                raise KeyError(name)
            dest = self.installed / t["file"]
            if dest.exists():
                raise ValueError(f"A plugin called {name} was installed since. Delete that one first.")
            (self.trash / t["file"]).replace(dest)
            self.state["plugins"][name] = t["rec"]
            self._save()
            self._sync(f"plugins/{t['file']}")
            p = self.plugins[name] = Plugin(name, dest, "installed")
            if self.enabled(p):
                self._load(p)
        self._changed()
        return self.info(p)

    def empty_trash(self):
        if self.trash.exists():
            shutil.rmtree(self.trash, ignore_errors=True)
            self._sync("plugins-trash")
        if self.state.pop("trash", None):
            self._save()


def github_download(url):
    """(URL to download, file name, folder inside the zip) for a raw file, a GitHub repo, folder or file page."""
    u = urllib.parse.urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    if u.netloc in ("github.com", "www.github.com") and len(parts) >= 2:
        owner, repo = parts[0], parts[1].removesuffix(".git")
        if len(parts) >= 5 and parts[2] == "blob":  # a file page: its raw copy
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{'/'.join(parts[3:])}", parts[-1], ""
        if len(parts) >= 4 and parts[2] == "tree":  # a branch, or a folder in it
            ref, sub = parts[3], "/".join(parts[4:])
            return f"https://codeload.github.com/{owner}/{repo}/zip/{ref}", f"{repo}.zip", sub
        if len(parts) == 2:
            return f"https://codeload.github.com/{owner}/{repo}/zip/HEAD", f"{repo}.zip", ""
    name = parts[-1] if parts else "plugin.py"
    return url, name, ""


def download(url, limit=MAX_DOWNLOAD, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"The download failed: HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"The download failed: {getattr(e, 'reason', e)}") from None
    if len(data) > limit:
        raise ValueError(f"That download is bigger than {limit // 2**20} MB, too big for a plugin.")
    return data


def start(store, notify=None, folder=FOLDER):
    """Load the plugins at server start (after the store, before the schedule resumes)."""
    global MANAGER
    MANAGER = Manager(store, folder=folder, notify=notify)
    MANAGER.empty_trash()
    try:
        MANAGER.load_all()
    except Exception:  # never stop the server over a plugin
        log.exception("plugins: loading failed")
    return MANAGER

