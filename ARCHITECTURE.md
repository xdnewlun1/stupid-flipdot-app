# Architecture, rendering and performance

How the flipdot server is put together: what runs where, how a picture gets from an app to the discs,
and what keeps it fast and steady on a Raspberry Pi 5. For using the board, see [README.md](README.md).

## The big picture

```
 browser / phone ──HTTP, WebSocket──┐        Music Assistant ── Snapcast / Sendspin ──┐
 USB gamepad ──evdev──┐             │                                                  │
 MQTT / Home Assistant ──┐          │                                                  │
                         ▼          ▼                                                  ▼
                   ┌──────────────────────────── server.py (one Python process) ─────────────────────┐
                   │  routes · ws.Hub · mqtt · rules · gamepad          audio links → audio_signal   │
                   │                       │                                      │                 │
                   │                       ▼                                      ▼                 │
                   │   runner.Runner ── app.render(canvas, now) ── transitions / layers / visualizer │
                   │                       │                                                         │
                   │                       ▼  board.set_canvas(picture)                              │
                   │   flipdot.Board: framebuffer → dirty panels → one sender thread per RS485 bus   │
                   └───────────────────────┬──────────────────────────────────────────────────────────┘
          child processes:                 │ TCP (or UDP), one connection per ip:port
          nes_flipdot, doomgeneric_flipdot │
          snapclient, ffmpeg               ▼
                                 RS485-to-Ethernet box (192.168.11.100:5000-5002)
                                           │ 3 buses × 6 panels, 57600 baud
                                           ▼
                                 18 AlfaZeta 28×7 panels = 84×42 discs
```

Everything the board shows is an **app** (`apps/`): a clock, a game, a slideshow, a stack of layers, the
music visualizer. The **runner** decides which app is on, asks it for a picture every `interval` seconds,
blends in transitions and notifications, and hands the result to the **board**, which works out which
panels changed and sends just those to the panels.

## Processes and threads

The server is one Python process with many threads, plus a few child processes for the heavy lifting.

| Where | What | Notes |
|---|---|---|
| main thread | `ThreadingHTTPServer` accept loop | one thread per HTTP request (`request_queue_size` 128) |
| runner | the render loop (`runner.Runner._loop`) | renders the app on top, transitions, notifications |
| one per RS485 bus | `flipdot.Board._bus_loop` | sends panel frames to its ip:port; 3 on this board |
| one per WebSocket | `ws.Hub` sender | pushes the packed picture and status to that browser |
| audio | `snapcast_link` reader + log reader, or Sendspin's asyncio loop | reads and analyses the live audio |
| background | timelapse recorder, board care, gamepad, MQTT, automations, widget fetches, video conversion and decode-ahead, storage mirror | mostly asleep or waiting on I/O |
| child process | `nes_flipdot`, `doomgeneric_flipdot` | the emulators, talking over stdin/stdout pipes |
| child process | `snapclient`, `ffmpeg` | audio decoding; video conversion |

**The GIL.** Python runs one thread's bytecode at a time, so all the Python work above shares about one
core; the threads interleave rather than run in parallel. Waiting on sockets, pipes and sleeps releases
the lock, and so does much of the C work in Pillow and zlib. The emulators, snapclient and ffmpeg are
separate processes, so they get the Pi's other cores. This is why the per-frame Python work is kept
small, and why the music visualizer hands its pictures to the board at a fixed time (see
[Steady timing](#steady-timing-music)): if a web request or the audio analysis holds the lock for a
moment, the timing at the discs doesn't move.

## Start-up and saved data

`server.main()` loads `config.yaml` (PyYAML if present, else a small parser for Maestro's flat format),
creates the `Board` (which starts its bus threads and pushes an all-black frame), then:

1. `storage.Storage.startup()` restores the `data/` folder from **`storage.db`** (SQLite, outside the
   app folder). The database is the source of truth; `data/` is a working copy the rest of the code
   reads and writes as plain files. JSON saves are copied in straight away, and a background mirror
   picks up anything else every few seconds.
2. `store.Store` loads settings, scenes, playlists, compositions and the schedule from `data/`.
   `refs.py` tracks where saved things are used, so renames follow and deletes don't leave broken links.
3. The video library, `Runner`, `BoardCare` (quiet hours, wear counts), timelapse `Recorder`,
   automations, the WebSocket `Hub`, MQTT, the Sendspin/Snapcast links and gamepads start.
4. `serve_forever()`.

## From an app to the discs

### Apps (`apps/base.py`)

An app is created with its params and then called as `render(canvas, now)` every `interval` seconds.
It draws into the canvas and returns `False` when it's finished (a one-shot notification); anything
else means keep going. Apps can change their own `interval` from frame to frame (a video sets it to its
frame time; the visualizer sets it to the next audio frame). Other hooks:

- `input(key, action, player)`: controller input (`interactive = True` apps).
- `input_now(...)`: optional fast path that takes input **without** the runner's lock (the emulators
  use it to push a button straight down their pipe).
- `pausable`: Pause freezes the app on its own clock (slideshows, animations, video).
- `steady_timing`: the board keeps every change the same time behind (the music visualizer).
- `Widget` subclasses fetch data in a background thread every `refresh` seconds; `http_get` caches
  responses so several apps share them. A missing setting raises `NeedsSetup`, shown as a hint.

`apps/__init__.py` imports every app module, which registers them; `apps.create(ctx, spec)` builds one
from `{"app": id, "params": {...}}`.

### The canvas (`canvas.py`)

A `Canvas` is a `bytearray` with **one byte per dot** (0 or 1), row-major. It draws lines, rectangles,
circles, text in the bitmap fonts (`fonts.py`, proportional by trimming empty columns) and
seven-segment digits. The same layout is used everywhere a whole picture moves around: the board's
framebuffer, layers, transitions and the video decoder, so pictures are copied with slices rather than
dot by dot. `charfx.py` animates single characters of a clock or text (odometer roll, flap...).

### The runner (`runner.py`)

The runner owns what's on the board: **manual** (drawing), **app**, **playlist** or **schedule**, plus a
notification on top and an optional transition. Its loop:

1. advance the playlist or schedule if an item is due; end a notification if it's over;
2. call `app.render()` into a fresh canvas (the app's clock stands still while paused);
3. if a transition is running, render the outgoing app too and mix the two;
4. `board.set_canvas(picture)`;
5. wait until `frame start + interval`, timed from the start of the frame so rendering doesn't slow the
   frame rate, or until `wake` is set (input, a new emulator frame, a control action).

Rendering happens under the runner's lock, so control calls from HTTP, MQTT and the WebSocket see a
consistent state. Game input that only needs to reach an emulator skips that lock (`input_now`).

### Transitions (`transitions.py`)

Two kinds, both mixing an old picture into a new one over `duration` seconds (eased by smoothstep):

- **Reveal maps** (wipes, dissolve, iris, clock, blinds, tiles, drip, spiral...): every dot gets a time
  0..1 at which it switches, worked out once per transition; each frame copies the dots whose time has
  passed, with a bright leading edge on wipes.
- **Moves** (slides, pushes, zoom, flip, melt, pixelate, glitch...) and **generative** ones: *morph*
  blends the two pictures' signed distance fields (chamfer 3-4) so shapes flow into each other, with a
  cache of fields keyed by picture (GIF frames repeat); *migrate/scatter* pair the white dots of both
  pictures along a Hilbert curve; *life* runs Game of Life settling into the new picture.

The outgoing app keeps animating under the transition, so both sides move. The music visualizer's Mix
has its own `LazyMorph` (see [Music](#music-the-visualizer-and-steady-timing)).

### Layers (`apps/layers.py`)

A layer set stacks apps, each rendering into its own canvas at its own rate and speed. Compositing
treats each whole-board bitmap as one big Python integer (one byte per dot, so shifts never carry), and
every blend mode is a handful of bitwise operations on whole boards: *add* is OR, *xor* is XOR, *mask*
and *cut* are AND with a box, *halo* dilates the layer by shifting the integer in eight directions.
Density (Bayer-dithered) and scrolling are applied on the same bitmaps. A layer only re-renders when
its own interval is due; the stack's interval is the soonest of its layers.

## The board driver (`flipdot.py`)

### Protocol

Each 28×7 panel takes one frame: `0x80, cmd, address, 28 column bytes, 0x8F`, where each column byte
holds the 7 dots of a column (bit 0 at the top). `0x83` writes and flips at once. With
`sync_refresh: true`, panels are written with `0x84` (write, don't flip yet) and one `0x80 0x82 0x8F`
then flips every panel on that bus together. A panel mounted upside down has `rotate: 180`.

### Framebuffer and change detection

The board keeps a `bytearray` framebuffer in the canvas layout. `set_canvas()` normalises the picture to
0/1 with `bytes.translate` and compares it with the framebuffer in one operation: an **unchanged picture
returns immediately** (most apps redraw the same picture several times a second). For a change, each
panel's 7 row slices are compared to find the dirty panels. `packed()` (the WebSocket format) is built
with `translate` and `int(..., 2)` and cached per version, so every browser shares one copy.

A panel frame is built from whole rows: a 28-dot row read as a big integer and shifted left by its row
number sets exactly that bit in every column byte, so seven shifts and ORs make all 28 column bytes.

### One sender thread per bus

Each `ip:port` (one RS485 line) has its own thread, socket and wake event, so a bus that's down (3 s
connect timeout, 2 s retry) or busy never holds up the others. A sender thread:

1. **coalesces**: if the previous send on this bus was under 20 ms ago, it waits out the rest, so a
   burst (dragging on the Draw page) goes out as one picture; a lone change goes out at once;
2. **paces to the line** with `baud:` set (57600 here): it never hands the line more than it can carry
   (10 bits per byte), so fast animations show the newest picture instead of queuing lag in the box;
3. collects its dirty panels, builds their frames, skips any identical to what the panel already shows,
   and sends them in **one write** (one TCP packet; one datagram per panel over UDP).

Flips per disc are counted on this path (`board.flips`, the Maintenance heatmap), and a frame is only
marked sent once the write succeeded, so failures retry with the newest picture.

### Timing modes

Panels flip their columns left to right, about 85 ms across a panel on this board (`sweep_ms`). The
driver chooses per change how the buses are timed:

| Mode | When | What the buses do |
|---|---|---|
| **Sweep wave** | a change that stands on its own (`sweep_mode: auto`: the previous wave has finished) | each column of panels starts `sweep_ms` after the one to its left, so a big change crosses the board as one wave; with `sync_refresh` only the 3-byte refresh is staggered |
| **Together** | moving content (changes closer together than a wave), or any game (`board.interactive`) | every bus sends at once: no tearing between columns, no extra delay for the first move after standing still |
| **Steady** | the music visualizer and Sync test (`board.steady`) | no wave; a bus with any change resends **all** its panels in a fixed order (unchanged ones get identical data, so no extra flips) and the picture goes out at a fixed `present_at` time; every change takes the same time to reach the discs |
| **Flip budget** | quiet hours (`max_flips_per_sec`) | a shared token bucket; panels over budget wait and pick up the newest picture later |

`POST /api/board/timing` changes `sweep_ms`, `sweep_mode` and `sync_refresh` live, for tuning by eye.

## Live views

- **WebSocket** (`ws.py`, stdlib RFC 6455): each browser gets a sender thread that wakes on any board
  or status change, throttled to 40 messages a second: a binary message (`u32 version, u16 w, u16 h`,
  then the dots packed MSB first) and a JSON status when it changed. Browsers send input, drawing dots,
  drawing-layer pictures and live-stream frames the other way. Pages fall back to polling `/api/board`.
- **Timelapse** (`timelapse.py`): a ring buffer of packed pictures (at most 10 a second, 30 minutes,
  4 MB), rendered to an animated GIF with Pillow on request.

## Media and video

- **Images and GIFs** (`apps/media.py`, `media_upload.py`): Pillow scales (stretch / cut / fit) and
  thresholds or dithers each frame once, at upload; slideshows play the stored dots.
- **Video** (`video.py`, `apps/video.py`): uploads are converted once by `ffmpeg` (one at a time,
  `nice 10`) to grey frames already at board size, stored in the board's own **FDV1** format: segment
  files of 60 chunks × 15 frames, each chunk zlib-compressed as the first frame plus byte-wise
  differences. Seeking is one read and one small decompress. The delta coding runs in Pillow
  (`ImageChops.add_modulo` / `subtract_modulo`), and while a chunk plays the next one is decoded in the
  background, so playback never waits on a decompress. The display options (dither, cutoff, contrast,
  gamma, negative) are a 256-byte lookup table plus a Pillow dither, applied as it plays, so they can
  change live. A board-size upload (84×42) is stored pixel for pixel at its own frame timing.

## Emulators (NES and Doom)

`native/nes/nes_flipdot.c` (agnes) and `native/doom/doomgeneric_flipdot.c` (doomgeneric) are small C
programs the apps start as child processes:

- **stdout**: a header plus the picture (NES: palette colours, a layer plane, sprites and palette RAM;
  Doom: 160×100 grey plus a geometry plane of surface type and distance), at the app's
  "Board updates per second".
- **stdin**: 3-byte (NES) or 2-byte (Doom) key events.
- **Latency**: after any button goes down or up, the emulator also sends every 2nd frame (NES) or every
  tic (Doom) for about 150 ms, then carries on at the normal rate from there, so a press shows up within
  a frame or two. The app draws each frame the moment it arrives (the reader thread wakes the runner),
  and buttons reach the pipe through `input_now`, without waiting for a render.
- Python turns the frames into dots with Pillow (the NES "outline" look marks where sprites and terrain
  meet; Doom outlines walls by distance), and a camera can follow the player in scrolling games.

## Music: the visualizer and steady timing

### Where the signal comes from

- **Snapcast** (`snapcast_link.py`): the server runs `snapclient --player file` writing raw PCM to a
  pipe; snapclient handles the protocol, codecs and clock sync, and writes each 10 ms of audio at the
  moment it's due to play. The board analyses it itself (`sendspin_legacy._Analyzer`): left channel,
  48 kHz decimated to 24 kHz, a Hann-windowed 512-point FFT (done as a 256-point complex FFT of the
  samples in pairs, pure Python), 16 log-spaced bands from 40 Hz to 8 kHz plus loudness, **50 times a
  second** (20 ms steps, so the 21 ms windows cover all the audio).
- **Sendspin** (`sendspin_link.py`): Music Assistant sends loudness, spectrum and beats, timestamped
  against a synchronised clock.
- **Neither**: a generated 120 BPM beat.

`audio_signal.py` turns either link's signal into what the visualizer and music-reactive slideshows use:
per-band auto-gain (so quiet and loud songs both fill the board), display envelopes, and beats from an
onset detector merged with the link's own.

### Steady timing (music)

The priority is that every beat reaches the discs **the same time** behind the speakers, so the other
speakers in the group can be delayed to match. Every stage that used to wait "until it got round to it"
is pinned to a clock:

1. **Audio clock.** Each analysis frame is stamped with when its audio plays: sample *n* plays at
   `anchor + n / rate`, where the anchor is the earliest any chunk has arrived relative to its sample
   position over the last 4 s (snapclient writes on time, so hiccups only ever make arrivals later). A
   stall resets it.
2. **Fixed delay.** A frame comes due **Board delay** (`snapcast.delay_ms`, 150 ms default, live) after
   its audio plays, however long reading and analysis took.
3. **Draws locked to frames.** The visualizer draws 25 times a second, each time exactly as an analysis
   frame comes due (`audio_signal.steady_interval`), rather than on a free-running timer.
4. **Fixed presentation time.** The runner hands each steady picture to the board with
   `present_at = frame start + 25 ms`, and the bus threads send it then, so render-time wobble on the Pi
   doesn't show; steady mode resends whole buses in a fixed order and skips the sweep wave.

Measured with a stand-in snapclient into fake 57600-baud buses: speakers to the flip command, standard
deviation 12.6 ms and 10th-90th percentile 215-245 ms with a 150 ms Board delay (before: about 75-340 ms);
renders randomly 0-15 ms slower made no real difference. What's left is the 40 ms between pictures.
The real total adds the RS485 send (about 34 ms per bus) and the panel's own flip (0-85 ms across a
panel). The **Sync test** app and `/api/sync/click.wav` measure the whole chain on the real board:
every click flips every disc through exactly this path.

The Mix's **morph** (`apps/visualizer.LazyMorph`) recomputes both moving styles' distance fields every
frame (the same result as the board-wide morph, a few ms); if that ever takes over 12 ms it updates one
side a frame instead, so steady timing holds.

## Other subsystems

- **MQTT** (`mqtt.py`): a stdlib MQTT 3.1.1 client (QoS 0) with Home Assistant discovery.
- **Automations** (`rules.py`): edge-triggered rules (music start/stop, weather, events, idle).
- **Gamepads** (`gamepad.py`): reads `/dev/input/event*` with `select()`; the Mayflash GameCube adapter's
  four ports map to players.
- **Guest wall** (`guestwall.py`), **AI drawing** (`aidraw.py`, the Anthropic Messages API over urllib),
  **panel setup** (`panelsetup.py`: identify panels, validate a mapping, rewrite `config.yaml`'s
  `panels:` block, applied without a restart).
- **Web UI** (`static/`): plain ES modules, no build step. `index.html` is the main app, `play.html` the
  phone controller, `guest.html` the guest wall form.

## Performance

### Per-frame costs

Measured on the development desktop with `python3 tests/bench_core.py`; a Raspberry Pi 5 (Cortex-A76)
is roughly 2.5-3× slower for single-threaded Python.

| Step | Before the perf work | Now |
|---|---|---|
| Board update, same picture | 0.53 ms | 0.004 ms |
| Board update, every dot new | 0.74 ms | 0.022 ms |
| Frames for all 18 panels | 0.84 ms | 0.090 ms |
| Packed picture for the web (now shared by every browser) | 0.21 ms each | 0.011 ms |
| Video: decode a 1 s chunk (now done ahead, in the background) | 3.1 ms | 0.20 ms |
| Video: encode a 1 s chunk during conversion | 3.6 ms | 0.20 ms |
| Video: ordered dither | 0.19 ms | 0.025 ms |
| Morph transition frame | 11 ms | 8.7 ms (identical output) |
| Audio analysis frame (FFT) | 0.39 ms | 0.21 ms (identical output) |

Most apps render in 0.1-2.5 ms. Game input latency (press to board picture, 10 updates a second):
NES (a stand-in emulator with the real send timing) 111-158 ms → 32-90 ms mean, depending on how fast
the game reacts; Doom (the real engine) 117 → 39 ms mean, 205 → 61 ms worst.

### Budgets on the Pi

- The RS485 lines carry a full bus (6 panels) in about 34 ms at 57600 baud, about 29 whole pictures a
  second; most changes touch fewer panels. Steady timing keeps the visualizer at 25 a second.
- Steady pictures must be rendered within 25 ms of the frame starting (`runner.STEADY_BUDGET`); a
  visualizer frame is a few ms even with a morph running.
- The Snapcast analysis is 0.2 ms a frame on the desktop, so well under 1 ms on the Pi 5 (estimated), 50 a second.

### Tuning (`config.yaml`)

| Setting | Effect |
|---|---|
| `baud: 57600` | pace sends to the RS485 line (0 = don't pace) |
| `sync_refresh: true` | write all panels, then flip them together (no tearing; tightest steady timing) |
| `sweep_ms: 85` | the panels' left-to-right flip time, for the sweep wave (0 = off) |
| `sweep_mode: auto` | wave only for standalone changes; `always` for every change |

Plus **Board delay** in Settings → Music for Snapcast, and each emulator's "Board updates per second".

### Tests and tools

- `python3 -m unittest tests.test_board tests.test_video_format tests.test_snapcast_timing ...` and
  `python3 -m pytest tests/test_audio_react.py tests/test_visualizer_mix.py`: the board driver (protocol
  frames against a dot-by-dot reference, real TCP sends to fake buses, timing modes), the video format,
  the audio clock and delay, the visualizer.
- `python3 tests/bench_core.py`: the per-frame timings above; run it on the Pi before and after a change.
- `tests/snapcast_e2e.py`: the Snapcast link against a real snapserver (needs snapserver and snapclient).
