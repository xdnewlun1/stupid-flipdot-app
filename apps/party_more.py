"""Even more Flipdot Party rounds (apps/pack_party.py): rounds that use the phones (drawing, voting, bidding,
private dice) and team rounds for a full table. Every round still works from a controller alone, and CPU seats
play all of them: a pad draws like an etch-a-sketch (hold a direction; hold B to move without drawing), votes
and guesses are A/B/X/Y, bids and dice bids go up and down with the d-pad.

  Draw-off:        two seats draw the same word, the others vote for the better drawing.
  Telephone:       draw -> guess -> draw -> guess round the table; the chain is revealed at the end.
  Copy the beat:   a short rhythm flashes on the board; tap it back as exactly as you can.
  Keep the tempo:  a metronome runs, then goes silent: keep pressing on the beat.
  How many dots:   dots flash up; bid how many there were, closest without going over.
  Liar's dice:     three private dice each (on your phone); raise the bid or call the liar.
  Team tug:        2 v 2 tug of war where pulls count double when teammates press together.
  Team targets:    2 v 2: both teammates must press the team's arrow within a moment of each other.
"""

import math

from fonts import NORMAL, SMALL

from .pack_party import Mini, TugOfWar, banner, box_text, fill_rect, invert_rect
from .party_minis import BUTTONS, DIRS, ICON, bitmap, icon
from .party_pictionary import BITS, words

PW, PH = 40, 28  # a drawing in the drawing rounds (two fit side by side on the board)
STEP = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def pick_words(rnd, stage, n):
    """n different words to draw: easy ones early, medium later."""
    w = words()
    pool = list(w.get("easy") or [])
    if stage >= 0.5:
        pool += list(w.get("medium") or [])
    pool = sorted(set(pool)) or ["cat", "house", "sun", "tree", "boat"]
    return rnd.sample(pool, min(n, len(pool)))


class Pad:
    """A small drawing surface: strokes from a phone, or an etch-a-sketch cursor from a controller."""

    def __init__(self, w=PW, h=PH):
        self.w, self.h = w, h
        self.px = bytearray(w * h)
        self.cx, self.cy = w // 2, h // 2
        self.moved = -1e9
        self.dots = 0
        self.turn = 0  # bumped on every change the phone should redraw from

    def plot(self, x, y, v=1, size=1):
        for dy in range(size):
            for dx in range(size):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.w and 0 <= yy < self.h:
                    self.px[yy * self.w + xx] = 1 if v else 0

    def stroke(self, segs, v=1, size=1):
        """Line segments [[x0, y0, x1, y1], ...] from a phone."""
        size = 2 if size == 2 else 1
        for seg in list(segs or [])[:200]:
            try:
                x0, y0, x1, y1 = (int(round(float(n))) for n in seg[:4])
            except (TypeError, ValueError):
                continue
            n = max(abs(x1 - x0), abs(y1 - y0), 1)
            for i in range(n + 1):
                self.plot(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n, 1 if v else 0, size)
        self.dots = sum(self.px)

    def nudge(self, d, now, pen=True):
        """A controller: move the cursor a dot (at most 20 a second), drawing unless the pen is up."""
        if now - self.moved < 0.05 or d not in STEP:
            return
        self.moved = now
        dx, dy = STEP[d]
        self.cx = max(0, min(self.w - 1, self.cx + dx))
        self.cy = max(0, min(self.h - 1, self.cy + dy))
        if pen:
            self.plot(self.cx, self.cy)
            self.dots += 1

    def clear(self):
        self.px[:] = bytes(len(self.px))
        self.dots = 0
        self.turn += 1

    def bits(self):
        return bytes(self.px).translate(BITS).decode()

    def blit(self, c, x0, y0):
        w = self.w
        for i, v in enumerate(self.px):
            if v:
                c.set(x0 + i % w, y0 + i // w)

    def scribble(self, rnd, progress=1.0):
        """A CPU's drawing: a wobbly loop with a couple of strokes through it, revealed as `progress` grows."""
        pts = []
        cx, cy, rx, ry = self.w / 2, self.h / 2, self.w * 0.32, self.h * 0.32
        for i in range(48):
            a = 2 * math.pi * i / 48
            r = 1 + 0.25 * math.sin(a * 3 + rnd.random() * 0.2) + 0.1 * rnd.random()
            pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
        for i in range(3):
            a = rnd.uniform(0, math.pi)
            pts.append((cx + rx * 0.9 * math.cos(a), cy + ry * 0.9 * math.sin(a)))
            pts.append((cx - rx * 0.9 * math.cos(a), cy - ry * 0.9 * math.sin(a)))
        n = int(len(pts) * max(0.0, min(1.0, progress)))
        self.px[:] = bytes(len(self.px))
        for (x0, y0), (x1, y1) in zip(pts[:n], pts[1:n]):
            self.stroke([[x0, y0, x1, y1]])
        self.dots = sum(self.px)


def seat_row(c, m, score):
    """The bottom row: every seat's name and score."""
    n = len(m.seats)
    cw = c.w // n
    for i, s in enumerate(m.seats):
        box_text(c, i * cw + 1, c.h - 5, f"{m.g.name(s)} {score.get(s, 0)}")


# ---- draw-off ---------------------------------------------------------------------------------------------

class DrawOff(Mini):
    title = "DRAW-OFF"
    hint = "TWO DRAW THE WORD, THE REST VOTE: A LEFT, B RIGHT"
    kind = "brain"
    min_players = 3
    VOTE_S = 6.0
    REVEAL_S = 2.5

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.drawers = self.rnd.sample(self.seats, 2)
        self.word = pick_words(self.rnd, self.stage, 1)[0]
        self.pads = {s: Pad() for s in self.drawers}
        self.seeds = {s: self.rnd.randrange(1 << 30) for s in self.drawers}
        self.draw_s = self.lvl(16.0, 11.0)
        self.phase, self.phase_at = "draw", now
        self.votes = {}
        self.cpu_vote_at = {s: now + self.draw_s + self.rnd.uniform(0.8, 3.0) for s in self.seats
                            if self.g.is_cpu(s) and s not in self.drawers}

    def voters(self):
        return [s for s in self.seats if s not in self.drawers]

    def key(self, t, key, seat):
        if self.phase == "vote" and seat in self.voters() and seat not in self.votes and key in ("a", "b"):
            self.votes[seat] = self.drawers[0 if key == "a" else 1]

    def phone_state(self, seat):
        if self.phase == "draw" and seat in self.pads and not self.g.is_cpu(seat):
            p = self.pads[seat]
            return {"widget": "draw", "w": p.w, "h": p.h, "pic": p.bits(), "turn": p.turn, "word": self.word,
                    "left": round(max(0.0, self.draw_s - (self.g.now - self.phase_at)), 1)}
        if self.phase == "vote" and seat in self.voters():
            return {"widget": "vote", "choices": [{"seat": s, "name": self.g.name(s)} for s in self.drawers],
                    "voted": self.votes.get(seat)}
        return {"widget": "wait", "word": self.word if self.phase != "draw" or seat in self.drawers else None}

    def phone(self, action, data, seat, t):
        if action == "draw" and self.phase == "draw" and seat in self.pads:
            self.pads[seat].stroke(data.get("segs"), data.get("v", 1), data.get("size", 1))
            return {"ok": True}
        if action == "clear" and self.phase == "draw" and seat in self.pads:
            self.pads[seat].clear()
            return {"ok": True}
        if action == "vote" and self.phase == "vote":
            try:
                who = int(data.get("seat"))
            except (TypeError, ValueError):
                return {"ok": False}
            if who in self.drawers and seat in self.voters() and seat not in self.votes:
                self.votes[seat] = who
            return {"ok": seat in self.votes}
        return None

    def tick(self, now):
        if self.phase == "draw":
            for s, p in self.pads.items():
                if self.g.is_cpu(s):
                    p.scribble(_Fixed(self.seeds[s]), (now - self.phase_at) / self.draw_s)
                else:
                    for d in DIRS:
                        if self.held(s, d):
                            p.nudge(d, now, pen=not self.held(s, "b"))
            if now - self.phase_at >= self.draw_s:
                self.phase, self.phase_at = "vote", now
        elif self.phase == "vote":
            for s, at in list(self.cpu_vote_at.items()):
                if now >= at:
                    del self.cpu_vote_at[s]
                    self.votes[s] = self.rnd.choice(self.drawers)
            if len(self.votes) >= len(self.voters()) or now - self.phase_at >= self.VOTE_S:
                for s in self.drawers:
                    self.score[s] = sum(1 for v in self.votes.values() if v == s)
                best = max(self.score[s] for s in self.drawers)
                winners = {s for s in self.drawers if self.score[s] == best}
                for s, v in self.votes.items():
                    if v in winners:
                        self.score[s] += 1  # voted with the room
                self.phase, self.phase_at = "reveal", now
        elif now - self.phase_at >= self.REVEAL_S:
            self.done = True

    def draw(self, c, now):
        if self.phase == "draw":
            left = max(0, math.ceil(self.draw_s - (now - self.phase_at)))
            c.text_center(0, f"DRAW: {self.word.upper()} {left}", SMALL)
        elif self.phase == "vote":
            c.text_center(0, "VOTE: A LEFT, B RIGHT", SMALL)
        for i, s in enumerate(self.drawers):
            x0 = 1 + i * 43
            c.rect(x0 - 1, 6, PW + 2, PH + 2)
            self.pads[s].blit(c, x0, 7)
            tag = self.g.name(s)
            if self.phase != "draw":
                tag += f" {self.score[s]}"
            box_text(c, x0 + 1, c.h - 6, tag)
        if self.phase == "reveal":
            best = max(self.score[s] for s in self.drawers)
            winners = [s for s in self.drawers if self.score[s] == best]
            banner(c, (self.g.name(winners[0]) + " DREW BEST") if len(winners) == 1 else "A DRAW!")

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.score[s])


class _Fixed:
    """A tiny deterministic 'random' so a CPU's scribble holds still while it's being revealed."""

    def __init__(self, seed):
        self.x = (seed * 2654435761) & 0xFFFFFFFF

    def random(self):
        self.x = (self.x * 1103515245 + 12345) & 0x7FFFFFFF
        return (self.x >> 8) / float(1 << 23)

    def uniform(self, a, b):
        return a + (b - a) * self.random()


# ---- telephone --------------------------------------------------------------------------------------------

class Telephone(Mini):
    title = "TELEPHONE"
    hint = "DRAW, GUESS, DRAW, GUESS ROUND THE TABLE"
    kind = "brain"
    min_players = 4
    GUESS_S = 6.0
    REVEAL_S = 5.0
    CPU_RIGHT = (0.75, 0.55)

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.chain = list(self.seats)
        self.rnd.shuffle(self.chain)
        self.chain = self.chain[:4]
        ws = pick_words(self.rnd, self.stage, 7)
        self.word = ws[0]
        self.decoys = ws[1:]
        self.draw_s = self.lvl(10.0, 8.0)
        self.steps = []  # ("draw", seat, Pad, word) | ("guess", seat, options, chosen)
        self.seeds = [self.rnd.randrange(1 << 30) for _ in self.chain]
        self.i = 0
        self.start_step(now)

    def current_word(self):
        """What the next drawer draws: the last guess, or the word itself."""
        for kind, seat, data, extra in reversed(self.steps):
            if kind == "guess":
                return extra if extra else data[0]
        return self.word

    def start_step(self, now):
        seat = self.chain[self.i]
        self.phase_at = now
        if self.i % 2 == 0:
            self.steps.append(("draw", seat, Pad(), self.current_word()))
        else:
            opts = [self.word]
            prev = self.current_word()
            if prev != self.word and len(self.steps) >= 2:
                opts.append(prev)
            for d in self.decoys:
                if len(opts) >= 4:
                    break
                if d not in opts:
                    opts.append(d)
            self.rnd.shuffle(opts)
            self.steps.append(("guess", seat, opts, None))
            self.cpu_at = now + self.rnd.uniform(1.5, 3.5) if self.g.is_cpu(seat) else None

    def step(self):
        return self.steps[-1] if self.i < len(self.chain) else None

    def guess(self, seat, k, t):
        st = self.step()
        if st is None or st[0] != "guess" or st[1] != seat or st[3] is not None:
            return False
        if k not in BUTTONS:
            return False
        chosen = st[2][BUTTONS.index(k)] if BUTTONS.index(k) < len(st[2]) else None
        if chosen is None:
            return False
        self.steps[-1] = (st[0], st[1], st[2], chosen)
        return True

    def key(self, t, key, seat):
        self.guess(seat, key, t)

    def phone_state(self, seat):
        st = self.step()
        if st is None:
            return {"widget": "wait", "word": self.word}
        kind, who, data, extra = st
        if kind == "draw" and who == seat and not self.g.is_cpu(seat):
            return {"widget": "draw", "w": data.w, "h": data.h, "pic": data.bits(), "turn": data.turn, "word": extra,
                    "left": round(max(0.0, self.draw_s - (self.g.now - self.phase_at)), 1)}
        if kind == "guess" and who == seat:
            return {"widget": "choices", "options": list(data), "chosen": extra,
                    "pic": self.steps[-2][2].bits(), "w": PW, "h": PH}
        return {"widget": "wait", "word": None, "who": self.g.name(who), "doing": kind}

    def phone(self, action, data, seat, t):
        st = self.step()
        if st is None:
            return None
        if action == "draw" and st[0] == "draw" and st[1] == seat:
            st[2].stroke(data.get("segs"), data.get("v", 1), data.get("size", 1))
            return {"ok": True}
        if action == "clear" and st[0] == "draw" and st[1] == seat:
            st[2].clear()
            return {"ok": True}
        if action == "choose" and st[0] == "guess":
            try:
                i = int(data.get("i"))
            except (TypeError, ValueError):
                return {"ok": False}
            return {"ok": 0 <= i < 4 and self.guess(seat, BUTTONS[i], t)}
        return None

    def tick(self, now):
        st = self.step()
        if st is None:
            if now - self.phase_at >= self.REVEAL_S:
                self.done = True
            return
        kind, seat, data, extra = st
        if kind == "draw":
            if self.g.is_cpu(seat):
                data.scribble(_Fixed(self.seeds[self.i]), (now - self.phase_at) / self.draw_s)
            else:
                for d in DIRS:
                    if self.held(seat, d):
                        data.nudge(d, now, pen=not self.held(seat, "b"))
            if now - self.phase_at >= self.draw_s:
                self.advance(now)
        else:
            if self.cpu_at is not None and now >= self.cpu_at and extra is None:
                self.cpu_at = None
                right = self.rnd.random() < self.lvl(*self.CPU_RIGHT)
                want = self.word if right else self.rnd.choice([o for o in data if o != self.word] or data)
                self.guess(seat, BUTTONS[data.index(want)], now)
            if self.steps[-1][3] is not None or now - self.phase_at >= self.GUESS_S:
                self.advance(now)

    def advance(self, now):
        self.i += 1
        if self.i < len(self.chain):
            self.start_step(now)
        else:
            self.phase_at = now
            for k, (kind, seat, data, extra) in enumerate(self.steps):
                if kind == "guess" and extra == self.word:
                    self.score[seat] += 2
                    self.score[self.steps[k - 1][1]] += 1  # the drawing got it across

    def draw(self, c, now):
        st = self.step()
        if st is None:  # the chain, revealed: word, drawing, guess, drawing, guess
            c.text_center(0, f"IT WAS: {self.word.upper()}", SMALL)
            x = 1
            for kind, seat, data, extra in self.steps:
                if kind == "draw":
                    scale = 2
                    for i, v in enumerate(data.px):
                        if v:
                            c.set(x + (i % data.w) // scale, 8 + (i // data.w) // scale)
                    x += data.w // scale + 2
                else:
                    txt = (extra or "-").upper()[:6]
                    c.text(x, 14, txt, SMALL, v=1)
                    if extra == self.word:
                        invert_rect(c, x - 1, 13, SMALL.width(txt) + 2, 7)
                    x += SMALL.width(txt) + 3
            seat_row(c, self, self.score)
            return
        kind, seat, data, extra = st
        who = self.g.name(seat)
        if kind == "draw":
            left = max(0, math.ceil(self.draw_s - (now - self.phase_at)))
            c.text_center(0, f"{who} DRAWS {left}", SMALL)
            c.rect(21, 6, PW + 2, PH + 2)
            data.blit(c, 22, 7)
        else:
            c.text_center(0, f"{who} GUESSES: A B X Y", SMALL)
            pic = self.steps[-2][2]
            c.rect(1, 6, PW + 2, PH + 2)
            pic.blit(c, 2, 7)
            for i, (k, o) in enumerate(zip(BUTTONS, data)):
                y = 8 + i * 7
                c.text(46, y, f"{k.upper()} {o.upper()[:6]}", SMALL)
                if extra == o:
                    invert_rect(c, 45, y - 1, SMALL.width(f"{k.upper()} {o.upper()[:6]}") + 2, 7)
        c.text_center(c.h - 6, f"STEP {self.i + 1}/{len(self.chain)}", SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.score[s])


# ---- copy the beat ----------------------------------------------------------------------------------------

class CopyBeat(Mini):
    title = "COPY THE BEAT"
    hint = "WATCH THE RHYTHM, THEN TAP IT BACK WITH A"
    kind = "brain"
    BEAT = 0.55

    def setup(self, now):
        n = self.ilvl(3, 6)
        gaps = [0.5, 0.5, 1.0, 1.0, 1.5, 0.25] if self.stage >= 0.5 else [0.5, 1.0, 1.0, 1.5]
        self.gaps = [self.rnd.choice(gaps) * self.BEAT for _ in range(n)]
        self.onsets = [0.0]
        for g in self.gaps:
            self.onsets.append(self.onsets[-1] + g)
        self.listen_at = now + 1.0
        self.tap_at = self.listen_at + self.onsets[-1] + 1.5
        self.tap_s = self.onsets[-1] + 2.5
        self.taps = {s: [] for s in self.seats}
        self.phase = "listen"
        self.err = {}
        self.cpu_plan = {}
        for s in self.seats:
            if self.g.is_cpu(s):
                sd = self.lvl(0.05, 0.11)
                plan = [self.tap_at + 0.5 + o + self.rnd.gauss(0, sd) for o in self.onsets]
                if self.rnd.random() < self.lvl(0.1, 0.3) and len(plan) > 2:
                    plan.pop(self.rnd.randrange(1, len(plan)))
                self.cpu_plan[s] = plan

    def key(self, t, key, seat):
        if key == "a" and self.phase == "tap" and t >= self.tap_at and len(self.taps[seat]) <= len(self.onsets) + 2:
            self.taps[seat].append(t)

    def error(self, seat):
        taps = self.taps[seat]
        if len(taps) < 2:
            return 2.0
        got = [b - a for a, b in zip(taps, taps[1:])]
        n = min(len(got), len(self.gaps))
        e = sum(abs(g - r) for g, r in zip(got[:n], self.gaps[:n])) / n
        return e + 0.3 * abs(len(got) - len(self.gaps))

    def tick(self, now):
        if self.phase == "listen" and now >= self.tap_at:
            self.phase = "tap"
        elif self.phase == "tap":
            for s, plan in self.cpu_plan.items():
                while plan and now >= plan[0]:
                    self.key(plan.pop(0), "a", s)
            if now - self.tap_at >= self.tap_s:
                self.err = {s: self.error(s) for s in self.seats}
                self.phase, self.phase_at = "reveal", now
        elif self.phase == "reveal" and now - self.phase_at >= 3.0:
            self.done = True

    def draw(self, c, now):
        if self.phase == "listen":
            c.text_center(0, "LISTEN", SMALL)
            t = now - self.listen_at
            on = any(0 <= t - o < 0.12 for o in self.onsets)
            if on:
                fill_rect(c, 30, 9, 24, 20)
            else:
                c.rect(30, 9, 24, 20)
            x0, w = 4, c.w - 8
            span = max(0.5, self.onsets[-1])
            for o in self.onsets:  # the beats so far, on a line
                if o <= t:
                    x = x0 + int(w * o / span)
                    fill_rect(c, x, 32, 2, 4)
            c.line(x0, 37, x0 + w, 37)
        elif self.phase == "tap":
            c.text_center(0, "YOUR TURN: TAP IT BACK", SMALL)
            self.draw_taps(c, now)
        else:
            c.text_center(0, "HOW CLOSE?", SMALL)
            self.draw_taps(c, now)

    def draw_taps(self, c, now):
        x0, w = 14, c.w - 18
        span = max(0.5, self.onsets[-1] + 1.0)
        rows = len(self.seats)
        for i, s in enumerate(self.seats):
            y = 8 + i * (28 // rows)
            box_text(c, 1, y, self.g.name(s))
            c.line(x0, y + 5, x0 + w, y + 5)
            taps = self.taps[s]
            if taps:
                for t in taps:
                    x = x0 + int(w * min(span, t - taps[0]) / span)
                    fill_rect(c, x, y + 1, 2, 4)
            if self.phase == "reveal":
                e = self.err.get(s, 2.0)
                c.text(x0 + w - SMALL.width(f"{int(e * 1000)}") - 1, y - 1, f"{int(e * 1000)}", SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: round(self.err.get(s, 2.0), 3))


# ---- keep the tempo ---------------------------------------------------------------------------------------

class KeepTempo(Mini):
    title = "KEEP THE TEMPO"
    hint = "PRESS A ON EVERY BEAT, EVEN AFTER IT GOES SILENT"
    kind = "action"

    def setup(self, now):
        self.bpm = self.lvl(100, 132)
        self.period = 60.0 / self.bpm
        self.count = 8
        self.silent = self.ilvl(6, 10)
        self.start = now + 1.0
        self.end = self.start + (self.count + self.silent) * self.period
        self.presses = {s: [] for s in self.seats}
        self.err = {}
        self.phase = "play"
        self.cpu_plan = {}
        for s in self.seats:
            if self.g.is_cpu(s):
                drift, t, plan = 0.0, self.start, []
                for k in range(self.count + self.silent):
                    sd = 0.02 if k < self.count else self.lvl(0.03, 0.08)
                    drift += self.rnd.gauss(0, sd)
                    plan.append(self.start + k * self.period + drift)
                self.cpu_plan[s] = plan

    def key(self, t, key, seat):
        if key == "a" and self.phase == "play" and t >= self.start - 0.3 and len(self.presses[seat]) < 40:
            self.presses[seat].append(t)

    def error(self, seat):
        ps = self.presses[seat]
        total, missing = 0.0, 0
        for k in range(self.count, self.count + self.silent):
            beat = self.start + k * self.period
            near = min((abs(p - beat) for p in ps), default=None)
            if near is None or near > self.period / 2:
                missing += 1
            else:
                total += near
        hit = self.silent - missing
        return (total / hit if hit else 0.0) + 0.5 * missing / self.silent + (1.0 if not hit else 0.0)

    def tick(self, now):
        if self.phase == "play":
            for s, plan in self.cpu_plan.items():
                while plan and now >= plan[0]:
                    self.key(plan.pop(0), "a", s)
            if now >= self.end + self.period / 2:
                self.err = {s: self.error(s) for s in self.seats}
                self.phase, self.phase_at = "reveal", now
        elif now - self.phase_at >= 3.0:
            self.done = True

    def draw(self, c, now):
        k = (now - self.start) / self.period
        if self.phase == "play" and k < self.count:
            c.text_center(0, f"{int(self.bpm)} BPM", SMALL)
            x = c.w // 2 + int(math.sin(math.pi * k) * 30)  # a swinging bar: at an end on each beat
            c.line(c.w // 2, 30, x, 8)
            fill_rect(c, x - 2, 6, 5, 5)
            if k >= 0 and k % 1 < 0.15:
                fill_rect(c, 0, 32, c.w, 3)
        elif self.phase == "play":
            c.text_center(0, "SILENT: KEEP GOING", SMALL)
            c.rect(c.w // 2 - 2, 6, 5, 5)
            c.line(c.w // 2, 11, c.w // 2, 30)
        else:
            c.text_center(0, "DRIFT IN MS: LESS IS BETTER", SMALL)
        n = len(self.seats)
        cw = c.w // n
        for i, s in enumerate(self.seats):
            x = i * cw + 1
            ps = self.presses[s]
            lit = ps and now - ps[-1] < 0.1
            txt = self.g.name(s)
            box_text(c, x, c.h - 5, txt)
            if lit:
                invert_rect(c, x - 1, c.h - 6, SMALL.width(txt) + 2, 7)
            if self.phase == "reveal":
                c.text(x, c.h - 13, str(int(self.err.get(s, 0) * 1000)), SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: round(self.err.get(s, 2.0), 3))


# ---- how many dots ----------------------------------------------------------------------------------------

class HowMany(Mini):
    title = "HOW MANY DOTS?"
    hint = "BID HOW MANY FLASHED UP: CLOSEST WITHOUT GOING OVER"
    kind = "luck"
    BID_S = 8.0
    REVEAL_S = 3.0

    def setup(self, now):
        self.n = self.rnd.randint(self.ilvl(15, 35), self.ilvl(35, 90))
        cells = [(x, y) for x in range(2, self.w - 2, 2) for y in range(7, self.h - 8, 2)]
        self.dots = self.rnd.sample(cells, self.n)
        self.show_s = self.lvl(2.0, 1.2)
        self.show_at = now + 0.8
        self.bid_at = self.show_at + self.show_s
        self.phase = "show"
        self.lo, self.hi = 1, 120
        self.bid = {s: 40 for s in self.seats}
        self.locked = set()
        self.repeat = {}
        self.cpu_at = {s: self.bid_at + self.rnd.uniform(1.0, 4.0) for s in self.seats if self.g.is_cpu(s)}

    def set_bid(self, seat, v):
        if seat in self.locked or self.phase != "bid":
            return False
        self.bid[seat] = max(self.lo, min(self.hi, int(v)))
        return True

    def lock(self, seat):
        if self.phase == "bid" and seat in self.seats:
            self.locked.add(seat)

    def key(self, t, key, seat):
        if self.phase != "bid":
            return
        if key == "a":
            self.lock(seat)
        elif key in ("up", "down", "left", "right"):
            d = {"up": 1, "down": -1, "right": 5, "left": -5}[key]
            self.set_bid(seat, self.bid[seat] + d)
            self.repeat[seat] = t + 0.4

    def phone_state(self, seat):
        if self.phase == "bid" and seat in self.seats:
            return {"widget": "bid", "bid": self.bid[seat], "locked": seat in self.locked, "min": self.lo,
                    "max": self.hi, "left": round(max(0.0, self.BID_S - (self.g.now - self.bid_at)), 1)}
        return {"widget": "wait"}

    def phone(self, action, data, seat, t):
        if action == "bid":
            try:
                return {"ok": self.set_bid(seat, int(data.get("bid")))}
            except (TypeError, ValueError):
                return {"ok": False}
        if action == "lock":
            try:
                if data.get("bid") is not None:
                    self.set_bid(seat, int(data.get("bid")))
            except (TypeError, ValueError):
                pass
            self.lock(seat)
            return {"ok": True}
        return None

    def tick(self, now):
        if self.phase == "show":
            if now >= self.bid_at:
                self.phase = "bid"
        elif self.phase == "bid":
            for s, at in list(self.repeat.items()):  # held: keep going
                if now >= at:
                    for k, d in (("up", 1), ("down", -1), ("right", 5), ("left", -5)):
                        if self.held(s, k):
                            self.set_bid(s, self.bid[s] + d)
                    self.repeat[s] = now + 0.08
            for s, at in list(self.cpu_at.items()):
                if now >= at:
                    del self.cpu_at[s]
                    guess = self.n * self.rnd.uniform(0.75, 1.15)
                    self.set_bid(s, round(guess))
                    self.lock(s)
            if len(self.locked) >= len(self.seats) or now - self.bid_at >= self.BID_S:
                self.phase, self.phase_at = "reveal", now
        elif now - self.phase_at >= self.REVEAL_S:
            self.done = True

    def draw(self, c, now):
        if self.phase == "show":
            if now >= self.show_at:
                for x, y in self.dots:
                    c.set(x, y)
            c.text_center(0, "HOW MANY?", SMALL)
            return
        if self.phase == "bid":
            left = max(0, math.ceil(self.BID_S - (now - self.bid_at)))
            c.text_center(0, f"BID! A LOCKS IT IN  {left}", SMALL)
        else:
            c.text_center(0, f"THERE WERE {self.n}", SMALL)
            for x, y in self.dots:
                c.set(x, y)
        n = len(self.seats)
        cw = c.w // n
        for i, s in enumerate(self.seats):
            x = i * cw + 1
            txt = f"{self.g.name(s)} {self.bid[s]}"
            y = c.h - 5 if self.phase == "reveal" else 20
            if self.phase == "bid":
                c.text(x, y - 8, self.g.name(s), SMALL)
                c.text(x, y, str(self.bid[s]), NORMAL)
                if s in self.locked:
                    invert_rect(c, x - 1, y - 1, NORMAL.width(str(self.bid[s])) + 2, 9)
            else:
                box_text(c, x, y, txt)
                if self.bid[s] > self.n:
                    c.text(x + SMALL.width(txt) + 2, y, "X", SMALL)

    def ranking(self):
        def key(s):
            b = self.bid[s]
            return (1, b - self.n) if b > self.n else (0, self.n - b)
        return self.groups(self.seats, key)


# ---- liar's dice ------------------------------------------------------------------------------------------

class LiarsDice(Mini):
    title = "LIAR'S DICE"
    hint = "YOUR DICE ARE ON YOUR PHONE. RAISE THE BID OR CALL THE LIAR"
    kind = "luck"
    min_players = 2
    DICE = 3
    REVEAL_S = 4.0

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.turn_s = self.lvl(7.0, 5.0)
        self.games = 0
        self.max_games = 2
        self.new_game(now)

    def new_game(self, now):
        self.dice = {s: sorted(self.rnd.randint(1, 6) for _ in range(self.DICE)) for s in self.seats}
        self.order = list(self.seats)
        self.rnd.shuffle(self.order)
        self.bid = None  # (count, face)
        self.bidder = None
        self.turn = 0
        self.turn_at = now
        self.phase = "bid"
        self.pending = {s: [1, 2] for s in self.seats}  # what each pad is lining up (count, face)
        self.games += 1
        self.cpu_at = None

    def current(self):
        return self.order[self.turn % len(self.order)]

    def valid(self, count, face):
        total = self.DICE * len(self.seats)
        if not (1 <= face <= 6 and 1 <= count <= total):
            return False
        if self.bid is None:
            return True
        c0, f0 = self.bid
        return count > c0 or (count == c0 and face > f0)

    def place(self, seat, count, face, now):
        if self.phase != "bid" or seat != self.current() or not self.valid(count, face):
            return False
        self.bid, self.bidder = (int(count), int(face)), seat
        self.turn += 1
        self.turn_at = now
        self.cpu_at = None
        return True

    def challenge(self, seat, now):
        if self.phase != "bid" or seat != self.current() or self.bid is None:
            return False
        count, face = self.bid
        actual = sum(1 for s in self.seats for d in self.dice[s] if d == face)
        self.actual = actual
        liar = actual < count
        self.winner = seat if liar else self.bidder
        self.loser = self.bidder if liar else seat
        self.challenger = seat
        for s in self.seats:
            self.score[s] += 2 if s == self.winner else 0 if s == self.loser else 1
        self.phase, self.phase_at = "reveal", now
        return True

    def minimal(self):
        """The smallest raise over the bid."""
        if self.bid is None:
            return (1, 2)
        c, f = self.bid
        return (c, f + 1) if f < 6 else (c + 1, 1)

    def key(self, t, key, seat):
        if self.phase != "bid" or seat != self.current():
            return
        p = self.pending[seat]
        lo = self.minimal()
        if key == "up":
            p[1] = min(6, p[1] + 1)
        elif key == "down":
            p[1] = max(1, p[1] - 1)
        elif key == "right":
            p[0] = min(self.DICE * len(self.seats), p[0] + 1)
        elif key == "left":
            p[0] = max(1, p[0] - 1)
        elif key == "a":
            if not self.valid(*p):
                p[0], p[1] = lo
            self.place(seat, p[0], p[1], t)
        elif key == "b":
            self.challenge(seat, t)
        if p[0] < lo[0] or (p[0] == lo[0] and p[1] < lo[1]):
            p[0], p[1] = lo  # the pad always lines up at least the minimal raise

    def phone_state(self, seat):
        out = {"widget": "dice", "dice": list(self.dice.get(seat, [])), "bid": list(self.bid) if self.bid else None,
               "bidder": self.g.name(self.bidder) if self.bidder else None,
               "turn": self.phase == "bid" and seat == self.current(), "who": self.g.name(self.current()),
               "min": list(self.minimal()), "max": self.DICE * len(self.seats), "phase": self.phase}
        if self.phase == "reveal":
            out.update(actual=self.actual, winner=self.g.name(self.winner), loser=self.g.name(self.loser))
        return out

    def phone(self, action, data, seat, t):
        if action == "dicebid":
            try:
                return {"ok": self.place(seat, int(data.get("count")), int(data.get("face")), t)}
            except (TypeError, ValueError):
                return {"ok": False}
        if action == "challenge":
            return {"ok": self.challenge(seat, t)}
        return None

    def cpu_move(self, seat, now):
        """A CPU: how many of that face there probably are, from its own dice and the odds on the rest."""
        others = self.DICE * (len(self.seats) - 1)
        if self.bid is not None:
            c, f = self.bid
            expect = self.dice[seat].count(f) + others / 6
            if c > expect + self.lvl(1.6, 1.1) or (self.turn >= 2 * len(self.seats) and c > expect + 0.6):
                self.challenge(seat, now)
                return
        best = max(range(1, 7), key=lambda f: (self.dice[seat].count(f), f))
        count, face = self.minimal()
        if face < best and count <= self.dice[seat].count(best) + others // 6:
            face = best
        elif self.rnd.random() < 0.3:
            count, face = count + 1, best
        if not self.valid(count, face):
            count, face = self.minimal()
        self.place(seat, count, face, now)

    def tick(self, now):
        if self.phase == "bid":
            s = self.current()
            if self.g.is_cpu(s):
                if self.cpu_at is None:
                    self.cpu_at = now + self.rnd.uniform(1.2, 2.5)
                elif now >= self.cpu_at:
                    self.cpu_move(s, now)
            elif now - self.turn_at >= self.turn_s:  # out of time: the smallest raise for you
                self.place(s, *self.minimal(), now)
        elif now - self.phase_at >= self.REVEAL_S:
            if self.games < self.max_games and now - self.t0 < 30:
                self.new_game(now)
            else:
                self.done = True

    def draw(self, c, now):
        if self.phase == "bid":
            who = self.g.name(self.current())
            if self.bid:
                c.text_center(0, f"{self.g.name(self.bidder)} BIDS {self.bid[0]} X {self.bid[1]}S", SMALL)
            else:
                c.text_center(0, "OPENING BID", SMALL)
            left = max(0, math.ceil(self.turn_s - (now - self.turn_at)))
            c.text_center(c.h - 6, f"{who}: A RAISE  B LIAR  {left}", SMALL)
        else:
            c.text_center(0, f"{self.actual} X {self.bid[1]}S: {self.g.name(self.loser)} LOSES", SMALL)
        n = len(self.seats)
        cw = c.w // n
        for i, s in enumerate(self.seats):
            x = i * cw + 1
            box_text(c, x, 8, f"{self.g.name(s)} {self.score[s]}")
            if self.phase == "bid" and s == self.current():
                p = self.pending[s]
                box_text(c, x, 15, f"{p[0]}X{p[1]}" if not self.g.is_cpu(s) else "...")
            for k in range(self.DICE):
                dx, dy = x + k * 7, 23
                c.rect(dx, dy, 6, 6)
                if self.phase == "reveal":
                    v = self.dice[s][k]
                    pips = {1: [(2, 2)], 2: [(1, 1), (3, 3)], 3: [(1, 1), (2, 2), (3, 3)], 4: [(1, 1), (3, 1), (1, 3), (3, 3)],
                            5: [(1, 1), (3, 1), (2, 2), (1, 3), (3, 3)], 6: [(1, 1), (3, 1), (1, 2), (3, 2), (1, 3), (3, 3)]}[v]
                    for px, py in pips:
                        c.set(dx + px, dy + py)
                    if v == self.bid[1]:
                        invert_rect(c, dx, dy, 6, 6)
                else:
                    c.set(dx + 2, dy + 3)
                    c.set(dx + 3, dy + 3)

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.score[s])


# ---- team rounds (four seats) -----------------------------------------------------------------------------

class TeamTug(TugOfWar):
    title = "TEAM TUG"
    hint = "2 V 2: PULLS COUNT DOUBLE WHEN YOU PRESS TOGETHER"
    min_players = 4
    SYNC = 0.25

    def setup(self, now):
        self.left = self.seats[:2]
        self.right = self.seats[2:4]
        self.pos = 0.0
        self.last = {}
        self.synced = {s: 0 for s in self.seats}

    def key(self, t, key, seat):
        if key != "a" or self.done or seat not in self.seats:
            return
        team = self.left if seat in self.left else self.right
        mate = next((m for m in team if m != seat), None)
        pull = 0.012
        if mate is not None and t - self.last.get(mate, -1e9) <= self.SYNC:
            pull *= 2.2
            self.synced[seat] += 1
        self.last[seat] = t
        self.pos += (-1 if team is self.left else 1) * pull

    def tick(self, now):
        for s, at in self.cpu_at.items():
            if now >= at:
                self.key(now, "a", s)
                self.cpu_at[s] = now + self.rnd.uniform(0.14, 0.24)
        if abs(self.pos) >= 1 or now - self.t0 >= self.length:
            self.done = True


class TeamTargets(Mini):
    title = "TEAM TARGETS"
    hint = "2 V 2: BOTH OF YOU PRESS YOUR TEAM'S ARROW TOGETHER"
    kind = "action"
    min_players = 4
    length = 20
    WINDOW = 0.6

    def setup(self, now):
        self.teams = [self.seats[:2], self.seats[2:4]]
        self.target = [self.rnd.choice(DIRS), self.rnd.choice(DIRS)]
        self.hits = [0, 0]
        self.last = {}
        self.flash = [0.0, 0.0]

    def team_of(self, seat):
        return 0 if seat in self.teams[0] else 1

    def key(self, t, key, seat):
        if key not in DIRS or self.done:
            return
        k = self.team_of(seat)
        if key != self.target[k]:
            self.last.pop(seat, None)
            return
        self.last[seat] = t
        mate = next(m for m in self.teams[k] if m != seat)
        if t - self.last.get(mate, -1e9) <= self.WINDOW:
            self.hits[k] += 1
            self.flash[k] = t
            self.last.pop(seat, None)
            self.last.pop(mate, None)
            new = self.rnd.choice([d for d in DIRS if d != self.target[k]])
            self.target[k] = new

    def tick(self, now):
        for s, at in self.cpu_at.items():
            if now >= at:
                k = self.team_of(s)
                good = self.rnd.random() < self.lvl(0.8, 0.9)
                self.key(now, self.target[k] if good else self.rnd.choice(DIRS), s)
                self.cpu_at[s] = now + self.rnd.uniform(0.25, 0.6)
        if now - self.t0 >= self.length:
            self.done = True

    def draw(self, c, now):
        for k, team in enumerate(self.teams):
            x0 = k * (c.w // 2)
            names = " ".join(self.g.name(s) for s in team)
            c.text(x0 + (c.w // 2 - SMALL.width(names)) // 2, 1, names, SMALL)
            cx, cy = x0 + c.w // 4, 20
            if now - self.flash[k] < 0.25:
                fill_rect(c, cx - 10, cy - 10, 21, 21)
                bitmap(c, cx - 2, cy - 2, ICON[self.target[k]], 0)
            else:
                c.rect(cx - 10, cy - 10, 21, 21)
                icon(c, cx, cy, self.target[k])
            c.text_center(34, str(self.hits[k]), SMALL, x0=x0, w=c.w // 2)
        c.line(c.w // 2, 0, c.w // 2, c.h - 1)
        left = max(0, self.length - (now - self.t0))
        box_text(c, 1, c.h - 6, f"{left:.0f}")

    def ranking(self):
        if self.hits[0] == self.hits[1]:
            return [list(self.seats)]
        win = 0 if self.hits[0] > self.hits[1] else 1
        return [list(self.teams[win]), list(self.teams[1 - win])]


MINIGAMES = [DrawOff, Telephone, CopyBeat, KeepTempo, HowMany, LiarsDice, TeamTug, TeamTargets]
