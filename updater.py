"""Updating the app from its git remote, from the web page, with rollback; and restarting the server.

Check: `git fetch <remote> <branch>`, then list the incoming commits. Update: refuse if tracked files have local
changes; back up storage.db; `git pull --ff-only`; reinstall requirements*.txt that changed (only into the venv the
server runs in, and only files that venv was set up from); import server and apps in a fresh Python; restart. If a
step after the pull fails, the checkout goes back to the commit it was on (`git reset --keep`, which only moves back
over the commits the pull brought in and never touches local changes) and the reason is shown.

Nothing here forces anything: no `--force`, no `reset --hard`, no rebase. A branch with commits of its own that the
remote doesn't have can't fast-forward, and the update says so instead.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("updater")

DEFAULTS = {"remote": "", "branch": ""}  # "" = the current branch's upstream (usually origin/main)
REQ_RE = re.compile(r"^requirements[^/]*\.txt$")
SELF_CHECK = "import server, apps"
MAX_COMMITS = 50

def under_systemd(env=None, cgroup=None):
    """True when systemd runs this process as a service (and so starts it again after it exits). $INVOCATION_ID
    alone isn't enough: programs started from a terminal inside a systemd scope inherit it, so the process's own
    cgroup must be a .service (a terminal's is a .scope)."""
    env = os.environ if env is None else env
    if env.get("NOTIFY_SOCKET"):
        return True
    if not env.get("INVOCATION_ID"):
        return False
    if cgroup is None:
        try:
            cgroup = Path("/proc/self/cgroup").read_text()
        except OSError:
            return False
    for line in cgroup.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1] in ("", "name=systemd"):
            return parts[2].rstrip("/").rsplit("/", 1)[-1].endswith(".service")
    return False


def restart(delay=0.5, env=None, before_exec=None):
    """Restart this process, after `delay` s so the HTTP reply gets out first. Under systemd: exit cleanly through
    SIGTERM (atexit handlers run; the unit's Restart=always starts it again). Run by hand: run the exit handlers,
    then exec the same command line again in place (same PID, same terminal)."""
    systemd = under_systemd(env)

    def go():
        time.sleep(delay)
        if systemd:
            log.info("restarting: exiting so systemd starts the server again")
            try:
                import health
                health.sd_notify("STOPPING=1")
            except Exception:
                pass
            os.kill(os.getpid(), signal.SIGTERM)
            return
        log.info("restarting: running the server again in place")
        import atexit
        try:
            if before_exec:
                before_exec()
            atexit._run_exitfuncs()
        except Exception:
            log.exception("cleanup before restart failed")
        for s in (sys.stdout, sys.stderr):
            try:
                s.flush()
            except Exception:
                pass
        argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
        os.execv(sys.executable, [sys.executable, *argv[1:]])

    threading.Thread(target=go, daemon=True, name="restart").start()
    return {"ok": True, "systemd": systemd,
            "message": "Restarting. The page reconnects when the server is back (usually 5 to 15 seconds)."}


class UpdateError(Exception):
    pass


class Updater:
    def __init__(self, repo, settings=lambda: {}, backup=None, restart=None, state_path=None, python=None,
                 config_name="config.yaml", reset_config_ok=None):
        """repo: the app's git checkout. settings(): the {"update": …} section. backup(tag): make a backup (raises on
        failure). restart(): restart the server. state_path: JSON file that keeps the last update's result across the
        restart. python: the interpreter the server runs with (its venv gets the requirements)."""
        self.repo = Path(repo)
        self._settings = settings
        self.backup = backup
        self.do_restart = restart
        self.state_path = Path(state_path) if state_path else None
        self.python = python or sys.executable
        self.config_name = config_name
        self.reset_config_ok = reset_config_ok  # () -> True when config.yaml's edits are all in config.local.yaml
        self.lock = threading.Lock()
        self.job = self._load_state()
        self.last_check = None

    # ---- git ----

    def git(self, *args, timeout=60, check=True):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
        env.pop("GIT_DIR", None)
        try:
            r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, timeout=timeout,
                               env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise UpdateError(f"git {args[0]} took longer than {timeout} s") from None
        except OSError as e:
            raise UpdateError(f"can't run git: {e.strerror or e}") from None
        if check and r.returncode:
            msg = (r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}").splitlines()
            raise UpdateError(f"git {args[0]} failed: " + " ".join(l.strip() for l in msg[-4:]))
        return r

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branch(self):
        r = self.git("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return r.stdout.strip() or None

    def target(self):
        """(remote, branch) to update from: the settings, else the current branch's upstream, else origin/<branch>."""
        cfg = {**DEFAULTS, **(self._settings() or {})}
        cur = self.branch()
        remote, branch = (cfg.get("remote") or "").strip(), (cfg.get("branch") or "").strip()
        if cur and not remote:
            remote = self.git("config", f"branch.{cur}.remote", check=False).stdout.strip()
        if cur and not branch:
            merge = self.git("config", f"branch.{cur}.merge", check=False).stdout.strip()
            branch = merge.removeprefix("refs/heads/")
        return remote or "origin", branch or cur

    def dirty(self):
        """Tracked files with local changes (staged or not). Untracked files don't block an update; if the update
        would overwrite one, git refuses and nothing changes."""
        out = self.git("status", "--porcelain=v1", "--untracked-files=no").stdout
        return [l[3:].split(" -> ")[-1] for l in out.splitlines() if l.strip()]

    def _commits(self, rng):
        out = self.git("log", f"--max-count={MAX_COMMITS}", "--format=%H%x1f%h%x1f%s%x1f%cI%x1f%an", rng).stdout
        return [dict(zip(("sha", "short", "subject", "date", "author"), l.split("\x1f"))) for l in out.splitlines() if l]

    def _count(self, rng):
        return int(self.git("rev-list", "--count", rng).stdout.strip() or 0)

    # ---- check ----

    def check(self):
        """Fetch, and describe what an update would bring in (and whether it can run)."""
        if not (self.repo / ".git").exists() and self.git("rev-parse", "--git-dir", check=False).returncode:
            raise UpdateError(f"{self.repo} isn't a git checkout, so it can't update itself")
        branch = self.branch()
        if not branch:
            raise UpdateError("the checkout isn't on a branch (detached HEAD); check out a branch first")
        remote, rbranch = self.target()
        if not rbranch:
            raise UpdateError("no branch to update from; set one under Update source")
        self.git("fetch", "--quiet", "--no-tags", remote, rbranch, timeout=120)
        incoming = self._count("HEAD..FETCH_HEAD")
        ahead = self._count("FETCH_HEAD..HEAD")
        dirty = self.dirty()
        res = {"time": time.time(), "remote": remote, "branch": rbranch, "local_branch": branch,
               "current": self.head(), "upstream": self.git("rev-parse", "FETCH_HEAD").stdout.strip(),
               "incoming": incoming, "ahead": ahead, "commits": self._commits("HEAD..FETCH_HEAD"), "dirty": dirty,
               "config_resettable": dirty == [self.config_name] and bool(self.reset_config_ok and self.reset_config_ok())}
        res["blocked"] = self._blocked(res)
        res["can_update"] = incoming > 0 and not res["blocked"]
        self.last_check = res
        return res

    @staticmethod
    def _blocked(res):
        if res["dirty"]:
            return (f"{len(res['dirty'])} tracked file{'s' if len(res['dirty']) != 1 else ''} changed here: "
                    f"{', '.join(res['dirty'][:8])}{'…' if len(res['dirty']) > 8 else ''}. "
                    "Commit or undo those changes first; the update never overwrites them.")
        if res["ahead"] and res["incoming"]:
            return (f"this checkout has {res['ahead']} commit{'s' if res['ahead'] != 1 else ''} of its own that "
                    f"{res['remote']}/{res['branch']} doesn't have, so it can't simply move forward. Merge by hand.")
        return None

    def reset_config(self):
        """Put config.yaml back to the committed version, when every edit in it is kept in config.local.yaml."""
        if self.dirty() != [self.config_name]:
            raise UpdateError(f"only works when {self.config_name} is the one changed file")
        if not (self.reset_config_ok and self.reset_config_ok()):
            raise UpdateError(f"{self.config_name} has edits that config.local.yaml doesn't override; move them "
                              "there first")
        self.git("checkout", "--", self.config_name)
        return {"ok": True, "dirty": self.dirty()}

    # ---- update ----

    def status(self):
        return {**(self.job or {"state": "idle"}), "check": self.last_check}

    def start(self, wait=False):
        if not self.lock.acquire(blocking=False):
            raise UpdateError("an update is already running")
        self.job = {"state": "running", "step": "starting", "log": [], "started": time.time()}
        t = threading.Thread(target=self._run_locked, daemon=True, name="update")
        t.start()
        if wait:
            t.join()
        return self.status()

    def _run_locked(self):
        try:
            self._run()
        except Exception as e:  # anything unexpected: say so rather than leave "running" forever
            log.exception("update failed")
            if self.job.get("state") == "running":
                self._fail(f"{type(e).__name__}: {e}")
        finally:
            self.lock.release()

    def _step(self, name, msg=None):
        self.job["step"] = name
        if msg:
            self.job["log"].append(msg)
            log.info("update: %s", msg)

    def _fail(self, why, rolled_back_to=None):
        self.job.update(state="failed", error=why, finished=time.time(), rolled_back_to=rolled_back_to)
        if rolled_back_to:
            self.job["error"] = f"{why} Rolled back to {rolled_back_to[:8]}."
        log.error("update: %s", self.job["error"])
        self._save_state()

    def _run(self):
        job = self.job
        self._step("checking", "Checking the checkout")
        try:
            branch = self.branch()
            if not branch:
                raise UpdateError("the checkout isn't on a branch (detached HEAD)")
            dirty = self.dirty()
            if dirty:
                raise UpdateError(self._blocked({"dirty": dirty}))
            remote, rbranch = self.target()
            prev = self.head()
        except UpdateError as e:
            return self._fail(str(e))
        job.update(remote=remote, branch=rbranch, **{"from": prev})

        if self.backup:
            self._step("backup", "Backing up saved data")
            try:
                self.backup("before-update")
            except Exception as e:
                return self._fail(f"The backup before updating failed, so nothing was changed: {e}")

        self._step("pull", f"git pull --ff-only {remote} {rbranch}")
        try:
            self.git("pull", "--ff-only", "--no-rebase", "--no-tags", remote, rbranch, timeout=300)
        except UpdateError as e:
            if self.head() != prev:
                return self._rollback(prev, f"The pull failed: {e}.", [])
            return self._fail(f"The pull failed and nothing was changed: {e}")
        new = self.head()
        job["to"] = new
        if new == prev:
            job.update(state="done", finished=time.time(), message="Already up to date: nothing to install.")
            self._save_state()
            return
        job["commits"] = self._commits(f"{prev}..{new}")
        changed = self.git("diff", "--name-only", prev, new).stdout.split()
        self._step("pull", f"{prev[:8]} → {new[:8]}: {len(job['commits'])} commit(s), {len(changed)} file(s) changed")

        installed = []
        reqs = [f for f in changed if REQ_RE.match(f)]
        if reqs:
            self._step("requirements", f"Requirements changed: {', '.join(reqs)}")
            try:
                installed = self._install_requirements(reqs, prev)
            except UpdateError as e:
                return self._rollback(prev, f"Installing requirements failed: {e}.", installed)

        self._step("self-check", f"Self-check: {self.python} -c '{SELF_CHECK}'")
        try:
            r = subprocess.run([self.python, "-c", SELF_CHECK], cwd=self.repo, capture_output=True, text=True,
                               timeout=180, stdin=subprocess.DEVNULL)
            if r.returncode:
                lines = (r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}").splitlines()
                raise UpdateError(lines[-1])
        except (OSError, subprocess.TimeoutExpired) as e:
            return self._rollback(prev, f"The self-check couldn't run: {e}.", installed)
        except UpdateError as e:
            return self._rollback(prev, f"The new version doesn't start: {e}.", installed)

        job.update(state="restarting", finished=time.time(),
                   message=f"Updated {prev[:8]} → {new[:8]} ({len(job['commits'])} commit(s)). Restarting.")
        self._step("restart", "Restarting the server")
        self._save_state()
        if self.do_restart:
            self.do_restart()

    def _rollback(self, prev, why, installed):
        self._step("rollback", f"Rolling back to {prev[:8]}")
        try:
            self.git("reset", "--keep", prev)
        except UpdateError as e:
            return self._fail(f"{why} Rolling back also failed ({e}); the checkout is at {self.head()[:8]}, "
                              f"run `git reset --keep {prev[:8]}` by hand.")
        for req in installed:  # put the old pins back, best effort
            try:
                old = self.git("show", f"{prev}:{req}").stdout
                self._pip_install(old)
            except UpdateError as e:
                self.job["log"].append(f"Couldn't reinstall the old {req}: {e}")
        return self._fail(why, rolled_back_to=prev)

    # ---- requirements ----

    def in_venv(self):
        r = subprocess.run([self.python, "-c", "import sys; print(int(sys.prefix != sys.base_prefix))"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() == "1"

    @staticmethod
    def _pins(text):
        return {m.group(1).lower(): m.group(2) for m in re.finditer(r"^\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s;#]+)", text, re.M)}

    def _installed_matches(self, pins):
        code = ("import json, sys\nfrom importlib import metadata\nout = {}\nfor n in sys.argv[1:]:\n"
                "    try: out[n] = metadata.version(n)\n    except metadata.PackageNotFoundError: out[n] = None\n"
                "print(json.dumps(out))")
        r = subprocess.run([self.python, "-c", code, *pins], capture_output=True, text=True, timeout=60)
        try:
            have = json.loads(r.stdout)
        except ValueError:
            return False
        return all(have.get(n) == v for n, v in pins.items())

    def _install_requirements(self, reqs, prev):
        """Install the changed files this venv was set up from. Returns the ones installed."""
        if not self.in_venv():
            self.job["log"].append("The server isn't running in a venv, so requirements weren't installed. "
                                   "Install them by hand if you use those features.")
            return []
        done = []
        for req in reqs:
            old = self.git("show", f"{prev}:{req}", check=False).stdout
            pins = self._pins(old)
            # in use = the venv has exactly the old pins (requirements-sendspin.txt and its -ma27 sibling pin
            # the same package at different versions; only the one the venv was set up from gets installed)
            if not old or not pins or not self._installed_matches(pins):
                self.job["log"].append(f"{req}: not what this venv was set up from, skipped")
                continue
            path = self.repo / req
            if not path.exists():
                continue
            self._pip_install(path.read_text())
            done.append(req)
            self.job["log"].append(f"{req}: installed")
        return done

    def _pip_install(self, text):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=self.repo, prefix=".update-req-", delete=False) as f:
            f.write(text)
        try:
            r = subprocess.run([self.python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", f.name],
                               cwd=self.repo, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise UpdateError(f"pip didn't finish: {e}") from None
        finally:
            Path(f.name).unlink(missing_ok=True)
        if r.returncode:
            lines = (r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}").splitlines()
            raise UpdateError("pip: " + lines[-1])

    # ---- the result, across the restart ----

    def _save_state(self):
        if not self.state_path:
            return
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self.job, indent=1))
            tmp.replace(self.state_path)
        except OSError as e:
            log.warning("update: couldn't save the result: %s", e)

    def _load_state(self):
        if not self.state_path:
            return None
        try:
            job = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return None
        if job.get("state") == "restarting":  # we're the restarted server: did it come up on the new commit?
            try:
                now = self.head()
            except UpdateError:
                now = None
            if now and now == job.get("to"):
                job.update(state="done", message=f"Updated {job['from'][:8]} → {job['to'][:8]} and restarted.",
                           restarted=time.time())
            else:
                job.update(state="failed", error=f"After the restart the checkout is at {(now or '?')[:8]}, "
                                                 f"not {str(job.get('to'))[:8]}.")
            self.job = job
            self._save_state()
        if job.get("state") == "running":  # the server stopped mid-update
            job.update(state="failed", error="The server stopped while the update was running. Check the version "
                                             "below; run Check for updates again.")
        return job
