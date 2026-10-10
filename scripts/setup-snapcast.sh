#!/usr/bin/env bash
# Set up live music for the visualizer over Snapcast (Music Assistant's Snapcast provider) on the Pi.
# Run from anywhere:   ./scripts/setup-snapcast.sh                (PORT=8081 ... if the board isn't on 8080)
# Snapcast server elsewhere than the Music Assistant host:  ./scripts/setup-snapcast.sh --host 192.168.1.20
#
# It: installs snapclient with apt, turns off the snapclient system service the package starts (that one
# would play the music through the Pi's audio output), selects Snapcast as the visualizer's audio source,
# restarts the board's server if it runs as a service and shows the link status.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8080}"
UNIT="$HOME/.config/systemd/user/flipdot.service"
HOST=auto
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?--host needs an address}"; shift 2 ;;
    *) echo "unknown option: $1 (use --host ADDRESS)"; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/4  Installing snapclient"
if ! command -v snapclient >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq snapclient
fi
snapclient -v | head -1

say "2/4  Turning off the snapclient system service (the board runs its own, silent snapclient)"
if systemctl list-unit-files snapclient.service >/dev/null 2>&1; then
  sudo systemctl disable --now snapclient.service || true
fi

say "3/4  Selecting Snapcast as the visualizer's audio source"
body="{\"visualizer\": {\"source\": \"snapcast\"}, \"snapcast\": {\"host\": \"$HOST\"}}"
if curl -s -m 2 "http://localhost:$PORT/api/board" >/dev/null 2>&1; then
  curl -s -m 5 -X POST "http://localhost:$PORT/api/settings" -d "$body" >/dev/null
  echo "Saved through the running server (Snapcast starts right away)."
else
  HOST="$HOST" python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path("data/settings.json")
d = json.loads(p.read_text()) if p.exists() else {}
d["visualizer"] = {**d.get("visualizer", {}), "source": "snapcast"}
sc = d.setdefault("snapcast", {})
sc["host"] = os.environ["HOST"]
sc.setdefault("port", 1704)
sc.setdefault("control_port", 1705)
sc.setdefault("name", "Flipdot board")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(d, indent=1))
print("snapcast settings:", sc)
PY
  if [ -f "$UNIT" ]; then
    systemctl --user restart flipdot
  else
    echo "Start the board's server (python3 server.py) and Snapcast starts with it."
    exit 0
  fi
fi

say "4/4  Waiting for the Snapcast server"
status=""
for i in $(seq 1 20); do
  status=$(curl -s -m 2 "http://localhost:$PORT/api/snapcast" || true)
  if echo "$status" | grep -q '"connected": true'; then
    echo "$status" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("OK:", d["status"], "·", d.get("codec"), "· snapclient", d.get("version"))'
    echo
    echo "Done. In Music Assistant (Settings → Providers → Snapcast) the board is the player \"Flipdot board\"."
    echo "Play to it, or group it with the speaker that's playing; keep it unmuted."
    echo "The Music visualizer app then says \"live from Music Assistant (Snapcast)\" while music plays."
    echo "Settings live in the web page under Settings → Music → Live audio (reload the page if it was open)."
    exit 0
  fi
  sleep 2
done
echo "Not connected yet. Current status:"
echo "$status"
echo
echo "Check that the Snapcast provider is enabled in Music Assistant and that port 1704 on its host is reachable."
exit 1
