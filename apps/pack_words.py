"""Word games: Wordle, Hangman and Boggle, played with the gamepad (a cursor over an on-board keyboard).

Words come from words_data/common.txt (a few thousand common English words; the 5-letter ones are Wordle's
answers) and words_data/hangman.json (words by category). A guess in Wordle or Boggle also counts when the
system dictionary (/usr/share/dict/words) has it, so unusual but real words aren't refused.
"""

import datetime as dt
import hashlib
import json
import random
import re
import time
from pathlib import Path

from fonts import NORMAL, SMALL

from .arcade import fill
from .base import P, register
from .pack_puzzle import DEMO, MOVES, Puzzle

DATA = Path(__file__).with_name("words_data")
SYSTEM_DICT = Path("/usr/share/dict/words")
_lists = {}


def common_words():
    """The built-in word list, lowercase, as a set (cached)."""
    if "common" not in _lists:
        words = set()
        for line in (DATA / "common.txt").read_text().splitlines():
            line = line.split("#", 1)[0]
            words.update(w for w in line.split() if re.fullmatch("[a-z]{3,}", w))
        _lists["common"] = words
    return _lists["common"]


def system_words():
    """Plain lowercase words of the system dictionary, or an empty set without one (cached)."""
    if "system" not in _lists:
        words = set()
        try:
            if SYSTEM_DICT.is_file():
                for w in SYSTEM_DICT.read_text(errors="replace").split():
                    if 3 <= len(w) <= 16 and w.isascii() and w.isalpha() and w.islower():
                        words.add(w)
        except OSError:
            pass
        _lists["system"] = words
    return _lists["system"]


def is_word(w):
    w = w.lower()
    return w in common_words() or w in system_words()


def answers():
    """Wordle's answers: the common 5-letter words, in a fixed order (cached)."""
    if "five" not in _lists:
        _lists["five"] = sorted(w for w in common_words() if len(w) == 5)
    return _lists["five"]


def hangman_words():
    if "hangman" not in _lists:
        _lists["hangman"] = json.loads((DATA / "hangman.json").read_text())
    return _lists["hangman"]


# ---- Wordle --------------------------------------------------------------------------------------------------

def wordle_feedback(secret, guess):
    """Per letter: 'G' right letter right place, 'Y' in the word elsewhere, '.' not in the word (each letter of
    the secret is used once: a repeated letter in the guess only lights up as often as the secret has it)."""
    secret, guess = secret.lower(), guess.lower()
    fb = ["."] * len(guess)
    left = {}
    for i, (s, g) in enumerate(zip(secret, guess)):
        if s == g:
            fb[i] = "G"
        else:
            left[s] = left.get(s, 0) + 1
    for i, g in enumerate(guess):
        if fb[i] != "G" and left.get(g, 0) > 0:
            fb[i] = "Y"
            left[g] -= 1
    return fb


def day_key(now=None):
    return (dt.date.fromtimestamp(now) if now is not None else dt.date.today()).isoformat()


def daily_word(day):
    """The same word for everyone on a given day (an ISO date), picked from the answers."""
    words = answers()
    n = int(hashlib.sha1(f"wordle:{day}".encode()).hexdigest(), 16)
    return words[n % len(words)]


def consistent(word, guesses):
    """Could `word` be the secret, given these (guess, feedback) pairs?"""
    return all(wordle_feedback(word, g) == fb for g, fb in guesses)


KEYS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
WORDLE_FILE = "wordle.json"


@register
class Wordle(Puzzle):
    id = "wordle"
    title = "Wordle"
    description = ("Guess the 5-letter word in 6 tries. Move over the keyboard, A types a letter (A on OK checks "
                   "the word), B deletes. A filled cell is the right letter in the right place, an outlined cell "
                   "is in the word somewhere else, a plain one isn't in it. One word a day for everyone, or random "
                   "words; wins and the streak are kept.")
    params = [P("mode", "select", "daily", "Words", options=[["daily", "The word of the day"], ["random", "Random"]]),
              DEMO]
    AI_STEP = 0.07
    DEMO = 150
    PITCH = 7      # the grid's cells
    KX, KY = 40, 9  # where the keyboard starts
    KP = 4          # a key every 4 dots

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.stats = {}
        try:
            self.stats = dict(ctx.store._load(WORDLE_FILE, {}) or {})
        except Exception:
            pass
        self.stats.setdefault("played", 0)
        self.stats.setdefault("won", 0)
        self.stats.setdefault("streak", 0)
        self.stats.setdefault("best_streak", 0)
        self.stats.setdefault("days", {})
        self.guesses, self.cur, self.secret = [], "", ""
        self.kx = self.ky = 0
        self.msg = ("", 0)
        self.done_at = 0
        self.won = False
        self.daily = False
        self.day = None
        self.cands = None

    # ---- the puzzle ----------------------------------------------------------------------------------

    def reset(self):
        self.day = day_key(self.now)
        self.daily = self.p["mode"] == "daily" and not self.demo
        self.guesses, self.cur = [], ""
        self.kx, self.ky = 0, 0
        self.done_at, self.won = 0, False
        self.cands = None
        self.msg = ("", 0)
        done = self.stats["days"].get(self.day) if self.daily else None
        if done:  # today's is played: the result stays up; a random word after that
            self.secret = done["word"]
            self.guesses = [(g, wordle_feedback(self.secret, g)) for g in done.get("guesses", [])]
            self.won = bool(done.get("result"))
            self.done_at = self.now
            self.daily = False
            self.msg = ("DONE TODAY", self.now + 3)
            return
        self.secret = daily_word(self.day) if self.daily else random.choice(answers())

    def key_at(self, ky, kx):
        """(label, x, width) of a key; row 2 ends with OK and < (delete)."""
        row = KEYS[ky]
        if kx < len(row):
            return row[kx], self.KX + kx * self.KP, 3
        if ky == 2 and kx == len(row):
            return "OK", self.KX + len(row) * self.KP + 1, 7
        return "<", self.KX + len(row) * self.KP + 10, 3

    def row_len(self, ky):
        return len(KEYS[ky]) + (2 if ky == 2 else 0)

    def press(self, key, player):
        if self.done_at:
            return
        if key in MOVES:
            dx, dy = MOVES[key]
            if dy:
                self.ky = (self.ky + dy) % 3
                self.kx = min(self.kx, self.row_len(self.ky) - 1)
            else:
                self.kx = (self.kx + dx) % self.row_len(self.ky)
        elif key == "b":
            self.cur = self.cur[:-1]
        elif key == "a":
            label = self.key_at(self.ky, self.kx)[0]
            if label == "OK":
                self.submit()
            elif label == "<":
                self.cur = self.cur[:-1]
            elif len(self.cur) < 5:
                self.cur += label.lower()

    def submit(self):
        if len(self.cur) < 5:
            self.msg = ("TOO SHORT", self.now + 1.5)
            return
        if not is_word(self.cur):
            self.msg = ("NOT A WORD", self.now + 1.5)
            return
        fb = wordle_feedback(self.secret, self.cur)
        self.guesses.append((self.cur, fb))
        self.cur = ""
        if all(f == "G" for f in fb):
            self.won, self.done_at = True, self.now
            self.finish(len(self.guesses))
        elif len(self.guesses) >= 6:
            self.won, self.done_at = False, self.now
            self.finish(0)

    def finish(self, result):
        """Keep the stats (a daily word: once a day; the streak counts daily wins on consecutive days)."""
        if self.demo:
            return
        s = self.stats
        s["played"] += 1
        s["won"] += 1 if result else 0
        if self.daily:
            days = sorted(s["days"])
            yesterday = (dt.date.fromisoformat(self.day) - dt.timedelta(days=1)).isoformat()
            last_won = s.get("last_won")
            s["streak"] = (s["streak"] + 1 if last_won == yesterday else 1) if result else 0
            if result:
                s["last_won"] = self.day
            s["best_streak"] = max(s["best_streak"], s["streak"])
            s["days"][self.day] = {"word": self.secret, "result": result, "guesses": [g for g, _ in self.guesses]}
            for old in days[:-60]:  # keep the last two months
                s["days"].pop(old, None)
        try:
            self.ctx.store._save(WORDLE_FILE, s)
        except Exception:
            pass

    def tick(self, now):
        if self.done_at and now - self.done_at > 2.5:
            n = len(self.guesses)
            if self.won:
                detail = f"IN {n} {'TRY' if n == 1 else 'TRIES'}"
                if self.daily:
                    detail += f"  STREAK {self.stats['streak']}"
                self.game_over("YOU GOT IT", self.secret.upper(), detail)
            else:
                self.game_over("NOT THIS TIME", self.secret.upper(), "THAT WAS THE WORD")

    # ---- drawing -------------------------------------------------------------------------------------

    def frame_key(self, now):
        return self.ver, self.blink(now), now < self.msg[1]

    def letter_states(self):
        """The keyboard's best-known state per letter: G beats Y beats '.'."""
        rank = {"G": 3, "Y": 2, ".": 1}
        out = {}
        for g, fb in self.guesses:
            for ch, f in zip(g, fb):
                if rank[f] > rank.get(out.get(ch.upper(), ""), 0):
                    out[ch.upper()] = f
        return out

    def cell(self, c, col, row, ch, state):
        x, y = col * self.PITCH, row * self.PITCH
        g = SMALL.glyph(ch)
        gx = x + (self.PITCH - len(g[0])) // 2
        if state == "G":
            fill(c, x, y, 7, 7)
            c.text(gx, y + 1, ch, SMALL, v=0)
        elif state == "Y":
            c.rect(x, y, 7, 7)
            c.text(gx, y + 1, ch, SMALL)
        elif state == "." or state == "typed":
            c.text(gx, y + 1, ch, SMALL)

    def draw(self, c, now):
        for row in range(6):
            if row < len(self.guesses):
                g, fb = self.guesses[row]
                for col in range(5):
                    self.cell(c, col, row, g[col].upper(), fb[col])
            elif row == len(self.guesses) and not self.done_at:
                for col, ch in enumerate(self.cur):
                    self.cell(c, col, row, ch.upper(), "typed")
                if len(self.cur) < 5 and self.blink(now):
                    c.rect(len(self.cur) * self.PITCH + 2, row * self.PITCH + 6, 3, 1, fill=True)
            else:
                for col in range(5):
                    c.set(col * self.PITCH + 3, row * self.PITCH + 3)
        # the keyboard
        states = self.letter_states()
        for ky in range(3):
            y = self.KY + ky * 7
            for kx in range(self.row_len(ky)):
                label, x, w = self.key_at(ky, kx)
                st = states.get(label)
                if st == ".":
                    c.set(x + 1, y + 2)
                elif st == "G":
                    fill(c, x - 1, y - 1, w + 2, 7)
                    c.text(x, y, label, SMALL, v=0)
                else:
                    c.text(x, y, label, SMALL)
                    if st == "Y":
                        c.rect(x, y + 6, w, 1, fill=True)
                if (ky, kx) == (self.ky, self.kx) and not self.done_at and self.blink(now):
                    for yy in range(y - 1, y + 6):
                        for xx in range(x - 1, x + w + 1):
                            c.set(xx, yy, not c.get(xx, yy))
        # the header and the footer
        head = f"{len(self.guesses)}/6" if not self.done_at else ("WON" if self.won else "LOST")
        c.text(self.KX, 1, head, SMALL)
        if self.daily:
            c.text(self.KX + 20, 1, f"DAY {self.stats['streak']}", SMALL)
        elif self.demo:
            c.text(self.KX + 20, 1, "DEMO", SMALL)
        else:
            c.text(self.KX + 20, 1, "RANDOM", SMALL)
        if now < self.msg[1]:
            c.text(self.KX, 34, self.msg[0], SMALL)
        elif not self.done_at:
            c.text(self.KX, 34, "A=KEY B=DEL", SMALL)

    def draw_title(self, c, now):
        c.text_center(24, "THE WORD OF THE DAY" if self.p["mode"] == "daily" else "RANDOM WORDS", SMALL)
        s = self.stats
        if s["played"]:
            c.text_center(30, f"{s['won']}/{s['played']} WON  STREAK {s['streak']}", SMALL)

    def draw_over(self, c, now):
        head, big, detail = self.result
        c.text_center(2, head, NORMAL)
        c.text_center(12, big, NORMAL, scale=2)
        c.text_center(28, detail, SMALL)
        c.text_center(36, "A = PLAY AGAIN", SMALL)

    # ---- the demo ------------------------------------------------------------------------------------

    def path_to(self, start, ky, kx):
        """Button presses that move the cursor from `start` (row, col) to key (ky, kx), then A. -> (keys, end)"""
        keys = []
        y, x = start
        while y != ky:
            down = (ky - y) % 3 == 1
            keys.append("down" if down else "up")
            y = (y + (1 if down else -1)) % 3
        x = min(x, self.row_len(y) - 1)
        n = self.row_len(y)
        while x != kx:
            right = (kx - x) % n <= n // 2
            keys.append("right" if right else "left")
            x = (x + (1 if right else -1)) % n
        keys.append("a")
        return keys, (y, x)

    def ai(self, now):
        if self.done_at:
            return []
        if self.cands is None or len(self.cands[1]) != len(self.guesses):
            pool = [w for w in answers() if consistent(w, self.guesses)]
            self.cands = (pool, list(self.guesses))
        pool = self.cands[0]
        word = random.choice(pool) if pool else random.choice(answers())
        if not self.guesses and random.random() < 0.7:
            openers = [w for w in ("raise", "stare", "crane", "slate", "trace", "adieu") if w in answers()]
            word = random.choice(openers or [word])
        keys, at = [], (self.ky, self.kx)
        for ch in word.upper():
            r = next(i for i, row in enumerate(KEYS) if ch in row)
            more, at = self.path_to(at, r, KEYS[r].index(ch))
            keys += more
        more, at = self.path_to(at, 2, len(KEYS[2]))  # OK
        return keys + more


# ---- Hangman -------------------------------------------------------------------------------------------------

HANG_PARTS = 7  # base, pole, beam, rope, head, body with arms, legs
CATEGORIES = ["animals", "food", "places", "things"]


@register
class Hangman(Puzzle):
    id = "hangman"
    title = "Hangman"
    description = ("Guess the word a letter at a time before the figure is drawn (7 wrong letters). Move over the "
                   "letters, A guesses. Words come from a category; every word solved in a row is a point.")
    params = [P("category", "select", "any", "Words", options=[["any", "Any category"]] + [[k, k.capitalize()]
                                                                                        for k in CATEGORIES]), DEMO]
    AI_STEP = 0.1
    LX, LY = 16, 29  # the letter rows
    LP = 4

    def reset(self):
        self.score = 0
        self.guessed = set()
        self.cx = self.cy = 0
        self.solved_at = 0
        self.lost_at = 0
        self.used = []
        self.new_word()

    def new_word(self):
        words = hangman_words()
        cat = self.p["category"] if self.p["category"] in words else random.choice(CATEGORIES)
        pool = [w for w in words[cat] if w not in self.used] or words[cat]
        self.category, self.word = cat, random.choice(pool)
        self.used.append(self.word)
        self.guessed = set()
        self.misses = 0
        self.solved_at = 0

    def letter(self, cy, cx):
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[cy * 13 + cx]

    def revealed(self):
        return "".join(ch if ch in self.guessed else "_" for ch in self.word)

    def press(self, key, player):
        if self.lost_at or self.solved_at:
            return
        if key in MOVES:
            dx, dy = MOVES[key]
            self.cy = (self.cy + dy) % 2
            self.cx = (self.cx + dx) % 13
        elif key == "a":
            self.guess(self.letter(self.cy, self.cx).lower())

    def guess(self, ch):
        if ch in self.guessed:
            return
        self.guessed.add(ch)
        if ch not in self.word:
            self.misses += 1
            if self.misses >= HANG_PARTS:
                self.lost_at = self.now
                self.record(self.score)
        elif "_" not in self.revealed():
            self.score += 1
            self.solved_at = self.now

    def tick(self, now):
        if self.solved_at and now - self.solved_at > 1.5:
            self.new_word()
            self.ver += 1
        if self.lost_at and now - self.lost_at > 2.5:
            new = self.best() == self.score and self.score > 0
            self.game_over("HANGED", str(self.score), f"IT WAS {self.word.upper()}"[:21] if self.score == 0
                           else f"{self.score} WORDS  " + self.best_line(new, self.score))

    def frame_key(self, now):
        return self.ver, self.blink(now)

    def draw_gallows(self, c, x, y, parts):
        """The gallows and the figure in a 24 x 22 box, `parts` of them."""
        if parts >= 1:
            c.line(x, y + 21, x + 14, y + 21)      # base
        if parts >= 2:
            c.line(x + 4, y + 21, x + 4, y)        # pole
        if parts >= 3:
            c.line(x + 4, y, x + 16, y)            # beam
            c.line(x + 4, y + 4, x + 8, y)         # brace
        if parts >= 4:
            c.line(x + 16, y, x + 16, y + 3)       # rope
        if parts >= 5:
            c.circle(x + 16, y + 6, 2.5)           # head
        if parts >= 6:
            c.line(x + 16, y + 9, x + 16, y + 15)  # body
            c.line(x + 16, y + 10, x + 12, y + 13)  # arms
            c.line(x + 16, y + 10, x + 20, y + 13)
        if parts >= 7:
            c.line(x + 16, y + 15, x + 13, y + 20)  # legs
            c.line(x + 16, y + 15, x + 19, y + 20)

    def draw(self, c, now):
        self.draw_gallows(c, 2, 1, self.misses if not self.lost_at else HANG_PARTS)
        c.text(30, 1, self.category.upper(), SMALL)
        c.text(30, 8, f"MISS {self.misses}/{HANG_PARTS}", SMALL)
        c.text(30, 15, f"WORDS {self.score}", SMALL)
        if self.demo:
            c.text(66, 1, "DEMO", SMALL)
        elif self.best() is not None:
            c.text(66, 1, f"HI {self.best()}", SMALL)
        # the word: 7 dots a letter, centred; a solved or lost word is shown whole (the missed letters inverted)
        done = bool(self.solved_at or self.lost_at)
        word = self.word.upper() if done else self.revealed().upper()
        pitch = 7 if len(word) <= 8 else 5  # right of the gallows: 58 dots
        font = NORMAL if pitch == 7 else SMALL
        x0 = 26 + (58 - len(word) * pitch + 2) // 2
        for i, ch in enumerate(word):
            x = x0 + i * pitch
            if ch == "_":
                c.rect(x, 27, pitch - 2, 1, fill=True)
            elif self.lost_at and ch.lower() not in self.guessed:
                fill(c, x - 1, 20, pitch, 9)
                c.text(x, 21, ch, font, v=0)
            else:
                c.text(x, 21, ch, font)
        # the letters
        for cy in range(2):
            for cx in range(13):
                ch = self.letter(cy, cx)
                x, y = self.LX + cx * self.LP, self.LY + cy * 7
                if ch.lower() in self.guessed:
                    c.set(x + 1, y + 2)
                else:
                    c.text(x, y, ch, SMALL)
                if (cy, cx) == (self.cy, self.cx) and self.blink(now) and not (self.lost_at or self.solved_at):
                    for yy in range(y - 1, y + 6):
                        for xx in range(x - 1, x + 4):
                            c.set(xx, yy, not c.get(xx, yy))

    def draw_title(self, c, now):
        c.text_center(24, "GUESS THE WORD", SMALL)
        c.text_center(30, "A LETTER AT A TIME", SMALL)

    def ai(self, now):
        if self.lost_at or self.solved_at:
            return []
        pattern = self.revealed()
        wrong = {ch for ch in self.guessed if ch not in self.word}
        pool = [w for w in hangman_words()[self.category]
                if len(w) == len(self.word) and not (set(w) & wrong)
                and all(p == "_" or p == ch for p, ch in zip(pattern, w))
                and all(ch in self.guessed or p == "_" for p, ch in zip(pattern, w))]
        counts = {}
        for w in pool or hangman_words()[self.category]:
            for ch in set(w):
                if ch not in self.guessed:
                    counts[ch] = counts.get(ch, 0) + 1
        if not counts:
            return ["a"]
        best = max(counts, key=counts.get)
        if random.random() < 0.15:  # a slip now and then, so the figure gets drawn
            best = random.choice([ch for ch in "abcdefghijklmnopqrstuvwxyz" if ch not in self.guessed])
        i = ord(best) - 97
        ty, tx = divmod(i, 13)
        keys = []
        y, x = self.cy, self.cx
        if y != ty:
            keys.append("down")
            y = ty
        while x != tx:
            right = (tx - x) % 13 <= 6
            keys.append("right" if right else "left")
            x = (x + (1 if right else -1)) % 13
        return keys + ["a"]


# ---- Boggle --------------------------------------------------------------------------------------------------

DICE = ["AAEEGN", "ABBJOO", "ACHOPS", "AFFKPS", "AOOTTW", "CIMOTU", "DEILRX", "DELRVY", "DISTTY", "EEGHNW",
        "EEINSU", "EHRTVW", "EIOSST", "ELRTTY", "HIMNQU", "HLNNRZ"]
NEIGHBOURS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def boggle_points(word):
    n = len(word)
    return 0 if n < 3 else 1 if n <= 4 else 2 if n == 5 else 3 if n == 6 else 5 if n == 7 else 11


def roll_dice(rng=random):
    """A 4x4 Boggle board: the 16 dice shuffled, one face each. 'Q' stands for QU."""
    dice = list(DICE)
    rng.shuffle(dice)
    return [[rng.choice(dice[y * 4 + x]) for x in range(4)] for y in range(4)]


def spell(board, path):
    return "".join("qu" if board[y][x] == "Q" else board[y][x].lower() for x, y in path)


def adjacent(a, b):
    return a != b and abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1


def find_words(board, words=None, limit=None):
    """{word: path} for every word of the list that can be traced on the board (one path each)."""
    words = common_words() if words is None else words
    prefixes = set()
    for w in words:
        for i in range(1, len(w) + 1):
            prefixes.add(w[:i])
    found = {}

    def walk(x, y, path, so_far):
        so_far += "qu" if board[y][x] == "Q" else board[y][x].lower()
        if so_far not in prefixes:
            return
        path = path + [(x, y)]
        if len(so_far) >= 3 and so_far in words and so_far not in found:
            found[so_far] = path
        if limit and len(found) >= limit:
            return
        for dx, dy in NEIGHBOURS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < 4 and 0 <= ny < 4 and (nx, ny) not in path:
                walk(nx, ny, path, so_far)

    for y in range(4):
        for x in range(4):
            walk(x, y, [], "")
    return found


@register
class Boggle(Puzzle):
    id = "boggle"
    title = "Boggle"
    description = ("Find words in the 4x4 grid of letters before time runs out: a word runs through touching "
                   "letters, each used once. Move the cursor, A adds the letter (A again on the last one checks the "
                   "word), B takes one back. 3-4 letters 1 point, 5 letters 2, 6 letters 3, 7 letters 5, longer 11.")
    params = [P("minutes", "select", "2", "Time", options=[["1", "1 minute"], ["2", "2 minutes"], ["3", "3 minutes"]]),
              DEMO]
    AI_STEP = 0.12
    DEMO = 150
    GX, GY, GP = 1, 3, 9  # the grid's cells

    def reset(self):
        self.board = roll_dice()
        self.cx = self.cy = 0
        self.path = []
        self.found = []
        self.score = 0
        self.end = self.now + 60 * int(self.p["minutes"])
        self.msg = ("", 0)
        self.over_pending = False
        self.plan = None

    def word(self):
        return spell(self.board, self.path)

    def press(self, key, player):
        if self.now >= self.end:
            return
        if key in MOVES:
            dx, dy = MOVES[key]
            self.cx, self.cy = (self.cx + dx) % 4, (self.cy + dy) % 4
        elif key == "b":
            if self.path:
                self.path.pop()
        elif key == "a":
            cell = (self.cx, self.cy)
            if self.path and cell == self.path[-1]:
                self.submit()
            elif cell in self.path:
                self.msg = ("USED", self.now + 1)
            elif not self.path or adjacent(cell, self.path[-1]):
                self.path.append(cell)
            else:
                self.msg = ("NOT NEXT TO IT", self.now + 1)

    def submit(self):
        w = self.word()
        self.path = []
        if len(w) < 3:
            self.msg = ("TOO SHORT", self.now + 1.2)
        elif w in self.found:
            self.msg = ("GOT THAT ONE", self.now + 1.2)
        elif not is_word(w):
            self.msg = ("NOT A WORD", self.now + 1.2)
        else:
            pts = boggle_points(w)
            self.found.append(w)
            self.score += pts
            self.msg = (f"{w.upper()} +{pts}", self.now + 1.5)

    def tick(self, now):
        if now >= self.end and not self.over_pending:
            self.over_pending = True
            self.end_at = now
            self.path = []
            self.ver += 1
            new = self.record(self.score, key=f"boggle:{self.p['minutes']}m")
            self.msg = ("TIME!", now + 2)
            self._new = new
        if self.over_pending and now - self.end_at > 2:
            self.game_over("TIME'S UP", str(self.score), f"{len(self.found)} WORDS  " + self.best_line(self._new, self.score, f"boggle:{self.p['minutes']}m"))

    def frame_key(self, now):
        return self.ver, self.blink(now), int(max(0, self.end - now)), now < self.msg[1]

    def draw(self, c, now):
        for y in range(4):
            for x in range(4):
                ch = self.board[y][x]
                px, py = self.GX + x * self.GP, self.GY + y * self.GP
                on = (x, y) in self.path
                if on:
                    fill(c, px, py, 8, 8)
                if ch == "Q":
                    c.text(px, py + 2, "QU", SMALL, v=0 if on else 1)
                else:
                    c.text(px + 1, py + 1, ch, NORMAL, v=0 if on else 1)
                if (x, y) == (self.cx, self.cy) and self.blink(now) and now < self.end:
                    c.rect(px - 1, py - 1, 10, 10)
        x0 = 40
        left = max(0, int(self.end - now + 0.999))
        c.text(x0, 1, f"{left // 60}:{left % 60:02d}", NORMAL)
        c.text(x0 + 26, 1, f"{self.score} PTS", SMALL)
        c.text(x0, 10, f"{len(self.found)} WORDS", SMALL)
        w = self.word().upper()
        if w:
            c.text(x0, 18, w[:11], NORMAL if len(w) <= 7 else SMALL)
        if now < self.msg[1]:
            c.text(x0, 28, self.msg[0], SMALL)
        if self.demo:
            c.text(x0, 36, "DEMO", SMALL)
        elif self.found:
            c.text(x0, 36, ("LAST " + self.found[-1].upper())[:11], SMALL)

    def draw_title(self, c, now):
        c.text_center(24, "FIND THE WORDS", SMALL)
        c.text_center(30, f"{self.p['minutes']} MIN  A=LETTER B=BACK", SMALL)

    def demo_begin(self):
        found = find_words(self.board)  # long words first, give or take, as a good player would spot them
        self.plan = sorted(found.values(), key=lambda p: -len(p) + 2.5 * random.random())

    def ai(self, now):
        if self.now >= self.end or not self.plan:
            return []
        path = self.plan.pop(0)
        keys = []
        x, y = self.cx, self.cy
        for (tx, ty) in path:
            while x != tx:
                right = (tx - x) % 4 <= 2
                keys.append("right" if right else "left")
                x = (x + (1 if right else -1)) % 4
            while y != ty:
                down = (ty - y) % 4 <= 2
                keys.append("down" if down else "up")
                y = (y + (1 if down else -1)) % 4
            keys.append("a")
        keys.append("a")  # once more on the last letter: check the word
        return keys
