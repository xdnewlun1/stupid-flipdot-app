"""Head-to-head games: Connect Four, Tic-tac-toe, Artillery, Checkers, Battleship,
Quick Draw and Air Hockey.

Each is played against the computer or by two players (Battleship is vs the
computer only). In vs-computer mode a player 2 who presses a button takes over
from the computer; in 2-player turn-based games one controller can play both
sides until the second player presses something. While nobody plays, the title
alternates with a computer-vs-computer demo.

Computer searches run a few milliseconds per frame (Think), so a frame never stalls.
"""

import math
import random
import time

from fonts import NORMAL, SMALL

from .arcade import STEP, fill, sprite
from .base import P, register
from .games import Game

MODES = [["cpu", "Computer"], ["2p", "2 players"]]
LEVELS = [["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"]]
LEVEL_INDEX = {"easy": 0, "medium": 1, "hard": 2}
THINK_BUDGET = 0.0025  # seconds of computer search per frame
IDLE = 120  # seconds without input before going back to the title and demo
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
WIN = 100000
INF = WIN * 10
popcount = int.bit_count if hasattr(int, "bit_count") else (lambda x: bin(x).count("1"))


def versus_params(rounds, label="Wins needed", max_rounds=9, mode=True):
    ps = [P("mode", "select", "cpu", "Opponent", options=MODES)] if mode else []
    return ps + [P("difficulty", "select", "medium", "Computer skill", options=LEVELS),
                 P("rounds", "number", rounds, label, min=1, max=max_rounds)]


def invert(c, x, y, w, h):
    for yy in range(max(y, 0), min(y + h, c.h)):
        i = yy * c.w
        for xx in range(max(x, 0), min(x + w, c.w)):
            c.px[i + xx] ^= 1


# ---- computer search ------------------------------------------------------------

class Think:
    """Runs a search generator for a few ms per frame. The search stores its best
    answer so far in .best; it is done when the generator ends or `limit` seconds
    have passed (then the best answer found so far is used)."""

    def __init__(self, search, limit):
        self.best = None
        self.done = False
        self.started = time.time()
        self.limit = limit
        self.gen = search(self)

    def run(self, budget=THINK_BUDGET):
        if not self.done:
            end = time.perf_counter() + budget
            try:
                while time.perf_counter() < end:
                    next(self.gen)
            except StopIteration:
                self.done = True
            if self.best is not None and time.time() - self.started > self.limit:
                self.done = True
        return self.done


def alphabeta(think, root, children, evaluate, max_depth):
    """Iterative-deepening negamax with alpha-beta pruning, as a generator.
    children(pos) -> [(move, child)], empty when the game is over;
    evaluate(pos) scores pos for the side to move (including finished games)."""
    work = [0]

    def ab(pos, depth, alpha, beta):
        kids = children(pos)
        if not kids:
            return evaluate(pos)
        work[0] += len(kids)
        if work[0] > 12:
            work[0] = 0
            yield
        best = -INF
        for _, child in kids:
            v = -evaluate(child) if depth == 1 else -(yield from ab(child, depth - 1, -beta, -alpha))
            if v > WIN // 2:  # prefer quicker wins and slower losses
                v -= 1
            elif v < -WIN // 2:
                v += 1
            if v > best:
                best = v
                if v > alpha:
                    alpha = v
                    if alpha >= beta:
                        break
        return best

    moves = children(root)
    if not moves:
        return
    random.shuffle(moves)
    think.best = moves[0][0]
    if len(moves) == 1:
        return
    for depth in range(1, max_depth + 1):
        scores = {}
        best_v, alpha = -INF, -INF
        for i, (move, child) in enumerate(moves):
            v = -evaluate(child) if depth == 1 else -(yield from ab(child, depth - 1, -INF, -alpha))
            scores[i] = v
            if v > best_v:
                best_v, best = v, move
                alpha = max(alpha, v)
        think.best = best
        if abs(best_v) > WIN // 2:
            return  # a forced win or loss: deeper search won't change it
        order = sorted(range(len(moves)), key=lambda i: -scores[i])
        moves = [moves[i] for i in order]
        yield


# ---- shared -------------------------------------------------------------------------

class Versus(Game):
    """A two-sided game played in rounds: vs the computer or 2 players, with a
    computer-vs-computer demo while idle. Subclasses implement new_round/press/tick/draw."""

    solo = False      # vs the computer only: every controller plays side 1
    TITLE_CARD = 4    # seconds of title card between stretches of demo
    DEMO_TIME = 26

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.now = time.time()
        self.demo = False
        self.cpu = set()
        self.seen = set()
        self.wins = [0, 0]
        self.round = 0
        self.first = 1
        self.round_winner = None
        self.next_round_at = 0
        self.think = None
        self.clock = None
        self.title_at = self.now
        self.demo_over_at = 0
        self.last_input = self.now

    @property
    def level(self):
        return 1 if self.demo else LEVEL_INDEX.get(self.p["difficulty"], 1)

    def name(self, side):
        if self.demo:
            return f"CPU{side}"
        return "CPU" if side in self.cpu else f"P{side}"

    def controls(self, player, side):
        """Can `player` move for `side`? One controller plays both sides (hot seat)
        until the other player presses something."""
        return side not in self.cpu and (player == side or side not in self.seen)

    def start_match(self, demo):
        self.demo = demo
        self.demo_over_at = 0
        if demo:
            self.cpu = {1, 2}
        elif self.solo or self.p.get("mode", "cpu") == "cpu":
            self.cpu = {2}
        else:
            self.cpu = set()
        self.seen = set()
        self.held.clear()
        self.wins = [0, 0]
        self.score = 0
        self.round = 1
        self.first = random.choice((1, 2)) if demo else 1
        self.think = None
        self.clock = None
        self.last_input = time.time()
        self.round_winner = None
        self.new_round()

    def reset(self):
        self.start_match(False)

    def end_round(self, winner, pause=2.5):
        """winner: 1, 2, or 0 for a draw. tick() calls advance() once the pause is over."""
        if winner:
            self.wins[winner - 1] += 1
        self.score = self.wins[0]
        self.round_winner = winner
        self.next_round_at = self.now + pause
        self.think = None

    def round_over(self):
        """True while a finished round is showing its result (advances when due)."""
        if self.round_winner is None:
            return False
        if self.now >= self.next_round_at:
            self.advance()
        return True

    def advance(self):
        if max(self.wins) >= int(self.p["rounds"]):
            return self.finish()
        self.round += 1
        self.first = 3 - self.first
        self.round_winner = None
        self.think = None
        self.new_round()

    def finish(self):
        if self.demo:
            self.demo_over_at = self.now
        else:
            self.state = "over"
            self.over_at = self.now

    def run_steps(self, now):
        """Fixed 40 Hz clock for the real-time games."""
        if self.clock is None or now - self.clock > 0.5:
            self.clock = now - STEP
        while now - self.clock >= STEP and not self.demo_over_at and self.state != "over":
            self.clock += STEP
            self.step()

    def input(self, key, action="down", player=1):
        player = 1 if self.solo else (player - 1) % 2 + 1
        if action != "down":
            self.held.discard((player, key))
            return
        self.last_input = self.now = time.time()
        if self.state == "title" or (self.state == "over" and key in ("a", "start")):
            self.state = "playing"
            self.start_match(False)
            return
        if self.state != "playing":
            return
        if player in self.cpu:
            if player != 2 or self.solo or self.demo:
                return
            self.cpu.discard(2)  # player 2 takes over from the computer
            self.think = None
        self.seen.add(player)
        self.held.add((player, key))
        self.press(key, player)

    def render(self, c, now):
        self.now = now
        if self.state == "title":
            if not self.demo:
                self.start_match(True)
                self.title_at = now
            if self.demo_over_at:
                self.draw_result(c, now)
                if now - self.demo_over_at > 4:
                    self.start_match(True)
                return
            if (now - self.title_at) % (self.TITLE_CARD + self.DEMO_TIME) < self.TITLE_CARD:
                return self.draw_title_card(c, now)
        elif self.state == "over":
            self.draw_result(c, now)
            if now - self.over_at > 20:
                self.state, self.demo = "title", False
            return
        elif now - self.last_input > IDLE:
            self.state, self.demo = "title", False
            return self.render(c, now)
        self.tick(now)
        self.draw(c, now)
        # frames come faster while the computer thinks, so its search finishes sooner
        self.interval = 0.02 if self.think else type(self).interval

    def score_entry(self):
        return None  # head-to-head: who won, not a score to rank

    def draw_title_card(self, c, now):
        c.text_center(8, self.title.upper(), NORMAL)
        level = self.p["difficulty"].upper()
        sub = "2 PLAYERS" if not self.solo and self.p.get("mode") == "2p" else f"VS CPU {level}"
        c.text_center(20, sub, SMALL)
        if int(now * 2) % 2:
            c.text_center(32, "PRESS ANY BUTTON", SMALL)

    def draw_result(self, c, now):
        a, b = self.wins
        c.text_center(4, "DRAW" if a == b else f"{self.name(1 if a > b else 2)} WINS", NORMAL)
        c.text_center(14, f"{a}-{b}", NORMAL, scale=2)
        if self.state == "over" and int(now * 2) % 2:
            c.text_center(35, "A: PLAY AGAIN", SMALL)

    def describe(self):
        if self.state != "playing":
            return f"{self.title} ({self.state})"
        return f"{self.title} ({self.name(1)} {self.wins[0]} - {self.wins[1]} {self.name(2)})"


# ---- Connect Four -------------------------------------------------------------------
# Search position: (bits of the side to move, bits of the other side, next free bit per
# column). Bit col*7+row, row 0 at the bottom; the 7th bit of each column stays empty.

C4_ORDER = (3, 2, 4, 1, 5, 0, 6)
C4_FULL = sum(1 << (col * 7 + row) for col in range(7) for row in range(6))
C4_WINDOWS = []
for _c in range(7):
    for _r in range(6):
        for _dc, _dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
            if 0 <= _c + 3 * _dc < 7 and 0 <= _r + 3 * _dr < 6:
                C4_WINDOWS.append(sum(1 << ((_c + i * _dc) * 7 + _r + i * _dr) for i in range(4)))
C4_WEIGHT = (0, 1, 5, 30, 0)
DISC = {1: [".XXX.", "XXXXX", "XXXXX", "XXXXX", ".XXX."],
        2: [".XXX.", "X...X", "X...X", "X...X", ".XXX."]}


def c4_won(bb):
    for s in (1, 7, 6, 8):
        m = bb & (bb >> s)
        if m & (m >> (2 * s)):
            return True
    return False


def c4_children(pos):
    cur, other, hs = pos
    if c4_won(other):
        return []
    kids = []
    for col in C4_ORDER:
        h = hs[col]
        if h < col * 7 + 6:
            kids.append((col, (other, cur | (1 << h), hs[:col] + (h + 1,) + hs[col + 1:])))
    return kids


def c4_eval(pos):
    cur, other, _ = pos
    if c4_won(other):
        return -WIN
    if cur | other == C4_FULL:
        return 0
    s = 0
    for w in C4_WINDOWS:
        a, b = cur & w, other & w
        if a and not b:
            s += C4_WEIGHT[popcount(a)]
        elif b and not a:
            s -= C4_WEIGHT[popcount(b)]
    return s


@register
class ConnectFour(Versus):
    id = "connect4"
    title = "Connect Four"
    description = "Four in a row wins. Left/right picks a column, A or down drops a disc. Player 1 has the filled discs."
    params = versus_params(3)
    X0, Y0 = 21, 5
    DEPTH = (2, 4, 8)

    def new_round(self):
        self.grid = [[] for _ in range(7)]  # each column's discs, bottom first
        self.turn = self.first
        self.col = 3
        self.drop = None  # (col, side, started)
        self.line = None
        self.target = None
        self.start_turn()

    def start_turn(self):
        self.turn_at = self.now
        self.think = None
        self.target = None
        self.col_moved = self.now

    def press(self, key, player):
        if self.drop or self.round_winner is not None or not self.controls(player, self.turn):
            return
        if key == "left":
            self.col = max(self.col - 1, 0)
        elif key == "right":
            self.col = min(self.col + 1, 6)
        elif key in ("a", "b", "down"):
            self.play(self.col)

    def play(self, col):
        if len(self.grid[col]) < 6:
            self.col = col
            self.drop = (col, self.turn, self.now)

    def row_y(self, row):
        return self.Y0 + 1 + (5 - row) * 6

    def drop_y(self):
        col, _, t0 = self.drop
        t = self.now - t0
        return min(200 * t * t, self.row_y(len(self.grid[col])))

    def position(self):
        bits = [0, 0, 0]
        for col, stack in enumerate(self.grid):
            for row, side in enumerate(stack):
                bits[side] |= 1 << (col * 7 + row)
        hs = tuple(col * 7 + len(stack) for col, stack in enumerate(self.grid))
        return bits[self.turn], bits[3 - self.turn], hs

    def four(self, col, row, side):
        g = self.grid

        def mine(c, r):
            return 0 <= c < 7 and 0 <= r < len(g[c]) and g[c][r] == side

        for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
            cells = [(col, row)]
            for s in (1, -1):
                c, r = col + dc * s, row + dr * s
                while mine(c, r):
                    cells.append((c, r))
                    c, r = c + dc * s, r + dr * s
            if len(cells) >= 4:
                return cells
        return None

    def tick(self, now):
        if self.round_over():
            return
        if self.drop:
            col, side, _ = self.drop
            if self.drop_y() >= self.row_y(len(self.grid[col])):
                self.grid[col].append(side)
                self.drop = None
                self.line = self.four(col, len(self.grid[col]) - 1, side)
                if self.line:
                    self.end_round(side, 3.5)
                elif all(len(s) == 6 for s in self.grid):
                    self.end_round(0)
                else:
                    self.turn = 3 - self.turn
                    self.start_turn()
            return
        if self.turn not in self.cpu:
            return
        if self.target is None:
            if self.think is None:
                pos = self.position()
                depth = self.DEPTH[self.level]
                self.think = Think(lambda t: alphabeta(t, pos, c4_children, c4_eval, depth), 3)
            if not self.think.run() or now - self.turn_at < 0.5:
                return
            self.target = self.think.best
            if self.level == 0 and random.random() < 0.3:
                self.target = random.choice([c for c in range(7) if len(self.grid[c]) < 6])
            self.think = None
        if now - self.col_moved > 0.12:  # slide the disc over to the chosen column
            self.col_moved = now
            if self.col == self.target:
                self.play(self.target)
            else:
                self.col += 1 if self.target > self.col else -1

    def draw(self, c, now):
        x0 = self.X0
        c.line(x0 - 1, self.Y0, x0 - 1, 41)
        c.line(x0 + 41, self.Y0, x0 + 41, 41)
        c.line(x0 - 1, 41, x0 + 41, 41)
        blink = int(now * 4) % 2
        for col, stack in enumerate(self.grid):
            for row in range(6):
                x, y = x0 + col * 6, self.row_y(row)
                if row < len(stack):
                    if not (blink and self.line and (col, row) in self.line):
                        sprite(c, x, y, DISC[stack[row]])
                else:
                    c.set(x + 2, y + 2)
        if self.drop:
            col, side, _ = self.drop
            sprite(c, x0 + col * 6, int(self.drop_y()), DISC[side])
        elif self.round_winner is None:
            sprite(c, x0 + self.col * 6, 0, DISC[self.turn])
        for side, px in ((1, 0), (2, 63)):
            c.text_center(1, self.name(side), SMALL, x0=px, w=21)
            sprite(c, px + 8, 9, DISC[side])
            c.text_center(18, str(self.wins[side - 1]), NORMAL, scale=2, x0=px, w=21)
            if self.round_winner == side and blink:
                c.text_center(35, "WINS", SMALL, x0=px, w=21)
            elif self.round_winner is None and self.turn == side:
                c.line(px + 6, 15, px + 14, 15)
        if self.round_winner == 0:
            c.text_center(35, "DRAW", SMALL, x0=0, w=21)
            c.text_center(35, "DRAW", SMALL, x0=63, w=21)


# ---- Tic-tac-toe ------------------------------------------------------------------------

TTT_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
MARK = {1: ["".join("X" if abs(x - y) <= 1 or abs(x + y - 10) <= 1 else "." for x in range(11)) for y in range(11)],
        2: ["".join("X" if 3.4 <= math.hypot(x - 5, y - 5) <= 5.4 else "." for x in range(11)) for y in range(11)]}


def ttt_winner(b):
    for i, j, k in TTT_LINES:
        if b[i] and b[i] == b[j] == b[k]:
            return b[i], (i, j, k)
    return 0, None


def ttt_children(pos):
    b, side = pos
    if ttt_winner(b)[0]:
        return []
    return [(i, (b[:i] + (side,) + b[i + 1:], 3 - side)) for i in range(9) if not b[i]]


def ttt_eval(pos):
    return -WIN if ttt_winner(pos[0])[0] else 0


@register
class TicTacToe(Versus):
    id = "tictactoe"
    title = "Tic-tac-toe"
    description = "Three in a row wins. Arrows move, A places. Player 1 is X; the starting side alternates. On hard the computer never loses."
    params = versus_params(3, max_rounds=20)
    X0 = 21
    DEPTH = (1, 2, 9)

    def new_round(self):
        self.board = (0,) * 9
        self.turn = self.first
        self.cursor = 4
        self.line = None
        self.turn_at = self.now
        self.think = None

    def press(self, key, player):
        if self.round_winner is not None or not self.controls(player, self.turn):
            return
        d = DIRS.get(key)
        if d:
            x, y = self.cursor % 3 + d[0], self.cursor // 3 + d[1]
            self.cursor = min(max(y, 0), 2) * 3 + min(max(x, 0), 2)
        elif key in ("a", "b") and not self.board[self.cursor]:
            self.place(self.cursor)

    def place(self, i):
        self.board = self.board[:i] + (self.turn,) + self.board[i + 1:]
        winner, self.line = ttt_winner(self.board)
        if winner:
            self.end_round(winner, 3)
        elif all(self.board):
            self.end_round(0, 2)
        else:
            self.turn = 3 - self.turn
            self.turn_at = self.now
            self.think = None

    def tick(self, now):
        if self.round_over() or self.turn not in self.cpu:
            return
        if self.think is None:
            pos = (self.board, self.turn)
            depth = self.DEPTH[self.level]
            self.think = Think(lambda t: alphabeta(t, pos, ttt_children, ttt_eval, depth), 1.0)
        if self.think.run() and now - self.turn_at > 0.6:
            move = self.think.best
            if random.random() < (0.5, 0.2, 0)[self.level]:
                move = random.choice([i for i in range(9) if not self.board[i]])
            self.think = None
            self.cursor = move
            self.place(move)

    def cell_xy(self, i):
        return self.X0 + (i % 3) * 14, (i // 3) * 14

    def draw(self, c, now):
        x0 = self.X0
        for k in (13, 27):
            c.line(x0 + k, 0, x0 + k, 40)
            c.line(x0, k, x0 + 40, k)
        blink = int(now * 4) % 2
        for i, v in enumerate(self.board):
            if v and not (blink and self.line and i in self.line):
                x, y = self.cell_xy(i)
                sprite(c, x + 1, y + 1, MARK[v])
        human = self.turn not in self.cpu
        if self.round_winner is None and human and int(now * 3) % 3:
            x, y = self.cell_xy(self.cursor)
            if self.board[self.cursor]:
                c.rect(x, y, 13, 13)
            else:
                sprite(c, x + 1, y + 1, MARK[self.turn])
        for side, px in ((1, 0), (2, 63)):
            c.text_center(1, self.name(side), SMALL, x0=px, w=21)
            c.text_center(8, "X" if side == 1 else "O", NORMAL, x0=px, w=21)
            c.text_center(18, str(self.wins[side - 1]), NORMAL, scale=2, x0=px, w=21)
            if self.round_winner == side and blink:
                c.text_center(35, "WINS", SMALL, x0=px, w=21)
            elif self.round_winner is None and self.turn == side:
                c.line(px + 7, 16, px + 13, 16)
        if self.round_winner == 0:
            for px in (0, 63):
                c.text_center(35, "DRAW", SMALL, x0=px, w=21)


# ---- Artillery ----------------------------------------------------------------------------

TANK = ["..XXX..", "XXXXXXX", "XXXXXXX", ".X.X.X."]
GRAVITY = 60   # dots/s^2
WIND = 14      # dots/s^2 at full wind
SPEED = 0.85   # muzzle speed (dots/s) per point of power
BLAST = 4      # crater radius


@register
class Artillery(Versus):
    id = "artillery"
    title = "Artillery"
    description = ("Two tanks on destructible hills. Up/down sets the barrel angle, left/right the power, A fires. "
                   "The arrow at the top is the wind. Each hit scores; the hills are new every round.")
    params = versus_params(3, "Hits to win")

    def new_round(self):
        self.make_terrain()
        self.aim = {1: [45.0, 60.0], 2: [45.0, 60.0]}
        self.trail = {1: [], 2: []}
        self.wind = random.uniform(-1, 1)
        self.turn = self.first
        self.clock = None
        self.start_turn()

    def make_terrain(self):
        w, h = self.w, self.h
        ph = [random.uniform(0, 6.3) for _ in range(3)]
        amp = (random.uniform(2, 6), random.uniform(1, 3), random.uniform(0, 1.2))
        base, hill = random.uniform(27, 32), random.uniform(0, 12)
        heights = []
        for x in range(w):
            y = (base - hill * math.exp(-((x - w / 2) / 11) ** 2) + amp[0] * math.sin(x * 0.07 + ph[0])
                 + amp[1] * math.sin(x * 0.17 + ph[1]) + amp[2] * math.sin(x * 0.45 + ph[2]))
            heights.append(int(min(max(y, 14), 38)))
        self.tanks = []
        for tx in (random.randint(3, 13), random.randint(w - 20, w - 10)):
            ground = heights[tx + 3]
            for x in range(tx - 1, tx + 8):
                heights[x] = ground
            self.tanks.append([tx, ground - 1])  # left x, bottom row
        self.ground = bytearray(w * h)
        for x, top in enumerate(heights):
            for y in range(top, h):
                self.ground[y * w + x] = 1

    def start_turn(self):
        self.phase = "aim"
        self.turn_at = self.now
        self.think = None
        self.cpu_aim = None
        self.shell = None
        self.boom = None
        self.wind = min(max(self.wind + random.uniform(-0.3, 0.3), -1), 1)

    def press(self, key, player):
        if self.phase != "aim" or self.round_winner is not None or not self.controls(player, self.turn):
            return
        a = self.aim[self.turn]
        if key in ("up", "down"):
            a[0] = min(max(round(a[0]) + (1 if key == "up" else -1), 0), 90)
        elif key in ("left", "right"):
            a[1] = min(max(round(a[1]) + (1 if key == "right" else -1), 0), 100)
        elif key == "a":
            self.fire()
        self.repeat_at = self.now + 0.3

    def muzzle(self, side, angle, power):
        tx, ty = self.tanks[side - 1]
        d = 1 if side == 1 else -1
        a = math.radians(angle)
        return [tx + 3 + math.cos(a) * 5 * d, ty - 3 - math.sin(a) * 5,
                math.cos(a) * power * SPEED * d, -math.sin(a) * power * SPEED]

    def fire(self):
        self.shell = self.muzzle(self.turn, *self.aim[self.turn])
        self.trail[self.turn] = []
        self.phase = "flight"

    def hit_test(self, x, y):
        """What the shell at (x, y) hits: a tank index, "ground", "out" or None."""
        if x < 0 or x >= self.w or y >= self.h:
            return "out"
        for i, (tx, ty) in enumerate(self.tanks):
            if tx <= x < tx + 7 and ty - 3 <= y < ty + 1:
                return i
        if y >= 0 and self.ground[int(y) * self.w + int(x)]:
            return "ground"
        return None

    def fly(self, s, dt):
        s[2] += self.wind * WIND * dt
        s[3] += GRAVITY * dt
        s[0] += s[2] * dt
        s[1] += s[3] * dt

    def simulate(self, side, angle, power):
        s = self.muzzle(side, angle, power)
        for _ in range(2000):
            self.fly(s, STEP / 2)
            what = self.hit_test(s[0], s[1])
            if what is not None:
                return s[0], s[1], what
        return s[0], s[1], "out"

    def aim_search(self, think, side):
        """Bisect the power for a spread of angles; keep the shot landing closest to the enemy."""
        enemy = 2 - side
        ex, ey = self.tanks[enemy][0] + 3, self.tanks[enemy][1] - 1
        d = 1 if side == 1 else -1
        best = None
        angles = list(range(20, 80, 5))
        random.shuffle(angles)
        for angle in angles:
            lo, hi = 5.0, 100.0
            for _ in range(9):
                power = (lo + hi) / 2
                x, y, what = self.simulate(side, angle, power)
                miss = 0 if what == enemy else math.hypot(x - ex, y - ey)
                if best is None or miss < best:
                    best, think.best = miss, (angle, power)
                if (x - ex) * d > 0 and what != side - 1:
                    hi = power
                else:
                    lo = power
                yield
            if best < 1:
                return

    def explode(self, x, y):
        w, h = self.w, self.h
        for yy in range(max(int(y) - BLAST, 0), min(int(y) + BLAST + 1, h - 1)):  # the bottom row stays
            for xx in range(max(int(x) - BLAST, 0), min(int(x) + BLAST + 1, w)):
                if (xx - x) ** 2 + (yy - y) ** 2 <= BLAST * BLAST + 1:
                    self.ground[yy * w + xx] = 0
        self.hits = {i + 1 for i, (tx, ty) in enumerate(self.tanks) if math.hypot(tx + 3 - x, ty - 1.5 - y) <= BLAST + 2.5}
        self.boom = (x, y, self.now)
        self.span = (max(int(x) - BLAST - 1, 0), min(int(x) + BLAST + 2, w))
        self.phase = "boom"

    def settle(self):
        """Unsupported dirt and tanks drop one dot per step; True while anything moved."""
        w, g = self.w, self.ground
        moved = False
        for x in range(*self.span):
            for y in range(self.h - 2, -1, -1):
                if g[y * w + x] and not g[(y + 1) * w + x]:
                    g[y * w + x], g[(y + 1) * w + x] = 0, 1
                    moved = True
        for t in self.tanks:
            tx, ty = t
            if ty < self.h - 2 and not any(g[(ty + 1) * w + x] for x in range(tx, tx + 7)):
                t[1] += 1
                moved = True
        return moved

    def tick(self, now):
        if self.round_over():
            return
        self.run_steps(now)

    def step(self):
        if self.round_winner is not None:
            return
        side = self.turn
        if self.phase == "aim":
            if side in self.cpu:
                self.cpu_step(side)
            elif self.now >= getattr(self, "repeat_at", 0):
                keys = {k for p, k in self.held if self.controls(p, side)}
                a = self.aim[side]
                a[0] = min(max(a[0] + (("up" in keys) - ("down" in keys)) * 30 * STEP, 0), 90)
                a[1] = min(max(a[1] + (("right" in keys) - ("left" in keys)) * 30 * STEP, 0), 100)
        elif self.phase == "flight":
            s = self.shell
            for _ in range(2):
                self.fly(s, STEP / 2)
                what = self.hit_test(s[0], s[1])
                if what is not None:
                    self.shell = None
                    if what == "out":
                        self.hits = set()
                        self.phase, self.boom = "settle", None
                        self.span = (0, 0)
                    else:
                        self.explode(s[0], s[1])
                    return
            if len(self.trail[side]) < 300:
                self.trail[side].append((int(s[0]), int(s[1])))
        elif self.phase == "boom":
            if self.now - self.boom[2] > 0.35:
                self.phase = "settle"
        elif self.phase == "settle":
            if not self.settle():
                for tx, ty in self.tanks:  # dirt never rests inside a tank
                    for y in range(ty - 3, ty + 1):
                        self.ground[y * self.w + tx:y * self.w + tx + 7] = bytes(7)
                if self.hits:
                    # hitting only yourself gives the point away
                    self.end_round(side if 3 - side in self.hits else 3 - side, 3)
                else:
                    self.turn = 3 - side
                    self.start_turn()

    def cpu_step(self, side):
        if self.cpu_aim is None:
            if self.think is None:
                self.think = Think(lambda t: self.aim_search(t, side), 1.0)
            if not self.think.run():
                return
            angle, power = self.think.best
            err = (0.5 + 5 * (2 - self.level) ** 1.3, 0.6 + 2.8 * (2 - self.level) ** 1.3)
            self.cpu_aim = (min(max(random.gauss(angle, err[0] / 2), 0), 90),
                            min(max(random.gauss(power, err[1] / 2), 0), 100))
            self.think = None
            self.aimed_at = None
        a, goal = self.aim[side], self.cpu_aim
        done = True
        for i, rate in ((0, 40), (1, 50)):  # turn the barrel and dial the power in view
            diff = goal[i] - a[i]
            if abs(diff) > rate * STEP:
                a[i] += math.copysign(rate * STEP, diff)
                done = False
            else:
                a[i] = goal[i]
        if done:
            self.aimed_at = self.aimed_at or self.now
            if self.now - self.aimed_at > 0.4 and self.now - self.turn_at > 1:
                self.fire()

    def draw(self, c, now):
        c.px[:] = self.ground
        side = self.turn
        if self.phase == "aim":
            for i, (x, y) in enumerate(self.trail[side]):
                if i % 3 == 0:
                    c.set(x, y)
        for i, (tx, ty) in enumerate(self.tanks):
            s = i + 1
            if self.round_winner is not None and s in self.hits and int(now * 6) % 2:
                continue
            sprite(c, tx, ty - 3, TANK)
            a = math.radians(self.aim[s][0])
            d = 1 if s == 1 else -1
            c.line(tx + 3, ty - 3, tx + 3 + math.cos(a) * 4 * d, ty - 3 - math.sin(a) * 4)
            if s == side and self.phase == "aim" and self.round_winner is None and int(now * 3) % 3:
                c.line(tx + 2, ty - 9, tx + 4, ty - 9)
                c.set(tx + 3, ty - 8)
        if self.shell and self.shell[1] >= 0:
            fill(c, self.shell[0] - 0.5, self.shell[1] - 0.5, 2, 2)
        if self.boom:
            x, y, t0 = self.boom
            r = min((now - t0) * 20, BLAST + 1)
            if self.phase == "boom":
                c.circle(x, y, r)
        # HUD: names and scores, the aim of the side to move, the wind arrow
        fill(c, 0, 0, c.w, 6, 0)
        c.text(0, 0, f"{self.name(1)} {self.wins[0]}", SMALL)
        right = f"{self.wins[1]} {self.name(2)}"
        c.text(c.w - SMALL.width(right), 0, right, SMALL)
        angle, power = self.aim[side]
        c.text_center(0, f"{round(angle)}° {round(power)}", SMALL)
        n = round(self.wind * 8)
        if n:
            sx = 42 - n // 2
            c.line(sx, 7, sx + n, 7)
            d = 1 if n > 0 else -1
            for k in (1, 2):
                c.set(sx + n - d * k, 7 - k)
                c.set(sx + n - d * k, 7 + k)
        else:
            c.set(42, 7)
        if self.round_winner is not None:
            msg = "HIT!" if self.round_winner in (side, 0) else "OOPS"
            tw = NORMAL.width(msg)
            fill(c, (c.w - tw) // 2 - 2, 10, tw + 4, 11, 0)
            c.text_center(12, msg, NORMAL)


# ---- Checkers ----------------------------------------------------------------------------------
# Board: tuple of 64; 1/2 = player 1's man/king (moves up), -1/-2 = player 2's (moves down).
# A move is (path of squares, captured squares).

DARK = tuple(i for i in range(64) if (i % 8 + i // 8) % 2)
KING_DIRS = ((-1, -1), (1, -1), (-1, 1), (1, 1))
PIECE = {1: [".XXX.", "XXXXX", "XXXXX", "XXXXX", ".XXX."],
         2: [".XXX.", "XXXXX", "XX.XX", "XXXXX", ".XXX."],
         -1: [".XXX.", "X...X", "X...X", "X...X", ".XXX."],
         -2: [".XXX.", "X...X", "X.X.X", "X...X", ".XXX."]}


def _ck_jumps(b, side, king, i, path, caps, out):
    x, y = i % 8, i // 8
    more = False
    for dx, dy in (KING_DIRS if king else ((-1, -side), (1, -side))):
        jx, jy = x + 2 * dx, y + 2 * dy
        if 0 <= jx < 8 and 0 <= jy < 8:
            m, j = (y + dy) * 8 + x + dx, jy * 8 + jx
            if b[m] * side < 0 and m not in caps and (b[j] == 0 or j == path[0]):
                more = True
                if not king and jy == (0 if side == 1 else 7):
                    out.append((path + (j,), caps + (m,)))  # crowned: the move ends here
                else:
                    _ck_jumps(b, side, king, j, path + (j,), caps + (m,), out)
    if not more and caps:
        out.append((path, caps))


def ck_moves(b, side):
    """Legal moves for side (1 or -1). Captures are compulsory and jump on as far as they can."""
    jumps, steps = [], []
    for i in DARK:
        p = b[i]
        if p * side <= 0:
            continue
        king = p in (2, -2)
        _ck_jumps(b, side, king, i, (i,), (), jumps)
        if not jumps:
            x, y = i % 8, i // 8
            for dx, dy in (KING_DIRS if king else ((-1, -side), (1, -side))):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 8 and 0 <= ny < 8 and b[ny * 8 + nx] == 0:
                    steps.append(((i, ny * 8 + nx), ()))
    return jumps or steps


def ck_apply(b, move, hops=None):
    """Board after a move (or its first `hops` hops, for showing a move in progress)."""
    path, caps = move
    if hops is not None:
        path, caps = path[:hops + 1], caps[:hops]
    nb = list(b)
    p = nb[path[0]]
    nb[path[0]] = 0
    for m in caps:
        nb[m] = 0
    end = path[-1]
    if p == 1 and end < 8:
        p = 2
    elif p == -1 and end >= 56:
        p = -2
    nb[end] = p
    return tuple(nb)


def ck_children(pos):
    b, side = pos
    return [(m, (ck_apply(b, m), -side)) for m in ck_moves(b, side)]


def ck_eval(pos):
    b, side = pos
    s = mine = theirs = 0
    for i in DARK:
        p = b[i]
        if p:
            if p == 1:
                s += 100 + (7 - i // 8) * 3
            elif p == -1:
                s -= 100 + (i // 8) * 3
            else:
                s += 160 if p > 0 else -160
            if p * side > 0:
                mine += 1
            else:
                theirs += 1
    if not mine:
        return -WIN
    if not theirs:
        return WIN
    if mine + theirs <= 8 and s:
        # endgame: the side ahead wants its kings close to the enemy and pieces traded off
        ahead = 1 if s > 0 else -1
        lead = [i for i in DARK if b[i] * ahead == 2]
        prey = [i for i in DARK if b[i] * ahead < 0]
        for k in lead:
            s -= ahead * 4 * min(max(abs(k % 8 - j % 8), abs(k // 8 - j // 8)) for j in prey)
        s += ahead * 8 * (12 - mine - theirs)
    return s * side


@register
class Checkers(Versus):
    id = "checkers"
    title = "Checkers"
    description = ("English draughts. Arrows move the cursor, A picks a piece and then where it goes (once per jump), "
                   "B cancels. Captures are compulsory. Player 1 is filled and moves up; kings have a dot.")
    params = versus_params(1)
    X0, Y0 = 22, 1
    DEPTH = (1, 3, 8)
    HOP = 0.35

    def new_round(self):
        self.board = tuple((-1 if i < 24 else 1 if i >= 40 else 0) if i in DARK else 0 for i in range(64))
        self.turn = self.first
        self.quiet = 0  # plies since the last capture
        self.cursor = (2, 5) if self.turn == 1 else (3, 2)
        self.start_turn()

    @property
    def sign(self):
        return 1 if self.turn == 1 else -1

    def start_turn(self):
        self.moves = ck_moves(self.board, self.sign)
        self.sel = ()
        self.anim = None
        self.think = None
        self.turn_at = self.now
        if not self.moves:
            self.end_round(3 - self.turn, 3)
        elif self.quiet >= 80:
            self.end_round(0, 3)
        elif not any(m[0][0] == self.cursor[1] * 8 + self.cursor[0] for m in self.moves):
            i = self.moves[0][0][0]
            self.cursor = (i % 8, i // 8)

    def press(self, key, player):
        if self.anim or self.round_winner is not None or not self.controls(player, self.turn):
            return
        d = DIRS.get(key)
        if d:
            self.cursor = (min(max(self.cursor[0] + d[0], 0), 7), min(max(self.cursor[1] + d[1], 0), 7))
            return
        if key == "b":
            self.sel = ()
            return
        if key != "a":
            return
        sq = self.cursor[1] * 8 + self.cursor[0]
        if len(self.sel) <= 1 and self.board[sq] * self.sign > 0:
            if any(m[0][0] == sq for m in self.moves):
                self.sel = (sq,)
            return
        if not self.sel:
            return
        want = self.sel + (sq,)
        match = [m for m in self.moves if m[0][:len(want)] == want]
        done = [m for m in match if len(m[0]) == len(want)]
        if done:
            self.apply(done[0])
        elif match:
            self.sel = want

    def apply(self, move):
        self.board = ck_apply(self.board, move)
        self.quiet = 0 if move[1] else self.quiet + 1
        end = move[0][-1]
        self.cursor = (end % 8, end // 8)
        self.turn = 3 - self.turn
        self.start_turn()

    def tick(self, now):
        if self.round_over():
            return
        if self.anim:
            move, t0 = self.anim
            if now - t0 >= self.HOP * len(move[0]):
                self.apply(move)
            return
        if self.turn not in self.cpu:
            return
        if self.think is None:
            pos, depth = (self.board, self.sign), self.DEPTH[self.level]
            self.think = Think(lambda t: alphabeta(t, pos, ck_children, ck_eval, depth), 2.5)
        if self.think.run() and now - self.turn_at > 0.4:
            move = self.think.best
            if self.level == 0 and random.random() < 0.3:
                move = random.choice(self.moves)
            self.think = None
            self.anim = (move, now)

    def shown(self):
        """The board as displayed, with a move in progress partly made."""
        if self.anim:
            move, t0 = self.anim
            hops = min(int((self.now - t0) / self.HOP), len(move[0]) - 1)
            return ck_apply(self.board, move, hops), move[0][hops]
        if len(self.sel) > 1:
            move = next(m for m in self.moves if m[0][:len(self.sel)] == self.sel)
            return ck_apply(self.board, move, len(self.sel) - 1), self.sel[-1]
        return self.board, self.sel[0] if self.sel else None

    def draw(self, c, now):
        x0, y0 = self.X0, self.Y0
        c.rect(x0 - 1, y0 - 1, 42, 42)
        b, moving = self.shown()
        blink = int(now * 4) % 2
        for i in DARK:
            x, y = x0 + i % 8 * 5, y0 + i // 8 * 5
            if b[i]:
                if not (moving == i and blink):
                    sprite(c, x, y, PIECE[b[i]])
            else:
                c.set(x + 2, y + 2)
        human = self.turn not in self.cpu and self.round_winner is None and not self.anim
        if human:
            if self.sel and blink:  # where the selected piece can go next
                for path, _ in self.moves:
                    if path[:len(self.sel)] == self.sel:
                        j = path[len(self.sel)]
                        c.rect(x0 + j % 8 * 5 + 1, y0 + j // 8 * 5 + 1, 3, 3)
            if int(now * 3) % 3:
                invert(c, x0 + self.cursor[0] * 5, y0 + self.cursor[1] * 5, 5, 5)
        counts = {1: sum(1 for v in b if v > 0), 2: sum(1 for v in b if v < 0)}
        for side, px in ((1, 0), (2, 63)):
            c.text_center(1, self.name(side), SMALL, x0=px, w=21)
            if self.round_winner is None and self.turn == side:
                c.line(px + 6, 7, px + 14, 7)
            sprite(c, px + 8, 10, PIECE[1 if side == 1 else -1])
            c.text_center(18, str(counts[side]), NORMAL, x0=px, w=21)
            if int(self.p["rounds"]) > 1:
                c.text_center(28, f"WON {self.wins[side - 1]}", SMALL, x0=px, w=21)
            if self.round_winner in (side, 0) and blink:
                c.text_center(35, "WINS" if self.round_winner else "DRAW", SMALL, x0=px, w=21)


# ---- Battleship -----------------------------------------------------------------------------------

FLEET = (5, 4, 3, 3, 2)
HIT = ["X.X", ".X.", "X.X"]


def fleet_fits(cells, taken):
    return all(0 <= x < 10 and 0 <= y < 10 and (x, y) not in taken for x, y in cells)


def ship_cells(x, y, n, horiz):
    return [(x + i, y) if horiz else (x, y + i) for i in range(n)]


def halo(cells):
    return {(x + dx, y + dy) for x, y in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)}


def place_fleet(lengths=FLEET, ships=()):
    """Random non-touching ships; returns a list of cell lists."""
    while True:
        out = [list(s) for s in ships]
        taken = set().union(*(halo(s) for s in out)) if out else set()
        for n in lengths:
            for _ in range(300):
                cells = ship_cells(random.randrange(10), random.randrange(10), n, random.random() < 0.5)
                if fleet_fits(cells, taken):
                    out.append(cells)
                    taken |= halo(cells)
                    break
            else:
                break  # boxed in: start over
        else:
            return out


def cpu_shot(shots, ships, level):
    """Pick a cell to fire at. shots: {cell: "hit"|"miss"} fired so far at this fleet; ships: its
    ship cell lists (only used for what the shooter is told: which ships are sunk)."""
    sunk = [s for s in ships if all(c in shots for c in s)]
    sunk_cells = {c for s in sunk for c in s}
    open_hits = [c for c, r in shots.items() if r == "hit" and c not in sunk_cells]
    unknown = [(x, y) for y in range(10) for x in range(10) if (x, y) not in shots]
    if level == 0 and (not open_hits or random.random() < 0.5):
        return random.choice(unknown)
    if level == 2:  # count the ways each remaining ship could still lie over each cell
        left = list(FLEET)
        for s in sunk:
            left.remove(len(s))
        blocked = {c for c, r in shots.items() if r == "miss"} | halo(sunk_cells)
        heat = {}
        hits = set(open_hits)
        for n in left:
            for horiz in (True, False):
                for y in range(10 if horiz else 11 - n):
                    for x in range(11 - n if horiz else 10):
                        cells = ship_cells(x, y, n, horiz)
                        if any(c in blocked for c in cells):
                            continue
                        k = sum(1 for c in cells if c in hits)
                        if hits and not k:
                            continue
                        for cell in cells:
                            if cell not in shots:
                                heat[cell] = heat.get(cell, 0) + 1 + 30 * k
        if heat:
            top = max(heat.values())
            return random.choice([c for c, v in heat.items() if v == top])
    if open_hits:  # target: extend a line of hits, else try around a hit
        hits = set(open_hits)
        lines = []
        for x, y in open_hits:
            for dx, dy in ((1, 0), (0, 1)):
                if (x + dx, y + dy) in hits:
                    lines += [(x + dx * k, y + dy * k) for k in (-1, 2)]
        around = [(x + dx, y + dy) for x, y in open_hits for dx, dy in DIRS.values()]
        for cands in (lines, around):
            cands = [c for c in cands if c in unknown]
            if cands:
                return random.choice(cands)
    hunt = [(x, y) for x, y in unknown if (x + y) % 2 == 0]  # every ship covers one of these
    return random.choice(hunt or unknown)


@register
class Battleship(Versus):
    id = "battleship"
    title = "Battleship"
    description = ("Sink the computer's fleet first. Arrows aim at the right-hand grid, A fires. "
                   "Placing ships yourself: arrows move, B turns, A places, X or start places the rest.")
    params = versus_params(1, "Games to win", mode=False) + [
        P("auto_place", "checkbox", True, "Place my ships automatically")]
    solo = True
    GRID_X = (2, 52)
    GRID_Y = 10

    def name(self, side):
        if self.demo:
            return f"CPU{side}"
        return "YOU" if side == 1 else "CPU"

    def new_round(self):
        self.ships = {1: place_fleet(), 2: place_fleet()}
        self.shots = {1: {}, 2: {}}  # shots fired at side 1's and side 2's fleet
        self.turn = self.first
        self.turn_at = self.now
        self.cursor = [4, 4]
        self.msg = ""
        self.placing = None
        if 1 not in self.cpu and not self.p["auto_place"]:
            self.ships[1] = []
            self.placing = [4, 4, True]  # ghost ship x, y, horizontal

    def afloat(self, side):
        return sum(1 for s in self.ships[side] if not all(c in self.shots[side] for c in s))

    def press(self, key, player):
        if self.round_winner is not None:
            return
        d = DIRS.get(key)
        if self.placing:
            g = self.placing
            n = FLEET[len(self.ships[1])]
            if d:
                g[0], g[1] = min(max(g[0] + d[0], 0), 9), min(max(g[1] + d[1], 0), 9)
            elif key == "b":
                g[2] = not g[2]
            elif key == "a":
                cells = ship_cells(g[0], g[1], n, g[2])
                taken = set().union(*(halo(s) for s in self.ships[1])) if self.ships[1] else set()
                if fleet_fits(cells, taken):
                    self.ships[1].append(cells)
            elif key in ("x", "y", "start"):
                self.ships[1] = place_fleet(FLEET[len(self.ships[1]):], self.ships[1])
            if len(self.ships[1]) == len(FLEET):
                self.placing = None
                self.turn_at = self.now
            return
        if d:
            self.cursor = [(self.cursor[0] + d[0]) % 10, (self.cursor[1] + d[1]) % 10]
        elif key in ("a", "b") and self.turn == 1 and self.now >= self.turn_at:
            cell = tuple(self.cursor)
            if cell not in self.shots[2]:
                self.fire(1, cell)

    def fire(self, shooter, cell):
        target = 3 - shooter
        shots = self.shots[target]
        ship = next((s for s in self.ships[target] if cell in s), None)
        shots[cell] = "hit" if ship else "miss"
        self.last = (target, cell, self.now)
        if not ship:
            self.msg = "MISS"
        elif all(c in shots for c in ship):
            self.msg = "SUNK"
        else:
            self.msg = "HIT"
        if not self.afloat(target):
            return self.end_round(shooter, 4)
        self.turn = target
        self.turn_at = self.now + (0.9 if target in self.cpu else 0.3)

    def tick(self, now):
        if self.round_over() or self.placing:
            return
        if self.turn in self.cpu and now >= self.turn_at + 0.4:
            target = 3 - self.turn
            self.fire(self.turn, cpu_shot(self.shots[target], self.ships[target], self.level))

    def draw_grid(self, c, side, now):
        gx, gy = self.GRID_X[side - 1], self.GRID_Y
        c.rect(gx - 2, gy - 2, 34, 34)
        shots = self.shots[side]
        sunk = set()
        for ship in self.ships[side]:
            if all(cell in shots for cell in ship):
                sunk.update(ship)
            # your own ships always show; the computer's once sunk, or blinking after the game
            if side == 1 or (self.round_winner is not None and int(now * 3) % 2):
                for x, y in ship:
                    if (x, y) not in shots:
                        fill(c, gx + x * 3, gy + y * 3, 3, 3)
        for (x, y), r in shots.items():
            px, py = gx + x * 3, gy + y * 3
            if r == "miss":
                c.set(px + 1, py + 1)
            elif side == 2 and (x, y) in sunk:
                fill(c, px, py, 3, 3)
            else:
                sprite(c, px, py, HIT)
        last = getattr(self, "last", None)
        if last and last[0] == side and now - last[2] < 0.8 and int(now * 8) % 2:
            invert(c, gx + last[1][0] * 3, gy + last[1][1] * 3, 3, 3)

    def draw(self, c, now):
        for side in (1, 2):
            gx = self.GRID_X[side - 1]
            c.text_center(1, f"{self.name(side)} {self.afloat(side)}", SMALL, x0=gx - 2, w=34)
        self.draw_grid(c, 1, now)
        gx, gy = self.GRID_X[0], self.GRID_Y
        if self.placing:
            g = self.placing
            n = FLEET[len(self.ships[1])]
            if int(now * 4) % 2:
                for x, y in ship_cells(g[0], g[1], n, g[2]):
                    if x < 10 and y < 10:
                        invert(c, gx + x * 3, gy + y * 3, 3, 3)
            for i, line in enumerate(("PLACE", "SHIPS", "A SET", "B TURN", "X AUTO")):
                c.text_center(10 + i * 6, line, SMALL, x0=self.GRID_X[1] - 2, w=34)
            return
        self.draw_grid(c, 2, now)
        mid = dict(x0=34, w=16)
        if self.round_winner is not None:
            if int(now * 2) % 2:
                c.text_center(17, self.name(self.round_winner), SMALL, **mid)
                c.text_center(23, "WINS", SMALL, **mid)
            return
        if self.msg:
            c.text_center(24, self.msg, SMALL, **mid)
        c.text_center(14, ">" if self.turn == 1 else "<", NORMAL, **mid)
        if self.turn == 1 and 1 not in self.cpu and now >= self.turn_at and int(now * 3) % 3:
            c.rect(self.GRID_X[1] + self.cursor[0] * 3 - 1, gy + self.cursor[1] * 3 - 1, 5, 5)


# ---- Quick Draw -------------------------------------------------------------------------------------

COWBOY = ["...XXXXX...", "...XXXXX...", ".XXXXXXXXX.", "....XXX....", "....XXX....", ".....X.....",
          "...XXXXX...", "..X.XXX.X..", "..X.XXX.X..", "..X.XXX.X..", "....XXX....", "....X.X....",
          "....X.X....", "....X.X....", "...XX.XX..."]
COWBOY_DRAWN = ["...XXXXX...", "...XXXXX...", ".XXXXXXXXX.", "....XXX....", "....XXX....", ".....X.....",
                "...XXXXXXXX", "..X.XXX..XX", "..X.XXX....", "..X.XXX....", "....XXX....", "....X.X....",
                "....X.X....", "....X.X....", "...XX.XX..."]
COWBOY_DOWN = ["".join(r[i] for r in reversed(COWBOY)) for i in range(len(COWBOY[0]))]  # turned 90 degrees


def mirror(rows):
    return [r[::-1] for r in rows]


@register
class QuickDraw(Versus):
    id = "quickdraw"
    title = "Quick Draw"
    description = "A reaction duel: wait for DRAW!, then press A before the other side. Pressing too soon loses the round."
    params = versus_params(3, "Rounds to win")
    interval = 0.02
    REACT = (0.42, 0.3, 0.22)  # computer's mean reaction time by difficulty

    def new_round(self):
        self.phase = "ready"
        self.phase_at = self.now
        self.draw_at = self.now + 1.5 + random.uniform(1.5, 4.5)
        self.shown_at = None
        self.cpu_at = {}
        self.result = None  # (winner, loser, early, reaction)
        for side in self.cpu:
            if self.level == 0 and random.random() < 0.1:  # an easy computer sometimes jumps the gun
                self.cpu_at[side] = self.draw_at - random.uniform(0.2, 1.2)

    def press(self, key, player):
        if key in ("a", "b") and self.round_winner is None and self.phase != "ready":
            self.shoot(player, time.time())

    def shoot(self, side, t):
        if self.round_winner is not None:
            return
        for s, ct in self.cpu_at.items():  # a computer press due earlier counts first
            if s != side and ct < t:
                return self.shoot(s, ct)
        self.cpu_at = {}
        if self.shown_at is None or t < self.shown_at:
            self.result = (3 - side, side, True, 0)
        else:
            self.result = (side, 3 - side, False, t - self.shown_at)
        self.end_round(self.result[0], 3)

    def tick(self, now):
        if self.round_over():
            return
        if self.phase == "ready" and now - self.phase_at > 1.5:
            self.phase = "wait"
        if self.phase != "draw" and now >= self.draw_at:
            self.phase, self.shown_at = "draw", now
            for side in self.cpu:
                self.cpu_at[side] = now + max(0.13, random.gauss(self.REACT[self.level], 0.05))
        due = [(t, s) for s, t in self.cpu_at.items() if t <= now]
        if due:
            t, s = min(due)
            self.shoot(s, t)
        elif self.phase == "draw" and now - self.shown_at > 3:
            self.result = None  # nobody fired: replay the round
            self.end_round(0, 1.5)

    def draw(self, c, now):
        c.line(0, 40, c.w - 1, 40)
        r = self.result
        for side, x in ((1, 5), (2, 68)):
            pose = COWBOY
            if r and side == r[0] and not r[2] or r and side == r[1] and r[2]:
                pose = COWBOY_DRAWN
            if r and side == r[1] and not r[2]:
                rows = mirror(COWBOY_DOWN) if side == 1 else COWBOY_DOWN  # falls over backwards
                sprite(c, x - 2, 40 - len(rows), rows)
            else:
                sprite(c, x, 25, pose if side == 1 else mirror(pose))
        c.text(0, 0, f"{self.name(1)} {self.wins[0]}", SMALL)
        right = f"{self.wins[1]} {self.name(2)}"
        c.text(c.w - SMALL.width(right), 0, right, SMALL)
        if self.round_winner is not None:
            if r is None:
                c.text_center(12, "NO SHOT", NORMAL)
                return
            winner, loser, early, react = r
            if not early and now - self.next_round_at + 3 < 0.15:  # muzzle flash across the street
                y = 31
                for x in range(17, 67, 3):
                    c.set(x, y)
            c.text_center(8, f"{self.name(winner)} WINS", SMALL)
            c.text_center(16, f"{self.name(loser)} TOO SOON" if early else f"{react:.3f} S", NORMAL if not early else SMALL)
        elif self.phase == "ready":
            c.text_center(12, f"ROUND {self.round}", NORMAL)
        elif self.phase == "wait":
            c.text_center(12, "WAIT" + "." * (int(now * 2) % 4), NORMAL)
        else:
            c.text_center(9, "DRAW!", NORMAL, scale=2)


# ---- Air Hockey -------------------------------------------------------------------------------

MALLET = [".XXXXX.", "XXXXXXX", "XXXXXXX", "XXXXXXX", "XXXXXXX", "XXXXXXX", ".XXXXX."]
MALLET_RING = [".XXXXX.", "XX...XX", "X.....X", "X.....X", "X.....X", "XX...XX", ".XXXXX."]


@register
class AirHockey(Versus):
    id = "air_hockey"
    title = "Air Hockey"
    description = ("Move your mallet anywhere in your half with the arrows and knock the puck into the other goal. "
                   "Player 1 (filled mallet) defends the left goal.")
    params = versus_params(7, "Goals to win", max_rounds=21)
    GOAL = (12, 30)   # goal mouth rows
    PR, MR = 1.5, 3.0  # puck and mallet radius
    MAX = 100          # puck top speed, dots/s

    def new_round(self):
        self.mallet = [[8.0, 21.0], [75.0, 21.0]]
        self.mvel = [[0.0, 0.0], [0.0, 0.0]]
        side = getattr(self, "serve_to", 0) or random.choice((1, 2))
        self.puck = [21.0 if side == 1 else 62.0, 21.0 + random.uniform(-6, 6)]
        self.pv = [0.0, 0.0]
        self.go_at = self.now + 1.0
        self.aim_y = 21.0
        self.look_at = [0, 0]
        self.glimpse = [tuple(self.puck) + (0.0, 0.0)] * 2
        self.clock = None

    def tick(self, now):
        if self.round_over():
            return
        self.run_steps(now)

    def cpu_target(self, side):
        m = self.mallet[side - 1]
        px, py = self.puck
        vx, vy = self.pv
        toward = 1 if side == 1 else -1  # direction of the opponent's goal
        home = 7.0 if side == 1 else 76.0
        mine = px < 42 if side == 1 else px > 42
        if mine and vx * toward > -45:
            if (px - m[0]) * toward > 2:  # behind the puck: drive through it toward the goal
                opp = self.mallet[2 - side][1]  # aim for the corner the other mallet is furthest from
                gx, gy = (90.0 if side == 1 else -6.0), (15.0 if opp > 21 else 27.0) + self.aim_y - 21
                dx, dy = gx - px, gy - py
                d = math.hypot(dx, dy) or 1
                return px + dx / d * 3, py + dy / d * 3
            if not 8 < px < 75:
                # too close to our end wall to get behind: stand clear until it drifts off the rail
                return px + toward * 9, py + (9 if py < 21 else -9)
            return px - toward * 7, py + (5 if m[1] < py else -5)  # go round it
        # defend from a glimpse of the puck that is refreshed only every so often
        if self.now >= self.look_at[side - 1]:
            self.look_at[side - 1] = self.now + (0.35, 0.2, 0.08)[self.level]
            self.glimpse[side - 1] = (px, py, vx, vy)
        px, y, vx, vy = self.glimpse[side - 1]
        if self.level == 2 and vx * toward < 0:  # predict where it crosses our goal line
            t = (home - px) / vx
            y = (y + vy * t - 2.5) % 72
            y = 2.5 + (y if y < 36 else 72 - y)
        return home, min(max(21 + (y - 21) * 0.8, 12), 30)

    def step(self):
        if self.round_winner is not None:
            return
        speed = (26, 38, 50)[self.level]
        for i, side in enumerate((1, 2)):
            m = self.mallet[i]
            ox, oy = m
            if side in self.cpu:
                tx, ty = self.cpu_target(side)
                if self.level == 0:
                    ty += math.sin(self.now * 2.3 + side) * 3
                dx, dy = tx - ox, ty - oy
                d = math.hypot(dx, dy)
                v = min(speed * (0.7 if tx in (7.0, 76.0) else 1), d / STEP)  # slower guarding the goal
                if d > 0:
                    m[0] += dx / d * v * STEP
                    m[1] += dy / d * v * STEP
            else:
                keys = {k for p, k in self.held if p == side}
                dx = ("right" in keys) - ("left" in keys)
                dy = ("down" in keys) - ("up" in keys)
                k = 50 * STEP / (1.41 if dx and dy else 1)
                m[0] += dx * k
                m[1] += dy * k
            lo, hi = (4.0, 38.0) if side == 1 else (46.0, 80.0)
            m[0] = min(max(m[0], lo), hi)
            m[1] = min(max(m[1], 4.0), 37.0)
            self.mvel[i] = [(m[0] - ox) / STEP, (m[1] - oy) / STEP]
        if self.now < self.go_at:
            return
        p, v = self.puck, self.pv
        n = 4
        for _ in range(n):
            p[0] += v[0] * STEP / n
            p[1] += v[1] * STEP / n
            for i in (0, 1):
                m, mv = self.mallet[i], self.mvel[i]
                dx, dy = p[0] - m[0], p[1] - m[1]
                d = math.hypot(dx, dy)
                reach = self.PR + self.MR
                if d < reach:
                    nx, ny = (dx / d, dy / d) if d else ((1.0, 0.0) if i == 0 else (-1.0, 0.0))
                    p[0], p[1] = m[0] + nx * reach, m[1] + ny * reach
                    rel = (v[0] - mv[0]) * nx + (v[1] - mv[1]) * ny
                    if rel < 0:
                        v[0] -= 1.9 * rel * nx
                        v[1] -= 1.9 * rel * ny
                        self.aim_y = 21 + random.uniform(-1, 1) * (6, 3, 1)[self.level]
            # walls last, so a mallet can never push the puck through one
            if p[1] < 1 + self.PR:
                p[1], v[1] = 1 + self.PR, abs(v[1])
            elif p[1] > 40 - self.PR:
                p[1], v[1] = 40 - self.PR, -abs(v[1])
            in_mouth = self.GOAL[0] < p[1] < self.GOAL[1]
            if not in_mouth:
                if p[0] < 1 + self.PR:
                    p[0], v[0] = 1 + self.PR, abs(v[0])
                elif p[0] > 82 - self.PR:
                    p[0], v[0] = 82 - self.PR, -abs(v[0])
            elif p[0] < -2 or p[0] > 85:
                scorer = 2 if p[0] < 0 else 1
                self.serve_to = 3 - scorer
                self.aim_y = 21 + random.uniform(-3, 3)
                return self.end_round(scorer, 1.2)
            for m in self.mallet:  # squeezed against a wall: the mallet gives way
                dx, dy = m[0] - p[0], m[1] - p[1]
                d = math.hypot(dx, dy)
                if 0 < d < self.PR + self.MR:
                    k = (self.PR + self.MR) / d
                    m[0], m[1] = p[0] + dx * k, p[1] + dy * k
        s = math.hypot(*v)
        if s < 2 and not (8 < p[0] < 75 and 5 < p[1] < 36):  # a slow puck on the rail drifts off it
            self.still = getattr(self, "still", 0) + STEP
            if self.still > 1:
                self.still = 0
                dx, dy = 42 - p[0], 21 - p[1]
                d = math.hypot(dx, dy)
                v[0], v[1] = dx / d * 20, dy / d * 20
        else:
            self.still = 0
        f = 0.996 if s < self.MAX else self.MAX / s * 0.996
        v[0] *= f
        v[1] *= f

    def draw(self, c, now):
        g0, g1 = self.GOAL
        c.line(0, 0, c.w - 1, 0)
        c.line(0, 41, c.w - 1, 41)
        for x in (0, c.w - 1):
            c.line(x, 0, x, g0)
            c.line(x, g1, x, 41)
        for y in range(2, 40, 3):
            c.set(41, y)
            c.set(42, y)
        c.text(36 - SMALL.width(str(self.wins[0])), 2, str(self.wins[0]), SMALL)
        c.text(48, 2, str(self.wins[1]), SMALL)
        for i, rows in enumerate((MALLET, MALLET_RING)):
            m = self.mallet[i]
            sprite(c, round(m[0]) - 3, round(m[1]) - 3, rows)
        if self.round_winner is not None:
            msg = "GOAL"
            if int(now * 6) % 2:
                c.text_center(17, msg, NORMAL)
            return
        if now >= self.go_at or int(now * 6) % 2:
            fill(c, round(self.puck[0]) - 1, round(self.puck[1]) - 1, 3, 3)
