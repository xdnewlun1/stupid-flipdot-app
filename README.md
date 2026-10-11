# Flipdot board

Web UI + HTTP API for an 84×42 AlfaZeta flipdot board (18 panels of 28×7, driven over the
RS485-to-Ethernet box at 192.168.11.100, ports 5000–5002, panel addresses 0–5 — see `config.yaml`).
How it's built, how pictures get to the discs, and the performance and timing work: [ARCHITECTURE.md](ARCHITECTURE.md).

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
| **Apps** | a search box and category chips (Everyday, Widgets, Generative, Utilities, Used in playlists, Games); everyday apps come first and games last, grouped as on the phone controller; "Used in playlists" holds the apps that show something you made (a picture, drawing layer, layer set or slideshow); the **Show on board** button sits under the app's name, above its options; options of the app you started change it on the board as you edit them (and the form opens with the values that are on the board); fields that don't apply hide (e.g. pixel scale vs size in dots, scroll speed only for the ticker); clocks (digital, analog, countdown), text (static / ticker / typewriter), weather, **calendar** (iCal links: next events with today/tomorrow labels and a countdown; all-day events, time zones, simple repeats), crypto, stocks, sports scores, any JSON URL, now playing from Music Assistant, music visualizer, Game of Life, matrix rain, plasma, bouncing balls, starfield, fireworks, ripples, metaballs, tunnel, spiral, maze, Lissajous, fire, **mushroom garden** (twelve kinds of fungi growing and withering), **random effects** (cycles through them all; every generative app can **react to music**, see below), Snake / Tetris / Pong / Breakout / Space Invaders / Tron light cycles (vs a CPU or a second player) / Flappy / **Platformer** (run, jump, stomp walkers, reach the flag) / Blackjack (dealt from deckofcardsapi.com), **Doom** (the real game; see below), **NES** (your own ROMs; see below), **arcade**: Asteroids, Galaxian, Missile Command, Centipede, Lunar Lander; **maze & action**: Pac-Man, Frogger, Qix, Bomberman (up to 4 players), Tank Combat; **runners & racing**: Dino Run, Road Racer (pseudo-3D road), Cave Copter, Stacker, Sky Jump, Pitfall Run; **two players** (or vs the computer): Connect Four, Tic-tac-toe, Artillery, Checkers, Battleship, Quick Draw, Air Hockey; **puzzles**: 2048, Minesweeper, Sokoban, Lights Out, Simon, Columns, Mastermind (every game plays a demo of itself when idle, except Doom and NES; scored games keep a top 5 with initials, see **High scores** below), **Sand hourglass** and **Sandbox** (falling sand), **Slow painting** (see below), disc exerciser, **Guest wall** (a QR code to `/guest`, where guests leave a name and message from their phone, alternating with the approved messages as scrolling text; moderate on the Guest wall page, or approve automatically), **Wi-Fi QR code** (guests scan it to join; very long names/passwords switch to a full-height inverted code) |
| **Layers** | stack apps with blend modes (On top, Add (light up), Halo, Invert where overlapping, Mask, Cut out), drawing layers you can keep editing live, position/size, scrolling, density, speed, Negative (swap a layer's lit and dark dots); saved **layer sets** (the API calls them compositions; usable in playlists) save themselves as you edit, and when the set you're editing is on the board, changes show there right away; picking a set only opens it, **Show on board** shows it; the editor follows the stack that's on the board |
| **Slideshows** | a playlist of your own images and GIFs: drop files or a .zip, which is unpacked automatically, any time; the pictures come first and **Display options** explain sizing, dithering and the brightness cutoff; while it plays, Previous / Pause / Next work and the picture on the board is highlighted; option changes apply to the playing slideshow right away; images show for 10 s by default, GIFs play through, then it moves on and loops; **GIF speed** (0.5×–4×) for the whole slideshow or per GIF, where fast GIFs at high speed skip frames the board can't flip in time; optional **random effects** give some items an effect such as glitch, wave or Game of Life, for a short burst or the whole item, with a chance, effect list and strength per slideshow; **React to music** lets the bass, beats and other parts of live music drive effects and GIF speed (see below) |
| **Playlists** | playlists of apps with durations and transitions (dissolve, wipe, slide); items that need setup (Weather without a location, Now playing without Music Assistant) are flagged and skipped; removing an item, rule, layer or slideshow image can be undone from the toast; renaming a playlist or composition updates the schedule, playlists and automations that use it, and deleting something lists what uses it first, then removes those items (automations are turned off); the **transitions** gallery (preview each one on the board) |
| **Schedule** | time-of-day/day-of-week rules choosing a playlist or "off", the default playlist, and **quiet hours** (cap on dots flipped per second + slower transitions during a time window) |
| **Automations** | when music starts/stops (Music Assistant), the weather turns rainy/snowy/clear, an event arrives, or the board has been idle N minutes → run an app or composition, play a playlist, show a notification, resume the schedule or go all black |
| **Guest wall** | messages left at `/guest`: approve, hide or delete them, or approve automatically; the count of waiting messages shows in the navigation |
| **Friend messages** | messages sent from friend links (see People & access below): approve or decline, history, show again, pin for N hours, limits |
| **Settings** | **Location & display** (theme, weather location and units, transition frame rate), **Fonts** (upload your own), **AI drawing**, **Music** (Music Assistant, live audio for the visualizer over Sendspin or Snapcast), **Smart home & API** (MQTT with optional Home Assistant discovery, webhook examples), **Controllers** (gamepads, button mapping), **Board hardware** (step through panels to check the mapping, edit column/row/ip/port/address/rotate, save to `config.local.yaml` without a restart), **Maintenance** (flips per disc as a heatmap, exercise rarely flipped discs now or daily; version, restart, updates with rollback), **Backup** (backup / restore of `storage.db`, automatic backups to a folder) |

`/play` is a phone game controller (pick any game from the grouped list; held sideways it puts the D-pad left and A/B right of the board; it reminds gamepad players that holding Start opens the game menu; D-pad + A/B with per-game labels, Start/X/Y/Z, player 1 or 2; Doom's extra buttons while Doom runs; Select / Start / Save / Load while the NES runs). USB gamepads work too; see below.

Drawing or showing something yourself pauses the schedule; **Resume schedule** (in the Now showing strip and on Now playing) goes back to it. "Draw me…" and Undo only change the board with **Draw on the board** ticked; otherwise a drawing goes onto a new drawing layer that you can add when you like.

## High scores, hourglass and slow painting

**High scores** (`apps/scores.py`): every scored game files its result in one table, `scores.json` in the data
folder (so it's in `storage.db` and the backups), a top 5 per game; Minesweeper keeps one per difficulty and
Sokoban one per level. A result that makes the top 5 asks for three initials on the board, arcade style: up/down
picks a letter, A or right moves on, B or left goes back, A on the last letter (or Start) saves; the table then
shows for a few seconds. The score is saved the moment the game ends, so walking away keeps it, unnamed ("---").
A game cut short (playlist moved on, another app started) still counts. Title screens alternate
PRESS ANY BUTTON with "HI 1234 ABC". Demo games never count, and neither do two-player games (Pong and Tron
against a second person, the versus games). Pong against the computer scores 10 a point plus 1 a return; Tron
1 a cell plus 100 a round won; Blackjack's score is the biggest stack in a game, which ends when the chips run
out or after 5 idle minutes. The older `puzzle_scores.json` is read in once.

**Demos for the classic games**: Snake, Tetris, Pong, Breakout, Space Invaders, Tron, Flappy, Platformer and
Blackjack now play themselves after 8 idle seconds on the title (the `demo` option turns it off), like the newer
packs: Snake heads for the food but never into a pocket smaller than itself, Tetris places pieces by the usual
height/holes/bumpiness weights, Breakout aims for where the ball lands, Invaders dodges bombs and picks off the
lowest aliens, Flappy aims for each gap, the Platformer runner jumps walls, pits and walkers, Blackjack plays
basic strategy from a local shoe (no API calls). Each misjudges now and then, so a demo ends. Any button takes
over with a fresh game.

**Sand hourglass** (`hourglass`, Clocks): a falling-sand hourglass that runs out at the set duration (the neck
lets grains through at grains/duration a second). Left/right (gamepad stick, phone D-pad) tilts it and the sand
leans against the low wall; A, B or up turns it over, and it then runs for as long as the sand that had fallen.
Shows the time left and an optional name beside it. When it runs out the board clatters (three bands sweep down
it), and with "Also raise a notification" a high-priority notification with the name goes out (a banner when
it's on the board itself, so you see it over a game too). It can stay empty or turn over and go again. Only
grains that moved are looked at (at most 900 a step): well under a millisecond a frame.

**Sandbox** (`sandbox`, Games): falling sand to play with on the phone controller or a gamepad. Arrows move the
cursor, hold A to pour sand / draw walls / erase (B switches tool), Start clears. Up to 1500 grains (option).
Left alone it pours from a few spouts and drains through the floor.

**Slow painting** (`slowpaint`, Generative): morphs from one picture to the next (Pictures, a slideshow, or the
daily postcards) within a flip budget a day or an hour (1000 a day by default), spread evenly over the day or
hour, so the board changes over hours or days. The next dot is chosen where the picture is most wrong (shapes
come before stray dots), away from the last few flips (the change creeps in all over) and, between near-equal
choices, on the less-worn disc (Board care's wear counts). A finished picture stays for "Keep a finished picture"
minutes. The status says how far it is ("63% of Sunset; 412 of 1000 flips today"). The picture so far and the
flips spent are kept in `slowpaint.json`, so a restart or a playlist going round neither starts over nor spends
the budget twice.

Tests: `tests/test_scores_sand_paint.py`.

## Music effects on games

Every game (the arcade and puzzle packs, Doom, NES games, the party games) has a **React to music** option in the
Apps panel: the slideshows' effects table runs over its frames (a glitch on the bass, a flash on each beat, static on
the highs) while the game itself plays exactly as usual; the music never drives the game's motion. **Settings → Music →
Music effects on games** turns the same thing on for every game that doesn't set its own option, so games started
from `/play`, the gamepad menu or a playlist get it too (`settings.games.music`: enabled, amount, fallback, fx rows;
`apps/react.py` `game_music`). Paused during quiet hours. Tests: `tests/test_games_react.py`.

## Party games (up to 4 players)

Five games in the **Party** group of the game pickers and the gamepad menu, for the GameCube pads and phones on
`/play` (now player 1-4; the phone remembers its number). They all work through a **player** link. Players join on
the board's lobby by pressing A (B leaves, Start begins; it also starts 12 s after the last join). Presses are
timestamped as they arrive (`input_now`), so reaction and rhythm timing don't depend on frames. The winner gets a
celebration of whole-board flips spaced so each one gets the sweep wave (none in quiet hours), then the usual
high-score initials.

- **Flipdot Party** (`party`): rounds of 15-40 s mini-games, 22 of them (`apps/pack_party.py`,
  `apps/party_minis.py`): mash race, reaction (an early press costs a point), memory, dodge, tug of war (teams),
  stop the clock, hot potato (passes land on the music's beat), quick count, quick sums, odd one out, what changed,
  longest bar, in order, opposite, Simon says, button combo, grid recall, shell game, sort it, moles, bank it and
  fill the glass. **Rounds** are picked in the lobby (left/right on a pad, the stepper on the phone; the Rounds
  setting is only the starting value) and the games **get harder round by round** (full difficulty by round 6):
  longer sequences, more to remember, subtler differences, decoys ("NO!" flashes, hollow moles, "SIMON SAID"),
  hidden clocks and numbers, less time. The intro shows a little difficulty meter. Rounds alternate between
  action, brain and luck games (the Games setting can keep to brain or action games) and never repeat a game until
  every one has been played. 3/2/1/0 points by place, scores between rounds. CPU players: one when you play alone
  (default), every empty seat, or none.
- **Buzzer Quiz** (`quiz`): buzz with any button; the buzzer's quarter of the board slams, then answer with A, B,
  X, Y (or the A-D buttons on the phone). Right scores 100/200/300 by difficulty, wrong costs half and locks you
  out of that question; buzzing while the question is still being read locks you out too. Claude writes the
  questions (category, difficulty, 20 at a time, cached in `quiz_cache.json` and fetched while players join) when
  Settings → AI drawing has a key; a bundled set of 90 (`apps/party_data/quiz.json`) fills in otherwise.
- **Flipdot Pictionary** (`pictionary`): the drawer gets the word and a drawing canvas on `/play`; the board
  mirrors each stroke. The others type guesses; the first right one scores 50 plus up to 50 for speed, and the
  drawer 50. Bundled words in three difficulties. With an API key Claude can guess (it's sent the drawing as a
  PNG every 14 s, 4 guesses a turn) and take a drawing turn (AI drawing, revealed a few dots at a time).
- **Rhythm** (`rhythm`): one lane per player. With live music, each bass note, snare, hat and beat is put on the
  beat grid and comes back as a note a bar later; without it there's a practice beat at a set tempo. Judged on
  what you hear: the board runs the Board delay (Settings → Music) behind the speakers, so notes are due that much
  earlier unless "The speakers are delayed to match the board" is ticked; a fine-tune offset is there too.
  Windows: perfect 45 ms, great 90, good 135; combos multiply up to x4. Scores and best combo (`rhythm:combo`)
  go in the high-score table.
- **Drum Machine** (`drummachine`): a 16-step grid on `/play` with 4-6 instruments made of flips (one dot, a
  small cluster, a panel crack, a column roll, a full sweep, a shaker), tempo, swing, built-in and saved patterns
  (`drum_patterns.json`), and "Follow the live music". Any number of phones edit the same pattern. Steps run on a
  clock, not frames (within a few ms). In quiet hours the loud instruments play as quiet ones under the flip budget.

`/api/party` (GET `?seat=N`, POST `{"action", "seat", "data"}`) and WebSocket `{"type": "party", ...}` messages
carry the phone side; both are open to player links. Tests: `tests/test_party.py` (Claude mocked).

## API (selected)

```
GET  /api/board                      current dots + what's running
GET  /api/health                     runner heartbeat, every bus, CPU temperature, throttling, disk, memory, uptime,
                                     version, and "status": "ok" | "degraded" | "failing" with the reasons
POST /api/board/refresh              resend every panel now, and retry buses that can't be reached (Retry now)
POST /api/dot     {"x":5,"y":3,"color":"white|black|toggle"}
POST /api/dots    {"dots":[...]}
POST /api/frame   {"rows":[42 strings of 84 "0"/"1"]}
POST /api/fill    {"color":"black"}        POST /api/invert
POST /api/animation {"frames":[{"rows":[...],"delay":100}], "loop":true}
POST /api/ai/draw {"prompt":"a cat","frames":1,"style":"outline only"}   → {"frames":[{"rows":[...]}]} (1–8 frames; doesn't touch the board)
GET  /api/guest                      guest wall queue; POST /api/guest/messages/<id>/approve|reject|delete, POST /api/guest/settings {"auto_approve":true}
GET  /api/access                     share links; POST /api/access {"name","role":"friend|player|viewer","expires","trusted"}, POST /api/access/<id>/revoke|rotate
GET  /api/friends                    friend messages; POST /api/friends/messages/<id>/approve|decline|show|pin {"hours"}|unpin|delete
POST /api/run     {"app":"clock","params":{...},"transition":"dissolve"}
GET  /api/scores                     the games' high-score tables: {"scores": {game: {"lower", "top": [{score, name, at}]}}}
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
Each bus (ip:port) is sent to on its own thread, so one that is unreachable doesn't hold up the others. Set `baud:` in
`config.yaml` to the converter's RS485 speed and sends are paced to it, so fast animations show the newest picture
instead of queuing up lag; `sync_refresh: true` writes every panel first and then flips them all at once, and
`auto` does that for a whole new picture only. A change to half the board or more starts on every bus at the same
moment, so the columns of panels flip together.
Panels flip their columns left to right, so on a big change a panel's right edge lands after the left edge of the
panel beside it. `sweep_ms:` (how long one panel takes to sweep) starts each column of panels that much after the
one to its left, so the change crosses the board as one wave. Moving content (video, games, transitions) would
tear across the seams that way, so with `sweep_mode: auto` (the default) only a change that stands on its own gets
the wave; a change arriving while the last wave is still crossing flips every column together. Tune it by eye with the disc exerciser running and
`POST /api/board/timing {"sweep_ms": 40, "sync_refresh": true}` (live, not saved), then put it in `config.yaml`.
`python3 tests/bench_core.py` times the per-frame paths (worth running on the Pi).
When a bus can't be reached, every page says so ("Board not reachable (192.168.11.100:5000, 5001, 5002, since
12:03)", or "6 of 18 panels not reachable" when only some are), with a Retry now button; the preview then shows what
the board would display. The warning clears by itself when the bus answers again (it's retried every 2 s), and that
bus resends all its panels, since the converter or the panels may have lost power. With `full_refresh_s:` (default
300, 0 = off) each bus also resends all its panels that often while it's idle, never during the music visualizer or
a game; resending a frame a panel already shows flips nothing, so it costs nothing against the quiet-hours budget.
The runner status (`/api/board`, the WebSocket) carries this as `link`.
Flip counts are saved to `data/wear.json` every two minutes and on shutdown.

Live updates: connect a WebSocket to `/ws`. The server pushes a binary message whenever the dots
change (`u32 version, u16 width, u16 height`, then the dots packed 8 per byte, MSB first, row-major)
and a text message `{"type":"status","runner":{...}}` when the status changes. You can send
`{"type":"input","key":"up","action":"down","player":1}` or `{"type":"dots","dots":[...]}`.
The web pages use this and fall back to polling `/api/board` if the socket drops. Sending
`{"type":"drawing_rows","drawing":ID,"rows":[...]}` replaces a drawing layer's picture (that's how live
video streams into a layer without stopping the rest of the composition), and
`{"type":"frame","rows":[...],"stream":{"id":ID,"name":NAME}}` shows a frame of a live stream on the whole board.

MQTT (Settings → Smart home & API): `flipdot/notify`, `flipdot/run`, `flipdot/command` (stop, schedule, next,
black, white, invert), `flipdot/event` (an automation event name); status is published retained on `flipdot/status`,
and `flipdot/availability` is `online`/`offline` (offline is also the MQTT Last Will). With **Home Assistant
discovery** on, the board appears in Home Assistant as a "Flipdot board" device: app and playlist selects, schedule
and quiet-hours switches, a Message text box (shows a notification), Next/Stop buttons and a "Showing" sensor.
Timers: `flipdot/timer` takes a timer command ("pasta 8m", "pause pasta", "cancel all", "pomodoro"); discovery
adds a Timer text box (point an Assist sentence like "set a {name} timer for {minutes} minutes" at it), a Timers
sensor ("Pasta until 18:42"), and Cancel timers / Start Pomodoro buttons.

Set `password:` in `config.local.yaml` (see below) to require a password (HTTP basic auth, or `?token=` for webhooks). Only the guest wall page `/guest` and its submit endpoint stay open (3 messages per address per 10 minutes, 2 KB bodies, 24-character names, 140-character messages).

### People & access: player, friend and viewer links

Settings → **People & access** makes links that open one part of the board without the password: a **friend** link
opens a page for sending messages, a **player** link opens the `/play` controller (game input, picking a game, the
on-board game menu), a **viewer** link shows the board live, read-only. Each link has a name, an optional expiry, and
can be revoked or replaced by a new one; it is shown once (with a QR code, which can also go on the board), because
only its SHA-256 is saved (`access.json`). Opening `/join/<token>` sets an HttpOnly, SameSite=Lax cookie and goes to
the role's page; scripts can send `Authorization: Bearer <token>`. Every route is owner-only unless `access.py` lists it
for a role, and `tests/test_friends.py` checks every route against every role.

**Friend messages.** A friend writes a message (up to 8 lines; 140 characters by default), sees a live preview of the
84×42 frame with the Text app's pixel fonts, sizes and letter effects, can draw a doodle on the preview, and sends it
now or for a time (a birthday morning). Friends marked "No approval needed" show right away or at their time; the
others wait under **Guests → Friend messages**, which also has the history (show again, pin for N hours, delete) and
the limits: length, how long each shows, messages per friend per period (5 an hour by default), and an optional
filter that holds messages with rude words (a short bundled list, off by default) for approval. Messages go through
the notification queue with the sender's name on top; a pinned one waits at low priority, so other notifications
still show and then it comes back. The **Message wall** app rotates the recent ones.

**Threat model.** The board is meant for your home network. Without a password anyone on the network can do
everything, so links only add something when `password:` is set. Links are bearer secrets: whoever has one can use
it until it expires or you revoke it. The server speaks plain HTTP, so on an untrusted Wi-Fi a link or the password
can be sniffed. Don't forward the port to the internet; for remote access put the board on a private network such as
[Tailscale](https://tailscale.com) (friends join your tailnet, or use Tailscale's node sharing), or behind a reverse
proxy with HTTPS. The owner password never appears in a link.
AI drawing (`aidraw.py`) calls the Anthropic Messages API; set `FLIPDOT_ANTHROPIC_BASE_URL` to point it at a test server.

## Playlists and the schedule

- **Add to playlist** is on Apps, Pictures (pictures, animations and drawing layers), Import (once the picture is saved), Slideshows, Videos, Layers (a saved set by name, an unsaved stack as it is) and Draw (a drawing layer). One dialog: pick a playlist or start a new one, set the seconds, then **Added** with a link to the playlist.
- **Playlists** can **Shuffle** (each item once per round, never the same one twice in a row), give items **random effects** and **React to music** on top of whatever they show (the same effects and table as slideshows; games being played are left alone, quiet hours pause it). Each item can have its own transition and seconds, and an **Only show it** condition: music playing or not, after sunset or in daylight, someone at the mirror camera or not, during quiet hours or not, or "it has something to show" (for departures: a departure within N minutes). Items that can't show are skipped; if none can, what's on stays.
- **Schedule rules** show a playlist, a layer set, a slideshow, a video, a picture, a drawing or any app with its options, or turn the board off. They repeat every week (chosen days), between dates every year (wrapping past New Year, e.g. 1 Dec to 1 Jan), or happen on one day (**Add one-off event**). Start and end can be a clock time or sunrise/sunset with an offset ("30 min after sunset"), worked out locally for the weather location (no network). The higher rule wins when rules overlap; the page says which rule is showing now, which ones also match, and until when, and draws what plays when for today or the next 7 days, with quiet hours and sunrise/sunset marked. Older schedules keep working (they're read in the new format). `GET /api/schedule/preview?hours=24|168` returns the same; `POST /api/playlists/add` adds one item.
- Testing: `FLIPDOT_FAKE_TIME=2026-10-10T18:25 python3 server.py --dry-run ...` runs the schedule's clock from that time (everything else uses the real time).

## Notifications: queue, priorities, do not disturb, ntfy

A new notification no longer replaces the one on the board. It waits its turn unless it has a higher priority (`notifications.py`).

- **Priorities.** `low`, `normal`, `high` and `critical` (ntfy's 1–5 also work). Critical shows at once, even over a game or another critical one. High shows over a game as a banner along the bottom and interrupts a live browser stream. Low and normal wait until the game or stream ends. If you turn on Settings → Notifications → *Banner over games*, they show as the banner too. A notification taken off the board by a more urgent one comes back afterwards with the time it had left.
- **Options** (in `/api/notify`, MQTT `<prefix>/notify` JSON and automations): `duration`, `repeat`, `hold` (stays until dismissed: here, or any controller button), `sound` (`none`/`tick`/`clatter`/`sweep`, discs flipped for the click before it shows), `icon` (`bell`, `mail`, `door`, `alert`, `timer`, `music`, `weather`, `calendar`, `person`; text with an icon shows as an icon card), `title`, and `key` (replaces the notification with the same key instead of adding another). Identical messages within 60 s are shown once.
- **Do not disturb.** By default this follows quiet hours. It can also have its own hours, be off, or be switched on by hand (there is a Home Assistant switch for it). During it, low and normal notifications are held and then shown as one summary card ("3 notifications"), or dropped if you choose that. High ones show without the sound pattern. Critical ones always show.
- **ntfy.** Subscribe to one or more topics on ntfy.sh or your own server (Settings → Notifications). Then `curl -d "Washer done" ntfy.sh/your-topic` reaches the board. Tags such as `door`, `warning` or `calendar` pick the icon.
- **Now playing** lists what's on the board, what's waiting (dismiss or clear all) and the last 20 notifications (show again), and has a test button. The API is `GET/POST /api/notifications` with `{"action": "dismiss"|"clear"|"reshow"|"test"}`. The MQTT command `dismiss` or `clear` works too. Home Assistant discovery adds a notify entity, a do-not-disturb switch, a "Notifications waiting" sensor and a Dismiss button.

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
(`POST /api/storage/restore?confirm=1`, raw `.db` bytes, streamed to disk so a backup full of videos doesn't
need to fit in memory); it's checked, then replaces all saved data when the server next restarts (the replaced
database is kept as `storage.db.before-restore`), and the page offers to restart right away. Backups are made
with SQLite's online backup in 1 MB steps, so saving carries on while a big one is written. Every backup also
holds `config.local.yaml` (panel mapping, password), which a restore puts back (the replaced one is kept as
`config.local.yaml.before-restore`).

### Automatic backups

Settings → *Backup* → *Automatic backups*: off, every day or every week at a set time, into a folder
(`../backups` by default, next to `storage.db`; a USB stick or a network share keeps them off the SD card).
The newest N are kept (7 by default) and older `flipdot-backup-*.db` files are deleted; nothing else in the
folder is touched. A backup is refused when the folder lacks room for it plus 50 MB. A time missed while the
server was off is caught up once, a couple of minutes after it starts. The list shows each backup with
Download, Restore and Delete; the last result also shows in `/api/health` (`backup`).
API: `GET /api/backups`, `POST /api/backups` (back up now), `POST /api/backups/settings`
`{"schedule": "daily", "time": "03:00", "weekday": 0, "folder": "/mnt/usb/flipdot", "keep": 14}`,
`GET /api/backups/<name>`, `POST /api/backups/<name>/delete`, `POST /api/storage/restore?confirm=1&name=<name>`.

## This board's settings: `config.local.yaml`

`config.yaml` is tracked by git and holds the defaults. `config.local.yaml` next to it (git-ignored) overrides it
key by key (a key there replaces the same key in `config.yaml`; `panels:` is replaced as a whole). Everything the
web page writes, such as the panel mapping from Settings → *Board hardware*, goes into `config.local.yaml`, so
`git pull` never conflicts and the password never ends up in a commit. Put `password:`, `panels:` and timing
changes there too:

```yaml
password: "s3cret"
sweep_ms: 60
```

The first time the server starts with this version, it creates `config.local.yaml` and copies into it every
setting whose value in `config.yaml` differs from the committed one (`git show HEAD:config.yaml`), so nothing
you changed by hand stops working. After that, `git checkout config.yaml` (or *Put config.yaml back* under
Settings → *Maintenance* → *Updates*) is safe. With `--config other.yaml` the local file is `other.local.yaml`.

## Version, restart and updates

Settings → *Maintenance* shows the version (`git describe`, the commit and its date) and how long the server has
been running. **Restart the server** (`POST /api/restart`): under systemd it exits cleanly and systemd starts it
again (the unit needs `Restart=always`, as below); run by hand, it runs its shutdown steps and starts itself
again in place with the same command line.

**Check for updates** runs `git fetch` and lists the incoming commits. **Update and restart**:

1. refuses if tracked files have local changes (and lists them), or if this checkout has commits of its own that
   the remote doesn't (it never forces, rebases or resets those away);
2. writes a backup (tagged `before-update`) to the backups folder;
3. `git pull --ff-only`;
4. reinstalls a `requirements*.txt` that changed, but only into the venv the server runs in, and only the file that
   venv was set up from (the installed versions match its old pins);
5. checks that the new version imports (`python -c "import server, apps"`);
6. restarts.

If step 3, 4 or 5 fails, the checkout goes back to the commit it was on (`git reset --keep`) and the page says
why. The update source is the branch the checkout tracks; set another remote (a name, URL or folder) and branch
under *Update source*. API: `GET /api/system`, `POST /api/update/check`, `POST /api/update` (then poll
`GET /api/update`), `POST /api/update/source`, `POST /api/update/reset-config`.

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
- **Sensitivity** (default 1): how big quieter sounds look. 2 draws quiet sounds nearly as big as loud
  ones; 0.5 lets only the loudest parts reach far.
- **Dynamics** (0–1, default 0.3): how much a quiet passage looks quieter. 0 lets every part of a song
  fill the display; 1 keeps a breakdown or quiet intro small. Levels adjust to each song either way.
- A part that stays loud without new notes (a constant sub-bass) sits a little lower, so kicks, bass notes
  and hats still pump on top of it instead of everything staying pinned at the top.
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
  the old picture turns and grows past you while the new one turns in from small. *Morph* is offered too
  (and is one of Random's picks): the same shape tween as the board-wide morph, recomputed for both moving
  styles every frame (about 6 ms on a Pi 5; on a slower machine it updates one side a frame to keep the
  visualizer's timing steady). Migrate, scatter, life, ghost, flash, glitch and zoom-in/out aren't offered:
  they're built from a single frame or flicker. **Length** (default 1.5 s) is rounded to whole beats when the beat is steady.
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
(loudness, 16 spectrum bands and beats, pure Python, 50 frames a second). snapclient handles the
codecs (flac by default; pcm, ogg and opus work too), clock sync and reconnecting; the link restarts it
if it exits. Server *auto* means the host in the Music Assistant URL; ports 1704 (audio) and 1705
(control, used once to name the player). Settings shows whether it's connected, receiving audio,
the codec and the server buffer.

**Keeping the board in step with the speakers.** The board shows the music a fixed time after the
speakers play it: **Board delay** in Settings → Music (150 ms by default; changes apply at once, without
reconnecting). Each analysis frame is stamped with when its audio plays (a sample clock anchored to when
snapclient hands the audio over) and comes due exactly that delay later; the visualizer then draws 25
times a second, each time just as a frame comes due, and while it runs the board sends every picture the
same way at a fixed time after it was started (no sweep wave; a bus with any change gets all its panels in
the same order). So every beat reaches the discs the same time behind the speakers, give or take the
40 ms between pictures, and you can delay the other speakers in the group (their latency in Snapcast /
Music Assistant) to match. Set the delay so the board keeps up on the Pi, then tune the speakers' latency
by ear; for the tightest timing turn on `sync_refresh: true` in `config.yaml`.

**Measuring the exact delay: the Sync test app** (Apps → Utilities). Download the click track from
`/api/sync/click.wav` (a 10 ms 2 kHz click every 1.013 s for 2 minutes; `?period=` and `?seconds=` change it),
play it in Music Assistant to a group with the board and a speaker, and run Sync test: every click flips
every disc. Film the speaker and the board together in slow motion and count the frames from a click to
the flip (in the middle of a panel: panels flip left to right). That's everything, panels included; delay
the other speakers by it. Clicks land at different points between the board's 25 pictures a second, so
average a few. It reads each click from the raw loudness and drives the board exactly like the visualizer
(same delay, draw timing and steady timing); the visualizer's bars also take about 25 ms to rise.

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
  drum, low notes) → glitch, **mid** → wave (it sways further in groovy, syncopated music), **high**
  (hi-hats) → static, **beat** → an invert (held just long enough for the discs to show it whole, then
  back; or **Invert sweep**: an inverted band crosses the picture left to right at the speed the panels'
  own wave would, while the GIF keeps playing under it; its amount is the band's width, 100% = the whole
  picture inverted at the peak),
  **loudness** → tones all of them down in quiet parts. Life and Melt aren't offered: they build up over
  time and don't suit a strength that changes every frame. Several effects can run at once; they're
  applied in a fixed order (pixelate, mirror, kaleidoscope, wave, vortex, glitch, outline, scanlines, static,
  flash, invert sweep, invert), after the item's random effect and the transition between items. **Vortex**
  is a whirlpool: the middle of the picture turns while the edges stay put, as far as its band says (the bass,
  say: louder, more twist), swaying one way then the other once a cycle (locked to two beats by default: in on
  one beat, out on the next). It's also a random slideshow effect and a transition (the old picture spins down
  the plughole in the middle while the new one unwinds out of it).
- **Follows** (bass, low-mid, mid, high rows): **level** (how loud that part is), **notes and hits** (each
  new bass note, snare, clap or hi-hat, found per band from the rise in its spectrum, so a moving bassline
  under a steady loud bass, ghost notes and off-beat hats all register), or **level and hits** (some of
  the level with the hits on top). Defaults: bass and mid follow both, high follows hits, low-mid the
  level. Rows saved before this get these defaults.
- **Amount and speed are separate.** Amount is how much an effect does (how far the wave bends, how many
  dots of static); speed is how fast it moves (how often it waves). Effects that move (wave, scanlines,
  flash, glitch, static) each run on their own clock, and **Speed follows** picks its rate: **Fixed**
  (0.25×–4×), **Its signal** (quiet parts slow, busy parts fast: wave on High waves faster when the hi-hats
  are busy), **Tempo** (scaled by the song's BPM against 120, so slow songs move slowly; optionally locked to
  one cycle per 1, 2 or 4 beats, so wave crests and scanline sweeps land on the beat), or **Tempo and
  signal**. Defaults: wave follows the tempo, locked to 1 cycle per 2 beats (today's speed at 120 BPM);
  scanlines and flash are locked to 1 cycle per beat; glitch and static follow their signal. Slideshows
  saved before this get these defaults. Changing the rate never makes the motion jump.
- The tempo comes from the autocorrelation of an onset envelope (all bands' spectral flux, about the last
  8 s), not from the gaps between beats, so syncopated kicks, missing downbeats and swing don't throw it.
  It covers 60–180 BPM; between half and double time it prefers 90–130 (groove music). The beat phase is
  found by folding the recent onsets onto the beat and followed smoothly; once the tempo is sure, beats
  come from that grid. Four-on-the-floor locks on the kick; heavily syncopated funk may lock on the "and"
  (still on the eighth-note grid). The tempo shows next to the status line ("≈ 96 BPM", or "tempo
  unsure"); the generated beat is 120 BPM. The same fold gives `groove` (the share of onsets off the beat)
  and `swing` (1 = straight, 2 = triplet swing).
- **Loudness drives GIF speed** (on by default): quiet parts play at 0.75×, loud parts up to 2×, on top of
  the GIF speed, eased so there are no jumps. **Skip a frame on each beat** jumps GIFs one frame forward.
- Levels adjust to each song: per band a ceiling near its loud end and a floor near its quiet end, drifting
  over about 10 s, so quiet and loud songs both use the whole range, and a steady loud bass reads high (its
  movement comes from the hits) instead of sinking to 0. A new song (track change, or sound after a
  gap) and a jump in loudness (intro → drop) re-adjust within about 2 s; a drop in loudness (a breakdown)
  adjusts slowly, so it still reads quieter for a while. Without live audio the slideshow plays normally, or reacts to a generated 120 BPM beat if
  you choose. Reacting pauses during quiet hours. It works the same when the slideshow is a layer.
- The section shows whether audio is arriving and small live meters (`GET /api/audio/levels`).

The audio reading lives in `audio_signal.py`, shared with the visualizer; it costs about 0.06 ms per
reading on a PC (`tests/bench_core.py`). `tests/test_audio_react.py` covers the band mapping, levels, the
display reading, beats, tempo from beat trains (and none from random onsets), the effects' motion clocks
and the variable-rate GIF clock. `tests/test_groove.py` runs synthetic songs (`tests/music_signals.py`:
funk at 100 BPM with swung 16ths, disco at 120, a jazz trio at −25 dB, brickwalled EDM at −3 dB, an
intro into a drop, a breakdown, song changes) through the real Sendspin/Snapcast analyser and checks tempo,
beat phase, swing, bass hits against the bassline, off-beat hats, range use and adaptation time.

## Generative apps that react to music

Every Generative app (and Random effects, which passes it on to the effect it runs) has **React to music**
in its options (off by default). It drives the app's own motion from the same live signal slideshows use,
so the effect dances rather than getting a filter on top:

| App | With music |
|---|---|
| Fire | flames burn lower in quiet parts, taller with the bass; each bass note flares them up |
| Starfield | the kick surges you forward (stars streak), louder passages fly faster |
| Fireworks | rockets go up on the beat (bass notes too while no beat comes), rockets near the top burst on it; louder music, bigger bursts |
| Matrix rain | the highs speed the streams; hi-hats start new ones |
| Plasma | the bass pushes the rings outwards; the mids roll the bands (palette shift) |
| Metaballs | the bass swells the blobs; the mids stir them faster |
| Ripples | drops fall on the hits: big ones on bass notes, smaller on snares, tiny on hats |
| Game of Life | generations on the tempo, landing on the beat; a burst of new cells on each beat |
| Spiral, Lissajous, Tunnel | rotation / one ring per beat locked to the tempo; bass kicks or squashes them, the mids widen the arms / morph the Lissajous ratio |
| Bouncing balls | bass notes kick every ball upwards |
| Maze | carves faster with the tempo, a spurt on each beat; the head blinks on the beat |

- **Music amount** (0–200 %) scales all of it; 0 % changes nothing. **Speed follows** (motion only): Fixed, The music
  (louder is faster), or Tempo (in time with the beat: periodic motion is locked to the beat phase). Speeds
  ease and every clock carries on from where it is, so nothing jumps.
- **Without live music**: do nothing (default) or use the generated 120 BPM beat. The switch shows the
  status ("Listening: Snapcast · ≈ 104 BPM", or "No live audio — Settings → Music").
- **Reacts with**: **Effects only** (the default for new setups): the app moves exactly as it does without
  music, so slow songs never make it jumpy, and the music only drives the slideshows' effects table
  (bass → glitch, …) over its frames. **Motion only**: beats and volume drive the movement (the table above).
  **Effects and motion**: both. Apps set up before this choice existed keep what they did (motion, plus the
  effects if "Effects on top" was ticked). The table is the same component as on the Slideshows page
  (`static/js/musicreact.js`).
- It works standalone, as a layer and in playlists; several reactive layers share one cached reading, and a
  small sampler thread reads the signal 25 times a second while any app listens, so slow apps still get
  steady beats and tempo. Reacting pauses in quiet hours.
- With React to music off, every app draws exactly what it drew before (checked frame by frame).

Adding it to an app: decorate the class with `@reactive` (`apps/react.py`) and read `self.music`
(`level(band)`, `hits(band)`, `both(band)`, `beat`, `hit(band)`, `pulse`, `speed`, `presence`,
`clock(key, now, rate, per_beat)`, `ticks(...)`). Cost on a PC: under 0.3 ms per frame extra; changed dots
per frame rise about 1.5–3× (spiral, fireworks, bounce, rain most) while reacting. Tests:
`tests/test_generative_react.py`.

## Live-data boards: departures, planes, weather sky

Three widgets in `apps/pack_live.py`. Each caches what it fetches, sends a User-Agent, waits longer after each
failed request (up to 15 min) and keeps the last good data on the board while offline.

- **Next departures** (`departures`): a station sign with route, destination and minutes ("due" under a minute,
  "now" at the stop, the clock time beyond an hour). Set your stop IDs plus the agency's GTFS schedule zip and/or
  GTFS-realtime TripUpdates URL (with an optional API-key header). The schedule zip is downloaded to
  `data/cache/live/`, refreshed weekly, and cut down to the chosen stops (a parent station includes its
  platforms), so the Pi only keeps a small index. Realtime predictions correct the schedule (delays carry on
  down the line, cancelled trips and skipped stops drop out); if the realtime feed fails, the header says
  SCHEDULED and the timetable carries on. Agencies without GTFS: choose "JSON URL" and give the paths to the
  list, route, destination and time (ISO time, Unix time or minutes from now). "Walk time" hides what you
  can't catch and inverts the row when it's time to leave. Characters change with a split-flap transition
  when one is installed (or any character transition you pick). The protobuf decoder (`gtfs_rt.py`) and the
  GTFS index (`gtfs_static.py`) need no extra packages.
- **Planes overhead** (`planes`): aircraft near the Settings location on a radar (heading lines, the closest one
  blinking, with its callsign, route, altitude, speed and distance) or a map with plane icons. For the most
  accurate centre, type your exact coordinates (`41.1234, -73.4567`, as a maps app copies them) into the app's
  Location field or the Settings location search: coordinates are used as they are, with no place lookup, so the
  radar is centred on your house rather than the town. Positions move on
  between updates from each plane's speed and track. Sources: your own dump1090/readsb/tar1090 `aircraft.json`
  (best), adsb.lol, or the OpenSky Network anonymously (at most every 15 s, and the app stops before the 400
  requests a day anonymous users get, and waits when OpenSky asks it to). Routes come from adsbdb.com, cached
  for 12 h; they are what a callsign usually flies, so treat them as a hint.
- **Live weather sky** (`weathersky`): clouds drift at the real wind speed and direction (the board is taken to
  face south; change it under "The board looks"), cloud cover sets how much of the sky is cloud, rain or snow
  falls as hard as Open-Meteo says (mm/h), fog and lightning show when reported, and the sun or moon (with its
  phase) sits where it really is. It shares the weather app's request and cache, and keeps the last weather on
  disk for restarts while offline.

Tests: `tests/test_live.py` (builds its own GTFS zip and a TripUpdates feed with a tiny protobuf encoder; plane
and weather responses are recorded fixtures, nothing touches the network).

## Daily channels, postcard, launches, counter, ISS

Five widgets in `apps/pack_daily.py`. Network results are kept in `data/cache/` (only the fields the app
uses), so a restart doesn't ask the APIs again; offline, each app shows its last copy or a quiet "no
connection" card.

- **Daily channels** (`daily`), one app with a Channel setting. Each channel picks something new each day.
  Long text goes over pages (or rolls up line by line). Large type is used when it fits on two pages.
  - **Quote of the day:** a built-in list of public-domain quotes, or ZenQuotes.io's quote of the day; when
    ZenQuotes can't be reached, the built-in list is used.
  - **Word of the day:** a built-in list with short definitions.
  - **On this day:** events from Wikipedia's on-this-day feed (text CC BY-SA), cached per day.
  - **Oblique Strategies:** the built-in deck is original prompts in the same spirit. Eno and Schmidt's cards
    are copyrighted, so they aren't included; to use your own deck, put one card per line in
    `data/oblique_strategies.txt`.
  - **Literature clock:** each minute, a sentence from a book that mentions that time, with the time shown
    inverted. The quotes (665, covering 333 of the 1,440 minutes) come from 87 public-domain books on
    Project Gutenberg, all published before 1930 by authors and translators who died more than 70 years
    ago; `scripts/build_litclock.py` rebuilds the list. Minutes with no quote show the time in words.
- **Daily postcard** (`postcard`): at the time you set (07:00 by default), Claude writes a haiku about the
  day (the date, the season and, if a location is set, the weather) and draws an 84 × 42 picture for it.
  It uses the key and model from Settings → AI drawing. The app shows the picture, then types out the
  haiku. Each postcard is saved to a "Postcards" slideshow (the picture and a haiku card) and/or to
  Pictures (a two-frame picture). Once the app has been used, a background job makes the postcard every
  morning even when the app isn't on the board; turn that off with "Make it every morning". After a
  failure it tries again 30 minutes later. `POST /api/postcard` makes today's postcard now. Without an API
  key, the app shows what to set up.
- **Launch countdown** (`launches`): the next launches from Launch Library 2 (The Space Devs), showing the
  provider, rocket, mission/pad and a T-minus countdown. Dates that aren't fixed yet show as "NET October".
  The last 10 seconds take the whole board as one big digit, followed by a short liftoff. The free tier
  allows 15 calls an hour; the app asks at most every 15 minutes, and a restart reuses the file copy.
  Settings: how many launches to cycle through, a provider/rocket filter, and the development server
  (unlimited calls, but its data may be stale).
- **Counter** (`counter`): one big number whose digits roll through the numbers in between when the value
  changes, like a mechanical counter. Options: a label, a goal bar with a percentage, decimals and
  thousands separators. Sources:
  - The API: `POST /api/counter/<name>` with `{"value": n}` or `{"add": n}` (or `?value=`/`?add=`), and
    `GET` to read it. Values are kept in `counters.json`.
  - A JSON URL plus a path, read like the JSON widget.
  - An MQTT topic on the broker from Settings → MQTT, for example a Home Assistant `mqtt_statestream`
    entity. The payload can be a number, JSON with a path, or `{"state": …}`.
- **ISS tracker** (`iss`): a 1-bit world map (Natural Earth, public domain), the station's position from
  wheretheiss.at and its ground track 45 minutes back and 50 minutes ahead, alternating with details:
  altitude, speed and the next pass over the Settings location. Pass times come from the station's orbital
  elements (fetched every 6 hours), worked out on the board to within about a minute, along with the
  highest elevation, the directions and whether the pass is visible. For the 10 minutes before a pass and
  during it, the app shows where to look instead.

Tests: `tests/test_daily.py` (recorded API responses in `tests/fixtures/daily/`; the Anthropic API is mocked).

## Silhouette mirror

Create → Mirror shows whoever stands in front of a camera as dots (app `mirror`, also usable in playlists and
layers). Camera pictures stay in the house: they're shrunk to 84 × 42 gray dots in the board's memory and never
saved or sent anywhere.

- **Sources:** this page's camera (a phone or laptop; front or rear camera; frames go over the WebSocket, so the
  page needs to stay open, and browsers allow cameras only on https or localhost), a USB webcam on the Pi
  (`/dev/videoN`, read with `ffmpeg`), a Pi camera module (`rpicam-vid`), or a looping video file for testing.
  The page lists what it finds. With no camera, the board shows a "start the camera" screen.
- **Finding you:** difference from the empty room ("Capture empty room"; the background also follows slow light
  changes), darker than the surroundings, or edges. The result is cleaned up and given per-dot hysteresis so
  sensor noise doesn't flip discs.
- **Modes:** solid, outline, fading trails, Game of Life seeded by your outline, a dithered pixel mirror; plus
  mirrored (selfie) and negative.
- **Take over when someone walks up:** after someone has been in view for a moment the mirror takes over the board
  as a notification, and whatever was on comes back after N seconds with nobody there. It never interrupts a game,
  a live stream or another notification, and stays off during quiet hours unless allowed. Automations also get a
  "Someone walks up / has left" trigger.

API: `GET|POST /api/mirror` (status and settings), `POST /api/mirror/background`, `GET /api/mirror/preview`,
`POST /api/mirror/frame` (HTTP fallback for the WebSocket message `{"type": "camera", "w", "h", "data"}`, with
`data` as base64 gray bytes). Tests: `tests/test_mirror.py` (synthetic walking figure over a noisy room).

## Apps where the click is the point

`apps/pack_ambient.py`. Each sets its flip rate on purpose (that's the volume) and goes quiet in quiet hours. All
work on their own, in playlists and as layers.

- **Percussion** (`percussion`): the board plays along with the music. A few dots flip on each hi-hat, a block on
  each snare and beat (sized by how loud that part is), and a fast sweep on every Nth beat (a downbeat when the
  link reports one); off-beat ticks when the groove swings. Plays round the border, in the corners or on the
  whole board. It's timed like the music visualizer (the same Board delay, a draw as each analysis frame comes
  due, steady timing), and a layer stack with it in keeps steady timing, so it fits as an Invert layer over a
  clock or text. Measured with the click track on the real link → runner → board path and fake RS485 buses:
  every click flipped, 189 ms behind the click (the sync test: 185 ms), the same ±11 ms spread as the sync
  test (the 40 ms picture grid), no flips between clicks.
- **Split-flap board** (`splitflap`): text on a departures board. Each character flaps forward through the
  alphabet to its new letter, staggered along the line. Pages are separated by a blank line; `{time}`, `{date}`
  and `{day}` fill themselves in. Quiet hours: one flap per change. "split-flap" is also a **character
  transition** (Digital clock, Countdown, Text: seven-segment digits go round a digit drum) and a whole-board
  transition for playlists.
- **Mechanical clock** (`mechclock`): one disc runs round the border once a minute, a step a second. On the hour
  it strikes the hour: each strike inverts the board as a sweep wave (the seconds stand still meanwhile so every
  strike gets its own wave), with a quick un-invert after an odd count. No strikes in quiet hours.
- **Rain on the roof** (`asmrrain`): single discs (sometimes two) flip at random times, Poisson-spaced like real
  rain, and flip back after a few seconds. The volume is drops a second; quiet hours cap it (and never above a
  sixth of the quiet-hours flip budget). "When it rains outside" follows Open-Meteo's current rain rate for the
  Settings location (drizzle a few drops, 8 mm/h and up the full volume); dry, it shows nothing as a layer.
- **Pendulum wave** (`pendulums`): 84 (or 42, 28, 21) pendulums, each swinging once more per cycle than the
  last. They drift through waves and stripes, line up again after the cycle and rest, still and silent, before
  the next round. React to music sets the pace and swing.
- **Mushroom garden** (`mushrooms`): fungi of all shapes sprout, grow, drop spores and wither on a patch of
  ground, and not just the dome on a stem. Twelve species, each drawn its own way: toadstools with spots and a
  skirt, honeycomb-pitted morels with a hollow stem, trumpet-shaped chanterelles with ridges running down into the
  stem, shaggy ink caps that melt away from the rim and drip, enoki clusters of thin stems with tiny caps, puffballs
  that burst into a cloud of spores (three times, then sag), bracket shelves with growth rings on a stump, branching
  coral fungus, a lion's mane with dangling spines, oyster caps shingled up a short stem, little glowing ones with a
  twinkling halo, and the plain button mushroom. Overlapping mushrooms keep a dark outline between them. Mycelium
  threads spread under the ground line and new mushrooms like to come up where they reach. Options: which kinds
  (a mix, "the odd ones" without button mushrooms, or one species), how many at a time, how long they take to grow,
  sizes, the ground (grass and mycelium, grass, none) and spores. It's in Random effects' pool. React to music: the
  garden grows to the music's pace, puffballs burst on the bass, caps pulse on the beat, the halos sparkle on the
  highs.
- **Timers** (`timers`): up to four named timers, or a Pomodoro (25/5 minutes, a long break every 4, sessions
  counted per day), as countdowns and shrinking bars. At zero the board clatters (four 3-flip bursts; just the
  border in quiet hours), then flashes the name for 20 s. When the Timers app isn't on the board, the alarm is
  shown over whatever is. Control them from the app's settings (start, pause, cancel, quick starts),
  `POST /api/timers` (`{"command": "pasta 8m"}` or `{"action": "start|pause|resume|toggle|cancel|dismiss|pomodoro",
  "name", "duration"}`; `GET` lists them), MQTT `flipdot/timer`, or Home Assistant (see MQTT above). Kept in
  `timers.json`.
- **On air** (`onair`): ON AIR, the meeting's title and when it ends, while an event in your iCal feeds is on
  (optionally only titles with a word like "meeting"). Between meetings it shows nothing (as a top layer, what's
  below) or the next meeting. To have it take over: Automations → "A calendar event starts" (iCal links, title
  keyword, "Until it ends") → Show an app → On air. It shows over what's on and goes away when the event ends.

Tests: `tests/test_ambient.py`, `tests/test_mushrooms.py`.

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
writes the board's dots (or small gray frames) + player stats to stdout and reads key events from stdin). It downloads
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

## Plugins and Pixlet apps

**Python plugins.** Drop a `.py` file (or a folder with `__init__.py`) into `plugins/` and press
**Settings → Plugins → Reload plugins**; its apps appear in **Apps → Plugins** and work standalone, as layers
and in playlists. They use the same `App`/`Widget`/`P`/`@register` API as the built-in apps:
`plugins/example_hello.py` is a commented example and `plugins/README.md` the reference. Settings → Plugins
also installs a plugin from an uploaded `.py`/`.zip` or a URL (a raw file, a GitHub file, repo or folder);
those go in `data/plugins/` so they're in `storage.db` and backups.

**Security:** a plugin runs inside the server with its full privileges (settings, passwords, files, network).
Only install code you trust. Installed plugins stay off until you've read the code and ticked "I trust this
code", and need that again if the code changes. Plugin management is owner-only (no share link reaches it).

A plugin that fails to import is skipped and its traceback shown; a crash while drawing shows "Plugin error";
5 crashes in a row turn the plugin off, with a low-priority notification.

**Pixlet apps** (the Tidbyt/Tronbyt community catalogue, github.com/tronbyt/apps, Apache-2.0). Run
`scripts/setup-pixlet.sh` once: it downloads Tronbyt's Pixlet v0.54.0 for arm64 (Pi 5) or x86-64, checks its
SHA-256 and installs it in `native/pixlet/build/`. Then search the catalogue in Settings → Plugins and install
apps; each becomes an app in Apps → Plugins with its own settings (from the app's schema; the location comes
from Settings → Location; OAuth and "generated" fields aren't supported) plus how it becomes dots:

- *Picture size*: 64×32 centred (default, sharpest), stretched to 84×42, drawn at 84×42 (only apps that
  adapt to the size look right) or, for 2× apps, 128×64 scaled down.
- *Colours to dots*: any lit colour (suits most apps: they draw on black), brightness threshold, or
  dithering (pictures and gradients). *Invert* helps apps that draw on white (e.g. Analog Clock).
- *Render again every*: Pixlet runs in the background (one at a time, low priority; a render takes
  ~0.05 s on a PC), and the frames loop like a GIF in between.

Clocks and text apps convert well; apps that use colour for meaning need tuning. Many catalogue apps fetch
data from the web when they render, as they do on a Tidbyt.

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

## Run at boot (systemd)

As a system service, `/etc/systemd/system/flipdot.service` (change the paths to where the app lives):

```ini
[Unit]
Description=Flipdot board server
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
WatchdogSec=30
User=pi
SupplementaryGroups=input
WorkingDirectory=/home/pi/Documents/Programming/flipdisk
ExecStart=/home/pi/Documents/Programming/flipdisk/venv/bin/python /home/pi/Documents/Programming/flipdisk/server.py --port 8080
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now flipdot
journalctl -u flipdot -f              # logs
curl -s localhost:8080/api/health     # add ?token=PASSWORD when a password is set
```

What changed from the earlier `Type=simple` unit:

- `Type=notify` and `NotifyAccess=main`: the server tells systemd when it's actually serving (`READY=1`), so
  `systemctl start` waits for that and anything ordered after it starts at the right time.
- `WatchdogSec=30`: the server pings systemd every 15 s, but only while the loop that drives the board is still
  ticking. If that loop hangs, the pings stop and systemd restarts the service within 30 s, even though the web
  page would still answer. An unreachable board doesn't stop the pings (a restart wouldn't fix a cable).
- `Restart=always` instead of `on-failure`: it also comes back after a clean exit or a watchdog kill.

Without systemd (run by hand) none of this happens: no `NOTIFY_SOCKET`, nothing is sent.
`flipdot.service` in this folder is the same thing as a user service (`~/.config/systemd/user/`):

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
- `health.py` – `/api/health` and the systemd notify/watchdog pings
- `localconfig.py` – `config.local.yaml` over `config.yaml`; `backups.py` – backups folder, schedule, rotation; `updater.py` – update from git with rollback, restart
- `transitions.py` – ~40 transitions (reveal maps like wipes/iris/clock/spiral, plus slides, pushes, zoom, melt, glitch, vortex…)
- `apps/layers.py` – the layer compositor
- `rules.py` – automations; `qr.py` – QR code encoder (byte mode, ECC L/M/Q/H, versions 1–40)
- `store.py` – JSON files in `data/`; `storage.py` – the `storage.db` they're kept in; `mqtt.py` – tiny MQTT client + Home Assistant discovery; `ws.py` – WebSocket push; `server.py` – HTTP
- `static/` – web UI
- `apps/pack_arcade.py`, `pack_maze.py`, `pack_runner.py`, `pack_versus.py`, `pack_puzzle.py` – the game packs
- `apps/pack_live.py`, `gtfs_rt.py`, `gtfs_static.py` – departures, planes overhead and the live weather sky
- `native/doom/` – Doom platform file + Makefile (`scripts/setup-doom.sh` fetches and builds the engine); `apps/doom.py` – the Doom app
- `native/nes/` – NES driver, agnes patch + Makefile (`scripts/setup-nes.sh` fetches and builds the emulator); `apps/nes.py` – the NES app
- `plugins.py`, `plugins_api.py`, `static/js/plugins.js` – Python plugins (`plugins/`, `data/plugins/`); `pixlet.py`, `apps/pixlet_apps.py`, `scripts/setup-pixlet.sh` – Pixlet apps
