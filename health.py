"""How the server is doing, for GET /api/health, and the systemd watchdog.

With `Type=notify` and `WatchdogSec=` in the unit, systemd hands the service a socket in $NOTIFY_SOCKET. The server
says READY=1 once it's up, then WATCHDOG=1 regularly, but only while the runner loop is ticking: if the loop hangs,
the pings stop and systemd restarts the service. Without $NOTIFY_SOCKET (run by hand) nothing is sent.
"""

import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("health")

ROOT = Path(__file__).parent
STARTED = time.time()
FRESH = 10.0  # the runner heartbeat counts as fresh this many seconds after its last tick
SLOW = 3.0  # a heartbeat older than this is worth mentioning (degraded)
HOT = 80.0  # °C: the Pi 5 starts throttling at 85
LOW_DISK = 200 * 1024 * 1024
THROTTLE_TTL = 60.0  # vcgencmd is a process start: ask at most once a minute

_cache = {}


def sd_notify(message, env=None):
    """Send one sd_notify message (e.g. "READY=1") to systemd. Returns False when there's no socket to send to."""
    addr = (os.environ if env is None else env).get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except OSError as e:
        log.debug("sd_notify %s failed: %s", message, e)
        return False


def watchdog_interval(env=None):
    """Seconds between WATCHDOG=1 pings: half of what systemd allows (WATCHDOG_USEC), or None without a watchdog."""
    env = os.environ if env is None else env
    if not env.get("NOTIFY_SOCKET"):
        return None
    try:
        usec = int(env.get("WATCHDOG_USEC") or 0)
        pid = int(env.get("WATCHDOG_PID") or 0)
    except ValueError:
        return None
    if usec <= 0 or (pid and pid != os.getpid()):
        return None
    return max(0.5, usec / 2e6)


def start_watchdog(heartbeat_age, env=None, interval=None):
    """Ping systemd's watchdog while heartbeat_age() (seconds since the runner last ticked) stays under FRESH.
    Returns the thread, or None when the service has no watchdog."""
    interval = interval or watchdog_interval(env)
    if not interval:
        return None

    def run():
        stale = False
        while True:
            age = heartbeat_age()
            if age < FRESH:
                sd_notify("WATCHDOG=1", env)
                if stale:
                    log.info("runner is ticking again")
                stale = False
            elif not stale:
                stale = True
                log.error("runner hasn't ticked for %.0f s: no watchdog ping, systemd will restart the service", age)
            time.sleep(interval)

    t = threading.Thread(target=run, daemon=True, name="watchdog")
    t.start()
    return t


# ---- the machine ---------------------------------------------------------------------

def cpu_temp():
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


THROTTLE_BITS = {0: "under-voltage", 1: "frequency capped", 2: "throttled", 3: "soft temperature limit"}


def throttled():
    """The Pi's `vcgencmd get_throttled`, decoded; None where there's no vcgencmd. Cached for a minute."""
    hit = _cache.get("throttled")
    if hit and time.monotonic() - hit[0] < THROTTLE_TTL:
        return hit[1]
    value = None
    exe = shutil.which("vcgencmd")
    if exe:
        try:
            out = subprocess.run([exe, "get_throttled"], capture_output=True, text=True, timeout=2).stdout
            bits = int(out.strip().partition("=")[2], 16)
            value = {"raw": hex(bits), "now": [n for b, n in THROTTLE_BITS.items() if bits >> b & 1],
                     "since_boot": [n for b, n in THROTTLE_BITS.items() if bits >> (b + 16) & 1]}
        except (OSError, ValueError, subprocess.SubprocessError):
            value = None
    # without vcgencmd, don't look again
    _cache["throttled"] = (time.monotonic() if exe else float("inf"), value)
    return value


def disk(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
    except OSError:
        return None
    return {"path": str(path), "free_mb": u.free // 2**20, "total_mb": u.total // 2**20, "free_pct": round(100 * u.free / u.total, 1)}


def memory():
    out = {}
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        out["total_mb"] = int(info["MemTotal"].split()[0]) // 1024
        out["available_mb"] = int(info["MemAvailable"].split()[0]) // 1024
    except (OSError, KeyError, ValueError):
        pass
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["process_mb"] = int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return out or None


def system_uptime():
    try:
        return round(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def version():
    """`git describe` of the app folder (or the commit), looked up once."""
    if "version" not in _cache:
        v = None
        try:
            r = subprocess.run(["git", "describe", "--always", "--dirty", "--tags"], cwd=ROOT, capture_output=True,
                               text=True, timeout=3)
            v = r.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
        if not v:
            try:
                head = (ROOT / ".git" / "HEAD").read_text().strip()
                if head.startswith("ref: "):
                    head = (ROOT / ".git" / head[5:]).read_text().strip()
                v = head[:12] or None
            except OSError:
                pass
        _cache["version"] = v
    return _cache["version"]


def version_info():
    """version() plus the commit, its date and the branch, looked up once (an update restarts the server)."""
    if "version_info" not in _cache:
        info = {"version": version(), "commit": None, "date": None, "subject": None, "branch": None}
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%H%x1f%cI%x1f%s"], cwd=ROOT, capture_output=True,
                               text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                info["commit"], info["date"], info["subject"] = r.stdout.strip().split("\x1f", 2)
            r = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=ROOT, capture_output=True,
                               text=True, timeout=3)
            info["branch"] = r.stdout.strip() or None
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        _cache["version_info"] = info
    return _cache["version_info"]


# ---- the verdict -----------------------------------------------------------------------

def assess(runner_hb, buses, temp=None, throttle=None, disks=()):
    """ok / degraded / failing, with the reasons. failing: the board can't show anything (the runner is stuck or
    no bus can be reached); degraded: it can, but something needs looking at."""
    failing, degraded = [], []
    age = runner_hb.get("age_s", 0)
    if age >= FRESH:
        failing.append(f"the runner hasn't ticked for {age:.0f} s")
    elif age >= SLOW:
        degraded.append(f"the runner last ticked {age:.0f} s ago")
    if runner_hb.get("errors_5min"):
        degraded.append(f"{runner_hb['errors_5min']} runner errors in the last 5 minutes")
    down = [b for b in buses if b.get("connected") is False or not b.get("sender_alive", True)]
    if buses and len(down) == len(buses):
        failing.append("no bus can be reached")
    elif down:
        degraded.append(f"{', '.join(b['bus'] for b in down)} can't be reached")
    if temp is not None and temp >= HOT:
        degraded.append(f"CPU at {temp:.0f} °C")
    if throttle and throttle.get("now"):
        degraded.append("the Pi reports " + ", ".join(throttle["now"]))
    for d in disks:
        if d and d["free_mb"] * 2**20 < LOW_DISK:
            degraded.append(f"only {d['free_mb']} MB free for {d['path']}")
    status = "failing" if failing else "degraded" if degraded else "ok"
    return status, failing + degraded


def report(board, runner, data_dir, storage_path):
    hb = runner.heartbeat()
    buses = board.bus_health()
    temp, throttle = cpu_temp(), throttled()
    disks = [disk(data_dir), disk(Path(storage_path).parent)]
    disks[1] = disks[1] and {**disks[1], "path": str(storage_path)}
    status, reasons = assess(hb, buses, temp, throttle, disks)
    return {
        "status": status, "reasons": reasons, "time": time.time(),
        "runner": hb, "buses": buses, "dry_run": board.dry_run,
        "full_refresh_s": board.full_refresh,
        "cpu_temp_c": temp, "throttled": throttle,
        "disk": {"data": disks[0], "storage": disks[1]},
        "memory": memory(),
        "uptime_s": round(time.time() - STARTED), "system_uptime_s": system_uptime(),
        "version": version(),
    }
