"""High scores for every game: one table in the data folder (scores.json, copied into storage.db
like any other JSON save), a top 5 per game with arcade-style three-letter initials.

A finished game's score is saved straight away with no name, so it isn't lost if nobody types
initials; the initials entry then fills in the name. Keys are the app id, or "id:variant" for
games that keep separate tables (Minesweeper per difficulty, Sokoban per level).

    {"games": {"snake": {"lower": false, "top": [{"id": "...", "score": 41, "name": "XDN", "at": 1760000000}]}},
     "initials": "XDN",
     "played": {"snake": 12},                       # scored games per game (not demos)
     "recent": [{"id", "key", "score", "name", "at"}],  # the last RECENT scores, for "today" on the leaderboard
     "achievements": {"first": {"at": ..., "key": "snake"}}}

Achievements (badges) are earned from this table alone: the first score, a new best, how many games were
played, a score in every game of a group, initials entered, late and early scores. Each is awarded once.
"""

import datetime as dt
import logging
import threading
import time

log = logging.getLogger("scores")

FILE = "scores.json"
RECENT = 200
OLD_PUZZLE = "puzzle_scores.json"  # before the shared table: one best per puzzle key, no names
TOP = 5
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
LOWER_KEYS = ("minesweeper", "sokoban", "mastermind")  # imported puzzle bests where smaller is better

ACHIEVEMENTS = [  # (id, title, how; the title fits the board in SMALL)
    ("first", "FIRST SCORE", "Your first score on the board"),
    ("best", "NEW BEST", "Beat the best score in a game that already had one"),
    ("named", "SIGNED IN", "Put your initials on a score"),
    ("ten", "10 GAMES", "Ten games scored"),
    ("fifty", "50 GAMES", "Fifty games scored"),
    ("hundred", "100 GAMES", "A hundred games scored"),
    ("classics", "CLASSIC SET", "A score in every classic: Snake, Tetris, Pong, Breakout, Invaders, Tron, Flappy"),
    ("arcade", "ARCADE TOUR", "A score in every arcade game"),
    ("runners", "MARATHON", "A score in every runner and racer"),
    ("puzzles", "PUZZLER", "A score in every puzzle"),
    ("full", "FIVE DEEP", "Five scores on one game's table"),
    ("owl", "NIGHT OWL", "A score between midnight and five"),
    ("bird", "EARLY BIRD", "A score before seven in the morning"),
    ("streak", "ON A ROLL", "Five games in one hour"),
]
GROUPS = {  # the game sets the group badges want (by score key prefix)
    "classics": ["snake", "tetris", "pong", "breakout", "invaders", "tron", "flappy"],
    "arcade": ["asteroids", "galaxian", "missile_command", "centipede", "lunar_lander"],
    "runners": ["dino", "racer", "copter", "stacker", "skyjump", "pitfall"],
    "puzzles": ["game2048", "minesweeper", "sokoban", "lights_out", "simon", "columns", "mastermind"],
}
COUNTS = {"ten": 10, "fifty": 50, "hundred": 100}

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
            game = key.split(":")[0]
            played = data.setdefault("played", {})
            played[game] = played.get(game, 0) + 1
            recent = data.setdefault("recent", [])
            recent.append({"id": eid, "key": key, "score": value, "name": "", "at": mine["at"]})
            del recent[:-RECENT]
        for r in data.get("recent", []):
            if r["id"] == eid:
                r["score"] = value
        # equal scores: the older entry stays ahead
        top.sort(key=lambda e: (e["score"] if lower else -e["score"], e["at"]))
        del top[TOP:]
        rank = top.index(mine) if mine in top else None
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
        for r in data.get("recent", []):
            if r["id"] == eid:
                r["name"] = initials
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


# ---- achievements ---------------------------------------------------------------------------------------------

def achievements(store):
    """[{id, title, how, at, key}]: every badge, earned ones with when and in which game."""
    with _lock:
        got = _data(store).get("achievements", {})
        return [{"id": a, "title": t, "how": h, **(got.get(a) or {"at": None, "key": None})} for a, t, h in ACHIEVEMENTS]


def played(store):
    with _lock:
        return dict(_data(store).get("played", {}))


def recent(store, since=None):
    """The latest scores, newest first (all of them, or those filed at/after `since`)."""
    with _lock:
        out = [dict(r) for r in _data(store).get("recent", [])]
    out.reverse()
    return [r for r in out if since is None or r["at"] >= since]


def _unlock(data, aid, key, now):
    got = data.setdefault("achievements", {})
    if aid in got:
        return None
    got[aid] = {"at": int(now), "key": key}
    return aid


def award(store, key, rank, had_table, now=None):
    """Called when a game filed a score: the badges this score earns, as [(id, title)], each only the first time."""
    now = time.time() if now is None else now
    game = key.split(":")[0]
    new = []
    with _lock:
        data = _data(store)
        played = data.get("played", {})
        total = sum(played.values())
        checks = [("first", total >= 1), ("best", rank == 0 and had_table)]
        checks += [(aid, total >= n) for aid, n in COUNTS.items()]
        scored = {k.split(":")[0] for k, g in data["games"].items() if g["top"]} | set(played)
        checks += [(aid, all(g in scored for g in ids)) for aid, ids in GROUPS.items()]
        checks.append(("full", len((data["games"].get(key) or {}).get("top", [])) >= TOP))
        hour = dt.datetime.fromtimestamp(now).hour
        checks += [("owl", 0 <= hour < 5), ("bird", hour < 7)]
        hour_ago = [r for r in data.get("recent", []) if now - r["at"] <= 3600]
        checks.append(("streak", len(hour_ago) >= 5))
        for aid, ok in checks:
            if ok and _unlock(data, aid, key, now):
                new.append(aid)
        if new:
            _write(store, data)
    titles = {a: t for a, t, _ in ACHIEVEMENTS}
    return [(a, titles[a]) for a in new]


def award_named(store, key, now=None):
    """Initials entered: the SIGNED IN badge (once)."""
    with _lock:
        data = _data(store)
        if _unlock(data, "named", key, time.time() if now is None else now):
            _write(store, data)
            return [("named", "SIGNED IN")]
    return []


def short(n):
    n = int(n) if float(n).is_integer() else n
    return str(n) if abs(n) < 100000 else f"{int(n) // 1000}K"
