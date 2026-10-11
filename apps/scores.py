"""High scores for every game: one table in the data folder (scores.json, copied into storage.db
like any other JSON save), a top 5 per game with arcade-style three-letter initials.

A finished game's score is saved straight away with no name, so it isn't lost if nobody types
initials; the initials entry then fills in the name. Keys are the app id, or "id:variant" for
games that keep separate tables (Minesweeper per difficulty, Sokoban per level).

    {"games": {"snake": {"lower": false, "top": [{"id": "...", "score": 41, "name": "XDN", "at": 1760000000}]}},
     "initials": "XDN"}
"""

import logging
import threading
import time

log = logging.getLogger("scores")

FILE = "scores.json"
OLD_PUZZLE = "puzzle_scores.json"  # before the shared table: one best per puzzle key, no names
TOP = 5
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
LOWER_KEYS = ("minesweeper", "sokoban", "mastermind")  # imported puzzle bests where smaller is better

_lock = threading.RLock()
_cache = {}  # id(store) -> (store, data)
_seq = [0]


def _fresh(store):
    data = store._load(FILE, None)
    if isinstance(data, dict) and isinstance(data.get("games"), dict):
        return data
    data = {"games": {}, "initials": "AAA"}
    try:
        old = store._load(OLD_PUZZLE, {}) or {}
    except Exception:
        old = {}
    for key, value in old.items():
        if isinstance(value, (int, float)):
            data["games"][key] = {"lower": key.split(":")[0] in LOWER_KEYS,
                                  "top": [{"id": f"old-{key}", "score": value, "name": "", "at": 0}]}
    if old:
        _write(store, data)
    return data


def _data(store):
    hit = _cache.get(id(store))
    if hit is None or hit[0] is not store:
        hit = _cache[id(store)] = (store, _fresh(store))
    return hit[1]


def _write(store, data):
    try:
        store._save(FILE, data)
    except Exception as e:
        log.warning("can't save high scores: %s", e)


def _better(a, b, lower):
    """Is score a strictly better than b?"""
    return a < b if lower else a > b


def table(store, key):
    """The top entries for a game, best first (copies)."""
    with _lock:
        g = _data(store)["games"].get(key)
        return [dict(e) for e in g["top"]] if g else []


def best(store, key):
    """The best entry for a game, or None."""
    t = table(store, key)
    return t[0] if t else None


def submit(store, key, value, lower=False, eid=None):
    """File a score. With eid (from an earlier submit in the same game) that entry is updated
    instead, if the new value is better. Returns (eid, rank): rank is 0 for a new best, None
    when the score didn't make the table."""
    with _lock:
        data = _data(store)
        g = data["games"].setdefault(key, {"lower": bool(lower), "top": []})
        lower = g["lower"]  # a table keeps the direction it was made with
        top = g["top"]
        mine = next((e for e in top if eid and e["id"] == eid), None)
        if mine is not None:
            if not _better(value, mine["score"], lower):
                return eid, top.index(mine)
            mine["score"], mine["at"] = value, int(time.time())
        else:
            _seq[0] += 1
            eid = eid or f"{int(time.time() * 1000):x}-{_seq[0]}"
            mine = {"id": eid, "score": value, "name": "", "at": int(time.time())}
            top.append(mine)
        # equal scores: the older entry stays ahead
        top.sort(key=lambda e: (e["score"] if lower else -e["score"], e["at"]))
        del top[TOP:]
        rank = top.index(mine) if mine in top else None
        if rank is not None:
            _write(store, data)
        return eid, rank


def name(store, key, eid, initials):
    """Put initials on an entry; also remembered as the default for the next entry."""
    initials = "".join(ch for ch in str(initials).upper() if ch in LETTERS)[:3]
    with _lock:
        data = _data(store)
        for e in (data["games"].get(key) or {}).get("top", []):
            if e["id"] == eid:
                e["name"] = initials
        if initials.strip():
            data["initials"] = initials.ljust(3)
        _write(store, data)


def last_initials(store):
    with _lock:
        s = str(_data(store).get("initials") or "AAA").upper()
    return "".join(ch if ch in LETTERS else "A" for ch in s.ljust(3)[:3])


def everything(store):
    """All tables, for GET /api/scores: {key: {"lower", "top"}}."""
    with _lock:
        return {k: {"lower": g["lower"], "top": [dict(e) for e in g["top"]]} for k, g in _data(store)["games"].items()}


def short(n):
    n = int(n) if float(n).is_integer() else n
    return str(n) if abs(n) < 100000 else f"{int(n) // 1000}K"
