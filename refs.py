"""Where saved things are used, so renames can follow and deletes don't leave broken links.

Kinds: playlist and composition (keyed by name), scene, gallery (slideshow) and drawing (keyed by id).
They're used by playlist items, composition layers, the schedule and automations. App specs point at
them through params whose type is the kind (see apps.base.P), including layers nested inside a
Layers app spec. Everything here works on plain dicts: `data` is
{"playlists": ..., "compositions": ..., "schedule": ..., "rules": [...]}.
"""

KINDS = ("playlist", "composition", "scene", "gallery", "drawing", "video")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _param_names(app_id, kind):
    from apps.base import REGISTRY
    cls = REGISTRY.get(app_id)
    return [p["name"] for p in getattr(cls, "params", []) if p.get("type") == kind]


def spec_uses(spec, kind, key):
    """Does this app spec (or a layer inside it) point at kind/key?"""
    params = spec.get("params") or {}
    if any(params.get(n) == key for n in _param_names(spec.get("app"), kind)):
        return True
    return any(spec_uses(layer, kind, key) for layer in params.get("layers") or [] if isinstance(layer, dict))


def spec_rename(spec, kind, old, new):
    """Point this spec (and nested layers) at the new key. Returns the number of changes."""
    params = spec.get("params") or {}
    n = 0
    for name in _param_names(spec.get("app"), kind):
        if params.get(name) == old:
            params[name] = new
            n += 1
    for layer in params.get("layers") or []:
        if isinstance(layer, dict):
            n += spec_rename(layer, kind, old, new)
    return n


def _rule_label(rule):
    return f'Automation "{rule.get("name") or rule.get("id")}"'


def _sched_label(i, rule):
    days = rule.get("days", list(range(7)))
    d = "every day" if len(days) == 7 else ", ".join(DAYS[x] for x in days if 0 <= x < 7) or "no days"
    return f"Schedule rule {i + 1} ({d} {rule.get('start', '00:00')}-{rule.get('end', '24:00')})"


def _action_uses(a, kind, key):
    t = a.get("type")
    if t == "app":
        return spec_uses(a, kind, key)
    return ((t == "playlist" and kind == "playlist" and a.get("playlist") == key)
            or (t == "composition" and kind == "composition" and a.get("composition") == key)
            or (t == "notify" and kind == "scene" and a.get("scene") == key))


def find(data, kind, key):
    """Human-readable list of the places that use kind/key."""
    out = []
    for name, pl in data.get("playlists", {}).items():
        for i, item in enumerate(pl.get("items", [])):
            if spec_uses(item, kind, key):
                out.append(f'Playlist "{name}", item {i + 1}')
    for name, comp in data.get("compositions", {}).items():
        if kind == "composition" and name == key:
            continue
        for i, layer in enumerate(comp.get("layers", [])):
            if spec_uses(layer, kind, key):
                out.append(f'Layer set "{name}", layer {i + 1}')
    if kind == "playlist":
        sched = data.get("schedule", {})
        if sched.get("default") == key:
            out.append("Schedule: default playlist")
        for i, rule in enumerate(sched.get("rules", [])):
            if rule.get("playlist") == key:
                out.append(_sched_label(i, rule))
    for rule in data.get("rules", []):
        if _action_uses(rule.get("action", {}), kind, key):
            out.append(_rule_label(rule))
    return out


def rename(data, kind, old, new):
    """Update every use of kind/old to kind/new in place. Returns (count, set of changed sections)."""
    n, changed = 0, set()
    for pl in data.get("playlists", {}).values():
        for item in pl.get("items", []):
            if spec_rename(item, kind, old, new):
                n += 1
                changed.add("playlists")
    for comp in data.get("compositions", {}).values():
        for layer in comp.get("layers", []):
            if spec_rename(layer, kind, old, new):
                n += 1
                changed.add("compositions")
    if kind == "playlist":
        sched = data.get("schedule", {})
        if sched.get("default") == old:
            sched["default"] = new
            n += 1
            changed.add("schedule")
        for rule in sched.get("rules", []):
            if rule.get("playlist") == old:
                rule["playlist"] = new
                n += 1
                changed.add("schedule")
    for rule in data.get("rules", []):
        a = rule.get("action", {})
        hit = 0
        if a.get("type") == "app":
            hit = spec_rename(a, kind, old, new)
        elif (a.get("type"), kind) in (("playlist", "playlist"), ("composition", "composition")) and a.get(kind) == old:
            a[kind] = new
            hit = 1
        elif a.get("type") == "notify" and kind == "scene" and a.get("scene") == old:
            a["scene"] = new
            hit = 1
        if hit:
            n += 1
            changed.add("rules")
    return n, changed


def _drop_layers(spec, kind, key):
    """Remove nested layers that use kind/key from a Layers spec. Returns True if any went."""
    layers = (spec.get("params") or {}).get("layers")
    if not isinstance(layers, list):
        return False
    keep = [l for l in layers if not (isinstance(l, dict) and spec_uses(l, kind, key))]
    gone = len(keep) != len(layers)
    for l in keep:
        if isinstance(l, dict):
            gone = _drop_layers(l, kind, key) or gone
    spec["params"]["layers"] = keep
    return gone


def remove(data, kind, key):
    """Clean up after kind/key was deleted: drop playlist items and layers that use it, point schedule
    rules at nothing, and turn off automations that use it. Returns (list of what changed, set of sections)."""
    done, changed = [], set()
    for name, pl in data.get("playlists", {}).items():
        items = pl.get("items", [])
        for i in reversed(range(len(items))):
            item = items[i]
            if _drop_layers(item, kind, key) and not spec_uses(item, kind, key):
                done.append(f'Playlist "{name}", item {i + 1}: layer removed')
                changed.add("playlists")
            elif spec_uses(item, kind, key):
                del items[i]
                done.append(f'Playlist "{name}", item {i + 1}: removed')
                changed.add("playlists")
    for name, comp in data.get("compositions", {}).items():
        layers = comp.get("layers", [])
        for i in reversed(range(len(layers))):
            _drop_layers(layers[i], kind, key)
            if spec_uses(layers[i], kind, key):
                del layers[i]
                done.append(f'Layer set "{name}", layer {i + 1}: removed')
                changed.add("compositions")
    if kind == "playlist":
        sched = data.get("schedule", {})
        if sched.get("default") == key:
            sched["default"] = ""
            done.append("Schedule: default playlist set to Nothing")
            changed.add("schedule")
        for i, rule in enumerate(sched.get("rules", [])):
            if rule.get("playlist") == key:
                rule["playlist"] = ""
                done.append(_sched_label(i, rule) + ": set to Nothing")
                changed.add("schedule")
    for rule in data.get("rules", []):
        if _action_uses(rule.get("action", {}), kind, key) and rule.get("enabled", True):
            rule["enabled"] = False
            done.append(_rule_label(rule) + ": turned off")
            changed.add("rules")
    return done, changed
