"""More Flipdot Party mini-games (apps/pack_party.py): WarioWare and Mario Party style quick rounds. Most are
Prompts: a handful of questions in a row that every seat answers once (sums, odd one out, what changed, which
is longer, in order, opposite, Simon says, button combo, grid recall, shell game, sort it); the rest run on
their own clock (moles, bank it, fill the glass). Every game reads self.stage (0 in round 1, 1 by round 6) and
asks for more as a match goes on: more to remember, subtler differences, decoys, less time."""

from fonts import NORMAL, SMALL

from .pack_party import ARROWS, Mini, box_text, fill_rect, invert_rect, quadrant

BUTTONS = ("a", "b", "x", "y")
DIRS = ("up", "left", "right", "down")
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

# 5x5 arrows and 7x7 shapes
ICON = {
    "up": ["00100", "01110", "11111", "00100", "00100"],
    "down": ["00100", "00100", "11111", "01110", "00100"],
    "left": ["00100", "01000", "11111", "01000", "00100"],
    "right": ["00100", "00010", "11111", "00010", "00100"],
}
SHAPES = {
    "square": ["1111111", "1111111", "1111111", "1111111", "1111111", "1111111", "1111111"],
    "circle": ["0011100", "0111110", "1111111", "1111111", "1111111", "0111110", "0011100"],
    "triangle": ["0001000", "0001000", "0011100", "0011100", "0111110", "0111110", "1111111"],
    "diamond": ["0001000", "0011100", "0111110", "1111111", "0111110", "0011100", "0001000"],
    "ring": ["0011100", "0100010", "1000001", "1000001", "1000001", "0100010", "0011100"],
    "cross": ["1100011", "1110111", "0111110", "0011100", "0111110", "1110111", "1100011"],
    "plus": ["0011100", "0011100", "1111111", "1111111", "1111111", "0011100", "0011100"],
    "corner": ["1111111", "1111111", "1100000", "1100000", "1100000", "1100000", "1100000"],
    "flag": ["1100000", "1111110", "1111111", "1111110", "1100000", "1100000", "1100000"],
    "stairs": ["1100000", "1100000", "1111000", "1111000", "1111110", "1111110", "1111111"],
}
ASYMMETRIC = ("triangle", "corner", "flag", "stairs")


def rot(rows):
    """A bitmap turned a quarter turn clockwise."""
    n = len(rows)
    return ["".join(rows[n - 1 - x][y] for x in range(n)) for y in range(n)]


def mirror(rows):
    return [r[::-1] for r in rows]


def bitmap(c, x, y, rows, v=1, scale=1):
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch == "1":
                if scale == 1:
                    c.set(x + dx, y + dy, v)
                else:
                    fill_rect(c, x + dx * scale, y + dy * scale, scale, scale, v)


def icon(c, cx, cy, key, v=1):
    """A key as a small picture centred on (cx, cy): an arrow, or a letter in a box."""
    if key in ICON:
        bitmap(c, cx - 2, cy - 2, ICON[key], v)
    else:
        c.rect(cx - 3, cy - 3, 7, 7, v)
        c.text(cx - 1, cy - 2, key.upper(), SMALL, v)


def cross_cells(w, h):
    """Four cells laid out as a d-pad (up, left, right, down): {key: (x, y, w, h)}, above the seats' row."""
    cw, ch = 20, 11
    cx = w // 2
    return {"up": (cx - cw // 2, 0, cw, ch), "left": (cx - cw // 2 - cw - 2, 12, cw, ch),
            "right": (cx + cw // 2 + 2, 12, cw, ch), "down": (cx - cw // 2, 24, cw, ch)}


class Prompts(Mini):
    """A few prompts in a row. Every seat answers each one once: a right answer scores (two for the first seat to
    get it, when FIRST_BONUS), a wrong one nothing. make(now) sets the prompt (self.answer, the key); check()
    may return None for games that take several presses. CPUs answer after a moment and are usually right."""

    kind = "brain"
    COUNT = 5
    ANSWER_S = 4.0
    REVEAL_S = 1.4
    FIRST_BONUS = True
    CPU_RIGHT = (0.8, 0.65)  # chance a CPU gets one right in the first round .. at full difficulty
    CPU_WAIT = (0.9, 2.2)
    keys = BUTTONS

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.n = 0
        self.ask_at = now
        self.next_prompt(now)

    def next_prompt(self, now):
        self.n += 1
        self.answers = {}
        self.first = None
        self.phase, self.phase_at = "ask", now
        self.ask_at = now  # make() may push it back (look first, then answer)
        self.make(now)
        self.cpu_due = {s: self.ask_at + self.rnd.uniform(*self.CPU_WAIT) for s in self.seats if self.g.is_cpu(s)}

    def make(self, now): ...

    def check(self, key, seat):
        return key == self.answer

    def cpu_right(self, seat):
        return self.answer

    def cpu_key(self, seat):
        """What a CPU presses: the right key most of the time, else a wrong one (None: nothing)."""
        right = self.cpu_right(seat)
        if self.rnd.random() < self.lvl(*self.CPU_RIGHT):
            return right
        wrong = [k for k in self.keys if k != right]
        return self.rnd.choice(wrong) if wrong else right

    def silent_right(self):
        """Seats that didn't answer when time's up were right (Simon's tricks)."""
        return False

    def time_up(self):
        return self.ANSWER_S

    def key(self, t, key, seat):
        if self.phase != "ask" or t < self.ask_at or seat in self.answers or key not in self.keys:
            return
        r = self.check(key, seat)
        if r is None:
            return
        self.answers[seat] = (key, r)
        if r:
            self.score[seat] += 2 if self.first is None and self.FIRST_BONUS else 1
            if self.first is None:
                self.first = seat

    def tick(self, now):
        if self.phase == "ask":
            for s, at in list(self.cpu_due.items()):
                if now >= at and s not in self.answers:
                    k = self.cpu_key(s)
                    if k is not None:
                        self.key(at, k, s)
                    if s in self.answers or k is None:
                        del self.cpu_due[s]
                    else:
                        self.cpu_due[s] = now + self.rnd.uniform(0.3, 0.6)  # the next press of a sequence
            if len(self.answers) == len(self.seats) or now - self.ask_at >= self.time_up():
                if self.silent_right():
                    for s in self.seats:
                        if s not in self.answers:
                            self.answers[s] = (None, True)
                            self.score[s] += 1
                self.phase, self.phase_at = "reveal", now
        elif now - self.phase_at >= self.REVEAL_S:
            if self.n >= self.COUNT or now - self.t0 >= self.length:
                self.done = True
            else:
                self.next_prompt(now)

    def draw(self, c, now):
        self.draw_prompt(c, now, self.phase == "reveal")
        self.draw_seats(c)

    def draw_prompt(self, c, now, reveal): ...

    def draw_seats(self, c):
        """The bottom row: each seat's name and score; inverted once it has answered right, an x after a wrong one."""
        n = len(self.seats)
        cw = self.w // n
        for i, s in enumerate(self.seats):
            x = i * cw
            txt = f"{self.g.name(s)} {self.score[s]}"
            box_text(c, x + 1, self.h - 5, txt)
            a = self.answers.get(s)
            if a is None:
                continue
            tw = SMALL.width(txt)
            if a[1]:
                invert_rect(c, x, self.h - 6, tw + 2, 7)
            elif x + tw + 7 <= (i + 1) * cw:
                c.text(x + tw + 3, self.h - 5, "X", SMALL)

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.score[s])


# ---- sums --------------------------------------------------------------------------------------------------

class QuickSum(Prompts):
    title = "QUICK SUMS"
    hint = "PICK THE ANSWER: A B X Y"
    length = 30
    CPU_RIGHT = (0.75, 0.6)

    def make(self, now):
        r = self.rnd
        if self.stage < 0.35:
            a, b = r.randint(1, 9), r.randint(1, 9)
            q, v = f"{a}+{b}", a + b
        elif self.stage < 0.7:
            a, b = r.randint(5, 20), r.randint(1, 12)
            q, v = (f"{a}-{b}", a - b) if r.random() < 0.5 else (f"{a}+{b}", a + b)
        else:
            if r.random() < 0.6:
                a, b = r.randint(2, 9), r.randint(2, 9)
                q, v = f"{a}x{b}", a * b
            else:
                a, b, d = r.randint(3, 15), r.randint(2, 12), r.randint(1, 9)
                q, v = f"{a}+{b}-{d}", a + b - d
        wrong = set()
        while len(wrong) < 3:
            w = v + r.choice([-3, -2, -1, 1, 2, 3, 10, -10])
            if w != v and w >= 0:
                wrong.add(w)
        opts = [v, *wrong]
        r.shuffle(opts)
        self.q, self.v = q, v
        self.opts = dict(zip(BUTTONS, opts))
        self.answer = next(k for k, o in self.opts.items() if o == v)

    def draw_prompt(self, c, now, reveal):
        scale = 2 if NORMAL.width(self.q) * 2 <= self.w - 4 else 1
        c.text_center(1 if scale == 2 else 4, self.q, NORMAL, scale=scale)
        for i, k in enumerate(BUTTONS):
            x = 6 + (i % 2) * 42
            y = 18 + (i // 2) * 8
            s = f"{k.upper()} {self.opts[k]}"
            c.text(x, y, s, SMALL)
            if reveal and k == self.answer:
                c.rect(x - 2, y - 2, SMALL.width(s) + 4, 9)


# ---- odd one out ------------------------------------------------------------------------------------------

class OddOneOut(Prompts):
    title = "ODD ONE OUT"
    hint = "WHICH ONE IS DIFFERENT? PRESS ITS DIRECTION"
    length = 32
    keys = DIRS
    CPU_RIGHT = (0.85, 0.6)

    def make(self, now):
        r = self.rnd
        self.answer = r.choice(DIRS)
        if self.stage < 0.3:  # another shape
            a, b = r.sample(list(SHAPES), 2)
            base, odd = SHAPES[a], SHAPES[b]
        elif self.stage < 0.65:  # the same shape turned or mirrored
            base = SHAPES[r.choice(ASYMMETRIC)]
            odd = rot(base) if r.random() < 0.5 else mirror(base)
            if odd == base:
                odd = rot(base)
        else:  # one dot different, at the shape's edge
            base = SHAPES[r.choice(list(SHAPES))]
            at = lambda x, y: base[y][x] if 0 <= x < 7 and 0 <= y < 7 else "0"  # noqa: E731
            edge = [(x, y) for y in range(7) for x in range(7)
                    if any(at(x + dx, y + dy) != base[y][x] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
            x, y = r.choice(edge)
            odd = [row if yy != y else row[:x] + ("0" if row[x] == "1" else "1") + row[x + 1:]
                   for yy, row in enumerate(base)]
        self.pics = {k: (odd if k == self.answer else base) for k in DIRS}

    def draw_prompt(self, c, now, reveal):
        for k, (x, y, w, h) in cross_cells(self.w, self.h).items():
            bitmap(c, x + (w - 7) // 2, y + (h - 7) // 2, self.pics[k])
            if reveal and k == self.answer:
                c.rect(x + 3, y - 1, w - 6, h + 2)


# ---- what changed -----------------------------------------------------------------------------------------

class WhatChanged(Prompts):
    title = "WHAT CHANGED"
    hint = "LOOK. ONE CORNER CHANGES. PRESS ITS DIRECTION"
    length = 40
    COUNT = 4
    keys = DIRS
    CPU_RIGHT = (0.7, 0.5)
    ANSWER_S = 4.5

    def make(self, now):
        r = self.rnd
        k = 1 + (self.stage >= 0.3) + (self.stage >= 0.7)  # shapes per cell
        self.before = {d: r.sample(list(SHAPES), k) for d in DIRS}
        self.after = {d: list(v) for d, v in self.before.items()}
        self.answer = r.choice(DIRS)
        cell = self.after[self.answer]
        others = [s for s in SHAPES if s not in cell]
        if k == 1 or r.random() < 0.6:
            cell[r.randrange(k)] = r.choice(others)  # one shape swapped
        elif r.random() < 0.5 and k > 1:
            cell.pop(r.randrange(k))  # one gone
        else:
            cell.append(r.choice(others))  # one more
        self.look_s = self.lvl(3.0, 2.0)
        self.ask_at = now + self.look_s + 1.0

    def draw_prompt(self, c, now, reveal):
        t = now - self.phase_at
        if t < self.look_s:
            pics, head = self.before, "LOOK"
        elif now < self.ask_at:
            c.text_center(14, "?", NORMAL, scale=2)
            return
        else:
            pics, head = self.after, "WHICH?"
        for k, (x, y, w, h) in cross_cells(self.w, self.h).items():
            shapes = pics[k]
            x0 = x + (w - (8 * len(shapes) - 1)) // 2
            for i, name in enumerate(shapes):
                bitmap(c, x0 + i * 8, y + (h - 7) // 2, SHAPES[name])
            if reveal and k == self.answer:
                c.rect(x - 1, y - 1, w + 2, h + 2)
        if not reveal:
            box_text(c, 1, 1, "LOOK" if t < self.look_s else "WHICH?")


# ---- which is longer --------------------------------------------------------------------------------------

class WhichIsLonger(Prompts):
    title = "LONGEST"
    hint = "WHICH BAR IS LONGEST? PRESS ITS ARROW"
    length = 30
    keys = DIRS
    CPU_RIGHT = (0.85, 0.6)
    ANSWER_S = 3.5

    def make(self, now):
        r = self.rnd
        self.shortest = self.stage >= 0.5 and r.random() < 0.4
        self.answer = r.choice(DIRS)
        base = r.randint(20, 34) if self.shortest else r.randint(44, 60)
        d = self.lvl(0.22, 0.06)
        steps = [1, 2, 3]
        r.shuffle(steps)
        lens = {}
        for k in DIRS:
            if k == self.answer:
                lens[k] = base
            else:
                f = 1 - d * steps.pop()
                lens[k] = min(70, round(base / f)) if self.shortest else max(6, round(base * f))
        self.lens = lens
        self.offs = {k: r.randint(0, 10) if self.stage >= 0.4 else 0 for k in DIRS}  # ends don't line up later

    def draw_prompt(self, c, now, reveal):
        for i, k in enumerate(("up", "left", "right", "down")):
            y = 2 + i * 8
            icon(c, 4, y + 2, k)
            x0 = 10 + self.offs[k]
            fill_rect(c, x0, y, self.lens[k], 4)
            if reveal and k == self.answer:
                c.rect(x0 - 2, y - 2, self.lens[k] + 4, 8)
        box_text(c, self.w - SMALL.width("SHORTEST?" if self.shortest else "LONGEST?") - 2, 30,
                 "SHORTEST?" if self.shortest else "LONGEST?")


# ---- in order ---------------------------------------------------------------------------------------------

class InOrder(Prompts):
    title = "IN ORDER"
    hint = "PRESS THE NUMBERS SMALLEST FIRST"
    length = 36
    COUNT = 3
    keys = DIRS
    ANSWER_S = 8.0
    CPU_RIGHT = (0.93, 0.9)  # per press
    CPU_WAIT = (0.8, 1.6)

    def make(self, now):
        r = self.rnd
        if self.stage >= 0.7:
            lo = r.randint(10, 80)
            nums = r.sample(range(lo, lo + 12), 4)  # close together
        else:
            nums = r.sample(range(1, 21 if self.stage < 0.4 else 60), 4)
        self.nums = dict(zip(DIRS, nums))
        self.order = sorted(DIRS, key=lambda k: self.nums[k])
        self.progress = {s: 0 for s in self.seats}
        self.hide_at = now + self.lvl(2.5, 1.5) if self.stage >= 0.4 else None
        self.answer = self.order[0]

    def check(self, key, seat):
        i = self.progress[seat]
        if key != self.order[i]:
            return False
        self.progress[seat] = i + 1
        return True if i + 1 == 4 else None

    def cpu_right(self, seat):
        return self.order[min(3, self.progress[seat])]

    def draw_prompt(self, c, now, reveal):
        hidden = self.hide_at is not None and now >= self.hide_at and not reveal
        for k, (x, y, w, h) in cross_cells(self.w, self.h).items():
            if reveal:
                s = f"{self.order.index(k) + 1}:{self.nums[k]}"
                c.text(x + (w - SMALL.width(s)) // 2, y + 3, s, SMALL)
            else:
                s = "?" if hidden else str(self.nums[k])
                c.text(x + (w - NORMAL.width(s)) // 2, y + 2, s, NORMAL)
            c.rect(x, y, w, h)
        if not reveal:
            # how far each seat has got: dots in its corner, clear of the cells
            for s in self.seats:
                x, y, w, h = quadrant(self.w, self.h, s)
                bx0, by = (x + 1 if x == 0 else x + w - 14), (2 if y == 0 else 31)
                for j in range(4):
                    bx = bx0 + j * 3
                    if j < self.progress[s]:
                        fill_rect(c, bx, by, 2, 2)
                    else:
                        c.set(bx, by)

    def draw_seats(self, c):
        if self.phase == "reveal":
            super().draw_seats(c)


# ---- opposite ---------------------------------------------------------------------------------------------

class Opposite(Prompts):
    title = "OPPOSITE"
    hint = "PRESS THE ARROW. INVERTED: PRESS THE OPPOSITE"
    length = 30
    kind = "action"
    keys = DIRS
    ANSWER_S = 2.5
    CPU_RIGHT = (0.85, 0.7)
    CPU_WAIT = (0.5, 1.4)

    def setup(self, now):
        self.count = self.ilvl(5, 8)
        super().setup(now)

    @property
    def COUNT(self):
        return self.count

    def make(self, now):
        self.shown = self.rnd.choice(DIRS)
        self.inverted = self.rnd.random() < self.lvl(0.3, 0.6)
        self.answer = OPPOSITE[self.shown] if self.inverted else self.shown
        self.gone_at = now + self.lvl(1.0, 0.5) if self.stage >= 0.6 else None  # later: shown briefly, then gone

    def draw_prompt(self, c, now, reveal):
        from .pack_party import arrow
        if self.gone_at is not None and now >= self.gone_at and not reveal:
            c.text_center(14, "?", NORMAL)
        else:
            arrow(c, self.w // 2, 17, self.shown, 10)
            if self.inverted:
                invert_rect(c, self.w // 2 - 16, 1, 33, 33)
        if reveal:
            box_text(c, self.w // 2 - 10, 29, f"= {self.answer.upper()}")
        c.text(1, 0, f"{self.n}/{self.COUNT}", SMALL)


# ---- Simon says -------------------------------------------------------------------------------------------

class SimonSays(Prompts):
    title = "SIMON SAYS"
    hint = "PRESS ONLY WHEN SIMON SAYS SO"
    length = 32
    COUNT = 6
    keys = BUTTONS + DIRS
    ANSWER_S = 3.0
    FIRST_BONUS = False
    CPU_RIGHT = (0.8, 0.65)
    CPU_WAIT = (0.7, 1.8)

    def make(self, now):
        r = self.rnd
        self.answer = r.choice(self.keys)
        self.trick = r.random() < self.lvl(0.3, 0.55)
        if not self.trick:
            self.lines = ["SIMON SAYS", f"PRESS {self.answer.upper()}"]
        else:
            tricks = [[f"PRESS {self.answer.upper()}", ""]]
            if self.stage >= 0.4:
                tricks.append(["SIMON SAID", f"PRESS {self.answer.upper()}"])
                tricks.append(["SIMONE SAYS", f"PRESS {self.answer.upper()}"])
            if self.stage >= 0.7:
                tricks.append(["SIMON SAYS", f"DON'T PRESS {self.answer.upper()}"])
            self.lines = r.choice(tricks)

    def check(self, key, seat):
        return (not self.trick) and key == self.answer

    def silent_right(self):
        return self.trick

    def cpu_key(self, seat):
        right = self.rnd.random() < self.lvl(*self.CPU_RIGHT)
        if self.trick:
            return None if right else self.answer
        return self.answer if right else self.rnd.choice([k for k in self.keys if k != self.answer])

    def draw_prompt(self, c, now, reveal):
        a, b = self.lines
        c.text_center(3, a, NORMAL)
        if b:
            c.text_center(14, b, NORMAL if NORMAL.width(b) <= self.w else SMALL)
        if reveal:
            box_text(c, (self.w - SMALL.width("DON'T!" if self.trick else "YES")) // 2, 27,
                     "DON'T!" if self.trick else "YES")
        else:
            c.text(1, 30, f"{self.n}/{self.COUNT}", SMALL)


# ---- button combo -----------------------------------------------------------------------------------------

class Combo(Prompts):
    title = "COMBO"
    hint = "ENTER THE BUTTONS IN ORDER"
    length = 36
    COUNT = 3
    keys = BUTTONS + DIRS
    ANSWER_S = 8.0
    CPU_RIGHT = (0.95, 0.9)  # per press
    CPU_WAIT = (0.8, 1.5)

    def make(self, now):
        n = self.ilvl(3, 7)
        self.seq = [self.rnd.choice(self.keys) for _ in range(n)]
        self.progress = {s: 0 for s in self.seats}
        self.hide_at = now + self.lvl(2.5, 1.5) if self.stage >= 0.45 else None
        self.answer = self.seq[0]

    def check(self, key, seat):
        i = self.progress[seat]
        if key != self.seq[i]:
            return False
        self.progress[seat] = i + 1
        return True if i + 1 == len(self.seq) else None

    def cpu_right(self, seat):
        return self.seq[min(len(self.seq) - 1, self.progress[seat])]

    def draw_prompt(self, c, now, reveal):
        hidden = self.hide_at is not None and now >= self.hide_at and not reveal
        x0 = (self.w - (len(self.seq) * 9 - 2)) // 2
        for i, k in enumerate(self.seq):
            if hidden:
                c.rect(x0 + i * 9, 3, 7, 7)
            else:
                icon(c, x0 + i * 9 + 3, 6, k)
        if hidden:
            c.text_center(13, "FROM MEMORY", SMALL)
        for i, s in enumerate(self.seats):
            x, y = 1 + (i % 2) * 42, 19 + (i // 2) * 8
            box_text(c, x, y, f"{self.g.name(s)} {self.progress[s]}/{len(self.seq)}")
        if not reveal:
            c.text(self.w - 14, 13, f"{self.n}/{self.COUNT}", SMALL)


# ---- grid recall ------------------------------------------------------------------------------------------

class GridRecall(Prompts):
    title = "GRID RECALL"
    hint = "REMEMBER THE LIT CELLS. WAS THAT ONE LIT? A YES, B NO"
    length = 40
    COUNT = 4
    keys = ("a", "b")
    ANSWER_S = 3.5
    CPU_RIGHT = (0.8, 0.6)

    def make(self, now):
        r = self.rnd
        self.size = 3 + (self.stage >= 0.5)
        cells = [(x, y) for x in range(self.size) for y in range(self.size)]
        self.lit = set(r.sample(cells, self.ilvl(3, 7)))
        self.probe = r.choice(cells)
        yes = self.probe in self.lit
        self.answer = "a" if yes else "b"
        self.look_s = self.lvl(2.5, 1.8)
        self.ask_at = now + self.look_s + 0.8

    def draw_prompt(self, c, now, reveal):
        t = now - self.phase_at
        cell = 7
        gx = (self.w - self.size * cell) // 2
        gy = (34 - self.size * cell) // 2
        for x in range(self.size):
            for y in range(self.size):
                px, py = gx + x * cell, gy + y * cell
                c.rect(px, py, cell + 1, cell + 1)
                if (t < self.look_s or reveal) and (x, y) in self.lit:
                    fill_rect(c, px + 2, py + 2, cell - 3, cell - 3)
                if now >= self.ask_at and (x, y) == self.probe:
                    c.rect(px + 1, py + 1, cell - 1, cell - 1)
                    if int(now * 4) % 2 and not reveal:
                        c.set(px + cell // 2, py + cell // 2)
        if t < self.look_s:
            box_text(c, 1, 14, "LOOK")
        elif now < self.ask_at:
            box_text(c, 1, 14, "...")
        elif reveal:
            box_text(c, 1, 14, "YES" if self.answer == "a" else "NO")
        else:
            box_text(c, 1, 10, "LIT?")
            box_text(c, 1, 18, "A/B")


# ---- shell game -------------------------------------------------------------------------------------------

class Shells(Prompts):
    title = "SHELL GAME"
    hint = "FOLLOW THE BALL: LEFT, UP OR RIGHT"
    length = 40
    COUNT = 2
    keys = ("left", "up", "right")
    ANSWER_S = 4.0
    CPU_RIGHT = (0.85, 0.55)
    SLOT_X = (14, 42, 70)
    SWAP_S = 0.55

    def make(self, now):
        r = self.rnd
        self.cups = [0, 1, 2]  # slot -> cup id
        self.ball = r.randrange(3)  # the cup id with the ball
        n = self.ilvl(3, 9)
        self.swaps = []
        for _ in range(n):
            a = r.randrange(3)
            b = r.choice([i for i in range(3) if i != a])
            self.swaps.append((a, b))
        self.show_s = 1.2
        self.ask_at = now + self.show_s + 0.4 + n * self.SWAP_S + 0.3
        # where the ball ends up
        cups = [0, 1, 2]
        for a, b in self.swaps:
            cups[a], cups[b] = cups[b], cups[a]
        self.answer = self.keys[cups.index(self.ball)]

    def positions(self, now):
        """x of each cup id at this moment (sliding during a swap), and whether the cups are raised."""
        t = now - self.phase_at
        cups = [0, 1, 2]
        xs = {cid: float(self.SLOT_X[i]) for i, cid in enumerate(cups)}
        if t < self.show_s:
            return xs, True
        t -= self.show_s + 0.4
        for a, b in self.swaps:
            if t <= 0:
                break
            f = min(1.0, t / self.SWAP_S)
            ca, cb = cups[a], cups[b]
            xa, xb = self.SLOT_X[a], self.SLOT_X[b]
            xs[ca] = xa + (xb - xa) * f
            xs[cb] = xb + (xa - xb) * f
            if f >= 1.0:
                cups[a], cups[b] = cb, ca
            t -= self.SWAP_S
        return xs, False

    def draw_prompt(self, c, now, reveal):
        xs, raised = self.positions(now)
        for i, x in enumerate(self.SLOT_X):
            icon(c, x, 29, self.keys[i])
        for cid, x in xs.items():
            x = int(round(x))
            y = 8 if (raised or reveal) and cid == self.ball else 14
            if (raised or reveal) and cid == self.ball:
                c.circle(x, 19, 2, fill=True)
            # a cup: a flat-topped bell
            fill_rect(c, x - 4, y, 9, 2)
            fill_rect(c, x - 5, y + 2, 11, 6)
            fill_rect(c, x - 6, y + 8, 13, 1)
        if now >= self.ask_at and not reveal:
            box_text(c, 1, 1, "WHERE?")
        elif raised:
            box_text(c, 1, 1, "WATCH")


# ---- sort it ----------------------------------------------------------------------------------------------

class SortIt(Prompts):
    title = "SORT IT"
    hint = "SQUARE: LEFT. CIRCLE: RIGHT"
    length = 30
    kind = "action"
    keys = DIRS
    FIRST_BONUS = True
    CPU_RIGHT = (0.85, 0.7)
    CPU_WAIT = (0.5, 1.3)

    def setup(self, now):
        self.count = self.ilvl(8, 10)
        self.rules = {"square": "left", "circle": "right"}
        if self.stage >= 0.35:
            self.rules["triangle"] = "down"
            self.hint = "SQUARE LEFT, CIRCLE RIGHT, TRIANGLE DOWN"
        self.swapped = False
        self.swap_at = self.count // 2 if self.stage >= 0.7 else None  # the rule flips half way
        super().setup(now)

    @property
    def COUNT(self):
        return self.count

    def time_up(self):
        return self.lvl(2.4, 1.6)

    def make(self, now):
        if self.swap_at is not None and self.n == self.swap_at + 1 and not self.swapped:
            self.swapped = True
            self.rules = {k: OPPOSITE[v] if v in ("left", "right") else v for k, v in self.rules.items()}
        self.shape = self.rnd.choice(list(self.rules))
        self.answer = self.rules[self.shape]

    def draw_prompt(self, c, now, reveal):
        bitmap(c, self.w // 2 - 7, 3, SHAPES[self.shape], scale=2)
        for shape, d in self.rules.items():
            x = {"left": 2, "right": self.w - 10, "down": self.w // 2 - 3}[d]
            y = {"left": 10, "right": 10, "down": 29}[d]
            bitmap(c, x, y, SHAPES[shape])
            icon(c, x + 3, y + 11 if d != "down" else y - 4, d)
        if self.swapped:
            box_text(c, 1, 1, "SWAPPED!")
        if reveal:
            box_text(c, (self.w - SMALL.width(self.answer.upper())) // 2, 19, self.answer.upper())
        else:
            c.text(self.w - 14, 1, f"{self.n}/{self.COUNT}", SMALL)


# ---- moles ------------------------------------------------------------------------------------------------

class Moles(Mini):
    title = "MOLES"
    hint = "WHACK THE MOLES: PRESS WHERE THEY POP UP"
    length = 30
    kind = "action"

    def setup(self, now):
        self.score = {s: 0 for s in self.seats}
        self.total = self.ilvl(8, 12)
        self.n = 0
        self.decoy_p = self.lvl(0.0, 0.4) if self.stage >= 0.3 else 0.0
        if self.decoy_p:
            self.hint = "WHACK THE MOLES. NOT THE HOLLOW ONES!"
        self.mole = None  # (dir, up_at, down_at, decoy)
        self.hit = None   # (seat, at)
        self.next_at = now + 1.0
        self.cpu_due = {}

    def pop(self, now):
        self.n += 1
        d = self.rnd.choice(DIRS)
        decoy = self.rnd.random() < self.decoy_p
        self.mole = (d, now, now + self.lvl(1.4, 0.8), decoy)
        self.hit = None
        self.cpu_due = {s: now + self.rnd.uniform(0.35, 0.75) for s in self.seats if self.g.is_cpu(s)}

    def key(self, t, key, seat):
        if key not in DIRS or self.mole is None or self.hit is not None:
            return
        d, up, down, decoy = self.mole
        if t < up or t > down:
            return
        if key == d and not decoy:
            self.score[seat] += 1
            self.hit = (seat, t)
        else:
            self.score[seat] -= 1  # the wrong hole, or a decoy

    def tick(self, now):
        if self.mole is None:
            if now >= self.next_at:
                if self.n >= self.total or now - self.t0 >= self.length:
                    self.done = True
                else:
                    self.pop(now)
            return
        d, up, down, decoy = self.mole
        for s, at in list(self.cpu_due.items()):
            if now >= at:
                del self.cpu_due[s]
                if decoy:
                    if self.rnd.random() < 0.15:
                        self.key(at, d, s)
                else:
                    self.key(at, d if self.rnd.random() < 0.85 else self.rnd.choice(DIRS), s)
        if (self.hit is not None and now - self.hit[1] >= 0.5) or now > down + 0.2:
            self.mole = None
            self.next_at = now + self.rnd.uniform(0.5, 1.3)

    def draw(self, c, now):
        cells = cross_cells(self.w, self.h)
        for k, (x, y, w, h) in cells.items():
            fill_rect(c, x + 3, y + h - 2, w - 6, 2)  # the hole
            if self.mole is not None and self.mole[0] == k:
                _, up, down, decoy = self.mole
                mx, my = x + w // 2, y + 1
                if decoy:
                    c.rect(mx - 4, my, 9, 8)
                    c.set(mx - 2, my + 3)
                    c.set(mx + 2, my + 3)
                else:
                    fill_rect(c, mx - 4, my, 9, 8)
                    c.set(mx - 2, my + 3, 0)
                    c.set(mx + 2, my + 3, 0)
                if self.hit is not None:
                    box_text(c, mx - 7, my + 2, "WHACK")
        n = len(self.seats)
        cw = self.w // n
        for i, s in enumerate(self.seats):
            box_text(c, i * cw + 1, self.h - 5, f"{self.g.name(s)} {self.score[s]}")
        box_text(c, 1, 1, f"{self.n}/{self.total}")

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.score[s])


# ---- bank it ----------------------------------------------------------------------------------------------

class BankIt(Mini):
    title = "BANK IT"
    hint = "THE POT GROWS. PRESS A TO BANK IT BEFORE IT BUSTS"
    length = 40
    kind = "luck"
    RUNS = 3

    def setup(self, now):
        self.banked = {s: 0 for s in self.seats}
        self.run = 0
        self.hidden = self.stage >= 0.5  # later: the pot's size hides, only its ticks show
        if self.hidden:
            self.hint = "THE POT GROWS UNSEEN. PRESS A TO BANK BEFORE IT BUSTS"
        self.start_run(now)

    def start_run(self, now):
        self.run += 1
        self.run_at = now + 1.0
        self.bust_at = self.run_at + self.rnd.uniform(self.lvl(2.5, 1.5), self.lvl(10, 7))
        self.took = {}  # seat -> pot when they banked
        self.bust = None
        self.cpu_goal = {s: self.rnd.uniform(4, 14) for s in self.seats if self.g.is_cpu(s)}

    def pot(self, now):
        return max(0, int((min(now, self.bust_at) - self.run_at) * 3))  # 3 a second

    def key(self, t, key, seat):
        if key == "a" and seat not in self.took and self.run_at <= t < self.bust_at and self.bust is None:
            self.took[seat] = self.pot(t)
            self.banked[seat] += self.took[seat]

    def tick(self, now):
        if self.bust is None:
            for s, goal in self.cpu_goal.items():
                if s not in self.took and self.pot(now) >= goal:
                    self.key(now, "a", s)
            if now >= self.bust_at or len(self.took) == len(self.seats):
                self.bust = now
        elif now - self.bust >= 2.0:
            if self.run >= self.RUNS or now - self.t0 >= self.length:
                self.done = True
            else:
                self.start_run(now)

    def draw(self, c, now):
        c.text(1, 0, f"RUN {self.run}/{self.RUNS}", SMALL)
        if now < self.run_at:
            c.text_center(12, "READY", NORMAL)
        elif self.bust is not None and len(self.took) < len(self.seats):
            c.text_center(8, "BUST!", NORMAL, scale=2)
        else:
            p = self.pot(now)
            if self.hidden:
                c.text_center(10, "?" * min(5, 1 + p // 6), NORMAL, scale=2)
            else:
                c.text_center(8, str(p), NORMAL, scale=2)
            r = 2 + int((now - self.run_at) * 3) % 3
            c.circle(self.w - 6, 4, r)  # a fuse that twitches
        n = len(self.seats)
        cw = self.w // n
        for i, s in enumerate(self.seats):
            x = i * cw
            box_text(c, x + 1, self.h - 5, f"{self.g.name(s)} {self.banked[s]}")
            if s in self.took:
                box_text(c, x + 1, self.h - 12, f"+{self.took[s]}")
            elif self.bust is not None and now >= self.run_at:
                box_text(c, x + 1, self.h - 12, "LOST")

    def ranking(self):
        return self.groups(self.seats, lambda s: -self.banked[s])


# ---- fill the glass ---------------------------------------------------------------------------------------

class FillGlass(Mini):
    title = "FILL THE GLASS"
    hint = "HOLD A TO FILL TO THE LINE. LET GO TO STOP"
    length = 16
    kind = "action"
    RATE = 9.0  # dots a second

    def setup(self, now):
        self.target = self.rnd.randint(5, 13)  # of 16
        self.level = {s: 0.0 for s in self.seats}
        self.locked = {}
        self.was = {s: False for s in self.seats}
        self.last = now
        self.go_at = now + 1.0
        self.hide_line = self.stage >= 0.4   # the line hides once you start pouring
        self.hide_fill = self.stage >= 0.7   # and so does the water
        if self.hide_fill:
            self.hint = "HOLD A TO FILL TO THE LINE. YOU WON'T SEE IT POUR"
        elif self.hide_line:
            self.hint = "HOLD A TO FILL TO THE LINE. THE LINE HIDES WHEN YOU START"
        self.cpu_hold = {s: (self.go_at + self.rnd.uniform(0.3, 1.5), (self.target + self.rnd.gauss(0, self.lvl(1.4, 1.0))) / self.RATE)
                         for s in self.seats if self.g.is_cpu(s)}

    def key(self, t, key, seat):
        pass  # pouring follows the held button

    def pouring(self, s, now):
        if self.g.is_cpu(s):
            at, dur = self.cpu_hold[s]
            return at <= now < at + dur
        return self.held(s, "a")

    def tick(self, now):
        dt = min(0.1, max(0.0, now - self.last))
        self.last = now
        if now < self.go_at:
            return
        for s in self.seats:
            if s in self.locked:
                continue
            on = self.pouring(s, now)
            if on:
                self.level[s] = min(16.0, self.level[s] + self.RATE * dt)
                self.was[s] = True
                if self.level[s] >= 16.0:
                    self.locked[s] = now  # overflowed
            elif self.was[s]:
                self.locked[s] = now
        if len(self.locked) == len(self.seats) or now - self.t0 >= self.length:
            self.done = True

    def draw(self, c, now):
        for s in self.seats:
            x, y, w, h = quadrant(self.w, self.h, s)
            self.label(c, s, x + 1, y)
            gx, gy = x + w // 2 - 6, y + 2
            # the glass: 12 wide, 16 tall inside
            c.line(gx - 1, gy, gx - 1, gy + 17)
            c.line(gx + 12, gy, gx + 12, gy + 17)
            c.line(gx - 1, gy + 17, gx + 12, gy + 17)
            started = self.was[s]
            show_line = not (self.hide_line and started) or s in self.locked
            if show_line:
                ly = gy + 17 - self.target
                for xx in range(gx - 3, gx + 15, 2):
                    c.set(xx, ly)
            show_fill = not (self.hide_fill and started) or s in self.locked
            lvl = int(round(self.level[s]))
            if show_fill and lvl > 0:
                fill_rect(c, gx, gy + 17 - lvl, 12, lvl)
            if s in self.locked:
                d = abs(self.level[s] - self.target)
                box_text(c, x + w - 16, y + h - 6, "OVER" if self.level[s] >= 16 else f"{d:.1f}")
        if now < self.go_at:
            box_text(c, (self.w - SMALL.width("READY")) // 2, 18, "READY")

    def ranking(self):
        return self.groups(self.seats, lambda s: round(abs(self.level[s] - self.target), 2) if s in self.locked else 99)


MINIGAMES = [QuickSum, OddOneOut, WhatChanged, WhichIsLonger, InOrder, Opposite, SimonSays, Combo, GridRecall,
             Shells, SortIt, Moles, BankIt, FillGlass]
