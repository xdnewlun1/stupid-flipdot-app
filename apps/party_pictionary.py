"""Flipdot Pictionary: one player draws on their phone (/play shows a drawing canvas while this runs), the board
mirrors the drawing stroke by stroke, and the others type guesses on their phones. The first right guess scores
for the guesser (more the quicker) and the drawer.

With an Anthropic API key (Settings → AI drawing) Claude can play too: as a guesser it is sent the drawing as an
image every so often; as a drawer it draws its word with the AI drawing feature and the board reveals it a few
dots at a time. Words are bundled (party_data/pictionary.json) in three difficulties.
"""

import base64
import io
import json
import logging
import re
import threading
from pathlib import Path

from fonts import NORMAL, SMALL

from .base import P, register
from .pack_party import PartyGame, banner, box_text, fill_rect

log = logging.getLogger("pictionary")

WORDS = Path(__file__).with_name("party_data") / "pictionary.json"
LEVELS = [["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard (ideas, not things)"]]
READY_S = 4.0
REVEAL_S = 5.0
AI = 0                    # Claude's seat number
AI_GUESS_EVERY = 14.0     # seconds between Claude's guesses...
AI_GUESSES = 4            # ...at most this many per turn
AI_MIN_DOTS = 40          # and only once the drawing has this many dots
FEED = 8                  # guesses kept for the phones
BITS = bytes.maketrans(b"\x00\x01", b"01")
_words = None


def words():
    global _words
    if _words is None:
        try:
            _words = json.loads(WORDS.read_text())
        except (OSError, ValueError) as e:
            log.warning("can't read the Pictionary words: %s", e)
            _words = {"easy": ["cat", "house", "sun", "tree", "boat"]}
    return _words


def norm(s):
    s = re.sub(r"[^a-z ]", "", str(s or "").lower())
    return " ".join(s.split())


def matches(guess, word):
    """A guess counts when it's the word, ignoring case, spaces, punctuation, a plural s and an article."""
    g, w = norm(guess), norm(word)
    if not g:
        return False
    for art in ("a ", "an ", "the "):
        if g.startswith(art):
            g = g[len(art):]
    squash = lambda x: x.replace(" ", "")
    g, w = squash(g), squash(w)
    return g == w or g.rstrip("s") == w.rstrip("s") or g == w + "es"


def mask(word):
    return " ".join("/" if ch == " " else "_" for ch in word)


def png(pic, w, h, scale=6):
    """The drawing as a black-on-white PNG (what a person would see on paper), for Claude."""
    from PIL import Image
    img = Image.new("L", (w, h), 255)
    img.putdata([0 if v else 255 for v in pic])
    img = img.resize((w * scale, h * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def ai_guess(settings, image, tried, timeout=60):
    """Claude's guess for a drawing (a word or two), or None. Raises aidraw.AiError."""
    import aidraw
    ai = settings.get("ai") or {}
    key = (ai.get("api_key") or "").strip()
    if not key:
        raise aidraw.AiError("No Anthropic API key.", 400)
    model = ai.get("model") or aidraw.DEFAULT_MODEL
    text = ("We're playing Pictionary. A player drew this on an 84x42 one-bit flip-dot display, so it's rough. "
            "What is it? Answer with only your guess, one or two words, no punctuation.")
    if tried:
        text += " You already guessed (wrong): " + ", ".join(tried) + "."
    body = {"model": model, "max_tokens": 400, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(image).decode()}},
                {"type": "text", "text": text}]}]}
    if model in aidraw.FALLBACK_MODELS:
        body["fallbacks"] = "default"
    resp = aidraw.call_api(key, body, timeout=timeout)
    if resp.get("stop_reason") == "refusal":
        return None
    out = "".join(b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
    out = norm(out.strip().splitlines()[0] if out.strip() else "")
    return " ".join(out.split()[:3]) or None


@register
class Pictionary(PartyGame):
    id = "pictionary"
    title = "Flipdot Pictionary"
    description = ("One player draws a secret word on their phone and the board shows it as it's drawn; the others "
                   "guess on their phones. Claude can guess or draw too (needs an API key).")
    phone_mode = "draw"
    cpu_ok = False
    min_players = 2
    join_hint = "JOIN ON YOUR PHONE: A"
    params = [
        P("difficulty", "select", "easy", "Words", options=LEVELS),
        P("time", "number", 75, "Seconds to draw", min=20, max=240, step=5),
        P("rounds", "number", 2, "Turns each", min=1, max=5),
        P("ai_guess", "checkbox", False, "Claude guesses too (needs an API key)"),
        P("ai_draw", "checkbox", False, "Claude takes a drawing turn each round (needs an API key)"),
    ]

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.ph = self.h - 1  # the bottom row is the timer
        self.pic = bytearray(self.w * self.ph)
        self.ai_pending = None  # a result from Claude's thread: ("guess", turn, text) / ("drawing", turn, rows)
        self.ai_busy = False

    def ai_ready(self):
        return bool(((self.ctx.store.settings.get("ai") or {}).get("api_key") or "").strip()) if hasattr(
            self.ctx.store, "settings") else False

    def can_start(self):
        ai = self.ai_ready() and (self.p["ai_guess"] or self.p["ai_draw"])
        return len(self.joined) >= (1 if ai else 2)

    def name(self, seat):
        return "AI" if seat == AI else super().name(seat)

    # ---- turns ----------------------------------------------------------------------------------

    def start_game(self, now):
        pool = list(words().get(self.p["difficulty"]) or words()["easy"])
        self.rnd.shuffle(pool)
        self.pool = pool
        self.order = []
        ai_draw = self.p["ai_draw"] and self.ai_ready()
        for _ in range(max(1, int(self.p["rounds"] or 2))):
            self.order += list(self.seats) + ([AI] if ai_draw else [])
        if self.p["ai_guess"] and self.ai_ready() or ai_draw:
            self.totals.setdefault(AI, 0)
        self.turn = -1
        self.next_turn(now)

    def guessers(self):
        out = [s for s in self.seats if s != self.drawer]
        if self.drawer != AI and self.p["ai_guess"] and self.ai_ready():
            out.append(AI)
        return out

    def next_turn(self, now):
        self.turn += 1
        if self.turn >= len(self.order):
            return self.finish(now)
        self.drawer = self.order[self.turn]
        self.word = self.pool[self.turn % len(self.pool)]
        self.pic[:] = bytes(len(self.pic))
        self.feed = []
        self.solved = None  # (seat, points)
        self.phase, self.phase_at = "ready", now
        self.ai_tried, self.ai_next, self.ai_changed = [], now + READY_S + AI_GUESS_EVERY, False
        self.reveal = None  # Claude's drawing: [dots in reveal order, shown so far]
        self.ai_failed = None
        self.rev += 1
        if self.drawer == AI:
            self.ask_ai("draw")

    def end_turn(self, now):
        self.phase, self.phase_at = "reveal", now
        self.rev += 1

    def guess(self, t, seat, text):
        if self.phase != "draw" or seat == self.drawer or seat not in self.guessers() or self.solved:
            return False
        text = " ".join(str(text or "").split())[:40]
        if not text:
            return False
        right = matches(text, self.word)
        self.feed.append({"seat": seat, "name": self.name(seat), "text": "(got it)" if right else text, "right": right})
        del self.feed[:-FEED]
        self.rev += 1
        if right:
            frac = max(0.0, 1 - (t - self.phase_at) / self.length())
            pts = 50 + int(frac * 5 + 0.5) * 10  # 50 for the guess, up to 50 more for speed
            self.totals[seat] = self.totals.get(seat, 0) + pts
            self.totals[self.drawer] = self.totals.get(self.drawer, 0) + 50
            self.solved = (seat, pts)
            self.end_turn(t)
        return right

    def length(self):
        return float(self.p["time"] or 75)

    # ---- drawing ----------------------------------------------------------------------------------

    def plot(self, x, y, v, size=1):
        for dy in range(size):
            for dx in range(size):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.w and 0 <= yy < self.ph:
                    self.pic[yy * self.w + xx] = v

    def stroke(self, seat, segs, v=1, size=1):
        """Line segments [[x0, y0, x1, y1], ...] from the drawer's phone."""
        if self.phase not in ("ready", "draw") or seat != self.drawer:
            return False
        size = 2 if size == 2 else 1
        for seg in list(segs or [])[:200]:
            try:
                x0, y0, x1, y1 = (int(round(float(n))) for n in seg[:4])
            except (TypeError, ValueError):
                continue
            n = max(abs(x1 - x0), abs(y1 - y0), 1)
            for i in range(n + 1):
                self.plot(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n, 1 if v else 0, size)
        self.ai_changed = True
        return True

    def clear_drawing(self, seat):
        if seat == self.drawer and self.phase in ("ready", "draw"):
            self.pic[:] = bytes(len(self.pic))
            return True
        return False

    # ---- Claude -------------------------------------------------------------------------------------

    def ask_ai(self, what):
        if self.ai_busy:
            return
        self.ai_busy = True
        turn, store, word = self.turn, self.ctx.store, self.word
        if what == "guess":
            image, tried = png(self.pic, self.w, self.ph), list(self.ai_tried)
            self.ai_changed = False

        def run():
            try:
                if what == "guess":
                    self.ai_pending = ("guess", turn, ai_guess(store.settings, image, tried))
                else:
                    import aidraw
                    got = aidraw.draw(store.settings, f"{word} (a game of Pictionary: no letters or words)", 1,
                                      "simple bold outline drawing, easy to recognise", self.w, self.ph, timeout=180)
                    self.ai_pending = ("drawing", turn, got["frames"][0]["rows"])
            except Exception as e:
                log.info("Pictionary: Claude couldn't %s: %s", what, e)
                self.ai_pending = ("failed", turn, str(e))
            finally:
                self.ai_busy = False
        threading.Thread(target=run, daemon=True, name=f"pictionary-{what}").start()

    def take_ai(self, now):
        got, self.ai_pending = self.ai_pending, None
        if not got or got[1] != self.turn:
            return
        kind, _, value = got
        if kind == "guess" and value:
            self.ai_tried.append(value)
            self.guess(now, AI, value)
        elif kind == "drawing" and self.drawer == AI:
            dots = [(x, y) for y, row in enumerate(value[:self.ph]) for x, ch in enumerate(row[:self.w]) if ch == "1"]
            # revealed roughly left to right with some scatter, so it builds up like a drawing
            dots.sort(key=lambda d: d[0] + self.rnd.uniform(-10, 10))
            self.reveal = [dots, 0]
        elif kind == "failed" and self.drawer == AI:
            self.ai_failed = value

    # ---- frames -------------------------------------------------------------------------------------

    def key(self, t, key, action, seat):
        if action == "down" and key in ("a", "start") and self.phase == "reveal" and t - self.phase_at > 1.5:
            self.phase_at = -1e9  # skip to the next turn

    def tick(self, now):
        self.take_ai(now)
        if self.phase == "ready" and now - self.phase_at >= READY_S:
            if self.drawer == AI and self.ai_failed:
                self.end_turn(now)
            elif self.drawer != AI or self.reveal is not None or now - self.phase_at > READY_S + 60:
                self.phase, self.phase_at = "draw", now
                self.rev += 1
        elif self.phase == "draw":
            left = self.length() - (now - self.phase_at)
            if self.reveal is not None:
                dots, shown = self.reveal
                upto = min(len(dots), int(len(dots) * min(1.0, (now - self.phase_at) / (self.length() * 0.6))) + 1)
                for x, y in dots[shown:upto]:
                    self.pic[y * self.w + x] = 1
                self.reveal[1] = upto
            if (AI in self.guessers() and not self.ai_busy and self.ai_changed and now >= self.ai_next
                    and len(self.ai_tried) < AI_GUESSES and sum(self.pic) >= AI_MIN_DOTS):
                self.ai_next = now + AI_GUESS_EVERY
                self.ask_ai("guess")
            if left <= 0:
                self.end_turn(now)
        elif self.phase == "reveal" and now - self.phase_at >= REVEAL_S:
            self.next_turn(now)

    def draw(self, c, now):
        if self.phase == "ready":
            c.text_center(4, f"{self.name(self.drawer)} DRAWS", NORMAL)
            c.text_center(15, f"TURN {self.turn + 1}/{len(self.order)}", SMALL)
            msg = ("CLAUDE IS THINKING" if self.drawer == AI and not self.ai_failed else
                   "CLAUDE CAN'T DRAW NOW" if self.drawer == AI else "LOOK AT YOUR PHONE")
            c.text_center(24, msg, SMALL)
            c.text_center(33, "GUESS ON YOUR PHONES", SMALL)
            return
        c.px[:len(self.pic)] = self.pic
        if self.phase == "draw":
            left = max(0.0, self.length() - (now - self.phase_at))
            fill_rect(c, 0, self.h - 1, int(self.w * left / self.length()), 1)
            if left < 10:
                box_text(c, self.w - 8, 1, f"{int(left) + 1}")
        else:
            word = self.word.upper()
            if self.solved:
                banner(c, f"{self.name(self.solved[0])} +{self.solved[1]}", 2)
            box_text(c, max(1, (self.w - SMALL.width(word)) // 2), self.h - 7, word)

    def status_text(self):
        if getattr(self, "turn", -1) < 0 or self.turn >= len(self.order):
            return "starting"
        return f"turn {self.turn + 1}/{len(self.order)}, {self.name(self.drawer)} draws"

    def phone_state(self, seat):
        out = super().phone_state(seat)
        out["w"], out["h"] = self.w, self.ph
        out["ai"] = self.ai_ready()
        if self.state == "playing" and getattr(self, "drawer", None) is not None and not self.celebration \
                and self.standings_at is None:
            role = "drawer" if seat == self.drawer else "guesser" if seat in self.seats else "watcher"
            left = self.length() - (self.now - self.phase_at) if self.phase == "draw" else None
            out.update(phase=self.phase, role=role, drawer=self.drawer, drawer_name=self.name(self.drawer),
                       turn=self.turn + 1, turns=len(self.order), feed=self.feed,
                       left=round(max(0.0, left), 1) if left is not None else None,
                       hint=mask(self.word),
                       word=self.word if role == "drawer" or self.phase == "reveal" else None,
                       solved=self.name(self.solved[0]) if self.solved else None,
                       guessed=any(f["seat"] == seat and f["right"] for f in self.feed),
                       pic=bytes(self.pic).translate(BITS).decode())  # "0"/"1" per dot, row by row
        return out

    def phone(self, action, data, seat):
        data = data or {}
        if action == "draw":
            if self.state != "playing" or not self.stroke(seat, data.get("segs"), data.get("v", 1), data.get("size", 1)):
                return {"ok": False}
            return {"ok": True}
        if action == "clear":
            return {"ok": self.state == "playing" and self.clear_drawing(seat)}
        if action == "guess":
            if self.state != "playing":
                return {"ok": False}
            return {"ok": True, "right": self.guess(self.clock(), seat, data.get("text"))}
        if action == "skip" and self.state == "playing" and seat == getattr(self, "drawer", None) \
                and self.phase in ("ready", "draw"):
            self.end_turn(self.clock())
            return {"ok": True}
        return super().phone(action, data, seat)
