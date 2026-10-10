#!/usr/bin/env bash
# Set up the live music visualizer link to Music Assistant (Sendspin) on the Pi.
# Run from anywhere:   ./scripts/setup-sendspin.sh        (PORT=8081 ./scripts/setup-sendspin.sh if not 8080)
# Music Assistant 2.7.x: ./scripts/setup-sendspin.sh --ma27  (or MA_VERSION=2.7 ./scripts/setup-sendspin.sh)
#
# It: creates ./venv (reusing the apt-installed Pillow), installs the aiosendspin version Music
# Assistant uses, turns Sendspin on in data/settings.json (auto-connect to Music Assistant, no
# pairing needed), points the systemd user service at the venv, restarts it and shows the status.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8080}"
UNIT="$HOME/.config/systemd/user/flipdot.service"
REQ=requirements-sendspin.txt
MA27=false
case "${MA_VERSION:-}" in 2.7*) MA27=true ;; esac
for arg in "$@"; do
  case "$arg" in
    --ma27) MA27=true ;;
    *) echo "unknown option: $arg (use --ma27 for Music Assistant 2.7.x)"; exit 2 ;;
  esac
done
$MA27 && REQ=requirements-sendspin-ma27.txt

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/5  Checking Python"
python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"Python {sys.version.split()[0]} is too old for aiosendspin (needs 3.12+). "
             "Raspberry Pi OS Trixie ships 3.13.")
print("Python", sys.version.split()[0], "OK")
PY

say "2/5  Installing aiosendspin into ./venv"
[ -d venv ] || python3 -m venv --system-site-packages venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r "$REQ"
MA27=$MA27 venv/bin/python - <<'PY'
import os
import sendspin_link as s
if not s.AVAILABLE:
    raise SystemExit("aiosendspin didn't import — see the pip output above")
want, label = ((s.MA27_AIOSENDSPIN, "Music Assistant 2.7.x") if os.environ["MA27"] == "true"
               else (s.MA_AIOSENDSPIN, "Music Assistant 2.8+"))
ok = s.VERSION == want
print(f"aiosendspin {s.VERSION} — {'matches ' + label if ok else 'DOES NOT MATCH ' + label + ' (' + want + ')'}")
PY

running_manually=false
if [ -f "$UNIT" ]; then
  say "3/5  Stopping the flipdot service while settings change"
  systemctl --user stop flipdot || true
elif curl -s -m 2 "http://localhost:$PORT/api/board" >/dev/null 2>&1; then
  running_manually=true
fi

if $running_manually; then
  say "3/5  The server is running (not as a service) — enabling Sendspin through it"
  curl -s -m 5 -X POST "http://localhost:$PORT/api/settings" \
    -d '{"sendspin": {"enabled": true, "unpaired": true, "server_url": "auto"}}' >/dev/null
else
  say "3/5  Enabling Sendspin in data/settings.json"
  venv/bin/python - <<'PY'
import json, pathlib
p = pathlib.Path("data/settings.json")
d = json.loads(p.read_text()) if p.exists() else {}
ss = d.setdefault("sendspin", {})
ss["enabled"] = True
ss.setdefault("name", "Flipdot board")
ss.setdefault("unpaired", True)          # Music Assistant approves it automatically
ss.setdefault("server_url", "auto")      # find Music Assistant and connect to it
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=1))
print("sendspin settings:", ss)
PY
fi

say "4/5  Starting the server with the venv's Python"
if [ -f "$UNIT" ]; then
  # keep any arguments (like --port), just swap the interpreter
  sed -i -E "s#^ExecStart=[^ ]+ (.*server\.py)#ExecStart=$PWD/venv/bin/python \1#" "$UNIT"
  grep '^ExecStart' "$UNIT"
  systemctl --user daemon-reload
  systemctl --user start flipdot
else
  echo "No systemd service found."
  pid=""
  if $running_manually && command -v ss >/dev/null; then
    pid=$(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)
  fi
  if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ]; then
    # same arguments it was started with, with the venv's Python
    args=$(tr '\0' '\n' < "/proc/$pid/cmdline" | tail -n +2 | sed "s/'/'\\\\''/g; s/.*/'&'/" | tr '\n' ' ')
    dir=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "$PWD")
    echo "The server is running by hand (PID $pid). Restart it with the venv's Python so Sendspin starts:"
    echo
    echo "    kill $pid"
    echo "    cd '$dir' && $PWD/venv/bin/python $args"
  else
    $running_manually && echo "The server is running by hand; stop it (Ctrl+C in its terminal), then start it with:" \
                      || echo "Start the server with:"
    echo
    echo "    $PWD/venv/bin/python $PWD/server.py --port $PORT"
  fi
  echo
  echo "Sendspin starts only after that restart."
  exit 0
fi

say "5/5  Waiting for Music Assistant"
for i in $(seq 1 30); do
  status=$(curl -s -m 2 "http://localhost:$PORT/api/sendspin" || true)
  if echo "$status" | grep -q '"connected": true'; then
    echo "$status" | venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print("✓", d["status"], "· aiosendspin", d.get("version"))'
    echo
    if $MA27; then
      echo "Done. In Music Assistant it appears as a Sendspin player named \"Flipdot board\"."
      echo "Play to it, or group it with the speaker that's playing, to send it song info and audio to analyse."
    else
      echo "Done. In Music Assistant it appears as a display player named \"Flipdot board\"."
    fi
    echo "Run the Music visualizer app on the board; it should say \"live from Music Assistant\" while music plays."
    exit 0
  fi
  sleep 2
done
echo "Not connected yet. Current status:"
echo "$status"
echo
echo "Check: journalctl --user -u flipdot -n 50   (look for 'Sendspin' lines)"
exit 1
