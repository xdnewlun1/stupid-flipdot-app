"""config.local.yaml (merge, first-run migration), updates from git with rollback (against local bare repos only),
streamed restore memory, the stepped online backup, and scheduled backups with rotation.
Run: python3 -m unittest tests.test_ops"""

import builtins
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backups  # noqa: E402
import localconfig  # noqa: E402
import storage  # noqa: E402
import updater  # noqa: E402
from flipdot import parse_config  # noqa: E402

GIT_ID = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
          "-c", "init.defaultBranch=main"]


def git(cwd, *args):
    r = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


def commit_all(cwd, msg):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


CONFIG = textwrap.dedent("""\
    # Board settings
    panel_width: 28
    transport: tcp
    invert: false
    password: ""           # set to require a password
    sweep_ms: 85

    panels:
      - column: "1"
        row: "1"
        ip: "192.168.11.100"
        port: "5000"
        address: "0"
    """)


def without_yaml():
    """Make `import yaml` fail, to exercise flipdot's fallback parser."""
    real = builtins.__import__

    def imp(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml")
        return real(name, *a, **k)
    return mock.patch.object(builtins, "__import__", imp)


class LocalConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = self.dir / "config.yaml"
        self.cfg.write_text(CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_path_follows_config_name(self):
        self.assertEqual(localconfig.local_path("/x/config.yaml"), Path("/x/config.local.yaml"))
        self.assertEqual(localconfig.local_path("/x/other.yaml"), Path("/x/other.local.yaml"))

    def test_merge_key_by_key(self):
        base = {"a": 1, "panels": [{"column": 1}, {"column": 2}], "mqtt": {"host": "h", "port": 1}}
        out = localconfig.merge(base, {"a": 2, "panels": [{"column": 9}], "mqtt": {"port": 2}})
        self.assertEqual(out, {"a": 2, "panels": [{"column": 9}], "mqtt": {"host": "h", "port": 2}})
        self.assertEqual(base["mqtt"], {"host": "h", "port": 1})  # not modified in place

    def test_round_trip_both_parsers(self):
        cfg = {"password": 'p#ss "w" \\ ok # not a comment', "sweep_ms": 60, "invert": True,
               "panels": [{"column": 1, "row": 2, "ip": "10.0.0.5", "port": 5000, "address": 3, "rotate": 180}]}
        localconfig.write_local(self.cfg, cfg)
        self.assertEqual(localconfig.read_local(self.cfg), cfg)  # PyYAML
        with without_yaml():
            fallback = localconfig.read_local(self.cfg)
        self.assertEqual(fallback["password"], cfg["password"])
        self.assertEqual(fallback["invert"], True)
        self.assertEqual(str(fallback["sweep_ms"]), "60")
        self.assertEqual({k: str(v) for k, v in fallback["panels"][0].items()},
                         {k: str(v) for k, v in cfg["panels"][0].items()})
        mode = stat.S_IMODE(localconfig.local_path(self.cfg).stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_load_overrides_and_update(self):
        localconfig.update(self.cfg, {"password": "pw", "panels": [{"column": 2, "row": 1, "ip": "1.2.3.4", "port": 1,
                                                                     "address": 0}]})
        merged = localconfig.load(self.cfg)
        self.assertEqual(merged["password"], "pw")
        self.assertEqual(merged["panels"][0]["column"], 2)
        self.assertEqual(merged["transport"], "tcp")  # from config.yaml
        merged = localconfig.update(self.cfg, {"password": None})
        self.assertEqual(merged["password"], "")
        self.assertIn("panels", localconfig.read_local(self.cfg))
        self.assertEqual(self.cfg.read_text(), CONFIG)  # the tracked file is never written

    def test_no_git_creates_empty_local(self):
        self.assertEqual(localconfig.migrate(self.cfg), [])
        self.assertTrue(localconfig.local_path(self.cfg).exists())
        self.assertEqual(localconfig.read_local(self.cfg), {})
        self.assertEqual(localconfig.load(self.cfg), parse_config(CONFIG))

    def test_migration_keeps_hand_edits(self):
        git(self.dir, "init", "-q")
        commit_all(self.dir, "defaults")
        edited = (CONFIG.replace('password: ""', 'password: "hunter2"').replace("sweep_ms: 85", "sweep_ms: 40")
                  .replace('port: "5000"', 'port: "5002"') + "sync_refresh: true\n")
        self.cfg.write_text(edited)
        before = localconfig.load(self.cfg)

        moved = localconfig.migrate(self.cfg)
        self.assertEqual(sorted(moved), ["panels", "password", "sweep_ms", "sync_refresh"])
        local = localconfig.read_local(self.cfg)
        self.assertEqual(local["password"], "hunter2")
        self.assertNotIn("transport", local)
        self.assertEqual(localconfig.load(self.cfg), before)
        self.assertTrue(localconfig.covers_edits(self.cfg))
        self.assertEqual(localconfig.migrate(self.cfg), [])  # only once

        git(self.dir, "checkout", "--", "config.yaml")  # back to the repo version: settings unchanged
        self.assertEqual(self.cfg.read_text(), CONFIG)
        self.assertEqual(localconfig.load(self.cfg), before)

        self.cfg.write_text(CONFIG.replace("transport: tcp", "transport: udp"))  # a new edit not in local
        self.assertFalse(localconfig.covers_edits(self.cfg))

    def test_migration_with_fallback_parser(self):
        git(self.dir, "init", "-q")
        commit_all(self.dir, "defaults")
        self.cfg.write_text(CONFIG.replace('password: ""', 'password: "x y"'))
        with without_yaml():
            self.assertEqual(localconfig.migrate(self.cfg), ["password"])
            self.assertEqual(localconfig.load(self.cfg)["password"], "x y")


# ---- updates -----------------------------------------------------------------------------

SERVER_OK = "import apps\nVERSION = {n}\n"


class UpdaterTest(unittest.TestCase):
    """origin.git (bare) <- dev (pushes new commits) ; app = the installed checkout that updates itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.origin, self.dev, self.app = t / "origin.git", t / "dev", t / "app"
        git(t, "init", "-q", "--bare", str(self.origin))
        git(t, "clone", "-q", str(self.origin), str(self.dev))
        (self.dev / "apps").mkdir()
        (self.dev / "apps" / "__init__.py").write_text("")
        (self.dev / "server.py").write_text(SERVER_OK.format(n=1))
        (self.dev / "config.yaml").write_text(CONFIG)
        pip_version = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True).stdout.split()
        self.pip_pin = f"pip=={pip_version[1]}" if len(pip_version) > 1 else None
        (self.dev / "requirements-extra.txt").write_text((self.pip_pin or "nothing==0") + "\n")
        commit_all(self.dev, "first")
        git(self.dev, "push", "-q", "origin", "HEAD:main")
        git(t, "clone", "-q", str(self.origin), str(self.app))
        self.backups, self.restarts = [], []
        self.state = t / "update.json"
        self.up = self.make_updater()

    def make_updater(self, **kw):
        args = dict(backup=lambda tag: self.backups.append(tag), restart=lambda: self.restarts.append(1),
                    state_path=self.state, reset_config_ok=lambda: localconfig.covers_edits(self.app / "config.yaml"))
        args.update(kw)
        return updater.Updater(self.app, **args)

    def tearDown(self):
        self.tmp.cleanup()

    def push(self, msg, files):
        for name, text in files.items():
            (self.dev / name).write_text(text)
        sha = commit_all(self.dev, msg)
        git(self.dev, "push", "-q", "origin", "HEAD:main")
        return sha

    def head(self):
        return git(self.app, "rev-parse", "HEAD")

    def test_check_lists_incoming_commits(self):
        self.assertEqual(self.up.check()["incoming"], 0)
        self.push("Add a clock face", {"server.py": SERVER_OK.format(n=2)})
        self.push("Fix the ticker", {"server.py": SERVER_OK.format(n=3)})
        c = self.up.check()
        self.assertEqual(c["incoming"], 2)
        self.assertEqual([x["subject"] for x in c["commits"]], ["Fix the ticker", "Add a clock face"])
        self.assertTrue(c["can_update"])
        self.assertEqual((c["remote"], c["branch"]), ("origin", "main"))

    def test_update_success(self):
        prev = self.head()
        new = self.push("Add a clock face", {"server.py": SERVER_OK.format(n=2)})
        job = self.up.start(wait=True)
        self.assertEqual(job["state"], "restarting", job)
        self.assertEqual(self.head(), new)
        self.assertEqual(self.backups, ["before-update"])
        self.assertEqual(self.restarts, [1])
        self.assertEqual((job["from"], job["to"]), (prev, new))
        # the restarted server reads the result back
        again = self.make_updater()
        self.assertEqual(again.status()["state"], "done")
        self.assertIn(new[:8], again.status()["message"])

    def test_configurable_remote_and_branch(self):
        git(self.dev, "checkout", "-q", "-b", "beta")
        new = self.push("Beta feature", {"server.py": SERVER_OK.format(n=5)})
        git(self.dev, "push", "-q", "origin", "HEAD:beta")
        other = Path(self.tmp.name) / "mirror.git"
        git(self.tmp.name, "clone", "-q", "--bare", str(self.origin), str(other))
        up = self.make_updater(settings=lambda: {"remote": str(other), "branch": "beta"})
        c = up.check()
        self.assertEqual((c["remote"], c["branch"], c["incoming"]), (str(other), "beta", 1))
        self.assertEqual(up.start(wait=True)["state"], "restarting")
        self.assertEqual(self.head(), new)

    def test_dirty_tree_refused(self):
        prev = self.head()
        self.push("Something new", {"server.py": SERVER_OK.format(n=2)})
        (self.app / "server.py").write_text(SERVER_OK.format(n=99))
        c = self.up.check()
        self.assertFalse(c["can_update"])
        self.assertEqual(c["dirty"], ["server.py"])
        self.assertIn("server.py", c["blocked"])
        job = self.up.start(wait=True)
        self.assertEqual(job["state"], "failed")
        self.assertIn("server.py", job["error"])
        self.assertEqual(self.head(), prev)
        self.assertEqual(self.backups, [])  # refused before anything was done
        self.assertEqual((self.app / "server.py").read_text(), SERVER_OK.format(n=99))  # local edit untouched

    def test_own_commits_never_forced_away(self):
        self.push("Upstream change", {"server.py": SERVER_OK.format(n=2)})
        (self.app / "mine.txt").write_text("local work\n")
        mine = commit_all(self.app, "My own commit")
        c = self.up.check()
        self.assertEqual((c["incoming"], c["ahead"]), (1, 1))
        self.assertFalse(c["can_update"])
        self.assertIn("of its own", c["blocked"])
        job = self.up.start(wait=True)
        self.assertEqual(job["state"], "failed")
        self.assertIn("pull failed and nothing was changed", job["error"])
        self.assertEqual(self.head(), mine)
        self.assertEqual(self.restarts, [])

    def test_broken_version_rolls_back(self):
        prev = self.head()
        self.push("Broken", {"server.py": "import apps\nraise ImportError('missing module flux')\n"})
        job = self.up.start(wait=True)
        self.assertEqual(job["state"], "failed", job)
        self.assertIn("flux", job["error"])
        self.assertIn("Rolled back", job["error"])
        self.assertEqual(self.head(), prev)
        self.assertEqual((self.app / "server.py").read_text(), SERVER_OK.format(n=1))
        self.assertEqual(self.restarts, [])
        self.assertEqual(git(self.app, "status", "--porcelain", "--untracked-files=no"), "")

    @unittest.skipUnless(sys.prefix == sys.base_prefix, "needs a Python outside a venv")
    def test_requirements_skipped_outside_venv(self):
        self.push("Bump", {"requirements-extra.txt": "pip==0.0.1\n", "server.py": SERVER_OK.format(n=2)})
        job = self.up.start(wait=True)
        self.assertEqual(job["state"], "restarting")
        self.assertTrue(any("isn't running in a venv" in line for line in job["log"]))

    def test_failed_requirements_roll_back(self):
        if not self.pip_pin:
            self.skipTest("no pip")
        # a stand-in for the venv's python: says it's a venv, and pip install fails
        fake = Path(self.tmp.name) / "fakepython"
        fake.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            case "$*" in
              *"sys.prefix != sys.base_prefix"*) echo 1; exit 0;;
              *"-m pip install"*) echo "ERROR: No matching distribution found for pip==0.0.1" >&2; exit 1;;
            esac
            exec {sys.executable} "$@"
            """))
        fake.chmod(0o755)
        prev = self.head()
        self.push("Bump pip", {"requirements-extra.txt": "pip==0.0.1\n"})
        job = self.make_updater(python=str(fake)).start(wait=True)
        self.assertEqual(job["state"], "failed", job)
        self.assertIn("No matching distribution", job["error"])
        self.assertEqual(self.head(), prev)

    def test_untracked_requirements_of_other_venv_skipped(self):
        fake = Path(self.tmp.name) / "fakepython"
        fake.write_text(f'#!/bin/sh\ncase "$*" in *"sys.prefix != sys.base_prefix"*) echo 1; exit 0;; '
                        f'*"-m pip install"*) echo pip-called >&2; exit 1;; esac\nexec {sys.executable} "$@"\n')
        fake.chmod(0o755)
        (self.dev / "requirements-extra.txt").write_text("not-installed-pkg==1.0\n")
        commit_all(self.dev, "unrelated pins")
        git(self.dev, "push", "-q", "origin", "HEAD:main")
        git(self.app, "pull", "-q", "--ff-only")
        self.push("Bump", {"requirements-extra.txt": "not-installed-pkg==2.0\n"})
        job = self.make_updater(python=str(fake)).start(wait=True)
        self.assertEqual(job["state"], "restarting", job)
        self.assertTrue(any("skipped" in line for line in job["log"]))

    def test_backup_failure_changes_nothing(self):
        prev = self.head()
        self.push("New", {"server.py": SERVER_OK.format(n=2)})

        def fail(tag):
            raise ValueError("not enough free space")
        job = self.make_updater(backup=fail).start(wait=True)
        self.assertEqual(job["state"], "failed")
        self.assertIn("not enough free space", job["error"])
        self.assertEqual(self.head(), prev)

    def test_reset_config_only_when_covered(self):
        cfg = self.app / "config.yaml"
        cfg.write_text(CONFIG.replace('password: ""', 'password: "pw"'))
        self.assertFalse(self.up.check()["config_resettable"])  # no local file holds it yet
        with self.assertRaises(updater.UpdateError):
            self.up.reset_config()
        localconfig.migrate(cfg)
        self.assertTrue(self.up.check()["config_resettable"])
        self.up.reset_config()
        self.assertEqual(self.up.dirty(), [])
        self.assertEqual(localconfig.load(cfg)["password"], "pw")

    def test_restart_mode(self):
        self.assertTrue(updater.under_systemd({"NOTIFY_SOCKET": "/run/x"}))
        svc = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/flipdot.service\n"
        term = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/ptyxis-spawn-1.scope\n"
        self.assertTrue(updater.under_systemd({"INVOCATION_ID": "abc"}, cgroup=svc))
        self.assertFalse(updater.under_systemd({"INVOCATION_ID": "abc"}, cgroup=term))  # run by hand in a terminal
        self.assertTrue(updater.under_systemd({"INVOCATION_ID": "a"}, cgroup="1:name=systemd:/system.slice/flipdot.service\n"))
        self.assertFalse(updater.under_systemd({}))


# ---- storage: streamed restore, stepped backup --------------------------------------------

def fake_db(path, mb):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, mtime REAL NOT NULL);
        CREATE TABLE files (path TEXT PRIMARY KEY, data BLOB NOT NULL, size INTEGER NOT NULL,
                            mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL);
        PRAGMA user_version = 1;""")
    chunk = os.urandom(1 << 20)
    with db:
        for i in range(mb):
            db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?)", (f"videos/v{i}.bin", chunk, len(chunk), 0, "x"))
    db.close()


RESTORE_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import storage
def hwm():  # peak RSS of this process image in KB (VmHWM; ru_maxrss would include the parent's from before exec)
    return next(int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmHWM:"))
st = storage.Storage(sys.argv[2], sys.argv[3])
base = hwm()
size = int(sys.argv[5])
with open(sys.argv[4], "rb") as f:
    if sys.argv[6] == "stream":
        files = st.stage_restore_stream(f, size)
    else:
        files = st.stage_restore(f.read())
print(json.dumps({"files": files, "base_kb": base, "peak_kb": hwm()}))
"""


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def restore_rss(self, mode, mb=200):
        big = self.dir / "big.db"
        if not big.exists():
            fake_db(big, mb)
        (self.dir / f"s-{mode}.db").unlink(missing_ok=True)
        r = subprocess.run([sys.executable, "-c", RESTORE_CHILD, str(ROOT), str(self.dir / f"s-{mode}.db"),
                            str(self.dir / f"data-{mode}"), str(big), str(big.stat().st_size), mode],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["files"], mb)
        return out

    def test_streamed_restore_of_200mb_stays_small(self):
        stream = self.restore_rss("stream")
        grew_mb = (stream["peak_kb"] - stream["base_kb"]) / 1024
        print(f"\n  streamed restore of a 200 MB backup: peak RSS {stream['peak_kb'] / 1024:.0f} MB "
              f"(+{grew_mb:.0f} MB over the {stream['base_kb'] / 1024:.0f} MB baseline)", file=sys.stderr)
        self.assertLess(grew_mb, 40)
        staged = Path(f"{self.dir / 's-stream.db'}{storage.RESTORE_SUFFIX}")
        self.assertEqual(staged.stat().st_size, (self.dir / "big.db").stat().st_size)
        whole = self.restore_rss("bytes")  # the old way, for comparison: the whole body in memory
        print(f"  same backup read into memory: peak RSS {whole['peak_kb'] / 1024:.0f} MB", file=sys.stderr)
        self.assertGreater(whole["peak_kb"] - whole["base_kb"], 150 * 1024)

    def test_cut_off_upload_leaves_nothing(self):
        import io
        st = storage.Storage(self.dir / "s.db", self.dir / "data")
        with self.assertRaises(ValueError):
            st.stage_restore_stream(io.BytesIO(b"x" * 1000), 5000)
        self.assertEqual([p.name for p in self.dir.iterdir() if "restore" in p.name], [])
        with self.assertRaises(ValueError):  # complete, but not a database
            st.stage_restore_stream(io.BytesIO(b"x" * 1000), 1000)
        self.assertEqual([p.name for p in self.dir.iterdir() if "restore" in p.name], [])

    def test_backup_lets_writes_through(self):
        st = storage.Storage(self.dir / "s.db", self.dir / "data")
        chunk = os.urandom(1 << 20)
        for i in range(60):
            st.put_file(f"v/{i}.bin", chunk)
        waits, done = [], threading.Event()

        def writer():
            n = 0
            while not done.is_set():
                t = time.perf_counter()
                st.set(f"during-{n}", n)
                waits.append(time.perf_counter() - t)
                n += 1
                time.sleep(0.005)

        th = threading.Thread(target=writer)
        t0 = time.perf_counter()
        th.start()
        st.backup_to(self.dir / "copy.db", pages=64, pause=0.002)
        took = time.perf_counter() - t0
        done.set()
        th.join()
        self.assertGreater(len(waits), 5, "the writer never got the lock during the backup")
        self.assertLess(max(waits), 0.25, f"a write waited {max(waits):.3f} s during a {took:.2f} s backup")
        copy = sqlite3.connect(self.dir / "copy.db")
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM files").fetchone()[0], 60)
        self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        # writes made through the same connection mid-backup are in the copy
        n_copy = copy.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'during-%'").fetchone()[0]
        self.assertGreater(n_copy, 0)
        copy.close()


# ---- scheduled backups ---------------------------------------------------------------------

class BackupsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "app").mkdir()
        self.cfg = self.dir / "app" / "config.yaml"
        self.cfg.write_text(CONFIG)
        self.persist = storage.Storage(self.dir / "s.db", self.dir / "data")
        self.persist.startup()
        self.settings = {"folder": str(self.dir / "bk"), "keep": 3}
        self.b = backups.Backups(self.persist, lambda: self.settings, self.dir / "app", self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_folder_is_next_to_the_app(self):
        b = backups.Backups(self.persist, lambda: {}, self.dir / "app", self.cfg)
        self.assertEqual(b.folder(), (self.dir / "backups").resolve())

    def test_rotation_keeps_newest(self):
        (self.dir / "bk").mkdir()
        (self.dir / "bk" / "notes.txt").write_text("mine")
        names = [self.b.make("manual")["name"] for _ in range(5)]
        listed = [e["name"] for e in self.b.list()]
        self.assertEqual(listed, names[::-1][:3])  # newest first, 3 kept
        self.assertTrue((self.dir / "bk" / "notes.txt").exists())  # other files untouched
        self.assertEqual(len(set(names)), 5)  # same-second backups get distinct names
        st = self.b.status()
        self.assertTrue(st["last"]["ok"])
        self.assertEqual(len(st["last"]["removed"]), 1)

    def test_free_space_check(self):
        fake = mock.Mock(free=10 * 2**20)
        with mock.patch.object(backups.shutil, "disk_usage", return_value=fake):
            with self.assertRaises(ValueError) as cm:
                self.b.make("auto")
        self.assertIn("not enough free space", str(cm.exception))
        self.assertEqual(self.b.list(), [])
        last = self.b.status()["last"]
        self.assertFalse(last["ok"])
        self.assertIn("free", last["error"])

    def test_path_rejects_other_names(self):
        for bad in ("../s.db", "flipdot-backup-x.db", "notes.txt", "flipdot-backup-20260101-000000-auto.db/../x"):
            with self.assertRaises(ValueError):
                self.b.path(bad)

    def test_config_local_travels_with_the_backup(self):
        localconfig.update(self.cfg, {"password": "pw1"})
        entry = self.b.make("manual")
        localconfig.update(self.cfg, {"password": "changed later"})
        self.persist.stage_restore_file(self.b.path(entry["name"]))
        self.persist.db.close()
        again = storage.Storage(self.dir / "s.db", self.dir / "data")  # the next start applies the restore
        self.assertTrue(again.restored_backup)
        self.assertTrue(backups.restore_config_copy(again, self.cfg))
        self.assertEqual(localconfig.load(self.cfg)["password"], "pw1")
        before = localconfig.local_path(self.cfg).with_name("config.local.yaml.before-restore")
        self.assertIn("changed later", before.read_text())

    def test_schedule_slots(self):
        now = datetime(2026, 10, 10, 14, 0)  # a Saturday
        self.assertEqual(backups.last_slot({"schedule": "daily", "time": "03:00"}, now), datetime(2026, 10, 10, 3, 0))
        self.assertEqual(backups.last_slot({"schedule": "daily", "time": "15:00"}, now), datetime(2026, 10, 9, 15, 0))
        self.assertEqual(backups.last_slot({"schedule": "weekly", "time": "03:00", "weekday": 0}, now),
                         datetime(2026, 10, 5, 3, 0))
        self.assertEqual(backups.next_slot({"schedule": "weekly", "time": "03:00", "weekday": 0}, now),
                         datetime(2026, 10, 12, 3, 0))
        self.assertIsNone(backups.last_slot({"schedule": "off"}, now))
        with self.assertRaises(ValueError):
            backups.clean_settings({"schedule": "hourly"})
        with self.assertRaises(ValueError):
            backups.clean_settings({"time": "25:00"})
        self.assertEqual(backups.clean_settings({"time": "3:5", "keep": "4"}), {"time": "03:05", "keep": 4})

    def test_scheduler_runs_once_per_slot(self):
        self.settings.update(schedule="daily", time="03:00")
        self.b.started = 0

        def tick(at):
            with mock.patch.object(time, "time", return_value=at.timestamp()):
                return self.b.tick(at)
        day1 = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self.assertFalse(tick(day1))  # switched on now: not straight away
        self.assertFalse(tick(day1 + timedelta(hours=10)))  # 22:00, the slot is still ahead
        day2 = day1 + timedelta(days=1)
        self.assertTrue(tick(day2))  # 03:00 the next day has passed (caught up at noon)
        self.assertEqual([e["tag"] for e in self.b.list()], ["auto"])
        self.assertFalse(tick(day2 + timedelta(hours=1)))  # once per slot
        self.assertTrue(tick(day2 + timedelta(days=1)))
        self.assertEqual(len(self.b.list()), 2)
        # turning it off disarms it; on again, the first backup waits for the next slot
        self.settings["schedule"] = "off"
        self.assertFalse(tick(day2 + timedelta(days=5)))
        self.assertIsNone(self.persist.get("backup_armed"))
        self.settings["schedule"] = "daily"
        self.assertFalse(tick(day2 + timedelta(days=5)))


if __name__ == "__main__":
    unittest.main()
