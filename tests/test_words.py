"""Word games (apps/pack_words.py): Wordle's feedback and daily word, Hangman's flow, Boggle's tracing, validation
and scoring, and that all three draw, take the gamepad and play their demos.
Run: python3 -m pytest -q tests/test_words.py"""

import json
import os
import random
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402
from apps import pack_words as W  # noqa: E402
from apps import scores  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

T0 = time.time()  # games read the real clock on input, so tests run near it (reset before each test below)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _near_now():
    """A full test run reaches this file minutes after import: keep T0 near the real clock for each test."""
    global T0
    T0 = time.time()
    yield


class Store:
    def __init__(self):
        self.saved = {}
        self.settings = {}

    def _load(self, name, default):
        return json.loads(json.dumps(self.saved.get(name, default)))

    def _save(self, name, data):
        self.saved[name] = json.loads(json.dumps(data))


def make(gid, store=None, **params):
    return apps.create(Context(84, 42, store or Store()), {"app": gid, "params": params})


def frame(app, now):
    c = Canvas(84, 42)
    app.render(c, now)
    return c


def lit(c):
    return sum(c.px)


def start(app, now):
    """Off the title card into a game, with the clock at `now` (the puzzle base reads time.time())."""
    with mock.patch("time.time", lambda: now):
        frame(app, now)
        app.input("a")
    app.now = now
    return app


def demo_max(app, seconds, fn):
    """Let the title card run into the demo for `seconds`; -> the biggest fn(app) seen while the demo played."""
    now = time.time()
    best = 0
    for _ in range(int((app.CARD + seconds) / app.interval)):
        frame(app, now)
        now += app.interval
        if app.demo:
            best = max(best, fn(app))
    return best


class WordLists(unittest.TestCase):
    def test_lists_load(self):
        self.assertGreater(len(W.common_words()), 2500)
        self.assertGreater(len(W.answers()), 800)
        self.assertTrue(all(len(w) == 5 for w in W.answers()))
        self.assertTrue(W.is_word("crane") and W.is_word("ELEPHANT"))
        self.assertFalse(W.is_word("zzqxv"))
        for cat in W.CATEGORIES:
            self.assertGreater(len(W.hangman_words()[cat]), 30)


class WordleTest(unittest.TestCase):
    def test_feedback_including_repeated_letters(self):
        self.assertEqual(W.wordle_feedback("crane", "crane"), ["G"] * 5)
        self.assertEqual(W.wordle_feedback("crane", "enact"), ["Y", "Y", "G", "Y", "."])
        # the secret has one E: the first E in the guess lights, the second doesn't
        self.assertEqual(W.wordle_feedback("crane", "eerie"), [".", ".", "Y", ".", "G"])
        self.assertEqual(W.wordle_feedback("crane", "eeeee"), [".", ".", ".", ".", "G"])
        # a letter in the right place is counted before one in the wrong place
        self.assertEqual(W.wordle_feedback("abbey", "babes"), ["Y", "Y", "G", "G", "."])
        self.assertEqual(W.wordle_feedback("abbey", "keeps"), [".", "Y", ".", ".", "."])
        self.assertEqual(W.wordle_feedback("level", "lllll"), ["G", ".", ".", ".", "G"])

    def test_daily_word_is_the_same_all_day_and_differs_between_days(self):
        self.assertEqual(W.daily_word("2026-10-11"), W.daily_word("2026-10-11"))
        self.assertIn(W.daily_word("2026-10-11"), W.answers())
        words = {W.daily_word(f"2026-10-{d:02d}") for d in range(1, 31)}
        self.assertGreater(len(words), 25)
        noon, night = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1)), time.mktime((2026, 10, 11, 23, 59, 0, 0, 0, -1))
        self.assertEqual(W.day_key(noon), W.day_key(night))
        self.assertNotEqual(W.day_key(noon), W.day_key(night + 120))

    def test_typing_checking_and_winning(self):
        st = Store()
        g = start(make("wordle", st, mode="random"), (T0 + 0.0))
        g.secret = "crane"
        # type S-T-A-R-E on the keyboard: move to each key and press A
        for ch in "STARE":
            r = next(i for i, row in enumerate(W.KEYS) if ch in row)
            while g.ky != r:
                g.input("down")
            while g.kx != W.KEYS[r].index(ch):
                g.input("right")
            g.input("a")
        self.assertEqual(g.cur, "stare")
        g.input("b")
        self.assertEqual(g.cur, "star")
        g.input("a")  # E again (the cursor is still on E)
        # over to OK and check
        g.ky = 2
        g.kx = len(W.KEYS[2])
        g.input("a")
        self.assertEqual(len(g.guesses), 1)
        self.assertEqual(g.guesses[0], ("stare", [".", ".", "G", "Y", "G"]))
        self.assertEqual(g.letter_states(), {"S": ".", "T": ".", "A": "G", "R": "Y", "E": "G"})
        g.cur = "zzqxv"
        g.submit()
        self.assertEqual(len(g.guesses), 1)
        self.assertEqual(g.msg[0], "NOT A WORD")
        g.cur = "cran"
        g.submit()
        self.assertEqual(g.msg[0], "TOO SHORT")
        g.cur = "crane"
        g.submit()
        self.assertTrue(g.won)
        self.assertEqual(st.saved[W.WORDLE_FILE]["won"], 1)
        c = frame(g, (T0 + 0.5))
        self.assertGreater(lit(c), 100)
        g.tick((T0 + 3.0))
        self.assertEqual(g.state, "over")
        self.assertEqual(g.result[1], "CRANE")

    def test_losing_after_six(self):
        g = start(make("wordle", mode="random"), (T0 + 0.0))
        g.secret = "crane"
        for w in ("stare", "stare", "stare", "stare", "stare", "stare"):
            g.cur = w
            g.submit()
        self.assertEqual(len(g.guesses), 6)
        self.assertFalse(g.won)
        self.assertTrue(g.done_at)

    def test_daily_once_a_day_with_a_streak(self):
        st = Store()
        day1 = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
        g = start(make("wordle", st, mode="daily"), day1)
        self.assertTrue(g.daily)
        self.assertEqual(g.secret, W.daily_word("2026-10-11"))
        g.cur = g.secret
        g.submit()
        self.assertEqual(st.saved[W.WORDLE_FILE]["streak"], 1)
        self.assertIn("2026-10-11", st.saved[W.WORDLE_FILE]["days"])
        # the same day again: it's done; the result shows and the next game is a random word
        g2 = start(make("wordle", st, mode="daily"), day1 + 600)
        self.assertTrue(g2.done_at and g2.won and not g2.daily)
        self.assertEqual(g2.msg[0], "DONE TODAY")
        # the next day: a new daily word, won again -> streak 2; a day missed resets it
        day2 = day1 + 86400
        g3 = start(make("wordle", st, mode="daily"), day2)
        self.assertTrue(g3.daily)
        g3.cur = g3.secret
        g3.submit()
        self.assertEqual(st.saved[W.WORDLE_FILE]["streak"], 2)
        g4 = start(make("wordle", st, mode="daily"), day2 + 3 * 86400)
        g4.cur = g4.secret
        g4.submit()
        self.assertEqual(st.saved[W.WORDLE_FILE]["streak"], 1)
        self.assertEqual(st.saved[W.WORDLE_FILE]["best_streak"], 2)

    def test_demo_solves_words(self):
        random.seed(5)
        g = make("wordle", mode="random")
        guessed = demo_max(g, 60, lambda a: len(a.guesses))
        self.assertTrue(g.demo or g.state == "title")
        self.assertGreaterEqual(guessed, 2)
        g.input("a")  # a player takes over with a fresh game
        self.assertFalse(g.demo)
        self.assertEqual(g.guesses, [])


class HangmanTest(unittest.TestCase):
    def test_guessing_misses_and_winning(self):
        st = Store()
        g = start(make("hangman", st, category="animals"), (T0 + 0.0))
        self.assertEqual(g.category, "animals")
        g.word = "zebra"
        self.assertEqual(g.revealed(), "_____")
        g.guess("e")
        self.assertEqual(g.revealed(), "_e___")
        g.guess("x")
        self.assertEqual(g.misses, 1)
        g.guess("x")  # already guessed: nothing
        self.assertEqual(g.misses, 1)
        for ch in "zbra":
            g.guess(ch)
        self.assertEqual(g.score, 1)
        self.assertTrue(g.solved_at)
        g.tick((T0 + 2.0))  # the next word comes
        self.assertFalse(g.solved_at)
        self.assertEqual(g.misses, 0)
        self.assertNotEqual(g.word, "zebra")
        # the cursor moves over the letters; A guesses the one under it
        g.input("right")
        g.input("down")
        self.assertEqual((g.cy, g.cx), (1, 1))
        g.input("a")
        self.assertIn("o", g.guessed)
        self.assertGreater(lit(frame(g, (T0 + 2.5))), 80)

    def test_losing_files_the_score(self):
        st = Store()
        g = start(make("hangman", st, category="food"), (T0 + 0.0))
        g.word = "pizza"
        g.guess("p")
        g.guess("i")
        g.guess("z")
        g.guess("a")
        self.assertEqual(g.score, 1)
        g.tick((T0 + 2.0))
        g.word = "mango"
        for ch in "qwxyvkj":
            g.guess(ch)
        self.assertTrue(g.lost_at)
        self.assertEqual(scores.best(st, "hangman")["score"], 1)
        c = frame(g, (T0 + 2.5))
        self.assertGreater(lit(c), 100)
        g.tick((T0 + 5.0))
        self.assertEqual(g.state, "over")

    def test_demo_plays(self):
        random.seed(2)
        g = make("hangman")
        self.assertGreater(demo_max(g, 30, lambda a: len(a.guessed)), 3)


class BoggleTest(unittest.TestCase):
    BOARD = [list("TEAM"), list("SQRO"), list("LINX"), list("DEWP")]

    def test_dice_and_points(self):
        b = W.roll_dice(random.Random(1))
        self.assertEqual(len(b), 4)
        self.assertTrue(all(len(r) == 4 and all(ch.isupper() for ch in r) for r in b))
        self.assertEqual([W.boggle_points(w) for w in ("tea", "team", "steam", "stream", "streams", "streamers")],
                         [1, 1, 2, 3, 5, 11])
        self.assertEqual(W.boggle_points("to"), 0)
        self.assertEqual(W.spell(self.BOARD, [(1, 1), (2, 2), (1, 2)]), "quni")

    def test_finding_words(self):
        found = W.find_words(self.BOARD)
        self.assertIn("team", found)
        self.assertIn("tea", found)
        self.assertNotIn("meat", found)  # M and E aren't touching
        for w, path in found.items():
            self.assertEqual(W.spell(self.BOARD, path), w)
            self.assertEqual(len(set(path)), len(path))
            self.assertTrue(all(W.adjacent(a, b) for a, b in zip(path, path[1:])))

    def test_tracing_and_scoring(self):
        st = Store()
        g = start(make("boggle", st, minutes="2"), (T0 + 0.0))
        g.board = [list(r) for r in self.BOARD]
        g.input("a")            # T
        g.input("right")
        g.input("a")            # E
        g.input("right")
        g.input("a")            # A
        self.assertEqual(g.word(), "tea")
        g.input("down")
        g.input("down")
        g.input("a")            # N (2,2): not next to A -> refused
        self.assertEqual(g.word(), "tea")
        self.assertEqual(g.msg[0], "NOT NEXT TO IT")
        g.input("up")
        g.input("up")
        g.input("right")
        g.input("a")            # M: next to A
        g.input("left")
        g.input("a")            # A again: used
        self.assertEqual(g.msg[0], "USED")
        g.input("right")
        g.input("a")            # A on the last letter checks the word
        self.assertEqual(g.found, ["team"])
        self.assertEqual(g.score, 1)
        self.assertEqual(g.path, [])
        g.input("b")            # nothing to take back
        g.input("left")
        g.input("a")
        g.input("a")            # "a" alone: too short
        self.assertEqual(g.msg[0], "TOO SHORT")
        # a duplicate and a non-word score nothing
        g.path = [(0, 0), (1, 0), (2, 0), (3, 0)]
        g.submit()
        self.assertEqual(g.msg[0], "GOT THAT ONE")
        g.path = [(3, 0), (2, 1), (3, 2)]  # M-R-X
        g.submit()
        self.assertEqual(g.msg[0], "NOT A WORD")
        self.assertEqual(g.score, 1)
        self.assertGreater(lit(frame(g, (T0 + 1.0))), 100)
        # time runs out: the score is filed per time setting
        g.tick((T0 + 0.0) + 121)
        self.assertEqual(scores.best(st, "boggle:2m")["score"], 1)
        g.tick((T0 + 0.0) + 124)
        self.assertEqual(g.state, "over")

    def test_demo_finds_words(self):
        random.seed(4)
        g = make("boggle", minutes="1")
        self.assertGreater(demo_max(g, 40, lambda a: a.score), 2)


class Registry(unittest.TestCase):
    def test_registered_as_games(self):
        for gid in ("wordle", "hangman", "boggle"):
            cls = apps.REGISTRY[gid]
            self.assertEqual(cls.category, "Games")
            self.assertTrue(cls.interactive)
            names = [p["name"] for p in cls.params]
            self.assertIn("demo", names)
            self.assertIn("react_fx", names)


if __name__ == "__main__":
    unittest.main()
