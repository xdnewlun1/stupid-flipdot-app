"""Party games: Flipdot Party mini-games, buzzer quiz, Pictionary, rhythm game, drum machine, and their /play API.
Claude is always mocked. Run: python3 -m pytest -q tests/test_party.py"""

import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aidraw  # noqa: E402
import apps  # noqa: E402
from apps import base, pack_party, party_drums, party_pictionary, party_quiz, party_rhythm, scores  # noqa: E402
from canvas import Canvas  # noqa: E402
from runner import Context  # noqa: E402

KEYS = ["a", "b", "x", "y", "up", "down", "left", "right", "a", "a"]
_guard = mock.patch.object(aidraw, "base_url", return_value="http://127.0.0.1:9")


def setUpModule():
    _guard.start()  # a call to Claude that a test forgot to mock goes nowhere


def tearDownModule():
    _guard.stop()


class Store:
    def __init__(self, settings=None):
        self.saved = {}
        self.settings = settings or {}

    def _load(self, name, default):
        return json.loads(json.dumps(self.saved.get(name, default)))

    def _save(self, name, data):
        self.saved[name] = json.loads(json.dumps(data))


def make(app_id, store=None, **params):
    app = apps.create(Context(84, 42, store if store is not None else Store()), {"app": app_id, "params": params})
    t = [1000.0]
    app.clock = lambda: t[0]
    return app, t


def frame(app, t, dt=0.04):
    c = Canvas(84, 42)
    app.render(c, t[0])
    t[0] += dt
    return c


def press(app, key, seat, t=None):
    app.input(key, "down", seat)
    app.input(key, "up", seat)


def start(app, t, seats=(1,), via="start"):
    for s in seats:
        press(app, "a", s)
    frame(app, t)
    press(app, via, seats[0])
    frame(app, t)


def lit(c):
    return sum(c.px)


class Lobby(unittest.TestCase):
    def test_join_leave_and_start(self):
        app, t = make("party", cpu="none")
        frame(app, t)
        press(app, "a", 2)
        press(app, "a", 4)
        press(app, "b", 4)
        frame(app, t)
        self.assertEqual(app.joined, [2])
        self.assertEqual(app.state, "title")
        press(app, "start", 2)
        frame(app, t)
        self.assertEqual(app.state, "playing")
        self.assertEqual(app.seats, [2])

    def test_starts_on_its_own_after_the_last_join(self):
        app, t = make("party")
        press(app, "a", 1)
        for _ in range(int(pack_party.LOBBY_WAIT / 0.04) - 5):
            frame(app, t)
        self.assertEqual(app.state, "title")
        for _ in range(10):
            frame(app, t)
        self.assertEqual(app.state, "playing")

    def test_cpu_seats(self):
        for mode, humans, want in (("pair", [1], [2]), ("pair", [1, 3], []), ("fill", [3], [1, 2, 4]), ("none", [1], [])):
            app, t = make("party", cpu=mode)
            start(app, t, humans)
            self.assertEqual(app.cpus, want, mode)
            self.assertEqual(app.seats, sorted(humans + want))

    def test_interactive_except_while_celebrating(self):
        self.assertIs(pack_party.FlipdotParty.interactive, True)
        json.dumps(apps.catalog())  # the class attribute is a plain bool for the catalog
        app, t = make("party", cpu="pair")
        start(app, t)
        self.assertTrue(app.interactive)
        app.totals = {1: 5, 2: 3}
        app.finish(t[0], standings=False)
        self.assertFalse(app.interactive)
        for _ in range(int(pack_party.CELEBRATE_S / 0.04) + 5):
            frame(app, t)
        self.assertTrue(app.interactive)

    def test_celebration_flips_stand_alone(self):
        """The whole board flips at most once per FLASH_EVERY, so each flip gets the sweep wave; quiet hours: none."""
        for quiet in (False, True):
            app, t = make("party", cpu="pair")
            start(app, t)
            app.totals = {1: 9, 2: 3}
            with mock.patch.object(pack_party, "quiet_now", return_value=quiet):
                app.finish(t[0], standings=False)
            prev, changes = None, []
            while app.celebration:
                c = frame(app, t, 0.02)
                if app.celebration is None:
                    break
                if prev is not None and sum(a != b for a, b in zip(prev, c.px)) > 84 * 42 // 2:
                    changes.append(t[0])
                prev = bytes(c.px)
            if quiet:
                self.assertEqual(changes, [])
            else:
                self.assertGreaterEqual(len(changes), 3)
                gaps = [b - a for a, b in zip(changes, changes[1:])]
                self.assertGreaterEqual(min(gaps), pack_party.FLASH_EVERY - 0.05)

    def test_champion_score_filed_for_humans(self):
        app, t = make("party", cpu="pair", rounds=2)
        start(app, t)
        app.totals = {1: 4, 2: 6}
        app.finish(t[0], standings=False)
        for _ in range(400):
            frame(app, t)
        self.assertEqual(scores.best(app.ctx.store, "party")["score"], 4)  # P1's 4: the CPU's 6 isn't filed

    def test_input_now_is_timestamped_at_arrival(self):
        app, t = make("party")
        self.assertTrue(app.input_now("a", "down", 3))
        self.assertEqual(app.events[-1], (1000.0, "a", "down", 3))


class MiniGames(unittest.TestCase):
    def party(self, seats=(1, 2, 3, 4), cpus=()):
        app, t = make("party", cpu="none")
        app.joined = [s for s in seats if s not in cpus]
        app.cpus = list(cpus)
        app.seats = sorted(seats)
        app.totals = {s: 0 for s in seats}
        app.state = "playing"
        app.rnd = random.Random(5)
        return app, t

    def test_placement_points_and_ties(self):
        self.assertEqual(pack_party.placement_points([[3], [1, 2], [4]], 4), {3: 3, 1: 2, 2: 2, 4: 0})
        self.assertEqual(pack_party.placement_points([[1, 2]], 2), {1: 1, 2: 1})

    def test_mash_race(self):
        app, t = self.party((1, 2))
        m = pack_party.MashRace(app, t[0])
        for i in range(pack_party.MashRace.NEED):
            m.key(t[0] + i * 0.1, "a", 2)
            if i < 30:
                m.key(t[0] + i * 0.1, "a", 1)
        m.tick(t[0] + 6)
        self.assertTrue(m.done)
        self.assertEqual(m.ranking(), [[2], [1]])

    def test_reaction_early_press_is_a_penalty(self):
        app, t = self.party((1, 2))
        m = pack_party.Reaction(app, t[0])
        go = m.go_at
        m.key(go - 0.3, "a", 1)          # too soon
        m.key(go + 0.05, "a", 1)         # doesn't count this flash
        m.key(go + 0.25, "a", 2)
        self.assertEqual(m.score, {1: -1, 2: 1})
        self.assertIn(1, m.early)
        self.assertEqual(m.winner, 2)
        self.assertAlmostEqual(m.best[2], 0.25)
        self.assertEqual(m.ranking(), [[2], [1]])

    def test_memory_wrong_arrow_is_out(self):
        app, t = self.party((1, 2))
        m = pack_party.Memory(app, t[0])
        m.tick(t[0] + m.show_time() + 0.01)
        self.assertEqual(m.phase, "input")
        for d in m.seq:
            m.key(t[0], d, 1)
        wrong = next(a for a in pack_party.ARROWS if a != m.seq[0])
        m.key(t[0], wrong, 2)
        self.assertEqual(m.level, {1: 1, 2: 0})
        self.assertEqual(m.out, {2})
        m.tick(t[0] + m.show_time() + 0.5)
        self.assertTrue(m.done)  # one left standing
        self.assertEqual(m.ranking(), [[1], [2]])

    def test_dodge_hit_is_out(self):
        app, t = self.party((1, 2))
        m = pack_party.Dodge(app, t[0])
        m.blocks = [[0.0, app.h - 3.0, 1.0]]  # a block across the whole strip, at the players
        m.tick(t[0] + 0.01)
        self.assertEqual(set(m.out), {1, 2})
        self.assertTrue(m.done)

    def test_tug_of_war_teams(self):
        app, t = self.party((1, 2, 3))
        m = pack_party.TugOfWar(app, t[0])
        self.assertEqual((m.left, m.right), ([1, 3], [2]))
        for _ in range(60):
            m.key(t[0], "a", 2)  # one player against two: pulls count per head
        m.tick(t[0] + 1)
        self.assertTrue(m.done)
        self.assertEqual(m.ranking(), [[2], [1, 3]])

    def test_stop_the_clock_closest_wins(self):
        app, t = self.party((1, 2, 3))
        m = pack_party.StopCounter(app, t[0])
        m.key(m.start + m.target + 0.4, "a", 1)
        m.key(m.start + m.target - 0.05, "a", 2)
        m.tick(m.start + m.target + 3.1)
        self.assertTrue(m.done)
        self.assertEqual(m.ranking(), [[2], [1], [3]])  # 3 never pressed

    def test_hot_potato_holder_loses(self):
        app, t = self.party((1, 2))
        m = pack_party.HotPotato(app, t[0])
        m.holder, m.fuse = 1, t[0] + 2
        m.key(t[0] + 0.5, "a", 1)
        self.assertIsNone(m.holder)
        self.assertEqual(m.flight[1], 2)
        m.tick(m.flight[3] + 0.01)
        self.assertEqual(m.holder, 2)
        m.tick(t[0] + 2.1)
        self.assertEqual(m.boom[0], 2)
        self.assertEqual(m.ranking()[-1], [2])

    def test_quick_count(self):
        app, t = self.party((1, 2))
        m = pack_party.QuickCount(app, t[0])
        at = m.answer_at + 0.1
        m.key(at, "a", 1)  # locks in 20
        for _ in range(abs(m.n - 20)):
            m.key(at, "up" if m.n > 20 else "down", 2)
        m.key(at + 1, "a", 2)
        m.tick(at + 1)
        self.assertTrue(m.done)
        self.assertEqual(m.guess[2], m.n)
        self.assertEqual(m.ranking()[0], [2] if m.n != 20 else [1, 2])


class Simulated(unittest.TestCase):
    """Four players (humans mashing at random, or CPUs) for a few thousand frames: nothing breaks, frames stay cheap."""

    def run_game(self, app_id, frames=2500, seats=(1, 2, 3, 4), cpu="none", phone=None, **params):
        app, t = make(app_id, cpu=cpu, **params)
        rnd = random.Random(7)
        start(app, t, seats)
        self.assertEqual(app.state, "playing")
        worst, total = 0.0, 0.0
        states = set()
        for i in range(frames):
            if rnd.random() < 0.5:
                k, s = rnd.choice(KEYS), rnd.choice(seats)
                app.input_now(k, "down", s)
                app.input_now(k, "up", s)
            if phone and rnd.random() < 0.2:
                phone(app, rnd)
            c = Canvas(84, 42)
            t0 = time.perf_counter()
            app.render(c, t[0])
            dt = time.perf_counter() - t0
            worst, total = max(worst, dt), total + dt
            states.add(app.state)
            t[0] += 0.04
            if app.state == "title" and i > 50:
                start(app, t, seats)  # a new match
        self.assertLess(total / frames, 0.003, f"{app_id}: {total / frames * 1000:.2f} ms a frame")
        return app, states

    def test_party(self):
        app, states = self.run_game("party", rounds=3, frames=6000)
        self.assertIn("title", states)  # at least one whole match played through

    def test_party_with_cpus(self):
        self.run_game("party", seats=(2,), cpu="fill", frames=3000)

    def test_quiz(self):
        def phone(app, rnd):
            s = rnd.choice((1, 2, 3, 4))
            app.phone(rnd.choice(["buzz", "answer"]), {"i": rnd.randrange(4)}, s)
        app, states = self.run_game("quiz", source="builtin", questions=5, phone=phone, frames=3000)
        self.assertIn("title", states)

    def test_pictionary(self):
        def phone(app, rnd):
            s = rnd.choice((1, 2, 3, 4))
            if rnd.random() < 0.5:
                app.phone("draw", {"segs": [[rnd.randrange(84), rnd.randrange(41), rnd.randrange(84), rnd.randrange(41)]]}, s)
            else:
                app.phone("guess", {"text": rnd.choice(["cat", "house", getattr(app, "word", "x")])}, s)
        self.run_game("pictionary", time=20, rounds=1, phone=phone, frames=3000)

    def test_rhythm(self):
        app, _ = self.run_game("rhythm", length=40, source="practice", frames=2500)

    def test_drum_machine(self):
        app, t = make("drummachine")
        rnd = random.Random(3)
        app.play(t[0])
        for i in range(2500):
            if rnd.random() < 0.1:
                app.phone("toggle", {"r": rnd.randrange(5), "s": rnd.randrange(16)}, rnd.randrange(1, 5))
            if rnd.random() < 0.01:
                app.phone("set", {"bpm": rnd.randrange(60, 200), "swing": rnd.randrange(50, 76)}, 1)
            frame(app, t, min(0.05, app.interval))
        self.assertTrue(app.playing)


class QuizRules(unittest.TestCase):
    def setUp(self):
        self.app, self.t = make("quiz", source="builtin", questions=3, difficulty="medium")
        start(self.app, self.t, (1, 2))
        self.app.rnd = random.Random(1)

    def open(self):
        self.t[0] = self.app.phase_at + party_quiz.READ_S + 0.01
        frame(self.app, self.t)
        self.assertEqual(self.app.phase, "open")

    def test_early_buzz_locks_you_out(self):
        self.app.buzz(self.t[0], 1)
        self.assertIn(1, self.app.locked)
        self.assertIn(1, self.app.early)
        self.open()
        self.assertFalse(self.app.buzz(self.t[0], 1))
        self.assertTrue(self.app.buzz(self.t[0], 2))

    def test_right_and_wrong_answers(self):
        self.open()
        self.app.buzz(self.t[0], 1)
        wrong = (self.app.q["c"] + 1) % 4
        self.assertFalse(self.app.answer(self.t[0] + 1, 1, wrong))
        self.assertEqual(self.app.totals[1], -100)
        self.assertEqual(self.app.phase, "open")  # P2 can still buzz
        self.assertFalse(self.app.buzz(self.t[0] + 1, 1))
        self.app.buzz(self.t[0] + 1.2, 2)
        self.assertTrue(self.app.answer(self.t[0] + 2, 2, self.app.q["c"]))
        self.assertEqual(self.app.totals[2], 200)
        self.assertEqual(self.app.phase, "reveal")

    def test_controller_keys_answer(self):
        self.open()
        press(self.app, "x", 2)  # buzz
        frame(self.app, self.t)
        self.assertEqual(self.app.buzzer, 2)
        self.t[0] += 1
        press(self.app, "abxy"[self.app.q["c"]] if self.app.q["c"] != 2 else "x", 2)
        frame(self.app, self.t)
        self.assertEqual(self.app.totals[2], 200)

    def test_too_slow_counts_as_wrong(self):
        self.open()
        self.app.buzz(self.t[0], 1)
        self.t[0] += party_quiz.ANSWER_S + party_quiz.SLAM_S + 0.1
        frame(self.app, self.t)
        self.assertEqual(self.app.totals[1], -100)

    def test_answers_shuffled_and_whole_game(self):
        for _ in range(3):
            self.open()
            self.app.buzz(self.t[0], 1)
            self.app.answer(self.t[0] + 1, 1, self.app.q["c"])
            self.t[0] = self.app.phase_at + party_quiz.REVEAL_S + 0.1
            frame(self.app, self.t)
        self.assertEqual(self.app.totals[1], 600)
        self.assertIsNotNone(self.app.standings_at)


def claude_reply(text, stop="end_turn"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop, "model": "claude-test"}


class QuizQuestions(unittest.TestCase):
    def batch(self, n=20):
        return {"questions": [{"q": f"Test question number {i}?", "answers": [f"A{i}", f"B{i}", f"C{i}", f"D{i}"],
                               "correct": i % 4} for i in range(n)]}

    def test_builtin_set(self):
        qs = party_quiz.builtin()
        self.assertGreaterEqual(len(qs), 90)
        for q in qs:
            self.assertIsNotNone(party_quiz.clean(q, q["cat"], q["d"]), q["q"])

    def test_claude_batch_is_cached_and_used_first(self):
        store = Store({"ai": {"api_key": "sk-test", "model": "claude-opus-5-5"}})
        calls = []

        def fake(key, body, timeout=0):
            calls.append(body)
            return claude_reply(json.dumps(self.batch()))
        with mock.patch.object(aidraw, "call_api", fake):
            app, t = make("quiz", store, category="science", difficulty="hard", questions=5)
            app.fetcher.join(5)
        self.assertEqual(len(calls), 1)
        body = calls[0]
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Science", body["messages"][0]["content"])
        self.assertEqual(body.get("fallbacks"), "default")
        self.assertEqual(len(store.saved[party_quiz.CACHE_FILE]["batches"]["science:hard"]), 20)
        start(app, t)
        self.assertTrue(all(q["q"].startswith("Test question") for q in app.qs))
        # asked questions aren't repeated while fresh ones are left
        app2, t2 = make("quiz", store, category="science", difficulty="hard", questions=5, source="auto")
        start(app2, t2)
        self.assertFalse({q["q"] for q in app.qs} & {q["q"] for q in app2.qs})

    def test_no_key_or_builtin_source_never_calls_claude(self):
        with mock.patch.object(aidraw, "call_api", side_effect=AssertionError("no API call expected")):
            app, t = make("quiz", Store({"ai": {"api_key": "sk"}}), source="builtin")
            self.assertIsNone(app.fetcher)
            app, t = make("quiz", Store(), source="auto")
            self.assertIsNone(app.fetcher)
            start(app, t)
            self.assertEqual(len(app.qs), 10)

    def test_bad_replies_fall_back_to_builtin(self):
        for reply in (claude_reply("not json"), claude_reply("", "refusal"),
                      claude_reply(json.dumps({"questions": [{"q": "x", "answers": ["a"], "correct": 9}]}))):
            store = Store({"ai": {"api_key": "sk-test"}})
            with mock.patch.object(aidraw, "call_api", return_value=reply):
                app, t = make("quiz", store, category="history", difficulty="easy", questions=4)
                app.fetcher.join(5)
            start(app, t)
            self.assertEqual(len(app.qs), 4)
            self.assertTrue(all(q["cat"] == "history" for q in app.qs))

    def test_api_error_is_noted(self):
        store = Store({"ai": {"api_key": "sk-test"}})
        with mock.patch.object(aidraw, "call_api", side_effect=aidraw.AiError("overloaded")):
            app, t = make("quiz", store)
            app.fetcher.join(5)
        self.assertIn("overloaded", app.phone_state(1)["note"])


class PictionaryRules(unittest.TestCase):
    def setUp(self):
        self.app, self.t = make("pictionary", time=60, rounds=1)
        start(self.app, self.t, (1, 2, 3))
        self.t[0] += party_pictionary.READY_S + 0.01
        frame(self.app, self.t)
        self.assertEqual(self.app.phase, "draw")

    def test_matching(self):
        m = party_pictionary.matches
        self.assertTrue(m("Ice-Cream!", "ice cream"))
        self.assertTrue(m("the cats", "cat"))
        self.assertTrue(m("a  Castle ", "castle"))
        self.assertFalse(m("cast", "castle"))
        self.assertFalse(m("", "cat"))

    def test_only_the_drawer_draws(self):
        d = self.app.drawer
        other = next(s for s in self.app.seats if s != d)
        self.assertFalse(self.app.stroke(other, [[0, 0, 10, 0]]))
        self.assertTrue(self.app.stroke(d, [[0, 0, 10, 0]]))
        self.assertEqual(sum(self.app.pic), 11)
        c = frame(self.app, self.t)
        self.assertEqual(sum(c.px[:84]), 11)  # the board mirrors it
        self.app.phone("clear", {}, d)
        self.assertEqual(sum(self.app.pic), 0)

    def test_first_right_guess_scores(self):
        d = self.app.drawer
        g1, g2 = [s for s in self.app.seats if s != d][:2]
        self.assertFalse(self.app.guess(self.t[0], g1, "definitely wrong"))
        self.assertFalse(self.app.guess(self.t[0], d, self.app.word))  # the drawer can't guess
        self.assertTrue(self.app.guess(self.app.phase_at + 30, g2, self.app.word.upper()))
        self.assertEqual(self.app.totals[g2], 80)  # 50 + half of 50 for half the time left, rounded to 10
        self.assertEqual(self.app.totals[d], 50)
        self.assertEqual(self.app.phase, "reveal")
        self.assertFalse(self.app.guess(self.t[0], g1, self.app.word))

    def test_phone_sees_word_only_as_drawer(self):
        d = self.app.drawer
        other = next(s for s in self.app.seats if s != d)
        self.assertEqual(self.app.phone_state(d)["word"], self.app.word)
        self.assertIsNone(self.app.phone_state(other)["word"])
        self.assertEqual(len(self.app.phone_state(other)["pic"]), 84 * 41)

    def test_timer_ends_turn(self):
        self.t[0] = self.app.phase_at + 60.1
        frame(self.app, self.t)
        self.assertEqual(self.app.phase, "reveal")
        self.assertIsNone(self.app.solved)


class PictionaryClaude(unittest.TestCase):
    def wait(self, app, t):
        for _ in range(200):
            if app.ai_pending is not None:
                break
            time.sleep(0.01)
        frame(app, t)

    def test_claude_guesses_from_an_image(self):
        store = Store({"ai": {"api_key": "sk-test", "model": "claude-sonnet-5-5"}})
        app, t = make("pictionary", store, ai_guess=True)
        start(app, t, (1,))  # one human is enough with Claude playing
        t[0] += party_pictionary.READY_S + 0.01
        frame(app, t)
        app.stroke(1, [[5, 5, 70, 5], [5, 5, 5, 35], [70, 5, 70, 35], [5, 35, 70, 35]])
        seen = []

        def fake(key, body, timeout=0):
            seen.append(body)
            return claude_reply(app.word.upper() + "\n")
        t[0] = app.ai_next + 0.01
        with mock.patch.object(aidraw, "call_api", fake):
            frame(app, t)
            self.wait(app, t)
        img = seen[0]["messages"][0]["content"][0]
        self.assertEqual(img["type"], "image")
        self.assertEqual(img["source"]["media_type"], "image/png")
        import base64
        self.assertTrue(base64.b64decode(img["source"]["data"]).startswith(b"\x89PNG"))
        self.assertEqual(app.solved[0], party_pictionary.AI)
        self.assertGreater(app.totals[party_pictionary.AI], 0)

    def test_claude_draws(self):
        store = Store({"ai": {"api_key": "sk-test"}})
        app, t = make("pictionary", store, ai_draw=True, rounds=1, time=30)
        start(app, t, (1,))
        rows = ["0" * 20 + "1" * 40 + "0" * 24] * 41
        with mock.patch.object(aidraw, "draw", return_value={"frames": [{"rows": rows}]}) as draw:
            app.next_turn(t[0])  # skip P1's turn: Claude's is next, and it starts drawing
            self.assertEqual(app.drawer, party_pictionary.AI)
            self.wait(app, t)
        self.assertIn(app.word, draw.call_args[0][1])
        t[0] = app.phase_at + party_pictionary.READY_S + 0.01
        frame(app, t)
        self.assertEqual(app.phase, "draw")
        for _ in range(int(30 * 0.6 / 0.04) + 5):
            frame(app, t)
        self.assertEqual(sum(app.pic), 40 * 41)  # fully revealed by 60% of the time
        self.assertTrue(app.guess(t[0], 1, app.word))

    def test_claude_needs_a_key(self):
        app, t = make("pictionary", Store(), ai_guess=True, ai_draw=True)
        press(app, "a", 1)
        self.assertFalse(app.can_start())


class Fake:
    """audio_signal.reading stand-in: a sure 120 BPM grid and band hits on demand."""

    def __init__(self, t0=0.0):
        self.t0, self.beats, self.hits = t0, 0, {"bass": 0, "lowmid": 0, "mid": 0, "high": 0}

    def __call__(self, store, fallback=None, now=None):
        phase = ((now - self.t0) * 2) % 1.0
        return {"live": True, "bpm": 120.0, "bpm_confidence": 1.0, "beat_phase": phase,
                "beat_count": self.beats, "hit_count": dict(self.hits)}


class RhythmRules(unittest.TestCase):
    def game(self, store=None, **params):
        app, t = make("rhythm", store, **params)
        start(app, t, (1, 2))
        return app, t

    def test_windows(self):
        self.assertEqual(party_rhythm.judge(0.04), ("perfect", 300))
        self.assertEqual(party_rhythm.judge(-0.08), ("great", 200))
        self.assertEqual(party_rhythm.judge(0.13), ("good", 100))
        self.assertIsNone(party_rhythm.judge(0.15))

    def test_practice_notes_on_the_grid(self):
        app, t = self.game(source="practice", bpm=120)
        for _ in range(100):
            frame(app, t)
        lane0 = sorted(n["t"] for n in app.notes if n["lane"] == 0)
        self.assertTrue(lane0)
        for nt in lane0:  # bass and beats: on eighth notes of the 120 BPM grid
            self.assertAlmostEqual(((nt - app.practice_from) / 0.25) % 1, 0, places=6)

    def test_combo_and_score(self):
        app, t = self.game(source="practice")
        app.notes = [{"lane": 0, "t": 2000.0 + i, "done": None} for i in range(12)]
        for i in range(11):
            self.assertEqual(app.press(2000.0 + i + 0.01, 1), "perfect")
        st = app.stats[1]
        self.assertEqual(st["combo"], 11)
        self.assertEqual(st["score"], 10 * 300 + 300 * 2)  # x2 from the 11th hit in a row
        self.assertEqual(app.press(2011.0 + 0.17, 1), "miss")  # outside every window: combo gone
        self.assertEqual(app.stats[1]["combo"], 0)
        self.assertEqual(app.stats[1]["best"], 11)
        self.assertIsNone(app.press(2050.0, 1))  # nothing near: ignored

    def test_live_notes_are_judged_against_the_board_delay(self):
        """A hit heard on the beat: the speakers play it a Board delay before the board's analysis does."""
        store = Store({"snapcast": {"delay_ms": 150}})
        for delayed, lag in ((False, 0.15), (True, 0.0)):
            fake = Fake(1000.0)
            with mock.patch.object(party_rhythm.audio_signal, "reading", fake):
                app, t = self.game(store, speakers_delayed=delayed)
                self.assertTrue(app.live)
                t[0] = 1010.0  # exactly on a beat (phase 0)
                frame(app, t)
                fake.hits["bass"] += 1
                frame(app, t)
            notes = [n for n in app.notes if n["lane"] == 0]
            self.assertEqual(len(notes), 1)
            # one bar (4 beats at 120 BPM = 2 s) later, minus the lag
            self.assertAlmostEqual(notes[0]["t"], 1010.0 + 2.0 - lag, places=3)
            self.assertEqual(app.press(1012.0 - lag + 0.01, 1), "perfect")
        # and a press on the analysis' beat (not what's heard) is only "good" with undelayed speakers
        fake = Fake(1000.0)
        with mock.patch.object(party_rhythm.audio_signal, "reading", fake):
            app, t = self.game(store)
            t[0] = 1010.0
            frame(app, t)
            fake.hits["bass"] += 1
            frame(app, t)
        self.assertEqual(app.press(1012.0, 1), "miss")

    def test_scores_filed(self):
        app, t = self.game(source="practice", length=20)
        app.stats[1]["score"], app.stats[1]["best"] = 4200, 33
        app.notes = []
        t[0] = app.end_at + 1.1
        app.tick(t[0])
        self.assertEqual(scores.best(app.ctx.store, "rhythm:combo")["score"], 33)
        for _ in range(400):
            frame(app, t)
        self.assertEqual(scores.best(app.ctx.store, "rhythm")["score"], 4200)


class DrumTiming(unittest.TestCase):
    def test_steps_within_10ms_on_a_fake_clock(self):
        """Drive render() like the runner: sleep the app's interval plus a few ms of scheduling jitter."""
        for bpm, swing in ((120, 50), (93, 66), (178, 75)):
            app, t = make("drummachine")
            app.phone("load", {"name": "Four on the floor"}, 1)
            app.phone("set", {"bpm": bpm, "swing": swing}, 1)
            rnd = random.Random(bpm)
            fired = []
            orig = app.hit
            app.hit = lambda inst, at, now: (fired.append((at, now)), orig(inst, at, now))
            app.play(t[0])
            d = 60 / bpm / 4
            for _ in range(3000):
                c = Canvas(84, 42)
                app.render(c, t[0])
                t[0] += app.interval + rnd.uniform(0, 0.003)
            self.assertGreater(len(fired), 100)
            worst = max(abs(now - at) for at, now in fired)
            self.assertLessEqual(worst, 0.010, f"{bpm} BPM swing {swing}: {worst * 1000:.1f} ms")
            # and the scheduled times are the swung 16th grid from the start
            times = sorted({round(at, 9) for at, _ in fired})
            steps = [k for k in range(16) if any(app.grid[r][k] == "x" for r in range(app.rows_n))]
            for at, k in zip(times, steps):
                self.assertAlmostEqual(at - times[0], party_drums.offsets(k, d, swing / 100) - party_drums.offsets(steps[0], d, swing / 100), places=6)

    def test_swing_offsets(self):
        d = 0.125
        self.assertAlmostEqual(party_drums.offsets(1, d, 0.5), 0.125)
        self.assertAlmostEqual(party_drums.offsets(1, d, 0.75), 0.1875)
        self.assertAlmostEqual(party_drums.offsets(2, d, 0.75), 0.25)

    def test_tempo_change_carries_on(self):
        clock = party_drums.Clock(120, 0.5)
        clock.start(0.0)
        self.assertEqual([k for k, _ in clock.due(0.2)], [0, 1])
        nxt = clock.time(clock.k)
        clock.retime(bpm=60)
        self.assertAlmostEqual(clock.time(clock.k), nxt)  # the next step keeps its time
        self.assertAlmostEqual(clock.time(clock.k + 1) - clock.time(clock.k), 0.25)

    def test_stall_skips_instead_of_a_burst(self):
        clock = party_drums.Clock(120, 0.5)
        clock.start(0.0)
        clock.due(0.0)
        self.assertLessEqual(len(clock.due(5.0)), 2)


class DrumMachine(unittest.TestCase):
    def test_patterns_save_load_delete(self):
        store = Store()
        app, t = make("drummachine", store)
        app.phone("clear", {}, 1)
        app.phone("toggle", {"r": 2, "s": 4}, 1)
        app.phone("toggle", {"r": 0, "s": 0, "on": True}, 2)  # a second phone
        app.phone("set", {"bpm": 133, "swing": 60}, 2)
        app.phone("save", {"name": "Mine"}, 1)
        self.assertEqual(store.saved[party_drums.FILE]["patterns"]["Mine"]["rows"][2][4], "x")
        app.phone("load", {"name": "Clockwork"}, 1)
        self.assertEqual(app.phone_state(1)["bpm"], 80)
        app.phone("load", {"name": "Mine"}, 1)
        st = app.phone_state(3)
        self.assertEqual((st["bpm"], st["swing"], st["grid"][0][0], st["grid"][2][4]), (133, 60, "x", "x"))
        with self.assertRaises(ValueError):
            app.phone("save", {"name": "Breakbeat"}, 1)
        # survives a restart
        app2, _ = make("drummachine", store)
        self.assertEqual(app2.phone_state(1)["grid"][2][4], "x")
        app2.phone("delete", {"name": "Mine"}, 1)
        self.assertNotIn("Mine", [p["name"] for p in app2.patterns()])

    def test_rows_and_bad_input(self):
        app, t = make("drummachine", rows=4)
        self.assertEqual(len(app.phone_state(1)["rows"]), 4)
        with self.assertRaises(ValueError):
            app.phone("toggle", {"r": 4, "s": 0}, 1)
        with self.assertRaises(ValueError):
            app.phone("toggle", {"r": "x"}, 1)

    def test_instruments_flip_counts(self):
        app, t = make("drummachine", rows=6)
        counts = {}
        for inst, _ in party_drums.INSTRUMENTS:
            app.pic[:] = bytes(len(app.pic))
            app.anims = []
            app.hit(inst, t[0], t[0])
            app.run_anims(t[0] + 1)
            counts[inst] = sum(app.pic)
        self.assertEqual(counts["tick"], 1)
        self.assertEqual(counts["cluster"], 9)
        self.assertEqual(counts["crack"], 28 * 7)
        self.assertEqual(counts["roll"], 28 * 7)
        self.assertEqual(counts["sweep"], 84 * 42)

    def test_quiet_hours(self):
        store = Store({"quiet_hours": {"max_flips": 20}})
        app, t = make("drummachine", store)
        with mock.patch.object(party_drums, "quiet_now", return_value=True):
            app.hit("sweep", t[0], t[0])  # plays as a tap
            self.assertEqual(app.anims, [])
            self.assertEqual(sum(app.pic), 9)
            for _ in range(5):
                app.hit("crack", t[0], t[0])
            self.assertLessEqual(sum(n for _, n in app.flips), 20)  # the flip budget

    def test_follow_the_music(self):
        fake = Fake(0.0)
        app, t = make("drummachine", follow=True)
        app.play(t[0])
        with mock.patch.object(party_drums.audio_signal, "reading", fake):
            for _ in range(200):
                fake.beats = int((t[0] - fake.t0) * 2)
                frame(app, t, min(0.02, app.interval))
        self.assertEqual(round(app.seq.bpm), 120)
        # the quarter notes (steps 0, 4, 8, 12) fall on the beats
        k = app.seq.k - app.seq.k % 4
        self.assertAlmostEqual((app.seq.time(k) - fake.t0) * 2 % 1, 0, delta=0.02)


class PlayerLink(unittest.TestCase):
    """The party API through a /join player link on a real HTTP server; owner routes stay closed."""

    @classmethod
    def setUpClass(cls):
        import logging
        import flipdot
        import runner as runner_mod
        import server
        from store import Store as RealStore
        from test_board import config
        from ws import Hub
        import access
        logging.disable(logging.CRITICAL)
        cls.server = server
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: getattr(server, k) for k in ("board", "store", "runner", "hub", "password")}
        server.board = flipdot.Board(config(), dry_run=True)
        server.store = RealStore(os.path.join(cls.tmp.name, "data"))
        server.runner = runner_mod.Runner(server.board, server.store)
        server.hub = Hub(server.board, server.runner, server.ws_message)
        server.password = "pw-owner"
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.tok = access.get(server.store).create("Party guest", "player")[1]

    @classmethod
    def tearDownClass(cls):
        import logging
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.server.runner.stop()
        for k, v in cls.saved.items():
            setattr(cls.server, k, v)
        cls.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def req(self, method, path, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Authorization": f"Bearer {self.tok}"}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_quiz_through_the_player_link(self):
        self.assertEqual(self.req("POST", "/api/party", {"action": "buzz"})[0], 409)  # nothing running
        self.assertEqual(self.req("POST", "/api/run", {"app": "quiz", "params": {"source": "builtin"}})[0], 200)
        status, body = self.req("POST", "/api/party", {"action": "join", "seat": 3})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["party"]["seats"], [3])
        self.req("POST", "/api/party", {"action": "join", "seat": 2})
        status, body = self.req("POST", "/api/party", {"action": "start", "seat": 2})
        self.assertEqual(body["party"]["state"], "playing")
        status, body = self.req("GET", "/api/party?seat=3")
        self.assertEqual(body["app"], "quiz")
        self.assertIn(body["party"]["phase"], ("read", "open"))
        self.assertEqual(len(body["party"]["answers"]), 4)
        self.assertEqual(self.req("POST", "/api/party", {"action": "answer", "seat": 3, "data": {"i": 9}})[0], 400)
        for path in ("/api/settings", "/api/access", "/api/playlists"):
            self.assertEqual(self.req("GET", path)[0], 403, path)
        self.assertEqual(self.req("POST", "/api/run", {"app": "clock"})[0], 403)
        self.server.runner.stop()

    def test_drum_machine_through_the_player_link(self):
        self.assertEqual(self.req("POST", "/api/run", {"app": "drummachine"})[0], 200)
        status, body = self.req("POST", "/api/party", {"action": "toggle", "seat": 1, "data": {"r": 1, "s": 3}})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["party"]["mode"], "drum")
        self.server.ws_message({"type": "party", "action": "play", "seat": 2})
        self.assertTrue(self.req("GET", "/api/party?seat=4")[1]["party"]["playing"])
        self.server.runner.stop()


if __name__ == "__main__":
    unittest.main()
