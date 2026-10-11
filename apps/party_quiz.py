"""Buzzer quiz show for up to 4 players: buzz on a controller or phone, answer A-D.

Questions come from Claude (Settings → AI drawing's API key and model), written in batches per category and
difficulty and kept in quiz_cache.json, so a game never waits for the API: the batch is fetched in the
background while players join, and the built-in set (party_data/quiz.json) fills any gap. A buzz slams that
player's quarter of the board (a burst of flips), then they have a few seconds to answer: A, B, X, Y on a
GameCube pad (or the A-D buttons /play shows). A right answer scores, a wrong one costs half and locks that
player out of the question; buzzing while the question is still being read locks you out too.
"""

import hashlib
import json
import logging
import random
import threading
import time
from pathlib import Path

from canvas import wrap_text
from fonts import NORMAL, SMALL

from .base import P, register
from .pack_party import CPU_MODES, PartyGame, box_text, fill_rect, invert_rect, quadrant

log = logging.getLogger("quiz")

DATA = Path(__file__).with_name("party_data") / "quiz.json"
CACHE_FILE = "quiz_cache.json"
BATCH = 20           # questions per Claude request
KEEP = 200           # generated questions kept per category and difficulty
RECENT = 300         # question ids remembered as asked (not repeated until the rest are used)
CATEGORIES = [["mixed", "Mixed"], ["general", "General knowledge"], ["science", "Science"], ["geography", "Geography"],
              ["history", "History"], ["music", "Music"], ["film", "Film and TV"], ["sports", "Sports"],
              ["food", "Food and drink"], ["nature", "Animals and nature"], ["tech", "Technology"]]
LEVELS = [["easy", "Easy (kids can play)"], ["medium", "Medium"], ["hard", "Hard"]]
POINTS = {"easy": 100, "medium": 200, "hard": 300}
READ_S = 2.0         # buzzing this soon after a question appears locks you out of it
OPEN_S = 15.0        # time to buzz
ANSWER_S = 8.0       # time to answer after buzzing
SLAM_S = 0.6         # the buzzer's quarter flips this long
REVEAL_S = 3.5
ANSWER_KEYS = {"a": 0, "b": 1, "x": 2, "y": 3}
LETTERS = "ABCD"
SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {
        "type": "object",
        "properties": {"q": {"type": "string"}, "answers": {"type": "array", "items": {"type": "string"}},
                       "correct": {"type": "integer"}},
        "required": ["q", "answers", "correct"], "additionalProperties": False}}},
    "required": ["questions"], "additionalProperties": False,
}

_lock = threading.Lock()
_fetching = set()
_builtin = None


def builtin():
    global _builtin
    if _builtin is None:
        try:
            _builtin = json.loads(DATA.read_text())["questions"]
        except (OSError, ValueError, KeyError) as e:
            log.warning("can't read the built-in quiz questions: %s", e)
            _builtin = []
    return _builtin


def qid(q):
    return hashlib.sha1(q["q"].strip().lower().encode()).hexdigest()[:12]


def clean(q, cat, diff):
    """A question in our format, or None when it won't fit the board and the buttons."""
    try:
        text = " ".join(str(q.get("q") or "").split())
        answers = [" ".join(str(a).split()) for a in q.get("answers") or q.get("a") or []]
        correct = int(q.get("correct", q.get("c", -1)))
    except (TypeError, ValueError, AttributeError):
        return None
    if not (8 <= len(text) <= 120) or len(answers) != 4 or not 0 <= correct < 4:
        return None
    if any(not a or len(a) > 28 for a in answers) or len({a.lower() for a in answers}) != 4:
        return None
    return {"q": text, "a": answers, "c": correct, "cat": cat, "d": diff}


def prompt(cat, diff, n, avoid):
    topic = "a mix of general knowledge topics" if cat == "mixed" else dict(CATEGORIES).get(cat, cat)
    level = {"easy": "easy: a 10-year-old should know most answers",
             "medium": "medium: an adult pub quiz",
             "hard": "hard: for keen quiz players"}.get(diff, diff)
    lines = [
        f"Write {n} multiple-choice trivia questions for a family quiz show played on a flip-dot display.",
        f"Topic: {topic}. Difficulty: {level}.",
        "Each question is at most 90 characters; each of the 4 answers is at most 16 characters, and exactly one "
        "is right (give its index 0-3 as correct). Vary the position of the right answer. Make the wrong answers "
        "plausible. Only facts that won't change over time; no trick questions.",
    ]
    if avoid:
        lines.append("Don't repeat any of these questions: " + " | ".join(avoid[:40]))
    return "\n".join(lines)


def generate(settings, cat, diff, n=BATCH, timeout=120):
    """Ask Claude for a batch of questions. Returns the ones that pass clean(); raises aidraw.AiError."""
    import aidraw
    ai = settings.get("ai") or {}
    key = (ai.get("api_key") or "").strip()
    if not key:
        raise aidraw.AiError("Add an Anthropic API key in Settings → AI drawing first.", 400)
    model = ai.get("model") or aidraw.DEFAULT_MODEL
    body = {"model": model, "max_tokens": 8000,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": prompt(cat, diff, n, [])}]}
    if model in aidraw.FALLBACK_MODELS:
        body["fallbacks"] = "default"
    resp = aidraw.call_api(key, body, timeout=timeout)
    if resp.get("stop_reason") == "refusal":
        raise aidraw.AiError("The model declined to write quiz questions.", 422)
    text = "".join(b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
    try:
        items = json.loads(text)["questions"]
    except (ValueError, KeyError, TypeError):
        raise aidraw.AiError("The model didn't send back questions in the expected format.") from None
    return [q for q in (clean(x, cat, diff) for x in items) if q]


class Bank:
    """Questions per (category, difficulty): Claude's cached batches first, then the built-in set; asked ones are
    remembered so they don't come round again until the rest have been used."""

    def __init__(self, store):
        self.store = store

    def _data(self):
        data = self.store._load(CACHE_FILE, None)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("batches", {})
        data.setdefault("asked", [])
        return data

    @staticmethod
    def _key(cat, diff):
        return f"{cat}:{diff}"

    def cached(self, cat, diff):
        with _lock:
            return list(self._data()["batches"].get(self._key(cat, diff), []))

    def fresh_count(self, cat, diff):
        with _lock:
            data = self._data()
            asked = set(data["asked"])
            return sum(1 for q in data["batches"].get(self._key(cat, diff), []) if qid(q) not in asked)

    def add(self, cat, diff, questions):
        with _lock:
            data = self._data()
            have = data["batches"].setdefault(self._key(cat, diff), [])
            seen = {qid(q) for q in have}
            for q in questions:
                if qid(q) not in seen:
                    have.append(q)
                    seen.add(qid(q))
            del have[:-KEEP]
            self.store._save(CACHE_FILE, data)

    def take(self, cat, diff, n, use_ai=True, rnd=random):
        """n questions, fresh ones first: generated, then built-in; the least recently asked fill the rest."""
        with _lock:
            data = self._data()
            asked = data["asked"]
            order = {k: i for i, k in enumerate(asked)}
            gen = data["batches"].get(self._key(cat, diff), []) if use_ai else []
            pool = [q for q in builtin() if (cat == "mixed" or q["cat"] == cat) and q["d"] == diff]
            pool = pool or [q for q in builtin() if cat == "mixed" or q["cat"] == cat] or list(builtin())
            fresh_gen = [q for q in gen if qid(q) not in order]
            fresh_pool = [q for q in pool if qid(q) not in order]
            rnd.shuffle(fresh_gen)
            rnd.shuffle(fresh_pool)
            out = (fresh_gen + fresh_pool)[:n]
            if len(out) < n:  # the least recently asked; then other difficulties, then any category
                wider = [q for q in builtin() if cat == "mixed" or q["cat"] == cat] + list(builtin())
                ids = {qid(q) for q in out}
                for more in (gen + pool, wider):
                    old = sorted((q for q in more if qid(q) not in ids), key=lambda q: order.get(qid(q), -1))
                    for q in old[:n - len(out)]:
                        if qid(q) not in ids:
                            out.append(q)
                            ids.add(qid(q))
            for q in out:
                k = qid(q)
                if k in order:
                    asked.remove(k)
                asked.append(k)
            del asked[:-RECENT]
            self.store._save(CACHE_FILE, data)
        return [dict(q) for q in out]


def refill(store, cat, diff, want, on_error=None):
    """Fetch a batch in the background when fewer than `want` unasked generated questions are cached."""
    key = (id(store), cat, diff)
    bank = Bank(store)
    if bank.fresh_count(cat, diff) >= want or not ((store.settings.get("ai") or {}).get("api_key") or "").strip():
        return None
    with _lock:
        if key in _fetching:
            return None
        _fetching.add(key)

    def run():
        try:
            bank.add(cat, diff, generate(store.settings, cat, diff))
        except Exception as e:
            log.info("quiz questions from Claude failed: %s", e)
            if on_error:
                on_error(str(e))
        finally:
            with _lock:
                _fetching.discard(key)
    t = threading.Thread(target=run, daemon=True, name="quiz-questions")
    t.start()
    return t


@register
class Quiz(PartyGame):
    id = "quiz"
    title = "Buzzer Quiz"
    description = ("A quiz show for up to 4 players: buzz on a controller or phone, answer A-D. Claude writes the "
                   "questions when an API key is set (Settings → AI drawing), otherwise the built-in questions are used.")
    phone_mode = "quiz"
    join_hint = "A TO JOIN, START GO"
    params = [
        P("category", "select", "mixed", "Category", options=CATEGORIES),
        P("difficulty", "select", "medium", "Difficulty", options=LEVELS),
        P("questions", "number", 10, "Questions", min=3, max=30),
        P("source", "select", "auto", "Questions from",
          options=[["auto", "Claude when an API key is set, else built-in"], ["builtin", "Built-in questions only"]]),
        P("cpu", "select", "none", "CPU players", options=CPU_MODES),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.ai_note = None
        self.fetcher = None
        if self.p["source"] == "auto":
            try:  # write the next batch while players join
                self.fetcher = refill(ctx.store, self.p["category"], self.p["difficulty"],
                                      int(self.p["questions"] or 10), self._ai_failed)
            except Exception as e:
                log.info("quiz: %s", e)

    def _ai_failed(self, msg):
        self.ai_note = msg

    def start_game(self, now):
        n = max(1, int(self.p["questions"] or 10))
        self.qs = Bank(self.ctx.store).take(self.p["category"], self.p["difficulty"], n,
                                            use_ai=self.p["source"] == "auto", rnd=self.rnd)
        for q in self.qs:  # the right answer isn't always in the same place
            right = q["a"][q["c"]]
            self.rnd.shuffle(q["a"])
            q["c"] = q["a"].index(right)
        self.qi = -1
        self.result = None
        self.next_question(now)

    def points(self):
        return POINTS.get(self.qs[self.qi].get("d") or self.p["difficulty"], 200)

    def next_question(self, now):
        self.qi += 1
        self.phase, self.phase_at = "read", now
        self.open_left = OPEN_S
        self.locked = set()
        self.early = set()
        self.buzzer = None
        self.buzz_at = None
        self.result = None
        self.cpu_buzz = {s: now + READ_S + self.rnd.uniform(1.5, 9) for s in self.seats if self.is_cpu(s)}
        self.rev += 1

    @property
    def q(self):
        return self.qs[self.qi]

    # ---- rules --------------------------------------------------------------------------------

    def buzz(self, t, seat):
        if seat in self.locked or seat not in self.seats:
            return False
        if self.phase == "read":
            self.locked.add(seat)  # buzzed before the question was read: out of this one
            self.early.add(seat)
            self.rev += 1
            return False
        if self.phase != "open":
            return False
        self.open_left -= t - self.phase_at
        self.buzzer, self.buzz_at = seat, t
        self.phase, self.phase_at = "answer", t
        self.rev += 1
        return True

    def answer(self, t, seat, i):
        if self.phase != "answer" or seat != self.buzzer or t - self.buzz_at < SLAM_S * 0.5:
            return None
        right = i == self.q["c"]
        pts = self.points()
        self.totals[seat] += pts if right else -(pts // 2)
        self.result = {"seat": seat, "right": right, "choice": i, "points": pts if right else -(pts // 2)}
        self.rev += 1
        if right:
            self.phase, self.phase_at = "reveal", t
        else:
            self.locked.add(seat)
            self.buzzer = None
            if all(s in self.locked for s in self.seats) or self.open_left <= 0:
                self.phase, self.phase_at = "reveal", t
            else:
                self.phase, self.phase_at = "open", t  # the others can still buzz
        return right

    def key(self, t, key, action, seat):
        if action != "down":
            return
        if self.phase in ("read", "open"):
            self.buzz(t, seat)
        elif self.phase == "answer" and seat == self.buzzer and key in ANSWER_KEYS:
            self.answer(t, seat, ANSWER_KEYS[key])

    def tick(self, now):
        if self.phase == "read" and now - self.phase_at >= READ_S:
            self.phase, self.phase_at = "open", now
            self.rev += 1
        if self.phase == "open":
            for s, at in list(self.cpu_buzz.items()):
                if now >= at and s not in self.locked:
                    del self.cpu_buzz[s]
                    if self.buzz(now, s):
                        break
            if self.phase == "open" and now - self.phase_at >= self.open_left:
                self.result = self.result or {"seat": None, "right": False}
                self.phase, self.phase_at = "reveal", now
                self.rev += 1
        if self.phase == "answer":
            if self.is_cpu(self.buzzer) and now - self.buzz_at >= 1.5:
                odds = {"easy": 0.8, "medium": 0.6, "hard": 0.45}.get(self.p["difficulty"], 0.6)
                c = self.q["c"]
                self.answer(now, self.buzzer, c if self.rnd.random() < odds else (c + self.rnd.randint(1, 3)) % 4)
            elif now - self.buzz_at >= ANSWER_S + SLAM_S:
                self.answer(now, self.buzzer, -1)  # out of time: counts as wrong
        if self.phase == "reveal" and now - self.phase_at >= REVEAL_S:
            if self.qi + 1 >= len(self.qs):
                self.finish(now)
            else:
                self.next_question(now)

    # ---- board --------------------------------------------------------------------------------

    def timer_bar(self, c, frac):
        fill_rect(c, 0, c.h - 1, int(c.w * max(0.0, min(1.0, frac))), 1)

    def draw(self, c, now):
        q = self.q
        if self.phase in ("read", "open"):
            lines = wrap_text(q["q"].upper(), SMALL, c.w)
            top = 0
            if len(lines) <= 5:
                c.text(0, 0, f"Q{self.qi + 1}/{len(self.qs)}", SMALL)
                hint = "BUZZ!" if self.phase == "open" else ""
                c.text(c.w - SMALL.width(hint), 0, hint, SMALL)
                top = 7
            for i, line in enumerate(lines[:6]):
                c.text_center(top + i * 6, line, SMALL)
            if self.phase == "open":
                self.timer_bar(c, (self.open_left - (now - self.phase_at)) / OPEN_S)
            for s in self.early:
                x, y, w, h = quadrant(c.w, c.h, s)
                box_text(c, x + 1, y + h - 7 if y else y + 1, f"{self.name(s)} EARLY")
            return
        if self.phase == "answer":
            age = now - self.buzz_at
            if age < SLAM_S:
                # the buzz: that player's quarter slams over and back a few times
                x, y, w, h = quadrant(c.w, c.h, self.buzzer)
                c.text_center(y + 4, self.name(self.buzzer), NORMAL, scale=2, x0=x, w=w)
                if int(age / 0.1) % 2 == 0:
                    invert_rect(c, x, y, w, h)
                return
            c.text(0, 0, self.name(self.buzzer), NORMAL)
            left = ANSWER_S - (age - SLAM_S)
            c.text(c.w - SMALL.width(f"{max(0, left):.0f}"), 1, f"{max(0, left):.0f}", SMALL)
            self.draw_options(c, 9)
            self.timer_bar(c, left / ANSWER_S)
            return
        # reveal
        r = self.result or {}
        if r.get("seat") is not None and r.get("right"):
            head = f"{self.name(r['seat'])} RIGHT +{r['points']}"
        elif r.get("seat") is not None:
            head = f"{self.name(r['seat'])} WRONG {r['points']}"
        else:
            head = "TIME UP"
        c.text_center(0, head, SMALL)
        right = f"{LETTERS[q['c']]} {q['a'][q['c']].upper()}"
        for i, line in enumerate(wrap_text(right, NORMAL, c.w)[:2]):
            c.text_center(10 + i * 9, line, NORMAL)
        self.draw_mini_scores(c, 36)

    def draw_options(self, c, y0):
        for i, a in enumerate(self.q["a"]):
            line = f"{LETTERS[i]} {a.upper()}"
            while SMALL.width(line) > c.w and len(line) > 3:
                line = line[:-1]
            c.text(0, y0 + i * 7, line, SMALL)

    def draw_mini_scores(self, c, y):
        parts = [f"{self.name(s)} {self.totals[s]}" for s in self.seats]
        lines = ["  ".join(parts)]
        if SMALL.width(lines[0]) > c.w:  # two to a line
            lines = ["  ".join(parts[i:i + 2]) for i in range(0, len(parts), 2)]
        for i, line in enumerate(lines):
            c.text_center(y - 6 * (len(lines) - 1) + i * 6, line, SMALL)

    def draw_lobby(self, c, now):
        super().draw_lobby(c, now)
        if self.ai_note and int(now) % 6 < 2:
            box_text(c, 1, 29, "BUILT-IN QUESTIONS")

    def status_text(self):
        return f"question {self.qi + 1}/{len(self.qs)}" if getattr(self, "qs", None) else "starting"

    def phone_state(self, seat):
        out = super().phone_state(seat)
        if self.state == "playing" and getattr(self, "qs", None) and self.qi >= 0 and not self.celebration:
            q = self.q
            left = {"open": self.open_left - (self.now - self.phase_at),
                    "answer": ANSWER_S + SLAM_S - (self.now - (self.buzz_at or self.now))}.get(self.phase)
            out.update(phase=self.phase, number=self.qi + 1, count=len(self.qs), question=q["q"], answers=q["a"],
                       buzzer=self.buzzer, locked=seat in self.locked, early=seat in self.early,
                       left=round(max(0.0, left), 1) if left is not None else None,
                       result=self.result,
                       correct=q["c"] if self.phase == "reveal" else None)
        if self.ai_note:
            out["note"] = "Using the built-in questions: " + self.ai_note
        return out

    def phone(self, action, data, seat):
        t = self.clock()
        if action == "buzz":
            if self.state == "playing" and getattr(self, "qs", None):
                self.buzz(t, seat)
        elif action == "answer":
            try:
                i = int((data or {}).get("i"))
            except (TypeError, ValueError):
                raise ValueError("answer needs i (0-3)") from None
            if not 0 <= i < 4:
                raise ValueError("answer needs i (0-3)")
            if self.state == "playing":
                self.answer(t, seat, i)
        else:
            return super().phone(action, data, seat)
