# Flipdot board

Web UI + HTTP API for an 84×42 AlfaZeta flipdot board (18 panels of 28×7, driven over the
RS485-to-Ethernet box at 192.168.11.100, ports 5000–5002, panel addresses 0–5 — see `config.yaml`).

Needs Python 3.10+ and Pillow (`sudo dnf install python3-pillow` or `pip install pillow`). Nothing else.

```sh
python3 server.py              # drive the board, UI at http://localhost:8080
python3 server.py --dry-run    # no hardware
```

Close Maestro first — the Ethernet box only accepts one connection per port.

## What's in the UI

| Tab | |
|---|---|
| **Draw** | pencil, line, rectangle, circle (outline/filled), flood fill, text stamp, select + copy/cut/paste/flip, undo/redo (Ctrl+Z / Ctrl+Y), save as scene, export PNG |
| **Image & video** | images and animated GIFs (stretch / cut / fit, threshold or dither), video files (stream live or convert a clip), webcam (works on `http://localhost` or https only); **slideshows** of images/GIFs (upload files or a .zip, which is unpacked automatically; images show for 10 s by default, GIFs play through, then it moves on and loops) |
| **Apps** | clocks (digital, analog, countdown), text (static / ticker / typewriter), weather, crypto, stocks, sports scores, any JSON URL, now playing from Music Assistant, music visualizer, Game of Life, matrix rain, plasma, bouncing balls, starfield, fireworks, ripples, metaballs, tunnel, spiral, maze, Lissajous, fire, **random effects** (cycles through them all), Snake / Tetris / Pong / Blackjack (dealt from deckofcardsapi.com), disc exerciser |
| **Layers** | stack apps with blend modes (normal, add, halo, invert/XOR, mask, cut), drawing layers you can keep editing live, position/size, scrolling, density, speed; live editing; saved compositions (usable in playlists); transition gallery |
| **Playlists & schedule** | playlists of apps with durations and transitions (dissolve, wipe, slide); time-of-day/day-of-week rules choosing a playlist or "off" |
| **Scenes** | saved drawings, images and animations |
| **Settings** | weather location, fonts (upload your own), Music Assistant, MQTT, webhook examples |

`/play` is a phone game controller (D-pad + A/B, player 1 or 2).

Drawing or running an app pauses the schedule; **Resume schedule** goes back to it.

## API (selected)

```
GET  /api/board                      current dots + what's running
POST /api/dot     {"x":5,"y":3,"color":"white|black|toggle"}
POST /api/dots    {"dots":[...]}
POST /api/frame   {"rows":[42 strings of 84 "0"/"1"]}
POST /api/fill    {"color":"black"}        POST /api/invert
POST /api/animation {"frames":[{"rows":[...],"delay":100}], "loop":true}
POST /api/run     {"app":"clock","params":{...},"transition":"dissolve"}
POST /api/notify  {"text":"Hi","duration":10} | {"url":"https://...png"} | {"app":...} | {"scene":id}
GET  /api/notify?text=Hi&duration=10
POST /api/image?mode=crop&dither=true&save=Name   (raw image bytes)
POST /api/stop | /api/next | /api/schedule/resume
POST /api/playlist/play {"name":"Default"}
POST /api/input   {"key":"up","action":"down","player":1}     (games, also inside layers)
POST /api/layers  {"layers":[{"app":"weather"},{"app":"life","blend":"xor"}], "transition":"iris-open"}
                  (or {"composition":"Life over weather"}; updates a running composition in place)
GET/POST /api/galleries, POST /api/galleries/<id>/upload?name=FILE (image, GIF or .zip), /api/galleries/<id> {settings, items}
POST /api/layers/add {"layer": {...}, "position": "top"|"bottom"}   (add to whatever is on the board)
GET/POST /api/compositions        GET /api/layers/current
GET/POST /api/drawings, POST /api/drawings/<id> {rows}, /api/drawings/<id>/dots, /api/drawings/<id>/show
GET  /api/apps    (every app and its parameters)
```

Live updates: connect a WebSocket to `/ws`. The server pushes a binary message whenever the dots
change (`u32 version, u16 width, u16 height`, then the dots packed 8 per byte, MSB first, row-major)
and a text message `{"type":"status","runner":{...}}` when the status changes. You can send
`{"type":"input","key":"up","action":"down","player":1}` or `{"type":"dots","dots":[...]}`.
The web pages use this and fall back to polling `/api/board` if the socket drops. Sending
`{"type":"drawing_rows","drawing":ID,"rows":[...]}` replaces a drawing layer's picture (that's how live
video streams into a layer without stopping the rest of the composition).

MQTT (Settings tab): `flipdot/notify`, `flipdot/run`, `flipdot/command` (stop, schedule, next,
black, white, invert); status is published retained on `flipdot/status`.

Set `password:` in `config.yaml` to require a password (HTTP basic auth, or `?token=` for webhooks).

Saved data (scenes, playlists, schedule, settings) lives in `data/`.

## Music visualizer over Sendspin (optional)

The Music visualizer app follows a generated beat out of the box. To make it follow the actual music,
let Music Assistant stream visualizer data to the board over Sendspin:

```sh
python3 -m venv --system-site-packages venv      # keeps the apt-installed Pillow
venv/bin/pip install -r requirements-sendspin.txt  # needs Python 3.12+; pinned to Music Assistant's version
venv/bin/python server.py                        # run the server with the venv's Python
```

Then Settings → *Music Assistant visualizer (Sendspin)* → Enabled. The board advertises itself on
port 8928; approve or pair it in Music Assistant. When it works the status shows *connected* and
*receiving visualizer data ✓*, and the visualizer says *live from Music Assistant*.

### Testing the Sendspin link

`tests/sendspin_e2e.py` runs the board's link against a real aiosendspin server at the version Music
Assistant pins (handshake, roles, song info, visualizer data, beats, refusing unapproved servers).
mDNS is off, so it doesn't touch anything on your network:

```sh
python3 -m venv .venv-test && .venv-test/bin/pip install "aiosendspin[server]==9.1.1" pillow
.venv-test/bin/python tests/sendspin_e2e.py      # exit code 0 = all checks passed
```

When Music Assistant moves to a new aiosendspin, update `requirements-sendspin.txt` and
`sendspin_link.MA_AIOSENDSPIN`, then run this test with the new version.

## Run at boot (systemd user service)

```sh
mkdir -p ~/.config/systemd/user
cp flipdot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flipdot
sudo loginctl enable-linger $USER     # keep it running when you're logged out
journalctl --user -u flipdot -f       # logs
```

## Files

- `flipdot.py` – framebuffer and panel protocol (`0x80 0x83 addr <28 column bytes> 0x8F`)
- `canvas.py`, `fonts.py` – 1-bit drawing, pixel fonts, seven-segment digits
- `apps/` – everything that draws over time
- `runner.py` – manual / app / playlist / schedule modes, notifications
- `transitions.py` – ~40 transitions (reveal maps like wipes/iris/clock/spiral, plus slides, pushes, zoom, melt, glitch…)
- `apps/layers.py` – the layer compositor
- `store.py` – JSON persistence; `mqtt.py` – tiny MQTT client; `ws.py` – WebSocket push; `server.py` – HTTP
- `static/` – web UI
