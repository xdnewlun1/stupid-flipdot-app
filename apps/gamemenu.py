"""On-board game menu for gamepads: switch games without the web UI.

GamepadMenu sits between the gamepad reader and runner.input. Holding Start for
HOLD seconds opens the menu as a notification over the running game, which is
not rendered (so it is paused) while the menu is up and carries on when the menu
closes. A short Start press is passed on to the game when it is released. With
no game running, pressing Start opens the menu straight away.

Only gamepads go through here: the phone controller has its own game picker and
the keyboard/phone "start" keeps its usual meaning in games.
"""

import threading
import time

from fonts import SMALL

from .base import REGISTRY, App

HOLD = 0.8      # seconds Start must be held in a game to open the menu
IDLE = 30       # the menu closes after this many seconds without input
ROWS = 4        # list entries shown at once
STATE_FILE = "gamemenu.json"

# Same grouping and order as the phone controller's picker (static/play.html)
GROUPS = [
    ("Classics", ["snake", "tetris", "pong", "breakout", "invaders", "tron", "flappy", "platformer", "blackjack", "doom", "nes"]),
    ("Arcade", ["asteroids", "galaxian", "missile_command", "centipede", "lunar_lander"]),
    ("Maze & action", ["pacman", "frogger", "qix", "bomberman", "tanks"]),
    ("Runners & racing", ["dino", "racer", "copter", "stacker", "skyjump", "pitfall"]),
    ("Two players", ["connect4", "tictactoe", "artillery", "checkers", "battleship", "quickdraw", "air_hockey"]),
    ("Puzzles", ["game2048", "minesweeper", "sokoban", "lights_out", "simon", "columns", "mastermind"]),
    ("Party", ["party", "quiz", "pictionary", "rhythm", "drummachine"]),
]
PAUSE_KEY = {"doom": "start", "nes": "pause"}  # games that keep running on their own: toggle their pause around the menu


def groups():
    """[(name, [(id, title), ...])] for every registered game; unlisted games go under "More"."""
    games = {cid: cls for cid, cls in REGISTRY.items() if cls.category == "Games"}
    out = [(name, [(g, games[g].title) for g in ids if g in games]) for name, ids in GROUPS]
    listed = {g for _, ids in GROUPS for g in ids}
    out.append(("More", [(cid, cls.title) for cid, cls in games.items() if cid not in listed]))
    return [(name, items) for name, items in out if items]


class GameMenu(App):
    id = "gamemenu"
    title = "Game menu"
    interactive = True
    interval = 0.5

    def __init__(self, ctx, player=1, current=None, last=None, on_pick=None, on_close=None):
        super().__init__(ctx)
        self.player = player
        self.current = current  # id of the game paused underneath (None if there isn't one)
        self.on_pick, self.on_close = on_pick, on_close
        self.groups = groups()
        self.gi, self.ii = 0, 0
        for gi, (_, items) in enumerate(self.groups):
            for ii, (gid, _) in enumerate(items):
                if gid == (current or last):
                    self.gi, self.ii = gi, ii
        self.touched = time.time()
        self.done = False
        self.closed = False

    def input(self, key, action="down", player=1):
        if action != "down" or player != self.player or self.done:
            return
        self.touched = time.time()
        items = self.groups[self.gi][1]
        if key in ("up", "down"):
            self.ii += -1 if key == "up" else 1
            if self.ii < 0:  # run on into the previous/next category
                self.gi = (self.gi - 1) % len(self.groups)
                self.ii = len(self.groups[self.gi][1]) - 1
            elif self.ii >= len(items):
                self.gi, self.ii = (self.gi + 1) % len(self.groups), 0
        elif key in ("left", "right"):
            self.gi = (self.gi + (1 if key == "right" else -1)) % len(self.groups)
            self.ii = 0
        elif key == "a":
            self.done = True
            gid = items[self.ii][0]
            if gid != self.current and self.on_pick:
                self.closed = True  # the picked game replaces both the menu and the old game
                self.on_pick(gid)
        elif key in ("b", "start"):
            self.done = True

    def render(self, c, now):
        if self.done or now - self.touched > IDLE:
            return False
        name, items = self.groups[self.gi]
        # header: category name between arrows hinting left/right, ruled off from the list
        c.text_center(0, name.upper(), SMALL)
        c.text(0, 0, "<", SMALL)
        c.text(c.w - 3, 0, ">", SMALL)
        for x in range(0, c.w, 2):
            c.set(x, 6)
        top = max(0, min(self.ii - ROWS // 2, len(items) - ROWS))
        for row, (gid, title) in enumerate(items[top:top + ROWS]):
            y = 10 + row * 8
            if top + row == self.ii:  # selected: in a box
                c.rect(0, y - 2, c.w - 2, 9)
            w = c.text(3, y, title.upper(), SMALL)
            if gid == self.current:  # the paused game: a pause sign after its name
                c.rect(w + 6, y, 1, 5, fill=True)
                c.rect(w + 8, y, 1, 5, fill=True)
        if len(items) > ROWS:  # scroll bar on the right edge
            h = max(2, 33 * ROWS // len(items))
            c.rect(c.w - 1, 8 + (33 - h) * top // (len(items) - ROWS), 1, h, fill=True)

    def stop(self):
        if not self.closed:
            self.closed = True
            if self.on_close:
                self.on_close()

    def describe(self):
        return f"Game menu ({self.groups[self.gi][0]})"


class GamepadMenu:
    """Gamepad input filter: on_input(key, action, player) for gamepad.GamepadManager."""

    def __init__(self, runner, store, hold=HOLD):
        self.runner, self.store, self.hold = runner, store, hold
        self.lock = threading.RLock()
        self.pending = {}      # player -> Timer for a Start press that may become a hold
        self.swallow = set()   # (player, key) whose release must not reach anything
        self.in_game = set()   # (player, key) held down in the game
        self.menu = None

    def enabled(self):
        return self.store.settings.get("gamepad", {}).get("menu_button", "hold_start") != "off"

    def menu_open(self):
        n = self.runner.notif
        return self.menu is not None and n is not None and n["app"] is self.menu

    def input(self, key, action, player):
        with self.lock:
            if not self.enabled():
                return self.runner.input(key, action, player)
            down = action == "down"
            if not down and (player, key) in self.swallow:
                self.swallow.discard((player, key))
                return True
            if self.menu_open():
                if down:  # its release comes after the menu may have closed: keep it from the game
                    self.swallow.add((player, key))
                return self.runner.input(key, action, player)
            if key == "start":
                if down:
                    with self.runner.lock:
                        target = self.runner.notif["app"] if self.runner.notif else self.runner.app
                        playing = bool(target and target.interactive)
                    if not playing:
                        self.swallow.add((player, key))
                        self.open(player)
                        return True
                    t = self.pending.pop(player, None)
                    if t:
                        t.cancel()
                    t = self.pending[player] = threading.Timer(self.hold, self._held, (player,))
                    t.daemon = True
                    t.start()
                    return True
                t = self.pending.pop(player, None)
                if t:  # released before the hold time: an ordinary Start press for the game
                    t.cancel()
                    self.runner.input("start", "down", player)
                    return self.runner.input("start", "up", player)
            if down:
                self.in_game.add((player, key))
            else:
                self.in_game.discard((player, key))
            return self.runner.input(key, action, player)

    def _held(self, player):
        with self.lock:
            if self.pending.pop(player, None) is None or self.menu_open():
                return
            self.swallow.add((player, "start"))
            self.open(player)

    def open(self, player):
        with self.lock, self.runner.lock:
            # let go of whatever the game thinks is held, so nothing keeps moving after the menu
            for p, k in sorted(self.in_game):
                self.runner.input(k, "up", p)
            self.in_game.clear()
            game = self.runner.app if self.runner.app is not None and self.runner.app.interactive else None
            if game is not None and game.id in PAUSE_KEY:
                self._tap(game, PAUSE_KEY[game.id])
            last = self.store._load(STATE_FILE, {}).get("last")
            self.menu = GameMenu(self.runner.ctx, player=player, current=game.id if game else None, last=last,
                                 on_pick=self._pick, on_close=lambda: self._closed(game))
            self.runner.notify(app=self.menu, duration=24 * 3600, transition="none", priority="critical",
                               sound="none", source="game menu", record=False, dedupe=False)

    @staticmethod
    def _tap(app, key):
        try:
            app.input(key, "down", 0)
            app.input(key, "up", 0)
        except Exception:
            pass

    def _closed(self, game):
        # back to the game underneath (still the same instance, so its state is intact)
        if game is not None and game is self.runner.app and game.id in PAUSE_KEY:
            self._tap(game, PAUSE_KEY[game.id])

    def _pick(self, gid):
        self.store._save(STATE_FILE, {"last": gid})
        r = self.runner
        with r.lock:
            if r.notif and r.notif["app"] is self.menu:
                r.notif = None
            r.run({"app": gid, "params": {}})
