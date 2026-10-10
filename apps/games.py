"""Games, controlled from the /play page (or the keyboard on the main page).

Input keys: up, down, left, right, a (rotate/action), b (drop). Each press
arrives as action "down" and its release as "up".
"""

import random
import time

from fonts import NORMAL, SMALL

from .base import App, P, register


class Game(App):
    category = "Games"
    interactive = True
    interval = 0.05

    def __init__(self, ctx, **params):
        super().__init__(ctx, **params)
        self.held = set()
        self.state = "title"  # title -> playing -> over
        self.over_at = 0
        self.score = 0

    def input(self, key, action="down", player=1):
        if action == "down":
            self.held.add((player, key))
            if self.state == "title":
                self.state = "playing"
                self.reset()
            elif self.state == "playing":
                self.press(key, player)
        else:
            self.held.discard((player, key))

    def game_over(self):
        self.state = "over"
        self.over_at = time.time()

    def render(self, c, now):
        if self.state == "title":
            c.text_center(8, self.title.upper(), NORMAL, scale=2)
            if int(now * 2) % 2:
                c.text_center(30, "PRESS ANY BUTTON", SMALL)
            return
        if self.state == "over":
            c.text_center(6, "GAME OVER", NORMAL)
            c.text_center(18, str(self.score), NORMAL, scale=2)
            if now - self.over_at > 4:
                self.state = "title"
            return
        self.tick(now)
        self.draw(c, now)

    def describe(self):
        return f"{self.title} ({self.state}, score {self.score})"

    def reset(self): ...
    def press(self, key, player): ...
    def tick(self, now): ...
    def draw(self, c, now): ...


@register
class Snake(Game):
    id = "snake"
    title = "Snake"
    description = "Eat the dots, don't hit yourself."
    params = [P("walls", "checkbox", False, "Walls kill (otherwise wrap around)")]
    CELL = 2

    def reset(self):
        # with walls, the playing area sits inside a 1-dot border
        self.margin = 1 if self.p["walls"] else 0
        self.gw = (self.w - 2 * self.margin) // self.CELL
        self.gh = (self.h - 2 * self.margin) // self.CELL
        self.body = [(self.gw // 2 - i, self.gh // 2) for i in range(4)]
        self.dir = self.next_dir = (1, 0)
        self.score = 0
        self.last_step = 0
        self.place_food()

    def place_food(self):
        free = [(x, y) for x in range(self.gw) for y in range(self.gh) if (x, y) not in self.body]
        self.food = random.choice(free)

    def press(self, key, player):
        d = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.get(key)
        if d and (d[0] != -self.dir[0] or d[1] != -self.dir[1]):
            self.next_dir = d

    def tick(self, now):
        if now - self.last_step < max(0.06, 0.16 - self.score * 0.004):
            return
        self.last_step = now
        self.dir = self.next_dir
        hx, hy = self.body[0][0] + self.dir[0], self.body[0][1] + self.dir[1]
        if self.p["walls"] and not (0 <= hx < self.gw and 0 <= hy < self.gh):
            return self.game_over()
        head = (hx % self.gw, hy % self.gh)
        if head in self.body[:-1]:
            return self.game_over()
        self.body.insert(0, head)
        if head == self.food:
            self.score += 1
            self.place_food()
        else:
            self.body.pop()

    def draw(self, c, now):
        s, m = self.CELL, self.margin
        for x, y in self.body:
            c.rect(m + x * s, m + y * s, s, s, fill=True)
        if int(now * 4) % 2:
            c.rect(m + self.food[0] * s, m + self.food[1] * s, s, s, fill=True)
        if m:
            c.rect(0, 0, self.gw * s + 2, self.gh * s + 2)


PIECES = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)], "O": [(1, 0), (2, 0), (1, 1), (2, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)], "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)], "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}


@register
class Tetris(Game):
    id = "tetris"
    title = "Tetris"
    description = "Left/right to move, up or A to rotate, down to drop faster, B to hard-drop."
    params = []
    COLS, ROWS, CELL = 10, 20, 2

    def reset(self):
        self.grid = [[0] * self.COLS for _ in range(self.ROWS)]
        self.score = self.lines = 0
        self.next = random.choice(list(PIECES))
        self.spawn()
        self.last_fall = time.time()

    def spawn(self):
        self.kind, self.next = self.next, random.choice(list(PIECES))
        self.cells = list(PIECES[self.kind])
        self.px, self.py = 3, 0
        if self.collides(self.cells, self.px, self.py):
            self.game_over()

    def collides(self, cells, px, py):
        for x, y in cells:
            gx, gy = px + x, py + y
            if gx < 0 or gx >= self.COLS or gy >= self.ROWS or (gy >= 0 and self.grid[gy][gx]):
                return True
        return False

    def rotate(self):
        if self.kind == "O":
            return
        size = 4 if self.kind == "I" else 3
        rotated = [(size - 1 - y, x) for x, y in self.cells]
        for kick in (0, -1, 1, -2, 2):
            if not self.collides(rotated, self.px + kick, self.py):
                self.cells, self.px = rotated, self.px + kick
                return

    def move(self, dx, dy):
        if not self.collides(self.cells, self.px + dx, self.py + dy):
            self.px += dx
            self.py += dy
            return True
        return False

    def lock(self):
        for x, y in self.cells:
            if self.py + y >= 0:
                self.grid[self.py + y][self.px + x] = 1
        full = [r for r in range(self.ROWS) if all(self.grid[r])]
        for r in full:
            del self.grid[r]
            self.grid.insert(0, [0] * self.COLS)
        self.lines += len(full)
        self.score += [0, 100, 300, 500, 800][len(full)]
        self.spawn()

    def press(self, key, player):
        if key == "left":
            self.move(-1, 0)
        elif key == "right":
            self.move(1, 0)
        elif key in ("up", "a"):
            self.rotate()
        elif key == "down":
            if not self.move(0, 1):
                self.lock()
            self.last_fall = time.time()
        elif key == "b":
            while self.move(0, 1):
                self.score += 2
            self.lock()

    def tick(self, now):
        speed = max(0.1, 0.8 - (self.lines // 10) * 0.07)
        if any(k == "down" for _, k in self.held):
            speed = 0.05
        if now - self.last_fall >= speed:
            self.last_fall = now
            if not self.move(0, 1):
                self.lock()

    def draw(self, c, now):
        s = self.CELL
        ox, oy = (c.w - self.COLS * s) // 2, c.h - self.ROWS * s - 1
        c.line(ox - 1, 0, ox - 1, c.h - 1)
        c.line(ox + self.COLS * s, 0, ox + self.COLS * s, c.h - 1)
        c.line(ox - 1, c.h - 1, ox + self.COLS * s, c.h - 1)
        for y, row in enumerate(self.grid):
            for x, v in enumerate(row):
                if v:
                    c.rect(ox + x * s, oy + y * s, s, s, fill=True)
        for x, y in self.cells:
            if self.py + y >= 0:
                c.rect(ox + (self.px + x) * s, oy + (self.py + y) * s, s, s, fill=True)
        c.text(1, 1, "SCORE", SMALL)
        c.text(1, 8, str(self.score), SMALL)
        c.text(1, 18, "LINES", SMALL)
        c.text(1, 25, str(self.lines), SMALL)
        nx = ox + self.COLS * s + 4
        c.text(nx, 1, "NEXT", SMALL)
        for x, y in PIECES[self.next]:
            c.rect(nx + x * s, 9 + y * s, s, s, fill=True)


@register
class Pong(Game):
    id = "pong"
    title = "Pong"
    description = "Player 1 = left paddle. Right paddle is the computer unless a player 2 joins."
    params = [P("to", "number", 7, "Points to win", min=1, max=21)]
    PADDLE = 9

    def reset(self):
        self.p1 = self.p2 = (self.h - self.PADDLE) / 2
        self.s1 = self.s2 = 0
        self.score = 0
        self.p2_human_at = 0
        self.serve(1)

    def input(self, key, action="down", player=1):
        if player == 2:
            self.p2_human_at = time.time()
        super().input(key, action, player)

    def serve(self, direction):
        self.bx, self.by = self.w / 2, self.h / 2
        self.vx = 0.9 * direction
        self.vy = random.uniform(-0.6, 0.6)
        self.pause_until = time.time() + 1

    def paddle_move(self, pos, player):
        if (player, "up") in self.held:
            pos -= 1.2
        if (player, "down") in self.held:
            pos += 1.2
        return min(max(pos, 0), self.h - self.PADDLE)

    def tick(self, now):
        self.p1 = self.paddle_move(self.p1, 1)
        if now - self.p2_human_at < 15:
            self.p2 = self.paddle_move(self.p2, 2)
        else:  # computer: follows the ball, a bit slower than it
            target = self.by - self.PADDLE / 2
            self.p2 += max(-0.75, min(0.75, target - self.p2))
            self.p2 = min(max(self.p2, 0), self.h - self.PADDLE)
        if now < self.pause_until:
            return
        self.bx += self.vx
        self.by += self.vy
        if self.by < 0 or self.by > self.h - 2:
            self.vy = -self.vy
            self.by = min(max(self.by, 0), self.h - 2)
        for px, pos, sign in ((2, self.p1, 1), (self.w - 4, self.p2, -1)):
            if (sign > 0 and self.vx < 0 and px <= self.bx <= px + 1.5) or (sign < 0 and self.vx > 0 and px - 1.5 <= self.bx <= px):
                if pos - 1.5 <= self.by <= pos + self.PADDLE:
                    hit = (self.by + 1 - (pos + self.PADDLE / 2)) / (self.PADDLE / 2)
                    self.vx = sign * min(abs(self.vx) * 1.06, 2.2)
                    self.vy = hit * 1.1
        if self.bx < -2:
            self.s2 += 1
            self.serve(1)
        elif self.bx > self.w:
            self.s1 += 1
            self.score = self.s1
            self.serve(-1)
        if max(self.s1, self.s2) >= int(self.p["to"]):
            self.game_over()

    def draw(self, c, now):
        for y in range(0, c.h, 4):
            c.line(c.w // 2, y, c.w // 2, y + 1)
        c.rect(2, int(self.p1), 2, self.PADDLE, fill=True)
        c.rect(c.w - 4, int(self.p2), 2, self.PADDLE, fill=True)
        c.rect(int(self.bx), int(self.by), 2, 2, fill=True)
        c.text(c.w // 2 - 3 - SMALL.width(str(self.s1)), 1, str(self.s1), SMALL)
        c.text(c.w // 2 + 3, 1, str(self.s2), SMALL)

    def render(self, c, now):
        if self.state == "over":
            c.text_center(6, "P1 WINS" if self.s1 > self.s2 else ("CPU WINS" if now - self.p2_human_at > 15 else "P2 WINS"), NORMAL)
            c.text_center(18, f"{self.s1}-{self.s2}", NORMAL, scale=2)
            if now - self.over_at > 4:
                self.state = "title"
            return
        super().render(c, now)
