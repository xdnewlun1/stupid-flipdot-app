"""This board's own settings, kept out of the tracked config.yaml.

config.yaml (in git) holds the defaults; config.local.yaml next to it (git-ignored) overrides it key by key. Every
setting the web page writes (the panel mapping, …) goes into the local file, so `git pull` never conflicts with it and
a password never ends up in a commit. With `--config other.yaml` the local file is `other.local.yaml`.

First run: when there's no local file yet, every top-level key whose value in config.yaml differs from the committed
version (`git show HEAD:config.yaml`) is copied into it, so values edited by hand stay in force after config.yaml is
put back to the repo's version.
"""

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from flipdot import parse_config

log = logging.getLogger("localconfig")

HEADER = ("# This board's own settings: they override config.yaml key by key. Not tracked by git, and written by\n"
          "# the web page (panel setup…), so keep hand edits simple: one `key: value` per line, `panels:` as in config.yaml.\n")
_lock = threading.Lock()


def local_path(config_path):
    p = Path(config_path)
    return p.with_name(f"{p.stem}.local{p.suffix or '.yaml'}")


def merge(base, over):
    """`over` on top of `base`: dicts merge key by key, anything else (lists such as panels too) is replaced."""
    out = dict(base or {})
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def read_local(config_path):
    p = local_path(config_path)
    try:
        return parse_config(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def load(config_path):
    """config.yaml with config.local.yaml on top."""
    return merge(parse_config(Path(config_path).read_text()), read_local(config_path))


# ---- writing ----------------------------------------------------------------------------

def _scalar(v):
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a JSON string is a valid double-quoted YAML scalar
    return json.dumps(v, ensure_ascii=False)  # lists/dicts as YAML flow style


def dump(cfg):
    """YAML that both PyYAML and flipdot's fallback parser read back: top-level scalars, `panels:`-style lists of
    flat dicts, and anything else as JSON flow style."""
    lines = []
    for k, v in cfg.items():
        if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
            lines.append(f"{k}:")
            for n, item in enumerate(v):
                if n:
                    lines.append("")
                for j, (ik, iv) in enumerate(item.items()):
                    lines.append(f"{'  - ' if j == 0 else '    '}{ik}: {_scalar(iv)}")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_local(config_path, cfg):
    p = local_path(config_path)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # it can hold the password
    with os.fdopen(fd, "w") as f:
        f.write(HEADER + "\n" + dump(cfg))
    tmp.replace(p)
    return p


def update(config_path, changes):
    """Set keys in config.local.yaml ({key: None} removes one). Returns the merged config."""
    with _lock:
        cfg = read_local(config_path)
        for k, v in changes.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
        write_local(config_path, cfg)
    return load(config_path)


# ---- first run: carry hand edits of config.yaml over ----------------------------------------

def committed_text(config_path):
    """config.yaml as committed (HEAD), or None when it isn't tracked by git (or there's no git)."""
    p = Path(config_path).resolve()
    try:
        r = subprocess.run(["git", "show", f"HEAD:./{p.name}"], cwd=p.parent, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def local_differences(current, default):
    """Top-level keys of `current` that the committed defaults don't have, or have with another value."""
    return {k: v for k, v in current.items() if k not in default or default[k] != v}


def migrate(config_path):
    """Create config.local.yaml once. Returns the keys carried over from config.yaml (empty if none or done before)."""
    p = local_path(config_path)
    if p.exists():
        return []
    with _lock:
        if p.exists():
            return []
        default_text = committed_text(config_path)
        changes = {}
        if default_text is not None:
            try:
                changes = local_differences(parse_config(Path(config_path).read_text()) or {},
                                            parse_config(default_text) or {})
            except Exception as e:  # a config.yaml git can't make sense of: leave it as it is
                log.warning("config: couldn't compare %s with the committed version: %s", config_path, e)
        write_local(config_path, changes)
    if changes:
        log.info("config: kept your values of %s in %s", ", ".join(changes), p.name)
    return list(changes)


def covers_edits(config_path):
    """True when config.yaml's uncommitted edits are all overridden by config.local.yaml, i.e. putting config.yaml
    back to the committed version changes no effective setting."""
    default_text = committed_text(config_path)
    if default_text is None:
        return False
    local = read_local(config_path)
    try:
        now = merge(parse_config(Path(config_path).read_text()) or {}, local)
        after = merge(parse_config(default_text) or {}, local)
    except Exception:
        return False
    return now == after
