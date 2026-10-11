"""The phone and team rounds of Flipdot Party (apps/party_more.py): scoring of each, phone actions, controller
fallbacks (etch-a-sketch drawing, d-pad bids), CPU seats through every round, and the rounds only coming up with
enough seats. Run: python3 -m pytest -q tests/test_party_more.py"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps  # noqa: E402,F401
from apps import pack_party, party_more as pm  # noqa: E402
from canvas import Canvas  # noqa: E402
from test_party import make  # noqa: E402


class Rounds(unittest.TestCase):
    def party(self, seats=(1, 2, 3, 4), cpus=(2, 3, 4), stage=0.0, seed=5):
        app, t = make("party", cpu="none")
        app.joined = [s for s in seats if s not in cpus]
        app.cpus = list(cpus)
        app.seats = sorted(seats)
        app.totals = {s: 0 for s in seats}
        app.state = "playing"
        app.rnd = random.Random(seed)
        app.stage = lambda: stage
        app.now = 1000.0
        return app, t

    def play(self, app, m, until, step=0.04, on=None):
        now = m.t0
        while now < until and not m.done:
            app.now = now
            if on:
                on(now)
            m.tick(now)
            m.draw(Canvas(84, 42), now)
            now += step
        return now

    def mini(self, cls, **kw):
        app, t = self.party(**kw)
        m = cls(app, 1000.0)
        app.mini, app.mini_cls, app.phase = m, cls, "play"
        return app, m

    # ---- draw-off ----------------------------------------------------------------------------------

    def test_draw_off_votes_score_the_drawers_and_the_voters_with_the_room(self):
        app, m = self.mini(pm.DrawOff, cpus=(3, 4))  # P1 and P2 human
        a, b = m.drawers
        self.assertEqual(sorted(m.drawers + m.voters()), [1, 2, 3, 4])
        if not app.is_cpu(a):  # a phone draws
            m.phone("draw", {"segs": [[0, 0, 20, 20]], "v": 1}, a, 1001.0)
            self.assertGreater(m.pads[a].dots, 15)
            st = m.phone_state(a)
            self.assertEqual(st["widget"], "draw")
            self.assertEqual(len(st["pic"]), pm.PW * pm.PH)
            self.assertEqual(st["word"], m.word)
        self.play(app, m, 1000.0 + m.draw_s + 0.1)
        self.assertEqual(m.phase, "vote")
        voters = m.voters()
        human = [s for s in voters if not app.is_cpu(s)]
        for s in human:
            self.assertEqual(m.phone_state(s)["widget"], "vote")
            self.assertEqual(m.phone("vote", {"seat": a}, s, 1020.0), {"ok": True})
            self.assertEqual(m.phone("vote", {"seat": b}, s, 1020.0), {"ok": True})  # a second vote changes nothing
            self.assertEqual(m.votes[s], a)
        m.key(1020.0, "a", a)  # drawers can't vote
        self.assertNotIn(a, m.votes)
        self.play(app, m, 1000.0 + m.draw_s + m.VOTE_S + 1.0)
        self.assertEqual(m.phase, "reveal")
        self.assertEqual(m.score[a] + m.score[b], len(m.votes))
        best = max(m.score[a], m.score[b])
        for s, v in m.votes.items():
            self.assertEqual(m.score[s], 1 if m.score[v] == best else 0)
        self.play(app, m, 1100.0)
        self.assertTrue(m.done)

    def test_a_pad_draws_like_an_etch_a_sketch(self):
        app, m = self.mini(pm.DrawOff, cpus=(3, 4))
        m.drawers = [1, 3]  # a human and a CPU draw
        m.pads = {s: pm.Pad() for s in m.drawers}
        m.seeds = {s: 7 for s in m.drawers}
        s = 1
        app.held.add((s, "right"))
        self.play(app, m, 1001.0)
        pad = m.pads[s]
        self.assertGreaterEqual(pad.dots, 10)  # up to 20 dots a second (the test ticks at 25 a second)
        app.held.add((s, "b"))  # pen up: moves without drawing
        before = pad.dots
        self.play(app, m, 1002.0)
        self.assertEqual(pad.dots, before)
        early = m.pads[3].dots
        self.play(app, m, 1000.0 + m.draw_s - 0.1)
        self.assertGreater(m.pads[3].dots, max(20, early))  # the CPU's scribble, revealed as the clock runs

    # ---- telephone ---------------------------------------------------------------------------------

    def test_telephone_chain_and_scoring(self):
        app, m = self.mini(pm.Telephone, cpus=(2, 3, 4), seed=3)
        self.assertEqual(len(m.chain), 4)
        seen = []
        now = m.t0
        while not m.done and now < 1100.0:
            st = m.step()
            if st is not None and (not seen or seen[-1] is not st):
                seen.append(st)
                kind, seat, data, extra = st
                if kind == "guess" and seat == 1:
                    i = data.index(m.word)
                    self.assertEqual(m.phone_state(1)["widget"], "choices")
                    self.assertEqual(m.phone("choose", {"i": i}, 1, now), {"ok": True})
                if kind == "draw" and seat == 1:
                    self.assertEqual(m.phone_state(1)["widget"], "draw")
                    m.phone("draw", {"segs": [[1, 1, 30, 20]]}, 1, now)
            app.now = now
            m.tick(now)
            m.draw(Canvas(84, 42), now)
            now += 0.04
        self.assertTrue(m.done)
        self.assertEqual([st[0] for st in seen], ["draw", "guess", "draw", "guess"])
        for k, (kind, seat, data, extra) in enumerate(m.steps):
            if kind == "guess" and extra == m.word:
                self.assertGreaterEqual(m.score[seat], 2)
                self.assertGreaterEqual(m.score[m.steps[k - 1][1]], 1)
        self.assertLessEqual(now - m.t0, 50)

    # ---- copy the beat and keep the tempo -----------------------------------------------------------

    def test_copy_the_beat_ranks_by_timing_error(self):
        app, m = self.mini(pm.CopyBeat, cpus=(3, 4))
        self.play(app, m, m.tap_at + 0.01)
        self.assertEqual(m.phase, "tap")
        for o in m.onsets:  # P1 taps the rhythm exactly
            m.key(m.tap_at + 0.3 + o, "a", 1)
        for o in m.onsets:  # P2 is late and drops a beat
            if o != m.onsets[1]:
                m.key(m.tap_at + 0.3 + o * 1.3, "a", 2)
        self.play(app, m, m.tap_at + m.tap_s + 4.0)
        self.assertTrue(m.done)
        self.assertLess(m.err[1], 0.001)
        self.assertGreater(m.err[2], m.err[1])
        self.assertEqual(m.ranking()[0], [1])
        for s in (3, 4):
            self.assertGreater(len(m.taps[s]), 1)  # the CPUs tapped

    def test_keep_the_tempo_scores_the_silent_beats(self):
        app, m = self.mini(pm.KeepTempo, cpus=(3, 4), stage=1.0)
        for k in range(m.count + m.silent):
            m.key(m.start + k * m.period, "a", 1)  # P1 is a metronome
            m.key(m.start + k * m.period + 0.12 * (k >= m.count), "a", 2)  # P2 drifts late once it's silent
        self.play(app, m, m.end + 5.0)
        self.assertTrue(m.done)
        self.assertLess(m.err[1], 0.001)
        self.assertAlmostEqual(m.err[2], 0.12, places=2)
        self.assertEqual(m.ranking()[0], [1])
        self.assertGreater(m.err[3], 0)
        self.assertGreater(m.bpm, 120)  # faster later in the match

    # ---- how many dots -----------------------------------------------------------------------------

    def test_how_many_closest_without_going_over(self):
        app, m = self.mini(pm.HowMany, cpus=(3, 4))
        self.play(app, m, m.bid_at + 0.05)
        self.assertEqual(m.phase, "bid")
        self.assertEqual(m.phone_state(1)["widget"], "bid")
        self.assertEqual(m.phone("bid", {"bid": m.n - 3}, 1, 1010.0), {"ok": True})
        self.assertEqual(m.bid[1], m.n - 3)
        m.phone("lock", {}, 1, 1010.0)
        self.assertIn(1, m.locked)
        self.assertEqual(m.phone("bid", {"bid": 5}, 1, 1010.0), {"ok": False})  # locked
        m.key(1010.0, "up", 2)
        m.key(1010.0, "right", 2)
        self.assertEqual(m.bid[2], 46)
        m.set_bid(2, m.n + 1)
        m.key(1010.0, "a", 2)
        m.set_bid(3, m.n), m.lock(3)
        m.set_bid(4, m.n - 10), m.lock(4)
        self.play(app, m, m.bid_at + m.BID_S + 4.0)
        self.assertTrue(m.done)
        self.assertEqual(m.ranking(), [[3], [1], [4], [2]])  # exact, under by 3, under by 10, over

    # ---- liar's dice -------------------------------------------------------------------------------

    def test_liars_dice_bids_challenge_and_scores(self):
        app, m = self.mini(pm.LiarsDice, cpus=(3, 4), seed=2)
        self.assertEqual(len(m.dice[1]), 3)
        st = m.phone_state(1)
        self.assertEqual(st["widget"], "dice")
        self.assertEqual(st["dice"], m.dice[1])
        first = m.current()
        self.assertFalse(m.challenge(first, 1000.0))  # nothing to call yet
        self.assertTrue(m.place(first, 2, 3, 1000.0))
        nxt = m.current()
        self.assertFalse(m.place(nxt, 2, 3, 1000.0))  # not a raise
        self.assertFalse(m.place(nxt, 1, 6, 1000.0))
        self.assertTrue(m.valid(2, 4) and m.valid(3, 1))
        self.assertTrue(m.challenge(nxt, 1000.0))
        self.assertEqual(m.phase, "reveal")
        threes = sum(d == 3 for s in m.seats for d in m.dice[s])
        liar = threes < 2
        self.assertEqual(m.winner, nxt if liar else first)
        self.assertEqual(m.score[m.winner], 2)
        self.assertEqual(m.score[m.loser], 0)
        self.assertEqual(sum(m.score.values()), 2 + 2)
        self.play(app, m, 1000.0 + m.REVEAL_S + 0.1)
        self.assertEqual(m.games, 2)  # a second game
        self.play(app, m, 1060.0, on=lambda now: m.phase == "bid" and not app.is_cpu(m.current()) and m.key(now, "a", m.current()))
        self.assertTrue(m.done)

    def test_liars_dice_pad_lines_up_a_raise(self):
        app, m = self.mini(pm.LiarsDice, cpus=(3, 4), seed=2)
        s = m.current()
        if app.is_cpu(s):
            self.play(app, m, 1004.0)
            s = m.current()
        m.key(1005.0, "right", s)
        m.key(1005.0, "up", s)
        c, f = m.pending[s]
        self.assertTrue(m.valid(c, f))
        m.key(1005.0, "a", s)
        self.assertEqual(m.bid, (c, f))
        self.assertEqual(m.bidder, s)

    # ---- team rounds -------------------------------------------------------------------------------

    def test_team_tug_pulls_double_when_pressed_together(self):
        app, m = self.mini(pm.TeamTug, cpus=())
        self.assertEqual((m.left, m.right), ([1, 2], [3, 4]))
        m.key(1001.0, "a", 1)
        solo = -m.pos
        m.key(1001.1, "a", 2)  # within the sync window of P1's pull
        self.assertGreater(-m.pos - solo, solo * 2)
        self.assertEqual(m.synced[2], 1)
        m.key(1005.0, "a", 3)
        m.key(1006.0, "a", 4)  # too late to count as together
        self.assertEqual(m.synced[4], 0)
        self.assertLess(m.pos, 0)
        self.assertEqual(m.ranking(), [[1, 2], [3, 4]])

    def test_team_targets_need_both_teammates(self):
        app, m = self.mini(pm.TeamTargets, cpus=())
        d = m.target[0]
        m.key(1001.0, d, 1)
        self.assertEqual(m.hits, [0, 0])
        m.key(1001.3, d, 2)
        self.assertEqual(m.hits, [1, 0])
        self.assertNotEqual(m.target[0], d)
        d = m.target[0]
        m.key(1002.0, d, 1)
        m.key(1003.0, d, 2)  # too late
        self.assertEqual(m.hits, [1, 0])
        wrong = next(x for x in pm.DIRS if x != m.target[1])
        m.key(1004.0, wrong, 3)
        m.key(1004.1, m.target[1], 4)
        self.assertEqual(m.hits, [1, 0])
        self.assertEqual(m.ranking(), [[1, 2], [3, 4]])
        self.play(app, m, 1030.0)
        self.assertTrue(m.done)

    # ---- the pool ----------------------------------------------------------------------------------

    def test_rounds_need_enough_seats(self):
        app, t = make("party", cpu="none")
        app.seats = [1, 2]
        two = app.choices()
        self.assertIn(pm.LiarsDice, two)
        for cls in (pm.DrawOff, pm.Telephone, pm.TeamTug, pm.TeamTargets):
            self.assertNotIn(cls, two)
        app.seats = [1, 2, 3]
        self.assertIn(pm.DrawOff, app.choices())
        self.assertNotIn(pm.Telephone, app.choices())
        app.seats = [1, 2, 3, 4]
        for cls in pm.MINIGAMES:
            self.assertIn(cls, app.choices())
        self.assertEqual(len(pack_party.MINIGAMES), 30)

    def test_every_round_plays_through_with_cpus_at_both_stages(self):
        for cls in pm.MINIGAMES:
            for stage in (0.0, 1.0):
                app, m = self.mini(cls, stage=stage, seed=9)
                end = self.play(app, m, 1000.0 + 70.0)
                self.assertTrue(m.done, (cls.title, stage))
                self.assertLess(end - 1000.0, 60.0, (cls.title, stage))
                ranked = sorted(s for g in m.ranking() for s in g)
                self.assertEqual(ranked, [1, 2, 3, 4], cls.title)

    def test_phone_actions_reach_the_round_and_unknown_ones_fail(self):
        app, m = self.mini(pm.HowMany, cpus=(3, 4))
        app.round, app.rounds = 1, 6
        self.play(app, m, m.bid_at + 0.05)
        st = app.phone_state(1)
        self.assertEqual(st["widget"], "bid")
        self.assertEqual(app.phone("bid", {"bid": 12}, 1), {"ok": True})
        self.assertEqual(m.bid[1], 12)
        with self.assertRaises(ValueError):
            app.phone("bid", {"bid": 12}, 3)  # a CPU seat has no phone
        with self.assertRaises(ValueError):
            app.phone("nonsense", {}, 1)


if __name__ == "__main__":
    unittest.main()
