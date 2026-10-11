"""HTTP routes for Settings → Plugins: Python plugins (plugins.py) and Pixlet apps (pixlet.py).
server.py calls install() to register them and start() once the store exists. All of them are
owner-only: none is listed in access.ALLOW, and installing a plugin means running its code."""

import pixlet
import plugins
from apps import pixlet_apps

S = None  # the server module (store, runner, route, HttpError)


def _mgr():
    if plugins.MANAGER is None:
        raise S.HttpError(503, "Plugins aren't loaded yet.")
    return plugins.MANAGER


def _lib():
    if pixlet.LIBRARY is None:
        raise S.HttpError(503, "Pixlet apps aren't loaded yet.")
    return pixlet.LIBRARY


def _flag(req, name):
    return (req.query.get(name) or [""])[0] in ("1", "true", "yes")


def _rerun_if_showing(names):
    """Reload: a plugin app on the board alone starts again with the new code."""
    try:
        st = S.runner.status()
        spec = st.get("spec") or {}
        owner = plugins.MANAGER.owner.get(spec.get("app")) if plugins.MANAGER else None
        if st.get("mode") == "app" and owner in names and spec.get("app") in S.apps.REGISTRY:
            S.runner.run(spec)
    except Exception as e:  # the reload itself worked; the board just keeps what it had
        S.logging.info("plugins: couldn't restart the app on the board: %s", e)


def _not_found(name):
    return S.HttpError(404, f"There's no plugin called {name}.")


# ---- Python plugins --------------------------------------------------------------------

def get_plugins(req):
    return {**_mgr().list(), "pixlet": pixlet_status()}


def post_reload(req):
    mgr = _mgr()
    out = mgr.load_all()
    _rerun_if_showing({p["name"] for p in out["plugins"]})
    return out


def post_upload(req):
    name = (req.query.get("filename") or ["plugin.py"])[0]
    data = req.raw()
    if not data:
        raise ValueError("The file is empty.")
    if not name.lower().endswith((".py", ".zip")):
        raise ValueError("Upload a .py file or a .zip.")
    return _installed(lambda: _mgr().install(data, filename=name, origin=f"upload: {name}", replace=_flag(req, "replace")))


def post_install(req):
    body = req.json()
    return _installed(lambda: _mgr().install_url(body.get("url"), replace=bool(body.get("replace"))))


def _installed(fn):
    try:
        info = fn()
    except FileExistsError as e:
        raise S.HttpError(409, f"A plugin called {e.args[0]} is already installed. Replace it?") from None
    return {"plugin": info, "source": _mgr().source(info["name"])}


def get_source(req, name):
    try:
        return _mgr().source(name)
    except KeyError:
        raise _not_found(name) from None


def post_plugin(req, name):
    body = req.json()
    mgr = _mgr()
    try:
        if "enabled" in body:
            info = mgr.set_enabled(name, bool(body["enabled"]), trust=bool(body.get("trust")),
                                   trusted_hash=body.get("hash"))
            if body["enabled"]:
                _rerun_if_showing({name})
            return {"plugin": info}
        return {"plugin": mgr.info(mgr.get(name))}
    except KeyError:
        raise _not_found(name) from None


def post_plugin_action(req, name, action):
    mgr = _mgr()
    try:
        if action == "delete":
            mgr.delete(name)
        elif action == "restore":
            mgr.restore(name)
        else:  # reload one
            mgr.reload(name)
            _rerun_if_showing({name})
    except KeyError:
        raise _not_found(name) from None
    return mgr.list()


# ---- Pixlet apps ------------------------------------------------------------------------

def pixlet_status():
    lib = pixlet.LIBRARY
    b = pixlet.binary()
    return {"binary": b, "version": pixlet.version() if b else None, "hint": None if b else pixlet.SETUP_HINT,
            "installed": [{k: m.get(k) for k in ("name", "title", "summary", "author", "page", "license", "installed_at")}
                          | {"app": pixlet_apps.app_id(m["name"])}
                          for m in (lib.installed() if lib else [])],
            "catalog_fetched": lib.index().get("fetched") if lib else None}


def get_pixlet(req):
    return pixlet_status()


def get_pixlet_catalog(req):
    lib = _lib()
    q = (req.query.get("q") or [""])[0]
    cat = (req.query.get("category") or [""])[0]
    if _flag(req, "refresh") or lib.index().get("fetched") is None:
        lib.refresh_index()
    limit = max(1, min(200, int((req.query.get("limit") or [24])[0])))
    return lib.search(q, cat, limit)


def post_pixlet_install(req):
    meta = _lib().install(str(req.json().get("name") or ""))
    return {"app": meta, **pixlet_status()}


def post_pixlet_action(req, name, action):
    lib = _lib()
    try:
        (lib.delete if action == "delete" else lib.restore)(name)
    except KeyError:
        raise S.HttpError(404, f"There's no Pixlet app called {name}.") from None
    return pixlet_status()


def install(server):
    global S
    S = server
    route = server.route
    route("GET", "/api/plugins")(get_plugins)
    route("POST", "/api/plugins/reload")(post_reload)
    route("POST", "/api/plugins/upload")(post_upload)
    route("POST", "/api/plugins/install")(post_install)
    route("GET", "/api/plugins/([a-z0-9_]+)/source")(get_source)
    route("POST", "/api/plugins/([a-z0-9_]+)")(post_plugin)
    route("POST", "/api/plugins/([a-z0-9_]+)/(delete|restore|reload)")(post_plugin_action)
    route("GET", "/api/pixlet")(get_pixlet)
    route("GET", "/api/pixlet/catalog")(get_pixlet_catalog)
    route("POST", "/api/pixlet/install")(post_pixlet_install)
    route("POST", "/api/pixlet/apps/([A-Za-z0-9_-]+)/(delete|restore)")(post_pixlet_action)


def start(store, notify=None):
    """After the store exists, before playlists or the schedule start: load plugins and Pixlet apps."""
    for name, fn in (("Pixlet apps", lambda: pixlet.start(store)), ("plugins", lambda: plugins.start(store, notify=notify))):
        try:
            fn()
        except Exception:
            S.logging.exception("%s didn't load", name)
