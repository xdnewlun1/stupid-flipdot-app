"""Backups of storage.db into a folder (../backups by default): by hand, on a schedule, and before an update.

A backup is a copy of the database made with SQLite's online backup in small steps, so the board keeps saving while
it runs. config.local.yaml (this board's panel mapping, password…) rides along inside it, in the kv table, and is put
back when the backup is restored. Only the newest `keep` files named flipdot-backup-*.db are kept; anything else in
the folder is left alone.
"""

import logging
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import localconfig

log = logging.getLogger("backups")

DEFAULTS = {"schedule": "off", "time": "03:00", "weekday": 0, "folder": "../backups", "keep": 7}
SCHEDULES = ("off", "daily", "weekly")
NAME_RE = re.compile(r"^flipdot-backup-(\d{8}-\d{6})(?:-([a-z][a-z-]*?))?(?:-(\d+))?\.db$")
MARGIN = 50 * 2**20  # bytes to leave free on the target besides the backup itself
CHECK_EVERY = 30.0  # seconds between schedule checks
SETTLE = 120.0  # no scheduled backup in the first minutes after a start (catch-up runs after that)
CONFIG_KEY = "config_local"  # kv row with config.local.yaml's text


# ---- config.local.yaml inside the database -------------------------------------------------

def save_config_copy(persist, config_path):
    """Keep config.local.yaml's text in the database, so every backup has it."""
    p = localconfig.local_path(config_path)
    try:
        text = p.read_text()
    except FileNotFoundError:
        text = None
    if persist.get(CONFIG_KEY) != {"name": p.name, "text": text}:
        persist.set(CONFIG_KEY, {"name": p.name, "text": text})


def restore_config_copy(persist, config_path):
    """After a restored backup: write its config.local.yaml back (the one replaced is kept as .before-restore).
    Returns True when the file was replaced."""
    saved = persist.get(CONFIG_KEY)
    if not saved or saved.get("text") is None:
        return False
    p = localconfig.local_path(config_path)
    if p.exists():
        if p.read_text() == saved["text"]:
            return False
        shutil.copy2(p, p.with_name(p.name + ".before-restore"))
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(saved["text"])
    tmp.chmod(0o600)
    tmp.replace(p)
    log.info("backups: restored %s from the backup", p.name)
    return True


# ---- the schedule ----------------------------------------------------------------------------

def parse_hhmm(s):
    try:
        h, m = (int(x) for x in str(s).split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    raise ValueError(f"{s!r} isn't a time like 03:00")


def last_slot(cfg, now):
    """The most recent scheduled time at or before `now` (a datetime), or None when backups are off."""
    if cfg.get("schedule") not in ("daily", "weekly"):
        return None
    h, m = parse_hhmm(cfg.get("time", "03:00"))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    if cfg["schedule"] == "weekly":
        slot -= timedelta(days=(slot.weekday() - int(cfg.get("weekday", 0))) % 7)
    return slot


def next_slot(cfg, now):
    slot = last_slot(cfg, now)
    if slot is None:
        return None
    return slot + timedelta(days=7 if cfg["schedule"] == "weekly" else 1)


def clean_settings(new):
    """Validate a {"backup": {...}} settings section from the web page."""
    out = {}
    if "schedule" in new:
        if new["schedule"] not in SCHEDULES:
            raise ValueError("schedule must be off, daily or weekly")
        out["schedule"] = new["schedule"]
    if "time" in new:
        h, m = parse_hhmm(new["time"])
        out["time"] = f"{h:02d}:{m:02d}"
    if "weekday" in new:
        out["weekday"] = int(new["weekday"]) % 7
    if "keep" in new:
        keep = int(new["keep"])
        if not 1 <= keep <= 365:
            raise ValueError("keep between 1 and 365 backups")
        out["keep"] = keep
    if "folder" in new:
        folder = str(new["folder"]).strip()
        if not folder:
            raise ValueError("choose a folder for backups")
        out["folder"] = folder
    return out


class Backups:
    def __init__(self, persist, settings, root, config_path):
        """settings(): the current {"backup": …} section (a dict, possibly empty); root: the app folder, which a
        relative backup folder is taken from."""
        self.persist = persist
        self._settings = settings
        self.root = Path(root)
        self.config_path = config_path
        self.lock = threading.Lock()
        self.running = None  # tag of the backup in progress
        self.started = time.time()
        self._stop = threading.Event()

    def settings(self):
        return {**DEFAULTS, **(self._settings() or {})}

    def folder(self, cfg=None):
        f = Path((cfg or self.settings())["folder"]).expanduser()
        return f if f.is_absolute() else (self.root / f).resolve()

    # ---- making, listing, deleting ----

    def needed_bytes(self):
        db = self.persist.path
        return sum(p.stat().st_size for p in (db, Path(f"{db}-wal")) if p.exists())

    def make(self, tag="manual"):
        """Write one backup now. Returns its listing entry; raises ValueError with the reason when it can't."""
        if not self.lock.acquire(blocking=False):
            raise ValueError("a backup is already being made")
        t0 = time.time()
        self.running = tag
        try:
            cfg = self.settings()
            folder = self.folder(cfg)
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ValueError(f"can't create the backup folder {folder}: {e.strerror or e}") from None
            need = self.needed_bytes()
            free = shutil.disk_usage(folder).free
            if free < need + MARGIN:
                raise ValueError(f"not enough free space in {folder}: the backup needs about {need // 2**20 + 1} MB "
                                 f"plus {MARGIN // 2**20} MB to spare, {free // 2**20} MB are free")
            self.persist.mirror_once()  # include anything written in the last few seconds
            save_config_copy(self.persist, self.config_path)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
            # more in the same second get -2, -3… (after every number already used, so they sort in order)
            same = [m for m in (NAME_RE.match(p.name) for p in folder.glob(f"flipdot-backup-{stamp}*.db")) if m]
            n = max((int(m.group(3) or 1) for m in same), default=0) + 1
            name = f"flipdot-backup-{stamp}-{tag}{f'-{n}' if n > 1 else ''}.db"
            dest = folder / name
            tmp = folder / (name + ".partial")
            try:
                self.persist.backup_to(tmp)
                tmp.replace(dest)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            removed = self.rotate(cfg["keep"])
            entry = self._entry(dest)
            self._record(tag, True, t0, name=name, size=entry["size"], seconds=round(time.time() - t0, 1),
                         removed=removed)
            log.info("backups: wrote %s (%.1f MB, %.1f s)%s", dest, entry["size"] / 2**20, time.time() - t0,
                     f", removed {len(removed)} old" if removed else "")
            return entry
        except Exception as e:
            msg = str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"
            self._record(tag, False, t0, error=msg)
            log.error("backups: %s backup failed: %s", tag, msg)
            raise ValueError(msg) from None
        finally:
            self.running = None
            self.lock.release()

    def _record(self, tag, ok, when, **extra):
        status = self.persist.get("backup_status") or {}
        rec = {"time": when, "ok": ok, "tag": tag, **extra}
        status["last"] = rec
        if tag == "auto":
            status["last_auto"] = rec
        self.persist.set("backup_status", status)

    def _entry(self, path):
        st = path.stat()
        m = NAME_RE.match(path.name)
        made = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp() if m else st.st_mtime
        return {"name": path.name, "size": st.st_size, "time": made, "tag": (m.group(2) if m else None) or "manual"}

    def list(self):
        folder = self.folder()
        try:
            files = [p for p in folder.iterdir() if NAME_RE.match(p.name) and p.is_file()]
        except FileNotFoundError:
            return []
        def order(p):  # by time, then the -2, -3… of backups made in the same second
            m = NAME_RE.match(p.name)
            return m.group(1), int(m.group(3) or 1)
        return [self._entry(p) for p in sorted(files, key=order, reverse=True)]

    def path(self, name):
        """The backup file called `name` (only names this module writes, so no path tricks)."""
        if not NAME_RE.match(name or ""):
            raise ValueError("no such backup")
        p = self.folder() / name
        if not p.is_file():
            raise ValueError("no such backup")
        return p

    def delete(self, name):
        self.path(name).unlink()

    def rotate(self, keep):
        """Delete all but the newest `keep` backups. Returns the names removed."""
        old = self.list()[max(1, int(keep)):]
        for e in old:
            (self.folder() / e["name"]).unlink(missing_ok=True)
        return [e["name"] for e in old]

    # ---- status and the scheduler ----

    def status(self):
        cfg = self.settings()
        st = self.persist.get("backup_status") or {}
        folder = self.folder(cfg)
        try:
            free = shutil.disk_usage(folder if folder.exists() else folder.parent).free
        except OSError:
            free = None
        nxt = next_slot(cfg, datetime.now()) if cfg["schedule"] != "off" else None
        return {"settings": cfg, "folder": str(folder), "free": free, "needed": self.needed_bytes(),
                "last": st.get("last"), "last_auto": st.get("last_auto"), "running": self.running,
                "next": nxt.timestamp() if nxt else None, "backups": self.list()}

    def due(self, now=None):
        """True when a scheduled backup should run now: the latest slot has passed since the schedule was switched
        on and no scheduled backup has been tried since that slot (a slot missed while the server was off is caught
        up once, after it starts)."""
        cfg = self.settings()
        if cfg["schedule"] == "off":
            if self.persist.get("backup_armed") is not None:
                self.persist.delete("backup_armed")
            return False
        now = now or datetime.now()
        armed = self.persist.get("backup_armed")
        if armed is None or armed.get("schedule") != [cfg["schedule"], cfg["time"], cfg["weekday"]]:
            # just switched on (or changed): the first backup is at the next slot, not straight away
            self.persist.set("backup_armed", {"time": now.timestamp(), "schedule": [cfg["schedule"], cfg["time"], cfg["weekday"]]})
            return False
        slot = last_slot(cfg, now)
        last = (self.persist.get("backup_status") or {}).get("last_auto") or {}
        return slot.timestamp() > max(armed["time"], last.get("time", 0))

    def tick(self, now=None):
        if time.time() - self.started < SETTLE or self.running:
            return False
        if not self.due(now):
            return False
        try:
            self.make("auto")
        except ValueError:
            pass  # recorded in the status; the next slot tries again
        return True

    def start(self):
        def loop():
            while not self._stop.wait(CHECK_EVERY):
                try:
                    self.tick()
                except Exception:
                    log.exception("backup scheduler failed")
        threading.Thread(target=loop, daemon=True, name="backups").start()

    def stop(self):
        self._stop.set()
