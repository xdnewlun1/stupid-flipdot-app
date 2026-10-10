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
| **Draw** | pencil, line, rectangle, circle (outline/filled), flood fill, text stamp, select + copy/cut/paste/flip, undo/redo (Ctrl+Z / Ctrl+Y), save as scene, **animation mode** (frame strip: add / duplicate / delete / reorder frames, per-frame duration, onion skin, play on the board, save as a scene or open a saved one; tools, undo and copy/paste work on the current frame), **Draw me…** (Claude draws a picture or a short animation from a description; needs an Anthropic API key in Settings → AI drawing), export PNG, **export GIF** (timelapse of the last 1–30 minutes, sped up; recorded in memory) |
| **Image & video** | images and animated GIFs (stretch / cut / fit, threshold or dither), video files (stream live or convert a clip), webcam (works on `http://localhost` or https only); **slideshows** of images/GIFs (upload files or a .zip, which is unpacked automatically; images show for 10 s by default, GIFs play through, then it moves on and loops; optional **random effects** give some items an effect such as glitch, wave or Game of Life, for a short burst or the whole item, with a chance, effect list and strength per slideshow) |
| **Apps** | clocks (digital, analog, countdown), text (static / ticker / typewriter), weather, **calendar** (iCal links: next events with today/tomorrow labels and a countdown; all-day events, time zones, simple repeats), crypto, stocks, sports scores, any JSON URL, now playing from Music Assistant, music visualizer, Game of Life, matrix rain, plasma, bouncing balls, starfield, fireworks, ripples, metaballs, tunnel, spiral, maze, Lissajous, fire, **random effects** (cycles through them all), Snake / Tetris / Pong / Breakout / Space Invaders / Tron light cycles (vs a CPU or a second player) / Flappy / **Platformer** (run, jump, stomp walkers, reach the flag) / Blackjack (dealt from deckofcardsapi.com), **Doom** (the real game; see below), **arcade**: Asteroids, Galaxian, Missile Command, Centipede, Lunar Lander; **maze & action**: Pac-Man, Frogger, Qix, Bomberman (up to 4 players), Tank Combat; **runners & racing**: Dino Run, Road Racer (pseudo-3D road), Cave Copter, Stacker, Sky Jump, Pitfall Run; **two players** (or vs the computer): Connect Four, Tic-tac-toe, Artillery, Checkers, Battleship, Quick Draw, Air Hockey; **puzzles**: 2048, Minesweeper, Sokoban, Lights Out, Simon, Columns, Mastermind (every game plays a demo of itself when idle and keeps a best score), disc exerciser, **Guest wall** (a QR code to `/guest`, where guests leave a name and message from their phone, alternating with the approved messages as scrolling text; moderate in Settings → Guest wall, or approve automatically), **Wi-Fi QR code** (guests scan it to join; very long names/passwords switch to a full-height inverted code) |
| **Layers** | stack apps with blend modes (normal, add, halo, invert/XOR, mask, cut), drawing layers you can keep editing live, position/size, scrolling, density, speed; live editing; saved compositions (usable in playlists); transition gallery |
| **Playlists & schedule** | playlists of apps with durations and transitions (dissolve, wipe, slide); time-of-day/day-of-week rules choosing a playlist or "off"; **automations**: when music starts/stops (Music Assistant), the weather turns rainy/snowy/clear, an event arrives, or the board has been idle N minutes → run an app or composition, play a playlist, show a notification, resume the schedule or go all black |
| **Scenes** | saved drawings, images and animations |
| **Settings** | weather location, **quiet hours** (cap on dots flipped per second + slower transitions during a time window), **board health** (flips per disc as a heatmap, exercise rarely flipped discs now or daily), **board setup** (step through panels to check the mapping, edit column/row/ip/port/address/rotate, save to `config.yaml` without a restart), fonts (upload your own), Music Assistant, MQTT (with optional Home Assistant discovery), **storage** (backup / restore of `storage.db`), webhook examples |

`/play` is a phone game controller (pick any game from the list; D-pad + A/B with per-game labels, Start/X/Y/Z, player 1 or 2; Doom's extra buttons while Doom runs). USB gamepads work too; see below.

Drawing or running an app pauses the schedule; **Resume schedule** goes back to it.

## API (selected)

```
GET  /api/board                      current dots + what's running
POST /api/dot     {"x":5,"y":3,"color":"white|black|toggle"}
POST /api/dots    {"dots":[...]}
POST /api/frame   {"rows":[42 strings of 84 "0"/"1"]}
POST /api/fill    {"color":"black"}        POST /api/invert
POST /api/animation {"frames":[{"rows":[...],"delay":100}], "loop":true}
POST /api/ai/draw {"prompt":"a cat","frames":1,"style":"outline only"}   → {"frames":[{"rows":[...]}]} (1–8 frames; doesn't touch the board)
GET  /api/guest                      guest wall queue; POST /api/guest/messages/<id>/approve|reject|delete, POST /api/guest/settings {"auto_approve":true}
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
GET  /api/wear                        flips per disc (row-major), total, most/least flipped
POST /api/wear/exercise               flip the least-used discs a few times, then restore the picture
GET/POST /api/panels {"panels":[{"column":1,"row":1,"ip":"…","port":5000,"address":0,"rotate":0}]}
POST /api/panels/identify {"index":0}   (label one panel on the board; no index = all)
GET  /api/timelapse.gif?minutes=5&speed=10&scale=6
GET/POST /api/rules {"rules":[{"trigger":{"type":"event","event":"doorbell"},"action":{"type":"notify","text":"Door"}}]}
POST /api/rules/<id>/run              run an automation's action now
POST /api/event/<name>                trigger the automations waiting for this event
```

Automations live in `data/rules.json`. Triggers: `music_start`, `music_stop`, `weather` (`rain`/`snow`/`clear`,
checked every 10 minutes), `event`, `idle` (`minutes`). Actions: `app` (`app`, `params`), `composition`, `playlist`,
`notify` (`text`, `scene` or `url`, `duration`), `schedule`, `black`. Each rule fires when its trigger starts and then
waits `cooldown` seconds (default 60) before it can fire again.

A panel mounted upside down can be fixed with `rotate: "180"` in its `config.yaml` entry.
Flip counts are saved to `data/wear.json` every two minutes and on shutdown.

Live updates: connect a WebSocket to `/ws`. The server pushes a binary message whenever the dots
change (`u32 version, u16 width, u16 height`, then the dots packed 8 per byte, MSB first, row-major)
and a text message `{"type":"status","runner":{...}}` when the status changes. You can send
`{"type":"input","key":"up","action":"down","player":1}` or `{"type":"dots","dots":[...]}`.
The web pages use this and fall back to polling `/api/board` if the socket drops. Sending
`{"type":"drawing_rows","drawing":ID,"rows":[...]}` replaces a drawing layer's picture (that's how live
video streams into a layer without stopping the rest of the composition).

MQTT (Settings tab): `flipdot/notify`, `flipdot/run`, `flipdot/command` (stop, schedule, next,
black, white, invert), `flipdot/event` (an automation event name); status is published retained on `flipdot/status`,
and `flipdot/availability` is `online`/`offline` (offline is also the MQTT Last Will). With **Home Assistant
discovery** on, the board appears in Home Assistant as a "Flipdot board" device: app and playlist selects, schedule
and quiet-hours switches, a Message text box (shows a notification), Next/Stop buttons and a "Showing" sensor.

Set `password:` in `config.yaml` to require a password (HTTP basic auth, or `?token=` for webhooks). Only the guest wall page `/guest` and its submit endpoint stay open (3 messages per address per 10 minutes, 2 KB bodies, 24-character names, 140-character messages).
AI drawing (`aidraw.py`) calls the Anthropic Messages API; set `FLIPDOT_ANTHROPIC_BASE_URL` to point it at a test server.

## Saved data: `storage.db`

Everything you save — settings, playlists, schedule, compositions, scenes, drawings, slideshows (the
uploaded images/GIFs too), uploaded fonts, automations, flip counts and the Sendspin pairing — is kept in
one SQLite file, `../storage.db`: next to the app folder, not inside it, so updating or replacing the app
folder doesn't lose anything. Use `--storage PATH` to put it elsewhere.

`data/` (or `--data PATH`) is now just a working copy. At startup any file missing or out of date there is
restored from the database (the first time, an existing `data/` folder is imported). Saves and uploads go
into the database straight away, and anything else written to `data/` is copied in within a few seconds
and on shutdown. Deleting `data/` while the server is stopped is harmless.

Settings → *Storage* shows where the database is and how big it is. **Download backup** saves a consistent
copy of it (`GET /api/storage/backup`). **Restore from backup** uploads one
(`POST /api/storage/restore?confirm=1`, raw `.db` bytes); it's checked, then replaces all saved data when
the server next restarts (the replaced database is kept as `storage.db.before-restore`). `config.yaml`
(panel mapping, password) stays in the app folder and isn't part of the database.

## Music visualizer over Sendspin (optional)

The Music visualizer app follows a generated beat out of the box. To make it follow the actual music,
let Music Assistant stream visualizer data to the board over Sendspin. On the Pi, just run:

```sh
./scripts/setup-sendspin.sh
```

It installs the right aiosendspin into `./venv`, enables Sendspin (auto-connects to Music Assistant,
no pairing needed), switches the systemd service to the venv and prints the connection status.
To do it by hand instead:

```sh
python3 -m venv --system-site-packages venv      # keeps the apt-installed Pillow
venv/bin/pip install -r requirements-sendspin.txt  # needs Python 3.12+; pinned to Music Assistant's version
venv/bin/python server.py                        # run the server with the venv's Python
```

Then Settings → *Music Assistant visualizer (Sendspin)* → Enabled. The board advertises itself on
port 8928; approve or pair it in Music Assistant. When it works the status shows *connected* and
*receiving visualizer data ✓*, and the visualizer says *live from Music Assistant*.

### Music Assistant 2.7.x

Music Assistant 2.7.x speaks an older Sendspin (aiosendspin 2.0.0) that the default install can't
talk to. Run `./scripts/setup-sendspin.sh --ma27` (or install `requirements-sendspin-ma27.txt`); the
board then switches to its 2.7 mode by itself (`sendspin_legacy.py`; the Settings status names the
protocol in use). Differences:

- There's no pairing or encryption; Music Assistant accepts the board straight away.
- Music Assistant 2.7 sends no visualizer data. The board joins as a Sendspin *player*, gets the audio
  (mono, 22 kHz), works out loudness, spectrum and beats itself and plays nothing.
- Data only flows while the board is playing. Play to *Flipdot board* or group it with the speaker
  that's playing. If that speaker isn't a Sendspin player, use a Music Assistant player group.

Test: `pip install "aiosendspin==2.0.0" pillow` in a separate venv, then run
`tests/sendspin_legacy_e2e.py` with it.

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

## Doom (optional)

The *Doom* app (Apps → Games) runs the real Doom engine and shrinks each frame to 84×42: the 3D view
on top and health, armor and ammo in a strip at the
bottom. Set it up on the Pi once:

```sh
sudo apt install build-essential git curl unzip
./scripts/setup-doom.sh                               # build the engine + download Freedoom
./scripts/setup-doom.sh --shareware /path/to/DOOM1.WAD  # optional: the DOOM shareware episode
```

The script fetches [doomgeneric](https://github.com/ozkl/doomgeneric) at a pinned commit into
`native/doom/build/` and builds it with our platform file `native/doom/doomgeneric_flipdot.c` (it
writes small gray frames + player stats to stdout and reads key events from stdin). It downloads
[Freedoom: Phase 1](https://freedoom.github.io/) into `data/doom/`, so it's kept in `storage.db`;
pick the shareware WAD in the app's settings if you installed it. Neither the engine source nor
any WAD is in this repo.

Controls on `/play`: up/down walk, left/right turn, **A** fires, **B** opens doors and respawns
after dying; *Strafe left/right*, *Next weapon* and *Run* (toggle) appear while Doom runs.
Keyboard on the main page: arrows/WASD, Space fire, Enter use, Q/E strafe, R next weapon, Shift run.
It starts straight in E1M1 (level and skill are settings); there's no menu and no sound.

**Look** (setting): a flat gray picture is hard to read at 84×42 (a far wall and the floor dither to
the same gray), so by default the engine also reports what each pixel is (wall, floor, ceiling, sky,
monster/item, weapon) and how far away it is, and the board draws that instead: walls dithered by
distance (near = dense, far = sparse), floor, ceiling and sky dark, an outline wherever surfaces meet
or one wall stands in front of another, monsters and items solid with a dark ring, the weapon as an
outline. *Wireframe* keeps only the edges and monsters (fewest flips); *Shaded picture* is the plain
dithered image. Menus and the automap always use the shaded picture.

Settings: updates per second (default 10), look, dithering for the shaded picture (ordered / threshold / error diffusion),
contrast stretch, gamma, brightness, and what to do in quiet hours (default: refuse to run;
or run at 3 updates per second). One Doom runs at a time; stopping the app ends the process.

Be aware: at 10 updates per second Doom flips a few hundred dots per frame. That's loud and wears
the discs far faster than clocks or text. It's a fun demo, not something to leave running.
At 84×42 you can make out walls, doorways, light and dark areas and big sprites up close; small
enemies at a distance are a few dots.

Licences: doomgeneric is GPL-2.0 (built locally from its source, not distributed here);
Freedoom is under a BSD-style licence (`data/doom/FREEDOOM-COPYING.txt`); the DOOM shareware WAD
is id Software's and only installed if you provide it.

## Gamepads and the GameCube adapter

Plug USB gamepads or a Mayflash GameCube adapter (PC mode, the switch on the side) into the Pi. Each
adapter port is a player (port 1 = player 1). Stick or D-pad steer, A and B are the game buttons; in Doom,
L/R strafe, X switches weapon, Y toggles run, Z opens doors and Start pauses. The server reads
`/dev/input` directly, so its user must be in the `input` group:

```sh
sudo usermod -aG input $USER   # then log out and back in, or restart the service
```

Settings → Controllers turns gamepads on or off, sets the stick dead zone, lists the connected
controllers with their player numbers, shows the last inputs live, and remaps buttons (code name →
game key). `GET /api/gamepad` returns the same status.

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
- `rules.py` – automations; `qr.py` – QR code encoder (byte mode, ECC L/M/Q/H, versions 1–40)
- `store.py` – JSON files in `data/`; `storage.py` – the `storage.db` they're kept in; `mqtt.py` – tiny MQTT client + Home Assistant discovery; `ws.py` – WebSocket push; `server.py` – HTTP
- `static/` – web UI
- `apps/pack_arcade.py`, `pack_maze.py`, `pack_runner.py`, `pack_versus.py`, `pack_puzzle.py` – the game packs
- `native/doom/` – Doom platform file + Makefile (`scripts/setup-doom.sh` fetches and builds the engine); `apps/doom.py` – the Doom app
