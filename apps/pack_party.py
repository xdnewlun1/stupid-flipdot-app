"""Party games for up to 4 players on the GameCube pads and phones (/play, player 1-4): Flipdot Party, a
rotation of quick mini-games with a running tally. The shared lobby, input and champion celebration
(PartyGame) are used by the quiz, Pictionary, rhythm game and drum machine too (apps/party_*.py).

Players join in the lobby by pressing A (B leaves, Start begins; it also begins on its own a few seconds
after the last join). CPU players can fill empty seats. Presses are timestamped as they arrive
(input_now, without waiting for the runner), so reaction times and rhythm timing don't depend on when
the next frame is drawn. The champion gets a celebration of whole-board flips spaced so each one stands
alone and gets the board's sweep wave (games never get it otherwise); in quiet hours it stays still.
"""

import collections
import math
import random
import time

from fonts import NORMAL, SMALL

from .base import P, register
from .games import Game
from .react import quiet_now

SEATS = (1, 2, 3, 4)
CPU_MODES = [["pair", "Add one when you play alone"], ["fill", "Fill every empty seat"], ["none", "No CPU players"]]
LOBBY_WAIT = 12     # seconds after the last join before the game starts on its own
CELEBRATE_S = 6.0   # the champion's celebration
FLASH_EVERY = 1.3   # seconds between its whole-board flips (longer than a sweep wave takes to cross the board)
STANDINGS_S = 5.0


def quadrant(w, h, seat):
    """(x, y, w, h) of a seat's quarter of the board: 1 top left, 2 top right, 3 bottom left, 4 bottom right."""
    i = seat - 1
    return (i % 2) * (w // 2), (i // 2) * (h // 2), w // 2, h // 2


def fill_rect(c, x, y, w, h, v=1):
    for yy in range(max(0, y), min(c.h, y + h)):
        i = yy * c.w
        x0, x1 = max(0, x), min(c.w, x + w)
        if x1 > x0:
            c.px[i + x0:i + x1] = bytes([v]) * (x1 - x0)


def invert_rect(c, x, y, w, h):
    for yy in range(max(0, y), min(c.h, y + h)):
        i = yy * c.w
        for xx in range(max(0, x), min(c.w, x + w)):
            c.px[i + xx] ^= 1


def box_text(c, x, y, s, font=SMALL):
    """Text on a cleared box with a 1-dot margin (reads over anything)."""
    tw = font.width(s)
    fill_rect(c, x - 1, y - 1, tw + 2, font.height + 2, 0)
    c.text(x, y, s, font)


def banner(c, s, y=None):
    """A centred inverted banner, e.g. "P2 WINS"."""
    tw = NORMAL.width(s)
    y = (c.h - 11) // 2 if y is None else y
    x = (c.w - tw) // 2
    fill_rect(c, x - 3, y, tw + 6, 11, 1)
    c.text(x, y + 2, s, NORMAL, v=0)


CROWN = ["1000100010001", "1100111011001", "1111111111111", "1111111111111", "0111111111110"]


def crown(c, x, y, v=1):
    for dy, row in enumerate(CROWN):
        for dx, ch in enumerate(row):
            if ch == "1":
                c.set(x + dx, y + dy, v)


class _Interactive:
    """`interactive` for party games: True on the class (the app catalog, /api/run), and per game False only while
    the champion's celebration plays, so its standalone whole-board flips get the sweep wave."""

    def __get__(self, obj, cls=None):
        return True if obj is None else not getattr(obj, "celebrating", False)


class PartyGame(Game):
    """Lobby (join with A), timestamped input, totals per seat, standings and the champion's celebration.
    Subclasses implement start_game(now), key(t, key, action, seat), tick(now), draw(c, now) and call
    finish(now) when the match is over; phone_state()/phone() serve the /play page (party_api.py)."""

    interactive = _Interactive()
    interval = 0.04
    min_players = 1     # humans + CPUs needed to start
    cpu_ok = True       # CPU players can take part
    phone_mode = "pad"  # what /play shows: pad, quiz, draw, drum
    cpu_names = False   # CPU seats shown as CPU instead of P<n>
    join_hint = "PRESS A TO JOIN"

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.clock = time.time  # timestamps for presses (tests swap in a fake clock)
        self.events = collections.deque()
        self.rnd = random.Random()
        self.celebrating = False
        self.celebration = None
        self.standings_at = None
        self.rev = 0  # bumped on every change a phone should see
        self.now = self.clock()
        self.lobby_reset(self.now)

    # ---- input ----------------------------------------------------------------------------

    def input_now(self, key, action="down", player=1):
        """Every press goes in the queue with its arrival time; render() works through it."""
        if self.scoreboard is not None:
            return False  # initials entry goes the usual way
        self.events.append((self.clock(), key, action, int(player)))
        return True

    def input(self, key, action="down", player=1):
        self.events.append((self.clock(), key, action, int(player)))

    def drain(self):
        while self.events:
            t, key, action, seat = self.events.popleft()
            if action == "down":
                self.held.add((seat, key))
            else:
                self.held.discard((seat, key))
            if self.state == "title":
                if action == "down":
                    self.lobby_key(t, key, seat)
            elif self.state == "playing" and not self.celebration and self.standings_at is None:
                if seat in self.seats or key == "start":
                    self.key(t, key, action, seat)

    # ---- lobby ------------------------------------------------------------------------------

    def lobby_reset(self, now):
        self.joined = []
        self.cpus = []
        self.seats = []
        self.totals = {}
        self.last_join = None
        self.lobby_at = now
        self.celebrating = False
        self.celebration = None
        self.standings_at = None
        self.held = set()
        self.rev += 1

    def cpu_count(self, humans):
        if not self.cpu_ok:
            return 0
        mode = self.p.get("cpu", "pair")
        free = len(SEATS) - len(humans)
        return free if mode == "fill" else max(0, 2 - len(humans)) if mode == "pair" else 0

    def can_start(self):
        return len(self.joined) >= 1 and len(self.joined) + self.cpu_count(self.joined) >= self.min_players

    def lobby_key(self, t, key, seat):
        if seat not in SEATS:
            return
        if key == "a" and seat not in self.joined:
            self.joined.append(seat)
            self.joined.sort()
            self.last_join = t
            self.rev += 1
        elif key == "b" and seat in self.joined:
            self.joined.remove(seat)
            self.last_join = t if self.joined else None
            self.rev += 1
        elif key == "start" and self.can_start():
            self.begin(t)

    def join(self, seat):
        """A phone's Join button: the same as pressing A in the lobby."""
        if self.state == "title":
            self.lobby_key(self.clock(), "a", seat)

    def begin(self, now):
        humans = sorted(self.joined)
        free = [s for s in SEATS if s not in humans]
        self.cpus = free[:self.cpu_count(humans)]
        self.seats = sorted(humans + self.cpus)
        self.totals = {s: 0 for s in self.seats}
        self.score = 0
        self.state = "playing"
        self.rev += 1
        self.start_game(now)

    def lobby_tick(self, now):
        if self.last_join is not None and self.can_start() and now - self.last_join >= LOBBY_WAIT:
            self.begin(now)

    def name(self, seat):
        if seat == 0:
            return "AI"
        return ("CPU" if self.cpu_names else "C") + str(seat) if seat in self.cpus else f"P{seat}"

    def is_cpu(self, seat):
        return seat in self.cpus

    def draw_lobby(self, c, now):
        title = self.title.upper()
        font = NORMAL if NORMAL.width(title) <= c.w else SMALL
        c.text_center(1, title, font)
        cw = c.w // 4
        for i, seat in enumerate(SEATS):
            x = i * cw
            c.text(x + (cw - SMALL.width(f"P{seat}")) // 2, 11, f"P{seat}", SMALL)
            bx, by, bw, bh = x + 3, 18, cw - 6, 11
            if seat in self.joined:
                fill_rect(c, bx, by, bw, bh, 1)
                c.text(bx + (bw - SMALL.width("IN")) // 2, by + 3, "IN", SMALL, v=0)
            else:
                c.rect(bx, by, bw, bh)
                if int(now * 2) % 2:
                    c.text(bx + (bw - SMALL.width("A")) // 2, by + 3, "A", SMALL)
        if self.last_join is not None and self.can_start():
            left = max(0, math.ceil(LOBBY_WAIT - (now - self.last_join)))
            c.text_center(35, f"START IN {left}", SMALL)
        else:
            phase = int(now * 2) % 4
            hi = self.hi_text() if phase == 3 else ""
            c.text_center(35, hi or self.join_hint, SMALL)

    # ---- frames -----------------------------------------------------------------------------

    def render(self, c, now):
        self.now = now
        self.drain()
        if self.state == "title":
            self.lobby_tick(now)
            if self.state == "title":
                return self.draw_lobby(c, now)
        if self.state == "over":
            # the score was filed (initials entry shows first if it made the top 5): back to the lobby
            self.lobby_reset(now)
            self.state = "title"
            return self.draw_lobby(c, now)
        if self.celebration:
            return self.draw_celebration(c, now)
        if self.standings_at is not None:
            if now - self.standings_at >= STANDINGS_S:
                self.start_celebration(now)
                return self.draw_celebration(c, now)
            return self.draw_standings(c, now, final=True)
        self.tick(now)
        if self.celebration or self.standings_at is not None:
            return self.render(c, now)
        self.draw(c, now)

    def scoreboard_done(self, now):
        super().scoreboard_done(now)
        self.lobby_reset(now)

    # ---- the end of a match -----------------------------------------------------------------

    def ranking(self):
        """Seats best first."""
        return sorted(self.seats, key=lambda s: (-self.totals.get(s, 0), s))

    def champions(self):
        if not self.seats:
            return []
        top = max(self.totals.get(s, 0) for s in self.seats)
        return [s for s in self.seats if self.totals.get(s, 0) == top]

    def finish(self, now, standings=True):
        """The match is over: final standings, then the champion's celebration, then the score is filed."""
        humans = [self.totals.get(s, 0) for s in self.seats if s not in self.cpus]
        self.score = max(humans) if humans else 0
        self.rev += 1
        if standings:
            self.standings_at = now
        else:
            self.start_celebration(now)

    def start_celebration(self, now):
        self.standings_at = None
        self.celebration = {"at": now, "champions": self.champions(), "quiet": self._quiet()}
        self.celebrating = True
        self.rev += 1

    def _quiet(self):
        try:
            return quiet_now(self.ctx.store)
        except Exception:
            return False

    def draw_celebration(self, c, now):
        cel = self.celebration
        age = now - cel["at"]
        if age >= CELEBRATE_S:
            self.celebration = None
            self.celebrating = False
            self.state = "over"  # files the score (Game hooks)
            self.rev += 1
            return self.draw_standings(c, now, final=True)
        champs = cel["champions"]
        crown(c, (c.w - len(CROWN[0])) // 2, 1)
        if len(champs) == 1:
            who = self.name(champs[0])
            c.text_center(8, who, NORMAL, scale=2)
            c.text_center(25, "IS THE CHAMPION" if len(who) < 4 else "CHAMPION", SMALL)
        else:
            c.text_center(9, " ".join(self.name(s) for s in champs), NORMAL)
            c.text_center(20, "SHARE THE CROWN", SMALL)
        pts = self.totals.get(champs[0], 0) if champs else 0
        c.text_center(33, f"{pts} PTS", SMALL)
        # a whole-board flip every FLASH_EVERY s: each stands alone, so the board gives it the sweep wave
        if not cel["quiet"] and int(age / FLASH_EVERY) % 2 == 1:
            c.invert()

    def draw_standings(self, c, now, final=False):
        c.text_center(0, "FINAL SCORES" if final else "SCORES", SMALL)
        rows = self.ranking()
        top = max([self.totals.get(s, 0) for s in rows] + [1])
        pitch = 9 if len(rows) <= 4 else 7
        for i, s in enumerate(rows[:4]):
            y = 8 + i * pitch
            pts = self.totals.get(s, 0)
            c.text(1, y + 1, self.name(s), SMALL)
            bw = int((c.w - 42) * max(0, pts) / top)  # room on the right for the number and a "+n"
            fill_rect(c, 15, y, max(1 if pts > 0 else 0, bw), 7)
            c.text(15 + bw + 2, y + 1, str(pts), SMALL)

    # ---- the /play page ----------------------------------------------------------------------

    def phone_state(self, seat):
        """What the /play page shows for this seat (JSON)."""
        return {"app": self.id, "mode": self.phone_mode, "state": self.state, "rev": self.rev,
                "seat": seat, "joined": seat in self.joined, "seats": self.seats or self.joined,
                "names": {s: self.name(s) for s in (self.seats or self.joined)},
                "totals": {s: self.totals.get(s, 0) for s in self.seats},
                "celebrating": bool(self.celebration)}

    def phone(self, action, data, seat):
        """An action from the /play page (party_api.py, under the runner's lock). Returns a dict or None."""
        if action == "join":
            self.join(seat)
        elif action == "leave" and self.state == "title":
            self.lobby_key(self.clock(), "b", seat)
        elif action == "start" and self.state == "title":
            self.join(seat)
            if self.can_start():
                self.begin(self.clock())
        else:
            raise ValueError(f"unknown action {action!r}")

    def score_entry(self):
        return (self.score_key(), self.score, False) if self.score > 0 else None

    def describe(self):
        if self.state == "title":
            who = " ".join(f"P{s}" for s in self.joined)
            return f"{self.title}: waiting for players" + (f" ({who} in)" if who else " (press A to join)")
        if self.celebration or self.standings_at is not None:
            return f"{self.title}: " + " and ".join(self.name(s) for s in self.champions()) + " won"
        return f"{self.title}: {self.status_text()}"

    def status_text(self):
        return "playing"

    # subclasses
    def start_game(self, now): ...
    def key(self, t, key, action, seat): ...


# ---- Flipdot Party mini-games ---------------------------------------------------------------

class Mini:
    """One mini-game. key() gets presses (down only) with their arrival time; tick() sets self.done; ranking()
    returns groups of seats, best first (a group is a tie)."""

    title = ""
    hint = ""
    length = 30
    min_players = 1

    def __init__(self, game, now):
        self.g = game
        self.seats = list(game.seats)
        self.t0 = now
        self.rnd = game.rnd
        self.done = False
        self.w, self.h = game.w, game.h
        self.cpu_at = {s: now + self.rnd.uniform(0.3, 0.8) for s in self.seats if game.is_cpu(s)}
        self.setup(now)

    def setup(self, now): ...
    def key(self, t, key, seat): ...
    def tick(self, now): ...
    def draw(self, c, now): ...

    def held(self, seat, key):
        return (seat, key) in self.g.held

    def label(self, c, seat, x, y):
        box_text(c, x, y, self.g.name(seat))

    def ranking(self):
        return [self.seats]

    @staticmethod
    def groups(seats, key):
        """Seats sorted by key (smaller is better), equal keys grouped."""
        out = []
        for s in sorted(seats, key=key):
            if out and key(out[-1][0]) == key(s):
                out[-1].append(s)
            else:
                out.append([s])
        return out


class MashRace(Mini):
    title = "MASH RACE"
    hint = "MASH A TO FILL YOUR CORNER"
    length = 20
    NEED = 40

    def setup(self, now):
        self.count = {s: 0 for s in self.seats}
        self.finished = {}

    def key(self, t, key, seat):
        if key == "a" and seat not in self.finished:
            self.count[seat] += 1
            if self.count[seat] >= self.NEED:
                self.finished[seat] = t

    def tick(self, now):
        for s, at in self.cpu_at.items():
            if now >= at and s not in self.finished:
                self.key(now, "a", s)
                self.cpu_at[s] = now + self.rnd.uniform(0.11, 0.2)
        first = min(self.finished.values()) if self.finished else None
        if (len(self.finished) == len(self.seats) or now - self.t0 >= self.length
                or (first is not None and now - first >= 1.5)):
            self.done = True

    def draw(self, c, now):
        for s in self.seats:
            x, y, w, h = quadrant(self.w, self.h, s)
            area = (w - 4) * (h - 9)
            n = min(area, area * self.count[s] // self.NEED)
            for i in range(n):  # fills column by column, left to right
                c.set(x + 2 + i // (h - 9), y + 7 + i % (h - 9))
            c.rect(x + 1, y + 6, w - 2, h - 7)
            self.label(c, s, x + 1, y)
            if s in self.finished:
                place = sorted(self.finished.values()).index(self.finished[s]) + 1
                box_text(c, x + w - 9, y, f"#{place}")

    def ranking(self):
        return self.groups(self.seats, lambda s: (0, self.finished[s]) if s in self.finished else (1, -self.count[s]))


class Reaction(Mini):
    title = "REACTION"
    hint = "PRESS A WHEN IT FLASHES. NOT BEFORE!"
    length = 30
    FLASHES = 3

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.best = {s: 9.0 for s in self.seats}
        self.n = 0
        self.next_flash(now)

    def next_flash(self, now):
        self.n += 1
        self.go_at = now + self.rnd.uniform(1.5, 4.0)
        self.early = set()
        self.winner = None
        self.won_at = None
        self.cpu_react = {s: self.go_at + self.rnd.uniform(0.22, 0.45) if self.rnd.random() > 0.06
                          else self.go_at - self.rnd.uniform(0.2, 1.0) for s in self.seats if self.g.is_cpu(s)}

    def key(self, t, key, seat):
        if key != "a" or seat in self.early or self.winner is not None:
            return
        if t < self.go_at:
            self.early.add(seat)  # too soon: a point off, and out of this flash
            self.score[seat] -= 1
        else:
            self.winner, self.won_at = seat, t
            self.score[seat] += 1
            self.best[seat] = min(self.best[seat], t - self.go_at)

    def tick(self, now):
        for s, at in list(self.cpu_react.items()):
            if now >= at:
                self.key(at, "a", s)
                del self.cpu_react[s]
        ended = (self.winner is not None and now - self.won_at >= 1.4) or now - self.go_at >= 3.0 \
            or (len(self.early) == len(self.seats))
        if ended:
            if self.n >= self.FLASHES or now - self.t0 >= self.length:
                self.done = True
            else:
                self.next_flash(now)

    def draw(self, c, now):
        flash = now >= self.go_at and self.winner is None and len(self.early) < len(self.seats)
        if flash:
            c.clear(1)
            c.text_center(14, "GO!", NORMAL, v=0, scale=2)
            return
        for s in self.seats:
            x, y, w, h = quadrant(self.w, self.h, s)
            self.label(c, s, x + 1, y)
            box_text(c, x + w - 4 * len(str(self.score[s])) - 2, y, str(self.score[s]))
            if s in self.early:
                c.text(x + 3, y + 10, "TOO SOON", SMALL)
            elif self.winner == s:
                ms = f"{int((self.won_at - self.go_at) * 1000)}MS"
                c.text(x + 3, y + 10, ms, SMALL)
                c.rect(x, y, w, h)
            elif self.winner is None:
                c.text(x + 3, y + 10, "WAIT" + "." * (int(now * 3) % 4), SMALL)
        box_text(c, (self.w - SMALL.width(f"{self.n}/{self.FLASHES}")) // 2, 18, f"{self.n}/{self.FLASHES}")

    def ranking(self):
        return self.groups(self.seats, lambda s: (-self.score[s], self.best[s]))


ARROWS = ("up", "right", "down", "left")


def arrow(c, cx, cy, d, r=8):
    """A filled arrow pointing d, centred on (cx, cy)."""
    for i in range(r + 1):
        half = i
        for k in range(-half, half + 1):
            dx, dy = {"up": (k, i - r // 2), "down": (k, r // 2 - i), "left": (i - r // 2, k), "right": (r // 2 - i, k)}[d]
            c.set(cx + dx, cy + dy)
    for i in range(r // 2 + 1, r + 3):
        for k in (-2, -1, 0, 1, 2):
            dx, dy = {"up": (k, i - r // 2), "down": (k, r // 2 - i), "left": (i - r // 2, k), "right": (r // 2 - i, k)}[d]
            c.set(cx + dx, cy + dy)


class Memory(Mini):
    title = "MEMORY"
    hint = "REPEAT THE ARROWS"
    length = 40
    MAX = 8

    def setup(self, now):
        self.seq = [self.rnd.choice(ARROWS) for _ in range(3)]
        self.level = {s: 0 for s in self.seats}  # sequences completed
        self.out = set()
        self.show(now)

    def show(self, now):
        self.phase, self.phase_at = "show", now
        self.pos = {s: 0 for s in self.seats if s not in self.out}
        self.cpu_next = {s: None for s in self.seats if self.g.is_cpu(s)}

    def show_time(self):
        return 0.8 + len(self.seq) * 0.65

    def key(self, t, key, seat):
        if self.phase != "input" or seat in self.out or seat not in self.pos or key not in ARROWS:
            return
        if self.pos[seat] >= len(self.seq):
            return
        if key == self.seq[self.pos[seat]]:
            self.pos[seat] += 1
            if self.pos[seat] == len(self.seq):
                self.level[seat] += 1
        else:
            self.out.add(seat)

    def tick(self, now):
        if self.phase == "show":
            if now - self.phase_at >= self.show_time():
                self.phase, self.phase_at = "input", now
            return
        if self.phase == "input":
            for s in self.cpu_next:
                if s in self.out or self.pos.get(s, 0) >= len(self.seq):
                    continue
                if self.cpu_next[s] is None:
                    self.cpu_next[s] = now + self.rnd.uniform(0.3, 0.6)
                elif now >= self.cpu_next[s]:
                    right = self.seq[self.pos[s]]
                    slip = self.rnd.random() < 0.025 * len(self.seq)
                    self.key(now, self.rnd.choice([a for a in ARROWS if a != right]) if slip else right, s)
                    self.cpu_next[s] = None
            alive = [s for s in self.pos if s not in self.out]
            limit = 2.5 + 0.8 * len(self.seq)
            if all(self.pos[s] >= len(self.seq) for s in alive) or now - self.phase_at >= limit:
                for s in alive:
                    if self.pos[s] < len(self.seq):
                        self.out.add(s)  # too slow
                alive = [s for s in alive if s not in self.out]
                if len(alive) <= (0 if len(self.seats) == 1 else 1) or len(self.seq) >= self.MAX \
                        or now - self.t0 >= self.length:
                    self.done = True
                else:
                    self.phase, self.phase_at = "next", now
        elif self.phase == "next" and now - self.phase_at >= 0.8:
            self.seq.append(self.rnd.choice(ARROWS))
            self.show(now)

    def draw(self, c, now):
        if self.phase == "show":
            t = now - self.phase_at - 0.8
            i = int(t / 0.65) if t >= 0 else -1
            c.text(1, 0, "WATCH", SMALL)
            c.text(c.w - SMALL.width(f"{len(self.seq)}") - 1, 0, f"{len(self.seq)}", SMALL)
            if 0 <= i < len(self.seq) and t - i * 0.65 < 0.5:
                arrow(c, c.w // 2, c.h // 2, self.seq[i], 12)
            return
        for s in self.seats:
            x, y, w, h = quadrant(self.w, self.h, s)
            self.label(c, s, x + 1, y)
            if s in self.out:
                c.text(x + 4, y + 10, "OUT", SMALL)
            else:
                done = self.pos.get(s, 0)
                for k in range(len(self.seq)):
                    bx = x + 3 + k * 4
                    if k < done:
                        fill_rect(c, bx, y + 11, 3, 3)
                    else:
                        c.rect(bx, y + 11, 3, 3)
        if self.phase == "input":
            c.text_center(18, "GO", SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.level[s])


class Dodge(Mini):
    title = "DODGE"
    hint = "LEFT AND RIGHT: DODGE THE BLOCKS"
    length = 30

    def setup(self, now):
        n = len(self.seats)
        self.sw = self.w // n
        self.x = {s: self.sw / 2 - 1.5 for s in self.seats}
        self.out = {}
        self.blocks = []  # [x fraction, y, width fraction]: the same blocks fall in every strip
        self.spawn_at = now + 0.6
        self.last = now

    def key(self, t, key, seat):
        pass  # moves come from the held keys

    def tick(self, now):
        dt = min(0.1, max(0.0, now - self.last))
        self.last = now
        age = now - self.t0
        speed = 13 + 1.1 * age
        for b in self.blocks:
            b[1] += speed * dt
        self.blocks = [b for b in self.blocks if b[1] < self.h + 2]
        if now >= self.spawn_at:
            self.blocks.append([self.rnd.random(), -2.0, self.rnd.uniform(0.18, 0.32)])
            self.spawn_at = now + max(0.3, 0.9 - age * 0.025)
        for s in self.seats:
            if s in self.out:
                continue
            if self.g.is_cpu(s):
                move = self.cpu_move(s)
            else:
                move = (1 if self.held(s, "right") else 0) - (1 if self.held(s, "left") else 0)
            self.x[s] = max(0.0, min(self.sw - 3.0, self.x[s] + move * 34 * dt))
            if self.hit(s):
                self.out[s] = now
        if len(self.out) >= len(self.seats) or age >= self.length:
            self.done = True

    def rects(self):
        for fx, y, fw in self.blocks:
            bw = max(2, round(fw * self.sw))
            yield int(fx * (self.sw - bw)), int(y), bw

    def hit(self, s):
        px = int(round(self.x[s]))
        for bx, by, bw in self.rects():
            if by + 2 > self.h - 3 and by < self.h - 1 and bx < px + 3 and px < bx + bw:
                return True
        return False

    def cpu_move(self, s):
        px = self.x[s] + 1.5
        danger = [(bx, bw) for bx, by, bw in self.rects() if self.h - 16 < by < self.h - 1]
        if not danger:
            return 0
        blocked = [False] * self.sw
        for bx, bw in danger:
            for xx in range(max(0, bx - 2), min(self.sw, bx + bw + 2)):
                blocked[xx] = True
        if not blocked[min(self.sw - 1, int(px))]:
            return 0
        free = [x for x in range(1, self.sw - 1) if not blocked[x]]
        if not free or self.rnd.random() < 0.04:
            return 0
        goal = min(free, key=lambda x: abs(x - px))
        return 1 if goal > px else -1

    def draw(self, c, now):
        for i, s in enumerate(self.seats):
            x0 = i * self.sw
            if i:
                for y in range(0, self.h, 2):
                    c.set(x0, y)
            for bx, by, bw in self.rects():
                fill_rect(c, x0 + bx + (1 if i else 0), by, bw, 2)
            px = x0 + int(round(self.x[s])) + (1 if i else 0)
            if s in self.out:
                if int(now * 4) % 2:
                    fill_rect(c, px, self.h - 3, 3, 2)
                box_text(c, x0 + 2, self.h - 12, "OUT")
            else:
                fill_rect(c, px, self.h - 3, 3, 2)
            box_text(c, x0 + 2, 1, self.g.name(s))

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.out.get(s, 1e12))


class TugOfWar(Mini):
    title = "TUG OF WAR"
    hint = "MASH A TO PULL YOUR TEAM'S WAY"
    length = 25
    min_players = 2

    def setup(self, now):
        self.left = self.seats[0::2]
        self.right = self.seats[1::2]
        self.pos = 0.0  # -1 left wins, 1 right wins

    def key(self, t, key, seat):
        if key != "a" or self.done:
            return
        team = self.left if seat in self.left else self.right
        self.pos += (-1 if team is self.left else 1) * 0.022 / len(team)

    def tick(self, now):
        for s, at in self.cpu_at.items():
            if now >= at:
                self.key(now, "a", s)
                self.cpu_at[s] = now + self.rnd.uniform(0.13, 0.22)
        if abs(self.pos) >= 1 or now - self.t0 >= self.length:
            self.done = True

    def draw(self, c, now):
        mid = self.w // 2
        y = 24
        c.line(2, y, self.w - 3, y)
        c.line(2, y + 1, self.w - 3, y + 1)
        for x in (2, self.w - 3):
            c.line(x, y - 6, x, y + 7)
        kx = mid + int(max(-1, min(1, self.pos)) * (mid - 6))
        fill_rect(c, kx - 2, y - 4, 5, 10)
        for xx in range(mid - 1, mid + 2):
            c.set(xx, y + 9)
        c.text(2, 2, " ".join(self.g.name(s) for s in self.left), SMALL)
        right = " ".join(self.g.name(s) for s in self.right)
        c.text(self.w - 2 - SMALL.width(right), 2, right, SMALL)
        left = max(0, self.length - (now - self.t0))
        c.text_center(34, f"{left:.0f}", SMALL)

    def ranking(self):
        if abs(self.pos) < 1e-9:
            return [self.seats]
        win, lose = (self.left, self.right) if self.pos < 0 else (self.right, self.left)
        return [list(win), list(lose)]


class StopCounter(Mini):
    title = "STOP THE CLOCK"
    hint = "PRESS A AT THE TARGET TIME"
    length = 14

    def setup(self, now):
        self.target = self.rnd.choice([3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0])
        self.start = now + 1.5
        self.stops = {}
        self.cpu_stop = {s: self.start + self.target + self.rnd.gauss(0, 0.25) for s in self.seats if self.g.is_cpu(s)}

    def key(self, t, key, seat):
        if key == "a" and seat not in self.stops and t >= self.start:
            self.stops[seat] = t - self.start

    def tick(self, now):
        for s, at in self.cpu_stop.items():
            if now >= at:
                self.key(at, "a", s)
        if len(self.stops) == len(self.seats) or now - self.start >= self.target + 3:
            self.done = True

    def draw(self, c, now):
        c.text_center(0, f"STOP AT {self.target:.2f}", SMALL)
        t = now - self.start
        if t < 0:
            c.text_center(14, "READY", NORMAL)
        elif self.done:
            for s in self.seats:
                x, y, w, h = quadrant(self.w, self.h, s)
                y += 7 if y == 0 else 2
                v = self.stops.get(s)
                c.text(x + 2, y + 1, self.g.name(s), SMALL)
                c.text(x + 2, y + 8, f"{v:.2f}" if v is not None else "--", SMALL)
        else:
            shown = f"{t:.2f}" if t < 1.5 else "?.??"
            c.text_center(13, shown, NORMAL, scale=2)
            c.text_center(31, " ".join(self.g.name(s) for s in self.seats if s in self.stops), SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: round(abs(self.stops[s] - self.target), 3) if s in self.stops else 99)


class HotPotato(Mini):
    title = "HOT POTATO"
    hint = "PRESS A TO PASS IT ON. DON'T HOLD IT WHEN IT BLOWS"
    length = 30
    min_players = 2

    def setup(self, now):
        self.holder = self.rnd.choice(self.seats)
        self.fuse = now + self.rnd.uniform(8, 18)
        self.held_for = {s: 0.0 for s in self.seats}
        self.flight = None  # (from, to, start, arrive)
        self.boom = None
        self.last = now
        self.cpu_throw = None

    def spot(self, s):
        x, y, w, h = quadrant(self.w, self.h, s)
        return x + w // 2, y + h // 2 + 2

    def next_beat(self, now):
        """When the next beat lands (live music, else a generated 120 BPM beat)."""
        try:
            import audio_signal
            r = audio_signal.reading(self.g.ctx.store, "beat", now)
            if r.get("bpm") and r.get("beat_phase") is not None:
                period = 60 / r["bpm"]
                wait = (1 - r["beat_phase"]) * period
                return now + (wait if wait >= 0.2 else wait + period)
        except Exception:
            pass
        return now + 0.4

    def key(self, t, key, seat):
        if key == "a" and seat == self.holder and self.flight is None and self.boom is None:
            others = [s for s in self.seats if s != seat]
            to = min(others, key=lambda o: (o - seat) % 5)  # the next seat round the table
            self.flight = (seat, to, t, self.next_beat(t))
            self.holder = None

    def tick(self, now):
        dt = max(0.0, now - self.last)
        self.last = now
        if self.boom is not None:
            if now - self.boom[1] >= 1.6:
                self.done = True
            return
        if self.holder is not None:
            self.held_for[self.holder] += dt
            if self.g.is_cpu(self.holder):
                if self.cpu_throw is None:
                    self.cpu_throw = now + self.rnd.uniform(0.3, 1.3)
                elif now >= self.cpu_throw:
                    self.cpu_throw = None
                    self.key(now, "a", self.holder)
        if self.flight and now >= self.flight[3]:
            self.holder = self.flight[1]
            self.flight = None
        if now >= self.fuse or now - self.t0 >= self.length:
            loser = self.holder if self.holder is not None else self.flight[1]
            self.boom = (loser, now)

    def draw(self, c, now):
        for s in self.seats:
            x, y = self.spot(s)
            c.text(x - SMALL.width(self.g.name(s)) // 2, y - 12 if y < self.h // 2 else y + 5, self.g.name(s), SMALL)
        if self.boom is not None:
            x, y = self.spot(self.boom[0])
            r = min(14, int((now - self.boom[1]) * 30) + 3)
            c.circle(x, y, r, fill=True)
            box_text(c, x - 8, y - 2, "BOOM")
            return
        if self.flight:
            a, b, t0, t1 = self.flight
            f = max(0.0, min(1.0, (now - t0) / max(0.01, t1 - t0)))
            (x0, y0), (x1, y1) = self.spot(a), self.spot(b)
            x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f - math.sin(f * math.pi) * 6
        else:
            x, y = self.spot(self.holder)
        c.circle(int(x), int(y), 3, fill=True)
        # sparks get busier as the fuse burns down
        left = self.fuse - now
        if int(now * (3 if left > 5 else 8)) % 2:
            c.set(int(x) + 3, int(y) - 4)
            c.set(int(x) + 4, int(y) - 5)

    def ranking(self):
        loser = self.boom[0] if self.boom else None
        rest = [s for s in self.seats if s != loser]
        out = self.groups(rest, lambda s: round(self.held_for[s], 1))
        return out + ([[loser]] if loser is not None else [])


class QuickCount(Mini):
    title = "QUICK COUNT"
    hint = "HOW MANY DOTS? UP/DOWN, THEN A"
    length = 22

    def setup(self, now):
        self.n = self.rnd.randint(12, 34)
        spots = set()
        while len(spots) < self.n:
            spots.add((self.rnd.randrange(8, self.w - 8, 3), self.rnd.randrange(6, self.h - 4, 3)))
        self.dots = sorted(spots)
        self.show_at = now + 1.2
        self.answer_at = self.show_at + 1.6
        self.guess = {s: 20 for s in self.seats}
        self.locked = {}
        self.cpu_lock = {s: (self.answer_at + self.rnd.uniform(2.5, 6), self.n + self.rnd.randint(-5, 5))
                         for s in self.seats if self.g.is_cpu(s)}

    def key(self, t, key, seat):
        if t < self.answer_at or seat in self.locked:
            return
        step = {"up": 1, "down": -1, "right": 5, "left": -5}.get(key)
        if step:
            self.guess[seat] = max(0, min(99, self.guess[seat] + step))
        elif key == "a":
            self.locked[seat] = t

    def tick(self, now):
        for s, (at, g) in self.cpu_lock.items():
            if now >= at and s not in self.locked:
                self.guess[s] = max(0, g)
                self.locked[s] = at
        if len(self.locked) == len(self.seats) or now - self.t0 >= self.length:
            self.done = True

    def draw(self, c, now):
        if now < self.show_at:
            c.text_center(16, "LOOK!", NORMAL)
            return
        if now < self.answer_at or self.done:
            for x, y in self.dots:
                fill_rect(c, x, y, 2, 2)
            if self.done:
                box_text(c, (self.w - SMALL.width(f"{self.n} DOTS")) // 2, 1, f"{self.n} DOTS")
                for s in self.seats:
                    x, y, w, h = quadrant(self.w, self.h, s)
                    box_text(c, x + 2, y + h - 7 if y else y + 9, f"{self.g.name(s)} {self.guess[s]}")
            return
        for s in self.seats:
            x, y, w, h = quadrant(self.w, self.h, s)
            self.label(c, s, x + 1, y)
            c.text(x + 6, y + 8, str(self.guess[s]), NORMAL)
            if s in self.locked:
                c.text(x + w - 10, y + 9, "OK", SMALL)
                c.rect(x, y, w, h)

    def ranking(self):
        return self.groups(self.seats, lambda s: abs(self.guess[s] - self.n) if s in self.locked else 999)


MINIGAMES = [MashRace, Reaction, Memory, Dodge, TugOfWar, StopCounter, HotPotato, QuickCount]


def placement_points(groups, n):
    """Points per seat from a ranking: n-1 for first, 0 for last; ties share the better place."""
    pts, place = {}, 0
    for g in groups:
        for s in g:
            pts[s] = max(0, n - 1 - place)
        place += len(g)
    return pts


@register
class FlipdotParty(PartyGame):
    id = "party"
    title = "Flipdot Party"
    description = ("Quick mini-games for up to 4 players on the controllers and phones: mash race, reaction, memory, "
                   "dodge, tug of war, stop the clock, hot potato and quick count. Points after every round, a "
                   "champion at the end. Join by pressing A.")
    params = [
        P("rounds", "number", 6, "Rounds", min=2, max=16),
        P("cpu", "select", "pair", "CPU players", options=CPU_MODES),
    ]
    INTRO_S = 3.5
    RESULT_S = 3.0
    SCORES_S = 4.0

    def start_game(self, now):
        self.round = 0
        self.mini = None
        self.bag = []
        self.next_round(now)

    def pick(self):
        n = len(self.seats)
        if not self.bag:
            self.bag = [m for m in MINIGAMES if m.min_players <= n]
            self.rnd.shuffle(self.bag)
            if self.mini is not None and len(self.bag) > 1 and self.bag[0] is type(self.mini):
                self.bag.append(self.bag.pop(0))  # never the same game twice in a row
        return self.bag.pop(0)

    def next_round(self, now):
        self.round += 1
        self.mini_cls = self.pick()
        self.mini = None
        self.phase, self.phase_at = "intro", now
        self.round_pts = {}
        self.rev += 1

    def key(self, t, key, action, seat):
        if action != "down":
            return
        if self.phase == "play" and self.mini is not None:
            self.mini.key(t, key, seat)
        elif self.phase in ("result", "scores") and key in ("a", "start") and not self.is_cpu(seat) \
                and t - self.phase_at > 1.2:
            self.phase_at = -1e9  # skip ahead

    def tick(self, now):
        if self.phase == "intro" and now - self.phase_at >= self.INTRO_S:
            self.mini = self.mini_cls(self, now)
            self.phase, self.phase_at = "play", now
            self.rev += 1
        elif self.phase == "play":
            self.mini.tick(now)
            if self.mini.done:
                self.round_pts = placement_points(self.mini.ranking(), len(self.seats))
                for s, v in self.round_pts.items():
                    self.totals[s] += v
                self.phase, self.phase_at = "result", now
                self.rev += 1
        elif self.phase == "result" and now - self.phase_at >= self.RESULT_S:
            self.phase, self.phase_at = "scores", now
        elif self.phase == "scores" and now - self.phase_at >= self.SCORES_S:
            if self.round >= int(self.p["rounds"] or 6):
                self.finish(now, standings=False)
            else:
                self.next_round(now)

    def draw(self, c, now):
        if self.phase == "intro":
            c.text_center(0, f"ROUND {self.round}/{int(self.p['rounds'] or 6)}", SMALL)
            title = self.mini_cls.title
            c.text_center(9, title, NORMAL if NORMAL.width(title) <= c.w else SMALL)
            from canvas import wrap_text
            for i, line in enumerate(wrap_text(self.mini_cls.hint, SMALL, c.w - 2)[:2]):
                c.text_center(20 + i * 6, line, SMALL)
            left = self.INTRO_S - (now - self.phase_at)
            if left < 1.5:
                c.text_center(34, "GO!" if left < 0.5 else "READY", SMALL)
        elif self.phase == "play":
            self.mini.draw(c, now)
        elif self.phase == "result":
            self.mini.draw(c, now)
            best = max(self.round_pts.values()) if self.round_pts else 0
            winners = [s for s, v in self.round_pts.items() if v == best]
            banner(c, (self.name(winners[0]) + " WINS") if len(winners) == 1 else "TIE")
        else:
            self.draw_standings(c, now)
            for i, s in enumerate(self.ranking()[:4]):
                gained = self.round_pts.get(s, 0)
                if gained:
                    c.text(c.w - SMALL.width(f"+{gained}") - 1, 9 + i * 9, f"+{gained}", SMALL)

    def status_text(self):
        name = self.mini_cls.title.title() if getattr(self, "mini_cls", None) else ""
        return f"round {getattr(self, 'round', 0)}/{int(self.p['rounds'] or 6)}, {name}"

    def phone_state(self, seat):
        out = super().phone_state(seat)
        if self.state == "playing" and getattr(self, "mini_cls", None):
            out.update(round=self.round, rounds=int(self.p["rounds"] or 6), game=self.mini_cls.title,
                       hint=self.mini_cls.hint, phase=self.phase)
        return out
