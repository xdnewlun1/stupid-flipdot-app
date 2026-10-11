"""Blackjack, dealt from a shoe shuffled by the Deck of Cards API (https://deckofcardsapi.com).

Controls: up/down = change bet, A = deal / hit, B = stand, right = double down.
Dealer stands on all 17s, blackjack pays 3:2. A game lasts until the chips run out
(or nobody has pressed a button for IDLE seconds); its score is the biggest stack
reached. The idle title shows a demo playing basic strategy from a local shoe.
"""

import collections
import json
import logging
import random
import threading
import time
import urllib.request

from fonts import NORMAL, SMALL

from .base import UA, P, register
from .games import DEMO, Attract, Game

log = logging.getLogger("blackjack")
API = "https://deckofcardsapi.com/api/deck"

RANKS = {"ACE": "A", "KING": "K", "QUEEN": "Q", "JACK": "J"}
SUIT_GLYPHS = {
    "HEARTS": ["01010", "11111", "11111", "01110", "00100"],
    "DIAMONDS": ["00100", "01110", "11111", "01110", "00100"],
    "CLUBS": ["01110", "01110", "11111", "11111", "00100"],
    "SPADES": ["00100", "01110", "11111", "00100", "01110"],
}
CARD_W, CARD_H = 12, 16
IDLE = 300  # seconds without a button before a game cashes out


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


class Shoe:
    """Cards come from the Deck of Cards API in small batches, fetched in the background.
    Falls back to a locally shuffled shoe if the API can't be reached."""

    BATCH = 12

    def __init__(self, decks):
        self.decks = decks
        self.deck_id = None
        self.remaining = None
        self.buffer = collections.deque()
        self.lock = threading.Lock()
        self.fetching = False
        self.offline = False
        self.shuffled_at = 0

    @property
    def size(self):
        return 52 * self.decks

    def needs_shuffle(self):
        # reshuffle when three quarters of the shoe has been dealt
        left = self.remaining if not self.offline else len(self.buffer)
        return left is not None and left + (len(self.buffer) if not self.offline else 0) < self.size // 4

    def shuffle(self):
        with self.lock:
            self.buffer.clear()
            self.remaining = None
            self.shuffled_at = time.time()
        self._start(reshuffle=True)

    def take(self):
        with self.lock:
            card = self.buffer.popleft() if self.buffer else None
            low = len(self.buffer) < self.BATCH // 2
        if low:
            self._start()
        return card

    def _start(self, reshuffle=False):
        with self.lock:
            if self.fetching:
                return
            self.fetching = True
        threading.Thread(target=self._fetch, args=(reshuffle,), daemon=True).start()

    def _fetch(self, reshuffle):
        try:
            if self.offline:
                self._local()
                return
            if self.deck_id is None:
                d = _get(f"{API}/new/shuffle/?deck_count={self.decks}")
                self.deck_id = d["deck_id"]
            elif reshuffle or (self.remaining is not None and self.remaining < self.BATCH):
                _get(f"{API}/{self.deck_id}/shuffle/")
            d = _get(f"{API}/{self.deck_id}/draw/?count={self.BATCH}")
            if not d.get("success"):
                raise ValueError(d.get("error", "draw failed"))
            cards = [(RANKS.get(c["value"], c["value"]), c["suit"], c["value"]) for c in d["cards"]]
            with self.lock:
                self.buffer.extend(cards)
                self.remaining = d["remaining"]
        except Exception as e:
            log.warning("Deck of Cards API unavailable (%s); using a local deck", e)
            self.offline = True
            self._local()
        finally:
            with self.lock:
                self.fetching = False

    def _local(self):
        values = ["ACE", "2", "3", "4", "5", "6", "7", "8", "9", "10", "JACK", "QUEEN", "KING"]
        cards = [(RANKS.get(v, v), s, v) for _ in range(self.decks) for s in SUIT_GLYPHS for v in values]
        random.shuffle(cards)
        with self.lock:
            self.buffer = collections.deque(cards)


def hand_value(cards):
    total, aces = 0, 0
    for _, _, v in cards:
        if v == "ACE":
            total, aces = total + 11, aces + 1
        elif v in ("KING", "QUEEN", "JACK"):
            total += 10
        else:
            total += int(v)
    while total > 21 and aces:
        total, aces = total - 10, aces - 1
    return total, aces > 0  # (total, soft)


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def basic_strategy(player, dealer_up, can_double):
    """'hit', 'stand' or 'double' for a hand against the dealer's up card (simplified basic strategy)."""
    total, soft = hand_value(player)
    up = 11 if dealer_up == "ACE" else 10 if dealer_up in ("KING", "QUEEN", "JACK") else int(dealer_up)
    if can_double and not soft and (total == 11 or (total == 10 and up < 10) or (total == 9 and 3 <= up <= 6)):
        return "double"
    if soft:
        return "hit" if total <= 17 or (total == 18 and up >= 9) else "stand"
    if total <= 11:
        return "hit"
    if total == 12:
        return "stand" if 4 <= up <= 6 else "hit"
    if total <= 16:
        return "stand" if up <= 6 else "hit"
    return "stand"


@register
class Blackjack(Attract, Game):
    id = "blackjack"
    title = "Blackjack"
    description = "Dealt from the Deck of Cards API. Up/down = bet, A = deal/hit, B = stand, right = double down."
    params = [
        P("decks", "number", 6, "Decks in the shoe", min=1, max=8),
        P("bankroll", "number", 100, "Starting chips", min=10, max=10000),
        P("deal_speed", "number", 0.45, "Seconds between dealt cards", min=0.1, max=2, step=0.05),
        DEMO,
    ]
    DEMO_POS = (-1, 1)

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.table_shoe = Shoe(int(self.p["decks"]))
        self.table_shoe._start()  # get the first cards while the title shows
        self.demo_shoe = Shoe(int(self.p["decks"]))
        self.demo_shoe.offline = True  # the demo doesn't spend API calls
        self.chips = int(self.p["bankroll"])
        self.bet = 10
        self.ai_at = 0
        self.reset_hand()

    @property
    def shoe(self):
        return self.demo_shoe if self.demo else self.table_shoe

    # ---- game state -------------------------------------------------------------

    def reset(self):
        """A new game (a button on the title screen, or the demo): a fresh bankroll."""
        self.chips = self.score = int(self.p["bankroll"])
        self.bet = 10
        self.touched = time.time()
        self.reset_hand()

    def reset_hand(self):
        self.player, self.dealer = [], []
        self.hole_hidden = True
        self.phase = "bet"  # bet -> dealing -> player -> dealer -> result
        self.queue = []
        self.next_at = 0
        self.msg = ""
        self.doubled = False

    def start_hand(self):
        if self.shoe.needs_shuffle():
            self.shoe.shuffle()
        self.bet = min(self.bet, self.chips)
        self.chips -= self.bet
        self.player, self.dealer = [], []
        self.hole_hidden = True
        self.doubled = False
        self.phase = "dealing"
        self.queue = ["player", "dealer", "player", "dealer", "check"]
        self.next_at = 0

    def input(self, key, action="down", player=1):
        self.touched = time.time()
        super().input(key, action, player)

    def autoplay(self, now):
        if now < self.ai_at:
            return
        self.ai_at = now + 0.9
        if self.phase == "bet":
            if random.random() < 0.3:
                self.press(random.choice(("up", "down")), 1)
            else:
                self.press("a", 1)
        elif self.phase == "player":
            move = basic_strategy(self.player, self.dealer[0][2], len(self.player) == 2 and self.chips >= self.bet)
            self.press({"hit": "a", "stand": "b", "double": "right"}[move], 1)
        elif self.phase == "result":
            self.ai_at = now + 2.5
            self.press("a", 1)

    def press(self, key, player):
        if self.phase == "bet":
            if key == "up":
                self.bet = min(self.chips, self.bet + 5)
            elif key == "down":
                self.bet = max(5, self.bet - 5)
            elif key in ("a", "b", "right"):
                self.start_hand()
        elif self.phase == "player":
            if key == "a":
                self.queue = ["player", "after_hit"]
            elif key == "b":
                self.dealer_turn()
            elif key == "right" and len(self.player) == 2 and self.chips >= self.bet:
                self.chips -= self.bet
                self.bet *= 2
                self.doubled = True
                self.queue = ["player", "after_double"]
            self.next_at = 0
        elif self.phase == "result":
            if self.chips < 5:
                return self.game_over()
            self.bet = min(self.bet, self.chips)
            self.reset_hand()

    def dealer_turn(self):
        self.phase = "dealer"
        self.queue = ["reveal", "dealer_step"]
        self.next_at = 0

    def settle(self, outcome=None):
        p, _ = hand_value(self.player)
        d, _ = hand_value(self.dealer)
        self.hole_hidden = False
        bet = self.bet
        if outcome is None:
            if p > 21:
                outcome = "bust"
            elif is_blackjack(self.player) and not is_blackjack(self.dealer):
                outcome = "blackjack"
            elif is_blackjack(self.dealer) and not is_blackjack(self.player):
                outcome = "dealer_bj"
            elif d > 21:
                outcome = "dealer_bust"
            elif p > d:
                outcome = "win"
            elif p < d:
                outcome = "lose"
            else:
                outcome = "push"
        payout, self.msg = {
            "blackjack": (bet + bet * 3 // 2, f"BLACKJACK! +{bet * 3 // 2}"),
            "dealer_bust": (bet * 2, f"DEALER BUSTS +{bet}"),
            "win": (bet * 2, f"YOU WIN +{bet}"),
            "push": (bet, "PUSH"),
            "bust": (0, f"BUST -{bet}"),
            "dealer_bj": (0, f"DEALER BLACKJACK -{bet}"),
            "lose": (0, f"DEALER WINS -{bet}"),
        }[outcome]
        self.chips += payout
        self.score = max(self.score, self.chips)
        if self.doubled:
            self.bet //= 2
        self.phase = "result"
        self.queue = []

    def tick(self, now):
        if not self.demo and time.time() - self.touched > IDLE:
            return self.game_over()  # cashed out: the biggest stack counts
        if not self.queue or now < self.next_at:
            return
        step = self.queue[0]
        if step in ("player", "dealer"):
            card = self.shoe.take()
            if card is None:
                self.msg = "SHUFFLING..."
                return  # waiting for the API; try again next tick
            self.msg = ""
            (self.player if step == "player" else self.dealer).append(card)
        elif step == "check":
            up = self.dealer[0][2]
            if is_blackjack(self.player) or (up in ("ACE", "10", "KING", "QUEEN", "JACK") and is_blackjack(self.dealer)):
                self.queue = []
                return self.settle()
            self.phase = "player"
        elif step == "after_hit":
            total = hand_value(self.player)[0]
            if total > 21:
                self.queue = []
                return self.settle("bust")
            if total == 21:
                self.queue = []
                return self.dealer_turn()
        elif step == "after_double":
            self.queue = []
            if hand_value(self.player)[0] > 21:
                return self.settle("bust")
            return self.dealer_turn()
        elif step == "reveal":
            self.hole_hidden = False
        elif step == "dealer_step":
            if hand_value(self.dealer)[0] < 17:
                self.queue = ["dealer", "dealer_step"]
                self.next_at = now + float(self.p["deal_speed"])
                return
            self.queue = []
            return self.settle()
        self.queue.pop(0)
        self.next_at = now + float(self.p["deal_speed"])

    # ---- drawing ------------------------------------------------------------------

    def draw_card(self, c, x, y, card, hidden=False):
        c.rect(x - 1, y - 1, CARD_W + 1, CARD_H + 2, v=0, fill=True)  # gap to the card underneath
        c.rect(x, y, CARD_W, CARD_H, fill=True)
        if hidden:
            for yy in range(2, CARD_H - 2):
                for xx in range(2, CARD_W - 2):
                    if (xx + yy) % 2:
                        c.set(x + xx, y + yy, 0)
            return
        rank, suit, _ = card
        c.text(x + 2, y + 2, rank, SMALL, v=0)
        for gy, row in enumerate(SUIT_GLYPHS[suit]):
            for gx, bit in enumerate(row):
                if bit == "1":
                    c.set(x + 2 + gx, y + 9 + gy, 0)

    def draw_hand(self, c, cards, y, hide_second=False):
        x0, avail = 22, 84 - 22
        n = len(cards)
        stride = 13 if n <= 1 else min(13, (avail - CARD_W) // (n - 1))
        for i, card in enumerate(cards):
            self.draw_card(c, x0 + i * stride, y, card, hidden=hide_second and i == 1)

    def draw(self, c, now):
        hide = self.hole_hidden and len(self.dealer) >= 2
        self.draw_hand(c, self.dealer, 1, hide_second=hide)
        self.draw_hand(c, self.player, 25)
        # totals
        c.text(0, 1, "DEALER", SMALL)
        if self.dealer:
            shown = self.dealer[:1] if hide else self.dealer
            c.text(0, 8, str(hand_value(shown)[0]), NORMAL, scale=1)
        c.text(0, 25, "YOU", SMALL)
        if self.player:
            total, soft = hand_value(self.player)
            c.text(0, 32, ("S" if soft and total < 21 else "") + str(total), NORMAL)
        # message line
        if self.phase == "bet":
            msg = self.msg or (f"${self.chips}  BET {self.bet}  A=DEAL" + (" (LOCAL DECK)" if self.shoe.offline and not self.demo else ""))
        elif self.phase == "player":
            msg = "A HIT  B STAND" + ("  > DBL" if len(self.player) == 2 and self.chips >= self.bet else "")
        elif self.phase == "result":
            msg = self.msg if int(now * 2) % 4 else f"${self.chips}  A=NEXT HAND"
        else:
            msg = self.msg or f"BET {self.bet}"
        c.text_center(19, msg, SMALL)

    def draw_title_screen(self, c, now):
        self.draw_card(c, 6, 4, ("A", "SPADES", "ACE"))
        self.draw_card(c, 15, 8, ("K", "HEARTS", "KING"))
        c.text(32, 8, "BLACK", NORMAL)
        c.text(32, 17, "JACK", NORMAL)
        self.title_footer(c, now, 32)

    def describe(self):
        if self.state != "playing" or self.scoreboard is not None:
            return super().describe()
        return f"Blackjack ({'demo, ' if self.demo else ''}${self.chips}, {self.phase})"
