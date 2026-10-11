"""Board games with real depth: Chess and Othello (Reversi).

Both play vs the computer or two players, with a computer-vs-computer demo while idle, the way the head-to-head
games in apps/pack_versus.py do (same Versus base, cursor controls and few-ms-per-frame search). The board is
8 x 8 cells of 5 x 5 dots in the middle of the display, with a panel each side for the players.

Chess: full rules (castling, en passant, promotion to a queen, check, checkmate, stalemate, the fifty-move rule,
threefold repetition, dead positions). White is drawn as filled glyphs, black as the same glyphs cut out of a
filled cell. The computer searches with alpha-beta (iterative deepening, captures first) on material plus a
little placement, and always moves within about a second.
Othello: legal moves with all their flips, a forced pass when there's none, the count at the end. The computer
weighs corners and edges and looks a few moves ahead.
"""

import random

from fonts import NORMAL, SMALL

from .arcade import sprite
from .base import P as Param, register
from .games import tag
from .pack_versus import DIRS, INF, WIN, Think, Versus, alphabeta, invert, versus_params

X0, Y0 = 22, 1  # the board's top-left on the display (8 cells of 5 dots, a border round it)


def _cell(i):
    return X0 + i % 8 * 5, Y0 + i // 8 * 5


def _draw_board(c, now, cursor, show_cursor):
    c.rect(X0 - 1, Y0 - 1, 42, 42)
    if show_cursor and int(now * 3) % 3:
        x, y = _cell(cursor[1] * 8 + cursor[0])
        invert(c, x, y, 5, 5)


# ======================================================================================================
# Chess
# ======================================================================================================
# Board: 64 ints, index row * 8 + col, row 0 at the top (black's back rank), white moves up the display.
# White pieces are positive (P N B R Q K = 1..6), black negative. A position is (board, side, castle, ep):
# side 1 white / -1 black to move, castle a bitmask (1 white short, 2 white long, 4 black short, 8 black long),
# ep the square a pawn may be taken en passant on (-1: none). A move is (from, to, promo-or-0).

P, N, B, R, Q, K = 1, 2, 3, 4, 5, 6
VALUE = {P: 100, N: 320, B: 330, R: 500, Q: 900, K: 0}
START = (-R, -N, -B, -Q, -K, -B, -N, -R, -P, -P, -P, -P, -P, -P, -P, -P,
         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
         P, P, P, P, P, P, P, P, R, N, B, Q, K, B, N, R)
START_POS = (START, 1, 15, -1)
WK, WQ, BK, BQ = 1, 2, 4, 8


def _rays():
    out = []
    for sq in range(64):
        x, y = sq % 8, sq // 8
        rays = {}
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            ray, nx, ny = [], x + dx, y + dy
            while 0 <= nx < 8 and 0 <= ny < 8:
                ray.append(ny * 8 + nx)
                nx, ny = nx + dx, ny + dy
            rays[(dx, dy)] = ray
        out.append(rays)
    return out


RAYS = _rays()
ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
ROOK_RAYS = [[r[d] for d in ORTHO] for r in RAYS]
BISHOP_RAYS = [[r[d] for d in DIAG] for r in RAYS]
QUEEN_RAYS = [[r[d] for d in ORTHO + DIAG] for r in RAYS]
KNIGHT = [[(y + dy) * 8 + x + dx for dx, dy in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1))
           if 0 <= x + dx < 8 and 0 <= y + dy < 8] for y in range(8) for x in range(8)]
KING = [[(y + dy) * 8 + x + dx for dx, dy in ORTHO + DIAG if 0 <= x + dx < 8 and 0 <= y + dy < 8]
        for y in range(8) for x in range(8)]
# a little placement: knights and bishops like the middle, pawns like to advance, the king likes the corners early
CENTRE = [max(0, 3 - max(abs(sq % 8 - 3.5), abs(sq // 8 - 3.5)) + 0.5) for sq in range(64)]
PST = {N: [int(8 * v) for v in CENTRE], B: [int(5 * v) for v in CENTRE], Q: [int(2 * v) for v in CENTRE],
       R: [0] * 64, K: [0] * 64, P: [0] * 64}


def attacked(b, sq, by):
    """Is square sq attacked by side `by` (1 white, -1 black)?"""
    x = sq % 8
    if by == 1:
        if sq + 9 < 64 and x < 7 and b[sq + 9] == P:
            return True
        if sq + 7 < 64 and x > 0 and b[sq + 7] == P:
            return True
    else:
        if sq - 7 >= 0 and x < 7 and b[sq - 7] == -P:
            return True
        if sq - 9 >= 0 and x > 0 and b[sq - 9] == -P:
            return True
    n, k = N * by, K * by
    for t in KNIGHT[sq]:
        if b[t] == n:
            return True
    for t in KING[sq]:
        if b[t] == k:
            return True
    r, q, bb = R * by, Q * by, B * by
    for ray in ROOK_RAYS[sq]:
        for t in ray:
            v = b[t]
            if v:
                if v == r or v == q:
                    return True
                break
    for ray in BISHOP_RAYS[sq]:
        for t in ray:
            v = b[t]
            if v:
                if v == bb or v == q:
                    return True
                break
    return False


def king_square(b, side):
    k = K * side
    for i in range(64):
        if b[i] == k:
            return i
    return -1


def in_check(pos):
    b, side = pos[0], pos[1]
    return attacked(b, king_square(b, side), -side)


def pseudo_moves(pos):
    b, side, castle, ep = pos
    out = []
    add = out.append
    for sq in range(64):
        v = b[sq]
        if v * side <= 0:
            continue
        p = abs(v)
        x, y = sq % 8, sq // 8
        if p == P:
            step = -8 if side == 1 else 8
            start, last = (6, 0) if side == 1 else (1, 7)
            t = sq + step
            if 0 <= t < 64 and b[t] == 0:
                add((sq, t, Q if t // 8 == last else 0))
                if y == start and b[t + step] == 0:
                    add((sq, t + step, 0))
            for dx in (-1, 1):
                if 0 <= x + dx < 8:
                    t = sq + step + dx
                    if 0 <= t < 64 and (b[t] * side < 0 or t == ep):
                        add((sq, t, Q if t // 8 == last else 0))
        elif p == N:
            for t in KNIGHT[sq]:
                if b[t] * side <= 0:
                    add((sq, t, 0))
        elif p == K:
            for t in KING[sq]:
                if b[t] * side <= 0:
                    add((sq, t, 0))
            if side == 1 and sq == 60:
                if castle & WK and b[61] == 0 and b[62] == 0 and b[63] == R:
                    add((60, 62, 0))
                if castle & WQ and b[59] == 0 and b[58] == 0 and b[57] == 0 and b[56] == R:
                    add((60, 58, 0))
            elif side == -1 and sq == 4:
                if castle & BK and b[5] == 0 and b[6] == 0 and b[7] == -R:
                    add((4, 6, 0))
                if castle & BQ and b[3] == 0 and b[2] == 0 and b[1] == 0 and b[0] == -R:
                    add((4, 2, 0))
        else:
            rays = ROOK_RAYS[sq] if p == R else BISHOP_RAYS[sq] if p == B else QUEEN_RAYS[sq]
            for ray in rays:
                for t in ray:
                    w = b[t]
                    if w == 0:
                        add((sq, t, 0))
                    else:
                        if w * side < 0:
                            add((sq, t, 0))
                        break
    return out


def apply(pos, move):
    b, side, castle, ep = pos
    f, t, promo = move
    nb = list(b)
    v = nb[f]
    p = abs(v)
    nb[f] = 0
    new_ep = -1
    if p == P:
        if t == ep and b[t] == 0:  # en passant: the pawn taken sits beside the destination
            nb[t + (8 if side == 1 else -8)] = 0
        elif abs(t - f) == 16:
            new_ep = (f + t) // 2
    elif p == K:
        castle &= ~(WK | WQ) if side == 1 else ~(BK | BQ)
        if t - f == 2:  # short
            nb[t + 1], nb[t - 1] = 0, R * side
        elif f - t == 2:  # long
            nb[t - 2], nb[t + 1] = 0, R * side
    for sq, bit in ((63, WK), (56, WQ), (7, BK), (0, BQ)):
        if f == sq or t == sq:
            castle &= ~bit
    nb[t] = promo * side if promo else v
    return (tuple(nb), -side, castle, new_ep)


_MOVES = {}  # position -> legal moves, for the search (children() fills it, the leaf evaluation reads it)


def legal_moves(pos):
    """Moves that don't leave (or put) the mover's king in check; castling also needs the way clear of attack."""
    hit = _MOVES.get(pos)
    if hit is not None:
        return list(hit)
    b, side = pos[0], pos[1]
    out = []
    for m in pseudo_moves(pos):
        f, t, _ = m
        if abs(b[f]) == K and abs(t - f) == 2:
            mid = (f + t) // 2
            if attacked(b, f, -side) or attacked(b, mid, -side):
                continue
        nxt = apply(pos, m)
        if not attacked(nxt[0], king_square(nxt[0], side), -side):
            out.append(m)
    if len(_MOVES) > 50000:
        _MOVES.clear()
    _MOVES[pos] = tuple(out)
    return out


def material(b):
    """Material from white's point of view, with the placement bonuses."""
    s = 0
    for i in range(64):
        v = b[i]
        if v:
            p = abs(v)
            if v > 0:
                s += VALUE[p] + PST[p][i] + (5 * (6 - i // 8) if p == P else 0)
            else:
                s -= VALUE[p] + PST[p][i] + (5 * (i // 8 - 1) if p == P else 0)
    return s


def ch_eval(pos):
    """For the side to move. A position with no legal moves is mate (-WIN) or stalemate (0)."""
    b, side = pos[0], pos[1]
    return material(b) * side


def ch_children(pos):
    moves = legal_moves(pos)
    if not moves:
        return []
    b = pos[0]
    # captures first (most valuable victim), which makes alpha-beta prune far more
    moves.sort(key=lambda m: -(abs(b[m[1]]) * 10 + m[2]))
    return [(m, apply(pos, m)) for m in moves]


def ch_terminal(pos):
    """For a position with no legal moves: -WIN when the side to move is mated, 0 when stalemated."""
    return -WIN if in_check(pos) else 0


def ch_search_eval(pos):
    """Leaves get the material; a node whose moves were generated (and found none) is mate or stalemate."""
    kids = _MOVES.get(pos)
    if kids is not None and not kids:
        return ch_terminal(pos)
    return ch_eval(pos)


def dead_position(b):
    """Nobody can mate: bare kings, or a king and one minor piece against a bare king."""
    men = [abs(v) for v in b if v and abs(v) != K]
    return not men or (len(men) == 1 and men[0] in (N, B))


GLYPH = {  # white: filled shapes
    P: [".....", "..X..", ".XXX.", "..X..", ".XXX."],
    N: [".XX..", "XXXX.", "..XX.", ".XXX.", ".XXX."],
    B: ["..X..", ".X.X.", ".XXX.", "..X..", ".XXX."],
    R: ["X.X.X", "XXXXX", ".XXX.", ".XXX.", "XXXXX"],
    Q: ["X.X.X", ".XXX.", "..X..", ".XXX.", "XXXXX"],
    K: ["..X..", ".XXX.", "X.X.X", "XXXXX", ".XXX."],
}
HOLLOW = {  # black: the same shapes, hollowed out
    P: [".....", "..X..", ".X.X.", "..X..", ".X.X."],
    N: [".XX..", "X..X.", "...X.", ".XX..", ".X.X."],
    B: ["..X..", ".X.X.", ".X.X.", "..X..", ".X.X."],
    R: ["X.X.X", "X...X", ".X.X.", ".X.X.", "XXXXX"],
    Q: ["X.X.X", ".X.X.", "..X..", ".X.X.", "XXXXX"],
    K: ["..X..", ".X.X.", "X.X.X", "X...X", ".XXX."],
}
STYLES = [["hollow", "Black pieces hollow"], ["inverted", "Black pieces on filled cells"]]


def draw_piece(c, x, y, v, style="hollow"):
    """A white piece as a filled glyph; a black one hollowed out, or cut out of a filled cell."""
    if v > 0:
        sprite(c, x, y, GLYPH[v])
    elif style == "inverted":
        for dy, r in enumerate(GLYPH[-v]):
            for dx, ch in enumerate(r):
                c.set(x + dx, y + dy, ch != "X")
    else:
        sprite(c, x, y, HOLLOW[-v])


@register
class Chess(Versus):
    id = "chess"
    title = "Chess"
    description = ("The real thing: castling, en passant, promotion (to a queen), check, mate, stalemate and the draws. "
                   "Arrows move the cursor, A picks a piece and then its square, B cancels. White is filled, black is "
                   "cut out of a filled cell. Player 1 is white in the first game; colours swap each game.")
    params = versus_params(1, label="Games to win", max_rounds=5) + [
        Param("pieces", "select", "hollow", "Pieces", options=STYLES)]
    DEPTH = (1, 3, 4)
    LIMIT = (0.3, 0.6, 1.0)  # seconds the computer may think at each level (it plays the best found so far)
    BUDGET = 0.012  # seconds of search per frame (frames come every 20 ms while it thinks)

    def new_round(self):
        self.pos = START_POS
        self.white = self.first  # the player who has white this game
        self.history = {}
        self.plies = 0  # since the last capture or pawn move
        self.cursor = (4, 6)
        self.sel = None
        self.last = None  # (move, at)
        self.result = ""  # why the game ended
        self.start_turn()

    @property
    def side(self):
        return self.pos[1]

    @property
    def turn(self):
        """The player (1 or 2) whose move it is."""
        return self.white if self.side == 1 else 3 - self.white

    def player_side(self, player):
        return 1 if player == self.white else -1

    def start_turn(self):
        self.moves = legal_moves(self.pos)
        self.sel = None
        self.think = None
        self.turn_at = self.now
        self.check = in_check(self.pos)
        key = (self.pos[0], self.pos[1], self.pos[2])
        self.history[key] = self.history.get(key, 0) + 1
        if not self.moves:
            if self.check:
                self.result = "MATE"
                self.end_round(3 - self.turn, 4)
            else:
                self.result = "STALEMATE"
                self.end_round(0, 4)
        elif self.history[key] >= 3:
            self.result = "REPETITION"
            self.end_round(0, 4)
        elif self.plies >= 100:
            self.result = "50 MOVES"
            self.end_round(0, 4)
        elif dead_position(self.pos[0]):
            self.result = "DEAD"
            self.end_round(0, 4)
        elif self.turn not in self.cpu and not any(m[0] == self.cursor[1] * 8 + self.cursor[0] for m in self.moves):
            i = self.moves[0][0] if self.last is None else min(self.moves, key=lambda m: abs(m[0] - self.last[0][1]))[0]
            self.cursor = (i % 8, i // 8)

    def press(self, key, player):
        if self.round_winner is not None or not self.controls(player, self.turn):
            return
        d = DIRS.get(key)
        if d:
            self.cursor = (min(max(self.cursor[0] + d[0], 0), 7), min(max(self.cursor[1] + d[1], 0), 7))
            return
        if key == "b":
            self.sel = None
            return
        if key != "a":
            return
        sq = self.cursor[1] * 8 + self.cursor[0]
        if self.sel is not None:
            for m in self.moves:
                if m[0] == self.sel and m[1] == sq:
                    return self.play(m)
        if any(m[0] == sq for m in self.moves):
            self.sel = sq
        else:
            self.sel = None

    def play(self, move):
        b = self.pos[0]
        self.plies = 0 if b[move[1]] or abs(b[move[0]]) == P else self.plies + 1
        self.pos = apply(self.pos, move)
        self.last = (move, self.now)
        self.cursor = (move[1] % 8, move[1] // 8)
        self.start_turn()

    def tick(self, now):
        if self.round_over():
            return
        if self.turn not in self.cpu:
            return
        if self.think is None:
            pos, depth = self.pos, self.DEPTH[self.level]
            self.think = Think(lambda t: alphabeta(t, pos, ch_children, ch_search_eval, depth), self.LIMIT[self.level])
        if self.think.run(self.BUDGET) and now - self.turn_at > 0.4:
            move = self.think.best
            if self.level == 0 and random.random() < 0.3:
                move = random.choice(self.moves)
            self.think = None
            self.play(move)

    def captured(self):
        """(white's material lead in pawns) as text for the panels: "+3" for the side ahead."""
        s = 0
        for v in self.pos[0]:
            if v:
                s += VALUE[abs(v)] if v > 0 else -VALUE[abs(v)]
        return s

    def draw(self, c, now):
        b = self.pos[0]
        human = self.turn not in self.cpu and self.round_winner is None
        _draw_board(c, now, self.cursor, human)
        blink = int(now * 4) % 2
        just = self.last if self.last and now - self.last[1] < 0.8 else None
        for i in range(64):
            x, y = _cell(i)
            v = b[i]
            if v:
                if just and just[0][1] == i and blink:
                    continue
                draw_piece(c, x, y, v, self.p["pieces"])
        if human and self.sel is not None:
            x, y = _cell(self.sel)
            c.rect(x, y, 5, 5, v=blink)
            if blink:
                for m in self.moves:
                    if m[0] == self.sel:
                        tx, ty = _cell(m[1])
                        c.rect(tx + 1, ty + 1, 3, 3)
        lead = self.captured()
        for player, px in ((1, 0), (2, 63)):
            side = self.player_side(player)
            c.text_center(1, self.name(player), SMALL, x0=px, w=21)
            if self.round_winner is None and self.turn == player:
                c.line(px + 6, 7, px + 14, 7)
            draw_piece(c, px + 8, 10, K * side, self.p["pieces"])
            mine = lead * side
            c.text_center(18, f"+{mine // 100}" if mine >= 100 else "", SMALL, x0=px, w=21)
            if int(self.p["rounds"]) > 1:
                c.text_center(25, f"WON {self.wins[player - 1]}", SMALL, x0=px, w=21)
            if self.round_winner is None and self.check and self.turn == player and blink:
                c.text_center(32, "CHECK", SMALL, x0=px, w=21)
            if self.round_winner is not None and blink:
                if self.round_winner == 0:
                    c.text_center(32, "DRAW", SMALL, x0=px, w=21)
                elif self.round_winner == player:
                    c.text_center(32, "WINS", SMALL, x0=px, w=21)
        if self.round_winner is not None and self.result:
            tag(c, None, 37, self.result)

    def describe(self):
        if self.state != "playing":
            return f"{self.title} ({self.state})"
        who = "white" if self.side == 1 else "black"
        return f"{self.title} ({self.name(self.turn)} to move, {who}{', check' if self.check else ''})"


# ======================================================================================================
# Othello
# ======================================================================================================
# Board: 64 ints, 1 black (player 1, moves first), -1 white. A position is (board, side).

OT_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
OT_WEIGHT = [120, -20, 20, 5, 5, 20, -20, 120,
             -20, -40, -5, -5, -5, -5, -40, -20,
             20, -5, 15, 3, 3, 15, -5, 20,
             5, -5, 3, 3, 3, 3, -5, 5,
             5, -5, 3, 3, 3, 3, -5, 5,
             20, -5, 15, 3, 3, 15, -5, 20,
             -20, -40, -5, -5, -5, -5, -40, -20,
             120, -20, 20, 5, 5, 20, -20, 120]
DISC = {1: [".XXX.", "XXXXX", "XXXXX", "XXXXX", ".XXX."], -1: [".XXX.", "X...X", "X...X", "X...X", ".XXX."]}


def ot_flips(b, sq, side):
    """The discs a move on sq would turn (empty when it's no move)."""
    if b[sq]:
        return []
    x, y = sq % 8, sq // 8
    out = []
    for dx, dy in OT_DIRS:
        run, nx, ny = [], x + dx, y + dy
        while 0 <= nx < 8 and 0 <= ny < 8 and b[ny * 8 + nx] == -side:
            run.append(ny * 8 + nx)
            nx, ny = nx + dx, ny + dy
        if run and 0 <= nx < 8 and 0 <= ny < 8 and b[ny * 8 + nx] == side:
            out += run
    return out


def ot_moves(b, side):
    return {sq: f for sq in range(64) if not b[sq] for f in [ot_flips(b, sq, side)] if f}


def ot_apply(b, sq, flips, side):
    nb = list(b)
    nb[sq] = side
    for f in flips:
        nb[f] = side
    return tuple(nb)


def ot_children(pos):
    b, side = pos
    moves = ot_moves(b, side)
    if moves:
        return [(sq, (ot_apply(b, sq, f, side), -side)) for sq, f in moves.items()]
    if ot_moves(b, -side):
        return [(None, (b, -side))]  # a pass
    return []


def ot_eval(pos):
    b, side = pos
    empties = b.count(0)
    mine = sum(1 for v in b if v == side)
    theirs = 64 - empties - mine
    if not ot_moves(b, side) and not ot_moves(b, -side):
        return WIN if mine > theirs else -WIN if mine < theirs else 0
    if empties <= 8:
        return (mine - theirs) * 10
    s = sum(OT_WEIGHT[i] * b[i] for i in range(64)) * side
    s += 4 * (len(ot_moves(b, side)) - len(ot_moves(b, -side)))
    return s


@register
class Othello(Versus):
    id = "othello"
    title = "Othello"
    description = ("Reversi. Arrows move the cursor, A plays a disc where it flips something, B does nothing. "
                   "Player 1 is black (filled) and moves first; no move means you pass. Most discs at the end wins.")
    params = versus_params(1, label="Games to win", max_rounds=5)
    DEPTH = (1, 2, 4)

    def new_round(self):
        b = [0] * 64
        b[27], b[36], b[28], b[35] = -1, -1, 1, 1
        self.board = tuple(b)
        self.black = self.first
        self.side = 1
        self.cursor = (2, 3)
        self.passed = None  # (player, at) who had to pass
        self.flipped = ()  # (squares, at) just turned
        self.start_turn()

    @property
    def turn(self):
        return self.black if self.side == 1 else 3 - self.black

    def start_turn(self):
        self.moves = ot_moves(self.board, self.side)
        self.think = None
        self.turn_at = self.now
        if not self.moves:
            if ot_moves(self.board, -self.side):
                self.passed = (self.turn, self.now)
                self.side = -self.side
                return self.start_turn()
            black = sum(1 for v in self.board if v == 1)
            white = sum(1 for v in self.board if v == -1)
            winner = 0 if black == white else (self.black if black > white else 3 - self.black)
            self.end_round(winner, 4)
        elif self.turn not in self.cpu and self.cursor[1] * 8 + self.cursor[0] not in self.moves:
            i = min(self.moves)
            self.cursor = (i % 8, i // 8)

    def press(self, key, player):
        if self.round_winner is not None or not self.controls(player, self.turn):
            return
        d = DIRS.get(key)
        if d:
            self.cursor = (min(max(self.cursor[0] + d[0], 0), 7), min(max(self.cursor[1] + d[1], 0), 7))
            return
        if key == "a":
            sq = self.cursor[1] * 8 + self.cursor[0]
            if sq in self.moves:
                self.play(sq)

    def play(self, sq):
        flips = self.moves[sq]
        self.board = ot_apply(self.board, sq, flips, self.side)
        self.flipped = (tuple(flips), self.now)
        self.cursor = (sq % 8, sq // 8)
        self.side = -self.side
        self.start_turn()

    def tick(self, now):
        if self.round_over():
            return
        if self.turn not in self.cpu:
            return
        if self.think is None:
            pos, depth = (self.board, self.side), self.DEPTH[self.level]
            self.think = Think(lambda t: alphabeta(t, pos, ot_children, ot_eval, depth), 1.5)
        if self.think.run() and now - self.turn_at > 0.5:
            sq = self.think.best
            if self.level == 0 and random.random() < 0.3:
                sq = random.choice(list(self.moves))
            self.think = None
            self.play(sq)

    def draw(self, c, now):
        human = self.turn not in self.cpu and self.round_winner is None
        _draw_board(c, now, self.cursor, human)
        blink = int(now * 4) % 2
        fresh = set(self.flipped[0]) if self.flipped and now - self.flipped[1] < 0.6 else set()
        for i in range(64):
            x, y = _cell(i)
            v = self.board[i]
            if v:
                sprite(c, x, y, DISC[-v if i in fresh and blink else v])
            elif human and i in self.moves and blink:
                c.set(x + 2, y + 2)
        counts = {1: sum(1 for v in self.board if v == 1), -1: sum(1 for v in self.board if v == -1)}
        for player, px in ((1, 0), (2, 63)):
            side = 1 if player == self.black else -1
            c.text_center(1, self.name(player), SMALL, x0=px, w=21)
            if self.round_winner is None and self.turn == player:
                c.line(px + 6, 7, px + 14, 7)
            sprite(c, px + 8, 10, DISC[side])
            c.text_center(18, str(counts[side]), NORMAL, x0=px, w=21)
            if int(self.p["rounds"]) > 1:
                c.text_center(28, f"WON {self.wins[player - 1]}", SMALL, x0=px, w=21)
            if self.passed and self.passed[0] == player and now - self.passed[1] < 2 and blink:
                c.text_center(35, "PASS", SMALL, x0=px, w=21)
            if self.round_winner in (player, 0) and blink:
                c.text_center(35, "WINS" if self.round_winner else "DRAW", SMALL, x0=px, w=21)

    def describe(self):
        if self.state != "playing":
            return f"{self.title} ({self.state})"
        black = sum(1 for v in self.board if v == 1)
        white = sum(1 for v in self.board if v == -1)
        return f"{self.title} ({self.name(self.turn)} to move, {black}-{white})"
