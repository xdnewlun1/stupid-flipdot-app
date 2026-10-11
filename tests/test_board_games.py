"""Chess and Othello (apps/pack_board.py): the chess rules (perft counts, castling, en passant, promotion, check,
mate, stalemate, draws), the computer's moves are legal and quick, whole computer games end, Othello's flips,
passes and count, and both apps render and take cursor input.
Run: python3 -m pytest -q tests/test_board_games.py"""

import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
from apps import pack_board as pb  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402


class Store:
    def __init__(self):
        self.settings = {"visualizer": {"source": "off"}}
        self.dir = "/nonexistent"

    def _load(self, name, default):
        return default

    def _save(self, name, data):
        pass


def fen(f):
    b = []
    for r in f.split("/"):
        for ch in r:
            if ch.isdigit():
                b += [0] * int(ch)
            else:
                p = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5, "k": 6}[ch.lower()]
                b.append(p if ch.isupper() else -p)
    return tuple(b)


def perft(pos, d):
    if d == 0:
        return 1
    return sum(perft(pb.apply(pos, m), d - 1) for m in pb.legal_moves(pos))


def make(app_id, **params):
    random.seed(3)
    a = apps.create(Context(84, 42, Store()), {"app": app_id, "params": params})
    return a


def frame(a, now):
    c = Canvas(84, 42)
    a.render(c, now)
    return c


def start_2p(a, now=10.0):
    frame(a, now)
    a.input("a", "down", 1)
    frame(a, now + 0.05)
    return a


class ChessRules(unittest.TestCase):
    def test_perft_from_the_start_and_two_tricky_positions(self):
        self.assertEqual([perft(pb.START_POS, d) for d in (1, 2, 3)], [20, 400, 8902])
        kiwi = (fen("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R"), 1, 15, -1)
        self.assertEqual([perft(kiwi, d) for d in (1, 2)], [48, 2039])  # castling, pins, promotions, en passant
        p3 = (fen("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8"), 1, 0, -1)
        self.assertEqual([perft(p3, d) for d in (1, 2, 3)], [14, 191, 2812])

    def test_castling_moves_the_rook_and_needs_a_clear_safe_path(self):
        pos = (fen("r3k2r/8/8/8/8/8/8/R3K2R"), 1, 15, -1)
        moves = pb.legal_moves(pos)
        self.assertIn((60, 62, 0), moves)
        self.assertIn((60, 58, 0), moves)
        after = pb.apply(pos, (60, 62, 0))
        self.assertEqual((after[0][62], after[0][61], after[0][63]), (pb.K, pb.R, 0))
        self.assertEqual(after[2] & 3, 0)  # white's rights are spent
        self.assertEqual(after[2] & 12, 12)
        blocked = (fen("r3k2r/8/8/8/8/8/8/R3KB1R"), 1, 15, -1)
        self.assertNotIn((60, 62, 0), pb.legal_moves(blocked))
        attacked = (fen("r3k2r/8/8/8/8/8/5r2/R3K2R"), 1, 15, -1)  # f1 is attacked: no short castling
        self.assertNotIn((60, 62, 0), pb.legal_moves(attacked))
        self.assertIn((60, 58, 0), pb.legal_moves(attacked))
        check = (fen("r3k2r/8/8/8/8/8/4r3/R3K2R"), 1, 15, -1)  # in check: no castling at all
        ms = pb.legal_moves(check)
        self.assertNotIn((60, 62, 0), ms)
        self.assertNotIn((60, 58, 0), ms)
        rook_moved = pb.apply(pos, (63, 55, 0))
        self.assertFalse(rook_moved[2] & pb.WK)

    def test_en_passant_and_promotion(self):
        pos = (fen("4k3/8/8/8/5p2/8/4P3/4K3"), 1, 0, -1)
        pos = pb.apply(pos, (52, 36, 0))  # e2-e4 beside the black pawn on f4
        self.assertEqual(pos[3], 44)
        self.assertIn((37, 44, 0), pb.legal_moves(pos))
        after = pb.apply(pos, (37, 44, 0))
        self.assertEqual(after[0][36], 0)  # the white pawn is gone
        self.assertEqual(after[0][44], -pb.P)
        promo = (fen("4k3/1P6/8/8/8/8/8/4K3"), 1, 0, -1)
        ms = [m for m in pb.legal_moves(promo) if m[0] == 9]
        self.assertEqual(ms, [(9, 1, pb.Q)])
        self.assertEqual(pb.apply(promo, ms[0])[0][1], pb.Q)
        capture_promo = (fen("r3k3/1P6/8/8/8/8/8/4K3"), 1, 0, -1)
        self.assertIn((9, 0, pb.Q), pb.legal_moves(capture_promo))

    def test_check_mate_stalemate_and_pins(self):
        mate = (fen("r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR"), -1, 15, -1)
        self.assertTrue(pb.in_check(mate))
        self.assertEqual(pb.legal_moves(mate), [])
        self.assertEqual(pb.ch_terminal(mate), -pb.WIN)
        stale = (fen("7k/5Q2/6K1/8/8/8/8/8"), -1, 0, -1)
        self.assertFalse(pb.in_check(stale))
        self.assertEqual(pb.legal_moves(stale), [])
        self.assertEqual(pb.ch_terminal(stale), 0)
        pinned = (fen("4k3/4r3/8/8/8/8/4B3/4K3"), 1, 0, -1)  # the bishop can't leave the e-file
        self.assertFalse([m for m in pb.legal_moves(pinned) if m[0] == 52 and m[1] % 8 != 4])
        self.assertTrue(pb.dead_position(fen("4k3/8/8/8/8/8/8/4KB2")))
        self.assertFalse(pb.dead_position(fen("4k3/8/8/8/8/8/8/4KR2")))

    def test_the_computer_finds_mate_in_one_and_moves_legally_in_time(self):
        pos = (fen("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR"), 1, 15, -1)
        from apps.pack_versus import Think, alphabeta
        t = Think(lambda th: alphabeta(th, pos, pb.ch_children, pb.ch_search_eval, 2), 5.0)
        t0 = time.perf_counter()
        while not t.run(0.05):
            pass
        self.assertEqual(t.best, (31, 13, 0))  # Qxf7#
        self.assertLess(time.perf_counter() - t0, 3.0)
        # a busy middlegame at full depth stays within the app's time limit and is legal
        mid = (fen("r2q1rk1/ppp2ppp/2np1n2/2b1p3/2B1P1b1/2NP1N2/PPP2PPP/R1BQ1RK1"), 1, 0, -1)
        t = Think(lambda th: alphabeta(th, mid, pb.ch_children, pb.ch_search_eval, 3), pb.Chess.LIMIT[2])
        t0 = time.perf_counter()
        while not t.run(pb.Chess.BUDGET):
            pass
        self.assertLess(time.perf_counter() - t0, pb.Chess.LIMIT[2] + 0.5)
        self.assertIn(t.best, pb.legal_moves(mid))


class ChessApp(unittest.TestCase):
    def test_cursor_select_and_move_with_two_players(self):
        a = start_2p(make("chess", mode="2p"))
        self.assertEqual(a.state, "playing")
        self.assertEqual(a.pos, pb.START_POS)
        a.input("a", "down", 1)  # the cursor starts on e2
        self.assertEqual(a.sel, 52)
        a.input("up", "down", 1)
        a.input("up", "down", 1)
        a.input("a", "down", 1)  # e4
        self.assertEqual(a.pos[0][36], pb.P)
        self.assertEqual(a.side, -1)
        self.assertEqual(a.turn, 2)
        a.input("a", "down", 1)  # player 1 can play black too until player 2 presses something
        self.assertIsNotNone(a.sel)
        a.input("b", "down", 1)
        self.assertIsNone(a.sel)
        a.input("a", "down", 2)
        self.assertIsNotNone(a.sel)
        a.input("a", "down", 1)  # now player 1 can't move black
        self.assertEqual(a.side, -1)
        c = frame(a, 11.0)
        self.assertGreater(sum(c.px), 100)
        self.assertIn("to move", a.describe())

    def test_a_computer_game_plays_to_the_end(self):
        a = make("chess", difficulty="easy")
        now = 100.0
        frame(a, now)  # title -> demo, CPU vs CPU
        self.assertTrue(a.demo)
        moves, last = 0, None
        while now < 100 + 1500 and not a.demo_over_at:
            frame(a, now)
            if a.pos != last:
                last, moves = a.pos, moves + 1
            now += a.interval
        self.assertTrue(a.demo_over_at)
        self.assertIn(a.result, ("MATE", "STALEMATE", "REPETITION", "50 MOVES", "DEAD"))
        self.assertGreater(moves, 10)

    def test_check_and_mate_are_announced_and_colours_swap_between_games(self):
        a = start_2p(make("chess", mode="2p", rounds=2))
        self.assertEqual(a.white, 1)
        a.pos = (fen("r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR"), -1, 15, -1)
        a.start_turn()
        self.assertEqual(a.result, "MATE")
        self.assertEqual(a.round_winner, 1)
        frame(a, 20.0)
        frame(a, 25.0)  # the pause is over: game 2
        self.assertEqual(a.round, 2)
        self.assertEqual(a.white, 2)
        self.assertEqual(a.pos, pb.START_POS)
        self.assertEqual(a.turn, 2)


class OthelloRules(unittest.TestCase):
    def test_flips_moves_and_passes(self):
        b = [0] * 64
        b[27], b[36], b[28], b[35] = -1, -1, 1, 1
        b = tuple(b)
        self.assertEqual(sorted(pb.ot_moves(b, 1)), [19, 26, 37, 44])
        self.assertEqual(pb.ot_flips(b, 19, 1), [27])
        after = pb.ot_apply(b, 19, [27], 1)
        self.assertEqual(after[19], 1)
        self.assertEqual(after[27], 1)
        self.assertEqual(pb.ot_flips(after, 19, 1), [])
        row = [0] * 64
        row[0], row[1], row[2], row[3] = 1, -1, -1, 0
        self.assertEqual(sorted(pb.ot_flips(tuple(row), 3, 1)), [1, 2])
        # white can't move, black can: the only child is a pass
        stuck = [1] * 64
        stuck[1:7] = [-1] * 6
        stuck[7] = 0
        kids = pb.ot_children((tuple(stuck), -1))
        self.assertEqual(kids, [(None, (tuple(stuck), 1))])
        full = tuple([1] * 40 + [-1] * 24)
        self.assertEqual(pb.ot_children((full, 1)), [])
        self.assertEqual(pb.ot_eval((full, 1)), pb.WIN)
        self.assertEqual(pb.ot_eval((full, -1)), -pb.WIN)

    def test_corners_are_worth_more(self):
        b = [0] * 64
        b[27], b[36], b[28], b[35] = -1, -1, 1, 1
        corner = list(b)
        corner[0] = 1
        middle = list(b)
        middle[19] = 1
        self.assertGreater(pb.ot_eval((tuple(corner), 1)), pb.ot_eval((tuple(middle), 1)))


class OthelloApp(unittest.TestCase):
    def test_play_pass_and_the_count(self):
        a = start_2p(make("othello", mode="2p"))
        self.assertEqual(a.side, 1)
        self.assertEqual(a.cursor, (2, 3))  # on a legal square
        a.input("a", "down", 1)
        self.assertEqual(sum(1 for v in a.board if v == 1), 4)
        self.assertEqual(a.side, -1)
        a.input("up", "down", 1)
        a.input("up", "down", 1)
        a.input("up", "down", 1)
        before = a.board
        a.input("a", "down", 1)  # not a legal square: nothing happens
        self.assertEqual(a.board, before)
        frame(a, 11.0)
        self.assertIn("to move", a.describe())
        # a forced pass: white (player 2) has no move, so it's black's turn again
        stuck = [1] * 64
        stuck[1:7] = [-1] * 6
        stuck[7] = 0
        a.board, a.side = tuple(stuck), -1
        a.start_turn()
        self.assertEqual(a.side, 1)
        self.assertEqual(a.passed[0], 2)
        # the end: most discs wins
        a.round_winner = None
        a.board, a.side = tuple([1] * 40 + [-1] * 24), 1
        a.start_turn()
        self.assertEqual(a.round_winner, 1)

    def test_a_computer_game_plays_to_the_end(self):
        a = make("othello", difficulty="hard")
        now = 100.0
        frame(a, now)
        self.assertTrue(a.demo)
        while now < 100 + 600 and not a.demo_over_at:
            frame(a, now)
            now += a.interval
        self.assertTrue(a.demo_over_at)
        self.assertEqual(a.board.count(0) + sum(1 for v in a.board if v) , 64)
        self.assertIn(a.round_winner, (0, 1, 2))


class VersusComputer(unittest.TestCase):
    def test_the_computer_answers_a_human_move(self):
        for app_id, moves in (("chess", ("a", "up", "up", "a")), ("othello", ("a",))):
            a = make(app_id, difficulty="medium")
            now = t0 = time.time()  # input stamps real time, so the clock here is real time moved on by hand
            frame(a, now)
            a.input("a", "down", 1)  # takes over from the demo: a fresh game vs the computer
            frame(a, now + 0.05)
            self.assertFalse(a.demo)
            self.assertEqual(a.cpu, {2})
            for k in moves:
                a.input(k, "down", 1)
            self.assertEqual(a.turn, 2)
            before = getattr(a, "pos", None) or a.board
            while now < t0 + 4.0 and (getattr(a, "pos", None) or a.board) == before:
                frame(a, now)
                now += a.interval
            self.assertNotEqual(getattr(a, "pos", None) or a.board, before, app_id)  # the computer moved...
            self.assertEqual(a.turn, 1, app_id)  # ...and it's the human's turn again
            self.assertLess(now - t0, 3.0, app_id)


class Registry(unittest.TestCase):
    def test_both_games_are_registered_with_the_usual_options(self):
        for app_id in ("chess", "othello"):
            cls = apps.REGISTRY[app_id]
            names = [p["name"] for p in cls.params]
            for want in ("mode", "difficulty", "rounds", "react_fx"):
                self.assertIn(want, names, app_id)
            self.assertEqual(cls.category, "Games")


if __name__ == "__main__":
    unittest.main()
