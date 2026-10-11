"""Durable storage in one SQLite file (../storage.db by default, next to the app folder).

The database is the source of truth; the data folder is a working copy that the rest of the code
reads and writes as plain files. At startup files missing from the data folder are restored from
the database (or, on first run, the data folder is imported). After that, JSON saves and uploads
are copied in straight away and a background mirror picks up anything else (e.g. files written by
libraries) every few seconds.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("storage")

SCHEMA_VERSION = 1
MIRROR_EVERY = 15  # seconds: saves and uploads are copied in at once (sync); this catches files written by hand
RESTORE_SUFFIX = ".restore"  # an uploaded backup waiting to replace the database at the next start


def _migrate(db, version):
    """Bring the schema from `version` (PRAGMA user_version) up to SCHEMA_VERSION."""
    if version < 1:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, mtime REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, data BLOB NOT NULL, size INTEGER NOT NULL,
                                              mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL);
        """)
    # future: if version < 2: ...
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def check_backup(path):
    """Raise ValueError unless `path` is a storage database this version can use. Returns the file count."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("the backup is damaged (integrity check failed)")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"kv", "files"} <= tables or version < 1:
                raise ValueError("that isn't a flipdot storage backup")
            if version > SCHEMA_VERSION:
                raise ValueError("that backup is from a newer version of the app")
            return db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            db.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"that isn't a SQLite database ({e})") from None


class Storage:
    def __init__(self, db_path, data_dir):
        self.path = Path(db_path)
        self.dir = Path(data_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.restored_backup = self._apply_pending_restore()
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # with WAL: durable across app crashes, cheap on SD cards
        with self._lock, self.db:
            self.db.execute("BEGIN")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                _migrate(self.db, version)
        # path -> (size, mtime_ns) of what's in the database, so the mirror only needs stat() calls
        self.index = {p: (s, m) for p, s, m in self.db.execute("SELECT path, size, mtime_ns FROM files")}
        self.last_sync = None
        self._stop = threading.Event()

    # ---- key/value JSON ------------------------------------------------------------

    def get(self, key, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)", (key, json.dumps(value), time.time()))

    def delete(self, key):
        with self._lock:
            self.db.execute("DELETE FROM kv WHERE key=?", (key,))

    # ---- files (paths relative to the data folder, with forward slashes) -------------

    def put_file(self, rel, data, mtime_ns=None):
        mtime_ns = mtime_ns if mtime_ns is not None else time.time_ns()
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)", (rel, data, len(data), mtime_ns, sha))
            self.index[rel] = (len(data), mtime_ns)

    def get_file(self, rel):
        """The file's bytes, or None if it's not stored or fails its checksum."""
        with self._lock:
            row = self.db.execute("SELECT data, sha256 FROM files WHERE path=?", (rel,)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            log.error("stored copy of %s is damaged (checksum mismatch)", rel)
            return None
        return bytes(row[0])

    def delete_file(self, rel):
        """Delete a file, or everything under it if it's a folder."""
        with self._lock:
            self.db.execute("DELETE FROM files WHERE path=? OR (path >= ? AND path < ?)", (rel, rel + "/", rel + "0"))
            for p in [p for p in self.index if p == rel or p.startswith(rel + "/")]:
                del self.index[p]

    def list_files(self):
        with self._lock:
            return dict(self.index)

    # ---- keeping the data folder and the database in step ----------------------------

    def _rel(self, path):
        return Path(path).resolve().relative_to(self.dir.resolve()).as_posix()

    def _scan(self):
        """rel path -> (size, mtime_ns) for every file in the data folder (temp files skipped)."""
        out = {}
        stack = [self.dir]
        while stack:
            d = stack.pop()
            try:
                entries = list(os.scandir(d))
            except FileNotFoundError:
                continue
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False) and not e.name.endswith(".tmp"):
                    st = e.stat(follow_symlinks=False)
                    out[Path(e.path).relative_to(self.dir).as_posix()] = (st.st_size, st.st_mtime_ns)
        return out

    def _import(self, rel, stat=None):
        f = self.dir / rel
        try:
            data = f.read_bytes()
            mtime = f.stat().st_mtime_ns if stat is None else stat[1]
        except FileNotFoundError:
            return False
        self.put_file(rel, data, mtime)
        return True

    def _write_out(self, rel):
        data = self.get_file(rel)
        if data is None:
            return False
        f = self.dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".restore.tmp")
        tmp.write_bytes(data)
        mtime = self.index[rel][1]
        os.utime(tmp, ns=(mtime, mtime))
        tmp.replace(f)
        return True

    def startup(self):
        """Run before anything reads the data folder. Returns a short summary for the log."""
        self.dir.mkdir(parents=True, exist_ok=True)
        disk = self._scan()
        imported = restored = removed = 0
        with self._lock:
            if not self.index:
                # first run (or a fresh database): take everything that's in the data folder
                for rel, st in disk.items():
                    imported += self._import(rel, st)
                if disk:
                    log.info("storage: imported %d files from %s into %s", imported, self.dir, self.path)
            else:
                for rel, (size, mtime) in list(self.index.items()):
                    st = disk.get(rel)
                    if st == (size, mtime):
                        continue
                    if st and st[1] > mtime and not self.restored_backup:
                        imported += self._import(rel, st)  # changed after the last sync (e.g. a crash): keep the newer copy
                    else:
                        restored += self._write_out(rel)
                for rel, st in disk.items():
                    if rel in self.index:
                        continue
                    if self.restored_backup:
                        (self.dir / rel).unlink(missing_ok=True)  # make the data folder match the backup exactly
                        removed += 1
                    else:
                        imported += self._import(rel, st)
        self.last_sync = time.time()
        self.set("last_start", {"time": self.last_sync, "imported": imported, "restored": restored, "removed": removed})
        msg = f"storage: {len(self.index)} files in {self.path} ({restored} restored, {imported} imported" + \
              (f", {removed} removed after restoring a backup)" if self.restored_backup else ")")
        log.info(msg)
        return msg

    def sync(self, *paths):
        """Copy these files (or folders) from the data folder into the database now; missing ones are deleted."""
        with self._lock:
            for p in paths:
                f = Path(p) if Path(p).is_absolute() else self.dir / p
                rel = self._rel(f)
                if f.is_dir():
                    seen = set()
                    for sub in f.rglob("*"):
                        if sub.is_file() and not sub.name.endswith(".tmp"):
                            r = self._rel(sub)
                            seen.add(r)
                            st = sub.stat()
                            if self.index.get(r) != (st.st_size, st.st_mtime_ns):
                                self._import(r)
                    for r in [r for r in self.index if r.startswith(rel + "/") and r not in seen]:
                        self.delete_file(r)
                elif f.is_file():
                    self._import(rel)
                else:
                    self.delete_file(rel)
            self.last_sync = time.time()

    def mirror_once(self):
        """Copy new/changed files into the database and drop rows for deleted ones. Returns (changed, deleted)."""
        disk = self._scan()
        changed = deleted = 0
        if not disk and self.index:
            # the whole data folder vanished (deleted by hand, unmounted…): don't take that as "delete everything"
            log.warning("storage: data folder %s is empty or missing; not mirroring deletions", self.dir)
            return 0, 0
        with self._lock:
            for rel, st in disk.items():
                if self.index.get(rel) != st:
                    changed += self._import(rel, st)
            for rel in [r for r in self.index if r not in disk]:
                self.delete_file(rel)
                deleted += 1
            self.last_sync = time.time()
        return changed, deleted

    def start_mirror(self, every=MIRROR_EVERY):
        def loop():
            while not self._stop.wait(every):
                try:
                    self.mirror_once()
                except Exception:
                    log.exception("storage mirror failed")
        threading.Thread(target=loop, daemon=True, name="storage-mirror").start()

    def flush(self):
        """Final sync at shutdown."""
        self._stop.set()
        try:
            self.mirror_once()
            with self._lock:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            log.exception("storage flush failed")

    # ---- status, backup and restore ----------------------------------------------------

    def info(self):
        size = sum(p.stat().st_size for p in (self.path, Path(f"{self.path}-wal")) if p.exists())
        with self._lock:
            files = len(self.index)
            data_bytes = sum(s for s, _ in self.index.values())
        return {"path": str(self.path.resolve()), "data_dir": str(self.dir.resolve()), "size": size, "files": files,
                "data_bytes": data_bytes, "last_sync": self.last_sync,
                "restore_pending": Path(str(self.path) + RESTORE_SUFFIX).exists()}

    def backup_to(self, dest):
        """A consistent copy of the database (safe while it's in use)."""
        out = sqlite3.connect(str(dest))
        try:
            with self._lock:
                self.db.backup(out)
        finally:
            out.close()

    def stage_restore(self, data):
        """Validate an uploaded backup and put it in place to replace the database at the next start."""
        staged = Path(str(self.path) + RESTORE_SUFFIX)
        tmp = staged.with_name(staged.name + ".tmp")
        tmp.write_bytes(data)
        try:
            files = check_backup(tmp)
        except ValueError:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(staged)
        return files

    def _apply_pending_restore(self):
        staged = Path(str(self.path) + RESTORE_SUFFIX)
        if not staged.exists():
            return False
        try:
            check_backup(staged)
        except ValueError as e:
            log.error("storage: ignoring staged backup %s: %s", staged, e)
            staged.rename(staged.with_name(staged.name + ".rejected"))
            return False
        if self.path.exists():
            # keep the database being replaced, just in case
            src = sqlite3.connect(str(self.path))
            out = sqlite3.connect(str(self.path) + ".before-restore")
            try:
                src.backup(out)
            finally:
                out.close()
                src.close()
        for suffix in ("-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        shutil.move(str(staged), str(self.path))
        log.info("storage: restored the database from an uploaded backup")
        return True
