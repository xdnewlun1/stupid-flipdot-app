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

The sidebar (a tab bar at the bottom on phones) has **Now playing**, **Create** (Draw, Import), **Library** (Pictures, Apps, Layers, Slideshows, Videos), **Programme** (Playlists, Schedule, Automations), **Guest wall** and **Settings**. Old links still work: `#media` opens Import, `#scenes` opens Pictures, and the old Settings groups open their new homes. `#settings/<group>` links straight to a Settings group (for example `#settings/music`).

| Page | |
|---|---|
| **Everywhere** | **Show on board** always puts something on the board (**Show for N s** shows it briefly, then goes back; **Add as layer** puts it on top of what's running; **Add to playlist**). Every page except Now playing and Draw has a **Now showing** strip at the top: a small live preview (click it for a bigger one), what's showing and why ("Digital clock · You started it · the schedule is paused"), **Resume schedule** whenever the schedule is paused, Next and Stop, and a warning with a Settings button when the app on the board needs setup or can't load its data. Stop, Previous, Pause and Next say what they'll do; Next and Previous step through a slideshow's pictures, Pause freezes a slideshow or animation (or holds a playlist item). Playlists, schedule, automations, layer sets, slideshow options and settings save themselves, with "Saved" next to what you changed; removals can be undone from the toast; naming and deleting use one dialog. Errors stay until dismissed, next to the button that failed where possible, with a button to the setting that fixes them. |
| **Now playing** (home) | the board large, what's on and why, Previous / Pause / Next / Stop / Resume schedule, a message box (**Show on board** or **Show for N s**), Clock, Weather and All black buttons, and the game controller link while a game runs. On a fresh install a **Getting started** list (set a location, show something, open the phone controller) sits here until you hide it |
| **Draw** | the board stays in view above the tools (pinned at the top on a phone); **zoom** in with the buttons under it or a two-finger pinch, and pan with the Move tool or by dragging while not drawing; the preview is view-only until you tick **Draw on the board** (that stops what's running) or pick a drawing layer; pencil, line, rectangle, circle (outline/filled), flood fill, text stamp, select + copy/cut/paste/flip, undo/redo (Ctrl+Z / Ctrl+Y), save to Pictures, **animation mode** (frame strip: add / duplicate / delete / reorder frames, per-frame duration, onion skin, play on the board, save to Pictures or open one from there; tools, undo and copy/paste work on the current frame), **Draw me…** (Claude draws a picture or a short animation from a description; needs an Anthropic API key in Settings → AI drawing), export PNG, **export GIF** (timelapse of the last 1–30 minutes, sped up; recorded in memory) |
| **Import** (was Image & video) | the one place to drop, choose or paste files, and each goes where it belongs, then that page opens with it highlighted: a **photo or GIF** is converted with the options on the page (stretch / cut / fit, threshold or dither, cutoff, negative) and saved to **Pictures** (change the options afterwards, or press **Change conversion** on its card, and the saved picture is updated); a **video** uploads into **Videos** (with progress) and opens selected while it converts; **several images or a .zip** ask whether to make a new **slideshow** (it opens on Slideshows as it uploads) or save each to Pictures. **Stream without saving**: a video file or the webcam (webcam on `http://localhost` or https only) streamed live (while a stream runs every status view says "Live stream: name", from this browser or another one, it stops when the tab closes or after 4 s without frames, and Stop ends it), or a short clip converted and saved to Pictures |
| **Pictures** (was Scenes) | one library for still pictures, animations and drawing layers (filter by kind), each with **Show on board**, **Add as layer**, **Edit** (opens it in Draw), Rename, PNG/JSON download and Delete; drawing layers on the board are marked |
| **Apps** | a search box and category chips (Everyday, Widgets, Generative, Utilities, Used in playlists, Games); everyday apps come first and games last, grouped as on the phone controller; "Used in playlists" holds the apps that show something you made (a picture, drawing layer, layer set or slideshow); the **Show on board** button sits under the app's name, above its options; options of the app you started change it on the board as you edit them (and the form opens with the values that are on the board); fields that don't apply hide (e.g. pixel scale vs size in dots, scroll speed only for the ticker); clocks (digital, analog, countdown), text (static / ticker / typewriter), weather, **calendar** (iCal links: next events with today/tomorrow labels and a countdown; all-day events, time zones, simple repeats), crypto, stocks, sports scores, any JSON URL, now playing from Music Assistant, music visualizer, Game of Life, matrix rain, plasma, bouncing balls, starfield, fireworks, ripples, metaballs, tunnel, spiral, maze, Lissajous, fire, **random effects** (cycles through them all), Snake / Tetris / Pong / Breakout / Space Invaders / Tron light cycles (vs a CPU or a second player) / Flappy / **Platformer** (run, jump, stomp walkers, reach the flag) / Blackjack (dealt from deckofcardsapi.com), **Doom** (the real game; see below), **NES** (your own ROMs; see below), **arcade**: Asteroids, Galaxian, Missile Command, Centipede, Lunar Lander; **maze & action**: Pac-Man, Frogger, Qix, Bomberman (up to 4 players), Tank Combat; **runners & racing**: Dino Run, Road Racer (pseudo-3D road), Cave Copter, Stacker, Sky Jump, Pitfall Run; **two players** (or vs the computer): Connect Four, Tic-tac-toe, Artillery, Checkers, Battleship, Quick Draw, Air Hockey; **puzzles**: 2048, Minesweeper, Sokoban, Lights Out, Simon, Columns, Mastermind (every game plays a demo of itself when idle and keeps a best score), disc exerciser, **Guest wall** (a QR code to `/guest`, where guests leave a name and message from their phone, alternating with the approved messages as scrolling text; moderate on the Guest wall page, or approve automatically), **Wi-Fi QR code** (guests scan it to join; very long names/passwords switch to a full-height inverted code) |
| **Layers** | stack apps with blend modes (On top, Add (light up), Halo, Invert where overlapping, Mask, Cut out), drawing layers you can keep editing live, position/size, scrolling, density, speed, Negative (swap a layer's lit and dark dots); saved **layer sets** (the API calls them compositions; usable in playlists) save themselves as you edit, and when the set you're editing is on the board, changes show there right away; picking a set only opens it, **Show on board** shows it; the editor follows the stack that's on the board |
| **Slideshows** | a playlist of your own images and GIFs: drop files or a .zip, which is unpacked automatically, any time; the pictures come first and **Display options** explain sizing, dithering and the brightness cutoff; while it plays, Previous / Pause / Next work and the picture on the board is highlighted; option changes apply to the playing slideshow right away; images show for 10 s by default, GIFs play through, then it moves on and loops; **GIF speed** (0.5×–4×) for the whole slideshow or per GIF, where fast GIFs at high speed skip frames the board can't flip in time; optional **random effects** give some items an effect such as glitch, wave or Game of Life, for a short burst or the whole item, with a chance, effect list and strength per slideshow; **React to music** lets the bass, beats and other parts of live music drive effects and GIF speed (see below) |
| **Playlists** | playlists of apps with durations and transitions (dissolve, wipe, slide); items that need setup (Weather without a location, Now playing without Music Assistant) are flagged and skipped; removing an item, rule, layer or slideshow image can be undone from the toast; renaming a playlist or composition updates the schedule, playlists and automations that use it, and deleting something lists what uses it first, then removes those items (automations are turned off); the **transitions** gallery (preview each one on the board) |
| **Schedule** | time-of-day/day-of-week rules choosing a playlist or "off", the default playlist, and **quiet hours** (cap on dots flipped per second + slower transitions during a time window) |
| **Automations** | when music starts/stops (Music Assistant), the weather turns rainy/snowy/clear, an event arrives, or the board has been idle N minutes → run an app or composition, play a playlist, show a notification, resume the schedule or go all black |
| **Guest wall** | messages left at `/guest`: approve, hide or delete them, or approve automatically; the count of waiting messages shows in the navigation |
| **Settings** | **Location & display** (theme, weather location and units, transition frame rate), **Fonts** (upload your own), **AI drawing**, **Music** (Music Assistant, live audio for the visualizer over Sendspin or Snapcast), **Smart home & API** (MQTT with optional Home Assistant discovery, webhook examples), **Controllers** (gamepads, button mapping), **Board hardware** (step through panels to check the mapping, edit column/row/ip/port/address/rotate, save to `config.yaml` without a restart), **Maintenance** (flips per disc as a heatmap, exercise rarely flipped discs now or daily), **Backup** (backup / restore of `storage.db`) |

`/play` is a phone game controller (pick any game from the grouped list; held sideways it puts the D-pad left and A/B right of the board; it reminds gamepad players that holding Start opens the game menu; D-pad + A/B with per-game labels, Start/X/Y/Z, player 1 or 2; Doom's extra buttons while Doom runs; Select / Start / Save / Load while the NES runs). USB gamepads work too; see below.

Drawing or showing something yourself pauses the schedule; **Resume schedule** (in the Now showing strip and on Now playing) goes back to it. "Draw me…" and Undo only change the board with **Draw on the board** ticked; otherwise a drawing goes onto a new drawing layer that you can add when you like.

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
POST /api/stop | /api/next | /api/prev | /api/schedule/resume   (next/prev step through a slideshow you started, else the playlist)
POST /api/pause   {"paused":true}      freeze a slideshow or animation, or hold the current playlist item
POST /api/params  {"app":"clock","params":{...}}   change the options of the app you started, live
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

MQTT (Settings → Smart home & API): `flipdot/notify`, `flipdot/run`, `flipdot/command` (stop, schedule, next,
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

## Videos

Longer clips play from the **Videos** page (Library). It needs ffmpeg on the server: `sudo apt install ffmpeg`
(the page says so if it's missing).

- **Upload**: drop WebM, MP4, MOV, MKV, GIF or anything else ffmpeg reads. Uploads stream straight to disk
  (limit: Videos → Options, 1024 MB by default) and are converted in the background, one at a time, with a
  progress bar. Sizing (cut to fit / fit inside / stretch) is chosen at upload; **Keep the original** (off by
  default) lets you convert again with other sizing later, at the cost of storing the whole file.
- **Format**: 8-bit grey frames already sized to the board, 15 frames a second by default (about as fast as
  the discs flip), in zlib chunks of 15 frames with an index (`video.py`), so any point is one small read away.
  Files are split into ~1-minute segments, so `storage.db` holds them as rows of a few hundred KB to ~1.5 MB.
  Size per hour of video: about 15 MB for simple footage, 50-85 MB for typical video, up to ~150 MB for
  pure noise. Kept originals add their full size.
- **Playing**: pick a video, then **Show on board**, Play/Pause, Stop, ±10 s and Previous/Next video. The
  timeline shows the time; dragging it previews that frame as dots, letting go seeks the board; arrow keys
  step 1 s (Shift: 10 s); the strip of thumbnails above it jumps there. **Set start / Set end** (or the
  handles on the timeline) mark a range to play or loop. Speed 0.5-2×. Display options (ordered dither,
  diffusion dither, threshold, cutoff, contrast, gamma, negative) are saved per video and apply while it
  plays. The title (from the file's metadata, else the video's name; editable, or clear it) scrolls across
  once when a video starts, on a black band. **Show the title when a video starts** (top of the Videos page,
  on by default) turns it off for every video; each video's player panel can say yes or no for that video,
  and the Video app's "Title when it starts" option (playlists, layers) overrides both. Older saved items
  with the old on/off box follow the setting when on and stay off when off.
- **Board-size videos** (exactly 84×42) play exactly as uploaded: no scaling, cropping or filtering, every
  frame kept at its own timing (a 60 fps file skips frames by timestamp as it plays, never blends them;
  uneven timing such as a GIF's per-frame delays is kept), each pixel one dot, lit from 128 up. Display
  options don't apply (Negative stays as an opt-in), the title is off unless turned on for that video,
  and they're marked "84×42 · dot for dot". Lossy codecs (H.264 MP4, ordinary VP9) can shift pixels near
  mid-grey; for exact pixel art upload lossless VP9 WebM (`ffmpeg -i frames%03d.png -c:v libvpx-vp9 -lossless 1 art.webm`)
  or put a GIF in Pictures. Other sizes, even 168×84, are scaled like any video.
- The **Video** app works in playlists (a video that isn't looping ends its item early) and as a layer.
  Pause, Previous and Next in the Now showing strip work for it, and the strip shows the position live.
- **Flips**: video flips a lot of discs, and the discs are loud. Ordered dither flips far fewer than
  diffusion. During quiet hours the app drops to 2 frames a second (option), and the quiet-hours flip limit
  still applies.

```
GET  /api/videos                        library, ffmpeg present?, options
POST /api/videos/upload?name=F&mode=crop&keep=0   (raw file bytes)
POST /api/videos/<id>                   {"name"|"title"|"show_title": "auto"|"on"|"off"|"display"|"range"}
POST /api/videos/<id>/play              {"t":120,"speed":1.5}
POST /api/videos/control                {"seek":s} | {"nudge":-10} | {"speed","loop","start","end"}
GET  /api/videos/<id>/frame?t=12.5      that frame as dots (scrub preview)
POST /api/videos/<id>/delete | /restore (deleted for good after 30 s) | /convert {"mode"}
```

## Music visualizer

Apps → Music visualizer moves with the frequencies of the music, continuously; it doesn't wait for beats.
Styles: **rain** (each column rains with its part of the spectrum, the bass fills a puddle), **spectrum
bars** (16, 21 or 42 full-height bars with falling peak caps; from the bottom, mirrored up and down, or
bass in the middle), **spectrum mountain** (the spectrum as a filled ridge, same layouts), **sunburst**
(rays from the middle), **waterfall** (a spectrogram scrolling out from the middle), **rings** (the bass
sizes a disc, each other band moves its own ring), **blob** (metaballs pulsing with the bass), **waveform**
(filled, thick line or mirrored), **warp** starfield, **sparkle**, and five pattern styles: **tunnel** (a
kaleidoscope rushing towards you), **vortex** (spiral arms turning into the middle), **moire** (two sets
of rings crossing), **lissajous** (a turning figure with an echo) and **plasma** (flowing stripes). In
those, the bass gives size and pulse, the mids bend the shape, the highs add sparkle.

Nothing stands still: every style runs on a clock that ticks slowly in silence and faster with the
music, and a low ripple runs under the spectrum, so quiet passages show slow, calm movement. The bass
disc in the middle (rings, sunburst, warp, tunnel, vortex) inverts what's under it and keeps a lit
edge, so it reads over everything else.

- **Scale** (0.5–4, default 1.5) multiplies how far it moves: bar height, ray and ring size, wave height,
  amount of rain, warp speed, sparkle density. At 1.5 loud parts reach the edges; 3 pins more of it there.
- **Sensitivity** (default 1) shows quiet parts bigger (higher) or smaller (lower).
- **Smoothing** (0 = snappy, 1 = slow fall; default 0.3) sets how fast things fall back.
- **Beat effects**: off (default), subtle (a ring from the middle on each beat) or big (a thick
  shockwave, and the board flashes every 4th beat).

**Mix** (a style) changes between styles while the music plays, with a transition at each change.
Both styles keep following the music during the transition, and each keeps its own state (rain drops,
peak caps, waterfall history).

- **When**: every 4/8/16/32 bars (default 16), counted from the first beat of the song and realigned on
  downbeats, so changes land on phrase boundaries. This needs a steady beat from live audio; with the
  generated beat, or a beat too irregular to count, it changes every N seconds instead (default 20).
  Also on each new song (on by default) and, optionally, on a drop or break (the last ~1.5 s getting
  much louder or quieter than the last ~10 s). **At least … seconds between changes** (default 8)
  holds a song change back until it has passed.
- **Styles in the mix**: a checklist, all by default; random order (never the same twice in a row) or
  in list order. **Random look** gives each new style a random layout, bar count, wave look and scale.
- **Transition**: random, one of the list, or none. Random picks *swirl* or *zoom-through* half the time:
  the old picture turns and grows past you while the new one turns in from small. Morph, migrate,
  scatter, life, ghost, flash, glitch and zoom-in/out aren't offered: they're too slow live, are built from a
  single frame, or flicker. **Length** (default 1.5 s) is rounded to whole beats when the beat is steady.
- The *Now showing* line names the current style: "Music visualizer · Mix (now: sunburst, …)". Mix
  works in layers and playlists too.

Levels come from `audio_signal.py`'s display reading: per band and spectrum bin a running peak and
floor, so quiet and loud songs both use the full height, and a held note stays up rather than sinking
back. Without live audio it follows a generated beat. Flips per update (of 3528 dots) on music-like
input at the defaults, then in silence: rain ~590/70, bars ~270/3, mountain ~270/6, sunburst ~290/50,
waterfall ~520/35, rings ~500/20, blob ~270/12, waveform ~220/13, warp ~370/80, sparkle ~230/85,
tunnel ~470/180, vortex ~590/80, moire ~680/200, lissajous ~760/120, plasma ~540/100. A Mix transition
flips ~500–750 per update. Render time on a desktop PC: under 1.2 ms per frame for any style, under
~2.5 ms (95th percentile) during a Mix transition with the two heaviest styles.

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

Then Settings → Music → *Audio source* → Sendspin. The board advertises itself on
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

## Music visualizer over Snapcast (optional)

The alternative to Sendspin: the board joins Music Assistant (2.7 or later) as a Snapcast player and
analyses the audio it receives. On the Pi:

```sh
./scripts/setup-snapcast.sh          # --host ADDRESS if the Snapcast server isn't on the Music Assistant host
```

It installs `snapclient` with apt, turns off the snapclient system service that comes with the package
(that one would play the music through the Pi's audio output) and sets Settings → Music → *Audio source*
to Snapcast. In Music Assistant, add the provider (**Settings → Providers → Add → Snapcast**, built-in
server), then play to the **Flipdot board** player or group it with the speaker that's playing.

How it works (`snapcast_link.py`): the board's server runs `snapclient --player file --mixer none`, which
writes the decoded audio to a pipe at the moment it's due to play, in sync with the other Snapcast
speakers. Nothing goes to a sound card. The audio is analysed the same way as Sendspin's 2.7 mode
(loudness, 16 spectrum bands and beats, pure Python, 20 frames a second). snapclient handles the
codecs (flac by default; pcm, ogg and opus work too), clock sync and reconnecting; the link restarts it
if it exits. Server *auto* means the host in the Music Assistant URL; ports 1704 (audio) and 1705
(control, used once to name the player). Settings shows whether it's connected, receiving audio,
the codec and the server buffer.

Compared with Sendspin:

- No Python 3.12 venv or version matching: Debian bookworm's snapclient 0.26 works, as do newer ones.
- Audio only. Song changes come from the Music Assistant API (the Server URL above), so set that too.
- Like Sendspin's 2.7 mode, audio flows only while the board plays: play to it or group it. Snapcast
  sends nothing to muted players. Music Assistant's volume for the board doesn't affect the visualizer.
- Light: in the test (flac) snapclient used 0.4% of a desktop core and the board's whole server 5% while
  showing the visualizer. Expect several times that on a Pi.

`tests/snapcast_e2e.py` runs the link against a local snapserver (127.0.0.1, flac and pcm, a 120 BPM
test signal, restart and reconnect). It needs `snapserver` and `snapclient` (or `SNAPSERVER` /
`SNAPCLIENT` pointing at them) and skips otherwise.

## Slideshows that react to music

Slideshows → Display options → **React to music** lets the live audio from Settings → Music (Sendspin or
Snapcast, the same signal the visualizer uses) drive effects on top of the pictures:

- Each part of the music drives one effect, with an amount and a sensitivity. Defaults: **bass** (kick
  drum, low notes) → glitch, **mid** → wave, **high** (hi-hats) → static, **beat** → a one-frame invert,
  **loudness** → tones all of them down in quiet parts. Life and Melt aren't offered: they build up over
  time and don't suit a strength that changes every frame. Several effects can run at once; they're
  applied in a fixed order (pixelate, mirror, kaleidoscope, wave, glitch, outline, scanlines, static,
  flash, invert), after the item's random effect and the transition between items.
- **Loudness drives GIF speed** (on by default): quiet parts play at 0.75×, loud parts up to 2×, on top of
  the GIF speed, eased so there are no jumps. **Skip a frame on each beat** jumps GIFs one frame forward.
- Levels adjust to each song (a running peak and floor per band), so quiet and loud songs both use the
  whole range. Without live audio the slideshow plays normally, or reacts to a generated 120 BPM beat if
  you choose. Reacting pauses during quiet hours. It works the same when the slideshow is a layer.
- The section shows whether audio is arriving and small live meters (`GET /api/audio/levels`).

The audio reading lives in `audio_signal.py`, shared with the visualizer. `tests/test_audio_react.py`
covers the band mapping, auto-gain, the display reading (held notes stay up, attack and release), beats and
the variable-rate GIF clock.

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

## NES games (optional)

The *NES* app (Apps → Games) emulates the NES and draws its picture on the board. **No games come
with it: you must supply your own `.nes` files** (dumps of cartridges you own, or free homebrew).
Set up the emulator on the Pi once:

```sh
sudo apt install build-essential patch curl python3
./scripts/setup-nes.sh
```

The script downloads [agnes](https://github.com/kgabis/agnes) (MIT) at a pinned commit into
`native/nes/build/`, checks the files' SHA-256, applies `native/nes/agnes_flipdot.patch` (it records
for every pixel whether it's background or a sprite and which palette entry it uses, and adds
mappers 3 and 7) and builds `native/nes/build/nes_flipdot` with our driver `native/nes/nes_flipdot.c`.
It rebuilds from scratch if the build was made on another CPU, and finishes by checking that the
emulator sends a frame. The emulator runs the game at 60 frames per second, sends the board only the
frames it shows, and reads buttons on stdin.

**ROMs:** Apps → NES → *NES ROMs* → *Upload ROM…* (or `POST /api/nes/roms?name=Game.nes` with the
file as the body; `GET /api/nes/roms` lists them, `POST /api/nes/roms/delete {"name": …}` removes one
and its saved game). The upload checks the iNES header, the size (1 MB at most) and the mapper:
NROM (0), MMC1 (1), UxROM (2), CNROM (3), MMC3 (4) and AxROM (7) run; others are refused with a
message. ROMs and saves live in `data/nes/`, so they're kept in `storage.db`.

**Controls:** D-pad, A, B and Start as on the NES; player 2 is the second pad (or Player 2 on
`/play`). Select is Z or X on a gamepad. Y saves the game and L loads it (one saved game per ROM,
in `data/nes/saves/`); `/play` shows *Select*, *Start*, *Save* and *Load* buttons while the NES runs.
A short Start on a gamepad reaches the game; holding it opens the game menu, which pauses the
emulator until you come back.

**Look:** *Layered outline* (default) scores the background by how much each colour stands out from
the backdrop (the most common colour on screen), outlines terrain with a light dither inside, and
draws sprites solid with a dark ring. *Filled* shades the background by that score instead;
*Plain dither* dithers the picture's brightness. *Shading strength* and *Outline threshold* tune them.
**Camera:** the whole 256×224 screen is squeezed to 84×42 (small text becomes unreadable); *Follow
the player* shows a 252×126 window (3× closer) that tracks the player's sprites; *Auto* (default)
follows only once a game has scrolled for a few seconds, so menus and one-screen games stay whole.
Other settings: updates per second (default 10) and quiet hours (refuse, or 3 updates per second).

Be aware: scrolling games flip a lot of dots. Measured on the PC at 10 updates per second: Nova the
Squirrel (a scrolling platformer) averaged about 150 flips per update with peaks of 600–900 while the
screen scrolls; Thwaite and Concentration Room (one-screen games) about 12–40. The emulator takes
about 1.5 ms per NES frame and the board conversion about 1.5 ms per update on the PC, roughly half a
core on a Pi 4 (estimated, not measured on a Pi). It's loud and wears the discs; don't leave it running.

Legal: agnes is MIT-licensed and built locally from its source (not distributed here). Commercial
NES games are copyrighted: only use ROMs you have the right to use. None are included, downloaded
or committed (`*.nes` is in `.gitignore`).

## Gamepads and the GameCube adapter

Plug USB gamepads or a Mayflash GameCube adapter (PC mode, the switch on the side) into the Pi. Each
adapter port is a player (port 1 = player 1). Stick or D-pad steer, A and B are the game buttons; in Doom,
L/R strafe, X switches weapon, Y toggles run, Z opens doors and Start pauses; in NES games, Z or X is
Select, Y saves and L loads. The server reads
`/dev/input` directly, so its user must be in the `input` group:

```sh
sudo usermod -aG input $USER   # then log out and back in, or restart the service
```

Hold Start on a gamepad to open the game menu: up/down choose, left/right change category, A plays, B returns to your game.
(Settings → Controllers → Menu button: Hold Start or Off.)

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
- `native/nes/` – NES driver, agnes patch + Makefile (`scripts/setup-nes.sh` fetches and builds the emulator); `apps/nes.py` – the NES app
